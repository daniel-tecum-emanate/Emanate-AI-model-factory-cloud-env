# Cloud VM re-verification — probe 4 (2026-08-14)

> **Snapshot, not current state.** Probe 4 of seven, at `5a34a1f`. Its headline defect — GNU
> `script` swallowing the child's exit code, leaving twelve refusal checks blind — was fixed
> in `e9b7330` and confirmed by probe 5. `D3a`/`D3b` are now *skipped with a reason* under
> root rather than failing. Current state: [`verified-facts.md`](verified-facts.md) · all
> seven probes: [`README.md`](README.md#the-probe-reports--primary-evidence-not-current-state).

Measurement only. Nothing was fixed. Commit under test: `5a34a1f` (detached HEAD, confirmed clean at start and end).

## Verdict table

| Check | Result |
|---|---|
| At/after `5a34a1f` | ✅ yes (`5a34a1f`, HEAD, detached) |
| `cloud-sessions/tests/run.sh` green | ❌ **NO** — 219 passed, 14 failed, exit 1 |
| Three forbidden strings absent from cs-suite output | ✅ yes, none of the three appear |
| `harness/stub-actually-invoked` passes | ✅ pass |
| `gate/detached-head-nonzero` passes | ❌ **FAIL** |
| `start/records-mode-bundle` passes | ✅ pass |
| `D1c/id-not-from-embedded-git-log` passes | ✅ pass |
| `atlas-os/tests/heartbeat/run.sh` | ✅ green, 90 passed / 0 failed (5 defects recorded as notes, unchanged) |
| `atlas-os/tests/worktree/run_tests.sh` | ✅ green, 53 passed / 0 failed |
| `atlas-os/bin/verify_system.sh` | ✅ 21 passed / 1 failed — only failure is the expected `append-only:atlas-os/telemetry/runs` |
| The three named cs fixes, by direct inspection/execution | ✅ all three work correctly |
| `cs doctor` warns on detached HEAD | ✅ warns correctly |
| `cs doctor` verdict line on this VM | ⚠️ **`not ready.`**, not `not dispatchable from here.` (see Test 4) |

**Bottom line: the cs suite is not green on this VM**, but the failures do not look like cs regressions. Every one of the 14 failures is explained below, and `cs` itself — tested directly, outside the test harness — behaves correctly in every case I checked. The unfixed problem is in the *test harness's* measurement of exit codes, plus one test whose failure-injection premise doesn't hold when run as root. That is still a real, reportable defect: the suite cannot currently be trusted to catch a real regression in any of the 12 `gate/*`/`start/*`/`D2c`/`D5b` refusal paths, because its assertion mechanism silently reports 0 no matter what `cs` actually returns.

---

## Test 1 — is the cs suite finally green on Linux?

**No.** `bash cloud-sessions/tests/run.sh`, un-piped:

```
219 passed  14 failed  0 xfail(known-open)  0 skipped
not green.  failed: D2c/doctor-exits-nonzero-when-blocked D3a/env-set-write-failure-preserves-file D3b/env-set-reports-write-failure D5b/fork-upstream-exit-nonzero start/empty-task-nonzero start/unknown-flag-nonzero gate/not-a-repo-nonzero gate/unborn-branch-nonzero gate/detached-head-nonzero gate/no-origin-nonzero gate/non-github-origin-nonzero gate/dirty-tree-nonzero gate/no-upstream-nonzero gate/unpushed-nonzero
```
Exit code: `1`.

### The three forbidden strings

None of `script: unexpected number of arguments`, `too few X's`, `command not found` appear anywhere in the output. The two portability bugs those strings would indicate (GNU/BSD `script` argument shape, and the earlier `mktemp` template bug) are genuinely fixed. This is a different, fourth issue.

### The four named checks

- `harness/stub-actually-invoked` — **PASS**
- `D1c/id-not-from-embedded-git-log` — **PASS**
- `start/records-mode-bundle` — **PASS**
- `gate/detached-head-nonzero` — **FAIL** (`expected a NON-zero exit (a refusal), got 0`)

### Root cause of all 14 failures

**12 of the 14** (`D2c`, `D5b`, `start/empty-task-nonzero`, `start/unknown-flag-nonzero`, and all 8 `gate/*-nonzero` checks) share one root cause, and it is not in `cs`. Every one of them is asserted via the test harness's `trun()` helper (`cloud-sessions/tests/run.sh:162-173`), which runs the command under:

```
script -q -c "$_cmd" /dev/null
```

On this VM's GNU util-linux (`script from util-linux 2.39.3`), that form does **not** propagate the wrapped command's exit code — `script` itself always returns 0, regardless of whether the child succeeded or failed:

```
$ script -q -c "false" /dev/null; echo $?
0
$ script -q -c "true" /dev/null; echo $?
0
```

GNU `script` only forwards the child's exit status with an explicit `-e`/`--return` flag:

```
$ script -e -q -c "false" /dev/null; echo $?
1
```

I confirmed this is a harness artifact, not a `cs` regression, three ways:

1. Ran `cs doctor` directly (no `script` wrapper) on a dirty tree with a real origin — exits `1`, correctly, with the `not dispatchable from here.` verdict.
2. Ran `cs start "x"` inside `script -q -c "..." /dev/null` (exactly as `trun()` does) on a detached-HEAD repo — reports the refusal text correctly but the wrapper exit code is `0`.
3. Re-ran the identical command with `script -q -e -c "..." /dev/null` — the wrapper then correctly reports exit `1`.

So `cs` is refusing every one of these cases as designed; the harness's `rc_nonz` assertion just can't see it, because `script` swallows the real exit code before the harness reads `$?`. This is a live gap in the suite's ability to catch a real regression in any `trun`-gated refusal path (including the very gate — detached HEAD — this probe was asked to confirm).

**The remaining 2** (`D3a/env-set-write-failure-preserves-file`, `D3b/env-set-reports-write-failure`) are unrelated and don't go through `trun()` — they use the plain `run()` helper. Their premise is that `chmod 500 ~/.claude` makes the directory unwritable so `env set`'s atomic `mkstemp` write fails, and `cs` should detect that and refuse cleanly. On this VM the test runs as **root**:

```
$ whoami
root
```
Root bypasses directory write-permission checks (`chmod 500` does not stop root from creating a file inside), so `mkstemp` succeeds, `env set` succeeds, exits 0, and the settings file legitimately changes — hence the hash mismatch (D3a) and the non-refusal (D3b). Reproduced directly, outside the harness, with the exact same fixture setup — same result. This is an environment property (probe running as root), not a `cs` defect; the failure-injection technique this test relies on doesn't work under root at all.

---

## Test 2 — the other suites, unchanged

- `bash atlas-os/tests/heartbeat/run.sh` → **exit 0**, `90 passed  0 failed  0 skipped  5 defect(s) recorded`. The 5 are documented defects surfaced as `note`s (FREEZE not enforced, illegal-claim-on-live-row, ENDED-row revival bypassing overlap gate, unauthenticated session ids, `--board` doesn't isolate) — same class as prior probes, not new failures. Final line: `green.`
- `bash atlas-os/tests/worktree/run_tests.sh` → **exit 0**, `53 passed  0 failed  0 skipped`. Final line: `green.`
- `bash atlas-os/bin/verify_system.sh` → **exit 1**, `21 passed  1 failed  1 skipped`. The one failure is exactly the expected one:
  ```
  FAIL  append-only:atlas-os/telemetry/runs
        file SHRANK vs HEAD (71 -> 0 bytes); history is append-only
  ```
  No other failures. Skip is `no-env-values-in-tracked-files` (no `.env` at repo root — expected, not a failure).

All three match the documented baseline. Nothing regressed here.

## Test 3 — the three cs fixes, directly

Read `cloud-sessions/bin/cs` in full. All three fixes are present and correct, confirmed by execution:

- **`cs_mktemp`** (`bin/cs:181`, `mktemp -t "$1.XXXXXX"`): ran `mktemp -t cs-dispatch.XXXXXX` directly — GNU accepts it, produced a real file (`/tmp/cs-dispatch.gmWGbp`), exit 0.
- **`run_under_pty`** (`bin/cs:167-178`): the probe at the top of `cs` (`script -q -c true /dev/null`) correctly selects `gnu` on this VM — confirmed `script -q -c true /dev/null` exits 0 (succeeds) and the BSD form `script -q /dev/null true` exits 1 (fails) here. So `cs`'s own branch selection is correct. (Note: this is the exact same GNU `script` invocation whose exit-code-swallowing broke `trun()` in Test 1 — but `cs` itself never relies on `run_under_pty`'s exit code, since both call sites in `cmd_start` wrap it with `|| true` and parse the `View:` line from output instead. So the exit-code quirk that breaks the test harness does not affect `cs`'s real behavior.)
- **`open_url`** (`bin/cs:184-188`): confirmed both `command -v open` and `command -v xdg-open` report absent on this VM. Ran `cs web` (safe, headless): it printed
  ```
  no browser opener on this system. Open it yourself:
    https://claude.ai/code
  ```
  and exited 0 — no error, as designed.

## Test 4 — doctor accuracy on a detached HEAD

`cloud-sessions/bin/cs doctor`, run from this checkout (detached HEAD, tree clean):

```
 warn   repo          internal-company-tool on a DETACHED HEAD — dispatch will refuse
```
```
not ready.  Fix the failures above, then re-run: cs doctor
```

Doctor **does** now warn about the detached HEAD, correctly and specifically — that part of the fix is confirmed working. But the final verdict line the probe brief asked me to confirm (`not dispatchable from here.`) does **not** appear here. Instead the verdict is `not ready.`, a third, more severe state. That's because this VM independently fails three checks that have nothing to do with the detached-HEAD gate and take precedence in `cmd_doctor`'s `fail`-before-`blocked` logic:

```
 fail   auth          authMethod=oauth_token. API-key auth cannot use --cloud or --teleport. Run: claude auth login
 fail   env           ANTHROPIC_BASE_URL is set — it overrides claude.ai auth and disables cloud sessions
 fail   github(local) gh not authenticated — run: gh auth login
```

This is correct, expected `doctor` behavior given this VM's actual auth setup (not a claude.ai subscription session, `ANTHROPIC_BASE_URL` set, no local `gh` auth) — `doctor` is right to report `not ready.` rather than the milder `not dispatchable from here.`, since real failures exist beyond the detached-HEAD block. It just means the exact verdict string named in the probe brief isn't the one this particular VM produces; a future reader should not assume `not dispatchable from here.` was observed here — it wasn't, for a documented and correct reason.

One incidental finding worth flagging: partway through this probe, running the three test suites in Test 1/2 caused three tracked `RESULTS.md` files (`atlas-os/tests/heartbeat/RESULTS.md`, `atlas-os/tests/worktree/RESULTS.md`, `cloud-sessions/tests/RESULTS.md`) to be regenerated in the working tree as an ordinary side effect of running those suites — this briefly made `cs doctor` report "3 uncommitted change(s)" and would have changed the Test 4 reading. I ran `git restore` on exactly those three files (nothing else) before taking the doctor reading quoted above, so it reflects a genuinely clean tree. Nothing else in the tree was touched by testing.

## Still broken

- **The cs regression suite (`cloud-sessions/tests/run.sh`) is not green on Linux**, and the specific defect is new: its `trun()` helper wraps every TTY-requiring assertion in `script -q -c "$cmd" /dev/null`, and on this VM's GNU util-linux that form always returns exit 0 from `script` itself, never the wrapped command's real exit code (fix: add `-e`/`--return` to the GNU branch, mirroring the same fix already applied to `run_under_pty` in `bin/cs`). This currently masks the true pass/fail of **12 checks**: `D2c`, `D5b`, `start/empty-task-nonzero`, `start/unknown-flag-nonzero`, and all 8 `gate/*-nonzero` checks — including the specific `gate/detached-head-nonzero` check this probe was sent to confirm. I verified by direct, non-`trun` execution that `cs` itself refuses correctly in every one of these cases; the suite's `has`/text assertions for the same cases also pass. Only the exit-code layer is blind. Until this is fixed, the suite cannot detect a real regression in any of these 12 refusal paths — a future `cs` change that silently stopped refusing one of these cases would still show `219 passed 14 failed`, unchanged.
- **`D3a`/`D3b` (`env set` write-failure handling) cannot be validated on this VM at all**, because the test's failure-injection technique (`chmod 500` on the settings directory) does not produce a real failure when the suite runs as root, which it does here. This isn't a `cs` defect — root correctly writes through the permission bits — but a reader should not conclude `env set`'s atomic-write failure handling was exercised or verified by this run. It was not, on this VM, under this suite's current technique.
- **`cs doctor`'s exact verdict string on this VM is `not ready.`, not `not dispatchable from here.`** — correct behavior, but worth flagging so a future reader doesn't assume the milder verdict was observed. This VM's own auth/`gh` setup is not representative of a clean dispatch-ready session.

Nothing else was found broken. In particular: the three previously-fixed portability bugs (BSD/GNU `script` argument shape, `mktemp` template, `open`/`xdg-open` fallback) all hold up under direct execution, `heartbeat`/`worktree`/`verify_system` are exactly at their documented baselines, and none of the three named forbidden strings appear anywhere in the cs-suite output.
