# Linux verification — 462-check suite (2026-08-15 workstream)

**Run by:** cloud session, 2026-08-16, following
[`docs/handoff-suite-linux-462.md`](../../docs/handoff-suite-linux-462.md).
**Platform:** root on Ubuntu 24.04 x86_64, ~4 vCPU / 15 GB, HEAD detached at
`11bc8b3` (all normal for this environment, per the handoff).

## Verdict table

| suite | tally | exit code |
|---|---|---|
| `cloud-sessions/tests/run.sh` | 339 passed, 1 failed, 0 xfail, 5 skipped | 1 |
| `atlas-os/tests/process/run.sh` | 18 passed, 0 failed, 0 skipped | 0 |
| `atlas-os/tests/heartbeat/run.sh` | 90 passed, 0 failed, 0 skipped, 5 defect(s) recorded | 0 |
| `atlas-os/tests/worktree/run_tests.sh` | 53 passed, 0 failed, 0 skipped | 0 |

Three of the four suites are fully green. `cloud-sessions/tests/run.sh` has one
failure, detailed below.

### The three known portability-bug signatures

Checked for `script: unexpected number of arguments`, `too few X's`, and
`command not found` across the full `run.sh` output: **none appear**. No
regression of the three previously-fixed BSD-vs-GNU bugs.

### Skips in `cloud-sessions/tests/run.sh` (5, all expected)

- `D3a/env-set-write-failure-preserves-file` — cannot make a directory
  unwritable for root; injection impossible. **Expected under root**, per the
  handoff.
- `D3b/env-set-reports-write-failure` — same reason. **Expected under root.**
- `shell-init/parses-under-zsh`, `shell-init/zsh/passes-through`,
  `shell-init/zsh/binary-resolution` — zsh is not installed on this host.
  Environment-dependent skip, not a portability defect.

## Failure, in full

```
9 — git-gate states the suite did not reach
FAIL  gate/ssh-origin-fixture-really-is-ssh
      expected output to contain: git@github.com:example/gate-ssh.git
```

This check exists to prove the fixture is real before trusting the checks
built on it: it creates a repo, attaches the scp-style SSH origin
`git@github.com:example/gate-ssh.git`, then asserts `git remote get-url
origin` echoes that exact string back. On this host it does not.

**Root cause (isolated, reproduced outside the suite):** this environment's
git proxy injects unconditional URL rewrites via environment variables —

```
GIT_CONFIG_COUNT=3
GIT_CONFIG_KEY_1=url.https://github.com/.insteadOf
GIT_CONFIG_VALUE_1=git@github.com:
GIT_CONFIG_KEY_2=url.https://github.com/.insteadOf
GIT_CONFIG_VALUE_2=ssh://git@github.com/
```

— which rewrite any scp-style or `ssh://` GitHub origin to `https://` for
*every* git invocation on this host, including `git remote get-url`. This is
not in `~/.gitconfig`, `/etc/gitconfig`, or the repo's own config; it is
layered in per-invocation by the sandbox's `/root/.ccr` agent proxy. A
minimal repro outside the suite confirms it:

```
$ git remote add origin git@github.com:example/gate-ssh.git
$ git remote get-url origin
https://github.com/example/gate-ssh.git
```

This is **not** a fifth BSD-vs-GNU portability bug and does not indicate the
git-gate logic is broken: every downstream check that depends on the gate's
actual *behavior* with an SSH-style origin still passed —
`gate/ssh-origin-proceeds`, `gate/ssh-origin-exit-zero`, and
`gate/ssh-origin-not-mistaken-for-non-github` are all `ok`. Only the
fixture's own self-check of the literal string failed, because the
environment rewrote the URL before the assertion ever ran. On a Linux host
without this proxy's `GIT_CONFIG_*` injection, this check should pass as
written.

## `cs sessions` and `cs ls --json` (today's new commands)

### `cs sessions` — verbatim output, exit 0

```
1 local session(s); 0 mechanically movable

  SELF             live  a081b815  internal-company-tool
                        this session

MOVABLE means the VM could clone what this session is working on — NOT that moving it
is useful. A session waiting on your decision is movable and pointless to move.
Use /cloud, which reads each transcript and triages intent before dispatching.
```

Note: the handoff brief expected this to "report 0 sessions on a VM." The
actual count is 1 — this session lists itself as a live `SELF` row. It did
not crash, exited 0, and the output is well-formed; flagging the discrepancy
from the brief's expectation rather than treating it as a failure, since I
can't tell from the repo alone whether a `SELF` row was meant to be excluded
from the VM-observed count.

### `cs ls --json` — verbatim output, exit 0, confirmed valid JSON

