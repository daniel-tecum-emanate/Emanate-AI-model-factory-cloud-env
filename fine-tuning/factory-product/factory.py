#!/usr/bin/env python3
"""factory — per-customer fine-tune pipeline runner (v0.5 skeleton).

Usage:
    python3 factory.py <org-slug> --stage <stage> [--dry-run] [--approved] [--live]
    python3 factory.py <org-slug> --status

Stages (in order):
    census      S1  auto      org readiness vs dose floors (reads on-disk census data)
    governance  S2  HUMAN GATE data-use clearance — refuses without --approved
    export      S3  auto      dossier export (wraps export-account-dossiers.ts --org)
    build       S4  auto*     corpus build — emits the parameterized V5-recipe work order (session-work today)
    verify      S5  auto      deterministic verification battery, all-must-pass
    project     S6  auto      cost projection (2x dual-bucket, 1.3x calibration) -> decision request
    launch      S6g HUMAN GATE spend approval at the calibrated projection — refuses without --approved
    train       S7  auto*     train + monitor (fw_autorun pattern) — stub, emits launch body
    eval        S8  auto*     paired eval battery vs org baseline — stub, emits command
    ship        S9  HUMAN GATE lexicographic verdict + accepted-risks — refuses without --approved
    activate    S10 runbook   serving + org_custom_models INSERT — emits runbook, never executes
    retrain     S11 proposed  monthly loop per TRAINING-RUNBOOK §B — never self-schedules (PROCESSES.md gate)

Every stage writes runs/<org>/<NN>-<stage>-report.json. Human gates persist approval to
runs/<org>/approvals/<stage>.json only when run with --approved; downstream stages refuse
to run while any earlier gate is unapproved. --dry-run never spawns a subprocess.

This skeleton is manually invoked only. It is NOT registered in PROCESSES.md and must not
be scheduled until it is (install_schedule.sh will refuse it anyway).
"""

import argparse
import copy
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # emanate-tecum-workflow root

# The stage vocabulary lives in `factory_stages` so there is exactly one
# definition of it (C0 / finding S0-4 — the display used to re-derive the code
# from a stage's position, which is wrong from `S6g` onward). Imported at module
# level rather than lazily: `factory_stages` imports nothing, so unlike
# `factory_sync` there is no reason to defer it.
from factory_stages import HUMAN_GATES, STAGE_CODES, STAGES  # noqa: E402

# Tier floors (ORG-READINESS-CENSUS-2026-07-14.md): tiers are data-shape classes, not
# numeric cliffs. QA dose floors are per-account (B5/DX): ~40 pairs/fact-bearing account,
# ~10 safe-default, >=8 phrasings per atomic fact.
QA_FLOOR_FACT_BEARING = 40
QA_FLOOR_SAFE_DEFAULT = 10
MODEL_STREAMS = ("per-account", "intelligence")
INTELLIGENCE_SETUP_ONLY_STAGES = frozenset({"census", "governance"})


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# The stage's TRUE start time, stamped by main() just before it dispatches the
# stage handler and consumed by write_report's sync hook so the `stage_started`
# timeline event carries event time, not sync-insert time (the `ts` gap
# research/04-ux-design.md flagged as the v2 scrubber blocker). None when a
# handler is driven directly (tests) — factory_sync then falls back to the
# report's own `generated_at`.
_STAGE_STARTED_AT = None


def die(msg, code=2):
    print(f"factory: {msg}", file=sys.stderr)
    sys.exit(code)


def load_config(slug, stream="per-account"):
    path = HERE / "configs" / f"{slug}.yaml"
    if not path.exists():
        die(f"no config for org '{slug}' — copy configs/_template.yaml to configs/{slug}.yaml")
    with open(path) as f:
        raw = yaml.safe_load(f)
    if stream == "per-account":
        return raw, path
    if stream not in MODEL_STREAMS:
        die(f"unsupported model stream '{stream}'")
    plan = (raw.get("model_plans") or {}).get(stream)
    if not isinstance(plan, dict):
        die(f"config for org '{slug}' has no model_plans.{stream} plan")
    cfg = copy.deepcopy(raw)
    for section in ("model_plan", "training", "champion", "gates", "budget", "paths"):
        if section not in plan:
            die(f"model_plans.{stream} is missing required section '{section}'")
        cfg[section] = copy.deepcopy(plan[section])
    cfg["_model_stream"] = stream
    cfg["_stream_plan_status"] = plan.get("status")
    cfg["_stream_output_id"] = plan.get("output_id")
    cfg["_stream_unblock_conditions"] = copy.deepcopy(plan.get("unblock_conditions") or {})
    return cfg, path


def _model_stream(cfg=None, args=None):
    return (
        (cfg or {}).get("_model_stream")
        or getattr(args, "stream", None)
        or "per-account"
    )


def run_dir(slug, stream="per-account"):
    d = HERE / "runs" / slug
    if stream != "per-account":
        d = d / stream
    (d / "approvals").mkdir(parents=True, exist_ok=True)
    return d


def write_report(slug, stage, payload, cfg=None, sync_url=None, sync=True):
    """Write the on-disk stage report (unchanged, still the ground-truth artifact
    per D-PI-7) and, when `cfg` is given and a sync URL is configured (either
    `sync_url` — normally `_sync_url(args)` from the caller — or the
    `FACTORY_SUPABASE_URL` env var), fire the T11 sync push as a post-write hook
    (PLAN.md §3.1). Sync is entirely opt-in — a developer running this CLI with
    no Supabase env/flag configured gets identical behavior to before this hook
    existed.

    The push is wrapped in a broad `except Exception` here (not just
    `factory_sync`'s own `SupabaseRestError` catch) because THIS is the actual
    pipeline-blocking boundary the fail-open invariant (D-PI-7 / PRRules.md rule
    5) protects — a stage report must finish writing and this function must
    return normally no matter what goes wrong in the sync sidecar, including
    errors `factory_sync` itself doesn't anticipate (e.g. `requests` missing).
    """
    idx = STAGES.index(stage) + 1
    stream = _model_stream(cfg)
    path = run_dir(slug, stream) / f"{idx:02d}-{stage}-report.json"
    payload = {
        "org": slug,
        "stage": stage,
        "stage_index": idx,
        "generated_at": now(),
        **payload,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"[{stage}] report -> {path.relative_to(REPO)}")
    effective_sync_url = sync_url or os.environ.get("FACTORY_SUPABASE_URL")
    if sync and cfg is not None and effective_sync_url:
        try:
            import factory_sync
            factory_sync.push_stage(cfg, slug, stage, payload, url=effective_sync_url,
                                    started_at=_STAGE_STARTED_AT, stream=stream)
        except Exception as exc:  # noqa: BLE001 - deliberate, see docstring (fail-open)
            print(f"[{stage}] factory_sync unavailable, continuing anyway (fail-open): {exc}", file=sys.stderr)
    return path


def gate_approved(slug, gate, stream="per-account"):
    return (run_dir(slug, stream) / "approvals" / f"{gate}.json").exists()


APPROVED_VIA_FLAG = "factory --approved flag (flag = Daniel typed it himself)"
APPROVED_VIA_TAB = "Model Factory tab gate card (factory_gate_decisions row, consumed via sync)"
APPROVED_VIA_POLICY = "budget.auto_approve_under_usd policy (no human in the loop for this gate)"
# Distinct from APPROVED_VIA_POLICY on purpose — that string names a budget
# threshold, which isn't what's true here. record_approval's own docstring
# warns that a mislabeled `via` poisons the audit trail; reusing the budget
# constant's text for a checklist-based approval would be exactly that.
APPROVED_VIA_GOVERNANCE_POLICY = "governance checklist clear policy (no human in the loop for this gate)"


def record_approval(slug, gate, note, via=APPROVED_VIA_FLAG, stream="per-account"):
    """`via` is the approval CHANNEL and must be passed truthfully by the caller.
    Before 2026-07-31 it was hardcoded to the --approved-flag string, so the first
    real tab approval (S2, run ac6a6078…) was recorded with a provenance claiming
    Daniel typed the flag locally when he clicked the UI button — exactly the
    audit-trail poisoning `_record_gate_decision`'s actor logic warns about.
    """
    path = run_dir(slug, stream) / "approvals" / f"{gate}.json"
    with open(path, "w") as f:
        json.dump({"gate": gate, "approved_at": now(), "note": note,
                   "approved_via": via}, f, indent=2)
    print(f"[{gate}] APPROVAL RECORDED -> {path.relative_to(REPO)}")


def _sync_url(args):
    """`--sync-url` (explicit, CLI-local) takes priority over `FACTORY_SUPABASE_URL`
    (env, the T11 convention) — a per-invocation override is what T12's tests need
    to exercise pending/approved/rejected/error against a fixed fake endpoint
    without mutating the shell environment.

    Every caller of this function only checks the result for truthiness — "is some
    form of sync possible at all" — then threads it down through `factory_sync.py`
    into `supabase_rest.py`, which is where the real direct-vs-relay transport
    decision happens (`_resolve_transport`). So `FACTORY_SYNC_RELAY_URL` belongs in
    this same truthiness chain: a cloud session (model-factory-cloud-environment-v1)
    never has `FACTORY_SUPABASE_URL` — only the relay pair — and without this,
    every sync attempt (gate push/poll, stage/run rows) would be skipped here,
    before ever reaching the transport layer that was actually built to handle it.
    The exact value returned doesn't matter once relay mode is chosen downstream;
    only its truthiness does.
    """
    return (
        getattr(args, "sync_url", None)
        or os.environ.get("FACTORY_SUPABASE_URL")
        or os.environ.get("FACTORY_SYNC_RELAY_URL")
    )


def _gate_status_via_sync(cfg, slug, stage, args, decision_request):
    """Return the gate's LITERAL status string (`pending`/`approved`/`rejected`/
    `withdrawn`) or `None` when unknown — no sync configured, no request row yet,
    or the check itself failed.

    Added 2026-07-27 (factory-graph-pr1, B1) because `_gate_approved_via_sync`
    below answers only "may this advance?", and S9 needs one more bit than that:
    **a rejection is a terminal NO-GO, an undecided gate is not.** Conflating
    them is what left `factory_runs.status` unable to ever reach `no_go`, which
    in turn disabled retrain-lineage minting for rejected runs — see
    `factory_sync._run_status_for`'s docstring for the full chain.

    Same fail-closed direction as its sibling: on any error this returns `None`
    (unknown), never a fabricated `"rejected"`. Unknown must not advance the run
    AND must not mark it terminal.
    """
    sync_url = _sync_url(args)
    if not sync_url:
        return None
    try:
        import factory_sync
        return factory_sync.resolve_gate_status(cfg, slug, stage, decision_request, url=sync_url)
    except Exception as exc:  # noqa: BLE001 - deliberate, mirrors _gate_approved_via_sync
        print(f"[{stage}] factory_sync gate check unavailable — treating status as UNKNOWN: {exc}", file=sys.stderr)
        return None


