#!/usr/bin/env python3
"""Local, append-only accounting for the two-lane model-training swarm.

The append-only JSONL ledger is authoritative. An explicit ``push`` command may
idempotently mirror allow-listed rows onto an existing local Model Factory run;
it never creates runs, changes gates, or writes production configuration.
Ledger records contain only metadata, identifiers, hashes and artifact pointers;
arbitrary payloads and raw customer text fields are rejected.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

FACTORY_DIR = Path(__file__).resolve().parent.parent
if str(FACTORY_DIR) not in sys.path:
    sys.path.insert(0, str(FACTORY_DIR))

import supabase_rest

LANES = ("ptc", "grand-steel")
EVENT_CATEGORIES = (
    "attempt",
    "attempt_cost",
    "contribution",
    "conflict",
    "budget",
    "flow",
    "cloud_lease",
    "delegation",
)
COST_CATEGORIES = ("agents", "corpus", "training", "evaluation", "shadow", "infrastructure")
ATTEMPT_STATES = ("PENDING", "RUNNING", "COMPLETED", "BLOCKED", "TIMED_OUT", "FAILED", "UNRESUMABLE")
TERMINAL_ATTEMPT_STATES = frozenset(ATTEMPT_STATES[2:])
LANE_TERMINAL_STATES = (
    "FINALIZED_SHIPPED",
    "FINALIZED_NO_TRAIN",
    "FINALIZED_NO_SHIP",
    "ABANDONED_BY_HUMAN",
)
CONTINUITY_MODES = ("AUTHOR_RESUME", "SUCCESSOR_RECONSTRUCTION")
CONTRIBUTION_CATEGORIES = ("gate", "data-change", "probe", "accepted-risk", "rejected", "contested")
EPISTEMIC_STATUSES = ("supported", "contradicted", "contested", "unsupported")
GATE_EFFECTS = ("block", "degrade-to-suspect", "informational", "none")
CONFLICT_STATUSES = ("open", "resolved", "human_accepted")
FLOW_TYPES = ("consumes", "produces", "corrects", "challenges", "confirms", "blocks", "handoff", "terminal_decision")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+={}-]{0,499}$")
FORBIDDEN_KEYS = frozenset(
    {
        "raw_customer_text",
        "customer_text",
        "prompt",
        "prompt_text",
        "transcript",
        "message",
        "tool_output",
        "report_body",
        "content",
    }
)
PROJECTION_STATUS = {
    "local_jsonl_ledger": "current",
    "local_decision_packets": "current",
    "existing_table_compatible_rows": "available",
    "database_push": "available_existing_local_run_only",
    "normalized_swarm_tables": "deferred",
    "model_factory_product_views": "partial_existing_agents_and_timeline",
}
PROGRAM_NAMESPACE = uuid.UUID("a3dc03be-6a89-51ec-86df-7279fd4ab602")


class LedgerError(ValueError):
    """Raised when an event would make the local ledger dishonest or ambiguous."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def _uuid5(namespace: uuid.UUID, *parts: object) -> str:
    return str(uuid.uuid5(namespace, "\x1f".join(str(part) for part in parts)))


def _require_choice(name: str, value: str, choices: Iterable[str]) -> None:
    if value not in choices:
        raise LedgerError(f"{name} must be one of {', '.join(choices)}")


