# `factory_handoffs`

Information transferred session → session within a run.

**Populated by:** `fine-tuning/factory-product/flow_sync.py`, dry-run by default and
`--live` for uuid5-idempotent upserts. The scheduled five-minute agents poll invokes the
live pass only after agent rows have synced, so current `factory_agents.id` values exist.
**Real sources of truth:** root + zone heartbeat boards, recent action/agent ledgers,
`handoffs/<slug>/{current,NEXT-PROMPT}.md`, explicit `consumes:` / `corrects:` headers,
and existing `factory_agents.report_path` pointers. Only run-scoped agent-pair flows with
both endpoint ids resolved land here.

## Columns

| column | type | constraints | notes |
|---|---|---|---|
| `id` | uuid | PK | |
| `run_id` | uuid | NOT NULL, FK → `factory_runs(id)` ON DELETE CASCADE | |
| `from_agent_id` | uuid | FK → `factory_agents(id)`, nullable | |
| `to_agent_id` | uuid | FK → `factory_agents(id)`, nullable | |
| `artifact` | text | NOT NULL | e.g. the handoff file's path, or an artifact name |
| `summary` | text | NOT NULL | the handoff's own summary text |
| `created_at` | timestamptz | NOT NULL, default `now()` | |

## Known gaps / accepted risks

- `run_id` is NOT NULL. Machine-wide/run-less flows cannot be written here (or to
  `factory_run_events`) without DDL; `flow_sync.py` reports them as unsupported and never
  invents a placeholder run.
- The table has no `kind`/`confidence` columns. PR-A therefore encodes the stable,
  parseable envelope `type=…; confidence=…; evidence=…; sync_version=…` in `summary`.
  This is metadata built from redacted pointers, not a handoff/report body.
- Every pointer and metadata string passes through `scripts/lib/redact.py`; raw prompts,
  tool arguments/results, chain of thought, and report bodies are never projected.

## Example row

```json
{
  "artifact": "handoffs/factory-graph-v2/NEXT-PROMPT.md",
  "summary": "type=HANDOFF; confidence=certain; evidence=handoffs/factory-graph-v2/NEXT-PROMPT.md#authored-by,consumes:handoffs/factory-graph-v2/NEXT-PROMPT.md; sync_version=1"
}
```
