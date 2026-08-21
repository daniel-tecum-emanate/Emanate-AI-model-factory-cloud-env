"""Fail-closed contracts for the PTC/Grand Steel model-training swarm.

This module is intentionally stdlib-only and side-effect free except for the CLI's
explicit file reads.  A dispatcher must call :func:`validate_work_order` before it
creates a prompt, starts a process, or spends money.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import uuid
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence


SCHEMA_VERSION = 1
LANES = frozenset({"ptc", "grand-steel"})
ROLES = frozenset(
    {
        "LANE-COORD",
        "KEEPER",
        "DATA-ARCH",
        "CORPUS-ARCH",
        "EVAL-DEV",
        "FINAL-EVAL-CUSTODIAN",
        "SERVE-ARCH",
        "REDTEAM",
    }
)
WAVES = frozenset(f"W{i}" for i in range(9))
CONTINUITY_MODES = frozenset({"AUTHOR_RESUME", "SUCCESSOR_RECONSTRUCTION"})
ATTEMPT_STATES = frozenset(
    {"PENDING", "RUNNING", "COMPLETED", "BLOCKED", "TIMED_OUT", "FAILED", "UNRESUMABLE"}
)
TERMINAL_ATTEMPT_STATES = ATTEMPT_STATES - {"PENDING", "RUNNING"}
LEGAL_ATTEMPT_TRANSITIONS = {
    "PENDING": frozenset({"RUNNING"}),
    "RUNNING": TERMINAL_ATTEMPT_STATES,
}
EPISTEMIC_STATUSES = frozenset({"supported", "contradicted", "contested", "unsupported"})
GATE_EFFECTS = frozenset({"block", "degrade-to-suspect", "informational", "none"})
CLAIM_TYPES = frozenset({"FACT", "INFERENCE", "PROPOSAL"})
TRUST_LEVELS = frozenset({"raw", "deterministic", "blind-specialist", "synthesis", "prompt"})
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
FINAL_EVAL_MARKERS = (
    "final-eval",
    "final_eval",
    "sealed-final",
    "heldout/",
    "/heldout",
    "grader-prompt",
    "grader_prompt",
    "rubric",
    "final-output",
    "final_output",
)


class ProtocolError(ValueError):
    """A stable, machine-readable protocol validation failure."""

    def __init__(self, code: str, path: str, message: str):
        super().__init__(message)
        self.code = code
        self.path = path
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


def _fail(code: str, path: str, message: str) -> None:
    raise ProtocolError(code, path, message)


def _object(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("TYPE", path, "must be an object")
    return value


def _strict_fields(
    value: Mapping[str, Any], *, required: set[str], optional: set[str] | None = None, path: str = "$"
) -> None:
    optional = optional or set()
    missing = sorted(required - set(value))
    if missing:
        _fail("MISSING_FIELD", path, f"missing required field(s): {', '.join(missing)}")
    unknown = sorted(set(value) - required - optional)
    if unknown:
        _fail("UNKNOWN_FIELD", path, f"unknown field(s): {', '.join(unknown)}")


def _string(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        _fail("TYPE", path, "must be a non-empty string")
    return value


def _integer(value: Any, path: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("TYPE", path, "must be an integer")
    if minimum is not None and value < minimum:
        _fail("RANGE", path, f"must be >= {minimum}")
    return value


def _number(value: Any, path: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        _fail("TYPE", path, "must be a finite number")
    result = float(value)
    if minimum is not None and result < minimum:
        _fail("RANGE", path, f"must be >= {minimum}")
    return result


def _enum(value: Any, allowed: frozenset[str], path: str) -> str:
    text = _string(value, path)
    if text not in allowed:
        _fail("ENUM", path, f"must be one of: {', '.join(sorted(allowed))}")
    return text


def _uuid(value: Any, path: str) -> str:
    text = _string(value, path)
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError):
        _fail("UUID", path, "must be a UUID")
    if str(parsed) != text.lower():
        _fail("UUID", path, "must use canonical UUID form")
    return text


def _sha256(value: Any, path: str) -> str:
    text = _string(value, path)
    if not HEX64_RE.fullmatch(text):
        _fail("SHA256", path, "must be 64 lowercase hexadecimal characters")
    return text


def _timestamp(value: Any, path: str) -> datetime:
    text = _string(value, path)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        _fail("TIMESTAMP", path, "must be an ISO-8601 timestamp")
    if parsed.tzinfo is None:
        _fail("TIMESTAMP", path, "must include a timezone")
    return parsed


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the SHA256 of canonical JSON (sorted keys, no insignificant space)."""

    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _path(value: Any, path: str) -> str:
    text = _string(value, path)
    if "\\" in text:
        _fail("PATH", path, "must use '/' separators")
    candidate = PurePosixPath(text)
    if candidate.is_absolute() or not candidate.parts:
        _fail("PATH", path, "must be a non-empty repository-relative path")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        _fail("PATH", path, "must be normalized and may not contain '.' or '..'")
    normalized = candidate.as_posix()
    if normalized != text.rstrip("/"):
        _fail("PATH", path, "must be normalized")
    return normalized