# The automation graph this pipeline IS. One stable id, versioned: a `graph_id`
# that changed per run would be a run id, and there already is one. Bump the
# suffix only when the graph's SHAPE changes (nodes added/removed/rewired), so a
# later meta-learning pass can tell "the same graph ran again" from "a different
# graph ran" — automation-graph/CHARTER.md §8.
GRAPH_ID = "fine-tune-factory-v1"

# The three human gates, as stage codes (decision D2's `node_id` vocabulary).
GATE_NODE_IDS = {"governance": "S2", "launch": "S6g", "ship": "S9"}


def _record_gate_decision(cfg, slug, stage, args, status, decision_request, actor_override=None,
                          decided_at=None):
    """Emit a B2 `factory_decision` for a human gate's resolution, plus (when
    the gate is actually RESOLVED — approved/no_go/REFUSED, never gate_pending)
    the `gate_decided` timeline event via `factory_sync.push_gate_decided`.

    The gates are the highest-value decisions in the entire pipeline — they are
    where a human's judgment enters, and (per
    `automation-graph/RETRAIN-AND-META-LEARNING.md` §3) that judgment is
    currently unrecoverable anywhere except as a bare approve/reject bit. This
    records the shape of the decision alongside it.

    `actor` is derived, not asserted: `--approved` means Daniel typed it locally,
    a tab approval is still a human, and a `REFUSED` came from the CLI's own
    checklist logic with no human involved — that last one is genuinely
    `deterministic` and mislabeling it `human` would poison exactly the dataset
    this log exists to build.

    `decided_at` is the true decision time when the caller knows it (stage_ship
    passes the tab rejection's `factory_gate_decisions.decided_at`); the event
    otherwise stamps now, which is correct for decisions made in this very
    invocation (--approved / policy / REFUSED).

    Never raises, in either half. A decision-log or timeline failure must not
    break a gate — and the two halves fail independently, so a broken decision
    ledger cannot also silence the timeline (or vice versa).
    """
    if (
        _model_stream(cfg, args) == "intelligence"
        and getattr(args, "dry_run", False)
        and status == "gate_pending"
    ):
        # Setup S2 is a decision packet, not a decision. Keep the dry-run write
        # surface to its stream-local report only.
        return

    if actor_override is not None:
        # An auto-approval is `policy`, never `human` — see stage_launch:
        # recording a policy approval as a human judgment would poison
        # exactly the dataset this log exists to build.
        actor = actor_override
    elif status == "REFUSED":
        actor = "deterministic"
    elif args.approved:
        actor = "human"
    elif status in ("approved", "no_go"):
        actor = "human"
    else:
        actor = "deterministic"

    sync_url = _sync_url(args)
    if sync_url and status in ("approved", "no_go", "REFUSED"):
        try:
            import factory_sync
            factory_sync.push_gate_decided(slug, stage, status, actor=actor,
                                           decided_at=decided_at, url=sync_url,
                                           stream=_model_stream(cfg, args))
        except Exception as exc:  # noqa: BLE001 - telemetry must never break a gate
            print(f"[{stage}] gate_decided event unavailable, continuing anyway: {exc}", file=sys.stderr)

    try:
        import factory_decisions
        idx = STAGES.index(stage) + 1
        factory_decisions.record(
            run_id=_run_id_for_decisions(slug, args),
            graph_id=GRAPH_ID,
            node_id=GATE_NODE_IDS.get(stage, stage),
            decision_point=f"{stage}-gate",
            options=["approve", "reject", "defer"],
            chosen={"approved": "approve", "no_go": "reject", "REFUSED": "reject"}.get(status, "defer"),
            rationale=decision_request.get("recommendation") or status,
            actor=actor,
            org_slug=slug,
            # Refs, never content (see factory_decisions.validate_context_refs).
            context_refs=[
                "fine-tuning/factory-product/runs/"
                f"{slug}/"
                f"{'' if _model_stream(cfg, args) == 'per-account' else _model_stream(cfg, args) + '/'}"
                f"{idx:02d}-{stage}-report.json"
            ],
            sync_url=_sync_url(args),
        )
    except Exception as exc:  # noqa: BLE001 - deliberate: the log must never break the gate
        print(f"[{stage}] decision log unavailable, continuing anyway: {exc}", file=sys.stderr)


def _write_iteration_digest(slug, at_stage, args=None):
    """B4: persist this iteration's digest. Never raises — a run that shipped
    must not be reported as failed because a snapshot could not be written.

    C1b (PR 2) also projects it into `factory_run_digests`. The file stays the
    record this run reads back (D-PI-7); the projection is what a retrain on a
    *different machine* reads, which before C1b found nothing and silently
    classified itself as a first run (decision D5).
    """
    try:
        import factory_context
        path = factory_context.write_iteration_digest(
            slug, stream=_model_stream(args=args)
        )
        print(f"[{at_stage}] iteration digest -> {path.relative_to(REPO)}")
    except Exception as exc:  # noqa: BLE001
        print(f"[{at_stage}] could not write the iteration digest: {exc}", file=sys.stderr)
        return None

    sync_url = _sync_url(args) if args is not None else None
    effective_url = sync_url or os.environ.get("FACTORY_SUPABASE_URL")
    if effective_url:
        try:
            import factory_sync
            digest = json.loads(path.read_text())
            if factory_sync.push_iteration_digest(slug, digest, url=effective_url):
                print(f"[{at_stage}] iteration digest projected to factory_run_digests")
        except Exception as exc:  # noqa: BLE001 - fail-open, same as every other push
            print(
                f"[{at_stage}] iteration-digest projection unavailable, continuing anyway "
                f"(fail-open): {exc}",
                file=sys.stderr,
            )
    return path


def _classify_run_kind(slug, stream="per-account"):
    """Node 0's answer, recorded in the retrain proposal so the next cycle's
    classification is visible rather than implicit."""
    try:
        import factory_context
        return factory_context.classify_run_kind(slug, stream=stream)
    except Exception:  # noqa: BLE001
        return None


