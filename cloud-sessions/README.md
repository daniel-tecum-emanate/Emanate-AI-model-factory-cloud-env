# cloud-sessions

> **Provenance note, 2026-08-18:** this folder's mechanism (`bin/cs`, the playbooks, the
> git gate, the test harness) is genuine and account-level. Its evidence record and dated
> probe reports, however, were written against a different repository
> (`daniel0tgc/internal-company-tool`), bulk-committed here by mistake in commit `7a5690b`,
> and have been moved whole to
> [`archive/foreign-repo-provenance-2026-08-18/`](archive/foreign-repo-provenance-2026-08-18/README.md)
> rather than rewritten as this repo's own history. See that folder's README for the full
> explanation, and `reference/README.md` for what remains current here.

**Run Claude Code sessions on cloud infrastructure so the work keeps going when you close
the laptop.**

The problem is not "keep the Mac awake." No amount of cleverness on a laptop keeps work
running while the laptop is *off*. So the answer is to **not run the work on the laptop**:
an Anthropic-managed VM clones your GitHub repo, does the work, pushes a branch, opens a
PR. You steer it from your phone. `--teleport` pulls it back into your terminal when
you're home.

Everything needed for that lives in this folder: the one entrypoint (`bin/cs`), the
playbooks that tell you how, the reference record of what is actually proven, and the
archive of how we got here.

---

## Run this first

```bash
bash cloud-sessions/bin/cs doctor
```

`cs doctor` is the **preflight**. It touches nothing, dispatches nothing, and costs
nothing. It checks the resolved `claude` binary and version, that auth is a claude.ai
subscription rather than an API key, that no `ANTHROPIC_*`/Bedrock/Vertex variable is
overriding that auth, local `gh` auth, and this repo's branch, remote, worktree
cleanliness and upstream state. It prints `ready.` only when `cs start` would actually
work, and distinguishes *"your install is broken"* (`not ready.`) from *"your install is
fine but a dispatch **from this directory** would be refused"* (`not dispatchable from
here.`).

Then the real front door:

```bash
cs new "Finish the auth refactor and get the tests green"

  where should this run?
    1  here    — this machine. Stops when the laptop sleeps.
    2  cloud   — an Anthropic VM. Survives a closed lid and a shutdown.
    3  here, but drivable from my phone  (Remote Control)

# → pick 2, close the laptop. Watch it at claude.ai/code or on your phone.
cs send last "skip the Redis part"     # steer it from anywhere, no laptop needed
cs tp last                             # pull it back into your terminal when you're home
```

### Installing `cs` on PATH — use a wrapper, not a symlink

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/cs <<EOF
#!/bin/bash
exec "$PWD/cloud-sessions/bin/cs" "\$@"
EOF
chmod +x ~/.local/bin/cs
```

**Superseded 2026-08-17 — this used to say `ln -sf … ~/.local/bin/cs`, and that install is
broken.** `cs` derives its root from `BASH_SOURCE` without resolving symlinks
([`bin/cs:14-15`](bin/cs)), so invoked *through* a symlink it computes
`ROOT=~/.local`, reads a ledger at `~/.local/state/sessions.jsonl` that does not exist,
and prints `no dispatches recorded` with **exit 0** over a real 14-row ledger. An
empty-but-fine answer where a populated one exists is exactly what ground rule 4 forbids.
Measured and written up in
[`porting-audit-2026-08-17.md`](archive/foreign-repo-provenance-2026-08-18/porting-audit-2026-08-17.md) (break 2).
The wrapper `exec`s the real path, so `BASH_SOURCE` stays truthful. The underlying
`bin/cs` resolution is still unfixed — the wrapper is the workaround, not the fix.

*Checked here, 2026-08-17:* `~/.local/bin/cs` on this machine is already the wrapper
(a 432-byte regular file, not a symlink) and `cs ls` through it reports the real 14 rows,
so the defect is not live here today. [`bin/README.md`](bin/README.md) and
[`playbooks/01-setup.md`](playbooks/01-setup.md) still print the symlink command; those
files belong to other owners and are flagged, not edited, from here.

