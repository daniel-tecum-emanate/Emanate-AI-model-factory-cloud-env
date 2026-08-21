"""S6g auto-approval policy (Daniel, 2026-07-29): `budget.auto_approve_under_usd`.

Daniel asked to "provide the option to allow auto allow under set budget
constraints". The property under test is threefold, and the order matters:

1. Policy approves ONLY when configured and at/under the threshold — and the
   decision record says `policy`, never `human` (mislabeling would poison the
   gate-judgment dataset `_record_gate_decision` exists to build).
2. An over-cap projection is refused BEFORE the policy is consulted — the
   policy can never approve what the CLI itself refuses (L17).
3. No config key = no behavior change: the gate blocks exactly as before.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory  # noqa: E402

SLUG = "pytest-auto-approve-org"


def _args(approved=False):
    return SimpleNamespace(approved=approved, dry_run=True, sync_url=None, live=False)


def _write_projection(run_path, status="ok", total=22.56):
    idx = factory.STAGES.index("project") + 1
    (run_path / f"{idx:02d}-project-report.json").write_text(json.dumps({
        "status": status,
        "decision_request": {
            "recommendation": "within caps",
            "cost": {"total_projection_usd": total},
        },
    }))


def _run_launch(tmp_path, cfg, args, total=22.56, proj_status="ok"):
    run_path = tmp_path / SLUG
    run_path.mkdir(parents=True, exist_ok=True)
    _write_projection(run_path, status=proj_status, total=total)
    reports = {}

    def fake_write_report(slug, stage, body, cfg_, sync_url=None):
        reports["body"] = body
        return run_path / "launch-report.json"

    decisions = {}

    def fake_record_gate_decision(cfg_, slug, stage, args_, status, decision_request, actor_override=None):
        decisions["status"] = status
        decisions["actor_override"] = actor_override

    approvals = []
    approval_channels = []
    with patch.object(factory, "run_dir", lambda s: run_path), \
         patch.object(factory, "write_report", fake_write_report), \
         patch.object(factory, "_record_gate_decision", fake_record_gate_decision), \
         patch.object(factory, "record_approval",
                      lambda s, st, note, via=factory.APPROVED_VIA_FLAG:
                      (approvals.append(note), approval_channels.append(via))), \
         patch.object(factory, "_gate_approved_via_sync", lambda *a, **k: False):
        factory.stage_launch(cfg, SLUG, args)
    decisions["approval_channels"] = approval_channels
    return reports["body"], decisions, approvals


def test_under_threshold_auto_approves_with_policy_actor(tmp_path):
    cfg = {"budget": {"auto_approve_under_usd": 30}}
    body, decisions, approvals = _run_launch(tmp_path, cfg, _args(), total=22.56)
    assert body["status"] == "approved"
    assert body["approved_by"] == "policy"
    assert decisions["actor_override"] == "policy"
    assert any("AUTO-APPROVED by policy" in n for n in approvals)
    assert decisions["approval_channels"] == [factory.APPROVED_VIA_POLICY]


def test_over_threshold_still_blocks_on_the_human(tmp_path):
    cfg = {"budget": {"auto_approve_under_usd": 30}}
    body, decisions, approvals = _run_launch(tmp_path, cfg, _args(), total=31.00)
    assert body["status"] == "gate_pending"
    assert body["approved_by"] is None
    assert approvals == []


def test_no_config_key_means_no_policy_path(tmp_path):
    body, _, approvals = _run_launch(tmp_path, {"budget": {}}, _args(), total=1.00)
    assert body["status"] == "gate_pending"
    assert approvals == []


def test_over_cap_refusal_beats_the_policy(tmp_path):
    """L17 unchanged: a projection report that is not ok is REFUSED before the
    policy is even consulted — auto-approve cannot force an over-cap launch."""
    cfg = {"budget": {"auto_approve_under_usd": 1000}}
    body, decisions, approvals = _run_launch(tmp_path, cfg, _args(), total=999.0, proj_status="over_cap")
    assert body["status"] == "REFUSED"
    assert approvals == []
    assert decisions["actor_override"] is None


def test_explicit_human_approval_is_still_recorded_as_human(tmp_path):
    cfg = {"budget": {"auto_approve_under_usd": 30}}
    body, decisions, approvals = _run_launch(tmp_path, cfg, _args(approved=True), total=22.56)
    assert body["status"] == "approved"
    assert body["approved_by"] == "human"
    assert decisions["actor_override"] is None
    assert any("approved" in n and "AUTO" not in n for n in approvals)
    assert decisions["approval_channels"] == [factory.APPROVED_VIA_FLAG]
