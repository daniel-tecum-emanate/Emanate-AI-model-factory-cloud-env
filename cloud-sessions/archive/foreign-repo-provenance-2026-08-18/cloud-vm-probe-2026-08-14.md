# Cloud VM probe — 2026-08-14

> **Snapshot, not current state.** Probe 1 of seven. The measurements below are evidence and
> stay as written; the conclusions have moved on. **The suite it found red (57 of 232) is
> green now** — that took three more probes and four fixes. Its two disproven claims still
> hold. Current state: [`verified-facts.md`](verified-facts.md) · all seven probes:
> [`README.md`](README.md#the-probe-reports--primary-evidence-not-current-state).

Ran as a scheduled verification probe. Every command below was actually executed in
this session; output is quoted verbatim (trimmed only where noted). Where a
written-down claim turned out wrong, it's marked DISPROVEN, not softened.

## VM specs

| Field | Value |
|---|---|
| `uname -a` | `Linux vm 6.18.5-fc-v20 #1 SMP PREEMPT_DYNAMIC @0 x86_64 x86_64 x86_64 GNU/Linux` |
| `nproc` | `4` |
| `free -g` | total `15G`, used `0G`, free `14G`, buff/cache `0G`, available `14G`; swap `0` |
| `df -h /` | `/dev/vda 252G, 7.3G used, 30G avail, 20% use` — see "what surprised me" |
| `/etc/os-release` | `Ubuntu 24.04.4 LTS` (noble) |
| `python3 --version` | `Python 3.11.15` |
| `node --version` | `v22.22.2` |
| `git --version` | `git version 2.43.0` |
| `pwd` | `/home/user/internal-company-tool` |
| `whoami` | `root` |

## Claims table

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| a | `gh` is NOT pre-installed | **CONFIRMED** | `command -v gh` → exit code 1, no output |
| b | System Python is PEP 668 externally-managed, `pip install` needs `--break-system-packages` | **PARTIALLY DISPROVEN** — the marker file exists, but the claim's practical conclusion is false for the `python3`/`pip` pair actually on PATH. See detail below. |
| c | Repo arrived by clone, not bundle | **CONFIRMED** | `git remote -v` shows `origin https://github.com/daniel0tgc/internal-company-tool`; `git log --oneline -3` shows real history (`c6b7093`, `a807fa6`, `74bc197`); `git status` reports `nothing to commit, working tree clean`. One caveat: HEAD is **detached** from `refs/heads/main`, not on a branch — see below. |
| d | `CLAUDE.md` exists at repo root and imports `AGENT.md` | **CONFIRMED** | `CLAUDE.md` is 348 bytes, sole content `@AGENT.md` plus a comment explaining why the import exists |
| e | Repo size / file count | measured, no prior claim to check | `du -sh .` → `130M`; `git ls-files \| wc -l` → `2155` |
| f | `twenty/` is an empty gitlink (uninitialized submodule) | **CONFIRMED** | `ls twenty/` → empty output; `git submodule status` → `-02a187d065354872c0f318b0723a1e7d8762ae00 twenty` (leading `-` = not initialized) |

### Detail on claim (b) — the interesting one

- `/usr/lib/python3.12/EXTERNALLY-MANAGED` exists.
- But `python3` on PATH resolves to `/usr/local/bin/python3` → **Python 3.11.15**, and
  `pip` on PATH resolves to `/usr/bin/pip` → **pip 24.0 from
  /usr/lib/python3/dist-packages/pip (python 3.11)**.
- `/usr/lib/python3.11/EXTERNALLY-MANAGED` does **not exist** (`ls`: No such file or
  directory).
- So the marker that would trigger PEP 668 enforcement sits under 3.12, but the
  python/pip pair actually invoked by `python3`/`pip` is 3.10/3.11's toolchain, which
  is not externally-managed.
- Ran the disproof directly: `pip install networkx` (no flag, as root) —

  ```
  Collecting networkx
    Downloading networkx-3.6.1-py3-none-any.whl.metadata (6.8 kB)
  Downloading networkx-3.6.1-py3-none-any.whl (2.1 MB)
  Installing collected packages: networkx
  Successfully installed networkx-3.6.1
  WARNING: Running pip as the 'root' user can result in broken permissions and
  conflicting behaviour with the system package manager. It is recommended to use a
  virtual environment instead: https://pip.pypa.io/warnings/venv
  ```

  Exit code 0. No `externally-managed-environment` error. The only complaint is the
  standard root-user warning.
- **Bottom line: on this VM, `--break-system-packages` is not required for `pip
  install` to succeed.** The flag is harmless to keep (it's accepted with no ill
  effect and protects against a future environment where 3.12 is the default), but
  the written claim that it's *needed* here is disproven. Don't drop the flag from
  the setup script on the strength of this one probe — a different VM image could
  wire `python3` to 3.12 — but stop asserting it's required as observed fact.

### Note on claim (c) — detached HEAD

`git status` is clean and remote-tracked correctly, but `HEAD` is **detached from
`refs/heads/main`**, not sitting on a local `main` branch. This didn't block anything
in this probe (branch creation for the report worked fine off detached HEAD), but any
setup-script step that assumes `git rev-parse --abbrev-ref HEAD` returns `main` will
get `HEAD` instead. Worth confirming whether this is intentional for cloud sessions or
an artifact of how this particular container was provisioned.

## Setup-script results (run exactly as specified)

Command 1 — `apt-get update -qq && apt-get install -y -qq gh`
- **Result: succeeded**, but with warnings, not cleanly.
- `apt-get update` printed two failures before continuing:
  ```
  W: Failed to fetch https://ppa.launchpadcontent.net/deadsnakes/ppa/ubuntu/dists/noble/InRelease  Invalid response from proxy: HTTP/1.1 403 Forbidden ...
  W: Failed to fetch https://ppa.launchpadcontent.net/ondrej/php/ubuntu/dists/noble/InRelease  Invalid response from proxy: HTTP/1.1 403 Forbidden ...
  W: Some index files failed to download. They have been ignored, or old ones used instead.
  ```
  Both are pre-configured third-party PPAs (deadsnakes, ondrej/php) blocked by the
  outbound proxy — not something this probe added. `apt-get` continued anyway using
  cached/main-archive indices, and `gh` installed fine from the main Ubuntu archive.
- Installed `gh` 2.45.0-1ubuntu0.3.
- No `sudo` needed — already running as `root`.
- Timing: `real 0m15.139s` (`user 0m4.455s`, `sys 0m1.581s`).

Command 2 — `pip install --break-system-packages --quiet 'networkx>=3.0' python-pptx`
- **Result: succeeded.**
- Only output was the same root-user warning as above.
- Timing: `real 0m3.618s` (`user 0m2.375s`, `sys 0m0.516s`).

Command 3 — `gh --version`
- **Result: succeeded.** `gh version 2.45.0 (2025-07-18 Ubuntu 2.45.0-1ubuntu0.3)`
- Timing: `real 0m0.071s`.

Command 4 — `python3 -c 'import networkx, pptx; print(networkx.__version__)'`
- **Result: succeeded.** Printed `3.6.1`. Exit code 0.

**Total wall-clock for the four steps: ~19 seconds** (15.1s + 3.6s + 0.07s +
negligible). Nothing failed; nothing silently fixed — the two PPA 403s are the only
blemish and they did not stop the install.

## Repo-capability results

- `python3 atlas-os/bin/heartbeat.py validate` — **runs, exit 0**, but not clean:
  ```
  note:  liveness oracle: telemetry ledger OK — 2 ledger file(s), 28 session(s), 0 unparseable line(s)
  WARN   heartbeat/STATE.md row 3 (lane-hb-stress): unproven ACTIVE row: no ledger entry; ...
  WARN   heartbeat/STATE.md row 4 (lane-worktree): unproven ACTIVE row: no ledger entry; ...
  WARN   heartbeat/STATE.md row 5 (lane-cs-tests): unproven ACTIVE row: no ledger entry; ...
  WARN   heartbeat/STATE.md row 6 (lane-integration): unproven ACTIVE row: no ledger entry; ...
  heartbeat/STATE.md: 0 error(s), 4 warning(s)
  ```
  4 pre-existing warnings about stale ACTIVE rows on `heartbeat/STATE.md`, unrelated to
  this probe — they predate this session (leases from ~1500 minutes before the probe
  ran). 0 errors.

- `bash atlas-os/bin/verify_system.sh` — **runs, offline, $0 as advertised.**
  **21 passed, 1 failed, 1 skipped.** The one failure:
  ```
  FAIL  append-only:atlas-os/telemetry/runs
        file SHRANK vs HEAD (71 -> 0 bytes); history is append-only
  ```
  This is a real, pre-existing invariant violation in the repo state as checked out
  here (`atlas-os/telemetry/runs` shrank relative to `HEAD`), not something this probe
  caused. Exit code 1 ("not green").

- `bash cloud-sessions/tests/run.sh` — **runs**, but **57 failed / 175 passed / 0
  skipped**, exit code 1. This is a materially large failure count, not noise:
  - The suite's own self-guard `harness/stub-actually-invoked` fails
    (`expected output to contain: <arg>--cloud</arg> <arg>harness probe</arg>`),
    meaning the harness's own stub-invocation proof doesn't hold in this environment.
  - Failures span nearly every numbered defect group (D1, D2, D3, D5, D8, D9), the
    entire `start/*` and `gate/*` groups, both `handoff/brief-*` behavior checks, and
    `args/*` double-dash handling.
  - Also printed a warning before the suite ran: `mktemp: too few X's in template
    'cs-tests'` — a portability issue with the script's own `mktemp` invocation on
    this VM's `mktemp` (GNU coreutils on Ubuntu 24.04 requires the template's X's
    literally at the end, and apparently this system's mktemp is stricter than
    whatever this suite was authored against).
  - **This disproves any assumption that `cloud-sessions/tests/run.sh` is currently
    green on a stock cloud VM.** Whether that's an environment mismatch (VM
    differs from wherever this suite was last verified) or a real regression in the
    `cs` binary/tests, I can't tell from this probe alone — but "run it and see 175/232
    passing" is the honest current state, not "it passes."

