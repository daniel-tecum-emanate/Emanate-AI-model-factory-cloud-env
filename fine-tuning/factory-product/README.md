# factory-product/ — the Model Factory CLI, its Supabase data-model docs, and its real run artifacts

Moved here from `factory-automation/factory/` + `factory-automation/data-model/` on 2026-08-13, into
one unified location. `git log --follow` on any file traces its full history through the move.

Design: `../../factory-automation/FACTORY-PLAN.md` (stages, gates, config schema). Plain-language:
`../../factory-automation/FACTORY-EXPLAINER.html`. (Those two docs stayed under `factory-automation/`
when this product moved to `fine-tuning/factory-product/` on 2026-08-13 — hence the extra `../` hop.)
For the deeper narrative on what's real versus stub in this product, and what a real, expensive
production run actually looked like against this design, read
[`../lessons/model-factory/`](../lessons/model-factory/README.md) — this folder is the code and the
data; that one is the story of running it for real. See `AGENTS.md` for the dense index, including
the stage-by-stage real/stub table and where the real run artifacts now live.

## Run it

```bash
cd fine-tuning/factory-product

python3 factory.py grand-steel --stage census --dry-run   # $0, read-only, works today
python3 factory.py grand-steel --status                   # pipeline state at a glance
python3 factory.py grand-steel --stage governance         # shows the clearance checklist (gate)
python3 factory.py <org> --stage export --live            # actually runs the dossier exporter (read-only prod)
```

- Stage order: `census → governance → export → build → verify → project → launch → train → eval → ship → activate → retrain`.
- Human gates (`governance`, `launch`, `ship`) need `--approved` — typed by Daniel, not an agent.
  A gate refuses approval when its own preconditions are untrue (governance checklist, over-cap projection).
- Any stage past an unapproved gate refuses with exit 3.
- Every stage writes `runs/<org>/NN-<stage>-report.json`. As of the 2026-08-13 move to
  `fine-tuning/factory-product/`, `runs/` is committed in full (real corpora, manifests, reports) —
  see `AGENTS.md` for the data-governance check behind that decision. It is no longer gitignored.
- `--dry-run` never spawns a subprocess.

## Add a customer

Copy `configs/_template.yaml` → `configs/<slug>.yaml`; fill it from the org's OWN census/readiness
report (never another org's numbers). `configs/grand-steel.yaml` is the real worked example.

## Rules this skeleton obeys

- Manually invoked only — NOT registered in PROCESSES.md; do not schedule it until it is.
- No training launches, no deploys, no prod writes — those stages emit runbooks/launch bodies only (v0.5).
- Champion models are never overwritten; NO-GO keeps the incumbent.

## What's real vs. documented-stub, per `factory.py`'s own docstring

This isn't a guess — `factory.py`'s module docstring states it plainly, stage by stage:

| Stage | Code | Kind | Reality |
|---|---|---|---|
| census | S1 | auto | Real — reads on-disk census data |
| governance | S2 | HUMAN GATE | Real — refuses without `--approved` |
| export | S3 | auto | Real — wraps `export-account-dossiers.ts --org` (read-only prod) |
| build | S4 | auto* | Real work order emitted; the actual corpus build has been session-work, not push-button, to date |
| verify | S5 | auto | Real — deterministic verification battery, all-must-pass |
| project | S6 | auto | Real — cost projection (2x dual-bucket, 1.3x calibration) |
| launch | S6g | HUMAN GATE | Real — refuses without `--approved` |
| **train** | **S7** | auto* | **Stub — emits the launch body, never launches** |
| **eval** | **S8** | auto* | **Stub — emits the eval command, doesn't run it** |
| ship | S9 | HUMAN GATE | Real — refuses without `--approved` |
| **activate** | **S10** | runbook | **Stub — emits a runbook, never executes the serving switch** |
| **retrain** | **S11** | proposed | **Stub — never self-schedules; PROCESSES.md gate** |

In practice, every real fine-tune run to date (PTC Steel, Grand Steel) reached S6/S6g through this
CLI, then had its actual train/eval/activate work happen as session-driven manual REST calls and
scripts sitting in `runs/<org>/`, outside `factory.py` entirely — not a failure of this code, just
the honest state of what's automated versus what's still a human running commands by hand. Read
[`../lessons/model-factory/architecture/`](../lessons/model-factory/architecture/README.md) for the
full stage-by-stage account of how that gap actually played out on a real, paid training job.

## Where the real run artifacts live

`runs/` is organized one subfolder per org/experiment slug, each holding that run's `NN-<stage>-report.json`
files plus whatever the `build`/`verify`/`swarm` stages actually produced on disk:

- `runs/ptc-steel/` — PTC Steel's real corpus build (`corpus/`, including `corpus/build/*.jsonl` —
  the actual rendered training records), its cloud swarm evidence (`swarm/cloud/`), and its audit
  trail (`audit/`, including `exclusions.yaml` — the org-partition exclusion list).
