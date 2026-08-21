#!/usr/bin/env python3
"""factory_sync — T11 (run/stage push). Pushes `factory.py`'s stage transitions to
the platform-alpha Supabase project so the Model Factory tab can render live state.

Called by `factory.py` as a post-write hook, right after `write_report()` — see
`push_stage()`. **Fail-open** (PLAN.md §3.1 / D-PI-7 / PRRules.md rule 5): a push
failure is logged loudly and swallowed here, never raised, because the on-disk
report — not this DB projection — is what `factory.py` itself trusts as ground
truth for run state (PRRules.md rule 10). T12 (gate push/poll) is the opposite
fail direction and lives in this same module once built, precisely so the two
opposing directions are easy to compare side by side.

PII: PLAN.md §2.10 / PRRules.md rule 6 require an ALLOW-list projection of the raw
on-disk stage report before anything reaches the DB — see `SAFE_REPORT_KEYS` below.
"""

import json
import logging
import os
import posixpath
import re
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from supabase_rest import SupabaseRestError, insert, select, update, upsert

log = logging.getLogger("factory_sync")

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # emanate-tecum-workflow root — where the cloud-fleet
# refs' `origin` remote lives (see the T1-1 section at the bottom of this file).

# factory.py's own internal lowercase stage names (its `STAGES` list) do not match
# the S-code convention the migration/platform-alpha UI actually stores and renders
# (types.ts's `FACTORY_STAGE_ORDER`; `factory_gate_requests.gate`'s CHECK
# constraint). factory.py's own module docstring documents this exact mapping
# (S6g sits between S6 and S7 and consumes no integer of its own — see
# product-integration/DELTAS.md D8) — copied verbatim from there, not guessed.
# Imported, no longer restated. This dict and `factory.py`'s docstring used to be
# two copies of one contract; C0 (factory-graph-pr2) moved it to `factory_stages`
# after finding that the `--status` display had drifted from both by re-deriving
# the code positionally (finding S0-4).
from factory_stages import STAGE_CODES  # noqa: E402,F401
from factory_stages import HUMAN_GATES as HUMAN_GATE_STAGES  # noqa: E402

# The 3 human-gate stages, by their S-code (matches factory_gate_requests.gate's
# CHECK constraint: 'S2' | 'S6g' | 'S9').
GATE_CODES = {"governance": "S2", "launch": "S6g", "ship": "S9"}

# PLAN.md §2.10 / PRRules.md rule 6: an explicit ALLOW-list, never a block-list — a
# stage-report key not named here is dropped on the floor, full stop, even if it
# looks harmless. A new stage-report field is invisible in the DB projection by
# default until someone deliberately adds it here; this is what makes the T11
# negative-PII test (an unrecognized fixture key) pass without needing to predict
# every future shape of "customer content."
#
# Verified against every real report shape `factory.py` emits today (read the
# whole file): none of these keys ever carry an account name, transcript excerpt,
# dossier content, or corpus example — they are exclusively counts, tier/verdict
# strings, cost numbers, methodology prose, and repo-relative evidence paths.
# `stdout_tail`/`stderr_tail` (raw subprocess output from a `--live` export) are
# the one real leak vector in this file and are deliberately NOT allow-listed.
SAFE_REPORT_KEYS = frozenset(
    {
        "status",
        "dry_run",
        "next",
        "todo_v1",
        "exit_code",
        "stage_index",
        "recommendation",
        "checklist",
        "findings",
        "policy",
        "battery",
        "decision_request",
        "verdict_contract",
        "serving_decision",
        "projection",
        "loop",
        "train_policy",
        "drift_triggers",
        "scheduling",
    }
)


# Second-layer defense-in-depth (QA finding, 2026-07-23 rigorous local-infra
# pass — DELTAS.md D19): SAFE_REPORT_KEYS is a top-level-KEY allow-list only —
# it never inspected the free-text VALUE under an allow-listed key (e.g.
# `decision_request.recommendation`, documented as "methodology prose" but
# with nothing mechanically stopping a human-authored string there from
# embedding a real email/phone/SSN). This regex pass catches STRUCTURED PII
# shapes only. It deliberately does NOT attempt to detect an unstructured
# customer/contact NAME embedded in prose — that is an unbounded NLP problem,
# not a mechanical one; see DELTAS.md D19 for why that residual risk is
# accepted here rather than "solved," and why the real mitigation is
# `factory.py` itself never writing customer names into these fields.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_STRUCTURED_PII_PATTERNS = (
    ("[redacted-email]", _EMAIL_RE),
    ("[redacted-ssn]", _SSN_RE),
    ("[redacted-phone]", _PHONE_RE),
)


