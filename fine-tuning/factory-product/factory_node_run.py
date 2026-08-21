"""B3 (factory-graph-pr1, 2026-07-27): dispatch a factory node to a real agent.

`EXECUTION-DESIGN.md`'s runtime dispatch, implemented. Turns an AgentSpec plus a
node id into one `scripts/tier2_run.sh` invocation, then records what happened.

## Why this calls `tier2_run.sh` instead of `claude -p` directly

`tier2_run.sh` is not a thin convenience wrapper — it is where every safety
property this dispatch needs already lives, proven in production:

  * the `STOP` sentinel check (a single file halts all headless spend),
  * the Fable refusal (`I7`: no scheduled job runs on the expensive model),
  * a HARD wall-clock `killpg` timeout (macOS has no GNU `timeout`; a hung
    headless call used to run for hours — that was the root cause of the
    2026-07-04..07 missed jobs),
  * `--max-turns` and `--max-budget-usd` caps,
  * the `tier2` ledger row with real token usage and cost.

Reimplementing any of that here would mean maintaining two copies of the spend
guardrails, and the copy that drifts is always the one that isn't in the
critical path of everything else. So this module builds an argv and shells out.
`tier2_run.sh` itself is **not modified by this PR.**

## Why Python and not `factory_node_run.sh`

`PRContext.md`'s B3 names a `.sh` wrapper. It is implemented in Python instead,
and the reason is concrete rather than stylistic: the wrapper's actual job is
parsing YAML AgentSpecs, applying defaults, and validating a tool allow-list.
Doing that in bash means either a yaml-to-json shell-out or string munging on
YAML, both of which are how tool-allowlist bugs get written. `factory.py` is
already Python and imports this directly, so a shell shim would add a process
boundary and buy nothing. Recorded as a deviation in `PRDone.md`, not made
silently.

## What the specs do NOT carry (finding F6)

`specs/schema.json` has `"additionalProperties": false` and defines no model,
budget, turn-cap, or tool-allowlist field — so "resolve the spec's model/budget"
had nothing to resolve. Rather than bury the numbers in this file, PR 1 adds an
**optional** `runtime` block to the schema so a spec can state its own budget and
have it reviewed like everything else. The 33 existing specs don't have one and
still validate; they fall back to `ROLE_DEFAULTS` below, which are deliberately
conservative. Populating real per-spec budgets is follow-up work, not something
to guess at here.
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

# One number, not two: `factory_sync` re-applies this cap defensively on anything
# reaching the DB, and a second literal here is how the two drift.
from factory_sync import STDERR_TAIL_MAX_CHARS

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SPECS_DIR = HERE / "specs"
TIER2 = REPO / "scripts" / "tier2_run.sh"

# Conservative fallbacks for specs with no `runtime` block. Chosen to be cheap
# rather than capable: a node that needs more should say so in its own spec,
# where the number is visible and reviewable, instead of inheriting a large
# budget by silence.
#
# C2 (factory-graph-pr2) added `platform`, `grader` and `eval-prep`. PR 1's table
# covered exactly the role families S4/S5 dispatch, which was correct then and
# became a trap the moment S7/S8/S10 were wired: their specs are `platform`,
# `grader` and `eval-prep`, none of which had an entry, so all six would have
# silently inherited FALLBACK_RUNTIME — haiku, 10 turns, $0.50. Dispatching
# `train-launcher`, whose job is to launch a real fine-tuning run against a
# signed dataset manifest, on a 10-turn haiku budget is not a conservative
# default. It is a broken one, and it fails by running out of turns halfway
# through something expensive rather than by refusing.
#
# Only the families this PR actually dispatches are added. `shadow`,
# `expert-lens`, `forensics`, `coordinator` and the rest still fall back, because
# guessing a budget for a spec nothing runs yet is how unreviewed numbers get
# into the critical path (PR 1 debt item 6 — real per-spec budgets are follow-up
# work, and belong in each spec's own `runtime` block where they get reviewed).
ROLE_DEFAULTS = {
    "builder": {"model": "sonnet", "max_turns": 30, "budget_usd": 2.00},
    "verifier": {"model": "sonnet", "max_turns": 25, "budget_usd": 1.50},
    "reader": {"model": "haiku", "max_turns": 15, "budget_usd": 0.50},
    "consolidator": {"model": "sonnet", "max_turns": 20, "budget_usd": 1.00},
    "packager": {"model": "haiku", "max_turns": 10, "budget_usd": 0.30},
    # Infrastructure work: reads configs, emits launch bodies, verifies a
    # deployment actually tore down. Multi-step and consequential.
    "platform": {"model": "sonnet", "max_turns": 35, "budget_usd": 2.50},
    # Grading. Deliberately NOT the cheapest tier: a grader that runs out of
    # budget mid-battery and reports what it has produces a passing verdict on a
    # partial eval, which is the self-inflation failure mode the charter's
    # anchors exist to prevent.
    "grader": {"model": "sonnet", "max_turns": 30, "budget_usd": 2.00},
    # Builds the eval instrument before the window opens.
    "eval-prep": {"model": "sonnet", "max_turns": 25, "budget_usd": 1.50},
}
FALLBACK_RUNTIME = {"model": "haiku", "max_turns": 10, "budget_usd": 0.50}

DEFAULT_ALLOWED_TOOLS = ("Read", "Grep", "Glob", "Write", "Edit", "Bash")

# A dispatched node must not be able to spawn its own subagents. Nested spawn
# means unbounded fan-out under a budget cap that only measures the parent —
# the cap stops being a cap. This is enforced here (not merely documented)
# because it is the one tool whose presence silently invalidates every spend
# guarantee above it.
FORBIDDEN_TOOLS = frozenset({"Task"})

# The S4 chain, in dependency order: plan the mixture, generate the families,
# read/critique the result, then consolidate into one curated corpus decision.
S4_CHAIN = ("corpus-planner", "corpus-family-generator", "corpus-reader", "curation-consolidator")
S5_CHAIN = ("preflight-linter",)

# C2 (factory-graph-pr2). Each chain is a dependency order, which is what makes
# `dispatch_stage`'s stop-at-first-failure correct rather than merely cautious.
#
# S7 rehearses before it launches. `launch-rehearsal` exists precisely so the
# expensive, hard-to-undo step is not the first time the launch path runs — and
# a rehearsal that runs *after* the launch is theatre.
S7_CHAIN = ("launch-rehearsal", "train-launcher")

# S8 builds the instrument, runs the window, then audits the grader. The audit is
# last because it grades the grading: `grader-auditor` checks verdict
# self-consistency, and there is nothing to check before the window has run.
S8_CHAIN = ("eval-instrument-builder", "eval-window-runner", "grader-auditor")

# S10 emits the serving runbook. One node, and it stays a runbook — S10 never
# performs the production write (PRRules rule 15; prod writes are human-executed
# in v0.5).
S10_CHAIN = ("serve-wire",)

STAGE_CHAINS = {
    "S4": S4_CHAIN,
    "S5": S5_CHAIN,
    "S7": S7_CHAIN,
    "S8": S8_CHAIN,
    "S10": S10_CHAIN,
}

# The curation side of the judge/generator boundary (charter §5.3). Kept next to
# the chains so the two cannot drift: C3's separation checks read these, and a
# spec that appears in both sets would make the Held-Out Eval anchor decorative.
CURATION_STAGES = ("S4",)
GRADING_STAGES = ("S8",)

_VERDICT_RE = re.compile(r"^\s*VERDICT\s*[:=]\s*(pass|fail|suspect)\b", re.IGNORECASE | re.MULTILINE)
VALID_VERDICTS = frozenset({"pass", "fail", "suspect"})


class NodeDispatchError(RuntimeError):
    pass


def load_spec(slug_or_path):
    path = Path(slug_or_path)
    if not path.is_file():
        path = SPECS_DIR / f"{slug_or_path}.yaml"
    if not path.is_file():
        raise NodeDispatchError(f"no AgentSpec found for {slug_or_path!r} (looked at {path})")
    return yaml.safe_load(path.read_text())


def resolve_runtime(spec):
    """Spec's own `runtime` block, else the role-family default, else the
    fallback. Explicit beats inherited at every level."""
    base = dict(ROLE_DEFAULTS.get(spec.get("role_family"), FALLBACK_RUNTIME))
    base.update({k: v for k, v in (spec.get("runtime") or {}).items() if v is not None})
    return base


def resolve_allowed_tools(spec):
    """The tool allow-list, with `Task` removed unconditionally.

    Stripped rather than rejected: a spec asking for `Task` is a spec written
    against a capability this runtime does not offer, and failing the whole
    dispatch over it would be a worse outcome than running it without nested
    spawn. The removal is logged so it is never silent.
    """
    tools = list((spec.get("runtime") or {}).get("allowed_tools") or DEFAULT_ALLOWED_TOOLS)
    kept = [t for t in tools if t not in FORBIDDEN_TOOLS]
    if len(kept) != len(tools):
        log.warning(
            "factory_node_run: stripped %s from %s's allowed tools — a dispatched node may not nest-spawn",
            sorted(FORBIDDEN_TOOLS & set(tools)),
            spec.get("slug"),
        )
    return kept


def node_id_for(stage_code, spec_slug=None):
    """Decision D2's vocabulary: a stage code, optionally agent-qualified."""
    return f"{stage_code}:{spec_slug}" if spec_slug else stage_code


