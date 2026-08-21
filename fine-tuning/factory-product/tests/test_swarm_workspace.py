import copy
import hashlib
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

FACTORY_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FACTORY_DIR))

from swarm import workspace  # noqa: E402


PROGRAM_ID = "11111111-1111-4111-8111-111111111111"
PREDECESSOR_ID = "22222222-2222-4222-8222-222222222222"
SHA_A = "a" * 64
NOW = datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc)


def make_roles(lane="ptc"):
    return [
        workspace.PersistentRole.create(
            PROGRAM_ID,
            lane,
            role_name,
            read_paths=[f"factory-automation/factory/runs/{lane}/cloud/inputs"],
            owned_output_paths=[
                f"factory-automation/factory/runs/{lane}/cloud/roles/{role_name.lower()}"
            ],
            budget_usd=None,
        )
        for role_name in workspace.LANE_ROLE_NAMES[lane]
    ]


def make_workspace(lane="ptc"):
    return workspace.LaneWorkspace.create(
        PROGRAM_ID, lane, make_roles(lane), created_at=NOW
    )


def make_continuation(role, **overrides):
    values = {
        "continuity_mode": "AUTHOR_RESUME",
        "predecessor_attempt_id": None,
        "predecessor_runtime_session_id": None,
        "predecessor_report_sha256": None,
        "state_capsule": "Open claims: none\nBlockers: none\nChanged assumptions: none",
        "next_prompt": "# NEXT-PROMPT\nContinue only from the hash-bound work order.",
    }
    values.update(overrides)
    return workspace.ContinuationEnvelope.create(role.logical_role_id, **values)


def make_attempt(role, *, lease=None, continuation=None, **overrides):
    values = {
        "attempt_key": "attempt-1",
        "runtime_session_id": "cloud-session-1",
        "state": "PENDING",
        "cost_usd": None,
        "cost_source": "cloud-runtime-unreported",
        "cost_evidence_sha256": None,
        "report_sha256": None,
    }
    values.update(overrides)
    return workspace.CloudAttempt.create(
        role,
        lease or workspace.Lease.issue(role.logical_role_id, now=NOW),
        continuation or make_continuation(role),
        **values,
    )


def make_launch_manifest(*, validation_time=NOW):
    lane_workspace = make_workspace()
    leases = [
        workspace.Lease.issue(role.logical_role_id, now=NOW)
        for role in lane_workspace.roles
    ]
    attempts = [
        make_attempt(
            role,
            lease=lease,
            attempt_key=f"launch-{role.role_name.lower()}",
            runtime_session_id=f"cloud-{index}",
        )
        for index, (role, lease) in enumerate(zip(lane_workspace.roles, leases))
    ]
    return workspace.LaneLaunchManifest.create(
        lane_workspace,
        input_hashes={
            "factory-automation/factory/runs/ptc/cloud/inputs/frozen.json": SHA_A,
            "factory-automation/factory/runs/ptc/cloud/inputs/contract.json": "b"
            * 64,
        },
        leases=leases,
        initial_attempts=attempts,
        terminal_criteria=[
            "All six attempts have terminal dispositions.",
            "All observed costs are reconciled or explicitly accepted unknown.",
        ],
        validation_time=validation_time,
    )


def assert_code(code, callable_, *args, **kwargs):
    with pytest.raises(workspace.WorkspaceError) as caught:
        callable_(*args, **kwargs)
    assert caught.value.code == code


def test_lane_manifest_has_exactly_six_deterministic_identities_and_round_trips():
    manifest = make_workspace()

    assert len(manifest.roles) == 6
    assert {role.role_name for role in manifest.roles} == set(
        workspace.LANE_ROLE_NAMES["ptc"]
    )
    assert all(role.logical_role_id.startswith(f"{PROGRAM_ID}/ptc/") for role in manifest.roles)
    assert workspace.LaneWorkspace.from_json(manifest.to_json()) == manifest
    assert make_workspace().workspace_id == manifest.workspace_id
    assert make_workspace().manifest_sha256 == manifest.manifest_sha256
    assert manifest.to_json() == json.dumps(
        json.loads(manifest.to_json()), sort_keys=True, separators=(",", ":")
    )


@pytest.mark.parametrize("lane", ["ptc", "grand-steel"])
def test_each_lane_has_its_own_closed_six_role_vocabulary(lane):
    manifest = make_workspace(lane)
    assert tuple(role.role_name for role in manifest.roles) == workspace.LANE_ROLE_NAMES[lane]