def _scrub_structured_pii(value):
    """Recursively redact structured PII shapes (email/SSN/phone) from any
    string found inside `value`, however deeply nested. Order matters: SSN
    before phone, since a bare 9-digit-with-dashes number can otherwise be
    partially eaten by the looser phone pattern first.
    """
    if isinstance(value, str):
        for placeholder, pattern in _STRUCTURED_PII_PATTERNS:
            value = pattern.sub(placeholder, value)
        return value
    if isinstance(value, dict):
        return {k: _scrub_structured_pii(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_structured_pii(v) for v in value]
    return value


def project_stage_report(report):
    """Return the PII-safe subset of a raw `factory.py` stage report — the
    allow-list projection PLAN.md §2.10 requires. Any key not in
    `SAFE_REPORT_KEYS` is dropped, regardless of its value (layer 1), then
    every surviving string value is passed through `_scrub_structured_pii`
    (layer 2) to catch structured PII that could otherwise ride through
    inside an allow-listed key's free-text value.
    """
    allow_listed = {k: v for k, v in report.items() if k in SAFE_REPORT_KEYS}
    return _scrub_structured_pii(allow_listed)


def _stream_from_cfg(cfg):
    return (cfg or {}).get("_model_stream") or "per-account"


def _run_state_path(slug, stream=None):
    path = HERE / "runs" / slug
    if stream and stream != "per-account":
        path = path / stream
    return path / ".sync_state.json"


def _load_run_state(slug, stream=None):
    path = _run_state_path(slug, stream=stream)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return {}


def _save_run_state(slug, state, stream=None):
    path = _run_state_path(slug, stream=stream)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


TERMINAL_RUN_STATUSES = {"shipped", "failed", "no_go", "abandoned"}


def get_or_create_run_id(slug, url=None, service_role_key=None, stream=None):
    """`factory_runs` has no natural unique key besides its own `id` — each
    `factory.py` invocation is a fresh, stateless process (D4/DELTAS.md), so the
    Supabase-side run id is minted once (first push for this org) and persisted
    to a local, gitignored state file (`runs/<org>/.sync_state.json`, covered by
    this directory's existing `.gitignore` — never committed) so every later
    stage push for the same run upserts the same row instead of creating a new
    one per invocation.

    **2026-07-24 fix (retrain regression — QA subagent, DELTAS.md D22):** a bare
    "mint once, reuse forever" cache doesn't distinguish "still mid-run" from
    "this org already shipped/failed and a brand-new invocation weeks later is
    a RETRAIN." Reusing the same id across that boundary was proven to (a)
    silently overwrite the finished run's `factory_runs`/`factory_run_stages`
    history in place as the retrain re-walks S1..S11, and (b) let a retrain's
    gate resolve to `approved` purely because the OLD run's already-resolved
    `factory_gate_requests` row still sits under the same `(run_id, gate)` key
    — with zero new `decide_factory_gate` call, i.e. a real fine-tune could
    sail past every human gate unreviewed. Now checks the cached run's live
    `factory_runs.status` before reusing it: a non-terminal run
    (`running`/`blocked_on_gate`) is safe to keep appending to (the common
    case — most calls land here); a terminal run means THIS invocation starts
    a new run for the same org, so a fresh id is minted and the historical
    link is recorded (`iteration`, `predecessor_run_id`) rather than silently
    dropped. Fails OPEN to "reuse the cached id" if the status check itself
    can't reach Supabase — same direction as T11's `push_stage` (a transient
    outage minting a spurious extra run would be worse than briefly risking
    the pre-fix behavior for one invocation).
    """
    state = _load_run_state(slug, stream=stream)
    if "run_id" not in state:
        # C5 (factory-graph-pr2): before minting, look for a run the PRODUCT
        # already queued for this org and adopt it. Without this, clicking Start
        # in the tab and then running the CLI produces two non-terminal rows for
        # one org — and since the tab's gate cards key off run_id, a human could
        # approve a gate on the twin that is not the one spending money.
        claimed = claim_queued_run(
            slug, url=url, service_role_key=service_role_key, stream=stream
        )
        if claimed:
            state["run_id"] = claimed["run_id"]
            state["iteration"] = claimed.get("iteration") or 1
            if claimed.get("predecessor_run_id"):
                state["predecessor_run_id"] = claimed["predecessor_run_id"]
            _save_run_state(slug, state, stream=stream)
            return state["run_id"]
        state["run_id"] = str(uuid.uuid4())
        state["iteration"] = 1
        _save_run_state(slug, state, stream=stream)
        return state["run_id"]

    try:
        rows = select(
            "factory_runs",
            params={"id": f"eq.{state['run_id']}", "select": "status,iteration", "limit": "1"},
            url=url,
            service_role_key=service_role_key,
        )
    except SupabaseRestError as exc:
        log.warning(
            "factory_sync: could not check prior run status for org=%s — reusing cached "
            "run_id (fail-open, matches push_stage's direction): %s",
            slug,
            exc,
        )
        return state["run_id"]

    if rows and rows[0].get("status") in TERMINAL_RUN_STATUSES:
        old_run_id = state["run_id"]
        old_iteration = rows[0].get("iteration") or 1
        # C5: same adoption check as the cold-start path above. This is the
        # retrain case — the prior run is finished and the product may already
        # have queued the next one from the tab, which is precisely the flow
        # "clicking start gets a retrain customer to S1" describes. Minting here
        # regardless would strand that queued row forever.
        claimed = claim_queued_run(
            slug, url=url, service_role_key=service_role_key, stream=stream
        )
        state["run_id"] = claimed["run_id"] if claimed else str(uuid.uuid4())
        state["predecessor_run_id"] = old_run_id
        state["iteration"] = (claimed.get("iteration") if claimed else None) or old_iteration + 1
        _save_run_state(slug, state, stream=stream)

    return state["run_id"]


def claim_queued_run(slug, url=None, service_role_key=None, stream=None):
    """Adopt the oldest `queued` run for `slug`, flipping it to `running`.

    Returns `{"run_id", "iteration", "predecessor_run_id"}` or `None` when there
    is nothing queued (the ordinary case — the CLI is still the usual way a run
    starts, and it queues nothing).

    **Fail-open to `None`**, matching every other read in this module: if the
    claim can't be reached, the caller mints a local id and the run proceeds. The
    cost is a stranded `queued` row someone has to look at, which is visible and
    fixable. The cost of the other direction is the CLI refusing to run because
    Supabase is unreachable — turning an optional sync layer into a hard
    dependency of the whole factory, which decision D3 exists to prevent.

    The flip to `running` is the claim, and it is why `status` is in the PATCH
    filter as well as the body: two runners racing the same queued row both send
    a PATCH filtered on `status=eq.queued`, and the loser updates zero rows and
    is told so. Postgres, not politeness, decides who wins.
    """
    try:
        params = {
            "org_slug": f"eq.{slug}",
            "status": "eq.queued",
            "select": "id,iteration,predecessor_run_id",
            "order": "started_at.asc",
            "limit": "1",
        }
        if stream is not None:
            params["model_stream"] = f"eq.{stream}"
        rows = select(
            "factory_runs",
            params=params,
            url=url,
            service_role_key=service_role_key,
        )
    except SupabaseRestError as exc:
        log.warning("factory_sync: could not check for a queued run for org=%s "
                    "— minting a local run id instead (fail-open): %s", slug, exc)
        return None

    if not rows:
        return None

    # `.get`, not `[...]`. The select asks for `id`, so its absence means the
    # response is not the shape this function assumes — and a KeyError here would
    # escape the SupabaseRestError handler above and take down the CLI, which is
    # the precise opposite of what a fail-open helper is for. An unexpected shape
    # is treated exactly like an unreachable Supabase: mint locally, log, proceed.
    run_id = (rows[0] or {}).get("id")
    if not run_id:
        log.warning("factory_sync: queued-run lookup for org=%s returned an unexpected row shape "
                    "%r — minting a local run id instead (fail-open)", slug, rows[0])
        return None

    try:
        updated = update(
            "factory_runs",
            {"id": f"eq.{run_id}", "status": "eq.queued"},
            {"status": "running"},
            url=url,
            service_role_key=service_role_key,
        )
    except SupabaseRestError as exc:
        log.warning("factory_sync: could not claim queued run %s for org=%s "
                    "— minting a local run id instead (fail-open): %s", run_id, slug, exc)
        return None

    if not updated:
        # Someone else claimed it between the select and the patch. Not an error;
        # this process just mints its own id and the other runner owns that row.
        log.warning("factory_sync: queued run %s for org=%s was claimed by another "
                    "runner first — minting a local run id instead", run_id, slug)
        return None

    log.info("factory_sync: claimed queued run %s for org=%s (started from the product)", run_id, slug)
    return {
        "run_id": run_id,
        "iteration": rows[0].get("iteration"),
        "predecessor_run_id": rows[0].get("predecessor_run_id"),
    }


def _run_status_for(stage_name, report):
    """Best-effort `factory_runs.status` derivation from a single stage's report.
    Not a full state machine (T11's scope is "upsert factory_runs", not a status
    spec) — covers the common paths the CLI actually produces today; T16's real
    dry-run is where this gets exercised end-to-end.

    **2026-07-27 (factory-graph-pr1, B1): the `no_go` arm below is new, and it
    closes a CONFIRMED-LIVE hole, not a hypothetical one.** Before this, no code
    path anywhere could produce `"no_go"` — the string appeared only inside
    `TERMINAL_RUN_STATUSES` and was unreachable. A NO-GO'd ship gate (the human
    declines, or the tab records a `reject`) made `factory.py` write
    `status: "gate_pending"`, which lands in the `blocked_on_gate` arm above:
    **non-terminal**. `get_or_create_run_id` therefore kept reusing that dead
    run's id forever, so the retrain-lineage minting added by D22
    (`Lesson_RetrainRunIdReuse`) never fired for a rejected run — meaning the one
    case where carrying predecessor context matters most (a prior failure, with
    its forensics) was exactly the case that silently didn't get it. Flagged as
    an unverified risk in `automation-graph/RETRAIN-AND-META-LEARNING.md` §1;
    verified live and fixed here. See `PRs/factory-graph-pr1/PRDebug.md`
    "Verification V6" for the reproduction.

    The ordering below is load-bearing: `no_go` is checked BEFORE the
    `gate_pending` arm so a decided rejection is never re-absorbed into
    "still waiting." Equally deliberate is what did NOT change — an
    undecided/unreachable gate still maps to `blocked_on_gate`, because
    over-correcting so any unresolved gate looked terminal would fragment every
    in-flight run into a spurious new run id on its next invocation.
    """
    status = report.get("status")
    if stage_name == "ship" and status == "no_go":
        return "no_go"
    if status == "BLOCKED_ON_GATE" or (stage_name in HUMAN_GATE_STAGES and status == "gate_pending"):
        return "blocked_on_gate"
    if status == "REFUSED":
        return "failed"
    if stage_name == "activate":
        # C2 (factory-graph-pr2): activate can now FAIL. Until S10 was wired to
        # `serve-wire` it dispatched nothing and could only ever emit its runbook,
        # so "reached activate" and "shipped" were the same fact and this arm was
        # unconditional. With a real node in the chain they have come apart, and
        # reporting a run as `shipped` because its activation stage ran — but
        # failed — would put a green terminal state on a model that never went
        # live. That is the worst direction for this particular lie: `shipped` is
        # terminal, so the run would not be retried either.
        return "failed" if status == "error" else "shipped"
    return "running"


def _run_row(cfg, slug, stage_name, report, run_id, stream=None):
    stage_code = STAGE_CODES.get(stage_name, stage_name)
    org = cfg["org"]
    champion = (cfg.get("champion") or {}).get("model_id")
    row = {
        "id": run_id,
        "org_slug": slug,
        "org_id": org.get("id"),
        "org_name": org["name"],
        "run_kind": "monthly-retrain" if champion else "first-train",
        "model_stream": stream or _stream_from_cfg(cfg),
        "status": _run_status_for(stage_name, report),
        "current_stage": stage_code,
        # factory_runs.trigger_source's CHECK admits exactly 'manual',
        # 'retrain-proposal', 'candidate-detection' (2026-07-28 migration). Only
        # 'manual' has a writer anywhere in this codebase today — S11 (retrain
        # proposals) is unimplemented and S11-lineage/candidate-detection remain
        # future work — so this is unconditionally correct for every run this
        # function ever sees, whether it originated from startFactoryRun (which
        # already writes 'manual' at insert) or a bare CLI/cloud-agent
        # invocation (which had no writer for this column at all until now).
        "trigger_source": "manual",
        # cfg is the org's static YAML pipeline config (org/training/champion/
        # gates/budget/paths) — never exported customer content — so unlike
        # `report` (project_stage_report's PII allow-list applies there for a
        # reason) it is safe to snapshot wholesale.
        "config_snapshot": cfg,
    }
    # 2026-07-24 (DELTAS.md D22): mirrors whatever get_or_create_run_id decided
    # for THIS run_id — set once, at mint time, then unchanged for the run's
    # whole lifetime; re-sending the same values on every stage push is a
    # harmless idempotent upsert, not a re-derivation.
    state = _load_run_state(slug, stream=stream)
    if state.get("run_id") == run_id:
        if state.get("iteration"):
            row["iteration"] = state["iteration"]
        if state.get("predecessor_run_id"):
            row["predecessor_run_id"] = state["predecessor_run_id"]
    cost = None
    decision_request = report.get("decision_request")
    if isinstance(decision_request, dict):
        cost = decision_request.get("cost")
    elif isinstance(report.get("projection"), dict):
        cost = report["projection"]
    if isinstance(cost, dict) and cost.get("total_projection_usd") is not None:
        row["cost_projected_usd"] = cost["total_projection_usd"]
    return row


def _stage_status_for(stage_name, report):
    """The `factory_run_stages.status` projection of one stage report — shared
    by `_stage_row` and `_stage_event_rows` so the stage row and its timeline
    event can never disagree about whether a stage passed or failed.
    """
    status = report.get("status")
    if status == "BLOCKED_ON_GATE" or (stage_name in HUMAN_GATE_STAGES and status == "gate_pending"):
        return "blocked_on_gate"
    # `no_go` is a RUN-level terminal state, not a stage-level one:
    # `factory_run_stages.status`'s CHECK constraint only allows
    # pending/running/passed/failed/blocked_on_gate/skipped (verified against the
    # migration, not assumed). The S9 stage genuinely completed — it produced a
    # decision — but the run did not ship, so `failed` is the honest projection
    # of the available values. Without this arm a NO-GO fell through to `passed`,
    # which would render a rejected ship gate as a green stage in the tab.
    #
    # `empty` is the S3 zero-dossier refusal (stage_export: "REFUSING to mark
    # ok"). Before 2026-07-31 it fell through to `passed`, so the one status the
    # export stage invented specifically to say "this did NOT work" rendered as
    # a green stage in the tab (stress-test finding F-03-2) — the DB projection
    # was quietly contradicting the stage's own refusal.
    if status in ("REFUSED", "error", "no_go", "empty"):
        return "failed"
    # `dispatched` is S7 (train) firing a REAL Fireworks fine-tune launch via
    # `_dispatch_launch_task` — a Trigger.dev task that runs asynchronously for
    # HOURS, entirely outside this process. `stage_train` writes this report the
    # instant the launch call succeeds, not when training finishes. Before this
    # arm (added 2026-08-21), "train"/"dispatched" fell through to `passed`, so
    # the tab showed S7 as a completed, green stage the moment the launch was
    # fired — hours before training even started, and still green even if the
    # launch later failed outright. `running` is the honest projection: the
    # stage genuinely started and has not finished.
    if stage_name == "train" and status == "dispatched":
        return "running"
    return "passed"


def _stage_row(run_id, stage_name, report, started_at=None):
    stage_code = STAGE_CODES.get(stage_name, stage_name)
    row = {
        "run_id": run_id,
        "stage": stage_code,
        "attempt": 1,
        "status": _stage_status_for(stage_name, report),
        "report": project_stage_report(report),
        # push_stage runs from write_report, at the moment the stage's report is
        # written — i.e. the stage is finishing right now. started_at is the
        # caller's own pre-dispatch timestamp (factory.py's _STAGE_STARTED_AT),
        # already threaded through push_stage for the stage_started event; this
        # is the same value reaching the reserved factory_run_stages columns.
        "finished_at": _now_iso(),
    }
    if started_at:
        row["started_at"] = started_at
    if report.get("exit_code") is not None:
        row["exit_code"] = report["exit_code"]
    verdict = report.get("findings", {}).get("verifier_verdict") if isinstance(report.get("findings"), dict) else None
    if verdict in ("pass", "fail", "suspect"):
        row["verifier_verdict"] = verdict
    return row


# =============================================================================
# Timeline events (stress-test 2026-07-31, EVENT-COVERAGE). 8 of the 12
# `factory_run_events.kind` values had NO live emitter (data-model/
# factory_run_events.md "Known gaps") — the tab's Timeline showed agent moments
# but never a stage transition, gate request, gate decision, or cost. The
# builders below close the ones with an HONEST source: `push_stage` already
# holds the exact stage transition, and the gate machinery already holds the
# request/decision, at the moment each would need to write its event row.
#
# Idempotency is uuid5, the same discipline every out-of-band events writer
# already uses (backfill_ptc_history, audit/project_findings, ab_shadow_sync):
# a deterministic `id` + `on_conflict="id"` upsert means re-running a stage
# re-asserts the same rows instead of duplicating them.
# =============================================================================

# The uuid5 namespace for deterministic `factory_run_events.id`s. Deliberately
# the SAME constant as `backfill_ptc_history.NAMESPACE` (not imported — the core
# sync module must not depend on a one-time backfill script; a drift-guard test
# in tests/test_run_events.py asserts the two stay equal). Key strings are
# prefixed (`stage:`/`gate:`/`cost:`/`agent:`) so they can never collide with
# the backfill's `factory_run_events:<name>:<kind>` keys.
EVENT_NAMESPACE = uuid.UUID("6f6e6520-6d6f-6465-6c20-666163746f72")


def _event_id(name):
    return str(uuid.uuid5(EVENT_NAMESPACE, f"factory_run_events:{name}"))


def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _stage_event_detail(report):
    """The counts-and-verdicts-only `detail` for a stage terminal event —
    an explicit construction, never a report passthrough (SAFE_REPORT_KEYS'
    philosophy applied to events): pointers and counts, no blobs.
    """
    detail = {}
    if isinstance(report.get("exported"), int):
        detail["exported"] = report["exported"]
    nodes = report.get("nodes")
    if isinstance(nodes, list) and nodes:
        detail["nodes_total"] = len(nodes)
        detail["nodes_ok"] = sum(1 for n in nodes if isinstance(n, dict) and n.get("ok"))
    findings = report.get("findings")
    if isinstance(findings, dict) and findings.get("verifier_verdict") in ("pass", "fail", "suspect"):
        detail["verifier_verdict"] = findings["verifier_verdict"]
    if report.get("exit_code") is not None:
        detail["exit_code"] = report["exit_code"]
    if report.get("dry_run"):
        detail["dry_run"] = True
    return detail


def _stage_event_rows(run_id, stage_name, report, started_at=None):
    """The `factory_run_events` rows one stage push honestly supports:

    - `stage_started` — `ts` is the TRUE start time when the caller recorded one
      (factory.py stamps it just before dispatching the stage handler), else the
      report's own `generated_at`. Research/04 flagged `ts` = sync clock as the
      blocker for the v2 time-scrubber; new events carry event time explicitly.
    - `stage_passed` / `stage_failed` — `ts` = the report's `generated_at`, which
      IS the moment the stage finished (write_report stamps it at write time).
      One shared id for the terminal event regardless of outcome, so a re-run
      that flips failed->passed REPLACES the event: a stage cannot both have
      passed and failed at the same attempt, and two rows would say it did.
    - `cost_recorded` — only when the report carries a real projection
      (`decision_request.cost.total_projection_usd`, i.e. S6); the numbers are
      the honest source, computed in that stage at that moment.

    A `BLOCKED_ON_GATE` report produces NO events: the stage never ran (main()
    refused it before the handler), so `stage_started` would be a lie, and the
    gate's own `gate_requested` event already covers the wait. A gate stage
    sitting `gate_pending` gets `stage_started` only — it genuinely ran, and its
    outcome does not exist yet.
    """
    if report.get("status") == "BLOCKED_ON_GATE":
        return []
    stage_code = STAGE_CODES.get(stage_name, stage_name)
    attempt = 1
    generated_at = report.get("generated_at") or _now_iso()
    ref = {"stage": stage_code, "attempt": attempt}

    rows = [
        {
            "id": _event_id(f"stage:{run_id}:{stage_code}:{attempt}:started"),
            "run_id": run_id,
            "ts": _iso(started_at) if started_at else generated_at,
            "kind": "stage_started",
            "headline": f"{stage_code} {stage_name} started",
            "ref": ref,
            "detail": {"dry_run": True} if report.get("dry_run") else {},
        }
    ]

    stage_status = _stage_status_for(stage_name, report)
    if stage_status in ("passed", "failed"):
        outcome = "passed" if stage_status == "passed" else f"FAILED ({report.get('status')})"
        rows.append(
            {
                "id": _event_id(f"stage:{run_id}:{stage_code}:{attempt}:finished"),
                "run_id": run_id,
                "ts": generated_at,
                "kind": f"stage_{stage_status}",
                "headline": f"{stage_code} {stage_name} {outcome}",
                "ref": ref,
                "detail": _stage_event_detail(report),
            }
        )

    cost = None
    if isinstance(report.get("decision_request"), dict):
        cost = report["decision_request"].get("cost")
    if isinstance(cost, dict) and cost.get("total_projection_usd") is not None:
        rows.append(
            {
                "id": _event_id(f"cost:{run_id}:{stage_code}:projected"),
                "run_id": run_id,
                "ts": generated_at,
                "kind": "cost_recorded",
                "headline": f"{stage_code} {stage_name}: cost projected ${cost['total_projection_usd']}",
                "ref": {"stage": stage_code, "cost_type": "projection"},
                "detail": {k: v for k, v in cost.items() if isinstance(v, (int, float))},
            }
        )
    return [_scrub_event_row(row) for row in rows]


def _scrub_event_row(row):
    """Value-level PII scrub of an event row's CONTENT fields only. The
    identifier columns (`id`, `run_id`, `ts`, `kind`) pass through untouched —
    `_digest_row`'s docstring documents the proven failure mode: `_PHONE_RE`
    can match inside a UUID's digit groups, and a corrupted uuid makes
    PostgREST reject the write, which a fail-open push then swallows silently.
    """
    return {
        **row,
        "headline": _scrub_structured_pii(row.get("headline")),
        "ref": _scrub_structured_pii(row.get("ref") or {}),
        "detail": _scrub_structured_pii(row.get("detail") or {}),
    }


def push_stage(
    cfg, slug, stage_name, report, url=None, service_role_key=None,
    started_at=None, stream=None,
):
    """T11: push one stage transition (a `factory_runs` upsert + a
    `factory_run_stages` upsert + the matching `factory_run_events` rows) to
    Supabase. **Fail-open**: any `SupabaseRestError`
    (transport failure, non-2xx response) is caught, logged, and swallowed — this
    function never raises, so a Supabase outage can never block `factory.py` from
    continuing (D-PI-7). Returns `True` on a successful push, `False` on a
    swallowed failure (informational only — callers must not branch pipeline
    behavior on this return value, only logging/metrics may).

    `started_at` is the stage's true start time when the caller has one
    (factory.py records it just before dispatching the handler) — see
    `_stage_event_rows` for how it reaches the `stage_started` event's `ts`.
    The events upsert runs AFTER the `factory_runs` upsert on purpose: the
    events table's `run_id` FK needs the run row to exist first.
    """
    try:
        effective_stream = stream or (
            _stream_from_cfg(cfg) if cfg.get("_model_stream") else None
        )
        run_id = get_or_create_run_id(
            slug, url=url, service_role_key=service_role_key, stream=effective_stream
        )
        run_row = _run_row(
            cfg, slug, stage_name, report, run_id, stream=effective_stream
        )
        upsert("factory_runs", [run_row], on_conflict="id", url=url, service_role_key=service_role_key)
        stage_row = _stage_row(run_id, stage_name, report, started_at=started_at)
        upsert(
            "factory_run_stages",
            [stage_row],
            on_conflict="run_id,stage,attempt",
            url=url,
            service_role_key=service_role_key,
        )
        event_rows = _stage_event_rows(run_id, stage_name, report, started_at=started_at)
        if event_rows:
            upsert("factory_run_events", event_rows, on_conflict="id", url=url, service_role_key=service_role_key)
        return True
    except SupabaseRestError as exc:
        log.warning(
            "factory_sync: push failed for org=%s stage=%s — continuing anyway (fail-open, D-PI-7): %s",
            slug,
            stage_name,
            exc,
        )
        return False


# =============================================================================
# C1b (factory-graph-pr2) — the iteration-digest projection.
#
# Decision D5 left the digest on disk under `runs/`, which is gitignored, and
# recorded the consequence honestly: a retrain on a different machine finds no
# digest and classifies itself `first_run`, losing the predecessor's corpus
# recipe, hyperparameters and — most valuable — the human's reason for the last
# ship decision. It does not fail; it just gets dumber, silently.
#
# These two functions are that gap closed. Same fail-open direction as
# `push_stage` (a projection is not the record of truth), but with one deliberate
# difference: the READ logs loudly on failure. The whole defect being fixed is
# that a run silently loses its history, so a fallback that quietly returns
# `None` would reproduce it one layer down.
# =============================================================================


def _digest_row(digest):
    """Project a digest into a `factory_run_digests` row.

    PII: the digest is already designed to carry no customer content — shape
    only (recipe, doses, caps, seed, hyperparameters, decision). That is the
    stated reason for its field selection, not a side effect. `_scrub_structured_pii`
    is applied anyway as the value-level second layer every payload reaching a
    `factory_*` table gets (PRRules rule 6, `Lesson_PIIAllowlistValueLevelLeak`):
    `ship.no_go_reason` is free text a human typed, which is exactly where an
    email or phone number arrives by accident.

    ## Why the scrub is applied to the content sections and NOT the whole row

    Scrubbing the identifier columns corrupts them. `_PHONE_RE` matches
    `\\d{3}[-.\\s]?\\d{3}[-.\\s]?\\d{4}`, and a UUID whose digit groups happen to
    contain no hex letters satisfies that — so a `run_id` can come out the other
    side as `dddddddd-0000-0000-0000-00[redacted-phone]`. Caught by
    `test_the_projection_round_trips_through_the_table_shape` while writing C1b,
    on a fixture UUID; a real one is only accidentally safe because it usually
    contains letters.

    The failure mode is nasty precisely because it is quiet: an invalid uuid makes
    PostgREST reject the insert, `push_iteration_digest` is fail-open, so the
    error is logged-and-swallowed and the digest is simply never projected. D5
    would stay open while looking closed.

    So: identifiers pass through untouched (`run_id`, `predecessor_run_id`,
    `iteration`, and `org_slug` — a config slug like `grand-steel`, already stored
    unscrubbed in `factory_runs.org_slug`), and the scrub is applied to exactly
    the three sections that carry content.
    """
    return {
        "run_id": digest.get("run_id"),
        "org_slug": digest.get("org_slug"),
        "iteration": digest.get("iteration") or 1,
        "predecessor_run_id": digest.get("predecessor_run_id"),
        "corpus": _scrub_structured_pii(digest.get("corpus") or {}),
        "training": _scrub_structured_pii(digest.get("training") or {}),
        "ship": _scrub_structured_pii(digest.get("ship") or {}),
    }


def push_iteration_digest(slug, digest, url=None, service_role_key=None):
    """Mirror one iteration digest into `factory_run_digests`. **Fail-open** —
    never raises, so a Supabase outage cannot fail a run that shipped.

    Returns True on a successful push, False otherwise (informational only —
    callers must not branch pipeline behaviour on it).
    """
    if not digest or not digest.get("run_id"):
        # No Supabase run id means no sync has ever succeeded for this run, so
        # there is no `factory_runs` row for the FK to point at. Skipped rather
        # than forced: inserting a digest for a run the DB has never heard of
        # would fail the FK anyway, and inventing a run row here would make this
        # function a second writer of `factory_runs` (it is not one).
        log.warning(
            "factory_sync: no run_id on the iteration digest for org=%s — digest NOT projected "
            "to the DB, so a retrain on another machine will not find it (decision D5)",
            slug,
        )
        return False
    try:
        upsert(
            "factory_run_digests",
            [_digest_row(digest)],
            on_conflict="run_id,iteration",
            url=url,
            service_role_key=service_role_key,
        )
        return True
    except SupabaseRestError as exc:
        log.warning(
            "factory_sync: iteration-digest push failed for org=%s — continuing anyway "
            "(fail-open, D-PI-7): %s",
            slug,
            exc,
        )
        return False


def fetch_predecessor_digest(
    slug, current_iteration, url=None, service_role_key=None, stream=None
):
    """The latest projected digest for `slug` strictly before `current_iteration`,
    or None.

    This is the D5 fallback: it only runs when no local digest file exists, which
    in practice means "this run is happening on a different machine than the one
    that produced its predecessor."

    Returns None both when there genuinely is no predecessor and when the lookup
    failed — but those two cases log differently on purpose. A run must never be
    blocked by an unreachable projection (so: no raise), and equally must never
    lose its predecessor without saying so (so: a warning, not silence). Silence
    is the bug this exists to fix.
    """
    try:
        if stream is None:
            rows = select(
                "factory_run_digests",
                params={
                    "org_slug": f"eq.{slug}",
                    "iteration": f"lt.{int(current_iteration)}",
                    "order": "iteration.desc",
                    "limit": "1",
                },
                url=url,
                service_role_key=service_role_key,
            )
        else:
            predecessors = select(
                "factory_runs",
                params={
                    "org_slug": f"eq.{slug}",
                    "model_stream": f"eq.{stream}",
                    "iteration": f"lt.{int(current_iteration)}",
                    "select": "id",
                    "order": "iteration.desc",
                    "limit": "1",
                },
                url=url,
                service_role_key=service_role_key,
            )
            rows = (
                select(
                    "factory_run_digests",
                    params={"run_id": f"eq.{predecessors[0]['id']}", "limit": "1"},
                    url=url,
                    service_role_key=service_role_key,
                )
                if predecessors
                else []
            )
    except SupabaseRestError as exc:
        log.warning(
            "factory_sync: could not read the predecessor digest for org=%s from the DB — "
            "this run will proceed WITHOUT predecessor context and may repeat a mistake the "
            "previous iteration already paid for: %s",
            slug,
            exc,
        )
        return None
    if not rows:
        return None
    row = rows[0]
    # Rebuilt into the on-disk digest's shape, so callers cannot tell which
    # source answered. A read path that returns two different shapes depending on
    # where the data came from is a bug waiting for the less-tested branch.
    return {
        "org_slug": row.get("org_slug"),
        "iteration": row.get("iteration"),
        "run_id": row.get("run_id"),
        "predecessor_run_id": row.get("predecessor_run_id"),
        "corpus": row.get("corpus") or {},
        "training": row.get("training") or {},
        "ship": row.get("ship") or {},
        # Provenance, so a debugging session can tell the DB answered rather than
        # wondering why the file is missing.
        "_source": "db_projection",
    }


# =============================================================================
# T12 — gate push + poll. The opposite fail direction from T11's push_stage
# above: a Supabase hiccup here must NEVER be read as "approved" (PRRules.md
# rule 5 / PLAN.md §3.2's fail-closed). `factory.py` is a one-shot CLI with no
# daemon (DELTAS.md D4) — "poll" here means "check once, this invocation," not
# a background loop; re-running the CLI later is how polling actually happens
# in v0.5's manual-invocation world.
# =============================================================================


def push_gate_request(
    cfg, slug, stage_name, decision_request, url=None, service_role_key=None,
    stream=None,
):
    """Idempotently ensure a `factory_gate_requests` row exists for this run+gate.
    If one already exists (pending OR resolved), this is a no-op — re-pushing an
    identical `decision_request` on every gate-stage re-invocation would just be
    noise, and T1's partial unique index only allows one *pending* row per
    (run_id, gate) anyway (a PARTIAL index, so PostgREST's `on_conflict` upsert
    can't target it directly — this checks first via `select` instead, see
    `supabase_rest.insert`'s docstring). Raises `SupabaseRestError` on any
    failure — deliberately NOT swallowed here, unlike T11's push_stage; the
    caller (`resolve_gate`) is what turns a failure into a fail-closed "not
    approved," not this function silently no-op'ing.
    """
    effective_stream = stream or (
        _stream_from_cfg(cfg) if cfg.get("_model_stream") else None
    )
    run_id = get_or_create_run_id(
        slug, url=url, service_role_key=service_role_key, stream=effective_stream
    )
    gate_code = GATE_CODES.get(stage_name, stage_name)
    existing = select(
        "factory_gate_requests",
        params={
            "run_id": f"eq.{run_id}",
            "gate": f"eq.{gate_code}",
            "select": "id,status",
            "order": "requested_at.desc",
            "limit": "1",
        },
        url=url,
        service_role_key=service_role_key,
    )
    if existing:
        return existing[0]
    row = {"run_id": run_id, "gate": gate_code, "decision_request": decision_request}
    try:
        result = insert("factory_gate_requests", [row], url=url, service_role_key=service_role_key)
    except SupabaseRestError as exc:
        # QA finding (DELTAS.md D19): the select-then-insert above is NOT
        # atomic, so a genuine concurrent race can lose to the DB's own
        # partial unique index (23505 duplicate key) here even though this
        # function's own docstring promises an idempotent no-op. Data
        # integrity always held (the index itself prevents two rows) — this
        # just re-selects and returns the winner's row instead of letting the
        # loser's insert failure propagate, so every caller (not just
        # `resolve_gate`, which happened to paper over this by catching
        # `SupabaseRestError` anyway) gets the promised graceful no-op.
        if "duplicate key" in str(exc).lower() or "23505" in str(exc):
            existing = select(
                "factory_gate_requests",
                params={
                    "run_id": f"eq.{run_id}",
                    "gate": f"eq.{gate_code}",
                    "select": "id,status",
                    "order": "requested_at.desc",
                    "limit": "1",
                },
                url=url,
                service_role_key=service_role_key,
            )
            if existing:
                return existing[0]
        raise
    # A brand-new request row was just created — the one moment `gate_requested`
    # is TRUE (the idempotent no-op paths above return before reaching here, so
    # re-invoking a pending gate never re-announces it). Emitted fail-OPEN in an
    # otherwise fail-CLOSED function, deliberately: the event is timeline
    # telemetry, and a telemetry hiccup must not read as "gate push failed"
    # (which the caller would fail-closed into "not approved").
    try:
        upsert(
            "factory_run_events",
            [
                _scrub_event_row(
                    {
                        "id": _event_id(f"gate:{run_id}:{gate_code}:requested"),
                        "run_id": run_id,
                        "ts": _now_iso(),
                        "kind": "gate_requested",
                        "headline": f"{gate_code} {stage_name} gate requested"
                        + (f" — recommendation: {decision_request.get('recommendation')}"
                           if isinstance(decision_request, dict) and decision_request.get("recommendation") else ""),
                        "ref": {"gate": gate_code, "stage": gate_code},
                        "detail": {},
                    }
                )
            ],
            on_conflict="id",
            url=url,
            service_role_key=service_role_key,
        )
    except SupabaseRestError as exc:
        log.warning("factory_sync: gate_requested event push failed for org=%s gate=%s "
                    "— gate request itself landed, continuing (fail-open): %s", slug, gate_code, exc)
    return result[0] if result else {"status": "pending"}


def push_gate_decided(
    slug, stage_name, status, actor=None, decided_at=None, url=None,
    service_role_key=None, stream=None,
):
    """Emit the `gate_decided` timeline event for a RESOLVED gate — `status` is
    factory.py's own gate-stage vocabulary (`approved` / `no_go` / `REFUSED`);
    anything else (pending, unknown) is not a decision and is skipped.

    **Fail-open** (telemetry, T11's direction — the decision itself is already
    durably recorded in `approvals/<gate>.json` + `factory_gate_decisions` +
    the decision ledger; this row is the Timeline's rendering of it). The run
    id comes from the cached sync state ONLY, never minted — same rule as
    `_run_id_for_decisions`: a logging call must not trigger run-lineage
    semantics. Returns True only when the event row landed.

    `ts` is `decided_at` when the caller knows the true decision time (a tab
    rejection carries `factory_gate_decisions.decided_at`); otherwise now —
    which IS the decision moment for `--approved`/policy/REFUSED resolutions,
    since those are decided inside this very invocation.

    Idempotent per (run, gate): a gate that is later re-decided (REFUSED config
    fixed, then approved) REPLACES its event rather than accumulating
    contradictory decisions — matching the single `approvals/<gate>.json` file
    that is the on-disk record of the same fact.
    """
    decision = {"approved": "approved", "no_go": "rejected", "REFUSED": "refused"}.get(status)
    if decision is None:
        return False
    run_id = _load_run_state(slug, stream=stream).get("run_id")
    if not run_id:
        return False
    gate_code = GATE_CODES.get(stage_name, stage_name)
    row = _scrub_event_row(
        {
            "id": _event_id(f"gate:{run_id}:{gate_code}:decided"),
            "run_id": run_id,
            "ts": _iso(decided_at) if decided_at else _now_iso(),
            "kind": "gate_decided",
            "headline": f"{gate_code} {stage_name} gate {decision}" + (f" ({actor})" if actor else ""),
            "ref": {"gate": gate_code, "decision": decision, **({"actor": actor} if actor else {})},
            "detail": {},
        }
    )
    try:
        upsert("factory_run_events", [row], on_conflict="id", url=url, service_role_key=service_role_key)
        return True
    except SupabaseRestError as exc:
        log.warning(
            "factory_sync: gate_decided event push failed for org=%s gate=%s — continuing anyway "
            "(fail-open): %s",
            slug,
            gate_code,
            exc,
        )
        return False


def poll_gate_status(
    slug, stage_name, url=None, service_role_key=None, stream=None
):
    """One-shot check of this run+gate's current `factory_gate_requests.status`.
    Returns `"pending"` / `"approved"` / `"rejected"` / `"withdrawn"`, or `None`
    if no request row exists yet OR the check itself failed (transport/HTTP
    error) — both are treated identically as "not approved" by the caller,
    which is the fail-closed direction this whole function exists to prove.
    """
    run_id = get_or_create_run_id(
        slug, url=url, service_role_key=service_role_key, stream=stream
    )
    gate_code = GATE_CODES.get(stage_name, stage_name)
    try:
        rows = select(
            "factory_gate_requests",
            params={
                "run_id": f"eq.{run_id}",
                "gate": f"eq.{gate_code}",
                "select": "status",
                "order": "requested_at.desc",
                "limit": "1",
            },
            url=url,
            service_role_key=service_role_key,
        )
    except SupabaseRestError as exc:
        log.warning(
            "factory_sync: gate poll failed for org=%s gate=%s — fail-closed, treating as NOT approved: %s",
            slug,
            gate_code,
            exc,
        )
        return None
    if not rows:
        return None
    return rows[0].get("status")


def resolve_gate_status(cfg, slug, stage_name, decision_request, url=None, service_role_key=None):
    """Push (idempotently) this gate's `decision_request`, then return its
    LITERAL current status — `"pending"` / `"approved"` / `"rejected"` /
    `"withdrawn"` — or `None` when the status is genuinely **unknown** (no
    request row yet, or the push/poll itself failed).

    Added 2026-07-27 (factory-graph-pr1, B1). `resolve_gate` below collapses
    every non-approved outcome into one `False`, which is exactly right for
    deciding "may this run advance?" but throws away the distinction B1 needs:
    **a decided rejection is terminal; an undecided gate is not.** Callers that
    must tell those apart use this function; callers that only need the
    advance/don't-advance bit keep using `resolve_gate`, which is now a thin
    wrapper over this so the two can never drift apart.

    `None` (unknown) is deliberately NOT collapsed into `"rejected"`. A transient
    Supabase outage marking a live run terminal would mint a spurious new run id
    on the next invocation and orphan the real one — the fail-closed rule is
    "never advance on doubt," not "declare failure on doubt."
    """
    stream = _stream_from_cfg(cfg) if cfg.get("_model_stream") else None
    try:
        push_gate_request(
            cfg, slug, stage_name, decision_request, url=url,
            service_role_key=service_role_key, stream=stream,
        )
    except SupabaseRestError as exc:
        log.warning(
            "factory_sync: gate push failed for org=%s stage=%s — fail-closed, treating as NOT approved: %s",
            slug,
            stage_name,
            exc,
        )
        return None
    return poll_gate_status(
        slug, stage_name, url=url, service_role_key=service_role_key, stream=stream
    )


def resolve_gate(cfg, slug, stage_name, decision_request, url=None, service_role_key=None):
    """T12's entry point, called by `factory.py`'s 3 human-gate stage handlers
    ONLY when `--approved` was not passed (that flag remains a full local bypass
    per D4 — this function is never even called in that path, which is what
    makes the `--approved`-with-unreachable-sync regression test meaningful).

    Pushes (idempotent) this gate's `decision_request`, then polls its status
    once. Returns `True` ONLY on a literal `"approved"` status. Every other
    outcome — `"pending"`, `"rejected"`, `"withdrawn"`, no row yet, or ANY
    transport/HTTP error on either call — returns `False`. This is the opposite
    fail direction from T11's `push_stage`, on purpose: a Supabase outage must
    block a real spend/launch/ship decision, never silently wave it through.
    """
    return (
        resolve_gate_status(
            cfg, slug, stage_name, decision_request, url=url, service_role_key=service_role_key
        )
        == "approved"
    )


def gate_rejection_note(
    slug, stage_name, url=None, service_role_key=None, stream=None
):
    """Best-effort retrieval of the human's free-text rationale for the most
    recent REJECT on this run+gate: `{"note", "decided_by_email", "decided_at"}`,
    or `None` if unavailable.

    `factory_gate_decisions.note` is **the only place a human's free-text
    override/rejection rationale lives** anywhere in this system
    (`RETRAIN-AND-META-LEARNING.md` §1) — it is the single highest-signal input
    to a retrain's context pack, which is why it is worth a second round-trip.

    Purely informational and always fail-soft: any error returns `None`. Nothing
    about gate enforcement or run status depends on this succeeding — B1's
    terminal-status decision is made from `resolve_gate_status` alone.
    """
    try:
        run_id = get_or_create_run_id(
            slug, url=url, service_role_key=service_role_key, stream=stream
        )
        gate_code = GATE_CODES.get(stage_name, stage_name)
        requests = select(
            "factory_gate_requests",
            params={
                "run_id": f"eq.{run_id}",
                "gate": f"eq.{gate_code}",
                "select": "id",
                "order": "requested_at.desc",
                "limit": "1",
            },
            url=url,
            service_role_key=service_role_key,
        )
        if not requests:
            return None
        decisions = select(
            "factory_gate_decisions",
            params={
                "gate_request_id": f"eq.{requests[0]['id']}",
                "decision": "eq.reject",
                "select": "note,decided_by_email,decided_at",
                "order": "decided_at.desc",
                "limit": "1",
            },
            url=url,
            service_role_key=service_role_key,
        )
        return decisions[0] if decisions else None
    except Exception as exc:  # noqa: BLE001 - deliberate: informational only, never blocking
        log.warning("factory_sync: could not read rejection note for org=%s gate=%s: %s", slug, stage_name, exc)
        return None


# =============================================================================
# T13 — agents/events projection. Back to T11's fail-open direction (this is
# telemetry — "who's working on this run" — not a human-approval gate, so
# PRRules.md rule 5's default fail-open applies, not T12's fail-closed).
#
# Unlike T11/T12, this has no live call site inside factory.py's stage
# handlers: `heartbeat/STATE.md` and `ledger/*.jsonl` are machine-wide,
# updated by Claude Code/Cursor hooks, not by factory.py itself — there is no
# stage transition to hang this off of. Per D4/PLAN.md §3 ("v1 needs no
# scheduled daemon"), this stays a pure, standalone, testable projection
# (`project_agents`) plus one push function (`push_agents`) a future
# scheduled sync job can call — not wired into any CLI path in this PR.
# =============================================================================

# Best-effort role classification: heartbeat's real "Active Sessions" table
# (read directly from heartbeat/STATE.md) has no `role`/`title` columns at
# all — only session/goal/allowed_paths/risk/status/seq/last_update. The
# richer role taxonomy PLAN.md §2.5 describes (coordinator/builder/reader/
# expert-lens/...) lives in AGENT-CATALOG's AgentSpecs, a separate system
# with no live join to a running heartbeat session today. `factory_agents.role`
# has no CHECK constraint (verified against the migration), so this is a
# best-effort default, not a guessed-but-enforced value — see DELTAS.md D16.
RISK_TO_ROLE = {
    "migration": "builder",
    "code": "builder",
    "read-only": "reader",
}

# Allow-list of internal, non-customer-identifying repo-relative path
# prefixes (mirrors T11's SAFE_REPORT_KEYS allow-list philosophy, D14) — any
# `files_touched` path outside these is assumed to potentially embed a
# customer/account name (PLAN.md §9.1's "any path embedding a customer name
# is redacted to its role") and is redacted to a generic placeholder rather
# than passed through. An allow-list, not a name-list, because there is no
# canonical customer-name registry this module could check against.
SAFE_PATH_PREFIXES = (
    "factory-automation/",
    "fine-tuning/factory-product/",
    "PRs/",
    "heartbeat/",
    "ledger/",
    "handoffs/",
    "memory/",
    "scripts/",
    "coding/",
    "company-brain/3-execution/",
    "supabase/",
    "agent-infrastructure-sweep/",
    ".claude/",
)

# `platform-alpha` gets its own boundary rule instead of a plain
# SAFE_PATH_PREFIXES string entry (QA finding, 2026-07-23 — DELTAS.md D19):
# this repo's own real convention is worktrees named `platform-alpha-<slug>`
# (a HYPHEN separator, e.g. `platform-alpha-model-factory-v1`,
# `platform-alpha-v5`), not just a `/` subdirectory. A bare
# `str.startswith("platform-alpha")` with no boundary check at all matched
# ANY string merely sharing that 14-character prefix, with no separator
# required — this regex at least requires a real `/`, `-`, or end-of-string
# right after "platform-alpha".
#
# KNOWN RESIDUAL GAP (flagged, not fully closed here — DELTAS.md D19/F7):
# because real worktree slugs are free text, this cannot mechanically tell a
# legitimate worktree (`platform-alpha-model-factory-v1/`) apart from a
# directory deliberately named to look like one
# (`platform-alpha-jane-doe-account-export/`) — both match the same shape.
# Fully closing that would need a real worktree-name registry (e.g.
# cross-checked against `git worktree list` or `PROJECTS.md`) to validate the
# slug against, which is a bigger design change than this bounded fix and is
# NOT built here — see DELTAS.md for why this is an accepted residual risk
# rather than a fix, and why breaking legitimate worktree paths outright
# (redacting every real PR's `files_touched`) would be a worse regression.
_PLATFORM_ALPHA_RE = re.compile(r"^platform-alpha([/-]|$)")

_REDACTED_PATH = "[external path — redacted]"


def _extract_section(markdown, heading):
    """Return the raw text between a `## <heading>` line and the next `## `
    heading line (or EOF) in a markdown document.
    """
    lines = markdown.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == f"## {heading}":
            start = i + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    return "\n".join(lines[start:end])


def parse_active_sessions(heartbeat_md):
    """Parse heartbeat/STATE.md's `## Active Sessions` table (format v2,
    2026-07-03) into row dicts keyed by the table's own header cells
    (session/goal/allowed_paths/risk/status/seq/last_update). Skips the
    header row, the `|---|---|` separator row, and the prose paragraph the
    real file always has above the table — anything that isn't a `|`-framed
    line is ignored.
    """
    rows = []
    header = None
    for line in _extract_section(heartbeat_md, "Active Sessions").splitlines():
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if all(set(c) <= {"-", " ", ":"} for c in cells):
            continue
        rows.append(dict(zip(header, cells)))
    return rows


def _session_matches_run(session_row, org_slug):
    """D7's best-effort heartbeat->run correlation: a session belongs to this
    run if its goal or allowed_paths text mentions the run's org_slug —
    there is no run_id column in heartbeat/STATE.md's real schema today.

    `org_slug=None` means "do not correlate at all" — every session matches. That
    is the machine-wide projection A4 (factory-observability-v1) needs, and it is
    expressed here rather than by the caller skipping the filter so that there is
    exactly one place that decides what "belongs to a run" means.
    """
    if org_slug is None:
        return True
    haystack = f"{session_row.get('goal', '')} {session_row.get('allowed_paths', '')}".lower()
    return org_slug.lower() in haystack


def _split_allowed_paths(raw):
    return [p.strip() for p in raw.split(",") if p.strip()]


def _redact_path(path):
    """Best-effort allow-list redaction (module docstring: "an allow-list, not
    a name-list"). Normalizes the path first — QA finding, DELTAS.md D19: a
    raw `str.startswith(prefix)` check with no normalization let a `../`
    sequence that LEXICALLY starts with a safe prefix but actually resolves
    outside it (e.g. escaping `factory-automation/` into a sibling directory)
    through unredacted. `posixpath.normpath` collapses `a/../b` -> `b`; if the
    result still starts with `..` (or is absolute), it has escaped upward out
    of any safe prefix entirely regardless of what it looked like before
    normalization, and is treated as unsafe.
    """
    normalized = posixpath.normpath(path)
    if normalized.startswith("..") or normalized.startswith("/"):
        return _REDACTED_PATH
    if _PLATFORM_ALPHA_RE.match(normalized):
        return path
    if any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in SAFE_PATH_PREFIXES):
        return path
    return _REDACTED_PATH


