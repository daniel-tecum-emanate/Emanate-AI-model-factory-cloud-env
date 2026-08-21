import copy
import hashlib
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

FACTORY_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FACTORY_DIR / "swarm"))

import cloud_ingest  # noqa: E402
import local_factory  # noqa: E402
import workspace  # noqa: E402


NOW = datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc)
VALIDATION_TIME = NOW + timedelta(minutes=10)
SHA_A = "a" * 64


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _setup(tmp_path, *, with_routed_delegation=True):
    ledger_root = tmp_path / "ledger"
    local_program = local_factory.create_program(
        ledger_root, "cloud-ingest-test", created_at="2026-08-04T20:00:00Z"
    )
    program_id = local_program["program_id"]
    repo = tmp_path / "repo"
    roles = [
        workspace.PersistentRole.create(
            program_id,
            "ptc",
            role_name,
            read_paths=["inputs/ptc"],
            owned_output_paths=[f"integrated/ptc/{role_name.lower()}"],
            budget_usd=None,
        )
        for role_name in workspace.LANE_ROLE_NAMES["ptc"]
    ]
    lane_workspace = workspace.LaneWorkspace.create(
        program_id, "ptc", roles, created_at=NOW
    )
    leases = [
        workspace.Lease.issue(role.logical_role_id, now=NOW) for role in roles
    ]
    attempts = []
    for index, (role, lease) in enumerate(zip(roles, leases)):
        continuation = workspace.ContinuationEnvelope.create(
            role.logical_role_id,
            continuity_mode="AUTHOR_RESUME",
            predecessor_attempt_id=None,
            predecessor_runtime_session_id=None,
            predecessor_report_sha256=None,
            state_capsule="Open claims: none\nBlockers: none",
            next_prompt="# NEXT-PROMPT\nStart the hash-bound cloud role.",
        )
        attempts.append(
            workspace.CloudAttempt.create(
                role,
                lease,
                continuation,
                attempt_key=f"launch-{role.role_name.lower()}",
                runtime_session_id=f"cloud-session-{index}",
            )
        )
    launch = workspace.LaneLaunchManifest.create(
        lane_workspace,
        input_hashes={"inputs/ptc/frozen.json": SHA_A},
        leases=leases,
        initial_attempts=attempts,
        terminal_criteria=["All six cloud role reports are dispositioned."],
        validation_time=NOW,
    )
    directories = {}
    for role, lease, attempt in zip(roles, leases, attempts):
        relative_dir = role.owned_output_paths[0]
        directories[role.logical_role_id] = relative_dir
        directory = repo / relative_dir
        directory.mkdir(parents=True)
        (directory / "STATE-CAPSULE.md").write_text(
            "Open claims: none\nBlockers: none\nChanged assumptions: none\n"
        )
        (directory / "NEXT-PROMPT.md").write_text(
            f"# NEXT-PROMPT\nContinue {role.logical_role_id} from accepted artifacts.\n"
        )
        delegation_rows = []
        if with_routed_delegation and role.role_name == "LANE-COORD":
            target = roles[1]
            request = workspace.DelegationRequest.create(
                program_id,
                "ptc",
                role.logical_role_id,
                target.logical_role_id,
                "verify-rescope",
                read_paths=["inputs/ptc/frozen.json"],
                owned_output_paths=[target.owned_output_paths[0] + "/delegated.json"],
                created_at=NOW + timedelta(minutes=2),
            )
            request = lane_workspace.route_delegation(
                lane_workspace.coordinator.logical_role_id, request
            )
            delegation_rows.append(request.to_dict())
        (directory / "DELEGATIONS.json").write_text(
            json.dumps({"schema_version": 1, "delegations": delegation_rows})
        )
        (directory / "HEARTBEAT.jsonl").write_text(
            json.dumps(lease.to_dict(), sort_keys=True) + "\n"
        )
        paths = {
            name: f"{relative_dir}/{name}"
            for name in cloud_ingest.REQUIRED_ARTIFACTS
        }
        report = {
            "schema_version": 1,
            "logical_role_id": role.logical_role_id,
            "execution_attempt_id": attempt.attempt_id,
            "runtime_session_id": attempt.runtime_session_id,
            "state": "COMPLETED",
            "wave": "W1",
            "task_id": f"checkpoint-{role.role_name.lower()}",
            "completed_at": "2026-08-04T20:08:00Z",
            "output_paths": list(paths.values()),
            "artifact_hashes": {
                name: _sha(directory / name)
                for name in cloud_ingest.HASHED_ARTIFACTS
            },
            "cost": {
                "value_usd": None,
                "source": "cloud-runtime-unreported",
                "evidence_sha256": None,
            },
            "contribution": {
                "category": "probe",
                "epistemic_status": "supported",
                "gate_effect": "informational",
                "disposition": "accepted",
                "evidence_chain": ["artifact:" + _sha(directory / "STATE-CAPSULE.md")],
            },
        }
        (directory / "REPORT.json").write_text(
            json.dumps(report, sort_keys=True)
        )
    return ledger_root, repo, launch, directories


