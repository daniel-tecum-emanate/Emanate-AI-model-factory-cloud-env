import copy
import hashlib
import json
import sys
import uuid
from pathlib import Path

import pytest

FACTORY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FACTORY_DIR))

from swarm import protocol


PROGRAM_ID = "11111111-1111-4111-8111-111111111111"
ATTEMPT_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def work_order(tmp_path):
    source = tmp_path / "inputs" / "ptc-steel" / "raw.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"rows": 3}\n')
    relative = "inputs/ptc-steel/raw.json"
    digest = _sha(source)
    owned = "outputs/ptc-steel/keeper/report.json"
    return {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "lane": "ptc",
        "wave": "W1",
        "logical_role_id": f"{PROGRAM_ID}/ptc/KEEPER",
        "execution_attempt_id": ATTEMPT_ID,
        "predecessor_attempt_id": None,
        "runtime_session_id": "cursor-session-1",
        "predecessor_runtime_session_id": None,
        "continuity_mode": "AUTHOR_RESUME",
        "factory_run_id": RUN_ID,
        "task_id": "ptc-keeper-rebaseline",
        "max_cost_usd": 0,
        "zone": "factory-automation",
        "read_paths": [relative],
        "write_paths": [owned],
        "owned_output_paths": [owned],
        "forbidden_paths": ["inputs/grand-steel", "secrets", "final-eval"],
        "visibility_manifest": [{"path": relative, "sha256": digest}],
        "input_hashes": {relative: digest},
        "partition_check_receipt": {
            "status": "pass",
            "command": "./scripts/check_partition_overlap.sh factory-automation outputs/ptc-steel/keeper/report.json",
            "checked_at": "2026-08-04T14:00:00-07:00",
            "receipt_sha256": SHA_A,
            "owned_paths": [owned],
        },
        "heartbeat": {
            "row_id": "CURSOR-MODEL-TRAINING-SWARM-KEEPER",
            "zone": "factory-automation",
            "lease_seconds": 1800,
            "sequence_interval_seconds": 600,
            "seq": 1,
            "updated_at": "2026-08-04T14:00:00-07:00",
            "lease_expires_at": "2026-08-04T14:30:00-07:00",
        },
        "state_capsule": None,
    }


def claim():
    return {
        "claim_id": "PTC-CALL-RATE-01",
        "claim_type": "FACT",
        "metric_version": "call-rate-v2",
        "source_hashes": [SHA_A],
        "observed_at": "2026-08-04T14:00:00-07:00",
        "reproduction_command": "python recompute.py --frozen manifest.json",
        "numerator": 10,
        "denominator": 20,
        "included_ids": ["row-1", "row-2"],
        "arm_hashes": {"champion": SHA_B},
        "trust": "deterministic",
        "method": "full deterministic census",
        "evidence_chain": ["source:raw", "derivation:call-rate-v2"],
        "epistemic_status": "supported",
        "gate_effect": "none",
        "load_bearing": True,
        "polarity": "positive",
        "finding_basis": "none",
        "challenger_evidence": [
            {
                "claim_id": "PTC-CALL-RATE-CHALLENGER",
                "method": "account-stratified independent replay",
                "evidence_chain": ["source:replay", "derivation:stratified-v1"],
                "source_hashes": [SHA_C],
            }
        ],
    }


def final_eval_contract():
    return {
        "schema_version": 1,
        "contract_id": "ptc-final-v1",
        "evaluation_version": "v1",
        "protocol_sha256": SHA_A,
        "sealed_frame_sha256": SHA_B,
        "signed_authority": {
            "signer": "Daniel Tecum",
            "signed_at": "2026-08-04T14:00:00-07:00",
            "signature_sha256": SHA_C,
        },
        "candidate_limit": 1,
        "looks_used": 0,
        "candidate_ids": [],
        "stopping_rule": "single-pre-registered-candidate",
    }


def shadow_contract():
    return {
        "schema_version": 1,
        "contract_id": "ptc-shadow-v1",
        "protocol_sha256": SHA_A,
        "signed_authority": {
            "signer": "Daniel Tecum",
            "signed_at": "2026-08-04T14:00:00-07:00",
            "signature_sha256": SHA_B,
        },
        "calendar_start": "2026-08-10",
        "calendar_end": "2026-08-16",
        "exposure_definition": "all eligible production decisions during each UTC night",
        "minimum_valid_nights": 5,
        "maximum_invalid_rate": 0.2,
        "intent_to_observe_denominator": 7,
        "extension_policy": "new-signed-protocol-only",
        "stopping_rule": "observe-entire-fixed-window",
    }


