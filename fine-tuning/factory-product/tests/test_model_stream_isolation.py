"""Safe two-stream identity for the Grand Steel Intelligence setup."""

import copy
import json
import sys
from pathlib import Path
from unittest.mock import patch

import importlib.util

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory  # noqa: E402
import factory_context  # noqa: E402
import factory_gates  # noqa: E402
import factory_heldout  # noqa: E402
import factory_node_run  # noqa: E402
import factory_sync  # noqa: E402
import org_config_sync  # noqa: E402

SLUG = "grand-steel"
LEGACY_RUN_ID = "523ecbbf-e049-405f-8354-779e2cd40307"
INT_RUN_ID = "11111111-1111-4111-8111-111111111111"
REAL_INT_CFG = factory.load_config(SLUG, "intelligence")[0]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for module in (factory, factory_context, factory_gates, factory_heldout, factory_sync):
        monkeypatch.setattr(module, "HERE", tmp_path)
    monkeypatch.setattr(factory, "REPO", tmp_path.parent)
    return tmp_path


def _int_cfg():
    return copy.deepcopy(REAL_INT_CFG)


def test_collision_01_duplicate_slug_for_same_org_id_is_rejected(tmp_path):
    body = (
        "org:\n  slug: {slug}\n  id: same-org\n  name: X\n"
        "governance:\n  permitted_uses: []\n"
    )
    (tmp_path / "grand-steel.yaml").write_text(body.format(slug="grand-steel"))
    (tmp_path / "grand-steel-intelligence.yaml").write_text(
        body.format(slug="grand-steel-intelligence")
    )
    with pytest.raises(org_config_sync.OrgConfigSyncError, match="already used by"):
        org_config_sync.build_rows(configs_dir=tmp_path)


def test_collision_02_intelligence_state_never_modifies_legacy_state(isolated):
    legacy = isolated / "runs" / SLUG / ".sync_state.json"
    legacy.parent.mkdir(parents=True)
    before = b'{"run_id":"523ecbbf-e049-405f-8354-779e2cd40307"}\n'
    legacy.write_bytes(before)
    factory_sync._save_run_state(
        SLUG, {"run_id": INT_RUN_ID, "iteration": 1}, stream="intelligence"
    )
    assert legacy.read_bytes() == before
    assert factory_sync._run_state_path(SLUG, "intelligence") == (
        isolated / "runs" / SLUG / "intelligence" / ".sync_state.json"
    )


def test_collision_03_reports_approvals_and_heldout_are_stream_local(isolated):
    cfg = {"_model_stream": "intelligence"}
    report = factory.write_report(SLUG, "census", {"status": "attention"}, cfg=cfg)
    factory.record_approval(
        SLUG, "governance", "fixture", stream="intelligence"
    )
    factory_heldout.seal(
        SLUG, [f"item-{n}" for n in range(30)], stream="intelligence"
    )
    assert report.parent == isolated / "runs" / SLUG / "intelligence"
    assert (report.parent / "approvals" / "governance.json").is_file()
    assert (report.parent / "heldout" / "01-manifest.json").is_file()
    assert not (isolated / "runs" / SLUG / "01-census-report.json").exists()
    assert not (isolated / "runs" / SLUG / "approvals" / "governance.json").exists()


@pytest.mark.parametrize("stream", ["per-account", "intelligence"])
def test_collision_04_queued_claim_filters_the_requested_stream(
    isolated, monkeypatch, stream
):
    calls = []

    def fake_select(table, params=None, **kwargs):
        calls.append(params)
        return [{"id": INT_RUN_ID, "iteration": 1, "predecessor_run_id": None}]

    monkeypatch.setattr(factory_sync, "select", fake_select)
    monkeypatch.setattr(factory_sync, "update", lambda *a, **k: [{"id": INT_RUN_ID}])
    claimed = factory_sync.claim_queued_run(SLUG, stream=stream)
    assert claimed["run_id"] == INT_RUN_ID
    queue_query = next(p for p in calls if p.get("status") == "eq.queued")
    assert queue_query["model_stream"] == f"eq.{stream}"


def test_collision_05_run_projection_preserves_intelligence_stream(isolated):
    cfg = _int_cfg()
    factory_sync._save_run_state(
        SLUG, {"run_id": INT_RUN_ID, "iteration": 1}, stream="intelligence"
    )
    row = factory_sync._run_row(
        cfg, SLUG, "census", {"status": "attention"}, INT_RUN_ID,
        stream="intelligence",
    )
    assert row["model_stream"] == "intelligence"
    assert row["run_kind"] == "first-train"


