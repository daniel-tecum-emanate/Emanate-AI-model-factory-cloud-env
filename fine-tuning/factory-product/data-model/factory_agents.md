# `factory_agents`

One row per contributing agent session on a run — the Fleet tab's data source. This is the
table with the most projection gaps of the 9, because its two real sources
(`heartbeat/STATE.md`, `ledger/*.jsonl`) were never designed with a `factory_agents` shape
in mind — they're the machine-wide session/tool-call logs every agent session writes to,
for every project, not just Model Factory runs.

**Populated by:** `factory_sync.project_agents()` → `push_agents()` —
`fine-tuning/factory-product/factory_sync.py`. **Not wired into any `factory.py` stage
handler** — `heartbeat`/`ledger` are updated by Claude Code/Cursor hooks, not by
`factory.py` itself, so there's no stage transition to hang this push on.

**2026-07-24 (`DELTAS.md` D20): now also called by a scheduled poll**,
`fine-tuning/factory-product/agents_sync_job.py` (wrapped by
`scripts/factory_agents_sync.sh`, Tier 0/$0, every 5 min) — this is exactly the "future
scheduled sync job" this section used to say didn't exist yet. It polls every run this
factory has a local record of (`runs/*/.sync_state.json`) and pushes agent/event state for
any whose `factory_runs.status` is `running`/`blocked_on_gate`; a finished run's directory
never generates new activity again. **Run-scoped, not machine-wide** — a Cursor/Claude Code
session working on an unrelated project is never synced, only sessions
`_session_matches_run()` (the same org_slug text match `project_agents()` always used) can
match to an open run. Code + 7 new tests written and verified live against local Supabase.
**[Corrected 2026-08-04: this section previously said the job was "not yet actually
running" pending PROCESSES.md registration. The job IS live — registered in `PROCESSES.md`
and activated 2026-07-31 as `com.tecum.workflow.factory-agents-sync` (launchd, every 5 min,
Tier 0/$0) through the standard I10 path; the wrapper also runs `flow_sync.py --live` after
agent rows succeed. D20's sign-off condition was satisfied by Daniel's 2026-07-31
stress-test instruction.]
**Real source of truth:** `heartbeat/STATE.md`'s `## Active Sessions` table, cross-referenced
with `ledger/<date>.jsonl`'s `tool` events.

## 2026-07-28 (`factory-observability-v1`): two more write paths, so a row now has three possible origins

This table used to have exactly one writer. It now has three (four as of 2026-07-29 — next
section), and telling them apart matters because they populate different columns:

| Origin | Writer | `run_id` | `stage` | `cost_usd` | `files_touched` / `commands_run` |
|---|---|---|---|---|---|
| Interactive session on an open run | `push_agents` (unchanged) | set | NULL | NULL | from ledger `tool` events, when an overlay supplies a `session_id` |
| **Dispatched node-agent** (B1) | `factory_sync.push_node_agent`, called from `factory_node_run.dispatch_node`'s `finally` | set | **set** | from the tier2 ledger match | **real** — correlated by the `session_id` on the matched tier2 row |
| **Machine-wide session** (A4) | `factory_sync.push_machine_sessions`, called from `agents_sync_job` | **NULL** | NULL | NULL | as the interactive path |

## 2026-07-29 (`factory-cursor-bridge-v1` C2/C3): the fourth writer — Cursor-SDK dispatch

A spec whose `runtime` block says `executor: cursor_sdk` (opt-in, D1 — zero specs flipped
so far) dispatches through `factory_cursor_dispatch.py` instead of `tier2_run.sh`, and its
row is written **twice by design**:

