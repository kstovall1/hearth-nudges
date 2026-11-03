"""Check the hearth-lapse serving endpoint: same answers as batch scoring, a live score for a brand-new
member, and latency from this machine.

Usage: uv run python scripts/query_lapse_endpoint.py [--calls 30]
Run the hearth-daily job first so hearth_gold.member_lapse_scores has the latest day.
"""

import argparse
import statistics
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

FEATURES = [
    "plan", "age_band", "platform", "market_size", "metro", "tenure_days", "days_since_last_session", "sessions_1d",
    "sessions_7d", "sessions_prev_7d", "sessions_trend_7d", "sessions_14d", "sessions_28d", "push_sessions_28d",
    "profile_views_7d", "likes_sent_7d", "likes_received_7d", "matches_14d", "messages_sent_7d", "messages_sent_prev_7d",
    "messages_sent_14d", "messages_received_14d", "reply_ratio_14d", "pushes_7d", "pushes_28d", "open_rate_28d",
    "reachable",
]
INTS = {f for f in FEATURES if f not in {"plan", "age_band", "platform", "market_size", "metro", "reply_ratio_14d",
                                         "open_rate_28d", "reachable"}}

# A member who signed up yesterday: one long first session, a few likes, no pushes yet
NEW_MEMBER = {
    "plan": "free", "age_band": "25-29", "platform": "ios", "market_size": "large", "metro": "Chicago",
    "tenure_days": 1, "days_since_last_session": 1, "sessions_1d": 0, "sessions_7d": 1, "sessions_prev_7d": 0,
    "sessions_trend_7d": 1, "sessions_14d": 1, "sessions_28d": 1, "push_sessions_28d": 0, "profile_views_7d": 9,
    "likes_sent_7d": 3, "likes_received_7d": 1, "matches_14d": 0, "messages_sent_7d": 0, "messages_sent_prev_7d": 0,
    "messages_sent_14d": 0, "messages_received_14d": 0, "reply_ratio_14d": 0.0, "pushes_7d": 0, "pushes_28d": 0,
    "open_rate_28d": 0.0, "reachable": True,
}


def sql(w, warehouse_id, statement):
    r = w.statement_execution.execute_statement(warehouse_id=warehouse_id, statement=statement, wait_timeout="50s")
    while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(2)
        r = w.statement_execution.get_statement(r.statement_id)
    cols = [c.name for c in r.manifest.schema.columns]
    return [dict(zip(cols, row)) for row in (r.result.data_array or [])]


def typed(row):
    out = {}
    for f in FEATURES:
        v = row[f]
        if f == "reachable":
            out[f] = v in (True, "true")
        elif f in INTS:
            out[f] = int(v)
        elif f in {"reply_ratio_14d", "open_rate_28d"}:
            out[f] = float(v)
        else:
            out[f] = v
    return out


def lapse_risk(w, endpoint, records):
    resp = w.serving_endpoints.query(name=endpoint, dataframe_records=records)
    return [round(p[1], 4) for p in resp.predictions]  # column 1 is the chance of lapsing


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default="fevm-katie-febar")
    p.add_argument("--catalog", default="katie_febar_catalog")
    p.add_argument("--endpoint", default="hearth-lapse")
    p.add_argument("--warehouse", default="Serverless Starter Warehouse")
    p.add_argument("--calls", type=int, default=30)
    args = p.parse_args()
    w = WorkspaceClient(profile=args.profile)
    warehouse_id = next(wh.id for wh in w.warehouses.list() if wh.name == args.warehouse)
    ep = w.serving_endpoints.get(args.endpoint)
    entity = ep.config.served_entities[0]
    print(f"## Endpoint\n\n{args.endpoint}: {ep.state.ready.value}, serving {entity.entity_name} version {entity.entity_version}\n")

    rows = sql(w, warehouse_id, f"""
      SELECT s.lapse_risk AS batch_risk, s.model_version, f.*
      FROM {args.catalog}.hearth_gold.member_lapse_scores s
      JOIN {args.catalog}.hearth_gold.member_daily_features f USING (member_id, feature_date)
      WHERE s.feature_date = (SELECT max(feature_date) FROM {args.catalog}.hearth_gold.member_lapse_scores)
      ORDER BY s.lapse_risk DESC LIMIT 3
    """) + sql(w, warehouse_id, f"""
      SELECT s.lapse_risk AS batch_risk, s.model_version, f.*
      FROM {args.catalog}.hearth_gold.member_lapse_scores s
      JOIN {args.catalog}.hearth_gold.member_daily_features f USING (member_id, feature_date)
      WHERE s.feature_date = (SELECT max(feature_date) FROM {args.catalog}.hearth_gold.member_lapse_scores)
      ORDER BY s.lapse_risk ASC LIMIT 2
    """)
    live = lapse_risk(w, args.endpoint, [typed(r) for r in rows])
    print("## Endpoint against batch scores, same features\n")
    print(f"{'member_id':<10} {'feature_date':<12} {'batch':>7} {'endpoint':>9}  match")
    for r, l in zip(rows, live):
        print(f"{r['member_id']:<10} {r['feature_date']:<12} {float(r['batch_risk']):>7.4f} {l:>9.4f}  "
              f"{abs(float(r['batch_risk']) - l) < 1e-4}")

    print("\n## Brand-new member, scored live from features in the request\n")
    print({k: NEW_MEMBER[k] for k in ["tenure_days", "sessions_7d", "profile_views_7d", "likes_sent_7d", "plan"]})
    print("lapse_risk:", lapse_risk(w, args.endpoint, [NEW_MEMBER])[0])

    lapse_risk(w, args.endpoint, [NEW_MEMBER])  # warm
    times = []
    for _ in range(args.calls):
        t = time.perf_counter()
        lapse_risk(w, args.endpoint, [NEW_MEMBER])
        times.append((time.perf_counter() - t) * 1000)
    times.sort()
    print(f"\n## Latency, {args.calls} single-member calls from a laptop over the internet, after a warm-up call\n")
    print(f"p50 {statistics.median(times):.0f} ms, p95 {times[int(0.95 * len(times)) - 1]:.0f} ms, "
          f"max {times[-1]:.0f} ms")


if __name__ == "__main__":
    main()
