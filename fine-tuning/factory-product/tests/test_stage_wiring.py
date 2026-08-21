"""C2 (factory-graph-pr2, 2026-07-28): S7/S8/S10 wired to real AgentSpecs.

The stages were `TODO-STUB`s that printed a contract and returned. They now
dispatch real specs through the same `tier2_run.sh` machinery PR 1 established
for S4/S5 (decision D8 — the dispatcher is the Python module, not a `.sh`
wrapper).

The single most valuable test in this file is
`test_every_dispatched_spec_has_a_role_default`. `ROLE_DEFAULTS` covered exactly
the role families S4/S5 use, so wiring three stages whose specs are `platform`,
`grader` and `eval-prep` would have handed all six of them
`FALLBACK_RUNTIME` — haiku, 10 turns, $0.50 — **silently**. `train-launcher`
launches a real fine-tuning run against a signed dataset manifest; on a 10-turn
haiku budget it does not refuse, it runs out of turns partway through something
expensive. Nothing else in either repo would have noticed, because a conservative
default looks like prudence right up until it is the reason a stage half-ran.
"""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory_node_run as fnr  # noqa: E402
import factory_sync  # noqa: E402

NEW_STAGES = ("S7", "S8", "S10")


# --------------------------------------------------------------------------
# Chains resolve to real specs, in the documented order
# --------------------------------------------------------------------------


@pytest.mark.parametrize("stage", NEW_STAGES)
def test_chain_is_registered(stage):
    assert fnr.STAGE_CHAINS.get(stage), f"{stage} has no chain"


@pytest.mark.parametrize("stage", NEW_STAGES)
def test_every_spec_in_the_chain_exists_on_disk(stage):
    """A chain naming a spec that does not exist fails at dispatch time, mid-run,
    after earlier stages have already spent money."""
    for slug in fnr.STAGE_CHAINS[stage]:
        spec = fnr.load_spec(slug)
        assert spec.get("slug") == slug


def test_s7_rehearses_before_it_launches():
    """Order is the point: a rehearsal that runs after the launch is theatre."""
    assert fnr.STAGE_CHAINS["S7"] == ("launch-rehearsal", "train-launcher")


def test_s8_audits_the_grader_last():
    """`grader-auditor` grades the grading, so it cannot run before the window has
    produced verdicts to audit."""
    chain = fnr.STAGE_CHAINS["S8"]
    assert chain.index("eval-window-runner") < chain.index("grader-auditor")
    assert chain[0] == "eval-instrument-builder"


def test_s10_is_one_node_and_stays_a_runbook():
    assert fnr.STAGE_CHAINS["S10"] == ("serve-wire",)


# --------------------------------------------------------------------------
# Budgets — the silent-fallback trap
# --------------------------------------------------------------------------


@pytest.mark.parametrize("stage", ("S4", "S5") + NEW_STAGES)
def test_every_dispatched_spec_has_a_role_default(stage):
    """No dispatched spec may inherit FALLBACK_RUNTIME by silence.

    This is the guard that makes adding a stage safe: wire a chain whose specs
    have an uncovered `role_family` and this fails immediately, instead of the
    stage quietly running on a 10-turn haiku budget.
    """
    for slug in fnr.STAGE_CHAINS[stage]:
        spec = fnr.load_spec(slug)
        family = spec.get("role_family")
        has_own_runtime = bool((spec.get("runtime") or {}).get("budget_usd"))
        assert family in fnr.ROLE_DEFAULTS or has_own_runtime, (
            f"{slug} ({stage}) has role_family={family!r}, which is not in ROLE_DEFAULTS and the "
            f"spec declares no runtime of its own — it would silently inherit "
            f"{fnr.FALLBACK_RUNTIME}"
        )


@pytest.mark.parametrize("stage", NEW_STAGES)
def test_no_dispatched_spec_resolves_to_the_fallback(stage):
    for slug in fnr.STAGE_CHAINS[stage]:
        runtime = fnr.resolve_runtime(fnr.load_spec(slug))
        assert runtime != fnr.FALLBACK_RUNTIME, f"{slug} resolved to the bare fallback"


def test_the_training_launcher_is_not_on_the_cheapest_tier():
    """Specifically called out because it is the one node whose under-budgeting
    costs real money rather than a wasted turn."""
    runtime = fnr.resolve_runtime(fnr.load_spec("train-launcher"))
    assert runtime["model"] != "haiku"
    assert runtime["max_turns"] >= 30


def test_the_grader_is_not_on_the_cheapest_tier():
    """A grader that exhausts its budget mid-battery and reports what it has
    produces a passing verdict on a partial eval."""
    runtime = fnr.resolve_runtime(fnr.load_spec("grader-auditor"))
    assert runtime["model"] != "haiku"


# --------------------------------------------------------------------------
# Inherited invariants still hold for the new stages
# --------------------------------------------------------------------------


@pytest.mark.parametrize("stage", NEW_STAGES)
def test_task_is_stripped_from_every_new_node(stage):
    """Rule 3, unconditional. Nested spawn makes every budget cap meaningless."""
    for slug in fnr.STAGE_CHAINS[stage]:
        assert "Task" not in fnr.resolve_allowed_tools(fnr.load_spec(slug))


@pytest.mark.parametrize("stage", NEW_STAGES)
def test_dry_run_spawns_nothing_and_spends_nothing(stage):
    """Rule 10 — the only reason this wiring is provable without real spend."""
    def runner(argv):
        pytest.fail(f"dry run spawned a subprocess: {argv}")

    results = fnr.dispatch_stage(
        stage, run_id=None, graph_id="fine-tune-factory-v1",
        org_slug="qa-dryrun-synth", dry_run=True, runner=runner,
    )
    assert results
    assert all(r["dispatched"] is False for r in results)
    assert all(r["ok"] for r in results)


@pytest.mark.parametrize("stage", NEW_STAGES)
def test_argv_carries_the_resolved_caps(stage, tmp_path):
    """The caps must reach `tier2_run.sh`, which is where they are enforced —
    resolving them and then not passing them would be worse than not resolving."""
    for slug in fnr.STAGE_CHAINS[stage]:
        spec = fnr.load_spec(slug)
        argv = fnr.build_tier2_argv(spec, fnr.node_id_for(stage, slug), tmp_path / "p.md")
        runtime = fnr.resolve_runtime(spec)
        assert "--max-turns" in argv and str(runtime["max_turns"]) in argv
        assert "--budget-usd" in argv and f"{runtime['budget_usd']:.2f}" in argv
        assert "Task" not in argv[argv.index("--allowed-tools") + 1]


# --------------------------------------------------------------------------
# The hazard C2 introduces: activate can now fail
# --------------------------------------------------------------------------


def test_a_failed_activate_is_not_reported_as_shipped():
    """Before C2, `activate` dispatched nothing, so "reached activate" and
    "shipped" were the same fact. With `serve-wire` in the chain they come apart,
    and `shipped` is TERMINAL — so mis-reporting it puts a green final state on a
    model that never went live AND stops the run being retried."""
    assert factory_sync._run_status_for("activate", {"status": "error"}) == "failed"


def test_a_successful_activate_still_ships():
    assert factory_sync._run_status_for("activate", {"status": "runbook"}) == "shipped"


def test_curation_and_grading_stage_sets_are_declared():
    """C3 reads these to check judge/generator separation, so they must exist and
    must not overlap."""
    assert fnr.CURATION_STAGES and fnr.GRADING_STAGES
    assert not (set(fnr.CURATION_STAGES) & set(fnr.GRADING_STAGES))
