"""model-factory-cloud-dispatch-v1 (ENVIRONMENT.md §5): `executor: self_dispatch`.

The load-bearing properties, and the specific failure each test prevents:

1. **First call renders and pauses; it never shells out.** A self_dispatch
   node reaching `subprocess.run` would defeat the entire point — it exists
   because this runtime has no credential to shell out with.
2. **Dry-run wins over every executor, self_dispatch included.** Unchanged
   contract from tier2/cursor_sdk: `--dry-run` renders and returns, $0.
3. **Resume without a prior first call is a clear error, not a silent no-op
   or a crash.** The state file is the only memory a fresh Python process
   has of what the first call already computed.
4. **A missing or over-budget cost report is refused, not trusted.** This is
   the self_dispatch analogue of LESSON-016: an agent that reports success
   without reporting a real, in-budget cost has not proven it stayed in
   budget.
5. **Gate-forgery detection still runs on resume**, against the snapshot the
   FIRST call took — not the (irrelevant) state at resume time.
6. **The state file is consumed exactly once.** A second resume against the
   same first call must not silently re-finalize (and re-sync) the same node.
7. **A stage chain pauses at a pending self_dispatch node**, same as it would
   at a real failure — dependency order must hold either way.
"""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory_node_run as fnr  # noqa: E402

RUN_ID = "dddddddd-0000-0000-0000-000000000002"
GRAPH_ID = "fine-tune-factory-v1"
SLUG = "qa-dryrun-synth"


def _builder_spec(**runtime_overrides):
    return {
        "slug": "self-dispatch-builder-fixture",
        "role_family": "builder",
        "mission": "fixture spec for self_dispatch tests",
        "read_first": [],
        "mandate": [{"task": "do the thing", "stop_or_ask": "ask if unsure"}],
        "laws": ["single-writer discipline"],
        "done_when": "done",
        "final_response_shape": "a summary",
        "runtime": {"executor": "self_dispatch", "budget_usd": 2.00, **runtime_overrides},
    }


def _verifier_spec(**runtime_overrides):
    spec = _builder_spec(**runtime_overrides)
    spec.update({"slug": "self-dispatch-verifier-fixture", "role_family": "verifier"})
    return spec


def _node_id():
    return fnr.node_id_for("S4", "self-dispatch-builder-fixture")


@pytest.fixture(autouse=True)
def _clean_self_dispatch_state():
    """Each test's first-call/resume pair shares one (org, node_id) — clean the
    on-disk state before AND after every test so execution order can never
    leak a state file (or a stale prompt) from one test into another."""

    def _wipe():
        for stage, slug in (("S4", "self-dispatch-builder-fixture"), ("S5", "self-dispatch-verifier-fixture")):
            node_id = fnr.node_id_for(stage, slug)
            _, prompt_file, state_file = fnr._self_dispatch_state_paths(SLUG, node_id)
            prompt_file.unlink(missing_ok=True)
            state_file.unlink(missing_ok=True)

    _wipe()
    yield
    _wipe()


# --------------------------------------------------------------------------
# first call: render + pause, never shell out
# --------------------------------------------------------------------------


def test_first_call_never_spawns_a_subprocess(tmp_path, monkeypatch):
    monkeypatch.chdir(FACTORY_DIR)
    calls = []
    result = fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_builder_spec(),
        runner=lambda argv: calls.append(argv),
    )
    assert calls == []
    assert result["dispatched"] is False
    assert result["ok"] is False
    assert "self_dispatch_required" in result


def test_first_call_reports_the_specs_runtime_in_the_required_block(tmp_path, monkeypatch):
    monkeypatch.chdir(FACTORY_DIR)
    result = fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_builder_spec(budget_usd=3.50, model="opus"),
    )
    req = result["self_dispatch_required"]
    assert req["node_id"] == _node_id()
    assert req["budget_usd"] == 3.50
    assert req["model"] == "opus"
    assert "Task" not in req["allowed_tools"]