def _role_dir(repo, launch, directories, role_name):
    role = next(
        role for role in launch.workspace.roles if role.role_name == role_name
    )
    return repo / directories[role.logical_role_id]


def _refresh_report_hashes(directory):
    report_path = directory / "REPORT.json"
    report = json.loads(report_path.read_text())
    report["artifact_hashes"] = {
        name: _sha(directory / name)
        for name in cloud_ingest.HASHED_ARTIFACTS
    }
    report_path.write_text(json.dumps(report, sort_keys=True))


def _add_role_artifact(repo, directory, relative_path, content):
    artifact = repo / relative_path
    artifact.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        artifact.write_bytes(content)
    else:
        artifact.write_text(content)
    report_path = directory / "REPORT.json"
    report = json.loads(report_path.read_text())
    report["output_paths"].append(relative_path)
    report["artifact_hashes"][relative_path] = _sha(artifact)
    report_path.write_text(json.dumps(report, sort_keys=True))
    return artifact


def _assert_code(code, callable_, *args, **kwargs):
    with pytest.raises(cloud_ingest.CloudIngestError) as caught:
        callable_(*args, **kwargs)
    assert caught.value.code == code


def _ingest(ledger_root, repo, launch, directories, *, at=VALIDATION_TIME):
    return cloud_ingest.ingest_cloud_checkpoints(
        ledger_root,
        repo,
        launch,
        directories,
        validation_time=at,
    )


def test_ingests_all_roles_locally_and_is_idempotent(tmp_path):
    ledger_root, repo, launch, directories = _setup(tmp_path)

    first = _ingest(ledger_root, repo, launch, directories)
    first_events = local_factory.read_events(ledger_root, "ptc")
    second = _ingest(ledger_root, repo, launch, directories)
    second_events = local_factory.read_events(ledger_root, "ptc")

    assert first == second
    assert first["delivery_status"] == "ingested_local_only_not_pushed"
    assert first["roles_ingested"] == 6
    assert first["events"] == {
        "attempt": 6,
        "cloud_lease": 6,
        "contribution": 6,
        "delegation": 1,
        "flow": 7,
    }
    assert len(first_events) == 26
    assert second_events == first_events


def test_unknown_cloud_cost_stays_null_and_accepted_unknown(tmp_path):
    ledger_root, repo, launch, directories = _setup(tmp_path)
    _ingest(ledger_root, repo, launch, directories)

    attempts = [
        event
        for event in local_factory.read_events(ledger_root, "ptc")
        if event["category"] == "attempt"
    ]
    assert all(event["data"]["cost"] == {
        "value_usd": None,
        "source": "cloud-runtime-unreported",
        "accepted_unknown": True,
    } for event in attempts)
    assert local_factory.build_lane_packet(
        ledger_root, "ptc"
    )["terminal_blockers"]["unaccepted_unknown_cost_attempt_ids"] == []


def test_zero_cost_requires_evidence_then_maps_attempt_cost(tmp_path):
    ledger_root, repo, launch, directories = _setup(tmp_path)
    directory = _role_dir(repo, launch, directories, "LANE-COORD")
    report_path = directory / "REPORT.json"
    report = json.loads(report_path.read_text())
    report["cost"] = {
        "value_usd": 0,
        "source": "cloud-provider-no-paid-call",
        "evidence_sha256": None,
    }
    report_path.write_text(json.dumps(report))
    _assert_code(
        "UNPROVEN_ZERO_COST", _ingest, ledger_root, repo, launch, directories
    )
    assert local_factory.read_events(ledger_root, "ptc") == []

    report["cost"]["evidence_sha256"] = hashlib.sha256(
        b"provider receipt proving no paid call"
    ).hexdigest()
    report_path.write_text(json.dumps(report))
    result = _ingest(ledger_root, repo, launch, directories)
    assert result["events"]["attempt_cost"] == 1
    cost_event = next(
        event
        for event in local_factory.read_events(ledger_root, "ptc")
        if event["category"] == "attempt_cost"
    )
    assert cost_event["data"]["cost"]["value_usd"] == 0


