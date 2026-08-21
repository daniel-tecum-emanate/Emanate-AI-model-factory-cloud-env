#!/usr/bin/env python3
"""Fail-closed bridge from swarm work orders to the existing factory dispatcher.

The bridge validates and hashes the complete work order before dispatch, invokes
``factory_node_run`` only after that validation succeeds, then records one
immutable final attempt plus a bounded contribution in the local swarm ledger.
Dry-run is the default and is recorded as a proven $0 attempt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
REPO = FACTORY_DIR.parent.parent
sys.path.insert(0, str(FACTORY_DIR))
sys.path.insert(0, str(HERE))

import factory_node_run  # noqa: E402
import factory_sync  # noqa: E402
import local_factory  # noqa: E402
import protocol  # noqa: E402


ROLE_TO_SPEC = {
    "LANE-COORD": "lane-swarm-coordinator",
    "KEEPER": "evidence-swarm-keeper",
    "DATA-ARCH": "data-swarm-architect",
    "CORPUS-ARCH": "corpus-swarm-architect",
    "EVAL-DEV": "eval-swarm-development",
    "FINAL-EVAL-CUSTODIAN": "final-eval-swarm-custodian",
    "SERVE-ARCH": "serve-swarm-architect",
    "REDTEAM": "symmetric-swarm-redteam",
}


class RunnerError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _role_from_id(logical_role_id: str) -> str:
    try:
        return logical_role_id.rsplit("/", 1)[1]
    except (AttributeError, IndexError) as exc:
        raise RunnerError("logical_role_id is malformed") from exc


def build_work_order(
    ledger_root: str | Path,
    repo_root: str | Path,
    *,
    lane: str,
    role: str,
    wave: str,
    task_id: str,
    attempt_key: str,
    read_paths: list[str],
    owned_output_paths: list[str],
    forbidden_paths: list[str],
    partition_command: str,
    heartbeat_row_id: str,
    product_target_run_id: str | None = None,
    lesson_manifest: list[dict[str, Any]] | None = None,
    predecessor_attempt_id: str | None = None,
    predecessor_runtime_session_id: str | None = None,
    continuity_mode: str = "AUTHOR_RESUME",
    state_capsule: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build and validate a first-attempt work order from current artifact bytes."""
    manifest = local_factory.load_program(ledger_root)
    if role not in ROLE_TO_SPEC:
        raise RunnerError(f"unsupported role: {role}")
    if lane not in local_factory.LANES:
        raise RunnerError(f"unsupported lane: {lane}")
    spec = factory_node_run.load_spec(ROLE_TO_SPEC[role])
    runtime = factory_node_run.resolve_runtime(spec)
    stamp = now or _now()
    repo_root = Path(repo_root).resolve()
    visibility = []
    input_hashes = {}
    for relative in read_paths:
        candidate = (repo_root / relative).resolve()
        try:
            candidate.relative_to(repo_root)
        except ValueError as exc:
            raise RunnerError(f"read path escapes repository: {relative}") from exc
        if not candidate.is_file():
            raise RunnerError(f"read path is not a file: {relative}")
        digest = _sha_file(candidate)
        visibility.append({"path": relative, "sha256": digest})
        input_hashes[relative] = digest
    attempt_id = str(
        uuid.uuid5(
            uuid.UUID(manifest["program_id"]),
            f"{lane}:{role}:{wave}:{task_id}:{attempt_key}",
        )
    )
    receipt_body = {
        "command": partition_command,
        "owned_paths": owned_output_paths,
        "checked_at": _iso(stamp),
    }
    work_order = {
        "schema_version": protocol.SCHEMA_VERSION,
        "program_id": manifest["program_id"],
        "lane": lane,
        "wave": wave,
        "logical_role_id": f"{manifest['program_id']}/{lane}/{role}",
        "execution_attempt_id": attempt_id,
        "predecessor_attempt_id": predecessor_attempt_id,
        "runtime_session_id": None,
        "predecessor_runtime_session_id": predecessor_runtime_session_id,
        "continuity_mode": continuity_mode,
        "factory_run_id": manifest["lanes"][lane]["model_run_id"],
        "task_id": task_id,
        "max_cost_usd": float(runtime["budget_usd"]),
        "zone": "factory-automation",
        "read_paths": read_paths,
        "write_paths": owned_output_paths,
        "owned_output_paths": owned_output_paths,
        "forbidden_paths": forbidden_paths,
        "visibility_manifest": visibility,
        "input_hashes": input_hashes,
        "partition_check_receipt": {
            "status": "pass",
            **receipt_body,
            "receipt_sha256": _canonical_sha(receipt_body),
        },
        "heartbeat": {
            "row_id": heartbeat_row_id,
            "zone": "factory-automation",
            "lease_seconds": 1800,
            "sequence_interval_seconds": 600,
            "seq": 1,
            "updated_at": _iso(stamp),
            "lease_expires_at": _iso(stamp + timedelta(seconds=1800)),
        },
        "state_capsule": state_capsule,
    }
    if product_target_run_id is not None:
        work_order["product_target_run_id"] = product_target_run_id
    if lesson_manifest is not None:
        work_order["lesson_manifest"] = lesson_manifest
    protocol.validate_work_order(work_order, root=repo_root)
    return work_order


