# Lane 03 — Cursor cloud agents

**Solves:** F4 — work continues with the laptop shut down, off the network, or out of battery.
The agent executes on a Cursor-managed VM, not here.

**Status: VERIFIED end-to-end, 2026-07-29.** Dispatch, status polling from a *separate* process,
and follow-up steering were all demonstrated against `Emanate-AI/platform-alpha`.

---

## How to use

```bash
./scripts/longrun.sh cloud <prompt-file|-> [--model M] [--branch B]
```

Deliberately **does not wait** on the run — waiting would tie observability to this terminal
staying open, which is the exact thing the lane exists to avoid.

### Session management — BUILT and proven 2026-07-29

```bash
./scripts/longrun.sh cloud ls                  # dispatched agents + live status
./scripts/longrun.sh cloud status <id>
./scripts/longrun.sh cloud logs <id> [--follow]
./scripts/longrun.sh cloud send <id> "<message>"
./scripts/longrun.sh cloud stop <id>
```

All five were exercised against a real agent (`bc-3689251a-9eb2-4390-a6b7-a100897ff427`), each
**from a different process than the one that dispatched it**:

| Command | What proved it |
|---|---|
| `ls` | Listed both dispatched agents, showing `RUNNING` mid-turn |
| `status` | Reported `RUNNING`, then `FINISHED`, matching real state |
| `logs` | Full transcript; `--follow` polled and exited on terminal status |
| `send` | Accepted **and executed** — the agent replied `LONGRUN-SEND-OK feat/workflow-system-v2` |
| `stop` | Truncated a run mid-task; the transcript ends without a final answer |

Implementation: `scripts/longrun/cloud_session.py` and `scripts/longrun/cursor_agents.py`.

### Conventions to preserve when extending

- **Reads go through a 45s TTL cache** (shorter than `cursor_repos.py`'s 900s, since agent status
  changes fast). A 429 falls back to the last cached answer **labelled with its age**.
- **Exit code `3` means UNKNOWN**, deliberately distinct from "not running". Same principle as
  [D-5](../decisions/DECISIONS.md): a rate limit must never collapse into a meaningful value.
- **`logs --follow` will not poll faster than 15s** and doubles its interval on a 429. Enforced by
  the `follow poll floor >=15s` check.
- **Raw `urllib`, not `cursor_sdk`** — deliberate. The SDK routes every call through a locally
  spawned `cursor-agent` bridge, costing seconds per call and collapsing a 429 into an
  indistinguishable error string, which destroys the very distinction the module exists to keep.
- `stop` uses `POST /v0/agents/{id}/stop`, **not `DELETE`**, so the run pauses without destroying
  the transcript.

### Known API limitation — a stopped agent reports `FINISHED`

v0 has no `STOPPED` or `CANCELLED` status. **An operator stop is indistinguishable from natural
completion by status alone** — only the transcript reveals the difference (it ends mid-task with no
final answer). Anything that reasons about *why* a run ended must read the transcript, not the
status field. This is the distinction `factory_cursor_dispatch.py` carries D7 for.

## What is proven

| Capability | How |
|---|---|
| Dispatch a cloud agent | `/tmp/cursor-cloud-spike.py` against `platform-alpha` |
| Poll status from a **separate process** | `/tmp/cursor-cloud-status.py` — proves observability does not depend on the dispatching shell |
| Steer with a follow-up message | `/tmp/cursor-cloud-resume.py` — `POST /v0/agents/{id}/followup` |

The separate-process result is the load-bearing one: it means a dispatched run is recoverable
from any process, which is what makes this a real lane rather than a fancy way to block a
terminal.

**Both repos are now granted** (this was not true earlier in the session):

```
$ python3 scripts/longrun/cursor_repos.py
ok 2
daniel-tecum-emanate/emanate-tecum-workflow
Emanate-AI/platform-alpha
```

So `longrun cloud` can dispatch from **this** repo as well as `platform-alpha`.

## The two failure modes that read misleadingly

`scripts/longrun/longrun_cloud.py` pre-flights both, because the SDK's own errors point at the
wrong thing:

1. **No connected repositories.** The SDK reports a startup error about verifying the *branch*,
   which reads like a git problem. It is an account-level GitHub grant that was never given
   (`GET /v0/repositories` → `{"repositories":[]}`). Same blocker recorded against
   `factory-cursor-bridge-v1`'s C1 spike.
