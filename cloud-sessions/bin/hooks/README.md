# bin/hooks/

> **Provenance note, 2026-08-18:** originally written against a different repo
> (`daniel0tgc/internal-company-tool`) — mechanism-level content stands. `atlas-os/bin/**`,
> mentioned below, does not exist in this repo (`emanate-tecum-workflow`); per this file's
> own documented contract, the hook finds no `heartbeat.py`, logs the skip, and exits 0.

Claude Code hooks that belong to this folder. Two files, and they are different kinds of
thing:

| File | What it is |
|---|---|
| [`session_register.sh`](session_register.sh) | the hook itself — executable, runs on every session start and every prompt |
| [`WIRING.md`](WIRING.md) | the **staged** `.claude/settings.json` snippet that turns it on. A human applies it |

Nothing here is wired by default. A hook that is on disk but not in `settings.json` does
nothing at all, which is the state of this directory until Daniel applies
[`WIRING.md`](WIRING.md).

## Why the wiring is staged and not applied

`.claude/settings.json` is deny-listed for agents. That file *is* the trust boundary: it
defines the permission rules and hooks that constrain agents, so an agent able to edit it
could grant itself anything. An agent writing its own hook wiring is the same class of move
as an agent editing `atlas-os/bin/**` — an enforcement gap is a measured defect, never
permission (`V-023`, invariant I10). So the snippet lives in `WIRING.md`, copy-paste ready,
and a human merges it. The full snippet, both events, the verification steps and the
back-out are all in [`WIRING.md`](WIRING.md) — they are not repeated here.

## What `session_register.sh` does

Every local Claude Code session appears on the heartbeat board (`cs board`) the moment it
starts, with **no prompting and no protocol**. That is the entire point: asking a session
to register itself is a request an agent can forget, misread, or decide against. A hook
fires whether or not the session cooperates.

It registers through `atlas-os/bin/heartbeat.py` — `overlap`, then `register`/`update`. It
never hand-edits the board file, and it refreshes `last_update` at most every 10 minutes,
so a session in active use never goes stale under the 30-minute lease.

## The exit-0 contract, and why it is absolute

**`session_register.sh` exits 0 on every path.** Not a git repository, `heartbeat.py`
missing, `python3` missing, board locked, malformed stdin, empty stdin, no session id,
running on a cloud VM — all exit 0. It never uses `set -e` or `set -u`, because either one
turns an unset variable into a non-zero exit.

This is not defensiveness for its own sake. A `SessionStart`/`UserPromptSubmit` hook sits
between the human and their prompt: a hook that fails noisily blocks work, and a hook that
blocks holds the prompt hostage. **Observability must never break the observed system.** A
call to `heartbeat.py` therefore runs under a 6-second watchdog — `BoardLock` blocks
indefinitely on a held `flock`, and a hung board must not become a hung session.

Failure is not swallowed, it is *diverted*: every failed step appends one line to
`cloud-sessions/state/hook-errors.log`, capped at 200 lines by dropping the oldest. That
log is where you look when a session did not appear. **No row and an empty log means the
hook never fired** — re-check the wiring, because "no error" and "did not run" look
identical from the board.

Performance is part of the contract: the common case (row already exists) is a read-only
`awk` over the board file *before* any Python is spawned, targeting <300ms. Measured 0.06s
for the refresh path and 0.22s for a one-time registration.

## Where it deliberately does nothing

- **On a cloud VM it is a silent no-op.** Detected three ways: running as root, a `cwd`
  under `/home/user`, or any `CLAUDE_CODE_REMOTE*` variable in the environment. A board row
  written on a VM lives in the VM's clone, is never pushed, and dies with the machine — so
  it would be invisible to every reader that matters. Cloud work is tracked by the dispatch
  ledger and, while it runs, by [`../cloud-heartbeat.sh`](../cloud-heartbeat.sh) and
  `cs fleet` — see [`../../playbooks/10-fleet.md`](../../playbooks/10-fleet.md).
- **In a linked worktree** it registers on the **main checkout's** board — the one board
  everyone reads — and labels the row `worktree=linked:<name>`.
- **Copied out of place** it refuses rather than guessing: it derives the repo root by
  stripping `/cloud-sessions/bin/hooks` from its own directory, and if that does not match,
  it returns without writing.
- **It never touches a deliberate registration.** A row whose goal does not begin with
  `auto-registered:` is left alone. A real claim always wins over the machinery.

## What it does NOT do — presence is not a claim

A presence row carries **no real owns-claims**. Its `owns` is the sentinel
`._presence/<session-id>`: a path that does not exist, claimed only because `heartbeat.py
register` requires `--owns`, and per-session sentinel paths are disjoint by construction so
they can never collide with a real claim.

That matters because `cs exodus` partitions a shared dirty tree by the board's owns-claims.
A sentinel covers no real file, so a presence-only session's dirty files **cannot be moved**
— the session is visible and immovable at the same time.

> **Presence makes a session VISIBLE. `cs claim` is what makes it MOVABLE.**

The hook will never upgrade a claim itself; inferring what a session owns from its
transcript is `cs claim`'s job, and it is deliberately a separate, deliberate act. See
[`../../playbooks/08-concurrent-sessions-and-exodus.md`](../../playbooks/08-concurrent-sessions-and-exodus.md).

## Dependency, and how it degrades

`heartbeat.py` lives in `atlas-os/bin/`, outside this folder. When this folder is ported to
a repo without `atlas-os/`, the hook finds no `heartbeat.py`, logs it, and exits 0 — so
sessions simply never appear on a board that does not exist. That is a **silent** loss of a
feature rather than a loud refusal, and it is called out as such in
[`../../playbooks/09-porting-to-another-repo.md`](../../playbooks/09-porting-to-another-repo.md).

## Adding another hook here

`bin/hooks/` is for hooks this folder owns. Anything new needs: the same exit-0 contract, a
staged snippet in `WIRING.md` rather than an applied edit, a row in the table at the top of
this file, and — if it makes a claim about what it measures — its evidence in
[`../../reference/verified-facts.md`](../../archive/foreign-repo-provenance-2026-08-18/verified-facts.md).