def save_work_order(
    ledger_root: str | Path, work_order: dict[str, Any]
) -> Path:
    manifest = local_factory.load_program(ledger_root)
    lane_root = local_factory.lane_dir(
        ledger_root, manifest, work_order["lane"]
    )
    target = lane_root / "work-orders" / (
        f"{work_order['wave']}-{_role_from_id(work_order['logical_role_id']).lower()}-"
        f"{work_order['execution_attempt_id'][:8]}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        work_order, sort_keys=True, indent=2, ensure_ascii=False
    ) + "\n"
    if target.exists() and target.read_text() != encoded:
        raise RunnerError(f"refusing to overwrite changed work order: {target}")
    target.write_text(encoded)
    return target


def _ledger_match(
    node_id: str, started_at: datetime, ended_at: datetime, ledger_dir: str | Path | None
) -> dict[str, Any] | None:
    ledger_path = factory_sync.ledger_path_for(ledger_dir, when=ended_at)
    return factory_sync.match_ledger_row(
        ledger_path, node_id, window_start=started_at, window_end=ended_at
    )


def _dispatch_context_paths(
    ledger_root: str | Path,
    repo_root: str | Path,
    work_order: dict[str, Any],
) -> list[str]:
    paths = list(work_order["read_paths"])
    manifest = local_factory.load_program(ledger_root)
    directory = local_factory.lane_dir(
        ledger_root, manifest, work_order["lane"]
    ) / "work-orders"
    matches = list(directory.glob(f"*-{work_order['execution_attempt_id'][:8]}.json"))
    if len(matches) == 1:
        try:
            relative = matches[0].resolve().relative_to(Path(repo_root).resolve())
        except ValueError:
            pass
        else:
            paths.append(str(relative))
    return paths


