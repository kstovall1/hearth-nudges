# Hearth synthetic data design

> [!IMPORTANT]
> **Reviewed 10/26.** Nothing has been generated yet. The answers to the open questions are recorded in `docs/build-log.md`.

## The story the data tells

Hearth is a fictional dating app. Its lifecycle team re-engages lapsing members with push notifications, and today that mostly means a generic "we miss you" blast to everyone who allows push.

Halfway through the 60-day history, the team raises the blast from once a week to three times a week to hit a reactivation goal. Opt-outs and uninstalls jump, the number of members Hearth can still reach drops, and reactivation barely moves. The members who leave are disproportionately ones who were going to come back on their own.

Alongside the blast, the team has been running a small randomized nudge test on lapsing members. Each eligible member gets a randomly chosen nudge type at a random hour, or nothing at all (the holdout). That test is what shows the better path. Some nudges work very well for specific members, some do nothing, and some backfire. Sending the right nudge at the right hour to fewer members beats blasting everyone.

**What the platform finds in this data**
- Who is about to lapse (activity decays for 1 to 2 weeks first)
- Which nudge, if any, actually changes whether each member comes back (compared with the holdout)
- That too many notifications drive opt-outs, and when it started (the blast change)
- That send hour matters, and each member's best hour shows up in their own activity

## Output location

```
📍 Catalog: katie_febar_catalog
   Raw files:   /Volumes/katie_febar_catalog/hearth_raw/landing/<entity>/dt=YYYY-MM-DD/
   Schemas:     hearth_raw, hearth_bronze, hearth_silver, hearth_gold, hearth_ml, hearth_serving
   Groups:      hearth_marketing, hearth_pii_readers (account groups)
```

## Raw entities

Raw files land in a UC Volume as they would from Hearth's source systems, one folder per day, so Auto Loader picks them up incrementally.

| Entity | Source system it mimics | Format | Approx. rows |
|---|---|---|---|
| `members` | Nightly export from the user database | CSV | 75,000 |
| `app_events` | Mobile event stream | JSON | ~12M |
| `notification_decisions` | Messaging tool export, one row per send-or-hold decision | JSON | ~1.4M |
| `notification_events` | Push provider callbacks | JSON | ~1.5M |
| `subscription_events` | Billing system | CSV | ~25,000 |

### members

| Column | Notes |
|---|---|
| `member_id` | `m_000001` style |
| `signup_ts` | ~67K members existed before the window, ~8K sign up during it |
| `first_name`, `email`, `phone` | Faker. **PII**, masked for marketing |
| `age_band` | 18-24 (31%), 25-29 (29%), 30-34 (19%), 35-44 (14%), 45+ (7%) |
| `gender_identity`, `seeking` | Coarse values only. **Sensitive**, see governance below |
| `metro` | ~12 US metros, Pareto weighted, plus "other". Each has a `market_size` of large, medium or small |
| `platform` | ios (58%), android (42%) |
| `plan` | free (82%), plus (13%), premium (5%) at the start of the window |
| `push_opt_in` | 71% opted in at signup |

### app_events

`event_id`, `member_id`, `session_id`, `event_ts`, `event_type`, `platform`, `properties` (JSON).

Event types are `session_start`, `session_end`, `profile_view`, `like_sent`, `like_received`, `match_created`, `message_sent`, `message_received`. Received likes and matches are what make some nudges true. "New likes waiting" is only eligible if the member has unseen likes, and "match is waiting on your reply" only if a match messaged them and they haven't answered.

Session counts are heavy tailed (a small share of members drive most activity), weekends run ~20% higher, and evenings peak.

### notification_decisions

One row per decision, including decisions to hold.

| Column | Notes |
|---|---|
| `decision_id`, `member_id`, `decided_ts` | |
| `campaign_id`, `campaign_type` | `blast` or `nudge_test` |
| `nudge_type` | `new_likes`, `match_waiting`, `profile_tip`, `new_people_nearby`, `we_miss_you` |
| `arm` | `treatment` or `holdout` |
| `scheduled_send_ts` | Null for holdout |
| `copy_variant_id`, `copy_text` | Template copy that Hearth used historically |

- **Blast.** Every opted-in member gets `we_miss_you` weekly, then three times a week from day 35.
- **Nudge test.** Every day, members with no session in 2 or more days are eligible, capped at one test decision per member every 3 days. 15% are held out. The rest get a nudge chosen at random from the ones they're eligible for, at a random hour from 8:00 to 22:00 local.

### notification_events

`notification_event_id`, `decision_id`, `member_id`, `event_ts`, `event_type`. Types are `delivered`, `opened`, `push_disabled`, `uninstalled`.