2. **Unpushed branch.** The cloud VM clones from **origin**, so it only sees pushed commits. A
   local-only branch fails at startup with that same branch-verification error.

## The rate limit — a design constraint, not an edge case

Cursor's docs, verbatim:

> **This endpoint has very strict rate limits.** Limit requests to **1 / user / minute**, and
> **30 / user / hour.** This request can take tens of seconds to respond for users with access to
> many repositories. Make sure to handle this information not being available gracefully.

**This produced a real defect.** `longrun doctor` called the endpoint on every invocation, and the
dispatch refusal keys off an *empty list* — so a 429 could be rendered as "no repositories
connected", the **opposite** diagnosis, sending you to the dashboard to fix nothing.

Fixed by `scripts/longrun/cursor_repos.py`: 900s TTL cache in `.longrun/cursor-repos.json`,
shared by `doctor` and `cloud`, and **429 → UNKNOWN, never `[]`**. Measured cold 1.99s, warm
0.14s. Any new subcommand must read through it.

## `{"repositories":[]}` does not necessarily mean "permissions" — VERIFIED

Two confirmed Cursor-side defects produce that exact symptom:

- `GET /v0/repositories` returned **401 with a valid API key** while other endpoints worked;
  confirmed by Cursor staff as a backend regression, later fixed.
- The repo picker showed **"No repositories available"** even with the GitHub App installed
  org-wide, for a GitHub org **owner**. Resolved only by a full disconnect and forced re-sync.

**Debug order:** confirm a paid plan → `GET /v0/me` to see which account the key belongs to →
check the picker at cursor.com/agents (Cursor recommends the dashboard as the workaround) → only
then re-check `/v0/repositories`, at most once per minute → if the UI is also empty, full
Disconnect → reconnect with **Selected repositories**.

Grant path: **https://cursor.com/dashboard/integrations** → Connect/Manage next to GitHub →
All or Selected repositories. Needs Cursor admin **and** GitHub org admin for org-owned repos.
Cloud agents need **read-write**. If the org uses an IP allow list, enable "Allow access by
GitHub Apps". Legacy Privacy Mode blocks repo access — check it is off.

## Unresolved: runtime bound — UNVERIFIED

**No max-runtime or idle-expiry value appears anywhere in Cursor's official docs**, and the
documented API has no `maxRuntime` field. The widely-repeated "24 hours by default, tunable with
`--max-runtime`" traces **only** to third-party skill files wrapping a community `cursor-api.sh`.

**Do not encode a 24-hour assumption.** Cursor's local `/loop` feature has a `--max-runtime` flag
and stops when the laptop sleeps — it is not this lane, and the two are easily conflated.

## API notes for whoever extends this

- **v0 is legacy.** Cursor's page: "New integrations should use the current Cloud Agents API,
  which reorganizes resources around a durable agent and per-prompt runs." The v1 API (public
  beta 2026-04-29) adds SSE streaming, reconnect, and lifecycle controls
  (archive/unarchive/delete). **Build against v1.**
- v0 surface: `GET /v0/agents`, `/v0/agents/{id}`, `/v0/agents/{id}/conversation`,
  `/v0/agents/{id}/artifacts`, `/artifacts/download`, `POST /v0/agents`,
  `POST /v0/agents/{id}/followup`, `POST /v0/agents/{id}/stop`, `DELETE /v0/agents/{id}`,
  `GET /v0/me`, `GET /v0/models`, `GET /v0/repositories`.
- Auth is HTTP Basic with the API key as username (`-u YOUR_API_KEY:`).
- **`webhook.url` + `webhook.secret` (min 32 chars) gives push-based status — prefer this over
  polling**, given the rate limits.
- Reuse `factory-automation/factory/factory_cursor_dispatch.py` rather than writing a second
  client.

## Cost

Charged at **API pricing for the selected model**; a spend limit is requested on first use. No
separate per-VM compute line item is documented. Requires a **paid** Cursor plan — Free cannot
run cloud agents. A larger selected context window increases token cost.

## Limitation

Fire-and-steer, not attach-and-detach. You can send follow-ups, but there is no interactive
session to join. For the multi-day interactive case see [lane 07](07-always-on-vm-BLOCKED.md).