def _path_is_within(path: str, parent: str) -> bool:
    path_parts = PurePosixPath(path).parts
    parent_parts = PurePosixPath(parent).parts
    return len(path_parts) >= len(parent_parts) and path_parts[: len(parent_parts)] == parent_parts


def _paths_overlap(left: str, right: str) -> bool:
    return _path_is_within(left, right) or _path_is_within(right, left)


def _validate_path_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list):
        _fail("TYPE", path, "must be an array")
    result = [_path(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        _fail("DUPLICATE_PATH", path, "must not contain duplicate paths")
    return result


def _lane_marker(path: str) -> str | None:
    lowered = f"/{path.lower()}/"
    if "/grand-steel/" in lowered:
        return "grand-steel"
    if "/ptc-steel/" in lowered or "/ptc/" in lowered:
        return "ptc"
    return None


def _contains_final_eval(path: str) -> bool:
    lowered = path.lower()
    return any(marker in lowered for marker in FINAL_EVAL_MARKERS)


def _validate_visibility_manifest(value: Any) -> dict[str, str]:
    if not isinstance(value, list) or not value:
        _fail("TYPE", "$.visibility_manifest", "must be a non-empty array")
    result: dict[str, str] = {}
    for index, raw_entry in enumerate(value):
        entry_path = f"$.visibility_manifest[{index}]"
        entry = _object(raw_entry, entry_path)
        _strict_fields(entry, required={"path", "sha256"}, path=entry_path)
        artifact_path = _path(entry["path"], f"{entry_path}.path")
        digest = _sha256(entry["sha256"], f"{entry_path}.sha256")
        if artifact_path in result:
            _fail("DUPLICATE_PATH", entry_path, f"duplicate manifest path: {artifact_path}")
        result[artifact_path] = digest
    return result


def _validate_lesson_manifest(
    value: Any,
    *,
    lane: str,
    role: str,
    read_paths: tuple[str, ...],
    visibility: Mapping[str, str],
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        _fail("LESSON_MANIFEST", "$.lesson_manifest", "must be a non-empty array")
    lesson_ids = []
    seen = set()
    for index, raw_lesson in enumerate(value):
        path = f"$.lesson_manifest[{index}]"
        lesson = _object(raw_lesson, path)
        _strict_fields(
            lesson,
            required={
                "lesson_id",
                "claim_type",
                "failure_mode",
                "mechanical_check",
                "gate_effect",
                "source_hashes",
                "owner_roles",
                "required_agreement",
                "supersedes",
            },
            path=path,
        )
        lesson_id = _string(lesson["lesson_id"], f"{path}.lesson_id")
        if not ID_RE.fullmatch(lesson_id) or not lesson_id.lower().startswith(f"{lane.lower()}-"):
            _fail(
                "LESSON_ID",
                f"{path}.lesson_id",
                "must use the current lane prefix and closed identifier characters",
            )
        if lesson_id in seen:
            _fail("LESSON_ID", f"{path}.lesson_id", "must be unique in the manifest")
        seen.add(lesson_id)
        lesson_ids.append(lesson_id)
        _enum(lesson["claim_type"], CLAIM_TYPES, f"{path}.claim_type")
        _string(lesson["failure_mode"], f"{path}.failure_mode")
        _string(lesson["mechanical_check"], f"{path}.mechanical_check")
        _enum(lesson["gate_effect"], GATE_EFFECTS, f"{path}.gate_effect")
        source_hashes = _object(lesson["source_hashes"], f"{path}.source_hashes")
        if not source_hashes:
            _fail("LESSON_SOURCE", f"{path}.source_hashes", "must not be empty")
        for source, digest in source_hashes.items():
            normalized = _path(source, f"{path}.source_hashes.<key>")
            expected = _sha256(digest, f"{path}.source_hashes.{source}")
            if normalized not in read_paths:
                _fail("LESSON_SOURCE", f"{path}.source_hashes", f"{normalized} is not a read_path")
            if visibility.get(normalized) != expected:
                _fail(
                    "LESSON_SOURCE",
                    f"{path}.source_hashes.{source}",
                    "must equal the work-order visibility hash",
                )
        owners = lesson["owner_roles"]
        if not isinstance(owners, list) or not owners:
            _fail("LESSON_OWNER", f"{path}.owner_roles", "must be a non-empty array")
        normalized_owners = [
            _enum(owner, ROLES, f"{path}.owner_roles[{owner_index}]")
            for owner_index, owner in enumerate(owners)
        ]
        if role not in normalized_owners:
            _fail("LESSON_OWNER", f"{path}.owner_roles", "must include the dispatched role")
        agreement = _integer(
            lesson["required_agreement"], f"{path}.required_agreement", minimum=1
        )
        if agreement not in (1, 2) or agreement > len(set(normalized_owners)):
            _fail(
                "LESSON_AGREEMENT",
                f"{path}.required_agreement",
                "must be 1 or 2 and no greater than the independent owner count",
            )
        supersedes = lesson["supersedes"]
        if not isinstance(supersedes, list):
            _fail("LESSON_SUPERSEDES", f"{path}.supersedes", "must be an array")
        for superseded_index, claim_id in enumerate(supersedes):
            _string(claim_id, f"{path}.supersedes[{superseded_index}]")
    return tuple(lesson_ids)


def _validate_partition_receipt(value: Any) -> None:
    receipt = _object(value, "$.partition_check_receipt")
    _strict_fields(
        receipt,
        required={"status", "command", "checked_at", "receipt_sha256", "owned_paths"},
        path="$.partition_check_receipt",
    )
    if receipt["status"] != "pass":
        _fail("PARTITION_CHECK", "$.partition_check_receipt.status", "must be exactly 'pass'")
    command = _string(receipt["command"], "$.partition_check_receipt.command")
    if "check_partition_overlap.sh" not in command:
        _fail(
            "PARTITION_CHECK",
            "$.partition_check_receipt.command",
            "must identify check_partition_overlap.sh",
        )
    _timestamp(receipt["checked_at"], "$.partition_check_receipt.checked_at")
    _sha256(receipt["receipt_sha256"], "$.partition_check_receipt.receipt_sha256")
    _validate_path_list(receipt["owned_paths"], "$.partition_check_receipt.owned_paths")


def _validate_heartbeat(value: Any) -> None:
    heartbeat = _object(value, "$.heartbeat")
    _strict_fields(
        heartbeat,
        required={
            "row_id",
            "zone",
            "lease_seconds",
            "sequence_interval_seconds",
            "seq",
            "updated_at",
            "lease_expires_at",
        },
        path="$.heartbeat",
    )
    _string(heartbeat["row_id"], "$.heartbeat.row_id")
    if heartbeat["zone"] != "factory-automation":
        _fail("HEARTBEAT", "$.heartbeat.zone", "must be 'factory-automation'")
    lease_seconds = _integer(heartbeat["lease_seconds"], "$.heartbeat.lease_seconds", minimum=1)
    if lease_seconds != 1800:
        _fail("HEARTBEAT", "$.heartbeat.lease_seconds", "must be exactly 1800")
    sequence_interval = _integer(
        heartbeat["sequence_interval_seconds"], "$.heartbeat.sequence_interval_seconds", minimum=1
    )
    if sequence_interval > 600:
        _fail("HEARTBEAT", "$.heartbeat.sequence_interval_seconds", "must be <= 600")
    _integer(heartbeat["seq"], "$.heartbeat.seq", minimum=0)
    updated = _timestamp(heartbeat["updated_at"], "$.heartbeat.updated_at")
    expires = _timestamp(heartbeat["lease_expires_at"], "$.heartbeat.lease_expires_at")
    if (expires - updated).total_seconds() != lease_seconds:
        _fail("HEARTBEAT", "$.heartbeat.lease_expires_at", "must be exactly one lease after updated_at")


def estimate_tokens(text: str) -> int:
    """Conservative deterministic token estimate: ceil(UTF-8 characters / 4)."""

    return math.ceil(len(text) / 4)


def validate_state_capsule(capsule: Any) -> dict[str, int]:
    """Validate a bounded capsule and return its deterministic size measurements."""

    text = _string(capsule, "$.state_capsule", allow_empty=True)
    line_count = 0 if text == "" else len(text.splitlines())
    token_estimate = estimate_tokens(text)
    if line_count > 150:
        _fail("CAPSULE_LINES", "$.state_capsule", "must contain <= 150 lines")
    if token_estimate > 4000:
        _fail("CAPSULE_TOKENS", "$.state_capsule", "must have a <= 4000 token estimate")
    return {"line_count": line_count, "token_estimate": token_estimate}


def validate_continuity(value: Any) -> None:
    record = _object(value, "$")
    required = {
        "execution_attempt_id",
        "predecessor_attempt_id",
        "runtime_session_id",
        "predecessor_runtime_session_id",
        "continuity_mode",
    }
    _strict_fields(record, required=required)
    current_attempt = _uuid(record["execution_attempt_id"], "$.execution_attempt_id")
    predecessor = record["predecessor_attempt_id"]
    predecessor_session = record["predecessor_runtime_session_id"]
    session = record["runtime_session_id"]
    mode = _enum(record["continuity_mode"], CONTINUITY_MODES, "$.continuity_mode")
    if session is not None:
        _string(session, "$.runtime_session_id")
    if predecessor is not None:
        predecessor = _uuid(predecessor, "$.predecessor_attempt_id")
        if predecessor == current_attempt:
            _fail("CONTINUITY", "$.predecessor_attempt_id", "must differ from the current attempt")
    if predecessor_session is not None:
        _string(predecessor_session, "$.predecessor_runtime_session_id")
    if predecessor is None:
        if mode != "AUTHOR_RESUME" or predecessor_session is not None:
            _fail("CONTINUITY", "$.continuity_mode", "an initial attempt must use AUTHOR_RESUME")
        return
    if predecessor_session is None:
        _fail("CONTINUITY", "$", "continued attempts require predecessor_runtime_session_id")
    if mode == "AUTHOR_RESUME" and session != predecessor_session:
        _fail("CONTINUITY", "$.continuity_mode", "AUTHOR_RESUME requires the same runtime session identity")
    if mode == "SUCCESSOR_RECONSTRUCTION" and session is not None and session == predecessor_session:
        _fail(
            "CONTINUITY",
            "$.continuity_mode",
            "SUCCESSOR_RECONSTRUCTION requires a distinct runtime session identity",
        )


def validate_attempt_transition(old_state: Any, new_state: Any) -> None:
    """Reject every transition not explicitly present in the program state graph."""

    old = _enum(old_state, ATTEMPT_STATES, "$.old_state")
    new = _enum(new_state, ATTEMPT_STATES, "$.new_state")
    if new not in LEGAL_ATTEMPT_TRANSITIONS.get(old, frozenset()):
        _fail("ILLEGAL_TRANSITION", "$", f"attempt transition {old} -> {new} is not legal")


def _validate_challenger(value: Any, path: str) -> tuple[str, tuple[str, ...]]:
    challenger = _object(value, path)
    _strict_fields(
        challenger,
        required={"claim_id", "method", "evidence_chain", "source_hashes"},
        path=path,
    )
    _string(challenger["claim_id"], f"{path}.claim_id")
    method = _string(challenger["method"], f"{path}.method")
    chain = challenger["evidence_chain"]
    if not isinstance(chain, list) or not chain:
        _fail("TYPE", f"{path}.evidence_chain", "must be a non-empty array")
    normalized_chain = tuple(_string(item, f"{path}.evidence_chain[{i}]") for i, item in enumerate(chain))
    hashes = challenger["source_hashes"]
    if not isinstance(hashes, list) or not hashes:
        _fail("TYPE", f"{path}.source_hashes", "must be a non-empty array")
    for index, digest in enumerate(hashes):
        _sha256(digest, f"{path}.source_hashes[{index}]")
    return method, normalized_chain


def validate_claim(claim: Any) -> None:
    """Validate one complete, provenance-bearing claim envelope."""

    value = _object(claim, "$")
    required = {
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
        "method",
        "evidence_chain",
        "epistemic_status",
        "gate_effect",
        "load_bearing",
        "polarity",
        "finding_basis",
        "challenger_evidence",
    }
    _strict_fields(value, required=required)
    claim_id = _string(value["claim_id"], "$.claim_id")
    if not ID_RE.fullmatch(claim_id):
        _fail("CLAIM_ID", "$.claim_id", "contains unsupported characters")
    _enum(value["claim_type"], CLAIM_TYPES, "$.claim_type")
    _string(value["metric_version"], "$.metric_version")
    source_hashes = value["source_hashes"]
    if not isinstance(source_hashes, list) or not source_hashes:
        _fail("TYPE", "$.source_hashes", "must be a non-empty array")
    for index, digest in enumerate(source_hashes):
        _sha256(digest, f"$.source_hashes[{index}]")
    _timestamp(value["observed_at"], "$.observed_at")
    _string(value["reproduction_command"], "$.reproduction_command")
    for field in ("numerator", "denominator"):
        if value[field] is not None:
            _number(value[field], f"$.{field}", minimum=0)
    if value["numerator"] is not None and value["denominator"] is None:
        _fail("CLAIM_METRIC", "$.denominator", "is required when numerator is present")
    if (
        value["numerator"] is not None
        and value["denominator"] is not None
        and float(value["numerator"]) > float(value["denominator"])
    ):
        _fail("CLAIM_METRIC", "$.numerator", "may not exceed denominator")
    if not isinstance(value["included_ids"], list):
        _fail("TYPE", "$.included_ids", "must be an array")
    for index, item in enumerate(value["included_ids"]):
        _string(item, f"$.included_ids[{index}]")
    arm_hashes = _object(value["arm_hashes"], "$.arm_hashes")
    for arm, digest in arm_hashes.items():
        _string(arm, "$.arm_hashes.<key>")
        _sha256(digest, f"$.arm_hashes.{arm}")
    _enum(value["trust"], TRUST_LEVELS, "$.trust")
    method = _string(value["method"], "$.method")
    evidence_chain = value["evidence_chain"]
    if not isinstance(evidence_chain, list) or not evidence_chain:
        _fail("TYPE", "$.evidence_chain", "must be a non-empty array")
    normalized_chain = tuple(
        _string(item, f"$.evidence_chain[{index}]") for index, item in enumerate(evidence_chain)
    )
    epistemic = _enum(value["epistemic_status"], EPISTEMIC_STATUSES, "$.epistemic_status")
    gate_effect = _enum(value["gate_effect"], GATE_EFFECTS, "$.gate_effect")
    if not isinstance(value["load_bearing"], bool):
        _fail("TYPE", "$.load_bearing", "must be a boolean")
    polarity = _enum(value["polarity"], frozenset({"positive", "negative", "neutral"}), "$.polarity")
    finding_basis = _enum(
        value["finding_basis"],
        frozenset({"deterministic-failure", "reproducible-critical", "specialist", "none"}),
        "$.finding_basis",
    )
    if gate_effect == "block":
        if finding_basis not in {"deterministic-failure", "reproducible-critical"}:
            _fail(
                "UNSUPPORTED_BLOCK",
                "$.gate_effect",
                "only deterministic failure or reproducible critical evidence may block",
            )
        if epistemic == "unsupported":
            _fail("UNSUPPORTED_BLOCK", "$.epistemic_status", "unsupported dissent may not block")
    challengers = value["challenger_evidence"]
    if not isinstance(challengers, list):
        _fail("TYPE", "$.challenger_evidence", "must be an array")
    validated = [
        _validate_challenger(challenger, f"$.challenger_evidence[{index}]")
        for index, challenger in enumerate(challengers)
    ]
    if value["load_bearing"] and polarity == "positive":
        distinct = any(ch_method != method and ch_chain != normalized_chain for ch_method, ch_chain in validated)
        if not distinct:
            _fail(
                "MISSING_CHALLENGER",
                "$.challenger_evidence",
                "a positive load-bearing claim requires methodologically distinct challenger evidence",
            )


def validate_claim_envelope(value: Any) -> None:
    envelope = _object(value, "$")
    _strict_fields(envelope, required={"schema_version", "claims"})
    if envelope["schema_version"] != SCHEMA_VERSION:
        _fail("SCHEMA_VERSION", "$.schema_version", f"must be {SCHEMA_VERSION}")
    claims = envelope["claims"]
    if not isinstance(claims, list) or not claims:
        _fail("TYPE", "$.claims", "must be a non-empty array")
    seen: set[str] = set()
    for index, claim in enumerate(claims):
        try:
            validate_claim(claim)
        except ProtocolError as exc:
            raise ProtocolError(exc.code, f"$.claims[{index}]{exc.path[1:]}", exc.message) from exc
        claim_id = claim["claim_id"]
        if claim_id in seen:
            _fail("DUPLICATE_CLAIM", f"$.claims[{index}].claim_id", f"duplicate claim ID: {claim_id}")
        seen.add(claim_id)


def _validate_signature(value: Any, path: str) -> None:
    signature = _object(value, path)
    _strict_fields(signature, required={"signer", "signed_at", "signature_sha256"}, path=path)
    _string(signature["signer"], f"{path}.signer")
    _timestamp(signature["signed_at"], f"{path}.signed_at")
    _sha256(signature["signature_sha256"], f"{path}.signature_sha256")


def validate_final_eval_contract(value: Any) -> None:
    contract = _object(value, "$")
    _strict_fields(
        contract,
        required={
            "schema_version",
            "contract_id",
            "evaluation_version",
            "protocol_sha256",
            "sealed_frame_sha256",
            "signed_authority",
            "candidate_limit",
            "looks_used",
            "candidate_ids",
            "stopping_rule",
        },
    )
    if contract["schema_version"] != SCHEMA_VERSION:
        _fail("SCHEMA_VERSION", "$.schema_version", f"must be {SCHEMA_VERSION}")
    _string(contract["contract_id"], "$.contract_id")
    _string(contract["evaluation_version"], "$.evaluation_version")
    _sha256(contract["protocol_sha256"], "$.protocol_sha256")
    _sha256(contract["sealed_frame_sha256"], "$.sealed_frame_sha256")
    _validate_signature(contract["signed_authority"], "$.signed_authority")
    if _integer(contract["candidate_limit"], "$.candidate_limit", minimum=1) != 1:
        _fail("OPTIONAL_STOPPING", "$.candidate_limit", "final evaluation permits exactly one candidate")
    looks_used = _integer(contract["looks_used"], "$.looks_used", minimum=0)
    if looks_used > 1:
        _fail("OPTIONAL_STOPPING", "$.looks_used", "at most one final-evaluation look is permitted")
    candidate_ids = contract["candidate_ids"]
    if not isinstance(candidate_ids, list):
        _fail("TYPE", "$.candidate_ids", "must be an array")
    if len(candidate_ids) != looks_used or len(candidate_ids) > 1:
        _fail("OPTIONAL_STOPPING", "$.candidate_ids", "must record exactly the candidates already viewed")
    for index, candidate in enumerate(candidate_ids):
        _string(candidate, f"$.candidate_ids[{index}]")
    if contract["stopping_rule"] != "single-pre-registered-candidate":
        _fail(
            "OPTIONAL_STOPPING",
            "$.stopping_rule",
            "must be 'single-pre-registered-candidate'; outcome-dependent stopping is forbidden",
        )


def validate_shadow_window_contract(value: Any) -> None:
    contract = _object(value, "$")
    _strict_fields(
        contract,
        required={
            "schema_version",
            "contract_id",
            "protocol_sha256",
            "signed_authority",
            "calendar_start",
            "calendar_end",
            "exposure_definition",
            "minimum_valid_nights",
            "maximum_invalid_rate",
            "intent_to_observe_denominator",
            "extension_policy",
            "stopping_rule",
        },
    )
    if contract["schema_version"] != SCHEMA_VERSION:
        _fail("SCHEMA_VERSION", "$.schema_version", f"must be {SCHEMA_VERSION}")
    _string(contract["contract_id"], "$.contract_id")
    _sha256(contract["protocol_sha256"], "$.protocol_sha256")
    _validate_signature(contract["signed_authority"], "$.signed_authority")
    try:
        start = date.fromisoformat(_string(contract["calendar_start"], "$.calendar_start"))
        end = date.fromisoformat(_string(contract["calendar_end"], "$.calendar_end"))
    except ValueError:
        _fail("DATE", "$", "calendar_start and calendar_end must be ISO dates")
    if end < start:
        _fail("SHADOW_WINDOW", "$.calendar_end", "must be on or after calendar_start")
    _string(contract["exposure_definition"], "$.exposure_definition")
    minimum_nights = _integer(contract["minimum_valid_nights"], "$.minimum_valid_nights", minimum=1)
    calendar_nights = (end - start).days + 1
    if minimum_nights > calendar_nights:
        _fail("SHADOW_WINDOW", "$.minimum_valid_nights", "may not exceed the fixed calendar window")
    maximum_invalid_rate = _number(
        contract["maximum_invalid_rate"], "$.maximum_invalid_rate", minimum=0
    )
    if maximum_invalid_rate > 1:
        _fail("RANGE", "$.maximum_invalid_rate", "must be <= 1")
    denominator = _integer(
        contract["intent_to_observe_denominator"], "$.intent_to_observe_denominator", minimum=1
    )
    if denominator != calendar_nights:
        _fail(
            "OPTIONAL_STOPPING",
            "$.intent_to_observe_denominator",
            "must equal every night in the frozen calendar window",
        )
    if contract["extension_policy"] != "new-signed-protocol-only":
        _fail(
            "OPTIONAL_STOPPING",
            "$.extension_policy",
            "window extension requires a new signed protocol",
        )
    if contract["stopping_rule"] != "observe-entire-fixed-window":
        _fail(
            "OPTIONAL_STOPPING",
            "$.stopping_rule",
            "must observe the entire fixed window, including adverse and invalid nights",
        )


WORK_ORDER_REQUIRED_FIELDS = {
    "schema_version",
    "program_id",
    "lane",
    "wave",
    "logical_role_id",
    "execution_attempt_id",
    "predecessor_attempt_id",
    "runtime_session_id",
    "predecessor_runtime_session_id",
    "continuity_mode",
    "factory_run_id",
    "task_id",
    "max_cost_usd",
    "zone",
    "read_paths",
    "write_paths",
    "owned_output_paths",
    "forbidden_paths",
    "visibility_manifest",
    "input_hashes",
    "partition_check_receipt",
    "heartbeat",
    "state_capsule",
}


def validate_work_order(
    work_order: Any, *, root: str | Path | None = None, verify_files: bool = True
) -> dict[str, Any]:
    """Validate a complete work order before dispatch.

    ``root`` is the repository root used to verify declared input bytes.  File
    verification is on by default; callers must opt out explicitly for schema-only
    validation.
    """

    value = _object(work_order, "$")
    _strict_fields(
        value,
        required=WORK_ORDER_REQUIRED_FIELDS,
        optional={"product_target_run_id", "lesson_manifest"},
    )
    if value["schema_version"] != SCHEMA_VERSION:
        _fail("SCHEMA_VERSION", "$.schema_version", f"must be {SCHEMA_VERSION}")
    program_id = _uuid(value["program_id"], "$.program_id")
    lane = _enum(value["lane"], LANES, "$.lane")
    _enum(value["wave"], WAVES, "$.wave")
    logical_role_id = _string(value["logical_role_id"], "$.logical_role_id")
    try:
        role_program, role_lane, role = logical_role_id.split("/")
    except ValueError:
        _fail("LOGICAL_ROLE_ID", "$.logical_role_id", "must be <program_id>/<lane>/<role>")
    if role_program != program_id or role_lane != lane or role not in ROLES:
        _fail(
            "LOGICAL_ROLE_ID",
            "$.logical_role_id",
            "program, lane, and role must exactly match the work order and closed role vocabulary",
        )
    continuity = {
        field: value[field]
        for field in (
            "execution_attempt_id",
            "predecessor_attempt_id",
            "runtime_session_id",
            "predecessor_runtime_session_id",
            "continuity_mode",
        )
    }
    validate_continuity(continuity)
    _uuid(value["factory_run_id"], "$.factory_run_id")
    if "product_target_run_id" in value:
        _uuid(value["product_target_run_id"], "$.product_target_run_id")
    task_id = _string(value["task_id"], "$.task_id")
    if not ID_RE.fullmatch(task_id):
        _fail("TASK_ID", "$.task_id", "contains unsupported characters")
    _number(value["max_cost_usd"], "$.max_cost_usd", minimum=0)
    if value["zone"] != "factory-automation":
        _fail("ZONE", "$.zone", "must be 'factory-automation'")
    paths = {
        name: _validate_path_list(value[name], f"$.{name}")
        for name in ("read_paths", "write_paths", "owned_output_paths", "forbidden_paths")
    }
    if not paths["read_paths"]:
        _fail("PATH", "$.read_paths", "must not be empty")
    if set(paths["write_paths"]) != set(paths["owned_output_paths"]):
        _fail(
            "OWNERSHIP",
            "$.owned_output_paths",
            "write_paths and owned_output_paths must be identical; shared writes are forbidden",
        )
    for index, left in enumerate(paths["owned_output_paths"]):
        for right in paths["owned_output_paths"][index + 1 :]:
            if _paths_overlap(left, right):
                _fail("OWNERSHIP", "$.owned_output_paths", f"overlapping owned paths: {left} and {right}")
    for list_name in ("read_paths", "write_paths", "owned_output_paths"):
        for artifact_path in paths[list_name]:
            marker = _lane_marker(artifact_path)
            if marker is not None and marker != lane:
                _fail(
                    "LANE_CROSSOVER",
                    f"$.{list_name}",
                    f"{artifact_path} belongs to {marker}, not {lane}",
                )
            if any(_paths_overlap(artifact_path, forbidden) for forbidden in paths["forbidden_paths"]):
                _fail(
                    "FORBIDDEN_PATH",
                    f"$.{list_name}",
                    f"{artifact_path} overlaps forbidden path {next(f for f in paths['forbidden_paths'] if _paths_overlap(artifact_path, f))}",
                )
    if role != "FINAL-EVAL-CUSTODIAN":
        for list_name in ("read_paths", "write_paths", "owned_output_paths"):
            leaked = next((item for item in paths[list_name] if _contains_final_eval(item)), None)
            if leaked:
                _fail(
                    "FINAL_EVAL_LEAKAGE",
                    f"$.{list_name}",
                    f"{role} may not receive final-evaluation material: {leaked}",
                )
    manifest = _validate_visibility_manifest(value["visibility_manifest"])
    for artifact_path in paths["read_paths"]:
        if artifact_path not in manifest:
            _fail(
                "VISIBILITY",
                "$.read_paths",
                f"{artifact_path} is not present in the visibility manifest",
            )
    for artifact_path in manifest:
        marker = _lane_marker(artifact_path)
        if marker is not None and marker != lane:
            _fail("LANE_CROSSOVER", "$.visibility_manifest", f"{artifact_path} belongs to {marker}")
        if role != "FINAL-EVAL-CUSTODIAN" and _contains_final_eval(artifact_path):
            _fail(
                "FINAL_EVAL_LEAKAGE",
                "$.visibility_manifest",
                f"{role} may not receive final-evaluation material: {artifact_path}",
            )
    lesson_ids: tuple[str, ...] = ()
    if "lesson_manifest" in value:
        lesson_ids = _validate_lesson_manifest(
            value["lesson_manifest"],
            lane=lane,
            role=role,
            read_paths=paths["read_paths"],
            visibility=manifest,
        )
    input_hashes = _object(value["input_hashes"], "$.input_hashes")
    if not input_hashes:
        _fail("TYPE", "$.input_hashes", "must be a non-empty object")
    normalized_inputs: dict[str, str] = {}
    for raw_path, raw_digest in input_hashes.items():
        artifact_path = _path(raw_path, "$.input_hashes.<key>")
        digest = _sha256(raw_digest, f"$.input_hashes.{artifact_path}")
        if artifact_path not in manifest:
            _fail("VISIBILITY", "$.input_hashes", f"{artifact_path} is absent from visibility_manifest")
        if manifest[artifact_path] != digest:
            _fail("STALE_HASH", f"$.input_hashes.{artifact_path}", "does not match visibility manifest")
        normalized_inputs[artifact_path] = digest
    if set(normalized_inputs) != set(paths["read_paths"]):
        _fail("INPUT_HASH", "$.input_hashes", "must hash every and only declared read path")
    _validate_partition_receipt(value["partition_check_receipt"])
    receipt_paths = value["partition_check_receipt"]["owned_paths"]
    if set(receipt_paths) != set(paths["owned_output_paths"]):
        _fail("PARTITION_CHECK", "$.partition_check_receipt.owned_paths", "must match owned_output_paths")
    _validate_heartbeat(value["heartbeat"])
    capsule = value["state_capsule"]
    if capsule is not None:
        validate_state_capsule(capsule)
    if value["continuity_mode"] == "SUCCESSOR_RECONSTRUCTION" and not capsule:
        _fail(
            "CONTINUITY",
            "$.state_capsule",
            "SUCCESSOR_RECONSTRUCTION requires a non-empty accepted state capsule",
        )
    if verify_files:
        if root is None:
            _fail("INPUT_HASH", "$", "root is required when verify_files is true")
        root_path = Path(root).resolve()
        for artifact_path, expected in normalized_inputs.items():
            candidate = (root_path / artifact_path).resolve()
            try:
                candidate.relative_to(root_path)
            except ValueError:
                _fail("PATH", f"$.input_hashes.{artifact_path}", "resolves outside repository root")
            if not candidate.is_file():
                _fail("INPUT_MISSING", f"$.input_hashes.{artifact_path}", "declared input file is missing")
            actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if actual != expected:
                _fail(
                    "STALE_HASH",
                    f"$.input_hashes.{artifact_path}",
                    f"declared {expected}, actual {actual}",
                )
    return {
        "valid": True,
        "program_id": program_id,
        "lane": lane,
        "logical_role_id": logical_role_id,
        "execution_attempt_id": value["execution_attempt_id"],
        "lesson_ids": list(lesson_ids),
        "canonical_sha256": canonical_sha256(value),
    }


VALIDATORS: dict[str, Callable[[Any], Any]] = {
    "claim": validate_claim,
    "claim-envelope": validate_claim_envelope,
    "continuity": validate_continuity,
    "final-eval": validate_final_eval_contract,
    "shadow-window": validate_shadow_window_contract,
    "state-capsule": validate_state_capsule,
}


def deterministic_validation(
    kind: str,
    payload: Any,
    *,
    root: str | Path | None = None,
    verify_files: bool = True,
) -> dict[str, Any]:
    """Run one validator and return stable JSON-ready output instead of raising."""

    try:
        if kind == "work-order":
            details = validate_work_order(payload, root=root, verify_files=verify_files)
        else:
            validator = VALIDATORS.get(kind)
            if validator is None:
                _fail("VALIDATOR", "$", f"unknown validator: {kind}")
            details = validator(payload)
        output: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "kind": kind,
            "valid": True,
            "errors": [],
            "payload_sha256": canonical_sha256(payload),
        }
        if isinstance(details, Mapping):
            output["details"] = dict(details)
        return output
    except ProtocolError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": kind,
            "valid": False,
            "errors": [exc.as_dict()],
            "payload_sha256": canonical_sha256(payload),
        }


def _load_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("INPUT_JSON", "$", f"could not read JSON input: {exc}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate fail-closed model-training swarm contracts")
    parser.add_argument(
        "kind",
        choices=("work-order", "claim", "claim-envelope", "continuity", "final-eval", "shadow-window", "state-capsule"),
    )
    parser.add_argument("input", help="JSON file, or UTF-8 text file for state-capsule")
    parser.add_argument("--root", help="repository root for work-order SHA256 verification")
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="validate a work order without reading declared input files",
    )
    args = parser.parse_args(argv)
    try:
        payload = (
            Path(args.input).read_text(encoding="utf-8")
            if args.kind == "state-capsule"
            else _load_json(args.input)
        )
        result = deterministic_validation(
            args.kind,
            payload,
            root=args.root,
            verify_files=not args.schema_only,
        )
    except (OSError, ProtocolError) as exc:
        error = exc if isinstance(exc, ProtocolError) else ProtocolError("INPUT", "$", str(exc))
        result = {
            "schema_version": SCHEMA_VERSION,
            "kind": args.kind,
            "valid": False,
            "errors": [error.as_dict()],
            "payload_sha256": None,
        }
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