def _validate_specialist_response(
    response: Any, assigned_lesson_ids: list[str]
) -> dict[str, Any]:
    if not isinstance(response, str):
        raise RunnerError("specialist final_response must be a JSON string")
    candidate = response.strip()
    if candidate.startswith("```json\n") and candidate.endswith("\n```"):
        candidate = candidate[len("```json\n") : -len("\n```")]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise RunnerError("specialist final_response is not strict JSON") from exc
    if not isinstance(parsed, dict) or set(parsed) != {
        "verdict",
        "lesson_dispositions",
        "claims",
        "blockers",
        "next_reversible_action",
    }:
        raise RunnerError("specialist JSON has missing or unsupported top-level fields")
    if parsed["verdict"] not in ("PASS", "BLOCKED"):
        raise RunnerError("specialist verdict must be PASS or BLOCKED")
    if not isinstance(parsed["claims"], list) or not isinstance(parsed["blockers"], list):
        raise RunnerError("specialist claims and blockers must be arrays")
    if not all(isinstance(item, str) and item for item in parsed["blockers"]):
        raise RunnerError("specialist blockers must contain non-empty strings")
    if not isinstance(parsed["next_reversible_action"], str) or not parsed["next_reversible_action"]:
        raise RunnerError("specialist next_reversible_action must be non-empty")
    dispositions = parsed["lesson_dispositions"]
    if not isinstance(dispositions, list):
        raise RunnerError("lesson_dispositions must be an array")
    observed = []
    for index, item in enumerate(dispositions):
        if not isinstance(item, dict) or set(item) != {
            "lesson_id",
            "disposition",
            "evidence_chain",
            "mechanical_check_result",
            "effect_on_plan",
        }:
            raise RunnerError(f"lesson_dispositions[{index}] has invalid fields")
        if item["disposition"] not in (
            "APPLIED",
            "NOT_APPLICABLE",
            "CONTRADICTED",
            "UNRESOLVED",
        ):
            raise RunnerError(f"lesson_dispositions[{index}] has invalid disposition")
        if item["mechanical_check_result"] not in ("PASS", "FAIL", "NOT_RUN"):
            raise RunnerError(f"lesson_dispositions[{index}] has invalid check result")
        if (
            not isinstance(item["evidence_chain"], list)
            or not item["evidence_chain"]
            or not all(isinstance(ref, str) and ref for ref in item["evidence_chain"])
        ):
            raise RunnerError(f"lesson_dispositions[{index}] needs evidence")
        if not isinstance(item["effect_on_plan"], str) or not item["effect_on_plan"]:
            raise RunnerError(f"lesson_dispositions[{index}] needs effect_on_plan")
        observed.append(item["lesson_id"])
    if len(observed) != len(set(observed)) or set(observed) != set(assigned_lesson_ids):
        raise RunnerError("lesson dispositions must cover every and only assigned lesson once")
    return parsed


def _write_report_artifact(
    repo_root: str | Path,
    work_order: dict[str, Any],
    result: dict[str, Any],
    cost: dict[str, Any],
    response_validation: dict[str, Any],
) -> tuple[str, str]:
    """Persist the bounded agent result at its single owned output path."""
    relative = work_order["owned_output_paths"][0]
    target = (Path(repo_root).resolve() / relative).resolve()
    try:
        target.relative_to(Path(repo_root).resolve())
    except ValueError as exc:
        raise RunnerError(f"report path escapes repository: {relative}") from exc
    report = {
        "schema_version": 1,
        "program_id": work_order["program_id"],
        "lane": work_order["lane"],
        "wave": work_order["wave"],
        "logical_role_id": work_order["logical_role_id"],
        "execution_attempt_id": work_order["execution_attempt_id"],
        "task_id": work_order["task_id"],
        "work_order_sha256": protocol.canonical_sha256(work_order),
        "dispatch": {
            "ok": bool(result.get("ok")),
            "node_id": result.get("node_id"),
            "spec": result.get("spec"),
            "dry_run": bool(result.get("dry_run")),
            "verdict": result.get("verdict"),
            "exit_code": result.get("exit_code"),
        },
        "cost": cost,
        "final_response": result.get("final_response"),
        "response_validation": response_validation,
    }
    digest = _canonical_sha(report)
    encoded = json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_text() != encoded:
        raise RunnerError(f"refusing to overwrite changed report artifact: {relative}")
    target.write_text(encoded)
    return relative, digest


