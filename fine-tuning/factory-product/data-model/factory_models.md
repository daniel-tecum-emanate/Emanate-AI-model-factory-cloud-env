# `factory_models`

One row per **trained artifact** (not per pipeline execution — a `factory_runs` row is a
pipeline execution attempt, most of which never ship; `factory_models` is the durable,
permanent catalog of the models those runs actually produced). Backs the product's Models tab
(`src/features/model-factory/models/ModelsTab.tsx`), read via `v_factory_models` (a live join
against `org_custom_models` that computes `in_production` — never a stored duplicate of real
serving status, per the base migration's own comment).

**Populated by:** `models_backfill.py` — `fine-tuning/factory-product/models_backfill.py`. As of
`PRs/model-factory-production-catchup` (2026-08-14) this is the **single canonical writer** for
all 10 real trained models across every stream (PTC iterations, PTC shadow-lane extras,
Intelligence, Grand Steel). It previously shared the job with two per-model
`record_model.py` scripts (`runs/grand-steel/gs_corpus/record_model.py`, real but stale, and
`runs/ptc-steel/corpus/record_model.py`, a stray never-run duplicate of the Grand Steel script)
— both folded in and deleted by that PR.
**Real source of truth:** each model's own `MODEL-CARD.md` under `fine-tuning/models/<model>/`,
cross-checked against `company-brain/3-execution/MIXTURE-HISTORY.md`,
`company-brain/finetune-playbook/07-RUN-LEDGER.md`, and per-model reports under
`company-brain/3-execution/reports/`. **Not** a projection of any live pipeline stage — there is
no S9/S10-triggered write path yet (see Known gaps below).
**Row identity:** `_model_row_id(name) = uuid5(NAMESPACE, f"factory_models:{name}")`
(`NAMESPACE` imported from `backfill_ptc_history.py`) — a stable, deterministic id per model
name, so re-running the writer for an already-catalogued model is a real `UPDATE` via
`upsert(..., on_conflict="org_slug,model_id")`, never a fresh insert with a new id.

## Columns

| column | type | constraints | notes |
|---|---|---|---|
| `id` | uuid | PK, default `gen_random_uuid()` | in practice always the deterministic `_model_row_id()` value, not the DB default |
| `org_slug` | text | NOT NULL | e.g. `ptc-steel`, `grand-steel`, `emanate-intelligence` |
| `org_id` | text | nullable | real for Grand Steel rows; `NULL` for every PTC/Intelligence row — predates the factory's org_id/org_slug bridge |
| `org_name` | text | NOT NULL | |
| `model_id` | text | NOT NULL | the trained artifact's name, e.g. `ptc-steel-v7-agent` |
| `source_run_id` | uuid | FK → `factory_runs(id)`, nullable | real for models with a live pipeline run (4 PTC iterations, v5 via backfill, grand-steel-v1-agent); `NULL` for models launched via the manual REST path with no run row (`ptc-steel-v7-agent`, `grand-steel-v2-agent`) or a different lane entirely (`ptc-steel-a1-v1`, `intv1-corpus-v1`) — **never fabricated to satisfy the FK** |
| `iteration` | int | NOT NULL, default 1 | the model's iteration number within its own lineage |
| `ship_status` | text | NOT NULL, default `no_go`, CHECK IN `('shipped','no_go','pending','retired')` | see Ship-status discipline below |
| `headline_verdict` | text | nullable | free-text summary; must trace to the card's own current verdict language, never guessed |
| `base_model`, `lora_rank`, `lora_alpha`, `learning_rate`, `epochs`, `weight_decay`, `target_modules` | text/int | nullable | training recipe fields; free-text where the source format varies (e.g. an unrecorded alpha for pre-F2 models) |
| `train_records` | int | nullable | `NULL` where the source never published a count (e.g. `intv1-corpus-v1`) |
| `training_cost_usd` | numeric | nullable | scope varies by row — train-only for some, all-in (incl. a dual-bucket multiplier) for others; read each card's own cost section, not just this column (`fine-tuning/models/INDEX.md`'s discrepancy #6) |
| `train_eval_loss` | text | nullable | free-text scalar/summary; `NULL` where no eval split or scalar was ever published |
| `mixture` | jsonb | NOT NULL, default `{}` | shape-free — the accounting unit changed across real iterations (record-count → loss-bearing-tokens); never force-converted |
| `report` | jsonb | NOT NULL, default `[]` | ordered `[{title, body}]` narrative sections, plain text (no HTML/markdown) — the Models tab's detail view |
| `lessons_minted` | text[] | NOT NULL, default `{}` | not currently populated by this writer (reserved) |
| `training_provider` | text | nullable | the REAL platform (`fireworks`, `together`, `runpod (self-run axolotl)`) — never derived from an env-var name (the historical `TOGETHER_*` env names pointed at Fireworks) |
| `provider_job_id` | text | nullable | |
| `provider_model_path` | text | nullable | `NULL` where no full serving path was ever published (e.g. `ptc-steel-v4-agent`) — the sharpest NULL-on-purpose example in this table |
| `gpu_count`, `gpu_type`, `gpu_rental_started_at`, `gpu_rental_ended_at` | int/text/timestamptz | nullable | `NULL` across the board for serverless Fireworks/Together jobs (no rental exists); real only for `ptc-steel-a1-v1` (self-run RunPod) |
| `infra` | jsonb | NOT NULL, default `{}` | shape-free provider extras (dataset ids, platform token counts, job timing, cost story) |
| `loss_curve` | jsonb | NOT NULL, default `[]` | ordered `[{step, loss, label?}]` — **only real recorded points, never interpolated**; `[]` where no series was ever published (e.g. `ptc-steel-v7-agent`, `grand-steel-v2-agent`) or where only a single final scalar exists (a one-point "curve" is a scalar, not a curve) |
| `trained_at` | timestamptz | nullable | |
| `created_at` / `updated_at` | timestamptz | NOT NULL, default `now()` | DB-managed; `updated_at` moves on every re-run of the writer even for an unchanged row, since the upsert always resubmits every column |

## Ship-status discipline

`ship_status` must come from what the source material actually concluded, never a convenient
default:
- `pending` — the eval genuinely has not concluded, or the card explicitly declines to make a
  ship call either way (`ptc-steel-v7-agent`: a confirmed scope-ceiling finding exists, but the
  card states plainly it "does not constitute a ship recommendation in either direction").
- `no_go` — the eval concluded, unfavorably, and finally (`grand-steel-v1-agent`'s
  `FINALIZED_NO_TRAIN` verdict; `grand-steel-v2-agent`'s neither-gate-cleared-its-bar result).
- `shipped` — **has never once been used.** No fine-tuned model from any stream has ever served
  a customer; a row claiming `shipped` anywhere in this catalog would be fabricated history.

## Known gaps / accepted risks

- **No trigger/runtime writer exists.** Every row is written by a manually-run backfill script,
  not an S9-approved/S10-sync pipeline hook. `factory-automation/automation-graph/
  INTERACTION-PARITY-AND-VIDEO.md` Gap 3 names this as the mechanism gap: closing it means an
  S9/S10 stage writing the row automatically (a real, separately-scoped pipeline change touching
  `factory.py`/`factory_sync.py`, not a backfill-script change). Until it exists, every new
  trained model is one more manual addition to `models_backfill.py` someone has to remember to
  make — exactly how `grand-steel-v2-agent`/`ptc-steel-v7-agent` became gaps in the first place,
  and how `grand-steel-v1-agent`'s row went stale for two weeks after its own diagnostic
  concluded (`fine-tuning/models/INDEX.md` discrepancy #7).
- **`model_stream` does not exist on this table** (same gap `factory_runs.md` notes for
  `factory_runs`/`factory_org_configs`) — a model row is distinguished by its own `model_id` and
  `source_run_id`, not a stream column.
- **`training_cost_usd`'s scope (train-only vs. all-in) varies by row** and is not itself
  recorded in the schema — read each model's own `MODEL-CARD.md` cost section, not just this
  column (`fine-tuning/models/INDEX.md` discrepancy #6).

## Example row

`grand-steel-v2-agent`, from the 2026-08-14 backfill (`models_backfill.py --dry-run`):

```json
{
  "org_slug": "grand-steel",
  "model_id": "grand-steel-v2-agent",
  "source_run_id": null,
  "ship_status": "no_go",
  "training_provider": "fireworks",
  "provider_job_id": "accounts/emanate/supervisedFineTuningJobs/ozhgeptn",
  "training_cost_usd": 12.3431,
  "train_records": 658,
  "loss_curve": []
}
```