def test_collision_06_first_intelligence_lineage_ignores_per_account_state(
    isolated, monkeypatch
):
    factory_sync._save_run_state(
        SLUG,
        {"run_id": LEGACY_RUN_ID, "iteration": 9, "predecessor_run_id": "older"},
    )
    monkeypatch.setattr(factory_sync, "select", lambda *a, **k: [])
    run_id = factory_sync.get_or_create_run_id(SLUG, stream="intelligence")
    state = factory_sync._load_run_state(SLUG, stream="intelligence")
    assert run_id != LEGACY_RUN_ID
    assert state == {"run_id": run_id, "iteration": 1}


def test_collision_07_per_account_approval_cannot_satisfy_intelligence(isolated):
    factory.record_approval(SLUG, "governance", "old", stream="per-account")
    assert factory.gate_approved(SLUG, "governance", "per-account") is True
    assert factory.gate_approved(SLUG, "governance", "intelligence") is False
    assert factory_gates.snapshot(SLUG, stream="intelligence")["governance"] is None


def test_collision_08_intelligence_never_inherits_per_account_budget_or_approval():
    legacy, _ = factory.load_config(SLUG)
    intelligence, _ = factory.load_config(SLUG, "intelligence")
    assert legacy["budget"]["auto_approve_under_usd"] == 30
    assert "auto_approve_under_usd" not in intelligence["budget"]
    assert intelligence["budget"]["train_all_in_cap_usd"] is None
    assert intelligence["_stream_plan_status"] == "blocked_on_approval"


def test_collision_09_cli_defaults_legacy_and_accepts_explicit_stream():
    parser = factory.build_parser()
    assert parser.parse_args([SLUG, "--status"]).stream == "per-account"
    assert parser.parse_args(
        [SLUG, "--stream", "intelligence", "--status"]
    ).stream == "intelligence"


def test_collision_10_live_sibling_does_not_block_stream_scoped_setup_claim(
    isolated, monkeypatch
):
    queries = []

    def fake_select(table, params=None, **kwargs):
        queries.append(params)
        if params.get("status") == "eq.queued":
            return [{"id": INT_RUN_ID, "iteration": 1, "predecessor_run_id": None}]
        return []

    monkeypatch.setattr(factory_sync, "select", fake_select)
    monkeypatch.setattr(factory_sync, "update", lambda *a, **k: [{"id": INT_RUN_ID}])
    claimed = factory_sync.claim_queued_run(SLUG, stream="intelligence")
    assert claimed["run_id"] == INT_RUN_ID
    assert all("neq." not in str(query) for query in queries)
    assert all(
        query.get("model_stream") == "eq.intelligence"
        for query in queries
        if query.get("status") == "eq.queued"
    )


def test_collision_11_catalog_identity_is_distinct_and_no_artifact_is_created():
    cfg = _int_cfg()
    assert cfg["_stream_output_id"] == "grand-steel-int-v1"
    assert cfg["model_plan"]["output_model"].endswith("/grand-steel-int-v1")
    assert cfg["model_plan"]["output_model"] != (
        "accounts/emanate/models/grand-steel-v1-agent"
    )
    assert "factory_models" not in Path(factory.__file__).read_text()


def test_collision_12_org_sync_emits_one_org_row_without_stream_plan_content():
    rows = [
        row for row in org_config_sync.build_rows()
        if row["org_slug"] == SLUG
    ]
    assert len(rows) == 1
    row = rows[0]
    assert "model_plans" not in row
    assert "model_plan" not in row
    assert "book" not in row


def test_intelligence_config_is_an_additive_appended_plan():
    data = (FACTORY_DIR / "configs" / "grand-steel.yaml").read_text()
    marker = "\n# Additive second stream."
    legacy_prefix, separator, appended = data.partition(marker)
    assert separator
    assert "model_plan:" in legacy_prefix
    assert "model_plans:" not in legacy_prefix
    assert "model_plans:" in appended
    assert "intelligence:" in appended


def test_intelligence_s1_is_truthful_and_s2_stays_pending(isolated, monkeypatch):
    cfg = _int_cfg()

    class Args:
        stream = "intelligence"
        dry_run = True
        approved = False
        live = False
        sync_url = None

    monkeypatch.delenv("FACTORY_SUPABASE_URL", raising=False)
    s1 = factory.stage_census(cfg, SLUG, Args())
    census = json.loads(s1.read_text())
    assert census["audited_counts"]["oracle_user_turns"] == 59
    assert census["audited_counts"]["exact_target_envelope_captures"] == 0
    assert census["audited_counts"]["partial_persisted_oracle_tool_call_logs"] == 87
    assert census["audited_counts"]["current_fact_bearing_accounts"] == 294
    assert census["factual_axis"]["legacy_snapshot_is_stale"] is True
    assert census["served_contract"]["unrestricted_tool_count"] == 10
    assert census["recommendation"].startswith("BLOCKED")

    s2 = factory.stage_governance(cfg, SLUG, Args())
    packet = json.loads(s2.read_text())
    assert packet["status"] == "gate_pending"
    architecture = packet["decision_request"]["intelligence_architecture_approval"]
    assert architecture["approval_ready"] is False
    assert architecture["held_out_seal_states"]["oracle_whole_conversation"]["state"] == "provisional_unsealed"
    assert architecture["model_need_and_comparators"]["comparator_battery"] == "not_run"
    assert architecture["exact_contract_capture"]["exact_target_envelope_captures"] == 0
    assert architecture["oracle_governance"]["status"] == "pending"
    assert architecture["provisional_base"]["status"] == "provisional_pending_approval"
    assert architecture["platform_token_budget"]["status"] == "not_approved"
    assert not (
        isolated / "runs" / SLUG / "intelligence" / "approvals" / "governance.json"
    ).exists()