def test_projects_cloud_leases_delegations_and_handoffs_without_push(tmp_path):
    ledger_root, repo, launch, directories = _setup(tmp_path)
    _ingest(ledger_root, repo, launch, directories)

    projected = local_factory.project_existing_table_rows(ledger_root, "ptc")
    steering_subtypes = {
        row["ref"].get("event_subtype")
        for row in projected["factory_run_events"]
        if row["kind"] == "steering_event"
    }
    assert {"swarm_cloud_lease", "swarm_delegation"} <= steering_subtypes
    assert len(projected["factory_handoffs"]) == 7
    assert projected["delivery_status"] == "generated_not_pushed"


def test_ingestion_never_calls_database_or_network_projection(tmp_path, monkeypatch):
    ledger_root, repo, launch, directories = _setup(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("network/database path must not be called")

    monkeypatch.setattr(local_factory.supabase_rest, "select", forbidden)
    monkeypatch.setattr(local_factory.supabase_rest, "upsert", forbidden)
    assert _ingest(ledger_root, repo, launch, directories)["roles_ingested"] == 6


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda report: report.update(unreviewed=True), "UNKNOWN_FIELD"),
        (
            lambda report: report.update(
                execution_attempt_id=str(uuid.uuid4())
            ),
            "ATTEMPT_MISMATCH",
        ),
        (lambda report: report.update(state="RUNNING"), "ATTEMPT_STATE"),
    ],
)
def test_strict_report_schema_and_identity_mutations_fail_before_append(
    tmp_path, mutation, code
):
    ledger_root, repo, launch, directories = _setup(tmp_path)
    directory = _role_dir(repo, launch, directories, "CORPUS-VERIFIER")
    report_path = directory / "REPORT.json"
    report = json.loads(report_path.read_text())
    mutation(report)
    report_path.write_text(json.dumps(report))

    _assert_code(code, _ingest, ledger_root, repo, launch, directories)
    assert local_factory.read_events(ledger_root, "ptc") == []


def test_artifact_hash_mutation_fails_before_append(tmp_path):
    ledger_root, repo, launch, directories = _setup(tmp_path)
    directory = _role_dir(repo, launch, directories, "EVAL-ARCH")
    (directory / "NEXT-PROMPT.md").write_text("# NEXT-PROMPT\nmutated\n")

    _assert_code(
        "STALE_HASH", _ingest, ledger_root, repo, launch, directories
    )
    assert local_factory.read_events(ledger_root, "ptc") == []


def test_ingests_hashed_role_specific_code_and_data_artifacts(tmp_path):
    ledger_root, repo, launch, directories = _setup(tmp_path)
    role = next(
        role
        for role in launch.workspace.roles
        if role.role_name == "RESCOPE-DATA-BUILDER"
    )
    directory = repo / directories[role.logical_role_id]
    code_path = role.owned_output_paths[0] + "/src/rebuild_rescope.py"
    data_path = role.owned_output_paths[0] + "/data/rescope-summary.json"
    _add_role_artifact(
        repo,
        directory,
        code_path,
        "def rebuild():\n    return 'hash-bound'\n",
    )
    _add_role_artifact(
        repo,
        directory,
        data_path,
        b'{"records":2569,"status":"prepared"}\n',
    )

    first = _ingest(ledger_root, repo, launch, directories)
    events = local_factory.read_events(ledger_root, "ptc")
    second = _ingest(ledger_root, repo, launch, directories)

    assert first == second
    assert len(events) == 26
    report = json.loads((directory / "REPORT.json").read_text())
    assert {code_path, data_path} <= set(report["output_paths"])
    assert report["artifact_hashes"][code_path] == _sha(repo / code_path)
    assert report["artifact_hashes"][data_path] == _sha(repo / data_path)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("delete", "ARTIFACT_MISSING"),
        ("mutate", "STALE_HASH"),
        ("unhash", "ARTIFACT_SET"),
    ],
)
def test_additional_artifact_mutations_fail_before_append(
    tmp_path, mutation, code
):
    ledger_root, repo, launch, directories = _setup(tmp_path)
    role = next(
        role for role in launch.workspace.roles if role.role_name == "CORPUS-VERIFIER"
    )
    directory = repo / directories[role.logical_role_id]
    relative_path = role.owned_output_paths[0] + "/checks/verify_corpus.py"
    _add_role_artifact(
        repo, directory, relative_path, "print('verified')\n"
    )

    if mutation == "unhash":
        report_path = directory / "REPORT.json"
        report = json.loads(report_path.read_text())
        report["artifact_hashes"].pop(relative_path)
        report_path.write_text(json.dumps(report))
    elif mutation == "delete":
        (repo / relative_path).unlink()
    else:
        (repo / relative_path).write_text("mutated after report")

    _assert_code(code, _ingest, ledger_root, repo, launch, directories)
    assert local_factory.read_events(ledger_root, "ptc") == []