def assert_code(code, fn, *args, **kwargs):
    with pytest.raises(protocol.ProtocolError) as caught:
        fn(*args, **kwargs)
    assert caught.value.code == code


def test_valid_work_order_verifies_real_input_hash_before_dispatch(tmp_path):
    order = work_order(tmp_path)
    result = protocol.validate_work_order(order, root=tmp_path)

    assert result["valid"] is True
    assert result["logical_role_id"].endswith("/ptc/KEEPER")
    assert len(result["canonical_sha256"]) == 64


def test_work_order_unknown_fields_fail_closed(tmp_path):
    order = work_order(tmp_path)
    order["dispatch_anyway"] = True

    assert_code("UNKNOWN_FIELD", protocol.validate_work_order, order, root=tmp_path)


def test_unauthorized_read_is_rejected_before_visibility_checks(tmp_path):
    order = work_order(tmp_path)
    order["read_paths"] = ["secrets/provider.key"]

    assert_code("FORBIDDEN_PATH", protocol.validate_work_order, order, root=tmp_path)


def test_actual_input_hash_mutation_is_rejected(tmp_path):
    order = work_order(tmp_path)
    (tmp_path / order["read_paths"][0]).write_text('{"rows": 4}\n')

    assert_code("STALE_HASH", protocol.validate_work_order, order, root=tmp_path)


def test_manifest_hash_disagreement_is_rejected_without_file_reads(tmp_path):
    order = work_order(tmp_path)
    order["input_hashes"][order["read_paths"][0]] = SHA_A

    assert_code(
        "STALE_HASH",
        protocol.validate_work_order,
        order,
        root=tmp_path,
        verify_files=False,
    )


def test_lane_crossover_in_read_path_is_rejected(tmp_path):
    order = work_order(tmp_path)
    cross_lane = "inputs/grand-steel/raw.json"
    order["read_paths"] = [cross_lane]
    order["visibility_manifest"] = [{"path": cross_lane, "sha256": SHA_A}]
    order["input_hashes"] = {cross_lane: SHA_A}
    order["forbidden_paths"] = ["secrets", "final-eval"]

    assert_code(
        "LANE_CROSSOVER",
        protocol.validate_work_order,
        order,
        root=tmp_path,
        verify_files=False,
    )


@pytest.mark.parametrize(
    "field",
    ["read_paths", "write_paths", "owned_output_paths"],
)
def test_final_eval_leakage_to_non_custodian_is_rejected(tmp_path, field):
    order = work_order(tmp_path)
    leaked = "runs/ptc-steel/final-eval/grader-prompt.json"
    order["forbidden_paths"] = ["inputs/grand-steel", "secrets"]
    if field == "read_paths":
        order[field] = [leaked]
        order["visibility_manifest"] = [{"path": leaked, "sha256": SHA_A}]
        order["input_hashes"] = {leaked: SHA_A}
    else:
        order["write_paths"] = [leaked]
        order["owned_output_paths"] = [leaked]
        order["partition_check_receipt"]["owned_paths"] = [leaked]

    assert_code(
        "FINAL_EVAL_LEAKAGE",
        protocol.validate_work_order,
        order,
        root=tmp_path,
        verify_files=False,
    )


def test_final_eval_custodian_may_receive_sealed_material(tmp_path):
    order = work_order(tmp_path)
    final_path = "runs/ptc-steel/final-eval/sealed-frame.json"
    order["logical_role_id"] = f"{PROGRAM_ID}/ptc/FINAL-EVAL-CUSTODIAN"
    order["read_paths"] = [final_path]
    order["visibility_manifest"] = [{"path": final_path, "sha256": SHA_A}]
    order["input_hashes"] = {final_path: SHA_A}
    order["forbidden_paths"] = ["inputs/grand-steel", "secrets"]

    assert protocol.validate_work_order(
        order, root=tmp_path, verify_files=False
    )["valid"]


