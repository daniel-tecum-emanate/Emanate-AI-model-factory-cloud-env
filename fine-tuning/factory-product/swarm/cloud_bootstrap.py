"""Deterministic dual-lane cloud launch manifest generation.

Builds :class:`workspace.LaneLaunchManifest` objects from on-disk inputs, hashes
every concrete input byte-for-byte, and writes immutable lane manifests under
``runs/<run-lane>/swarm/cloud/manifest.json``.  Missing or excluded inputs fail
closed before any manifest is written.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

try:
    from . import workspace
except ImportError:  # pragma: no cover
    import workspace  # type: ignore


PROGRAM_ID = "ac51d99e-ea11-5691-a51f-aee4c982b0c8"

RUN_LANE = {
    "ptc": "ptc-steel",
    "grand-steel": "grand-steel",
}

ROLE_SLUGS = {
    "LANE-COORD": "lane-coord",
    "RESCOPE-DATA-BUILDER": "rescope-builder",
    "CORPUS-VERIFIER": "corpus-verifier",
    "EVAL-ARCH": "eval-architect",
    "PROVIDER-COST-VERIFIER": "cost-verifier",
    "REDTEAM": "redteam",
    "BASE1-HARNESS": "base1-harness",
    "RUNTIME-CONTROL": "runtime-control",
    "CONDITIONAL-CORPUS-ARCH": "conditional-corpus",
    "SERVE-ROLLBACK-ARCH": "serve-rollback",
}

SHARED_PROGRAM_DOCS = (
    "fine-tuning/program-coordination/PROGRAM.md",
    "fine-tuning/program-coordination/BOARD.md",
    "fine-tuning/program-coordination/LESSON-LOCK.md",
    "fine-tuning/program-coordination/CLOUD-ROLE-CONTRACTS.md",
)

PTC_SWARM_ROOT = "fine-tuning/factory-product/runs/ptc-steel/swarm"
GS_SWARM_ROOT = "fine-tuning/factory-product/runs/grand-steel/swarm"
PTC_CORPUS_ROOT = "fine-tuning/factory-product/runs/ptc-steel/corpus"

EXCLUDED_PATH_PARTS = frozenset({"__pycache__", ".pytest_cache"})
EXCLUDED_FILE_SUFFIXES = (".pyc",)
EXCLUDED_CORPUS_FILES = frozenset(
    {
        f"{PTC_CORPUS_ROOT}/build/ptc-corpus-v6.jsonl",
    }
)

TERMINAL_CRITERIA: Mapping[str, tuple[str, ...]] = {
    "ptc": (
        "All six PTC cloud roles have terminal REPORT.json artifacts with "
        "reconciled or accepted-unknown costs.",
        "A rebuilt, linted, signed 2,569-record rescope exists with "
        "provider-ready token measurement under the cumulative "
        "strictly-below-$110 ceiling.",
        "No training, dataset upload, paid evaluation, deployment, or "
        "production mutation occurred.",
    ),
    "grand-steel": (
        "All six Grand Steel cloud roles have terminal REPORT.json artifacts "
        "with reconciled or accepted-unknown costs.",
        "Deterministic BASE-1 and runtime-control harnesses exist with "
        "dry-run self-tests; no paid scored cells executed.",
        "Score-free evaluation and rollback contracts are frozen; conditional "
        "corpus design exists or FINALIZED_NO_TRAIN is recorded.",
    ),
}

PTC_CORPUS_SOURCE_SUFFIXES = (".py", ".md", ".json", ".ts")
PTC_CORPUS_SOURCE_NAMES = frozenset(
    {
        "BUILD-SPEC.md",
        "WORK-ORDER-STATUS.md",
    }
)

PTC_BUILD_ALLOWLIST = frozenset(
    {
        "action_cf.jsonl",
        "action_cf.summary.json",
        "stale_recall.jsonl",
        "stale_recall.summary.json",
        "twins.jsonl",
        "twins.summary.json",
        "replay-candidates.jsonl",
        "replay-merge-report.json",
        "replay-window-cost-base.json",
        "replay-window-cost-v3.json",
        "assembly-manifest.json",
        "census.json",
    }
)
PTC_BUILD_ROOT = f"{PTC_CORPUS_ROOT}/build"

GS_EVAL_SCRIPTS = (
    "fine-tuning/factory-product/runs/grand-steel/gs_eval/gs_runpod_eval2.py",
    "fine-tuning/factory-product/runs/grand-steel/gs_eval/gs_eval_common.py",
    "fine-tuning/factory-product/runs/grand-steel/gs_eval/build_scenarios.py",
    "fine-tuning/factory-product/runs/grand-steel/gs_eval/grade.py",
    "fine-tuning/factory-product/runs/grand-steel/gs_eval/self_test.py",
)

GS_CORPUS_REFERENCE = (
    "fine-tuning/factory-product/runs/grand-steel/gs_corpus/gs_common.py",
    "fine-tuning/factory-product/runs/grand-steel/gs_corpus/assemble.py",
    "fine-tuning/factory-product/runs/grand-steel/gs_corpus/verify_corpus_gs.py",
)

COMPANY_BRAIN_GS = {
    "shadow_readiness": "company-brain/3-execution/reports/GS-SHADOW-READINESS.md",
    "eval_battery": "company-brain/3-execution/reports/gs-v1/EVAL-BATTERY.md",
    "eval_results": "company-brain/3-execution/reports/gs-v1/EVAL-RESULTS.md",
}


@dataclass(frozen=True)
class RoleLaunchSpec:
    role_name: str
    read_paths: tuple[str, ...]
    required_inputs: tuple[str, ...]


def _fail(code: str, path: str, message: str) -> None:
    raise workspace.WorkspaceError(code, path, message)


def _repo_path(repo_root: Path, relative: str) -> Path:
    candidate = (repo_root / relative).resolve()
    try:
        candidate.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise workspace.WorkspaceError(
            "PATH", relative, "path escapes repository root"
        ) from exc
    return candidate


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path_within(path: str, parent: str) -> bool:
    child_parts = PurePosixPath(path).parts
    parent_parts = PurePosixPath(parent).parts
    return child_parts[: len(parent_parts)] == parent_parts


def _is_excluded_path(relative: str) -> bool:
    if relative in EXCLUDED_CORPUS_FILES:
        return True
    parts = PurePosixPath(relative).parts
    if any(part in EXCLUDED_PATH_PARTS for part in parts):
        return True
    return any(relative.endswith(suffix) for suffix in EXCLUDED_FILE_SUFFIXES)


def _swarm_report_paths(swarm_root: str) -> tuple[str, ...]:
    return (
        f"{swarm_root}/lesson-registry.json",
        f"{swarm_root}/reports/w1-cost-reprobe.json",
        f"{swarm_root}/reports/w1-data-arch-live-v1.json",
        f"{swarm_root}/reports/w1-data-arch-input-hash-verification.json",
        f"{swarm_root}/reports/w1-keeper-live-v2.json",
        f"{swarm_root}/reports/w1-twin-lineage-audit.json",
        f"{swarm_root}/reports/w2-corpus-arch-v1.json",
        f"{swarm_root}/reports/w2-eval-dev-v1.json",
        f"{swarm_root}/reports/w2-eval-dev-v2.json",
        f"{swarm_root}/reports/w2-lane-aggregation-v1.json",
        f"{swarm_root}/reports/w2-redteam-twin-conflict-v1.json",
        f"{swarm_root}/reports/w2-canonical-report-hash-verification.json",
        f"{swarm_root}/reports/w2-conflict-resolution.json",
        f"{swarm_root}/reports/w2-twin-falsifier-resolution.json",
        f"{swarm_root}/reports/w3-pretraining-decision-packet-v1.json",
        f"{swarm_root}/reports/live-lane-coord-w0.json",
    )


def _grand_steel_swarm_paths() -> tuple[str, ...]:
    return (
        f"{GS_SWARM_ROOT}/accepted-evidence-ledger.json",
        f"{GS_SWARM_ROOT}/development-partition-manifest.json",
        f"{GS_SWARM_ROOT}/eval-lesson-manifest.json",
        f"{GS_SWARM_ROOT}/eval-methodology-lessons.json",
        f"{GS_SWARM_ROOT}/eval-prereg-evidence-ledger.json",
        f"{GS_SWARM_ROOT}/eval-prereg-lesson-manifest.json",
        f"{GS_SWARM_ROOT}/lesson-registry.json",
        f"{GS_SWARM_ROOT}/redteam-lesson-manifest.json",
        f"{GS_SWARM_ROOT}/serve-lesson-manifest.json",
        f"{GS_SWARM_ROOT}/reports/live-lane-coord-w0.json",
        f"{GS_SWARM_ROOT}/reports/w1-cost-reprobe.json",
        f"{GS_SWARM_ROOT}/reports/w1-data-arch-input-hash-verification.json",
        f"{GS_SWARM_ROOT}/reports/w1-data-arch-live-v2.json",
        f"{GS_SWARM_ROOT}/reports/w1-input-hash-verification.json",
        f"{GS_SWARM_ROOT}/reports/w1-keeper-live-v2.json",
        f"{GS_SWARM_ROOT}/reports/w1-keeper-live.json",
        f"{GS_SWARM_ROOT}/reports/w2-eval-dev-score-free-v1.json",
        f"{GS_SWARM_ROOT}/reports/w2-eval-dev-v1.json",
        f"{GS_SWARM_ROOT}/reports/w2-eval-dev-v2.json",
        f"{GS_SWARM_ROOT}/reports/w2-gate-and-integrity-resolution.json",
        f"{GS_SWARM_ROOT}/reports/w2-lane-aggregation-v1.json",
        f"{GS_SWARM_ROOT}/reports/w2-redteam-v1.json",
        f"{GS_SWARM_ROOT}/reports/w2-serve-arch-input-hash-verification.json",
        f"{GS_SWARM_ROOT}/reports/w2-serve-arch-v1.json",
        f"{GS_SWARM_ROOT}/reports/w3-pretraining-decision-packet-v1.json",
        f"{GS_SWARM_ROOT}/reports/w3-pretraining-decision-resolution.json",
        COMPANY_BRAIN_GS["shadow_readiness"],
        COMPANY_BRAIN_GS["eval_battery"],
        COMPANY_BRAIN_GS["eval_results"],
    )


def ptc_build_allowlist_paths() -> tuple[str, ...]:
    return tuple(f"{PTC_BUILD_ROOT}/{name}" for name in sorted(PTC_BUILD_ALLOWLIST))


def collect_ptc_build_allowlist(repo_root: Path) -> tuple[str, ...]:
    """Return committed immutable build artifacts required for no-paid-call rescope."""
    paths = ptc_build_allowlist_paths()
    for relative in paths:
        if _is_excluded_path(relative):
            _fail("EXCLUDED_INPUT", relative, "build allowlist path is explicitly excluded")
        target = _repo_path(repo_root, relative)
        if not target.is_file():
            _fail("MISSING_INPUT", relative, "required build allowlist file is missing")
    return paths


def collect_ptc_corpus_sources(repo_root: Path) -> tuple[str, ...]:
    """Return bounded PTC corpus source paths, excluding cache and the v6 jsonl."""
    corpus_dir = _repo_path(repo_root, PTC_CORPUS_ROOT)
    if not corpus_dir.is_dir():
        _fail("MISSING_INPUT", PTC_CORPUS_ROOT, "corpus directory is missing")

    build_allowlist = collect_ptc_build_allowlist(repo_root)
    discovered: list[str] = list(build_allowlist)
    for path in sorted(corpus_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(repo_root.resolve()).as_posix()
        if _is_excluded_path(relative):
            continue
        if "/build/" in f"/{relative}/":
            if path.name not in PTC_BUILD_ALLOWLIST:
                continue
        name = path.name
        if name in PTC_CORPUS_SOURCE_NAMES or name.endswith(PTC_CORPUS_SOURCE_SUFFIXES):
            discovered.append(relative)
            continue
        if "/tests/" in relative or "/inputs/" in relative:
            discovered.append(relative)
    if not discovered:
        _fail("MISSING_INPUT", PTC_CORPUS_ROOT, "no bounded corpus sources found")
    return tuple(sorted(set(discovered)))


def collect_directory_files(repo_root: Path, relative_root: str) -> tuple[str, ...]:
    root = _repo_path(repo_root, relative_root)
    if not root.is_dir():
        _fail("MISSING_INPUT", relative_root, "directory is missing")
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not _is_excluded_path(
            path.relative_to(repo_root.resolve()).as_posix()
        ):
            files.append(path.relative_to(repo_root.resolve()).as_posix())
    return tuple(files)


def hash_required_inputs(repo_root: Path, paths: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in sorted(set(paths)):
        if _is_excluded_path(relative):
            _fail("EXCLUDED_INPUT", relative, "path is explicitly excluded")
        target = _repo_path(repo_root, relative)
        if not target.is_file():
            _fail("MISSING_INPUT", relative, "required input file is missing")
        result[relative] = _sha256_file(target)
    return result


def owned_output_path(lane: str, role_name: str) -> str:
    slug = ROLE_SLUGS[role_name]
    run_lane = RUN_LANE[lane]
    return f"fine-tuning/factory-product/runs/{run_lane}/swarm/cloud/roles/{slug}"


def manifest_output_path(lane: str) -> str:
    run_lane = RUN_LANE[lane]
    return f"fine-tuning/factory-product/runs/{run_lane}/swarm/cloud/manifest.json"


def _initial_state_capsule(role_name: str) -> str:
    return "\n".join(
        [
            f"Role: {role_name}",
            "Open claims: none",
            "Blockers: initial cloud launch pending",
            "Changed assumptions: none",
        ]
    )


def _initial_next_prompt(lane: str, role_name: str) -> str:
    slug = ROLE_SLUGS[role_name]
    run_lane = RUN_LANE[lane]
    return "\n".join(
        [
            "# NEXT-PROMPT",
            f"LOGICAL_ROLE: {PROGRAM_ID}/{lane}/{role_name}",
            f"OWNED_OUTPUT: fine-tuning/factory-product/runs/{run_lane}/swarm/cloud/roles/{slug}/",
            "FORBIDDEN: training, paid inference, deployment, production writes, cross-lane reads",
            "COST: null until a cloud receipt exists",
            "CONTINUITY: SUCCESSOR_RECONSTRUCTION on resume; do not claim author continuity",
            "TERMINAL: satisfy all lane terminal_criteria in manifest.json",
        ]
    )


def _ptc_role_specs(
    corpus_sources: Sequence[str],
    build_allowlist: Sequence[str],
) -> tuple[RoleLaunchSpec, ...]:
    ptc_swarm_reports = _swarm_report_paths(PTC_SWARM_ROOT)
    pr_eval = "PRs/ptc-retrain-v6/EVAL-PLAN.md"
    pr_corpus = "PRs/ptc-retrain-v6/CORPUS-EVIDENCE.md"
    pr_champion = "PRs/ptc-retrain-v6/CHAMPION-PROFILE.md"
    return (
        RoleLaunchSpec(
            "LANE-COORD",
            (
                *SHARED_PROGRAM_DOCS,
                PTC_SWARM_ROOT,
            ),
            (
                *SHARED_PROGRAM_DOCS,
                f"{PTC_SWARM_ROOT}/lesson-registry.json",
                *ptc_swarm_reports,
            ),
        ),
        RoleLaunchSpec(
            "RESCOPE-DATA-BUILDER",
            (
                PTC_CORPUS_ROOT,
                f"{PTC_SWARM_ROOT}/reports/w2-corpus-arch-v1.json",
                f"{PTC_SWARM_ROOT}/reports/w1-cost-reprobe.json",
                pr_corpus,
            ),
            (
                *corpus_sources,
                f"{PTC_SWARM_ROOT}/reports/w2-corpus-arch-v1.json",
                f"{PTC_SWARM_ROOT}/reports/w1-cost-reprobe.json",
                pr_corpus,
            ),
        ),
        RoleLaunchSpec(
            "CORPUS-VERIFIER",
            (
                f"{PTC_CORPUS_ROOT}/verify_corpus_ptc.py",
                f"{PTC_CORPUS_ROOT}/BUILD-SPEC.md",
                f"{PTC_CORPUS_ROOT}/inputs/envelope.json",
                f"{PTC_CORPUS_ROOT}/inputs/heldout-seal.json",
                PTC_BUILD_ROOT,
                f"{PTC_SWARM_ROOT}/corpus-lesson-manifest.json",
                f"{PTC_SWARM_ROOT}/reports/w1-twin-lineage-audit.json",
            ),
            (
                f"{PTC_CORPUS_ROOT}/verify_corpus_ptc.py",
                f"{PTC_CORPUS_ROOT}/BUILD-SPEC.md",
                f"{PTC_CORPUS_ROOT}/inputs/envelope.json",
                f"{PTC_CORPUS_ROOT}/inputs/heldout-seal.json",
                *build_allowlist,
                f"{PTC_SWARM_ROOT}/corpus-lesson-manifest.json",
                f"{PTC_SWARM_ROOT}/reports/w1-twin-lineage-audit.json",
            ),
        ),
        RoleLaunchSpec(
            "EVAL-ARCH",
            (
                pr_eval,
                f"{PTC_SWARM_ROOT}/eval-lesson-manifest.json",
                f"{PTC_SWARM_ROOT}/ptc-eval-statistical-contract.json",
                f"{PTC_SWARM_ROOT}/reports/w2-eval-dev-v1.json",
                f"{PTC_SWARM_ROOT}/reports/w2-eval-dev-v2.json",
            ),
            (
                pr_eval,
                f"{PTC_SWARM_ROOT}/eval-lesson-manifest.json",
                f"{PTC_SWARM_ROOT}/ptc-eval-statistical-contract.json",
                f"{PTC_SWARM_ROOT}/reports/w2-eval-dev-v1.json",
                f"{PTC_SWARM_ROOT}/reports/w2-eval-dev-v2.json",
            ),
        ),
        RoleLaunchSpec(
            "PROVIDER-COST-VERIFIER",
            (
                f"{PTC_SWARM_ROOT}/reports/w1-cost-reprobe.json",
                f"{PTC_SWARM_ROOT}/reports/w2-corpus-arch-v1.json",
                f"{PTC_SWARM_ROOT}/reports/w2-conflict-resolution.json",
            ),
            (
                f"{PTC_SWARM_ROOT}/reports/w1-cost-reprobe.json",
                f"{PTC_SWARM_ROOT}/reports/w2-corpus-arch-v1.json",
                f"{PTC_SWARM_ROOT}/reports/w2-conflict-resolution.json",
            ),
        ),
        RoleLaunchSpec(
            "REDTEAM",
            (
                f"{PTC_SWARM_ROOT}/redteam-lesson-manifest.json",
                f"{PTC_SWARM_ROOT}/reports/w2-redteam-twin-conflict-v1.json",
                f"{PTC_SWARM_ROOT}/reports/w2-twin-falsifier-resolution.json",
                pr_corpus,
                pr_champion,
            ),
            (
                f"{PTC_SWARM_ROOT}/redteam-lesson-manifest.json",
                f"{PTC_SWARM_ROOT}/reports/w2-redteam-twin-conflict-v1.json",
                f"{PTC_SWARM_ROOT}/reports/w2-twin-falsifier-resolution.json",
                pr_corpus,
                pr_champion,
            ),
        ),
    )


def _grand_steel_role_specs() -> tuple[RoleLaunchSpec, ...]:
    gs_swarm = _grand_steel_swarm_paths()
    return (
        RoleLaunchSpec(
            "LANE-COORD",
            (
                *SHARED_PROGRAM_DOCS,
                GS_SWARM_ROOT,
            ),
            (
                *SHARED_PROGRAM_DOCS,
                *gs_swarm,
            ),
        ),
        RoleLaunchSpec(
            "BASE1-HARNESS",
            (
                "fine-tuning/factory-product/runs/grand-steel/gs_eval",
                COMPANY_BRAIN_GS["eval_battery"],
                COMPANY_BRAIN_GS["eval_results"],
                f"{GS_SWARM_ROOT}/reports/w2-serve-arch-v1.json",
                f"{GS_SWARM_ROOT}/development-partition-manifest.json",
            ),
            (
                *GS_EVAL_SCRIPTS,
                COMPANY_BRAIN_GS["eval_battery"],
                COMPANY_BRAIN_GS["eval_results"],
                f"{GS_SWARM_ROOT}/reports/w2-serve-arch-v1.json",
                f"{GS_SWARM_ROOT}/development-partition-manifest.json",
            ),
        ),
        RoleLaunchSpec(
            "RUNTIME-CONTROL",
            (
                "fine-tuning/factory-product/runs/grand-steel/gs_eval",
                COMPANY_BRAIN_GS["shadow_readiness"],
                f"{GS_SWARM_ROOT}/eval-methodology-lessons.json",
                f"{GS_SWARM_ROOT}/reports/w2-serve-arch-v1.json",
            ),
            (
                *GS_EVAL_SCRIPTS,
                COMPANY_BRAIN_GS["shadow_readiness"],
                f"{GS_SWARM_ROOT}/eval-methodology-lessons.json",
                f"{GS_SWARM_ROOT}/reports/w2-serve-arch-v1.json",
            ),
        ),
        RoleLaunchSpec(
            "CONDITIONAL-CORPUS-ARCH",
            (
                "fine-tuning/factory-product/runs/grand-steel/gs_corpus",
                f"{GS_SWARM_ROOT}/accepted-evidence-ledger.json",
                f"{GS_SWARM_ROOT}/reports/w2-redteam-v1.json",
            ),
            (
                *GS_CORPUS_REFERENCE,
                f"{GS_SWARM_ROOT}/accepted-evidence-ledger.json",
                f"{GS_SWARM_ROOT}/reports/w2-redteam-v1.json",
            ),
        ),
        RoleLaunchSpec(
            "EVAL-ARCH",
            (
                COMPANY_BRAIN_GS["eval_battery"],
                f"{GS_SWARM_ROOT}/development-partition-manifest.json",
                f"{GS_SWARM_ROOT}/eval-methodology-lessons.json",
                f"{GS_SWARM_ROOT}/eval-prereg-evidence-ledger.json",
                f"{GS_SWARM_ROOT}/eval-prereg-lesson-manifest.json",
                f"{GS_SWARM_ROOT}/accepted-evidence-ledger.json",
            ),
            (
                COMPANY_BRAIN_GS["eval_battery"],
                f"{GS_SWARM_ROOT}/development-partition-manifest.json",
                f"{GS_SWARM_ROOT}/eval-methodology-lessons.json",
                f"{GS_SWARM_ROOT}/eval-prereg-evidence-ledger.json",
                f"{GS_SWARM_ROOT}/eval-prereg-lesson-manifest.json",
                f"{GS_SWARM_ROOT}/accepted-evidence-ledger.json",
            ),
        ),
        RoleLaunchSpec(
            "SERVE-ROLLBACK-ARCH",
            (
                COMPANY_BRAIN_GS["shadow_readiness"],
                f"{GS_SWARM_ROOT}/serve-lesson-manifest.json",
                f"{GS_SWARM_ROOT}/reports/w2-serve-arch-v1.json",
                f"{GS_SWARM_ROOT}/reports/w3-pretraining-decision-packet-v1.json",
                f"{GS_SWARM_ROOT}/reports/w3-pretraining-decision-resolution.json",
            ),
            (
                COMPANY_BRAIN_GS["shadow_readiness"],
                f"{GS_SWARM_ROOT}/serve-lesson-manifest.json",
                f"{GS_SWARM_ROOT}/reports/w2-serve-arch-v1.json",
                f"{GS_SWARM_ROOT}/reports/w3-pretraining-decision-packet-v1.json",
                f"{GS_SWARM_ROOT}/reports/w3-pretraining-decision-resolution.json",
            ),
        ),
    )


def role_launch_specs(lane: str, repo_root: Path) -> tuple[RoleLaunchSpec, ...]:
    if lane == "ptc":
        build_allowlist = collect_ptc_build_allowlist(repo_root)
        corpus_sources = collect_ptc_corpus_sources(repo_root)
        return _ptc_role_specs(corpus_sources, build_allowlist)
    if lane == "grand-steel":
        return _grand_steel_role_specs()
    _fail("LANE", "$.lane", f"unsupported lane: {lane}")


def build_persistent_roles(
    lane: str, specs: Sequence[RoleLaunchSpec]
) -> tuple[workspace.PersistentRole, ...]:
    roles = []
    for spec in specs:
        if spec.role_name not in workspace.LANE_ROLE_NAMES[lane]:
            _fail("ROLE", "$.role_name", f"{spec.role_name} is not a {lane} cloud role")
        roles.append(
            workspace.PersistentRole.create(
                PROGRAM_ID,
                lane,
                spec.role_name,
                read_paths=list(spec.read_paths),
                owned_output_paths=[owned_output_path(lane, spec.role_name)],
                budget_usd=None,
            )
        )
    return tuple(roles)


def build_lane_workspace(
    lane: str, repo_root: Path, *, created_at: datetime
) -> workspace.LaneWorkspace:
    specs = role_launch_specs(lane, repo_root)
    roles = build_persistent_roles(lane, specs)
    return workspace.LaneWorkspace.create(PROGRAM_ID, lane, roles, created_at=created_at)


def build_lane_launch_manifest(
    lane: str,
    repo_root: Path,
    *,
    validation_time: datetime,
    created_at: datetime | None = None,
) -> workspace.LaneLaunchManifest:
    created_at = created_at or validation_time
    specs = role_launch_specs(lane, repo_root)
    lane_workspace = build_lane_workspace(lane, repo_root, created_at=created_at)

    required_inputs: list[str] = []
    for spec in specs:
        required_inputs.extend(spec.required_inputs)
    input_hashes = hash_required_inputs(repo_root, required_inputs)

    if lane == "grand-steel":
        eval_arch = next(
            role for role in lane_workspace.roles if role.role_name == "EVAL-ARCH"
        )
        forbidden = COMPANY_BRAIN_GS["eval_results"]
        if forbidden in eval_arch.read_paths or forbidden in input_hashes and any(
            _path_within(forbidden, read_scope) for read_scope in eval_arch.read_paths
        ):
            _fail(
                "FORBIDDEN_INPUT",
                forbidden,
                "Grand Steel EVAL-ARCH may not receive historical score results",
            )

    leases = [
        workspace.Lease.issue(role.logical_role_id, now=validation_time)
        for role in lane_workspace.roles
    ]
    attempts = []
    for index, (role, lease) in enumerate(zip(lane_workspace.roles, leases)):
        continuation = workspace.ContinuationEnvelope.create(
            role.logical_role_id,
            continuity_mode="AUTHOR_RESUME",
            predecessor_attempt_id=None,
            predecessor_runtime_session_id=None,
            predecessor_report_sha256=None,
            state_capsule=_initial_state_capsule(role.role_name),
            next_prompt=_initial_next_prompt(lane, role.role_name),
        )
        attempts.append(
            workspace.CloudAttempt.create(
                role,
                lease,
                continuation,
                attempt_key=f"cloud-launch-{ROLE_SLUGS[role.role_name]}",
                runtime_session_id=f"cloud-{lane}-{index}",
                state="PENDING",
                cost_usd=None,
                cost_source="cloud-runtime-unreported",
                cost_evidence_sha256=None,
                report_sha256=None,
            )
        )

    return workspace.LaneLaunchManifest.create(
        lane_workspace,
        input_hashes=input_hashes,
        leases=leases,
        initial_attempts=attempts,
        terminal_criteria=TERMINAL_CRITERIA[lane],
        validation_time=validation_time,
    )


def write_lane_manifest(
    repo_root: Path,
    lane: str,
    manifest: workspace.LaneLaunchManifest,
) -> Path:
    target = _repo_path(repo_root, manifest_output_path(lane))
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = manifest.to_json() + "\n"
    if target.exists() and target.read_text() != encoded:
        _fail(
            "IMMUTABLE_MANIFEST",
            manifest_output_path(lane),
            "refusing to overwrite changed manifest.json",
        )
    target.write_text(encoded)
    return target


def bootstrap_lane(
    repo_root: Path,
    lane: str,
    *,
    validation_time: datetime | None = None,
    write: bool = True,
) -> workspace.LaneLaunchManifest:
    stamp = validation_time or datetime.now(timezone.utc)
    manifest = build_lane_launch_manifest(lane, repo_root, validation_time=stamp)
    if write:
        write_lane_manifest(repo_root, lane, manifest)
    return manifest


def bootstrap_all(
    repo_root: Path,
    *,
    validation_time: datetime | None = None,
    write: bool = True,
) -> dict[str, workspace.LaneLaunchManifest]:
    stamp = validation_time or datetime.now(timezone.utc)
    manifests = {}
    for lane in workspace.LANES:
        manifests[lane] = bootstrap_lane(
            repo_root, lane, validation_time=stamp, write=write
        )
    return manifests