def _require_uuid(name: str, value: str | None, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not UUID_RE.fullmatch(value):
        raise LedgerError(f"{name} must be a canonical UUID")


def _require_sha(name: str, value: str) -> None:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise LedgerError(f"{name} must be a lowercase sha256")


def _require_token(name: str, value: str | None, *, nullable: bool = False) -> None:
    """Accept identifiers and pointers, never prose or multiline report content."""
    if value is None and nullable:
        return
    if not isinstance(value, str) or not TOKEN_RE.fullmatch(value):
        raise LedgerError(f"{name} must be a bounded metadata identifier or artifact pointer")


def _reject_raw_text(value: Any, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_KEYS or key.startswith("raw_"):
                raise LedgerError(f"{path}.{key} is forbidden; store a hash or artifact pointer")
            _reject_raw_text(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_raw_text(child, f"{path}[{index}]")


def _validate_cost(cost: dict[str, Any]) -> None:
    if set(cost) - {"value_usd", "source", "accepted_unknown"}:
        raise LedgerError("cost contains unsupported fields")
    value = cost.get("value_usd")
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
        raise LedgerError("cost.value_usd must be a non-negative number or null")
    if not isinstance(cost.get("source"), str) or not cost["source"]:
        raise LedgerError("cost.source is required")
    _require_token("cost.source", cost["source"])
    if value is not None and cost.get("accepted_unknown"):
        raise LedgerError("known cost cannot be accepted_unknown")


def _validate_event(category: str, data: dict[str, Any]) -> None:
    _reject_raw_text(data)
    if category == "attempt":
        required = {"logical_role_id", "execution_attempt_id", "predecessor_attempt_id", "runtime_session_id",
                    "continuity_mode", "task_id", "wave", "state", "cost"}
        if set(data) != required:
            raise LedgerError(f"attempt fields must be exactly {sorted(required)}")
        _require_uuid("execution_attempt_id", data["execution_attempt_id"])
        _require_uuid("predecessor_attempt_id", data["predecessor_attempt_id"], nullable=True)
        _require_token("logical_role_id", data["logical_role_id"])
        _require_token("runtime_session_id", data["runtime_session_id"], nullable=True)
        _require_token("task_id", data["task_id"])
        _require_choice("continuity_mode", data["continuity_mode"], CONTINUITY_MODES)
        _require_choice("state", data["state"], ATTEMPT_STATES)
        if not re.fullmatch(r"W[0-8]", data["wave"]):
            raise LedgerError("wave must be W0 through W8")
        _validate_cost(data["cost"])
    elif category == "attempt_cost":
        required = {
            "correction_id",
            "execution_attempt_id",
            "runtime_session_id",
            "evidence_sha256",
            "cost",
        }
        if set(data) != required:
            raise LedgerError(f"attempt_cost fields must be exactly {sorted(required)}")
        _require_uuid("correction_id", data["correction_id"])
        _require_uuid("execution_attempt_id", data["execution_attempt_id"])
        _require_token("runtime_session_id", data["runtime_session_id"])
        _require_sha("evidence_sha256", data["evidence_sha256"])
        _validate_cost(data["cost"])
        if data["cost"]["value_usd"] is None:
            raise LedgerError("attempt_cost correction requires a known value_usd")
    elif category == "contribution":
        required = {"contribution_id", "execution_attempt_id", "category", "epistemic_status", "gate_effect",
                    "report_sha256", "evidence_chain", "disposition"}
        if set(data) != required:
            raise LedgerError(f"contribution fields must be exactly {sorted(required)}")
        _require_uuid("contribution_id", data["contribution_id"])
        _require_uuid("execution_attempt_id", data["execution_attempt_id"])
        _require_choice("category", data["category"], CONTRIBUTION_CATEGORIES)
        _require_choice("epistemic_status", data["epistemic_status"], EPISTEMIC_STATUSES)
        _require_choice("gate_effect", data["gate_effect"], GATE_EFFECTS)
        _require_sha("report_sha256", data["report_sha256"])
        if not data["evidence_chain"] or not all(isinstance(item, str) and item for item in data["evidence_chain"]):
            raise LedgerError("evidence_chain must contain identifiers or hashed pointers")
        for item in data["evidence_chain"]:
            _require_token("evidence_chain item", item)
        _require_choice("disposition", data["disposition"], ("accepted", "rejected", "contested"))
    elif category == "conflict":
        required = {"conflict_id", "claim_ids", "severity", "status", "resolution_ref"}
        if set(data) != required:
            raise LedgerError(f"conflict fields must be exactly {sorted(required)}")
        _require_uuid("conflict_id", data["conflict_id"])
        _require_choice("severity", data["severity"], ("critical", "noncritical"))
        _require_choice("status", data["status"], CONFLICT_STATUSES)
        if len(data["claim_ids"]) < 2:
            raise LedgerError("conflict requires at least two claim_ids")
        for claim_id in data["claim_ids"]:
            _require_token("claim_id", claim_id)
        if data["status"] != "open" and not data["resolution_ref"]:
            raise LedgerError("resolved conflict requires resolution_ref")
        _require_token("resolution_ref", data["resolution_ref"], nullable=True)
    elif category == "budget":
        required = {"budget_id", "cost_category", "kind", "cost"}
        if set(data) != required:
            raise LedgerError(f"budget fields must be exactly {sorted(required)}")
        _require_uuid("budget_id", data["budget_id"])
        _require_choice("cost_category", data["cost_category"], COST_CATEGORIES)
        _require_choice("kind", data["kind"], ("projected", "actual", "ceiling"))
        _validate_cost(data["cost"])
    elif category == "flow":
        required = {"flow_id", "flow_type", "from_ref", "to_ref", "artifact", "artifact_sha256",
                    "terminal_state", "signed_by"}
        if set(data) != required:
            raise LedgerError(f"flow fields must be exactly {sorted(required)}")
        _require_uuid("flow_id", data["flow_id"])
        _require_choice("flow_type", data["flow_type"], FLOW_TYPES)
        if data["artifact_sha256"] is not None:
            _require_sha("artifact_sha256", data["artifact_sha256"])
        _require_token("from_ref", data["from_ref"], nullable=True)
        _require_token("to_ref", data["to_ref"], nullable=True)
        _require_token("artifact", data["artifact"])
        if data["flow_type"] == "terminal_decision":
            _require_choice("terminal_state", data["terminal_state"], LANE_TERMINAL_STATES)
            if not data["signed_by"]:
                raise LedgerError("terminal decision requires signed_by")
            _require_token("signed_by", data["signed_by"])
        elif data["terminal_state"] is not None or data["signed_by"] is not None:
            raise LedgerError("terminal fields are only valid for terminal_decision flows")
        if data["flow_type"] == "handoff":
            _require_uuid("from_ref", data["from_ref"], nullable=True)
            _require_uuid("to_ref", data["to_ref"], nullable=True)
    elif category == "cloud_lease":
        required = {
            "lease_id",
            "logical_role_id",
            "sequence",
            "issued_at",
            "expires_at",
            "heartbeat_sha256",
        }
        if set(data) != required:
            raise LedgerError(f"cloud_lease fields must be exactly {sorted(required)}")
        _require_uuid("lease_id", data["lease_id"])
        _require_token("logical_role_id", data["logical_role_id"])
        if (
            isinstance(data["sequence"], bool)
            or not isinstance(data["sequence"], int)
            or data["sequence"] < 1
        ):
            raise LedgerError("cloud_lease.sequence must be a positive integer")
        try:
            issued = datetime.fromisoformat(data["issued_at"].replace("Z", "+00:00"))
            expires = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise LedgerError("cloud_lease timestamps must be ISO-8601") from exc
        if issued.tzinfo is None or expires.tzinfo is None:
            raise LedgerError("cloud_lease timestamps must include a timezone")
        if (expires - issued).total_seconds() != 1800:
            raise LedgerError("cloud_lease must span exactly 1800 seconds")
        _require_sha("heartbeat_sha256", data["heartbeat_sha256"])
    elif category == "delegation":
        required = {
            "request_id",
            "requester_role_id",
            "target_role_id",
            "task_id",
            "status",
            "routed_by_role_id",
            "artifact",
            "artifact_sha256",
        }
        if set(data) != required:
            raise LedgerError(f"delegation fields must be exactly {sorted(required)}")
        _require_uuid("request_id", data["request_id"])
        _require_token("requester_role_id", data["requester_role_id"])
        _require_token("target_role_id", data["target_role_id"])
        _require_token("task_id", data["task_id"])
        _require_choice("status", data["status"], ("requested", "routed"))
        _require_token(
            "routed_by_role_id", data["routed_by_role_id"], nullable=True
        )
        _require_token("artifact", data["artifact"])
        _require_sha("artifact_sha256", data["artifact_sha256"])
        if data["status"] == "routed":
            if (
                not data["routed_by_role_id"]
                or not data["routed_by_role_id"].endswith("/LANE-COORD")
            ):
                raise LedgerError(
                    "routed delegation requires the lane coordinator identity"
                )
        elif data["routed_by_role_id"] is not None:
            raise LedgerError(
                "requested delegation may not name routed_by_role_id"
            )
    else:
        raise LedgerError(f"unsupported event category: {category}")


def create_program(root: str | Path, program_key: str, *, created_at: str | None = None) -> dict[str, Any]:
    """Create or return a deterministic program with two isolated lane runs."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,80}", program_key):
        raise LedgerError("program_key must be a lowercase slug")
    root = Path(root)
    program_id = _uuid5(PROGRAM_NAMESPACE, "program", program_key)
    lanes = {
        lane: {
            "lane": lane,
            "model_run_id": _uuid5(uuid.UUID(program_id), "model-run", lane),
        }
        for lane in LANES
    }
    manifest_path = root / "program.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("program_id") != program_id or manifest.get("program_key") != program_key:
            raise LedgerError("root already belongs to a different program")
        return manifest
    root.mkdir(parents=True, exist_ok=True)
    for lane, lane_data in lanes.items():
        (root / "lanes" / lane / lane_data["model_run_id"] / "exports").mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "program_id": program_id,
        "program_key": program_key,
        "created_at": created_at or _now(),
        "lanes": lanes,
        "projection_status": PROJECTION_STATUS,
        "data_policy": "metadata_hashes_and_artifact_pointers_only_no_raw_customer_text",
    }
    try:
        fd = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return create_program(root, program_key, created_at=created_at)
    with os.fdopen(fd, "wb") as handle:
        handle.write(_json_bytes(manifest))
        handle.flush()
        os.fsync(handle.fileno())
    return manifest


def load_program(root: str | Path) -> dict[str, Any]:
    path = Path(root) / "program.json"
    if not path.is_file():
        raise LedgerError(f"program not initialized: {path}")
    return json.loads(path.read_text())


def lane_dir(root: str | Path, manifest: dict[str, Any], lane: str) -> Path:
    _require_choice("lane", lane, LANES)
    return Path(root) / "lanes" / lane / manifest["lanes"][lane]["model_run_id"]


def append_event(
    root: str | Path,
    lane: str,
    category: str,
    data: dict[str, Any],
    idempotency_key: str,
    *,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Append once by UUIDv5; repeated semantic submissions return the first row."""
    manifest = load_program(root)
    _require_choice("lane", lane, LANES)
    _require_choice("category", category, EVENT_CATEGORIES)
    if not idempotency_key or len(idempotency_key) > 200:
        raise LedgerError("idempotency_key is required and must be <=200 characters")
    _validate_event(category, data)
    event_id = _uuid5(uuid.UUID(manifest["program_id"]), lane, category, idempotency_key)
    event = {
        "schema_version": 1,
        "event_id": event_id,
        "program_id": manifest["program_id"],
        "model_run_id": manifest["lanes"][lane]["model_run_id"],
        "lane": lane,
        "category": category,
        "observed_at": observed_at or _now(),
        "data": data,
    }
    path = lane_dir(root, manifest, lane) / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        for raw in handle:
            existing = json.loads(raw)
            if existing["event_id"] == event_id:
                comparable = {key: value for key, value in existing.items() if key != "observed_at"}
                proposed = {key: value for key, value in event.items() if key != "observed_at"}
                if comparable != proposed:
                    raise LedgerError(f"idempotency collision for {event_id}")
                return existing
        handle.seek(0, os.SEEK_END)
        handle.write(_json_bytes(event))
        handle.flush()
        os.fsync(handle.fileno())
    return event


def read_events(root: str | Path, lane: str) -> list[dict[str, Any]]:
    manifest = load_program(root)
    path = lane_dir(root, manifest, lane) / "events.jsonl"
    if not path.exists():
        return []
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    ids = [event["event_id"] for event in events]
    if len(ids) != len(set(ids)):
        raise LedgerError(f"duplicate immutable event IDs in {path}")
    return events


def make_attempt(
    manifest: dict[str, Any],
    lane: str,
    role: str,
    attempt_key: str,
    *,
    state: str,
    cost_usd: float | None,
    cost_source: str,
    accepted_unknown: bool = False,
    predecessor_attempt_id: str | None = None,
    runtime_session_id: str | None = None,
    continuity_mode: str = "AUTHOR_RESUME",
    task_id: str = "unspecified",
    wave: str = "W0",
) -> dict[str, Any]:
    logical_role_id = f"{manifest['program_id']}/{lane}/{role}"
    attempt_id = _uuid5(uuid.UUID(manifest["program_id"]), logical_role_id, attempt_key)
    return {
        "logical_role_id": logical_role_id,
        "execution_attempt_id": attempt_id,
        "predecessor_attempt_id": predecessor_attempt_id,
        "runtime_session_id": runtime_session_id,
        "continuity_mode": continuity_mode,
        "task_id": task_id,
        "wave": wave,
        "state": state,
        "cost": {"value_usd": cost_usd, "source": cost_source, "accepted_unknown": accepted_unknown},
    }


def _latest_by(events: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        latest[event["data"][key]] = event
    return latest


def _effective_attempt_costs(
    events: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    corrections = _latest_by(
        [event for event in events if event["category"] == "attempt_cost"],
        "execution_attempt_id",
    )
    effective = {}
    for event in [event for event in events if event["category"] == "attempt"]:
        attempt_id = event["data"]["execution_attempt_id"]
        correction = corrections.get(attempt_id)
        effective[attempt_id] = {
            "cost": correction["data"]["cost"] if correction else event["data"]["cost"],
            "runtime_session_id": (
                correction["data"]["runtime_session_id"]
                if correction
                else event["data"]["runtime_session_id"]
            ),
            "corrected": correction is not None,
        }
    return effective


def _cost_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = [event for event in events if event["category"] == "attempt"]
    effective = _effective_attempt_costs(events)
    known_attempts = [
        row for row in effective.values() if row["cost"]["value_usd"] is not None
    ]
    known_total = sum(float(row["cost"]["value_usd"]) for row in known_attempts)
    categories: dict[str, dict[str, Any]] = {}
    budgets = _latest_by([event for event in events if event["category"] == "budget"], "budget_id")
    for category in COST_CATEGORIES:
        rows = [event["data"] for event in budgets.values() if event["data"]["cost_category"] == category]
        values = [row["cost"]["value_usd"] for row in rows]
        categories[category] = {
            "known_usd": sum(float(value) for value in values if value is not None),
            "unknown_entries": sum(value is None for value in values),
            "entries": len(rows),
        }
    count = len(attempts)
    return {
        "attempts_total": count,
        "known_cost_attempts": len(known_attempts),
        "unknown_cost_attempts": count - len(known_attempts),
        "known_attempt_cost_usd": known_total,
        "attempt_cost_coverage": (len(known_attempts) / count) if count else None,
        "dollar_cost_coverage": (1.0 if count and len(known_attempts) == count else None),
        "costs_by_category": categories,
    }


def build_lane_packet(root: str | Path, lane: str) -> dict[str, Any]:
    manifest = load_program(root)
    events = read_events(root, lane)
    attempts = [event for event in events if event["category"] == "attempt"]
    effective_costs = _effective_attempt_costs(events)
    contributions = [event for event in events if event["category"] == "contribution"]
    conflicts = _latest_by([event for event in events if event["category"] == "conflict"], "conflict_id")
    terminal = [event for event in events if event["category"] == "flow" and event["data"]["flow_type"] == "terminal_decision"]
    terminal_event = terminal[-1] if terminal else None
    attempt_ids = {event["data"]["execution_attempt_id"] for event in attempts}
    orphan_contributions = [
        event["event_id"] for event in contributions
        if event["data"]["execution_attempt_id"] not in attempt_ids
    ]
    role_count = len({event["data"]["logical_role_id"] for event in attempts})
    unresolved_critical = [
        event["data"]["conflict_id"] for event in conflicts.values()
        if event["data"]["severity"] == "critical" and event["data"]["status"] == "open"
    ]
    incomplete_attempts = [
        event["data"]["execution_attempt_id"] for event in attempts
        if event["data"]["state"] not in TERMINAL_ATTEMPT_STATES
    ]
    unaccepted_unknown = [
        event["data"]["execution_attempt_id"] for event in attempts
        if (
            effective_costs[event["data"]["execution_attempt_id"]]["cost"]["value_usd"]
            is None
            and not effective_costs[event["data"]["execution_attempt_id"]]["cost"].get(
                "accepted_unknown", False
            )
        )
    ]
    latest_budgets = _latest_by(
        [event for event in events if event["category"] == "budget"], "budget_id"
    )
    budget_categories = {event["data"]["cost_category"] for event in latest_budgets.values()}
    missing_cost_categories = sorted(set(COST_CATEGORIES) - budget_categories)
    unreconciled_cost_categories = sorted({
        event["data"]["cost_category"]
        for event in latest_budgets.values()
        if event["data"]["cost"]["value_usd"] is None
        and not event["data"]["cost"].get("accepted_unknown", False)
    })
    blockers = {
        "missing_terminal_decision": terminal_event is None,
        "incomplete_attempt_ids": incomplete_attempts,
        "unaccepted_unknown_cost_attempt_ids": unaccepted_unknown,
        "missing_cost_categories": missing_cost_categories,
        "unreconciled_cost_categories": unreconciled_cost_categories,
        "unresolved_critical_conflict_ids": unresolved_critical,
        "orphan_contribution_event_ids": orphan_contributions,
    }
    complete = not any(
        value if isinstance(value, bool) else bool(value)
        for value in blockers.values()
    )
    return {
        "schema_version": 1,
        "packet_type": "swarm_lane_decision",
        "generated_at": _now(),
        "program_id": manifest["program_id"],
        "model_run_id": manifest["lanes"][lane]["model_run_id"],
        "lane": lane,
        "terminal_state": terminal_event["data"]["terminal_state"] if terminal_event else None,
        "signed_by": terminal_event["data"]["signed_by"] if terminal_event else None,
        "terminal_export_complete": complete,
        "terminal_blockers": blockers,
        "counts": {
            "execution_attempts": len(attempts),
            "logical_roles": role_count,
            "retry_attempts": max(0, len(attempts) - role_count),
            "contributions": len(contributions),
            "accepted_contributions": sum(event["data"]["disposition"] == "accepted" for event in contributions),
            "conflicts": len(conflicts),
        },
        "cost": _cost_summary(events),
        "contributions": [event["data"] | {"event_id": event["event_id"]} for event in contributions],
        "conflicts": [event["data"] | {"event_id": event["event_id"]} for event in conflicts.values()],
        "projection_status": PROJECTION_STATUS,
    }


def project_existing_table_rows(root: str | Path, lane: str) -> dict[str, Any]:
    """Generate compatible row shapes only; this function performs no network I/O."""
    manifest = load_program(root)
    run_id = manifest["lanes"][lane]["model_run_id"]
    events = read_events(root, lane)
    attempts = [event for event in events if event["category"] == "attempt"]
    effective_costs = _effective_attempt_costs(events)
    agents = []
    run_events = []
    for event in attempts:
        data = event["data"]
        effective = effective_costs[data["execution_attempt_id"]]
        role_title = data["logical_role_id"].rsplit("/", 1)[-1]
        title = f"{role_title}-{data['execution_attempt_id'][:8]}"
        agent_id = data["execution_attempt_id"]
        agents.append({
            "id": agent_id, "run_id": run_id, "stage": data["wave"], "role": "reader",
            "title": title, "goal": data["task_id"],
            "status": "active" if data["state"] in ("PENDING", "RUNNING") else "ended",
            "risk": "read-only", "allowed_paths": [], "files_touched": [], "commands_run": 0,
            "cost_usd": effective["cost"]["value_usd"],
            "ledger_session_id": effective["runtime_session_id"],
        })
        detail = {
            "spec_slug": role_title,
            "role_family": "reader",
            "budget_usd": None,
            "cost_usd": effective["cost"]["value_usd"],
            "verdict": data["state"].lower(),
            "ledger_session_id": effective["runtime_session_id"],
        }
        run_events.append({
            "id": _uuid5(uuid.UUID(run_id), "agent_dispatched", data["execution_attempt_id"]),
            "run_id": run_id,
            "ts": event["observed_at"],
            "kind": "agent_dispatched",
            "headline": f"{title} attempt {data['state'].lower()}",
            "ref": {"node_id": data["execution_attempt_id"], "stage": data["wave"], "agent_title": title},
            "detail": {key: value for key, value in detail.items() if value is not None},
        })
    for event in events:
        if event["category"] in (
            "attempt_cost",
            "contribution",
            "conflict",
            "budget",
            "cloud_lease",
            "delegation",
        ):
            subtype = {
                "attempt_cost": "swarm_attempt_cost",
                "contribution": "swarm_contribution",
                "conflict": "swarm_conflict",
                "budget": "swarm_budget_state",
                "cloud_lease": "swarm_cloud_lease",
                "delegation": "swarm_delegation",
            }[event["category"]]
            run_events.append({
                "id": event["event_id"], "run_id": run_id, "ts": event["observed_at"],
                "kind": "steering_event",
                "headline": f"{subtype} recorded for {lane}",
                "ref": {"event_subtype": subtype, "swarm_event_id": event["event_id"]},
                "detail": {"category": event["category"], "lane": lane},
            })
    handoffs = []
    for event in events:
        data = event["data"]
        if event["category"] == "flow" and data["flow_type"] == "handoff":
            handoffs.append({
                "id": event["event_id"], "run_id": run_id,
                "from_agent_id": data["from_ref"], "to_agent_id": data["to_ref"],
                "artifact": data["artifact"],
                "summary": "type=HANDOFF; confidence=certain; evidence="
                f"{data['artifact']}#{data['artifact_sha256']}; sync_version=swarm-local-v1",
                "created_at": event["observed_at"],
            })
    return {
        "delivery_status": "generated_not_pushed",
        "factory_agents": agents,
        "factory_run_events": run_events,
        "factory_handoffs": handoffs,
    }


def push_existing_table_rows(
    root: str | Path,
    lane: str,
    target_run_id: str,
    *,
    url: str | None = None,
    service_role_key: str | None = None,
) -> dict[str, Any]:
    """Idempotently project one local lane onto an existing local Factory run.

    The target must already exist; this function never mints or mutates a
    ``factory_runs`` row, so a swarm cannot bypass the factory's real run
    lineage, live-run uniqueness, or human gates.
    """
    _require_uuid("target_run_id", target_run_id)
    existing = supabase_rest.select(
        "factory_runs",
        {"id": f"eq.{target_run_id}", "select": "id", "limit": 1},
        url=url,
        service_role_key=service_role_key,
    )
    if len(existing) != 1:
        raise LedgerError(f"target factory run does not exist: {target_run_id}")
    projected = project_existing_table_rows(root, lane)
    tables = {
        "factory_agents": [],
        "factory_run_events": [],
        "factory_handoffs": [],
    }
    for table in tables:
        for row in projected[table]:
            tables[table].append({**row, "run_id": target_run_id})
        if tables[table]:
            supabase_rest.upsert(
                table,
                tables[table],
                "id",
                url=url,
                service_role_key=service_role_key,
            )
    return {
        "delivery_status": "pushed_to_existing_local_run",
        "lane": lane,
        "target_run_id": target_run_id,
        "counts": {table: len(rows) for table, rows in tables.items()},
        "source_model_run_id": load_program(root)["lanes"][lane]["model_run_id"],
    }


def _lane_markdown(packet: dict[str, Any]) -> str:
    cost = packet["cost"]
    coverage = cost["attempt_cost_coverage"]
    coverage_text = "null" if coverage is None else f"{coverage:.0%}"
    dollar_text = "null" if cost["dollar_cost_coverage"] is None else f"{cost['dollar_cost_coverage']:.0%}"
    return (
        f"# {packet['lane']} swarm decision packet\n\n"
        f"- Terminal state: {packet['terminal_state'] or 'not terminal'}\n"
        f"- Terminal export complete: {str(packet['terminal_export_complete']).lower()}\n"
        f"- Execution attempts: {packet['counts']['execution_attempts']}\n"
        f"- Logical roles: {packet['counts']['logical_roles']}\n"
        f"- Retry attempts: {packet['counts']['retry_attempts']}\n"
        f"- Known attempt cost USD: {cost['known_attempt_cost_usd']}\n"
        f"- Attempt cost coverage: {coverage_text}\n"
        f"- Dollar cost coverage: {dollar_text}\n"
        f"- Database delivery: {packet['projection_status']['database_push']}\n"
    )


def export_packets(root: str | Path, *, emit_projections: bool = False, require_terminal: bool = False) -> dict[str, Any]:
    manifest = load_program(root)
    lane_packets = {lane: build_lane_packet(root, lane) for lane in LANES}
    if require_terminal:
        incomplete = [lane for lane, packet in lane_packets.items() if not packet["terminal_export_complete"]]
        if incomplete:
            raise LedgerError(f"terminal export incomplete for lanes: {', '.join(incomplete)}")
    program_packet = {
        "schema_version": 1,
        "packet_type": "swarm_program_decision",
        "generated_at": _now(),
        "program_id": manifest["program_id"],
        "program_terminal": all(packet["terminal_export_complete"] for packet in lane_packets.values()),
        "lane_packets": {
            lane: {
                "model_run_id": packet["model_run_id"],
                "terminal_state": packet["terminal_state"],
                "terminal_export_complete": packet["terminal_export_complete"],
                "counts": packet["counts"],
                "cost": packet["cost"],
            }
            for lane, packet in lane_packets.items()
        },
        "projection_status": PROJECTION_STATUS,
    }
    exports = Path(root) / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    for lane, packet in lane_packets.items():
        lane_exports = lane_dir(root, manifest, lane) / "exports"
        (lane_exports / "decision-packet.json").write_bytes(_json_bytes(packet))
        (lane_exports / "decision-packet.md").write_text(_lane_markdown(packet))
    (exports / "program-decision-packet.json").write_bytes(_json_bytes(program_packet))
    (exports / "program-decision-packet.md").write_text(
        "# Swarm program decision packet\n\n"
        f"- Program terminal: {str(program_packet['program_terminal']).lower()}\n"
        f"- PTC terminal: {lane_packets['ptc']['terminal_state'] or 'not terminal'}\n"
        f"- Grand Steel terminal: {lane_packets['grand-steel']['terminal_state'] or 'not terminal'}\n"
        f"- Database delivery: {PROJECTION_STATUS['database_push']}\n"
    )
    result: dict[str, Any] = {"program": program_packet, "lanes": lane_packets}
    if emit_projections:
        projections = {lane: project_existing_table_rows(root, lane) for lane in LANES}
        (exports / "existing-table-projections.not-pushed.json").write_bytes(_json_bytes({
            "delivery_status": "generated_not_pushed",
            "lanes": projections,
        }))
        result["projections"] = projections
    return result


def _load_json_arg(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise LedgerError("--data must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="create deterministic program and two lane runs")
    init.add_argument("--root", required=True)
    init.add_argument("--program-key", required=True)
    append = sub.add_parser("append", help="append one allow-listed immutable event")
    append.add_argument("--root", required=True)
    append.add_argument("--lane", required=True, choices=LANES)
    append.add_argument("--category", required=True, choices=EVENT_CATEGORIES)
    append.add_argument("--idempotency-key", required=True)
    append.add_argument("--data", required=True, help="JSON object; raw customer text fields are forbidden")
    export = sub.add_parser("export", help="write lane and program decision packets")
    export.add_argument("--root", required=True)
    export.add_argument("--emit-projections", action="store_true")
    export.add_argument("--require-terminal", action="store_true")
    push = sub.add_parser("push", help="push projections onto an existing local Factory run")
    push.add_argument("--root", required=True)
    push.add_argument("--lane", required=True, choices=LANES)
    push.add_argument("--target-run-id", required=True)
    push.add_argument("--url")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            output = create_program(args.root, args.program_key)
        elif args.command == "append":
            output = append_event(args.root, args.lane, args.category, _load_json_arg(args.data), args.idempotency_key)
        elif args.command == "export":
            output = export_packets(args.root, emit_projections=args.emit_projections, require_terminal=args.require_terminal)
        else:
            output = push_existing_table_rows(
                args.root,
                args.lane,
                args.target_run_id,
                url=args.url,
            )
        print(json.dumps(output, sort_keys=True))
        return 0
    except (LedgerError, supabase_rest.SupabaseRestError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