| Origin | Writer | `run_id` | `stage` | `status` | `cursor_*` columns |
|---|---|---|---|---|---|
| **Cursor dispatch, mid-run** | `factory_sync.push_node_agent_started`, called right after `agent.send()` while the dispatch is still blocked in the SDK's `wait()` | set | set | `active` | **set** |
| **Cursor dispatch, finished** | the same `push_node_agent` `finally` path as B1 | set | set | `ended` — or **`cancelled`** when the result carries `stopped: True` (an operator's Stop/Pause) | **set** |

Both pushes share the `(run_id, title)` upsert identity, so the second UPDATEs the first
rather than duplicating it. The mid-run `active` row is load-bearing, not cosmetic: the
product's Stop/Pause buttons render only on an `active` row with a non-null
`cursor_agent_id`, and without this push there would be nothing to click while the agent
runs. The finished push deliberately **never writes `resumable`** — the
`request_factory_agent_control` RPC (Half A) owns that column (it is how Pause and Stop
are told apart), and a PATCH including it would overwrite the operator's verdict.

A Cursor-dispatched row has **no tier2 ledger row** (nothing runs through `tier2_run.sh`),
so `cost_usd`/`ledger_session_id`/`files_touched` stay NULL/`[]` — honest, not a bug.

**Reproduce:**
```bash
cd factory-automation/factory && python3 -m pytest tests/test_cursor_dispatch.py tests/test_factory_sync_cursor_fields.py -q
```

**All three paths aggregate tool calls through the same `_aggregate_ledger` + `_redact_path`**, so
there is exactly one redaction rule for `files_touched` rather than one per writer. A dispatched
agent gets its `session_id` for free from the tier2 row the cost match already found; when that match
fails there is no session id, so `files_touched`/`commands_run` degrade to `[]`/`0` rather than
picking up a *different* session's tool calls.

**`stage` non-null is the discriminator between a dispatched node-agent and an interactive
session**, and it is a property of the writers rather than a UI convention: `push_node_agent`
always sets it (it dispatches *for* a stage), and neither heartbeat-sourced path has a stage to
set. The product relies on this (`AgentsTable.tsx`'s `isDispatched`). Do not start writing
`stage` from a heartbeat session without changing that read too.

**A4's machine-wide rows partition with the run-scoped ones; they do not overlap.**
`push_machine_sessions` skips any session an open run already claimed
(`factory_sync.sessions_claimed_by`), so one session is stored either with a run or without one,
never twice. It also emits **no** `factory_run_events` rows — `run_id` there is `NOT NULL`, so a
run-less session has no representable timeline row.

**Reproduce:**
```bash
cd factory-automation/factory && python3 -m pytest tests/test_node_dispatch_sync.py tests/test_machine_sessions.py -q
```

## Columns

| column | type | constraints | notes |
|---|---|---|---|
| `id` | uuid | PK | |
| `run_id` | uuid | FK → `factory_runs(id)` ON DELETE CASCADE, nullable | |
| `stage` | text | nullable | **set by `push_node_agent` (B1)** — the stage code the node was dispatched for. NULL for both heartbeat-sourced paths; this is the dispatched-vs-interactive discriminator |
| `role` | text | NOT NULL, no CHECK constraint | best-effort, via `RISK_TO_ROLE` map — see below |
| `title` | text | NOT NULL | the heartbeat session's own label |
| `goal` | text | NOT NULL | heartbeat's `goal` column, verbatim |
| `status` | text | NOT NULL, default `active`, IN `('active','ended','stale','cancelled')` | `ended` iff heartbeat's status is `ENDED`; `stale` never derived yet. **`cancelled` added 2026-07-29 (`factory-cursor-bridge-v1` A1)**: an operator Stopped or Paused a Cursor-dispatched agent — the two are told apart by `resumable`, not by a fifth status |
| `risk` | text | nullable, IN `('read-only','code','migration')` | heartbeat's own `risk` column, passed through if it matches |
| `allowed_paths` | jsonb | NOT NULL, default `[]` | heartbeat's comma-separated `allowed_paths`, split into an array |
| `files_touched` | jsonb | NOT NULL, default `[]` | from ledger `tool` events, **redacted** — see below. Real for dispatched node-agents too as of 2026-07-28 (B1) |
| `commands_run` | int | NOT NULL, default `0` | count of ledger `tool` events for this session. **0 is a meaningful value** (an agent that touched nothing) and the UI renders it as `0`, unlike `cost_usd` where NULL renders as `—` |
| `cost_usd` | numeric | nullable | **populated for dispatched node-agents (B1)** from the matched `tier2` ledger row. Still NULL for every heartbeat-sourced row — see gaps. NULL on a failed ledger match, **never 0** |
| `ledger_session_id` | text | nullable | dispatched rows: the real Claude Code `session_id` from the matched ledger row. Heartbeat rows: only via an explicit overlay map (see below) |
| `final_response` | text | nullable | dispatched rows: the agent's own closing text, capped. Heartbeat rows: only via an explicit overlay map |
| `report_path` / `report_summary` | text | nullable | **populated for dispatched node-agents (B1)**: the repo-relative prompt file, and the capped final response. NULL for heartbeat-sourced rows |
| `started_at` / `last_seen_at` / `ended_at` | timestamptz | NOT NULL/nullable | DB-managed defaults |
| `resumable` | boolean | nullable | **added 2026-07-29 (`factory-cursor-bridge-v1` A1).** Meaningful only when `status = 'cancelled'`: `true` = Pause (product offers Resume/Send-message), `false`/NULL = Stop (terminal). **Written ONLY by the `request_factory_agent_control` RPC** — no sync path touches it, deliberately |
| `cursor_agent_id` | text | nullable | **added 2026-07-29 (A1), written by the Cursor-dispatch paths (C2/C3).** The control boundary: NULL (every interactive, tier2 and pre-PR-B row) = never controllable from the product, full stop |
| `cursor_run_id` | text | nullable | the SDK run id of the dispatch's current/last run — what `run.cancel()` targets |
| `cursor_runtime` | text | nullable, IN `('local','cloud')` | where the Cursor agent executes; `cloud` is D2's default |

`role` derivation (`RISK_TO_ROLE`): heartbeat's `risk` column (`migration`/`code` →
`builder`, `read-only` → `reader`) maps to a coarse role; anything else defaults to
`contributor`. The richer role taxonomy `PLAN.md` describes (coordinator/builder/reader/
expert-lens/...) lives in the AgentSpec catalog (`factory_specs`), a *separate* system with
no live join to a running heartbeat session today — this is a deliberate, documented
simplification, not a bug (`DELTAS.md` D16).

## The two explicit overlays

`project_agents()` takes two optional dicts that are **never parsed automatically**:

- `session_id_map` (heartbeat session label → ledger `session_id`) — heartbeat rows carry
  no ledger session id today; without this map, every agent projects with
  `ledger_session_id: None`.
- `final_responses` (heartbeat session label → closing headline text) — ledger's
  `session_end` events carry no closing-response text field; without this map,
  `final_response` is unset.

A caller with neither map gets the honest current state (no ledger correlation, no closing
text), not a fabricated one — this is exercised directly by `factory_sync`'s own
untracked-agent test.

## Path redaction (`files_touched`)

Every path is passed through `_redact_path()`: normalized (`posixpath.normpath`, closing a
real `../`-traversal bypass found in QA — `DELTAS.md` D19), then checked against an
allow-list of internal, non-customer-identifying repo-relative prefixes
(`SAFE_PATH_PREFIXES` — `factory-automation/`, `PRs/`, `heartbeat/`, `ledger/`, `handoffs/`,
`memory/`, `scripts/`, `coding/`, `company-brain/3-execution/`, `supabase/`,
`agent-infrastructure-sweep/`, `.claude/`) plus a boundary-aware regex for
`platform-alpha`-prefixed worktree names. Anything else is redacted to
`[external path — redacted]` — an allow-list, not a name-list, because there is no
canonical customer-name registry to check paths against.

## Known gaps / accepted risks

- `cost_usd` has no data source **for a heartbeat-sourced session** (interactive or
  machine-wide) — but **not for the reason this file used to give.** The old text said ledger's
  `tier1`/`tier2` rows "carry no `session_id`"; re-read live 2026-07-28, that is wrong twice over.
  `tier2` rows (`scripts/tier2_run.sh:154-159`) carry `label`, `cost_usd` **and** `session_id`;
  all 135 real ones in `ledger/` do. `tier1` rows (`scripts/llm_local.sh:96-99`) carry neither
  `cost_usd` nor `session_id`, and correctly so — Tier 1 is local Ollama at $0, so there is no
  cost to join. The actual blocker is on the **heartbeat** side: a heartbeat session row has no
  ledger `session_id` column, so there is no key to join *from*. Deliberately left unset rather
  than guessed (`DELTAS.md` D16). **Dispatched node-agents are the exception** and
  now do get a real cost (B1): they are matched to their `tier2` row by the `--label
  factory-node-<node_id>` that `build_tier2_argv` sets, within the dispatch's own wall-clock
  window. The match is deliberately conservative — no ledger file, no matching label, a match
  outside the window, or **two** candidate rows all resolve to `cost_usd: None` rather than to a
  guess, because a wrong cost on a spend-approval surface is worse than an absent one.
- `_redact_path` cannot mechanically tell a legitimate `platform-alpha-<slug>` worktree
  apart from a directory deliberately named to look like one (e.g.
  `platform-alpha-jane-doe-account-export/`) — both match the same shape. Accepted residual
  risk, not fixed (`DELTAS.md` D19/F7) — closing it fully would need a real worktree-name
  registry cross-check, a bigger design change than this bounded fix.
- ~~`stage`, `report_path`, `report_summary`, `ended_at` are real columns with no current
  write path.~~ **Closed 2026-07-28 by `factory-observability-v1` B1** — all four are written for
  dispatched node-agents. They remain unwritten for heartbeat-sourced rows, which is correct
  rather than a residual gap: an interactive session has no stage, no report file and no
  reliable end time.
- `stale` is still never derived. A dispatched row is born `ended` (dispatch is synchronous), and
  a heartbeat row is `active`/`ended` from heartbeat's own status column — nothing computes
  "went quiet without ending".

## Example row

```json
{
  "role": "builder",
  "title": "corpus-planner-session-1",
  "goal": "size every family's dose from measured evidence for qa-dryrun-synth",
  "status": "active",
  "risk": "code",
  "allowed_paths": ["fine-tuning/factory-product/runs/qa-dryrun-synth/"],
  "files_touched": ["fine-tuning/factory-product/runs/qa-dryrun-synth/06-project-report.json"],
  "commands_run": 4,
  "ledger_session_id": null,
  "final_response": null
}
```
