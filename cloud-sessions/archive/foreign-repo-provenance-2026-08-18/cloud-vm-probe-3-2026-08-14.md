# Cloud VM re-verification probe 3 — 2026-08-14

> **Snapshot, not current state.** Probe 3 of seven, at `8527a27`. Everything in its "still
> broken" list was fixed in `5a34a1f` and `e9b7330` — the `mktemp`/`script`/`open`
> constructs in `bin/cs`, the harness's missing `-e`, and `doctor` calling a detached HEAD
> `ok`. Current state: [`verified-facts.md`](verified-facts.md) · all seven probes:
> [`README.md`](README.md#the-probe-reports--primary-evidence-not-current-state).

Ran as RE-VERIFICATION PROBE 3, measuring only — no fixes applied. Confirmed at commit
`8527a27` (`git log --oneline -3` — `8527a27 Fix a second BSD/GNU portability bug the
re-verification probe exposed`, `24bd92c`, `e48a852`). Every command below was actually
executed in this session; output is quoted verbatim (trimmed only where noted). Working
tree carries only the harness's own `RESULTS.md` log-append diffs from running the
suites — nothing else was touched.

## Verdict table

| # | Test | Result | Evidence |
|---|---|---|---|
| 1 | `cs` test suite green | **FAIL** | 195 passed, 38 failed, exit 1. The `script`-syntax fix from probe 2 held (no "unexpected number of arguments" anywhere), but a **third** BSD-only construct — `mktemp -t cs-dispatch` in `bin/cs` itself, not the harness — aborts real dispatch before `script` ever runs, plus a harness-side GNU `script` exit-code gap and a chmod-as-root gap in one test, account for all 38. |
| 2a | `atlas-os/tests/heartbeat/run.sh` | **PASS** | 90 passed, 0 failed, 5 defect(s) recorded (pre-existing, documented findings, not failures), exit 0. Test `03.21-case-sensitivity-vs-the-filesystem` passes, taking the case-**sensitive** branch (Linux ext4/overlayfs here is case-sensitive; the branch added after probe 2 was exactly this one). |
| 2b | `atlas-os/tests/worktree/run_tests.sh` | **PASS** | 53 passed, 0 failed, exit 0. Still green, as before. |
| 2c | `atlas-os/bin/verify_system.sh` | **FAIL (known, unfixed, genuine)** | 21 passed, 1 failed, 1 skipped, exit 1. Only failure is `append-only:atlas-os/telemetry/runs` — documented as known and correctly left unfixed (I10 human-only directory). Nothing new broke. |
| 3 | Hunt for a third portability bug | **FOUND — two, in the real `cs` binary** | `mktemp -t cs-dispatch` (`cloud-sessions/bin/cs:299`) fails on GNU coreutils ("too few X's"); `script -q "$log" "$CLAUDE" "${args[@]}"` (`cloud-sessions/bin/cs:306,308`) is the same BSD-only argument form probe 2 fixed **in the test harness** but never fixed **in the product code being tested** — confirmed it fails on GNU util-linux too, and silently (`\|\| true`) rather than loudly. Also found: `cmd_open`/`cmd_web` (`cloud-sessions/bin/cs:628-629`) shell out to `open`, a macOS-only binary absent on this VM — masked in tests by a stub, real on a real Linux run. |
| 4 | `cs` sanity commands | **PASS, with one doctor accuracy gap** | `bash -n`, `help`, `ls`, `env` all clean. `doctor` correctly returns non-zero ("not ready") given three real failures (oauth-only auth, `ANTHROPIC_BASE_URL` override, unauthenticated `gh`) and two real warnings (no TTY, dirty worktree) — but its "ok repo … on HEAD" line does **not** flag that HEAD is detached, even though `git_gate()` in the same file would refuse dispatch for exactly that reason. |

---

## Test 1 — the cs test suite (THE headline)

```
$ bash cloud-sessions/tests/run.sh
...
195 passed  38 failed  0 xfail(known-open)  0 skipped
not green.  failed: harness/stub-actually-invoked D1a/id-not-from-task-echo
D1b/id-not-from-trailing-line D1c/id-not-from-embedded-git-log
D1c/git-log-really-was-in-the-brief D1d/no-view-line-warns
D2c/doctor-exits-nonzero-when-blocked D3a/env-set-write-failure-preserves-file
D3b/env-set-reports-write-failure D5b/fork-upstream-exit-nonzero
D9a/handoff-title-is-short D9b/ls-shows-the-handoff-instruction
start/empty-task-nonzero start/unknown-flag-nonzero start/env-passed-through
start/records-mode-clone start/records-repo start/records-branch
start/records-mode-bundle start/sets-force-bundle gate/not-a-repo-nonzero
gate/unborn-branch-nonzero gate/detached-head-nonzero gate/no-origin-nonzero
gate/no-origin-bundle-dispatches gate/non-github-origin-nonzero
gate/non-github-bundle-dispatches gate/dirty-tree-nonzero gate/dirty-bundle-dispatches
gate/no-upstream-nonzero gate/no-upstream-bundle-dispatches gate/unpushed-nonzero
gate/unpushed-bundle-dispatches gate/clean-and-pushed-accepted
args/double-dash-makes-a-literal-task args/double-dash-does-not-bundle
args/task-may-start-with-dashes args/unquoted-task-is-joined
```

Real exit code, captured from a plain, un-piped invocation so `tee` couldn't mask it:
**`1`**.

**Checked the three specific things requested:**

- `grep -i "unexpected number of arguments"` over the full run (verbose, `-v`) →
  **no match**. Probe 2's `script`-syntax fix in the test harness's `trun()` held.
- `grep -i "too few X"` over the full run → **no match in the non-verbose run**, but
  **21 occurrences in the verbose (`-v`) run**, e.g.:
  ```
  $ script(tty) /tmp/cs-tests.inzsd8/csroot/bin/cs start harness probe
  mktemp: too few X's in template 'cs-dispatch'
  FAIL  harness/stub-actually-invoked
        expected output to contain: <arg>--cloud</arg> <arg>harness probe</arg>
  ```
  The non-verbose run doesn't print the raw stdout/stderr captured by `trun`, so this
  only surfaces with `-v`. It is real and it is the dominant cause of the 38 failures
  (see root causes below).
- `harness/stub-actually-invoked` — **FAILS**. This is the suite's designed self-guard
  ("a stub that is never called still lets every dispatch test 'pass' by refusing
  early") and it is doing exactly its job: it is correctly reporting that `cs start`
  never actually reached the fake `claude` stub on this VM, because `bin/cs` aborts
  before `script` runs (see Test 3). Its failure here is not spurious — it is the
  single most informative line in the whole run.

### Root-caused all 38 failures to three distinct bugs

Traced every failure by re-running with `-v` and correlating each `FAIL` with the raw
`trun` output immediately preceding it.

**Cause A — `mktemp -t cs-dispatch` in `bin/cs:299` (24 failures).** `set -euo
pipefail` is active at the top of `cs` (line 12). `local log; log="$(mktemp -t
cs-dispatch)"` fails on GNU coreutils:
```
$ mktemp -t cs-dispatch
mktemp: too few X's in template 'cs-dispatch'
```
Because the assignment's exit status is the substituted command's exit status, and
`errexit` is on, the `dispatch()` function aborts on that line — **before** the
`script -q "$log" "$CLAUDE" "${args[@]}"` call two lines later ever runs. The fake
`claude` stub is never invoked, `$log` is never populated, and every assertion that
reads the dispatch log or the resulting ledger row sees empty output. This accounts
for: `harness/stub-actually-invoked`, `D1a`, `D1b`, `D1c` (both), `D1d/no-view-line-warns`,
`D9a`, `D9b`, `start/env-passed-through`, `start/records-mode-clone`,
`start/records-repo`, `start/records-branch`, `start/records-mode-bundle`,
`start/sets-force-bundle`, `gate/no-origin-bundle-dispatches`,
`gate/non-github-bundle-dispatches`, `gate/dirty-bundle-dispatches`,
`gate/no-upstream-bundle-dispatches`, `gate/unpushed-bundle-dispatches`,
`gate/clean-and-pushed-accepted`, `args/double-dash-makes-a-literal-task`,
`args/double-dash-does-not-bundle`, `args/task-may-start-with-dashes`,
`args/unquoted-task-is-joined` — 24 in total.

**Cause B — GNU util-linux `script` does not propagate the wrapped command's exit
code by default (12 failures).** This is a gap in the test harness's own probe-2 fix,
not in `bin/cs`. Confirmed directly:
```
$ script -q -c "false" /dev/null </dev/null; echo $?
0
$ script -qe -c "false" /dev/null </dev/null; echo $?
1
```
`--return`/`-e` is required on GNU `script` to get the child's real exit status;
without it, `script`'s own (always-0-if-script-itself-ran) exit code is returned
instead. `trun()`'s GNU branch (`cloud-sessions/tests/run.sh:166`) does not pass
`-e`, so `$RC` is always 0 after a `trun`-wrapped call regardless of what the wrapped
`cs` invocation actually exited with. Every failure in this group is a `rc_nonz`
check whose paired `has`-content check on the *same* command **passed** — e.g.
`ok gate/not-a-repo` / `FAIL gate/not-a-repo-nonzero` — which is the fingerprint of
an exit-code-only miss. Affects: `D2c/doctor-exits-nonzero-when-blocked`,
`D5b/fork-upstream-exit-nonzero`, `start/empty-task-nonzero`,
`start/unknown-flag-nonzero`, `gate/not-a-repo-nonzero`,
`gate/unborn-branch-nonzero`, `gate/detached-head-nonzero`, `gate/no-origin-nonzero`,
`gate/non-github-origin-nonzero`, `gate/dirty-tree-nonzero`,
`gate/no-upstream-nonzero`, `gate/unpushed-nonzero` — 12 in total.

**Cause C — `chmod 500` does not restrict a root process (2 failures).** This session
runs as root. `cloud-sessions/tests/run.sh:317` does `chmod 500 "$HOME/.claude"` to
simulate a write failure for `cs env set`, but root ignores DAC permission bits on
Linux, so the write it's meant to block **succeeds**. This is a test-environment
artifact (root execution), not a BSD/GNU difference. Affects:
`D3a/env-set-write-failure-preserves-file`, `D3b/env-set-reports-write-failure` — 2 in
total.

`24 + 12 + 2 = 38` — every failure accounted for, no residue.

---

## Test 2 — the other suites

### `atlas-os/tests/heartbeat/run.sh`

```
90 passed  0 failed  0 skipped  5 defect(s) recorded
green.
```
Exit code `0`. `03.21-case-sensitivity-vs-the-filesystem` passes:
```
  ok  03.21-case-sensitivity-vs-the-filesystem
```
Read `atlas-os/tests/heartbeat/sections/03-subtree.sh:100-112` to confirm which
branch: the test creates `File.md`, checks whether `file.md` also resolves (that
would mean case-insensitive), and on this VM it does **not** —
```
mkdir -p "$WORK/casetest" && : > "$WORK/casetest/File.md"
[[ -e "$WORK/casetest/file.md" ]] && fs_case="insensitive"
```
— so `fs_case=sensitive`, and the register call correctly grants both
`sandbox/Docs/File.md` and `sandbox/docs/file.md` to two different lanes (they really
are two different files on a case-sensitive filesystem), taking the **case-SENSITIVE**
branch: `t_pass "gate allowed both and the filesystem is case-SENSITIVE — two distinct
files, correct"`. This is the exact branch probe 2 found missing on Linux.

The 5 "defect(s) recorded" are pre-existing, intentionally-surfaced heartbeat.py
findings (cross-board session-id collisions, FREEZE not enforced, etc.) — they are
`ok` test outcomes that *document* a defect, not suite failures; they existed before
this probe and are unrelated to portability.

### `atlas-os/tests/worktree/run_tests.sh`

```
53 passed  0 failed  0 skipped
green.
```
Exit code `0`. Unchanged from probe 2 — still fully green.

### `atlas-os/bin/verify_system.sh`

```
21 passed  1 failed  1 skipped
not green.  failed: append-only:atlas-os/telemetry/runs
```
Exit code `1`. The single failure:
```
FAIL  append-only:atlas-os/telemetry/runs
      file SHRANK vs HEAD (71 -> 0 bytes); history is append-only
```
This is the documented, deliberately-unfixed failure (the file lives in an I10
human-only directory). Confirmed it is still the **only** failure — nothing new
broke.

---

## Test 3 — hunt for a third portability bug

Grepped `cloud-sessions/tests/run.sh`, `atlas-os/tests/heartbeat/run.sh`,
`atlas-os/tests/worktree/run_tests.sh`, and `cloud-sessions/bin/cs` for every
construct on the requested list.

| Construct | Hit? | Verdict |
|---|---|---|
| `stat -f` | none | — |
| `sed -i ''` | none | — |
| `date -r` | none | — |
| `date -v` | none | — |
| `readlink -f` / `-e` | none | — |
| `mktemp` without X's | **`cloud-sessions/bin/cs:299`** — `mktemp -t cs-dispatch` | **Fails on GNU.** Tested directly: `mktemp -t cs-dispatch` → `mktemp: too few X's in template 'cs-dispatch'`, exit 1. This is the dominant root cause of Test 1's failures (Cause A above). BSD/macOS `mktemp -t prefix` auto-appends randomness; GNU requires literal `X`s in the template. |
| `script` with a trailing command | **`cloud-sessions/bin/cs:306,308`** — `script -q "$log" "$CLAUDE" "${args[@]}"` | **Fails on GNU**, same BSD-only form probe 2 already diagnosed and fixed in `trun()` — but that fix only touched the test harness, not this product code. Tested directly: `script -q /path/log echo hello world </dev/null` → `script: unexpected number of arguments`, exit 1. Reproduced the exact `dispatch()` sequence with `mktemp` "fixed" to a GNU-valid template and this line still misbehaves — GNU `script` parses `--cloud` (the first real arg) as an option to `script` itself (`script: unrecognized option '--cloud'`) and, because of the trailing `\|\| true`, the failure is swallowed silently: the wrapped `claude` binary is **never invoked**, `$log` stays empty, and `cs start` reports "no session ID was found" instead of erroring. This is currently hidden behind Cause A (mktemp aborts first) but would surface immediately if only the mktemp line were fixed — exactly the "hides behind the previous one" pattern probes 1 and 2 already saw twice. |
| `xargs -J` | none | — |
| `find -E` | none | — |
| `base64 -D` | none | — |
| `sed -E` | `cloud-sessions/tests/run.sh:170,219`, `cloud-sessions/bin/cs:104,158` | Fine on both (GNU and BSD `sed` both support `-E`); all four uses ran without error during the test runs above. |
| `grep -o` | `atlas-os/tests/heartbeat/run.sh:118` (`grep -oE`) | Fine on both; exercised successfully during the green heartbeat run. |
| `open` / `pbcopy` | **`cloud-sessions/bin/cs:628-629`** — `cmd_open`/`cmd_web` call the bare `open` command | **Absent on this VM.** `which open` → exit 1, `type open` → "not found". This is the macOS `open(1)` binary (opens a URL/file with the default app); Linux has no equivalent by that name (would need `xdg-open`). Currently invisible in the test suite because `cloud-sessions/tests/fixtures/fake-open` is stubbed onto `$PATH` for every test (`cp "$FIXTURES/fake-open" "$WORK/stub/open"`), so `open/opens-the-session-url` and `web/opens-the-session-list` pass against the stub regardless. A real `cs open <id>` or `cs web` run on this VM, outside the test harness, would fail with "open: command not found." Not run for real here — out of scope and unnecessary to prove; the absence of the binary is sufficient. |

**Net finding for Test 3: three BSD-only constructs found, two of them (`mktemp`,
`script`) inside `bin/cs` itself — the actual product, not just its test harness —
and both currently causing the suite to be red on this VM. The third (`open`) is
real but currently masked by the test stub.**

---

## Test 4 — does cs itself work here

Ran only the commands explicitly cleared as safe (`start`, `handoff`, `send`, `tp`,
`rc` were not run).

```
$ bash -n cloud-sessions/bin/cs
(no output, exit 0)

$ cloud-sessions/bin/cs help
cs — deliberate driver for Claude Code cloud sessions
...
(exit 0)

$ cloud-sessions/bin/cs ls
no dispatches recorded. Authoritative list: https://claude.ai/code
(exit 0)

$ cloud-sessions/bin/cs env
cloud environment
  ok   pinned        none — dispatches use your account's default environment
...
(exit 0)
```

```
$ cloud-sessions/bin/cs doctor
claude cloud sessions — preflight

  ok   binary        /opt/node22/bin/claude (2.1.231 (Claude Code))
 fail   auth          authMethod=oauth_token. API-key auth cannot use --cloud or --teleport. Run: claude auth login
 fail   env           ANTHROPIC_BASE_URL is set — it overrides claude.ai auth and disables cloud sessions
 fail   github(local) gh not authenticated — run: gh auth login
 warn   github(cloud) NOT CHECKABLE from here. If /web-setup has never been accepted, dispatch
                silently bundles instead of cloning: no branch, no PR, and routines 403.
  ok   repo          internal-company-tool on HEAD
  ok   remote        https://github.com/daniel0tgc/internal-company-tool
 warn   worktree      3 uncommitted change(s) — invisible to the cloud VM
 warn   pushed        no upstream for 'HEAD'
 warn   tty           not a TTY. `claude --cloud` refuses without one; run `cs start` from a real terminal
  ok   ledger        0 recorded dispatch(es) — /home/user/internal-company-tool/cloud-sessions/state/sessions.jsonl

not ready.  Fix the failures above, then re-run: cs doctor
```
Exit code `1`.

**Is the verdict correct?** The bottom line ("not ready", exit 1) is correct — every
one of the three `fail` rows is genuine and independently verified:
- `ANTHROPIC_BASE_URL=https://api.anthropic.com` really is set (`env | grep -i
  anthropic`), which is this managed environment's own routing, not a user mistake,
  but doctor's assessment of its *effect* (disables cloud-session auth) is accurate.
- `tty` — really no TTY: `tty` → `not a tty`, exit 1. Correctly flagged.
- `gh` unauthenticated and oauth-only `auth` — both plausible for this VM and not
  independently falsifiable without credentials; taking doctor's report at face value
  is reasonable here.

**One accuracy gap found, as hinted:** HEAD really is detached —
```
$ git status
HEAD detached from refs/heads/main
$ git rev-parse --abbrev-ref HEAD
HEAD
```
— and `git_gate()` (`cloud-sessions/bin/cs:122-123`) would refuse any real dispatch
for exactly that reason: `[[ "$branch" != "HEAD" ]] || die "detached HEAD. Check out
a branch before dispatching."`. But `doctor`'s own repo check
(`cloud-sessions/bin/cs:215`) prints `ok repo internal-company-tool on HEAD`
unconditionally — it interpolates whatever `git rev-parse --abbrev-ref HEAD` returns
into an "ok" line without checking whether that value is the literal string `HEAD`
(i.e. detached). On this VM that reads as "ok, on branch HEAD," which is misleading:
a real dispatch would be refused here for a reason `doctor`'s output never names,
even though the same file's own gate function knows how to detect it two lines away
from where `doctor` also reads the branch. The overall "not ready" verdict still
lands correctly (three other genuine failures dominate), but if those three were
fixed, `doctor` would falsely say "ready" while `cs start`/`cs handoff` would still
refuse on the detached-HEAD gate.

---

## Still broken

- **`cloud-sessions/bin/cs:299`** — `mktemp -t cs-dispatch` fails on GNU coreutils,
  aborting real dispatch under `set -euo pipefail` before anything else in
  `dispatch()` runs. **Not fixed.** This is a bug in the product, not the test
  harness — probe 2's fix only touched `cloud-sessions/tests/run.sh`.
- **`cloud-sessions/bin/cs:306,308`** — `script -q "$log" "$CLAUDE" "${args[@]}"` uses
  the same BSD-only argument form probe 2 diagnosed and fixed in the test harness's
  `trun()`, but the identical construct in the product code was never touched. It is
  currently masked by the `mktemp` bug above (which aborts first) and will surface as
  soon as that one is fixed — confirmed by direct reproduction. On GNU it silently
  no-ops (`\|\| true` swallows the `script: unrecognized option` failure) rather than
  erroring, so a fix must be verified by confirming the stub is genuinely invoked, not
  just by confirming the command exits without complaint.
- **`cloud-sessions/tests/run.sh:166`** — the GNU branch of `trun()` calls `script -q
  -c "$_cmd" /dev/null` without `-e`/`--return`, so `$RC` after a `trun`-wrapped
  invocation is always `script`'s own exit status (effectively always 0), never the
  wrapped `cs` command's real exit code, on GNU util-linux. This is a harness bug, not
  a `cs` bug, and it silently turns every `rc_nonz` assertion downstream of a `trun`
  call into a false pass on any host where `cs` actually does the right thing — the
  12 failures it currently causes are real refusals inside `cs` that the harness is
  simply failing to detect.
- **`cloud-sessions/tests/run.sh:317`** — `chmod 500 "$HOME/.claude"` does not
  restrict a root process on Linux, so the write-failure simulation for `cs env set`
  never actually fails. Not a portability bug; an artifact of this session running as
  root. Not fixed.
- **`cloud-sessions/bin/cs:628-629`** — `cmd_open`/`cmd_web` shell out to the bare
  `open` command, which does not exist on this VM (`which open` → exit 1). Currently
  invisible in the test suite (stubbed via `fixtures/fake-open`); would break `cs
  open`/`cs web` for a real user on Linux. Not fixed, not currently test-visible.
- **`atlas-os/bin/verify_system.sh`** — `append-only:atlas-os/telemetry/runs` still
  fails. Documented as known, genuine, and intentionally unfixed (I10 human-only
  directory). No change from probe 2.

## Confirmed still holding from probes 1 and 2

- No `script: unexpected number of arguments` anywhere in the harness's own `trun()`
  invocations (the probe-2 fix to `cloud-sessions/tests/run.sh` holds).
- `atlas-os/tests/heartbeat/run.sh` test `03.21` now passes on Linux, taking the
  case-sensitive-filesystem branch added in response to probe 2.
- `atlas-os/tests/worktree/run_tests.sh` remains fully green.
- `atlas-os/bin/verify_system.sh`'s one failure remains the same single known,
  unfixed, genuine defect — nothing new broke.
