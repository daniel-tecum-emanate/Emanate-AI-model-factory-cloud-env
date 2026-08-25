# Limits, quotas, and costs

Design inputs, not edge cases. A limit you did not plan for shows up as a session that
mysteriously stopped.

Sourced from Anthropic's documentation as of **2026-08-12** unless marked otherwise —
`DOCS`, in this folder's vocabulary. Where a number has since been **measured inside a real
cloud VM** (2026-08-14) it is marked as such and shown next to the documented one. Where
the two disagree, the measurement wins and the documented value is kept so the drift is
visible.

---

## Cost

> "There is no separate compute charge for the cloud VM."

Cloud sessions draw down **the same subscription rate limits** as everything else on the
account. Running four sessions in parallel consumes limits about four times as fast. On
a Max plan this is the cheapest way to get work off the laptop — cheaper than a VPS
(~€16–24/mo) and far cheaper than GitHub Actions (~$259/mo for 4×6h jobs/day).

If usage credits are enabled, work continues on metered overage past the subscription
limit; without them, further runs are rejected until the window resets.

## The cloud VM

Documented values, with what was **measured** in a real VM on 2026-08-14 beside them
(probe 1).

| | Documented | Measured 2026-08-14 |
|---|---|---|
| OS / arch | Ubuntu 24.04 on **x86_64** — regardless of your Mac's arm64 | Ubuntu 24.04.4 LTS, `x86_64`, kernel 6.18.5 |
| CPU | ~4 vCPU | `nproc` → 4 |
| RAM | ~16 GB | `free -g` → **15 GB total, 14 GB available** — plan against 14, not 16 |
| Disk | ~30 GB | 30 GB available. `df` *reports* a 252 GB filesystem: a per-session allowance on a larger backing volume. **Do not read 252 GB as headroom** |
| User | — | **root.** No `sudo` needed, and no `sudo` available to drop from — anything relying on permission bits to stop a write does not work here |
| Isolation | one fresh VM per session | fresh clone, **HEAD detached** from `refs/heads/main`, not on a branch |

The architecture difference is a real trap: precompiled dependencies (native gem
extensions, prebuilt Python wheels) must be the **x86_64 Linux** build, not the macOS
arm64 one that works on your machine.

The VM will kill tasks that need significantly more memory than it has. Large builds and
memory-hungry test suites are the usual casualties. That is the point at which Remote
Control on your own hardware becomes the right answer instead.

**Pre-installed:** Python 3.x (pip, poetry, uv, black, mypy, pytest, ruff) · Node 20/21/22
(npm, yarn, pnpm, bun, eslint, prettier, chromedriver; 22 on PATH, others under
`/opt/node20` etc.) · Ruby 3.1–3.3 · PHP 8.4 · OpenJDK 21 (Maven, Gradle) · Go · Rust ·
GCC/Clang/cmake/ninja/conan · Docker + compose · PostgreSQL 16 · Redis 7 · git, jq, yq,
ripgrep, tmux, vim.

Measured versions on PATH: `python3` → **3.11.15**, `node` → **v22.22.2**, `git` →
**2.43.0**. Python 3.12 is also on the image but *not* what `python3` resolves to — which is
why a setup script that pip-installs into the system Python needs
`--break-system-packages`, and why targeting 3.12 explicitly is not the same as targeting
`python3`.

Anything else — the .NET SDK, `gh` — needs a [setup script](../playbooks/06-environments.md).
`gh` is genuinely absent; `apt-get install -y gh` took **15 seconds** and needed no `sudo`.

## Session lifetime

Cloud sessions **stop after a period of inactivity** and the VM is reclaimed. The
duration is not published, and is `UNVERIFIED` here.

