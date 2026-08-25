# tests/

> **Provenance note, 2026-08-18:** this suite and its documentation were originally
> written against `daniel0tgc/internal-company-tool`, a repo with an `atlas-os/` folder
> providing `heartbeat.py`/`worktree.py`/`queue.py` and their own companion test suites
> (`atlas-os/tests/heartbeat/`, `atlas-os/tests/worktree/`, `atlas-os/tests/process/`).
> **`atlas-os/` does not exist in this repo** (`emanate-tecum-workflow`) — every mention of
> it below describes that dependency, not something fixable by a path substitution.
> `run.sh`'s sections 12–15 (`cs board`/`cs exodus`) will therefore fail their real-fixture
> setup here rather than exercise real `heartbeat.py`/`worktree.py` logic, since there is no
> such script to symlink to; the other sections (1–11, 16–17) are self-contained and
> unaffected. `RESULTS.md`, which recorded a run including the three `atlas-os` companion
> suites, was archived and then removed from this repo — that specific run's numbers were
> the other repo's, not this one's, so keeping them here would only invite miscitation.
> Re-run `run.sh` here to get a fresh, real `RESULTS.md` for this repo.

The regression suite for [`bin/cs`](../bin/cs). It exists because the ten defects an
adversarial review found in `cs` on 2026-08-13 were, until now, only verified by a
throwaway script in a temp directory. A fix nobody re-runs is a fix that comes back.

```bash
bash cloud-sessions/tests/run.sh          # from anywhere; the script locates itself
bash cloud-sessions/tests/run.sh --keep   # leave the temp work dir for inspection
bash cloud-sessions/tests/run.sh -v       # echo every command's output as it runs
```

Exit 0 means green. Exit 1 means a regression. Every run rewrites
[`RESULTS.md`](RESULTS.md) — one line per check with what it asserts, plus the command,
exit code and full output for anything that is not a pass. It is appended as the run
proceeds, so a crashed run still leaves the log of everything up to the crash.

`cs doctor` and the refusal paths must be re-run after any change to `bin/cs`
([`../AGENT.md`](../AGENT.md)). This suite is how you do that.

## It costs nothing and needs no network

This is the property that makes the suite safe to run on a whim, and it is enforced by
construction rather than by care:

| Real thing | What the suite uses instead |
|---|---|
| the `claude` CLI | [`fixtures/fake-claude`](fixtures/fake-claude), wired in **twice** — `CS_CLAUDE_BIN` points at it *and* it shadows `claude` on `PATH`, so both of `cs`'s resolution routes land on the stub |
| the `claude` CLI, where the test must prove *nothing was dispatched* | [`fixtures/argv-recorder`](fixtures/argv-recorder) — records its argv and prints no `View:` line, so `cs` can record no session id and an empty ledger becomes evidence |
| `cloud-sessions/state/sessions.jsonl` | `cs` is **copied** into a temp fixture root, so the ledger it computes is `$WORK/csroot/state/sessions.jsonl` |
| `~/.claude/settings.json` | `$HOME` is redirected to a temp dir before any test runs |
| this git repository | throwaway repos built from scratch under `$WORK`, with `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` neutralised |
| `gh auth status` (a network call) | [`fixtures/fake-gh`](fixtures/fake-gh) |
| `open` (launches a browser) | [`fixtures/fake-open`](fixtures/fake-open), which records the URL so tests can assert it |
| a human at the `cs new` prompt | [`fixtures/pty-drive.py`](fixtures/pty-drive.py) — runs the command on a real pty and types the answer once the prompt has actually appeared |
| the real heartbeat board `atlas-os/heartbeat/STATE.md` | throwaway **symlinked roots** per [`atlas-os/tests/heartbeat/README.md`](../../atlas-os/tests/heartbeat/README.md): the real `heartbeat.py`/`worktree.py` are symlinked into a fixture repo and resolve their board relative to their own (unresolved) path, so the real scripts run against a sandbox board |
| `claude agents --json` (what `cs sessions`, and through it `cs board`, shells out to) | an `agents-claude` stub generated into the sandbox that answers that one verb with a fixed session list — `fake-claude` deliberately does *not* speak it, which is what drives the degraded-sessions checks |