def _parse_ledger_lines(lines):
    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def _aggregate_ledger(events, ledger_session_id):
    """Aggregate this session's `tool` events into (files_touched, commands_run).

    Verified against real `ledger/*.jsonl` data: `tool` events carry `session_id`
    + `files` (3374/3374 of them do).

    CORRECTED 2026-07-28 (PR-A finding FA-3): this docstring used to claim
    `tier1`/`tier2` rows "carry no `session_id` at all", and that was wrong in
    both directions. `tier2` rows carry `label`, `cost_usd` AND `session_id`
    (135/135 real ones); `tier1` rows carry neither `cost_usd` nor `session_id`,
    correctly, since Tier 1 is local Ollama at $0. The real blocker for a
    per-agent cost is on the HEARTBEAT side — a heartbeat session row has no
    ledger `session_id`, so there is no key to join *from*. That still holds for
    interactive sessions, which is why they keep `cost_usd: None`. Dispatched
    node-agents are matched by label instead and do get a real cost.
    """
    if not ledger_session_id:
        return [], 0
    files = []
    commands = 0
    for ev in events:
        if ev.get("session_id") != ledger_session_id or ev.get("event") != "tool":
            continue
        commands += 1
        for f in ev.get("files") or []:
            redacted = _redact_path(f)
            if redacted not in files:
                files.append(redacted)
    return files, commands


