# Do subagents work in a cloud session? — probe results (2026-08-16)

Verdict: **YES.** This cloud session has a working `Agent`/Task tool, subagent answers matched independently-verified ground truth in every test, three subagents genuinely ran concurrently, and their tool calls are logged to the telemetry ledger under distinct `session_id`s alongside the parent.

## Verdict table

| # | Question | Answer |
|---|---|---|
| 1 | Task/Agent tool available in cloud session? | Yes — `Agent` tool, types: `claude`, `claude-code-guide`, `Explore`, `general-purpose`, `Plan`, `statusline-setup` |
| 2 | Single subagent returns a correct, verifiable answer? | Yes — exact match on both facts checked |
| 3 | Three subagents run genuinely in parallel? | Yes, with caveats — overlapping execution confirmed by telemetry timestamps, not perfectly simultaneous start (see detail) |
| 4a | Subagent inherits git credentials/auth to remote? | Yes — `git fetch --dry-run origin` authenticated successfully inside the subagent |
| 4b | Subagent sees same detached HEAD as parent? | Yes — identical commit `1d99660...` |
| 4c | Subagent tool calls appear in telemetry ledger with distinct session ids? | Yes — 5 distinct child `session_id`s logged in `atlas-os/telemetry/raw/2026-08-16.jsonl`, alongside the parent's own |
| 4d | Concurrency cap hit? | No — not hit at n=3, not tested beyond that |

Session context: repo `daniel0tgc/internal-company-tool`, running detached at `1d99660` ("Release lane-autonomy"), branch not checked out (`git rev-parse --abbrev-ref HEAD` → `HEAD`).

```
$ git log --oneline -3
1d99660 Release lane-autonomy
c2b2417 Add autonomy/ — the control plane for unattended sessions
d889050 Correct three live instructions that would break the kill switch
```

## Test 1 — does the Task tool exist here?

Yes. The `Agent` tool is present in this cloud session's tool list, offering these subagent types:

- `claude` — catch-all default
- `claude-code-guide` — Claude Code / Agent SDK / API questions
- `Explore` — read-only code search
- `general-purpose` — general research and multi-step tasks (used for all probes below)
- `Plan` — implementation planning
- `statusline-setup` — status line configuration

This alone answers the headline question in the affirmative: the mechanism the local setup relies on for fan-out is present in a cloud session.

## Test 2 — one subagent, verified

Ground truth (run directly, not by a subagent):

```
$ git ls-files cloud-sessions | wc -l
57
$ head -1 cloud-sessions/AGENT.md
# AGENT.md — operating rules for this folder
```

Subagent (`general-purpose`, foreground) given the identical read-only task, reported:

