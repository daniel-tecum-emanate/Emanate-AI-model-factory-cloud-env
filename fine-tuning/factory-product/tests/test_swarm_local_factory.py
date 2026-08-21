import hashlib
import json
import sys
import uuid
from pathlib import Path

import pytest

FACTORY_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FACTORY_DIR / "swarm"))

import local_factory as lf  # noqa: E402


def initialized(tmp_path):
    root = tmp_path / "swarm-program"
    manifest = lf.create_program(root, "ptc-grand-steel-local")
    return root, manifest


def record_attempt(
    root,
    manifest,
    lane,
    role,
    key,
    *,
    cost=None,
    accepted_unknown=False,
    predecessor=None,
):
    data = lf.make_attempt(
        manifest,
        lane,
        role,
        key,
        state="COMPLETED",
        cost_usd=cost,
        cost_source="no-paid-call" if cost == 0 else "cursor-unavailable",
        accepted_unknown=accepted_unknown,
        predecessor_attempt_id=predecessor,
        task_id=f"{role}-task",
        wave="W1",
    )
    return lf.append_event(root, lane, "attempt", data, f"attempt:{role}:{key}")


def record_all_budget_categories(root, manifest, lane):
    for category in lf.COST_CATEGORIES:
        budget_id = str(uuid.uuid5(uuid.UUID(manifest["program_id"]), f"{lane}:{category}"))
        lf.append_event(
            root,
            lane,
            "budget",
            {
                "budget_id": budget_id,
                "cost_category": category,
                "kind": "actual",
                "cost": {
                    "value_usd": 0 if category in ("training", "evaluation", "shadow") else None,
                    "source": "provider-receipt-no-job" if category in ("training", "evaluation", "shadow") else "not-reconciled",
                    "accepted_unknown": category not in ("training", "evaluation", "shadow"),
                },
            },
            f"budget:{category}",
        )


def terminal_flow(root, manifest, lane, state):
    flow_id = str(uuid.uuid5(uuid.UUID(manifest["program_id"]), f"{lane}:terminal"))
    return lf.append_event(
        root,
        lane,
        "flow",
        {
            "flow_id": flow_id,
            "flow_type": "terminal_decision",
            "from_ref": f"{lane}:decision-request",
            "to_ref": f"{lane}:signed-decision",
            "artifact": f"decisions/{lane}.json",
            "artifact_sha256": hashlib.sha256(lane.encode()).hexdigest(),
            "terminal_state": state,
            "signed_by": "operator-id",
        },
        "terminal-decision",
    )


def test_init_and_append_are_idempotent_and_reject_raw_customer_text(tmp_path):
    root, manifest = initialized(tmp_path)
    again = lf.create_program(root, "ptc-grand-steel-local")
    assert again == manifest
    assert set(manifest["lanes"]) == {"ptc", "grand-steel"}
    for lane, row in manifest["lanes"].items():
        assert (root / "lanes" / lane / row["model_run_id"]).is_dir()

    first = record_attempt(root, manifest, "ptc", "KEEPER", "first", cost=0)
    second = record_attempt(root, manifest, "ptc", "KEEPER", "first", cost=0)
    assert second == first
    assert len(lf.read_events(root, "ptc")) == 1

    bad = dict(first["data"])
    bad["raw_customer_text"] = "must never land"
    with pytest.raises(lf.LedgerError, match="raw_customer_text"):
        lf.append_event(root, "ptc", "attempt", bad, "bad")


def test_lane_isolation_and_retries_are_new_attempts(tmp_path):
    root, manifest = initialized(tmp_path)
    original = record_attempt(root, manifest, "ptc", "KEEPER", "try-1", cost=0)
    retry = record_attempt(
        root,
        manifest,
        "ptc",
        "KEEPER",
        "try-2",
        cost=0,
        predecessor=original["data"]["execution_attempt_id"],
    )
    gs = record_attempt(root, manifest, "grand-steel", "KEEPER", "try-1", cost=0)

    assert retry["data"]["execution_attempt_id"] != original["data"]["execution_attempt_id"]
    assert gs["model_run_id"] != original["model_run_id"]
    assert len(lf.read_events(root, "ptc")) == 2
    assert len(lf.read_events(root, "grand-steel")) == 1
    packet = lf.build_lane_packet(root, "ptc")
    assert packet["counts"]["execution_attempts"] == 2
    assert packet["counts"]["logical_roles"] == 1
    assert packet["counts"]["retry_attempts"] == 1


def test_null_and_zero_cost_have_distinct_honest_coverage(tmp_path):
    root, manifest = initialized(tmp_path)
    record_attempt(root, manifest, "ptc", "KEEPER", "known-free", cost=0)
    record_attempt(root, manifest, "ptc", "DATA-ARCH", "unknown", cost=None, accepted_unknown=True)

    cost = lf.build_lane_packet(root, "ptc")["cost"]
    assert cost["known_attempt_cost_usd"] == 0
    assert cost["known_cost_attempts"] == 1
    assert cost["unknown_cost_attempts"] == 1
    assert cost["attempt_cost_coverage"] == 0.5
    assert cost["dollar_cost_coverage"] is None