def _dispatch_stage_nodes(stage_code, cfg, slug, args):
    """B3: run this stage's AgentSpec chain. Returns per-node result dicts.

    Under `--dry-run` (the default) this resolves each spec and renders its
    prompt but **spawns nothing and spends nothing** — `factory.py`'s existing
    contract, and the only reason the wiring is provable without real money.

    Never raises: a broken dispatcher must not take down a stage that still has
    a useful report to write. A dispatch that fails returns `ok: False`, which
    the caller turns into a failed stage — the failure is propagated as data,
    not as an exception.
    """
    try:
        import factory_node_run
        return factory_node_run.dispatch_stage(
            stage_code,
            run_id=_run_id_for_decisions(slug, args),
            graph_id=GRAPH_ID,
            org_slug=slug,
            model_stream=_model_stream(cfg, args),
            dry_run=args.dry_run,
            sync_url=_sync_url(args),
            self_dispatch_results=_self_dispatch_results(args),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[{stage_code}] node dispatch unavailable: {exc}", file=sys.stderr)
        return [{"node_id": stage_code, "ok": False, "error": str(exc)}]


def _self_dispatch_results(args):
    """Parse every `--self-dispatch-result NODE_ID=PATH` into `{node_id: {...}}`
    (ENVIRONMENT.md §5). Absent flag -> `{}`, identical to today for every
    existing caller."""
    out = {}
    for item in getattr(args, "self_dispatch_result", None) or []:
        node_id, sep, path = item.partition("=")
        if not sep:
            die(f"--self-dispatch-result expects NODE_ID=PATH, got {item!r}")
        out[node_id] = json.loads(Path(path).read_text())
    return out


def _pending_self_dispatch(nodes):
    """True if any dispatched node in this stage's chain is an
    `executor: self_dispatch` node waiting on the orchestrating agent's own
    `Task` tool (ENVIRONMENT.md §5) — pending, not failed."""
    return any(n.get("self_dispatch_required") for n in nodes)


def _judge_generator_separation():
    """C3: the four separation dimensions, recorded on every S8 report.

    Recorded rather than merely asserted so a reviewer reading one run's eval
    report can see that the boundary held for THAT run — including the one
    dimension that cannot be enforced today, which is reported as
    `NOT-SEPARABLE` rather than omitted (PRRules rule 12).
    """
    try:
        import factory_heldout
        return factory_heldout.separation_report()
    except Exception as exc:  # noqa: BLE001
        # An unavailable check is not a passing check (LESSON-016 / D10).
        return {"error": str(exc), "status": "unverified"}


def _heldout_state(slug, stream="per-account"):
    """The held-out anchor's state for this org's current iteration.

    Reported at S4 (so an unsealed corpus is visible while it is being built) and
    at S8 (where it decides whether a verdict is trustworthy at all).
    """
    try:
        import factory_context
        import factory_heldout
        iteration = (
            factory_context._load_state(slug, stream=stream).get("iteration") or 1
        )
        manifest = factory_heldout.load_manifest(slug, iteration, stream=stream)
        if not manifest:
            return {
                "sealed": False,
                "iteration": iteration,
                "why_it_matters": (
                    "No held-out set was sampled before curation, so any paired-eval number for "
                    "this iteration is measured on data the curator could see. Seal one before S4 "
                    "via factory_heldout.seal()."
                ),
            }
        return {
            "sealed": True,
            "iteration": iteration,
            "heldout_count": manifest.get("heldout_count"),
            "population_size": manifest.get("population_size"),
            "population_fingerprint": manifest.get("population_fingerprint"),
            "hash_algo": manifest.get("hash_algo"),
            "seed": manifest.get("seed"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"sealed": False, "error": str(exc)}


def _run_id_for_decisions(slug, args):
    """The run id to stamp on decision events, or None when sync isn't
    configured. Never mints one — `get_or_create_run_id` has real lineage
    semantics (it can start a NEW run when the prior one is terminal), and a
    logging call must never be the thing that triggers that."""
    if not _sync_url(args):
        return None
    try:
        import factory_sync
        return factory_sync._load_run_state(
            slug, stream=_model_stream(args=args)
        ).get("run_id")
    except Exception:  # noqa: BLE001
        return None


def _gate_rejection_note(slug, stage, args):
    """The human's free-text rejection rationale, or `None`. Informational only —
    never gates anything, so every failure path returns `None` silently rather
    than disturbing a decision that has already been made."""
    sync_url = _sync_url(args)
    if not sync_url:
        return None
    try:
        import factory_sync
        return factory_sync.gate_rejection_note(
            slug, stage, url=sync_url, stream=_model_stream(args=args)
        )
    except Exception:  # noqa: BLE001 - deliberate: a missing note must never alter the NO-GO
        return None


def _attribute_lessons(slug, args, status):
    """B2 (factory-observability-v1): stamp `factory_lessons.source_run_id` for the
    lesson codes this ship decision names.

    ## Why the codes come from `--lesson` and not from the report text

    `lessons_sync.attribute_lesson_to_run` has existed since factory-graph-pr1 with
    **zero callers** (PRContext.md V3). Wiring it up needed a source of lesson
    codes, and the honest finding is that `stage_ship`'s report and
    `decision_request` payloads carry none: no `L<N>`/`E<N>` code appears in either,
    and `factory_runs.lessons_minted` has no writer anywhere in the codebase. The
    L-codes elsewhere in this file are prose citations of *existing* canon lessons
    inside checklists, not lessons this run minted.

    So the code could either be passed explicitly by the operator, or scraped out
    of the human's free-text rejection note. Scraping it would mean inventing a
    lesson-code extractor over prose that has never been written to contain one —
    a fabricated data source, and PRContext.md B2 rules it out by name. `--lesson`
    is the explicit alternative: the operator naming what this iteration taught, at
    the moment they make the call.

    ## Fail-soft, and why that differs from `lessons_sync`'s own stance

    `attribute_lesson_to_run` raises on transport errors because it is normally
    reached from a maintenance job. Here it is reached from a HUMAN GATE. The ship
    decision has already been made and written to disk; attribution is additive
    metadata on an existing row (PRRules rule 3). A Supabase hiccup must not be
    able to break S9 — the same fail-soft reasoning `_gate_rejection_note` above
    states for the same stage.

    ## Only a real decision, and never a dry run

    `gate_pending` means nobody has decided yet, and `REFUSED` is the CLI's own
    checklist logic rather than a human judgment. Neither has taught anything worth
    attributing. A `--dry-run` writes nothing to the DB, here as everywhere else.
    """
    codes = list(dict.fromkeys(getattr(args, "lesson", None) or []))  # de-duped, order kept
    if not codes:
        return []
    if status not in ("approved", "no_go"):
        print(f"[ship] --lesson ignored: the gate is {status}, so no decision has been made to attribute to.")
        return []
    if getattr(args, "dry_run", False):
        print(f"[ship] --dry-run: would attribute {codes} to this run, pushing nothing.")
        return []

    sync_url = _sync_url(args)
    run_id = _run_id_for_decisions(slug, args)
    if not sync_url or not run_id:
        print(f"[ship] --lesson ignored: no run id available for {slug} (sync not configured).")
        return []

    attributed = []
    for code in codes:
        try:
            import lessons_sync
            if lessons_sync.attribute_lesson_to_run(code, run_id, url=sync_url):
                attributed.append(code)
                print(f"[ship] lesson {code} attributed to run {run_id}")
            else:
                # First-mint-wins. Already attributed elsewhere, or the code isn't
                # in the canon yet — both are "not ours to claim", not errors.
                print(f"[ship] lesson {code} not attributed (already minted by another run, or not in the canon)")
        except Exception as exc:  # noqa: BLE001 - see the docstring: the ship decision stands regardless
            print(f"[ship] lesson {code} attribution failed (non-fatal): {exc}")
    return attributed


def _gate_approved_via_sync(cfg, slug, stage, args, decision_request):
    """T12: the fail-closed sync check — see `factory_sync.resolve_gate`'s
    docstring for the full contract. Deliberately never called when
    `args.approved` is truthy (D4/DELTAS.md: `--approved` is a full local bypass
    that never touches the network) — callers must check that themselves; this
    helper only decides "did the tab approve it," not "was --approved passed."
    Wrapped in a broad `except Exception` (not just `SupabaseRestError`) for the
    same reason `write_report`'s hook is: this is a pipeline-correctness boundary
    (fail-CLOSED here, the opposite of T11's fail-OPEN boundary) and must behave
    identically ("not approved") no matter what goes wrong, including errors
    `factory_sync` itself doesn't anticipate.
    """
    sync_url = _sync_url(args)
    if not sync_url:
        return False
    try:
        import factory_sync
        return factory_sync.resolve_gate(cfg, slug, stage, decision_request, url=sync_url)
    except Exception as exc:  # noqa: BLE001 - deliberate, see docstring (fail-closed)
        print(f"[{stage}] factory_sync gate check unavailable — fail-closed, treating as NOT approved: {exc}", file=sys.stderr)
        return False


def check_upstream_gates(slug, stage, stream="per-account"):
    """Refuse to run any stage while an earlier human gate is unapproved."""
    for gate in HUMAN_GATES:
        if (
            STAGES.index(gate) < STAGES.index(stage)
            and not gate_approved(slug, gate, stream=stream)
        ):
            return gate
    return None


# ---------------------------------------------------------------- S1 census

def stage_census(cfg, slug, args):
    """Org readiness vs dose floors, from on-disk census data. $0, read-only.

    v0.5 reads the committed census/readiness reports (via the org config seeded from
    them) and cross-checks the org actually appears in the census doc. v1 TODO: re-run
    the live PostgREST counts (read-only prod) instead of trusting a snapshot —
    the census method is recorded in ORG-READINESS-CENSUS-2026-07-14.md (F2 pattern,
    no committed script yet).
    """
    if _model_stream(cfg, args) == "intelligence":
        conditions = cfg.get("_stream_unblock_conditions") or {}
        prerequisites = {
            "prior_int_v1_launch_verdict": conditions.get("intelligence_launch_verdict"),
            "base_model_decision": conditions.get("base_model_decision"),
            "oracle_chat_source_governance": conditions.get("oracle_chat_source_governance"),
            "dynamic_contract_snapshot": conditions.get("dynamic_contract_snapshot"),
        }
        return write_report(slug, "census", {
            "status": "attention",
            "dry_run": args.dry_run,
            "model_stream": "intelligence",
            "readiness": {
                "tier": "B",
                "verdict": "ADAPT-ONLY",
                "s4": "NO-GO",
            },
            "audited_counts": {
                "oracle_conversations": 27,
                "oracle_user_turns": 59,
                "oracle_assistant_turns": 59,
                "exact_target_envelope_captures": 0,
                "partial_persisted_oracle_tool_call_logs": 87,
                "intelligence_shadow_prompts": 0,
                "processed_int_v1_shadow_replays": 0,
                "account_agent_reviews": 6041,
                "account_agent_tool_calls": 27246,
                "calendar_focus_selections": 119,
                "live_accounts": 326,
                "current_fact_bearing_accounts": 294,
            },
            "source_families": [
                {"family": "oracle conversations", "coverage": "2026-06-04..2026-07-20",
                 "use": "seed/eval references only; not reconstructable target trajectories"},
                {"family": "per-account agent", "coverage": "2026-06-25..2026-08-01",
                 "use": "different envelope; optional native-envelope auxiliary dose <=2%"},
                {"family": "neutral INT-V1", "coverage": "existing audited corpus",
                 "use": (
                     "machinery, failure lessons, neutral schema, masks, receipts, and "
                     "provider-lifecycle design only; no corpus pooling and no >=90% "
                     "corpus-reuse claim"
                 )},
            ],
            "factual_axis": {
                "legacy_warning": "61/327 fact-bearing in the 2026-07-14 snapshot",
                "legacy_snapshot_is_stale": True,
                "current_production": "294/326 fact-bearing as audited 2026-08-02",
                "required_action": "reconcile 326 live accounts vs 325 dossiers/seal before S4",
            },
            "served_contract": {
                "required": "actual dynamic Grand Steel Oracle contract",
                "incumbent": "gpt-5.6 via OpenAI Responses",
                "unrestricted_tool_count": 10,
                "dynamic_tool": "search_inventory from grandsteel-coil-tag v1",
                "historical_three_tool_int_v1_contract_valid": False,
                "snapshot_status": "pending provider-boundary capture",
                "exact_target_envelope_captures": 0,
                "historical_log_status": (
                    "87 partial persisted tool-call logs; not exact provider-boundary "
                    "target-envelope captures"
                ),
            },
            "candidate_lane": {
                "base": cfg["model_plan"].get("base_model"),
                "provider": cfg["model_plan"].get("provider"),
                "status": cfg["model_plan"].get("base_model_status"),
                "serving": cfg["training"].get("serving_mode"),
            },
            "prerequisites": prerequisites,
            "recommendation": (
                "BLOCKED — S2 must resolve the prior INT-V1 launch verdict, approve the "
                "provisional Qwen/Together lane, clear Oracle-chat source use, and freeze "
                "the actual dynamic 10-tool contract. No S4/corpus work is authorized."
            ),
            "next": "governance (S2 stream-specific decision packet; remains pending)",
        }, cfg, _sync_url(args))

    book = cfg["book"]
    census_doc = REPO / cfg["census"]["census_doc"]
    cross_check = {"census_doc": str(cfg["census"]["census_doc"]), "org_found": None}
    if census_doc.exists():
        text = census_doc.read_text()
        cross_check["org_found"] = (cfg["org"]["name"] in text) or (cfg["org"]["id"] in text)
    else:
        cross_check["org_found"] = False
        cross_check["note"] = "census doc missing on disk"

    # Tier logic mirrors the census: A = accounts + transcripts + reviews; B = book only; C = insufficient.
    if book["accounts"] > 0 and book["transcripts"] > 0 and book["reviews"] > 0:
        tier = "A"
    elif book["accounts"] >= 220:  # census B/C boundary region: "substantial book"
        tier = "B"
    else:
        tier = "C"

    qa_dose = (book["fact_bearing_accounts"] * QA_FLOOR_FACT_BEARING
               + book["zero_order_accounts"] * QA_FLOOR_SAFE_DEFAULT)
    fact_share = (book["fact_bearing_accounts"] / book["accounts"]) if book["accounts"] else 0.0

    if tier == "C":
        recommendation = "DECLINE — below dose floors; not trainable"
    elif tier == "B":
        recommendation = ("BEHAVIORAL-DEFERRED — factual QA + behavioral slices now; "
                          "real-trajectory slice after ~2-4 weeks of agent activity, folds into first monthly retrain")
    elif fact_share < 0.25:
        recommendation = ("BEHAVIORAL-DOMINANT — thin factual book: floor-only QA with entity anchoring, "
                          "trajectory/behavioral-heavy corpus")
    else:
        recommendation = "FULL — factual + behavioral + real-trajectory corpus"

    tier_match = (tier == cfg["census"]["tier"])
    status = "ok" if (tier_match and cross_check["org_found"]) else "attention"
    return write_report(slug, "census", {
        "status": status,
        "dry_run": args.dry_run,
        "inputs": {"config_book": book, "cross_check": cross_check},
        "findings": {
            "tier_computed": tier,
            "tier_in_config": cfg["census"]["tier"],
            "tier_match": tier_match,
            "fact_bearing_share": round(fact_share, 3),
            "qa_dose_estimate_pairs": qa_dose,
            "usable_real_trajectories": book["usable_runs"],
            "trajectory_selection_estimate": cfg["model_plan"]["trajectory_selection_estimate"],
            "axis_verdict": cfg["census"]["axis_verdict"],
        },
        "recommendation": recommendation,
        "next": "governance (HUMAN GATE — data-use clearance)",
        "todo_v1": "re-run live read-only PostgREST census instead of trusting the snapshot; commit the census script",
    }, cfg, _sync_url(args))


def _exported_count_from_stdout(stdout):
    """The exporter prints a JSON summary block ending with `"exported": N`.
    Returns N, or None when no summary is recognizable (older exporter output) —
    None deliberately does NOT trip the empty-export refusal, only a literal 0 does."""
    m = re.findall(r'"exported"\s*:\s*(\d+)', stdout or "")
    return int(m[-1]) if m else None


# ------------------------------------------------------------ S2 governance

def stage_governance(cfg, slug, args):
    """HUMAN GATE: per-customer data-use clearance. Never auto-passes."""
    gov = cfg["governance"]
    checklist = {
        "customer_status_confirmed": cfg["org"].get("customer_status") == "CONTRACTED",
        "data_use_clearance": gov.get("data_use_clearance") == "CLEARED",
        "dpa_signed": bool(gov.get("dpa_signed")),
        "training_rights_clause": bool(gov.get("training_rights_clause")),
        "pii_egress_cleared": bool(gov.get("pii_egress_cleared")),
        "permitted_uses_include_sft": "sft" in (gov.get("permitted_uses") or []),
    }
    all_clear = all(checklist.values())
    stream = _model_stream(cfg, args)
    architecture = None
    architecture_clear = True
    if stream == "intelligence":
        conditions = cfg.get("_stream_unblock_conditions") or {}
        heldout_seals = {
            "account_anchor_65": {
                "state": "preserved_unmodified",
                "fresh_326_account_contamination_audit": "pending",
            },
            "oracle_whole_conversation": {
                "state": "provisional_unsealed",
                "proposal": "10 conversations / 17 user turns",
            },
            "synthetic_template": {"state": "unsealed"},
            "future_traffic": {"state": "quarantine_required"},
        }
        comparator_status = {
            "model_need_policy": "not_frozen",
            "comparator_battery": "not_run",
            "required_arms": [
                "deterministic-ranker",
                "retrieval-deterministic",
                "production-gpt-5.6",
                "exact-untuned-base",
                "shared-int-v1-if-compatible",
            ],
            "customer_adapter_decision": "not_available",
        }
        contract_capture = {
            "status": conditions.get("dynamic_contract_snapshot"),
            "exact_target_envelope_captures": 0,
            "required": "live exact provider-boundary capture under current dynamic contract",
        }
        oracle_governance = {
            "status": conditions.get("oracle_chat_source_governance"),
            "required_scope": [
                "user_text",
                "assistant_text",
                "tool_arguments_results_citations",
                "train_eval_seed_uses",
                "provider_egress",
                "pii",
                "retention",
                "deletion",
            ],
        }
        provisional_base = {
            "model": cfg["model_plan"].get("base_model"),
            "status": cfg["model_plan"].get("base_model_status"),
            "provider_lane": cfg["model_plan"].get("provider"),
            "together_read_entitlement": "verified",
            "write_paid_lifecycle": "unproven_and_unauthorized",
            "k3_tuned_serverless": "blocked",
        }
        platform_token_budget = {
            "status": "not_approved",
            "ready_platform_token_count": None,
            "projected_cap_usd": None,
            "s6g_approval": "not_requested",
        }
        architecture = {
            "provisional_base": provisional_base,
            "why_over_prior_int_v1_base": "pending S2 human decision",
            "training_serving_support": "read-side Together entitlement verified; paid create/deploy not exercised",
            "int_v1_transfer_scope": (
                "machinery and design only; no corpus pooling or >=90% corpus reuse"
            ),
            "prior_int_v1_launch_verdict": conditions.get("intelligence_launch_verdict"),
            "oracle_governance": oracle_governance,
            "exact_contract_capture": contract_capture,
            "model_need_and_comparators": comparator_status,
            "held_out_seal_states": heldout_seals,
            "platform_token_budget": platform_token_budget,
            "required_contract": cfg["model_plan"].get("served_contract"),
            "approval_ready": False,
        }
        architecture_clear = (
            architecture["approval_ready"] is True
            and cfg.get("_stream_plan_status") != "blocked_on_approval"
            and all(value not in (None, "pending") for value in conditions.values())
            and cfg["model_plan"].get("base_model_status") == "approved"
        )
    decision_request = {
        "decision": f"clear {cfg['org']['name']} ({slug}) {stream} stream for SFT",
        "recommendation": (
            "approve" if all_clear and architecture_clear
            else "STOP — Intelligence architecture prerequisites remain unresolved"
            if stream == "intelligence"
            else "STOP — governance checklist incomplete"
        ),
        "shared_org_governance_reattestation": checklist,
        **({"intelligence_architecture_approval": architecture} if architecture else {}),
    }
    # 2026-08-19 (Daniel): the checklist above is a boolean compliance fact
    # (contract signed, DPA signed, PII cleared, permitted uses include SFT) —
    # not a graduated risk the way spend is. Once it's genuinely true there is
    # nothing left for a human to weigh, so requiring a SEPARATE approve-click
    # on top of it is friction, not more safety. These are established
    # customers already running nightly fine-tunes on their own agent
    # trajectories, so their checklist is expected to already be clear.
    #
    # Auto-approve exactly like stage_launch's budget.auto_approve_under_usd
    # policy (2026-07-29): the checklist enforcement itself is COMPLETELY
    # UNCHANGED below — a not-clear config still gets REFUSED, never silently
    # passed. Only the extra human-click requirement on top of an
    # already-true checklist is removed.
    #
    # `args.approved` is checked FIRST, same order as stage_launch: if Daniel
    # actually typed the flag himself, that is a real human decision and must
    # stay attributed to `human`, not get relabeled `policy` just because the
    # checklist also happened to be clear (same audit-trail-poisoning concern
    # `record_approval`'s docstring already warns about).
    actor_override = None
    if args.approved:
        if all_clear and architecture_clear:
            note = "config governance block fully clear + Daniel approved"
            record_approval(slug, "governance", note, via=APPROVED_VIA_FLAG, stream=stream)
            status = "approved"
        else:
            status = "REFUSED"
            print("[governance] --approved given but checklist is NOT clear — refusing to record approval.")
            print("             The gate protects against exactly this; fix the config truthfully first.")
    elif all_clear and architecture_clear:
        note = ("config governance block fully clear — AUTO-APPROVED (checklist-based "
                "policy, no human in the loop for this gate)")
        record_approval(slug, "governance", note, via=APPROVED_VIA_GOVERNANCE_POLICY, stream=stream)
        status = "approved"
        actor_override = "policy"
        print(f"[governance] {note}")
    else:
        status = "gate_pending"
        print("[governance] HUMAN GATE — not approved. Re-run with --approved, or approve via the Model Factory tab.")
    # Emitted AFTER write_report on purpose: `write_report`'s sync hook is what
    # ensures a `factory_runs` row (and therefore a cached run id) exists, so
    # emitting first would stamp `run_id: null` on the first gate of a fresh run.
    path = write_report(slug, "governance", {
        "status": status,
        "dry_run": args.dry_run,
        "checklist": checklist,
        "decision_request": decision_request,
        "approved_by": actor_override or ("human" if status == "approved" else None),
        "standard_pattern": {
            # The proposed standard data-use pattern (so each customer isn't a one-off — see FACTORY-PLAN.md §S2):
            "contract": "standard training-rights clause in MSA/DPA (pending Kiara — census open item)",
            "permitted_uses": "explicit per-org list: retrieval/eval/sft/preference/research",
            "pii": "tool_result blocks carry customer PII — egress clearance is per-org, recorded verbatim on the board",
            "deletion_story": "throw away the adapter (per-org LoRA); dossiers/corpora live gitignored on one machine, deletable",
            "capture": "R8 governance layer (owner, consent, permitted uses, PII class, retention, deletion key) recorded at capture",
        },
        "next": "export" if status == "approved" else "blocked until S2 prerequisites are resolved",
    }, cfg, _sync_url(args))
    _record_gate_decision(cfg, slug, "governance", args, status, decision_request,
                          actor_override=actor_override)
    return path


# ---------------------------------------------------------------- S3 export

def stage_export(cfg, slug, args):
    """Dossier export — wraps the proven org-parameterized exporter. Read-only prod."""
    platform = (REPO / cfg["paths"]["platform_repo"]).resolve()
    cmd = (f"npx tsx --env-file=.env.local {cfg['paths']['export_script']} "
           f"--org {cfg['org']['id']} --out {cfg['paths']['dossiers_out']}")
    payload = {
        "dry_run": args.dry_run,
        "wraps": "platform-alpha/scripts/export-account-dossiers.ts (--org bypasses the enabled gate; no LLM; $0)",
        "command": cmd,
        "cwd": str(platform),
        "expected_output": f"{cfg['book']['accounts']} dossiers in {cfg['paths']['dossiers_out']}/ (gitignored — customer data never committed)",
    }
    if args.dry_run:
        payload["status"] = "ok"
        print(f"[export] DRY RUN — would run in {platform}:\n         {cmd}")
    elif args.live:
        if not platform.exists():
            payload["status"] = "error"
            payload["error"] = f"platform repo not found at {platform}"
        else:
            r = subprocess.run(cmd, shell=True, cwd=platform, capture_output=True, text=True)
            payload["status"] = "ok" if r.returncode == 0 else "error"
            payload["exit_code"] = r.returncode
            payload["stdout_tail"] = r.stdout[-2000:]
            payload["stderr_tail"] = r.stderr[-2000:]
            # A zero-dossier export is NOT ok, whatever the exit code says. Found
            # 2026-07-31 (ptc-steel v6 S3): platform-alpha/.env.local pointed at the
            # empty LOCAL Supabase, the exporter printed `exported: 0`, exited 0, and
            # this stage recorded a green S3 for a dossier refresh that refreshed
            # nothing. Exit code is the wrong signal here; the exporter's own summary
            # is the real one.
            if r.returncode == 0:
                exported = _exported_count_from_stdout(r.stdout)
                payload["exported"] = exported
                if exported == 0:
                    payload["status"] = "empty"
                    payload["error"] = (
                        "exporter exited 0 but exported 0 dossiers — almost always the wrong "
                        "database (check which SUPABASE_URL is active in platform-alpha/.env.local, "
                        "or override via process env, which wins over --env-file)")
                    print(f"[export] REFUSING to mark ok: {payload['error']}")
    else:
        payload["status"] = "ok"
        print(f"[export] command emitted (pass --live to execute):\n         {cmd}")
    payload["next"] = "build"
    return write_report(slug, "export", payload, cfg, _sync_url(args))


# ----------------------------------------------------------------- S4 build

def stage_build(cfg, slug, args):
    """Corpus build — the V5-ARCHITECTURE recipe, parameterized per org.

    TODAY this is session-work (Cursor subagents at ~$0, per L18), not a push-button
    script: the PTC v5 generator chain (platform-alpha-v5/scripts/v5/) is org-specific
    in its inputs. This stage emits the parameterized WORK ORDER — a board-ready prompt
    with the exact recipe, slices, seeds, and caps for this org — which is the v0.5
    contract (D2: templated prompts, Daniel pastes).
    """
    mp = cfg["model_plan"]
    work_order = {
        "recipe": "V5-ARCHITECTURE.md corpus shape: 45% replay / 35% real tool trajectories / 20% specialist (by loss-bearing tokens, +-5pp)",
        "org_inputs": {
            "dossiers": cfg["paths"]["dossiers_out"],
            "org_id": cfg["org"]["id"],
            "system_prompt": mp["system_prompt_source"],
            "inventory_tool": mp["inventory_tool"],
            "custom_watch_items": mp["custom_watch_items"],
            "seed": mp["seed"],
        },
        "slices": mp["slices"],
        "variant_notes": {
            "behavioral-dominant": "floor-only factual QA with entity anchoring; replay teacher = untuned base (no org champion yet)",
            "full": "full QA dose + behavioral + trajectories",
        }.get(mp["variant"], ""),
        "style_caps": "semantic class <=5-7% | skeleton cluster <=1-2% | unpaired decisive <=2-3% | >=2:1 off-mode:on-mode",
        "state_conditioning": "Ground/Scope/Decline via <state> section inside the single position-0 system message",
        "reference_chain_ptc": ("cd platform-alpha-v5/scripts/v5 && python3 generate-anchor.py && python3 generate-a1-postcutoff.py && "
                                "python3 generate-a2-citecount.py && python3 generate-a3-fieldunavail.py && python3 generate-a4-fabricated.py && "
                                "python3 generate-a5-triage.py && python3 select-v5-slices.py && python3 mixture-and-assemble-v5.py --write"),
        "hard_rules": [
            "SEED-deterministic; regeneration command + script git SHA registered in DATASETS.md (a dataset that can't be regenerated doesn't exist)",
            "over-generate ~2x -> deterministic select; regenerate-never-repair (L18/L19)",
            "eval-key disjointness: diff vs EVAL_RESERVED + auditLeakage, print the diff in the report (L33)",
            ">=1/3 of every gate's members held OUT of training (L39)",
            "no unmarked closed-book value-QA (L40 / D-MEM-01)",
        ],
    }
    # C3: report the anchor's state BEFORE curation runs. S4 is not blocked on it
    # — the hard gate is at S8, where the number is actually produced — but an
    # unsealed corpus must be visible here, while there is still time to seal one,
    # rather than discovered at eval time when it is too late to fix.
    heldout = _heldout_state(slug, _model_stream(cfg, args))
    if not heldout.get("sealed"):
        print(
            "[build] WARNING: no held-out eval set is sealed for this iteration. Any paired-eval "
            "number produced later will be measured on data this curation stage can see. "
            "Seal one before S4 (factory_heldout.seal); S8 will refuse a live verdict without it.",
            file=sys.stderr,
        )
    nodes = _dispatch_stage_nodes("S4", cfg, slug, args)
    all_ok = all(n.get("ok") for n in nodes) if nodes else True
    return write_report(slug, "build", {
        "status": (
            "todo" if (args.dry_run or not nodes)
            else "needs_self_dispatch" if _pending_self_dispatch(nodes)
            else "ok" if all_ok else "error"
        ),
        "dry_run": args.dry_run,
        "heldout_anchor": heldout,
        "work_order": work_order,
        "nodes": nodes,
        "todo_v1": "parameterize the v5 generator chain into an org-generic package (generators read org config, not PTC constants)",
        "next": "verify",
    }, cfg, _sync_url(args))


# ---------------------------------------------------------------- S5 verify

def stage_verify(cfg, slug, args):
    """Deterministic verification battery — ALL must pass, $0. Wraps the proven checks."""
    platform = (REPO / cfg["paths"]["platform_repo"]).resolve()
    corpus = f"finetune-out/together-export/combined-train-{slug}-v1.jsonl"
    battery = [
        {"check": "deterministic corpus verifier (100% coverage: verbatim grounding, masks, weights, caps, probe guard)",
         "command": "python3 scripts/v5/verify-v5-corpus.py", "catalog": "#1"},
        {"check": "contradiction linter — 0 unmarked (MANDATORY, L37)",
         "command": f"python3 scripts/qa-contradiction-lint.py --corpus {corpus} --gate", "catalog": "#2"},
        {"check": "shortcut hunt (position bias, counter-instances, cue separation, sibling eff-lb ratios)",
         "command": "V5-SHORTCUT-CHECK pattern — mechanize per corpus", "catalog": "#3"},
        {"check": "reserved-key / leakage diff vs EVAL_RESERVED",
         "command": "python3 scripts/v5/reserved-diff-v5.py", "catalog": "#4"},
        {"check": "style-concentration detector (top-1 opening >=30% / top-3 >=60% / length CV <=0.20)",
         "command": "python3 scripts/preflight/style_concentration.py --self-test", "catalog": "#29"},
        {"check": "render-verify pre-upload (masks correct on the EXACT Fireworks renderer, qwen3_6)",
         "command": "python3 scripts/v5/render-verify-upload.py", "catalog": "runbook B.9-10"},
    ]
    presence = []
    for item in battery:
        script = item["command"].split()
        found = None
        if len(script) >= 2 and script[0] == "python3":
            found = (platform.parent / "platform-alpha-v5" / script[1]).exists() or (platform / script[1]).exists()
        presence.append({**item, "script_on_disk": found})
    print(f"[verify] battery has {len(battery)} all-must-pass checks (dry-run: presence-checked, not executed)")
    nodes = _dispatch_stage_nodes("S5", cfg, slug, args)
    findings = {}
    if nodes and not args.dry_run:
        # `factory_run_stages.verifier_verdict` is a real column that
        # `factory_sync._stage_row` has always read from `findings` — and that
        # nothing has ever written. This is the first writer. A verifier that
        # produced no parseable verdict yields `suspect`, never `pass`: an
        # unverified corpus and a verified-clean corpus must not look the same
        # downstream (LESSON-016).
        verdicts = [n.get("verdict") for n in nodes if n.get("role_family") == "verifier"]
        findings["verifier_verdict"] = (
            "fail" if "fail" in verdicts
            else "suspect" if (not verdicts or None in verdicts or "suspect" in verdicts)
            else "pass"
        )
    all_ok = all(n.get("ok") for n in nodes) if nodes else True
    return write_report(slug, "verify", {
        "status": (
            "needs_self_dispatch" if (not args.dry_run and _pending_self_dispatch(nodes))
            else "ok" if (args.dry_run or all_ok) else "error"
        ),
        "dry_run": args.dry_run,
        "policy": "hard fail-stop: any check fails -> stop, write report, regenerate at the root (never repair in place)",
        "battery": presence,
        "nodes": nodes,
        **({"findings": findings} if findings else {}),
        "todo_v1": "generalize PTC's v5 scripts to read the org config; run them here directly and parse pass/fail",
        "next": "project",
    }, cfg, _sync_url(args))


# --------------------------------------------------------------- S6 project

def stage_project(cfg, slug, args):
    """Cost projection with the calibrated multipliers -> a bucket-2 decision request."""
    b = cfg["budget"]
    tokens = b["ptc_reference_tokens_per_epoch"] * b["token_volume_fraction_of_ptc"]
    epochs = cfg["training"]["epochs"]
    sft_est = tokens * epochs / 1e6 * b["sft_rate_usd_per_m"]
    # trajectories unroll (L22) — apply to the trajectory-share of the corpus (approx: whole corpus, conservative)
    sft_est_unrolled = sft_est * b["unroll_multiplier"]
    all_in = sft_est_unrolled * b["dual_bucket_multiplier"] * b["projection_calibration"]
    total = all_in + b["eval_window_cap_usd"]
    over_cap = total > b["train_all_in_cap_usd"] + b["eval_window_cap_usd"]
    over_stop = total > b["stop_gate_usd"]
    decision_request = {
        "decision": f"approve training spend for {cfg['org']['name']} ({slug}) v1",
        "options": ["approve at projection", "reject", "re-scope corpus and re-project"],
        "recommendation": "approve" if not (over_cap or over_stop) else "STOP — projection exceeds cap; re-scope",
        "assumptions": [
            f"token volume = {b['token_volume_fraction_of_ptc']:.0%} of PTC reference ({b['ptc_reference_tokens_per_epoch']:,} tok/epoch)",
            f"epochs = {epochs} (frozen recipe)",
            f"unroll x{b['unroll_multiplier']} (L22), dual-bucket x{b['dual_bucket_multiplier']} (L34), calibration x{b['projection_calibration']} (runbook #6)",
            "validate against the LAST completed job's actual estimatedCost before launch (drift-stop pattern)",
            "trust platform estimatedTokenCount over tiktoken (L6/L22)",
        ],
        "evidence": [str(cfg["census"]["readiness_report"]), "company-brain/3-execution/COSTS.md"],
        "cost": {
            "sft_estimate_usd": round(sft_est_unrolled, 2),
            "train_all_in_usd": round(all_in, 2),
            "eval_window_cap_usd": b["eval_window_cap_usd"],
            "total_projection_usd": round(total, 2),
        },
        "reversibility": "high pre-launch ($0 spent); capacity-FAILED jobs bill $0; PAUSED jobs resume (L34a); champion untouched",
    }
    print(f"[project] all-in projection ${total:.2f} "
          f"(cap ${b['train_all_in_cap_usd'] + b['eval_window_cap_usd']}, stop-gate ${b['stop_gate_usd']})"
          + (" — OVER CAP, factory will refuse launch" if (over_cap or over_stop) else ""))
    return write_report(slug, "project", {
        "status": "over_cap" if (over_cap or over_stop) else "ok",
        "dry_run": args.dry_run,
        "decision_request": decision_request,
        "todo_v1": "submit via scripts/lib/validate_core.py add so the launch gate lands in validation/QUEUE.md + dashboard",
        "next": "launch (HUMAN GATE — Daniel approves the calibrated projection; no auto-launch, ever)",
    }, cfg, _sync_url(args))


# ---------------------------------------------------------------- S6g launch

def stage_launch(cfg, slug, args):
    """HUMAN GATE: spend approval. Requires an in-cap projection report AND --approved.

    Auto-approval policy (Daniel, 2026-07-29): an org config may set
    `budget.auto_approve_under_usd`. A projection at or under that number is
    approved by POLICY without waiting for a human — Daniel: "provide the
    option to allow auto allow under set budget constraints". Three properties
    keep this honest:
      1. The over-cap refusal above still runs FIRST — policy can never
         approve what the CLI itself would refuse (L17 unchanged).
      2. The approval note and the decision record both say `policy`, never
         `human` — mislabeling would poison the gate-judgment dataset
         (_record_gate_decision's own rationale).
      3. Absent config = absent behavior: no `auto_approve_under_usd`, no
         policy path, gate blocks exactly as before.
    """
    stream = _model_stream(cfg, args)
    proj_path = run_dir(slug, stream) / f"{STAGES.index('project') + 1:02d}-project-report.json"
    if not proj_path.exists():
        die("no projection on file — run --stage project first")
    proj = json.loads(proj_path.read_text())
    decision_request = proj.get("decision_request", {})
    actor_override = None
    if proj["status"] != "ok":
        # This check runs BEFORE any approval/sync check, on purpose: an
        # over-cap projection is refused regardless of a tab approval too — the
        # gate's own refusal logic still applies after approval (PLAN.md §3.2 /
        # L17), the tab cannot force a launch the CLI itself would refuse. No
        # sync check is even attempted in this branch — nothing to poll for.
        status = "REFUSED"
        print("[launch] projection is over-cap — refusing regardless of --approved (L17: quality/budget conflicts go to Daniel, not past him).")
    elif args.approved or _gate_approved_via_sync(cfg, slug, "launch", args, decision_request):
        note = (f"projection ${decision_request['cost']['total_projection_usd']} approved" if args.approved
                else f"projection ${decision_request['cost']['total_projection_usd']} approved via the Model Factory tab")
        record_approval(
            slug, "launch", note,
            via=APPROVED_VIA_FLAG if args.approved else APPROVED_VIA_TAB,
            stream=stream,
        )
        status = "approved"
    else:
        auto_cap = (cfg.get("budget") or {}).get("auto_approve_under_usd")
        total = (decision_request.get("cost") or {}).get("total_projection_usd")
        if auto_cap is not None and total is not None and float(total) <= float(auto_cap):
            note = (f"projection ${total} AUTO-APPROVED by policy: at/under "
                    f"budget.auto_approve_under_usd=${auto_cap} (no human in the loop for this gate)")
            record_approval(
                slug, "launch", note, via=APPROVED_VIA_POLICY, stream=stream
            )
            status = "approved"
            actor_override = "policy"
            print(f"[launch] {note}")
        else:
            status = "gate_pending"
            print("[launch] HUMAN GATE — review the projection report, then re-run with --approved, or approve via the Model Factory tab.")
    path = write_report(slug, "launch", {
        "status": status, "dry_run": args.dry_run,
        "projection": proj["decision_request"]["cost"],
        "approved_by": actor_override or ("human" if status == "approved" else None),
        "next": "train" if status == "approved" else "blocked",
    }, cfg, _sync_url(args))
    _record_gate_decision(cfg, slug, "launch", args, status, decision_request,
                          actor_override=actor_override)
    return path


# ----------------------------------------------------------------- S7 train

def _dispatch_launch_task(cfg, slug, args, launch_body):
    """T2-3 (model-factory-finetune-launcher-v1, PRContext-phase2.md) — the
    real trigger. Dispatches platform-alpha's `factory-launch-monitor`
    Trigger.dev task (T2-1, the config-driven TypeScript port of
    `fw_launch_monitor.py`) via `trigger_dev_rest.trigger_task`, and returns a
    trackable Trigger.dev run id.

    Mirrors `_dispatch_stage_nodes`'s resilience contract: NEVER raises. A
    dispatch failure (missing/mismatched manifest, unreachable Trigger.dev,
    no resolvable `factory_runs` id) is returned as `{"ok": False, "error":
    ...}` data, so a Trigger.dev outage degrades the S7 report rather than
    crashing the CLI. Verifies the signed manifest itself (same check as the
    Python original's `verify_manifest`, and the same check T2-1's Trigger.dev
    task repeats independently against the `datasetFileSha256` this function
    sends it) — belt-and-braces: this process has the actual corpus file on
    disk, the Trigger.dev container does not (see
    `factory-launch-monitor.ts`'s own module doc comment for that split).

    Never called under `--dry-run` (enforced by the caller, `stage_train`) —
    same "dry-run never spawns a subprocess" contract this file already
    documents for `_dispatch_stage_nodes`, extended to "never dispatches a
    real launch either."
    """
    stream = _model_stream(cfg, args)

    try:
        manifest = json.loads(Path(args.manifest).read_text())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"could not read --manifest {args.manifest}: {exc}"}

    try:
        corpus = Path(manifest["dataset_file"])
        dataset_sha256 = hashlib.sha256(corpus.read_bytes()).hexdigest()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"could not hash the manifest's dataset_file: {exc}"}

    if dataset_sha256 != manifest.get("sha256"):
        return {
            "ok": False,
            "error": (
                f"MANIFEST MISMATCH: on-disk {dataset_sha256} != signed "
                f"{manifest.get('sha256')} — the corpus changed after signing. "
                "REFUSING to dispatch (runbook B.12)."
            ),
        }
    if not manifest.get("signed_by") or not manifest.get("signed_at"):
        return {"ok": False, "error": "manifest is not signed — refusing to dispatch"}

    # The S6g approval basis (PRContext T2-1: "sourced from the S6g approval
    # basis — thread this through as a parameter, not a hardcoded env
    # default"). Falls back to FW_COST_CEILING only if the launch report is
    # somehow unreadable — the projection having already been approved by a
    # human is what makes reading it back here safe.
    cost_ceiling = None
    try:
        proj_path = run_dir(slug, stream) / f"{STAGES.index('launch') + 1:02d}-launch-report.json"
        launch_report = json.loads(proj_path.read_text())
        cost_ceiling = (launch_report.get("projection") or {}).get("train_all_in_usd")
    except Exception:  # noqa: BLE001
        pass
    if cost_ceiling is None:
        cost_ceiling = float(os.environ.get("FW_COST_CEILING", "30"))

    try:
        import factory_sync
        run_id = factory_sync.get_or_create_run_id(slug, url=_sync_url(args), stream=stream)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"could not resolve factory_runs id: {exc}"}

    # Field names match `factory-launch-monitor.ts`'s `FactoryLaunchConfig`
    # exactly (T2-1's contract) — this is the one cross-language wire
    # boundary in the whole pipeline, so the JSON keys are camelCase here
    # (the TypeScript side's idiom) even though the rest of this file is
    # snake_case.
    payload = {
        "runId": run_id,
        "datasetId": launch_body["dataset"].split("/")[-1],
        "baseModel": launch_body["baseModel"],
        "outputModelId": launch_body["outputModel"].split("/")[-1],
        "hyperparams": {
            "loraRank": launch_body["loraRank"],
            "epochs": launch_body["epochs"],
            "learningRate": launch_body["learningRate"],
            "optimizerWeightDecay": launch_body["optimizerWeightDecay"],
        },
        "costCeilingUsd": cost_ceiling,
        "manifest": {
            "datasetFile": manifest["dataset_file"],
            "sha256": manifest["sha256"],
            "signedBy": manifest.get("signed_by"),
            "signedAt": manifest.get("signed_at"),
        },
        "datasetFileSha256": dataset_sha256,
    }

    try:
        import trigger_dev_rest
        trigger_run_id = trigger_dev_rest.trigger_task(
            "factory-launch-monitor",
            payload,
            options={"idempotencyKey": f"factory-launch-{run_id}", "idempotencyKeyTTL": "1d"},
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"trigger.dev dispatch failed: {exc}"}

    return {
        "ok": True,
        "trigger_run_id": trigger_run_id,
        "factory_run_id": run_id,
        "config": payload,
    }