- `runs/grand-steel/` — Grand Steel's equivalent: `gs_corpus/`, `gs_eval/`, `swarm/`, and the
  `v2-candidate/` build that fed its canary + full training launch.
- `runs/qa-dryrun-synth/`, `runs/synthetic-test-org/`, `runs/zz-stress-sandbox/` — synthetic/test
  fixtures for exercising the pipeline without touching real customer data.
- `runs/swarm/` — cross-lane swarm coordination artifacts (program-level ledgers, decision packets)
  that aren't scoped to a single org slug.

These were gitignored before the 2026-08-13 move; they are committed in full now. See `AGENTS.md`
for the data-governance check that cleared that decision.

## 2026-08-14 — three connected PRs: catalog backfill, graph rebase, Fleet/A-B consolidation

A 2026-08-13 re-audit of Model Factory logging found three separate, real gaps — one in the
Models tab's own data, one in a stuck platform feature branch, one in tab-level duplication.
Three PRs closed (or deliberately deferred) them together. Each has its own full `PRD.md`/
`PRDone.md`/`PRContext.md` in `PRs/<slug>/` (this repo's root) — this section is the connecting
narrative, not a replacement for reading them.

### 1. `factory_models` catalog was 30% missing, 10% stale, with no mechanism to stay current

`PRs/model-factory-production-catchup/` (**DONE**, commit `cd3e411`, 2026-08-14). The original
kickoff premise ("the schema itself is missing from `origin/main`") was already stale by the time
Discovery ran — the three `factory_models` migrations had already shipped via platform-alpha
PR #2180. The real gap was data, not schema: of the 10 real trained models
([`../models/INDEX.md`](../models/INDEX.md)), `grand-steel-v2-agent` and `ptc-steel-v7-agent` had
no catalog row at all, and `grand-steel-v1-agent`'s row was stuck on its
day-training-completed `pending` state for two weeks, predating the diagnostic waves that reached
this program's `FINALIZED_NO_TRAIN` verdict. Three separate one-off writers existed
(`models_backfill.py` plus two per-model `record_model.py` scripts, one of them a stray never-run
duplicate). This PR backfilled/refreshed the three rows and folded all of it into
`models_backfill.py` alone — now the single canonical writer for all 10 models, extended test
coverage 23→28 cases. See [`data-model/factory_models.md`](data-model/factory_models.md) for the
schema this produced.

**Deliberately not built:** an automatic S9/S10-triggered runtime writer (the *mechanism* half of
`factory-automation/automation-graph/INTERACTION-PARITY-AND-VIDEO.md` Gap 3) — that's a real
pipeline change touching `factory.py`/`factory_sync.py`, not a backfill script, and stays a named,
open follow-up. Production Supabase was not touched — this PR wrote to local Supabase only.

### 2. A real graph rewrite sat stuck, unrebased, for weeks

`platform-alpha` PR #2252 (`feat/factory-graph-v2`, tracked in `PRs/factory-graph-v2/`). The
force-directed graph rewrite, five-tab agent inspector, and 2–12-run aggregate comparison view had
been implemented and QA'd on 2026-07-31/08-04, then sat on a branch that drifted ~870 commits
behind `origin/main`. A 2026-08-14 session rebased it clean (0 behind / 12 ahead), retimestamped 4
migrations, fixed 4 test files that hardcoded pre-retimestamp migration filenames, and closed 3
small test gaps found during the rebase (`layoutForceGraph` determinism, `nodes.tsx` helpers, the
`AggregateGraphControls` 2–12 session floor/cap boundary). Full suite: 18,216/18,229 passing (13
failures are pre-existing, environment-gated — a live Microsoft Graph mailbox, an Apollo API key,
or local Docker, none touching the graph). Full detail:
[`../../PRs/factory-graph-v2/PRQAResults.md`](../../PRs/factory-graph-v2/PRQAResults.md). Pushed,
**not merged** — Daniel asked for an independent reviewer rather than merging his own PR.

**Deliberately stashed, not built here:** a separate timeline/session-clustering utility redesign
(`TimelineView`, `SessionIOCard`, `collapse.ts`, `aggregate-clusters.ts`,
`aggregate/timeline-projection.ts`) had accumulated uncommitted on the same worktree. The
2026-08-14 rebase session confirmed it was safely `git stash`ed, not deleted, and explicitly out
of this PR's scope — a real, separate follow-up, not abandoned work.

The workflow-side data producer for this graph, `flow_sync.py` (derives 8-type/3-confidence edges
from heartbeat/ledger/handoffs/dispatch records into `factory_handoffs` + `info_flow` events), is
already implemented and has run live (199 flows derived, 74 handoffs, 177 events, 22 run-less rows
correctly left unsupported rather than fabricated) — but as its own workflow-repo commit, decoupled
from the platform PR because the two repos have different release paths. It is not yet on a
schedule (Daniel-gated per I10, `PROCESSES.md`) — today its liveness still depends on someone
running it by hand.

### 3. The Fleet tab was a strict, provable subset of My Sessions

`PRs/model-factory-ab-consolidation-v1/` (**in progress** — Discovery + Plan complete, Fleet
removal executing now in worktree `platform-alpha-model-factory-ab-consolidation-v1`). Confirmed
directly: Fleet and My Sessions both read `factory_agents` through the identical `AgentsTable`
component; Fleet's own action (`actions/fleet.ts`) adds a `.not("run_id", "is", null)` filter My
Sessions doesn't, so My Sessions ⊇ Fleet exactly, by construction, not incidental overlap. Fleet's
one real distinguishing behavior — sorting agents grouped by run — is being folded into My Sessions
as a sort-mode toggle rather than dropped. `agents_sync_job.py` (the Python write side) is
untouched; this is a read-side-only change.

### What this same PR scoped, found genuinely real, and deliberately did **not** build yet

Daniel's original ask for this PR also named two more items. Both turned out to be real gaps, and
both are **blocked**, not abandoned:

- **Challenger cost (`model_ab_nights.challenger_cost_usd`) is genuinely unwired end-to-end.** The
  column exists, is nullable, and is already threaded through the writer's plumbing — nothing
  computes a real value. RunPod's own per-hour billing rate is available in its API response and
  currently discarded; there is no wall-clock capture of a pod's actual rental duration anywhere in
  the session code. Real fix requires extending `RunPodApiPodResponse`, capturing rental
  start/teardown timestamps, and threading the product through `finalizeNight`'s existing
  `costLatency` argument — never coercing a missing input to `0`.
- **The idempotency-key overwrite bug is real and already destroyed a real proof row once.** On
  2026-08-12/13, a live test produced Grand Steel's first-ever 149-valid clean comparison, and the
  real scheduled cron overwrote that row hours later with a pre-fix failure outcome, because
  `transitionNight`/`finalizeNight` are unconditional `update ... WHERE idempotency_key = X` calls
  with no protection against clobbering an already-terminal, already-`clean`, already-`sealed` row.

Both fixes touch `src/features/accounts/lib/model-ab-history.ts` — the exact file
platform-alpha PR #2373 (open, unmerged, `fix/model-ab-shadow-failure-categorization`) is actively
modifying (it already fixed one half: `beginNight`'s blind-upsert-reset). Building either fix
against a stale base risks silently re-introducing the bug #2373 already fixed, or a merge
conflict that picks the wrong version of a just-fixed function. **Both items wait on #2373 landing
first** (or this PR's branch being built directly on top of #2373's branch, with a coordination
zone registered for `platform-alpha-model-ab-hardening-v1` first — it currently has none despite
being an active worktree with unmerged work).

An old, unrelated pipeline (`src/ab-shadow-trigger/*`, `scripts/ab-shadow/*`, uncommitted on a
~2,900-commit-stale `feat/qa-dataset-v2` branch) was also in scope by name in the original ask;
Discovery found it predates and is structurally incompatible with the real, shipped
`model_ab_nights` system that already does what it was meant to do. It is recommended for deletion,
pending Daniel's explicit sign-off (not silently dropped, per this same PR's own restated lesson
about not silently reconciling a moved premise).

### What's still open after all three land

- **A/B cost wiring and the idempotency-key fix remain blocked on PR #2373.** Neither has code yet
  in `model-factory-ab-consolidation-v1` — only a Discovery-level design.
- **`lessons_sync.py` has the same "never actually runs" bug class the `factory_models` catalog
  had before this week's catchup — and it hasn't been fixed.** Per its own neighbor script's
  docstring (`backfill_ptc_history.py`): *"`lessons_sync.py` has existed since T14 but nothing has
  ever actually run it against production — it is fully built and tested, just never invoked."*
  `backfill_ptc_history.py` closed a different, unrelated gap (missing PTC Steel run history) and
  left this one exactly as it found it. The Lessons tab today is empty (or stale) for the same
  underlying reason the Models tab was, and nothing in this week's three PRs touches it.
- **The stashed timeline/session-clustering graph work is a real, separate follow-up** — recoverable
  from the `feat/factory-graph-v2` worktree's stash, not scoped into #2252 or any other open PR yet.
- **PR #2252 itself is not merged** — pushed, awaiting an independent reviewer.
- **The `factory_models` mechanism gap (no S9/S10 runtime writer) is still open** — every future
  trained model is still one manual `models_backfill.py` edit someone has to remember to make,
  exactly the way `grand-steel-v2-agent`/`ptc-steel-v7-agent` became gaps in the first place.
