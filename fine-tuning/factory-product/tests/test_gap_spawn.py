"""C4 (factory-graph-pr2, 2026-07-28): dynamic gap-closing spawn.

PRRules rule 14 rates this the highest-risk item in PR 2: an agent nobody
reviewed, running on a budget nobody approved per-instance. The tests here are
mostly refusals, because the value of this capability is entirely in what it
will not do.

The two that matter most:

* `test_the_tool_grant_is_the_gate_denial` — a synthesized agent cannot approve a
  human gate because approval is a file existing and it cannot write files. That
  is prevention rather than `factory_gates`' detection, and it holds only as long
  as this grant stays read-only, so it is pinned here.
* `test_an_action_mandate_is_refused_at_synthesis` — an action mandate handed to a
  read-only agent does not fail cleanly. It reads what it can, cannot perform the
  change, and reports on the part that worked.
"""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory_gap_spawn as gs  # noqa: E402
import factory_heldout as fh  # noqa: E402
import factory_node_run as fnr  # noqa: E402

SLUG = "qa-dryrun-synth"
GAP = "the census reports the corpus is dose-short but not which slice is short"
MANDATE = "investigate which slices fall under their dose floor and report the shortfall per slice"


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------


def test_a_scoped_investigation_synthesizes():
    spec = gs.synthesize(GAP, MANDATE, SLUG)
    assert spec["adhoc"] is True
    assert spec["slug"].startswith("adhoc-")
    assert spec["mandate"][0]["task"] == MANDATE
    assert spec["done_when"] and spec["final_response_shape"]


def test_the_slug_is_derived_and_bounded():
    """Ad-hoc slugs come from free text, and they become a filename
    (`runs/<org>/prompts/<node>.md`) and a `tier2_run.sh --label`."""
    spec = gs.synthesize("Why is " + "x" * 500, "investigate the cause", SLUG)
    assert len(spec["slug"]) <= 54
    assert spec["slug"] == spec["slug"].lower()
    assert "/" not in spec["slug"] and " " not in spec["slug"]


# --------------------------------------------------------------------------
# The gate denial — structural
# --------------------------------------------------------------------------


def test_the_tool_grant_is_the_gate_denial():
    """A human-gate approval is a file existing (`factory.py:119`). An agent with
    no write capability cannot create one. This is the whole guarantee, so any
    future edit widening this grant must fail here first."""
    tools = gs.synthesize(GAP, MANDATE, SLUG)["runtime"]["allowed_tools"]
    assert tuple(tools) == ("Read", "Grep", "Glob")
    for forbidden in ("Write", "Edit", "Bash", "Task", "NotebookEdit", "WebFetch"):
        assert forbidden not in tools


def test_the_resolved_tools_match_the_declared_ones():
    """`resolve_allowed_tools` falls back to the broad `DEFAULT_ALLOWED_TOOLS` for a
    spec with no `runtime.allowed_tools` — the exact silent-widening path that would
    hand an ad-hoc agent `Bash`."""
    spec = gs.synthesize(GAP, MANDATE, SLUG)
    assert tuple(fnr.resolve_allowed_tools(spec)) == gs.READ_ONLY_TOOLS


def test_a_mandate_naming_a_gate_is_refused():
    with pytest.raises(gs.GapSpawnRefused, match="human gate"):
        gs.synthesize("the spend gate has been pending for three days",
                      "investigate whether the spend gate can be cleared", SLUG)


def test_the_gate_refusal_outranks_the_action_refusal():
    """"...whether the spend gate can be cleared" trips both checks ("spend" is an
    action verb). Reporting the action reason first would teach the coordinator to
    reword until the verb is gone and resubmit; the gate reason is the one that
    will not move, so it has to be the one reported."""
    with pytest.raises(gs.GapSpawnRefused, match="human gate"):
        gs.synthesize("corpus is ready", "investigate whether the spend gate can be cleared", SLUG)


@pytest.mark.parametrize("text", ("investigate whether S6g is safe to pass",
                                  "determine if S2 governance is satisfied",
                                  "review the S9 ship gate evidence"))
def test_gate_codes_are_caught_in_any_form(text):
    with pytest.raises(gs.GapSpawnRefused, match="human gate"):
        gs.synthesize("gap", text, SLUG)