def test_logical_role_must_be_lane_scoped_and_closed(tmp_path):
    order = work_order(tmp_path)
    order["logical_role_id"] = f"{PROGRAM_ID}/grand-steel/KEEPER"

    assert_code(
        "LOGICAL_ROLE_ID",
        protocol.validate_work_order,
        order,
        root=tmp_path,
        verify_files=False,
    )


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda order: order["partition_check_receipt"].update(status="suspect"), "PARTITION_CHECK"),
        (lambda order: order["partition_check_receipt"].update(command="echo pass"), "PARTITION_CHECK"),
        (lambda order: order["heartbeat"].update(lease_seconds=3600), "HEARTBEAT"),
        (lambda order: order["heartbeat"].update(sequence_interval_seconds=601), "HEARTBEAT"),
        (
            lambda order: order["heartbeat"].update(
                lease_expires_at="2026-08-04T14:29:59-07:00"
            ),
            "HEARTBEAT",
        ),
    ],
)
def test_partition_and_lease_mutations_are_rejected(tmp_path, mutation, code):
    order = work_order(tmp_path)
    mutation(order)

    assert_code(
        code,
        protocol.validate_work_order,
        order,
        root=tmp_path,
        verify_files=False,
    )


def test_write_paths_must_be_exclusively_owned_and_receipted(tmp_path):
    order = work_order(tmp_path)
    order["owned_output_paths"] = ["outputs/ptc-steel/other.json"]

    assert_code(
        "OWNERSHIP",
        protocol.validate_work_order,
        order,
        root=tmp_path,
        verify_files=False,
    )


def test_successor_reconstruction_requires_distinct_session_and_capsule(tmp_path):
    order = work_order(tmp_path)
    order.update(
        execution_attempt_id="44444444-4444-4444-8444-444444444444",
        predecessor_attempt_id=ATTEMPT_ID,
        predecessor_runtime_session_id="cursor-session-1",
        runtime_session_id="cursor-session-2",
        continuity_mode="SUCCESSOR_RECONSTRUCTION",
        state_capsule="Open claim: PTC-CALL-RATE-01\nReport: sha256:" + SHA_A,
    )

    assert protocol.validate_work_order(
        order, root=tmp_path, verify_files=False
    )["valid"]

    order["runtime_session_id"] = "cursor-session-1"
    assert_code(
        "CONTINUITY",
        protocol.validate_work_order,
        order,
        root=tmp_path,
        verify_files=False,
    )


def test_author_resume_requires_same_runtime_identity():
    record = {
        "execution_attempt_id": "44444444-4444-4444-8444-444444444444",
        "predecessor_attempt_id": ATTEMPT_ID,
        "runtime_session_id": "new-session",
        "predecessor_runtime_session_id": "old-session",
        "continuity_mode": "AUTHOR_RESUME",
    }

    assert_code("CONTINUITY", protocol.validate_continuity, record)


@pytest.mark.parametrize(
    ("old_state", "new_state"),
    [
        ("PENDING", "COMPLETED"),
        ("RUNNING", "PENDING"),
        ("COMPLETED", "RUNNING"),
        ("FAILED", "RUNNING"),
        ("RUNNING", "RUNNING"),
    ],
)
def test_illegal_attempt_transitions_are_rejected(old_state, new_state):
    assert_code(
        "ILLEGAL_TRANSITION",
        protocol.validate_attempt_transition,
        old_state,
        new_state,
    )


@pytest.mark.parametrize(
    ("old_state", "new_state"),
    [
        ("PENDING", "RUNNING"),
        ("RUNNING", "COMPLETED"),
        ("RUNNING", "BLOCKED"),
        ("RUNNING", "TIMED_OUT"),
        ("RUNNING", "FAILED"),
        ("RUNNING", "UNRESUMABLE"),
    ],
)
def test_legal_attempt_transitions_are_accepted(old_state, new_state):
    protocol.validate_attempt_transition(old_state, new_state)


def test_capsule_over_150_lines_is_rejected():
    assert_code(
        "CAPSULE_LINES",
        protocol.validate_state_capsule,
        "\n".join("line" for _ in range(151)),
    )


def test_capsule_over_4000_token_estimate_is_rejected():
    assert_code("CAPSULE_TOKENS", protocol.validate_state_capsule, "x" * 16001)


def test_capsule_boundary_is_accepted():
    capsule = "\n".join("x" * 100 for _ in range(150))
    size = protocol.validate_state_capsule(capsule)
    assert size["line_count"] == 150
    assert size["token_estimate"] <= 4000


def test_complete_claim_and_envelope_are_accepted():
    value = claim()
    protocol.validate_claim(value)
    protocol.validate_claim_envelope({"schema_version": 1, "claims": [value]})


