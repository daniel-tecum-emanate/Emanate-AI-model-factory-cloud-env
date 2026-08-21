import hashlib
import json
import sys
import uuid
from pathlib import Path

import pytest

FACTORY_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FACTORY_DIR / "swarm"))

import local_factory  # noqa: E402
import protocol  # noqa: E402
import runner  # noqa: E402


PARTITION_COMMAND = (
    "./scripts/check_partition_overlap.sh factory-automation "
    "factory/runs/swarm/ptc-grand-steel-final-v1/**"
)


def make_order(tmp_path, *, role="KEEPER", lane="ptc", wave="W1"):
    ledger_root = tmp_path / "ledger"
    local_factory.create_program(ledger_root, "runner-test-program")
    input_path = "factory-automation/model-training-swarm/PROGRAM.md"
    repo = tmp_path / "repo"
    source = repo / input_path
    source.parent.mkdir(parents=True)
    source.write_text("test program evidence\n")
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    output = f"factory-automation/factory/runs/{'ptc-steel' if lane == 'ptc' else 'grand-steel'}/swarm/{role.lower()}.json"
    order = runner.build_work_order(
        ledger_root,
        repo,
        lane=lane,
        role=role,
        wave=wave,
        task_id=f"{lane}-{role.lower()}",
        attempt_key="attempt-1",
        read_paths=[input_path],
        owned_output_paths=[output],
        forbidden_paths=[
            "factory-automation/factory/runs/grand-steel"
            if lane == "ptc"
            else "factory-automation/factory/runs/ptc-steel"
        ],
        partition_command=PARTITION_COMMAND,
        heartbeat_row_id="CURSOR-MODEL-TRAINING-SWARM-IMPL",
        lesson_manifest=(
            [
                {
                    "lesson_id": f"{lane}-L001",
                    "claim_type": "FACT",
                    "failure_mode": "Prior evidence was not mechanically reproduced.",
                    "mechanical_check": "sha256sum source",
                    "gate_effect": "degrade-to-suspect",
                    "source_hashes": {input_path: source_digest},
                    "owner_roles": [role],
                    "required_agreement": 1,
                    "supersedes": [],
                }
            ]
            if role != "LANE-COORD"
            else None
        ),
    )
    return ledger_root, repo, order


def dispatch_ok(*args, **kwargs):
    return {
        "node_id": f"{args[1]}:{args[0]}",
        "spec": args[0],
        "ok": True,
        "verdict": None,
        "exit_code": 0,
        "dry_run": kwargs["dry_run"],
    }


def dispatch_specialist_ok(*args, **kwargs):
    result = dispatch_ok(*args, **kwargs)
    result["final_response"] = json.dumps(
        {
            "verdict": "PASS",
            "lesson_dispositions": [
                {
                    "lesson_id": "ptc-L001",
                    "disposition": "APPLIED",
                    "evidence_chain": ["source:test-program"],
                    "mechanical_check_result": "PASS",
                    "effect_on_plan": "Keep the reproduced evidence gate.",
                }
            ],
            "claims": [],
            "blockers": [],
            "next_reversible_action": "Aggregate the accepted report.",
        }
    )
    return result


def dispatch_fenced_specialist_ok(*args, **kwargs):
    result = dispatch_specialist_ok(*args, **kwargs)
    result["final_response"] = f"```json\n{result['final_response']}\n```"
    return result


def test_build_and_save_work_order_is_hash_bound_and_immutable(tmp_path):
    ledger_root, repo, order = make_order(tmp_path)
    result = protocol.validate_work_order(order, root=repo)
    assert result["valid"] is True
    assert result["lesson_ids"] == ["ptc-L001"]
    path = runner.save_work_order(ledger_root, order)
    assert json.loads(path.read_text()) == order
    assert runner.save_work_order(ledger_root, order) == path

    changed = dict(order)
    changed["task_id"] = "changed"
    with pytest.raises(runner.RunnerError, match="refusing to overwrite"):
        runner.save_work_order(ledger_root, changed)


def test_dry_run_dispatch_records_proven_zero_cost_and_contribution(tmp_path):
    ledger_root, repo, order = make_order(tmp_path)
    result = runner.run_work_order(
        ledger_root, repo, order, dispatcher=dispatch_ok
    )
    assert result["dispatch"]["dry_run"] is True
    assert result["dispatch"]["node_id"].startswith("W1-PTC:")
    assert result["cost"] == {
        "value_usd": 0,
        "source": "no-paid-call",
        "accepted_unknown": False,
    }
    packet = local_factory.build_lane_packet(ledger_root, "ptc")
    assert packet["counts"]["execution_attempts"] == 1
    assert packet["counts"]["contributions"] == 1
    assert packet["cost"]["known_attempt_cost_usd"] == 0
    assert packet["cost"]["attempt_cost_coverage"] == 1
    report = json.loads((repo / order["owned_output_paths"][0]).read_text())
    assert report["execution_attempt_id"] == order["execution_attempt_id"]
    assert protocol.canonical_sha256(report) == result["dispatch"]["report_sha256"]


