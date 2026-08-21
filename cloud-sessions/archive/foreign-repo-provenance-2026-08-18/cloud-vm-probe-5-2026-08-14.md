# Cloud VM re-verification — probe 5 (2026-08-14)

> **Snapshot, not current state.** Probe 5 of seven, at `e9b7330` — the first fully green
> run on Linux. The check counts here are pinned to that commit and the suite has grown
> since; the two BSD constructs it flagged as unverified (`sort -V`, `grep`'s `\|`) were
> **run on this Mac afterwards and both are accepted** — output quoted in
> [`verified-facts.md`](verified-facts.md#green-on-both-platforms--verified-2026-08-14). All
> seven probes: [`README.md`](README.md#the-probe-reports--primary-evidence-not-current-state).

Measurement only. Nothing was fixed. Commit under test: `e9b7330` (at HEAD, detached, confirmed via `git log --oneline -3` before anything else ran).

## Verdict table

| Check | Result |
|---|---|
| At/after `e9b7330` | ✅ yes — `e9b7330` is HEAD |
| `cloud-sessions/tests/run.sh` green | ✅ **YES** — 231 passed, 0 failed, 2 skipped, exit 0 |
| All 12 named `*-nonzero` checks pass, by name | ✅ all 12 `ok` |
| `D3a`/`D3b` reported SKIPPED with a root-reason | ✅ yes, both |
| Final tally reports a non-zero skip count | ✅ `2 skipped` |
| `script -q -e -c "false" /dev/null` → `1` | ✅ confirmed |
| `script -q -c "false" /dev/null` → `0` | ✅ confirmed |
| `atlas-os/tests/heartbeat/run.sh` | ✅ green, 90 passed / 0 failed / 0 skipped, exit 0 |
| `atlas-os/tests/worktree/run_tests.sh` | ✅ green, 53 passed / 0 failed / 0 skipped, exit 0 |
| `atlas-os/bin/verify_system.sh` | ✅ 21 passed / 1 failed / 1 skipped, exit 1 — only failure is `append-only:atlas-os/telemetry/runs` |
| Fresh portability sweep (Test 3) | ⚠️ two latent GNU-only constructs found; both pass on **this** VM, both would need checking on macOS |
| `bash -n cloud-sessions/bin/cs` | ✅ syntax OK |
| `cs help` / `cs ls` / `cs env` / `cs doctor` / `cs web` | ✅ all exit as designed |
| `cs doctor` warns on detached HEAD | ✅ warns correctly |
| `cs web` prints the URL instead of erroring | ✅ confirmed |

**Bottom line: everything the first four probes were chasing is now fixed and confirmed green.** The `cs` regression suite passes in full on this VM, the `-e` fix does exactly what it claims, the other three suites are exactly at their documented baselines, and the four rounds of BSD/GNU bugs found across probes 1–4 stay fixed under direct re-execution. This fifth sweep, done with fresh eyes and reasoning from the *opposite* direction (constructs that pass here on GNU but could fail on the author's BSD/macOS box), turned up two candidates — `sort -V` and a `grep` BRE alternation — neither of which fails on this VM, so neither shows up as a suite failure. They are reported below as things a future reader should verify on an actual Mac, not as bugs "still broken" here.

---

## Test 1 — the cs suite

`bash cloud-sessions/tests/run.sh`, real un-piped exit code:

```
231 passed  0 failed  0 xfail(known-open)  2 skipped
2 skipped check(s) — a skipped check is not a passed check.
green.  No regression in the ten reviewed defects.
```
Exit code: `0`.

### The twelve named `*-nonzero` checks, individually confirmed `ok`

```
gate/detached-head-nonzero      ok
gate/dirty-tree-nonzero         ok
gate/no-upstream-nonzero        ok
gate/unpushed-nonzero           ok
gate/not-a-repo-nonzero         ok
gate/unborn-branch-nonzero      ok
gate/no-origin-nonzero          ok
gate/non-github-origin-nonzero  ok
start/empty-task-nonzero        ok
start/unknown-flag-nonzero      ok
D2c/doctor-exits-nonzero-when-blocked  ok
D5b/fork-upstream-exit-nonzero  ok
```

All twelve are the `trun()`-gated refusal-path checks that Probe 4 found silently blind (GNU `script -q -c ... /dev/null` was swallowing the wrapped command's real exit code, always returning 0). With `-e` added to the GNU branch, every one of them now correctly observes the child's nonzero exit and passes for real, not by the harness accident that a false pass would have looked identical to.

### D3a / D3b — skipped, not passed

```
skip  D3a/env-set-write-failure-preserves-file  (cannot make a directory unwritable for root; injection impossible)
skip  D3b/env-set-reports-write-failure  (cannot make a directory unwritable for root; injection impossible)
```

Both reasons name root explicitly, matching the brief's requirement. This VM still runs the suite as `root` (`whoami` → `root`), so `chmod 500` on the settings directory still can't produce a real write failure — the suite now recognizes that and skips rather than mis-scoring these two as pass or fail. Final tally line reports `2 skipped` — a non-zero skip count, as required. (Confirms the suite no longer silently treats "the injection didn't take" as a pass, and no longer treats it as an unexplained fail either.)

### The `-e` fix, checked independently of the suite

```
$ script -q -e -c "false" /dev/null; echo $?
1
$ script -q -c "false" /dev/null; echo $?
0
```

Exactly as expected: adding `-e`/`--return` is what makes GNU `script` forward the wrapped command's real exit status; without it, `script` itself always reports `0` regardless of what ran inside. This is the mechanism behind all twelve `*-nonzero` fixes above, confirmed outside the test harness so the harness result isn't the only evidence.

---

## Test 2 — the other suites

- `bash atlas-os/tests/heartbeat/run.sh` → exit `0`. `90 passed  0 failed  0 skipped  5 defect(s) recorded`. The five recorded items are the same class of documented, pre-existing defects as prior probes (space-splitting in `--owns`, session-id reuse across boards, FREEZE not enforced, illegal-claim rows contributing zero claims, ENDED-row revival bypassing the overlap gate) — surfaced as `note`s, not failures. Final line: `green.`
- `bash atlas-os/tests/worktree/run_tests.sh` → exit `0`. `53 passed  0 failed  0 skipped`. Final line: `green.`
- `bash atlas-os/bin/verify_system.sh` → exit `1`. `21 passed  1 failed  1 skipped`. The one failure is exactly the expected one:
  ```
  FAIL  append-only:atlas-os/telemetry/runs
        file SHRANK vs HEAD (71 -> 0 bytes); history is append-only
  ```
  The one skip is `no-env-values-in-tracked-files (no .env at repo root)` — expected, not a defect.

All three match the documented baseline exactly. No regressions.

---

## Test 3 — one more portability sweep, fresh eyes

Searched `cloud-sessions/bin/cs`, `cloud-sessions/tests/run.sh`, `atlas-os/tests/heartbeat/run.sh`, and `atlas-os/tests/worktree/run_tests.sh` (plus the Python under test, `heartbeat.py` and `worktree.py`) for constructs that differ between BSD and GNU userlands, testing the doubtful ones directly rather than reasoning about them.

### Checked and cleared (identical behavior expected on both platforms, or already defended)

- **`sed -i`** — not used anywhere in the four files.
- **`stat`** — not used as a command; the only textual hits are `git diff --stat`, unrelated.
- **`date`** — every call uses `date -u '+FORMAT'` with only POSIX-common specifiers (`%Y %m %d %H %M %S %Z`). No `date -d` (GNU) or `date -j -f` (BSD) parsing of arbitrary strings appears anywhere, so there's no divergent code path to trip on.
- **`readlink`** — not used.
- **`cp -a`** — not used.
- **`chmod` recursion** — `chmod -R u+rwX`, `chmod +x`, `chmod 500`/`700` — all portable forms, no GNU-only flags.
- **`grep -P`** — not used (see below for the one BRE-alternation hit, which is a different issue).
- **`head -c`** — not used.
- **`timeout`/`gtimeout`** — not used anywhere in these files.
- **`md5`/`sha256sum`/`shasum`** — `shasum -a 256` is used in `cloud-sessions/tests/run.sh`. Confirmed present (`/usr/bin/shasum`, version 6.04) — this is the Perl `Digest::SHA`-based tool that ships on both macOS and most Linux distros by default, so it's the actually-portable choice here (unlike `sha256sum`, which is GNU-only). No divergence.
- **`wc -l` whitespace** — used at `cloud-sessions/bin/cs:124,243,272`, every call piped through `tr -d ' '`. This already defends against the real BSD-vs-GNU difference (BSD `wc` right-pads its count with spaces; GNU doesn't) — confirmed correctly normalized, not a live bug.
- **`printf %q`** — not used.
- **`mapfile`/`readarray`** — not used anywhere.
- **`declare -A`, `local -n`, `${var^^}`, `${var,,}`** — none found in any of the four shell files. Combined with the bash-3.2 warning in the brief: since none of these bash-4+-only constructs are used, none of the suites should fail to *parse* under macOS's stock bash 3.2 — the reverse-direction bug the brief specifically asked me to check for does not appear to exist here. (I can't run an actual bash 3.2 to prove it positively; this is "nothing found that would break it," not "confirmed running under 3.2.")
- **`mktemp`** — already correctly handled: `cs_mktemp()` at `bin/cs:181` uses `mktemp -t "$1.XXXXXX"` with an explicit comment noting the BSD/GNU template difference, and the test harness's own `mktemp -d "..."` calls use the portable trailing-X form too. Tested directly: `mktemp -d -t 'test.XXXXXX'` succeeds here. This is a previously-fixed bug (probe 1/2 territory), re-confirmed still fixed.
- **`awk`** — two call sites, both simple `{print $N}` field splitting — portable POSIX awk, no GNU-only extensions.
- **`realpath`, `ps -o`/`ps aux`, `/proc/`, `kill -9`/`kill -0`, `egrep`/`fgrep`, `shopt -s globstar`, `printf -v`, process substitution `<(...)`** — none of these appear anywhere in the four files.
- **`${!var}` indirect expansion** — one use, at `bin/cs:225` (`${!v:-}`). Supported since bash 2.0; safe under bash 3.2.
- **Python layer** — `atlas-os/bin/heartbeat.py` shells out to nothing (pure file I/O + `fcntl.flock`, which Python provides consistently on both BSD/Darwin and Linux). `atlas-os/tests/worktree/staged/worktree.py` only ever invokes `git` via `subprocess.run` — no BSD/GNU-divergent external tool usage.

### Found — GNU-only, passes here, worth checking on an actual Mac

Two constructs that are **not** in the brief's explicit checklist turned up as genuine BSD/GNU divergences. Both are confirmed working correctly *on this VM* (GNU userland); both would need direct verification on macOS before anyone calls this platform-complete.

1. **`sort -V`** — `cloud-sessions/bin/cs:37`:
   ```sh
   bundled="$(ls -d "$HOME"/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude 2>/dev/null | sort -V | tail -1 || true)"
   ```
   `-V` (version sort) is a GNU coreutils extension; BSD `sort` (what ships on macOS) has historically not supported it and exits with `illegal option -- V`. Tested directly on this VM — GNU `sort -V` orders `1.1 1.2 1.10` correctly as `1.1, 1.2, 1.10`, confirming it works *here*. If BSD `sort` rejects `-V` on the author's Mac, the failure is **silent, not fatal**: the pipeline is `ls ... | sort -V | tail -1`, wrapped in `$(... || true)`. `sort` would write nothing useful to stdout, `tail -1` would just emit an empty line, and `bundled` would end up empty — `resolve_claude()` falls through to `return 1` instead of finding the newest VS Code-bundled binary. Not a crash, just a silently-skipped fallback path. Worth a macOS check; not a bug on this VM.

2. **`grep` BRE alternation (`\|`)** — `atlas-os/tests/worktree/run_tests.sh:278`:
   ```sh
   git status --porcelain | grep -q 'wt-root\|worktrees' && echo POLLUTED || echo TRUE
   ```
   GNU `grep` treats `\|` as alternation inside a basic regular expression (a long-standing GNU extension to POSIX BRE); BSD `grep` (macOS) does not — `\|` is either a literal or an error, not an alternation operator. Tested directly on this VM: `grep -q 'wt-root\|worktrees'` against a three-line input correctly matched. On BSD `grep`, this pattern would almost never match anything (it would be hunting for the literal, near-impossible substring `wt-root\|worktrees`), so `grep -q` would almost always report "no match" — meaning the test would silently print `TRUE` (not-polluted) *even when the tree actually is polluted*. That's a false negative in a test meant to catch pollution, not a crash, and it wouldn't show up as a suite failure on macOS — it would just quietly stop testing what it claims to test. This is the "reverse direction" case the brief was specifically watching for: a construct that passes here (GNU) and could misbehave, silently, on the author's Mac.

Neither of these is a suite failure on this VM — both are reported for the record, as risks to verify on real BSD/macOS, per the brief's request to note what was checked even when it's not broken here.

---

## Test 4 — cs sanity

`bash -n cloud-sessions/bin/cs` → **syntax OK**, no output, exit 0.

```
$ cs help    → exit 0, full command list printed, "no cs stop" note intact
$ cs ls      → "no dispatches recorded. Authoritative list: https://claude.ai/code" — exit 0
$ cs env     → prints current pin (none) and the network-tier explainer — exit 0
$ cs doctor  → exit 1, "not ready."
$ cs web     → "no browser opener on this system. Open it yourself: https://claude.ai/code" — exit 0
```

`cs doctor` confirms the detached-HEAD warning:
```
 warn   repo          internal-company-tool on a DETACHED HEAD — dispatch will refuse
```
The overall verdict is `not ready.` rather than a milder string, because this VM independently fails `auth`, `env` (`ANTHROPIC_BASE_URL` set), and `github(local)` (`gh` not authenticated) — none of which are `cs` bugs, all of which are this VM's actual auth posture, consistent with probe 4's reading of the same three failures.

`cs web` prints the session-list URL and exits 0 rather than erroring, as required — confirmed above.

Did not run `start`, `handoff`, `send`, `tp`, or `rc`, per the brief.

---

## Still broken

Nothing. Every check the brief asked for passed:

- The cs suite is fully green (231/0/0, 2 skipped-with-reason, exit 0), including all twelve previously-blind `*-nonzero` checks.
- The `-e` fix behaves exactly as claimed, confirmed independently of the suite.
- `heartbeat`, `worktree`, and `verify_system` are exactly at their documented, unchanged baselines.
- The fresh portability sweep found no construct that actually misbehaves on this VM. It surfaced two GNU-only constructs (`sort -V` in `bin/cs:37`, `grep`'s `\|` BRE alternation in `worktree/run_tests.sh:278`) that pass here but have not been verified on real BSD/macOS userland — flagged above for a future reader, not as failures of this run.
- `cs` itself parses cleanly and every safe sanity command (`help`, `ls`, `env`, `doctor`, `web`) exits and behaves as designed, including the detached-HEAD warning and the non-erroring `cs web` URL print.

This is an unambiguous green result. No regressions, no new failures, no residual mismeasurement in the suite's exit-code layer.