def test_manifest_rejects_duplicate_role_identity():
    value = make_workspace().to_dict()
    value["roles"][-1] = copy.deepcopy(value["roles"][0])

    assert_code("DUPLICATE_ROLE", workspace.LaneWorkspace.from_dict, value)


def test_manifest_rejects_missing_identity():
    value = make_workspace().to_dict()
    value["roles"].pop()

    assert_code("ROLE_COUNT", workspace.LaneWorkspace.from_dict, value)


def test_manifest_rejects_overlapping_role_ownership():
    value = make_workspace().to_dict()
    value["roles"][1]["owned_output_paths"] = value["roles"][0]["owned_output_paths"] + [
        value["roles"][0]["owned_output_paths"][0] + "/child"
    ]

    assert_code("SCOPE_OVERLAP", workspace.LaneWorkspace.from_dict, value)


def test_manifest_rejects_cross_lane_role():
    value = make_workspace().to_dict()
    value["roles"][0] = make_roles("grand-steel")[0].to_dict()

    assert_code("LANE_CROSSOVER", workspace.LaneWorkspace.from_dict, value)


def test_strict_manifest_json_rejects_unknown_fields():
    value = make_workspace().to_dict()
    value["launch_anyway"] = True

    assert_code("UNKNOWN_FIELD", workspace.LaneWorkspace.from_dict, value)


def test_lease_is_exactly_thirty_minutes_and_renews_monotonically():
    role = make_workspace().roles[0]
    lease = workspace.Lease.issue(role.logical_role_id, now=NOW)
    renewed = lease.renew(now=NOW + timedelta(minutes=10))

    assert lease.expires_at == "2026-08-04T20:30:00Z"
    assert renewed.sequence == lease.sequence + 1
    assert renewed.lease_id != lease.lease_id
    renewed.assert_active(NOW + timedelta(minutes=39))


@pytest.mark.parametrize(
    "at",
    [NOW + timedelta(minutes=30), NOW + timedelta(hours=1)],
)
def test_lease_fails_closed_at_or_after_expiry(at):
    lease = workspace.Lease.issue(make_workspace().roles[0].logical_role_id, now=NOW)
    assert_code("STALE_LEASE", lease.assert_active, at)


def test_lease_rejects_noncanonical_window():
    value = workspace.Lease.issue(make_workspace().roles[0].logical_role_id, now=NOW).to_dict()
    value["expires_at"] = "2026-08-04T20:29:59Z"

    assert_code("LEASE_WINDOW", workspace.Lease.from_dict, value)


def test_continuation_hash_binds_bounded_capsule_and_next_prompt():
    role = make_workspace().roles[1]
    envelope = make_continuation(role)

    assert envelope.state_capsule_sha256 == hashlib.sha256(
        envelope.state_capsule.encode()
    ).hexdigest()
    assert envelope.next_prompt_sha256 == hashlib.sha256(
        envelope.next_prompt.encode()
    ).hexdigest()
    assert workspace.ContinuationEnvelope.from_dict(envelope.to_dict()) == envelope


def test_continuation_rejects_missing_capsule_or_next_prompt():
    role = make_workspace().roles[1]

    assert_code(
        "TYPE",
        workspace.ContinuationEnvelope.create,
        role.logical_role_id,
        continuity_mode="AUTHOR_RESUME",
        predecessor_attempt_id=None,
        predecessor_runtime_session_id=None,
        predecessor_report_sha256=None,
        state_capsule="",
        next_prompt="# NEXT-PROMPT",
    )
    assert_code(
        "TYPE",
        workspace.ContinuationEnvelope.create,
        role.logical_role_id,
        continuity_mode="AUTHOR_RESUME",
        predecessor_attempt_id=None,
        predecessor_runtime_session_id=None,
        predecessor_report_sha256=None,
        state_capsule="bounded",
        next_prompt="",
    )


def test_continuation_uses_protocol_capsule_bounds():
    role = make_workspace().roles[1]
    assert_code(
        "CAPSULE_LINES",
        workspace.ContinuationEnvelope.create,
        role.logical_role_id,
        continuity_mode="AUTHOR_RESUME",
        predecessor_attempt_id=None,
        predecessor_runtime_session_id=None,
        predecessor_report_sha256=None,
        state_capsule="\n".join("line" for _ in range(151)),
        next_prompt="# NEXT-PROMPT",
    )


