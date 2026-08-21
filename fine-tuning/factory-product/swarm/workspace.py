"""Persistent, fail-closed cloud workspace contracts for the dual-model swarm.

The objects in this module are immutable value objects.  They describe cloud
work; they do not launch agents, spend money, or mutate Model Factory state.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, ClassVar, Mapping, Sequence

try:  # Package import in tests and direct import beside runner.py are both supported.
    from . import protocol
except ImportError:  # pragma: no cover - exercised by runner-style direct imports
    import protocol  # type: ignore


SCHEMA_VERSION = 1
LANES = frozenset({"ptc", "grand-steel"})
CONTINUITY_MODES = frozenset({"AUTHOR_RESUME", "SUCCESSOR_RECONSTRUCTION"})
ATTEMPT_STATES = frozenset(
    {"PENDING", "RUNNING", "COMPLETED", "BLOCKED", "TIMED_OUT", "FAILED", "UNRESUMABLE"}
)
TERMINAL_ATTEMPT_STATES = ATTEMPT_STATES - {"PENDING", "RUNNING"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
WORKSPACE_NAMESPACE = uuid.UUID("d6bd483f-33e0-54af-a7be-15dad4335c25")

LANE_ROLE_NAMES: Mapping[str, tuple[str, ...]] = {
    "ptc": (
        "LANE-COORD",
        "RESCOPE-DATA-BUILDER",
        "CORPUS-VERIFIER",
        "EVAL-ARCH",
        "PROVIDER-COST-VERIFIER",
        "REDTEAM",
    ),
    "grand-steel": (
        "LANE-COORD",
        "BASE1-HARNESS",
        "RUNTIME-CONTROL",
        "CONDITIONAL-CORPUS-ARCH",
        "EVAL-ARCH",
        "SERVE-ROLLBACK-ARCH",
    ),
}


class WorkspaceError(ValueError):
    """Stable validation failure raised before cloud work may be routed."""

    def __init__(self, code: str, path: str, message: str):
        super().__init__(message)
        self.code = code
        self.path = path
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


def _fail(code: str, path: str, message: str) -> None:
    raise WorkspaceError(code, path, message)


def _strict(value: Mapping[str, Any], fields: set[str], path: str) -> None:
    missing = sorted(fields - set(value))
    if missing:
        _fail("MISSING_FIELD", path, f"missing required field(s): {', '.join(missing)}")
    unknown = sorted(set(value) - fields)
    if unknown:
        _fail("UNKNOWN_FIELD", path, f"unknown field(s): {', '.join(unknown)}")


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("TYPE", path, "must be an object")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("TYPE", path, "must be a non-empty string")
    return value


def _token(value: Any, path: str) -> str:
    text = _text(value, path)
    if not TOKEN_RE.fullmatch(text):
        _fail("TOKEN", path, "contains unsupported identifier characters")
    return text


def _uuid(value: Any, path: str) -> str:
    text = _text(value, path)
    try:
        parsed = uuid.UUID(text)
    except ValueError:
        _fail("UUID", path, "must be a canonical UUID")
    if str(parsed) != text.lower():
        _fail("UUID", path, "must be a canonical lowercase UUID")
    return text


def _sha(value: Any, path: str) -> str:
    text = _text(value, path)
    if not SHA256_RE.fullmatch(text):
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


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        _fail("TIMESTAMP", "$", "datetime must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _path(value: Any, path: str) -> str:
    text = _text(value, path)
    candidate = PurePosixPath(text)
    if "\\" in text or candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        _fail("PATH", path, "must be a normalized repository-relative POSIX path")
    normalized = candidate.as_posix()
    if normalized != text.rstrip("/"):
        _fail("PATH", path, "must be normalized")
    return normalized


def _paths(value: Any, path: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        _fail("TYPE", path, "must be an array")
    result = tuple(_path(item, f"{path}[{index}]") for index, item in enumerate(value))
    if not allow_empty and not result:
        _fail("PATH", path, "must not be empty")
    if len(result) != len(set(result)):
        _fail("DUPLICATE_PATH", path, "must not contain duplicate paths")
    return result


def _within(path: str, parent: str) -> bool:
    child_parts = PurePosixPath(path).parts
    parent_parts = PurePosixPath(parent).parts
    return child_parts[: len(parent_parts)] == parent_parts


def _overlap(left: str, right: str) -> bool:
    return _within(left, right) or _within(right, left)


def _lane_marker(path: str) -> str | None:
    lowered = f"/{path.lower()}/"
    if "/grand-steel/" in lowered:
        return "grand-steel"
    if "/ptc-steel/" in lowered or "/ptc/" in lowered:
        return "ptc"
    return None


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _stable_uuid(*parts: object) -> str:
    return str(uuid.uuid5(WORKSPACE_NAMESPACE, "\x1f".join(str(part) for part in parts)))


def _role_parts(logical_role_id: str, path: str = "$.logical_role_id") -> tuple[str, str, str]:
    try:
        program_id, lane, role_name = logical_role_id.split("/")
    except ValueError:
        _fail("LOGICAL_ROLE_ID", path, "must be <program_id>/<lane>/<role>")
    _uuid(program_id, path)
    if lane not in LANES:
        _fail("LANE", path, "contains an unsupported lane")
    return program_id, lane, role_name


@dataclass(frozen=True)
class PersistentRole:
    """One durable logical identity, independent of any cloud runtime session."""

    program_id: str
    lane: str
    role_name: str
    logical_role_id: str
    read_paths: tuple[str, ...]
    owned_output_paths: tuple[str, ...]
    budget_usd: float | None = None

    FIELDS: ClassVar[set[str]] = {
        "program_id", "lane", "role_name", "logical_role_id", "read_paths",
        "owned_output_paths", "budget_usd",
    }

    @classmethod
    def create(
        cls,
        program_id: str,
        lane: str,
        role_name: str,
        *,
        read_paths: Sequence[str],
        owned_output_paths: Sequence[str],
        budget_usd: float | None = None,
    ) -> "PersistentRole":
        return cls.from_dict({
            "program_id": program_id,
            "lane": lane,
            "role_name": role_name,
            "logical_role_id": f"{program_id}/{lane}/{role_name}",
            "read_paths": list(read_paths),
            "owned_output_paths": list(owned_output_paths),
            "budget_usd": budget_usd,
        })

    @classmethod
    def from_dict(cls, raw: Any) -> "PersistentRole":
        value = _mapping(raw, "$")
        _strict(value, cls.FIELDS, "$")
        program_id = _uuid(value["program_id"], "$.program_id")
        lane = _text(value["lane"], "$.lane")
        if lane not in LANES:
            _fail("LANE", "$.lane", f"must be one of: {', '.join(sorted(LANES))}")
        role_name = _token(value["role_name"], "$.role_name")
        if role_name not in LANE_ROLE_NAMES[lane]:
            _fail("ROLE", "$.role_name", f"is not one of the six {lane} cloud roles")
        logical_role_id = _text(value["logical_role_id"], "$.logical_role_id")
        if logical_role_id != f"{program_id}/{lane}/{role_name}":
            _fail("LOGICAL_ROLE_ID", "$.logical_role_id", "must exactly match program, lane, and role")
        reads = _paths(value["read_paths"], "$.read_paths")
        owned = _paths(value["owned_output_paths"], "$.owned_output_paths", allow_empty=False)
        for field, paths in (("read_paths", reads), ("owned_output_paths", owned)):
            for item in paths:
                marker = _lane_marker(item)
                if marker is not None and marker != lane:
                    _fail("LANE_CROSSOVER", f"$.{field}", f"{item} belongs to {marker}")
        for index, left in enumerate(owned):
            for right in owned[index + 1:]:
                if _overlap(left, right):
                    _fail("SCOPE_OVERLAP", "$.owned_output_paths", f"overlapping paths: {left} and {right}")
        budget = value["budget_usd"]
        if budget is not None:
            if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget) or budget < 0:
                _fail("COST", "$.budget_usd", "must be a finite non-negative number or null")
            budget = float(budget)
        return cls(program_id, lane, role_name, logical_role_id, reads, owned, budget)

    def to_dict(self) -> dict[str, Any]:
        return {
            "program_id": self.program_id,
            "lane": self.lane,
            "role_name": self.role_name,
            "logical_role_id": self.logical_role_id,
            "read_paths": list(self.read_paths),
            "owned_output_paths": list(self.owned_output_paths),
            "budget_usd": self.budget_usd,
        }


@dataclass(frozen=True)
class Lease:
    """A renewable 30-minute authority token for exactly one persistent role."""

    lease_id: str
    holder_role_id: str
    sequence: int
    issued_at: str
    expires_at: str

    FIELDS: ClassVar[set[str]] = {"lease_id", "holder_role_id", "sequence", "issued_at", "expires_at"}

    @classmethod
    def issue(cls, holder_role_id: str, *, now: datetime, sequence: int = 1) -> "Lease":
        _role_parts(holder_role_id, "$.holder_role_id")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            _fail("LEASE_SEQUENCE", "$.sequence", "must be a positive integer")
        issued = _iso(now)
        expires = _iso(now + timedelta(seconds=1800))
        return cls.from_dict({
            "lease_id": _stable_uuid("lease", holder_role_id, sequence, issued),
            "holder_role_id": holder_role_id,
            "sequence": sequence,
            "issued_at": issued,
            "expires_at": expires,
        })

    @classmethod
    def from_dict(cls, raw: Any) -> "Lease":
        value = _mapping(raw, "$")
        _strict(value, cls.FIELDS, "$")
        lease_id = _uuid(value["lease_id"], "$.lease_id")
        holder = _text(value["holder_role_id"], "$.holder_role_id")
        _role_parts(holder, "$.holder_role_id")
        sequence = value["sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            _fail("LEASE_SEQUENCE", "$.sequence", "must be a positive integer")
        issued = _timestamp(value["issued_at"], "$.issued_at")
        expires = _timestamp(value["expires_at"], "$.expires_at")
        if expires - issued != timedelta(seconds=1800):
            _fail("LEASE_WINDOW", "$.expires_at", "must be exactly 1800 seconds after issued_at")
        expected_id = _stable_uuid("lease", holder, sequence, _iso(issued))
        if lease_id != expected_id:
            _fail("DETERMINISTIC_ID", "$.lease_id", "does not match the lease identity fields")
        return cls(lease_id, holder, sequence, _iso(issued), _iso(expires))

    def assert_active(self, now: datetime) -> None:
        if _timestamp(_iso(now), "$.now") >= _timestamp(self.expires_at, "$.expires_at"):
            _fail("STALE_LEASE", "$.lease", f"lease {self.lease_id} has expired")

    def renew(self, *, now: datetime) -> "Lease":
        self.assert_active(now)
        return Lease.issue(self.holder_role_id, now=now, sequence=self.sequence + 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "holder_role_id": self.holder_role_id,
            "sequence": self.sequence,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class ContinuationEnvelope:
    """Hash-bound artifact continuity for a persistent role."""

    logical_role_id: str
    continuity_mode: str
    predecessor_attempt_id: str | None
    predecessor_runtime_session_id: str | None
    predecessor_report_sha256: str | None
    state_capsule: str
    state_capsule_sha256: str
    next_prompt: str
    next_prompt_sha256: str

    FIELDS: ClassVar[set[str]] = {
        "logical_role_id", "continuity_mode", "predecessor_attempt_id",
        "predecessor_runtime_session_id", "predecessor_report_sha256", "state_capsule",
        "state_capsule_sha256", "next_prompt", "next_prompt_sha256",
    }

    @classmethod
    def create(
        cls,
        logical_role_id: str,
        *,
        continuity_mode: str,
        predecessor_attempt_id: str | None,
        predecessor_runtime_session_id: str | None,
        predecessor_report_sha256: str | None,
        state_capsule: str,
        next_prompt: str,
    ) -> "ContinuationEnvelope":
        return cls.from_dict({
            "logical_role_id": logical_role_id,
            "continuity_mode": continuity_mode,
            "predecessor_attempt_id": predecessor_attempt_id,
            "predecessor_runtime_session_id": predecessor_runtime_session_id,
            "predecessor_report_sha256": predecessor_report_sha256,
            "state_capsule": state_capsule,
            "state_capsule_sha256": hashlib.sha256(state_capsule.encode()).hexdigest(),
            "next_prompt": next_prompt,
            "next_prompt_sha256": hashlib.sha256(next_prompt.encode()).hexdigest(),
        })

    @classmethod
    def from_dict(cls, raw: Any) -> "ContinuationEnvelope":
        value = _mapping(raw, "$")
        _strict(value, cls.FIELDS, "$")
        role_id = _text(value["logical_role_id"], "$.logical_role_id")
        _role_parts(role_id)
        mode = _text(value["continuity_mode"], "$.continuity_mode")
        if mode not in CONTINUITY_MODES:
            _fail("CONTINUITY", "$.continuity_mode", "is not a supported continuity mode")
        predecessor = value["predecessor_attempt_id"]
        predecessor_session = value["predecessor_runtime_session_id"]
        predecessor_report = value["predecessor_report_sha256"]
        if predecessor is not None:
            predecessor = _uuid(predecessor, "$.predecessor_attempt_id")
        if predecessor_session is not None:
            predecessor_session = _token(predecessor_session, "$.predecessor_runtime_session_id")
        if predecessor_report is not None:
            predecessor_report = _sha(predecessor_report, "$.predecessor_report_sha256")
        capsule = _text(value["state_capsule"], "$.state_capsule")
        try:
            protocol.validate_state_capsule(capsule)
        except protocol.ProtocolError as exc:
            raise WorkspaceError(exc.code, "$.state_capsule", exc.message) from exc
        capsule_hash = _sha(value["state_capsule_sha256"], "$.state_capsule_sha256")
        if hashlib.sha256(capsule.encode()).hexdigest() != capsule_hash:
            _fail("STALE_HASH", "$.state_capsule_sha256", "does not match state_capsule bytes")
        next_prompt = _text(value["next_prompt"], "$.next_prompt")
        prompt_hash = _sha(value["next_prompt_sha256"], "$.next_prompt_sha256")
        if hashlib.sha256(next_prompt.encode()).hexdigest() != prompt_hash:
            _fail("STALE_HASH", "$.next_prompt_sha256", "does not match NEXT-PROMPT bytes")
        if predecessor is None:
            if any(item is not None for item in (predecessor_session, predecessor_report)):
                _fail("CONTINUITY", "$", "initial continuity may not claim predecessor artifacts")
            if mode != "AUTHOR_RESUME":
                _fail("CONTINUITY", "$.continuity_mode", "initial continuity must use AUTHOR_RESUME")
        else:
            if predecessor_session is None or predecessor_report is None:
                _fail("CONTINUITY", "$", "continued work requires predecessor session and report hash")
        return cls(
            role_id, mode, predecessor, predecessor_session, predecessor_report,
            capsule, capsule_hash, next_prompt, prompt_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_role_id": self.logical_role_id,
            "continuity_mode": self.continuity_mode,
            "predecessor_attempt_id": self.predecessor_attempt_id,
            "predecessor_runtime_session_id": self.predecessor_runtime_session_id,
            "predecessor_report_sha256": self.predecessor_report_sha256,
            "state_capsule": self.state_capsule,
            "state_capsule_sha256": self.state_capsule_sha256,
            "next_prompt": self.next_prompt,
            "next_prompt_sha256": self.next_prompt_sha256,
        }


@dataclass(frozen=True)
class DelegationRequest:
    """A specialist's request; inert until routed by its lane coordinator."""

    request_id: str
    program_id: str
    lane: str
    requester_role_id: str
    target_role_id: str
    task_id: str
    read_paths: tuple[str, ...]
    owned_output_paths: tuple[str, ...]
    created_at: str
    routed_by_role_id: str | None = None

    FIELDS: ClassVar[set[str]] = {
        "request_id", "program_id", "lane", "requester_role_id", "target_role_id",
        "task_id", "read_paths", "owned_output_paths", "created_at", "routed_by_role_id",
    }

    @classmethod
    def create(
        cls,
        program_id: str,
        lane: str,
        requester_role_id: str,
        target_role_id: str,
        task_id: str,
        *,
        read_paths: Sequence[str],
        owned_output_paths: Sequence[str],
        created_at: datetime,
    ) -> "DelegationRequest":
        stamp = _iso(created_at)
        request_id = _stable_uuid(
            "delegation", program_id, lane, requester_role_id, target_role_id, task_id,
            canonical_sha256([list(read_paths), list(owned_output_paths)]), stamp,
        )
        return cls.from_dict({
            "request_id": request_id,
            "program_id": program_id,
            "lane": lane,
            "requester_role_id": requester_role_id,
            "target_role_id": target_role_id,
            "task_id": task_id,
            "read_paths": list(read_paths),
            "owned_output_paths": list(owned_output_paths),
            "created_at": stamp,
            "routed_by_role_id": None,
        })

    @classmethod
    def from_dict(cls, raw: Any) -> "DelegationRequest":
        value = _mapping(raw, "$")
        _strict(value, cls.FIELDS, "$")
        request_id = _uuid(value["request_id"], "$.request_id")
        program_id = _uuid(value["program_id"], "$.program_id")
        lane = _text(value["lane"], "$.lane")
        if lane not in LANES:
            _fail("LANE", "$.lane", "is unsupported")
        requester = _text(value["requester_role_id"], "$.requester_role_id")
        target = _text(value["target_role_id"], "$.target_role_id")
        for field, role_id in (("requester_role_id", requester), ("target_role_id", target)):
            role_program, role_lane, _ = _role_parts(role_id, f"$.{field}")
            if (role_program, role_lane) != (program_id, lane):
                _fail("LANE_CROSSOVER", f"$.{field}", "must belong to this program and lane")
        task_id = _token(value["task_id"], "$.task_id")
        reads = _paths(value["read_paths"], "$.read_paths")
        owned = _paths(value["owned_output_paths"], "$.owned_output_paths", allow_empty=False)
        created = _iso(_timestamp(value["created_at"], "$.created_at"))
        expected_id = _stable_uuid(
            "delegation", program_id, lane, requester, target, task_id,
            canonical_sha256([list(reads), list(owned)]), created,
        )
        if request_id != expected_id:
            _fail("DETERMINISTIC_ID", "$.request_id", "does not match the delegation fields")
        routed = value["routed_by_role_id"]
        if routed is not None:
            routed = _text(routed, "$.routed_by_role_id")
            _, routed_lane, routed_name = _role_parts(routed, "$.routed_by_role_id")
            if routed_lane != lane or routed_name != "LANE-COORD":
                _fail("DELEGATION_AUTHORITY", "$.routed_by_role_id", "must be this lane's coordinator")
        return cls(request_id, program_id, lane, requester, target, task_id, reads, owned, created, routed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "program_id": self.program_id,
            "lane": self.lane,
            "requester_role_id": self.requester_role_id,
            "target_role_id": self.target_role_id,
            "task_id": self.task_id,
            "read_paths": list(self.read_paths),
            "owned_output_paths": list(self.owned_output_paths),
            "created_at": self.created_at,
            "routed_by_role_id": self.routed_by_role_id,
        }


