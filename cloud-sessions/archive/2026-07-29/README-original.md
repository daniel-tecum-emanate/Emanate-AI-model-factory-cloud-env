# cloud-sessions

**The question this folder answers:** how do Cursor and Claude sessions keep running when
Daniel's laptop is closed, asleep, or shut down — and which of those answers are actually
proven versus merely plausible?

Started 2026-07-29 from a single request: *"set up a virtual machine or process so that cursor
and claude sessions remain running even when I close my computer."* That turned out to be four
different problems wearing one coat, which is why this folder exists instead of a single doc.

---

## Read in this order

| # | File | Why |
|---|------|-----|
| 1 | [`STATUS.md`](STATUS.md) | **The board.** Every lane, its verification status, and the exact command. If you read one file, read this one. |
| 2 | [`PROBLEM.md`](PROBLEM.md) | The four distinct failure modes hiding inside "computer closed", and why the obvious answers (`caffeinate`, `nohup`) do not work. Read before proposing anything. |
| 3 | [`lanes/`](lanes/) | One file per approach — what it does, what is proven, what it costs, how it fails. |
| 4 | [`decisions/DECISIONS.md`](decisions/DECISIONS.md) | Locked calls and their reasoning, so they are not relitigated. |
| 5 | [`decisions/OPEN-QUESTIONS.md`](decisions/OPEN-QUESTIONS.md) | What is genuinely unresolved and what needs Daniel specifically. |
| 6 | [`evidence/`](evidence/) | Verbatim command output. Nothing in this folder claims a result that is not pasted here. |
| 7 | [`agents/`](agents/) | Every subagent spawned, what it was asked, what came back, and which ones failed. |
| 8 | [`session-logs/`](session-logs/) | Chronological narrative per working session. |

## The lanes at a glance

Full detail in [`lanes/`](lanes/); current status in [`STATUS.md`](STATUS.md).

| Lane | One-line summary |
|------|------------------|
| [01 local tmux](lanes/01-local-tmux.md) | Work survives the terminal and the IDE closing. Machine must stay awake. |
| [02 lid-close](lanes/02-lid-close-pmset.md) | `pmset disablesleep` as root. **Proven** — the answer for the in-IDE Cursor agent. |
| [03 Cursor cloud](lanes/03-cursor-cloud.md) | **Proven.** Runs on Cursor's VM; survives shutdown. Fire-and-steer, not attach. |
| [04 Claude Code cloud](lanes/04-claude-code-cloud.md) | First-party Anthropic cloud + teleport back. **Compiled into the installed CLI but not yet run.** Likely the best fit. |
| [05 Claude local background](lanes/05-claude-local-background.md) | `claude --bg`. Local only — dies with the machine. |
| [06 GitHub Actions](lanes/06-github-actions-DROPPED.md) | **Dropped.** Hosted-runner AUP forbids it. |
| [07 always-on VM](lanes/07-always-on-vm-BLOCKED.md) | **Blocked** on a spend decision. GCP unusable (billing closed). |

---

## Ground rules for this folder

These exist because this repo has repeatedly been burned by confident documentation that
turned out to be wrong. Two entries in `validation/QUEUE.md` (V-101, V-102) are corrections
to claims made *in this very workstream* within hours of making them.

1. **Tag every claim** `VERIFIED` / `PARTIALLY VERIFIED` / `UNVERIFIED` / `DISPROVEN`, and say
   *how* it was verified. "It should work" is not a status.
2. **Paste the output.** A claim with no command output in [`evidence/`](evidence/) is a guess.
   Summarised output hides exactly the detail that later turns out to matter.
3. **A skipped check is not a passed check.** Applies to `verify_system.sh` banners and to
   anything here.
4. **Absence from `--help` is not absence of the feature.** This cost real time on 2026-07-29:
   `claude --cloud` was declared non-existent because it was not in `--help`, when the
   documentation had already said it is hidden. See lane 04 and
   [`decisions/DECISIONS.md`](decisions/DECISIONS.md) D-6.