def test_stream_specific_context_and_heldout_paths(isolated):
    assert factory_context.iterations_dir(SLUG, stream="intelligence") == (
        isolated / "runs" / SLUG / "intelligence" / "iterations"
    )
    assert factory_heldout.manifest_path(SLUG, 1, stream="intelligence") == (
        isolated / "runs" / SLUG / "intelligence" / "heldout" / "01-manifest.json"
    )


def test_intelligence_approval_flag_cannot_bypass_unresolved_s2(isolated):
    cfg = _int_cfg()

    class Args:
        stream = "intelligence"
        dry_run = True
        approved = True
        live = False
        sync_url = None

    path = factory.stage_governance(cfg, SLUG, Args())
    packet = json.loads(path.read_text())
    assert packet["status"] == "REFUSED"
    assert packet["decision_request"]["intelligence_architecture_approval"]["approval_ready"] is False
    assert not (
        isolated / "runs" / SLUG / "intelligence" / "approvals" / "governance.json"
    ).exists()


def test_intelligence_s3_is_release_blocked_even_with_approval_flag(
    isolated, monkeypatch
):
    monkeypatch.setenv("FACTORY_SUPABASE_URL", "https://sync.invalid")
    monkeypatch.setattr(
        sys, "argv",
        ["factory.py", SLUG, "--stream", "intelligence", "--stage", "export",
         "--approved", "--dry-run"],
    )
    monkeypatch.setattr(
        factory, "HANDLERS",
        {**factory.HANDLERS, "export": lambda *args: pytest.fail("S3 handler ran")},
    )
    monkeypatch.setattr(factory, "load_config", lambda *args: (_int_cfg(), Path("fixture")))
    with patch.object(factory_sync, "push_stage") as sync_stage, \
         patch.object(factory_sync, "upsert") as sync_upsert, \
         patch.object(factory_sync, "update") as sync_update, \
         patch.object(factory.subprocess, "run") as provider_call, \
         patch.object(factory, "_dispatch_stage_nodes") as model_call, \
         pytest.raises(SystemExit) as exc:
        factory.main()
    assert exc.value.code == 3
    sync_stage.assert_not_called()
    sync_upsert.assert_not_called()
    sync_update.assert_not_called()
    provider_call.assert_not_called()
    model_call.assert_not_called()
    report = json.loads(
        (isolated / "runs" / SLUG / "intelligence" / "03-export-report.json").read_text()
    )
    assert report["status"] == "RELEASE_BLOCKED"
    assert report["blocked_on"] == "org_scoped_execution_lease"
    assert report["approval_flag_ignored"] is True


def test_node_dispatch_threads_intelligence_to_heldout_and_gate_guards(
    isolated, monkeypatch
):
    spec = {
        "slug": "synthetic",
        "role_family": "builder",
        "mission": "test",
        "read_first": [],
        "mandate": [],
        "laws": [],
        "done_when": "done",
        "final_response_shape": "done",
    }
    seen = {"heldout": [], "snapshot": [], "assert": []}
    monkeypatch.setattr(factory_node_run, "HERE", isolated)
    monkeypatch.setattr(factory_node_run, "REPO", isolated)
    monkeypatch.setattr(factory_node_run, "load_spec", lambda slug: spec)
    monkeypatch.setattr(
        factory_heldout, "assert_not_visible",
        lambda *a, **k: seen["heldout"].append(k["stream"]),
    )
    monkeypatch.setattr(
        factory_gates, "snapshot",
        lambda *a, **k: seen["snapshot"].append(k["stream"]) or {},
    )
    monkeypatch.setattr(
        factory_gates, "assert_unchanged",
        lambda *a, **k: seen["assert"].append(k["stream"]),
    )
    monkeypatch.setattr(factory_sync, "push_node_agent", lambda *a, **k: True)
    result = factory_node_run.dispatch_node(
        "synthetic", "S4", INT_RUN_ID, "g", SLUG,
        dry_run=False,
        runner=lambda argv: type("Proc", (), {"returncode": 0, "stdout": "ok", "stderr": ""})(),
        model_stream="intelligence",
    )
    assert result["ok"] is True
    assert seen == {
        "heldout": ["intelligence"],
        "snapshot": ["intelligence"],
        "assert": ["intelligence"],
    }
    assert "runs/grand-steel/intelligence/prompts/" in result["prompt_file"]


