# Build log

How this build was made: what was decided, why, and where AI helped. This feeds the README's "Decisions and trade-offs" and "How I used AI" sections.

**Tools.** Claude Code with Databricks skills (synthetic data, pipelines, Unity Catalog, model serving, Lakebase, Genie, apps, bundles), the Databricks CLI, and the FE Vending Machine for the workspace.

---

## 2026-10-26 · Setup and design decisions

**What happened**
- Set up the workspace, the Lakebase project (`hearth-nudges`, branch `dev`) and this repo.
- Tested the AI building blocks in the workspace before designing around them. The decision model (OpenJev) answered through both the System One API and `ai_decide` in SQL (after enabling the AI Decide preview). Claude Haiku answered through the Unity AI Gateway chat endpoint.
- Drafted the synthetic data design (`docs/data-design.md`).

**Decisions**

| Decision | Choice | Why |
|---|---|---|
| Orchestration | One bundle job: SDP pipeline, scoring, nudge picker, copy generation, copy check, Lakebase sync, approvals copy-back | One connected flow, easy to rerun end to end |
| Ingestion | SDP with Auto Loader, run on a schedule | Incremental and cheap now, continuous is a one-line change later |
| Picking the nudge | Trained lapse model now, uplift model later | Learns from Hearth's own outcomes. A decision model only reasons over what it's given |
| Serving endpoint | Batch scoring uses the UC model directly; the endpoint scores brand-new members live | Batch is faster and cheaper for known members, live covers cold start |
| Copy | `ai_query` with Claude Haiku, only for members picked to get a send | Short messages need a fast, cheap model; most members don't get a send |
| Copy check | OpenJev via `ai_decide` for PII, tone and policy before Lakebase | Structured yes/no and scores are cheaper and more consistent than asking a chat model |
| App LLM calls | Unity AI Gateway OpenAI-compatible endpoint | One governed path with usage tracking, and the provider can change without code changes |
| Notification service | A simulator reads Lakebase by member ID over Postgres and logs latency | Proves the next action is served without reverse ETL |
| Approvals | App writes to Lakebase, a job step copies them to Delta for Genie | Closes the loop between the marketer's decisions and the analytics |

**Prompts that shaped the work**
- "Make sure the decisions make sense for a real dating-app customer, not just the certification."
- "Is this an opportunity to use the new Jev model for decisions, or Unity AI Gateway, FMAPI or OMAPI?"

## 2026-10-26 · Data design review

**What happened**
- Went through the seven open questions in `docs/data-design.md` and removed the section once each had an answer.
- Tested whether UC grants can use groups made in this workspace. Groups from the workspace SCIM API (`databricks groups create`) are workspace-local, and a `GRANT` on them failed with `PRINCIPAL_DOES_NOT_EXIST`. Groups from the workspace's account SCIM endpoint (`/api/2.0/account/scim/v2/Groups`) are account groups, and the same `GRANT USE CATALOG` worked. Created `hearth_marketing` and `hearth_pii_readers` that way, revoked the test grant and deleted the workspace-local test groups.

**Decisions**

| Decision | Choice | Why |
|---|---|---|
| Data size | 75,000 members, 60 days | Runs in minutes on serverless. Covers the 14-day lookback and 7-day label window, leaves 25 days after the incident, and gives each nudge and segment enough test decisions to show the planted effects against the holdout |
| Plans | free, plus, premium | Matches how dating apps tier and gives Genie richer questions. The upgrade pattern treats both paid tiers as paid, so it adds little work |
| Lapse label | Session in the 14 days up to *d*, none in the 7 days after | Matches "inactive in the next 7 days" and lands near a 15 to 20% positive rate. Days since last session will be the strongest feature, which is easy to explain |
| Return window | 72 hours | Matches the one-test-decision-per-3-days cap, so test windows don't overlap and each return maps to one decision |
| Incident | Blast frequency triples on day 35 | A business decision gone wrong backs the "fewer, better nudges" story for the VP of Growth better than a technical send-time bug would |
| Schema names | `hearth_raw`, `hearth_bronze`, `hearth_silver`, `hearth_gold`, `hearth_ml`, `hearth_serving` | Keeps the project's objects distinct in a catalog that other work may share |
| Groups | Account groups `hearth_marketing` and `hearth_pii_readers`, created from the workspace | UC grants and `is_account_group_member()` masks need account groups, and a workspace admin can create them without the account console. Names are prefixed because account groups are visible across the shared metastore |

**Prompts that shaped the work**
- "Let's answer the open questions in docs/data-design.md."

## 2026-10-27 · Bundle skeleton

**What happened**
- Fixed the Databricks git hooks crashing on Python 3.15, which removed `SourceFileLoader.load_module`. `~/.zshrc` now puts Python 3.13 first on `PATH`.
- Added the bundle: `databricks.yml` with a `dev` target on the `fevm-katie-febar` profile, the six `hearth_` schemas in `resources/schemas.yml`, and the `landing` Volume in `resources/landing.volume.yml`. Deployed it and saved the CLI output to `docs/evidence/bundle-deploy.txt`.
- Ignored `.isaac/` (local tool state) and `.local/` (the private denylist for the pre-commit check).
- A subagent drafted the bundle. Its first version put every resource in `databricks.yml` and summarized the deploy in its own words. I moved the resources into `resources/` and replaced the summary with real CLI output.

**Decisions**

| Decision | Choice | Why |
|---|---|---|
| Bundle mode | No `mode: development` on the `dev` target | Development mode adds a `dev_<user>_` prefix to every name, and the schemas must match the names in the data design. Development mode would also pause schedules, and the pipeline runs on one |
| Resource layout | `resources/*.yml`, included from `databricks.yml` | Each later phase adds its own resource file without editing the root config |
| Workspace host | Profile only, never the host | The repo is public |