def stage_train(cfg, slug, args):
    """Train + monitor. S6g (`stage_launch`) stays the only spend-approval
    gate — unchanged, still a human decision — and this stage is unreachable
    while it is unapproved (`check_upstream_gates` in `main()` refuses any
    later stage until every earlier `HUMAN_GATES` entry is approved; this
    function does not re-check that itself, same as `stage_eval`/`stage_ship`
    trusting the CLI-level gate).

    T2-3: once reachable, this stage dispatches the REAL launch — T2-1's
    `factory-launch-monitor` Trigger.dev task, which makes the actual `POST
    supervisedFineTuningJobs` call — via `_dispatch_launch_task`, in addition
    to (not instead of) the existing S7 coding-agent node chain, which may
    still have real prep work to do ahead of a launch. Dispatch requires
    `--manifest <path>`: factory.py has no automated manifest-signing step
    anywhere in S1-S6g today, so a signed manifest must be produced the same
    way Grand Steel's was and passed explicitly — this stage REFUSES to
    dispatch without one, exactly like the Python original refused to launch
    without one.
    """
    t = cfg["training"]
    body = {
        "dataset": f"accounts/emanate/datasets/combined-{slug}-v1-train",
        "baseModel": cfg["model_plan"]["base_model"],
        "outputModel": cfg["model_plan"]["output_model"],
        "loraRank": t["lora_rank"],
        "epochs": t["epochs"],
        "learningRate": t["learning_rate"],
        "optimizerWeightDecay": t["weight_decay"],
    }
    nodes = _dispatch_stage_nodes("S7", cfg, slug, args)
    all_ok = all(n.get("ok") for n in nodes) if nodes else True

    dispatch = None
    if args.dry_run or not nodes:
        print("[train] launch body + monitor contract emitted; S7 chain resolved but nothing spawned (dry run).")
    elif not all_ok:
        print("[train] S7 node chain failed — not attempting the real launch dispatch.", file=sys.stderr)
    elif not args.manifest:
        dispatch = {
            "ok": False,
            "error": (
                "no --manifest supplied — factory.py has no automated manifest-signing step "
                "(S1-S6g never produce one); a signed dataset manifest must be created the same "
                "way Grand Steel's was, then passed via --manifest. REFUSING to dispatch the real "
                "launch (runbook B.12)."
            ),
        }
        print(f"[train] {dispatch['error']}", file=sys.stderr)
    else:
        dispatch = _dispatch_launch_task(cfg, slug, args, body)
        if dispatch.get("ok"):
            print(f"[train] DISPATCHED factory-launch-monitor trigger_run_id={dispatch['trigger_run_id']}")
        else:
            print(f"[train] launch dispatch failed: {dispatch.get('error')}", file=sys.stderr)

    if args.dry_run or not nodes:
        status = "todo"
    elif _pending_self_dispatch(nodes):
        status = "needs_self_dispatch"
    elif not all_ok:
        status = "error"
    elif dispatch and dispatch.get("ok"):
        status = "dispatched"
    else:
        status = "blocked"

    return write_report(slug, "train", {
        "status": status,
        "dry_run": args.dry_run,
        "nodes": nodes,
        "launch_body": body,
        # Fireworks' own REST payload has no loraAlpha/targetModules field (a real
        # vendor limitation, confirmed by reading `body` above) — this is the full
        # configured hyperparameter set, independent of what the API happens to
        # accept, so build_iteration_digest() has something to read besides launch_body.
        "training_config": dict(t),
        "endpoint": "POST /v1/accounts/emanate/supervisedFineTuningJobs",
        "trigger_dispatch": dispatch,
        "monitor_contract": [
            "upload via scripts/d3/fw_upload_dataset.py after render-verify",
            "sign + store the immutable dataset manifest BEFORE launch (runbook B.12)",
            "monitor via fw_monitor.py pattern: auto-resume on JOB_STATE_PAUSED (180s cooldown); NEVER delete+relaunch (L34a)",
            "HTTP 412 = reads blind, job state UNKNOWN — halt recommendations, require ground-truth dashboard read (L43)",
            "capacity-FAILED bills $0 -> auto-retry is cost-safe",
            "record everything per runbook D (provenance, hyperparams, per-slice metrics, wall-clock+cost)",
        ],
        "todo_v1": "org-parameterized fw_autorun (launch + monitor + cost-gate kill at 0% if estimatedCost x2 exceeds approval)",
        "next": "eval",
    }, cfg, _sync_url(args))