def test_continuation_rejects_mutated_capsule_or_prompt_bytes():
    envelope = make_continuation(make_workspace().roles[1]).to_dict()
    envelope["state_capsule"] += "\nmutated"
    assert_code("STALE_HASH", workspace.ContinuationEnvelope.from_dict, envelope)

    envelope = make_continuation(make_workspace().roles[1]).to_dict()
    envelope["next_prompt"] += "\nmutated"
    assert_code("STALE_HASH", workspace.ContinuationEnvelope.from_dict, envelope)


def test_successor_continuity_requires_complete_predecessor_chain():
    role = make_workspace().roles[1]
    valid = make_continuation(
        role,
        continuity_mode="SUCCESSOR_RECONSTRUCTION",
        predecessor_attempt_id=PREDECESSOR_ID,
        predecessor_runtime_session_id="cloud-session-old",
        predecessor_report_sha256=SHA_A,
    )
    assert valid.continuity_mode == "SUCCESSOR_RECONSTRUCTION"

    value = valid.to_dict()
    value["predecessor_report_sha256"] = None
    assert_code("CONTINUITY", workspace.ContinuationEnvelope.from_dict, value)


def test_initial_continuity_cannot_claim_successor_mode():
    role = make_workspace().roles[1]
    assert_code(
        "CONTINUITY",
        workspace.ContinuationEnvelope.create,
        role.logical_role_id,
        continuity_mode="SUCCESSOR_RECONSTRUCTION",
        predecessor_attempt_id=None,
        predecessor_runtime_session_id=None,
        predecessor_report_sha256=None,
        state_capsule="bounded",
        next_prompt="# NEXT-PROMPT",
    )


def test_only_lane_coordinator_may_route_delegation():
    manifest = make_workspace()
    requester, target = manifest.roles[1], manifest.roles[2]
    request = workspace.DelegationRequest.create(
        PROGRAM_ID,
        "ptc",
        requester.logical_role_id,
        target.logical_role_id,
        "verify-corpus",
        read_paths=[target.read_paths[0] + "/frozen.json"],
        owned_output_paths=[target.owned_output_paths[0] + "/report.json"],
        created_at=NOW,
    )

    assert_code(
        "DELEGATION_AUTHORITY",
        manifest.route_delegation,
        requester.logical_role_id,
        request,
    )
    routed = manifest.route_delegation(manifest.coordinator.logical_role_id, request)
    assert routed.routed_by_role_id == manifest.coordinator.logical_role_id
    assert request.routed_by_role_id is None


def test_delegation_fails_closed_outside_target_scope():
    manifest = make_workspace()
    requester, target = manifest.roles[1], manifest.roles[2]
    request = workspace.DelegationRequest.create(
        PROGRAM_ID,
        "ptc",
        requester.logical_role_id,
        target.logical_role_id,
        "verify-corpus",
        read_paths=[target.read_paths[0] + "/frozen.json"],
        owned_output_paths=[manifest.roles[3].owned_output_paths[0] + "/report.json"],
        created_at=NOW,
    )

    assert_code(
        "WRITE_SCOPE",
        manifest.route_delegation,
        manifest.coordinator.logical_role_id,
        request,
    )


def test_routed_delegation_cannot_be_routed_twice():
    manifest = make_workspace()
    requester, target = manifest.roles[1], manifest.roles[2]
    request = workspace.DelegationRequest.create(
        PROGRAM_ID,
        "ptc",
        requester.logical_role_id,
        target.logical_role_id,
        "verify-corpus",
        read_paths=[],
        owned_output_paths=[target.owned_output_paths[0] + "/report.json"],
        created_at=NOW,
    )
    routed = manifest.route_delegation(manifest.coordinator.logical_role_id, request)

    assert_code(
        "DELEGATION_STATE",
        manifest.route_delegation,
        manifest.coordinator.logical_role_id,
        routed,
    )


def test_cloud_attempt_preserves_unknown_cost_as_null():
    role = make_workspace().roles[1]
    attempt = make_attempt(role)

    assert attempt.cost_usd is None
    assert attempt.to_dict()["cost_usd"] is None
    assert workspace.CloudAttempt.from_dict(attempt.to_dict()) == attempt
    assert uuid.UUID(attempt.attempt_id)


