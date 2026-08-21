# Open gaps — the cloud-sessions workstream

**Status:** ACTIVE · rebuilt 2026-08-17 · this Mac · macOS 26.5.1 arm64 · `claude` 2.1.231 ·
repo at `80fcf8c` (`2026-08-17T17:36:04-07:00`), branch `main`, concurrent lanes active.

> **On the date.** The brief commissioning this rebuild dated it 2026-08-18. Every
> measurement quoted below was taken with this machine's clock reading **2026-08-17**, and
> `HEAD` was committed `2026-08-17T17:36:04-07:00`. Dated by measurement, not by brief —
> ground rule 4 applies to the register's own header as much as to anything it lists.

What is still open, ranked by **how likely it is to make something report success while
doing nothing**. Not by difficulty, not by age, not by who filed it.

Status tags follow [`../AGENT.md`](../AGENT.md) ground rule 1: `VERIFIED` (run here, output
quoted) · `DOCS` · `UNVERIFIED` · `DISPROVEN`. Every gap below carries the command or
`file:line` that establishes it. Where I could not re-run something, I say so rather than
inheriting the claim.

> **This file is a register, not an authority.** Canonical claims live in
> [`verified-facts.md`](verified-facts.md); founder decisions live in
> [`../../atlas-os/queue/QUEUE.md`](../../atlas-os/queue/QUEUE.md), which is written only by
> `queue.py`. Nothing here was filed to the queue — several items below *should* be, and
> that is a human's call.