def build_prompt(
    spec, node_id, run_id, graph_id, org_slug, context_pack=None,
    model_stream="per-account",
):
    """Render the AgentSpec into the prompt text the agent actually receives.

    Deliberately mechanical — every line traces to a spec field. The spec is the
    contract; this function must not add instructions of its own, or the
    reviewed artifact stops being what actually ran.
    """
    identity = f"Node: {node_id} | run: {run_id} | graph: {graph_id} | org: {org_slug}"
    if model_stream != "per-account":
        identity += f" | stream: {model_stream}"
    lines = [
        f"# {spec['slug']} — {spec['mission']}",
        "",
        identity,
        "",
        "## Read first",
    ]
    lines += [f"- {item}" for item in spec.get("read_first", [])]
    if context_pack:
        lines += ["", "## Context pack"] + [f"- {item}" for item in context_pack]
    lines += ["", "## Mandate"]
    for m in spec.get("mandate", []):
        lines.append(f"- {m['task']}")
        lines.append(f"  - STOP OR ASK: {m['stop_or_ask']}")
    lines += ["", "## Laws (binding)"]
    lines += [f"- {law}" for law in spec.get("laws", [])]
    lines += ["", "## Done when", spec.get("done_when", ""), "", "## Final response shape",
              spec.get("final_response_shape", "")]
    if spec.get("role_family") == "verifier":
        lines += [
            "",
            "## Required machine-readable verdict",
            "End your final response with a line of exactly this form:",
            "VERDICT: pass|fail|suspect",
            "Use `suspect` when the checks could not be run to completion. Do not omit this line — "
            "a missing verdict is treated as a failure, never as a pass.",
        ]
    return "\n".join(lines)