def test_cloud_attempt_rejects_unproven_zero_cost():
    role = make_workspace().roles[1]

    assert_code("UNPROVEN_ZERO_COST", make_attempt, role, cost_usd=0)


def test_cloud_attempt_accepts_zero_only_with_hashed_evidence():
    role = make_workspace().roles[1]
    attempt = make_attempt(
        role,
        cost_usd=0,
        cost_source="no-paid-call",
        cost_evidence_sha256=SHA_A,
    )
    assert attempt.cost_usd == 0


def test_unknown_cost_cannot_carry_value_evidence():
    role = make_workspace().roles[1]
    assert_code(
        "UNKNOWN_COST",
        make_attempt,
        role,
        cost_usd=None,
        cost_evidence_sha256=SHA_A,
    )


def test_attempt_fails_dispatch_on_stale_lease():
    manifest = make_workspace()
    role = manifest.roles[1]
    attempt = make_attempt(role)

    assert_code(
        "STALE_LEASE",
        manifest.start_attempt,
        attempt,
        now=NOW + timedelta(minutes=30),
    )


def test_attempt_fails_when_lease_belongs_to_another_role():
    manifest = make_workspace()
    role = manifest.roles[1]
    wrong_lease = workspace.Lease.issue(manifest.roles[2].logical_role_id, now=NOW)

    assert_code("ROLE_MISMATCH", make_attempt, role, lease=wrong_lease)


def test_terminal_attempt_requires_report_hash():
    role = make_workspace().roles[1]
    assert_code("MISSING_REPORT", make_attempt, role, state="COMPLETED")

    completed = make_attempt(role, state="COMPLETED", report_sha256=SHA_A)
    assert completed.report_sha256 == SHA_A


def test_attempt_id_is_deterministic_and_attempt_key_sensitive():
    role = make_workspace().roles[1]
    first = make_attempt(role)
    second = make_attempt(role)
    third = make_attempt(role, attempt_key="attempt-2")

    assert first.attempt_id == second.attempt_id
    assert first.attempt_id != third.attempt_id


@pytest.mark.parametrize(
    ("mode", "predecessor_session", "runtime_session"),
    [
        ("AUTHOR_RESUME", "cloud-session-old", "cloud-session-new"),
        ("SUCCESSOR_RECONSTRUCTION", "cloud-session-same", "cloud-session-same"),
    ],
)
def test_attempt_rejects_false_runtime_continuity(
    mode, predecessor_session, runtime_session
):
    role = make_workspace().roles[1]
    continuation = make_continuation(
        role,
        continuity_mode=mode,
        predecessor_attempt_id=PREDECESSOR_ID,
        predecessor_runtime_session_id=predecessor_session,
        predecessor_report_sha256=SHA_A,
    )

    assert_code(
        "CONTINUITY",
        make_attempt,
        role,
        continuation=continuation,
        runtime_session_id=runtime_session,
    )


def test_attempt_rejects_self_predecessor_continuity():
    role = make_workspace().roles[1]
    attempt = make_attempt(role)
    value = attempt.to_dict()
    continuation = value["continuation"]
    continuation.update(
        continuity_mode="SUCCESSOR_RECONSTRUCTION",
        predecessor_attempt_id=attempt.attempt_id,
        predecessor_runtime_session_id="cloud-session-old",
        predecessor_report_sha256=SHA_A,
    )

    assert_code("CONTINUITY", workspace.CloudAttempt.from_dict, value)


@pytest.mark.parametrize(
    ("factory", "field"),
    [
        (lambda: make_workspace().to_dict(), "workspace_id"),
        (
            lambda: workspace.Lease.issue(
                make_workspace().roles[0].logical_role_id, now=NOW
            ).to_dict(),
            "lease_id",
        ),
        (lambda: make_attempt(make_workspace().roles[1]).to_dict(), "attempt_id"),
    ],
)
def test_deterministic_identifiers_reject_substitution(factory, field):
    value = factory()
    value[field] = "99999999-9999-4999-8999-999999999999"
    parser = {
        "workspace_id": workspace.LaneWorkspace.from_dict,
        "lease_id": workspace.Lease.from_dict,
        "attempt_id": workspace.CloudAttempt.from_dict,
    }[field]

    assert_code("DETERMINISTIC_ID", parser, value)


def test_role_scope_rejects_other_lane_paths():
    value = make_roles("ptc")[0].to_dict()
    value["owned_output_paths"] = [
        "factory-automation/factory/runs/grand-steel/cloud/stolen"
    ]

    assert_code("LANE_CROSSOVER", workspace.PersistentRole.from_dict, value)


