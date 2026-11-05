"""A stand-in for Hearth's notification service, reading next best actions from Lakebase over Postgres.

What a real service would do, and what this times:
1. Connect with a short-lived OAuth token from the workspace. No password is stored anywhere.
2. Look up one member's next action by member_id, the call made when a push is about to go out.
3. Pull every send due in the next hour, the batch a dispatcher would run.
4. Dispatch only copy that passed the check, or a version a marketer approved in the lifecycle console. Skip
   nudge types a marketer paused for the day, messages a marketer rejected, and messages still waiting for review.

Runs from a laptop (uv run python src/hearth_serving/notification_simulator.py) or as a serverless job
(notification_simulator_job.py), which shows latency from inside the cloud region.
"""

import argparse
import random
import re
import socket
import statistics
import subprocess
import time
from datetime import datetime, timedelta, timezone

import psycopg
from databricks.sdk import WorkspaceClient

TABLE = "hearth.next_best_action"
LOOKUP_SQL = f"""
  SELECT member_id, action, hold_reason, nudge_type, send_at_utc, copy_text, copy_status, decided_at
  FROM {TABLE} WHERE member_id = %s
"""
DUE_SQL = f"""
  SELECT member_id, nudge_type, send_at_utc, copy_text, copy_status,
    NULL::text AS control_status, NULL::text AS review_decision, NULL::text AS final_copy
  FROM {TABLE}
  WHERE action = 'send' AND send_at_utc >= %s AND send_at_utc < %s
"""
# The same, honoring the lifecycle console's decisions once its schema exists
DUE_WITH_CONSOLE_SQL = f"""
  SELECT n.member_id, n.nudge_type, n.send_at_utc, n.copy_text, n.copy_status,
    c.status AS control_status, r.decision AS review_decision, r.final_copy
  FROM {TABLE} n
  LEFT JOIN console.campaign_controls c ON c.decision_date = n.decision_date AND c.nudge_type = n.nudge_type
  LEFT JOIN LATERAL (
    SELECT decision, final_copy FROM console.copy_reviews r
    WHERE r.member_id = n.member_id AND r.decision_date = n.decision_date
    ORDER BY created_at DESC LIMIT 1
  ) r ON true
  WHERE n.action = 'send' AND n.send_at_utc >= %s AND n.send_at_utc < %s
"""


def dispatch_decision(row):
    """What the service does with one due send: (sent?, copy, why)."""
    _, _, _, copy_text, copy_status, control_status, review_decision, final_copy = row
    if control_status == "paused":
        return False, None, "nudge paused"
    if review_decision == "rejected":
        return False, None, "copy rejected"
    if review_decision == "approved":
        return True, final_copy, "approved in console"
    if copy_status == "passed":
        return True, copy_text, "passed the check"
    return False, None, "waiting for review"


def connect(w, project, branch, endpoint, database):
    path = f"projects/{project}/branches/{branch}/endpoints/{endpoint}"
    host = w.postgres.get_endpoint(path).status.hosts.host
    token = w.postgres.generate_database_credential(endpoint=path).token
    user = w.current_user.me().user_name
    kwargs = {"host": host, "dbname": database, "user": user, "password": token, "sslmode": "require",
              "connect_timeout": 15}
    try:
        socket.getaddrinfo(host, 5432)
    except socket.gaierror:
        # Some macOS resolvers fail on long hostnames. Resolve with dig and keep host for TLS.
        ip = subprocess.run(["dig", "+short", host], capture_output=True, text=True).stdout.split()
        kwargs["hostaddr"] = [x for x in ip if x[0].isdigit()][-1]
    t = time.perf_counter()
    conn = psycopg.connect(**kwargs, autocommit=True)
    return conn, (time.perf_counter() - t) * 1000


def timed(cur, sql, params):
    t = time.perf_counter()
    cur.execute(sql, params)
    rows = cur.fetchall()
    return rows, (time.perf_counter() - t) * 1000


def plan_text(cur, sql, params):
    """The query plan's first line, with internal ids removed."""
    cur.execute(f"EXPLAIN {sql}", params)
    line = cur.fetchall()[0][0].strip()
    line = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27}", "<id>", line)
    return re.sub(r"partition_\d+", "partition", line.split("  (cost")[0])


def percentiles(ms):
    s = sorted(ms)
    pick = lambda q: s[min(len(s) - 1, int(q * len(s)))]
    return f"p50 {statistics.median(s):.1f} ms, p95 {pick(0.95):.1f} ms, p99 {pick(0.99):.1f} ms, max {s[-1]:.1f} ms"