5. **Do not delete a wrong claim — supersede it.** Keep the wrong version, mark it, and say what
   corrected it. The record of *how* we got it wrong is the useful part.
6. **Distinguish "not permitted" from "not possible".** Lane 06 is technically fine and
   policy-forbidden. Those need different handling.

## How to extend it

- **New approach?** Add `lanes/NN-<name>.md` using the existing lane files as the shape
  (What it is / Status / How to use / What is proven / How it fails / Cost). Add a row to
  `STATUS.md` and to the table above.
- **Killed or blocked a lane?** Keep the file, suffix it `-DROPPED` or `-BLOCKED`, and state the
  reason at the top. Do not delete it — the next session will otherwise re-propose it.
- **New working session?** Add `session-logs/YYYY-MM-DD.md` and update `STATUS.md`.
- **Spawned subagents?** Log every one in `agents/`, including failures and wrong answers.
  A subagent that returned a confidently wrong result is more important to record than one that
  worked.
- **New evidence?** `evidence/command-log-YYYY-MM-DD.md`, verbatim.
- **Decision made?** `decisions/DECISIONS.md`, with the reasoning and what would reverse it.

## Where the code and the rest of the record live

This folder is the *knowledge*. The implementation and the formal records live elsewhere and
are deliberately not duplicated here:

| Thing | Location |
|-------|----------|
| Operator entry point | `scripts/longrun.sh` (`start`/`ls`/`attach`/`stop`/`keepalive`/`cloud`/`doctor`) |
| Helpers | `scripts/longrun/` — `keepalive_watchdog.sh`, `longrun_cloud.py`, `spawn_detached.py`, `cursor_repos.py` |
| Operator runbook | `LONG-RUNNING-SESSIONS.md` (how to *use* it; this folder is why it is built that way) |
| Architecture entry | `WORKFLOW-SYSTEM.md` §11l |
| Regression tests | `scripts/verify_system.sh` section `━ 14`, currently 9 checks |
| Full external research | `PRs/longrun-remote/RESEARCH-FINDINGS.md` — 40KB, primary sources, C1–C11 contradiction table |
| Fact-check + corrections | `PRs/longrun-remote/FACT-CHECK-2026-07-29.md` |
| Pending decisions | `validation/QUEUE.md` — V-095, V-096, V-101, V-102 |
| Runtime state (gitignored) | `.longrun/` |

**Do not move `PRs/longrun-remote/FACT-CHECK-2026-07-29.md` into this folder.** `validation/QUEUE.md`
item V-101 cites it as an evidence pointer, and the queue is append-only — relocating the file
silently breaks that pointer with no error anywhere.

## Orphaned code in `scripts/longrun/` — read before running anything there

The two subagents that died mid-task left **75KB of working-looking but unfinished shell** behind.
None of it is referenced by `longrun.sh`, `verify_system.sh`, or `MANIFEST.yaml`, and none of it has
run end-to-end. All of `scripts/longrun/` is still **untracked in git**, so nothing is committed yet.

| File | Status |
|---|---|
| `gha_remote.sh` (17KB) | **DO NOT USE.** Implements the lane dropped on policy — hosted-runner AUP, penalty ladder ends at account termination. Now carries a header saying so. Its original comment claims it is "the only lane that survives a shutdown", which is **false**. |
| `provision_remote.sh` (32KB) | Untested against any real host — and it **spends money**, which agents must never do (I5/I9, [D-8](decisions/DECISIONS.md)). Its author's own `NEVER-EXECUTED-AGAINST-A-REAL-HOST` banner is accurate; trust it. |
| `ssh_remote.sh` (26KB) | Untested attach driver for a box that does not exist. Blocked with [lane 07](lanes/07-always-on-vm-BLOCKED.md). |

Treat all three as **sketches**, not scripts. The in-use, tested files in that directory are
`keepalive_watchdog.sh`, `spawn_detached.py`, `cursor_repos.py`, `longrun_cloud.py`,
`cloud_session.py`, and `cursor_agents.py` — those are the ones registered in `MANIFEST.yaml` and
covered by the suite. Registration and test coverage are the signal; file presence is not.