def test_malformed_json_and_unknown_nested_fields_fail_closed():
    assert_code("INPUT_JSON", workspace.LaneWorkspace.from_json, "{")

    value = make_workspace().to_dict()
    value["roles"][0]["shared_write"] = True
    assert_code("UNKNOWN_FIELD", workspace.LaneWorkspace.from_dict, value)


def test_launch_manifest_packages_six_roles_leases_attempts_and_round_trips():
    manifest = make_launch_manifest()

    assert len(manifest.workspace.roles) == 6
    assert len(manifest.leases) == 6
    assert len(manifest.initial_attempts) == 6
    assert all(attempt.cost_usd is None for attempt in manifest.initial_attempts)
    assert workspace.LaneLaunchManifest.from_json(
        manifest.to_json(), validation_time=NOW
    ) == manifest
    assert manifest.to_json() == json.dumps(
        json.loads(manifest.to_json()), sort_keys=True, separators=(",", ":")
    )
    assert manifest.manifest_sha256 == workspace.canonical_sha256(
        manifest.to_dict()
    )


def test_launch_manifest_is_deeply_immutable_at_public_boundaries():
    manifest = make_launch_manifest()

    with pytest.raises(TypeError):
        manifest.input_hashes[
            "factory-automation/factory/runs/ptc/cloud/inputs/new.json"
        ] = SHA_A
    with pytest.raises(AttributeError):
        manifest.terminal_criteria.append("mutate")


@pytest.mark.parametrize("field", ["leases", "initial_attempts"])
def test_launch_manifest_rejects_missing_role_record(field):
    value = make_launch_manifest().to_dict()
    value[field].pop()

    assert_code(
        "MISSING_ROLE",
        workspace.LaneLaunchManifest.from_dict,
        value,
        validation_time=NOW,
    )


@pytest.mark.parametrize("field", ["leases", "initial_attempts"])
def test_launch_manifest_rejects_duplicate_role_record(field):
    value = make_launch_manifest().to_dict()
    value[field][-1] = copy.deepcopy(value[field][0])

    assert_code(
        "DUPLICATE_ROLE",
        workspace.LaneLaunchManifest.from_dict,
        value,
        validation_time=NOW,
    )


def test_launch_manifest_rejects_input_outside_all_role_read_scopes():
    value = make_launch_manifest().to_dict()
    value["input_hashes"] = {
        "factory-automation/factory/runs/ptc/private/undeclared.json": SHA_A
    }

    assert_code(
        "INPUT_SCOPE",
        workspace.LaneLaunchManifest.from_dict,
        value,
        validation_time=NOW,
    )


def test_launch_manifest_rejects_malformed_input_hash():
    value = make_launch_manifest().to_dict()
    input_path = next(iter(value["input_hashes"]))
    value["input_hashes"][input_path] = "not-a-sha"

    assert_code(
        "SHA256",
        workspace.LaneLaunchManifest.from_dict,
        value,
        validation_time=NOW,
    )


def test_launch_manifest_rejects_attempt_lease_role_mismatch():
    value = make_launch_manifest().to_dict()
    value["initial_attempts"][0]["lease"] = copy.deepcopy(value["leases"][1])

    assert_code(
        "ROLE_MISMATCH",
        workspace.LaneLaunchManifest.from_dict,
        value,
        validation_time=NOW,
    )


def test_launch_manifest_rejects_distinct_lease_for_same_role():
    manifest = make_launch_manifest()
    value = manifest.to_dict()
    first_role = manifest.workspace.roles[0]
    replacement = workspace.Lease.issue(
        first_role.logical_role_id,
        now=NOW + timedelta(minutes=1),
        sequence=2,
    )
    lease_index = next(
        index
        for index, lease in enumerate(value["leases"])
        if lease["holder_role_id"] == first_role.logical_role_id
    )
    value["leases"][lease_index] = replacement.to_dict()

    assert_code(
        "ROLE_MISMATCH",
        workspace.LaneLaunchManifest.from_dict,
        value,
        validation_time=NOW + timedelta(minutes=1),
    )


def test_launch_manifest_revalidates_all_leases_at_provided_time():
    manifest = make_launch_manifest()

    assert_code(
        "STALE_LEASE",
        workspace.LaneLaunchManifest.from_dict,
        manifest.to_dict(),
        validation_time=NOW + timedelta(minutes=30),
    )
    assert_code(
        "STALE_LEASE",
        manifest.validate_at,
        NOW + timedelta(minutes=30),
    )