- `market-analysis/frontier-ai-atlas/infrastructure/detect_ecosystem_cycles.py --help`
  — **exists, runs, exit 0.** Full help text printed cleanly, confirming the `networkx`
  install from the setup script satisfies its import (no `ModuleNotFoundError`):
  ```
  usage: detect_ecosystem_cycles.py [-h] [--input INPUT] [--top TOP]
                                    [--min-length MIN_LENGTH]
                                    [--max-length MAX_LENGTH] [--tag-hubs]
  ```

## Network results

Outbound proxy is `http://127.0.0.1:46101`. `no_proxy` env var (observed via curl -v)
already whitelists a specific set of hosts for **direct** (non-proxied) connection,
including `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`, `jsr.io`,
`index.crates.io`, `proxy.golang.org`, and `*.anthropic.com` — i.e. package registries
are pre-allowed outside the proxy's own filtering logic.

| Host | Result | Notes |
|---|---|---|
| `https://pypi.org` | **200** | Direct connection, bypasses proxy per `no_proxy` |
| `https://example.com` | **403** (CONNECT tunnel failed) | Proxied and blocked |
| `https://api.github.com` | **200** | Proxied and allowed |

- No response carried an `x-deny-reason` header — that header does **not** exist on
  this proxy. The `example.com` block instead returns a **plain-text body** on the
  `403`, retrieved by opening a raw socket to the proxy and issuing `CONNECT` by hand
  (curl doesn't surface tunnel-failure response bodies):
  ```
  HTTP/1.1 403 Forbidden
  Content-Type: text/plain; charset=utf-8
  X-Content-Type-Options: nosniff
  Content-Length: 69

  request blocked: no rule or allowlist entry allows host "example.com"
  ```
- So: the default Trusted network level here is an **explicit allowlist**, not a
  denylist — package registries, `api.github.com`, and `anthropic.com` domains are
  allowed by name; an arbitrary host like `example.com` is rejected outright with a
  plain-text reason in the body, not a header.

## What surprised me

1. **`--break-system-packages` isn't actually required on this VM** — the
   EXTERNALLY-MANAGED marker sits on Python 3.12, but `python3`/`pip` on PATH resolve
   to Python 3.11's toolchain, which has no such marker. A written claim treated as
   settled fact was actually only half-true. Worth re-checking on any VM before
   relying on it, since the answer depends on which Python `python3` happens to
   resolve to, which is an implementation detail of the image, not something in this
   repo.
2. **The `cloud-sessions/tests/run.sh` suite is nowhere near green here** — 57 of 232
   checks fail, including the suite's own self-guard for "was the stub actually
   invoked." That's a much bigger gap than "cloud-sessions is one flaky test away from
   passing"; something about this VM (bash/mktemp version, missing binary, or a
   genuine regression) breaks a large fraction of the harness's assumptions. This
   deserves investigation before anyone treats `cs` as verified-working here.
3. **`df -h /` is misleading** — it reports a 252G filesystem with only 7.3G used, yet
   only 30G is "available." That's consistent with this being a fixed per-session
   writable-disk allowance layered on a much larger backing volume, not a real
   252G-capacity disk. Don't read the 252G/20%-use numbers as headroom.
4. **The outbound network policy is an allowlist with a plain-text-body 403, not a
   header-flagged one.** There is no `x-deny-reason` header on this proxy at all — the
   deny reason lives in the response body, and curl only shows it if you avoid
   treating a CONNECT failure as fatal (a plain `curl -sS` swallows the body; a raw
   socket CONNECT does not).
5. **`git status` is clean but `HEAD` is detached**, not on `main`. Anything in the
   proposed setup or in `cs` that assumes an active local branch should account for
   detached HEAD as the normal starting state for a cloud session checkout, not an
   edge case.
