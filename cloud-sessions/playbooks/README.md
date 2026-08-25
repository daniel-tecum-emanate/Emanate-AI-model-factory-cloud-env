# playbooks/

Task-shaped instructions — how to do a thing. Numbered in reading order; one workflow per
file. What is *true* about a thing goes in [`../reference/`](../reference/) instead.

| File | What it covers |
|---|---|
| [`01-setup.md`](01-setup.md) | One-time setup on a machine: installing the `claude` CLI, confirming claude.ai subscription auth, putting `cs` on PATH, granting the Claude account access to your GitHub repositories, accepting workspace trust, and the optional default environment and phone app |
| [`02-dispatch.md`](02-dispatch.md) | Handing work to a cloud VM and walking away — `cs handoff`, what `cs start` refuses to do and why, asking for a branch and a PR instead of a teleport, writing the prompt as if the author is leaving, parallel sessions, and repos without a GitHub remote |
| [`03-steer-and-return.md`](03-steer-and-return.md) | Finding your sessions, the three ways to steer a running one (`cs send`, browser, mobile app), and pulling it back onto this machine with `cs tp` |
| [`04-remote-control.md`](04-remote-control.md) | The opposite trade: keep the session on your Mac with your files, MCP servers and secrets, and use the phone or browser as a window into it. Status on this account is **connects** — server mode reached ready and printed a spawn URL; actually driving it from the phone is untested |
| [`05-routines.md`](05-routines.md) | Scheduled, HTTP-triggered, and GitHub-event-triggered cloud runs — the only way to start cloud work with no human at a terminal, since `claude --cloud` requires a TTY. Verified 2026-08-12; research preview. Includes the connector-inheritance and "Run now" footguns |
| [`06-environments.md`](06-environments.md) | Configuring the VM a session boots into: network access tiers, environment variables, setup scripts, and self-hosted environments |
| [`07-making-cloud-the-default.md`](07-making-cloud-the-default.md) | How to stop deciding where each session runs. **There is no "always cloud" setting** — so this is the five routes that get you there instead, what each one costs, what you give up by defaulting to cloud at all, and why the obvious shell wrapper around `claude` is the one to talk yourself out of |
| [`08-concurrent-sessions-and-exodus.md`](08-concurrent-sessions-and-exodus.md) | The operator's guide to the concurrent-session architecture: sessions auto-register on the heartbeat board via hook, owns-claims make the shared dirty tree partitionable, `cs board` is the single view, `cs exodus` builds per-lane branches and a dispatch manifest, `/cloud all` dispatches it, and `/cloud workflow <file>` puts an entire workflow — orchestrator included — in the cloud. Includes the honest limits and the close-the-lid runbook |
| [`09-porting-to-another-repo.md`](09-porting-to-another-repo.md) | Copying this folder into another repository: what ports, what refuses loudly, what breaks silently, and the ten-step checklist. Built from the 2026-08-17 transplant audit. |
| [`10-fleet.md`](10-fleet.md) | Running **many agents on cloud VMs at once**: the dispatch shape (create → run → **disable**, connectors read back because an empty list does not mean none), the heartbeat instruction block every payload carries, how a cloud agent declares a BLOCKER instead of guessing, the fetch-and-materialise step that makes beats readable on a laptop, watching the fleet with `cs fleet` and `fleet_ui.py`, what to do when an agent reports blocked, and the honest limits |
| [`11-finetune-pipeline-agent.md`](11-finetune-pipeline-agent.md) | **DOCS, design-only.** The dispatch brief for a cloud-hosted Claude Code session that orchestrates one fine-tuning pipeline run (`factory.py`/`run_pipeline.py`), stops and declares a blocker at every human gate (governance/launch/ship) instead of guessing past it, and never holds `FIREWORKS_API_KEY` or the Supabase service-role key — it triggers a separate credentialed task and only ever watches. Written for `PRs/model-factory-finetune-launcher-v1` T2-4; not yet exercised end to end |
| [`12-fleet-claims.md`](12-fleet-claims.md) | **Fleet claims**: a mutual-exclusion claim on one `(org_slug, model_stream)`, so two cloud sessions never both advance the same fine-tune pipeline run — `cloud-claim.sh check/claim/renew/release`, the ref naming and JSON schema, why the push on `claim` is never forced (the one piece that is genuinely load-bearing for correctness), and the staleness dereference into `10`'s own heartbeat ref. Built from `PRs/model-factory-cloud-environment-v1/04-multi-session-coordination-design.md` |

Read them in order the first time. After that, the routing table in
[`../README.md`](../README.md) points at the one you need.

08 and 10 are a pair: 08 is many sessions on **this machine** and how to move them, 10 is
many agents already **on cloud VMs** and how to see them. 11 is a concrete application of
10's mechanism to one specific workflow (fine-tuning), not a new mechanism of its own. 12
is a genuinely new mechanism that reads 10's heartbeats for one fact (staleness) — read 10
before 12.

Adding a new capability or workflow? It gets a new numbered file here **and** a row in
[`../reference/surfaces.md`](../reference/surfaces.md). Claims made in a playbook still
need their status tag (`VERIFIED` / `DOCS` / `UNVERIFIED`) and the evidence stated inline
next to the claim — the date it was measured and the command or transcript that showed it.