@dataclass(frozen=True)
class CloudAttempt:
    """One immutable cloud execution record with honest cost semantics."""

    attempt_id: str
    attempt_key: str
    logical_role_id: str
    runtime_session_id: str
    state: str
    lease: Lease
    continuation: ContinuationEnvelope
    cost_usd: float | None
    cost_source: str
    cost_evidence_sha256: str | None
    report_sha256: str | None

    FIELDS: ClassVar[set[str]] = {
        "attempt_id", "attempt_key", "logical_role_id", "runtime_session_id", "state", "lease",
        "continuation", "cost_usd", "cost_source", "cost_evidence_sha256", "report_sha256",
    }

    @classmethod
    def create(
        cls,
        role: PersistentRole,
        lease: Lease,
        continuation: ContinuationEnvelope,
        *,
        attempt_key: str,
        runtime_session_id: str,
        state: str = "PENDING",
        cost_usd: float | None = None,
        cost_source: str = "cloud-runtime-unreported",
        cost_evidence_sha256: str | None = None,
        report_sha256: str | None = None,
    ) -> "CloudAttempt":
        attempt_id = _stable_uuid("attempt", role.program_id, role.logical_role_id, attempt_key)
        return cls.from_dict({
            "attempt_id": attempt_id,
            "attempt_key": attempt_key,
            "logical_role_id": role.logical_role_id,
            "runtime_session_id": runtime_session_id,
            "state": state,
            "lease": lease.to_dict(),
            "continuation": continuation.to_dict(),
            "cost_usd": cost_usd,
            "cost_source": cost_source,
            "cost_evidence_sha256": cost_evidence_sha256,
            "report_sha256": report_sha256,
        })

    @classmethod
    def from_dict(cls, raw: Any) -> "CloudAttempt":
        value = _mapping(raw, "$")
        _strict(value, cls.FIELDS, "$")
        attempt_id = _uuid(value["attempt_id"], "$.attempt_id")
        attempt_key = _token(value["attempt_key"], "$.attempt_key")
        role_id = _text(value["logical_role_id"], "$.logical_role_id")
        program_id, _, _ = _role_parts(role_id)
        expected_id = _stable_uuid("attempt", program_id, role_id, attempt_key)
        if attempt_id != expected_id:
            _fail("DETERMINISTIC_ID", "$.attempt_id", "does not match the attempt identity fields")
        session_id = _token(value["runtime_session_id"], "$.runtime_session_id")
        state = _text(value["state"], "$.state")
        if state not in ATTEMPT_STATES:
            _fail("ATTEMPT_STATE", "$.state", "is unsupported")
        lease = Lease.from_dict(value["lease"])
        continuation = ContinuationEnvelope.from_dict(value["continuation"])
        if lease.holder_role_id != role_id or continuation.logical_role_id != role_id:
            _fail("ROLE_MISMATCH", "$", "attempt, lease, and continuation must share one role identity")
        if continuation.predecessor_attempt_id == attempt_id:
            _fail("CONTINUITY", "$.continuation.predecessor_attempt_id", "may not equal current attempt")
        if continuation.predecessor_attempt_id is not None:
            predecessor_session = continuation.predecessor_runtime_session_id
            if (
                continuation.continuity_mode == "AUTHOR_RESUME"
                and session_id != predecessor_session
            ):
                _fail(
                    "CONTINUITY", "$.runtime_session_id",
                    "AUTHOR_RESUME requires the predecessor runtime session",
                )
            if (
                continuation.continuity_mode == "SUCCESSOR_RECONSTRUCTION"
                and session_id == predecessor_session
            ):
                _fail(
                    "CONTINUITY", "$.runtime_session_id",
                    "SUCCESSOR_RECONSTRUCTION requires a distinct runtime session",
                )
        cost = value["cost_usd"]
        if cost is not None:
            if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
                _fail("COST", "$.cost_usd", "must be a finite non-negative number or null")
            cost = float(cost)
        source = _token(value["cost_source"], "$.cost_source")
        evidence = value["cost_evidence_sha256"]
        if evidence is not None:
            evidence = _sha(evidence, "$.cost_evidence_sha256")
        if cost == 0 and evidence is None:
            _fail("UNPROVEN_ZERO_COST", "$.cost_evidence_sha256", "zero cost requires hashed evidence")
        if cost is None and evidence is not None:
            _fail("UNKNOWN_COST", "$.cost_evidence_sha256", "unknown cost may not claim value evidence")
        report = value["report_sha256"]
        if report is not None:
            report = _sha(report, "$.report_sha256")
        if state in TERMINAL_ATTEMPT_STATES and report is None:
            _fail("MISSING_REPORT", "$.report_sha256", "terminal attempt requires a report hash")
        return cls(
            attempt_id, attempt_key, role_id, session_id, state, lease,
            continuation, cost, source, evidence, report,
        )

    def assert_dispatchable(self, now: datetime) -> None:
        self.lease.assert_active(now)
        if self.state != "PENDING":
            _fail("ATTEMPT_STATE", "$.state", "only a PENDING attempt may be dispatched")

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "attempt_key": self.attempt_key,
            "logical_role_id": self.logical_role_id,
            "runtime_session_id": self.runtime_session_id,
            "state": self.state,
            "lease": self.lease.to_dict(),
            "continuation": self.continuation.to_dict(),
            "cost_usd": self.cost_usd,
            "cost_source": self.cost_source,
            "cost_evidence_sha256": self.cost_evidence_sha256,
            "report_sha256": self.report_sha256,
        }