def test_invalid_hash_fails_before_dispatch(tmp_path):
    ledger_root, repo, order = make_order(tmp_path)
    order["input_hashes"][order["read_paths"][0]] = "0" * 64
    called = False

    def should_not_run(*args, **kwargs):
        nonlocal called
        called = True

    with pytest.raises(protocol.ProtocolError, match="visibility manifest"):
        runner.run_work_order(
            ledger_root, repo, order, dispatcher=should_not_run
        )
    assert called is False
    assert local_factory.read_events(ledger_root, "ptc") == []


def test_budget_mismatch_fails_before_dispatch(tmp_path):
    ledger_root, repo, order = make_order(tmp_path)
    order["max_cost_usd"] += 0.01
    # Revalidation permits a numeric cap; the bridge binds it to the reviewed spec.
    protocol.validate_work_order(order, root=repo)
    with pytest.raises(runner.RunnerError, match="does not equal"):
        runner.run_work_order(ledger_root, repo, order, dispatcher=dispatch_ok)
    assert local_factory.read_events(ledger_root, "ptc") == []


def test_live_final_eval_requires_separate_signed_contract(tmp_path):
    ledger_root, repo, order = make_order(
        tmp_path, role="FINAL-EVAL-CUSTODIAN", wave="W6"
    )
    with pytest.raises(runner.RunnerError, match="separately validated"):
        runner.run_work_order(
            ledger_root, repo, order, live=True, dispatcher=dispatch_ok
        )
    assert local_factory.read_events(ledger_root, "ptc") == []


def test_live_missing_cost_correlation_remains_unknown_and_unaccepted(
    tmp_path, monkeypatch
):
    ledger_root, repo, order = make_order(tmp_path)
    order["product_target_run_id"] = str(uuid.uuid4())
    monkeypatch.setattr(runner, "_ledger_match", lambda *args, **kwargs: None)
    result = runner.run_work_order(
        ledger_root, repo, order, live=True, dispatcher=dispatch_ok
    )
    assert result["cost"]["value_usd"] is None
    assert result["cost"]["accepted_unknown"] is False
    packet = local_factory.build_lane_packet(ledger_root, "ptc")
    assert packet["terminal_blockers"]["unaccepted_unknown_cost_attempt_ids"] == [
        order["execution_attempt_id"]
    ]


def test_live_specialist_requires_valid_complete_lesson_dispositions(
    tmp_path, monkeypatch
):
    ledger_root, repo, order = make_order(tmp_path)
    order["product_target_run_id"] = str(uuid.uuid4())
    monkeypatch.setattr(runner, "_ledger_match", lambda *args, **kwargs: None)
    result = runner.run_work_order(
        ledger_root,
        repo,
        order,
        live=True,
        dispatcher=dispatch_specialist_ok,
    )
    assert result["attempt_event"]["data"]["state"] == "COMPLETED"
    report = json.loads((repo / order["owned_output_paths"][0]).read_text())
    assert report["response_validation"]["valid"] is True
    assert report["response_validation"]["structured_response"]["verdict"] == "PASS"


def test_single_json_fence_is_accepted_without_surrounding_prose(
    tmp_path, monkeypatch
):
    ledger_root, repo, order = make_order(tmp_path)
    order["product_target_run_id"] = str(uuid.uuid4())
    monkeypatch.setattr(runner, "_ledger_match", lambda *args, **kwargs: None)
    result = runner.run_work_order(
        ledger_root,
        repo,
        order,
        live=True,
        dispatcher=dispatch_fenced_specialist_ok,
    )
    assert result["attempt_event"]["data"]["state"] == "COMPLETED"


def test_live_specialist_malformed_response_is_logged_blocked(
    tmp_path, monkeypatch
):
    ledger_root, repo, order = make_order(tmp_path)
    order["product_target_run_id"] = str(uuid.uuid4())
    monkeypatch.setattr(runner, "_ledger_match", lambda *args, **kwargs: None)
    result = runner.run_work_order(
        ledger_root, repo, order, live=True, dispatcher=dispatch_ok
    )
    assert result["attempt_event"]["data"]["state"] == "BLOCKED"
    assert result["contribution_event"]["data"]["disposition"] == "contested"
    report = json.loads((repo / order["owned_output_paths"][0]).read_text())
    assert report["response_validation"]["valid"] is False


def test_live_dispatch_requires_hash_bound_product_target(tmp_path):
    ledger_root, repo, order = make_order(tmp_path)
    with pytest.raises(runner.RunnerError, match="product_target_run_id"):
        runner.run_work_order(
            ledger_root, repo, order, live=True, dispatcher=dispatch_ok
        )


def test_live_specialist_requires_role_scoped_lesson_manifest(tmp_path):
    ledger_root, repo, order = make_order(tmp_path)
    order["product_target_run_id"] = str(uuid.uuid4())
    del order["lesson_manifest"]
    protocol.validate_work_order(order, root=repo)
    with pytest.raises(runner.RunnerError, match="lesson_manifest"):
        runner.run_work_order(
            ledger_root, repo, order, live=True, dispatcher=dispatch_ok
        )


def test_lesson_manifest_rejects_wrong_owner(tmp_path):
    _, repo, order = make_order(tmp_path)
    order["lesson_manifest"][0]["owner_roles"] = ["REDTEAM"]
    with pytest.raises(protocol.ProtocolError, match="dispatched role"):
        protocol.validate_work_order(order, root=repo)