**And do not put `cloud-sessions/bin` on PATH at all.** It is tracked, agent-writable, and
files there keep their exec bit through a clone — so a file committed there named `git`
(or `ls`, or `python3`) would shadow the real binary the next time you type it. A cloud
session has Bash and is told to commit and push, so such a file reaches your Mac through a
merged PR, a branch checkout, or `cs tp`. Naming one known file closes the path without
giving up the command. Found by the safety audit, 2026-08-15.

---

## The commands

Every command `bin/cs` accepts, verified against `cs help` and the dispatcher at
[`bin/cs:1797-1817`](bin/cs) on 2026-08-17. **`last`** resolves to the most recent ledger
row; an ID argument may also be a full `claude.ai/code` URL, which is stripped to the bare
ID. Per-command internals are in [`bin/README.md`](bin/README.md); what is *proven* about
each is in [`verified-facts.md`](archive/foreign-repo-provenance-2026-08-18/verified-facts.md).

| Command | What it does | Cost |
|---|---|---|
| `cs doctor` | Preflight every gate. Exits non-zero for a broken install (`not ready.`) **or** a directory a dispatch would be refused from (`not dispatchable from here.`) | touches nothing |
| `cs new ["<task>"]` `[--here\|--cloud\|--plan <f>]` | **The front door**, and the only interactive command. Asks whether the work runs *here*, in the *cloud*, or here-but-phone-drivable. With no TTY it exits rather than guessing; `--cloud` with no task is refused, because a cloud session runs unattended; `--plan` with `here`/`rc` is refused rather than silently dropped | dispatches if you pick cloud |
| `cs handoff "<what to continue>"` `[--plan <committed file>]` | The *"I am closing the laptop now"* command. Composes a self-contained brief from repo state — branch, last five commits, finish autonomously, commit to a `claude/` branch, open a PR, stop rather than guess — then hands it to `cs start`. The git gate runs *before* the brief, because the brief asserts the branch is clean and pushed | **dispatches real work** |
| `cs start "<task>"` `[--bundle] [--env <ccpool_…>]` | Dispatch to a cloud VM and return. Refuses without a TTY, then applies the git gate. `--bundle` uploads the local repo instead of cloning from GitHub (tracked edits only; untracked files are not bundled). `--env` is for **self-hosted `ccpool_…`** environments only | **dispatches real work** |
| `cs sessions [--json]` | Live **local** Claude sessions on this machine, and whether each one *could* move to the cloud | reads only |
| `cs board [--json]` | **The master view.** Heartbeat lanes + local sessions + worktrees + cloud dispatches, merged on session id, one screen. Reads `atlas-os/heartbeat/STATE.md`; without it, exits 1 `DEGRADED` and says the view is **PARTIAL, not empty** | reads only |
| `cs exodus [--dry-run]` `[--manifest <path>]` | Partition a dirty tree by the board's owns-claims into per-lane `claude/<lane>` branches plus a dispatch manifest. Prepares the aggregate move; **dispatches nothing** — `/cloud all` does that. Requires `atlas-os/bin/heartbeat.py` and (to execute) `worktree.py`, and refuses by name without them | writes branches |
| `cs env [set <id>\|clear]` | Which environment dispatches use, and how much internet it gets. `set` writes `remote.defaultEnvironmentId` into `~/.claude/settings.json` atomically, and warns when a higher-precedence layer already pins something else. **Run it from the repo root** — it reads the project layer from `cwd`, not the git toplevel | writes `~/.claude/settings.json` |
| `cs shell-init` | Print a shell function so a bare `claude` in this repo routes through the chooser. **Prints only — never edits a shell file** | prints |
| `cs send <id\|last> "<msg>"` | Steer a running session. The one steering path that needs **no TTY** — works from a script, a cron job, or a phone-tethered shell with the laptop shut | consumes limits |
| `cs ls [--json]` | Sessions dispatched from this machine. Says in its own output that claude.ai/code remains the authoritative list | reads only |
| `cs track <id> ["title"]` `[--lane L --owns P --from S --brief F --trigger T --expect-branch B]` | Record a session started on the web or phone, with provenance: which lane, which paths it owns, source session, committed brief, routine id, and the branch its output should appear on | appends a ledger row |
| `cs rm <id>` | Forget a session locally. **Does NOT stop, archive, or delete it** | rewrites the ledger |
| `cs open [id\|last]` / `cs web` | Open that session / the full list at claude.ai/code | opens a browser |
| `cs tp [id\|last]` *(alias `cs teleport`)* | Teleport the session back into this terminal. Needs a checkout of the session's repo with a clean tree | pulls a session |
| `cs rc ["name"]` *(alias `cs remote-control`)* | Keep the session **local** and drive it from the phone or browser | starts a local session |
| `cs help` *(also `-h`, `--help`, no args)* | Usage. An unknown command is an **error**, not a fallthrough |  |

