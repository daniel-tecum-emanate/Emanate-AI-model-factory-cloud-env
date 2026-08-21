"""Validate integrated cloud checkpoints and append local-only swarm events."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

try:
    from . import local_factory, protocol, workspace
except ImportError:  # pragma: no cover - supports direct imports beside runner.py
    import local_factory  # type: ignore
    import protocol  # type: ignore
    import workspace  # type: ignore


REQUIRED_ARTIFACTS = (
    "REPORT.json",
    "STATE-CAPSULE.md",
    "NEXT-PROMPT.md",
    "DELEGATIONS.json",
    "HEARTBEAT.jsonl",
)
HASHED_ARTIFACTS = REQUIRED_ARTIFACTS[1:]
REPORT_FIELDS = {
    "schema_version",
    "logical_role_id",
    "execution_attempt_id",
    "runtime_session_id",
    "state",
    "wave",
    "task_id",
    "completed_at",
    "output_paths",
    "artifact_hashes",
    "cost",
    "contribution",
}
COST_FIELDS = {"value_usd", "source", "evidence_sha256"}
CONTRIBUTION_FIELDS = {
    "category",
    "epistemic_status",
    "gate_effect",
    "disposition",
    "evidence_chain",
}
DELEGATION_FILE_FIELDS = {"schema_version", "delegations"}


class CloudIngestError(ValueError):
    """A checkpoint failed deterministic validation before any ledger append."""

    def __init__(self, code: str, path: str, message: str):
        super().__init__(message)
        self.code = code
        self.path = path
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


def _fail(code: str, path: str, message: str) -> None:
    raise CloudIngestError(code, path, message)


def _strict(value: Mapping[str, Any], fields: set[str], path: str) -> None:
    missing = sorted(fields - set(value))
    if missing:
        _fail("MISSING_FIELD", path, f"missing required field(s): {', '.join(missing)}")
    unknown = sorted(set(value) - fields)
    if unknown:
        _fail("UNKNOWN_FIELD", path, f"unknown field(s): {', '.join(unknown)}")


def _object(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("TYPE", path, "must be an object")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("TYPE", path, "must be a non-empty string")
    return value


def _token(value: Any, path: str) -> str:
    text = _text(value, path)
    if not local_factory.TOKEN_RE.fullmatch(text):
        _fail("TOKEN", path, "must be a bounded metadata identifier or pointer")
    return text


def _sha(value: Any, path: str) -> str:
    text = _text(value, path)
    if not workspace.SHA256_RE.fullmatch(text):
        _fail("SHA256", path, "must be 64 lowercase hexadecimal characters")
    return text


def _timestamp(value: Any, path: str) -> datetime:
    text = _text(value, path)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _fail("TIMESTAMP", path, "must be an ISO-8601 timestamp")
    if parsed.tzinfo is None:
        _fail("TIMESTAMP", path, "must include a timezone")
    return parsed.astimezone(timezone.utc)


def _relative_path(value: Any, path: str) -> str:
    text = _text(value, path)
    candidate = PurePosixPath(text)
    if (
        "\\" in text
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or candidate.as_posix() != text.rstrip("/")
    ):
        _fail("PATH", path, "must be a normalized repository-relative path")
    return candidate.as_posix()


def _within(path: str, parent: str) -> bool:
    child = PurePosixPath(path).parts
    scope = PurePosixPath(parent).parts
    return child[: len(scope)] == scope


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _no_duplicate_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("DUPLICATE_FIELD", "$", f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_no_duplicate_object,
        )
    except OSError as exc:
        _fail("ARTIFACT_MISSING", str(path), str(exc))
    except json.JSONDecodeError as exc:
        _fail("INPUT_JSON", str(path), str(exc))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class _ValidatedRoleCheckpoint:
    role: workspace.PersistentRole
    initial_attempt: workspace.CloudAttempt
    report: Mapping[str, Any]
    report_path: str
    report_sha256: str
    capsule_path: str
    capsule_sha256: str
    next_prompt_path: str
    next_prompt_sha256: str
    delegations_path: str
    delegations_sha256: str
    heartbeat_sha256: str
    leases: tuple[workspace.Lease, ...]
    delegations: tuple[workspace.DelegationRequest, ...]


def _validate_report(
    raw: Any,
    *,
    role: workspace.PersistentRole,
    initial_attempt: workspace.CloudAttempt,
    repo_root: Path,
    expected_paths: Mapping[str, str],
    actual_hashes: Mapping[str, str],
) -> Mapping[str, Any]:
    report = _object(raw, "$.REPORT.json")
    _strict(report, REPORT_FIELDS, "$.REPORT.json")
    if report["schema_version"] != 1:
        _fail("SCHEMA_VERSION", "$.REPORT.json.schema_version", "must be 1")
    if report["logical_role_id"] != role.logical_role_id:
        _fail("ROLE_MISMATCH", "$.REPORT.json.logical_role_id", "does not match directory role")
    if report["execution_attempt_id"] != initial_attempt.attempt_id:
        _fail(
            "ATTEMPT_MISMATCH",
            "$.REPORT.json.execution_attempt_id",
            "does not match the launch manifest's initial attempt",
        )
    if report["runtime_session_id"] != initial_attempt.runtime_session_id:
        _fail("ATTEMPT_MISMATCH", "$.REPORT.json.runtime_session_id", "does not match initial attempt")
    if report["state"] not in workspace.TERMINAL_ATTEMPT_STATES:
        _fail("ATTEMPT_STATE", "$.REPORT.json.state", "checkpoint report must be terminal")
    if not isinstance(report["wave"], str) or not re.fullmatch(r"W[0-8]", report["wave"]):
        _fail("WAVE", "$.REPORT.json.wave", "must be W0 through W8")
    _token(report["task_id"], "$.REPORT.json.task_id")
    _timestamp(report["completed_at"], "$.REPORT.json.completed_at")

    output_paths = report["output_paths"]
    if not isinstance(output_paths, list):
        _fail("TYPE", "$.REPORT.json.output_paths", "must be an array")
    normalized_outputs = [
        _relative_path(item, f"$.REPORT.json.output_paths[{index}]")
        for index, item in enumerate(output_paths)
    ]
    if len(normalized_outputs) != len(set(normalized_outputs)):
        _fail("DUPLICATE_PATH", "$.REPORT.json.output_paths", "contains duplicate paths")
    required_outputs = set(expected_paths.values())
    if not required_outputs <= set(normalized_outputs):
        missing = sorted(required_outputs - set(normalized_outputs))
        _fail(
            "ARTIFACT_SET",
            "$.REPORT.json.output_paths",
            f"must include all five control artifacts; missing={missing}",
        )
    for output_path in normalized_outputs:
        if not any(_within(output_path, scope) for scope in role.owned_output_paths):
            _fail("WRITE_SCOPE", "$.REPORT.json.output_paths", f"{output_path} is outside role ownership")

    hashes = _object(report["artifact_hashes"], "$.REPORT.json.artifact_hashes")
    additional_outputs = set(normalized_outputs) - required_outputs
    expected_hash_keys = set(HASHED_ARTIFACTS) | additional_outputs
    if set(hashes) != expected_hash_keys:
        missing = sorted(expected_hash_keys - set(hashes))
        unknown = sorted(set(hashes) - expected_hash_keys)
        _fail(
            "ARTIFACT_SET",
            "$.REPORT.json.artifact_hashes",
            f"must hash every listed non-report artifact; missing={missing}, unknown={unknown}",
        )
    for name in HASHED_ARTIFACTS:
        declared = _sha(hashes[name], f"$.REPORT.json.artifact_hashes.{name}")
        if declared != actual_hashes[name]:
            _fail("STALE_HASH", f"$.REPORT.json.artifact_hashes.{name}", "does not match artifact bytes")
    for additional_path in sorted(additional_outputs):
        declared = _sha(
            hashes[additional_path],
            f"$.REPORT.json.artifact_hashes.{additional_path}",
        )
        candidate = (repo_root / additional_path).resolve()
        try:
            candidate.relative_to(repo_root)
        except ValueError:
            _fail("PATH", additional_path, "resolves outside repository root")
        if not candidate.is_file():
            _fail(
                "ARTIFACT_MISSING",
                additional_path,
                "listed role-specific artifact is missing",
            )
        actual = _digest(candidate)
        if declared != actual:
            _fail(
                "STALE_HASH",
                f"$.REPORT.json.artifact_hashes.{additional_path}",
                f"declared {declared}, actual {actual}",
            )

    cost = _object(report["cost"], "$.REPORT.json.cost")
    _strict(cost, COST_FIELDS, "$.REPORT.json.cost")
    value = cost["value_usd"]
    if value is not None and (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        _fail("COST", "$.REPORT.json.cost.value_usd", "must be non-negative finite or null")
    _token(cost["source"], "$.REPORT.json.cost.source")
    evidence = cost["evidence_sha256"]
    if evidence is not None:
        _sha(evidence, "$.REPORT.json.cost.evidence_sha256")
    if value == 0 and evidence is None:
        _fail("UNPROVEN_ZERO_COST", "$.REPORT.json.cost.evidence_sha256", "zero requires hash evidence")
    if value is not None and evidence is None:
        _fail("COST_EVIDENCE", "$.REPORT.json.cost.evidence_sha256", "known cost requires hash evidence")
    if value is None and evidence is not None:
        _fail("UNKNOWN_COST", "$.REPORT.json.cost.evidence_sha256", "unknown cost cannot claim evidence")

    contribution = _object(report["contribution"], "$.REPORT.json.contribution")
    _strict(contribution, CONTRIBUTION_FIELDS, "$.REPORT.json.contribution")
    if contribution["category"] not in local_factory.CONTRIBUTION_CATEGORIES:
        _fail("ENUM", "$.REPORT.json.contribution.category", "unsupported contribution category")
    if contribution["epistemic_status"] not in local_factory.EPISTEMIC_STATUSES:
        _fail("ENUM", "$.REPORT.json.contribution.epistemic_status", "unsupported epistemic status")
    if contribution["gate_effect"] not in local_factory.GATE_EFFECTS:
        _fail("ENUM", "$.REPORT.json.contribution.gate_effect", "unsupported gate effect")
    if contribution["disposition"] not in ("accepted", "rejected", "contested"):
        _fail("ENUM", "$.REPORT.json.contribution.disposition", "unsupported disposition")
    chain = contribution["evidence_chain"]
    if not isinstance(chain, list) or not chain or not all(isinstance(item, str) and item for item in chain):
        _fail("TYPE", "$.REPORT.json.contribution.evidence_chain", "must be a non-empty string array")
    for index, item in enumerate(chain):
        _token(item, f"$.REPORT.json.contribution.evidence_chain[{index}]")
    return report


def _validate_role_directory_closure(
    directory: Path,
    *,
    repo_root: Path,
    report: Mapping[str, Any],
) -> None:
    """Reject undeclared, cache-generated, and symlinked role artifacts."""

    declared = set(report["output_paths"])
    for artifact in directory.rglob("*"):
        relative = artifact.relative_to(repo_root).as_posix()
        parts = artifact.relative_to(directory).parts
        if artifact.is_symlink():
            _fail(
                "SYMLINK_ARTIFACT",
                relative,
                "role directories may not contain symlinks",
            )
        if (
            "__pycache__" in parts
            or ".pytest_cache" in parts
            or artifact.suffix == ".pyc"
        ):
            _fail(
                "CACHE_ARTIFACT",
                relative,
                "cache and compiled Python artifacts are forbidden",
            )
        if artifact.is_file() and relative not in declared:
            _fail(
                "UNLISTED_ARTIFACT",
                relative,
                "every regular file in the role directory must appear in REPORT.json output_paths",
            )


def _validate_heartbeats(
    path: Path,
    *,
    initial_lease: workspace.Lease,
    role_id: str,
    validation_time: datetime,
) -> tuple[workspace.Lease, ...]:
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError as exc:
        _fail("ARTIFACT_MISSING", str(path), str(exc))
    if not lines:
        _fail("HEARTBEAT", str(path), "must contain at least the launch lease")
    leases: list[workspace.Lease] = []
    for index, line in enumerate(lines):
        try:
            raw = json.loads(line, object_pairs_hook=_no_duplicate_object)
            lease = workspace.Lease.from_dict(raw)
        except json.JSONDecodeError as exc:
            _fail("INPUT_JSON", f"{path}:{index + 1}", str(exc))
        except workspace.WorkspaceError as exc:
            raise CloudIngestError(exc.code, f"{path}:{index + 1}", exc.message) from exc
        if lease.holder_role_id != role_id:
            _fail("ROLE_MISMATCH", f"{path}:{index + 1}", "heartbeat belongs to another role")
        if leases:
            previous = leases[-1]
            if lease.sequence != previous.sequence + 1:
                _fail("HEARTBEAT_SEQUENCE", f"{path}:{index + 1}", "sequence must increment by one")
            issued = _timestamp(lease.issued_at, f"{path}:{index + 1}")
            previous_issued = _timestamp(previous.issued_at, f"{path}:{index}")
            previous_expires = _timestamp(previous.expires_at, f"{path}:{index}")
            if issued <= previous_issued or issued >= previous_expires:
                _fail("HEARTBEAT_SEQUENCE", f"{path}:{index + 1}", "renewal must occur within prior lease")
        leases.append(lease)
    if leases[0] != initial_lease:
        _fail("LEASE_MISMATCH", str(path), "first heartbeat must equal launch lease")
    try:
        leases[-1].assert_active(validation_time)
    except workspace.WorkspaceError as exc:
        raise CloudIngestError(exc.code, str(path), exc.message) from exc
    return tuple(leases)


def _validate_delegations(
    path: Path,
    *,
    lane_workspace: workspace.LaneWorkspace,
    role: workspace.PersistentRole,
) -> tuple[workspace.DelegationRequest, ...]:
    raw = _object(_load_json(path), "$.DELEGATIONS.json")
    _strict(raw, DELEGATION_FILE_FIELDS, "$.DELEGATIONS.json")
    if raw["schema_version"] != 1:
        _fail("SCHEMA_VERSION", "$.DELEGATIONS.json.schema_version", "must be 1")
    if not isinstance(raw["delegations"], list):
        _fail("TYPE", "$.DELEGATIONS.json.delegations", "must be an array")
    requests: list[workspace.DelegationRequest] = []
    seen: set[str] = set()
    for index, item in enumerate(raw["delegations"]):
        try:
            request = workspace.DelegationRequest.from_dict(item)
        except workspace.WorkspaceError as exc:
            raise CloudIngestError(
                exc.code, f"$.DELEGATIONS.json.delegations[{index}]", exc.message
            ) from exc
        if request.request_id in seen:
            _fail("DUPLICATE_DELEGATION", f"$.DELEGATIONS.json.delegations[{index}]", "duplicate request")
        seen.add(request.request_id)
        if request.requester_role_id != role.logical_role_id:
            _fail("ROLE_MISMATCH", f"$.DELEGATIONS.json.delegations[{index}]", "requester is not directory role")
        unrouted = workspace.DelegationRequest.from_dict(
            {**request.to_dict(), "routed_by_role_id": None}
        )
        try:
            routed = lane_workspace.route_delegation(
                lane_workspace.coordinator.logical_role_id, unrouted
            )
        except workspace.WorkspaceError as exc:
            raise CloudIngestError(
                exc.code, f"$.DELEGATIONS.json.delegations[{index}]", exc.message
            ) from exc
        if request.routed_by_role_id is not None and request != routed:
            _fail(
                "DELEGATION_AUTHORITY",
                f"$.DELEGATIONS.json.delegations[{index}].routed_by_role_id",
                "only the exact lane coordinator route may be marked routed",
            )
        requests.append(request)
    return tuple(requests)


def _validate_role_checkpoint(
    repo_root: Path,
    relative_dir: str,
    *,
    role: workspace.PersistentRole,
    initial_attempt: workspace.CloudAttempt,
    initial_lease: workspace.Lease,
    lane_workspace: workspace.LaneWorkspace,
    validation_time: datetime,
) -> _ValidatedRoleCheckpoint:
    directory_path = _relative_path(relative_dir, "$.role_directories")
    if not any(_within(directory_path, scope) for scope in role.owned_output_paths):
        _fail("WRITE_SCOPE", directory_path, "role directory is outside owned output paths")
    absolute_dir = (repo_root / directory_path).resolve()
    try:
        absolute_dir.relative_to(repo_root)
    except ValueError:
        _fail("PATH", directory_path, "resolves outside repository root")
    if not absolute_dir.is_dir():
        _fail("ARTIFACT_MISSING", directory_path, "role directory does not exist")
    paths = {name: absolute_dir / name for name in REQUIRED_ARTIFACTS}
    for name, artifact in paths.items():
        if not artifact.is_file():
            _fail("ARTIFACT_MISSING", f"{directory_path}/{name}", "required artifact is missing")
    relative_paths = {
        name: f"{directory_path}/{name}" for name in REQUIRED_ARTIFACTS
    }
    actual_hashes = {name: _digest(paths[name]) for name in HASHED_ARTIFACTS}
    capsule = paths["STATE-CAPSULE.md"].read_text(encoding="utf-8")
    try:
        protocol.validate_state_capsule(capsule)
    except protocol.ProtocolError as exc:
        raise CloudIngestError(exc.code, relative_paths["STATE-CAPSULE.md"], exc.message) from exc
    _text(paths["NEXT-PROMPT.md"].read_text(encoding="utf-8"), relative_paths["NEXT-PROMPT.md"])
    leases = _validate_heartbeats(
        paths["HEARTBEAT.jsonl"],
        initial_lease=initial_lease,
        role_id=role.logical_role_id,
        validation_time=validation_time,
    )
    delegations = _validate_delegations(
        paths["DELEGATIONS.json"], lane_workspace=lane_workspace, role=role
    )
    report = _validate_report(
        _load_json(paths["REPORT.json"]),
        role=role,
        initial_attempt=initial_attempt,
        repo_root=repo_root,
        expected_paths=relative_paths,
        actual_hashes=actual_hashes,
    )
    _validate_role_directory_closure(
        absolute_dir,
        repo_root=repo_root,
        report=report,
    )
    return _ValidatedRoleCheckpoint(
        role=role,
        initial_attempt=initial_attempt,
        report=report,
        report_path=relative_paths["REPORT.json"],
        report_sha256=_digest(paths["REPORT.json"]),
        capsule_path=relative_paths["STATE-CAPSULE.md"],
        capsule_sha256=actual_hashes["STATE-CAPSULE.md"],
        next_prompt_path=relative_paths["NEXT-PROMPT.md"],
        next_prompt_sha256=actual_hashes["NEXT-PROMPT.md"],
        delegations_path=relative_paths["DELEGATIONS.json"],
        delegations_sha256=actual_hashes["DELEGATIONS.json"],
        heartbeat_sha256=actual_hashes["HEARTBEAT.jsonl"],
        leases=leases,
        delegations=delegations,
    )


def ingest_cloud_checkpoints(
    ledger_root: str | Path,
    repo_root: str | Path,
    launch_manifest: workspace.LaneLaunchManifest,
    role_directories: Mapping[str, str],
    *,
    validation_time: datetime,
) -> dict[str, Any]:
    """Validate all six integrated roles, then idempotently append local events.

    This function performs filesystem reads/writes only.  It never calls the
    Model Factory projection push path or any network/database client.
    """

    local_program = local_factory.load_program(ledger_root)
    if local_program["program_id"] != launch_manifest.workspace.program_id:
        _fail("PROGRAM_MISMATCH", "$.launch_manifest", "does not match local ledger program")
    expected_roles = {
        role.logical_role_id: role for role in launch_manifest.workspace.roles
    }
    if set(role_directories) != set(expected_roles):
        missing = sorted(set(expected_roles) - set(role_directories))
        unknown = sorted(set(role_directories) - set(expected_roles))
        _fail(
            "ROLE_SET",
            "$.role_directories",
            f"must contain exactly six roles; missing={missing}, unknown={unknown}",
        )
    if len(set(role_directories.values())) != len(role_directories):
        _fail("DUPLICATE_ROLE_DIRECTORY", "$.role_directories", "directories must be unique")

    repo = Path(repo_root).resolve()
    attempts = {
        attempt.logical_role_id: attempt
        for attempt in launch_manifest.initial_attempts
    }
    leases = {
        lease.holder_role_id: lease for lease in launch_manifest.leases
    }
    existing = local_factory.read_events(
        ledger_root, launch_manifest.workspace.lane
    )
    existing_attempts = [
        event for event in existing if event["category"] == "attempt"
    ]
    checkpoints: list[_ValidatedRoleCheckpoint] = []
    for role_id in sorted(expected_roles):
        checkpoint = _validate_role_checkpoint(
            repo,
            role_directories[role_id],
            role=expected_roles[role_id],
            initial_attempt=attempts[role_id],
            initial_lease=leases[role_id],
            lane_workspace=launch_manifest.workspace,
            validation_time=validation_time,
        )
        conflicting = [
            event
            for event in existing_attempts
            if event["data"]["logical_role_id"] == role_id
            and event["data"]["execution_attempt_id"]
            != checkpoint.initial_attempt.attempt_id
        ]
        if conflicting:
            _fail("DUPLICATE_ROLE_ATTEMPT", checkpoint.report_path, "role already has another attempt")
        checkpoints.append(checkpoint)

    lane = launch_manifest.workspace.lane
    program_uuid = uuid.UUID(local_program["program_id"])
    attempt_id_by_role = {
        item.initial_attempt.logical_role_id: item.initial_attempt.attempt_id
        for item in checkpoints
    }
    planned: list[tuple[str, dict[str, Any], str, str]] = []
    for item in checkpoints:
        report = item.report
        observed_at = _iso(_timestamp(report["completed_at"], "$.completed_at"))
        cost = report["cost"]
        ledger_cost = {
            "value_usd": cost["value_usd"],
            "source": cost["source"],
            "accepted_unknown": cost["value_usd"] is None,
        }
        attempt_data = {
            "logical_role_id": item.role.logical_role_id,
            "execution_attempt_id": item.initial_attempt.attempt_id,
            "predecessor_attempt_id": None,
            "runtime_session_id": report["runtime_session_id"],
            "continuity_mode": "AUTHOR_RESUME",
            "task_id": report["task_id"],
            "wave": report["wave"],
            "state": report["state"],
            "cost": ledger_cost,
        }
        planned.append(
            (
                "attempt",
                attempt_data,
                f"cloud-attempt:{item.initial_attempt.attempt_id}",
                observed_at,
            )
        )
        if cost["value_usd"] is not None:
            correction_id = str(
                uuid.uuid5(
                    program_uuid,
                    f"cloud-cost:{item.initial_attempt.attempt_id}:{cost['evidence_sha256']}",
                )
            )
            planned.append(
                (
                    "attempt_cost",
                    {
                        "correction_id": correction_id,
                        "execution_attempt_id": item.initial_attempt.attempt_id,
                        "runtime_session_id": report["runtime_session_id"],
                        "evidence_sha256": cost["evidence_sha256"],
                        "cost": ledger_cost,
                    },
                    f"cloud-cost:{item.initial_attempt.attempt_id}",
                    observed_at,
                )
            )
        contribution = report["contribution"]
        contribution_id = str(
            uuid.uuid5(
                program_uuid,
                f"cloud-contribution:{item.initial_attempt.attempt_id}:{item.report_sha256}",
            )
        )
        planned.append(
            (
                "contribution",
                {
                    "contribution_id": contribution_id,
                    "execution_attempt_id": item.initial_attempt.attempt_id,
                    "category": contribution["category"],
                    "epistemic_status": contribution["epistemic_status"],
                    "gate_effect": contribution["gate_effect"],
                    "report_sha256": item.report_sha256,
                    "evidence_chain": contribution["evidence_chain"],
                    "disposition": contribution["disposition"],
                },
                f"cloud-contribution:{item.initial_attempt.attempt_id}",
                observed_at,
            )
        )
        next_flow_id = str(
            uuid.uuid5(
                program_uuid, f"cloud-next-prompt:{item.initial_attempt.attempt_id}"
            )
        )
        planned.append(
            (
                "flow",
                {
                    "flow_id": next_flow_id,
                    "flow_type": "handoff",
                    "from_ref": item.initial_attempt.attempt_id,
                    "to_ref": None,
                    "artifact": item.next_prompt_path,
                    "artifact_sha256": item.next_prompt_sha256,
                    "terminal_state": None,
                    "signed_by": None,
                },
                f"cloud-next-prompt:{item.initial_attempt.attempt_id}",
                observed_at,
            )
        )
        for lease in item.leases:
            lease_sha256 = workspace.canonical_sha256(lease.to_dict())
            planned.append(
                (
                    "cloud_lease",
                    {
                        "lease_id": lease.lease_id,
                        "logical_role_id": lease.holder_role_id,
                        "sequence": lease.sequence,
                        "issued_at": lease.issued_at,
                        "expires_at": lease.expires_at,
                        "heartbeat_sha256": lease_sha256,
                    },
                    f"cloud-lease:{lease.lease_id}",
                    lease.issued_at,
                )
            )
        for request in item.delegations:
            routed = request.routed_by_role_id is not None
            request_sha256 = workspace.canonical_sha256(request.to_dict())
            planned.append(
                (
                    "delegation",
                    {
                        "request_id": request.request_id,
                        "requester_role_id": request.requester_role_id,
                        "target_role_id": request.target_role_id,
                        "task_id": request.task_id,
                        "status": "routed" if routed else "requested",
                        "routed_by_role_id": request.routed_by_role_id,
                        "artifact": item.delegations_path,
                        "artifact_sha256": request_sha256,
                    },
                    f"cloud-delegation:{request.request_id}",
                    request.created_at,
                )
            )
            if routed:
                flow_id = str(
                    uuid.uuid5(program_uuid, f"cloud-delegation-flow:{request.request_id}")
                )
                planned.append(
                    (
                        "flow",
                        {
                            "flow_id": flow_id,
                            "flow_type": "handoff",
                            "from_ref": attempt_id_by_role[request.requester_role_id],
                            "to_ref": attempt_id_by_role[request.target_role_id],
                            "artifact": item.delegations_path,
                            "artifact_sha256": request_sha256,
                            "terminal_state": None,
                            "signed_by": None,
                        },
                        f"cloud-delegation-flow:{request.request_id}",
                        request.created_at,
                    )
                )

    for category, data, _, _ in planned:
        local_factory._validate_event(category, data)
    existing_by_id = {event["event_id"]: event for event in existing}
    for category, data, key, _ in planned:
        event_id = local_factory._uuid5(program_uuid, lane, category, key)
        prior = existing_by_id.get(event_id)
        if prior is None:
            continue
        proposed = {
            "schema_version": 1,
            "event_id": event_id,
            "program_id": local_program["program_id"],
            "model_run_id": local_program["lanes"][lane]["model_run_id"],
            "lane": lane,
            "category": category,
            "data": data,
        }
        comparable = {
            name: value
            for name, value in prior.items()
            if name != "observed_at"
        }
        if comparable != proposed:
            _fail(
                "IDEMPOTENCY_COLLISION",
                "$.ledger",
                f"immutable event {event_id} already has different data",
            )
    events = [
        local_factory.append_event(
            ledger_root,
            lane,
            category,
            data,
            key,
            observed_at=observed_at,
        )
        for category, data, key, observed_at in planned
    ]
    counts: dict[str, int] = {}
    for event in events:
        counts[event["category"]] = counts.get(event["category"], 0) + 1
    return {
        "delivery_status": "ingested_local_only_not_pushed",
        "lane": lane,
        "launch_manifest_id": launch_manifest.launch_manifest_id,
        "roles_ingested": len(checkpoints),
        "events": counts,
    }


def load_lane_launch_manifest(path: str | Path) -> workspace.LaneLaunchManifest:
    """Load a manifest at a safe instant immediately after all leases issue."""

    manifest_path = Path(path)
    try:
        raw = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_no_duplicate_object,
        )
    except OSError as exc:
        _fail("MANIFEST_READ", str(manifest_path), str(exc))
    except json.JSONDecodeError as exc:
        _fail("INPUT_JSON", str(manifest_path), str(exc))
    value = _object(raw, "$.lane_manifest")
    raw_leases = value.get("leases")
    if not isinstance(raw_leases, list) or not raw_leases:
        _fail("MANIFEST_LEASE", "$.lane_manifest.leases", "must be a non-empty array")
    issued_at = max(
        _timestamp(
            _object(lease, f"$.lane_manifest.leases[{index}]").get("issued_at"),
            f"$.lane_manifest.leases[{index}].issued_at",
        )
        for index, lease in enumerate(raw_leases)
    )
    try:
        return workspace.LaneLaunchManifest.from_dict(
            value,
            validation_time=issued_at + timedelta(seconds=1),
        )
    except workspace.WorkspaceError as exc:
        raise CloudIngestError(exc.code, exc.path, exc.message) from exc


def infer_role_directories(
    launch_manifest: workspace.LaneLaunchManifest,
) -> dict[str, str]:
    """Derive one unambiguous integrated directory for every logical role."""

    result: dict[str, str] = {}
    for role in launch_manifest.workspace.roles:
        if len(role.owned_output_paths) != 1:
            _fail(
                "ROLE_DIRECTORY",
                role.logical_role_id,
                "CLI inference requires exactly one owned_output_path per role",
            )
        result[role.logical_role_id] = role.owned_output_paths[0]
    if len(set(result.values())) != 6:
        _fail(
            "ROLE_DIRECTORY",
            "$.lane_manifest.workspace.roles",
            "inferred role directories must be unique",
        )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate and locally ingest one cloud lane checkpoint"
    )
    parser.add_argument(
        "--lane-manifest",
        "--manifest",
        dest="lane_manifest",
        required=True,
    )
    parser.add_argument("--ledger-root", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--validation-time", required=True)
    parser.add_argument(
        "--projection-output",
        help="optional local JSON output from project_existing_table_rows",
    )
    args = parser.parse_args(argv)
    try:
        launch_manifest = load_lane_launch_manifest(args.lane_manifest)
        validation_time = _timestamp(args.validation_time, "$.validation_time")
        summary = ingest_cloud_checkpoints(
            args.ledger_root,
            args.repo_root,
            launch_manifest,
            infer_role_directories(launch_manifest),
            validation_time=validation_time,
        )
        if args.projection_output:
            projection = local_factory.project_existing_table_rows(
                args.ledger_root, launch_manifest.workspace.lane
            )
            projection_path = Path(args.projection_output)
            projection_path.parent.mkdir(parents=True, exist_ok=True)
            projection_path.write_text(
                json.dumps(
                    projection,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            summary = {
                **summary,
                "projection_output": str(projection_path),
                "projection_delivery_status": projection["delivery_status"],
            }
        print(
            json.dumps(
                summary,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
        return 0
    except (
        CloudIngestError,
        workspace.WorkspaceError,
        local_factory.LedgerError,
        OSError,
    ) as exc:
        if isinstance(exc, (CloudIngestError, workspace.WorkspaceError)):
            error = exc.as_dict()
        else:
            error = {"code": "INGEST", "path": "$", "message": str(exc)}
        print(
            json.dumps(
                {"valid": False, "error": error},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
