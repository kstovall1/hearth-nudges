# Hearth synthetic data design

> [!IMPORTANT]
> **Built 10/28.** `src/hearth_data/` implements this design. Measured values come from `docs/evidence/generator-patterns.txt` (seed 7, window 8/28 to 10/26/2026). Decisions are recorded in `docs/build-log.md`.

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
   Generator:   /Volumes/katie_febar_catalog/hearth_raw/generator/ (drip state, not for ingestion)
   Truth:       katie_febar_catalog.hearth_ml.generator_truth
   Schemas:     hearth_raw, hearth_bronze, hearth_silver, hearth_gold, hearth_ml, hearth_serving
   Groups:      hearth_marketing, hearth_pii_readers (account groups)
```

## Raw entities

Raw files land in a UC Volume as they would from Hearth's source systems, one folder per UTC day, so Auto Loader picks them up incrementally. Timestamps are UTC with milliseconds. The app uploads a session's events together, so a session that crosses midnight lands in the folder of the day it started.

| Entity | Source system it mimics | Format | Approx. rows |
|---|---|---|---|
| `members` | Nightly export of new members from the user database | CSV | 75,162 |
| `app_events` | Mobile event stream | JSON | 11.0M, of which ~1.6% are bad rows |
| `notification_decisions` | Messaging tool export, one row per send-or-hold decision | JSON | 1.39M |
| `notification_events` | Push provider callbacks | JSON | 1.34M, of which 1% are duplicates |
| `subscription_events` | Billing system | CSV | 26,589 |

### members

| Column | Notes |
|---|---|
| `member_id` | `m_000001` style |
| `signup_ts` | ~67K members existed before the window, ~8K sign up during it |
| `first_name`, `email`, `phone` | Faker. **PII**, masked for marketing |
| `age_band` | 18-24 (31%), 25-29 (29%), 30-34 (19%), 35-44 (14%), 45+ (7%) |
| `gender_identity`, `seeking` | Coarse values only. **Sensitive**, see governance below |
| `metro` | ~12 US metros, Pareto weighted, plus "other". Each has a `market_size` of large, medium or small |
| `timezone` | IANA name from the metro, so silver can work in local hours |
| `platform` | ios (58%), android (42%) |
| `plan` | free (82%), plus (13%), premium (5%) at the start of the window |
| `push_opt_in` | 71% opted in at signup |
| `last_active_ts` | Last session as of the export, so days away is known for members quiet since before the window |

The first day's file holds every member who joined before the window. Each later day holds that day's signups.

### app_events

`event_id`, `member_id`, `session_id`, `event_ts`, `event_type`, `platform`, `properties` (JSON), and `app_version` from day 40.

`properties` holds `source` (`push` or `organic`) on `session_start`, `duration_s` on `session_end`, and `other_member_id` on interactions. Likes and messages that arrive while a member is away have no `session_id`. The other member is random, so pairs don't reconcile across members.

Event types are `session_start`, `session_end`, `profile_view`, `like_sent`, `like_received`, `match_created`, `message_sent`, `message_received`. Received likes and matches are what make some nudges true. "New likes waiting" is only eligible if the member has unseen likes, and "match is waiting on your reply" only if a match messaged them and they haven't answered.

Session counts are heavy tailed (a small share of members drive most activity), weekends run ~20% higher, and evenings peak. 1.1M sessions in all.

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

The messaging tool decides once a day at 07:00 UTC, so `decided_ts` is the same for every decision that day.

- **Blast.** Every reachable member gets `we_miss_you` weekly, then three times a week from day 35. It goes out at 13:00 UTC, which is 9:00 Eastern but 6:00 Pacific and Arizona, so a quarter of members get it at night.
- **Nudge test.** Every day, reachable members with no session in 2 or more days are eligible, capped at one test decision per member every 3 days. Each gets a nudge chosen at random from the ones they're eligible for, then 15% are held out. Holdout rows keep the nudge they would have got, so each nudge has its own control group. Treated members get it at a random hour from 8:00 to 22:00 local.

### notification_events

`notification_event_id`, `decision_id`, `member_id`, `event_ts`, `event_type`. Types are `delivered`, `opened`, `push_disabled`, `uninstalled`. 97% of sends are delivered. A fifth of opt-outs are uninstalls, and an uninstalled member never comes back.

"Came back" isn't a raw event. It's computed in silver from `app_events`: a session in the 72 hours after `decided_ts`. Both arms use `decided_ts`, so the holdout compares like with like. Decisions in the last 3 days of the data don't have a full window yet.

### subscription_events

`subscription_event_id`, `member_id`, `event_ts`, `event_type` (`trial_start`, `upgrade`, `renew`, `cancel`), `plan`, `price_usd` (plus $29.99, premium $49.99 a month, fictional).

## Patterns planted on purpose

These are the signals the models and Genie should find. They're simulated, and the README will say so.

**1. Activity decays before a lapse.** 27.5% of members are on a lapse path at some point in the window. Their sessions halve each week for 1 to 2 weeks and their message replies drop faster before they go quiet.

**2. Nudges work for some members and backfire for others.** Uplift is in points of coming back within 72 hours, compared with the same nudge's holdout.

| Nudge | Segment | Planted | Measured in the test |
|---|---|---|---|
| `match_waiting` | Everyone eligible | +10 | +6.6 |
| `new_likes` | Ages 18-29 | +7 | +4.3 |
| | Ages 30-34 | +4 | +3.6 |
| | Ages 35+ | +1 | +1.5 |
| `profile_tip` | First 30 days | +5 | +3.5 |
| | After 30 days | 0, 1.3x opt-outs | -0.1 |
| `new_people_nearby` | Large markets | +4 | +2.5 |
| | Medium markets | +1.5 | +1.9 |
| | Small markets | -1, 2x opt-outs | -0.9, opt-outs 1.8% against 0.8% |
| `we_miss_you` | Away 2 days | +1 | +0.4 |
| | Away 3 to 7 days | +3 | +2.8 |
| | Away 8 to 14 days | +1.5, 1.5x opt-outs | +2.0 |
| | Away 15+ days | 0, 3x opt-outs | +0.2, opt-outs 2.8% |

Planted uplift is at the member's preferred hour. Other daytime hours get 60% of it. The test sends at random hours, so measured uplift averages about 70% of planted, give or take about a point in the smaller segments. `hearth_ml.generator_truth` has the planted values for every test decision.

Most nudges go to members who were coming back anyway. Holdout return within 72 hours is 55% at 2 to 3 days away, 45% at 4 to 6, 31% at 7 to 14 and 5% beyond.

**3. Too many notifications drive opt-outs.** The base opt-out chance is 0.4% per delivered push. It goes up 1.8x when the member got 3 to 4 pushes in the last 7 days and 3.5x at 5 or more. Open rates drop 15% with each extra push in 7 days.

**4. Send hour matters.** Each member has a preferred hour that shows up in their session times. Sends within 2 hours of it open at the normal rate, other daytime sends at 60% and sends from 23:00 to 07:00 at 30%, and night sends raise opt-outs 1.5x. Measured open rates are 7.5%, 3.9% and 1.1%. Night is lower than planted because night sends are all blasts.

**5. Coming back leads to paying.** Free members who return and get a match within 72 hours upgrade within 14 days at 3.0%, against 0.6% for returners without a match.

**6. The incident.** From day 35 the blast triples. Weekly opt-outs rise 2.6x (weeks 5 to 7 against weeks 1 to 3) and reactivations after 7+ days away rise only 1.06x. Reachable members fall from 47,300 to 34,400 over the window.

## Bad data for the pipeline's expectations

To give the SDP expectations something to catch:
- ~0.3% of app events have a null `member_id` (dropped)
- ~1% duplicate `event_id`s in app events and push callbacks, from at-least-once delivery (deduplicated)
- ~0.1% of app events have timestamps 1 to 3 years ahead from device clock skew (quarantined)
- ~0.2% of app events have unknown `event_type` values: `screen_view`, `ad_impression`, `SESSION_START` (quarantined)
- `app_version` appears in `app_events` from day 40 (schema evolution in Auto Loader)

The bad rows are copies of real events with the defect added, so dropping them leaves the planted patterns intact.

## How the data feeds the build

| Layer | Tables |
|---|---|
| Silver | `sessions`, `member_daily_activity`, `notification_funnel` (decision → delivered → opened → returned within 72h → opted out → upgraded) |
| Gold | `member_daily_features`, `lapse_labels`, `nudge_outcomes`, `campaign_daily_kpis` |
| ML | Lapse model trained on `member_daily_features` + `lapse_labels`. Uplift model (Phase 2) trained on `nudge_outcomes` |
| Genie | `campaign_daily_kpis`, `nudge_outcomes`, `notification_funnel` |

**Lapse label.** A member counts as lapsed on date *d* if they had a session in the 14 days up to *d* and none in the 7 days after it. Features only use data up to *d*, and the train/test split is by date so nothing from the future leaks in. The measured positive rate is 21%.

## Governance

- `email`, `phone`, `first_name` get column masks. Only `hearth_pii_readers` sees them unmasked.
- `gender_identity` and `seeking` are tagged sensitive. They're **left out of model features and copy prompts** and masked for `hearth_marketing`. The eval checks that nudge allocation rates don't differ sharply across these groups.
- Generated copy is checked for PII before it can reach Lakebase.

## How it gets generated

1. A **member-day simulation** in NumPy (`simulate.py`) steps through the 60 days one day at a time (about 4.5M member-days, all vectorized, 20 seconds). Each day's nudges change whether members come back, and that carries into the next day, which a single-pass Spark job can't do cleanly.
2. **Spark expands** each session into individual events (`write.py`), adds Faker PII with a pandas UDF, adds the bad rows, and writes the daily files. Emails use reserved `example` domains and phones use the fictional 555-01xx range.
3. A **seed** makes it reproducible. The window ends 2 days before the run, so "last week" questions in Genie have data.
4. **Drip mode** continues from the saved state and appends the next day, never past yesterday, so new data can land during the live demo. A drip day matches what a longer full run would have produced, row for row. Push callbacks that fall after the last day wait for the next run, so each day's folder is complete.

`check.py` measures the planted patterns from the simulated rows, the way silver will. The generator also writes the planted uplift of every nudge for every test decision, and each member's preferred hour, to `hearth_ml.generator_truth`. That table is **only used to check whether the uplift model recovered the planted effects**, never as a feature.

It runs on serverless as a one-time job in the bundle (`resources/generator.job.yml`), separate from the main pipeline job:

```
databricks bundle run hearth_generate_data                       # full history, replaces the landing files
databricks bundle run hearth_generate_data --params mode=drip    # add the next day
```

After a full run, full-refresh the pipeline, because the old files are gone.
