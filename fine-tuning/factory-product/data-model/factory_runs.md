# `factory_runs`

One row per fine-tune pipeline execution (one row per org, per training attempt — a
monthly retrain is a *new* run, linked back to its predecessor via `predecessor_run_id`).

**Populated by:** `factory_sync.push_stage()` → `_run_row()`, upserted on every stage
transition (`on_conflict=id`) — `fine-tuning/factory-product/factory_sync.py`
**Real source of truth:** the config file passed to `factory.py`
(`fine-tuning/factory-product/configs/<org>.yaml`) plus whichever stream-scoped stage
report was just written. Legacy per-account reports remain at
`runs/<org-slug>/NN-<stage>-report.json`; additive streams use
`runs/<org-slug>/<model-stream>/NN-<stage>-report.json`.
**Run-id persistence:** `factory.py` is a fresh, stateless process on every invocation
(no daemon) — the Supabase row's `id` is minted once and cached locally by
`(org_slug, model_stream)`. The per-account cache remains at the grandfathered
`runs/<org-slug>/.sync_state.json`; other streams use
`runs/<org-slug>/<model-stream>/.sync_state.json`. These files are never committed,
so every later stage push for the same run updates the same row instead of inserting a
new one.

## Columns

| column | type | constraints | notes |
|---|---|---|---|
| `id` | uuid | PK, default `gen_random_uuid()` | minted once per run, cached in `.sync_state.json` |
| `org_slug` | text | NOT NULL | the config's org slug, e.g. `qa-dryrun-synth` |
| `org_id` | text | nullable | `cfg["org"]["id"]` — often absent, hence nullable |
| `org_name` | text | NOT NULL | `cfg["org"]["name"]` |
| `run_kind` | text | NOT NULL, IN `('first-train','monthly-retrain')` | derived: `monthly-retrain` iff `cfg["champion"]["model_id"]` is set |
| `model_stream` | text | NOT NULL, IN `('per-account','intelligence')` | selected by `factory.py --stream`; absence of the flag remains `per-account` |
| `status` | text | NOT NULL, default `running`, includes `queued`, `running`, `blocked_on_gate`, `paused`, `failed`, `no_go`, `shipped`, `abandoned` | best-effort derivation — see gaps below; queued/paused count as live |
| `current_stage` | text | NOT NULL | S-code (`S1`..`S11`), see `STAGE_CODES` map |
| `config_snapshot` | jsonb | NOT NULL, default `{}` | not currently populated by `_run_row()` (reserved column) |
| `cost_projected_usd` | numeric | nullable | pulled from the `project`/`launch` stage report's `decision_request.cost.total_projection_usd` |
| `cost_actual_usd` | numeric | nullable | not currently populated (reserved column) |
| `iteration` | int | NOT NULL, default `1` | **populated since 2026-07-24 (DELTAS.md D22)** — `get_or_create_run_id` bumps this on every new run for an org whose predecessor already reached a terminal status |
| `predecessor_run_id` | uuid | FK → `factory_runs(id)` | **populated since 2026-07-24 (DELTAS.md D22)** — set to the org's prior run's id the moment a retrain mints a new run_id |
| `metrics` | jsonb | NOT NULL, default `{}` | not currently populated (reserved column) |
| `lessons_minted` | text[] | NOT NULL, default `{}` | not currently populated (reserved column) |
| `started_at` / `updated_at` | timestamptz | NOT NULL, default `now()` | DB-managed |

## Where the data really comes from

Every field traces back to the YAML config (`fine-tuning/factory-product/configs/<org>.yaml`)
`factory.py` was invoked with, plus whatever the *current* stage's on-disk report says.
Legacy top-level `model_plan`/training/champion/gates/budget/paths are the implicit
`per-account` plan. An additive `model_plans.<stream>` block is resolved without
inheriting those per-account sections.
`status` is a **best-effort** derivation (`_run_status_for()`), not a real state machine —
it reads the single most-recent report's `status`/stage name and maps it to one of the 6
DB states; it has no memory of the run's history beyond that one report.