def test_append_only_attempt_cost_correction_restores_coverage(tmp_path):
    root, manifest = initialized(tmp_path)
    attempt = record_attempt(
        root, manifest, "ptc", "KEEPER", "ambiguous", cost=None
    )
    correction_id = str(
        uuid.uuid5(uuid.UUID(manifest["program_id"]), "ptc:attempt-cost:ambiguous")
    )
    lf.append_event(
        root,
        "ptc",
        "attempt_cost",
        {
            "correction_id": correction_id,
            "execution_attempt_id": attempt["data"]["execution_attempt_id"],
            "runtime_session_id": "provider-session-1",
            "evidence_sha256": hashlib.sha256(b"unique receipt").hexdigest(),
            "cost": {
                "value_usd": 1.25,
                "source": "tier2-ledger-manual-disambiguation",
                "accepted_unknown": False,
            },
        },
        "attempt-cost:ambiguous",
    )
    packet = lf.build_lane_packet(root, "ptc")
    assert packet["cost"]["known_attempt_cost_usd"] == 1.25
    assert packet["cost"]["attempt_cost_coverage"] == 1
    assert packet["terminal_blockers"]["unaccepted_unknown_cost_attempt_ids"] == []
    agent = lf.project_existing_table_rows(root, "ptc")["factory_agents"][0]
    assert agent["cost_usd"] == 1.25
    assert agent["ledger_session_id"] == "provider-session-1"


def test_categories_stay_separate_and_contributions_trace_to_attempts(tmp_path):
    root, manifest = initialized(tmp_path)
    attempt = record_attempt(root, manifest, "ptc", "EVAL-DEV", "first", cost=0)
    values = {"agents": None, "corpus": 4.0, "training": 0, "evaluation": 2.5, "shadow": 0, "infrastructure": None}
    for category, value in values.items():
        budget_id = str(uuid.uuid5(uuid.UUID(manifest["program_id"]), f"ptc:{category}"))
        lf.append_event(
            root,
            "ptc",
            "budget",
            {
                "budget_id": budget_id,
                "cost_category": category,
                "kind": "actual",
                "cost": {"value_usd": value, "source": "test-receipt", "accepted_unknown": value is None},
            },
            f"budget:{category}",
        )

    contribution_id = str(uuid.uuid5(uuid.UUID(manifest["program_id"]), "contribution:eval"))
    lf.append_event(
        root,
        "ptc",
        "contribution",
        {
            "contribution_id": contribution_id,
            "execution_attempt_id": attempt["data"]["execution_attempt_id"],
            "category": "probe",
            "epistemic_status": "supported",
            "gate_effect": "informational",
            "report_sha256": hashlib.sha256(b"bounded report").hexdigest(),
            "evidence_chain": ["claim:PTC-E21-01", "sha256:" + hashlib.sha256(b"source").hexdigest()],
            "disposition": "accepted",
        },
        "contribution:eval",
    )
    packet = lf.build_lane_packet(root, "ptc")
    assert packet["cost"]["costs_by_category"]["corpus"]["known_usd"] == 4.0
    assert packet["cost"]["costs_by_category"]["evaluation"]["known_usd"] == 2.5
    assert packet["cost"]["costs_by_category"]["training"]["known_usd"] == 0
    assert packet["counts"]["accepted_contributions"] == 1
    assert packet["contributions"][0]["execution_attempt_id"] == attempt["data"]["execution_attempt_id"]
    assert packet["terminal_blockers"]["orphan_contribution_event_ids"] == []


def test_optional_projections_are_compatible_and_explicitly_not_pushed(tmp_path):
    root, manifest = initialized(tmp_path)
    record_attempt(root, manifest, "ptc", "KEEPER", "first", cost=None, accepted_unknown=True)
    projections = lf.project_existing_table_rows(root, "ptc")
    assert projections["delivery_status"] == "generated_not_pushed"
    assert projections["factory_agents"][0]["cost_usd"] is None
    dispatched = projections["factory_run_events"][0]
    assert dispatched["kind"] == "agent_dispatched"
    assert "cost_usd" not in dispatched["detail"]
    assert set(dispatched) >= {"id", "run_id", "ts", "kind", "headline", "ref", "detail"}


