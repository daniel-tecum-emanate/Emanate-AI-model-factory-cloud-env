# `factory_gate_decisions`

**Append-only** audit trail of every gate approve/reject call — the SOC 2 artifact. This
table exists specifically so "who approved this spend, and when" can never be edited or
deleted after the fact, by anyone, including the service role.

**Populated by:** the `decide_factory_gate(p_gate_request_id, p_decision, p_approver_email,
p_note)` Postgres RPC only — there is no direct table INSERT path from application code.
Called from platform-alpha's `decideGate` server action when an internal user clicks
Approve/Reject on a Gate Card.
**Real source of truth:** this table *is* the source of truth — unlike every other table in
this folder, it has no upstream file it's projecting from. The approver's email/decision/
note are entered live, in the UI, by a human.

## Columns

| column | type | constraints | notes |
|---|---|---|---|
| `id` | uuid | PK | |
| `gate_request_id` | uuid | NOT NULL, FK → `factory_gate_requests(id)` | |
| `decision` | text | NOT NULL, IN `('approve','reject')` | |
| `decided_by_email` | text | NOT NULL | the approving internal user's email, passed by the server action after its own `isInternalEmanateUser` check |
| `note` | text | nullable | optional free-text from the approver |
| `decided_at` | timestamptz | NOT NULL, default `now()` | |

## Why this table is different from the other 8

**No `UPDATE`/`DELETE` grant to anyone — including `service_role`.** Every other table in
this migration grants `service_role` `ALL` (it's the only role that ever writes any of
these tables). This one grants `service_role` `SELECT, INSERT` only, via an *explicit*
`REVOKE ALL ... FROM anon, authenticated, service_role` before the narrower grant. The one
write path that exists — `decide_factory_gate()` — is `SECURITY DEFINER` (runs as the
function owner, not the caller), so it's unaffected by that revoke; only the *caller role's*
direct table-level mutation ability is removed, which is the entire point.

This is the fix for a real P0 finding from the 2026-07-23 rigorous QA pass (`DELTAS.md`
D18): this Supabase project's schema-wide `ALTER DEFAULT PRIVILEGES` baseline grants
`service_role` `ALL` on every new table the instant it's created, so a narrower `GRANT
SELECT, INSERT` alone is a no-op — a `GRANT` is additive and never revokes what the default
already conferred. The explicit `REVOKE ALL` is what actually closes the gap. Verified live
by attempting the exploit (denied) — see `PRs/archive/model-factory-v1/PRDebug.md`.

## The RPC's atomicity

`decide_factory_gate` does a row-locked (`FOR UPDATE`) check-then-write in one transaction,
so two approvers clicking Approve/Reject within the same request window can't both write a
decision — the second call sees `status <> 'pending'` and returns the *existing* (first,
real) decision instead of writing a second row, with `outcome='already_resolved'` so the UI
can render the actual approver rather than the caller's own attempted values (`DELTAS.md`
D2/D11).

## Known gaps / accepted risks

None open — this table was the subject of a P0 fix, not an accepted gap. See `PRDone.md`'s
task log for 2026-07-23 for the full verification trail.

## Example row

```json
{
  "decision": "approve",
  "decided_by_email": "daniel@emanate.ai",
  "note": null,
  "decided_at": "2026-07-23T22:15:04Z"
}
```