def test_factory_stage_dispatch_passes_explicit_intelligence_stream():
    cfg = _int_cfg()

    class Args:
        stream = "intelligence"
        dry_run = True
        sync_url = None

    with patch.object(factory_node_run, "dispatch_stage", return_value=[]) as dispatch:
        factory._dispatch_stage_nodes("S4", cfg, SLUG, Args())
    assert dispatch.call_args.kwargs["model_stream"] == "intelligence"


# Cloud-environment guard, added 2026-08-24 (post-trim verification): this test patches
# `factory_cursor_dispatch.execute`, and importing that module requires `cursor_guard`,
# which is DELIBERATELY absent here (V-364 — it backs a local `claude` CLI with a machine's
# own Anthropic auth, which this environment is structurally barred from holding).
# Do NOT "fix" this by adding cursor_guard.py. Same guard as tests/conftest.py's
# collect_ignore for test_cursor_dispatch.py.
@pytest.mark.skipif(
    importlib.util.find_spec("cursor_guard") is None,
    reason="cursor_guard is deliberately absent from this cloud environment (V-364)",
)
def test_intelligence_cursor_dispatch_refuses_before_default_paths_are_used(
    monkeypatch
):
    spec = {
        "slug": "cursor-synthetic",
        "role_family": "reader",
        "mission": "test",
        "read_first": [],
        "mandate": [],
        "laws": [],
        "done_when": "done",
        "final_response_shape": "done",
        "runtime": {"executor": "cursor_sdk"},
    }
    monkeypatch.setattr(factory_heldout, "assert_not_visible", lambda *a, **k: None)
    with patch("factory_cursor_dispatch.execute") as execute, \
         pytest.raises(factory_node_run.NodeDispatchError, match="must not fall back"):
        factory_node_run.dispatch_node(
            spec["slug"], "S4", INT_RUN_ID, "g", SLUG,
            dry_run=False, spec_override=spec, model_stream="intelligence",
        )
    execute.assert_not_called()


def test_post_platform_intelligence_setup_registration_allows_live_sibling(
    isolated, monkeypatch
):
    queries = []
    writes = []

    def fake_select(table, params=None, **kwargs):
        queries.append((table, params))
        if table == "factory_runs" and params.get("status") == "eq.queued":
            return [{"id": INT_RUN_ID, "iteration": 1, "predecessor_run_id": None}]
        return []

    monkeypatch.setattr(factory_sync, "select", fake_select)
    monkeypatch.setattr(factory_sync, "update", lambda *a, **k: [{"id": INT_RUN_ID}])
    monkeypatch.setattr(
        factory_sync, "upsert",
        lambda table, rows, **kwargs: writes.append((table, rows)),
    )
    assert factory_sync.push_stage(
        _int_cfg(), SLUG, "census", {"status": "attention"},
        stream="intelligence",
    ) is True
    assert any(
        table == "factory_runs" and rows[0]["model_stream"] == "intelligence"
        for table, rows in writes
    )
    assert all("neq." not in str(params) for _, params in queries)


def test_forged_intelligence_approval_is_quarantined_in_intelligence_only(
    isolated
):
    int_approvals = isolated / "runs" / SLUG / "intelligence" / "approvals"
    legacy_approvals = isolated / "runs" / SLUG / "approvals"
    int_approvals.mkdir(parents=True)
    legacy_approvals.mkdir(parents=True)
    (legacy_approvals / "launch.json").write_text('{"human": true}')
    before = factory_gates.snapshot(SLUG, stream="intelligence")
    (int_approvals / "launch.json").write_text('{"forged": true}')
    with pytest.raises(factory_gates.GateForgeryError):
        factory_gates.assert_unchanged(
            SLUG, before, node_id="S4:synthetic", stream="intelligence"
        )
    assert not (int_approvals / "launch.json").exists()
    assert (legacy_approvals / "launch.json").exists()


def test_same_stream_claim_race_updates_only_one_queued_row(isolated, monkeypatch):
    monkeypatch.setattr(
        factory_sync,
        "select",
        lambda *a, **k: [{"id": INT_RUN_ID, "iteration": 1, "predecessor_run_id": None}],
    )
    updates = iter(([{"id": INT_RUN_ID}], []))
    monkeypatch.setattr(factory_sync, "update", lambda *a, **k: next(updates))
    assert factory_sync.claim_queued_run(SLUG, stream="intelligence")["run_id"] == INT_RUN_ID
    assert factory_sync.claim_queued_run(SLUG, stream="intelligence") is None
