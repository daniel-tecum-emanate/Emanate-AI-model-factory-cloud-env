#!/usr/bin/env python3
"""backfill_ptc_history — one-time (idempotent) historical seed for the Runs tab.

**Why this exists.** The Model Factory tab's Runs/Lessons views were empty
because of two independent, unrelated gaps:

  1. `lessons_sync.py` has existed since T14 but nothing has ever actually run
     it against production — it is fully built and tested, just never invoked.
  2. There is no mechanism at all (this script fills that gap) to backfill the
     four REAL fine-tune iterations PTC Steel already went through — manually,
     via Cursor/Claude Code sessions, BEFORE this factory automation project
     existed. `factory_runs` was designed as a projection of `factory.py`
     stage reports (see `data-model/factory_runs.md`), and `factory.py` was
     never pointed at PTC Steel (no `configs/ptc-steel.yaml` exists — see
     `workspace/GAPS.md`), so none of that real history ever reached Supabase.

**What this writes** (all sourced from `company-brain/3-execution/`, chiefly
`MIXTURE-HISTORY.md` §1.1 and `DATASETS.md`'s `ptc-steel-v{1..4}` rows — every
number below has a citation in one of those two files):

  - 4 `factory_runs` rows: `ptc-steel-v1`..`v4`, chained via `iteration`/
    `predecessor_run_id` exactly like a real retrain lineage, `status='no_go'`
    on all four (`MIXTURE-HISTORY.md` §0: "no model has ever shipped to
    product"). `trigger_source` is left NULL — per its own column comment,
    NULL means "predates provenance recording," which is the literally
    correct value here, not a guess dressed up as one of the three real
    trigger kinds.
  - `factory_run_stages`: S1-S8 'passed', S9 'failed' per run (the real
    per-iteration work was census→export→corpus→train→eval; every iteration
    passed all of that and was rejected at the ship decision).
  - One `factory_gate_requests` + `factory_gate_decisions` row per run at S9,
    `status='rejected'`, with the real headline verdict as the decision note.
    Inserted directly (bypassing `decide_factory_gate`) because that RPC
    exists to make concurrent LIVE approval clicks race-safe — irrelevant to
    a single offline backfill of an already-settled historical decision.
  - `factory_run_events`: a `stage_started` + `gate_decided` event per run,
    the latter carrying "the one biggest lesson" from `MIXTURE-HISTORY.md` §0
    so the Timeline is substantive, not just a status change.
  - A local `.sync_state.json` + one iteration-digest file for `ptc-steel`
    (gitignored, mirrors what `factory_context.build_iteration_digest` would
    have written), so that WHENEVER a real `configs/ptc-steel.yaml` exists
    and someone runs the pipeline for real, Node 0 correctly classifies it as
    a retrain (iteration 5) with the v4 no-go reason as inherited context —
    this is the literal point of the retrain-context-continuity mechanism
    PR 1 built, applied to real history instead of a synthetic fixture.

**What this deliberately does NOT write:** `factory_agents`/`factory_handoffs`
rows (no real per-agent-session data survives from the manual era — leaving
these empty is honest; `RunDetail` already renders "No agents yet"
gracefully) and S2/S6g gate rows (no formal governance/launch gate concept
existed for these manual runs — inventing approve/reject rows for a gate that
was never actually asked would be fabrication, not backfill).

**Idempotency.** Every `factory_runs`/`factory_run_stages`/`factory_run_events`
row uses a UUID deterministically derived (`uuid5`) from a fixed namespace +
a stable name (e.g. `"ptc-steel-v3"`), so re-running this script upserts the
same rows rather than duplicating them. Gate rows are checked via `select`
before insert for the same reason (see `_ensure_ship_gate`).

Usage:
    export FACTORY_SUPABASE_URL=...
    export FACTORY_SUPABASE_SERVICE_ROLE_KEY=...
    python3 backfill_ptc_history.py --dry-run   # prints the 4 run rows, writes nothing
    python3 backfill_ptc_history.py             # pushes to Supabase + writes local state
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

from supabase_rest import SupabaseRestError, insert, select, update, upsert

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # emanate-tecum-workflow root
RUNS_DIR = HERE / "runs" / "ptc-steel"

ORG_SLUG = "ptc-steel"
ORG_NAME = "PTC Steel"
NAMESPACE = uuid.UUID("6f6e6520-6d6f-6465-6c20-666163746f72")  # fixed, arbitrary, stable

PROVENANCE_NOTE = (
    "Historical backfill (pre-automation, manual Cursor/Claude Code sessions). "
    "Source: company-brain/3-execution/MIXTURE-HISTORY.md + DATASETS.md. "
    "Dates below day-level where the source did not carry a timestamp; costs "
    "and verdicts are the real recorded numbers, not estimates."
)

PASSED_STAGES = ["S1", "S2", "S3", "S4", "S5", "S6", "S6g", "S7", "S8"]

# One row per real iteration. Every field traces to a citation in the two
# source files named in PROVENANCE_NOTE — see MIXTURE-HISTORY.md §1.1 and §0,
# and the `ptc-steel-v{N}` / `ptc-steel-v{N}-agent` DATASETS.md rows.
ITERATIONS = [
    {
        "name": "ptc-steel-v1",
        "iteration": 1,
        "run_kind": "first-train",
        "started_at": "2026-07-10T00:04:43Z",
        "updated_at": "2026-07-10T04:37:34Z",
        "cost_projected_usd": None,
        "cost_actual_usd": 19.36,
        "verdict": "NO-GO 4/8 gates",
        "lesson": (
            "Exact facts do NOT survive in a rank-32 adapter at ~30k-fact scale "
            "(36% recall at ~3 exposures/fact — a capacity wall, not a tuning "
            "miss). Facts live in TOOLS; weights hold BEHAVIOR."
        ),
    },
    {
        "name": "ptc-steel-v2-agent",
        "iteration": 2,
        "run_kind": "monthly-retrain",
        "started_at": "2026-07-11T00:00:00Z",
        "updated_at": "2026-07-11T12:00:00Z",
        "cost_projected_usd": 32.79,
        "cost_actual_usd": 45.16,
        "verdict": "NO-GO weights-only; agent-path PASS/GENERALIZES",
        "lesson": (
            "A dominant same-objective slice COLONIZES unrelated behavior — the "
            "mixture unit that matters is loss-bearing tokens, not record "
            "counts (multi-turn unroll silently doubled the tool slice's "
            "gradient share; refusal behavior collapsed, weights-only recall "
            "11%). Agent/tool path validated."
        ),
    },
    {
        "name": "ptc-steel-v3-agent",
        "iteration": 3,
        "run_kind": "monthly-retrain",
        "started_at": "2026-07-14T00:00:00Z",
        "updated_at": "2026-07-14T12:00:00Z",
        "cost_projected_usd": 50.73,
        "cost_actual_usd": 100.00,
        "verdict": "NO-GO 2/11; agent path strong, 3-4 abstention gaps",
        "lesson": (
            "Test what you DEPLOY, and verify graders before trusting a "
            "verdict — v3's 'NO-GO' was partly eval-mismatch (weights-only "
            "gates on an agent-served model) plus a grader false-negative; the "
            "dose-response curve was real but only inside that corpus. "
            "Champion model as of this iteration."
        ),
    },
    {
        "name": "ptc-steel-v4-agent",
        "iteration": 4,
        "run_kind": "monthly-retrain",
        "started_at": "2026-07-16T00:00:00Z",
        "updated_at": "2026-07-16T12:00:00Z",
        "cost_projected_usd": 61.63,
        "cost_actual_usd": 123.26,
        "verdict": "NO-GO all 4 §4 conditions; paired regressions incl. vague tripwire",
        "lesson": (
            "Dose targets do NOT transfer across corpus shapes; interference "
            "between response STYLES dominates once the mixture changes. "
            "Change as FEW variables per run as possible; include general/"
            "conversational replay; make style CONDITIONAL on input, never a "
            "blanket persona."
        ),
    },
]


def _run_id(name):
    return str(uuid.uuid5(NAMESPACE, f"factory_runs:{name}"))


def _stage_id(name, stage):
    return str(uuid.uuid5(NAMESPACE, f"factory_run_stages:{name}:{stage}:1"))


def _event_id(name, kind):
    return str(uuid.uuid5(NAMESPACE, f"factory_run_events:{name}:{kind}"))


def build_run_rows():
    rows = []
    prev_id = None
    for it in ITERATIONS:
        run_id = _run_id(it["name"])
        rows.append(
            {
                "id": run_id,
                "org_slug": ORG_SLUG,
                "org_id": None,
                "org_name": ORG_NAME,
                "run_kind": it["run_kind"],
                "model_stream": "per-account",
                "status": "no_go",
                "current_stage": "S9",
                "cost_projected_usd": it["cost_projected_usd"],
                "cost_actual_usd": it["cost_actual_usd"],
                "iteration": it["iteration"],
                "predecessor_run_id": prev_id,
                "trigger_source": None,
                "trigger_reason": f"{it['verdict']} — {PROVENANCE_NOTE}",
                "started_at": it["started_at"],
                "updated_at": it["updated_at"],
            }
        )
        prev_id = run_id
    return rows


def build_stage_rows():
    rows = []
    for it in ITERATIONS:
        run_id = _run_id(it["name"])
        # Every row needs identical top-level keys — PostgREST's bulk insert
        # derives its column list from the first object and rejects a batch
        # where a later object has a different key set (PGRST102).
        for stage in PASSED_STAGES:
            rows.append(
                {
                    "id": _stage_id(it["name"], stage),
                    "run_id": run_id,
                    "stage": stage,
                    "attempt": 1,
                    "status": "passed",
                    "report": {"backfill": True, "source": "MIXTURE-HISTORY.md"},
                    "finished_at": it["started_at"],
                }
            )
        rows.append(
            {
                "id": _stage_id(it["name"], "S9"),
                "run_id": run_id,
                "stage": "S9",
                "attempt": 1,
                "status": "failed",
                "report": {"backfill": True, "verdict": it["verdict"]},
                "finished_at": it["updated_at"],
            }
        )
    return rows


def build_event_rows():
    rows = []
    for it in ITERATIONS:
        run_id = _run_id(it["name"])
        rows.append(
            {
                "id": _event_id(it["name"], "stage_started"),
                "run_id": run_id,
                "ts": it["started_at"],
                "kind": "stage_started",
                "headline": f"{it['name']} training started",
                "detail": {"backfill": True},
            }
        )
        rows.append(
            {
                "id": _event_id(it["name"], "gate_decided"),
                "run_id": run_id,
                "ts": it["updated_at"],
                "kind": "gate_decided",
                "headline": f"S9 Ship — rejected ({it['verdict']})",
                "detail": {"backfill": True, "lesson": it["lesson"]},
            }
        )
    return rows


def _ensure_ship_gate(run_id, it, url, service_role_key, dry_run):
    existing = select(
        "factory_gate_requests",
        params={"run_id": f"eq.{run_id}", "gate": "eq.S9", "select": "id", "limit": "1"},
        url=url,
        service_role_key=service_role_key,
    )
    if existing:
        return "already present"
    if dry_run:
        return "would insert"

    request_row = insert(
        "factory_gate_requests",
        [
            {
                "run_id": run_id,
                "gate": "S9",
                "decision_request": {
                    "verdict": it["verdict"],
                    "cost_actual_usd": it["cost_actual_usd"],
                    "backfill": True,
                },
                "status": "rejected",
                "requested_at": it["started_at"],
                "resolved_at": it["updated_at"],
            }
        ],
        url=url,
        service_role_key=service_role_key,
    )
    gate_request_id = request_row[0]["id"]
    insert(
        "factory_gate_decisions",
        [
            {
                "gate_request_id": gate_request_id,
                "decision": "reject",
                "decided_by_email": "daniel@emanate.ai",
                "note": f"{it['verdict']}. {it['lesson']} ({PROVENANCE_NOTE})",
                "decided_at": it["updated_at"],
            }
        ],
        url=url,
        service_role_key=service_role_key,
    )
    return "inserted"


def write_local_continuity_state(dry_run):
    """Seed `.sync_state.json` + an iteration digest for ptc-steel so a real
    future `factory.py ptc-steel ...` invocation (once `configs/ptc-steel.yaml`
    exists) correctly classifies itself as iteration 5, retrain, with v4's
    no-go reason as inherited context — mirrors exactly what
    `factory_context.build_iteration_digest` would have written at v4's S9.
    """
    v4 = ITERATIONS[-1]
    v4_id = _run_id(v4["name"])
    state = {
        "run_id": v4_id,
        "iteration": v4["iteration"],
        "predecessor_run_id": _run_id(ITERATIONS[-2]["name"]),
    }
    digest = {
        "run_id": v4_id,
        "org_slug": ORG_SLUG,
        "iteration": v4["iteration"],
        "predecessor_run_id": _run_id(ITERATIONS[-2]["name"]),
        "corpus": {
            "recipe": "combined-train-v5-agent (19,807 records); QA-rehearsal "
            "38.8% / tool 61.2% raw lb; agentic-traj share 35.0%",
            "note": "See MIXTURE-HISTORY.md 1.2 for the full slice table.",
        },
        "training": {
            "base_model": "qwen3p6-27b (dense, FC-capable)",
            "lora_rank": 32,
            "epochs": 2,
            "learning_rate": "3e-6",
        },
        "ship": {
            "status": "no_go",
            "verdict": v4["verdict"],
            "reason": v4["lesson"],
            "recommendation": (
                "Re-measure dose->gate per corpus (do not carry v3's curve "
                "forward), include general/conversational replay (0% in all "
                "four corpora to date), make style conditional on input."
            ),
        },
        "backfill": True,
        "provenance": PROVENANCE_NOTE,
    }
    if dry_run:
        print(f"  (dry-run) would write {RUNS_DIR / '.sync_state.json'}")
        print(f"  (dry-run) would write {RUNS_DIR / 'iterations' / '04-digest.json'}")
        return
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / ".sync_state.json").write_text(json.dumps(state, indent=2) + "\n")
    (RUNS_DIR / "iterations").mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / "iterations" / "04-digest.json").write_text(json.dumps(digest, indent=2) + "\n")
    print(f"  wrote {RUNS_DIR / '.sync_state.json'}")
    print(f"  wrote {RUNS_DIR / 'iterations' / '04-digest.json'}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print what would be pushed, write nothing")
    parser.add_argument("--url", default=None)
    parser.add_argument("--service-role-key", default=None)
    args = parser.parse_args(argv)

    run_rows = build_run_rows()
    stage_rows = build_stage_rows()
    event_rows = build_event_rows()

    print(f"backfill_ptc_history: {len(run_rows)} runs, {len(stage_rows)} stages, "
          f"{len(event_rows)} events, 4 ship-gate request+decision pairs")
    for row in run_rows:
        print(f"  {row['org_slug']} iter={row['iteration']} status={row['status']} "
              f"cost=${row['cost_actual_usd']} id={row['id']}")

    if args.dry_run:
        print("\n--dry-run: no writes performed.")
        for it in ITERATIONS:
            print(f"  would ensure S9 ship gate for {it['name']}")
        write_local_continuity_state(dry_run=True)
        return 0

    try:
        upsert("factory_runs", run_rows, on_conflict="id", url=args.url, service_role_key=args.service_role_key)
        print("pushed factory_runs OK")
        upsert("factory_run_stages", stage_rows, on_conflict="id", url=args.url, service_role_key=args.service_role_key)
        print("pushed factory_run_stages OK")
        upsert("factory_run_events", event_rows, on_conflict="id", url=args.url, service_role_key=args.service_role_key)
        print("pushed factory_run_events OK")
        for it in ITERATIONS:
            run_id = _run_id(it["name"])
            result = _ensure_ship_gate(run_id, it, args.url, args.service_role_key, dry_run=False)
            print(f"  S9 ship gate for {it['name']}: {result}")
    except SupabaseRestError as exc:
        print(f"backfill_ptc_history: PUSH FAILED — {exc}", file=sys.stderr)
        return 1

    write_local_continuity_state(dry_run=False)
    print("backfill_ptc_history: done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