No test dispatches a cloud session, consumes a rate limit, or makes a network request.
Section 17 of the suite checksums the real ledger, the real `~/.claude/settings.json`,
the real heartbeat board, the repo's default exodus-manifest path, the staged hook script
and `bin/cs` itself **before** the sandbox is set up and asserts all are byte-identical
afterwards — because an earlier ad-hoc verification script wrote six junk rows into the
real ledger, and a guarantee nobody checks is a guarantee that quietly stops being true.
(The `bin/cs` guard compares content hashes rather than `git diff` against `HEAD`: its
claim is that *this run* modified nothing, and diff-vs-HEAD also fires on someone's
normal uncommitted in-flight work, which is not this suite's business.)

### The operator's own environment is neutralised too

Two ambient variables can decide an answer under test, so the harness unsets both:

- **`CS_DEFAULT`** answers `cs new`'s chooser. Until 2026-08-15 the suite inherited it, so
  an operator who had set `CS_DEFAULT=cloud` in their shell got a red run
  (`new/no-tty-refuses-to-guess`) from a `cs` with nothing wrong with it. Reproduced
  against the previous revision before fixing.
- **`CLAUDE_CODE_SESSION_ID`** is set in every shell Claude Code spawns, and the
  `shell-init` wrapper passes straight through when it is set. Running the suite from
  inside Claude Code therefore made the wrapper's interception untestable — silently,
  because a passthrough looks like a pass. Section 8 sets it explicitly per check instead.

## What it covers

| Section | Checks |
|---|---|
| 1 — harness self-guards | that the suite is testing a byte-identical copy of `bin/cs`, that `cs` really resolved the stub, that the ledger and `$HOME` under test are inside the sandbox, and that the stub was genuinely executed |
| 2 — the ten reviewed defects | one or more regression checks per defect, listed below |
| 3 — every command | `doctor`, `start`, `handoff`, `env` (show/set/clear), `send`, `ls`, `track`, `rm`, `open`, `web`, `tp`, `rc`, `help` — refusal paths first, plus what each one passes to the CLI |
| 4 — the git gate | not a repo, unborn branch, detached HEAD, dirty tree, no origin, non-GitHub origin, no upstream, upstream on a fork, unpushed commits — each with the `--bundle` behaviour, and a clean-and-pushed positive control |
| 5 — ledger robustness | absent, empty, whitespace-only, a malformed line, a truncated final line, no trailing newline, and a valid-JSON row missing fields |
| 6 — argument parsing | flags with no value, flags after the positional argument, `--`, and messages containing quotes, newlines, backticks, `$(…)` and leading dashes |
| 7 — the `cs new` chooser | the full flag × `CS_DEFAULT` × TTY cross product as a table, the aliases, the answered prompt, and `--plan` against a destination that cannot use it |
| 8 — `cs shell-init` | the emitted function as an artifact: it parses, passes arguments through, no-ops inside a session, resolves the binary correctly and returns 127 when there is none |
| 9 — git-gate shapes | an SSH origin, a linked worktree, a deleted upstream ref, and a submodule |
| 10 — idempotency | `ls`, `env`, `doctor` and `help` run twice, and `rm` on an id that is already gone |
| 12 — `cs board` | all four sources healthy, each source degraded loudly and independently, the merge (local / unseen / cloud), the stale-lease marker, the unregistered-session callout, unknown board columns, `--json` validity, and that rendering is read-only |
| 13 — `cs exodus --dry-run` | the partition asserted **file by file** against a 3-lane board built through the real `heartbeat.py register`, CONFLICT as a hard refusal naming path + holders + fix, UNCLAIMED listed and left behind, self-lane exclusion, and dry-run-writes-nothing — with a **negative control on each of the two dangerous claims** |
| 14 — `cs exodus` executed | branches carrying exactly the claimed files, commits naming the source session, push to a sandbox origin, a mid-batch worktree collision that fails one unit while the units behind it still complete, the shared tree byte-identical, scratch worktrees removed, the manifest validated against its own schema (including the **explicit** empty `mcp_connections`), and the re-run refusing existing branches |
| 15 — the auto-register hook | fresh register with the `._presence/` sentinel, idempotent warm path (board hash unchanged), garbage stdin with and without an id, missing id as a *normal* unlogged path, cloud-VM no-op, never-clobber of a deliberate registration, stale-row refresh preserving `started=`, and the <300ms warm-path timing |
| 16 — the command surface | the load-bearing sentences of `/cloud` all-mode, `/cloud-workflow` and the workflow TEMPLATE exist; every relative link resolves; the TEMPLATE's example block parses under an independent tiny parser — which its own negative control proves can go red |
| 17 — tamper guards | proof that nothing real was written: ledger, settings, heartbeat board, default manifest path, hook script, `bin/cs` |

### 7 — the `cs new` chooser matrix

`cs new` resolves one thing — where the work runs — from an explicit flag, `CS_DEFAULT`,
and whether there is a TTY to ask in. Resolving it wrongly is asymmetric: cloud dispatches
real autonomous work and consumes rate limits, here ties the work to a laptop that is
about to close. The 48 cases are an explicit table in `run.sh` rather than a loop that
recomputes the expectation, so the table is a specification and not a second copy of the
same `case` statement. Two rules it exists to prove: **an explicit flag always beats
`CS_DEFAULT`**, including a `CS_DEFAULT` that is not a valid value at all, and **an
unrecognised `CS_DEFAULT` refuses** rather than falling back to a side.

The `here` branch is exercised without starting a session: `CS_CLAUDE_BIN` points at
`fixtures/argv-recorder`, so the recorded argv says which branch resolved, and the ledger
being empty after all 48 cases plus 8 answered prompts says none of them dispatched.

Note the two different refusals in the no-TTY column. `needs a terminal to ask in` is the
chooser declining to guess; `cs start needs an interactive terminal` means the destination
*did* resolve to cloud and the gate that refused was `cs start`'s. Reading one for the
other would hide a resolution bug.

### 8 — `cs shell-init` is an artifact, not a message

The command prints a shell function for the operator to paste into `~/.zshrc`. Nothing
re-checks it after that, and a mistake there breaks the `claude` command itself. Section 3
asserts what the text *says*; section 8 sources it in bash and zsh subshells with
`fixtures/argv-recorder` on `PATH` and asserts what it *does* — including the one that
cannot be caught by reading: the wrapper must resolve the binary with `whence -p`/`type -P`
and never `command -v`, which returns the function itself and makes the `~/.local/bin`
fallback dead code. That is invisible while `claude` is on `PATH`, so the check puts the
binary **only** in `~/.local/bin` and asserts it is still found.

Every "does not intercept" check is paired with the positive control that it *does*
intercept a bare interactive `claude` inside the repo — without it, a wrapper that
intercepted nothing at all would pass the lot.

### 9 — repository shapes, and which way each one goes

Section 4 walks the gate's own branches. Section 9 covers repository *shapes* that reach
the same code differently. Three of the four correctly **proceed**, so each check's
description states which answer is the right one — "it refused" would be just as wrong an
outcome here as "it dispatched" is for the fourth.

| Shape | `cs start` | Why |
|---|---|---|
| origin is `git@github.com:owner/repo.git` | **proceeds** | the scp-style SSH form still contains `github.com`; the VM clones over the account's own grant, so the local URL form is not the deciding fact |
| a linked worktree, where `.git` is a **file** | **proceeds** | `rev-parse` resolves through the `.git` file exactly as in a normal checkout. The ledger records the *worktree directory's* name as the repo and the worktree's own branch |
| upstream configured but its ref deleted | **refuses** | reported as `has no upstream`, with the `git push -u` fix — not as a raw git error. `cs doctor` predicts the same refusal |
| a superproject with a submodule | **proceeds** when the submodule is at its recorded commit; **refuses** when anything inside the submodule is dirty, because that surfaces as ` M sub` in the superproject |
| run from **inside** a submodule | **proceeds**, gating the submodule — its origin, its branch, its cleanliness — and recording *it* as the repo, not the superproject that contains it |

Each fixture is paired with a check that the fixture is really the shape it claims (the
`.git` really is a file, the upstream really is still configured, the submodule really is
checked out), because a fixture that quietly degraded into an ordinary repo would make
every assertion about it pass while measuring nothing.

### 10 — the safe four must stay safe

[`../AGENT.md`](../AGENT.md) calls `cs help`, `cs ls`, `cs env` and `cs doctor` "the safe
four" — what you run when you must not dispatch. That is a claim about side effects, and
nothing checked it until now. Each runs twice: the output must be identical and every file
under a **pristine** `$HOME` plus the fixture root must be byte-identical afterwards.

The pristine home matters. An earlier version of these checks reused the sandbox `$HOME`
that previous sections had already run every command against, and a deliberately injected
`: > "$HOME/.claude/doctor-was-here"` in `cmd_doctor` passed it — the file was already
there from an earlier section, and truncating it again changed no bytes. Comparing
before/after only finds side effects on state that is *not already dirty*.

`cs rm` is the one command that rewrites the ledger, so it gets the inverse check: a
refused `rm` must not rewrite the file at all. The ledger for that check is hand-written
with no spaces after the JSON separators, which is not what `json.dumps` emits, so any
rewrite — even one that would have preserved every row — changes the bytes.

### 12–15 — the aggregate surface, and why its fixtures are symlinked roots

`cs board` and `cs exodus` read the heartbeat board, and `heartbeat.py --board`
**isolates nothing** — the real lock and the real board are always consulted
([`atlas-os/tests/heartbeat/README.md`](../../atlas-os/tests/heartbeat/README.md)). So
every fixture here is a throwaway repo with `atlas-os/bin/heartbeat.py` and
`worktree.py` symlinked to the real scripts: `os.path.abspath()` does not resolve
symlinks, so the real, unmodified code runs against a sandbox board, and section 17
hashes the real board before and after to prove the isolation held. The exodus lanes are
registered through the **real** `register`, so the claims under test are exactly what
the real overlap gate admits; the one thing the real gate refuses to create — a double
claim — is manufactured by editing the *fixture* board directly, which is also the
realistic shape (a hand-edited or merge-damaged board is what the CONFLICT refusal
exists to catch).

Two claims in section 13 are dangerous enough to carry **negative controls**: the
partition-correctness comparator is re-run against a deliberately wrong expected set and
must go red (`nc/partition-check-can-go-red`), and the dry-run-writes-nothing detector
is shown to fire on a file touched inside the guarded window and to calm down when it is
removed (`nc/writes-nothing-detector-*`). Both controls run through the *same* `same()`
comparator as the green checks, so they prove the actual check can fail, not a
lookalike. The TEMPLATE parser in section 16 carries the same construction
(`nc/template-parser-can-go-red`).

The hook's warm-path timing check takes the **minimum of three** runs against the 300ms
threshold; if even the best of three misses, the machine is too loaded to measure
honestly and the check **skips loudly with both numbers** rather than flaking red or
quietly passing (measured 60ms on 2026-08-17).

### The ten defects, and where each is pinned down

| # | Defect | Checks |
|---|---|---|
| 1 | session ID grepped from the whole transcript, so a task title or commit message could win | `D1a`–`D1d` |
| 2 | `cs doctor` printed `ready.` when a gate would have refused the dispatch | `D2a`–`D2i` |
| 3 | `cs env set` truncated `~/.claude/settings.json` before writing it | `D3a`–`D3e` |
| 4 | `cs env set`/`clear` reported success while a higher-precedence layer decided | `D4a`–`D4j` |
| 5 | a branch tracking a **fork** passed the "pushed" gate | `D5a`–`D5c` |
| 6 | one malformed ledger byte broke `last` for every command | `D6a`–`D6d` |
| 7 | `--env`/`--plan` with no value exited silently under `set -e` | `D7a`–`D7d` |
| 8 | `--plan` was not repo-root-relative, so the session could not find its own plan | `D8a`–`D8e` |
| 9 | every handoff row in `cs ls` was the same truncated 1.5KB brief | `D9a`–`D9c` |
| 10 | `resolve_id` mangled malformed input instead of rejecting it | `D10/*` |

Defect 1 is the one to read first. `D1c` dispatches a real `cs handoff` in a fixture repo
whose commit message names a decoy session id — the exact vector that made the original
bug near-certain — and asserts the recorded id came from the `View:` line. `D1c` is paired
with a check that the decoy actually reached the brief, because a regression test whose
bait is missing passes forever while verifying nothing.

## Reading the outcome: pass, fail, xfail, skip

- **PASS / FAIL** — as expected. Any FAIL exits 1.
- **xfail** — a check written against a defect in `cs` that is *still open*. It is printed,
  counted separately, and listed by name in the summary. It is **not** a pass. If an xfail
  starts passing it is reported as `XPASS` and counted as a **failure**, so a defect cannot
  be fixed and left mislabelled as broken. Fixing the defect means changing `xhas`/`xhasnt`
  to `has`/`hasnt` in the same commit.
- **skip** — printed and counted. A skipped check is not a passed check.

Open defects as of the last run are listed at the end of [`RESULTS.md`](RESULTS.md).

### FIXED (superseded 2026-08-17): `--plan` is silently dropped unless the destination is cloud

The defect below was fixed in `cmd_new` (it now **refuses** `--plan` for any
non-cloud destination) and the three xfails were promoted to the enforcing checks
`new/plan-with-here-is-refused`, `new/plan-with-rc-is-refused` and
`new/plan-with-here-refused-before-file-is-checked`. The original write-up stays, per
this folder's supersede-never-delete rule, because it documents the xfail mechanism
working exactly as designed. As of 2026-08-17 the suite carries **zero** open xfails.

`cs new` accepts `--plan` for any destination but only ever uses it on the cloud path —
`cmd_new` hands it to `cmd_handoff` and nowhere else. Choose `here` or `rc`, by flag, by
`CS_DEFAULT`, or by answering the chooser, and the flag is dropped without a word. It is
not validated on that path either, so the file need not exist and need not be committed,
while the cloud path refuses both (`D8c`, `D8e`). Same family as reviewed defect 7: a flag
accepted and then silently ignored.

Three xfails hold it: `new/plan-with-here-is-silently-dropped`,
`new/plan-with-rc-is-silently-dropped`, `new/plan-with-here-is-not-even-validated`. Each
asserts only that the output says *something* about the plan, so either fix — refusing the
flag or honouring it — flips them to XPASS and forces the promotion to `has`. A positive
control next to them (`new/plan-reaches-a-cloud-session`) proves `--plan` does work on the
cloud path, so the xfails are about the destination and not about `--plan` being broken
everywhere.

## These checks have been seen to go red

A check nobody has watched fail is a check that might be asserting nothing — the failure
this suite was written after (twelve refusals asserted against an exit code that was
always 0). Every section here has been mutation-tested: `bin/cs` was broken one way at a
time in a throwaway copy and the suite re-run, on 2026-08-15. Fifteen mutations, fifteen
caught, each by the checks that name the behaviour:

| Mutation to `cs` | Caught by |
|---|---|
| `CS_DEFAULT` consulted even when a flag was given | 40 matrix cases + `new/flag-beats-default` |
| an unrecognised `CS_DEFAULT` falls back to `here` | the four `CS_DEFAULT=nonsense` cases + `new/bogus-default-refused` |
| `shell-init` resolves the binary with `command -v` | `shell-init/*/falls-back-to-local-bin`, `*/no-binary-says-so`, and both text checks |
| `shell-init` stops honouring `CLAUDE_CODE_SESSION_ID` | `shell-init/no-chooser-inside-a-session` |
| the GitHub test accepts only `https://` origins | `gate/ssh-origin-proceeds` |
| `cmd_doctor` gains a side effect | `idem/doctor-changes-nothing-on-disk` |
| `cs rm` stops refusing an absent id | `rm/refuses-an-absent-id`, `idem/rm-*` including the no-rewrite check |
| the dirty check ignores submodules | `gate/submodule-dirty-refused` |
| answering `3` runs a local session instead of Remote Control | `new-chooser/answers-3-rc`, `answers-phone-rc` |
| the `here` branch dispatches to the cloud | 42 cases, including every `--here`/`--local` row |
| a deleted upstream ref stops being refused | `gate/deleted-upstream-refused` |
| `cs doctor` output stops being reproducible | `idem/doctor-output-identical` |
| the repo test becomes "is there a `.git` directory" | all four `gate/worktree-*` and both `gate/inside-a-submodule-*` |
| the recorded repo name stops naming the checkout | `gate/worktree-records-the-worktree-dir-as-repo`, `gate/inside-a-submodule-records-the-submodule` |
| **the `--plan` defect above, fixed** | all three xfails flip to XPASS, as designed |

## What this suite deliberately does NOT cover

Everything here is a claim about `cs`, never about Anthropic's cloud. Nothing below can be
checked without a real dispatch, and a real dispatch is not a test — it consumes the
account's rate limits and runs an autonomous agent. Per
[`../AGENT.md`](../AGENT.md): *do not dispatch a session to test the tooling.*

- **That a dispatch actually creates a cloud session.** The stub prints an output shape
  recorded from a real dispatch by hand; it does not prove the real CLI still prints that
  shape. **If Anthropic changes the `View:` line, this suite stays green and `cs start`
  silently stops recording ids.** That is the suite's single biggest blind spot. It is
  checkable only by dispatching once by hand and comparing.
- **That the cloud VM clones rather than bundles**, that a session can push a branch, or
  that a routine fires. None of that is covered here — each was established by hand-run
  dispatches, and re-establishing any of them means dispatching for real again.
- **Whether `--environment` with an `env_…` id does anything.** `cs` warns that this is
  `UNVERIFIED`; the suite asserts the warning, not the underlying behaviour.
- **The real auth, `gh`, and browser paths.** `auth status`, `gh auth status` and `open` are
  all stubbed, so the suite tests how `cs` *reacts* to each answer, not which answer the
  real tools give. `cs doctor` on the real machine remains the check for that.
- **`cs tp` and `cs rc` beyond argument construction.** Both `exec` into the CLI; the suite
  asserts the refusals and the exact argv, then stops.
- **Concurrency.** `record` appends non-atomically. The suite covers the *damage* that
  leaves behind, not two `cs start` runs racing.
- **Whether a cloud VM clones submodules, or bundles them.** Section 9 proves what `cs`
  decides about a repository containing a submodule. What the VM then does with
  `.gitmodules` — and whether `--bundle` carries submodule contents at all — is unmeasured
  from here and is not documented in `cs`'s own warnings either.
- **Whether passing the task as Remote Control's *name* is right.** `cs new` answered `3`,
  and `CS_DEFAULT=rc`, both send the task text to `claude --remote-control <name>`, where
  it becomes the session's name rather than a prompt. The suite pins that this is what
  happens; whether it is what should happen is a design question nobody has settled.
- **`shell-init` under anything but bash and zsh.** Those are the two the emitted text
  targets (`whence -p` / `type -P`). It is not valid fish or POSIX-sh, and the suite does
  not pretend otherwise.
- **That the `here` branch produces a working local session.** The checks prove `cs`
  resolved to `here` and `exec`'d the binary with exactly the task as its argument. What
  the real CLI does next is out of scope by construction — the stub is the whole reason
  this is safe to run on a whim.
- **The chooser's tolerance of partial or slow input.** `fixtures/pty-drive.py` sends one
  complete line once the prompt appears. A half-typed answer, a `SIGINT` at the prompt, or
  a terminal that closes mid-answer are not covered; the EOF case is (`tty=yes` with stdin
  closed refuses with `input closed before you answered`).
- **Live dispatch of `/cloud all` and `/cloud-workflow`.** Both are prompts, not programs:
  section 16 asserts the load-bearing sentences exist and the structures parse, and stops
  there. Whether an agent follows create → run → **disable**, writes the brief, or records
  provenance is unmeasurable at $0 — a real routine run is real spend and a real armed
  trigger. The manifest checks in section 14 pin the *inputs* `/cloud all` would consume
  (explicit empty `mcp_connections` included), never the dispatch itself.
- **Real hook wiring.** `.claude/settings.json` is deny-listed for agents, so the wiring
  in [`../bin/hooks/WIRING.md`](../bin/hooks/WIRING.md) is staged, not applied, and this
  suite exercises the hook by invoking the script directly. Whether Claude Code actually
  fires `SessionStart`/`UserPromptSubmit` with the payload shape the hook parses, and
  what `$CLAUDE_PROJECT_DIR` expands to at fire time, is checkable only after Daniel
  applies the snippet and opens a real session.
- **The hook's board-locked path.** The 6-second watchdog around a held `flock` is
  asserted by the hook's author against a live lock; reproducing a held `BoardLock`
  deterministically requires the lock-hog machinery in `atlas-os/tests/heartbeat/`, which
  belongs to that suite, not this one.
- **The cloud-VM uid-0 and `/home/user*` detections.** The hook's third VM signal
  (`CLAUDE_CODE_REMOTE*`) is exercised; the first two cannot be produced honestly on a
  developer Mac without root, and faking `id -u` would test the fake.
- **`cs sessions` against the real `claude agents --json`.** The stub answers with the
  documented shape; whether the real CLI still emits `sessionId`/`cwd` rows is the same
  class of blind spot as the `View:` line above, and checkable the same way.
- **`heartbeat.py` and `worktree.py` internals.** Sections 13–14 consume them as real
  dependencies through symlinked roots; their own guarantees (locking, overlap, lease,
  worktree placement) are proven by `atlas-os/tests/heartbeat/` and
  `atlas-os/tests/worktree/`, and re-proving them here would test the wrong suite's
  claims in the wrong place.

## Adding a check

Put it in the section it belongs to, and give it a name that says what regresses if it
goes red. Assert the refusal *and* its exit code — half the value here is that a guard has
been seen go red. Where a check could pass vacuously (an empty input, a missing bait, a
stub that never ran), add the positive control next to it; sections 1, 2, 4, 7, 8 and 9
each have one already. The design rules are restated at the top of [`run.sh`](run.sh).

Then **watch it fail**. Break `bin/cs` the one way the check exists to catch, in a copy —
`cp -R cloud-sessions/{bin,tests} "$(mktemp -d)/cloud-sessions"`, edit the copy, run
`run.sh` from there — and confirm the red is your check and not forty others. A new check
that has only ever been green is a claim, not a test. That is how the table above was
produced, and it is how the pristine-`$HOME` bug in section 10 was found: the check as
first written passed against a doctor that had been given a side effect.

Four harness helpers exist for the awkward cases: `trun` (a pty with stdin closed),
`ptyrun` (a pty with an answer typed at the prompt), `manifest` (every file under a
directory, by content hash) and `argflat` (a whole invocation's argv on one line, so it
can be compared with `eq` rather than matched loosely with `has`).
