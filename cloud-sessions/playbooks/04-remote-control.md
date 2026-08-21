# 04 — Keep it local, drive it from your phone

> Originally verified against a different repo (`daniel0tgc/internal-company-tool`);
> mechanism-level claims transfer, repo-specific paths corrected 2026-08-18.

Remote Control is the opposite trade from a cloud session: the work stays on **your Mac**
with your files, your MCP servers and your secrets, and the phone or browser becomes a
window into it.

Status on this account: **`claude remote-control` (server mode) connects — `VERIFIED`.**
The `--remote-control` *flag* form that `cs rc` uses is **`UNVERIFIED`**; the evidence below
is for the server subcommand, which is a different entry point. Server mode reached `·✔︎· Ready ·
emanate-tecum-workflow · main`, `Capacity: 0/32`, and printed a spawn URL. The phone half —
actually driving it from the Claude app — is still untested.

Two things the probe turned up:

- **Server mode is a spawn host, not just a steering channel.** It prints
  `https://claude.ai/code?environment=env_…`, and from that URL you can *start new
  sessions* in this project from your phone, up to 32 of them.
- **It registers a `bridge` environment** that then appears in the cloud-environment
  picker next to `Default` (`kind: anthropic_cloud`), named after your machine — here
  `MacBook-Pro:emanate-tecum-workflow:7fcb`. Picking a bridge environment for a routine or
  a cloud session routes the work **back to your Mac**, silently removing the
  survives-a-closed-laptop property. Use `Default` for anything that must outlive the
  machine.

---

## When this is the right answer instead of a cloud session

- the task needs your **local** Docker stack, database, or credentials
- the build needs more than the cloud VM's ~4 vCPU / 16 GB
- you are already mid-way through local work and just want to keep going from the couch
- the repo cannot leave your machine

## When it is the wrong answer

**Anything that has to survive the laptop being shut.** Remote Control is a local process.
Close the terminal, quit VS Code, or shut the Mac and the session ends. Use a
[cloud session](02-dispatch.md) for that — that is the entire distinction.

## Start it

```bash
cs rc                       # interactive session with Remote Control on
cs rc "Migration work"      # …with a name you'll recognise in the session list
```

Three other entry points:

| | |
|---|---|
| `claude remote-control` | **server mode** — stays running, serves multiple sessions, press space for a QR code |
| `/remote-control` (or `/rc`) | turn it on **inside** a session you already have going, carrying the history over |
| VS Code | type `/rc` in the prompt box; a status banner appears above it |

## Connect

Open the printed session URL, scan the QR code with your phone, or find the session by
name at [claude.ai/code](https://claude.ai/code) — Remote Control sessions show a computer
icon with a green dot. In the mobile app, tap **Code**.

You can use both surfaces at once. Messages, subagent progress, and workflow status stay
in sync across the terminal, browser and phone.

## What survives what

| Event | Result |
|---|---|
| Laptop sleeps | session pauses; Claude Code **reconnects automatically** on wake and delivers queued status updates |
| Network drops briefly | same — automatic reconnect |
| Machine awake but offline **>10 min** | session times out and the process exits; start a new one |
| Terminal closed / VS Code quit | session ends. Run it inside `tmux` if you need it to survive an SSH disconnect |
| Mac shut down | session ends. This is what cloud sessions are for |

## Push notifications

With Remote Control active, Claude can push to your phone when a long task finishes or it
needs a decision. In a session, `/config` → enable **Push when Claude decides** and/or
**Push when actions required**. You can also just ask in the prompt: *"notify me when the
tests finish"*.

Notifications are suppressed while you are typing at the connected terminal.

## Turning it on for every session

`/config` → **Enable Remote Control for all sessions**, or set (`VERIFIED` the key exists —
8 occurrences in the 2.1.231 binary; its behaviour is `DOCS`, not exercised here)
`remoteControlAtStartup:
true` in `~/.claude/settings.json`. Each interactive process then registers its own remote
session.

## Worth knowing

- **Subscription auth only.** API keys and `setup-token` tokens are rejected —
  `setup-token` tokens can only make model requests
- The session **transcript is stored on Anthropic servers** while connected, so the
  conversation stays in sync and can survive a reconnect. Execution and filesystem access
  stay on your machine
- `DISABLE_TELEMETRY`, `DO_NOT_TRACK`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` and
  `DISABLE_GROWTHBOOK` each **break** Remote Control by disabling the feature-flag check it
  depends on
- `ANTHROPIC_BASE_URL` pointing anywhere other than `api.anthropic.com` disables it too
- Some commands are terminal-only from mobile (`/plugin`, `/resume`); `/model`, `/effort`
  and `/rename` take their value as an argument instead of opening a picker

## Combining with teleport

Teleport is one-way — after `cs tp`, the phone loses sight of the session. Starting
Remote Control in the teleported local session hands phone access back:

```bash
cs tp last     # cloud → this terminal
/rc            # …and back onto the phone, now running locally
```

## Next

→ [05 — Routines: scheduled cloud runs](05-routines.md)
