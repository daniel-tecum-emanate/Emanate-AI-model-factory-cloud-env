# archive/

A preserved historical investigation record. **Read-only. Do not fix, update, or delete
it** — corrections go in [`../reference/`](../reference/), never into the archive.

Nothing here is current. It is kept because the approaches it *rejected* are the reason
the current approach was chosen, and that reasoning is not written down anywhere else.

| Path | What it is |
|---|---|
| [`2026-07-29/`](2026-07-29/) | The original investigation, dated and kept whole |

## What 2026-07-29/ was

It started from one request — *"set up a virtual machine or process so that cursor and
claude sessions remain running even when I close my computer"* — and found that this was
four different problems wearing one coat: the terminal closing, the machine idling to
sleep, the lid closing, and the machine being shut down. Seven approaches were evaluated
as separate "lanes", each with its own status.

## Its status today: superseded

- The lane it named the highest-value **untested** option — Claude Code cloud sessions —
  is now the verified default. Its own snapshot lists that lane as `AVAILABLE, NOT YET
  RUN`; today's evidence is in
  [`../reference/verified-facts.md`](../reference/verified-facts.md), not here.
- The implementation it documents (the `longrun` scripts, its runbook, its research files)
  **no longer exists on disk**. Every command in these files that invokes `longrun.sh` is
  unrunnable. This is why [`../bin/cs`](../bin/cs) is self-contained.
- Its `STATUS.md` describes a keepalive watchdog, a `pmset` state, and a battery level
  from that evening. Those are a 2026-07-29 snapshot of one machine, not a live reading.
- The paid always-on VM it was blocked on is very likely unnecessary; that question is
  still open and is tracked in [`../README.md`](../README.md#open-questions), not here.

Current instructions are in [`../playbooks/`](../playbooks/). Current claims and their
evidence are in [`../reference/`](../reference/).

## Contents

| Path | What it holds |
|---|---|
| [`2026-07-29/PROBLEM.md`](2026-07-29/PROBLEM.md) | The four failure modes, why `caffeinate` cannot survive a lid close (in Apple's own words), and why `nohup … & disown` does not detach a process. Still the best account of why the local approaches fail |
| [`2026-07-29/STATUS.md`](2026-07-29/STATUS.md) | The capability board as of that evening, plus an incident where a test suite disabled the very protection it was verifying, twice, and still reported all green |
| [`2026-07-29/README-original.md`](2026-07-29/README-original.md) | The folder's original front page, before the current [`../README.md`](../README.md) replaced it |
| [`2026-07-29/lanes/`](2026-07-29/lanes/) | One file per evaluated approach, with the rejections named in the filenames |
| [`2026-07-29/decisions/`](2026-07-29/decisions/) | `DECISIONS.md` — nine locked calls and what would reverse each — and `OPEN-QUESTIONS.md`, unresolved as of that date |
| [`2026-07-29/evidence/`](2026-07-29/evidence/) | `command-log-2026-07-29.md`, verbatim output for the load-bearing claims of the time |
| [`2026-07-29/agents/`](2026-07-29/agents/) | What five spawned subagents returned, including what the one that succeeded got wrong |
| [`2026-07-29/session-logs/`](2026-07-29/session-logs/) | The working log for the day, including how the request was narrowed |

## The lanes, and why they are the useful part

| Lane | Verdict recorded that day |
|---|---|
| [`01-local-tmux.md`](2026-07-29/lanes/01-local-tmux.md) | Survives the terminal closing and idle sleep; not a lid close, not a shutdown |
| [`02-lid-close-pmset.md`](2026-07-29/lanes/02-lid-close-pmset.md) | `pmset disablesleep` survives a lid close on that hardware; not a shutdown, and needs the machine powered and online |
| [`03-cursor-cloud.md`](2026-07-29/lanes/03-cursor-cloud.md) | Cursor cloud agents survive shutdown; verified end-to-end that day |
| [`04-claude-code-cloud.md`](2026-07-29/lanes/04-claude-code-cloud.md) | Claude Code cloud sessions — available but **not yet run** at the time. This is the lane that became the current approach |
| [`05-claude-local-background.md`](2026-07-29/lanes/05-claude-local-background.md) | Local background agents run on the machine and die with it |
| [`06-github-actions-DROPPED.md`](2026-07-29/lanes/06-github-actions-DROPPED.md) | **DROPPED on policy, not capability.** It works technically; using GitHub-hosted runners as a general agent host is against GitHub's acceptable-use terms |
| [`07-always-on-vm-BLOCKED.md`](2026-07-29/lanes/07-always-on-vm-BLOCKED.md) | **BLOCKED on a spend decision** and never provisioned — recurring spend is a founder decision |

The two rejections carry their verdicts in their filenames deliberately, so a future reader
cannot mistake an evaluated-and-refused option for an unexplored one.

Do not cite anything in this folder as current fact. Cite it only as *what was believed and
why, on 2026-07-29*.