def build_tier2_argv(spec, node_id, prompt_file, timeout_s=900):
    runtime = resolve_runtime(spec)
    tools = resolve_allowed_tools(spec)
    return [
        str(TIER2),
        "--label", f"factory-node-{node_id.replace(':', '-')}",
        "--model", str(runtime["model"]),
        "--max-turns", str(runtime["max_turns"]),
        "--budget-usd", f"{float(runtime['budget_usd']):.2f}",
        "--allowed-tools", ",".join(tools),
        "--timeout", str(timeout_s),
        "--prompt-file", str(prompt_file),
    ]


def parse_verifier_verdict(text):
    """Extract `VERDICT: pass|fail|suspect`, or `None` if absent.

    `None` is NOT a pass. LESSON-016: a process that exits 0 having logged
    per-item errors is the normal failure mode here, so the verdict must come
    from parsed content or not at all. Callers treat `None` as a stage failure.
    """
    if not text:
        return None
    matches = _VERDICT_RE.findall(text)
    if not matches:
        return None
    # Last wins: an agent that reconsiders mid-response ends on its real answer.
    return matches[-1].lower()


def _self_dispatch_state_paths(org_slug, node_id, model_stream="per-account"):
    """Where a self_dispatch node's prompt and pause/resume state live on disk
    — the same `runs/<org>/prompts/` directory tier2 already writes prompts
    into, plus a small state file carrying what the first call computed
    (the pre-dispatch gate snapshot, the start time) that a SECOND, entirely
    fresh Python process needs back on resume."""
    prompt_dir = HERE / "runs" / org_slug
    if model_stream != "per-account":
        prompt_dir = prompt_dir / model_stream
    prompt_dir = prompt_dir / "prompts"
    safe_id = node_id.replace(":", "-")
    return prompt_dir, prompt_dir / f"{safe_id}.md", prompt_dir / f"{safe_id}.self-dispatch-state.json"


