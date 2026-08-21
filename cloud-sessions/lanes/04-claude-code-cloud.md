# Lane 04 — Claude Code cloud sessions (`--cloud` / `--teleport` / Routines)

**Solves:** F4 — Anthropic-managed cloud infrastructure, explicitly designed, in Anthropic's own
words, to "keep working when your laptop is closed."

**Status: AVAILABLE on the installed CLI, NOT YET RUN.** The feature strings are compiled into
`claude` 2.1.206. No cloud session has been dispatched, so nothing here is end-to-end verified.

**This is the highest-value untested lane** and probably the best fit for the original request:
first-party, **no separate compute charge for the cloud VM**, and `--teleport` pulls the session
back into your terminal — which is closer to the attach/steer loop Daniel described than
[lane 03](03-cursor-cloud.md) manages.

---

## The mistake that nearly buried this lane

A research agent reported this lane existed. It was **declared non-existent** because
`claude --help | grep -iE 'cloud|teleport'` returned nothing.

That was wrong, and the research doc had **already said so** — §4a states plainly that `--cloud`
and `--teleport` "**do not appear in `claude --help`** but are accepted." The `--help` grep was
never capable of disproving the claim.

A second probe was also inconclusive and initially misread: `claude --cloud --version` exits 0,
but so does `claude --definitelynotaflag --version`, because `--version` short-circuits before
option validation. **A probe that a bogus control also passes proves nothing.**

What settled it — the installed Mach-O binary at
`/opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe`:

```
$ strings claude.exe | grep -c teleport          # 191
$ strings claude.exe | grep -c web-setup         #   9
$ strings claude.exe | grep -c "cloud session"   #  96
$ strings claude.exe | grep -c CCR_FORCE_BUNDLE  #   5
$ strings claude.exe | grep -c routine           # 209
```

`CCR_FORCE_BUNDLE` is a highly specific env var named in Anthropic's docs. Its presence, with
`teleport` and `web-setup`, confirms the feature set is compiled in and merely hidden from
`--help`. Corrected in queue item **V-102**; **V-101 contains the wrong version of this claim.**

**Lesson, now a folder ground rule:** absence from `--help` is not absence of the feature, and a
probe that the control also passes is not a probe.

## How to use — from Anthropic's docs, UNVERIFIED locally

```bash
claude --cloud "Execute the migration plan in docs/migration-plan.md"   # dispatch
# /tasks           inside the CLI — lists background sessions, press `t` to teleport in
claude --teleport            # interactive picker
claude --teleport <id>       # specific session
# /teleport or /tp  from inside a session
```

One-time setup: run **`/web-setup`** in the CLI to sync your `gh` token and create a Default cloud
environment. Sessions are also visible at **claude.ai/code** and monitorable from the Claude
mobile app — which is the phone-access requirement from [`PROBLEM.md`](../PROBLEM.md).

## Documented behaviour — VERIFIED from docs, not exercised here

- Sessions run on Anthropic-managed infrastructure and **"persist even if you close your browser."**
- Clones your current directory's **GitHub remote at your current branch** — the VM clones from
  GitHub, so **push local commits first** (same trap as lane 03).
- If the repo has **no** GitHub remote, Claude Code bundles and uploads the local repo (full
  history plus uncommitted changes to tracked files); force with `CCR_FORCE_BUNDLE=1`.
- One repository at a time. `--remote` is a deprecated alias for `--cloud`.
- **Teleport requirements:** clean git working tree, same repository (not a fork), branch pushed
  to the remote, and **claude.ai subscription auth — not an API key.**
- **Teleport is one-way** (cloud → terminal). After teleporting, the terminal copy is independent;
  new work does **not** flow back to the cloud session. The Desktop app's "Continue in" menu can
  push a local session *to* the web.
- Status: **research preview**, for Pro / Max / Team (and Enterprise with the right seats).

### Cost — VERIFIED from docs

> "There is no separate compute charge for the cloud VM."

It shares your account's rate limits with all other Claude usage; parallel tasks consume limits
proportionately. **This is the cheapest cloud lane available** — compare lane 03 (API pricing per
model) and lane 07 (€16–50/mo).

### Idle expiry — exists, duration UNVERIFIED

> "Cloud sessions stop after a period of inactivity and the session's VM is reclaimed."

No numeric value is published. If Claude asks a question and the session sits idle, you can still
answer later "up to environment expiry."

### Blocking gotcha — VERIFIED

If the org has **IP allowlisting** enabled, **every** cloud session fails with an authentication
error (same for Code Review and Routines), because cloud sessions call the API from Anthropic
infrastructure. Requires a support exemption.

Also, a security note worth knowing: a cloud session "can access **any repository the connecting
GitHub account can see**, not just the repositories the Claude GitHub App is installed on." App
installation governs PR webhooks, **not** session-level access. Restrict on GitHub if that matters.

## Routines — scheduled/triggered cloud runs

A routine is a saved config (prompt + repos + connectors) that "execute[s] on Anthropic-managed
cloud infrastructure, so they keep working when your laptop is closed." Research preview.

Create with **`/schedule`** (alias `/routines`) in any session, or at claude.ai/code. Triggers:

1. **Schedule** — hourly/daily/weekdays/weekly presets or a one-off; custom cron via
   `/schedule update`. **Minimum interval one hour** — anything more frequent is rejected.
2. **API** — each routine gets a dedicated endpoint plus a **per-routine bearer token** (shown
   once, not retrievable). `POST` starts a session and returns a session URL. Beta header
   `experimental-cc-routine-2026-04-01`, namespace `/v1/claude_code/...`. Authenticate with the
   **per-routine token, not** a Claude API key.
3. **GitHub events** — web UI only; per-routine and per-account hourly caps during preview, and
   **events beyond the limit are silently dropped.**

**Caps — VERIFIED:** routines draw down subscription usage like normal sessions, **plus** a daily
run cap per account: **Pro 5/day, Max 15/day, Team/Enterprise 25/day.** One-off runs are exempt
from the cap. Hitting it returns `429` with `Retry-After`. Routines are per-individual-account and
act **as you** — commits and PRs carry your GitHub user.

**Relevance to this repo:** `OPERATIONS.md` tier routing and the scheduled jobs in `PROCESSES.md`
currently run locally through `scripts/tier2_run.sh`. Routines are a plausible substrate for
moving nightly work off this machine entirely — **not evaluated**, and it would need a
`PROCESSES.md` registration and a tier decision first.

## What to do next

1. Run one real dispatch from `platform-alpha`: `/web-setup`, push a branch, `claude --cloud "…"`.
2. Confirm it appears in `/tasks` and at claude.ai/code, and check it from the phone app.
3. Test `claude --teleport <id>` and confirm the clean-tree / pushed-branch / subscription-auth
   requirements match reality.
4. Measure idle expiry empirically — it is unpublished and matters for long runs.
5. Only then decide whether this replaces lane 03 as the default cloud lane.

## Version caveat

Installed **2.1.206**; latest published **2.1.220** (`npm view @anthropic-ai/claude-code version`).
The features are present in 2.1.206, so an upgrade is **not** required to try this lane.

**Do not upgrade casually:** `scripts/tier2_run.sh` and the nightly scheduled jobs run through
this CLI, so a version bump can break Tier-2 enforcement. Daniel's call — queued.
