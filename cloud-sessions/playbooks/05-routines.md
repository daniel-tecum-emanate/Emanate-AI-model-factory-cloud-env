# 05 — Routines: scheduled and triggered cloud runs

A routine is a saved prompt + repositories + environment + connectors that fires on a
schedule, an HTTP call, or a GitHub event, and runs as a full cloud session. Same
infrastructure as [`cs start`](02-dispatch.md) — so it keeps working when the laptop is
closed.

**This is the only way to get cloud work started without a human at a terminal.**
`claude --cloud` requires a TTY; routines do not.

Status: **VERIFIED 2026-08-12** — created via the API and fired on demand
(`trig_01Bxk1rjkAZMB2P1pVS2L9st` → `cse_01U63391Ug1GFZTvEgJUUNKe`). Research preview.

**Routines need GitHub connected to your Claude account**, not just `gh` authenticated
locally. Without it, creation fails with `403 You don't have access to a repository this
routine uses` — while `cs start` keeps working, because it can fall back to bundling and a
routine cannot. Run `/web-setup` first.

---

## Create one

```
/schedule daily PR review at 9am
/schedule clean up the feature flag in one week
/schedule
```

Claude asks follow-up questions about cadence, repositories and prompt, then saves it to
your account. Alias: `/routines`. Or use the web form at
[claude.ai/code/routines](https://claude.ai/code/routines), which is the only place to add
an **API trigger**.

Manage from the CLI:

```
/schedule list
/schedule update      # also the only way to set a custom cron expression
/schedule run
/schedule why did my nightly review do nothing this morning?
```

That last form reads the run log and explains what happened — tool errors, permission
denials, final result. Needs 2.1.227+.

## The three triggers

### Schedule

Presets: hourly, daily, weekdays, weekly; or a one-off at a specific time. Times are
entered in your local zone and converted, so it fires at that wall-clock time wherever
the infrastructure is.

**Minimum interval is one hour** — anything more frequent is rejected. Runs start a few
minutes late by design (a consistent per-routine stagger).

One-off runs auto-disable after firing and **do not count against the daily cap**.

### API

Each routine gets a dedicated endpoint and a **per-routine bearer token, shown once**.

```bash
curl -X POST https://api.anthropic.com/v1/claude_code/routines/trig_…/fire \
  -H "Authorization: Bearer sk-ant-oat01-…" \
  -H "anthropic-beta: experimental-cc-routine-2026-04-01" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{"text": "Sentry alert SEN-4521 fired in prod."}'
```

Returns the new session ID and URL.

**The `text` field arrives wrapped in a `<routine-fire-payload>` block labelled untrusted,
and the routine ignores it unless the saved prompt explicitly says to use it.** So write
the prompt to reference it — *"Investigate the alert described in the routine-fire-payload
block"* — or your payload is inert. This is deliberate: anyone holding the token can send
`text`, so it arrives as data, never as instructions.

### GitHub events

Pull request and release events, with filters on author, title, body, base/head branch,
labels, draft and merged state. Operators: equals, contains, starts with, is one of, is
not one of, matches regex.

`matches regex` tests the **entire** value — use `.*hotfix.*`, not `hotfix`. For plain
substring matching use `contains`.

Requires the **Claude GitHub App** installed on the repository. `/web-setup` grants
cloning access but does **not** install the App or enable webhooks — a common and
confusing failure.

## Writing the prompt

Routines run fully autonomously: no permission mode, no approval prompts. The prompt must
be self-contained and explicit about what success looks like. Everything a routine can
reach comes from three places you choose — the repositories, the environment's network
access and variables, and the connectors. Scope each to what it actually needs.

## Connectors

All your claude.ai connectors are included by default, and Claude may use **any** tool
from an included connector, **including writes**, without asking. Remove the ones the
routine does not need.

MCP servers added locally with `claude mcp add` are not connectors — they live on your
machine. To use one, add it at claude.ai/customize/connectors or commit a `.mcp.json` to
the repo.

## Limits

- a **per-account daily run cap** on top of normal subscription usage; one-off runs exempt
- GitHub events have hourly caps in preview and **events beyond the limit are silently
  dropped**
- routines act **as you**: commits and PRs carry your GitHub user, connector actions use
  your linked accounts
- routines are personal, not shared with teammates
- Claude pushes to `claude/`-prefixed branches; pushes elsewhere are rejected if the branch
  is protected, has someone else's open PR, or carries someone else's commits
- routine sessions carry the **`cse_`** ID prefix rather than `session_`; both work with
  `cs send` and `--teleport`
- a **`Claude_Code_Remote` MCP connection is attached automatically** to routines created
  through the API, even when you requested none — check the routine if you are trying to
  keep its tool surface minimal

## "Run now" does not consume a one-off schedule — footgun

Firing a routine on demand (`Run now`, or `action: "run"` via the API) executes it
**without clearing `run_once_at`**. The routine stays armed and fires again at its
scheduled time.

Observed 2026-08-12: a push probe fired manually at 02:18 UTC still reported
`next_run_at: 2026-08-13T09:00:00Z` afterwards, and would have re-created the branch
overnight. Both probes had to be explicitly disabled:

```
update trig_… {"enabled": false}   → HTTP 200, enabled:false
```

`next_run_at` still shows a timestamp when disabled — `enabled` is the field that decides,
not `next_run_at`. **After firing a one-off manually, disable it.**

You cannot delete routines from the CLI or the API; deletion is web-only at
[claude.ai/code/routines](https://claude.ai/code/routines).

## Reading the results

**A green status means the session started and exited without an infrastructure error. It
does not mean the task succeeded.** Blocked network requests, missing connector tools and
task-level failures all surface in the transcript and nowhere else. Open the run.

## Sensible first routines

| Routine | Trigger |
|---|---|
| Summarise yesterday's merged PRs, post to Slack | weekdays 9am |
| Review every non-draft PR against the team checklist | GitHub `pull_request.opened`, filter `is draft = false` |
| Investigate the alert in the fire payload, open a draft PR with a fix | API, called from your monitoring tool |
| Scan merged PRs for docs drift, open update PRs | weekly |

## Next

→ [06 — Configuring the cloud environment](06-environments.md)