def test_first_call_writes_a_real_prompt_file(tmp_path, monkeypatch):
    monkeypatch.chdir(FACTORY_DIR)
    result = fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_builder_spec(),
    )
    prompt_path = fnr.REPO / result["self_dispatch_required"]["prompt_file"]
    assert prompt_path.exists()
    assert "fixture spec for self_dispatch tests" in prompt_path.read_text()


def test_dry_run_wins_over_self_dispatch_same_as_every_other_executor():
    result = fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=True,
        spec_override=_builder_spec(),
    )
    assert result["dispatched"] is False
    assert result["ok"] is True
    assert "self_dispatch_required" not in result
    assert result["note"] == "dry-run: prompt rendered and argv resolved, nothing spawned, $0"


# --------------------------------------------------------------------------
# resume: the second call
# --------------------------------------------------------------------------


def test_resume_without_a_prior_first_call_is_a_clear_error(monkeypatch):
    monkeypatch.chdir(FACTORY_DIR)
    result = fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_builder_spec(),
        self_dispatch_result={"final_response": "did it", "cost_usd": 0.10, "exit_code": 0},
    )
    assert result["ok"] is False
    assert "no pending state" in result["error"]


def test_resume_ok_finalizes_like_a_real_dispatch(tmp_path, monkeypatch):
    monkeypatch.chdir(FACTORY_DIR)
    fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_builder_spec(),
    )
    result = fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_builder_spec(),
        self_dispatch_result={"final_response": "did the thing", "cost_usd": 0.75, "exit_code": 0},
        ledger_dir=tmp_path,
    )
    assert result["ok"] is True
    assert result["dispatched"] is True
    assert result["exit_code"] == 0
    assert result["cost_usd"] == 0.75
    assert result["final_response"] == "did the thing"


def test_resume_missing_cost_is_refused_not_assumed_free(monkeypatch):
    monkeypatch.chdir(FACTORY_DIR)
    fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_builder_spec(),
    )
    result = fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_builder_spec(),
        self_dispatch_result={"final_response": "did it", "exit_code": 0},
    )
    assert result["ok"] is False
    assert result["exit_code"] == 1


def test_resume_over_budget_is_refused(monkeypatch):
    monkeypatch.chdir(FACTORY_DIR)
    fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_builder_spec(budget_usd=1.00),
    )
    result = fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_builder_spec(budget_usd=1.00),
        self_dispatch_result={"final_response": "did it", "cost_usd": 1.01, "exit_code": 0},
    )
    assert result["ok"] is False
    assert "budget" in result["final_response"]


def test_resume_at_exactly_the_budget_ceiling_is_allowed(monkeypatch):
    monkeypatch.chdir(FACTORY_DIR)
    fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_builder_spec(budget_usd=1.00),
    )
    result = fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_builder_spec(budget_usd=1.00),
        self_dispatch_result={"final_response": "did it", "cost_usd": 1.00, "exit_code": 0},
    )
    assert result["ok"] is True


def test_resume_nonzero_exit_code_fails_even_with_a_reported_cost(monkeypatch):
    monkeypatch.chdir(FACTORY_DIR)
    fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_builder_spec(),
    )
    result = fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_builder_spec(),
        self_dispatch_result={"final_response": "crashed", "cost_usd": 0.10, "exit_code": 1},
    )
    assert result["ok"] is False


def test_resume_verifier_without_a_parseable_verdict_is_not_a_pass(monkeypatch):
    monkeypatch.chdir(FACTORY_DIR)
    fnr.dispatch_node(
        "self-dispatch-verifier-fixture", "S5", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_verifier_spec(),
    )
    result = fnr.dispatch_node(
        "self-dispatch-verifier-fixture", "S5", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_verifier_spec(),
        self_dispatch_result={"final_response": "ran 6 checks, some errored", "cost_usd": 0.10, "exit_code": 0},
    )
    assert result["verdict"] is None
    assert result["ok"] is False