Tired of being asked? `CS_DEFAULT=cloud` (or `here`, `rc`, `ask`) answers `cs new` once, in
your environment; an explicit flag still wins, and an unrecognised value refuses rather
than picking a side.

**Two things `cs` deliberately does not have.** There is **no `cs stop`** — Anthropic
exposes no call that stops a running cloud session, so you `cs send` it an instruction to
wind down and archive it from the web. And **`--env` is not a general environment
override**: the CLI documents `--environment` for self-hosted `ccpool_…` ids only. For an
Anthropic-managed environment use `cs env set`, which writes the setting the CLI reads.

**`cs` is no longer self-contained** — corrected 2026-08-17. The core
(`new/start/handoff/send/ls/track/rm/open/web/tp/rc/env/doctor/sessions/shell-init`) still
needs only bash, `python3` and the `claude` CLI. But `cs board`, `cs exodus` and the
auto-registration hook read `atlas-os/`, which does not travel when this folder is copied
elsewhere. The dependency is deliberate — the heartbeat board *is* the master registry —
and all three **degrade loudly**. See
[`playbooks/09-porting-to-another-repo.md`](playbooks/09-porting-to-another-repo.md).

---

## The playbooks — how to do a thing

Read them in order the first time. Index and per-file summaries:
[`playbooks/README.md`](playbooks/README.md).

| # | Playbook | One line |
|---|---|---|
| 01 | [`01-setup.md`](playbooks/01-setup.md) | One-time setup on a machine: install the `claude` CLI, confirm subscription auth, put `cs` on PATH, grant the Claude account access to your GitHub repos, accept workspace trust |
| 02 | [`02-dispatch.md`](playbooks/02-dispatch.md) | Hand work to a cloud VM and walk away — writing the prompt as if the author is leaving, what `cs start` refuses and why, asking for a branch and a PR instead of a teleport |
| 03 | [`03-steer-and-return.md`](playbooks/03-steer-and-return.md) | Find your sessions, the three ways to steer a running one (`cs send`, browser, mobile), and pull it home with `cs tp` |
| 04 | [`04-remote-control.md`](playbooks/04-remote-control.md) | The opposite trade: keep the session on your Mac with your files, MCP servers and secrets, and use the phone as a window into it |
| 05 | [`05-routines.md`](playbooks/05-routines.md) | Scheduled, HTTP- and GitHub-triggered cloud runs — **the only way to start cloud work with no human at a terminal**, plus the connector-inheritance and "Run now" footguns |
| 06 | [`06-environments.md`](playbooks/06-environments.md) | Configure the VM a session boots into: network tiers, environment variables, setup scripts, self-hosted environments |
| 07 | [`07-making-cloud-the-default.md`](playbooks/07-making-cloud-the-default.md) | **There is no "always cloud" setting** — so: the five routes that get you there, what each costs, and why the obvious shell wrapper around `claude` is the one to talk yourself out of |
| 08 | [`08-concurrent-sessions-and-exodus.md`](playbooks/08-concurrent-sessions-and-exodus.md) | Many sessions at once: auto-registration on the heartbeat board, owns-claims that make one dirty tree partitionable, `cs board` as the single view, `cs exodus` → `/cloud all`, and the close-the-lid runbook |
| 09 | [`09-porting-to-another-repo.md`](playbooks/09-porting-to-another-repo.md) | Copying this folder into another repository: what ports, what refuses loudly, what breaks **silently**, and the ten-step checklist |
| 10 | [`10-fleet.md`](playbooks/10-fleet.md) | Many agents on cloud VMs at once: the heartbeat block every payload carries, the blocker taxonomy, watching with `cs fleet`/`fleet_ui.py`, and what to do when one reports blocked |
| 11 | [`11-finetune-pipeline-agent.md`](playbooks/11-finetune-pipeline-agent.md) | **DOCS, design-only.** Dispatch brief for a cloud agent orchestrating one fine-tuning pipeline run — stops at every human gate instead of guessing past it, never holds the Fireworks or Supabase credential |