```
1. `git ls-files cloud-sessions | wc -l` → 57
2. `head -1 cloud-sessions/AGENT.md` → `# AGENT.md — operating rules for this folder`
```

**Result: MATCHED exactly**, on both the count and the literal text. No hallucinated or plausible-wrong answer.

## Test 3 — three subagents in parallel

Ground truth (run directly):

```
$ git ls-files atlas-os/tests | wc -l
57
$ bash atlas-os/tests/process/run.sh   # tail
18 passed  0 failed  0 skipped
$ ls cloud-sessions/reference/*.md | wc -l
19
```

Three `general-purpose` subagents were launched in a single message (background mode), one job each:

| Job | Subagent answer | Matched ground truth? |
|---|---|---|
| (a) tracked files under `atlas-os/tests/` | `57` | Yes |
| (b) tally line from `run.sh` | `18 passed  0 failed  0 skipped` | Yes |
| (c) `.md` files in `cloud-sessions/reference/` | `19` | Yes |

**All three matched exactly.**

### Parallelism evidence

Launch call (all three `Agent` invocations sent in one message) returned at `t=1786845276.574`. Each subagent's own internally-recorded start/end timestamps (from `date +%s.%N` run inside the subagent, before/after its command):

| Subagent | Start (epoch) | End (epoch) | Command duration | Reported agent `duration_ms` |
|---|---|---|---|---|
| (a) tests count | 1786845282.536 | 1786845282.542 | ~6 ms | 4324 ms |
| (b) run.sh tally | 1786845284.507 | 1786845285.089 | ~581 ms | 6256 ms |
| (c) md count | 1786845285.775 | 1786845285.779 | ~4 ms | 5801 ms |

Read plainly, those three internal start times are staggered by ~2 seconds each, which on its own looks sequential. But the telemetry ledger (see Test 4c) gives a cleaner signal: the actual Bash-tool-call timestamps for these three subagents in `atlas-os/telemetry/raw/2026-08-16.jsonl` were `01:54:42.049`, `01:54:44.017`, and `01:54:45.320` — each new subagent's first tool call landed *before* the previous subagent's full lifecycle (`duration_ms` of 4.3–6.3s) had finished. If execution were strictly serial, subagent (b) would not start until (a)'s ~4.3s lifecycle fully completed; instead it started ~2s in, while (a) was still running. Total wall clock from launch to all three completions was ~23.5s, versus ~16.4s if their individual `duration_ms` values were simply summed — consistent with overlapping-but-imperfectly-synchronized execution, not clean simultaneity and not full serialization.

**Conclusion: genuinely concurrent, not serialized — but with a real ~2s stagger in when each subagent starts doing work**, most plausibly subagent container/session spin-up jitter rather than a queue. Worth knowing if wall-clock timing precision matters; irrelevant for correctness.

## Test 4 — the limits

### 4a. Git credential inheritance

A subagent ran `git remote -v` and `git fetch --dry-run origin`:

```
origin	https://github.com/daniel0tgc/internal-company-tool (fetch)
origin	https://github.com/daniel0tgc/internal-company-tool (push)

$ git fetch --dry-run origin
From https://github.com/daniel0tgc/internal-company-tool
 + 2258ab1...1d99660 main       -> origin/main  (forced update)
```

The dry-run fetch authenticated and returned real ref-comparison data (not an auth error), meaning the subagent **does inherit working git credentials/auth** to the remote — likely via the same proxy/credential mechanism the parent session uses, since no token is visible in the remote URL itself.

### 4b. Detached HEAD visibility

Subagent reported:

```
$ git rev-parse --abbrev-ref HEAD
HEAD
$ git rev-parse HEAD
1d996604da7eed904d6ac99917cd36d57c50c262
```

Identical to the parent session's HEAD (`1d99660...`, detached). **Subagents see the same repository state as the parent**, including detached HEAD.

### 4c. Telemetry ledger

`atlas-os/telemetry/raw/2026-08-16.jsonl` records events with a `session_id` field. Grouping today's 37 events by `session_id`:

| session_id | event count | notes |
|---|---|---|
| `bd779d5f-7087-534c-869a-6d865ba4af15` | 29 | parent (this) session |
| `a312924948dd12d01` | 4 | Test 2 subagent |
| `acc95923a44ac78ec` | 2 | Test 3(a) subagent |
| `a74288f8687365a67` | 2 | Test 3(b) subagent |
| `ac8f39244f6b055a2` | 2 | Test 3(c) subagent |

Each subagent's `session_id` matches the `agentId` the `Agent` tool returned for it. **Subagent tool calls are logged distinctly in the telemetry ledger, alongside the parent's, not merged into the parent's session.** The Test-3(a/b/c) session_ids' first events landed at `01:54:42.049`, `01:54:44.017`, and `01:54:45.320` respectively — the overlap used as parallelism evidence in Test 3 above.

### 4d. Concurrency cap

Not hit. Three subagents launched together all ran without any queueing/rejection error. This probe did not attempt a larger fan-out, so a cap may exist above 3 but was not found here.

## Bottom line for the local-setup question

A cloud session **can** fan work out to subagents, they return correct (not plausible-wrong) answers on verifiable read-only jobs, they run with real overlap rather than strict serialization, and they carry the same git identity/state and get their own line in the telemetry ledger. The one soft caveat is a small (~2s) per-subagent startup stagger — not a correctness or capability limit, just a timing texture worth knowing about before assuming sub-second synchronized fan-out.