"Came back" isn't a raw event. It's computed in silver from `app_events`, the same way a real team would do it.

### subscription_events

`subscription_event_id`, `member_id`, `event_ts`, `event_type` (`trial_start`, `upgrade`, `renew`, `cancel`), `plan`, `price_usd` (plus $29.99, premium $49.99 a month, fictional).

## Patterns planted on purpose

These are the signals the models and Genie should find. They're simulated, and the README will say so.

**1. Activity decays before a lapse.** About 30% of members are on a lapse path at some point in the window. Their sessions roughly halve each week for 1 to 2 weeks and their message replies drop before they go quiet.

**2. Nudges work for some members and backfire for others.** These are the effects on the chance of coming back within 72 hours, compared with the holdout.

| Nudge | Works for | Does nothing or backfires for |
|---|---|---|
| `match_waiting` | Everyone eligible, about +10 pts | n/a |
| `new_likes` | Ages 18-29, about +7 pts | 35+, about +1 pt |
| `profile_tip` | Members in their first 30 days, about +5 pts | Everyone else, about 0, slight opt-out increase |
| `new_people_nearby` | Large metros, about +4 pts | Small markets, about -1 pt and 2x opt-outs |
| `we_miss_you` | Inactive 3 to 7 days, about +3 pts | Inactive 15+ days, about 0 and 3x opt-outs |

Baseline return without a nudge depends on how long the member has been away, around 25% at 2 to 3 days, 8% at 7 to 14 days, and 3% beyond that.

**3. Too many notifications drive opt-outs.** The base opt-out chance is ~0.4% per delivered push. It goes up 1.8x when the member got 3 to 4 pushes in the last 7 days and 3.5x at 5 or more. Open rates also drop with each extra push.

**4. Send hour matters.** Each member has a preferred hour that shows up in their session times. Sends within 2 hours of it open at the normal rate, other daytime sends open at ~60% of that, and sends from 23:00 to 07:00 open at ~30% and raise opt-outs 1.5x.

**5. Coming back leads to paying.** Free members who return and get a match within 72 hours upgrade within 14 days at ~3%, compared with ~0.5% otherwise.

**6. The incident.** From day 35 the blast frequency triples. Weekly opt-outs should rise roughly 2.5x, with no matching rise in reactivation.

## Bad data for the pipeline's expectations

To give the SDP expectations something to catch:
- ~0.3% of events have a null `member_id` (dropped)
- ~1% duplicate `event_id`s from at-least-once delivery (deduplicated)
- ~0.1% timestamps in the future from device clock skew (quarantined)
- ~0.2% unknown `event_type` values (quarantined)
- `app_version` appears in `app_events` from day 40 (schema evolution in Auto Loader)

## How the data feeds the build

| Layer | Tables |
|---|---|
| Silver | `sessions`, `member_daily_activity`, `notification_funnel` (decision → delivered → opened → returned within 72h → opted out → upgraded) |
| Gold | `member_daily_features`, `lapse_labels`, `nudge_outcomes`, `campaign_daily_kpis` |
| ML | Lapse model trained on `member_daily_features` + `lapse_labels`. Uplift model (Phase 2) trained on `nudge_outcomes` |
| Genie | `campaign_daily_kpis`, `nudge_outcomes`, `notification_funnel` |

**Lapse label.** A member counts as lapsed on date *d* if they had a session in the 14 days up to *d* and none in the 7 days after it. Features only use data up to *d*, and the train/test split is by date so nothing from the future leaks in. The expected positive rate is around 15 to 20%.

## Governance

- `email`, `phone`, `first_name` get column masks. Only `hearth_pii_readers` sees them unmasked.
- `gender_identity` and `seeking` are tagged sensitive. They're **left out of model features and copy prompts** and masked for `hearth_marketing`. The eval checks that nudge allocation rates don't differ sharply across these groups.
- Generated copy is checked for PII before it can reach Lakebase.

## How it gets generated

1. A **member-day simulation** in NumPy steps through the 60 days one day at a time (about 4.5M member-days, all vectorized). Each day's nudges change whether members come back, and that carries into the next day, which a single-pass Spark job can't do cleanly.
2. **Spark expands** each member-day into individual events and timestamps, adds Faker PII with a pandas UDF, injects the bad data, and writes the daily files.
3. A **seed** makes it reproducible. A **drip mode** generates one more day on demand, so new data can land during the live demo and be picked up incrementally.

The generator also writes each member's true preferred hour and nudge response to `hearth_ml.generator_truth`. That table is **only used to check whether the uplift model recovered the planted effects**, never as a feature.

It runs on serverless as a one-time job in the bundle, separate from the main pipeline job.