# ------------------------------------------------------------------ S8 eval

def stage_eval(cfg, slug, args):
    """Paired eval battery vs the org's baseline — stub: emits the one-window contract."""
    # C3: this is where the anchor has teeth. A live eval with no sealed held-out
    # set produces a number measured on data the curator saw — which is worse than
    # no number, because it is believed. Fail-CLOSED here, deliberately, unlike the
    # projection/logging paths which are fail-open: the asymmetry is the point
    # (PRRules rule 4).
    heldout = _heldout_state(slug, _model_stream(cfg, args))
    separation = _judge_generator_separation()
    anchor_ok = bool(heldout.get("sealed"))
    if not args.dry_run and not anchor_ok:
        print(
            "[eval] REFUSING a live paired eval: no held-out set is sealed for this iteration. "
            "The verdict would be graded on data the corpus curator could see.",
            file=sys.stderr,
        )
    nodes = [] if (not args.dry_run and not anchor_ok) else _dispatch_stage_nodes("S8", cfg, slug, args)
    all_ok = all(n.get("ok") for n in nodes) if nodes else True
    if args.dry_run:
        print("[eval] one-window contract emitted; S8 chain resolved but nothing spawned (dry run).")
    if args.dry_run:
        status = "todo"
    elif not anchor_ok:
        status = "REFUSED"
    elif _pending_self_dispatch(nodes):
        status = "needs_self_dispatch"
    else:
        status = "ok" if all_ok else "error"
    return write_report(slug, "eval", {
        "status": status,
        "dry_run": args.dry_run,
        "nodes": nodes,
        "heldout_anchor": heldout,
        # C3: recorded on every S8 report so a reviewer can see the anchor held for
        # THIS run, rather than trusting that it held in general.
        "judge_generator_separation": separation,
        "window_contract": [
            "ONE dedicated window (2x accelerators, ~$14/hr); assertDedicatedRouting() on every call (L34-routing)",
            "FC smoke FIRST — fail -> teardown <=$1 (#15/L28)",
            "paired lexicographic battery vs baseline: Gate1 invariants 0-margin -> Gate2 specialists <=2pp -> Gate3 conversational -> Gate4 operational (#31)",
            f"baseline: {cfg['champion']['baseline']} (org champion: {cfg['champion']['model_id']}) — L41: gates baselined on incumbent BEFORE training",
            "self-auditing graders DEFAULT-ON: A1 verdict self-audit (>10% disagreement -> GRADER-SUSPECT STOP), A2 cross-gate linter, A3 fresh-eyes (#38-40)",
            "teardown verified: GET deployments must show NONE; serverless bucket ~$0 or investigate",
        ],
        "command_pattern": ("npx tsx --env-file=.env.local scripts/eval-custom-model.ts --base-url https://api.fireworks.ai/inference/v1 "
                            f"--model 'accounts/emanate/models/{slug}-v1-agent#<deployment>' --key-env FIREWORKS_API_KEY "
                            "--extra-body-json '{\"reasoning_effort\":\"none\"}'"),
        "todo_v1": "wrap eval-all.ts --paired with org config; parse the verdict JSON into this report",
        "next": "ship (HUMAN GATE)",
    }, cfg, _sync_url(args))