def run_work_order(
    ledger_root: str | Path,
    repo_root: str | Path,
    work_order: dict[str, Any],
    *,
    live: bool = False,
    ledger_dir: str | Path | None = None,
    dispatcher=factory_node_run.dispatch_node,
) -> dict[str, Any]:
    """Validate, dispatch, cost-correlate, and immutably record one attempt."""
    validated = protocol.validate_work_order(work_order, root=repo_root)
    role = _role_from_id(work_order["logical_role_id"])
    spec_slug = ROLE_TO_SPEC.get(role)
    if not spec_slug:
        raise RunnerError(f"no AgentSpec mapping for role {role}")
    spec = factory_node_run.load_spec(spec_slug)
    runtime = factory_node_run.resolve_runtime(spec)
    if float(runtime["budget_usd"]) != float(work_order["max_cost_usd"]):
        raise RunnerError("work-order max_cost_usd does not equal reviewed AgentSpec budget")
    if live and role == "FINAL-EVAL-CUSTODIAN":
        raise RunnerError("final evaluation requires a separately validated signed contract")
    product_target_run_id = work_order.get("product_target_run_id")
    if live and not product_target_run_id:
        raise RunnerError("live dispatch requires a hash-bound product_target_run_id")
    if live and role != "LANE-COORD" and not validated["lesson_ids"]:
        raise RunnerError("live specialist dispatch requires a validated lesson_manifest")

    started_at = _now()
    dispatch_stage = (
        "S8"
        if role == "FINAL-EVAL-CUSTODIAN"
        else f"{work_order['wave']}-{work_order['lane'].upper()}"
    )
    result = dispatcher(
        spec_slug,
        dispatch_stage,
        product_target_run_id if live else work_order["factory_run_id"],
        "model-training-swarm-v1",
        "ptc-steel" if work_order["lane"] == "ptc" else "grand-steel",
        dry_run=not live,
        sync_url=None,
        ledger_dir=ledger_dir,
        context_pack=_dispatch_context_paths(ledger_root, repo_root, work_order),
    )
    ended_at = _now()
    ledger_row = None if not live else _ledger_match(
        result.get("node_id") or work_order["wave"],
        started_at,
        ended_at,
        ledger_dir,
    )
    if not live:
        cost_usd, cost_source, accepted_unknown = 0, "no-paid-call", False
    elif ledger_row and ledger_row.get("cost_usd") is not None:
        cost_usd, cost_source, accepted_unknown = (
            float(ledger_row["cost_usd"]),
            "tier2-ledger",
            False,
        )
    else:
        cost_usd, cost_source, accepted_unknown = (
            None,
            "tier2-ledger-match-missing",
            False,
        )
    response_validation: dict[str, Any] = {
        "required": bool(live and role != "LANE-COORD"),
        "valid": True,
        "error": None,
        "structured_response": None,
    }
    if response_validation["required"]:
        try:
            response_validation["structured_response"] = _validate_specialist_response(
                result.get("final_response"), validated["lesson_ids"]
            )
        except RunnerError as exc:
            response_validation["valid"] = False
            response_validation["error"] = str(exc)
    if not result.get("ok"):
        state = "FAILED"
    elif not response_validation["valid"]:
        state = "BLOCKED"
    elif (
        response_validation["structured_response"]
        and response_validation["structured_response"]["verdict"] == "BLOCKED"
    ):
        state = "BLOCKED"
    else:
        state = "COMPLETED"
    cost = {
        "value_usd": cost_usd,
        "source": cost_source,
        "accepted_unknown": accepted_unknown,
    }
    report_artifact, report_sha = _write_report_artifact(
        repo_root, work_order, result, cost, response_validation
    )
    manifest = local_factory.load_program(ledger_root)
    attempt = local_factory.make_attempt(
        manifest,
        work_order["lane"],
        role,
        work_order["execution_attempt_id"],
        state=state,
        cost_usd=cost_usd,
        cost_source=cost_source,
        accepted_unknown=accepted_unknown,
        predecessor_attempt_id=work_order["predecessor_attempt_id"],
        runtime_session_id=(ledger_row or {}).get("session_id"),
        continuity_mode=work_order["continuity_mode"],
        task_id=work_order["task_id"],
        wave=work_order["wave"],
    )
    # Preserve the ID validated before dispatch instead of minting a second identity.
    attempt["execution_attempt_id"] = work_order["execution_attempt_id"]
    attempt_event = local_factory.append_event(
        ledger_root,
        work_order["lane"],
        "attempt",
        attempt,
        f"attempt:{work_order['execution_attempt_id']}",
    )
    bounded_result = {
        "ok": bool(result.get("ok")),
        "state": state,
        "spec": spec_slug,
        "node_id": result.get("node_id"),
        "dry_run": not live,
        "verdict": result.get("verdict"),
        "exit_code": result.get("exit_code"),
        "work_order_sha256": validated["canonical_sha256"],
        "report_artifact": report_artifact,
        "report_sha256": report_sha,
    }
    contribution_id = str(
        uuid.uuid5(
            uuid.UUID(manifest["program_id"]),
            f"contribution:{work_order['execution_attempt_id']}",
        )
    )
    contribution = local_factory.append_event(
        ledger_root,
        work_order["lane"],
        "contribution",
        {
            "contribution_id": contribution_id,
            "execution_attempt_id": work_order["execution_attempt_id"],
            "category": "probe",
            "epistemic_status": "supported" if state == "COMPLETED" else "contested",
            "gate_effect": "informational" if state == "COMPLETED" else "degrade-to-suspect",
            "report_sha256": report_sha,
            "evidence_chain": [
                f"work-order:{validated['canonical_sha256']}",
                f"spec:{spec_slug}",
                f"artifact:{report_artifact}",
                f"sha256:{report_sha}",
            ],
            "disposition": "accepted" if state == "COMPLETED" else "contested",
        },
        f"contribution:{work_order['execution_attempt_id']}",
    )
    return {
        "validated": validated,
        "attempt_event": attempt_event,
        "contribution_event": contribution,
        "dispatch": bounded_result,
        "cost": cost,
    }


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise RunnerError("JSON input must be an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("make", help="build and save a validated first-attempt work order")
    make.add_argument("--ledger-root", required=True)
    make.add_argument("--repo-root", default=str(REPO))
    make.add_argument("--lane", required=True, choices=local_factory.LANES)
    make.add_argument("--role", required=True, choices=sorted(ROLE_TO_SPEC))
    make.add_argument("--wave", required=True, choices=sorted(protocol.WAVES))
    make.add_argument("--task-id", required=True)
    make.add_argument("--attempt-key", required=True)
    make.add_argument("--read-path", action="append", required=True)
    make.add_argument("--owned-output-path", action="append", required=True)
    make.add_argument("--forbidden-path", action="append", default=[])
    make.add_argument("--partition-command", required=True)
    make.add_argument("--heartbeat-row-id", required=True)
    make.add_argument("--product-target-run-id")
    make.add_argument("--lesson-manifest-file")
    make.add_argument("--predecessor-attempt-id")
    make.add_argument("--predecessor-runtime-session-id")
    make.add_argument(
        "--continuity-mode",
        choices=("AUTHOR_RESUME", "SUCCESSOR_RECONSTRUCTION"),
        default="AUTHOR_RESUME",
    )
    make.add_argument("--state-capsule-file")
    run = sub.add_parser("run", help="validate and dispatch one work order")
    run.add_argument("--ledger-root", required=True)
    run.add_argument("--repo-root", default=str(REPO))
    run.add_argument("--work-order", required=True)
    run.add_argument("--ledger-dir")
    run.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "make":
            order = build_work_order(
                args.ledger_root,
                args.repo_root,
                lane=args.lane,
                role=args.role,
                wave=args.wave,
                task_id=args.task_id,
                attempt_key=args.attempt_key,
                read_paths=args.read_path,
                owned_output_paths=args.owned_output_path,
                forbidden_paths=args.forbidden_path,
                partition_command=args.partition_command,
                heartbeat_row_id=args.heartbeat_row_id,
                product_target_run_id=args.product_target_run_id,
                lesson_manifest=(
                    _load_json(args.lesson_manifest_file).get("lessons")
                    if args.lesson_manifest_file
                    else None
                ),
                predecessor_attempt_id=args.predecessor_attempt_id,
                predecessor_runtime_session_id=args.predecessor_runtime_session_id,
                continuity_mode=args.continuity_mode,
                state_capsule=(
                    Path(args.state_capsule_file).read_text()
                    if args.state_capsule_file
                    else None
                ),
            )
            output = {"work_order": str(save_work_order(args.ledger_root, order)), "data": order}
        else:
            output = run_work_order(
                args.ledger_root,
                args.repo_root,
                _load_json(args.work_order),
                live=args.live,
                ledger_dir=args.ledger_dir,
            )
        print(json.dumps(output, sort_keys=True))
        return 0
    except (
        RunnerError,
        protocol.ProtocolError,
        local_factory.LedgerError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
