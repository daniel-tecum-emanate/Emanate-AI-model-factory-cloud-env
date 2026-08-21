#!/usr/bin/env python3
"""agents_sync_job — the scheduled poll `factory_sync.py`'s own T13 docstring
anticipated ("not wired into any CLI path in this PR... callable by a future
scheduled sync job"). Wires `project_agents()`/`push_agents()` up to actually
run periodically, per Daniel's 2026-07-24 ask: "while I work on cursor or
claude code and have whatever agents running, that it automatically syncs to
the model factory UI."

Scope decision (Daniel, 2026-07-24 — asked via AskQuestion, not assumed):
RUN-SCOPED, not machine-wide. This job only ever syncs agent sessions for a
run this factory itself knows about (`fine-tuning/factory-product/runs/<slug>/`
having a `.sync_state.json`) AND whose `factory_runs.status` is currently
`running` or `blocked_on_gate` — i.e. only work that's actually part of an
active Model Factory pipeline execution, using the exact same
`_session_matches_run()` org_slug filter `project_agents()` already applies.
It deliberately does NOT show every Cursor/Claude Code session machine-wide
(that "whole-machine Live Board" is out of scope today — PLAN.md §7, and
FleetTab.tsx's own comment says as much) — a session working on an unrelated
project never appears here, matching this tab's stated purpose (tracking
*this* fine-tune, not general engineering work).

Mechanism decision (Daniel, 2026-07-24): a SCHEDULED POLL, not a live Cursor
hook — same call this repo's own checkpoint-video system made for the
identical trade-off (see PROCESSES.md's checkpoint-video entry: a live
SessionEnd hook "fires on every session exit machine-wide with no gate").
This script is invoked every 5 minutes by
`scripts/factory_agents_sync.sh` via the standard `run_job.sh` wrapper.
[Corrected 2026-08-04: this docstring previously said the job was NOT
registered in PROCESSES.md. It IS — registered and activated 2026-07-31 as
`com.tecum.workflow.factory-agents-sync` (every 5 min, Tier 0, $0) through
the standard I10 path, citing Daniel's stress-test instruction as approval;
the same wrapper also runs `flow_sync.py --live` after agent rows succeed.
See PROCESSES.md's factory-agents-sync entry.]

Fail-open, same direction as `push_stage`/`push_agents` themselves (this is
telemetry, not a human-approval gate — PRRules.md rule 5): any error for one
run is logged and skipped, never crashes the whole poll or the other runs.

Cost: Tier 0, $0 — pure script, zero LLM calls, matches every other Tier-0
job in PROCESSES.md (ledger-summarize, compute-metrics, status-watcher).
"""

import json
import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # emanate-tecum-workflow root
RUNS_DIR = HERE / "runs"
HEARTBEAT_PATH = REPO / "heartbeat" / "STATE.md"
LEDGER_DIR = REPO / "ledger"

sys.path.insert(0, str(HERE))

from factory_sync import (  # noqa: E402
    parse_active_sessions,
    push_agents,
    push_cloud_fleet_agents,
    push_machine_sessions,
    sessions_claimed_by,
)
from graph_artifacts import (  # noqa: E402
    build_session_id_map,
    load_agent_records,
    sync_agent_artifacts,
)
from supabase_rest import SupabaseRestError, select  # noqa: E402

log = logging.getLogger("agents_sync_job")

OPEN_STATUSES = {"running", "blocked_on_gate"}


def _discover_run_slugs():
    """Every org slug this factory has ever pushed a stage for — one directory
    per slug under `runs/`, each with its own `.sync_state.json` holding the
    minted `run_id` (see `factory_sync.get_or_create_run_id`'s docstring for
    why that file, not a Supabase query, is the map from slug -> run_id).
    """
    if not RUNS_DIR.is_dir():
        return []
    return sorted(p.name for p in RUNS_DIR.iterdir() if p.is_dir())


def _run_id_for_slug(slug):
    state_path = RUNS_DIR / slug / ".sync_state.json"
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text()).get("run_id")
    except (ValueError, OSError):
        return None


def _is_run_open(run_id, url=None, service_role_key=None):
    """A run only appears in the Fleet tab's live sync while it's actually in
    progress — a `shipped`/`failed`/`no_go`/`abandoned` run stops accumulating
    new agent rows the moment it lands there, so a stale, long-finished run's
    directory never keeps generating live-looking activity forever.
    """
    try:
        rows = select(
            "factory_runs",
            params={"id": f"eq.{run_id}", "select": "status", "limit": "1"},
            url=url,
            service_role_key=service_role_key,
        )
    except SupabaseRestError as exc:
        log.warning("agents_sync_job: could not check run %s status — skipping this poll: %s", run_id, exc)
        return False
    if not rows:
        return False
    return rows[0].get("status") in OPEN_STATUSES


def _recent_ledger_lines(days=2):
    """The last `days` day-files' worth of ledger lines — mirrors
    `status_watcher.sh`'s own "last 2 day-files" windowing for the same reason
    (a session's tool-call history rarely needs to look back further than
    that for a live sync, and reading the whole ledger on every poll would
    grow unboundedly with the ledger's own age).
    """
    if not LEDGER_DIR.is_dir():
        return []
    names = sorted(p.name for p in LEDGER_DIR.iterdir() if p.name.endswith(".jsonl"))
    lines = []
    for name in names[-days:]:
        try:
            lines.extend((LEDGER_DIR / name).read_text().splitlines())
        except OSError:
            continue
    return lines