# --------------------------------------------------------------------------
# Investigate-only
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mandate", (
    "investigate the failed launch and restart it",
    "diagnose the export failure and fix the query",
    "find the missing rows and write them to the corpus",
    "determine the right hyperparameters and train the model",
    "review the migration and apply it",
))
def test_an_action_mandate_is_refused_at_synthesis(mandate):
    """Each of these is two requests. The read-only grant makes the second half
    fail *partway* rather than cleanly, and the agent reports on the half that
    worked — so this is refused before anything spawns."""
    with pytest.raises(gs.GapSpawnRefused, match="implies action"):
        gs.synthesize(GAP, mandate, SLUG)


def test_the_refusal_names_the_offending_verb():
    """A refusal that does not say which word tripped it produces a coordinator
    that rewrites the mandate at random until something passes."""
    with pytest.raises(gs.GapSpawnRefused, match="restart"):
        gs.synthesize(GAP, "investigate the stall and restart the run", SLUG)


def test_an_action_verb_as_a_noun_is_not_a_false_positive():
    """"investigate why the deployment failed" is a question. Word-boundary
    matching, or the deny-list refuses most legitimate mandates and gets deleted."""
    spec = gs.synthesize("deployment failed", "investigate why the deployment failed", SLUG)
    assert spec["adhoc"] is True


def test_a_bare_imperative_with_no_question_is_refused():
    """Allow-list on investigative intent, on top of the verb deny-list — a verb
    deny-list is inherently incomplete, and two independent checks failing the same
    way is much less likely than either failing alone."""
    with pytest.raises(gs.GapSpawnRefused, match="does not read as an investigation"):
        gs.synthesize(GAP, "handle the slice problem", SLUG)


def test_the_stop_or_ask_covers_the_read_only_boundary():
    stop = gs.synthesize(GAP, MANDATE, SLUG)["mandate"][0]["stop_or_ask"]
    assert "STOP" in stop
    for term in ("writing", "spending", "S2/S6g/S9"):
        assert term in stop


def test_the_laws_forbid_gate_recommendations_and_nesting():
    laws = " ".join(gs.synthesize(GAP, MANDATE, SLUG)["laws"]).lower()
    assert "may not approve" in laws
    assert "may not spawn subagents" in laws


def test_uncertainty_must_be_reported_as_uncertainty():
    """The coordinator acts on this output and cannot distinguish a confident guess
    from a finding, so the spec has to ask for the distinction explicitly."""
    spec = gs.synthesize(GAP, MANDATE, SLUG)
    assert "could not determine" in " ".join(spec["laws"]).lower()
    assert "could not determine" in spec["final_response_shape"].lower()


# --------------------------------------------------------------------------
# Fan-out and nesting
# --------------------------------------------------------------------------


def test_spawns_are_capped_per_run():
    """The per-node budget cap says nothing about how many nodes there are."""
    for i in range(gs.MAX_SPAWNS_PER_RUN):
        assert gs.synthesize(GAP, MANDATE, SLUG, spawn_index=i)
    with pytest.raises(gs.GapSpawnRefused, match="cap"):
        gs.synthesize(GAP, MANDATE, SLUG, spawn_index=gs.MAX_SPAWNS_PER_RUN)


def test_the_cap_refusal_points_at_writing_a_real_spec():
    with pytest.raises(gs.GapSpawnRefused, match="specs/"):
        gs.synthesize(GAP, MANDATE, SLUG, spawn_index=99)


def test_a_synthesized_agent_may_not_synthesize():
    """Depth check, not tool check. `Task` is already absent from the grant, but a
    chain does not need `Task` if the dispatch path itself is reachable."""
    with pytest.raises(gs.GapSpawnRefused, match="may not synthesize"):
        gs.synthesize(GAP, MANDATE, SLUG, parent_node_id="S-adhoc:adhoc-something")


def test_the_budget_is_small_and_the_turns_are_few():
    runtime = gs.synthesize(GAP, MANDATE, SLUG)["runtime"]
    assert runtime["budget_usd"] <= 1.0
    assert runtime["max_turns"] <= 15
    assert gs.MAX_SPAWNS_PER_RUN * runtime["budget_usd"] <= 3.0


# --------------------------------------------------------------------------
# Not a route around spec review
# --------------------------------------------------------------------------


def test_a_gap_an_existing_spec_covers_is_refused():
    catalogue = {"preflight-linter": "lint the exported corpus for schema and dose-floor violations"}
    with pytest.raises(gs.GapSpawnRefused, match="preflight-linter"):
        gs.synthesize("lint the exported corpus for schema and dose-floor violations",
                      "investigate the corpus lint", SLUG, catalogue=catalogue)


