# `factory_gate_requests`

One row per human-approval ask — the 3 hard-coded gates in the pipeline (S2 governance
clearance, S6g launch/spend approval, S9 ship approval). This is what the Gate Card renders
while a request is `pending`.

**Populated by:** `factory_sync.push_gate_request()`, called from `resolve_gate()`, which
`factory.py`'s 3 human-gate stage handlers call **only when `--approved` was not passed**
(that flag is a full local bypass that never touches this table at all) —
`fine-tuning/factory-product/factory_sync.py`
**Real source of truth:** the same `NN-<stage>-report.json`'s `decision_request` object —
this table stores that object (whole, un-filtered by `SAFE_REPORT_KEYS`) as its own field,
not the projected `factory_run_stages.report`.

## Columns

| column | type | constraints | notes |
|---|---|---|---|
| `id` | uuid | PK | |
| `run_id` | uuid | NOT NULL, FK → `factory_runs(id)` ON DELETE CASCADE | |
| `gate` | text | NOT NULL, IN `('S2','S6g','S9')` | via `GATE_CODES[stage_name]` |
| `decision_request` | jsonb | NOT NULL | the raw `decision_request` object from the stage report — **not** allow-listed the way `factory_run_stages.report` is (see PII note below) |
| `status` | text | NOT NULL, default `pending`, IN `('pending','approved','rejected','withdrawn')` | mutated only via `decide_factory_gate()` RPC |
| `requested_at` | timestamptz | NOT NULL, default `now()` | |
| `resolved_at` | timestamptz | nullable | set by `decide_factory_gate()` |
| UNIQUE (partial) | | `(run_id, gate)` WHERE `status='pending'` | at most one *open* request per run+gate — corrected from `PLAN.md`'s original "(run_id, stage)" wording, see `DELTAS.md` D1 |

## Idempotency — why this isn't a normal upsert

The partial unique index above means PostgREST's `on_conflict` upsert can't target it
directly. `push_gate_request()` instead selects for an existing row (pending OR resolved —
re-pushing an identical request on every re-invocation of a stateless CLI would just be
noise) and only inserts if none exists. A genuine concurrent double-invocation can still
lose a race to the DB's own partial index (`23505 duplicate key`) even with the select-first
check — this is handled by catching that specific error and re-selecting the winner's row,
so every caller still gets the promised idempotent no-op (`DELTAS.md` D19).

## Fail-closed direction

Unlike the stage-report push (fail-open — a Supabase outage must never block the pipeline),
gate push/poll is **fail-closed**: any transport error on either the push or the poll is
treated identically to "not approved." `--approved` bypasses this function entirely and is
the only way to skip a real human gate — it is never confused with a Supabase-outage
false-approve, because the sync path isn't even called in that branch.

## Known gaps / accepted risks

- `decision_request` here is the **raw** object (unlike `factory_run_stages.report`, which
  is allow-listed). This is intentional — the Gate Card needs to render the full ask
  verbatim for the approver — but it means this column carries a slightly wider surface
  than the stage-report projection. Verified against every real gate-stage report shape:
  the same "counts/verdicts/cost/methodology prose" character as the allow-listed keys,
  no different in kind.

## Example row

From the real dry-run fixture
(`fine-tuning/factory-product/runs/qa-dryrun-synth/06-project-report.json`):

```json
{
  "gate": "S6g",
  "status": "pending",
  "decision_request": {
    "decision": "approve training spend for QA Dryrun Synth Org (qa-dryrun-synth) v1",
    "options": ["approve at projection", "reject", "re-scope corpus and re-project"],
    "recommendation": "approve",
    "cost": {
      "sft_estimate_usd": 0.83,
      "train_all_in_usd": 2.16,
      "eval_window_cap_usd": 15,
      "total_projection_usd": 17.16
    },
    "reversibility": "high pre-launch ($0 spent); capacity-FAILED jobs bill $0"
  }
}
```