@pytest.mark.parametrize(
    "field",
    [
        "claim_id",
        "claim_type",
        "metric_version",
        "source_hashes",
        "observed_at",
        "reproduction_command",
        "numerator",
        "denominator",
        "included_ids",
        "arm_hashes",
        "trust",
        "epistemic_status",
        "gate_effect",
    ],
)
def test_malformed_incomplete_claims_are_rejected(field):
    value = claim()
    del value[field]

    assert_code("MISSING_FIELD", protocol.validate_claim, value)


def test_positive_load_bearing_claim_requires_distinct_challenger():
    value = claim()
    value["challenger_evidence"][0]["method"] = value["method"]
    value["challenger_evidence"][0]["evidence_chain"] = value["evidence_chain"]

    assert_code("MISSING_CHALLENGER", protocol.validate_claim, value)


def test_unsupported_blocking_dissent_is_rejected():
    value = claim()
    value.update(
        epistemic_status="unsupported",
        gate_effect="block",
        finding_basis="specialist",
    )

    assert_code("UNSUPPORTED_BLOCK", protocol.validate_claim, value)


def test_epistemic_status_and_gate_effect_cannot_be_conflated():
    value = claim()
    value["epistemic_status"] = "block"

    assert_code("ENUM", protocol.validate_claim, value)


def test_reproducible_critical_supported_claim_may_block():
    value = claim()
    value.update(
        epistemic_status="supported",
        gate_effect="block",
        finding_basis="reproducible-critical",
        polarity="negative",
        challenger_evidence=[],
    )

    protocol.validate_claim(value)


def test_duplicate_claim_ids_are_rejected():
    value = claim()
    assert_code(
        "DUPLICATE_CLAIM",
        protocol.validate_claim_envelope,
        {"schema_version": 1, "claims": [value, copy.deepcopy(value)]},
    )


def test_valid_final_eval_one_look_contract():
    contract = final_eval_contract()
    protocol.validate_final_eval_contract(contract)
    contract["looks_used"] = 1
    contract["candidate_ids"] = ["candidate-v1"]
    protocol.validate_final_eval_contract(contract)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(candidate_limit=2),
        lambda value: value.update(looks_used=2, candidate_ids=["a", "b"]),
        lambda value: value.update(stopping_rule="continue-until-significant"),
        lambda value: value.update(looks_used=1, candidate_ids=[]),
    ],
)
def test_final_eval_optional_stopping_mutations_are_rejected(mutation):
    value = final_eval_contract()
    mutation(value)

    assert_code("OPTIONAL_STOPPING", protocol.validate_final_eval_contract, value)


def test_valid_fixed_shadow_window_contract():
    protocol.validate_shadow_window_contract(shadow_contract())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(intent_to_observe_denominator=5),
        lambda value: value.update(extension_policy="extend-until-five-valid-nights"),
        lambda value: value.update(stopping_rule="stop-after-five-successes"),
    ],
)
def test_shadow_optional_stopping_mutations_are_rejected(mutation):
    value = shadow_contract()
    mutation(value)

    assert_code("OPTIONAL_STOPPING", protocol.validate_shadow_window_contract, value)


def test_deterministic_validation_output_is_byte_stable(tmp_path):
    order = work_order(tmp_path)
    first = protocol.deterministic_validation("work-order", order, root=tmp_path)
    second = protocol.deterministic_validation("work-order", order, root=tmp_path)

    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(
        second, sort_keys=True, separators=(",", ":")
    )
    assert first["valid"] is True
    assert first["errors"] == []


def test_cli_emits_deterministic_json_and_nonzero_for_invalid(tmp_path, capsys):
    value = final_eval_contract()
    value["candidate_limit"] = 2
    input_path = tmp_path / "contract.json"
    input_path.write_text(json.dumps(value))

    exit_code = protocol.main(["final-eval", str(input_path)])
    output = capsys.readouterr().out.strip()

    assert exit_code == 1
    assert json.loads(output)["errors"][0]["code"] == "OPTIONAL_STOPPING"
    assert output == json.dumps(json.loads(output), sort_keys=True, separators=(",", ":"))


def test_validation_failure_json_is_stable_and_has_no_traceback():
    value = claim()
    del value["source_hashes"]

    result = protocol.deterministic_validation("claim", value)

    assert result["valid"] is False
    assert result["errors"] == [
        {
            "code": "MISSING_FIELD",
            "path": "$",
            "message": "missing required field(s): source_hashes",
        }
    ]
    assert uuid.UUID(PROGRAM_ID)
