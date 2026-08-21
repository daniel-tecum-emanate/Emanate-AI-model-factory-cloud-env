> **Provenance note, 2026-08-18:** originally written against a different repo
> (`daniel0tgc/internal-company-tool`) — mechanism-level content stands. Its `atlas-os/`
> fallback path (§ "Run `cs board`") does not exist in this repo
> (`emanate-tecum-workflow`); wiring this hook here still registers the sentinel claim,
> it just has no board to register against yet.

**Writer:** the lane that owns `cloud-sessions/bin/hooks/**`.
**Status:** STAGED — not wired. Daniel applies this by hand. · as_of 2026-08-17

# Wiring `session_register.sh` into `.claude/settings.json`

This is the copy-paste snippet that turns on session auto-registration: every
local Claude Code session appears on the heartbeat board (`cs board`) the
moment it starts, with no prompting and no protocol — the hook fires whether
or not the session cooperates.

## Why an agent cannot apply this, and why that is correct

`.claude/settings.json` is deny-listed for agents (Write/Edit/tee/cp/mv).
That file is the trust boundary: it defines the permission rules and hooks
that constrain agents, so an agent that could edit it could grant itself
anything. The deny-list is doing its job; this file stages the change so a
human applies it. Do not route around it (the same rule as I10 and `V-023`:
an enforcement gap is a measured defect, not permission).

## The snippet

Merge the two entries below into the `"hooks"` object of
`.claude/settings.json` at the repo root. If a `"hooks"` key or either event
array already exists, append to it rather than replacing it.

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"$CLAUDE_PROJECT_DIR/cloud-sessions/bin/hooks/session_register.sh\"",
            "timeout": 10
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"$CLAUDE_PROJECT_DIR/cloud-sessions/bin/hooks/session_register.sh\"",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

Both events matter: `SessionStart` catches the session the moment it opens;
`UserPromptSubmit` re-catches sessions that predate the wiring, sessions
resumed from disk, and rows that have gone stale (the hook refreshes
`last_update` at most every 10 minutes — the 30-minute lease never expires
under it while the session is actually in use).

## What changes for you

Nothing visible. The hook exits 0 on every path, writes nothing to the
terminal, and targets <300ms when the row already exists (measured 0.06s;
the one-time registration measured 0.22s). Sessions simply appear on `cs board`.
In a linked worktree the row registers on the main checkout's board — the
one board every reader looks at — labeled `worktree=linked:<name>`. On a
cloud VM the hook is a silent no-op: a row written there dies with the VM
and is invisible to every reader that matters, so it is skipped by design.

## How to verify it worked

1. Open a new Claude Code session anywhere in this repo.
2. Run `cs board` (landed 2026-08-17; fallback: read `atlas-os/heartbeat/STATE.md`).
3. See a row keyed on the session's id with goal
   `auto-registered: cwd=… worktree=… started=…` and owns
   `._presence/<session-id>`.
4. Failures, if any, are in `cloud-sessions/state/hook-errors.log`
   (append-only, capped at 200 lines). No row and an empty log means the
   hook never fired — re-check the wiring.

## How to back it out

Delete the two entries from `.claude/settings.json` (or just the
`session_register.sh` command objects, if other hooks share those arrays).
Already-registered rows age out on their own: the board lease marks them
STALE after 30 minutes, and `heartbeat.py reset` prunes ENDED rows. Nothing
else was touched — the hook holds no state beyond board rows and the error
log.

## The one thing this does NOT do

A presence row carries **no real owns-claims** — its `owns` is the sentinel
`._presence/<session-id>`, a path that does not exist, claimed only because
`register` requires `--owns` and per-session sentinel paths are disjoint by
construction. `cs exodus` partitions the shared dirty tree by the board's
owns-claims, so it still cannot partition a presence-only session's dirty
files until that session (or you, via `/cloud`) upgrades the claim to the
paths it actually owns. **Presence makes sessions VISIBLE; claims make them
MOVABLE.** The hook will never upgrade a claim itself, and it never touches
a row whose goal does not begin with `auto-registered:` — a deliberate
registration always wins over the machinery.