def test_additional_artifact_must_be_listed_hashed_and_role_owned(tmp_path):
    ledger_root, repo, launch, directories = _setup(tmp_path)
    role = next(
        role for role in launch.workspace.roles if role.role_name == "EVAL-ARCH"
    )
    directory = repo / directories[role.logical_role_id]
    owned_path = role.owned_output_paths[0] + "/eval/scorecard.json"
    artifact = repo / owned_path
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"score":null}\n')
    report_path = directory / "REPORT.json"
    report = json.loads(report_path.read_text())
    report["artifact_hashes"][owned_path] = _sha(artifact)
    report_path.write_text(json.dumps(report))
    _assert_code(
        "ARTIFACT_SET", _ingest, ledger_root, repo, launch, directories
    )

    ledger_root, repo, launch, directories = _setup(tmp_path / "outside")
    role = next(
        role for role in launch.workspace.roles if role.role_name == "EVAL-ARCH"
    )
    directory = repo / directories[role.logical_role_id]
    outside_path = "integrated/ptc/redteam/stolen-scorecard.json"
    outside = repo / outside_path
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text('{"score":"stolen"}\n')
    report_path = directory / "REPORT.json"
    report = json.loads(report_path.read_text())
    report["output_paths"].append(outside_path)
    report["artifact_hashes"][outside_path] = _sha(outside)
    report_path.write_text(json.dumps(report))
    _assert_code(
        "WRITE_SCOPE", _ingest, ledger_root, repo, launch, directories
    )


def test_unlisted_regular_file_in_role_directory_is_rejected(tmp_path):
    ledger_root, repo, launch, directories = _setup(tmp_path)
    directory = _role_dir(
        repo, launch, directories, "PROVIDER-COST-VERIFIER"
    )
    unlisted = directory / "receipts" / "provider-response.json"
    unlisted.parent.mkdir()
    unlisted.write_text('{"cost_usd":null}\n')

    _assert_code(
        "UNLISTED_ARTIFACT",
        _ingest,
        ledger_root,
        repo,
        launch,
        directories,
    )
    assert local_factory.read_events(ledger_root, "ptc") == []


def test_symlink_in_role_directory_is_rejected(tmp_path):
    ledger_root, repo, launch, directories = _setup(tmp_path)
    directory = _role_dir(repo, launch, directories, "REDTEAM")
    (directory / "REPORT-LINK.json").symlink_to("REPORT.json")

    _assert_code(
        "SYMLINK_ARTIFACT",
        _ingest,
        ledger_root,
        repo,
        launch,
        directories,
    )
    assert local_factory.read_events(ledger_root, "ptc") == []


@pytest.mark.parametrize(
    "relative_suffix",
    [
        "__pycache__/probe.cpython-313.pyc",
        ".pytest_cache/v/cache/nodeids",
        "compiled/probe.pyc",
    ],
)
def test_cache_and_pyc_artifacts_are_rejected_even_when_listed(
    tmp_path, relative_suffix
):
    ledger_root, repo, launch, directories = _setup(tmp_path)
    role = next(
        role for role in launch.workspace.roles if role.role_name == "EVAL-ARCH"
    )
    directory = repo / directories[role.logical_role_id]
    relative_path = role.owned_output_paths[0] + "/" + relative_suffix
    _add_role_artifact(
        repo,
        directory,
        relative_path,
        b"generated cache bytes",
    )

    _assert_code(
        "CACHE_ARTIFACT",
        _ingest,
        ledger_root,
        repo,
        launch,
        directories,
    )
    assert local_factory.read_events(ledger_root, "ptc") == []


