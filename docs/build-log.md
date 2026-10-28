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