## Stream identity and setup concurrency

Run cache, queued-run claim, reports, approvals, held-out manifests, iteration digests,
predecessor lookup, and gate lookup are scoped by `(org_slug, model_stream)`. A first
Intelligence run is therefore iteration 1 / `first-train` with no per-account predecessor.

After the prerequisite platform migration, live uniqueness is one row per
`(org_slug, model_stream)` for `queued`, `running`, `blocked_on_gate`, and `paused`.
A per-account row and an Intelligence row for the same customer may therefore coexist;
a second live row in the same stream remains an atomic database rejection. The workflow
claim filters on the requested stream and does not use a sibling-row pre-check as a
substitute for a lease.

Concurrency is intentionally narrower than row coexistence. Intelligence may perform only
S1/S2 setup and read work while a sibling stream is live. S3 and every later Intelligence
stage fail closed, even with `--approved`, until a separately implemented atomic org-scoped
execution lease exists. A check-then-act query for a live sibling row is not such a lease
and must not be described as one. Existing per-account execution behavior is unchanged.

## Historical backfill (2026-07-28)

Not every row in this table is a projection of a real `factory.py` invocation. 4 rows
(`org_slug='ptc-steel'`, `iteration` 1-4) are a one-time historical backfill of REAL
pre-automation fine-tune iterations that happened manually via Cursor/Claude Code sessions,
recorded only in `company-brain/3-execution/MIXTURE-HISTORY.md` — written by
`fine-tuning/factory-product/backfill_ptc_history.py` (idempotent, deterministic `uuid5` ids).
These are distinguishable from a real pipeline run: `trigger_source` is `NULL` (its own column
comment defines that as "predates provenance recording," which is literally true here) and
every stage/gate/event row the backfill wrote carries `"backfill": true` in its jsonb payload.
See `workspace/PRODUCTION.md` for what exactly landed and `backfill_ptc_history.py`'s own
module docstring for the full citation trail.

## Known gaps / accepted risks

- `config_snapshot`, `cost_actual_usd`, `metrics`, `lessons_minted` are real columns in the
  migration with **no current write path** — nothing populates them yet. A Runs tab reading
  these today gets DB defaults (`{}`, `NULL`), not fabricated values.
- `iteration`/`predecessor_run_id` **were the same kind of unpopulated-reserved-column gap
  until 2026-07-24 — closed, and it turned out to be a real correctness bug, not just a
  missing nice-to-have.** `get_or_create_run_id` was caching a run_id per org slug FOREVER,
  with nothing checking whether the org's prior run had already finished — a second
  invocation (a real monthly retrain) silently overwrote the finished run's own history in
  place AND could resolve a fresh gate to `approved` purely because the old run's
  already-decided `factory_gate_requests` row sat under the same `(run_id, gate)` key, with
  zero new human decision. Fixed in `get_or_create_run_id` (checks the cached run's live
  `status` before reusing it; a terminal status mints a fresh id and records both columns).
  See `DELTAS.md` D22 for the full repro/fix/live-verification.
- `factory_models` and `factory_org_configs` still have no `model_stream` column.
  No model row is created during setup; a future trained artifact remains distinguishable
  by its distinct `model_id` and `source_run_id`.
- The product start dialog/action and stream-scoped uniqueness migration remain the
  prerequisite platform half before an operator can safely queue an explicit stream from
  the UI. The workflow permits stream-scoped setup registration/claim after that platform
  change, while the independent S3+ execution-lease blocker remains in force.

## Example row

Derived from the real dry-run fixture at
`fine-tuning/factory-product/runs/qa-dryrun-synth/06-project-report.json`:

```json
{
  "org_slug": "qa-dryrun-synth",
  "org_name": "QA Dryrun Synth Org",
  "run_kind": "first-train",
  "model_stream": "per-account",
  "status": "running",
  "current_stage": "S6",
  "cost_projected_usd": 17.16
}
```