```json
[
  {
    "ts": "2026-08-12T16:33:35-07:00",
    "id": "session_01B3GFCqymJeAJMyyn1QH1PT",
    "title": "Read-only VM verification probe (2026-08-12)",
    "repo": "internal-company-tool",
    "branch": "main",
    "mode": "tracked",
    "url": "https://claude.ai/code/session_01B3GFCqymJeAJMyyn1QH1PT"
  },
  {
    "ts": "2026-08-12T18:02:40-07:00",
    "id": "cse_01U63391Ug1GFZTvEgJUUNKe",
    "title": "Routine one-off probe (trig_01Bxk1rjkAZMB2P1pVS2L9st)",
    "repo": "internal-company-tool",
    "branch": "main",
    "mode": "tracked",
    "url": "https://claude.ai/code/cse_01U63391Ug1GFZTvEgJUUNKe"
  },
  {
    "ts": "2026-08-14T11:32:00-07:00",
    "id": "cse_0172tmL2VPMENs87PiiD4Mqk",
    "title": "Cloud VM probe: setup script + unverified claims (trig_01BC9JWjZnSXAiYvLSPjuMq4)",
    "repo": "internal-company-tool",
    "branch": "main",
    "mode": "tracked",
    "url": "https://claude.ai/code/cse_0172tmL2VPMENs87PiiD4Mqk"
  },
  {
    "ts": "2026-08-14T11:40:13-07:00",
    "id": "cse_014qWyrGkPfPiCr5cQaQdQHC",
    "title": "Cloud VM re-verification probe 2 (trig_013cmDzb9kiZueHTGVwtmbTg)",
    "repo": "internal-company-tool",
    "branch": "main",
    "mode": "tracked",
    "url": "https://claude.ai/code/cse_014qWyrGkPfPiCr5cQaQdQHC"
  },
  {
    "ts": "2026-08-14T11:49:47-07:00",
    "id": "cse_01NnRQwJk4AYALHbZXc7sN2u",
    "title": "Cloud VM probe 3: portability confirmation (trig_01SEa39WGRm8D5SVCvunnRrJ)",
    "repo": "internal-company-tool",
    "branch": "main",
    "mode": "tracked",
    "url": "https://claude.ai/code/cse_01NnRQwJk4AYALHbZXc7sN2u"
  },
  {
    "ts": "2026-08-14T12:01:34-07:00",
    "id": "cse_019KJFzXLCLBrPtSLSaC8rbT",
    "title": "Cloud VM probe 4: final confirmation (trig_01Eb3Nbkcqt5WGarZnE87PF4)",
    "repo": "internal-company-tool",
    "branch": "main",
    "mode": "tracked",
    "url": "https://claude.ai/code/cse_019KJFzXLCLBrPtSLSaC8rbT"
  },
  {
    "ts": "2026-08-14T12:09:36-07:00",
    "id": "cse_019VZAVtT2Djx1X32j2q9WHr",
    "title": "Cloud VM probe 5: final green check (trig_014oZHZ66JBRjR4yqttdaNSV)",
    "repo": "internal-company-tool",
    "branch": "main",
    "mode": "tracked",
    "url": "https://claude.ai/code/cse_019VZAVtT2Djx1X32j2q9WHr"
  },
  {
    "ts": "2026-08-14T13:01:29-07:00",
    "id": "cse_01GiqJCUcHFpUcnekbJPfKdJ",
    "title": "Probe 6: chooser + session logging inside a cloud VM",
    "repo": "internal-company-tool",
    "branch": "main",
    "mode": "tracked",
    "url": "https://claude.ai/code/cse_01GiqJCUcHFpUcnekbJPfKdJ"
  },
  {
    "ts": "2026-08-14T14:55:42-07:00",
    "id": "cse_01FMQZBzegc91HwwxWjSGncc",
    "title": "Probe 7: does I10 hold in cloud sessions? (+ first PR from a cloud session)",
    "repo": "internal-company-tool",
    "branch": "main",
    "mode": "tracked",
    "url": "https://claude.ai/code/cse_01FMQZBzegc91HwwxWjSGncc"
  }
]
```

## Still broken

Empty — nothing found on this platform meets the bar of an actual code
defect. The one failing check (`gate/ssh-origin-fixture-really-is-ssh`) is
an artifact of this specific host's git proxy rewriting SSH-style GitHub
URLs at the environment level, not a suite or product bug; the git-gate
behavior it exists to sanity-check passed on every downstream assertion.
The `cs sessions` discrepancy against the brief's prediction is noted above
for the coordinator's judgment, not logged here as broken, since nothing in
the repo says a `SELF` row is wrong.

## Process notes

Running the four suites produces append-only writes to their own
`RESULTS.md` log files (`atlas-os/tests/{process,heartbeat,worktree}/RESULTS.md`
and `cloud-sessions/tests/RESULTS.md`) as a side effect of the harness's own
logging. Per the handoff's "do not modify any file other than your one
report," those were reverted with `git checkout --` after each run; they are
not part of this commit.
