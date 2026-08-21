# `factory_run_events`

The one chronological timeline feed per run — every stage/agent/gate/cost/alert moment,
append-only, in one queryable stream.

**Populated by:** nine call sites today (was four; 2026-07-31 stress-test
EVENT-COVERAGE closed the stage/gate/cost gap — see
`factory-automation/stress-test-2026-07-31/findings/03-event-coverage.md`) —
1. `factory_sync.push_agents()` → `project_agents()` emits `agent_spawned` /
   `agent_finished` alongside the `factory_agents` rows it projects — since
   2026-07-31 with deterministic uuid5 ids + an `on_conflict=id` upsert (the
   old id-less insert duplicated every active session once per poll).
2. `factory_decisions.py` emits `decision_recorded` when a decision is recorded
   (rode on `steering_event` + `ref.event_subtype` until 2026-07-31 — the
   `20260728064545` migration had already widened the CHECK and re-mapped the
   old rows, but the emitter was never switched).
3. `factory_sync.push_node_agent()` emits `agent_dispatched`
   (`factory-observability-v1` B1), for every dispatched node-agent outcome.
4. `platform-alpha`'s `startFactoryRun` server action emits a `steering_event` with
   `ref.event_subtype: "run_queued"` when a run is queued from the product.
5. **`factory_sync.push_stage()` emits `stage_started` + `stage_passed`/`stage_failed`
   (and `cost_recorded` when the report carries a projection)** via
   `_stage_event_rows()` — uuid5-idempotent per (run, stage, attempt), `ts` =
   true event time (start stamped by factory.py before the handler runs;
   finish = the report's own `generated_at`). A `BLOCKED_ON_GATE` report emits
   nothing (the stage never ran); a pending gate stage emits `stage_started` only.
6. **`factory_sync.push_gate_request()` emits `gate_requested`** — only on the
   fresh insert, never on the idempotent no-op paths, and fail-open so a
   timeline hiccup can never read back as "gate push failed" → "not approved".
7. **`factory_sync.push_gate_decided()` emits `gate_decided`** — called from
   factory.py's `_record_gate_decision` for RESOLVED gates only
   (approved / no_go / REFUSED, never gate_pending), with the true
   `decided_at` when the caller knows it (tab rejections).
8. `audit/project_findings.py` and `ab_shadow_sync.py` emit `steering_event`
   rows (`ref.event_subtype` = `audit_finding`/`audit_verdict`/`ab_*`) —
   out-of-band CLIs, uuid5-idempotent.
9. **`flow_sync.py` emits typed information-flow mirrors** as `steering_event`
   rows with `ref.event_subtype='info_flow'`, `flow_type` in
   SPAWN/REPORT/ARTIFACT/ADDENDUM/HANDOFF/GATE/STEER/PROJECTION, and
   `confidence` in certain/inferred/heuristic. Ids are uuid5 over the canonical
   logical edge key; prompts/tool arguments/reasoning never enter the payload.

Still with **no emitter**: `alert` (nothing in the workflow layer produces an
alert-shaped fact today — the staleness sentinel / machine-health rules that
could honestly source it live outside the factory).

**Real source of truth:** `heartbeat/STATE.md` + zone boards + `ledger/*.jsonl` for the heartbeat-derived
kinds; for `agent_dispatched`, `factory_node_run.dispatch_node`'s own return value plus the
matched `tier2` ledger row. Information-flow rows additionally derive from
`ledger/agents/*.jsonl`, handoff files, `consumes:`/`corrects:` headers, report pointers,
and existing gate/event rows.

## `agent_dispatched` — why it is not `agent_spawned` (2026-07-28, `factory-observability-v1` A1a/B1)

`agent_spawned` is a *human's* interactive session that heartbeat noticed and the sync correlated
to a run. `agent_dispatched` is the factory itself spawning a headless sub-agent under a turn cap
and a dollar budget, with a model, a verdict and a real cost. Reusing one kind for both would
make them indistinguishable in the timeline, which is precisely the distinction that was asked
for. It is written on **every** outcome — success, non-zero exit, or exception — never only on
success, so a failed dispatch is visible rather than absent.

Its `detail` is the one populated `detail` in this table, and it is **allow-listed, not
passed through**: `factory_sync.SAFE_DISPATCH_DETAIL_KEYS` is the complete set of permitted keys
(`spec_slug`, `role_family`, `model`, `max_turns`, `budget_usd`, `cost_usd`, `verdict`,
`exit_code`, `final_response`, `usage`, `stderr_tail`, `ledger_session_id`), every surviving
string is then PII-scrubbed however deeply nested, and `final_response`/`stderr_tail` are capped.
Keys whose value is `None` are **dropped** rather than stored as JSON null — an absent key
renders as nothing in the UI, whereas `cost_usd: null` renders as an empty labelled field, and
absent is the truth when a ledger match failed.

`usage` is deliberately coarse (decision D2): `{turns_used, tokens_in, tokens_out,
cache_read_tokens, cache_creation_tokens}`, not a per-tool-call list. And **`turns_used` is
`None` when the provider supplied no per-iteration data** — it is never inferred from `max_turns`,
which would report every cheap 2-turn node as having burned its full 30-turn budget.

**Reproduce:**
```bash
cd factory-automation/factory && python3 -m pytest tests/test_node_dispatch_sync.py -q
```

## Columns

| column | type | constraints | notes |
|---|---|---|---|
| `id` | uuid | PK | |
| `run_id` | uuid | NOT NULL, FK → `factory_runs(id)` ON DELETE CASCADE | |
| `ts` | timestamptz | NOT NULL, default `now()` | |
| `kind` | text | NOT NULL, CHECK — see below | **12** allowed values as of 2026-07-28; 11 have a real emitter (2026-07-31 — `alert` is the one left dark) |
| `headline` | text | NOT NULL | one-line, human-readable |
| `ref` | jsonb | NOT NULL, default `{}` | structured reference (e.g. `{"agent_title": "..."}`, or `{"event_subtype": "run_queued", ...}`) |
| `detail` | jsonb | NOT NULL, default `{}` | allow-listed metadata/counts only; `info_flow` carries redacted evidence pointers + sync version |

The `kind` CHECK constraint has been widened twice by migration, so the value list is not
what the original `factory_observability` migration created:

| Added | Value | By |
|---|---|---|
| baseline | `stage_started`, `stage_passed`, `stage_failed`, `agent_spawned`, `agent_finished`, `gate_requested`, `gate_decided`, `steering_event`, `cost_recorded`, `alert` | initial `factory_*` migration |
| 2026-07-2x | `decision_recorded` | `factory-graph-pr2` |
| **2026-07-28** | **`agent_dispatched`** | `factory-observability-v1` (A1) — `DROP CONSTRAINT` + re-`ADD` with the full 12-value list, because Postgres has no "extend a CHECK" |

Anything writing a `kind` outside this list gets a constraint violation, by design: the timeline
is a closed vocabulary so the UI can map every value to a label and a colour.

## Known gaps / accepted risks

- **1 of 12 `kind` values still has no emitter: `alert`** (was 8 of 12 until the
  2026-07-31 stress-test EVENT-COVERAGE pass — see
  `stress-test-2026-07-31/findings/03-event-coverage.md` for the full
  kind-by-kind table and what each remaining gap would need as an honest source).
- `detail` is populated by `agent_dispatched` (allow-listed), the stage terminal
  events (counts only: `exported`, `nodes_total`/`nodes_ok`, `verifier_verdict`,
  `exit_code`, `dry_run`), `cost_recorded` (the projection's numeric fields), and
  `decision_recorded`; `{}` from the agent_spawned/finished and gate emitters.
- **A run-less agent session produces no event row at all.** `run_id` is `NOT NULL` here, so
  A4's machine-wide sessions (`factory_agents.run_id IS NULL`) are deliberately not represented
  in this table — there is no run whose timeline they belong on. The "My Sessions" tab therefore
  reads `factory_agents` directly and has no timeline. `flow_sync.py` reports the corresponding
  run-less flow edges as unsupported; `factory_handoffs.run_id` is also NOT NULL, so zero-DDL
  offers no honest storage target.
- **`ts` is true event time for the stage/gate/cost/audit kinds; sync-observation
  time for the heartbeat-derived kinds.** `stage_started` carries the start time
  factory.py stamps before dispatching the handler; `stage_passed`/`stage_failed`
  carry the report's `generated_at`; `gate_decided` carries `decided_at` when the
  caller knows it. `agent_spawned`/`agent_finished` still default to insert time —
  but since the 2026-07-31 idempotency fix that is *first-observation* time (later
  polls re-assert the row without touching `ts`), the closest to spawn time
  heartbeat can honestly support, since it records no session start timestamp.
- **Historical duplication (pre-2026-07-31 rows).** Before the deterministic ids,
  every agents_sync_job poll re-INSERTed `agent_spawned`/`agent_finished` for every
  active session — run `ac6a6078…` carries ~527 duplicate agent events (verified
  live). New polls no longer add to them; deleting the historical duplicates is a
  prod cleanup that needs Daniel's sign-off (SQL in the findings file above).

## Example rows

From `project_agents()`'s real event construction:

```json
[
  { "kind": "agent_spawned",  "headline": "corpus-planner-session-1 started",  "ref": {"agent_title": "corpus-planner-session-1"}, "detail": {} },
  { "kind": "agent_finished", "headline": "corpus-planner-session-1 finished", "ref": {"agent_title": "corpus-planner-session-1"}, "detail": {} }
]
```

And from `push_node_agent()`'s real `agent_dispatched` construction — note `detail` is the
allow-listed projection, and `turns_used` is absent rather than guessed when the provider gave no
per-iteration data:

```json
{
  "kind": "agent_dispatched",
  "headline": "corpus-sizer dispatched for S3 — ok",
  "ref": { "node_id": "corpus-sizer", "stage": "S3", "agent_title": "corpus-sizer" },
  "detail": {
    "spec_slug": "corpus-sizer", "role_family": "builder",
    "model": "claude-sonnet-4-6", "max_turns": 30, "budget_usd": 2.5,
    "cost_usd": 0.418, "verdict": "ok", "exit_code": 0,
    "final_response": "Sized all 6 families from measured evidence; see 06-project-report.json.",
    "usage": { "tokens_in": 184203, "tokens_out": 5120, "cache_read_tokens": 160000 }
  }
}
```
