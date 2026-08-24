# Emanate Model Factory — cloud fine-tuning environment

**This repository is the source of truth for the fine-tuning pipeline.** It is
what the Model Factory's cloud-dispatch routine clones and executes. If you are
changing pipeline code, change it here.

Declared canonical 2026-08-24 (Daniel). Read the next section before editing
anything, because the history behind that sentence is the thing most likely to
trip you up.

## Why this repo exists, and what it replaced

The pipeline used to live in `emanate-tecum-workflow`, a large private control-
plane repo. Two problems made that unworkable as a cloud environment:

1. **The Claude GitHub App had no access to it.** It is a personal-account repo
   the App was never granted, separate from the `Emanate-AI` org. No branch
   could fix that — the block was on the whole repo. Four consecutive dispatch
   fires produced total silence before this was understood.
2. **It cannot be pushed to.** Its history carries 100MB+ corpus blobs, so the
   branch of record is stuck (V-309).

This repo was carved out to solve both. It carries only what a cloud fine-tune
actually needs, at the root, on `main` — no branch-switching step.

## Layout

| Path | What it is |
|---|---|
| `fine-tuning/factory-product/` | The pipeline. `factory.py` (per-stage CLI), `run_pipeline.py` (S1→S6 walk), `factory_sync.py` (Supabase projection), `specs/` (AgentSpecs) |
| `cloud-sessions/` | Cloud-session machinery: `bin/cloud-heartbeat.sh` (the ONLY telemetry channel — a pushed `cloud-fleet/<session-id>` git ref), `bin/cloud-claim.sh` (per-org mutual exclusion), `playbooks/` |
| `scripts/lib/` | The small shared helpers the pipeline imports |

Start with `cloud-sessions/playbooks/11-finetune-pipeline-agent.md` and
`fine-tuning/factory-product/ENVIRONMENT.md`. **Read ENVIRONMENT.md's §5 before
touching AgentSpec dispatch** — it explains which executors this environment can
and cannot use, and why.

## What deliberately is NOT here, and must not be added

This is not tidiness — each of these would break something or cost real money.

- **`scripts/tier2_run.sh`** — 37 of 41 AgentSpecs default to it, so shipping it
  looks like an obvious win. It resolves a local `claude` CLI backed by *that
  machine's own* Anthropic authentication, which this environment is
  structurally barred from holding (same reasoning that bars `FIREWORKS_API_KEY`).
  Adding it converts a loud `FileNotFoundError` into a silent credential failure
  or unmetered spend. **The correct fix for those specs is migrating them to
  `executor: self_dispatch`, not shipping the launcher.** (V-364)
- **`cursor_sdk` / `scripts/lib/cursor_guard.py`** — same shape, needs
  `CURSOR_API_KEY`. `factory_node_run.py` imports the cursor path lazily, so the
  S1→S6g path never touches it. That is why the pipeline works here today.
- **Any credential.** `FIREWORKS_API_KEY` lives only in the Trigger.dev task.
  The Supabase service-role key is never handed to a cloud session — it exchanges
  a short-lived claim code for a scoped relay token instead.
- **Large run artifacts.** `runs/ptc-steel` (810MB) and `runs/grand-steel` (21MB)
  stay out. Re-importing them recreates the exact problem this repo escaped; the
  root `.gitignore` enforces it.

## Where the rest of the system lives

Product UI, the Trigger.dev launch monitor, and the database live in
`Emanate-AI/platform-alpha`. PR records, handoffs, and the validation queue stay
in `emanate-tecum-workflow` — none of it is reachable from a cloud fine-tune,
and none of it should move here.
