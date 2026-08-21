# Cloud VM Probe 6 — 2026-08-14

> **Snapshot, not current state.** Probe 6 of seven, at `d63ac00`. Its headline — hooks fire
> in a cloud session but the record dies with the VM — still stands. **What changed is the
> response:** the first fix (telling sessions to commit their own telemetry) shipped and was
> reverted within the hour, and sessions are now told *not* to. The reasoning, and what was
> adopted instead, is in
> [`verified-facts.md`](verified-facts.md#the-first-fix-was-wrong-and-was-reverted-the-same-hour--superseded-2026-08-14).
> All seven probes: [`README.md`](README.md#the-probe-reports--primary-evidence-not-current-state).

Run from inside a Claude Code cloud session on this checkout, at commit `d63ac00`
(`Add cs new: choose local or cloud at the front door`). Measurement only; nothing
fixed. Session/run id for this probe: `981665d0-fdba-56fa-b664-1d5fae1744f3`.

## Verdict table

| # | Question | Verdict |
|---|---|---|
| 1a | Do hooks fire in this cloud session? | **YES** — `ledger_hook.py` fired on SessionStart, UserPromptSubmit, PreToolUse (x21), PostToolUse (x19) for this exact session id |
| 1b | Does this session's telemetry SURVIVE the VM being reclaimed? | **NO** — `raw/` is gitignored; promotion to the committed `runs/` copy is manual-only (`promote.py`) and nothing in this repo calls it automatically. Unless a human runs `promote.py` and commits before this VM is reclaimed, every line from this session is lost. |
| 1c | Hook exits 0 on every path? | YES — manual invocation confirmed |
| 1d | Any hook errors this session? | NO — `hook-errors.log` does not exist |
| 2 | `cs new` chooser refusal paths correct on this platform? | YES — all 5 checks passed |
| 3 | Full test suites green here? | YES — cloud-sessions 240/0/2skip (exit 0), heartbeat 90/0/0skip (exit 0, 5 known defects reported), worktree 53/0/0skip (exit 0) |
| 4 | `/cloud` slash command present and would this session see it? | YES, present; YES, this session sees it (it appears in this session's own skill list as `cloud`); but it is **not usable to dispatch** from here — it can only prep a branch/brief for a human to paste into a real terminal |

**The headline finding is 1b.** The hook mechanism works correctly in a cloud session — this
run proves it end to end, with matching session ids in the ledger. But "recorded" and
"survives" are different claims, and only the first is currently true here.

---

## Test 1 — is this session being logged?

### Directory and today's file exist

```
$ ls -la atlas-os/telemetry/raw/
total 24
drwxr-xr-x 3 root root 4096 Aug 14 20:01 .
drwxr-xr-x 7 root root 4096 Aug 14 20:01 ..
drwxr-xr-x 2 root root 4096 Aug 14 20:01 .state
-rw------- 1 root root 7112 Aug 14 20:01 2026-08-14.jsonl
-rw-r--r-- 1 root root 3160 Aug 14 20:01 README.md
```

`atlas-os/telemetry/raw/2026-08-14.jsonl` exists (UTC date). 11 lines at time of writing
(more will land as this probe continues and on Stop/SessionEnd).

### Lines correspond to THIS session

```
$ python3 -c "... parse and summarize ..."
kinds: {'session_start': 1, 'prompt': 1, 'tool_call': 21, 'tool_result': 19}
hooks: {'SessionStart': 1, 'UserPromptSubmit': 1, 'PreToolUse': 21, 'PostToolUse': 19}
session_ids seen: {'981665d0-fdba-56fa-b664-1d5fae1744f3'}
```

Every line's `session_id` is `981665d0-fdba-56fa-b664-1d5fae1744f3` — this probe's own
session id (matches the scratchpad path assigned to this session, and the `transcript_path`
below). `Stop` and `SessionEnd` have not fired yet at time of writing because the session is
still running; that is expected, not a gap.

First line, the `session_start` row, redacted-by-design but shows env fingerprinting works:

```json
{"v":2,"ts":"2026-08-14T20:01:32.688381Z","run_id":"981665d0-fdba-56fa-b664-1d5fae1744f3","session_id":"981665d0-fdba-56fa-b664-1d5fae1744f3","seq":1,"kind":"session_start","actor":"agent","tool":null,"paths":[],"input":{"source":"startup","cwd":"/home/user/internal-company-tool","model":null,"permission_mode":null,"agent_type":null},"output_bytes":null,"ok":true,"redacted":false,"hook":"SessionStart","output_head":null,"error":null,"transcript_path":"/root/.claude/projects/-home-user-internal-company-tool/981665d0-fdba-56fa-b664-1d5fae1744f3.jsonl","env":{"repo_root":"/home/user/internal-company-tool","git_commit":"d63ac000fc7326e9b2c13aa348e9baece592530f","git_branch":"HEAD","git_dirty":false,"git_changed_files":0,"os":"Linux","os_release":"6.18.5-fc-v20","arch":"x86_64","claude_version":"2.1.231 (Claude Code)","python_version":"3.11.15","probe_ms":875,"settings_sha256":"8b329e61c818b38cecc8f8d362019ebf06786f5327f7bf930a555a4fc509a522"}}
```

A later `tool_result` row showing redaction working (a high-entropy git commit SHA output
got redacted):

```json
{"v":2,"ts":"2026-08-14T20:01:46.056966Z","run_id":"981665d0-...","session_id":"981665d0-...","seq":10,"kind":"tool_result","actor":"deterministic","tool":"Bash","paths":[],"input":{"tool_use_id":"toolu_01NGwxiF16jcgFcH9qq8Tyax","duration_ms":342},"output_bytes":195,"ok":true,"redacted":true,"hook":"PostToolUse","output_head":"{\"stdout\": \"---\\n.claude/commands/cloud.md\\n.claude/settings.json\\n---\\[REDACTED:HIGH-ENTROPY]\", ...}"}
```

### Hook errors

```
$ ls -la atlas-os/telemetry/raw/hook-errors.log
ls: cannot access 'atlas-os/telemetry/raw/hook-errors.log': No such file or directory
```

File does not exist. No hook failures diverted here this session — the good outcome, not
the "empty ledger + populated error log" failure mode the task asked me to watch for.

### Manual hook invocation

```
$ echo '{}' | python3 atlas-os/bin/ledger_hook.py; echo "exit=$?"
exit=0
```

Confirms the hook exits 0 even on a minimal/edge input, as designed.

### Does anything get written to git during this session?

```
$ git status --short atlas-os/telemetry/
(empty output)

$ git check-ignore -v atlas-os/telemetry/raw/2026-08-14.jsonl
.gitignore:11:atlas-os/telemetry/raw/*    atlas-os/telemetry/raw/2026-08-14.jsonl

$ git ls-files atlas-os/telemetry/raw/
atlas-os/telemetry/raw/README.md
```

`git status` shows nothing because the file that's actively being written
(`raw/2026-08-14.jsonl`) is gitignored — by design, per `.gitignore`:

```
# Atlas telemetry: raw/ is the uncommitted full-fidelity spool. It holds verbatim
# prompt text, Bash commands, absolute home paths, and bounded heads of tool output.
# runs/ is the committed authoritative copy - see atlas-os/telemetry/SCHEMA.md
atlas-os/telemetry/raw/*
!atlas-os/telemetry/raw/README.md
```

### The survival question: does anything promote `raw/` → `runs/` (the committed copy)?

```
$ ls -la atlas-os/telemetry/runs/
-rw-r--r-- 1 root root       0 .gitkeep
-rw-r--r-- 1 root root 2762986 2026-08-13.jsonl   <- yesterday, already committed
-rw-r--r-- 1 root root    3170 README.md

$ git ls-files atlas-os/telemetry/runs/
atlas-os/telemetry/runs/.gitkeep
atlas-os/telemetry/runs/2026-08-13.jsonl
atlas-os/telemetry/runs/README.md
```

There is **no `2026-08-14.jsonl` in `runs/`** — today (this session's day) has not been
promoted. Searching the repo for what calls `promote.py`:

```
$ grep -rn "promote.py" ...
atlas-os/telemetry/runs/README.md:33:**Nothing calls it.** `bin/nightly.sh` contains no
  reference to `promote.py`, so promotion...
atlas-os/reference/ROADMAP.md:33: Telemetry promotion | 3 — scripted, **called by hand
  only** | Wiring `telemetry/promote.py` into `bin/nightly.sh`
atlas-os/reference/ROADMAP.md:47: `raw` → `runs` promotion ad hoc, corpus empty |
  Idempotent, lock-guarded, ... — **but still invoked by hand; `bin/nightly.sh`
  contains no call to it**
```

No cron, no GitHub Actions workflow (`.github/` has no files in this checkout), no hook,
nothing in `nightly.sh` — `promote.py` genuinely runs only when a human types it.

**Conclusion for Test 1:** this cloud session IS being logged — the hook mechanism, the
session-id threading, and the redaction all work correctly and I can see this exact
session's own rows in `raw/2026-08-14.jsonl`. But `raw/` lives only on this VM's disk and
is explicitly gitignored. The committed, durable copy (`runs/`) only gets these rows if a
human (or some future automation) runs `python3 atlas-os/telemetry/promote.py` and commits
the result — and nothing does that today. Since this VM is discarded on reclaim, **these 11+
lines will be lost when this session ends**, unless promoted and committed first. That is a
gap between "recording" (works) and "durable record" (does not happen automatically),
consistent with what `ROADMAP.md` already flags as an open item.

---

## Test 2 — `cs new` chooser, refusal paths only

```
$ cloud-sessions/bin/cs new "a task"
error: cs new needs a terminal to ask in. Use --here or --cloud to say which.
EXIT=1
```
Confirmed: exact expected message, non-zero exit, no TTY here so it correctly refuses
rather than guessing.

```
$ cloud-sessions/bin/cs new --cloud
error: a cloud session runs unattended, so it needs a task.
       Run: cs new --cloud "<what to do>"
EXIT=1
```
Confirmed.

```
$ cloud-sessions/bin/cs new --bogus "t"
error: unknown flag: --bogus
EXIT=1
```
Confirmed.

```
$ cloud-sessions/bin/cs new --plan
error: --plan needs a file path
EXIT=1
```
Confirmed.

```
$ cloud-sessions/bin/cs help
cs — deliberate driver for Claude Code cloud sessions

  cs new ["<task>"]                START HERE — asks whether to run here or in the cloud
       [--here | --cloud | --plan <f>]   …or say which, for scripts
  cs doctor                        preflight every gate; fix what it flags before dispatching
  ...
EXIT=0
```
`cs new` is listed first and carries the `START HERE` marker, as expected.

All five chooser checks behave correctly on this platform. No dispatch was attempted.

---

## Test 3 — full suites here

### `cloud-sessions/tests/run.sh`

```
240 passed  0 failed  0 xfail(known-open)  2 skipped
2 skipped check(s) — a skipped check is not a passed check.
green.  No regression in the ten reviewed defects.
REAL_EXIT_CODE=0
```

Skips, quoted in full:
```
skip  D3a/env-set-write-failure-preserves-file  (cannot make a directory unwritable for root; injection impossible)
skip  D3b/env-set-reports-write-failure  (cannot make a directory unwritable for root; injection impossible)
```
Both skips are because this container runs as root (can't remove write permission from a
directory to itself), not a real gap — matches the shape of the 2-skip count seen at
`e9b7330`. Count is 240 as predicted (231 + 9 new chooser checks). No failures.

### `atlas-os/tests/heartbeat/run.sh`

```
90 passed  0 failed  0 skipped  5 defect(s) recorded
log: /home/user/internal-company-tool/atlas-os/tests/heartbeat/RESULTS.md
green.
REAL_EXIT_CODE=0
```
The 5 "defects recorded" are pre-existing known findings the suite documents in passing
checks (space-splitting claims, cross-board id collision blindness, FREEZE not enforced,
illegal claims on live rows, ENDED-row revival bypassing the overlap gate) — none of these
are new regressions, all are `ok`-status checks that assert the (known) defect behavior.

### `atlas-os/tests/worktree/run_tests.sh`

```
53 passed  0 failed  0 skipped
log appended to /home/user/internal-company-tool/atlas-os/tests/worktree/RESULTS.md
green.
REAL_EXIT_CODE=0
```

All three suites are green on this cloud VM with real (not piped-away) exit codes of 0.

**Side effect noted:** running these suites modified tracked files as designed —
`atlas-os/tests/heartbeat/RESULTS.md`, `atlas-os/tests/worktree/RESULTS.md`, and
`cloud-sessions/tests/RESULTS.md` all show as locally modified (`git status --short`) after
the runs, since each suite appends its own log. Per this probe's instructions ("commit ONLY
[the report file]"), these are left as uncommitted local changes and not staged.

---

## Test 4 — is `/cloud` present and usable here?

```
$ git ls-files .claude/
.claude/commands/cloud.md
.claude/settings.json
```
`.claude/commands/cloud.md` exists and is tracked.

Frontmatter:
```yaml
---
description: Hand the current session's work off to a cloud VM so it survives a closed laptop
---
```

**Would this cloud session see it?** Yes — directly confirmed, not inferred: this very
session's own available-skills listing includes `cloud: Hand the current session's work off
to a cloud VM so it survives a closed laptop`, which is exactly this file's description.
`.claude/` being tracked (rather than gitignored, as it used to be) means the cloud
checkout picks it up like any other slash command / skill.

**Would it be usable to actually dispatch from here?** No, and the command's own body says
so: "**You cannot dispatch it yourself.** `claude --cloud` requires an interactive terminal
and refuses when run from a tool... Do not try to work around the TTY requirement." Its
real job inside a cloud session is limited to prep — running `cs doctor`, checking git
status, writing and committing a handoff brief — and then handing the user a `cs new --cloud
--plan ...` command to paste into a real terminal themselves. That matches Test 2's finding
that `cs new` without `--here`/`--cloud` correctly refuses when there's no TTY: the slash
command and the CLI agree on this boundary.

---

## Still broken / open items

1. **Telemetry promotion is not automatic (the headline gap).** `raw/` is gitignored and
   `promote.py` is "invoked by hand only" per `ROADMAP.md`. A cloud session's entire
   ledger — proven here to be captured correctly at the hook level — is discarded when the
   VM is reclaimed unless a human runs `promote.py` and commits before that happens. This
   was true before this probe and remains true after it; this probe changed nothing about
   that mechanism (measurement only, per instructions).
2. `bin/nightly.sh` still contains no call to `promote.py`, confirmed directly by grep in
   this checkout, consistent with `ROADMAP.md`'s existing note.
3. Nothing else found broken: hooks fire correctly and exit 0, the chooser refusal paths on
   `cs new` are all correct on this platform, all three test suites are green with real exit
   code 0, and `/cloud` is present, tracked, and visible from a cloud session (with its own
   documented, correct limitation that it cannot dispatch itself).