Two surfaces outside this folder drive it and must be copied with it: the
[`/cloud`](../.claude/commands/cloud.md) and
[`/cloud-workflow`](../.claude/commands/cloud-workflow.md) slash commands. `cs` itself
never calls the routines API — those files are the dispatch brains. `/cloud` prepares and
commits a brief and hands you the command to paste; it cannot dispatch, because that needs
a terminal.

---

## Where claims live vs. where instructions live

This is the folder's organising rule, and it is why nothing is duplicated between files.

| | [`playbooks/`](playbooks/README.md) | [`reference/`](reference/README.md) |
|---|---|---|
| **Answers** | *How do I do X?* | *What is true about X?* |
| **Shape** | task-shaped, numbered in reading order, one workflow per file | claims, limits, matrices, error tables, and dated probe reports |
| **Obligation** | steps you can follow | every claim carries a status tag |

**Every claim in `reference/` is tagged** — `VERIFIED` (run here, output quoted) · `DOCS`
(Anthropic's documentation, not exercised here) · `UNVERIFIED` · `DISPROVEN`. *"It should
work"* is not a status.

The authority ladder, when two files disagree:

1. [`verified-facts.md`](archive/foreign-repo-provenance-2026-08-18/verified-facts.md) is **the evidence record
   and the only current one**. Every load-bearing claim with the command that checked it
   and its quoted output, including the corrections.
2. Everything else in `reference/` is either **current-state** (kept up to date in place)
   or **dated-evidence** (a probe report or audit, true of the commit and date it names
   and nothing later). [`reference/README.md`](reference/README.md) labels every file with
   which it is.
3. The capability table below is a **summary**. When it disagrees with `verified-facts.md`,
   the evidence record wins and the summary is wrong.

A **probe report is not a status.** Probe 1 says the test suite fails 57 checks; that was
true on 2026-08-14 and stopped being true four commits later.

| I want to… | Read |
|---|---|
| set this up on a new machine | [`playbooks/01-setup.md`](playbooks/01-setup.md) |
| decide **which** approach fits my situation | [`reference/surfaces.md`](reference/surfaces.md) |
| know what will run out, expire, or get blocked | [`reference/limits.md`](reference/limits.md) |
| know what is proven vs. merely documented | [`verified-facts.md`](archive/foreign-repo-provenance-2026-08-18/verified-facts.md) |
| know whether any of this can bill me | [`billing.md`](archive/foreign-repo-provenance-2026-08-18/billing.md) |
| understand how a cloud session reaches the internet | [`reference/networking.md`](reference/networking.md) |
| fix an error | [`reference/troubleshooting.md`](reference/troubleshooting.md) |
| know what **this repo** can and cannot do in a cloud session | [`this-repo-in-the-cloud.md`](archive/foreign-repo-provenance-2026-08-18/this-repo-in-the-cloud.md) |
| see how Cursor and Codex architect this, and what to borrow | [`reference/how-others-build-this.md`](reference/how-others-build-this.md) |
| know what is still broken or unmeasured | [`OPEN-GAPS.md`](archive/foreign-repo-provenance-2026-08-18/OPEN-GAPS.md) — the register, **stale as of 2026-08-17**; see the note in [`reference/README.md`](reference/README.md) |
| see what was actually measured inside a cloud VM | the seven probes and the six later audits, indexed in [`reference/README.md`](reference/README.md#the-seven-cloud-vm-probes--2026-08-14) |
| change `bin/cs` without breaking it | [`tests/README.md`](tests/README.md) — run `bash cloud-sessions/tests/run.sh`; it costs nothing and dispatches nothing |
| turn on session auto-registration | [`bin/hooks/WIRING.md`](bin/hooks/WIRING.md) — staged, applied by a human, because `.claude/settings.json` is the trust boundary |

Working in this folder as an agent? Start with [`AGENT.md`](AGENT.md) — it is the local
authority, and its seven ground rules are binding.

---

## Current state — 2026-08-17

The full chain was run end-to-end on this machine on 2026-08-12, re-measured **inside real
cloud VMs** by seven probes on 2026-08-14, and has since been pushed considerably further:
a cloud session has closed six documented gaps unattended (2026-08-16), spawned genuinely
concurrent subagents (2026-08-16), and run an **entire workflow** — coordinator included —
fanning out to subagents and opening one PR (2026-08-17). Dispatch, headless steering,
teleport and routines are verified working. Remote Control **connects**, with the phone
half untested; interactive terminal attach is **gated off** for this account.

The single most important open item is **G24**. Run at the same commit, the byte-identical
`tests/run.sh` reported **627 checks on macOS and 421 on Linux**, with only 6 of them
declared skipped — so roughly **206 checks silently never execute** on the platform every
cloud VM runs, and the tally prints a confident number either way. Every "green on Linux"
claim in this folder inherits that uncertainty. It is being diagnosed on a cloud VM,
because it is not reproducible from macOS. Details:
[`OPEN-GAPS.md`](archive/foreign-repo-provenance-2026-08-18/OPEN-GAPS.md).

| Capability | How | Status |
|---|---|---|
| Dispatch work to a cloud VM | `cs new` → *cloud*, or `cs start "<task>"` | **VERIFIED 2026-08-12** — `claude --cloud` run by hand |
| Work survives lid close and shutdown | it is not on your Mac | **VERIFIED 2026-08-12** |
| Steer a running session from a shell | `cs send <id> "…"` — no TTY needed | **VERIFIED 2026-08-12** |
| Steer from browser or phone | claude.ai/code, Claude mobile app | **VERIFIED 2026-08-12** |
| Pull the session back into your terminal | `cs tp <id>` | **VERIFIED 2026-08-12** |
| Attach a terminal to a live cloud session | `claude --cloud <id>` | **not enabled for this account** — a rollout gate, not a bug |
| Scheduled / triggered cloud runs | `/schedule`, routines | **VERIFIED 2026-08-12** — created, fired, and used to dispatch every probe |
| A cloud session pushes a branch back to GitHub | `claude/` branches | **VERIFIED 2026-08-12** |
| A cloud session opens a pull request | `gh pr create` from the VM | **VERIFIED 2026-08-14** — PR #1, by probe 7 |
| This repo's hooks and deny rules reach a cloud VM | `.claude/` is tracked now | **VERIFIED 2026-08-14** — hooks fire (probe 6); deny rules load and refuse (probe 7), though not at the filesystem layer |
| A cloud session's own telemetry survives the VM | — | **NO, by decision.** Hooks fire; the record is gitignored and dies with the machine. Sessions are told not to commit it — [why](archive/foreign-repo-provenance-2026-08-18/verified-facts.md#the-first-fix-was-wrong-and-was-reverted-the-same-hour--superseded-2026-08-14) |
| Subagents work inside a cloud session | the Task/`Agent` tool | **VERIFIED 2026-08-16** — three ran genuinely concurrently, and child tool calls land in telemetry under distinct session ids |
| A cloud session closes documented gaps unattended | a routine + a written brief | **VERIFIED 2026-08-16** — six gaps (G06–G09, G11, G13), none requiring `atlas-os/bin/**` |
| A cloud session runs a whole workflow and opens one PR | `/cloud-workflow` | **VERIFIED 2026-08-17** — coordinator + 2 concurrent subagents, one PR |
| `allowed_tools` restricts what a cloud session may do | — | **NO.** It is an auto-approval list, not a sandbox — `VERIFIED 2026-08-17` |
| An explicit `mcp_connections: []` blocks connector inheritance | — | **NO** — `V-041`, `VERIFIED 2026-08-17`. An empty list reads as *unspecified*, not *none*. Read the routine back after creating it |
| `cs` works on Linux as well as macOS | `bash tests/run.sh` | **VERIFIED 2026-08-14**, after four BSD/GNU bugs — **but see G24**: the Linux run executes ~206 fewer checks than the macOS run and does not say so |
| Keep a session local, drive it from your phone | `cs rc` | **connects**; phone half untested |
| This folder ports cleanly to another repo | copy + checklist | **PARTIAL** — the pre-exodus command set transplants green; `board`/`exodus`/auto-registration refuse loudly without `atlas-os`; **seven** things break silently unless the checklist is followed |

**On this machine, today:**

```
$ bash cloud-sessions/tests/run.sh 2>&1 | tail -2
628 passed  0 failed  0 xfail(known-open)  0 skipped
green.  No regression in the ten reviewed defects.
```

### Three things had to be fixed to get here

- **There was no `claude` CLI on this machine.** The only copy was the VS Code extension's
  private binary, so every `claude …` instruction in the older record was unrunnable. Now
  installed at `~/.local/bin/claude` and on PATH — **2.1.231** as of 2026-08-14, which is
  also what the cloud VMs run.
- **The implementation the older record documents no longer exists on disk** — the
  `longrun` scripts, its runbook and its research files are all gone.
- **GitHub was never connected to the Claude account**, so dispatch was silently
  *bundling* the repo instead of cloning it — no branches, no PRs, routines failing with a
  403. Fixed with `/web-setup`. `gh` being authenticated locally is a different grant and
  looks identical from the outside. See
  [verified-facts 4a](archive/foreign-repo-provenance-2026-08-18/verified-facts.md#4a-but-it-was-a-bundle-not-a-github-clone--verified-correction).

A fourth, since 2026-08-14: **`cs` had four BSD-vs-GNU bugs** that made it fail on the
Linux VM it dispatches to, found one at a time because each was hidden behind the last —
and two failed *silently*. That is the most instructive sequence in the folder; the seven
probe reports are indexed in
[`reference/README.md`](reference/README.md#the-seven-cloud-vm-probes--2026-08-14).

---

## Layout — exactly two files at the root, by rule

**Only `AGENT.md` and `README.md` live at the root of this folder. Everything else goes in
a subfolder.** If you need a new document it belongs in `playbooks/` (how to do a thing)
or `reference/` (what is true about a thing) — and a new reference file needs a row in
[`reference/README.md`](reference/README.md) the same hour it lands.

The reason is not tidiness. A flat root is where a folder like this dies: a reader arriving
cold cannot tell an instruction from a claim from a dated snapshot, and starts trusting a
three-day-old probe report as current state. The two-way split *is* the epistemics —
`reference/` carries status tags and dates because it must, `playbooks/` does not because
it is instructions. A file at the root belongs to neither category and so escapes both
obligations. **A porter copying this folder into a new repo will feel the pull to drop a
`PORTING-NOTES.md` at the root. Don't.**

```
cloud-sessions/
├── AGENT.md          operating rules for agents working here — the local authority
├── README.md         this file
├── bin/
│   ├── cs            the one entrypoint
│   ├── README.md     per-command internals, the git gate, how it resolves `claude`
│   └── hooks/        session_register.sh + WIRING.md — staged, human-applied
├── playbooks/        01 setup · 02 dispatch · 03 steer & return · 04 remote control
│                     05 routines · 06 environments · 07 cloud by default
│                     08 concurrent sessions & exodus · 09 porting to another repo
│                     10 fleet
├── reference/        surfaces · limits · troubleshooting · networking ·
│                     how-others-build-this (the dated probes/audits/evidence-record/
│                     gap-register are archived, below — see reference/README.md)
├── tests/            run.sh — the regression suite for bin/cs. $0, offline, dispatches
│                     nothing. doc-drift.sh guards reference wording. RESULTS.md is the log
├── state/            sessions.jsonl — the dispatch ledger, truncated 2026-08-18, tracked
│                     on purpose
└── archive/          2026-07-29 — the original research record, superseded, read-only
                      foreign-repo-provenance-2026-08-18 — this folder's corpus as
                      originally verified against a different repo, moved not rewritten
```

## The archive

[`archive/2026-07-29/`](archive/2026-07-29/) is the original investigation, kept whole. Its
conclusions are superseded — the cloud path it called "the highest-value untested lane" is
now the verified default, and the paid VM it was blocked on is very likely unnecessary.
It is not deleted, because the reasoning is still the best account of **why the local
approaches fail**:

- [`PROBLEM.md`](archive/2026-07-29/PROBLEM.md) — the four failure modes, why `caffeinate`
  cannot survive a lid close (with Apple's own words), and why `nohup … & disown` does not
  detach a process
- [`lanes/`](archive/2026-07-29/lanes/) — one file per approach, including the two that
  were dropped and why
- [`decisions/DECISIONS.md`](archive/2026-07-29/decisions/DECISIONS.md) — locked calls and
  what would reverse them
- [`STATUS.md`](archive/2026-07-29/STATUS.md) — including an incident where a test suite
  disabled the very protection it was verifying, twice, and still reported all green

Treat it as read-only. Corrections go in `reference/`, never into the archive.

## What it costs

No separate compute charge. A cloud session draws on the same subscription rate limits as
any other Claude usage. It is cheaper than the always-on Linux box this workstream was
about to buy in July (~€16–24/mo), and far cheaper than parking sessions in GitHub Actions
(~$259/mo, and against GitHub's terms besides) — both written up with the reasoning in
[`reference/surfaces.md`](reference/surfaces.md#what-is-deliberately-not-here). Whether
anything here can ever bill dollars, and the one setting that would change that, is in
[`billing.md`](archive/foreign-repo-provenance-2026-08-18/billing.md).

## Open questions

The full ranked register is [`OPEN-GAPS.md`](archive/foreign-repo-provenance-2026-08-18/OPEN-GAPS.md) — read its
staleness note in [`reference/README.md`](reference/README.md) first. The questions a
*person* can answer, as opposed to a probe:

| # | Question | How to answer |
|---|---|---|
| 1 | How long is a cloud session's idle expiry? | Undocumented. Leave one idle and time it (`G21`) |
| 2 | Does Remote Control connect from the phone? | `cs rc`, then open it in the mobile app (`G22`) |
| 3 | Can interactive attach be enabled? | It is a rollout gate — ask the Anthropic account team |
| 4 | Is the always-on Linux box (`V-095`) now unnecessary? | Probably yes. Answer 2 and 3 first; the only remaining gap is terminal attach, which a VPS does not fix |
| 5 | Should cloud-session telemetry ever reach the committed ledger? | Founder call, filed as **V-020** / `G14`. The obvious fix — have the session commit its own trace — was shipped and reverted; a self-committed record is cooperative and silently truncated |
| 6 | Is `atlas-os/bin/**` human-only in any enforceable sense? | Founder call, filed as **V-023** / `G05`. Probe 7 wrote into it from a cloud session with `python3 -c open()`; the guard matches tool names and Bash idioms, not filesystem writes. It is a measured defect, **not** permission to route around it |

**Answered since:** *"Does opening a PR from a cloud session work?"* — yes, PR #1, probe 7,
2026-08-14. *"Do subagents work in the cloud?"* — yes, 2026-08-16. *"Can a cloud session
run a whole workflow?"* — yes, 2026-08-17.