def test_launch_manifest_requires_null_initial_predecessor_links():
    lane_workspace = make_workspace()
    leases = [
        workspace.Lease.issue(role.logical_role_id, now=NOW)
        for role in lane_workspace.roles
    ]
    attempts = [
        make_attempt(
            role,
            lease=lease,
            attempt_key=f"launch-{role.role_name.lower()}",
            runtime_session_id=f"cloud-{index}",
        )
        for index, (role, lease) in enumerate(zip(lane_workspace.roles, leases))
    ]
    role = lane_workspace.roles[0]
    successor = make_continuation(
        role,
        continuity_mode="SUCCESSOR_RECONSTRUCTION",
        predecessor_attempt_id=PREDECESSOR_ID,
        predecessor_runtime_session_id="cloud-old",
        predecessor_report_sha256=SHA_A,
    )
    attempts[0] = make_attempt(
        role,
        lease=leases[0],
        continuation=successor,
        attempt_key="launch-lane-coord",
        runtime_session_id="cloud-new",
    )

    assert_code(
        "INITIAL_CONTINUITY",
        workspace.LaneLaunchManifest.create,
        lane_workspace,
        input_hashes={
            "factory-automation/factory/runs/ptc/cloud/inputs/frozen.json": SHA_A
        },
        leases=leases,
        initial_attempts=attempts,
        terminal_criteria=["All work is dispositioned."],
        validation_time=NOW,
    )


def test_launch_manifest_requires_initial_costs_to_remain_null():
    lane_workspace = make_workspace()
    leases = [
        workspace.Lease.issue(role.logical_role_id, now=NOW)
        for role in lane_workspace.roles
    ]
    attempts = [
        make_attempt(
            role,
            lease=lease,
            attempt_key=f"launch-{role.role_name.lower()}",
            runtime_session_id=f"cloud-{index}",
        )
        for index, (role, lease) in enumerate(zip(lane_workspace.roles, leases))
    ]
    attempts[0] = make_attempt(
        lane_workspace.roles[0],
        lease=leases[0],
        attempt_key="launch-lane-coord",
        runtime_session_id="cloud-0",
        cost_usd=0,
        cost_source="no-paid-call",
        cost_evidence_sha256=SHA_A,
    )

    assert_code(
        "INITIAL_COST",
        workspace.LaneLaunchManifest.create,
        lane_workspace,
        input_hashes={
            "factory-automation/factory/runs/ptc/cloud/inputs/frozen.json": SHA_A
        },
        leases=leases,
        initial_attempts=attempts,
        terminal_criteria=["All work is dispositioned."],
        validation_time=NOW,
    )


@pytest.mark.parametrize("criteria", [[], [""]])
def test_launch_manifest_requires_nonempty_terminal_criteria(criteria):
    manifest = make_launch_manifest()
    value = manifest.to_dict()
    value["terminal_criteria"] = criteria

    expected = "TERMINAL_CRITERIA" if criteria == [] else "TYPE"
    assert_code(
        expected,
        workspace.LaneLaunchManifest.from_dict,
        value,
        validation_time=NOW,
    )


def test_launch_manifest_rejects_unknown_fields_and_tampered_identity():
    value = make_launch_manifest().to_dict()
    value["auto_launch"] = True
    assert_code(
        "UNKNOWN_FIELD",
        workspace.LaneLaunchManifest.from_dict,
        value,
        validation_time=NOW,
    )

    value = make_launch_manifest().to_dict()
    value["launch_manifest_id"] = "99999999-9999-4999-8999-999999999999"
    assert_code(
        "DETERMINISTIC_ID",
        workspace.LaneLaunchManifest.from_dict,
        value,
        validation_time=NOW,
    )


def test_launch_manifest_normalizes_record_and_input_order_for_same_hash():
    manifest = make_launch_manifest()
    value = manifest.to_dict()
    value["leases"].reverse()
    value["initial_attempts"].reverse()
    value["input_hashes"] = dict(reversed(list(value["input_hashes"].items())))

    parsed = workspace.LaneLaunchManifest.from_dict(value, validation_time=NOW)
    assert parsed == manifest
    assert parsed.manifest_sha256 == manifest.manifest_sha256
