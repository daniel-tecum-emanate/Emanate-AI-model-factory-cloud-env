# cloud-sessions

**The cloud-session machinery the Model Factory's fine-tuning pipeline runs on top of.**

This folder used to be a general-purpose workstream about running Claude Code anywhere that
survives a closed laptop. In this repo it has a narrower job: it is the harness half of the
cloud fine-tuning environment. The pipeline itself lives in
[`../fine-tuning/factory-product/`](../fine-tuning/factory-product/); this folder is how a
cloud session reports that it is alive, claims an org so two sessions cannot fight over one
run, and knows what it is allowed to do.

Working here as an agent? **[`AGENT.md`](AGENT.md) is the local authority** — read it first.
Its ground rules are binding, and its "constraints that will bite you" list is the shortest
route to not making an expensive mistake.

---

## If you are the dispatched fine-tuning agent

You need exactly four things from this folder, and one from outside it:

| What | Why |
|---|---|
| [`playbooks/11-finetune-pipeline-agent.md`](playbooks/11-finetune-pipeline-agent.md) | **Read it in full before starting.** The whole run — stage walk, gate behaviour, blocker taxonomy, the hard credential constraint |
| [`bin/cloud-heartbeat.sh`](bin/cloud-heartbeat.sh) | **The only telemetry channel.** It pushes a `cloud-fleet/<session-id>` git ref. Nothing else you write on that VM survives it |
| [`bin/cloud-claim.sh`](bin/cloud-claim.sh) | Per-org mutual exclusion, keyed on `(org_slug, model_stream)`. Claim before real work, renew when you beat, release when you stop |
| [`AGENT.md`](AGENT.md) | The constraints — above all: **cloud environments have no secrets store**, so credentialed work stays out of cloud sessions |
| [`../fine-tuning/factory-product/ENVIRONMENT.md`](../fine-tuning/factory-product/ENVIRONMENT.md) | Read in full too. Especially §5 — which executors this environment can and cannot use, and why |

Playbook 11 assumes [`02-dispatch.md`](playbooks/02-dispatch.md) and
[`10-fleet.md`](playbooks/10-fleet.md), and points at
[`06-environments.md`](playbooks/06-environments.md) and
[`08-concurrent-sessions-and-exodus.md`](playbooks/08-concurrent-sessions-and-exodus.md).
All four are here. Nothing playbook 11 references is missing.

---

## Layout

```
cloud-sessions/
├── AGENT.md          operating rules for agents working here — the local authority
├── README.md         this file
├── bin/
│   ├── cs                 the operator entrypoint (dispatch, steer, teleport, fleet)
│   ├── cloud-heartbeat.sh the cloud-side liveness emitter — the pushed git ref
│   ├── cloud-claim.sh     per-(org, stream) claims
│   ├── fleet_ui.py        the fleet page
│   ├── README.md          per-command internals, cost, side effects, the git gate
│   └── hooks/             session_register.sh + WIRING.md — staged, human-applied
├── playbooks/        01 setup · 02 dispatch · 03 steer & return · 04 remote control
│                     05 routines · 06 environments · 07 cloud by default
│                     08 concurrent sessions & exodus · 09 porting to another repo
│                     10 fleet · 11 fine-tune pipeline agent · 12 fleet claims
├── reference/        surfaces · limits · troubleshooting · networking ·
│                     how-others-build-this
├── tests/            run.sh — the regression suite for bin/cs. $0, offline, dispatches
│                     nothing. run-claims.sh covers cloud-claim.sh
└── state/            sessions.jsonl — the dispatch ledger, tracked on purpose
```

**Only `AGENT.md` and `README.md` live at the root, by rule.** A new document belongs in
`playbooks/` (how to do a thing) or `reference/` (what is true about a thing). That split is
the epistemics, not tidiness: `reference/` carries status tags and dates because it must,
`playbooks/` does not because it is instructions. A file at the root belongs to neither
category and escapes both obligations.

---

## Where to look for what

| I want to… | Read |
|---|---|
| orchestrate a fine-tuning run | [`playbooks/11-finetune-pipeline-agent.md`](playbooks/11-finetune-pipeline-agent.md) |
| know what each `cs` command does, costs, and touches | [`bin/README.md`](bin/README.md) |
| understand the heartbeat contract and the blocker taxonomy | [`playbooks/10-fleet.md`](playbooks/10-fleet.md) |
| understand claims — "is this org already being worked?" | [`playbooks/12-fleet-claims.md`](playbooks/12-fleet-claims.md) |
| decide which execution surface fits a situation | [`reference/surfaces.md`](reference/surfaces.md) |
| know what will run out, expire, or get blocked | [`reference/limits.md`](reference/limits.md) |
| understand how a cloud session reaches the internet | [`reference/networking.md`](reference/networking.md) · [`playbooks/06-environments.md`](playbooks/06-environments.md) |
| fix an error | [`reference/troubleshooting.md`](reference/troubleshooting.md) |
| change `bin/cs` without breaking it | [`tests/README.md`](tests/README.md) — run `bash cloud-sessions/tests/run.sh`; it costs nothing and dispatches nothing |
| see how Cursor and Codex architect this | [`reference/how-others-build-this.md`](reference/how-others-build-this.md) |

---

## Capability status

Dispatch, headless steering, teleport and routines are **verified working** (2026-08-12). A
cloud session can push a `claude/` branch and, since 2026-08-14, open a pull request.
Subagents run genuinely concurrently inside a cloud VM (2026-08-16), and one cloud session
has run an entire workflow — coordinator included — and opened a PR (2026-08-17).

Three things that are **not** true, and that cost time when assumed:

- **Interactive terminal attach (`claude --cloud <id>`) is gated off for this account.** A
  rollout gate, not a bug. `cs send` steers a running session instead, and needs no TTY.
- **`allowed_tools` is an auto-approval list, not a sandbox.** It does not restrict what a
  cloud session may do.
- **A cloud session's own telemetry dies with the VM, by decision.** Do not "fix" this by
  having a session commit its own trace — that shipped once and was reverted the same hour.
  `bin/cloud-heartbeat.sh`'s pushed ref is the channel that is meant to survive.

## What it costs

No separate compute charge — a cloud session draws on the same subscription rate limits as
any other Claude usage. Nothing in this folder should ever provision paid infrastructure;
recurring spend is Daniel's decision. The one real-money gate in the pipeline is **S6g**, and
it is held by a human on purpose — see playbook 11 §3.

## Provenance note

Some of this folder's original evidence record (dated cloud-VM probe reports, an audit set, a
gap register) was written and verified against a **different repository**,
`daniel0tgc/internal-company-tool`, and rode along into this repo by mistake. It was archived
in 2026-08-18 and removed in 2026-08-24 when this repo was trimmed to the fine-tuning harness.
Findings that still matter are stated inline in the files that rely on them; the originals
remain in git history. The mechanism documented here — `bin/cs`, the playbooks, the git gate,
the test harness — is genuine and account-level, and was never in question.
