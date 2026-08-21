# factory-product — agent index

Dense index. Read `README.md` for the full walkthrough (usage, add-a-customer, data-governance note).
Working on the cloud-dispatch environment, `factory_node_run.py`'s dispatch mechanism, or
`supabase_rest.py`'s transport layer? Read `ENVIRONMENT.md` first — it's the living design doc for
all three, including the one open, unresolved credential-wall question that blocks real (non-dry-run)
sub-agent dispatch from a cloud session.

Moved here from `factory-automation/factory/` (CLI + configs + runs) and `factory-automation/data-model/`
(Supabase schema docs, now at `data-model/`) on 2026-08-13, into one unified location. Git history for
every file traces through the move (`git log --follow`).

## What lives here

| Path | What it is | Real or stub? |
|---|---|---|
| `factory.py`, `factory_*.py`, `*_sync.py` | The Model Factory pipeline CLI (S1–S11) and its Supabase sync jobs | Mixed — see table below |
| `configs/*.yaml` | Per-org pipeline config (budgets, dose floors, governance clearance) | Real |
| `specs/*.yaml` | The AgentSpec catalog — roles an agent can be dispatched into | Real catalog; most specs still `draft` (unreviewed) |
| `audit/` | The data-audit swarm's deterministic lineage/parity/comparator checks | Real |
| `swarm/` | Cloud launch manifest generation + dual-lane swarm protocol/workspace code | Real |
| `data-model/` | Schema reference docs for the 9 Supabase tables the Model Factory tab renders | Real (docs, not code) |
| `runs/` | **Real training-run artifacts** — corpora, manifests, rigor/cost/eval reports, swarm evidence, per org/experiment slug | Real data (committed in full since this move — see governance note below) |
| `tests/` | pytest suite for the above | Real |
| `ENVIRONMENT.md` | The cloud-dispatch environment's design doc — the credential wall, the sync-relay, coordination primitives, prior-iteration lessons | Real (docs) |

## Stage-by-stage reality (from `factory.py`'s own docstring — not a guess)

| Stage | Code | Reality |
|---|---|---|
| census | S1 | Real |
| governance | S2 | Real (HUMAN GATE) |
| export | S3 | Real |
| build | S4 | Real work-order emitter; actual build has been session-work to date |
| verify | S5 | Real |
| project | S6 | Real |
| launch | S6g | Real (HUMAN GATE) |
| **train** | **S7** | **Stub — emits launch body, never launches** |
| **eval** | **S8** | **Stub — emits eval command, doesn't run it** |
| ship | S9 | Real (HUMAN GATE) |
| **activate** | **S10** | **Stub — emits a runbook, never executes** |
| **retrain** | **S11** | **Stub — never self-schedules** |

Every real fine-tune run to date (PTC Steel, Grand Steel) reached S6/S6g through this CLI, then had
its actual train/eval/activate happen as session-driven manual work living in `runs/<org>/`, outside
`factory.py`. For the full story of how that gap played out on a real, paid training job — including
a near-miss where a training spend was approved before anyone confirmed the launch mechanism
existed as working code — read
[`../lessons/model-factory/`](../lessons/model-factory/AGENTS.md), this product's parallel narrative
history. That folder documents the *story*; this folder is the *code and the data* the story is about.

## The 2026-08-14 catalog/graph/A-B initiative (three connected PRs)

A 2026-08-13 re-audit of Model Factory logging found three real, separate gaps and closed (or
deliberately deferred) them together. Full narrative + honest "what's still open" list in
`README.md`'s matching section — read that before touching any of this again.