What that costs you is smaller than it sounds: reopening the session from
[claude.ai/code](https://claude.ai/code) provisions a fresh VM **with the conversation
history restored**. You lose the machine, not the thread. Anything that was only running
— a `docker compose up` stack, a dev server — is gone and must be restarted.

If Claude asks a question and you do not answer for hours, the answer still lands when
you come back, up to that expiry.

## Network

Every session runs in a **cloud environment** that sets one network access level:

| Level | Reaches |
|---|---|
| **None** | nothing outbound (the Anthropic API is still reachable) |
| **Trusted** *(default)* | an allowlist: package registries, GitHub, cloud SDKs, container registries |
| **Custom** | exactly the domains you list, optionally plus the Trusted list |
| **Full** | unrestricted |

Requests to non-allowlisted hosts fail with a `403` whose **plain-text body** names the
host — `request blocked: no rule or allowlist entry allows host "example.com"`. Measured
2026-08-14 in a real VM (probe 1), and re-confirmed on a second VM (probe 2). There is
**no `x-deny-reason` header** on this proxy, and a plain `curl` swallows the body on a
CONNECT tunnel failure, so a blocked host can look like a hang or a generic failure.

Package registries are reached **directly**, outside the proxy: `no_proxy` pre-allows
`pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`, `jsr.io`, `index.crates.io`,
`proxy.golang.org` and `*.anthropic.com`. MCP connector traffic also bypasses the
allowlist, routed via Anthropic's servers, so connectors work without adding their hosts.

**There is no secrets store.** Environment variables and setup scripts are readable by
anyone who uses that environment. Do not put API keys in them.

## Local repository bundles

When the repo has no GitHub remote, `cs start --bundle` (or `CCR_FORCE_BUNDLE=1`) uploads
it instead. Constraints:

- must be a git repo with at least one commit
- must be under **100 MB**; larger falls back to current-branch-only, then to a single
  squashed snapshot, and fails only if that is still too large
- includes full history plus **uncommitted changes to tracked files**
- **excludes untracked files entirely** — `git add` anything the session needs to see
- the session **cannot push back** to a remote unless GitHub auth is separately configured

## Setup scripts

- run once per environment, before Claude starts, then the filesystem is **snapshotted**
  and reused; later sessions skip the script
- keep total runtime under **~5 minutes** or the cache cannot build
- the cache is a filesystem snapshot: installed packages and pulled Docker images carry
  over; **running processes do not**
- re-runs when you change the script or the allowed hosts, and after roughly **7 days**
- resuming an existing session never re-runs it

Measured 2026-08-14 for this repo's script: **~19 seconds** end to end, and **idempotent** —
a second run reinstalls nothing (`apt` no-ops, `pip` drops from 7s to 0.7s). Two
pre-configured third-party PPAs (deadsnakes, `ondrej/php`) return **403 through the proxy
on every `apt-get update`**. They are harmless — the main archive still resolves — but they
make a healthy setup script look like a failing one, which is worth knowing before you
debug the wrong thing.

## Routines

- **minimum interval: one hour.** More frequent cron expressions are rejected
- a **per-account daily run cap** exists on top of normal subscription limits; one-off
  runs are exempt. Current numbers: claude.ai/settings/usage
- runs start a few minutes late by design (consistent per-routine stagger)
- GitHub-event triggers have per-routine and per-account hourly caps during the research
  preview, and **events beyond the limit are silently dropped**
- routines act **as you** — commits and PRs carry your GitHub user
- a green status in the run list means the session started and exited without an
  infrastructure error. **It does not mean the task succeeded.** Open the run and read it

## Remote Control

- one remote session per interactive Claude Code process (use server mode for more)
- the local process must stay running; closing the terminal ends the session
- machine awake but **offline for more than ~10 minutes** → the session times out and the
  process exits
- forwarded dialogs other than permission prompts expire after 5 minutes and take their
  no-action default

## Hard blockers

| Condition | Effect |
|---|---|
| **Organization IP allowlisting** enabled | **Every** Anthropic-hosted cloud session fails with an auth error, because the session calls the API from Anthropic's infrastructure, not your network. Also breaks Code Review and routines. Needs a support exemption |
| **Zero Data Retention** on the org | `/web-setup` and cloud session features unavailable; Remote Control cannot be enabled |
| Auth via API key, Bedrock, Vertex, or Foundry | `--cloud` and `--teleport` refuse. claude.ai subscription auth only |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` set in the shell | Takes precedence over the claude.ai login and disables these features. `cs doctor` checks for this |
| Non-GitHub remote (GitLab, Bitbucket) | Can be sent as a bundle, but the session cannot push results back |

## Security note worth knowing

A cloud session can access **any repository the connecting GitHub account can see** — not
just repositories the Claude GitHub App is installed on. App installation governs PR
webhooks, not session-level access. If that matters, restrict on GitHub itself.

## Sources

- [Claude Code on the web](https://code.claude.com/docs/en/claude-code-on-the-web)
- [Cloud environments](https://code.claude.com/docs/en/cloud-environments)
- [Routines](https://code.claude.com/docs/en/routines)
- [Remote Control](https://code.claude.com/docs/en/remote-control)