def test_capsule_bounds_are_revalidated(tmp_path):
    ledger_root, repo, launch, directories = _setup(tmp_path)
    directory = _role_dir(repo, launch, directories, "REDTEAM")
    (directory / "STATE-CAPSULE.md").write_text(
        "\n".join("line" for _ in range(151))
    )
    _refresh_report_hashes(directory)

    _assert_code(
        "CAPSULE_LINES", _ingest, ledger_root, repo, launch, directories
    )


def test_missing_artifact_and_write_scope_fail_closed(tmp_path):
    ledger_root, repo, launch, directories = _setup(tmp_path)
    directory = _role_dir(repo, launch, directories, "REDTEAM")
    (directory / "NEXT-PROMPT.md").unlink()
    _assert_code(
        "ARTIFACT_MISSING", _ingest, ledger_root, repo, launch, directories
    )

    ledger_root, repo, launch, directories = _setup(tmp_path / "second")
    role_id = launch.workspace.roles[-1].logical_role_id
    directories[role_id] = "integrated/ptc/not-owned"
    _assert_code(
        "WRITE_SCOPE", _ingest, ledger_root, repo, launch, directories
    )


def test_heartbeat_must_be_active_and_sequence_contiguous(tmp_path):
    ledger_root, repo, launch, directories = _setup(tmp_path)
    _assert_code(
        "STALE_LEASE",
        _ingest,
        ledger_root,
        repo,
        launch,
        directories,
        at=NOW + timedelta(minutes=30),
    )

    ledger_root, repo, launch, directories = _setup(tmp_path / "sequence")
    role = launch.workspace.roles[0]
    directory = repo / directories[role.logical_role_id]
    initial = next(
        lease for lease in launch.leases if lease.holder_role_id == role.logical_role_id
    )
    skipped = workspace.Lease.issue(
        role.logical_role_id,
        now=NOW + timedelta(minutes=5),
        sequence=3,
    )
    (directory / "HEARTBEAT.jsonl").write_text(
        json.dumps(initial.to_dict()) + "\n" + json.dumps(skipped.to_dict()) + "\n"
    )
    _refresh_report_hashes(directory)
    _assert_code(
        "HEARTBEAT_SEQUENCE", _ingest, ledger_root, repo, launch, directories
    )


def test_routed_delegation_requires_exact_lane_coordinator(tmp_path):
    ledger_root, repo, launch, directories = _setup(tmp_path)
    directory = _role_dir(repo, launch, directories, "LANE-COORD")
    path = directory / "DELEGATIONS.json"
    payload = json.loads(path.read_text())
    request = payload["delegations"][0]
    other_program = str(uuid.uuid4())
    request["routed_by_role_id"] = f"{other_program}/ptc/LANE-COORD"
    path.write_text(json.dumps(payload))
    _refresh_report_hashes(directory)

    _assert_code(
        "DELEGATION_AUTHORITY",
        _ingest,
        ledger_root,
        repo,
        launch,
        directories,
    )


def test_requested_delegation_remains_requested_without_handoff(tmp_path):
    ledger_root, repo, launch, directories = _setup(
        tmp_path, with_routed_delegation=False
    )
    role = launch.workspace.roles[1]
    target = launch.workspace.roles[2]
    request = workspace.DelegationRequest.create(
        launch.workspace.program_id,
        "ptc",
        role.logical_role_id,
        target.logical_role_id,
        "request-review",
        read_paths=["inputs/ptc/frozen.json"],
        owned_output_paths=[target.owned_output_paths[0] + "/review.json"],
        created_at=NOW + timedelta(minutes=2),
    )
    directory = repo / directories[role.logical_role_id]
    (directory / "DELEGATIONS.json").write_text(
        json.dumps({"schema_version": 1, "delegations": [request.to_dict()]})
    )
    _refresh_report_hashes(directory)
    _ingest(ledger_root, repo, launch, directories)

    delegation = next(
        event
        for event in local_factory.read_events(ledger_root, "ptc")
        if event["category"] == "delegation"
    )
    assert delegation["data"]["status"] == "requested"
    assert delegation["data"]["routed_by_role_id"] is None
    assert len(local_factory.project_existing_table_rows(
        ledger_root, "ptc"
    )["factory_handoffs"]) == 6


