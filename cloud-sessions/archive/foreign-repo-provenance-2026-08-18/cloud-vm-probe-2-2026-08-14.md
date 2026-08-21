# Cloud VM re-verification probe 2 — 2026-08-14

> **Snapshot, not current state.** Probe 2 of seven, at `e48a852`. The 58 failures it
> reports were **one root cause** and are fixed; the suite is green now. The
> `atlas-os` findings here were also since addressed or accepted. Current state:
> [`verified-facts.md`](verified-facts.md) · all seven probes:
> [`README.md`](README.md#the-probe-reports--primary-evidence-not-current-state).

Ran as RE-VERIFICATION PROBE 2, checking whether the fixes made in response to
[`cloud-vm-probe-2026-08-14.md`](cloud-vm-probe-2026-08-14.md) actually hold on this VM.
Confirmed at commit `e48a852` (`git log --oneline -3` — `e48a852 Verify the cloud
environment in a real VM; fix what it disproved`, `14e0c49`, `c6b7093`). Every command
below was actually executed in this session; output is quoted verbatim (trimmed only
where noted).

## Verdict table

| # | Test | Result | Evidence |
|---|---|---|---|
| 1 | `cs` test suite green | **FAIL** | 175 passed, 58 failed, exit 1. The `mktemp` fix held (no more warning), but a *different*, larger bug — `trun()`'s use of BSD `script` syntax — accounts for nearly all 58 failures. |
| 2a | `atlas-os/tests/heartbeat/run.sh` | **FAIL (test bug, not a real defect)** | 89 passed, 1 failed, exit 1. The one failure is a missing branch in the test's own case-sensitivity check, not a heartbeat.py defect. |
| 2b | `atlas-os/tests/worktree/run_tests.sh` | **PASS** | 53 passed, 0 failed, exit 0. |
| 2c | `atlas-os/bin/verify_system.sh` | **FAIL (genuine, platform-independent bug)** | 21 passed, 1 failed, 1 skipped, exit 1. `atlas-os/telemetry/runs` is a directory; the append-only check assumes it's a file. Would misfire on macOS too. |
| 3 | Network-claim correction (no `x-deny-reason` header; body-only 403) | **PASS — reconfirmed independently** | Raw-socket `CONNECT` shows the plain-text body; plain `curl` genuinely swallows it. All 6 remaining `x-deny-reason` mentions in `cloud-sessions/` are corrections, none stale. |
| 4 | Detached HEAD handling | **PASS** | HEAD is detached, exactly as probe 1 found. `git_gate()` in `bin/cs` correctly refuses with "detached HEAD..." before any dispatch — verified by reading the code, not by running `cs start`/`cs handoff`. |
| 5 | Setup script idempotency | **PASS** | Second run is idempotent: `apt-get install gh` no-ops cleanly (~12s, mostly `apt-get update`), `pip install` drops from ~7s to ~0.7s ("already satisfied"), no errors, exit 0 both times. |

---

## Test 1 — the cs test suite (THE headline)

```
$ bash cloud-sessions/tests/run.sh
...
175 passed  58 failed  0 xfail(known-open)  0 skipped
not green.  failed: harness/stub-actually-invoked D1a/id-not-from-task-echo
D1b/id-not-from-trailing-line D1c/id-not-from-embedded-git-log
D1c/git-log-really-was-in-the-brief D1d/no-view-line-warns D2a/doctor-blocked-verdict
D2h/doctor-green-when-everything-passes D2i/doctor-zero-when-ready
D3a/env-set-write-failure-preserves-file D3b/env-set-reports-write-failure
D5a/fork-upstream-refused D8a/plan-path-is-repo-root-relative D9a/handoff-title-is-short
D9b/ls-shows-the-handoff-instruction start/refuses-empty-task start/refuses-flag-only
start/refuses-unknown-flag start/warns-on-non-ccpool-env start/env-passed-through
start/records-mode-clone start/records-repo start/records-branch
start/records-mode-bundle start/sets-force-bundle handoff/brief-says-autonomous
handoff/brief-requires-branch-and-push handoff/brief-requires-pr
handoff/brief-asks-for-unverified handoff/brief-covers-blocked-hosts
handoff/brief-warns-curl-hides-body handoff/brief-forbids-force-push gate/not-a-repo
gate/not-a-repo-even-with-bundle gate/unborn-branch gate/unborn-even-with-bundle
gate/detached-head gate/detached-even-with-bundle gate/no-origin
gate/no-origin-bundle-warns gate/no-origin-bundle-dispatches gate/non-github-origin
gate/non-github-bundle-dispatches gate/dirty-tree gate/dirty-bundle-warns-about-untracked
gate/dirty-bundle-dispatches gate/no-upstream gate/no-upstream-names-the-fix
gate/no-upstream-bundle-dispatches gate/unpushed-commits gate/unpushed-bundle-dispatches
gate/fork-upstream gate/clean-and-pushed-accepted gate/clean-and-pushed-exit-zero
args/double-dash-makes-a-literal-task args/double-dash-does-not-bundle
args/task-may-start-with-dashes args/unquoted-task-is-joined
```

Real exit code (captured separately from a plain, un-piped invocation so `tee`
couldn't mask it): `1`.

**The `mktemp` warning is confirmed gone.** `grep -i "too few X" <full output>` →
no match. So that specific fix held.

**But the tally is barely better than probe 1's (57/232 failing → 58/233 failing).**
That is the most important finding of this probe: the mktemp fix did not meaningfully
move the needle, because it was not the dominant cause of failures. Root-causing the
remaining 58:

```
$ which script && script --version
/usr/bin/script
script from util-linux 2.39.3

$ script -q /dev/null echo hello </dev/null
script: unexpected number of arguments
Try 'script --help' for more information.

$ script --help
Usage:
 script [options] [file]
```

`cloud-sessions/tests/run.sh` has two command runners:

- `run()` (line 142) — plain `"$@" 2>&1`, no TTY simulation. Used by most of section 3
  ("every command in bin/README.md") — this is why those checks pass.
- `trun()` (line 151) — `script -q /dev/null "$@" </dev/null 2>&1`, meant to simulate
  a TTY so `cs start`/`cs handoff` don't bail on "refuses without a tty". This is
  **BSD `script` syntax** (`script [-akq] [file [command ...]]`). GNU util-linux's
  `script` (what ships on this Ubuntu 24.04 VM) only accepts `[options] [file]` — no
  trailing command — and rejects the extra arguments outright with "unexpected number
  of arguments", producing empty/garbage `$OUT` for every `trun`-based assertion
  regardless of what `cs` actually printed.

Every failing check name in the list above that isn't a section-3 `run()`-based check
is a `trun()`-based check: all of `harness/stub-actually-invoked`, the `D1`/`D2`/`D3`/
`D5a`/`D8a`/`D9` block, every `start/*` and `handoff/brief-*` case, the entire
`gate/*` section, and the `args/*` double-dash/task-parsing cases. This is a single
root cause with ~58 symptoms, not 58 independent bugs — and it is a genuine,
unfixed portability gap in the test harness itself (`tests/run.sh`), not in `cs`
or in the mktemp change.

**This did not hold. The suite is not green on this VM**, and the reason is different
from what was fixed.

## Test 2 — the other suites on this platform

### 2a. `atlas-os/tests/heartbeat/run.sh`

```
89 passed  1 failed  0 skipped  5 defect(s) recorded
not green.  failed: 03.21-case-sensitivity-vs-the-filesystem
```

Failure detail:
```
FAIL  03.21-case-sensitivity-vs-the-filesystem
      rc=0 on a case-variant claim, filesystem is case-sensitive
registered `lane-probe` on heartbeat/STATE.md
  owns : sandbox/docs/file.md
```

Root cause (`atlas-os/tests/heartbeat/sections/03-subtree.sh:93-111`): the test
registers a claim on `sandbox/Docs/File.md`, then tries to register a second, case-
different claim on `sandbox/docs/file.md`, and detects at runtime whether the
underlying filesystem is case-sensitive or -insensitive (`fs_case`). Its verdict logic
only has two passing branches:

```sh
if [[ $rc -eq 0 && "$fs_case" == "insensitive" ]]; then
  t_pass ...   # both registered; that's the known defect on case-insensitive fs
elif [[ $rc -eq 2 ]]; then
  t_pass ...   # gate refused the case-variant claim
else
  t_fail "rc=$rc on a case-variant claim, filesystem is case-$fs_case"
fi
```

The comment above it says outright: "The claim algebra is case-SENSITIVE. On this
machine (and on every default macOS checkout) the filesystem is case-INSENSITIVE...".
On this Linux VM the filesystem genuinely is case-sensitive (`ext4`), so `rc=0` here
is the *correct* result — two distinct paths, both grantable — but the test was only
ever written to expect `rc=0` when the filesystem is case-insensitive (a real defect
worth recording) or `rc=2` (gate refuses). It has no passing branch for "rc=0 AND
filesystem is genuinely case-sensitive," which is exactly the state on this VM. So
this is a **test-script bug (missing branch)**, exposed by running on a case-sensitive
filesystem for the first time — not a heartbeat.py defect and not something the
probe-1 fixes touched.

The other 5 "defect" notes in the run (FREEZE mode not enforced, illegal-claim-on-
live-row, ENDED-row revival bypassing the overlap gate, unauthenticated session IDs,
`--board` not isolating the collision namespace) are pre-existing, intentionally-
recorded findings from the suite's own defect-tracking mechanism, not new failures —
they show as `ok` with a `note`, and were not part of what probe 1 or this probe was
asked to re-verify.

### 2b. `atlas-os/tests/worktree/run_tests.sh`

```
53 passed  0 failed  0 skipped
green.
```
Exit code 0. Fully green, no caveats.

### 2c. `atlas-os/bin/verify_system.sh`

```
21 passed  1 failed  1 skipped
not green.  failed: append-only:atlas-os/telemetry/runs
```

Failure detail:
```
FAIL  append-only:atlas-os/telemetry/runs
      file SHRANK vs HEAD (71 -> 0 bytes); history is append-only
```

Root cause (`atlas-os/bin/verify_system.sh:180-194`):

```sh
APPEND_ONLY=(atlas-os/metrics/history.jsonl atlas-os/telemetry/runs)
for p in "${APPEND_ONLY[@]}"; do
  ...
  OLD=$(git show "HEAD:$p" 2>/dev/null | wc -c | tr -d ' ')
  NEW=$(wc -c < "$p" 2>/dev/null | tr -d ' ')
  if [[ "${NEW:-0}" -lt "${OLD:-0}" ]]; then
    bad "append-only:$p" "file SHRANK vs HEAD ($OLD -> $NEW bytes); history is append-only"
```

`atlas-os/telemetry/runs` is a **directory**, not a file:

```
$ ls -la atlas-os/telemetry/runs
drwxr-xr-x  .gitkeep  2026-08-13.jsonl (2762986 bytes)  README.md

$ wc -c atlas-os/telemetry/runs
wc: atlas-os/telemetry/runs: Is a directory

$ git show HEAD:atlas-os/telemetry/runs
tree HEAD:atlas-os/telemetry/runs

.gitkeep
2026-08-13.jsonl
README.md
```

`git ls-files --error-unmatch atlas-os/telemetry/runs` succeeds (it matches tracked
files under the directory), so the check treats the path as a trackable blob. But
`wc -c < "$p"` on a directory fails silently under `2>/dev/null`, yielding `NEW=0`,
while `git show HEAD:$p` for a tree path returns a textual tree listing (`tree
HEAD:...\n\n.gitkeep\n2026-08-13.jsonl\nREADME.md`, which is 71 bytes) instead of blob
content. The comparison `0 < 71` then reads as "the file shrank."

**This is a genuine logic bug in `verify_system.sh`, and it is not a macOS/Linux
portability issue** — `wc -c < dir` and `git show HEAD:dir` behave identically on
macOS's BSD `wc` and any git version; the same directory structure would trip this
check there too. It's a latent bug that just hadn't been exercised before (the append-
only list conflates a directory of per-day telemetry files with the single-file
`metrics/history.jsonl` case it was clearly designed for).

## Test 3 — the corrected network claim, reconfirmed independently

```
$ curl -sS -o body.txt -w "HTTP_CODE=%{http_code}\n" https://example.com
curl: (56) CONNECT tunnel failed, response 403
HTTP_CODE=000
(body.txt was never created — curl swallowed it)
```

`curl -v` shows the tunnel negotiation and a bare `403` status line, but still no body
and, critically, **no `x-deny-reason` header** — only `Content-Type`,
`X-Content-Type-Options`, `Content-Length: 69`.

Independently retrieved the actual refusal by opening a raw socket to the proxy and
issuing `CONNECT` by hand (not copying probe 1's approach, done fresh):

```python
s = socket.create_connection(("127.0.0.1", 46269), timeout=10)
s.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
# response:
HTTP/1.1 403 Forbidden
Content-Type: text/plain; charset=utf-8
X-Content-Type-Options: nosniff
Content-Length: 69

request blocked: no rule or allowlist entry allows host "example.com"
```

This reconfirms probe 1's correction exactly: the deny reason is a **plain-text 403
body**, there is **no `x-deny-reason` header**, and a plain `curl` genuinely cannot see
the body because it treats a non-2xx `CONNECT` response as a fatal tunnel failure
before the body is ever exposed.

Checked whether the codebase and docs now describe this correctly:

```
$ grep -rli "x-deny-reason" cloud-sessions/
cloud-sessions/reference/cloud-vm-probe-2026-08-14.md
cloud-sessions/reference/how-others-build-this.md
cloud-sessions/reference/verified-facts.md
cloud-sessions/reference/limits.md
cloud-sessions/tests/run.sh
cloud-sessions/playbooks/06-environments.md

$ grep -n "x-deny-reason" cloud-sessions/bin/cs
(no output — zero mentions)
```

All 6 hits are corrections:
- `verified-facts.md:330` — `### \`x-deny-reason: host_not_allowed\` does not exist — \`DISPROVEN\``
- `limits.md:76` — "**no `x-deny-reason` header** on this proxy"
- `how-others-build-this.md:366` — "not the `x-deny-reason` header this folder first assumed; measured 2026-08-14"
- `06-environments.md:67-68` — "the `x-deny-reason` header this folder previously claimed does not exist"
- `cloud-vm-probe-2026-08-14.md:175,212` — the original probe-1 finding
- `tests/run.sh:535-537` — a comment explaining the two related test assertions were pinned to the header claim and correctly went red when it was corrected

**Zero stale claims remain.** `bin/cs` itself never referenced the header (0 hits), so
there was nothing to fix there.

## Test 4 — detached HEAD

```
$ git rev-parse --abbrev-ref HEAD
HEAD

$ git status | head -3
HEAD detached from refs/heads/main
Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
```

Confirmed: HEAD is still detached on this VM, exactly as probe 1 found — this is a
property of how the environment provisions the repo, not something the prior fix
round claimed to change.

Read `git_gate()` in `cloud-sessions/bin/cs` (lines 114-156) rather than running `cs
start`/`cs handoff`, per instructions. The relevant line:

```sh
branch="$(git rev-parse --abbrev-ref HEAD)"
[[ "$branch" != "HEAD" ]] || die "detached HEAD. Check out a branch before dispatching."
```

Since `git rev-parse --abbrev-ref HEAD` literally prints the string `HEAD` when
detached (confirmed above), this check fires correctly and calls `die`, which (per the
rest of the script) prints the message and exits non-zero before any dispatch logic
runs. **`cs start`/`cs handoff` would correctly refuse to dispatch from this VM's
current state** — no misbehavior found by reading the code.

`bash -n cloud-sessions/bin/cs` → exits clean, no syntax errors.

## Test 5 — setup script idempotency

First run on this fresh VM:
```
$ apt-get update -qq && apt-get install -y -qq gh
... (two proxy 403 warnings for deadsnakes/ondrej PPAs, ignored)
Setting up gh (2.45.0-1ubuntu0.3) ...
real  0m13.310s

$ pip install --break-system-packages --quiet 'networkx>=3.0' python-pptx
real  0m7.009s

$ gh --version && python3 -c 'import networkx, pptx; print(networkx.__version__)'
gh version 2.45.0 (2025-07-18 Ubuntu 2.45.0-1ubuntu0.3)
3.6.1
```

Second, immediately-repeated run:
```
$ apt-get update -qq && apt-get install -y -qq gh
... (same two proxy 403 warnings for deadsnakes/ondrej PPAs)
real  0m11.834s
(no unpack/setup lines — gh was already the newest version, nothing to do)

$ pip install --break-system-packages --quiet 'networkx>=3.0' python-pptx
real  0m0.663s
(no output beyond the root-user warning — "already satisfied", pip did no work)

$ gh --version && python3 -c 'import networkx, pptx; print(networkx.__version__)'
gh version 2.45.0 (2025-07-18 Ubuntu 2.45.0-1ubuntu0.3)
3.6.1
```

**Idempotent, with no errors on the second run.** `apt-get install` re-verified the
package is present and did no unpacking (dropping from install+download time to just
`apt-get update` overhead); `pip install` recognized both packages were already
satisfied and dropped from ~7s to ~0.7s. Exit code was 0 both times. The two PPA-proxy
403 warnings (`deadsnakes`, `ondrej/php`) are pre-existing and appear identically on
both runs — they're non-fatal (`apt-get update` ignores the failed index and
continues), unrelated to the `gh`/`networkx`/`python-pptx` packages being tested, and
not something introduced or fixed by the probe-1 round.

---

## Still broken

1. **`cloud-sessions/tests/run.sh` is not green on Linux.** 58 of 233 checks fail.
   The mktemp fix held but was not the dominant cause — `trun()`'s use of BSD `script
   -q /dev/null CMD ARGS...` syntax is rejected outright by GNU util-linux's `script`
   (`Usage: script [options] [file]`, no trailing command), so every TTY-simulated
   assertion (all of sections `D1`-`D9`, `gate/*`, `start/*`, `handoff/brief-*`,
   `args/double-dash-*` and `args/task-may-start-with-dashes`/`unquoted-task-is-joined`)
   gets empty/garbage output regardless of what `cs` actually does. This needs its own
   fix (e.g. `script -qc "$*" /dev/null` or an `unbuffer`/`faketty` equivalent that
   works on both BSD and GNU `script`), separate from and larger than the mktemp issue.
2. **`atlas-os/bin/verify_system.sh`'s append-only check is broken for
   `atlas-os/telemetry/runs`**, which is a directory, not a file — `wc -c` on it
   silently reads as 0 bytes while `git show HEAD:<dir>` returns a tree listing instead
   of blob content, so the check always reads as "shrank." This is not a portability
   bug; it would reproduce identically on macOS. Needs either a file-vs-directory guard
   in the check, or the append-only list should point at a specific file under that
   directory instead of the directory itself.
3. **`atlas-os/tests/heartbeat/run.sh`'s `03.21-case-sensitivity-vs-the-filesystem`
   check has no passing branch for a genuinely case-sensitive filesystem returning
   `rc=0`**, which is the correct behavior and exactly what this VM's `ext4` produces.
   This is a test-authoring gap (the suite was only ever exercised on macOS's default
   case-insensitive filesystem), not a heartbeat.py defect.

## Confirmed fixed / holding

- The `mktemp -d -t cs-tests` → `mktemp -d -t cs-tests.XXXXXX` fix: the "too few X's"
  warning no longer appears anywhere in the `cs` test run.
- The network-refusal documentation: no stale `x-deny-reason` claims remain anywhere
  in `cloud-sessions/`; every mention is a correction, and independent re-verification
  confirms the plain-text-body, no-header behavior.
- `git_gate()`'s detached-HEAD handling: correct by inspection, refuses safely.
- `atlas-os/tests/worktree/run_tests.sh`: fully green, 53/53.
- Setup script idempotency: clean on a second run, no errors, no new claims to check
  here since this was net-new testing (not a probe-1 finding).