def project_agents(heartbeat_md, run_id, org_slug, ledger_lines=None, session_id_map=None, final_responses=None):
    """T13: project heartbeat's Active Sessions (filtered to this run) plus
    ledger tool-call aggregates into `factory_agents` row dicts and
    `agent_spawned`/`agent_finished` `factory_run_events` row dicts.

    `session_id_map` (heartbeat session label -> ledger `session_id`) and
    `final_responses` (heartbeat session label -> closing headline text) are
    both explicit, optional overlays — NOT parsed automatically. Verified
    against real ledger data (DELTAS.md D16): heartbeat rows carry no ledger
    session_id today (PLAN.md's own "known gap," `factory_agents.ledger_session_id`
    is nullable for exactly this reason), and `session_end` events carry no
    closing-response text field.     A real caller with neither map gets every
    agent projecting with `ledger_session_id: None` and no `final_response`
    — the honest current state, not a bug — exercised by this function's own
    untracked-agent test.

    ## The machine-wide mode (A4, factory-observability-v1)

    `run_id=None, org_slug=None` projects EVERY active heartbeat session rather
    than the ones correlated to one run — the "My Sessions" surface's data source.
    In that mode **no event rows are produced at all**, and that is a schema fact
    rather than a choice: `factory_run_events.run_id` is `NOT NULL REFERENCES
    factory_runs(id)`, so an `agent_spawned` row for a session belonging to no run
    is not representable. Returning `([...], [])` is the honest answer; inventing a
    placeholder run to hang the events off would be the alternative, and it would
    pollute the runs list with rows that are not fine-tunes.
    """
    session_id_map = session_id_map or {}
    final_responses = final_responses or {}
    ledger_events = _parse_ledger_lines(ledger_lines or [])

    agent_rows = []
    event_rows = []
    for session in parse_active_sessions(heartbeat_md):
        if not _session_matches_run(session, org_slug):
            continue
        label = session.get("session", "")
        risk = session.get("risk") if session.get("risk") in ("read-only", "code", "migration") else None
        status = "ended" if (session.get("status") or "").upper() == "ENDED" else "active"
        ledger_session_id = session_id_map.get(label)
        files_touched, commands_run = _aggregate_ledger(ledger_events, ledger_session_id)

        row = {
            "run_id": run_id,
            "role": RISK_TO_ROLE.get(risk, "contributor"),
            "title": label,
            "goal": session.get("goal", ""),
            "status": status,
            "risk": risk,
            "allowed_paths": _split_allowed_paths(session.get("allowed_paths", "")),
            "files_touched": files_touched,
            "commands_run": commands_run,
            "ledger_session_id": ledger_session_id,
        }
        final_response = final_responses.get(label)
        if final_response:
            row["final_response"] = final_response
        agent_rows.append(row)

        # See the machine-wide note in this function's docstring: with no run there
        # is no legal `factory_run_events` row to write.
        if run_id is None:
            continue

        # Deterministic ids (2026-07-31, stress-test EVENT-COVERAGE finding
        # F-03-1): these rows used to carry no `id` and were INSERTed by every
        # `agents_sync_job` poll, so one active session accumulated a duplicate
        # `agent_spawned` event per poll cycle for as long as it stayed active.
        # uuid5 per (run, session, kind) + the upsert in `push_agents` makes
        # every poll re-assert the same two rows instead. Bonus honesty for the
        # `ts` gap research/04 flagged: since `ts` is not in this payload, the
        # first upsert's DB default sets it and later polls leave it alone — so
        # `ts` becomes "first observed", the closest to spawn time heartbeat
        # can honestly support (it records no session start time).
        event_rows.append(
            {
                "id": _event_id(f"agent:{run_id}:{label}:spawned"),
                "run_id": run_id,
                "kind": "agent_spawned",
                "headline": f"{label} started",
                "ref": {"agent_title": label},
                "detail": {},
            }
        )
        if status == "ended":
            event_rows.append(
                {
                    "id": _event_id(f"agent:{run_id}:{label}:finished"),
                    "run_id": run_id,
                    "kind": "agent_finished",
                    "headline": final_response or f"{label} finished",
                    "ref": {"agent_title": label},
                    "detail": {},
                }
            )
    return agent_rows, event_rows


