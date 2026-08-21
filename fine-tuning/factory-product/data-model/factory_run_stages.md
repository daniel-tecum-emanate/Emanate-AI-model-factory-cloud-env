# `factory_run_stages`

One row per stage attempt within a run — the Timeline/Run-detail view's main data source.

**Populated by:** `factory_sync.push_stage()` → `_stage_row()`, upserted on every stage
transition (`on_conflict=run_id,stage,attempt`) — `fine-tuning/factory-product/factory_sync.py`
**Real source of truth:** `fine-tuning/factory-product/runs/<org-slug>/NN-<stage>-report.json`,
written directly by `factory.py` at the end of every stage (before the sync push even runs —
the on-disk report is the ground truth `factory.py` itself trusts; the DB row is a
projection of it, never the other way around, per `PRRules.md` rule 10).

## Columns

| column | type | constraints | notes |
|---|---|---|---|
| `id` | uuid | PK | |
| `run_id` | uuid | NOT NULL, FK → `factory_runs(id)` ON DELETE CASCADE | |
| `stage` | text | NOT NULL | S-code, via `STAGE_CODES[stage_name]` |
| `attempt` | int | NOT NULL, default `1` | always `1` today — no retry-tracking call site yet |
| `status` | text | NOT NULL, IN `('pending','running','passed','failed','blocked_on_gate','skipped')` | derived in `_stage_row()`, see below |
| `report` | jsonb | NOT NULL, default `{}` | **PII-filtered** — see "PII handling" below, never the raw on-disk report |
| `verifier_verdict` | text | nullable, IN `('pass','fail','suspect')` | from `report.findings.verifier_verdict` when present |
| `exit_code` | int | nullable | from `report.exit_code` when present |
| `started_at` / `finished_at` | timestamptz | nullable | not currently populated by the sync code (reserved columns) |
| UNIQUE | | `(run_id, stage, attempt)` | the upsert's `on_conflict` target |

`status` derivation (`_stage_row()`): `blocked_on_gate` if the raw report's `status` is
`BLOCKED_ON_GATE`, or the stage is one of the 3 human-gate stages AND its status is
`gate_pending`; `failed` if raw status is `REFUSED` or `error`; otherwise `passed`.

## PII handling — the allow-list projection

`report` is **never** the raw on-disk JSON. `project_stage_report()` applies two layers
before anything reaches the DB (`PRRules.md` rule 6 / `PLAN.md` §2.10):

1. **Key allow-list** (`SAFE_REPORT_KEYS`) — any top-level key not explicitly named is
   dropped, full stop, regardless of its value. See the frozenset in `factory_sync.py`
   for the exact 18 keys currently allowed (`status`, `decision_request`, `findings`,
   `checklist`, `recommendation`, cost/projection fields, etc. — no key here has ever
   carried an account name, transcript excerpt, or corpus example, verified against every
   real report shape `factory.py` emits).
2. **Structured-PII regex scrub** (`_scrub_structured_pii`) — recursively redacts
   email/SSN/phone-shaped strings from every surviving value, in case free-text prose
   under an allow-listed key (e.g. `decision_request.recommendation`) ever embeds one.

## Known gaps / accepted risks

- The regex scrub only catches **structured** PII shapes. An unstructured customer name
  embedded in prose (e.g. "approve for Jane Doe's org") is **not** caught — this is an
  accepted residual risk (`DELTAS.md` D19), because the real mitigation is `factory.py`
  itself never writing customer names into these fields, not an unbounded NLP filter here.
- `attempt`, `started_at`, `finished_at` are real columns with no current write path.

## Example row

From the real dry-run fixtures
(`fine-tuning/factory-product/runs/qa-dryrun-synth/{02-governance,06-project}-report.json`):

```json
{
  "stage": "S2",
  "attempt": 1,
  "status": "passed",
  "report": {
    "status": "approved",
    "dry_run": true,
    "checklist": {
      "customer_status_confirmed": true,
      "data_use_clearance": true,
      "dpa_signed": true
    },
    "decision_request": {
      "decision": "clear QA Dryrun Synth Org (qa-dryrun-synth) for data-use / SFT training",
      "recommendation": "approve"
    }
  }
}
```