**Prompts that shaped the work**
- "Can't I update the Python version or something, do we need 3.15?"
- "Go ahead with items 1 and 2 using subagents."

## 2026-10-28 · Pre-commit leak check

**What happened**
- Added `scripts/check_leaks.py` and `scripts/install-hooks.sh`. The check scans the lines each commit adds for workspace hosts, UUIDs, tokens, warehouse IDs, Slack, Google Docs and go/ links, and Databricks emails. It also blocks the literal values in `.local/denylist.txt`, which is gitignored and seeded from `CLAUDE.local.md`. It refuses `CLAUDE.local.md` and anything under `.local/`, even if force-added.
- Tested it through real commits in a scratch clone with fake values: 11 cases blocked and 5 passed, as expected. An audit of every tracked file found nothing. The output is in `docs/evidence/leak-check-tests.txt`.
- A subagent wrote the first version, and I reviewed its files before using them. Its evidence file held 6 real values from `CLAUDE.local.md`, though its report said everything was redacted. Its error messages printed the whole line, it gave no line numbers, and it let an empty force-added file through. Nothing had been committed. I deleted that evidence file and the agent's scratch repos, fixed the script, and regenerated the evidence from real output.
- The Claude Code permission check stopped the first launch of that subagent, because it reads `CLAUDE.local.md` and installs a git hook. I relaunched it after approving both.

**Decisions**

| Decision | Choice | Why |
|---|---|---|
| Where the check runs | Repo-local `.git/hooks/pre-commit`, which the managed Databricks hook runs before gitleaks | The managed hook owns `core.hooksPath`, so this is the supported way to add a check. Gitleaks covers secrets, and this covers workspace details and names |
| Real values | Kept in a gitignored denylist, never in the script | The script is public, so a list of what to block would itself be a leak |
| Warehouse IDs | Flagged only next to the word "warehouse" | Any 16 hex characters also match git hashes and other IDs |
| False positives | Inline `leak-check: allow` marker | Keeps each exception visible on the line it allows |
| Subagent output | Checked by me before it goes into the repo | Its report and its files disagreed. Checking the files is what caught the leak |

**Prompts that shaped the work**
- "Go ahead with items 1 and 2 using subagents."
- "Approve the agent as written... good w/ that"

## 2026-10-28 · Synthetic data generator

**What happened**
- Built the generator in `src/hearth_data/` as a wheel the bundle builds. `simulate.py` steps through the window one day at a time in NumPy. `write.py` turns the result into raw files with Spark. `check.py` measures the planted patterns from the simulated rows. `config.py` holds every planted number.
- Added `resources/generator.job.yml`, a serverless job with `full` and `drip` modes, and a `generator` Volume for drip state.
- Calibrated against the data design. The first run had 28.8M app events and holdout returns far above the design, because engaged members' session rates spread too wide. A floor on session rates and shorter sessions brought it to 10.8M events. Measured results are in `docs/evidence/generator-patterns.txt`, and `docs/data-design.md` now shows planted and measured values side by side.
- Tested that drip continues a full run exactly: 60 days then one drip day matches 61 days straight through, row for row.
- Ran the job on serverless. It failed twice before it worked. Databricks passes the named parameter `end-date` as `--end_date`, so the script now accepts both. Then saving the drip state failed, because a zip write seeks and Volume files can't, so the state is written locally and copied in.
- Checked the landed files with SQL in `scripts/validate_landing.py`. Every bad-row rate landed within 0.01 points of its target, `app_version` first appears on day 40, nothing is orphaned, and every email and phone is in a reserved fictional range. The first check also found a 61st `app_events` folder from sessions crossing midnight, so a session's events now land in the folder of the day it started. The output is in `docs/evidence/landing-validation.txt`.

**Decisions**

| Decision | Choice | Why |
|---|---|---|
| Nudge test outcome | Decided when the decision is made: natural return at the member's baseline, plus the nudge's uplift if sent. Sessions in the 72 hours then agree with it | Treatment and holdout are comparable by construction, so the uplift model has a clean target, and `generator_truth` holds the exact planted value per decision |
| Holdout rows | Keep the nudge they would have got | Each nudge gets its own control group. Members eligible for `match_waiting` come back more anyway, so a shared holdout would overstate it |
| "Came back" window | 72 hours from `decided_ts` for both arms | Holdouts have no send time. Measuring both arms from the decision keeps the comparison fair |
| Planted uplift | At the member's preferred hour, 60% at other daytime hours | Ties the nudge and send-hour patterns together. The test's random hours measure about 70% of planted, and the doc says so |
| Doc numbers that didn't hold | Updated the doc to measured values instead of forcing the sim | Holdout baselines came out at 55% at 2 to 3 days away, not the draft's 25%. The mechanism is the planted truth, and aggregates follow from it |
| Blast send time | 13:00 UTC, so 6:00 for Pacific and Arizona | One fixed send time is how a blast tool works, and it puts a quarter of members in the night window without inventing a separate bug |
| Days away for members quiet before the window | `last_active_ts` in the members export | A real user database export has it, and without it silver can't tell a member away 3 days from one away 60 |
| Window dates | Ends 2 days before the run, drip never past yesterday | Genie questions like "last week" need recent data, and drip days must not look like future timestamps to the quarantine rule |
| Bad rows | Copies of real events with a defect | Dropping them leaves the planted patterns intact |
| Fake PII | `example` email domains and 555-01xx phones | Reserved for fiction, so no generated contact can belong to a real person |
| Wheel version | `dynamic_version` on the artifact | Serverless can reuse a cached wheel with the same version |

**Prompts that shaped the work**
- "Go for it."