def test_resume_verifier_with_a_pass_verdict_is_ok(monkeypatch):
    monkeypatch.chdir(FACTORY_DIR)
    fnr.dispatch_node(
        "self-dispatch-verifier-fixture", "S5", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_verifier_spec(),
    )
    result = fnr.dispatch_node(
        "self-dispatch-verifier-fixture", "S5", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_verifier_spec(),
        self_dispatch_result={"final_response": "VERDICT: pass", "cost_usd": 0.10, "exit_code": 0},
    )
    assert result["ok"] is True
    assert result["verdict"] == "pass"


def test_state_file_is_consumed_after_a_successful_resume(monkeypatch):
    monkeypatch.chdir(FACTORY_DIR)
    fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_builder_spec(),
    )
    fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_builder_spec(),
        self_dispatch_result={"final_response": "did it", "cost_usd": 0.10, "exit_code": 0},
    )
    replay = fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_builder_spec(),
        self_dispatch_result={"final_response": "did it again", "cost_usd": 0.10, "exit_code": 0},
    )
    assert replay["ok"] is False
    assert "no pending state" in replay["error"]


def test_gate_forgery_during_the_self_dispatched_window_is_detected(monkeypatch, tmp_path):
    monkeypatch.chdir(FACTORY_DIR)
    import factory_gates

    monkeypatch.setattr(factory_gates, "approvals_dir", lambda org_slug, stream="per-account": tmp_path)

    fnr.dispatch_node(
        "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=_builder_spec(),
    )
    # Simulate a forged approval appearing during the (external) self-dispatch window.
    (tmp_path / "launch.json").write_text(json.dumps({"approved_by": "not-daniel"}))

    with pytest.raises(factory_gates.GateForgeryError):
        fnr.dispatch_node(
            "self-dispatch-builder-fixture", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
            spec_override=_builder_spec(),
            self_dispatch_result={"final_response": "did it", "cost_usd": 0.10, "exit_code": 0},
        )


# --------------------------------------------------------------------------
# stage-level pause/resume
# --------------------------------------------------------------------------


def test_dispatch_stage_pauses_at_a_pending_self_dispatch_node(monkeypatch):
    monkeypatch.chdir(FACTORY_DIR)
    real_load_spec = fnr.load_spec
    reached = []

    def _load_spec(slug_or_path):
        if slug_or_path == "self-dispatch-builder-fixture":
            return _builder_spec()
        reached.append(slug_or_path)
        return real_load_spec(slug_or_path)

    monkeypatch.setattr(fnr, "load_spec", _load_spec)
    monkeypatch.setattr(
        fnr, "STAGE_CHAINS",
        {**fnr.STAGE_CHAINS, "S4": ("self-dispatch-builder-fixture", "corpus-reader")},
    )

    results = fnr.dispatch_stage("S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False)
    assert len(results) == 1
    assert results[0]["self_dispatch_required"]["node_id"] == _node_id()
    assert reached == []  # corpus-reader must never be reached


def test_dispatch_stage_resumes_the_named_node_and_continues(monkeypatch, tmp_path):
    monkeypatch.chdir(FACTORY_DIR)

    real_load_spec = fnr.load_spec

    def _load_spec(slug_or_path):
        if slug_or_path == "self-dispatch-builder-fixture":
            return _builder_spec()
        return real_load_spec(slug_or_path)

    monkeypatch.setattr(fnr, "load_spec", _load_spec)
    monkeypatch.setattr(
        fnr, "STAGE_CHAINS",
        {**fnr.STAGE_CHAINS, "S4": ("self-dispatch-builder-fixture",)},
    )

    fnr.dispatch_stage("S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False)
    node_id = fnr.node_id_for("S4", "self-dispatch-builder-fixture")
    results = fnr.dispatch_stage(
        "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False, ledger_dir=tmp_path,
        self_dispatch_results={
            node_id: {"final_response": "did it", "cost_usd": 0.10, "exit_code": 0},
        },
    )
    assert len(results) == 1
    assert results[0]["ok"] is True