# ------------------------------------------------------------------ S9 ship

def stage_ship(cfg, slug, args):
    """HUMAN GATE: ship call on the lexicographic verdict + accepted-risks table.

    This records the GO/NO-GO decision only. There is no automated ship action
    behind it today — putting a model live means Daniel wiring it into the
    A/B-logging pipeline by hand (real code, real endpoints, a real PR). See
    `factory_stages.HUMAN_GATES`'s comment for the future-automation note.
    """
    decision_request = {
        "decision": f"GO/NO-GO ship call for {cfg['org']['name']} ({slug}) v1",
        "recommendation": "pending-human-judgment",
        "reversibility": "NO-GO: champion never overwritten, $0 (L35 rigor phase before any retrain). "
                          "GO: activate (S10) commits to a live model.",
    }
    # Three-way, not two-way (factory-graph-pr1 B1). `--approved` is still the
    # full local bypass that never touches the network (D4), so the sync status
    # is only consulted when it wasn't passed. The third state is the point: a
    # literal `rejected` is a DECIDED no, and must be recorded as terminal, while
    # `pending` / `withdrawn` / unknown all stay `gate_pending`. Before this, all
    # of them collapsed into `gate_pending` and `factory_runs.status` could never
    # reach `no_go` at all — see `factory_sync._run_status_for`'s docstring.
    gate_status = None if args.approved else _gate_status_via_sync(cfg, slug, "ship", args, decision_request)
    no_go_reason = None
    if args.approved or gate_status == "approved":
        note = "Daniel ship call: GO" if args.approved else "ship call: GO, approved via the Model Factory tab"
        record_approval(
            slug, "ship", note,
            via=APPROVED_VIA_FLAG if args.approved else APPROVED_VIA_TAB,
            stream=_model_stream(cfg, args),
        )
        status = "approved"
    elif gate_status == "rejected":
        status = "no_go"
        # Read the human's rationale into the report so the NEXT run's context
        # pack can carry "why the last one didn't ship" — the highest-signal
        # input a retrain has (RETRAIN-AND-META-LEARNING.md §1). Fail-soft: the
        # NO-GO stands whether or not the note is retrievable.
        no_go_reason = _gate_rejection_note(slug, "ship", args)
        who = (no_go_reason or {}).get("decided_by_email") or "the Model Factory tab"
        why = (no_go_reason or {}).get("note") or "(no rationale recorded)"
        print(f"[ship] HUMAN GATE — NO-GO recorded by {who}: {why}")
        print("[ship] champion stays live, $0. Mandatory rigor phase (L35) before any retrain is designed.")
    else:
        status = "gate_pending"
        print("[ship] HUMAN GATE — GO/NO-GO on the paired verdict. NO-GO -> champion stays, $0 rigor phase before any retrain (L35).")
    path = write_report(slug, "ship", {
        "status": status, "dry_run": args.dry_run,
        # Local-only by design: `no_go_reason` is deliberately NOT added to
        # `factory_sync.SAFE_REPORT_KEYS`, so it never reaches the DB. The tab
        # already renders the authoritative copy from `factory_gate_decisions`;
        # widening the PII allow-list to duplicate it would be pure added risk.
        **({"no_go_reason": no_go_reason} if no_go_reason else {}),
        "decision_request": decision_request,
        "verdict_contract": {
            "input": "paired lexicographic verdict (eval report) + accepted-risks table (every consciously-accepted hole, Daniel-signed — ERROR-REGISTER disposition d)",
            "no_go_path": "champion never overwritten; failures harvested as data; mandatory $0 rigor phase (L35) before retrain design",
            "go_path": "activate (S10)",
        },
        "next": "activate" if status == "approved" else ("rigor-phase (L35)" if status == "no_go" else "blocked"),
    }, cfg, _sync_url(args))
    _record_gate_decision(cfg, slug, "ship", args, status, decision_request,
                          decided_at=(no_go_reason or {}).get("decided_at"))
    # B2 (factory-observability-v1): attribute any `--lesson` codes this decision
    # names to the run that earned them. After `_record_gate_decision` so the
    # decision is logged first — attribution is metadata about a decision that has
    # already been recorded, never a precondition for recording it.
    _attribute_lessons(slug, args, status)
    # B4: snapshot this iteration BEFORE a retrain can re-enter at S4 and
    # overwrite the very reports the digest is built from. Written on every
    # outcome, not just a GO — a NO-GO iteration is the one a successor most
    # needs to know about.
    _write_iteration_digest(slug, "ship", args)
    return path


