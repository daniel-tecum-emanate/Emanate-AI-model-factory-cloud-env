"""B3 (factory-graph-pr1, 2026-07-27): S4/S5 node dispatch to real AgentSpecs.

The load-bearing properties, and the specific failure each test prevents:

1. **`--dry-run` spawns nothing and spends nothing.** Asserted against a mocked
   subprocess, not inferred from a $0 bill. This is `factory.py`'s existing
   contract and the only reason any of the rest of this is testable without
   real money.
2. **No nested spawn.** `Task` must never reach `--allowedTools`. A dispatched
   node that can spawn its own subagents makes the parent's budget cap
   meaningless, since only the parent's spend is measured.
3. **Exit code 0 is not proof** (LESSON-016). A verifier that exits cleanly
   without emitting a parseable verdict has verified nothing, and must not be
   read as a pass. This is the most likely real-world failure mode: a script
   that logs per-item errors and returns 0.
4. **The budget actually reaches the runner.** A cap that isn't in the argv is
   not a cap.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory_node_run as fnr  # noqa: E402

RUN_ID = "dddddddd-0000-0000-0000-000000000001"
GRAPH_ID = "fine-tune-factory-v1"
SLUG = "qa-dryrun-synth"


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _tier2_spec(slug, role_family="builder", **runtime_overrides):
    """A minimal synthetic AgentSpec whose `runtime` omits `executor` — i.e.
    tier2, the still-current default — so a test of tier2's own generic
    dispatch mechanics (chain-stop, verdict gating, decision events, the
    paid-spec guard) does not depend on which real on-disk spec happens to use
    tier2 today. `corpus-planner`/`corpus-family-generator`/`curation-consolidator`
    moved to `executor: self_dispatch` (model-factory-cloud-dispatch-v1); this
    fixture is what several tests that used to piggyback on `corpus-planner`
    for exactly this purpose were redirected to. Mirrors `_builder_spec()`'s
    shape in `test_self_dispatch.py`.
    """
    return {
        "slug": slug,
        "role_family": role_family,
        "mission": "fixture spec for tier2 dispatch-mechanics tests",
        "read_first": [],
        "mandate": [{"task": "do the thing", "stop_or_ask": "ask if unsure"}],
        "laws": ["single-writer discipline"],
        "done_when": "done",
        "final_response_shape": "a summary",
        "runtime": {"budget_usd": 2.00, **runtime_overrides},
    }


# --------------------------------------------------------------------------
# dry-run spends nothing
# --------------------------------------------------------------------------


def test_dry_run_spawns_no_subprocess():
    with patch("subprocess.run") as mock_run:
        results = fnr.dispatch_stage("S4", RUN_ID, GRAPH_ID, SLUG, dry_run=True)
    mock_run.assert_not_called()
    assert all(r["dispatched"] is False for r in results)


def test_dry_run_still_resolves_the_full_chain():
    """Spending nothing must not mean checking nothing — a dry run that skipped
    spec resolution would hide a broken spec until the first paid run.
    """
    results = fnr.dispatch_stage("S4", RUN_ID, GRAPH_ID, SLUG, dry_run=True)
    assert [r["spec"] for r in results] == list(fnr.S4_CHAIN)
    assert all(r["runtime"]["budget_usd"] > 0 for r in results)


def test_dry_run_writes_no_prompt_files():
    before = set((FACTORY_DIR / "runs").glob("*/prompts/*")) if (FACTORY_DIR / "runs").exists() else set()
    fnr.dispatch_stage("S5", RUN_ID, GRAPH_ID, SLUG, dry_run=True)
    after = set((FACTORY_DIR / "runs").glob("*/prompts/*")) if (FACTORY_DIR / "runs").exists() else set()
    assert before == after


# --------------------------------------------------------------------------
# chain ordering
# --------------------------------------------------------------------------


def test_s4_chain_is_in_dependency_order():
    assert fnr.S4_CHAIN == (
        "corpus-planner", "corpus-family-generator", "corpus-reader", "curation-consolidator",
    )


def test_every_chain_spec_exists_and_declares_its_stage():
    """Guards the chain against a spec being renamed or re-staged out from
    under it — the kind of drift that only shows up on a paid run."""
    for stage_code, chain in fnr.STAGE_CHAINS.items():
        for slug in chain:
            spec = fnr.load_spec(slug)
            assert spec["slug"] == slug
            assert stage_code in spec["stages"], f"{slug} does not declare {stage_code}"


def test_chain_stops_at_the_first_failure(tmp_path, monkeypatch):
    """Continuing past a failed first node would have the second node working
    from a result that does not exist — real money for garbage.

    Uses two synthetic tier2 fixtures rather than the real S4_CHAIN: this is a
    test of `dispatch_stage`'s generic stop-at-first-failure mechanic via the
    tier2 subprocess-mock pattern, and `corpus-planner` (the chain's real first
    node) is now `executor: self_dispatch`, which never calls `runner` at all.
    """
    spec_a = _tier2_spec("chain-stop-fixture-a")
    spec_b = _tier2_spec("chain-stop-fixture-b")
    specs = {spec_a["slug"]: spec_a, spec_b["slug"]: spec_b}
    monkeypatch.setattr(fnr, "load_spec", lambda slug: specs[slug])
    monkeypatch.setattr(fnr, "STAGE_CHAINS", {**fnr.STAGE_CHAINS, "S4": (spec_a["slug"], spec_b["slug"])})

    calls = []

    def _runner(argv):
        calls.append(argv)
        return _Proc(returncode=1, stderr="node blew up")

    results = fnr.dispatch_stage(
        "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False, runner=_runner, ledger_dir=tmp_path
    )
    assert len(calls) == 1
    assert len(results) == 1
    assert results[0]["ok"] is False


# --------------------------------------------------------------------------
# argv: caps and tools
# --------------------------------------------------------------------------


def test_argv_carries_model_turn_cap_and_dollar_cap(tmp_path):
    spec = fnr.load_spec("corpus-planner")
    argv = fnr.build_tier2_argv(spec, "S4:corpus-planner", tmp_path / "p.md")
    assert argv[0].endswith("tier2_run.sh")
    for flag in ("--model", "--max-turns", "--budget-usd", "--timeout", "--prompt-file", "--label"):
        assert flag in argv, f"{flag} missing from the dispatch argv"
    runtime = fnr.resolve_runtime(spec)
    assert argv[argv.index("--model") + 1] == runtime["model"]
    assert argv[argv.index("--max-turns") + 1] == str(runtime["max_turns"])
    assert float(argv[argv.index("--budget-usd") + 1]) == pytest.approx(runtime["budget_usd"])


def test_allowed_tools_never_contains_task(tmp_path):
    for chain in fnr.STAGE_CHAINS.values():
        for slug in chain:
            spec = fnr.load_spec(slug)
            argv = fnr.build_tier2_argv(spec, "S4", tmp_path / "p.md")
            tools = argv[argv.index("--allowed-tools") + 1].split(",")
            assert "Task" not in tools, f"{slug} would be able to nest-spawn"


def test_task_is_stripped_even_when_a_spec_asks_for_it(caplog):
    """Defense in depth. The schema forbids `Task` in `runtime.allowed_tools`,
    but the dispatcher must not rely on validation having run — it strips
    unconditionally, and says so rather than doing it silently.
    """
    spec = {"slug": "hypothetical", "role_family": "builder",
            "runtime": {"allowed_tools": ["Read", "Task", "Bash"]}}
    import logging
    with caplog.at_level(logging.WARNING, logger="factory_node_run"):
        tools = fnr.resolve_allowed_tools(spec)
    assert tools == ["Read", "Bash"]
    assert any("nest-spawn" in r.getMessage() for r in caplog.records), \
        "Task was stripped silently — the removal must be logged, not hidden"


def test_spec_runtime_overrides_the_role_default():
    spec = {"role_family": "reader", "runtime": {"budget_usd": 7.5}}
    runtime = fnr.resolve_runtime(spec)
    assert runtime["budget_usd"] == 7.5
    assert runtime["model"] == fnr.ROLE_DEFAULTS["reader"]["model"]


def test_unknown_role_family_falls_back_conservatively():
    runtime = fnr.resolve_runtime({"role_family": "not-a-real-family"})
    assert runtime == fnr.FALLBACK_RUNTIME


def test_label_is_ledger_greppable(tmp_path):
    """PRDebug greps the ledger for `factory-node` to prove a dry run spent
    nothing. That only works if the label actually starts that way."""
    spec = fnr.load_spec("preflight-linter")
    argv = fnr.build_tier2_argv(spec, "S5:preflight-linter", tmp_path / "p.md")
    assert argv[argv.index("--label") + 1].startswith("factory-node")


# --------------------------------------------------------------------------
# verdict parsing: exit code is not proof
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("all checks ran\nVERDICT: pass", "pass"),
        ("VERDICT: fail", "fail"),
        ("VERDICT: suspect", "suspect"),
        ("verdict: PASS", "pass"),
        ("VERDICT = fail", "fail"),
        ("VERDICT: suspect\nreconsidered\nVERDICT: fail", "fail"),
        ("everything looks great!", None),
        ("", None),
        (None, None),
        ("the verdict is that it passes", None),
    ],
)
def test_verdict_parsing(text, expected):
    assert fnr.parse_verifier_verdict(text) == expected


def test_a_verifier_exiting_zero_without_a_verdict_is_not_a_pass(tmp_path):
    """LESSON-016, stated as a test: the realistic failure is a script that
    logs per-item errors and returns 0. A missing verdict must fail the node.
    """
    result = fnr.dispatch_node(
        "preflight-linter", "S5", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        runner=lambda argv: _Proc(returncode=0, stdout="ran 6 checks, some errored"),
        ledger_dir=tmp_path,
    )
    assert result["verdict"] is None
    assert result["ok"] is False


def test_a_verifier_exiting_zero_with_a_fail_verdict_is_not_a_pass(tmp_path):
    result = fnr.dispatch_node(
        "preflight-linter", "S5", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        runner=lambda argv: _Proc(returncode=0, stdout="VERDICT: fail"),
        ledger_dir=tmp_path,
    )
    assert result["ok"] is False


def test_a_clean_verifier_pass_is_a_pass(tmp_path):
    result = fnr.dispatch_node(
        "preflight-linter", "S5", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        runner=lambda argv: _Proc(returncode=0, stdout="VERDICT: pass"),
        ledger_dir=tmp_path,
    )
    assert result["ok"] is True
    assert result["verdict"] == "pass"


def test_nonzero_exit_fails_the_node_even_with_a_pass_verdict(tmp_path):
    """A crashed process claiming success is not success."""
    result = fnr.dispatch_node(
        "preflight-linter", "S5", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        runner=lambda argv: _Proc(returncode=2, stdout="VERDICT: pass"),
        ledger_dir=tmp_path,
    )
    assert result["ok"] is False


def test_non_verifier_roles_are_not_verdict_gated(tmp_path):
    """Generic tier2 mechanic (verdict gating is skipped for non-verifier
    roles) — exercised via a synthetic tier2 fixture rather than the real
    `corpus-planner`, which is now `executor: self_dispatch` and would never
    reach the mocked runner at all.
    """
    spec = _tier2_spec("verdict-gating-fixture")
    result = fnr.dispatch_node(
        spec["slug"], "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=spec,
        runner=lambda argv: _Proc(returncode=0, stdout="wrote the mixture plan"),
        ledger_dir=tmp_path,
    )
    assert result["ok"] is True
    assert result["verdict"] is None


# --------------------------------------------------------------------------
# prompt rendering
# --------------------------------------------------------------------------


def test_prompt_carries_the_specs_mandate_laws_and_identity():
    spec = fnr.load_spec("corpus-planner")
    prompt = fnr.build_prompt(spec, "S4:corpus-planner", RUN_ID, GRAPH_ID, SLUG)
    assert spec["mission"] in prompt
    assert spec["mandate"][0]["task"] in prompt
    assert spec["laws"][0] in prompt
    assert RUN_ID in prompt and GRAPH_ID in prompt and SLUG in prompt


def test_default_stream_preserves_exact_legacy_prompt_identity_line():
    prompt = fnr.build_prompt(
        fnr.load_spec("corpus-planner"),
        "S4:corpus-planner",
        RUN_ID,
        GRAPH_ID,
        SLUG,
    )
    assert prompt.splitlines()[2] == (
        f"Node: S4:corpus-planner | run: {RUN_ID} | graph: {GRAPH_ID} | org: {SLUG}"
    )
    assert "| stream:" not in prompt.splitlines()[2]


def test_intelligence_prompt_appends_explicit_stream_identity():
    prompt = fnr.build_prompt(
        fnr.load_spec("corpus-planner"),
        "S4:corpus-planner",
        RUN_ID,
        GRAPH_ID,
        SLUG,
        model_stream="intelligence",
    )
    assert prompt.splitlines()[2] == (
        f"Node: S4:corpus-planner | run: {RUN_ID} | graph: {GRAPH_ID} | org: {SLUG}"
        " | stream: intelligence"
    )


def test_verifier_prompts_demand_a_machine_readable_verdict():
    prompt = fnr.build_prompt(fnr.load_spec("preflight-linter"), "S5", RUN_ID, GRAPH_ID, SLUG)
    assert "VERDICT: pass|fail|suspect" in prompt
    assert "never as a pass" in prompt


def test_non_verifier_prompts_do_not_demand_a_verdict():
    prompt = fnr.build_prompt(fnr.load_spec("corpus-planner"), "S4", RUN_ID, GRAPH_ID, SLUG)
    assert "VERDICT:" not in prompt


def test_node_id_vocabulary_is_stage_code_qualified():
    """Decision D2: stage codes, not charter node numbers 1-13."""
    assert fnr.node_id_for("S4") == "S4"
    assert fnr.node_id_for("S4", "corpus-planner") == "S4:corpus-planner"


# --------------------------------------------------------------------------
# decision events
# --------------------------------------------------------------------------


def test_each_real_dispatch_emits_a_decision_event(tmp_path):
    """Generic tier2 mechanic — a synthetic fixture, not `corpus-planner`
    (now `executor: self_dispatch`, whose first call returns before this
    module's own decision-log call is ever reached).
    """
    spec = _tier2_spec("decision-event-fixture")
    fnr.dispatch_node(
        spec["slug"], "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=spec,
        runner=lambda argv: _Proc(returncode=0, stdout="ok"),
        ledger_dir=tmp_path,
    )
    rows = [
        json.loads(line)
        for line in (list(tmp_path.glob("*.jsonl"))[0]).read_text().splitlines()
        if line.strip()
    ]
    decisions = [r for r in rows if r["event"] == "factory_decision"]
    assert len(decisions) == 1
    assert decisions[0]["node_id"] == f"S4:{spec['slug']}"
    assert decisions[0]["actor"] == "agent"


def test_a_missing_spec_is_a_clear_error():
    with pytest.raises(fnr.NodeDispatchError):
        fnr.load_spec("no-such-agent-spec")


# --------------------------------------------------------------------------
# crash_resume.paid specs refuse to dispatch for real without an explicit,
# out-of-band opt-in (model-factory-cloud-dispatch-v1) — --dry-run defaults to
# False and S6g can self-approve under auto_approve_under_usd, so neither is a
# real gate on train-launcher specifically, and a cloud-dispatched agent
# reaches dispatch_node with the same Bash access a human at a terminal has.
# --------------------------------------------------------------------------


def test_paid_spec_refuses_to_dispatch_without_the_env_opt_in(monkeypatch):
    monkeypatch.delenv("FACTORY_ALLOW_REAL_SPEND", raising=False)

    def _runner(argv):
        pytest.fail("must not reach the runner — the guard sits before it")

    with pytest.raises(fnr.NodeDispatchError, match="FACTORY_ALLOW_REAL_SPEND"):
        fnr.dispatch_node(
            "train-launcher", "S7", RUN_ID, GRAPH_ID, SLUG, dry_run=False, runner=_runner,
        )


def test_paid_spec_still_refuses_even_with_approved_and_dry_run_false():
    """The whole point: this is not routed around by the flags that gate
    everything else. No --approved/--manifest equivalent is passed here on
    purpose — dispatch_node has no such params, which is exactly the finding:
    nothing upstream of this function's own check stops it."""
    with pytest.raises(fnr.NodeDispatchError, match="FACTORY_ALLOW_REAL_SPEND"):
        fnr.dispatch_node(
            "train-launcher", "S7", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
            runner=lambda argv: pytest.fail("must not reach the runner"),
        )


def test_paid_spec_dispatches_once_the_env_opt_in_is_set(monkeypatch, tmp_path):
    monkeypatch.setenv("FACTORY_ALLOW_REAL_SPEND", "1")
    calls = []

    def _runner(argv):
        calls.append(argv)
        return _Proc(returncode=0, stdout="VERDICT: pass")

    result = fnr.dispatch_node(
        "train-launcher", "S7", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        runner=_runner, ledger_dir=tmp_path,
    )
    assert len(calls) == 1
    assert result["dispatched"] is True


def test_a_non_paid_spec_is_unaffected_by_the_guard(monkeypatch, tmp_path):
    """The guard must not become a second, redundant dry-run switch for the
    other 30+ specs that were never paid in the first place. A synthetic tier2
    fixture stands in for "a non-paid spec" here rather than the real
    `corpus-planner` (now `executor: self_dispatch`, which would never reach
    the mocked runner and would report `dispatched: False` on its first call
    regardless of this guard)."""
    monkeypatch.delenv("FACTORY_ALLOW_REAL_SPEND", raising=False)
    spec = _tier2_spec("non-paid-guard-fixture")
    calls = []

    def _runner(argv):
        calls.append(argv)
        return _Proc(returncode=0, stdout="VERDICT: pass")

    result = fnr.dispatch_node(
        spec["slug"], "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=spec,
        runner=_runner, ledger_dir=tmp_path,
    )
    assert len(calls) == 1
    assert result["dispatched"] is True


# --------------------------------------------------------------------------
# factory.py wiring
# --------------------------------------------------------------------------


@pytest.fixture
def build_slug(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_LEDGER_DIR", str(tmp_path))
    slug = "pytest-b3-synthetic-org"
    yield slug
    import shutil
    shutil.rmtree(FACTORY_DIR / "runs" / slug, ignore_errors=True)


def _cfg():
    return {
        "org": {"name": "Synthetic B3 Org", "id": "x", "customer_status": "CONTRACTED"},
        "model_plan": {
            "system_prompt_source": "src/x.ts", "inventory_tool": "t", "custom_watch_items": [],
            "seed": 42, "slices": {"a": 1}, "variant": "full",
        },
        "paths": {"dossiers_out": "out/", "platform_repo": "platform-alpha", "export_script": "x.ts"},
    }


def test_stage_build_dry_run_spawns_nothing_and_records_the_chain(build_slug):
    import factory as factory_module
    slug = build_slug

    class Args:
        approved = False
        dry_run = True
        live = False
        sync_url = None

    with patch("subprocess.run") as mock_run:
        path = factory_module.stage_build(_cfg(), slug, Args())
    mock_run.assert_not_called()
    report = json.loads(Path(path).read_text())
    assert [n["spec"] for n in report["nodes"]] == list(fnr.S4_CHAIN)
    assert all(n["dispatched"] is False for n in report["nodes"])


def test_stage_verify_dry_run_spawns_nothing(build_slug):
    import factory as factory_module
    slug = build_slug

    class Args:
        approved = False
        dry_run = True
        live = False
        sync_url = None

    with patch("subprocess.run") as mock_run:
        path = factory_module.stage_verify(_cfg(), slug, Args())
    mock_run.assert_not_called()
    report = json.loads(Path(path).read_text())
    assert [n["spec"] for n in report["nodes"]] == list(fnr.S5_CHAIN)


def test_verifier_verdict_lands_where_stage_row_reads_it(build_slug):
    """`factory_sync._stage_row` has always read `findings.verifier_verdict`
    and nothing ever wrote it. This is the first writer — assert the handoff at
    the seam, not just that the key exists.
    """
    import factory as factory_module
    import factory_sync
    slug = build_slug

    class Args:
        approved = False
        dry_run = False
        live = True
        sync_url = None

    with patch.object(fnr, "dispatch_stage", return_value=[
        {"node_id": "S5:preflight-linter", "spec": "preflight-linter", "role_family": "verifier",
         "ok": True, "verdict": "pass", "dispatched": True},
    ]):
        path = factory_module.stage_verify(_cfg(), slug, Args())
    report = json.loads(Path(path).read_text())
    assert report["findings"]["verifier_verdict"] == "pass"
    assert factory_sync._stage_row("run-id", "verify", report)["verifier_verdict"] == "pass"


@pytest.mark.parametrize(
    "verdicts,expected",
    [
        (["pass"], "pass"),
        (["fail"], "fail"),
        (["suspect"], "suspect"),
        ([None], "suspect"),
        (["pass", "fail"], "fail"),
        (["pass", None], "suspect"),
    ],
)
def test_verdict_aggregation_never_upgrades_uncertainty_to_pass(build_slug, verdicts, expected):
    """An unverified corpus and a verified-clean corpus must not look the same
    downstream. Any missing or suspect verdict degrades the stage's verdict.
    """
    import factory as factory_module
    slug = build_slug

    class Args:
        approved = False
        dry_run = False
        live = True
        sync_url = None

    nodes = [{"node_id": f"S5:v{i}", "spec": "preflight-linter", "role_family": "verifier",
              "ok": v == "pass", "verdict": v, "dispatched": True} for i, v in enumerate(verdicts)]
    with patch.object(fnr, "dispatch_stage", return_value=nodes):
        path = factory_module.stage_verify(_cfg(), slug, Args())
    assert json.loads(Path(path).read_text())["findings"]["verifier_verdict"] == expected


def test_a_broken_dispatcher_fails_the_stage_rather_than_crashing(build_slug):
    import factory as factory_module
    slug = build_slug

    class Args:
        approved = False
        dry_run = True
        live = False
        sync_url = None

    with patch.object(fnr, "dispatch_stage", side_effect=RuntimeError("spec dir vanished")):
        path = factory_module.stage_build(_cfg(), slug, Args())
    report = json.loads(Path(path).read_text())
    assert report["nodes"][0]["ok"] is False