def test_existing_different_attempt_for_role_blocks_ingestion(tmp_path):
    ledger_root, repo, launch, directories = _setup(tmp_path)
    manifest = local_factory.load_program(ledger_root)
    role = launch.workspace.roles[0]
    conflicting = local_factory.make_attempt(
        manifest,
        "ptc",
        role.role_name,
        "different-attempt",
        state="FAILED",
        cost_usd=None,
        cost_source="unknown",
        accepted_unknown=True,
        runtime_session_id="other-session",
        task_id="conflict",
        wave="W1",
    )
    local_factory.append_event(
        ledger_root, "ptc", "attempt", conflicting, "conflicting-role-attempt"
    )

    _assert_code(
        "DUPLICATE_ROLE_ATTEMPT",
        _ingest,
        ledger_root,
        repo,
        launch,
        directories,
    )


def test_duplicate_or_missing_role_directory_is_rejected(tmp_path):
    ledger_root, repo, launch, directories = _setup(tmp_path)
    missing = dict(directories)
    missing.pop(next(iter(missing)))
    _assert_code(
        "ROLE_SET", _ingest, ledger_root, repo, launch, missing
    )

    duplicate = dict(directories)
    keys = list(duplicate)
    duplicate[keys[1]] = duplicate[keys[0]]
    _assert_code(
        "DUPLICATE_ROLE_DIRECTORY",
        _ingest,
        ledger_root,
        repo,
        launch,
        duplicate,
    )


def test_cli_loads_manifest_infers_directories_and_prints_json(tmp_path, capsys):
    ledger_root, repo, launch, _ = _setup(tmp_path)
    manifest_path = tmp_path / "lane-launch-manifest.json"
    manifest_path.write_text(launch.to_json())

    exit_code = cloud_ingest.main(
        [
            "--lane-manifest",
            str(manifest_path),
            "--ledger-root",
            str(ledger_root),
            "--repo-root",
            str(repo),
            "--validation-time",
            "2026-08-04T20:10:00Z",
        ]
    )
    output = capsys.readouterr()
    summary = json.loads(output.out)

    assert exit_code == 0
    assert output.err == ""
    assert summary["roles_ingested"] == 6
    assert summary["delivery_status"] == "ingested_local_only_not_pushed"
    assert cloud_ingest.load_lane_launch_manifest(manifest_path) == launch
    assert cloud_ingest.infer_role_directories(launch) == {
        role.logical_role_id: role.owned_output_paths[0]
        for role in launch.workspace.roles
    }
    assert output.out.strip() == json.dumps(
        summary, sort_keys=True, separators=(",", ":")
    )


def test_cli_optionally_writes_projection_without_network_calls(
    tmp_path, capsys, monkeypatch
):
    ledger_root, repo, launch, _ = _setup(tmp_path)
    manifest_path = tmp_path / "lane-launch-manifest.json"
    manifest_path.write_text(launch.to_json())
    projection_path = tmp_path / "exports" / "projection.json"

    def forbidden(*args, **kwargs):
        raise AssertionError("CLI projection must remain local-only")

    monkeypatch.setattr(local_factory.supabase_rest, "select", forbidden)
    monkeypatch.setattr(local_factory.supabase_rest, "upsert", forbidden)
    exit_code = cloud_ingest.main(
        [
            "--manifest",
            str(manifest_path),
            "--ledger-root",
            str(ledger_root),
            "--repo-root",
            str(repo),
            "--validation-time",
            "2026-08-04T20:10:00Z",
            "--projection-output",
            str(projection_path),
        ]
    )
    summary = json.loads(capsys.readouterr().out)
    projection_text = projection_path.read_text()
    projection = json.loads(projection_text)

    assert exit_code == 0
    assert summary["projection_output"] == str(projection_path)
    assert summary["projection_delivery_status"] == "generated_not_pushed"
    assert projection["delivery_status"] == "generated_not_pushed"
    assert len(projection["factory_agents"]) == 6
    assert projection_text == json.dumps(
        projection, sort_keys=True, separators=(",", ":")
    ) + "\n"


def test_cli_invalid_validation_time_fails_before_ledger_append(tmp_path, capsys):
    ledger_root, repo, launch, _ = _setup(tmp_path)
    manifest_path = tmp_path / "lane-launch-manifest.json"
    manifest_path.write_text(launch.to_json())

    exit_code = cloud_ingest.main(
        [
            "--manifest",
            str(manifest_path),
            "--ledger-root",
            str(ledger_root),
            "--repo-root",
            str(repo),
            "--validation-time",
            "not-a-timestamp",
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 2
    assert output.out == ""
    assert json.loads(output.err)["error"]["code"] == "TIMESTAMP"
    assert local_factory.read_events(ledger_root, "ptc") == []