def _upsert_agent_row(row, url=None, service_role_key=None):
    """`factory_agents` has no unique constraint besides its own `id`
    (`ledger_session_id` is nullable and unconstrained — most rows will be
    null per T13's own untracked-agent case), so there is no `on_conflict`
    target for a real upsert. Instead: select the existing row for this
    (run_id, ledger_session_id) — or (run_id, title) when there is no ledger
    session id — and PATCH it if found, INSERT if not.     Mirrors T12's
    `push_gate_request` select-then-write pattern for the same reason.

    A machine-wide row (A4) has `run_id: None`, which needs PostgREST's `is.null`
    rather than `eq.` — `eq.None` would filter for the literal string "None", match
    nothing, and therefore INSERT a duplicate row on every single poll instead of
    updating the one that is already there.
    """
    run_id = row.get("run_id")
    filters = {
        "run_id": "is.null" if run_id is None else f"eq.{run_id}",
        "select": "id",
        "limit": "1",
    }
    if row.get("ledger_session_id"):
        filters["ledger_session_id"] = f"eq.{row['ledger_session_id']}"
    else:
        filters["title"] = f"eq.{row['title']}"
    existing = select("factory_agents", params=filters, url=url, service_role_key=service_role_key)
    if existing:
        update(
            "factory_agents",
            {"id": f"eq.{existing[0]['id']}"},
            row,
            url=url,
            service_role_key=service_role_key,
        )
    else:
        insert("factory_agents", [row], url=url, service_role_key=service_role_key)


def push_agents(
    cfg,
    slug,
    run_id,
    heartbeat_md,
    ledger_lines=None,
    session_id_map=None,
    final_responses=None,
    url=None,
    service_role_key=None,
):
    """T13: sync heartbeat/ledger-derived agent + event rows for this run.
    **Fail-open** (PRRules.md rule 5's default direction — telemetry, like
    T11's `push_stage`, not a human-approval gate like T12's `resolve_gate`):
    any `SupabaseRestError` is logged and swallowed here, never raised.
    """
    del cfg  # not needed today (no org-scoped config affects this projection); kept for call-site symmetry with push_stage/push_gate_request
    try:
        agent_rows, event_rows = project_agents(
            heartbeat_md,
            run_id,
            slug,
            ledger_lines=ledger_lines,
            session_id_map=session_id_map,
            final_responses=final_responses,
        )
        for row in agent_rows:
            _upsert_agent_row(row, url=url, service_role_key=service_role_key)
        if event_rows:
            # Upsert on the deterministic id, NOT insert — see the comment in
            # `project_agents`: a scheduled poll re-announcing the same session
            # every 5 minutes was duplicating the timeline (F-03-1).
            upsert("factory_run_events", event_rows, on_conflict="id", url=url, service_role_key=service_role_key)
        return True
    except SupabaseRestError as exc:
        log.warning(
            "factory_sync: agents/events push failed for org=%s — continuing anyway (fail-open): %s",
            slug,
            exc,
        )
        return False