def test_a_genuinely_novel_gap_passes_the_coverage_check():
    catalogue = {"preflight-linter": "lint the exported corpus for schema violations",
                 "serve-wire": "emit the serving runbook for the shipped adapter"}
    assert gs.synthesize("the vendor's tokenizer version changed between iterations",
                         "investigate whether the tokenizer change affects the corpus",
                         SLUG, catalogue=catalogue)


def test_the_coverage_check_is_skipped_when_no_catalogue_is_supplied():
    """Passed in rather than read from disk, so this must not silently pass by
    virtue of the caller forgetting — `spawn`'s callers supply it, and the absence
    of a catalogue means "unknown", which is not a refusal."""
    assert gs.find_covering_spec(GAP, None) is None
    assert gs.find_covering_spec(GAP, {}) is None


# --------------------------------------------------------------------------
# Empty / degenerate input
# --------------------------------------------------------------------------


@pytest.mark.parametrize("gap,mandate", (("", MANDATE), ("   ", MANDATE), (GAP, ""), (GAP, "  ")))
def test_empty_gap_or_mandate_is_refused(gap, mandate):
    with pytest.raises(gs.GapSpawnRefused):
        gs.synthesize(gap, mandate, SLUG)


def test_refusal_raises_rather_than_returning_a_failed_result():
    """A refusal that came back as `{"ok": False}` would read to the coordinator as
    "the investigation failed", and it would retry the same refused spawn."""
    with pytest.raises(gs.GapSpawnRefused):
        gs.spawn(GAP, "ship the model", SLUG, dry_run=True)


# --------------------------------------------------------------------------
# Dispatch integration
# --------------------------------------------------------------------------


def test_spawn_dispatches_through_the_ordinary_node_path():
    """Not a second dispatch path: one path means the ad-hoc agent inherits the
    held-out check, the Task strip, the gate snapshot and the decision log."""
    def runner(argv):
        pytest.fail("dry run spawned a subprocess")

    result = gs.spawn(GAP, MANDATE, SLUG, dry_run=True, runner=runner)
    assert result["adhoc"] is True and result["gap"] == GAP
    assert result["dispatched"] is False and result["ok"] is True
    assert tuple(result["allowed_tools"]) == gs.READ_ONLY_TOOLS


def test_the_adhoc_node_id_is_distinguishable_in_the_ledger():
    result = gs.spawn(GAP, MANDATE, SLUG, dry_run=True)
    assert result["node_id"].startswith(f"{gs.ADHOC_STAGE_CODE}:")
    assert gs.ADHOC_STAGE_CODE not in fnr.STAGE_CHAINS


def test_the_adhoc_stage_cannot_read_heldout_material():
    """Inherited from C3's allow-list rather than re-implemented: `S-adhoc` is not
    a listed stage, so default-deny applies. This is the payoff for C3 having been
    written as an allow-list instead of a deny-list."""
    assert fh.may_read_heldout(gs.ADHOC_STAGE_CODE) is False
    with pytest.raises(fh.HeldoutError):
        gs.spawn(GAP, MANDATE, SLUG, dry_run=True,
                 read_first=[f"runs/{SLUG}/heldout/01-manifest.json"])


def test_the_spec_is_never_written_to_the_reviewed_catalogue():
    """An ad-hoc spec on disk is indistinguishable next week from a reviewed one,
    and the catalogue would grow by accretion with nothing approving an entry."""
    before = {p.name for p in fnr.SPECS_DIR.glob("*.yaml")}
    gs.spawn(GAP, MANDATE, SLUG, dry_run=True)
    assert {p.name for p in fnr.SPECS_DIR.glob("*.yaml")} == before
    assert not list(fnr.SPECS_DIR.glob("adhoc-*"))


def test_the_prompt_carries_the_mandate_and_the_laws(tmp_path):
    """The spec only binds the agent to the extent it reaches the prompt."""
    spec = gs.synthesize(GAP, MANDATE, SLUG)
    prompt = fnr.build_prompt(spec, "S-adhoc:x", None, "g", SLUG)
    assert MANDATE in prompt
    assert "may not approve" in prompt
    assert "STOP OR ASK" in prompt


def test_the_caps_reach_tier2(tmp_path):
    """Resolving a tight budget and then not passing it to the one component that
    enforces it would be worse than not resolving it."""
    spec = gs.synthesize(GAP, MANDATE, SLUG)
    argv = fnr.build_tier2_argv(spec, "S-adhoc:x", tmp_path / "p.md")
    assert "0.75" in argv and "12" in argv
    tools = argv[argv.index("--allowed-tools") + 1]
    assert tools == "Read,Grep,Glob"
