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
| `fine-tuning/factory-product/` | The pipeline. `factory.py` (per-stage CLI), `run_pipeline.py` (S1→S6 walk), `factory_sync.py` (Supabase projection), `specs/` (AgentSpecs), `factory_heldout.py` + `audit/` (the eval anchor and comparison tooling) |
| `cloud-sessions/` | Cloud-session machinery: `bin/cloud-heartbeat.sh` (the ONLY telemetry channel — a pushed `cloud-fleet/<session-id>` git ref), `bin/cloud-claim.sh` (per-org mutual exclusion), `playbooks/`, `reference/`, `tests/` |
| `scripts/lib/` | The small shared helpers the pipeline imports |

Start with `cloud-sessions/playbooks/11-finetune-pipeline-agent.md` and
`fine-tuning/factory-product/ENVIRONMENT.md`. **Read ENVIRONMENT.md's §5 before
touching AgentSpec dispatch** — it explains which executors this environment can
and cannot use, and why.

## What this repo deliberately contains: the harness, and nothing else

This is a **harness**, not an archive. Everything here should be something a cloud
fine-tune either executes or needs to read while executing. That rule was applied
directly on 2026-08-24: material belonging to *other* initiatives had ridden along
in the original import — a provenance corpus for a different repository entirely,
a superseded 2026-07-29 research record about keeping a laptop awake, and the
lane/decision/evidence/session-log folders from that same workstream. None of it
was reachable from a fine-tuning run. It was removed, and every document that
cited it was rewritten so no instruction points at a path that no longer exists.
Git history keeps all of it if it is ever wanted again.

**Evaluation material is retained on purpose, including the parts that are not yet
wired.** This is the one place where "unused" must not be read as "dead." The
intended direction for this repo is improving how well we can evaluate the model
the pipeline produces, so the eval surface is the thing being built *on*, not
trimmed. Concretely, all of the following stay regardless of current wiring:

- `factory.py`'s `stage_eval` (S8), still a stub emitting the one-window contract —
  its docstring carries the `todo_v1` about wrapping `eval-all.ts --paired`
- `factory_heldout.py` — the sealed held-out anchor. `stage_eval` **fails closed**
  without it, deliberately, because a number measured on data the curator already
  saw is worse than no number: it gets believed
- `specs/eval-instrument-builder.yaml`, `eval-window-runner.yaml`,
  `eval-swarm-development.yaml`, `final-eval-swarm-custodian.yaml`
- `audit/compare_runs.py` and `ab_shadow_sync.py`, plus `tests/test_heldout_anchor.py`,
  `test_audit_compare_runs.py` and `test_ab_shadow_sync.py`
- `runs/qa-dryrun-synth/09-eval-report.json` — the synthetic fixture's eval output

If you are trimming this repo again later: cutting something because it is
eval-shaped and currently unreferenced is the specific mistake to avoid.

**Known first thing to widen when eval work starts** (found by the 2026-08-24
post-trim verification, recorded here rather than changed, because it is a
data-egress decision and every key in that allow-list carries a written PII
review): `factory_sync.SAFE_REPORT_KEYS` does **not** include `heldout_anchor`,
`judge_generator_separation`, `nodes` or `window_contract`. `stage_eval` produces
the sealed/unsealed held-out anchor and the judge/generator separation block, but
because those keys are not allow-listed they are stripped before sync — they exist
only in the on-disk `09-eval-report.json` and never reach Supabase or the Model
Factory tab. Any work on "make eval results visible in the product" starts there.

Related, and also left as-is: the committed `runs/qa-dryrun-synth/09-eval-report.json`
fixture is a `BLOCKED_ON_GATE` snapshot with `nodes: []` and no `heldout_anchor`, so
it does not exercise the real S8 payload shape and nothing currently
regression-guards it. A fixture captured from a real S8 dry-run would be a better
target once eval work begins.

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

## Verifying this environment (and what "skipped" means here)

Two suites, both $0 and offline:

```bash
cd fine-tuning/factory-product && python3 -m pytest tests/ -q   # 1096 passed, 26 skipped
bash cloud-sessions/tests/run.sh                                # regression suite for bin/cs
```

**Read the skip reasons, not just the count.** Every skip in the pytest suite is
conditional on a file being genuinely absent and states which file and why, so on
the operator's machine (where those files exist) the same tests run for real. Run
`pytest tests/ -q -rs` to print them. They fall into three groups:

| Count | Guarded on | Why it is not a defect here |
|---|---|---|
| 15 | a `platform-alpha` checkout | pre-existing schema-drift guard; the migration file it diffs against is in another repo |
| 8 | `company-brain/3-execution/*.md` | `lessons_sync`'s canon parse is a maintenance job. The pipeline only calls `attribute_lesson_to_run()`, a DB update that reads neither file |
| 1 | `scripts/factory_agents_sync.sh` | workflow-layer scheduling; a cloud run drives stages through `factory.py` directly |
| 1 | `cursor_guard` | deliberately absent (V-364) — see the section above |
| 1 | `WORKFLOW_ROOT` identity | the test hardcodes an `emanate-tecum-workflow` path and asserts the redactor treats it as repo-*internal*. Here it is genuinely external, so redacting it is correct |

None of these are on the pipeline path (S1→S10). If you add one of those files to
this repo, the corresponding tests un-skip automatically — the guards are absence
checks, not permanent opt-outs.

Before 2026-08-24, `pytest tests/` ran **zero** tests: `test_cursor_dispatch.py`
imports `factory_cursor_dispatch`, which imports the deliberately-absent
`cursor_guard` at module scope, and a collection error aborts the entire run.
`tests/conftest.py` now `collect_ignore`s that one module when `cursor_guard` is
unimportable. Do not resolve it by adding `cursor_guard.py`.

## Where the rest of the system lives

Product UI, the Trigger.dev launch monitor, and the database live in
`Emanate-AI/platform-alpha`. PR records, handoffs, and the validation queue stay
in `emanate-tecum-workflow` — none of it is reachable from a cloud fine-tune,
and none of it should move here.