def main(argv=None, client=None, where="laptop"):
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default="fevm-katie-febar")
    p.add_argument("--project", default="hearth-nudges")
    p.add_argument("--branch", default="dev")
    p.add_argument("--endpoint", default="primary")
    p.add_argument("--database", default="databricks_postgres")
    p.add_argument("--lookups", type=int, default=500)
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args(argv)
    w = client or WorkspaceClient(profile=args.profile)
    rng = random.Random(args.seed)
    out = []
    say = lambda line="": (print(line), out.append(line))

    say(f"## Connect (from {where})\n")
    conn, connect_ms = connect(w, args.project, args.branch, args.endpoint, args.database)
    say(f"OAuth token from the workspace, TLS connection to Lakebase in {connect_ms:.0f} ms (includes waking the compute if it had scaled to zero)")
    cur = conn.cursor()

    cur.execute(f"SELECT count(*), count(*) FILTER (WHERE action = 'send'), max(decided_at), min(decision_date) FROM {TABLE}")
    total, sends, decided_at, decision_date = cur.fetchone()
    cur.execute(f"SELECT copy_status, count(*) FROM {TABLE} WHERE action = 'send' GROUP BY 1 ORDER BY 2 DESC")
    by_status = dict(cur.fetchall())
    say(f"{TABLE}: {total:,} members, {sends:,} sends for {decision_date}, decided at {decided_at:%Y-%m-%d %H:%M} UTC")
    say(f"copy status of sends: {by_status}")

    say("\n## Index for the dispatcher\n")
    try:
        cur.execute(f"CREATE INDEX IF NOT EXISTS next_best_action_send_at ON {TABLE} (send_at_utc) WHERE action = 'send'")
        say("send_at_utc index on sends: in place (synced tables allow read queries and indexes only)")
    except psycopg.Error as e:
        say(f"send_at_utc index: not created ({e.diag.message_primary})")

    cur.execute(f"SELECT member_id FROM {TABLE}")
    ids = [r[0] for r in cur.fetchall()]
    sample = rng.sample(ids, min(args.lookups, len(ids)))
    unknown = [f"m_{9_000_000 + i}" for i in range(20)]  # members the table doesn't know, like new signups

    say(f"\n## Point lookups by member_id, {len(sample)} random members plus {len(unknown)} unknown ids\n")
    for mid in sample[:20]:  # warm the connection and cache
        timed(cur, LOOKUP_SQL, (mid,))
    lat, actions, missing = [], {}, 0
    for mid in sample + unknown:
        rows, ms = timed(cur, LOOKUP_SQL, (mid,))
        lat.append(ms)
        if rows:
            key = rows[0][1] if rows[0][1] == "hold" else f"send ({rows[0][6]})"
            actions[key] = actions.get(key, 0) + 1
        else:
            missing += 1
    say(percentiles(lat))
    say(f"answers: {dict(sorted(actions.items()))}, not found: {missing} (a new member falls back to live scoring on the hearth-lapse endpoint)")

    say(f"plan: {plan_text(cur, LOOKUP_SQL, (sample[0],))}")

    cur.execute(f"SELECT member_id FROM {TABLE} WHERE action = 'send' AND copy_status = 'passed' ORDER BY member_id LIMIT 1")
    for mid in [sample[0], cur.fetchone()[0]]:
        rows, ms = timed(cur, LOOKUP_SQL, (mid,))
        m = rows[0]
        say(f"\nexample answer for {m[0]} in {ms:.1f} ms: action={m[1]}, hold_reason={m[2]}, nudge={m[3]}, "
            f"send_at={m[4]}, copy_status={m[6]}")
        if m[5]:
            say(f"  copy: {m[5]}")

    cur.execute("SELECT to_regclass('console.campaign_controls') IS NOT NULL AND to_regclass('console.copy_reviews') IS NOT NULL")
    console = cur.fetchone()[0]
    due_sql = DUE_WITH_CONSOLE_SQL if console else DUE_SQL
    say("\n## Dispatcher: sends due in each hour of the decision day\n")
    say("honoring lifecycle console approvals and pauses" if console else "no lifecycle console decisions yet")
    start = datetime(decision_date.year, decision_date.month, decision_date.day, tzinfo=timezone.utc)
    batch_ms, outcomes = [], {}
    busiest = (None, 0)
    for h in range(30):  # local evenings in the US run past midnight UTC
        lo, hi = start + timedelta(hours=h), start + timedelta(hours=h + 1)
        rows, ms = timed(cur, due_sql, (lo, hi))
        batch_ms.append(ms)
        for r in rows:
            sent, _, why = dispatch_decision(r)
            key = ("sent: " if sent else "skipped: ") + why
            outcomes[key] = outcomes.get(key, 0) + 1
        if len(rows) > busiest[1]:
            busiest = (lo, len(rows))
    say(f"{len(batch_ms)} hourly batches: {percentiles(batch_ms)}")
    for key, n in sorted(outcomes.items()):
        say(f"  {key}: {n:,}")
    say(f"busiest hour starts {busiest[0]:%Y-%m-%d %H:%M} UTC with {busiest[1]:,} sends")

    say(f"plan: {plan_text(cur, due_sql, (start, start + timedelta(hours=1)))}")
    conn.close()
    return "\n".join(out)


if __name__ == "__main__":
    main()