| PR (`PRs/<slug>/`, this repo's root) | Status | What it closed |
|---|---|---|
| `model-factory-production-catchup` | **DONE** — commit `cd3e411` | `factory_models` catalog: 2 missing rows backfilled, 1 stale row refreshed, 3 writer scripts consolidated into `models_backfill.py` |
| `factory-graph-v2` (platform-alpha #2252) | Pushed, not merged — awaiting review | Rebased ~870 commits, retimestamped 4 migrations, closed 3 test gaps found during rebase; force-graph rewrite + inspector + 2–12-run aggregate comparison it ships were already QA'd |
| `model-factory-ab-consolidation-v1` | In progress — Fleet removal executing now | Fleet tab removal (confirmed strict subset of My Sessions); A/B cost wiring + idempotency-overwrite fix scoped but **blocked on platform-alpha PR #2373** |

Deliberately not built by any of the three: an automatic S9/S10 runtime writer for `factory_models`
(mechanism half of `INTERACTION-PARITY-AND-VIDEO.md` Gap 3 — still open), and the old
`ab-shadow-trigger`/`scripts/ab-shadow` pipeline (superseded, pending Daniel's sign-off to delete).
**Not touched by any of the three, and still carrying the exact same bug class the catalog had:**
`lessons_sync.py` — built and tested since T14, never once run against production
(`backfill_ptc_history.py`'s own docstring says so).

## Where the real run artifacts are, concretely

- `runs/ptc-steel/` — real corpus (`corpus/build/*.jsonl`), audit trail (`audit/exclusions.yaml` —
  the org-partition exclusion list), cloud swarm evidence (`swarm/cloud/`).
- `runs/grand-steel/` — real corpus (`gs_corpus/`, `gs_eval/`), swarm evidence, the `v2-candidate/`
  build that fed its canary + full training launch.
- `runs/qa-dryrun-synth/`, `runs/synthetic-test-org/`, `runs/zz-stress-sandbox/` — synthetic fixtures,
  not real customer data.
- `runs/swarm/` — cross-lane, not-org-scoped swarm coordination artifacts.

## Data-governance check run before this move (2026-08-13)

Before committing `runs/` (previously gitignored, never committed — real corpora built from real
production account data), checked against this program's own existing rules
(`fine-tuning/lessons/cross-model-lessons/data-governance/`):

- **Cross-org contamination (`FORBIDDEN_ORG_PREFIX` guard, `ptc_common.py`):** checked every real
  PTC corpus file (`ptc-corpus-v6.jsonl`, `real-trajectory-rendered-v6.jsonl`, `action_cf.jsonl`,
  `twins.jsonl`, `stale_recall.jsonl`, `ptc-rescope-2569.jsonl`) for the forbidden org id
  (`10664bd2…`), its envelope-hash prefix, and the Intelligence 3-tool contract hash — zero hits in
  all of them. `selection-pool.json`'s one `10664bd2` occurrence is guard metadata
  (`"excluded_org_prefix": "10664bd2", "excluded_org_rows_found": false`), not a violation. A
  238-record Grand Steel file (`gs_corpus/v2-candidate/families/1a-real-trajectories-fixed.jsonl`)
  is 100% `org_id: 10664bd2` — initially flagged, then confirmed this is Grand Steel's own real
  production org id (see `runs/grand-steel/03-export-report.json`'s export command), not the
  "second, forbidden" org from PTC's perspective — i.e. Grand Steel data in Grand Steel's own run
  directory, not cross-contamination. **Passed — nothing excluded.**
- **Unredacted personal PII:** found business-contact emails in real corpus data (role-based
  addresses like `info@…`, and individual work emails like `firstname@company.com`) — consistent
  with the account/transcript/review data this program's `ptc-steel.yaml` governance block already
  marks `pii_egress_cleared: true` under a standard product-usage/ToS basis, the same basis that
  already cleared five prior real PTC fine-tune iterations on this exact class of data. No phone
  numbers or non-business personal PII found. **Passed.**

No files were excluded from this move on data-governance grounds.

## `.gitignore` decision

The old `factory-automation/factory/.gitignore` (`runs/`) was replaced — `runs/` is no longer
ignored here, per the governance check above. `__pycache__/`, `*.pyc`, `.DS_Store` stay ignored via
the repo-root `.gitignore` (unaffected by the move); `.pytest_cache/` and `.ruff_cache/` stay ignored
via their own nested `.gitignore` files, which moved along with this directory.

## Fixed on move: paths that assumed the old `factory-automation/` location

- `factory.py`'s `context_refs` (S2 decision log), `ab_shadow_sync.py`'s `projected_by` provenance
  string, and `swarm/cloud_bootstrap.py`'s `PTC_SWARM_ROOT`/`GS_SWARM_ROOT`/`PTC_CORPUS_ROOT` all
  hard-coded `factory-automation/factory/...` — now `fine-tuning/factory-product/...`.
- `swarm/cloud_bootstrap.py`'s `SHARED_PROGRAM_DOCS` and every `specs/*.yaml` `evidence_pointer` /
  `read_first` entry pointed at `factory-automation/model-training-swarm/...` — that folder was
  itself renamed to `fine-tuning/program-coordination/` by a parallel move; updated to match.
- `factory_sync.py`'s `SAFE_PATH_PREFIXES` redaction allow-list gained `fine-tuning/factory-product/`
  alongside the pre-existing `factory-automation/` entry (which stays — other `factory-automation/`
  subfolders still exist and are still legitimately referenced elsewhere).
- `README.md`'s links to `FACTORY-PLAN.md`/`FACTORY-EXPLAINER.html` (which stayed under
  `factory-automation/`) gained an extra `../` hop for the new depth.
- Left untouched, deliberately: `"zone": "factory-automation"` string literals in `swarm/runner.py`
  and `swarm/protocol.py` — that's a coordination *zone name* (`coordination/heartbeat/zones/`), not
  a filesystem path, and changing it would break integration with the zone registry (out of this
  move's scope). Also untouched: references to other still-in-place `factory-automation/` subfolders
  (`data-audit-swarm/`, `product-integration/`, `stress-test-2026-07-31/`, `automation-graph/`) and
  test-fixture string literals under `tests/` (arbitrary example paths, not real dependencies).
