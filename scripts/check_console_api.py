"""Exercise the deployed lifecycle console's API as a marketer would, and print what comes back with timings.

Calls the app over HTTPS with the CLI's OAuth token, so requests run as the signed-in user. Covers the plan,
segments, member lookup, copy review (check, failed approval, edited approval, rejection), a nudge pause, and
live scoring of a new member. Reviewer emails are shown as <user>.

Usage: uv run python scripts/check_console_api.py [--write-tests]
--write-tests makes the review and pause calls, which write to the console's Lakebase tables.
"""

import argparse
import json
import re
import subprocess
import time
import urllib.error
import urllib.request

from databricks.sdk import WorkspaceClient

EMAIL = re.compile(r"[a-z0-9._+-]+@[a-z0-9.-]+\.[a-z]+", re.I)
USER_ID = re.compile(r"\b\d{6,}@\d{6,}\b")  # x-forwarded-user is <user id>@<workspace id>


def call(base, token, method, route, body=None):
    req = urllib.request.Request(base + route, method=method, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            status, data = r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        status, data = e.code, json.loads(e.read() or b"null")
    return status, data, (time.perf_counter() - t) * 1000


def show(title, status, data, ms, keep=None):
    print(f"## {title}\n\nHTTP {status}, {ms:.0f} ms round trip from a laptop")
    if isinstance(data, dict) and "ms" in data:
        print(f"server-side query time: {data['ms']} ms")
    shown = {k: data[k] for k in keep if k in data} if keep and isinstance(data, dict) else data
    print(USER_ID.sub("<user id>", EMAIL.sub("<user>", json.dumps(shown, indent=1, default=str)))[:2500])
    print()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default="fevm-katie-febar")
    p.add_argument("--write-tests", action="store_true")
    args = p.parse_args()
    w = WorkspaceClient(profile=args.profile)
    base = w.apps.get("hearth-console").url.rstrip("/")
    token = json.loads(subprocess.run(["databricks", "auth", "token", "--profile", args.profile, "-o", "json"],
                                      capture_output=True, text=True, check=True).stdout)["access_token"]

    s, d, ms = call(base, token, "GET", "/api/whoami")
    show("Who the app sees", s, d, ms)
    s, d, ms = call(base, token, "GET", "/api/status")
    show("Serving tables right now", s, d, ms)
    s, d, ms = call(base, token, "GET", "/api/plan")
    show("Tomorrow's plan by nudge", s, d, ms)
    decision_date = d["by_nudge"][0]["decision_date"] if s == 200 and d["by_nudge"] else None

    for title, q in [("Segment: every planned send", "action=send"),
                     ("Segment: high-risk 45+ members on the free plan", "risk_band=high&age_band=45%2B&plan=free"),
                     ("Segment: small markets, all actions", "market_size=small")]:
        s, d, ms = call(base, token, "GET", f"/api/segments?{q}")
        show(title, s, d, ms, keep=["totals", "ms", "filters"])

    for mid in ["m_000001", "m_050003", "m_999999"]:
        s, d, ms = call(base, token, "GET", f"/api/members/{mid}")
        show(f"Member lookup {mid}", s, d, ms)

    s, d, ms = call(base, token, "GET", "/api/copy?status=needs_review&limit=3")
    show("Copy waiting for review", s, d, ms)
    review = d["rows"][0] if s == 200 and d["rows"] else None

    s, d, ms = call(base, token, "POST", "/api/serving/invoke", {"dataframe_records": [{
        "plan": "free", "age_band": "25-29", "platform": "ios", "market_size": "large", "metro": "Chicago",
        "tenure_days": 1, "days_since_last_session": 1, "sessions_1d": 0, "sessions_7d": 1, "sessions_prev_7d": 0,
        "sessions_trend_7d": 1, "sessions_14d": 1, "sessions_28d": 1, "push_sessions_28d": 0, "profile_views_7d": 9,
        "likes_sent_7d": 3, "likes_received_7d": 1, "matches_14d": 0, "messages_sent_7d": 0, "messages_sent_prev_7d": 0,
        "messages_sent_14d": 0, "messages_received_14d": 0, "reply_ratio_14d": 0.0, "pushes_7d": 0, "pushes_28d": 0,
        "open_rate_28d": 0.0, "reachable": True}]})
    show("Live score for a one-day-old member (hearth-lapse, run as the user)", s, d, ms)

    if not args.write_tests or review is None:
        return
    mid, original = review["member_id"], review["copy_text"] or ""
    s, d, ms = call(base, token, "POST", "/api/copy/check", {"member_id": mid, "copy": original})
    show(f"Check the failed message as written ({mid})", s, d, ms)
    s, d, ms = call(base, token, "POST", "/api/copy/review", {"member_id": mid, "copy": original, "decision": "approved"})
    show("Try to approve it unchanged (the server must refuse)", s, d, ms)
    fixed = {
        "new_likes": "New likes are waiting for you. Take a look when you have a moment.",
        "match_waiting": "A match sent you a message. Say hi back when you're ready.",
        "new_people_nearby": "New people joined Hearth near you. Take a look around.",
        "profile_tip": "Add a photo or answer a prompt so people can get to know you.",
        "we_miss_you": "Hearth is here whenever you want to look around again.",
    }[review["nudge_type"]]
    s, d, ms = call(base, token, "POST", "/api/copy/review", {"member_id": mid, "copy": fixed, "decision": "approved"})
    show("Approve an edited version", s, d, ms)

    s, d, ms = call(base, token, "GET", "/api/copy?status=needs_review&limit=3")
    other = next((r for r in d.get("rows", []) if r["member_id"] != mid), None) if s == 200 else None
    if other:
        s, d, ms = call(base, token, "POST", "/api/copy/review", {"member_id": other["member_id"], "copy": other["copy_text"] or "-", "decision": "rejected"})
        show(f"Reject another failed message ({other['member_id']})", s, d, ms)

    if decision_date:
        s, d, ms = call(base, token, "POST", "/api/plan/controls", {"decision_date": decision_date, "nudge_type": "new_people_nearby", "status": "paused",
                                                                    "note": "Small markets opt out at 1.5x. Pausing while we review the segment."})
        show("Pause new_people_nearby for the day", s, d, ms)
        for nudge in ["new_likes", "match_waiting"]:
            s, d, ms = call(base, token, "POST", "/api/plan/controls", {"decision_date": decision_date, "nudge_type": nudge, "status": "approved"})
            show(f"Approve {nudge}", s, d, ms)
    s, d, ms = call(base, token, "GET", f"/api/members/{mid}")
    show(f"Member lookup {mid} after the edited approval", s, d, ms, keep=["member", "ms"])


if __name__ == "__main__":
    main()
