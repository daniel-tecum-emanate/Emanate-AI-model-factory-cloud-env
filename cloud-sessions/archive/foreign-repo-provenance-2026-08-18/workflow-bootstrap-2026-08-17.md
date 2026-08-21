**Status:** dated evidence · 2026-08-17 · one run, not current state

# Workflow bootstrap — first end-to-end `/cloud-workflow` dispatch

This is the record of the first real dispatch of
[`docs/workflows/reference-index-refresh.md`](../../docs/workflows/reference-index-refresh.md)
as a cloud-session coordinator run: one session reads the workflow brief, fans out one
subagent per lane, verifies each lane's own claimed done-when independently, merges,
re-runs the suites, and opens one PR. Every claim below is tagged `VERIFIED` (I ran the
command myself, in this session, and it is quoted) or `UNVERIFIED` (observed once, not
independently re-tested, or inferred rather than measured). Nothing here is `DISPROVEN` —
the run did what it set out to do — but several things behaved differently from what the
brief's phrasing would suggest.

## 1. Was the Task/subagent tool present?

`VERIFIED` — yes. This coordinator session had a working `Agent` tool (this repo's cloud
tooling calls the underlying capability "Task"; the tool surfaced here is named `Agent`)
and used it to spawn both lanes. No fallback to serial hand-work (brief rule 7) was
needed.

## 2. How many subagents ran, and were they concurrent?

`VERIFIED` — exactly 2 subagents, one per lane (`lane-refidx`, `lane-refcheck`), matching
the brief's two-lane decomposition 1:1. Both were launched in a single coordinator
message (two `Agent` tool calls in one turn), each with `run_in_background` (the
default), which is the mechanism this harness uses for concurrent execution.

Evidence of genuine concurrency, not serial execution disguised as parallel dispatch:
completion notifications arrived independently, at different times, and their reported
per-agent durations overlap:

| Lane | Tool uses | Duration | Notification arrival (session-relative) |
|---|---|---|---|
| lane-refcheck | 15 | ~165.1s | first |
| lane-refidx | 21 | ~233.6s | second, arrived independently later |

If these had run serially, total wall-clock would be ~165s + ~234s ≈ 399s. The
coordinator's own elapsed time between dispatch and the second (later) notification was
consistent with the *longer* single duration (~234s), not the sum — i.e. the two were
running at the same time, not queued one after the other. This is the same style of
concurrency evidence `subagents-in-cloud-2026-08-16.md` used, applied to this session
directly rather than to a nested probe.

## 3. Did `allowed_tools` filtering of Task have any observable effect?

`UNVERIFIED`. No restriction was encountered: both `Agent` calls succeeded without a
permission prompt or refusal, and each subagent completed its own git/bash/file-edit
work inside its worktree without any tool being denied. This session did not attempt a
negative control (deliberately requesting a tool expected to be filtered), so "no
restriction was hit" is not the same as "no restriction exists" — this line item is
`UNVERIFIED` in both directions, not `DISPROVEN`.

## 4. Isolation mechanism used, and why

`VERIFIED`. Rather than the `Agent` tool's built-in `isolation: "worktree"` option
(which does not let the caller name the resulting branch), each subagent was briefed to
run `git worktree add <path> -b claude/reference-index-refresh-<lane> <base-commit>`
itself, using the exact branch name the brief's Report-as convention requires
(`claude/reference-index-refresh-<lane>`). Both worktrees were rooted at the same base
commit (`f144b17b88fe891951566e262f60da8ce39c0b4e`, the commit this coordinator session
started at). This worked cleanly: `git status --porcelain` inside each lane's worktree,
checked by the subagent itself and re-checked by the coordinator after fetching each
pushed branch, showed changes to exactly one file per lane, nothing outside `owns`.

## 5. What the suites reported

All commands below were run by the coordinator directly (not relayed from a subagent
claim), on the fully merged tree (`claude/reference-index-refresh` after merging both
lane branches, then merging in `origin/main`, which advanced mid-dispatch — see §6).

`VERIFIED`:

```
$ bash cloud-sessions/tests/run.sh
414 passed  1 failed  0 xfail(known-open)  6 skipped
not green.  failed: wf/task-filtering-unverified

$ bash atlas-os/tests/heartbeat/run.sh
90 passed  0 failed  0 skipped  5 defect(s) recorded
green.

$ bash atlas-os/tests/process/run.sh
18 passed  0 failed  0 skipped
green.

$ bash atlas-os/tests/telemetry/run.sh
155 passed  0 failed  1 skipped  1 known-defect
green, with 1 skipped check(s).
```

Three of four suites are green by their own reporting convention (heartbeat's 5 recorded
defects and telemetry's 1 known-defect/1 skip are pre-existing, by-design non-passes per
those suites' own documentation, not new). `cloud-sessions/tests/run.sh` is the one
genuinely red suite — see §6, this predates and is unrelated to this workflow's two
lanes.

## 6. Deviations from the brief's expectations

### 6a. `origin/main` advanced mid-dispatch — the brief's "re-run the suites" step needed one more merge than written

`VERIFIED`. The brief's step 5 says: "merge the lane branches into
`claude/reference-index-refresh`, re-run the repository's suites on the merged result."
Read literally, this only names the two lane branches. Partway through this dispatch,
`git fetch origin main` returned a **forced update**: `origin/main` moved from `2258ab1`
(the commit this coordinator's base, `f144b17`, itself descended from) to `6da6704`, five
commits ahead, landed by activity outside this session while it was running. `f144b17`
is still an ancestor of the new `origin/main` (`git merge-base --is-ancestor` confirmed),
so nothing was lost or rewritten — but `claude/reference-index-refresh` was now missing
five upstream commits it would otherwise conflict with or silently shadow at PR time.
The coordinator merged `origin/main` into the integration branch before opening the PR;
this is not written as an explicit step in the brief but is necessary to avoid opening a
PR against a base it has already diverged from.

### 6b. Merging in that upstream drift surfaced a real, pre-existing suite failure — exactly the "disjoint files can still conflict semantically" case the brief warned about, but not from the two lanes

`VERIFIED`. After merging `origin/main`, `cloud-sessions/tests/run.sh` went from
415/0/6-skipped to 414/1/6-skipped, newly failing `wf/task-filtering-unverified`
(asserts `.claude/commands/cloud-workflow.md` still contains the literal string
`UNVERIFIED` tagging the unexercised `allowed_tools`/Task claim; it no longer does).
The coordinator checked out bare `origin/main` (`6da6704`) in a separate detached
worktree, with no lane changes present at all, and ran the same suite:

```
$ bash cloud-sessions/tests/run.sh   # at origin/main 6da6704, no lane changes
414 passed  1 failed  0 xfail(known-open)  6 skipped
not green.  failed: wf/task-filtering-unverified
```

Identical failure, identical tally. This confirms the regression was introduced by
whatever landed `.claude/commands/cloud-workflow.md`'s changes in the 5 new upstream
commits, entirely independent of `lane-refidx` or `lane-refcheck` (neither lane's `owns`
includes anything under `.claude/`). Per this dispatch's own rule 7 ("if a lane is
blocked by something the file does not cover, do not guess — finish what is safely
finishable and write exactly what stopped you"), this is called out in the PR
description rather than fixed — fixing `.claude/commands/cloud-workflow.md` is outside
both lanes' owned paths and outside this workflow's mission (a documentation-hygiene
pass on `cloud-sessions/reference/`, not on `.claude/commands/`).

### 6c. Concurrent lanes produced a real, self-correcting sequencing gap

`VERIFIED`. `lane-refidx` rebuilt `cloud-sessions/reference/README.md`'s index table
against the base commit, before `lane-refcheck`'s new file
(`reachability-2026-08-17.md`) existed anywhere — so the merged result briefly had a
reachability report with no row pointing to it, which the brief's own done-when check
(`comm -23 ...`) caught immediately post-merge. The coordinator added one row during
integration (not attributable to either lane's `owns`, since at the time each lane
wrote its file the other's didn't exist yet).

Separately, `lane-refcheck`'s reachability report (evidence frozen at the base commit)
named three orphans: `linux-verification-2026-08-15.md`, `safety-audit-2026-08-15.md`,
`subagents-in-cloud-2026-08-16.md`. Checking the merged tree, all three are in fact
linked from `lane-refidx`'s rebuilt table with real `[...](...)` markdown syntax — the
two lanes independently converged on fixing the same underlying problem from opposite
directions (one built the index, one detected the gap), and by the time both landed,
the gap was already closed. The reachability report's orphan list is therefore
correctly dated evidence of the pre-merge state, not a live defect in the merged
result — exactly the "dated evidence vs. current state" distinction this folder's own
convention insists on, now demonstrated by the report's own contents going stale within
the same dispatch that produced it.

### 6d. The done-when's own verification command has a case-sensitivity bug

`VERIFIED`. The brief's done-when check —
```
comm -23 <(ls cloud-sessions/reference/*.md | xargs -n1 basename | sort) <(grep -oE '[a-z0-9-]+\.md' cloud-sessions/reference/README.md | sort -u)
```
— uses a lowercase-only regex (`[a-z0-9-]+\.md`) against `ls`-derived basenames that
preserve real case. `OPEN-GAPS.md` and `README.md` are uppercase on disk; no substring
of their literal filenames can ever match `[a-z0-9-]+\.md`, so the check reports both as
missing regardless of file content. Confirmed by running the identical check against the
pristine base commit before any lane edits — both were already flagged, on a table that
didn't exist yet to be checked. Content-wise, `OPEN-GAPS.md` has a real row with a
resolving link; `README.md` is the index itself and is explicitly footnoted as exempt
from listing itself. This is a defect in the check as written, not a content gap, and no
edit to the README's content can close it.

### 6e. Nothing registered on any heartbeat board, by design

`VERIFIED`. Per dispatch rule 1, no lane and no coordinator step called
`python3 atlas-os/bin/heartbeat.py register` (or `overlap`, `update`, `end`) at any
point. Isolation was achieved entirely through git worktrees with coordinator-assigned
branch names (§4), not through the heartbeat board's overlap gate. `atlas-os/bin/**` was
never written to by either lane or by the coordinator, and `atlas-os/telemetry/**` was
never committed.

### 6f. A routine's own title asked to be disabled after firing — not acted on

`UNVERIFIED` as a claim about intent, `VERIFIED` as an observation: this session's title,
visible via `get_session`, reads "workflow: reference-index-refresh coordinator (one-off,
disable after firing)". Dispatch constraint 6 forbids creating, firing, or modifying a
routine from inside this session. The coordinator did not call any trigger-management
tool. Whether or by whom that routine gets disabled is outside this session's authority
by the brief's own rule, so this is recorded rather than acted on.

## 7. Summary table

| Question | Answer | Tag |
|---|---|---|
| Task/subagent tool present | Yes | `VERIFIED` |
| Subagents dispatched | 2 (1 per lane) | `VERIFIED` |
| Genuinely concurrent | Yes (overlapping durations, independent completion times) | `VERIFIED` |
| `allowed_tools` filtering observed | No restriction hit; not deliberately tested | `UNVERIFIED` |
| Heartbeat board touched | No | `VERIFIED` |
| `atlas-os/bin/**` written | No | `VERIFIED` |
| `atlas-os/telemetry/**` committed | No | `VERIFIED` |
| Routine created/fired/modified from inside | No | `VERIFIED` |
| `cloud-sessions/tests/run.sh` | 414 passed / 1 failed / 6 skipped — failure pre-exists on bare `origin/main`, unrelated to either lane | `VERIFIED` |
| `atlas-os/tests/heartbeat/run.sh` | 90 passed / 0 failed / 0 skipped, 5 defects recorded (by design) | `VERIFIED` |
| `atlas-os/tests/process/run.sh` | 18 passed / 0 failed / 0 skipped | `VERIFIED` |
| `atlas-os/tests/telemetry/run.sh` | 155 passed / 0 failed / 1 skipped, 1 known-defect (by design) | `VERIFIED` |
| Every reference doc reachable from the index, post-merge | Yes | `VERIFIED` |
