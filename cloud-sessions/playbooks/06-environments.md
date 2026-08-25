# 06 — Configuring the cloud environment

A cloud environment is the saved configuration a session's VM boots into: **network
access, environment variables, and a setup script**. The same environments apply
everywhere a cloud session can start — `cs start`, the web, the mobile app, routines, and
Slack.

Most work needs nothing here. Reach for it when a session cannot reach a host it needs, or
keeps reinstalling the same toolchain.

---

## The Default environment

Created for you at onboarding: **Trusted** network access, no variables, no setup script.
Sessions start with just the [pre-installed tools](../reference/limits.md#the-cloud-vm) —
which already covers Python, Node, Ruby, PHP, Java, Go, Rust, C/C++, Docker, Postgres 16
and Redis 7.

## Editing

At [claude.ai/code](https://claude.ai/code) → **Add cloud environment**, or hover an
existing one and click the settings icon. The dialog holds all four fields: name, network
access, environment variables, setup script.

To see which environment your dispatches will use, and what internet they will have:

```bash
cs env
```

To pin one:

```bash
cs env set <environment-id>     # writes remote.defaultEnvironmentId to user settings
cs env clear                    # back to the account default
```

`/remote-env` inside a session does the same thing with a picker:

```
/remote-env
```

It writes `remote.defaultEnvironmentId` to your **user** settings, so it applies in every
project on the machine. Per-dispatch override:

```bash
cs start --env <environment-id> "…"
```

`/remote-env` only sets the default — it cannot create or edit environments.

## Network access

| Level | Reaches |
|---|---|
| **None** | nothing outbound. The Anthropic API is still reachable, so data can still leave the VM |
| **Trusted** *(default)* | package registries, GitHub, cloud SDKs, container registries |
| **Custom** | exactly the domains you list, optionally plus the Trusted list |
| **Full** | unrestricted |

Blocked requests fail with a `403` whose plain-text body reads `request blocked: no rule
or allowlist entry allows host "..."`. Worth remembering, because otherwise it looks like
the remote service is down — and a plain `curl` **hides** that body on a CONNECT tunnel
failure, so the block can present as a hang. Measured directly against a cloud VM on
2026-08-14; the `x-deny-reason` header this folder previously claimed does not exist.

**"Trusted" is not "has internet."** It is an allowlist: package registries, GitHub, cloud
SDKs. A session that needs to call your own API, scrape a page, or reach a SaaS endpoint
will be refused by default. If you want a session that can genuinely reach the internet,
you need **Custom** (your domains, plus the default list) or **Full**. There is no CLI or
API for creating environments — it is web-only, and `cs env` prints the exact steps.

**Custom** takes one domain per line; a leading `*.` matches all subdomains. Tick *"Also
include default list of common package managers"* unless you genuinely want only your
list.

Two things bypass the allowlist: **MCP connector traffic** (routed via Anthropic's
servers) and **GitHub traffic** (a dedicated proxy, scoped to repositories attached to the
session — a setup script fetching release assets from an unattached repo gets a `403`).

## Environment variables

`.env` format in the dialog.

**There is no secrets store.** Values are readable by anyone who uses the environment, and
shared environments push them into every member's sessions. Do not put API keys here.

Useful non-secret ones:

| Variable | Effect |
|---|---|
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70` | compact at 70% of the window instead of waiting until nearly full — useful for long autonomous runs |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | change the auto-compact window itself |
| `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` | enable agent teams (off by default in cloud sessions) |

## Setup scripts

A Bash script that runs when a **new** session starts, before Claude Code launches. Use it
for toolchains that are not pre-installed:

```bash
apt update && apt install -y gh          # gh is NOT pre-installed
curl -fsSL https://example.com/tool | sh &
npm ci --prefer-offline &
wait
```

Three constraints:

1. **Finish in ~5 minutes**, or the cache cannot build. Run independent installs in
   parallel with `&` … `wait`; push a single huge download into a background
   `SessionStart` hook instead.
2. **Installs need network access.** With **None**, they fail.
3. It runs as the VM's provisioning step — not as project setup. See the split below.

### Caching — the part that makes this worth doing

After the script completes, Anthropic **snapshots the filesystem** and reuses it for later
sessions, which skip the script entirely. Installed packages, pulled Docker images and
written files all carry over.

**Running processes do not.** A database the script started, a `docker compose up` stack —
gone. Start those per session, via a `SessionStart` hook or by asking Claude.

The script re-runs when you change it, when you change the allowed hosts, and after
roughly **7 days**. Resuming an existing session never re-runs it.

If your images are slow to pull, put `docker compose pull` in the setup script — the cache
keeps the images, so every later session starts with them on disk.

### Setup script vs. `SessionStart` hook

| | Setup script | `SessionStart` hook |
|---|---|---|
| Where it lives | environment config | committed to the repo |
| Runs | once per environment cache | every session, cloud **and** local |
| For | toolchains, CLI tools, system packages | `npm install`, migrations, starting services |

The setup script runs first, and only when no cached environment exists.

## Self-hosted environments

`--environment ccpool_…` routes sessions onto your organization's own runners, inside your
network. **Team and Enterprise only** — not available on this Max account. Noted so nobody
spends an afternoon on it.

## Sources

- [Cloud environments](https://code.claude.com/docs/en/cloud-environments)
- [Hooks](https://code.claude.com/docs/en/hooks)

## Next

→ [07 — Making cloud the default](07-making-cloud-the-default.md)