# ------------------------------------------------------------- S10 activate

def stage_activate(cfg, slug, args):
    """Serve + activate runbook — emitted, never executed (prod write is Daniel-run in v0.5)."""
    nodes = _dispatch_stage_nodes("S10", cfg, slug, args)
    all_ok = all(n.get("ok") for n in nodes) if nodes else True
    print("[activate] runbook emitted — prod writes stay human-executed in v0.5.")
    return write_report(slug, "activate", {
        # Stays "runbook" on success: S10 wires an agent that PRODUCES the runbook,
        # it does not become an executor (PRRules rule 15). Only a failed node
        # changes the status, and it must — see `_run_status_for`, which maps a
        # completed activate to a shipped run.
        "status": (
            "needs_self_dispatch" if (not args.dry_run and _pending_self_dispatch(nodes))
            else "runbook" if (args.dry_run or all_ok) else "error"
        ),
        "dry_run": args.dry_run,
        "nodes": nodes,
        "serving_decision": {
            "mode_in_config": cfg["serving"]["mode"],
            "inputs": "SERVING-RESEARCH-2026-07 §7.3 (multi-customer roadmap): 1-10 customers = shared base + per-request adapters (rank-32 standardized, session affinity); dedicated only if isolation/SLA demands",
            "blocking_vendor_questions": "SERVING-RESEARCH §5.7 — adapters per deployment, hot-swap latency, mixed-adapter batching, FP8/BF16 multi-LoRA, cache policy, routing overhead (get WRITTEN answers + 1/10/50-adapter load test first)",
        },
        "runbook": [
            "deploy adapter: scale-to-zero on-demand, 2x accelerators min (~$14/hr active, $0 idle, ~3.5min cold start)",
            "env: CUSTOM_MODEL_TOOLS_DISABLED per model capability; TOGETHER_BASE_URL/TOGETHER_API_KEY -> Fireworks values (historical names)",
            f"INSERT INTO org_custom_models (org_id, custom_model_id, trained_at, status) VALUES ('{cfg['org']['id']}', '<deployed endpoint name — NOT the training output-model name>', '<ts>', 'active');",
            "rollback restores the FULL config: base digest, adapter, scaling, system prompt, tool schemas, decoding params, runtime version, policy layer, evaluator version (runbook F)",
        ],
        "next": "retrain (monthly, proposed-not-scheduled)",
    }, cfg, _sync_url(args))


