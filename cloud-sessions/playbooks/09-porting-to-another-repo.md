# 09 — Porting this folder to another repository

> **This is the exact playbook that governed the 2026-08-18 cleanup of this folder in
> `emanate-tecum-workflow` itself** — this repo received `cloud-sessions/` as a bulk commit
> from `daniel0tgc/internal-company-tool` (commit `7a5690b`) without this checklist ever
> being run. Step 4 below is what T0-1 of
> `PRs/model-factory-finetune-launcher-v1/PRContext.md` executed. This file's own content
> (the audit measurements, the checklist) is mechanism-level and stands as-is.

**Status:** ACTIVE · as_of 2026-08-17. Built from the porting audit (moved to
[`../archive/foreign-repo-provenance-2026-08-18/porting-audit-2026-08-17.md`](../archive/foreign-repo-provenance-2026-08-18/porting-audit-2026-08-17.md) —
performed against `daniel0tgc/internal-company-tool`, the origin repo, dated evidence not
rewritten as this repo's own), which performed the naive port into a throwaway repo and
measured what happened. Every "breaks" claim below is `VERIFIED` there; every "should work"
is tagged where it is not.

Previous: [`08-concurrent-sessions-and-exodus.md`](08-concurrent-sessions-and-exodus.md).

---

## What actually ports

The audit copied `cloud-sessions/` into a bare repo with **no `atlas-os/`, no `.claude/`**
and ran everything safe. Result:

| | |
|---|---|
| **Works anywhere** | `new` `start` `handoff` `send` `ls` `track` `rm` `env` `doctor` `sessions` `shell-init`, the whole test suite (**467 green inside the transplant**), `doc-drift.sh` |
| **Refuses loudly without `atlas-os/`** | `cs board` (exit 1, `DEGRADED … PARTIAL, not empty`), `cs exodus` (exit 1, names the missing `heartbeat.py`), `session_register.sh` (exit 0 by contract, logs the skip) |
| **Breaks silently — fix before porting** | the travelling ledger, the symlink install, the hardcoded repo/account literals, and four more (all fixed as of `b229c88`, except where noted below) |

**A green suite in a port measures only the portable subset.** `tests/run.sh` has zero
checks touching `board`, `exodus` or `sessions` — so `467 passed` in a transplant is real
*and* says nothing about the three commands that couple to `atlas-os`. Ground rule 7
applies to the suite itself.

## The three decisions, before you copy anything

**1. Does the new repo get a heartbeat board?**
No → `cs board`, `cs exodus`, auto-registration and `/cloud all` are dead (loudly).
Single-session `/cloud` still works end to end. **Delete the WIRING.md snippet from the
port**, or a wired-but-boardless hook logs a skip for every session forever.
Yes → `atlas-os/bin/heartbeat.py`, `atlas-os/bin/worktree.py` and the board come along,
and you now have a second surface to keep in sync.

**2. What replaces the guarded-path clauses?**
The handoff brief's telemetry clause, `/cloud-workflow`'s "never write `atlas-os/bin/**`",
and the cannot-move list's rows (Twenty, Supabase MCP, meeting-recorder) all name *this*
repo's protected things. Name the new repo's equivalents or delete the clauses —
**shipping instructions about directories that don't exist teaches sessions to ignore
instructions.**

**3. Where do briefs live?**
`docs/handoff-<lane>.md` and `docs/workflows/*.md` are baked into `cs exodus` manifests,
`/cloud` step 4 and `/cloud-workflow` step 1. Adopt `docs/` in the new repo or change all
three together — a partial change strands briefs where the routine prompt will not look.

## The checklist

1. **Copy** `cloud-sessions/` to `<new-root>/cloud-sessions`. **Placement is
   load-bearing**: nested (e.g. `tools/cloud-sessions/`) makes `cs board` answer for the
   wrong root and makes the hook write a stray `cloud-sessions/state/` at the repo root.
2. **Truncate the ledger** — `: > cloud-sessions/state/sessions.jsonl`. It is tracked on
   purpose, so a clone carries this repo's rows. `cs ls` must answer
   `no dispatches recorded`. Since `b229c88` a foreign row is refused rather than
   silently targeted, but an inherited ledger is still noise you do not want.
3. **Copy from outside the folder**: `.claude/commands/cloud.md`,
   `.claude/commands/cloud-workflow.md`, `.claude/commands/routines-audit.md`,
   `docs/workflows/TEMPLATE.md`. Add the heartbeat files only if decision 1 was yes.
4. **Substitute every literal.** This must come back empty of anything not clearly
   labelled as history:
   ```bash
   grep -rn "daniel0tgc\|internal-company-tool\|env_01Xmz\|-Users-danieltecum" \
     cloud-sessions .claude/commands docs/workflows
   ```
5. **Run the suite** — `bash cloud-sessions/tests/run.sh`. Green, knowing what green
   covers.
6. **Install `cs` by wrapper, never by symlink.** `cs` derives its root from
   `BASH_SOURCE` without resolving links, so a symlink in `~/.local/bin` makes it read an
   empty ledger and report `no dispatches recorded` with exit 0. Use:
   ```bash
   printf '#!/bin/bash\nexec "%s/cloud-sessions/bin/cs" "$@"\n' "<new-repo-root>" \
     > ~/.local/bin/cs && chmod +x ~/.local/bin/cs
   ```
   Do **not** add `cloud-sessions/bin` to `PATH` — a repo directory early in `PATH` lets
   anything that can commit shadow a system command on your laptop.
7. **`cs doctor` from the repo root**, in a real terminal, until it says `ready.` Open the
   repo once interactively to clear the trust prompt, or the first dispatch stalls
   silently on it.
8. **Web side:** confirm the Claude GitHub app covers the new repo — a routine-create 403
   is the only tell. Then `cs env set <id>` **from the repo root**; never a bridge env.
9. **If heartbeat=yes:** apply the WIRING.md snippet by hand and run its "How to verify"
   section. The `hook-errors.log` check is the only thing that catches a dead hook.
10. **First dispatch: one read-only probe.** `cs track` it with full provenance and read
    the transcript. A green routine is a session that started, not a task that
    succeeded.

## What no port has done yet

**A live round-trip.** The audit was offline by construction — it never dispatched from a
ported repo. Step 10 is therefore `UNVERIFIED` as a whole: the pieces are each verified
here, the sequence has never been run in a foreign repo. Treat the first port's step 10 as
the experiment it is, and write down what happened.

## Related

- [`../archive/foreign-repo-provenance-2026-08-18/porting-audit-2026-08-17.md`](../archive/foreign-repo-provenance-2026-08-18/porting-audit-2026-08-17.md) —
  the measurements, the coupling inventory, and the full silent-break list (archived
  2026-08-18 — dated evidence against the origin repo, not this one).
- [`../AGENT.md`](../AGENT.md) — the self-containment claim, superseded 2026-08-17.
- [`08-concurrent-sessions-and-exodus.md`](08-concurrent-sessions-and-exodus.md) — what
  you are porting, if decision 1 is yes.
