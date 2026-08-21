# Model Factory data model — where the product's information actually lives

## Why this folder exists

The Model Factory tab (`PRs/archive/model-factory-v1`) surfaces 10 tables in the platform-alpha
Supabase project. None of those tables are hand-edited — every row is a **projection** of
information that already exists somewhere else in this repo, in a shape nothing else was
designed to be queried as. Today those real sources are scattered across at least four
unrelated top-level locations:

| Real source | What lives there |
|---|---|
| `heartbeat/STATE.md` | Active Sessions table (who's working on what, right now) |
| `ledger/*.jsonl` | Per-day tool-call event log (files touched, commands run) |
| `company-brain/3-execution/PLAYBOOK-AND-LESSONS.md` + `ERROR-REGISTER.md` | The lesson/error-class canon |
| `fine-tuning/factory-product/specs/*.yaml` | The AgentSpec catalog |
| `fine-tuning/factory-product/runs/<org-slug>/*.json` | Per-run, per-stage reports written by `factory.py` |
| `fine-tuning/models/<model>/MODEL-CARD.md` | The trained-artifact catalog (`factory_models`) |

This folder is **not** a second copy of that data, and it does not relocate any of those
files — `heartbeat/STATE.md` and `ledger/*.jsonl` are machine-wide files other tooling
depends on today; `company-brain/` is hand-maintained canon; moving any of them would be a
much bigger, riskier change than what was asked for here. What this folder *is*: a single
place to look up, for each of the 10 tables the product actually renders —

1. its exact column schema (kept in sync with the migration, not a guess),
2. which real file(s) the data is really sourced from,
3. which script/function performs the projection, and
4. the known gaps or accepted residual risks in that projection (with the `DELTAS.md`
   decision code that accepted them, so the reasoning isn't lost).

If you're asking "where does the Fleet tab's agent list actually come from" or "what does
`factory_run_stages.report` actually contain," start here instead of re-reading
`factory_sync.py` from scratch.

## One entity, one file

| File | Table | One-line purpose |
|---|---|---|
| [`factory_runs.md`](./factory_runs.md) | `factory_runs` | One row per fine-tune pipeline execution |
| [`factory_run_stages.md`](./factory_run_stages.md) | `factory_run_stages` | One row per stage attempt within a run |
| [`factory_gate_requests.md`](./factory_gate_requests.md) | `factory_gate_requests` | One row per human-approval ask (S2/S6g/S9) |
| [`factory_gate_decisions.md`](./factory_gate_decisions.md) | `factory_gate_decisions` | Append-only audit trail of gate approve/reject calls |
| [`factory_agents.md`](./factory_agents.md) | `factory_agents` | One row per contributing agent session on a run |
| [`factory_handoffs.md`](./factory_handoffs.md) | `factory_handoffs` | Session-to-session handoff artifacts within a run |
| [`factory_run_events.md`](./factory_run_events.md) | `factory_run_events` | The one chronological timeline feed per run |
| [`factory_specs.md`](./factory_specs.md) | `factory_specs` | The premade AgentSpec library, made queryable |
| [`factory_lessons.md`](./factory_lessons.md) | `factory_lessons` | The Lessons tab's browse-only canon feed |
| [`factory_models.md`](./factory_models.md) | `factory_models` | The Models tab's durable catalog of trained fine-tune artifacts |

## Ground truth, in order

1. **The migration** — `supabase/migrations/20260725003051_model_factory_tables.sql`, on
   `platform-alpha`'s `main` branch (merged via PR `model-factory-v1` / #2125, 2026-07-25; the
   scratch `platform-alpha-model-factory-v1` worktree that originally held this PR has since
   been removed — worktree cleanup, 2026-07-27) — is the actual schema for the first 9 tables.
   `factory_models` (the 10th, added later) is its own three-migration set —
   `supabase/migrations/20260730233000_factory_models.sql` (base table + `v_factory_models`
   view), `..._233100_factory_models_report.sql` (`report` column), and
   `..._233200_factory_models_infra.sql` (`training_provider`/`provider_job_id`/
   `provider_model_path`/`gpu_*`/`infra`/`loss_curve`) — merged via PR `#2180`
   (`feat/factory-cursor-bridge-v1`), live on `main` since 2026-07-29. Every column table in
   this folder is a transcription of the relevant migration. If a migration changes, these docs
   are stale until someone updates them — there is no automated check tying the two together (a
   cross-repo test would be fragile here, since this is a workflow-repo doc describing a
   `platform-alpha`-repo file; see the note in each file instead).
2. **The sync code** — `fine-tuning/factory-product/{factory_sync,specs_sync,lessons_sync,
   models_backfill}.py` is what actually performs every projection this folder documents (
   `models_backfill.py` is a manually-run writer, not a live sync job — see `factory_models.md`
   for that distinction). When in doubt, the code is right and a doc here that disagrees with
   it is the one that's wrong — file a fix here, don't trust this folder blindly.
3. **`factory-automation/product-integration/{PLAN,DECISIONS,DELTAS}.md`** — the design
   record and the log of every deviation from it. This folder summarizes the parts of that
   record relevant to "where does this column's data come from," but isn't a replacement
   for it — read `DELTAS.md` directly for the full reasoning behind any gap flagged here.

## Adding a new field

If you add a column to one of these 9 tables (or wire up a new source), update the matching
file in this folder as part of that same change — not as a follow-up. A schema doc that
silently drifts out of sync with the code is worse than no doc at all, because it actively
misleads the next person who trusts it.