**Read first:** [G34](#g34--the-ledger-and-the-board-are-two-claim-registries-that-cannot-see-each-other--verified-2026-08-18),
in section (a). It is the only open item that undermines every other measurement in this
folder, and a cloud session is diagnosing it right now.

---

## What changed in this rebuild — 2026-08-17

The previous compile was dated 2026-08-15 at `767adf3`. It had gone stale in four
separate ways, three of which are the register committing the failure it exists to catch:
a document reporting a state it had not re-measured.

| Defect in the register | Fixed how |
|---|---|
| **Six closed gaps still listed as open.** G06, G07, G08, G09, G11, G13 were closed by a cloud session on 2026-08-16 ([`gap-closure-2026-08-16.md`](gap-closure-2026-08-16.md)). The register never learned | Each one re-verified **on disk here**, not read from the report, then moved to section (d) as D13–D18 with its confirming command |
| **`(a) … — 5` in prose over a 6-row table.** A known off-by-one, uncorrected | Section (a) now carries **7** rows and the header says 7 (this cell itself said "6" until 2026-08-18, reproducing the very off-by-one it announces fixing). Every count line in this file is derived from the section it labels |
| **Nothing filed since 2026-08-15.** Four dated reports and three new `verified-facts.md` findings had landed with no register entry | Eight new gaps, G25–G32, each with what is wrong, what it costs, and who can close it |
| **G04's evidence had been overturned by a commit.** The register asserted `STOP` is a tracked empty file at `HEAD`; it was removed from the index in `f28e861` | G04 rewritten in place, **superseded block preserved** per ground rule 5. The correction inverts who was right — see G04 |

**ID hygiene.** IDs are never reused. `G06`, `G07`, `G08`, `G09`, `G11` and `G13` are
**retired** — they name closed gaps and now appear only in section (d). New work takes
`G33` and up. `G24` was renumbered from an earlier draft because `G20` was already the
submodule question; verified today that no other collision exists:

```
$ grep -oE '\bG[0-9]{2}\b' OPEN-GAPS.md | sort | uniq -d
(no output — every ID appears in exactly one gap heading; duplicates below are references)
```

---

## The ordering rationale, stated plainly

A gap that makes a mechanism **report success while doing nothing** outranks a missing
feature, an unmeasured number, and an honestly-labelled unknown — every time.

The reasoning is this folder's own ground rule 4: *never let a degraded condition read as a
meaningful value*. A missing capability announces itself the first time you need it. A
guard that prints green while not guarding never announces itself at all, and every reader
downstream inherits the false belief. This workstream has already produced that failure
four separate ways — twelve refusal checks asserting against an exit code that was always
0 (probe 4), a dispatch silently bundling instead of cloning for two days
([`verified-facts.md`](verified-facts.md)), five of ten reviewed `cs` defects that "printed
green success while doing the wrong thing", and — measured 2026-08-17 — a routine created
with an explicit empty connector list coming back holding Gmail and Drive (G25).

So the tiers are:

| Tier | Shape | Which gaps |
|---|---|---|
| 1 | A guard, gate, brake or **control field** that reports success while not controlling | G34, G01, G02, G25, G03, G04, G05, G33 (G24 closed 2026-08-18) |
| 2 | A **claim tagged VERIFIED whose evidence does not support it** — the reader has no signal at all that anything is wrong | **none currently open.** All four (G06–G09) closed 2026-08-16; the tier is kept because it recurs |
| 3 | A check that **cannot fail, cannot pass, or cannot run**, where the outcome is read as a verdict | G10, G32, G31 |
| 4 | A stated capability or documented instruction that is **absent, ignored, or wrong** | G30, G28, G27, G29, G12, G15, G14 |
| 5 | **Honestly-labelled unknowns** — named as unverified, costing only confidence | G16, G17, G26, G18, G19, G20, G21, G22, G23 |

Tier 2 is deliberately above tier 3. A skipped check announces itself in the summary line;
a wrong `VERIFIED` tag does not. **Tier 2 being empty is the single best news in this
rebuild, and it is also the least durable** — it was empty because one cloud session spent
an afternoon on it, and [`../tests/doc-drift.sh`](../tests/doc-drift.sh) is the only thing
now standing between those four corrections and a silent revert.

---

## Ranked master list

| # | Gap | Section | Who closes | Silent? |
|---|---|---|---|---|
| G24 | The suite ran ~206 fewer checks inside a cloud session and did not say so — **CLOSED 2026-08-18** | (d) | closed by cloud lane | — |
| G01 | Six heartbeat gate defects — patch **staged, not applied** | (b) | human, I10 | **yes** |
| G02 | `refuses()` credits any non-zero exit as a refusal (`V-035`) | (b) | human, I10 | **yes** |
| G25 | An explicit `mcp_connections: []` does **not** prevent connector inheritance — *and is filed under a number that belongs to something else* | (b) | founder | **yes** |
| G03 | `FREEZE` mode is documentation only — the incident brake does nothing | (b) | human, I10 | **yes** |
| G04 | `STOP` no longer ships at all — a fresh clone and every cloud VM start **unbraked** | (b) | founder (`V-034`) | **yes** |
| G05 | I10 is a tool-layer matcher, not a filesystem guard (`V-023`) | (b) | founder | **yes** |
| G33 | A one-off routine titled "disable after firing" was never confirmed disabled | (b) | founder / trigger owner | **yes** |
| G10 | `append-only:atlas-os/telemetry/runs` is a check that can only ever fail (`V-013`) | (b) | human, I10 | **yes** |
| G32 | The reference-index workflow's done-when check **can never pass**, whatever the content | (a) | agent | **yes** |
| G31 | `cs sessions` has **zero** regression checks; a green suite is not "ported" | (a) | agent (tests lane) | **yes** |
| G30 | Two of three install docs still say `ln -sf`; `cs` mis-roots through a symlink | (a) | agent (cs lane) | **yes** |
| G28 | `cs env` reads the project settings layer from `cwd`, not the repo root | (a) | agent (cs lane) | **yes** |
| G27 | A fresh VM's `origin/main` is days stale, and no brief warns a session | (a) | agent (cs lane) | **yes** |
| G29 | Placement at `<repo-root>/cloud-sessions` is load-bearing; nothing enforces it | (a) | agent (cs lane) | partly |
| G12 | Telemetry `run_id` coerced to string — known-defect, open | (b) | human, I10 | **yes** |
| G15 | Heartbeat lanes go STALE while working; cloud lanes can never be visible (`V-021`) | (b) | founder | partly |
| G14 | Cloud-session hook telemetry dies with the VM; `promote.py` is called by nothing (`V-020`) | (b) | founder | no |
| G16 | `github(cloud)` is **not checkable** from this machine, by construction | (c) | measurement only | **yes** |
| G17 | The real CLI's `View:` line could change and the suite would stay green | (c) | one real dispatch | **yes** |
| G26 | Whether an *empty* `allowed_tools` differs from a populated one | (c) | one dispatch | **yes** |
| G18 | Eight unmeasured network-policy behaviours | (c) | probe | no |
| G19 | `--environment` with an `env_…` id — does it do anything? | (c) | one dispatch | **yes** |
| G20 | Whether a cloud VM clones or bundles submodules (`twenty/`) | (c) | probe | no |
| G21 | Cloud session idle expiry duration; routine daily cap for Max | (c) | measurement / claude.ai | no |
| G22 | Remote Control's phone half is untested | (c) | five minutes with a phone | no |
| G23 | `cs shell-init` has never lived in a real `~/.zshrc` | (c) | one operator, one week | no |

**Counts:** (a) 7 · (b) 11 · (c) 9 · **27 open** · (d) 24 resolved.

*Before this rebuild:* (a) 5 declared / 6 listed · (b) 8 · (c) 8 · (d) 12 · 22 open.
Six moved out of (a); eight new gaps entered; twelve items entered (d).

Every count above is derived from the section it labels, and the derivation is a command
anyone can re-run:

```
$ awk '/^## \(a\)/,/^## \(b\)/' OPEN-GAPS.md | grep -cE '^### G'      # 7
$ awk '/^## \(b\)/,/^## \(c\)/' OPEN-GAPS.md | grep -cE '^### G'      # 11
$ awk '/^## \(c\)/,/^## \(d\)/' OPEN-GAPS.md | grep -cE '^### G'      # 9
$ grep -cE '^\| G[0-9]{2} \|' OPEN-GAPS.md                            # 27 master-table rows
$ grep -oE '^\| G[0-9]{2} \|.*\| \((a|b|c)\) \|' OPEN-GAPS.md | grep -oE '\((a|b|c)\)' | sort | uniq -c
   7 (a)
  11 (b)
   9 (c)
```

The master table and the sections agree row for row. That check is in the file because the
previous compile's `(a) … — 5` over a six-row table is the same defect class as everything
this register tracks: a stated number that nothing re-derived.

---

## G34 · The ledger and the board are two claim registries that cannot see each other — `VERIFIED 2026-08-18`

Tier 1 by this register's own ranking: it makes the master view report success while
showing nothing.

`cs track --owns "<paths>"` records an ownership claim in
[`../state/sessions.jsonl`](../state/sessions.jsonl). `atlas-os/bin/heartbeat.py` records a
separate one on the board. **Neither reads the other:**

```
$ grep -c "sessions.jsonl\|cloud-sessions/state" atlas-os/bin/heartbeat.py
0

$ cs board
  ledger     ok       14 row(s)          <- reports healthy
  (no cse_ row in the merged table, no ledger-sourced owns in the owns column)
```

So a cloud lane's claim is invisible to `heartbeat.py overlap` — the gate whose entire job
is refusing collisions — and invisible in `cs board`'s `owns` column, which is sourced only
from board rows. The `ledger` source line still prints `ok`, so **absence reads as "nothing
claimed"**: ground rule 4, in the one view built to prevent exactly this.

**This is not hypothetical; it fired during the work that wrote this entry.** On 2026-08-17
the coordinator gave the cloud probe `--owns cloud-sessions/reference/OPEN-GAPS.md` via
`cs track`, then registered a local lane on the same path via `heartbeat.py`. Both claims
succeeded. Neither tool could see the other, so no gate refused, and two rebuilds of this
file were in flight at once — the precise collision `G01`'s staged patch exists to prevent,
occurring inside the work that documents `G01`.

**How to close it:** `cs board` must merge ledger `owns` into the same namespace it renders
for board rows (they key differently — `cse_…` versus a lane name — which is why "merged on
session id" silently drops them), and `cs exodus`/`/cloud all` must treat a ledger claim as
a claim. The deeper fix is one registry, not two. Deferred one cycle deliberately: the
regression suite that would guard it, `tests/run.sh`, is claimed by a live cloud lane this
hour, and shipping the fix without its check would be the same class of mistake.

## (a) Closable by an agent right now — 7

These need no founder decision, no Anthropic change, and touch no I10-guarded path.

**Lane-ownership caveat.** `cloud-sessions/tests/run.sh` is owned by a live cloud lane this
hour and `cloud-sessions/bin/cs` has uncommitted edits from a sibling lane. An agent can
close all six; *this* agent owned exactly one path
(`cloud-sessions/reference/OPEN-GAPS.md`) and wrote nothing else.

### G24 · The suite ran ~206 fewer checks inside a cloud session and did not say so — **CLOSED 2026-08-18**

**Closed** by `cse_01GLiZoKBW37NrAduMzsTLwE`, branch `claude/lane-linux-gapclose`, merged to
main. The original framing — preserved below per ground rule 5 — was wrong in an instructive
way: the split is not macOS vs Linux but *where the suite runs*, inside a CCR session versus
on an operator's own machine, and it reproduces on any platform. Three causes; the second
matters more than the missing checks:

1. Operator environment leaked into the hook fixtures — `env VAR=val bash "$HOOK"` adds
   variables but never clears the parent's exports, so `CLAUDE_CODE_REMOTE*` passed through.
2. **~10 checks were passing vacuously.** `session_register.sh:121` no-ops when `id -u` is 0
   — true on every cloud VM (`G11`) — and those checks assert "no change", which cannot tell
   a correctly guarded refusal from a hook that never tried. Green for the wrong reason.
3. Two collapse lines silently stood in for **62** and **29** individual checks.

Now: named skips, a `COLLAPSED` counter, and a section-18 parity check re-derived from the
live script text with a negative control (`nc/acct-parity-can-go-red`). Measured after:
**500/0/14-named** on a cloud VM, **631/0/0** on this Mac. Evidence:
[`verified-facts.md`](verified-facts.md).

Original filing, superseded:

The single most dangerous open item, because it undermines every other measurement in this
folder.

The same `cloud-sessions/tests/run.sh`, byte-identical (`git diff` empty), at the same
merged commit:

| Platform | passed | failed | skipped | **total** |
|---|---|---|---|---|
| macOS (this Mac) | 626 | 1 | 0 | **627** |
| Linux cloud VM, root | 414 | 1 | 6 | **421** |

Same single failure on both, so the suite is running the same *file* — but **~206 checks
never execute on Linux and only 6 are reported as skipped.** The tally prints a confident
number either way.

This is ground rule 4 and ground rule 7 turned on the suite itself: a green run on Linux
would measure two-thirds of what a green run on macOS measures, and nothing in the output
says so. Every "verified in a bare transplant" and "green on Linux" claim in this folder
inherits that uncertainty — including the porting audit's `467 passed` inside the
transplant, which was run on macOS and so is unaffected, and the gap-closure session's
Linux runs, which are.

**Re-measured here today**, so the macOS half is current rather than inherited:

```
$ bash cloud-sessions/tests/run.sh 2>&1 | tail -2
628 passed  0 failed  0 xfail(known-open)  0 skipped
green.  No regression in the ten reviewed defects.
```

macOS is now **628 / 0 failed / 0 skipped** at `80fcf8c` — up from 627, and the one failure
in the table above (`tamper/bin-cs-unmodified`, an artifact of a sibling lane's uncommitted
edits) is gone. **The Linux side has not been re-measured at this commit**, so the ~206
figure is dated evidence from 2026-08-17, not a current delta. Do not quote it as today's
number.

**Not yet diagnosed.** The suite has no early `exit` (the one at line ~1780 is inside a
stub heredoc), and its only platform gates are the `id -u` root checks around `D3`, which
now account for a handful of checks at most since G11 replaced the root skip with a
bind-mount injection (see D17). Candidates: a section aborting mid-run without failing the
suite, a subshell dying under `set -o pipefail`, checks whose setup silently no-ops when a
BSD-only tool is absent, or the harness losing a `run` whose stub is unavailable.

**Who can close it, and a standing instruction.** A cloud session is diagnosing this
**right now** on branch `claude/lane-linux-gapclose`. **Do not pre-empt its answer, and do
not edit `cloud-sessions/tests/run.sh` while it holds that file.** The phenomenon does not
exist on macOS, which is exactly why it survived this long; a macOS agent cannot reproduce
it and would be guessing. That lane branch is not fetched into this checkout —
`git branch -a` here lists only `main` and `origin/main` — so this register cannot report
its progress and does not try to.

**Cost of leaving it open.** Every capability claim in this folder that rests on a Linux
suite run is, quantitatively, two-thirds of a claim, and nothing in the output discloses
that. The cloud is where sessions run unattended; it is also where the measurement is
weakest.

**How to verify it closed.** The suite prints per-section counts, a macOS run and a Linux
run of the same commit agree on the total, and any platform-conditional check reports as a
**loud skip** with a named reason rather than never appearing.

### G30 · The documented install mis-roots `cs`, and two of three install docs still teach it — `VERIFIED 2026-08-17`

**What it is.** `cs` resolves its own root from `BASH_SOURCE` **without resolving
symlinks** ([`../bin/cs`](../bin/cs), lines 14–21). Invoked through the documented symlink,
`HERE=~/.local/bin`, so `ROOT=~/.local`, so the ledger becomes
`~/.local/state/sessions.jsonl` — a file that does not exist. `cs ls` then prints
`no dispatches recorded` and **exits 0** over a real, populated ledger. Measured and
written up as break 2 of [`porting-audit-2026-08-17.md`](porting-audit-2026-08-17.md).

**Evidence it is still real, `VERIFIED` here 2026-08-17.** The resolution is unchanged:

```
$ sed -n '14,15p' cloud-sessions/bin/cs
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

$ wc -l < cloud-sessions/state/sessions.jsonl
      14
```

**Partly closed, and this is the part that matters.**
[`../README.md`](../README.md) was corrected on 2026-08-17 — it now ships a two-line
`exec` wrapper and carries a *"Superseded 2026-08-17"* block explaining why. The real
machine was fixed too; `~/.local/bin/cs` is a wrapper, not a symlink. **But two of the
three install documents still hand out the broken command:**

```
$ grep -n 'ln -sf' cloud-sessions/bin/README.md cloud-sessions/playbooks/01-setup.md
cloud-sessions/bin/README.md:11:mkdir -p ~/.local/bin && ln -sf "$PWD/cloud-sessions/bin/cs" ~/.local/bin/cs
cloud-sessions/playbooks/01-setup.md:73:mkdir -p ~/.local/bin && ln -sf "$PWD/cloud-sessions/bin/cs" ~/.local/bin/cs
```

Both are followed by a paragraph headed *"Why a symlink and not a PATH entry"* that
defends the symlink on security grounds — correct as far as it goes, and it now reads as an
endorsement of an install that silently blinds the tool.

**Who can close it.** An agent, two ways and both should happen: fix the docs
([`../bin/README.md`](../bin/README.md), [`../playbooks/01-setup.md`](../playbooks/01-setup.md))
to ship the wrapper, **and** fix the resolution in [`../bin/cs`](../bin/cs) with a
`readlink`/`pwd -P` loop over `BASH_SOURCE`. The wrapper is a workaround; the tool is still
wrong for anyone who installs it the documented way. Neither file is I10-guarded.

**Cost of leaving it open.** A new operator — or a port into another repo — follows the
setup playbook, gets a `cs` that reports an empty ledger with exit 0, and has no signal
that anything is wrong. Ground rule 4 in its purest form: empty-but-fine standing in for a
populated answer. It has gone unnoticed here only because in-repo sessions invoke
`cloud-sessions/bin/cs` by path.

**How to verify it closed.** All three install docs give the same working install; and
`cs ls` invoked through a symlink at `~/.local/bin/cs` prints the real 14-row ledger (or
refuses, naming the symlink) rather than `no dispatches recorded`.

### G28 · `cs env` reads the project settings layer from `cwd`, not the repo root — `VERIFIED 2026-08-17`

**What it is.** `settings_env_id()` in [`../bin/cs`](../bin/cs) joins `.claude/settings.json`
onto `os.getcwd()`. Run `cs env show` from the repo root and a project pin is found; run it
from any subdirectory and the same pin is invisible, and the answer is tagged `ok`.

**Evidence it is real, `VERIFIED` here 2026-08-17** — the resolver is unchanged:

```
$ awk '/^settings_env_id\(\)/,/^}/' cloud-sessions/bin/cs | sed -n '4,6p'
    (os.path.join(os.getcwd(), ".claude/settings.local.json"), "project-local"),
    (os.path.join(os.getcwd(), ".claude/settings.json"), "project"),
    (os.path.expanduser("~/.claude/settings.json"), "user"),
```

Measured in the transplant as break 4 of
[`porting-audit-2026-08-17.md`](porting-audit-2026-08-17.md): with a project pin at the
root, `cs env show` from the root says `pinned  env_ProjectPin (from project settings)`;
from a subdirectory it says `pinned  none`.

**Who can close it.** An agent — resolve the repo root with `git rev-parse --show-toplevel`
and fall back to `cwd` only outside a work tree. [`../bin/cs`](../bin/cs) is folder-owned,
not I10.

**Cost of leaving it open.** Two costs, and the second is the expensive one. `cs env show`
from a subdirectory gives an `ok`-tagged wrong answer about which environment a dispatch
will land in — and the environment decides how much internet the session has and whether it
runs on Anthropic's infrastructure at all. Worse, `cs env set` from a subdirectory misses
the `NOT IN EFFECT` warning for the same reason, so it writes the user layer, reports
success, and is silently outranked by the project pin it could not see. That warning exists
precisely because the user layer is the lowest.

**How to verify it closed.** `cs env show` returns the same answer from the repo root and
from three levels down, and `cs env set` emits the higher-precedence-layer warning from
both.

### G31 · `cs sessions` has zero regression checks — a green suite is not "ported" — `VERIFIED 2026-08-17`

**What it is.** Break 7 of [`porting-audit-2026-08-17.md`](porting-audit-2026-08-17.md)
found `tests/run.sh` had **zero** checks touching `cs sessions`, `cs board` or `cs exodus`,
because section 3 tests "every command in `bin/README.md`" and that table omitted all
three. So a transplant's `467 passed / 0 failed` was real *and* said nothing about the three
commands that couple to `atlas-os/`.

**Two-thirds closed since, `VERIFIED` here 2026-08-17:**

```
$ for c in sessions board exodus; do printf '%-10s %s\n' "$c" "$(grep -cE "\"$c/" cloud-sessions/tests/run.sh)"; done
sessions   0
board      32
exodus     52
```

`cs board` and `cs exodus` are now covered. **`cs sessions` is still at zero** — and it is
not a leaf: `cs board` shells out to `cs sessions --json` for one of its four sources, so a
regression in `sessions` reaches the board through a path no check exercises directly.

The `bin/README.md` half is fixed at the dependency line —
[`../bin/README.md`](../bin/README.md) line 80 now reads *"'Nothing outside this folder' was
true until 2026-08-17 and is now wrong"* — but the **command table in that same file still
has no row for `cs sessions`, `cs board` or `cs exodus`**, which is the omission that
generated the coverage hole in the first place:

```
$ grep -cE '^\| `cs (sessions|board|exodus)' cloud-sessions/bin/README.md
0
```

**Who can close it.** An agent, in the tests lane — add `cs sessions` checks to
[`../tests/run.sh`](../tests/run.sh) and the three missing rows to
[`../bin/README.md`](../bin/README.md). **`tests/run.sh` was held by the G24 lane and is now released (merged 2026-08-18); it was right
now**; the `bin/README.md` half is free.

**Cost of leaving it open.** Ground rule 7 applied to the suite itself: green is not
"ported", and the mechanism that produced the hole (a table omission silently deciding test
coverage) is still in place, so the next command added without a table row is untested the
same way and nothing says so.

**How to verify it closed.** `grep -cE '"sessions/' cloud-sessions/tests/run.sh` is
non-zero, the three rows exist in `bin/README.md`'s command table, and a deliberate break in
`cs sessions --json` turns a check red.

### G27 · A fresh VM's `origin/main` is days stale, and no brief warns the session — `VERIFIED 2026-08-17`

**What it is.** A cloud VM checks out the correct commit but its **remote-tracking ref
points days back**. From [`verified-facts.md`](verified-facts.md), measured on a real VM:

```
$ git rev-parse HEAD origin/main
f144b17b88fe891951566e262f60da8ce39c0b4e     <- correct, today
2258ab1a47f2da0ee030473f1192ce636c3d48a0     <- days old
```

`HEAD` is right, so work proceeds correctly. The trap is any session that *reasons about*
`origin/main`. `git diff origin/main HEAD` reported a **59.8 KB** diff the session had to
persist to a file; `git log origin/main..HEAD` lists commits long since merged.

**A second, independent instance of the same class**, from
[`workflow-bootstrap-2026-08-17.md`](workflow-bootstrap-2026-08-17.md) §6a: `origin/main`
also **moved mid-dispatch** — a forced update five commits ahead, landed by activity outside
the session while it ran. The coordinator had to merge `origin/main` into its integration
branch before opening the PR, a step the brief never named.

**Who can close it.** An agent. The instruction belongs in the brief that `cs handoff`
composes ([`../bin/cs`](../bin/cs)) and in the two command files that compose dispatch
payloads ([`../../.claude/commands/cloud.md`](../../.claude/commands/cloud.md),
[`../../.claude/commands/cloud-workflow.md`](../../.claude/commands/cloud-workflow.md)).
None is I10-guarded.

**Cost of leaving it open.** A session asking *"am I up to date with main?"* gets a wrong
answer, and one asking *"what did I change?"* gets days of other people's work mixed into
its own diff — silently, with no error anywhere. It has already cost one dispatch a 59.8 KB
detour and one coordinator an unwritten merge step. Both recovered; neither was warned.

**How to verify it closed.** The composed brief tells a cloud session to diff against its
dispatch-time `HEAD`, or to `git fetch origin` before comparing, and says outright that the
remote-tracking ref a fresh VM hands you is stale. A dispatched session's log shows the
fetch.

### G29 · Placement at `<repo-root>/cloud-sessions` is load-bearing and nothing enforces it — `VERIFIED 2026-08-17`

**What it is.** The folder must land at exactly `<repo-root>/cloud-sessions`. Nothing
checks, and two surfaces degrade quietly when it does not. Break 5 of
[`porting-audit-2026-08-17.md`](porting-audit-2026-08-17.md), measured in a nested fixture
(`tools/cloud-sessions/`):

- `cs board` answered for the **wrong root** — `cs board — tools`, seeking the board at
  `tools/atlas-os/…`, because [`../bin/cs`](../bin/cs) resolves the repo as `$ROOT/..`
  rather than via git.
- the session-registration hook **exited 0 and created a stray
  `cloud-sessions/state/hook-errors.log` at the repo root** of a repo that had deliberately
  put the folder elsewhere.

**Evidence the hard-coding is still there, `VERIFIED` here 2026-08-17:**

```
$ grep -n 'cloud-sessions' cloud-sessions/bin/hooks/session_register.sh
127:  REPO_ROOT=${SELF_DIR%/cloud-sessions/bin/hooks}
129:  LOG="$REPO_ROOT/cloud-sessions/state/hook-errors.log"
151:  LOG="$MAIN_ROOT/cloud-sessions/state/hook-errors.log"
```

`cs exodus` alone survives nesting — it resolves through `git rev-parse --show-toplevel`,
and then looks for `atlas-os/` at that toplevel, so it fails for a different reason.

**Who can close it.** An agent — the same `git rev-parse --show-toplevel` fix as G28, plus
one sentence in [`../playbooks/09-porting-to-another-repo.md`](../playbooks/09-porting-to-another-repo.md)
stating that placement is a requirement, not a convention.

**Cost of leaving it open.** Marked *partly* silent rather than fully: `cs board` answering
for the wrong root is loud enough to notice if you read the header, but a hook that writes
a file into a directory the repo owner deliberately avoided, and exits 0, is not. This only
bites on a port — which is now a documented, playbooked activity.

**How to verify it closed.** With the folder at `tools/cloud-sessions/`, `cs board` names
the real repo root or refuses, and the registration hook writes nothing outside the folder's
own tree.

### G32 · The reference-index workflow's done-when check can never pass, whatever the content — `VERIFIED 2026-08-17`

**What it is.** [`../../docs/workflows/reference-index-refresh.md`](../../docs/workflows/reference-index-refresh.md)
line 33 states the workflow's acceptance condition as:

```
comm -23 <(ls reference/*.md | xargs -n1 basename | sort) \
         <(grep -oE '[a-z0-9-]+\.md' reference/README.md | sort -u)
```

The regex is **lowercase-only**. `OPEN-GAPS.md` and `README.md` are uppercase on disk, so no
substring of their literal filenames can ever match — the check reports both as un-indexed
regardless of what the README contains.

**Evidence it is real, with a control, `VERIFIED` here 2026-08-17:**

```
$ cd cloud-sessions
$ comm -23 <(ls reference/*.md | xargs -n1 basename | sort) \
           <(grep -oE '[a-z0-9-]+\.md' reference/README.md | sort -u)
OPEN-GAPS.md
README.md

$ # control: identical pipeline, only the regex made case-insensitive
$ comm -23 <(ls reference/*.md | xargs -n1 basename | sort) \
           <(grep -oiE '[a-z0-9_-]+\.md' reference/README.md | sort -u)
(no output)

$ grep -c '](OPEN-GAPS.md)' reference/README.md
2
```

The control is the point: flipping **only** the regex empties the output, so the regex is
the sole cause. `README.md` is the index itself and is footnoted exempt from listing
itself; `OPEN-GAPS.md` has two resolving links in it.

First named in [`workflow-bootstrap-2026-08-17.md`](workflow-bootstrap-2026-08-17.md) §6d,
which confirmed it against the pristine base commit — both files were already flagged
against a table that did not yet exist to be checked.

**Who can close it.** An agent — a one-character fix (`-oiE`, or `[A-Za-z0-9_.-]`) plus an
explicit exemption for `README.md`, in a `docs/` file that is not I10-guarded.

**Cost of leaving it open.** This is tier 3 inverted and it is the more corrosive
direction: a check that can never pass trains its readers to ignore it. The next dispatch of
this workflow gets two false positives on every run, and the run *after* someone starts
skimming them is the one where a genuinely orphaned file hides in the same two lines. That
this defect was found by a session reading its own done-when — rather than by the check
ever going green — is the whole argument.

**How to verify it closed.** The check prints nothing on a tree where every file is
indexed, and prints exactly the filename when a new `reference/*.md` is added without a
README row.

---

## (b) Blocked on a human — 11

Every item here sits behind `atlas-os/bin/**` (I10) or a founder decision. No agent can
close any of them, and an agent that "fixed" one would be bypassing the guard rather than
satisfying it.

> **These now have a founder-facing companion.** A sibling lane wrote
> [`founder-decisions-2026-08-18.md`](founder-decisions-2026-08-18.md) while this rebuild was
> in progress — the same items, grouped by *what the founder has to do* (five minutes ·
> a real decision · Anthropic's to fix) rather than by why an agent cannot. It reached its
> conclusions independently and **corrected this register twice**, both folded in below and
> re-verified here rather than inherited: the staged patch already enforces FREEZE (G03), and
> the connector finding is filed under a number that belongs to something else (G25).
>
> | This register | Sheet | | This register | Sheet |
> |---|---|---|---|---|
> | G01, G03 | F1 | | G12 | F10 |
> | G04 | F5 | | G14 | F11 |
> | G02 | F6 | | G25 | F4, F12 |
> | G05 | F7 | | G26 *(in (c))* | F13 |
> | G10 | F8 | | G27 *(in (a))* | F14 |
> | G15 | F9 | | G33 | — *(not on the sheet)* |
>
> Two items are on one side only, and both are worth a founder's eye: **G33** (an armed
> one-off routine) appears on no sheet, and the sheet's **F2**, **F3**, **F15** and **F16**
> are outside this register's scope.

### G01 · The six heartbeat gate defects — patch is staged, **not applied** · *re-checked 2026-08-17*

**What it is.** `atlas-os/bin/heartbeat.py` is the overlap gate that
[`../AGENT.md`](../AGENT.md) and [`../../atlas-os/playbooks/DISPATCH.md`](../../atlas-os/playbooks/DISPATCH.md)
rely on to stop concurrent agents colliding. Six recorded defects make it **grant a path it
should refuse, and print success while doing it**.

**Evidence the patch is still not applied.** `VERIFIED` here, 2026-08-17 — three
independent ways, unchanged from the 2026-08-15 compile:

```
$ git apply --check atlas-os/tests/heartbeat/fixes/heartbeat-defects.patch
(exit 0 — applies cleanly, therefore NOT yet applied)

$ git apply --reverse --check atlas-os/tests/heartbeat/fixes/heartbeat-defects.patch
error: patch failed: atlas-os/bin/heartbeat.py:58
error: atlas-os/bin/heartbeat.py: patch does not apply
(does not reverse, therefore NOT applied)

$ grep -c 'row_claims_for_gate' atlas-os/bin/heartbeat.py
0                                  # the patch introduces this symbol 3 times
```

The staged patch's own test log agrees
([`../../atlas-os/tests/heartbeat/fixes/RESULTS-fixes.md`](../../atlas-os/tests/heartbeat/fixes/RESULTS-fixes.md)):
*"Script under test … looks UNPATCHED (row_claims_for_gate absent)"*, and
`2 passed  7 failed  0 skipped` — the seven regression checks that map onto the six defects
all fail against the live script.

**The six, verbatim from [`../../atlas-os/tests/heartbeat/RESULTS.md`](../../atlas-os/tests/heartbeat/RESULTS.md)
— note every one is recorded under a check marked PASS.** The defect is the observed
behaviour, not a failing assertion:

| # | Defect |
|---|---|
| 1 | Claim matching is case-sensitive, the filesystem is not — two lanes are granted the same file, **"I3 is violated with the gate reporting success"** |
| 2 | A path containing a space is **silently split into two unrelated claims** — the file the operator meant is protected by neither, "Reports success" |
| 3 | A session id reused on another board makes each lane invisible to the other — **"the gate prints success and I3 is broken"** |
| 4 | **FREEZE is not enforced** (see G03) |
| 5 | A live row whose claim will not parse contributes **zero** claims to the gate — `register` hands the path away and prints success |
| 6 | `update --status ACTIVE` revives a dead row **without re-running the overlap gate** — corruption is detectable only after it is written |

That suite reports itself green while recording those defects — **a green suite and six open
defects, simultaneously** — because the checks assert what the tool *does*, and what it does
is wrong. A **seventh** exists that the staged patch does not close: an *empty* `owns_paths`
cell on a live row also protects nothing. Applying the patch does not finish the job.

**Who can close it.** A human under I10. `atlas-os/bin/**` is deny-guarded and — see G05 —
that guard is a matcher, not a lock, so "an agent could bypass it" is true and is not
permission.

**Cost of leaving it open.** Five of the six make the coordination gate hand one path to two
lanes while printing success. This is the mechanism the cloud workstream depends on to run
concurrent agents safely, and it is the exact failure it exists to prevent. Defect 2 is
worse than a no-op: the lane ends up holding two paths it never asked for, one of which is a
subtree claim that will refuse *unrelated* lanes.

**Newly relevant.** [`workflow-bootstrap-2026-08-17.md`](workflow-bootstrap-2026-08-17.md)
§6e records that the first end-to-end cloud workflow **registered nothing on any heartbeat
board at all** — isolation came from git worktrees and coordinator-assigned branch names
instead. That is a legitimate design (and was the brief's own rule), but it means the gate
these six defects sit in is now being routed *around* rather than fixed, and the concurrency
story has quietly forked into two mechanisms. Worth a founder's attention alongside the
patch. See [`../playbooks/08-concurrent-sessions-and-exodus.md`](../playbooks/08-concurrent-sessions-and-exodus.md).

**How to verify it closed.** `git apply --reverse --check` on the patch succeeds,
`grep -c row_claims_for_gate atlas-os/bin/heartbeat.py` is non-zero,
`bash atlas-os/tests/heartbeat/fixes/run-fixes.sh` reports 9/9 rather than 2 passed 7
failed, and the six `PASS · …` blocks in `RESULTS.md` no longer carry a `DEFECT:` note. The
seventh needs its own patch.

### G02 · `refuses()` credits **any** non-zero exit as a refusal — `V-035`

**What it is.** The four-line `refuses()` helper in `atlas-os/bin/verify_system.sh`
manufactures the false-green class at 4 call sites by accepting ANY non-zero exit as a
refusal — it cannot distinguish rc 2 (a real gate refusal) from rc 3 (a usage error) from
rc 127 (a missing binary), so **a check passes when its target is broken or absent**.

**Evidence it is still real, `VERIFIED` here 2026-08-17** — the helper is unchanged:

```
$ sed -n '30p' atlas-os/bin/verify_system.sh
refuses() { local name="$1"; shift; if "$@" >/dev/null 2>&1; then bad "$name" "command SUCCEEDED but should have refused: $*"; else ok "$name"; fi; }
```

There is no expected-exit-code argument anywhere in the signature. Filed as `V-035`, status
`OPEN`, source founder. The e2e run records it independently as F-7
([`../../atlas-os/tests/results/e2e-2026-08-15.md`](../../atlas-os/tests/results/e2e-2026-08-15.md))
— *"`refuses()` credits any crash as a refusal; 6 checks ride on it"* — and notes the same
copy exists in `proposed_checks.sh`. The queue item adds that *"Patching individual
instances has now failed five times."*

**Who can close it.** A human under I10 (`atlas-os/bin/verify_system.sh`).

**Cost of leaving it open.** This is the *generator* of the class, not an instance of it.
`verify_system.sh` is the suite every cloud probe ran to declare the repository healthy —
probes 1 through 5 all quote its result. A check whose target has been deleted reads as a
passed refusal, so the suite's coverage number means less than it says.

**How to verify it closed.** `refuses()` asserts the **specific** expected exit code; a
deliberately-removed target produces a FAIL rather than a PASS at all four call sites; the
mutation matrix in `atlas-os/tests/heartbeat-arch/` reaches past `heartbeat.py`.

### G25 · An explicit `mcp_connections: []` does **not** prevent connector inheritance — **NEW 2026-08-17**

> **The tracking number in every citation of this finding is wrong.** Five files in this
> folder cite it as **`V-041`**. `V-041` in the queue is a different item entirely —
> *"A self-perpetuating `send_later` chain ran unregistered for days"*
> (`atlas-os/queue/QUEUE.md:503`, created 2026-08-17). `VERIFIED` here 2026-08-17: the queue
> has **never heard of the connector finding**, under `V-041` or any other number, and IDs
> run to `V-058`. The five mis-citations are [`../README.md`](../README.md),
> [`../AGENT.md`](../AGENT.md), [`../tests/run.sh`](../tests/run.sh),
> [`../tests/RESULTS.md`](../tests/RESULTS.md) and [`verified-facts.md`](verified-facts.md).
> This register deliberately does **not** make it six — the finding is named here in words,
> not by a borrowed number. A citation that resolves to the wrong item is this folder's own
> failure shape one layer up: it looks like provenance and carries none. Filing it under a
> real number is item **F4** on [`founder-decisions-2026-08-18.md`](founder-decisions-2026-08-18.md).

**What it is.** This folder said since 2026-08-13 that routines silently inherit every
connected claude.ai connector, and prescribed the fix: *"Pass an explicit `mcp_connections`
list, or review the routine after creating it."* **The first half of that advice is wrong.**

A routine created with `"mcp_connections": []` in the body came back carrying four
([`verified-facts.md`](verified-facts.md), `VERIFIED 2026-08-17`):

```
Google_Calendar     calendarmcp.googleapis.com
Claude_Code_Remote  api.anthropic.com/v1/code/mcp/meta   (infrastructure, expected)
Gmail               gmailmcp.googleapis.com
Google_Drive        drivemcp.googleapis.com
```

An empty list reads as **"unspecified", not "none"**. A docs-only workflow was handed Gmail
and Drive, and ran with no approval prompts.

**This is why it is tier 1 and not a documentation nit.** It is a field that looks exactly
like a control and does nothing — the same shape as `FREEZE` (G03) and the `STOP` sentinel
(G04), except this one governs whether an unattended session can read the founder's mail.

**The documentation half is closed**, `VERIFIED` here 2026-08-17. [`../AGENT.md`](../AGENT.md)
now carries the correction, [`../../.claude/commands/cloud-workflow.md`](../../.claude/commands/cloud-workflow.md)
documents it inline, and two regression checks pin it:

```
$ bash cloud-sessions/tests/run.sh 2>&1 | grep -E 'wf/mcp'
  ok  wf/mcp-connections-empty
  ok  wf/mcp-empty-does-not-work
```

**What remains open, and needs a human.**

1. **No audit of what is already armed.** Every routine created on this account with an
   empty or omitted `mcp_connections` may be holding Gmail and Drive right now. Nobody has
   `get`-ed them back and read the field. This register did not do it either: reading the
   routine list is a trigger-management surface, and this lane's constraints forbid touching
   routines at all. The `/routines-audit` skill exists for exactly this.
2. **What value, if any, means "none", is `UNVERIFIED`.** Whether a *non-empty* list
   restricts rather than adds, and whether any value denies everything, both cost a dispatch
   each to settle. Until then the only proven control is the second half of the old advice:
   `get` the routine after creating it and read `mcp_connections` back; disable rather than
   fire if it holds connectors the work has no business with.

**Who can close it.** A founder — item 1 requires listing and disabling routines, item 2
requires spending dispatches. Neither is an agent's call.

**Cost of leaving it open.** Unattended sessions with unaudited mailbox and Drive access,
and a repo-wide privacy rule (`../../AGENT.md`: never let mailbox content or private contact
details into Git) whose enforcement currently depends on nobody having asked a routine to
read mail.

**How to verify it closed.** Every enabled routine on the account has had
`mcp_connections` read back and recorded; and a probe settles whether any value means
"none", with the result in [`verified-facts.md`](verified-facts.md).

### G03 · `FREEZE` is documentation only — the incident brake does nothing

**What it is.** Listed separately from G01 because it is a *brake*, not a gate, and because
two independent records confirm it was left deliberately.

[`../../atlas-os/tests/heartbeat/RESULTS.md`](../../atlas-os/tests/heartbeat/RESULTS.md),
verbatim:

> **FREEZE is not enforced.** `heartbeat/README.md` states 'FREEZE means no new
> registrations: integration, incident, or founder review in progress', and DISPATCH.md
> relies on it as the incident brake. `cmd_register` never reads `board.mode` except to flip
> IDLE→DISPATCH, so a registration during a freeze succeeds silently and the board still
> reads FREEZE afterwards. **The one control an operator has for stopping the world does
> nothing.**

[`../../atlas-os/tests/results/heartbeat-arch-2026-08-14.md`](../../atlas-os/tests/results/heartbeat-arch-2026-08-14.md)
lists it under "Defects deliberately left": *"Making it refuse is a policy change… Recorded
in the spec as a gap with the decision named as a founder's."*

**Evidence it is still real, `VERIFIED` here 2026-08-17.** `cmd_register` begins at
`heartbeat.py:935`; the only `board.mode` reference on that path is at `:990`, and it is the
IDLE→DISPATCH flip. Nothing between them tests for FREEZE:

```
$ grep -n 'board.mode\|def cmd_register' atlas-os/bin/heartbeat.py
935:def cmd_register(args):
990:        if board.mode == "IDLE":
```

**Corrected 2026-08-17 — the previous compile framed this wrongly.** It said the fix was a
policy call awaiting a founder before anyone could write code. **The staged patch already
contains the enforcement**, which I confirmed by reading it rather than taking the claim:

```
$ sed -n '149,155p' atlas-os/tests/heartbeat/fixes/heartbeat-defects.patch
+        if board.mode == "FREEZE":
+            print("REFUSED: %s is in FREEZE — no new registrations (integration, "
+                  "incident, or founder review in progress)." % rel(primary))
+            print("Existing lanes keep running; nothing new joins. `update` and "
+                  "`end` are unaffected.")
+            print("fix: wait for the freeze to lift, or have a human set "
+                  "`mode: DISPATCH` on the board.")
+            return 2
```

So **applying G01's patch *is* the policy decision** — after it, registering a lane on a
frozen board fails instead of succeeding silently. There is no separate code to write. The
only judgement left is whether freeze should stay advisory, and nothing in the record argues
that it should.

**Who can close it.** A human under I10, by applying the same patch as G01 — the FREEZE hunk
is not optional-extra, it is hunk five of six. See item **F1** on
[`founder-decisions-2026-08-18.md`](founder-decisions-2026-08-18.md), which reaches the same
conclusion independently.

**Cost of leaving it open.** An operator freezing the board during an incident gets a board
that says FREEZE, a registration that exits 0, and no indication the two disagree. Same
failure shape as G04 and G25 — a control that reads engaged and is not.

**How to verify it closed.** `heartbeat.py register` against a `mode: FREEZE` board exits
non-zero with a message naming the freeze, and check `07.16` asserts the refusal rather than
recording the success.

### G04 · `STOP` no longer ships at all — a fresh clone and every cloud VM start **unbraked** — `VERIFIED 2026-08-17`

> **Superseded 2026-08-17 — the previous entry is preserved because the correction inverts
> who was right.** The 2026-08-15 compile of this register asserted, with quoted output,
> that root [`../../AGENT.md`](../../AGENT.md) and `V-034` were **wrong** about `STOP`:
> `git ls-files STOP` returned `STOP`, `git ls-tree HEAD STOP` showed mode `100644` and the
> empty blob `e69de29…`, so `STOP` shipped as a tracked empty **file** and a fresh clone
> started *frozen*, in the self-deleting form. That was true at `767adf3`. It is no longer
> true: `STOP` was removed from the index in commit `f28e861` *("Hand-run the bug sweep, kill
> the chain, pave the road to the cloud")*. **The documentation was not wrong; the register
> was measuring a state the repository has since left.** Recorded rather than deleted, per
> ground rule 5 — the useful part is that a register can be overtaken by a commit between
> one compile and the next, which is the same failure class it exists to catch.

**What it is now.** `STOP` is untracked at `HEAD` and exists on this machine only as a local
directory. `VERIFIED` here, 2026-08-17:

```
$ git rev-parse --short HEAD
80fcf8c
$ git ls-files STOP
(no output)
$ git ls-tree HEAD STOP
(no output)
$ git status --porcelain STOP
(no output — nothing tracked, nothing pending)
$ ls -ld STOP
drwxr-xr-x  2 danieltecum  staff  64 Aug 17 16:40 STOP
$ git log --oneline --diff-filter=D -- STOP | head -1
f28e861 Hand-run the bug sweep, kill the chain, pave the road to the cloud
```

So root `AGENT.md`'s STOP block and `V-034` are **now literally correct**: git cannot track
an empty directory, `git ls-files STOP` is empty, and **every fresh clone and every cloud
session starts with no kill switch at all.** What was a documentation contradiction is now a
live, unmitigated condition — and `V-034`, which was previously arguing from a false premise,
is now arguing from a true one and is still `OPEN`.

**The local directory form is holding, and that is worth recording.** `verify_system.sh`
still ends its sentinel check with an unconditional `rm -f STOP` (line 145), preceded by
`touch STOP` (line 139). Against a directory both commands fail, so the mandated `mkdir STOP`
form survives a suite run — which is exactly the fix the root `AGENT.md` block describes.
The protection is real *here*; it simply does not travel.

**Who can close it.** A founder — `V-034` is a founder decision. The known bind is that the
obvious fix (committing `STOP/.gitkeep`) breaks the `rmdir STOP` unfreeze, because `rmdir`
refuses a non-empty directory. `V-030`'s framing ("STOP is a tracked empty FILE at HEAD") is
now the stale one, and should be marked as historical when the queue is next touched.

**Cost of leaving it open.** A cloud session runs unattended, on infrastructure that bills,
with **no brake at all** — not a self-deleting one, none. Anyone reasoning from the previous
version of this register ("cloud sessions start frozen, so the spend guard is present but
fragile") is now wrong in the more expensive direction. The repository has an operator-facing
kill switch that exists only on one laptop.

**How to verify it closed.** A founder decision recorded via `queue.py` that names what a
fresh clone materialises and by what mechanism, and `V-030` marked historical. The
verification is a decision, not a measurement.

### G05 · I10 is a tool-layer matcher, not a filesystem guard — `V-023`

**What it is.** `permissions.deny` matches tool names and Bash command prefixes. Anything
reaching the filesystem another way walks through.

**Evidence it is real.** Probe 7, six write attempts against `atlas-os/bin/**` from a cloud
VM ([`cloud-vm-probe-7-2026-08-14.md`](cloud-vm-probe-7-2026-08-14.md)):

```
$ python3 -c "open('atlas-os/bin/probe7-canary.py','w').write('# canary')"
(no output)   EXIT:0            ← the other five (Write, Edit, cp, echo >, tee) were BLOCKED
```

A control write to a non-guarded path succeeded, so the test had teeth. Filed as `V-023`,
OPEN. [`verified-facts.md`](verified-facts.md) records it as `DISPROVEN` and is right to.

**Who can close it.** A founder — `V-023` asks whether to enforce below the tool layer or
restate I10 as an intent. Also proposed as `V-026`: extend the guarded list to the
*detection* substrate, not only the rules.

**Cost of leaving it open.** [`../../atlas-os/reference/INVARIANTS.md`](../../atlas-os/reference/INVARIANTS.md)
describing I10 as ENFORCED overstates the mechanism — it holds against agents that cooperate
with it. This is load-bearing for every other (b) item in this list, all of which are
"blocked on a human because I10". If I10 is advisory, "blocked" means "conventionally
blocked", and that should be said out loud rather than assumed. Probe 7's own framing is the
right one and worth preserving: *a gap in enforcement is not permission.*

**How to verify it closed.** Either a filesystem-level control refuses the
`python3 -c open()` write, or `INVARIANTS.md` restates I10 as an intent with the matcher's
scope named.

### G33 · A one-off routine asked to be disabled after firing; nobody has confirmed it was — **NEW 2026-08-17**

**What it is.** [`workflow-bootstrap-2026-08-17.md`](workflow-bootstrap-2026-08-17.md) §6f
records that the coordinator session's own title, read back via `get_session`, was
*"workflow: reference-index-refresh coordinator (one-off, disable after firing)"*. The
session's dispatch constraints forbade it from touching trigger management, so it recorded
the observation and did not act. **No record anywhere says the routine was subsequently
disabled.**

**Why this is tier 1 and not housekeeping.** [`../AGENT.md`](../AGENT.md) states the rule
that makes it dangerous: *"Firing a routine does not disarm it. `run` does not consume
`run_once_at`, and a disabled routine still displays a future `next_run_at` — `enabled` is
the deciding field. Create → run → **disable**, as one sequence."* Nine probe routines were
left armed on 2026-08-14 by the person who wrote that warning. An armed routine is a
scheduled, unattended, billable dispatch that inherits the account's connectors (G25) and
starts on a VM with no kill switch (G04). The three compound.

**Status is `UNVERIFIED`, deliberately.** This register did not list the account's routines.
Reading them is a trigger-management surface and this lane's constraints forbid touching
routines at all — and per ground rule 4, an unread list is not an empty one. **Do not read
"UNVERIFIED" here as "probably fine."**

**And a point-in-time count would not settle it anyway.** The queue's *real* `V-041`
(`atlas-os/queue/QUEUE.md:503`, OPEN, created 2026-08-17) is precisely this problem, one
level harder — *"A chain of `send_later` routines polled two PRs roughly hourly, each link
disabling itself and creating its successor, so a point-in-time count of armed routines
reads 1–2 and looks normal. Two were still armed on 2026-08-17 and were disabled. The
registry built on 08-15 was correct and empty while the account was not; nothing reconciles
them on a schedule."* So the account has already produced a self-perpetuating chain that
defeats exactly the audit this gap asks for, and the standing registry was *correct and
empty* while routines were live. An audit that counts must also key on `last_fired_at` and
the rate of `run_once_fired` rows, not only on `enabled` — which is the open question
`V-041` puts to the founder.

**Who can close it.** A founder, or whoever holds trigger management — run `/routines-audit`,
confirm `enabled: false` on that routine by re-listing (not by having sent a disable), and
record the result in [`billing.md`](billing.md) alongside the nine from 2026-08-14.

**Cost of leaving it open.** Recurring spend nobody authorised, on a repository whose total
billed to date is **$0.5192**, where "nothing may spend without a founder approval recorded
in the queue" is the standing rule.

**How to verify it closed.** A re-listing of the account's routines showing `enabled: false`
for that trigger, quoted, with the date — not a claim that a disable was sent.

### G10 · `append-only:atlas-os/telemetry/runs` is a check that can only ever fail — `V-013`

**What it is.** The check `wc -c`'s a **directory**, always reads 0 bytes, and always
reports `SHRANK`.

**Evidence it is still real, `VERIFIED` here 2026-08-17.** The check now guards *tracked vs
untracked* but still not *file vs directory*, so the arithmetic is unchanged. Reproduced by
running the check's own two lines against the same path:

```
$ ls -ld atlas-os/telemetry/runs
drwxr-xr-x  5 danieltecum  staff  160 Aug 13 10:15 atlas-os/telemetry/runs

$ git ls-files --error-unmatch atlas-os/telemetry/runs >/dev/null 2>&1 && echo TRACKED
TRACKED                                   # so the skip branch is not taken

$ echo "OLD=$(git show HEAD:atlas-os/telemetry/runs 2>/dev/null | wc -c | tr -d ' ')  NEW=$(wc -c < atlas-os/telemetry/runs 2>/dev/null | tr -d ' ')"
OLD=71  NEW=                              # NEW is empty; ${NEW:-0} = 0 < 71 -> bad "SHRANK"
```

`OLD=71` is the byte count of the *tree listing*, not of any file. Every probe from 1 to 5
records the identical failure. See [`cloud-vm-probe-5-2026-08-14.md`](cloud-vm-probe-5-2026-08-14.md)
and [`../../atlas-os/tests/results/spend-2026-08-14.md`](../../atlas-os/tests/results/spend-2026-08-14.md):
*"Until `V-013` is fixed, treat the append-only protection on this file as unproven."*

**Who can close it.** A human under I10.
[`../../atlas-os/tests/results/wiring-2026-08-14.md`](../../atlas-os/tests/results/wiring-2026-08-14.md)
says a staged patch closes it — and says in its own header that the patch is **"STAGED, not
applied"**.

**Cost of leaving it open.** A permanently-red check is read as furniture. While it is red
for a known bad reason it cannot go red for a real one, so the append-only protection on the
telemetry ledger is unenforced *and* looks enforced-but-noisy. See D15 for the wording that
used to obscure this, now corrected.

**How to verify it closed.** The check guards file-vs-directory (or names a file under the
directory), `verify_system.sh` reports it as a pass on a clean tree, and it goes **red** when
a line is deliberately removed from a `runs/*.jsonl` file.

### G12 · Telemetry `run_id` coerced to string — open known-defect

**What it is.** From [`../../atlas-os/tests/telemetry/RESULTS.md`](../../atlas-os/tests/telemetry/RESULTS.md),
verbatim:

> | known-defect | `degraded/run_id-coerced-to-string` | a non-string session_id lands in run_id untyped (got 12345); SCHEMA section 2 requires a string. bin/ is I10-guarded — human fix |

**Evidence it is still real, `VERIFIED` here 2026-08-17** — the latest logged run:

```
$ grep -c 'run_id-coerced-to-string' atlas-os/tests/telemetry/RESULTS.md
10
$ tail -20 atlas-os/tests/telemetry/RESULTS.md | grep passed
**155 passed · 0 failed · 1 skipped · 1 known-defect** — 2026-08-17T23:45:06Z
```

The suite's own category definition is exactly right about why it is counted separately:
*"the check ran, found a real defect, and the defect is in a file this lane may not edit
(I10). Counted and named separately so it can never be mistaken for a pass."*

**Who can close it.** A human under I10.

**Cost of leaving it open.** A ledger field the schema requires to be a string can hold a
non-string, silently. Every consumer that string-matches `run_id` — including the liveness
oracle in G15 — gets a miss rather than an error.

**How to verify it closed.** The check moves from `known-defect` to `PASS`, and feeding a
non-string session id produces a typed string or a refusal.

### G15 · Heartbeat lanes go STALE while working; cloud lanes can never be visible — `V-021`

**What it is.** `heartbeat.py` registers rows under a human lane name; the liveness oracle
keys on the ledger's `session_id`, a UUID. A lane name can never appear in the ledger, so
every such row reports `no ledger entry` no matter how hard the agent is working
([`verified-facts.md`](verified-facts.md), `VERIFIED` with the clock override).

**Evidence it is still real, `VERIFIED` here 2026-08-17** — the oracle's wording is
unchanged in the live script:

```
$ grep -n 'no ledger entry' atlas-os/bin/heartbeat.py | head -3
508:            return "UNKNOWN", "no ledger entry and last_update is unparseable"
513:                "no ledger entry; board self-report %s (%.0fm ago, lease is %dm)"
516:            "no ledger entry; board self-report %s (%.0fm ago) — a claim, not proof"
```

**Who can close it.** A founder — `V-021`, OPEN. The measurement is `VERIFIED`; the fix is
`PROPOSED`.

**Cost of leaving it open.** Lanes keep going STALE while actively working, so a stale row
and a dead lane stay indistinguishable. For the cloud half there is no fix at all: a cloud
session's ledger writes never leave the VM (G14), so a cloud lane is invisible to the oracle
however it is keyed. Note the third message quoted above — *"a claim, not proof"* — is the
oracle degrading correctly and loudly; the gap is that the degraded state is the *normal*
state.

**How to verify it closed.** A founder decision, then a `heartbeat.py validate` run 45+
minutes into an active lane that reports a dated ledger sighting rather than `NO LEDGER
ENTRY`.

### G14 · A cloud session's hook telemetry dies with the VM — `V-020`

**What it is.** Hooks fire in cloud sessions (probe 6, proven with matching session ids —
and subagents inside a cloud session are logged too, five distinct child `session_id`s, see
[`subagents-in-cloud-2026-08-16.md`](subagents-in-cloud-2026-08-16.md)), but
`atlas-os/telemetry/raw/` is gitignored and nothing calls `promote.py` automatically.

**Evidence it is still real, `VERIFIED` here 2026-08-17:**

```
$ grep -rn 'promote.py' atlas-os/bin/
atlas-os/bin/export_finetune.py:308:        print("promote raw/ first: python3 atlas-os/telemetry/promote.py")
atlas-os/bin/outcome.py:155:                continue                      # torn line; promote.py counts these
```

Two mentions, both in prose or a comment. **No caller.** `nightly.sh` still contains none.

**This is deliberate and must not be "fixed" by an agent.** The obvious fix — have the
session commit its own trace — **was shipped and reverted within the hour** (`b71e0c6` →
`1ad5228`), for two reasons worth more than the fix
([`verified-facts.md`](verified-facts.md)): it converts an involuntary record into a
cooperative one, and `Stop`/`SessionEnd` fire after the last commit so the trajectory is
*always* truncated, identically and invisibly. Partial data that looks complete is worse than
absent data.

**Who can close it.** A founder, via `V-020` — which asks whether `DECISION.md`'s reversal
trigger should fire on cloud telemetry **arriving in `runs/`** rather than on hooks becoming
available, since hooks becoming available has already happened and solved nothing.

**Cost of leaving it open.** *"A future reader may retire the Supabase backend on the
strength of hooks firing in the cloud, when the problem it exists to solve is still
unsolved."* The subagent finding sharpens this: cloud sessions now fan out to five or more
child sessions, so the volume of telemetry dying with each VM has gone up, not down.

**How to verify it closed.** A founder decision recorded in the queue. Not a measurement.

---

## (c) Genuine unknowns awaiting measurement — 9

Named as unverified in the folder already, and correctly. Listed so they stop being
rediscovered. None of these is a defect; each is a number or a behaviour nobody has measured.

### G16 · `github(cloud)` is not checkable from this machine — by construction

`cs doctor` still carries the warning, `VERIFIED` here 2026-08-17
(`grep -n 'NOT CHECKABLE' cloud-sessions/bin/cs` → line 273):

```
warn  github(cloud) NOT CHECKABLE from here. If /web-setup has never been accepted, dispatch
      silently bundles instead of cloning: no branch, no PR, and routines 403.
```

This is the highest-consequence unknown in the folder, because it is the exact failure that
already happened once and went undetected for two days
([`verified-facts.md`](verified-facts.md)): *"It is silent. Nothing in the dispatch output
distinguishes a clone from a bundle."* `gh auth status` being green is a **different grant**
and doctor can only see that one. Settled by: a routine creating successfully against the
repo (403 vs 200), which is proof the clone path works since a routine has no local machine
to bundle from.

### G17 · The real CLI's `View:` line could change and the suite would stay green

Named by [`../tests/README.md`](../tests/README.md) as *"the suite's single biggest blind
spot"*: the stub prints the recorded output shape, not the current one. It was checked once
by a real dispatch on 2026-08-14 and nothing keeps it checked. Settled by: one real dispatch,
read by eye. **Distinct from D14**, which was about the *table* mis-describing this row; the
row is now correct and this is the standing risk that survives the correction.

### G26 · Whether an *empty* `allowed_tools` behaves differently from a populated one — **NEW 2026-08-17**

**The larger finding is settled and is not a gap.** `allowed_tools` **does not restrict**:
a routine payload listing seven tools produced a session that then used `ToolSearch`,
`TaskCreate` and `TaskUpdate` — none of them on the list — before doing any work
([`verified-facts.md`](verified-facts.md), `VERIFIED 2026-08-17`). It is an **auto-approval
list, not a sandbox**: listing a tool spares it a permission prompt, omitting one does not
take it away. The documentation half is closed and pinned by a regression check:

```
$ bash cloud-sessions/tests/run.sh 2>&1 | grep 'wf/allowed-tools'
  ok  wf/allowed-tools-does-not-restrict
```

**What is left is one unknown:** whether an *empty* `allowed_tools` behaves differently from
a populated one — plausibly "approve nothing", plausibly "unspecified, so inherit", which is
precisely how `mcp_connections: []` turned out (G25). `UNVERIFIED`. Settled by: one dispatch.

**Why it stays on the list at all.** The controls that actually bind a cloud session are the
repo's `.claude/settings.json` deny rules (which travel, since the file is tracked) and the
prompt itself. Anyone who reaches for `allowed_tools` as a safety mechanism has built on
sand, and the empty-list case is the one shape nobody has checked.

### G18 · Eight unmeasured network-policy behaviours

[`networking.md`](networking.md) carries them as an explicit table, each with the probe that
would settle it: what else is on the Trusted allowlist; whether `no_proxy` is trimmed under
**None**; whether a client ignoring `*_proxy` reaches anything; non-443 ports and
ssh/websockets on a Custom-allowed host; whether `*.example.com` matches the apex; whether an
unattached repo really 403s; whether the agent's own `WebFetch`/`WebSearch` are subject to
the allowlist; whether the proxy port is per-VM or per-session. All `UNVERIFIED` and honestly
tagged.

### G19 · `--environment` with an `env_…` id

The CLI documents `--environment` for self-hosted `ccpool_…` only; `cs start --env` warns and
passes it through anyway. Silent by nature — if it is ignored, the session runs somewhere
other than you asked and says nothing. Settled by: dispatch once with an `env_…` id and check
which environment ran it.

**Improved since 2026-08-15, worth recording:** the account-specific default environment id
is no longer baked into the tool. `VERIFIED` here 2026-08-17:

```
$ grep -n 'DEFAULT_ENV_ID' cloud-sessions/bin/cs
1336:DEFAULT_ENV_ID = os.environ.get("CS_EX_ENV_ID", "")  # empty = let the account default decide
```

That closes the *portability* half (see D20); the *does-it-do-anything* half is still open.

### G20 · Whether a cloud VM clones or bundles submodules

[`../tests/README.md`](../tests/README.md) section 9 proves what `cs` decides about a repo
containing a submodule; what the VM then does with `.gitmodules`, and whether `--bundle`
carries submodule contents at all, is unmeasured and not in `cs`'s warnings either.
`VERIFIED` here 2026-08-17 that the condition still exists — `twenty/` is an uninitialized
gitlink:

```
$ git ls-files -s twenty
160000 02a187d065354872c0f318b0723a1e7d8762ae00 0	twenty
```

Settled by: `git submodule update --init` inside a cloud session.

### G21 · Cloud session idle expiry; routine daily run cap for Max

[`verified-facts.md`](verified-facts.md) — expiry documented to exist, no number published.
`DOCS` say a per-account daily cap exists; the July record cites Pro 5 / Max 15 / Team 25 and
current docs no longer publish numbers. Settled by: leaving a session idle and timing it;
checking claude.ai/settings/usage.

### G22 · Remote Control's phone half

**PARTIALLY VERIFIED** — server mode connects and registers a bridge environment
([`verified-facts.md`](verified-facts.md)); nobody has opened the printed URL on a phone.
Settled by: five minutes with a phone.

### G23 · `cs shell-init` has never lived in a real `~/.zshrc`

Eleven regression checks cover the emitted function, including that `shell-init` writes
nothing. [`verified-facts.md`](verified-facts.md): *"`UNVERIFIED` in a real shell startup —
nobody here has yet `eval`'d it into a `~/.zshrc` and lived with it."* Settled by: one
operator, one week.

**Also here, and worth keeping honest:** the two BSD constructs probe 5 flagged (`sort -V` in
[`../bin/cs`](../bin/cs), `grep`'s `\|` alternation in
`atlas-os/tests/worktree/run_tests.sh`) were *first* recorded as "cleared" with no output
quoted — a `VERIFIED` tag resting on nobody's measurement — and then actually run. They are
now genuinely resolved (see D08), but the scope is *this* Mac's BSD userland, not every BSD.
If `sort -V` were ever rejected the failure is silent.

---

## (d) Resolved — 24

Recorded so they stop being re-reported. Each says **what** resolved it. Nothing is deleted
from this section; per ground rule 5 a closed gap keeps its evidence.

| # | Was a gap | What resolved it |
|---|---|---|
| D01 | `worktree.py` staged but never installed (`V-012`) | **Installed by a human 2026-08-14.** Byte-identical to the staged copy; its suite passes against the installed script |
| D02 | `mktemp -d -t cs-tests` — GNU rejects it, 57/232 red | Fixed to `cs-tests.XXXXXX`; probe 2 confirmed the warning gone |
| D03 | `trun()`'s BSD `script` form — 58/233 red, one root cause | `trun()` probes the flavour at startup; probe 3 confirmed |
| D04 | The same two constructs in `bin/cs` itself, plus macOS-only `open` | `run_under_pty`, `cs_mktemp`, `open_url`; probe 4 confirmed |
| D05 | GNU `script` swallows the child's exit code — 12 refusal checks asserting against a value that was always 0 | `-e` added to the GNU branch only. Probe 5 confirmed: `script -q -e -c "false" /dev/null` → 1 |
| D06 | The suite is not green on Linux | Probe 5, `e9b7330`: **231 passed, 0 failed, 2 skipped, exit 0**. *Qualified by G24 — see below* |
| D07 | Ten reviewed `cs` defects, five of which printed green success, + three more found while writing the suite | All thirteen fixed; pinned by name `D1`–`D10` in [`../tests/run.sh`](../tests/run.sh) |
| D08 | `sort -V` and `grep \|` unverified on BSD | Run on this Mac 2026-08-14 with output quoted. Scope: this Mac |
| D09 | `x-deny-reason: host_not_allowed` header | **DISPROVEN** — it is a plain-text 403 body. Corrected in `bin/cs`, [`limits.md`](limits.md), `06-environments.md`, [`how-others-build-this.md`](how-others-build-this.md). The `cs handoff` brief had been telling every session to look for a header that does not exist |
| D10 | GitHub never connected — dispatch silently bundling | `/web-setup` → `Connected as daniel0tgc.` Routine creation went 403 → **HTTP 200** against the same repo |
| D11 | Can a cloud session push a branch / open a PR? | Branch: probe on 2026-08-12. **PR #1** opened by probe 7, 2026-08-14 |
| D12 | Nine probe routines left armed | All nine `enabled: false`, confirmed by re-listing ([`billing.md`](billing.md)). *Compare G33 — a tenth is unconfirmed* |
| **D13** | **G06** — `billing.md` proved "no API key can bill you" with a probe that could not see `~/.zshrc` | Closed 2026-08-16. Confirmed here — see below |
| **D14** | **G07** — the not-verified table contradicted the body of its own file | Closed 2026-08-16. Confirmed here — see below |
| **D15** | **G08** — the `append-only` failure described as an I10 artifact rather than a broken check | Closed 2026-08-16. Confirmed here — see below |
| **D16** | **G09** — the `cs start` record-path proof was deleted from the ledger after it was taken | Closed 2026-08-16. Confirmed here — see below |
| **D17** | **G11** — `D3a`/`D3b` could never run on a root VM | Closed 2026-08-16. Confirmed here — see below |
| **D18** | **G13** — `--plan` silently dropped unless the destination is cloud | Was **already fixed** in `a1b05ef`, seven minutes before the register that called it open was compiled. Confirmed here — see below |
| **D19** | A ported ledger made `cs send last` steer a live session of the *old* repo, rc 0 (porting audit break 1) | `last_id()` in [`../bin/cs`](../bin/cs) now **refuses** on a positive repo mismatch: *"the most recent ledger row belongs to repo 'X', but you are in 'Y'."* Only on a positive mismatch — outside a repo, or on an old row with no `repo` field, it behaves as before rather than inventing a refusal |
| **D20** | `/cloud` and `/cloud-workflow` hard-coded this repo's clone URL and this account's env id into every dispatch payload (porting audit break 3) | Removed. `grep 'github.com/daniel0tgc/internal-company-tool' .claude/commands/cloud.md .claude/commands/cloud-workflow.md` → no output; the hardcoded transcript path is gone; `DEFAULT_ENV_ID` now defaults to empty (see G19) |
| **D21** | [`../state/README.md`](../state/README.md) contradicted itself about whether the ledger is tracked (porting audit break 6) | Corrected in place with a **"superseded 2026-08-17"** marker, and it now points at the porting playbook's truncate-the-ledger step |
| **D22** | `wf/task-filtering-unverified` failed on bare `origin/main`, discovered mid-dispatch (workflow-bootstrap §6b) | Fixed upstream. All nine `wf/` checks pass here today, and the suite is `628 passed  0 failed` |
| **D23** | Three `reference/` files were orphaned from the index — `linux-verification`, `safety-audit`, `subagents-in-cloud` (reachability audit) | All three carry a real markdown link from [`README.md`](README.md) — grepping that file for each filename inside a markdown link returns exactly 1 hit per file. The orphan list is correctly dated evidence of a pre-merge state, not a live defect |
| **D24** | `bin/README.md` claimed `cs` depends on **"nothing outside this folder"**, false since `board`/`exodus` landed (porting audit break 7, doc half) | [`../bin/README.md`](../bin/README.md) line 80 now says so explicitly and names both dependencies. *The coverage half of that break is still open — see G31* |

### D13–D18 in full — the six closed by a cloud session, **re-verified on disk here**

The 2026-08-16 cloud session reported all six closed. Per ground rule 7 a report claiming
closure is not closure, so each was checked against the working tree rather than read from
[`gap-closure-2026-08-16.md`](gap-closure-2026-08-16.md). **All six confirmed. None was
found still open.**

| Was | Closed by | How I confirmed it, here, 2026-08-17 |
|---|---|---|
| D13 (G06) | [`billing.md`](billing.md) rewritten: both probes shown per key, `_get_env_or_zshrc` named, the `cs doctor` sentence scoped to the five env vars it actually reads, `V-037` reconciled | `grep -n 'cs doctor' billing.md` → line 67: *"`cs doctor` **only** fails loudly for the three…"*; `_get_env_or_zshrc` present at :29; the two-probe table at :37–:57 shows `env` **and** `~/.zshrc` columns; `V-037` has its own dated subsection at :76 |
| D14 (G07) | The `:770` over-claim replaced; the `View:` row now states *proven once, standing recurrence risk* | `grep -c "closed the last open item" verified-facts.md` → **0**; `:801` reads *"What Probe 7 actually closed was **one specific row**"*; the not-verified table's `View:` row now reads `UNVERIFIED — proven true once, on 2026-08-14 … it is not "never checked," it is "checked once, and could silently regress"` |
| D15 (G08) | The table cell reduced to the bare fact; a new paragraph names `V-013` and separates *check is broken* from *invariant is violated* | `grep -c "correctly left alone as I10 human-only" verified-facts.md` → **0**; `:616` reads *"Filed as `V-013`. **The check is broken; that is a distinct claim from 'the invariant is violated,'**"* and adds that the protection is *"unproven, not merely human-gated"* |
| D16 (G09) — *was listed "partly", now resolved outright* | A paragraph added to the "gap worth naming" section stating the gap **was** closed on 2026-08-14 and that the closing proof's ledger row was removed by design | `sed -n '405,440p' verified-facts.md` shows the added block: *"**This gap was closed on 2026-08-14** … **Corrected 2026-08-16 (G09):** … that row was then removed with `cs rm` … a deliberate choice, not an oversight … the quoted terminal output in the later section is the whole surviving record."* **Nothing remains.** The register's original "how to verify it closed" asked for exactly one of two outcomes — say plainly the artifact was removed by design, *or* keep a non-`tracked` row. The first was chosen and executed. The ledger is now 14 rows and still all `tracked`, which is the expected consequence, not a residual gap |
| D17 (G11) | `D3a`/`D3b` now inject failure with a **read-only bind mount** when root, falling back to `chmod` for a non-root operator and to a loud `_skip` only without `CAP_SYS_ADMIN`; new `D3z` positive control asserts the injection genuinely fired | Read [`../tests/run.sh`](../tests/run.sh) lines 382–423: `mount --bind` → `mount -o remount,ro,bind`, and the `_skip` branch now names *"root and no CAP_SYS_ADMIN on this host"* rather than "cannot make a directory unwritable for root". Ran the suite here: `ok D3z/env-set-injection-genuinely-fired`, `ok D3a/…`, `ok D3b/…` — **and 0 skipped overall** |
| D18 (G13) | `cmd_new` refuses `--plan` outright for any non-cloud destination; the three weak `has "plan"` xfails replaced by 8 checks asserting exit code, exact message and non-dispatch | `grep -n "applies only to a cloud session" cloud-sessions/bin/cs` → line 819, live in the tool. All 8 checks plus the `plan-reaches-a-cloud-session` positive control pass here, and the suite reports **`0 xfail(known-open)`**. **This one was never closed by that session — it was already closed before the register was written**, in `a1b05ef`. The register's error was staleness at compile time, not a missed fix |

**The regression guard is real and has teeth.** D13–D16 are wording fixes, so
[`../tests/doc-drift.sh`](../tests/doc-drift.sh) was added to stop them rotting. Run here:

```
$ bash cloud-sessions/tests/doc-drift.sh
ok    G06/billing-doctor-sentence-not-overclaimed
ok    G06/billing-doctor-sentence-scoped
ok    G06/billing-names-V-037
ok    G07/last-open-item-not-overclaimed
ok    G07/last-open-item-names-the-row
ok    G08/append-only-not-conflated-with-I10
ok    G08/append-only-names-V-013
ok    G09/ledger-gap-cross-referenced
green.
```

**Negative control, run independently of the 2026-08-16 report's own** — ground rule 3, a
probe a bogus control also passes is not a probe. Copies of `doc-drift.sh`, `billing.md` and
`verified-facts.md` were placed in a scratch tree outside the repo and the four corrections
substituted back to their pre-fix wording:

```
$ bash $SCRATCH/tests/doc-drift.sh
FAIL  G06/billing-doctor-sentence-not-overclaimed — stale wording is back: …
FAIL  G06/billing-doctor-sentence-scoped — expected correction is missing: …
FAIL  G07/last-open-item-names-the-row — expected correction is missing: **one specific row**
FAIL  G08/append-only-not-conflated-with-I10 — stale wording is back: …
FAIL  G08/append-only-names-V-013 — expected correction is missing: V-013
FAIL  G09/ledger-gap-cross-referenced — expected correction is missing: This gap was closed on 2026-08-14
NOT GREEN.   (exit 1)
```

Six of eight went red on reverted inputs, so the suite is measuring the documents and not
itself. **The two that stayed green are a real limitation, not a pass:**
`G06/billing-names-V-037` and `G07/last-open-item-not-overclaimed` both assert against
patterns my substitution did not fully restore — the first is a bare `V-037` substring that
survives any rewording, the second an exact long sentence that a paraphrase would evade.
Both would miss a partial regression. **No repository file was modified to run this**; the
whole control lived in a scratch directory.

### D06 is qualified by G24 — read it that way

`D06` says the suite is green on Linux, and it was, at `e9b7330`, `231 passed / 2 skipped`.
G24 shows that a Linux total can be ~206 checks short of the macOS total at the same commit
without saying so. **D06 is not withdrawn** — it is a correct record of a run that happened
— but it should not be read as "Linux coverage equals macOS coverage". Nothing in this
folder has ever established that, and G24 is the open question of whether it is true.

---

## Where this register contradicts the folder's own documentation

Stated plainly, per the standing brief. **Down from four places to one**, and the one that
remains has changed sides.

1. **`V-030` is now the stale one on `STOP`, not `V-034`** (G04). The 2026-08-15 compile
   asserted the opposite with quoted output, and was correct at `767adf3`; commit `f28e861`
   removed `STOP` from the index and inverted the answer. Today `git ls-files STOP` returns
   nothing, so root [`../../AGENT.md`](../../AGENT.md) and `V-034` are **right**, and
   `V-030`'s *"STOP is a tracked empty FILE at HEAD"* is the claim that needs marking
   historical. The superseded block in G04 preserves both readings and names the commit that
   settled it.

**Resolved since the last compile, and recorded so the change is legible:** the
`billing.md` method (was #2, now D13), the `verified-facts.md` table-vs-body contradiction
(was #3, now D14), and the `append-only` mischaracterisation (was #4, now D15) are all
closed and pinned by [`../tests/doc-drift.sh`](../tests/doc-drift.sh).

**Checked for a contradiction and did not find one:** `verified-facts.md`'s `VERIFIED` chain
(dispatch, TTY refusal, headless steering, teleport, routines, push, PR, connectors,
subagents) is supported by quoted output at every link, and all eleven dated reports are
indexed in [`README.md`](README.md) as the folder's rules require.

**One live staleness this rebuild creates and cannot fix.**
[`README.md`](README.md) carries a block headed *"`OPEN-GAPS.md` is stale in section (a) —
corrected here, 2026-08-17"*, and its row for this file is tagged **"partly stale"**. That
note was correct an hour ago and is now itself out of date — section (a) has been rebuilt.
`README.md` is another lane's path; this register owned exactly one file and did not touch
it. Whoever next edits `reference/README.md` should retire that block.

---

## What this rebuild could not do

- **Nothing was filed to the queue.** `QUEUE.md` is written only by
  `atlas-os/bin/queue.py`. **G04**, **G25** and **G33** are the three that most warrant a
  founder item — all three bear on spending or on private data reaching an unattended
  session. `V-037`'s reconciliation and the `V-030`/`V-034` inversion also need a founder.
- **No row was added to [`README.md`](README.md)**, and its staleness note about this file
  was not retired. This lane owned exactly one path.
- **G24 was not investigated.** Not reproducible on macOS, and a cloud session holds
  `cloud-sessions/tests/run.sh` right now. Deliberately not pre-empted.
- **`cloud-sessions/tests/run.sh` and `cloud-sessions/bin/cs` were read, never written.**
  Both are held by sibling lanes.
- **The account's routines were not listed** (G33). Trigger management was out of scope by
  constraint; an unread list is not an empty one.
- **No dispatch, no routine, no `cs start`/`handoff`/`new`/`send`/`tp`/`rc`.** Only the safe
  read-only surface plus the offline suites were exercised. **$0.00 spent, nothing
  dispatched, no routine created, fired or modified, no branch, no commit.**

### Link integrity — verified by script, not by eye

Every relative link in this file was extracted and resolved against the filesystem from
`cloud-sessions/reference/`:

```
$ grep -oE '\]\([^)#][^)]*\)' OPEN-GAPS.md | sed -E 's/^\]\(//; s/\)$//' | sort -u \
  | while read -r p; do [ -e "$p" ] || echo "BROKEN: $p"; done
(no output — all 40 distinct relative targets resolve)

$ # negative control: the same loop with one deliberately wrong path
$ printf 'verified-facts.md\nverified-facts-NOPE.md\n' \
  | while read -r p; do [ -e "$p" ] || echo "BROKEN: $p"; done
BROKEN: verified-facts-NOPE.md
```

The control matters: a loop that prints nothing because it read nothing looks identical to a
loop that prints nothing because everything resolved.