# -------------------------------------------------------------- S11 retrain

def stage_retrain(cfg, slug, args):
    """Monthly retrain loop — auto-PROPOSED, human-approved. Never self-schedules."""
    print("[retrain] proposal emitted. NOT registered as a scheduled job (PROCESSES.md gate) — by design.")
    path = write_report(slug, "retrain", {
        "status": "proposed", "dry_run": args.dry_run,
        "loop": "TRAINING-RUNBOOK §B: freeze snapshot IDs -> diff vs prior month -> regenerate changed (SEED-pinned) -> reuse by content hash -> remove obsolete -> dedup/contamination -> render-verify -> manifest",
        "train_policy": "always from frozen base, never the previous adapter (warm-start = challenger-only after 3-6 clean cycles); champion-challenger via same S5-S9 gates",
        "drift_triggers": "account/fact diff vs prior month above threshold; unresolved incident regression cases; capability anchors stay 20-30%",
        "scheduling": "each cycle re-enters this pipeline at S4 with the same human gates; a scheduled trigger requires PROCESSES.md registration + Daniel sign-off first",
        "run_kind_next": _classify_run_kind(slug, _model_stream(cfg, args)),
        "next": "(loop)",
    }, cfg, _sync_url(args))
    # Refresh at S11: S10 may have added activation detail after S9 wrote the
    # first copy, and this is the last moment before the loop re-enters at S4.
    _write_iteration_digest(slug, "retrain", args)
    return path


HANDLERS = {
    "census": stage_census, "governance": stage_governance, "export": stage_export,
    "build": stage_build, "verify": stage_verify, "project": stage_project,
    "launch": stage_launch, "train": stage_train, "eval": stage_eval,
    "ship": stage_ship, "activate": stage_activate, "retrain": stage_retrain,
}


def print_status(slug, stream="per-account"):
    d = run_dir(slug, stream)
    print(f"factory status — {slug} [{stream}]")
    for i, stage in enumerate(STAGES, 1):
        report = d / f"{i:02d}-{stage}-report.json"
        gate = " [HUMAN GATE]" if stage in HUMAN_GATES else ""
        if stage in HUMAN_GATES and gate_approved(slug, stage, stream=stream):
            mark = "APPROVED"
        elif report.exists():
            mark = json.loads(report.read_text()).get("status", "?")
        else:
            mark = "-"
        print(f"  {STAGE_CODES[stage]:>3} {stage:<11}{gate:<13} {mark}")


def build_parser():
    """The CLI surface, extracted from `main()` so it can be asserted against
    without invoking the pipeline (factory-observability-v1 B2 — a new flag whose
    only proof was "it appears in --help" is not proven).
    """
    p = argparse.ArgumentParser(prog="factory", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("org", help="org slug (configs/<slug>.yaml)")
    p.add_argument(
        "--stream",
        choices=MODEL_STREAMS,
        default="per-account",
        help="model stream (default: per-account)",
    )
    p.add_argument("--stage", choices=STAGES)
    p.add_argument("--status", action="store_true", help="show per-stage status for this org")
    p.add_argument("--dry-run", action="store_true", help="never spawn a subprocess; read-only")
    p.add_argument("--approved", action="store_true", help="record human approval at a gate stage (Daniel only)")
    p.add_argument(
        "--lesson",
        action="append",
        metavar="CODE",
        help="canon lesson code (e.g. L41, E12) this iteration earned; repeatable. "
             "Attributed to this run at S9 on a real GO/NO-GO, first-mint-wins.",
    )
    p.add_argument("--live", action="store_true", help="actually execute wrapped commands (S3 export only, read-only prod)")
    p.add_argument("--sync-url", default=None,
                    help="override FACTORY_SUPABASE_URL for this invocation (T11 run/stage push, T12 gate poll)")
    p.add_argument(
        "--manifest",
        default=None,
        help="path to the signed dataset manifest JSON (runbook B.12: {dataset_file, sha256, "
             "signed_by, signed_at}), required by `--stage train` to dispatch the real launch "
             "(T2-3). factory.py has no automated manifest-signing step (S1-S6g never produce "
             "one) — this must be created the same way Grand Steel's was.",
    )
    p.add_argument(
        "--check-run-id",
        default=None,
        metavar="TRIGGER_RUN_ID",
        help="poll a previously dispatched factory-launch-monitor Trigger.dev run "
             "(the trigger_run_id from --stage train's report) and print its status as JSON; "
             "read-only, no spend. `org` is still required by the parser but unused here.",
    )
    p.add_argument(
        "--self-dispatch-result",
        action="append",
        default=[],
        metavar="NODE_ID=PATH",
        help="repeatable; resumes a pending executor:self_dispatch node from a prior run of this "
             "same --stage (ENVIRONMENT.md §5). PATH is a JSON file shaped {final_response, "
             "cost_usd, exit_code} — what your own Task-dispatched sub-agent produced for that "
             "node_id (e.g. S4:corpus-planner).",
    )
    return p


def main():
    args = build_parser().parse_args()

    if args.check_run_id:
        import trigger_dev_rest
        print(json.dumps(trigger_dev_rest.get_run(args.check_run_id), indent=2))
        return

    if not re.fullmatch(r"[a-z0-9-]+", args.org):
        die("org slug must be kebab-case")
    cfg, cfg_path = load_config(args.org, args.stream)

    if args.status:
        print_status(args.org, args.stream)
        return
    if not args.stage:
        die("pass --stage <stage> or --status")

    if (
        args.stream == "intelligence"
        and args.stage not in INTELLIGENCE_SETUP_ONLY_STAGES
    ):
        write_report(args.org, args.stage, {
            "status": "RELEASE_BLOCKED",
            "blocked_on": "org_scoped_execution_lease",
            "dry_run": args.dry_run,
            "approval_flag_ignored": bool(args.approved),
            "note": (
                "Intelligence is setup-only: S1/S2 read/setup may coexist with the "
                "per-account stream, but S3 and later are refused until a separately "
                "implemented atomic org-scoped execution lease exists. Sibling-row "
                "checks are not an execution lease."
            ),
        }, cfg, _sync_url(args), sync=False)
        die(
            f"stage '{args.stage}' is release-blocked for Intelligence until an "
            "atomic org-scoped execution lease exists",
            code=3,
        )

    blocked_on = check_upstream_gates(args.org, args.stage, args.stream)
    if blocked_on and args.stage not in HUMAN_GATES:
        write_report(args.org, args.stage, {
            "status": "BLOCKED_ON_GATE", "blocked_on": blocked_on, "dry_run": args.dry_run,
            "note": f"human gate '{blocked_on}' is unapproved — the factory does not proceed past a human gate",
        }, cfg, _sync_url(args))
        die(f"stage '{args.stage}' is blocked on unapproved human gate '{blocked_on}'", code=3)

    print(f"factory: {cfg['org']['name']} ({args.org}) [{args.stream}] — stage {args.stage}"
          + (" [DRY RUN]" if args.dry_run else ""))
    global _STAGE_STARTED_AT
    _STAGE_STARTED_AT = now()
    HANDLERS[args.stage](cfg, args.org, args)


if __name__ == "__main__":
    main()