def _dispatch_self(
    spec, node_id, stage_code, run_id, graph_id, org_slug, result,
    context_pack=None, model_stream="per-account", ledger_dir=None,
    sync_url=None, self_dispatch_result=None,
):
    """`executor: self_dispatch` (ENVIRONMENT.md §5) — for a runtime that IS a
    live Claude Code agent with its own `Task` tool (the cloud-dispatch
    routine session), dispatching a sub-agent needs no credential this
    process can shell out for. But `factory.py`'s own Python process cannot
    call `Task` itself — only a live agent turn can — so this cannot be one
    synchronous call the way tier2/cursor_sdk are. It is a two-call protocol:

    1. First call (`self_dispatch_result=None`): render the prompt with the
       exact same `build_prompt()` every executor uses, persist the
       pre-dispatch gate snapshot and start time to disk (this process is
       about to exit; nothing survives in memory), and return a
       `self_dispatch_required` result telling the ORCHESTRATING agent to
       dispatch that prompt itself via its own `Task` tool, then re-invoke
       this exact stage with the result.
    2. Second call (`self_dispatch_result={"final_response", "cost_usd",
       "exit_code"}`): reload the persisted state, hard-refuse if the
       reported cost is missing or exceeds the spec's own `budget_usd`
       (tier2_run.sh enforces its `--max-budget-usd` cap by killing the
       process; nothing here can kill a `Task` call mid-flight, so this is a
       strictly weaker, after-the-fact, self-reported guarantee — refusing a
       missing or over-budget report is the honest version of that
       enforcement, not a claim of parity with tier2's hard wall-clock cap),
       then finalize exactly like a real tier2 dispatch would have: gate-
       forgery check, verdict parsing, decision log, sync push.
    """
    runtime = resolve_runtime(spec)
    prompt_dir, prompt_file, state_file = _self_dispatch_state_paths(org_slug, node_id, model_stream)

    if self_dispatch_result is None:
        prompt_dir.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(
            build_prompt(spec, node_id, run_id, graph_id, org_slug, context_pack, model_stream=model_stream)
        )
        import factory_gates
        state_file.write_text(json.dumps({
            "gates_before": factory_gates.snapshot(org_slug, stream=model_stream),
            "started_at": datetime.now().astimezone().isoformat(),
        }))
        result.update({
            "dispatched": False,
            "ok": False,
            "self_dispatch_required": {
                "node_id": node_id,
                "prompt_file": str(prompt_file.relative_to(REPO)),
                "model": runtime["model"],
                "max_turns": runtime["max_turns"],
                "budget_usd": runtime["budget_usd"],
                "allowed_tools": resolve_allowed_tools(spec),
            },
            "note": (
                f"self_dispatch: read {prompt_file.relative_to(REPO)}, dispatch it yourself via "
                f"your own Task tool (model={runtime['model']}, max budget_usd={runtime['budget_usd']}), "
                f"then re-run this exact stage with a self-dispatch result for node_id={node_id!r} "
                "carrying {final_response, cost_usd, exit_code}."
            ),
        })
        return result

    if not state_file.exists():
        result.update({
            "dispatched": False,
            "ok": False,
            "error": (
                f"self_dispatch resume for {node_id} but no pending state at "
                f"{state_file.relative_to(REPO)} (never dispatched, or already consumed) — "
                "re-run without a self-dispatch result to start it fresh"
            ),
        })
        return result

    state = json.loads(state_file.read_text())
    gates_before = state["gates_before"]
    started_at = datetime.fromisoformat(state["started_at"])

    budget_usd = float(runtime["budget_usd"])
    cost_usd = self_dispatch_result.get("cost_usd")
    output = self_dispatch_result.get("final_response") or ""
    if cost_usd is None or float(cost_usd) > budget_usd:
        exit_code = 1
        output = (
            f"self_dispatch budget refusal: reported cost_usd={cost_usd!r} is missing or exceeds "
            f"this spec's budget_usd={budget_usd}. self_dispatch cost is self-reported by the "
            "orchestrating agent, not independently metered like tier2_run.sh's --max-budget-usd — "
            "a missing or over-budget report is refused rather than trusted."
        ) + (f"\n\nOriginal final_response:\n{output}" if output else "")
    else:
        exit_code = int(self_dispatch_result.get("exit_code", 0))

    try:
        import factory_gates
        factory_gates.assert_unchanged(org_slug, gates_before, node_id=node_id, stream=model_stream)
        verdict = parse_verifier_verdict(output) if spec.get("role_family") == "verifier" else None
        ok = exit_code == 0
        if spec.get("role_family") == "verifier":
            ok = ok and verdict == "pass"
        result.update({
            "dispatched": True,
            "ok": ok,
            "exit_code": exit_code,
            "verdict": verdict,
            "prompt_file": str(prompt_file.relative_to(REPO)),
            "stderr_tail": None,
            "final_response": output or None,
            "cost_usd": cost_usd,
        })
        try:
            import factory_decisions
            factory_decisions.record(
                run_id=run_id,
                graph_id=graph_id,
                node_id=node_id,
                decision_point=f"{stage_code}-node-dispatch",
                options=[spec.get("slug")],
                chosen=spec.get("slug"),
                rationale=(f"verdict={verdict}" if verdict else f"exit={exit_code}"),
                actor="agent",
                org_slug=org_slug,
                context_refs=[result["prompt_file"]],
                ledger_dir=ledger_dir,
                sync_url=sync_url,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("factory_node_run: decision log failed for %s: %s", node_id, exc)
        return result
    finally:
        result.setdefault("dispatched", True)
        result.setdefault("ok", False)
        try:
            state_file.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            import factory_sync
            factory_sync.push_node_agent(
                result, run_id, stage_code, spec,
                prompt_file=result.get("prompt_file") or str(prompt_file),
                started_at=started_at, ended_at=datetime.now().astimezone(),
                ledger_dir=ledger_dir, url=sync_url,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("factory_node_run: node-agent sync failed for %s: %s", node_id, exc)


def dispatch_node(
    spec_slug,
    stage_code,
    run_id,
    graph_id,
    org_slug,
    dry_run=True,
    sync_url=None,
    ledger_dir=None,
    timeout_s=900,
    context_pack=None,
    runner=None,
    spec_override=None,
    model_stream="per-account",
    self_dispatch_result=None,
):
    """Dispatch one node. Returns a result dict; never raises on agent failure
    (the caller decides what a failed node means for the stage).

    `self_dispatch_result` only applies to an `executor: self_dispatch` spec
    that is being RESUMED (see `_dispatch_self`) — a dict shaped
    `{"final_response": str, "cost_usd": float, "exit_code": int}`, the
    orchestrating agent's report of what its own `Task`-dispatched sub-agent
    produced. `None` (the default) means "first call" for every executor,
    self_dispatch included.

    `spec_override` dispatches a spec that is not on disk — the C4 gap-closing
    path, whose specs are synthesized per-run and deliberately never written to
    `specs/` (see `factory_gap_spawn`). Everything downstream of here is
    identical for an ad-hoc spec, which is the point: it inherits the held-out
    read check, the `Task` strip, the gate-forgery snapshot and the decision log
    rather than getting a parallel dispatch path with its own set of oversights.

    **`dry_run=True` must never spawn a subprocess and never spend.** That is
    `factory.py`'s existing contract and the only reason this is provable
    without real money. It is enforced by returning before `runner` is reached,
    and asserted by `tests/test_node_dispatch.py` against a mocked subprocess.
    """
    spec = spec_override or load_spec(spec_slug)
    node_id = node_id_for(stage_code, spec_slug)
    runtime = resolve_runtime(spec)

    # C3: a curation or training node must not be handed held-out evaluation
    # material. Enforced HERE — before the prompt is rendered and before anything
    # is spawned — because a restriction that lives in prompt text is a request,
    # and this is the one boundary whose failure produces a confident wrong number
    # instead of an error (PRRules rule 11).
    #
    # Deliberately allowed to propagate: HeldoutError is the one hard failure in
    # this module. Every other path here degrades to `ok: False` so a broken
    # dispatch cannot take down a stage that still has a useful report to write,
    # but continuing past a violated anchor is worse than stopping.
    import factory_heldout
    factory_heldout.assert_not_visible(
        stage_code,
        org_slug,
        list(spec.get("read_first") or []) + list(context_pack or []),
        spec_slug=spec_slug,
        stream=model_stream,
    )
    result = {
        "node_id": node_id,
        "spec": spec_slug,
        "role_family": spec.get("role_family"),
        "runtime": runtime,
        "allowed_tools": resolve_allowed_tools(spec),
        "dry_run": bool(dry_run),
    }

    if dry_run:
        result.update({"dispatched": False, "ok": True, "verdict": None,
                       "note": "dry-run: prompt rendered and argv resolved, nothing spawned, $0"})
        return result

    # model-factory-cloud-dispatch-v1: a spec marked crash_resume.paid (today,
    # only train-launcher — "Assemble, upload, launch, and monitor one SFT
    # job") must not dispatch for real without an explicit, out-of-band opt-in.
    # `--dry-run` defaults to False (real) and S6g can self-approve under
    # auto_approve_under_usd, so neither is a real gate on this specific spec —
    # and a cloud-dispatched agent reaches this exact function with the same
    # Bash access as a human at a terminal. This sits below every caller
    # (CLI, run_pipeline.py, any future cloud routine) and cannot be routed
    # around by a flag or a forged manifest, unlike the checks in factory.py.
    if spec.get("crash_resume", {}).get("paid") and not os.environ.get("FACTORY_ALLOW_REAL_SPEND"):
        raise NodeDispatchError(
            f"{spec_slug} is crash_resume.paid and FACTORY_ALLOW_REAL_SPEND is "
            "unset — refusing to dispatch for real, independent of --approved/"
            "--manifest/gate state. Set FACTORY_ALLOW_REAL_SPEND=1 in this "
            "process's own environment (never in a spec, a routine's job_config, "
            "or anything an agent's prompt could set) to allow it."
        )

    # C2 (factory-cursor-bridge-v1, D1): a spec whose runtime block says
    # `executor: cursor_sdk` dispatches through the Cursor SDK instead of
    # tier2_run.sh. The branch sits HERE — after the held-out check, the result
    # seeding and the dry-run return — so both executors share one pre-flight
    # and the dry-run guarantee has exactly one implementation. Absent/`tier2`
    # falls through to the path below, unchanged, which is what keeps all 33+
    # existing specs unaffected. Imported lazily so a machine that never
    # dispatches an opted-in spec never needs `cursor-sdk` installed.
    if runtime.get("executor") == "cursor_sdk":
        if model_stream != "per-account":
            raise NodeDispatchError(
                "refusing cursor_sdk dispatch for a non-default model stream: "
                "the Cursor executor's prompt/state and approval snapshots are not "
                "stream-scoped. Intelligence must not fall back to per-account paths."
            )
        import factory_cursor_dispatch
        return factory_cursor_dispatch.execute(
            spec,
            node_id,
            stage_code,
            run_id,
            graph_id,
            org_slug,
            result=result,
            timeout_s=timeout_s,
            context_pack=context_pack,
            sync_url=sync_url,
            ledger_dir=ledger_dir,
        )

    # model-factory-cloud-dispatch-v1 (ENVIRONMENT.md §5): a spec whose runtime
    # block says `executor: self_dispatch` dispatches through the ORCHESTRATING
    # agent's own `Task` tool instead of tier2_run.sh/cursor_sdk — the one path
    # that needs no credential this process can shell out for, because a cloud
    # routine session already has `Task` in its own allowed_tools. Sits here for
    # the same reason cursor_sdk does: after the held-out check, the paid-spend
    # opt-in, and the dry-run return, so all three executors share one pre-flight.
    if runtime.get("executor") == "self_dispatch":
        return _dispatch_self(
            spec, node_id, stage_code, run_id, graph_id, org_slug, result,
            context_pack=context_pack, model_stream=model_stream,
            ledger_dir=ledger_dir, sync_url=sync_url,
            self_dispatch_result=self_dispatch_result,
        )

    prompt_dir = HERE / "runs" / org_slug
    if model_stream != "per-account":
        prompt_dir = prompt_dir / model_stream
    prompt_dir = prompt_dir / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = prompt_dir / f"{node_id.replace(':', '-')}.md"
    prompt_file.write_text(
        build_prompt(
            spec, node_id, run_id, graph_id, org_slug, context_pack,
            model_stream=model_stream,
        )
    )

    argv = build_tier2_argv(spec, node_id, prompt_file, timeout_s=timeout_s)

    # C4: this node is about to run with Write/Edit/Bash and cwd=REPO, and a human
    # gate approval is nothing more than a file existing (factory.py:119). Snapshot
    # the approvals directory across the spawn: there is no legitimate writer in
    # this window, so any change is this node's, and it is quarantined rather than
    # trusted. See factory_gates' header for why detection is the strongest
    # available primitive here and what it does not achieve.
    import factory_gates
    gates_before = factory_gates.snapshot(org_slug, stream=model_stream)

    # B1 (factory-observability-v1): everything from here to the return is wrapped
    # so the product sees the dispatch even when it ends badly.
    #
    # `EXECUTION-DESIGN.md` requires a try/finally around the not-yet-built Cursor
    # dispatch path. The same discipline applies here for the same reason, and this
    # path is the one that actually exists: a headless Claude Code session can
    # crash, time out (`tier2_run.sh` killpg's at the wall clock), or raise a
    # GateForgeryError mid-run, and before this every one of those outcomes was
    # invisible to anyone looking at the run in the product. An agent that burned
    # $2 and died is exactly the row Daniel needs to see; a missing row reads as
    # "no agent ran".
    #
    # The `finally` deliberately does NOT swallow the exception — GateForgeryError
    # and HeldoutError must still propagate (they are the two hard failures in this
    # module). It only guarantees the sync attempt happens on the way out.
    started_at = datetime.now().astimezone()
    run = runner or (lambda a: subprocess.run(a, capture_output=True, text=True, cwd=str(REPO)))
    try:
        proc = run(argv)

        # Checked before the exit code is even looked at: a node that forged an
        # approval and then failed has still forged it. GateForgeryError propagates
        # (like HeldoutError above) — the alternative is a report that says
        # `ok: false` on a run whose next stage is now unlocked.
        factory_gates.assert_unchanged(
            org_slug, gates_before, node_id=node_id, stream=model_stream
        )

        output = (getattr(proc, "stdout", "") or "").strip()
        exit_code = getattr(proc, "returncode", 1)

        verdict = parse_verifier_verdict(output) if spec.get("role_family") == "verifier" else None
        ok = exit_code == 0
        if spec.get("role_family") == "verifier":
            # Exit code alone is not proof (LESSON-016). A verifier that exits 0
            # without emitting a parseable verdict has not verified anything.
            ok = ok and verdict == "pass"

        result.update({
            "dispatched": True,
            "ok": ok,
            "exit_code": exit_code,
            "verdict": verdict,
            "prompt_file": str(prompt_file.relative_to(REPO)),
            "stderr_tail": (getattr(proc, "stderr", "") or "")[-STDERR_TAIL_MAX_CHARS:] or None,
            # The agent's closing message, in the shape its own
            # `final_response_shape` field defines — i.e. authored to be read by a
            # human, which is why it is surfaced at all. `factory_sync` bounds it
            # before anything reaches the DB.
            "final_response": output or None,
        })

        try:
            import factory_decisions
            factory_decisions.record(
                run_id=run_id,
                graph_id=graph_id,
                node_id=node_id,
                decision_point=f"{stage_code}-node-dispatch",
                options=[spec_slug],
                chosen=spec_slug,
                rationale=(f"verdict={verdict}" if verdict else f"exit={exit_code}"),
                actor="agent",
                org_slug=org_slug,
                context_refs=[result["prompt_file"]],
                ledger_dir=ledger_dir,
                sync_url=sync_url,
            )
        except Exception as exc:  # noqa: BLE001 - the log must never break the dispatch
            log.warning("factory_node_run: decision log failed for %s: %s", node_id, exc)

        return result
    finally:
        # Marked dispatched even on the exception path: the subprocess was spawned
        # and the money was spent, so a row saying otherwise would be a lie. `ok`
        # stays whatever the try block managed to set — False by default, since
        # `result` is seeded without it and an unset `ok` must never read as
        # success.
        result.setdefault("dispatched", True)
        result.setdefault("ok", False)
        try:
            import factory_sync
            factory_sync.push_node_agent(
                result,
                run_id,
                stage_code,
                spec,
                prompt_file=result.get("prompt_file") or str(prompt_file),
                started_at=started_at,
                ended_at=datetime.now().astimezone(),
                ledger_dir=ledger_dir,
                url=sync_url,
            )
        except Exception as exc:  # noqa: BLE001 — fail-open; the spend already happened
            log.warning("factory_node_run: node-agent sync failed for %s: %s", node_id, exc)


def dispatch_stage(
    stage_code, run_id, graph_id, org_slug, dry_run=True,
    model_stream="per-account", self_dispatch_results=None, **kwargs,
):
    """Run a stage's whole chain in order, stopping at the first failure.

    Stopping is the point: the chain is a dependency order, so continuing past a
    failed `corpus-planner` would have the family generator working from a plan
    that doesn't exist — spending real money to produce garbage. A node that
    returns `self_dispatch_required` (ok: False, nothing failed, it's just
    pending the orchestrating agent's own `Task` dispatch) stops the chain the
    same way a real failure would, which is the point: the pause preserves
    dependency order exactly like a stop-on-failure does.

    `self_dispatch_results` — `{node_id: {final_response, cost_usd,
    exit_code}}` — resumes whichever node in this chain the caller has a
    result for; every other node dispatches (or resumes) normally.
    """
    self_dispatch_results = self_dispatch_results or {}
    results = []
    for slug in STAGE_CHAINS.get(stage_code, ()):
        node_id = node_id_for(stage_code, slug)
        res = dispatch_node(
            slug, stage_code, run_id, graph_id, org_slug, dry_run=dry_run,
            model_stream=model_stream,
            self_dispatch_result=self_dispatch_results.get(node_id),
            **kwargs,
        )
        results.append(res)
        if not res.get("ok"):
            break
    return results


def main(argv=None):
    p = argparse.ArgumentParser(description="Dispatch a factory graph node to an agent via tier2_run.sh")
    p.add_argument("--node-id", required=True, help="stage code, e.g. S4 (optionally S4:corpus-planner)")
    p.add_argument("--agent-spec", help="spec slug or path; defaults to the stage's whole chain")
    p.add_argument("--run-id", required=True)
    p.add_argument("--graph-id", default="fine-tune-factory-v1")
    p.add_argument("--org-slug", required=True)
    p.add_argument(
        "--stream", choices=("per-account", "intelligence"),
        default="per-account",
    )
    p.add_argument("--sync-url")
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--dry-run", action="store_true", default=True)
    p.add_argument("--live", dest="dry_run", action="store_false", help="actually spend money")
    p.add_argument(
        "--self-dispatch-result",
        action="append",
        default=[],
        metavar="NODE_ID=PATH",
        help="repeatable; resumes a pending executor:self_dispatch node (ENVIRONMENT.md §5). "
             "PATH is a JSON file shaped {final_response, cost_usd, exit_code} — what your own "
             "Task-dispatched sub-agent produced for that node_id.",
    )
    args = p.parse_args(argv)

    self_dispatch_results = {}
    for item in args.self_dispatch_result:
        node_id_key, sep, path = item.partition("=")
        if not sep:
            raise SystemExit(f"--self-dispatch-result expects NODE_ID=PATH, got {item!r}")
        self_dispatch_results[node_id_key] = json.loads(Path(path).read_text())

    stage_code, _, qualifier = args.node_id.partition(":")
    spec = args.agent_spec or qualifier or None
    if spec:
        node_id = node_id_for(stage_code, spec)
        out = [dispatch_node(spec, stage_code, args.run_id, args.graph_id, args.org_slug,
                             dry_run=args.dry_run, sync_url=args.sync_url, timeout_s=args.timeout,
                             model_stream=args.stream,
                             self_dispatch_result=self_dispatch_results.get(node_id))]
    else:
        out = dispatch_stage(stage_code, args.run_id, args.graph_id, args.org_slug,
                             dry_run=args.dry_run, sync_url=args.sync_url, timeout_s=args.timeout,
                             model_stream=args.stream, self_dispatch_results=self_dispatch_results)
    print(json.dumps(out, indent=2))
    return 0 if all(r.get("ok") for r in out) else 1


if __name__ == "__main__":
    sys.exit(main())