def test_push_projects_idempotently_onto_existing_run_only(tmp_path, monkeypatch):
    root, manifest = initialized(tmp_path)
    record_attempt(root, manifest, "ptc", "KEEPER", "first", cost=0)
    target_run_id = str(uuid.uuid4())
    pushed = []

    monkeypatch.setattr(
        lf.supabase_rest,
        "select",
        lambda table, params, **kwargs: [{"id": target_run_id}],
    )
    monkeypatch.setattr(
        lf.supabase_rest,
        "upsert",
        lambda table, rows, on_conflict, **kwargs: pushed.append(
            (table, rows, on_conflict)
        ),
    )
    result = lf.push_existing_table_rows(root, "ptc", target_run_id)
    assert result["delivery_status"] == "pushed_to_existing_local_run"
    assert result["target_run_id"] == target_run_id
    assert result["counts"]["factory_agents"] == 1
    assert result["counts"]["factory_run_events"] == 1
    assert all(on_conflict == "id" for _, _, on_conflict in pushed)
    assert all(
        row["run_id"] == target_run_id
        for _, rows, _ in pushed
        for row in rows
    )

    monkeypatch.setattr(lf.supabase_rest, "select", lambda *args, **kwargs: [])
    with pytest.raises(lf.LedgerError, match="does not exist"):
        lf.push_existing_table_rows(root, "ptc", target_run_id)


def test_complete_terminal_export_requires_both_lanes_and_writes_all_packets(tmp_path):
    root, manifest = initialized(tmp_path)
    with pytest.raises(lf.LedgerError, match="ptc, grand-steel"):
        lf.export_packets(root, require_terminal=True)

    for lane, state in (("ptc", "FINALIZED_NO_TRAIN"), ("grand-steel", "FINALIZED_NO_SHIP")):
        record_attempt(root, manifest, lane, "LANE-COORD", "final", cost=None, accepted_unknown=True)
        record_all_budget_categories(root, manifest, lane)
        terminal_flow(root, manifest, lane, state)

    result = lf.export_packets(root, emit_projections=True, require_terminal=True)
    assert result["program"]["program_terminal"] is True
    assert all(packet["terminal_export_complete"] for packet in result["lanes"].values())
    assert (root / "exports" / "program-decision-packet.json").is_file()
    assert (root / "exports" / "program-decision-packet.md").is_file()
    assert (root / "exports" / "existing-table-projections.not-pushed.json").is_file()
    for lane, row in manifest["lanes"].items():
        packet_path = root / "lanes" / lane / row["model_run_id"] / "exports" / "decision-packet.json"
        payload = json.loads(packet_path.read_text())
        assert payload["terminal_export_complete"] is True
        assert payload["projection_status"]["database_push"] == "available_existing_local_run_only"
        assert packet_path.with_suffix(".md").is_file()


def test_cloud_lease_and_delegation_categories_are_strict_idempotent_and_projected(
    tmp_path,
):
    root, manifest = initialized(tmp_path)
    coordinator = f"{manifest['program_id']}/ptc/LANE-COORD"
    target = f"{manifest['program_id']}/ptc/CORPUS-VERIFIER"
    lease_id = str(uuid.uuid4())
    lease_data = {
        "lease_id": lease_id,
        "logical_role_id": coordinator,
        "sequence": 1,
        "issued_at": "2026-08-04T20:00:00Z",
        "expires_at": "2026-08-04T20:30:00Z",
        "heartbeat_sha256": hashlib.sha256(b"lease row").hexdigest(),
    }
    request_id = str(uuid.uuid4())
    delegation_data = {
        "request_id": request_id,
        "requester_role_id": coordinator,
        "target_role_id": target,
        "task_id": "verify-corpus",
        "status": "routed",
        "routed_by_role_id": coordinator,
        "artifact": "integrated/ptc/lane-coord/DELEGATIONS.json",
        "artifact_sha256": hashlib.sha256(b"request row").hexdigest(),
    }

    first_lease = lf.append_event(
        root, "ptc", "cloud_lease", lease_data, f"lease:{lease_id}"
    )
    assert lf.append_event(
        root, "ptc", "cloud_lease", lease_data, f"lease:{lease_id}"
    ) == first_lease
    lf.append_event(
        root,
        "ptc",
        "delegation",
        delegation_data,
        f"delegation:{request_id}",
    )

    subtypes = {
        row["ref"]["event_subtype"]
        for row in lf.project_existing_table_rows(root, "ptc")["factory_run_events"]
    }
    assert {"swarm_cloud_lease", "swarm_delegation"} <= subtypes
    invalid = dict(delegation_data)
    invalid["routed_by_role_id"] = target
    with pytest.raises(lf.LedgerError, match="coordinator"):
        lf.append_event(
            root, "ptc", "delegation", invalid, "invalid-delegation"
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update(sequence=0),
        lambda data: data.update(expires_at="2026-08-04T20:29:59Z"),
        lambda data: data.update(extra=True),
    ],
)
def test_cloud_lease_schema_mutations_are_rejected(tmp_path, mutation):
    root, manifest = initialized(tmp_path)
    data = {
        "lease_id": str(uuid.uuid4()),
        "logical_role_id": f"{manifest['program_id']}/ptc/LANE-COORD",
        "sequence": 1,
        "issued_at": "2026-08-04T20:00:00Z",
        "expires_at": "2026-08-04T20:30:00Z",
        "heartbeat_sha256": hashlib.sha256(b"lease row").hexdigest(),
    }
    mutation(data)
    with pytest.raises(lf.LedgerError):
        lf.append_event(root, "ptc", "cloud_lease", data, "invalid-lease")