def sync_all_open_runs(url=None, service_role_key=None):
    """Poll every run this factory has a local record of; push agents/events
    for the ones still open. Returns a summary dict for the caller to log —
    never raises (this is the top-level entry point `main()` calls; a single
    run's failure inside `push_agents` is already swallowed there, and a
    Supabase reachability failure inside `_is_run_open` is swallowed above).
    """
    if not HEARTBEAT_PATH.exists():
        log.warning("agents_sync_job: no heartbeat/STATE.md found — nothing to sync.")
        return {"runs_checked": 0, "runs_open": 0, "runs_synced": 0, "sessions_synced": 0}

    heartbeat_md = HEARTBEAT_PATH.read_text()
    ledger_lines = _recent_ledger_lines()

    # The session_id_map gap, closed (factory-graph-v2 artifact pipeline,
    # 2026-08-04): `project_agents`' session_id_map overlay existed since T13
    # but this job never fed it, so tool aggregates (files_touched/
    # commands_run) stayed empty for every interactive row. The map is built
    # from `ledger/agents/` records via the label-in-prompt join (design
    # research/03 §4.2 method 3); ambiguity maps to absence, conservatively.
    agent_records = load_agent_records(agents_dir=LEDGER_DIR / "agents")
    labels = [
        s.get("session", "") for s in parse_active_sessions(heartbeat_md) if s.get("session")
    ]
    session_id_map = build_session_id_map(labels, agent_records)

    slugs = _discover_run_slugs()
    runs_open = 0
    runs_synced = 0
    open_slugs = []
    for slug in slugs:
        run_id = _run_id_for_slug(slug)
        if not run_id:
            continue
        if not _is_run_open(run_id, url=url, service_role_key=service_role_key):
            continue
        runs_open += 1
        open_slugs.append(slug)
        ok = push_agents(
            cfg={},
            slug=slug,
            run_id=run_id,
            heartbeat_md=heartbeat_md,
            ledger_lines=ledger_lines,
            session_id_map=session_id_map,
            url=url,
            service_role_key=service_role_key,
        )
        if ok:
            runs_synced += 1

    # A4 (factory-observability-v1) — the machine-wide pass, feeding the "My
    # Sessions" surface.
    #
    # This does NOT widen the run-scoped sync above, and the scope decision recorded
    # in this module's docstring still stands: the Fleet tab shows work that is part
    # of an active pipeline execution, and a session on an unrelated project does not
    # appear there. What changed is that such a session now reaches the product at
    # all — as a row with `run_id: NULL`, on its own surface. `listAgents` (the Fleet
    # tab's read) excludes those rows explicitly, which is what keeps the decision
    # true on the product side rather than by omission.
    #
    # `sessions_claimed_by` is what stops a session that IS on an open fine-tune from
    # being pushed twice — once scoped by the loop above and once unscoped here.
    sessions_synced = push_machine_sessions(
        heartbeat_md=heartbeat_md,
        ledger_lines=ledger_lines,
        session_id_map=session_id_map,
        skip_titles=sessions_claimed_by(heartbeat_md, open_slugs),
        url=url,
        service_role_key=service_role_key,
    )

    # T1-1 (model-factory-finetune-launcher-v1) — the cloud-fleet heartbeat
    # bridge: cloud-heartbeat.sh's git-pushed `cloud-fleet/<session-id>` refs,
    # projected the same way the machine-wide heartbeat pass above is. Given
    # the SAME "not added to the summary dict" treatment as the artifacts pass
    # just below, and for the same reason: this is a distinct telemetry
    # source (a git fetch, not heartbeat/STATE.md), and its failure must never
    # read as the agents sync itself failing.
    cloud_fleet_synced = push_cloud_fleet_agents(url=url, service_role_key=service_role_key)
    log.info("agents_sync_job: cloud-fleet pass synced %s agent(s)", cloud_fleet_synced)

    # The artifacts pass (factory-graph-v2 research/03 §4.1): runs AFTER the
    # agent pushes so the read-back sees the rows this poll just wrote. Wholly
    # fail-open inside sync_agent_artifacts — including the case where the
    # `factory_agent_artifacts` table has not shipped from the platform repo
    # yet (one warning, then dormant). Deliberately NOT added to the summary
    # dict: it is telemetry-of-telemetry, and its failure must never read as
    # the agents sync failing (the wrapper's staleness sentinel keys off the
    # agents counts).
    artifacts_processed = sync_agent_artifacts(
        url=url, service_role_key=service_role_key, agent_records=agent_records
    )
    log.info(
        "agents_sync_job: artifacts pass processed %s agent(s)",
        "none (fail-open)" if artifacts_processed is None else artifacts_processed,
    )

    return {
        "runs_checked": len(slugs),
        "runs_open": runs_open,
        "runs_synced": runs_synced,
        # `None` when the push failed (fail-open) — distinct from 0, which means the
        # push worked and every active session was already claimed by an open run.
        "sessions_synced": sessions_synced,
    }


def main(argv=None):
    del argv
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    summary = sync_all_open_runs()
    sessions = summary["sessions_synced"]
    print(
        "agents_sync_job: checked {runs_checked} known run(s), {runs_open} open, "
        "{runs_synced} synced OK".format(**summary)
        + f"; machine-wide sessions: {'push failed' if sessions is None else sessions}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