@dataclass(frozen=True)
class LaneWorkspace:
    """A six-role, lane-isolated cloud manifest and routing authority."""

    workspace_id: str
    program_id: str
    lane: str
    created_at: str
    roles: tuple[PersistentRole, ...]

    FIELDS: ClassVar[set[str]] = {"schema_version", "workspace_id", "program_id", "lane", "created_at", "roles"}

    @classmethod
    def create(
        cls,
        program_id: str,
        lane: str,
        roles: Sequence[PersistentRole],
        *,
        created_at: datetime,
    ) -> "LaneWorkspace":
        stamp = _iso(created_at)
        return cls.from_dict({
            "schema_version": SCHEMA_VERSION,
            "workspace_id": _stable_uuid("workspace", program_id, lane),
            "program_id": program_id,
            "lane": lane,
            "created_at": stamp,
            "roles": [role.to_dict() for role in roles],
        })

    @classmethod
    def from_dict(cls, raw: Any) -> "LaneWorkspace":
        value = _mapping(raw, "$")
        _strict(value, cls.FIELDS, "$")
        if value["schema_version"] != SCHEMA_VERSION:
            _fail("SCHEMA_VERSION", "$.schema_version", f"must be {SCHEMA_VERSION}")
        workspace_id = _uuid(value["workspace_id"], "$.workspace_id")
        program_id = _uuid(value["program_id"], "$.program_id")
        lane = _text(value["lane"], "$.lane")
        if lane not in LANES:
            _fail("LANE", "$.lane", "is unsupported")
        if workspace_id != _stable_uuid("workspace", program_id, lane):
            _fail("DETERMINISTIC_ID", "$.workspace_id", "does not match program and lane")
        created = _iso(_timestamp(value["created_at"], "$.created_at"))
        if not isinstance(value["roles"], list):
            _fail("TYPE", "$.roles", "must be an array")
        roles = tuple(PersistentRole.from_dict(item) for item in value["roles"])
        if len(roles) != 6:
            _fail("ROLE_COUNT", "$.roles", "lane manifest must contain exactly six identities")
        role_ids = [role.logical_role_id for role in roles]
        if len(role_ids) != len(set(role_ids)):
            _fail("DUPLICATE_ROLE", "$.roles", "logical role identities must be unique")
        names = {role.role_name for role in roles}
        if names != set(LANE_ROLE_NAMES[lane]):
            _fail("ROLE_SET", "$.roles", f"must contain the exact six {lane} cloud roles")
        for role in roles:
            if role.program_id != program_id or role.lane != lane:
                _fail("LANE_CROSSOVER", "$.roles", "every role must belong to this workspace")
        owners: list[tuple[str, str]] = []
        for role in roles:
            for owned_path in role.owned_output_paths:
                for other_role, other_path in owners:
                    if _overlap(owned_path, other_path):
                        _fail(
                            "SCOPE_OVERLAP", "$.roles",
                            f"{role.logical_role_id}:{owned_path} overlaps {other_role}:{other_path}",
                        )
                owners.append((role.logical_role_id, owned_path))
        return cls(workspace_id, program_id, lane, created, roles)

    @classmethod
    def from_json(cls, encoded: str) -> "LaneWorkspace":
        try:
            value = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise WorkspaceError("INPUT_JSON", "$", str(exc)) from exc
        return cls.from_dict(value)

    @property
    def coordinator(self) -> PersistentRole:
        return next(role for role in self.roles if role.role_name == "LANE-COORD")

    def role(self, logical_role_id: str) -> PersistentRole:
        matches = [role for role in self.roles if role.logical_role_id == logical_role_id]
        if len(matches) != 1:
            _fail("UNKNOWN_ROLE", "$.logical_role_id", "role is not in this lane manifest")
        return matches[0]

    def route_delegation(
        self, actor_role_id: str, request: DelegationRequest
    ) -> DelegationRequest:
        if actor_role_id != self.coordinator.logical_role_id:
            _fail("DELEGATION_AUTHORITY", "$.actor_role_id", "only LANE-COORD may route delegation")
        if request.routed_by_role_id is not None:
            _fail("DELEGATION_STATE", "$.request", "delegation request is already routed")
        if (request.program_id, request.lane) != (self.program_id, self.lane):
            _fail("LANE_CROSSOVER", "$.request", "delegation belongs to another workspace")
        self.role(request.requester_role_id)
        target = self.role(request.target_role_id)
        for path in request.read_paths:
            if not any(_within(path, allowed) for allowed in target.read_paths):
                _fail("READ_SCOPE", "$.request.read_paths", f"{path} is outside target read scope")
        for path in request.owned_output_paths:
            if not any(_within(path, allowed) for allowed in target.owned_output_paths):
                _fail("WRITE_SCOPE", "$.request.owned_output_paths", f"{path} is outside target ownership")
        return replace(request, routed_by_role_id=actor_role_id)

    def start_attempt(self, attempt: CloudAttempt, *, now: datetime) -> None:
        self.role(attempt.logical_role_id)
        attempt.assert_dispatchable(now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "workspace_id": self.workspace_id,
            "program_id": self.program_id,
            "lane": self.lane,
            "created_at": self.created_at,
            "roles": [role.to_dict() for role in self.roles],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def manifest_sha256(self) -> str:
        return canonical_sha256(self.to_dict())


@dataclass(frozen=True)
class LaneLaunchManifest:
    """Complete immutable launch contract for one six-role lane workspace."""

    launch_manifest_id: str
    workspace: LaneWorkspace
    input_bindings: tuple[tuple[str, str], ...]
    leases: tuple[Lease, ...]
    initial_attempts: tuple[CloudAttempt, ...]
    terminal_criteria: tuple[str, ...]

    FIELDS: ClassVar[set[str]] = {
        "schema_version",
        "launch_manifest_id",
        "workspace",
        "input_hashes",
        "leases",
        "initial_attempts",
        "terminal_criteria",
    }

    @classmethod
    def create(
        cls,
        workspace: LaneWorkspace,
        *,
        input_hashes: Mapping[str, str],
        leases: Sequence[Lease],
        initial_attempts: Sequence[CloudAttempt],
        terminal_criteria: Sequence[str],
        validation_time: datetime,
    ) -> "LaneLaunchManifest":
        sorted_leases = sorted(leases, key=lambda lease: lease.holder_role_id)
        sorted_attempts = sorted(
            initial_attempts, key=lambda attempt: attempt.logical_role_id
        )
        body = {
            "schema_version": SCHEMA_VERSION,
            "workspace": workspace.to_dict(),
            "input_hashes": dict(sorted(input_hashes.items())),
            "leases": [lease.to_dict() for lease in sorted_leases],
            "initial_attempts": [
                attempt.to_dict() for attempt in sorted_attempts
            ],
            "terminal_criteria": list(terminal_criteria),
        }
        return cls.from_dict(
            {
                "launch_manifest_id": _stable_uuid(
                    "lane-launch-manifest", canonical_sha256(body)
                ),
                **body,
            },
            validation_time=validation_time,
        )

    @classmethod
    def from_dict(
        cls, raw: Any, *, validation_time: datetime
    ) -> "LaneLaunchManifest":
        value = _mapping(raw, "$")
        _strict(value, cls.FIELDS, "$")
        if value["schema_version"] != SCHEMA_VERSION:
            _fail("SCHEMA_VERSION", "$.schema_version", f"must be {SCHEMA_VERSION}")
        launch_manifest_id = _uuid(
            value["launch_manifest_id"], "$.launch_manifest_id"
        )
        workspace = LaneWorkspace.from_dict(value["workspace"])

        raw_inputs = _mapping(value["input_hashes"], "$.input_hashes")
        if not raw_inputs:
            _fail("INPUT_HASH", "$.input_hashes", "must not be empty")
        bindings: list[tuple[str, str]] = []
        for raw_path, raw_digest in raw_inputs.items():
            input_path = _path(raw_path, "$.input_hashes.<key>")
            digest = _sha(raw_digest, f"$.input_hashes.{input_path}")
            marker = _lane_marker(input_path)
            if marker is not None and marker != workspace.lane:
                _fail(
                    "LANE_CROSSOVER",
                    f"$.input_hashes.{input_path}",
                    f"{input_path} belongs to {marker}",
                )
            if not any(
                _within(input_path, read_scope)
                for role in workspace.roles
                for read_scope in role.read_paths
            ):
                _fail(
                    "INPUT_SCOPE",
                    f"$.input_hashes.{input_path}",
                    "is not permitted by any role read scope",
                )
            bindings.append((input_path, digest))
        bindings.sort()

        leases = cls._parse_role_records(
            value["leases"],
            path="$.leases",
            parser=Lease.from_dict,
            role_id=lambda lease: lease.holder_role_id,
            expected_roles=workspace.roles,
        )
        attempts = cls._parse_role_records(
            value["initial_attempts"],
            path="$.initial_attempts",
            parser=CloudAttempt.from_dict,
            role_id=lambda attempt: attempt.logical_role_id,
            expected_roles=workspace.roles,
        )
        lease_by_role = {lease.holder_role_id: lease for lease in leases}
        for index, attempt in enumerate(attempts):
            role_id = attempt.logical_role_id
            if attempt.lease != lease_by_role[role_id]:
                _fail(
                    "ROLE_MISMATCH",
                    f"$.initial_attempts[{index}].lease",
                    "must equal the role's single launch lease",
                )
            continuation = attempt.continuation
            if (
                continuation.continuity_mode != "AUTHOR_RESUME"
                or continuation.predecessor_attempt_id is not None
                or continuation.predecessor_runtime_session_id is not None
                or continuation.predecessor_report_sha256 is not None
            ):
                _fail(
                    "INITIAL_CONTINUITY",
                    f"$.initial_attempts[{index}].continuation",
                    "initial attempt predecessor links must be null and mode AUTHOR_RESUME",
                )
            if attempt.state != "PENDING":
                _fail(
                    "ATTEMPT_STATE",
                    f"$.initial_attempts[{index}].state",
                    "initial launch attempt must be PENDING",
                )
            if (
                attempt.cost_usd is not None
                or attempt.cost_evidence_sha256 is not None
            ):
                _fail(
                    "INITIAL_COST",
                    f"$.initial_attempts[{index}].cost_usd",
                    "initial cloud cost must remain null until observed",
                )

        if not isinstance(value["terminal_criteria"], list):
            _fail("TYPE", "$.terminal_criteria", "must be an array")
        criteria = tuple(
            _text(item, f"$.terminal_criteria[{index}]")
            for index, item in enumerate(value["terminal_criteria"])
        )
        if not criteria:
            _fail(
                "TERMINAL_CRITERIA",
                "$.terminal_criteria",
                "must contain at least one criterion",
            )
        if len(criteria) != len(set(criteria)):
            _fail(
                "TERMINAL_CRITERIA",
                "$.terminal_criteria",
                "must not contain duplicate criteria",
            )

        normalized_leases = tuple(
            sorted(leases, key=lambda lease: lease.holder_role_id)
        )
        normalized_attempts = tuple(
            sorted(attempts, key=lambda attempt: attempt.logical_role_id)
        )
        normalized_body = {
            "schema_version": SCHEMA_VERSION,
            "workspace": workspace.to_dict(),
            "input_hashes": dict(bindings),
            "leases": [lease.to_dict() for lease in normalized_leases],
            "initial_attempts": [
                attempt.to_dict() for attempt in normalized_attempts
            ],
            "terminal_criteria": list(criteria),
        }
        expected_id = _stable_uuid(
            "lane-launch-manifest", canonical_sha256(normalized_body)
        )
        if launch_manifest_id != expected_id:
            _fail(
                "DETERMINISTIC_ID",
                "$.launch_manifest_id",
                "does not match the normalized launch contract",
            )
        result = cls(
            launch_manifest_id,
            workspace,
            tuple(bindings),
            normalized_leases,
            normalized_attempts,
            criteria,
        )
        result.validate_at(validation_time)
        return result

    @staticmethod
    def _parse_role_records(
        raw: Any,
        *,
        path: str,
        parser: Any,
        role_id: Any,
        expected_roles: Sequence[PersistentRole],
    ) -> tuple[Any, ...]:
        if not isinstance(raw, list):
            _fail("TYPE", path, "must be an array")
        records = tuple(parser(item) for item in raw)
        observed = [role_id(record) for record in records]
        if len(observed) != len(set(observed)):
            _fail("DUPLICATE_ROLE", path, "must contain exactly one record per role")
        expected = {role.logical_role_id for role in expected_roles}
        missing = sorted(expected - set(observed))
        unknown = sorted(set(observed) - expected)
        if missing:
            _fail("MISSING_ROLE", path, f"missing role(s): {', '.join(missing)}")
        if unknown:
            _fail("UNKNOWN_ROLE", path, f"unknown role(s): {', '.join(unknown)}")
        if len(records) != len(expected):
            _fail("ROLE_COUNT", path, "must contain exactly six role records")
        return records

    @classmethod
    def from_json(
        cls, encoded: str, *, validation_time: datetime
    ) -> "LaneLaunchManifest":
        try:
            value = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise WorkspaceError("INPUT_JSON", "$", str(exc)) from exc
        return cls.from_dict(value, validation_time=validation_time)

    @property
    def input_hashes(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self.input_bindings))

    def validate_at(self, validation_time: datetime) -> None:
        for index, lease in enumerate(self.leases):
            try:
                lease.assert_active(validation_time)
            except WorkspaceError as exc:
                raise WorkspaceError(
                    exc.code, f"$.leases[{index}]", exc.message
                ) from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "launch_manifest_id": self.launch_manifest_id,
            "workspace": self.workspace.to_dict(),
            "input_hashes": dict(self.input_bindings),
            "leases": [lease.to_dict() for lease in self.leases],
            "initial_attempts": [
                attempt.to_dict() for attempt in self.initial_attempts
            ],
            "terminal_criteria": list(self.terminal_criteria),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @property
    def manifest_sha256(self) -> str:
        return canonical_sha256(self.to_dict())