def sessions_claimed_by(heartbeat_md, org_slugs):
    """The set of heartbeat session labels that at least one of `org_slugs` claims,
    per the same `_session_matches_run` correlation `project_agents` uses.

    Exists so `push_machine_sessions` can skip them (see its docstring) while there
    is still exactly ONE definition of "this session belongs to that run". A second,
    parallel implementation in the sync job would drift, and the failure mode of
    drift here is a session appearing twice in the product.
    """
    claimed = set()
    for session in parse_active_sessions(heartbeat_md):
        label = session.get("session", "")
        if not label:
            continue
        if any(_session_matches_run(session, slug) for slug in org_slugs):
            claimed.add(label)
    return claimed


def push_machine_sessions(
    heartbeat_md,
    ledger_lines=None,
    session_id_map=None,
    final_responses=None,
    skip_titles=None,
    url=None,
    service_role_key=None,
):
    """A4 (factory-observability-v1) — sync every active heartbeat session that no
    open run already claimed, as `factory_agents` rows carrying `run_id: NULL`.

    ## Why `skip_titles` exists, and why it is not optional in practice

    A session working on an open fine-tune is already synced by `push_agents` with a
    real `run_id`. Pushing it again here would not update that row — the upsert
    lookup filters on `run_id is.null` — it would INSERT a second, run-less copy, and
    the "My Sessions" surface (which reads every active row, scoped or not) would
    show the same session twice, once with its run and once without.
    So the two pushes partition the sessions between them rather than overlapping,
    and `agents_sync_job` passes the open runs' claims via `sessions_claimed_by`.

    ## Why this is a separate function and not a flag on `push_agents`

    `push_agents` is run-scoped by design, and that scoping was a decision Daniel
    made explicitly on 2026-07-24 (`agents_sync_job.py`'s own docstring) and
    re-confirmed while planning this PR: the Fleet tab tracks *this fine-tune*, not
    general engineering work, and widening it was rejected in favour of a separate
    surface. A boolean parameter would put both behaviours behind one call site and
    make it easy to widen the Fleet tab by accident — which is the specific outcome
    that decision exists to prevent.

    ## No events, and no cost

    `factory_run_events.run_id` is NOT NULL, so a session belonging to no run has no
    representable timeline row (see `project_agents`). And `cost_usd` stays unset:
    heartbeat sessions have never had a per-session cost source (DELTAS.md D16 —
    `tier1`/`tier2` ledger rows carry no `session_id`), and B1's ledger matching does
    not apply here because that keys on a dispatched node's `--label`, which an
    interactive session does not have.

    Redaction is inherited, not reinvented: `files_touched` goes through the same
    `_redact_path` allow-list inside `_aggregate_ledger` that the Fleet tab's rows
    already use (PRRules invariant 12 — no new redaction policy for a new surface).

    Fail-open, same direction as `push_agents` and for the same reason: telemetry.
    """
    skip_titles = skip_titles or set()
    try:
        agent_rows, _events = project_agents(
            heartbeat_md,
            None,
            None,
            ledger_lines=ledger_lines,
            session_id_map=session_id_map,
            final_responses=final_responses,
        )
        pushed = 0
        for row in agent_rows:
            if row.get("title") in skip_titles:
                continue
            _upsert_agent_row(row, url=url, service_role_key=service_role_key)
            pushed += 1
        return pushed
    except SupabaseRestError as exc:
        log.warning(
            "factory_sync: machine-wide session push failed — continuing anyway (fail-open): %s",
            exc,
        )
        return None


# =============================================================================
# B1 (factory-observability-v1) — dispatched node-agent -> factory_agents
# =============================================================================
#
# The gap this closes. Since factory-graph-pr1/pr2, `factory_node_run.dispatch_node`
# has dispatched S4/S5/S7/S8/S10's AgentSpecs to REAL headless Claude Code
# sessions — real model, real turn/budget caps, real cost, a real Claude Code
# `session_id` — and none of it reached Supabase (PRContext.md V1). `factory_agents`
# was fed by a completely separate mechanism (`agents_sync_job.py`) that only ever
# sees heartbeat's interactive human sessions, which is why clicking into a run
# showed nothing about the agents the factory itself had run.
#
# `factory_agents.stage`/`report_path`/`report_summary`/`cost_usd` were created in
# model-factory-v1 with NO write path at all, documented as such in
# `data-model/factory_agents.md`'s own "Known gaps". This is that write path.
#
# ## The two row kinds now sharing this table, and how to tell them apart
#
# `stage` is the discriminator, and it is a reliable one rather than a convention:
# `project_agents` above (heartbeat-sourced, interactive) never sets `stage`,
# because heartbeat's Active Sessions table has no stage column to source it from.
# A non-null `stage` therefore means "the factory dispatched this", full stop.
# `platform-alpha`'s AgentsTable relies on exactly this (A2).
#
# ## Fail direction: OPEN, and this one is load-bearing
#
# `push_node_agent` runs in the `finally` of a real, paid agent dispatch. A
# Supabase outage must never fail or block that dispatch — the spend has already
# happened and the on-disk artifacts are ground truth (D-PI-7). Same direction as
# `push_stage`/`push_agents` above, for a stronger reason. Contrast
# `org_config_sync.py`, which is a maintenance job and fails LOUDLY; PRRules rule 4
# names both directions specifically so they are not confused.

# The exact label `factory_node_run.build_tier2_argv` passes to `tier2_run.sh`.
# Duplicating the string would mean the matcher silently stops matching the day
# the label format changes, so the format lives in one place and both sides derive
# from it.
TIER2_LABEL_PREFIX = "factory-node-"

# `tier2_run.sh` writes its ledger row with `timespec="seconds"`, so a dispatch
# that began at 10:00:00.7 produces a row stamped 10:00:00 — earlier than the
# window it belongs to. A couple of seconds of slack on both ends absorbs that
# truncation (and ordinary clock jitter) without widening the window enough to
# start capturing a neighbouring dispatch of the same node.
LEDGER_MATCH_GRACE_S = 2

# The cap on raw subprocess stderr in anything that reaches the DB.
# `factory_node_run.dispatch_node` already truncates to this before returning, and
# imports this constant so there is ONE number rather than two that can drift.
# Re-applied here as defence in depth: this function must be safe for any caller,
# not only the one that happens to pre-truncate.
STDERR_TAIL_MAX_CHARS = 500

# The agent's own closing message, written to the shape its AgentSpec's
# `final_response_shape` field defines — i.e. already authored to be read by a
# human, which is why it is allowed through at all. Bounded anyway: an agent that
# ignores its response shape and dumps a whole file would otherwise put unbounded
# text into a jsonb column. This is NOT the on-disk report body, which never
# leaves the runner (PRRules invariant 13) — only `report_path`, a repo-relative
# pointer, is stored.
FINAL_RESPONSE_MAX_CHARS = 4000

# The ONLY keys admitted into an `agent_dispatched` event's `detail` (PRRules rule
# 6 / invariant 6: an explicit documented allow-list, never "whatever the tier2
# result object happens to contain"). A key not named here is dropped on the
# floor, exactly like `SAFE_REPORT_KEYS` above — so a new field appearing on
# `dispatch_node`'s result is invisible in the DB until someone deliberately adds
# it, which is what makes the negative test meaningful without having to predict
# every future shape of that dict.
#
# Deliberately ABSENT: `allowed_tools` (not sensitive, just noise), `prompt_file`
# (carried as the agent row's `report_path` instead of duplicated per event),
# `dry_run` (a dry run never reaches this function), and anything resembling raw
# tool arguments or tool output.
SAFE_DISPATCH_DETAIL_KEYS = frozenset(
    {
        "spec_slug",
        "spec_version",
        "role_family",
        "model",
        "max_turns",
        "budget_usd",
        "cost_usd",
        "verdict",
        "exit_code",
        "final_response",
        "usage",
        "stderr_tail",
        "ledger_session_id",
    }
)

# The coarse usage projection (decision D2). An allow-list over the ledger row's
# `usage` block, NOT the block itself: real rows carry `service_tier`,
# `inference_geo`, a `server_tool_use` sub-object and a full per-iteration
# `iterations` array (verified against ledger/2026-07-28.jsonl), none of which
# belongs in a run-detail payload.
#
# D2 asked for `{turns_used, tokens_in, tokens_out}`. Two of those three exist.
# **There is no turn count in the ledger row** — `tier2_run.sh` records
# `max_turns` (the CAP it enforced) and never the turns actually consumed, and
# that script is out of scope for this PR to modify. `turns_used` is therefore
# reported as the length of `usage.iterations` when the provider supplied one, and
# `None` when it did not — never inferred from `max_turns`, which would report
# every cheap 2-turn node as having used its full 30-turn budget.
_USAGE_TOKEN_KEYS = {
    "tokens_in": "input_tokens",
    "tokens_out": "output_tokens",
    "cache_read_tokens": "cache_read_input_tokens",
    "cache_creation_tokens": "cache_creation_input_tokens",
}


def _iso(value):
    """Serialize a timestamp for a `timestamptz` column. Accepts a `datetime` or an
    already-formatted string, so a caller that has one or the other does not have
    to know which this wants.
    """
    return value.isoformat() if isinstance(value, datetime) else str(value)


def tier2_label_for(node_id):
    """The `--label` value `build_tier2_argv` gives `tier2_run.sh` for this node.

    `factory_node_run` replaces `:` with `-` because the label ends up in a
    filename; that substitution is reproduced here rather than re-derived, and
    `tests/test_node_dispatch_sync.py` asserts the two agree against the real
    `build_tier2_argv`.
    """
    return f"{TIER2_LABEL_PREFIX}{node_id.replace(':', '-')}"


def project_usage(usage):
    """Coarse token/turn summary from a ledger row's `usage` block, or `None`.

    Returns `None` (not `{}`) when there is nothing to report, so "no usage data"
    is distinguishable from "zero tokens" — the same reasoning PR 1's quality
    signal used for emitting no `scores` key at all rather than an empty one.
    """
    if not isinstance(usage, dict):
        return None
    out = {}
    for out_key, ledger_key in _USAGE_TOKEN_KEYS.items():
        value = usage.get(ledger_key)
        if isinstance(value, int):
            out[out_key] = value
    iterations = usage.get("iterations")
    if isinstance(iterations, list):
        out["turns_used"] = len(iterations)
    return out or None


def _parse_ledger_ts(raw):
    """Parse a ledger row's `ts` (local-offset ISO-8601, seconds precision)."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def match_ledger_row(ledger_path, node_id, window_start=None, window_end=None):
    """The `tier2` ledger row this dispatch produced, or `None` (decision D1).

    Matching is by `label` — `build_tier2_argv` already stamps every dispatch with
    `factory-node-<node_id>`, so no new plumbing is needed to correlate them — and
    then narrowed to the dispatch's own wall-clock window.

    **Every failure mode returns `None`, and NEVER a guess** (PRRules invariant
    15). That includes the ambiguous case: if two rows match the same label inside
    the window (a node retried within the same window, or two runs racing), this
    refuses rather than picking one, because a cost silently attributed to the
    wrong attempt is worse than a missing one. The tie-breaking rule is therefore
    "there is no tie-break; ambiguity degrades to None", stated here rather than
    left to whichever row `next()` happened to reach first.

    A missing file, an unreadable file, and malformed lines all degrade the same
    way — this runs in the `finally` of a paid dispatch and must not raise.
    """
    label = tier2_label_for(node_id)
    try:
        path = Path(ledger_path)
        if not path.is_file():
            return None
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        log.warning("factory_sync: could not read ledger %s for %s: %s", ledger_path, node_id, exc)
        return None

    candidates = []
    for row in _parse_ledger_lines(lines):
        if row.get("event") != "tier2" or row.get("label") != label:
            continue
        if window_start or window_end:
            ts = _parse_ledger_ts(row.get("ts"))
            if ts is None:
                # A tier2 row with no parseable timestamp cannot be placed inside
                # or outside the window, so it is not evidence either way.
                continue
            grace = timedelta(seconds=LEDGER_MATCH_GRACE_S)
            if window_start and ts < window_start - grace:
                continue
            if window_end and ts > window_end + grace:
                continue
        candidates.append(row)

    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        log.warning(
            "factory_sync: %d ledger rows match label=%s in the dispatch window — "
            "cost/session degrade to NULL rather than attributing to an arbitrary one (D1)",
            len(candidates),
            label,
        )
    return None


def ledger_path_for(ledger_dir=None, when=None):
    """Today's ledger file. Same precedence as `factory_decisions._ledger_path`
    (explicit argument, then `FACTORY_LEDGER_DIR`, then the real `ledger/`) — the
    env override is what stops the test suite reading or writing the production
    ledger, and a second resolution rule here would defeat it.

    Unlike that function this NEVER creates the directory: this is a read path,
    and a missing ledger is a legitimate outcome that must degrade to `None`
    rather than being papered over with an empty directory.
    """
    directory = Path(ledger_dir) if ledger_dir else Path(os.environ.get("FACTORY_LEDGER_DIR") or HERE.parent.parent / "ledger")
    stamp = (when or datetime.now()).strftime("%Y-%m-%d")
    return directory / f"{stamp}.jsonl"


def read_ledger_events(ledger_path):
    """Every parsed row in one ledger file, or `[]`.

    Deliberately separate from `match_ledger_row`, which answers a narrower
    question (one `tier2` row) and keeps its own contract. This one exists so the
    dispatched agent's `tool` events can be aggregated by the same
    `_aggregate_ledger` the heartbeat path uses.

    Degrades to `[]` on a missing/unreadable file, same as `match_ledger_row`:
    this also runs in the `finally` of a paid dispatch and must not raise.
    """
    try:
        path = Path(ledger_path)
        if not path.is_file():
            return []
        return _parse_ledger_lines(
            path.read_text(encoding="utf-8", errors="replace").splitlines()
        )
    except OSError as exc:
        log.warning("factory_sync: could not read ledger %s: %s", ledger_path, exc)
        return []


def project_node_agent(
    dispatch_result,
    run_id,
    stage_code,
    spec,
    prompt_file=None,
    started_at=None,
    ended_at=None,
    ledger_row=None,
    ledger_events=None,
):
    """Build the (`factory_agents` row, `agent_dispatched` event row) pair for one
    dispatched node. Pure — no network, no clock, no filesystem — so the row
    content is testable without mocking a push (LESSON-016: the tests must assert
    on what gets pushed, not merely that the push returned).

    `ledger_row` is the matched `tier2` row (cost/usage/session_id); `ledger_events`
    is every parsed ledger row for the day, from which this session's `tool` events
    are aggregated into files_touched/commands_run. Both are passed in rather than
    read here, so this stays pure.
    """
    node_id = dispatch_result.get("node_id") or stage_code
    runtime = dispatch_result.get("runtime") or {}
    ledger_row = ledger_row or {}

    # NEVER 0 on a failed match (PRRules invariant 15). `.get` returning None is
    # the honest answer; `or 0` here would be the bug.
    cost_usd = ledger_row.get("cost_usd")
    ledger_session_id = ledger_row.get("session_id")
    usage = project_usage(ledger_row.get("usage"))

    # Daniel's ask was "what every agent had as input, tool calls and their
    # environment" — so the tool calls have to be real, not just the token
    # counts. The matched tier2 row hands us the dispatched agent's actual
    # `session_id`, and every `tool` event in the ledger carries one, so this
    # reuses `_aggregate_ledger` (the same function, the same `_redact_path`
    # allow-list) rather than adding a second correlation path with its own
    # redaction rules. No match -> no session_id -> ([], 0), which is the
    # honest empty rather than a fabricated one.
    files_touched, commands_run = _aggregate_ledger(
        ledger_events or [], ledger_session_id
    )

    final_response = (dispatch_result.get("final_response") or "")[:FINAL_RESPONSE_MAX_CHARS] or None
    stderr_tail = (dispatch_result.get("stderr_tail") or "")[-STDERR_TAIL_MAX_CHARS:] or None

    # C2/C3 (factory-cursor-bridge-v1, D7): an operator's Stop/Pause is its own
    # terminal shape, not a failure. The row lands as `cancelled` — matching what
    # the product's `request_factory_agent_control` RPC already set on it, so
    # this sync never flips an operator-stopped row back to `ended`. `resumable`
    # is deliberately NOT written here: the RPC owns it, and a PATCH that omits
    # the key leaves the RPC's verdict (pause vs stop) intact.
    stopped = bool(dispatch_result.get("stopped"))

    agent_row = {
        "run_id": run_id,
        # `stage` is what distinguishes a dispatched node from a heartbeat-sourced
        # interactive session (see this section's header). It must always be set.
        "stage": stage_code,
        "role": spec.get("role_family") or "contributor",
        "title": dispatch_result.get("spec") or spec.get("slug") or node_id,
        "goal": spec.get("mission") or "",
        # Dispatch is synchronous — `dispatch_node` does not return until the
        # subprocess (or the SDK's wait()) has exited — so by the time THIS row
        # is built the agent is over: 'cancelled' if an operator ended it,
        # otherwise 'ended'. (The mid-run 'active' row is push_node_agent_started's.)
        "status": "cancelled" if stopped else "ended",
        "cost_usd": cost_usd,
        "ledger_session_id": ledger_session_id,
        "report_path": str(prompt_file) if prompt_file else dispatch_result.get("prompt_file"),
        "report_summary": final_response,
        "files_touched": files_touched,
        "commands_run": commands_run,
    }
    # C3: the control path's keys, written ONLY when the dispatch carried them —
    # a tier2-executed node's row has none of these keys at all (not null-valued
    # keys; PR-A's established convention), exactly like every pre-PR-B row, and
    # the product's V7 hard line ("no cursor_agent_id = never controllable")
    # holds by construction.
    for key in ("cursor_agent_id", "cursor_run_id", "cursor_runtime"):
        if dispatch_result.get(key) is not None:
            agent_row[key] = dispatch_result[key]

    if started_at is not None:
        agent_row["started_at"] = _iso(started_at)
    if ended_at is not None:
        agent_row["ended_at"] = _iso(ended_at)
        agent_row["last_seen_at"] = _iso(ended_at)
    if final_response:
        agent_row["final_response"] = final_response

    ok = bool(dispatch_result.get("ok"))
    verdict = dispatch_result.get("verdict")
    detail = {
        "spec_slug": dispatch_result.get("spec") or spec.get("slug"),
        "spec_version": spec.get("spec_version"),
        "role_family": spec.get("role_family"),
        "model": runtime.get("model"),
        "max_turns": runtime.get("max_turns"),
        "budget_usd": runtime.get("budget_usd"),
        "cost_usd": cost_usd,
        "verdict": verdict,
        "exit_code": dispatch_result.get("exit_code"),
        "final_response": final_response,
        "usage": usage,
        "stderr_tail": stderr_tail,
        "ledger_session_id": ledger_session_id,
    }
    # D7's vocabulary, load-bearing: "stopped" must never render as "FAILED" —
    # an operator's deliberate Stop counted as a spec-reliability failure is the
    # exact misread V8 flagged. The headline is the reliability-facing surface
    # a human (or a future lessons pass) reads, so the distinction lives here.
    if stopped:
        outcome_word = "stopped by operator"
    elif ok:
        outcome_word = "completed"
    else:
        outcome_word = "FAILED"
    event_row = {
        "run_id": run_id,
        "kind": "agent_dispatched",
        # The headline says what happened including when it FAILED — a dispatch
        # that exited non-zero still gets a row, and still reads as a failure
        # rather than being silently absent from the timeline.
        "headline": (
            f"{node_id} {outcome_word}"
            + (f" (verdict: {verdict})" if verdict else "")
        ),
        "ref": {"node_id": node_id, "stage": stage_code, "agent_title": agent_row["title"]},
        "detail": project_dispatch_detail(detail),
    }
    return agent_row, event_row


def project_dispatch_detail(detail):
    """Allow-list + PII scrub of an `agent_dispatched` event's `detail`.

    Two layers, identical in shape to `project_stage_report`: drop every key not
    in `SAFE_DISPATCH_DETAIL_KEYS` (layer 1), then scrub structured PII from every
    surviving string however deeply nested (layer 2). Layer 2 matters here in a way
    it does not for stage reports: `final_response` and `stderr_tail` are
    agent-authored and subprocess-emitted text respectively, so neither has a
    reviewed shape the way a stage report's fields do.

    Keys whose value is `None` are dropped rather than stored as JSON null —
    `usage: null` in a run-detail payload renders as an empty labelled field in the
    UI, whereas an absent key renders as nothing at all, which is the truth.
    """
    allow_listed = {k: v for k, v in detail.items() if k in SAFE_DISPATCH_DETAIL_KEYS and v is not None}
    if "stderr_tail" in allow_listed:
        allow_listed["stderr_tail"] = str(allow_listed["stderr_tail"])[-STDERR_TAIL_MAX_CHARS:]
    if "final_response" in allow_listed:
        allow_listed["final_response"] = str(allow_listed["final_response"])[:FINAL_RESPONSE_MAX_CHARS]
    return _scrub_structured_pii(allow_listed)


def push_node_agent(
    dispatch_result,
    run_id,
    stage_code,
    spec,
    prompt_file=None,
    started_at=None,
    ended_at=None,
    ledger_dir=None,
    url=None,
    service_role_key=None,
):
    """Sync one dispatched node-agent: upsert a `factory_agents` row and append an
    `agent_dispatched` `factory_run_events` row.

    **Fail-open, unconditionally.** Called from `dispatch_node`'s `finally`, after
    real money has been spent. Every exception is caught — not just
    `SupabaseRestError`, because the ledger read and the row projection are also
    in this path and a `TypeError` from an unexpected result shape must not be the
    thing that takes down a paid dispatch either. Returns True/False so a caller
    (or a test) can tell whether the sync landed without having to inspect logs.

    Returns False without attempting anything if `run_id` is falsy: a dispatch
    that has no run to attach to is a real case (a bare `factory_node_run` CLI
    invocation), and inventing a run id for it would be worse than skipping.
    """
    if not run_id:
        log.debug("factory_sync: no run_id for %s — skipping node-agent sync", dispatch_result.get("node_id"))
        return False
    try:
        ledger_path = ledger_path_for(ledger_dir, when=ended_at)
        ledger_row = match_ledger_row(
            ledger_path,
            dispatch_result.get("node_id") or stage_code,
            window_start=started_at,
            window_end=ended_at,
        )
        agent_row, event_row = project_node_agent(
            dispatch_result,
            run_id,
            stage_code,
            spec,
            prompt_file=prompt_file,
            started_at=started_at,
            ended_at=ended_at,
            ledger_row=ledger_row,
            ledger_events=read_ledger_events(ledger_path),
        )
        _upsert_agent_row(agent_row, url=url, service_role_key=service_role_key)
        insert("factory_run_events", [event_row], url=url, service_role_key=service_role_key)
        return True
    except Exception as exc:  # noqa: BLE001 — see the docstring: this runs after the spend
        log.warning(
            "factory_sync: node-agent sync failed for %s — continuing anyway (fail-open): %s",
            dispatch_result.get("node_id"),
            exc,
        )
        return False


def push_node_agent_started(
    dispatch_result,
    run_id,
    stage_code,
    spec,
    started_at=None,
    url=None,
    service_role_key=None,
):
    """C2 (factory-cursor-bridge-v1) — the mid-run half of a Cursor-SDK dispatch's
    sync: a minimal `active` `factory_agents` row pushed immediately after
    `send()`, while `dispatch_node` is still blocked in the SDK's `wait()`.

    This is what makes the product's Stop/Pause buttons reachable at all for a
    running dispatch: they render only on an `active` row with a non-null
    `cursor_agent_id`, and `push_node_agent` (above) runs after the run is over.
    Recorded as a deviation from PRContext C2's letter ("local state") in
    `PRDone.md` — the local state file still happens, in `factory_cursor_dispatch`.

    Same (run_id, title) upsert identity as the final push, so the `finally`'s
    full row UPDATEs this one rather than duplicating it. No event row — the
    timeline's `agent_dispatched` event belongs to the outcome, which is not
    known yet.

    **Fail-open, unconditionally** — mid-dispatch, money already committed.
    Returns True/False, informational only. Skips (False) without a `run_id`,
    same as `push_node_agent` and for the same reason.
    """
    if not run_id:
        return False
    try:
        agent_row = {
            "run_id": run_id,
            "stage": stage_code,
            "role": spec.get("role_family") or "contributor",
            "title": dispatch_result.get("spec") or spec.get("slug") or dispatch_result.get("node_id"),
            "goal": spec.get("mission") or "",
            "status": "active",
            "report_path": dispatch_result.get("prompt_file"),
        }
        for key in ("cursor_agent_id", "cursor_run_id", "cursor_runtime"):
            if dispatch_result.get(key) is not None:
                agent_row[key] = dispatch_result[key]
        if started_at is not None:
            agent_row["started_at"] = _iso(started_at)
            agent_row["last_seen_at"] = _iso(started_at)
        _upsert_agent_row(agent_row, url=url, service_role_key=service_role_key)
        return True
    except Exception as exc:  # noqa: BLE001 — fail-open; this runs mid-dispatch
        log.warning(
            "factory_sync: started-row sync failed for %s — continuing anyway (fail-open): %s",
            dispatch_result.get("node_id"),
            exc,
        )
        return False


# =============================================================================
# T1-1 (model-factory-finetune-launcher-v1) — cloud-fleet heartbeat bridge
# =============================================================================
#
# The gap this closes: `cloud-sessions/bin/cloud-heartbeat.sh` already gives a
# cloud-hosted Claude Code session (dispatched via `claude --cloud`, laptop
# possibly closed) a real, git-pushed liveness/phase/blocker record — one JSON
# file per session, pushed to its own `refs/heads/cloud-fleet/<session-id>` ref
# (never the agent's own branch/HEAD) — but nothing has ever read it into
# `factory_agents`. `cs fleet`/`fleet_ui.py` render it standalone, offline. This
# section is that bridge: fetch every `cloud-fleet/*` ref, read each one's
# materialized fleet-JSON, and project it the same way `push_machine_sessions`
# already projects a machine-wide heartbeat row — reusing `_upsert_agent_row`
# as the one write path (PRContext.md T1-1: "do not invent a second writer").
#
# ## Why machine-wide (`run_id: NULL`), not run-scoped like `push_agents`
#
# A cloud-fleet session's only self-description is `--lane <name>` (free text,
# chosen at `start`) — there is no org_slug/allowed_paths the way a
# heartbeat/STATE.md row carries, so `_session_matches_run`'s correlation has
# nothing to key on here. This partitions exactly like `push_machine_sessions`
# (a separate, run-less surface) rather than trying to force a run
# correlation that does not exist in the source data.
#
# ## Fail direction: OPEN, at every layer
#
# Fetching a git ref over the network, reading a possibly-stale or malformed
# blob, and pushing to Supabase are three independent ways this can fail, and
# none of them may block the scheduled poll (`agents_sync_job.py`) or crash a
# sibling session's sync — same direction as `push_machine_sessions` and for
# the same reason: telemetry, not a human-approval gate (PRRules rule 5).
#
# ## What does NOT fit an existing column, and where it goes instead
#
# `environment`/`blocker` are the two new columns T1-2 added specifically
# because nothing existing fit them. `phase`/`progress`/`host`/`git` have no
# dedicated column either, and PRContext.md T1-1 says to map them "onto
# whatever existing columns fit" — `report_summary` is the closest existing
# fit (free-text, human-readable, already used for a dispatched node's status
# summary), so this synthesizes one human-readable line from them rather than
# inventing a fourth new column for what is, today, purely descriptive text no
# UI needs to query structurally. `session_id` (the real Claude Code session
# id cloud-heartbeat.sh records) maps onto `ledger_session_id`, which already
# means exactly that for every other writer in this file.

# The ref namespace and on-disk path cloud-heartbeat.sh itself writes to
# (FLEET_NS / FLEET_DIR in that script) — not re-derived, copied verbatim so
# the two sides of this bridge cannot drift out of step with each other.
FLEET_REF_GLOB = "refs/heads/cloud-fleet/*"
FLEET_REMOTE_NS = "refs/remotes/origin/cloud-fleet"
FLEET_STATE_PATH_TEMPLATE = "cloud-sessions/state/fleet/{session_id}.json"

# cloud-heartbeat.sh's own closed blocker taxonomy (its `BLOCKER_KINDS`
# variable and `blocked`'s `refuse "unknown blocker kind"` path) — copied
# verbatim, not re-derived, so the two sides can never silently drift. A
# blocker whose `kind` is not in this set is dropped (not stored, not
# guessed) rather than passed through — the same "unknown kinds are refused"
# discipline the shell script already enforces at the source, applied again
# here in case a future/foreign emitter writes a fleet-JSON file by hand.
CLOUD_FLEET_BLOCKER_KINDS = frozenset(
    {
        "needs-decision",
        "needs-credential",
        "needs-human-action",
        "dependency-missing",
        "rate-limited",
        "ambiguous-instruction",
    }
)

# cloud-heartbeat.sh's own status vocabulary is ACTIVE/BLOCKED/DONE/FAILED;
# factory_agents.status's CHECK constraint only admits
# active/ended/stale/cancelled (verified against the migration, not assumed).
# BLOCKED maps to 'active' (still alive, still beating — see cloud-heartbeat.sh's
# own "blocked-and-alive" comment) with the detail carried entirely by the
# `blocker` column, not by status. FAILED maps to 'ended' rather than a new
# status value: widening the CHECK constraint is a bigger, separate schema
# change this PR does not make, and 'ended' is not a lie (the session did
# stop) — it is a known, accepted simplification, same spirit as this table's
# other documented gaps (`stale` never derived, `cost_usd` unset for
# heartbeat-sourced rows).
_CLOUD_FLEET_STATUS_MAP = {
    "ACTIVE": "active",
    "BLOCKED": "active",
    "DONE": "ended",
    "FAILED": "ended",
}

# Cap on the synthesized `report_summary` line — same order of magnitude as
# cloud-heartbeat.sh's own per-field caps (note/phase capped at 2000 chars in
# the script's Python writer) rather than an arbitrary new number.
CLOUD_FLEET_SUMMARY_MAX_CHARS = 2000


def fetch_cloud_fleet_refs(repo_dir=None):
    """`git fetch origin '+refs/heads/cloud-fleet/*:refs/remotes/origin/cloud-fleet/*'`
    — the exact command cloud-heartbeat.sh's own module docstring documents as
    the one-fetch-gets-the-whole-fleet read path.

    Best-effort: offline, no origin, or a transient git failure all log a
    warning and return False rather than raising — fail-open, same direction
    as every other push/read in this section. Returns True only when the
    fetch itself exited zero (informational; callers must not branch sync
    behavior on it beyond "did we get anything new").
    """
    try:
        subprocess.run(
            ["git", "fetch", "origin", f"+{FLEET_REF_GLOB}:{FLEET_REMOTE_NS}/*"],
            cwd=str(repo_dir or REPO),
            check=True,
            capture_output=True,
            timeout=30,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — deliberate: any failure here is non-fatal telemetry
        log.warning(
            "factory_sync: cloud-fleet ref fetch failed — continuing anyway (fail-open): %s", exc
        )
        return False


def list_cloud_fleet_session_ids(repo_dir=None):
    """Every session id with a local `refs/remotes/origin/cloud-fleet/<id>` ref
    — i.e. whatever `fetch_cloud_fleet_refs` most recently pulled down. A
    separate step from the fetch itself (mirrors cloud-heartbeat.sh's own
    fetch-then-enumerate split) so a test can stub either half independently.

    Returns `[]` on any git failure, logged — never raises.
    """
    try:
        result = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname)", FLEET_REMOTE_NS],
            cwd=str(repo_dir or REPO),
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        log.warning(
            "factory_sync: could not list cloud-fleet refs — continuing anyway (fail-open): %s", exc
        )
        return []
    prefix = f"{FLEET_REMOTE_NS}/"
    return [
        line.strip()[len(prefix):]
        for line in result.stdout.splitlines()
        if line.strip().startswith(prefix)
    ]


def read_cloud_fleet_state(session_id, repo_dir=None):
    """The materialized fleet-JSON for one session id, read via `git show
    <remote-ref>:<path>` — plumbing, matching cloud-heartbeat.sh's own
    discipline of never touching the caller's HEAD, index or working tree to
    read or write a beat.

    Returns `None` on any failure (ref gone, path missing at that ref, invalid
    JSON, a non-dict top level) — logged, never raised, so one unreadable
    session can never stop the rest of the fleet from syncing.
    """
    ref = f"{FLEET_REMOTE_NS}/{session_id}"
    path = FLEET_STATE_PATH_TEMPLATE.format(session_id=session_id)
    try:
        result = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            cwd=str(repo_dir or REPO),
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        log.warning(
            "factory_sync: could not read cloud-fleet state for session=%s — skipping (fail-open): %s",
            session_id,
            exc,
        )
        return None
    try:
        state = json.loads(result.stdout)
    except ValueError as exc:
        log.warning(
            "factory_sync: cloud-fleet state for session=%s is not valid JSON — skipping: %s",
            session_id,
            exc,
        )
        return None
    if not isinstance(state, dict):
        log.warning(
            "factory_sync: cloud-fleet state for session=%s is not a JSON object — skipping", session_id
        )
        return None
    return state


def _cloud_fleet_report_summary(state):
    """Synthesize the one existing-column-shaped fit for phase/progress/host/git
    — see this section's header comment for why these four fields have no
    dedicated column. Returns `None` (not `""`) when nothing is present, so an
    absent key renders as nothing in the UI rather than an empty labelled field.
    """
    parts = []
    phase = state.get("phase")
    if phase:
        parts.append(f"phase: {phase}")
    progress = state.get("progress") or {}
    if isinstance(progress, dict) and progress.get("total"):
        parts.append(f"{progress.get('done')}/{progress.get('total')}")
    host = state.get("host") or {}
    git = state.get("git") or {}
    if isinstance(host, dict) and host.get("hostname"):
        branch_sha = git.get("branch") if isinstance(git, dict) else None
        head = git.get("head") if isinstance(git, dict) else None
        if head:
            branch_sha = f"{branch_sha}@{head}" if branch_sha else head
        host_str = host["hostname"] + (f" ({branch_sha})" if branch_sha else "")
        parts.append(f"host: {host_str}")
    if not parts:
        return None
    return " | ".join(parts)[:CLOUD_FLEET_SUMMARY_MAX_CHARS]


def project_cloud_fleet_agent(state):
    """Pure: one cloud-heartbeat.sh state dict -> a `factory_agents` row dict.
    No network, no clock, no filesystem (matches `project_node_agent`'s own
    "pure" discipline, LESSON-016) — the row content is testable directly
    against a hand-built fixture dict, without mocking a push.
    """
    session_id = state.get("session_id") or ""
    lane = state.get("lane") or session_id
    status = _CLOUD_FLEET_STATUS_MAP.get(state.get("status"), "active")

    blocker = state.get("blocker")
    if blocker is not None:
        kind = blocker.get("kind") if isinstance(blocker, dict) else None
        if kind not in CLOUD_FLEET_BLOCKER_KINDS:
            log.warning(
                "factory_sync: dropping cloud-fleet blocker with unknown kind %r for session=%s",
                kind,
                session_id,
            )
            blocker = None

    note = state.get("note") or ""
    row = {
        "run_id": None,
        "role": "contributor",
        "title": lane,
        "goal": note,
        "status": status,
        "environment": "cloud_vm",
        "blocker": blocker,
        "ledger_session_id": session_id or None,
    }
    report_summary = _cloud_fleet_report_summary(state)
    if report_summary:
        row["report_summary"] = report_summary
    # The closing text is only meaningful once the session has actually ended
    # (DONE/FAILED) — an ACTIVE/BLOCKED session's `note` is "what I'm doing
    # now", not a final word, and belongs in `goal` above, not `final_response`.
    if status == "ended" and note:
        row["final_response"] = note[:FINAL_RESPONSE_MAX_CHARS]
    if state.get("started_at"):
        row["started_at"] = _iso(state["started_at"])
    if state.get("updated_at"):
        row["last_seen_at"] = _iso(state["updated_at"])
    if state.get("ended_at"):
        row["ended_at"] = _iso(state["ended_at"])
    return row


def push_cloud_fleet_agents(repo_dir=None, url=None, service_role_key=None):
    """T1-1: sync every fetchable `cloud-fleet/<session-id>` heartbeat state
    into `factory_agents`. Wired into `agents_sync_job.py`'s scheduled poll,
    the same way `push_machine_sessions` already is.

    **Fail-open at every layer** (telemetry, PRRules rule 5's default
    direction, same as `push_machine_sessions`): a fetch failure, an
    unreadable ref, or a Supabase error for one session is logged and
    skipped — this function itself never raises.

    Returns the count of rows successfully upserted (0 is a meaningful,
    honest "looked and found nothing live" — distinct from a raised
    exception, which cannot happen here).
    """
    fetch_cloud_fleet_refs(repo_dir=repo_dir)
    session_ids = list_cloud_fleet_session_ids(repo_dir=repo_dir)
    pushed = 0
    for session_id in session_ids:
        state = read_cloud_fleet_state(session_id, repo_dir=repo_dir)
        if not state:
            continue
        row = project_cloud_fleet_agent(state)
        try:
            _upsert_agent_row(row, url=url, service_role_key=service_role_key)
            pushed += 1
        except SupabaseRestError as exc:
            log.warning(
                "factory_sync: cloud-fleet agent push failed for session=%s — continuing anyway "
                "(fail-open): %s",
                session_id,
                exc,
            )
    return pushed
