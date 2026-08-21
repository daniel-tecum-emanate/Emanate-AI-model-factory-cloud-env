import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

FACTORY_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FACTORY_DIR))

from swarm import cloud_bootstrap, workspace  # noqa: E402


NOW = datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc)
SHA_A = "a" * 64


def touch(repo_root: Path, relative: str, content: str = "fixture\n") -> str:
    target = repo_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return hashlib.sha256(content.encode()).hexdigest()


def build_ptc_fixture(repo_root: Path) -> None:
    for doc in cloud_bootstrap.SHARED_PROGRAM_DOCS:
        touch(repo_root, doc, f"{doc}\n")
    for relative in cloud_bootstrap._swarm_report_paths(cloud_bootstrap.PTC_SWARM_ROOT):
        touch(repo_root, relative, json.dumps({"path": relative}) + "\n")
    touch(repo_root, f"{cloud_bootstrap.PTC_SWARM_ROOT}/lesson-registry.json")
    touch(repo_root, f"{cloud_bootstrap.PTC_SWARM_ROOT}/corpus-lesson-manifest.json")
    touch(repo_root, f"{cloud_bootstrap.PTC_SWARM_ROOT}/eval-lesson-manifest.json")
    touch(
        repo_root,
        f"{cloud_bootstrap.PTC_SWARM_ROOT}/ptc-eval-statistical-contract.json",
    )
    touch(repo_root, f"{cloud_bootstrap.PTC_SWARM_ROOT}/redteam-lesson-manifest.json")

    touch(repo_root, "PRs/ptc-retrain-v6/EVAL-PLAN.md", "# eval plan\n")
    touch(repo_root, "PRs/ptc-retrain-v6/CORPUS-EVIDENCE.md", "# corpus evidence\n")
    touch(repo_root, "PRs/ptc-retrain-v6/CHAMPION-PROFILE.md", "# champion\n")

    corpus_files = {
        f"{cloud_bootstrap.PTC_CORPUS_ROOT}/assemble.py": "def assemble():\n    pass\n",
        f"{cloud_bootstrap.PTC_CORPUS_ROOT}/generate_twins.py": "def twins():\n    pass\n",
        f"{cloud_bootstrap.PTC_CORPUS_ROOT}/verify_corpus_ptc.py": "def verify():\n    pass\n",
        f"{cloud_bootstrap.PTC_CORPUS_ROOT}/BUILD-SPEC.md": "# build spec\n",
        f"{cloud_bootstrap.PTC_CORPUS_ROOT}/inputs/envelope.json": "{}\n",
        f"{cloud_bootstrap.PTC_CORPUS_ROOT}/inputs/heldout-seal.json": "{}\n",
        f"{cloud_bootstrap.PTC_CORPUS_ROOT}/inputs/selection-pool.json": "[]\n",
        f"{cloud_bootstrap.PTC_CORPUS_ROOT}/inputs/train-pool.json": "[]\n",
        f"{cloud_bootstrap.PTC_CORPUS_ROOT}/tests/test_assemble.py": "def test_x():\n    pass\n",
    }
    for relative, content in corpus_files.items():
        touch(repo_root, relative, content)

    for name in cloud_bootstrap.PTC_BUILD_ALLOWLIST:
        touch(
            repo_root,
            f"{cloud_bootstrap.PTC_BUILD_ROOT}/{name}",
            json.dumps({"artifact": name}) + "\n",
        )

    excluded_build_files = {
        f"{cloud_bootstrap.PTC_CORPUS_ROOT}/build/ptc-corpus-v6.jsonl": "X" * 120,
        f"{cloud_bootstrap.PTC_CORPUS_ROOT}/build/ptc-corpus-v6.meta.jsonl": "meta\n",
        f"{cloud_bootstrap.PTC_CORPUS_ROOT}/build/replay-raw-base.jsonl": "raw-base\n",
        f"{cloud_bootstrap.PTC_CORPUS_ROOT}/build/replay-window-base.log": "log\n",
        f"{cloud_bootstrap.PTC_CORPUS_ROOT}/build/replay_window.py": "def replay():\n    pass\n",
    }
    for relative, content in excluded_build_files.items():
        touch(repo_root, relative, content)

    cache_dir = repo_root / cloud_bootstrap.PTC_CORPUS_ROOT / "__pycache__"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "assemble.cpython-313.pyc").write_bytes(b"cache")


def build_grand_steel_fixture(repo_root: Path) -> None:
    for doc in cloud_bootstrap.SHARED_PROGRAM_DOCS:
        touch(repo_root, doc, f"{doc}\n")
    for relative in cloud_bootstrap._grand_steel_swarm_paths():
        touch(repo_root, relative, json.dumps({"path": relative}) + "\n")
    for relative in cloud_bootstrap.GS_EVAL_SCRIPTS:
        touch(repo_root, relative, f"# {relative}\n")
    for relative in cloud_bootstrap.GS_CORPUS_REFERENCE:
        touch(repo_root, relative, f"# {relative}\n")
    # Present on disk but must never enter cloud launch scopes.
    touch(
        repo_root,
        "factory-automation/factory/runs/grand-steel/heldout/01-manifest.json",
        '{"heldout_identities": ["secret"]}\n',
    )


def build_fixture_repo(repo_root: Path) -> None:
    build_ptc_fixture(repo_root)
    build_grand_steel_fixture(repo_root)


def assert_code(code, callable_, *args, **kwargs):
    with pytest.raises(workspace.WorkspaceError) as caught:
        callable_(*args, **kwargs)
    assert caught.value.code == code


def test_bootstrap_generates_deterministic_two_lane_manifests(tmp_path):
    build_fixture_repo(tmp_path)

    first = cloud_bootstrap.bootstrap_all(tmp_path, validation_time=NOW, write=False)
    second = cloud_bootstrap.bootstrap_all(tmp_path, validation_time=NOW, write=False)

    for lane in workspace.LANES:
        assert len(first[lane].workspace.roles) == 6
        assert {role.role_name for role in first[lane].workspace.roles} == set(
            workspace.LANE_ROLE_NAMES[lane]
        )
        assert first[lane] == second[lane]
        assert first[lane].manifest_sha256 == second[lane].manifest_sha256
        assert first[lane].launch_manifest_id == second[lane].launch_manifest_id
        assert all(attempt.cost_usd is None for attempt in first[lane].initial_attempts)
        assert all(
            lease.expires_at == "2026-08-04T20:30:00Z" for lease in first[lane].leases
        )


def test_manifest_uses_program_id_and_role_slugs(tmp_path):
    build_fixture_repo(tmp_path)
    manifest = cloud_bootstrap.bootstrap_lane(
        tmp_path, "ptc", validation_time=NOW, write=False
    )

    assert manifest.workspace.program_id == cloud_bootstrap.PROGRAM_ID
    for role in manifest.workspace.roles:
        slug = cloud_bootstrap.ROLE_SLUGS[role.role_name]
        assert list(role.owned_output_paths) == [
            f"factory-automation/factory/runs/ptc-steel/swarm/cloud/roles/{slug}"
        ]
        assert role.logical_role_id == (
            f"{cloud_bootstrap.PROGRAM_ID}/ptc/{role.role_name}"
        )


def test_write_manifest_is_immutable_and_round_trips(tmp_path):
    build_fixture_repo(tmp_path)
    manifest = cloud_bootstrap.bootstrap_lane(
        tmp_path, "grand-steel", validation_time=NOW, write=True
    )
    target = cloud_bootstrap.write_lane_manifest(tmp_path, "grand-steel", manifest)

    assert target == tmp_path / cloud_bootstrap.manifest_output_path("grand-steel")
    parsed = workspace.LaneLaunchManifest.from_json(
        target.read_text(), validation_time=NOW
    )
    assert parsed == manifest

    mutated = json.loads(target.read_text())
    mutated["terminal_criteria"].append("mutated")
    target.write_text(json.dumps(mutated))

    assert_code(
        "IMMUTABLE_MANIFEST",
        cloud_bootstrap.write_lane_manifest,
        tmp_path,
        "grand-steel",
        manifest,
    )


def test_fail_closed_on_missing_required_input(tmp_path):
    build_ptc_fixture(tmp_path)
    (tmp_path / "PRs/ptc-retrain-v6/EVAL-PLAN.md").unlink()

    assert_code(
        "MISSING_INPUT",
        cloud_bootstrap.bootstrap_lane,
        tmp_path,
        "ptc",
        validation_time=NOW,
        write=False,
    )


def test_ptc_corpus_collection_excludes_v6_jsonl_and_cache(tmp_path):
    build_ptc_fixture(tmp_path)
    sources = cloud_bootstrap.collect_ptc_corpus_sources(tmp_path)
    build_root = cloud_bootstrap.PTC_BUILD_ROOT

    for name in cloud_bootstrap.PTC_BUILD_ALLOWLIST:
        assert f"{build_root}/{name}" in sources
    assert (
        f"{cloud_bootstrap.PTC_CORPUS_ROOT}/build/ptc-corpus-v6.jsonl" not in sources
    )
    assert f"{build_root}/ptc-corpus-v6.meta.jsonl" not in sources
    assert f"{build_root}/replay-raw-base.jsonl" not in sources
    assert f"{build_root}/replay-window-base.log" not in sources
    assert f"{build_root}/replay_window.py" not in sources
    assert not any("__pycache__" in path for path in sources)
    assert not any(path.endswith(".pyc") for path in sources)


def test_ptc_builder_and_verifier_hash_build_allowlist(tmp_path):
    build_ptc_fixture(tmp_path)
    manifest = cloud_bootstrap.bootstrap_lane(
        tmp_path, "ptc", validation_time=NOW, write=False
    )
    allowlist = cloud_bootstrap.ptc_build_allowlist_paths()

    builder = next(
        role for role in manifest.workspace.roles if role.role_name == "RESCOPE-DATA-BUILDER"
    )
    verifier = next(
        role for role in manifest.workspace.roles if role.role_name == "CORPUS-VERIFIER"
    )

    for relative in allowlist:
        expected = hashlib.sha256((tmp_path / relative).read_bytes()).hexdigest()
        assert manifest.input_hashes[relative] == expected
        assert relative in builder.read_paths or cloud_bootstrap.PTC_CORPUS_ROOT in builder.read_paths
        assert relative in verifier.read_paths or cloud_bootstrap.PTC_BUILD_ROOT in verifier.read_paths

    specs = cloud_bootstrap.role_launch_specs("ptc", tmp_path)
    builder_spec = next(spec for spec in specs if spec.role_name == "RESCOPE-DATA-BUILDER")
    verifier_spec = next(spec for spec in specs if spec.role_name == "CORPUS-VERIFIER")
    for relative in allowlist:
        assert relative in builder_spec.required_inputs
        assert relative in verifier_spec.required_inputs


def test_grand_steel_eval_arch_excludes_historical_score_results(tmp_path):
    build_grand_steel_fixture(tmp_path)
    manifest = cloud_bootstrap.bootstrap_lane(
        tmp_path, "grand-steel", validation_time=NOW, write=False
    )
    eval_arch = next(
        role for role in manifest.workspace.roles if role.role_name == "EVAL-ARCH"
    )
    forbidden = cloud_bootstrap.COMPANY_BRAIN_GS["eval_results"]

    assert forbidden not in eval_arch.read_paths
    assert not any(
        cloud_bootstrap._path_within(forbidden, scope)
        for scope in eval_arch.read_paths
    )
    eval_arch_spec = next(
        spec
        for spec in cloud_bootstrap.role_launch_specs("grand-steel", tmp_path)
        if spec.role_name == "EVAL-ARCH"
    )
    assert forbidden not in eval_arch_spec.required_inputs


GS_HELDOUT_MANIFEST = (
    "factory-automation/factory/runs/grand-steel/heldout/01-manifest.json"
)


def test_grand_steel_manifest_excludes_heldout_identities(tmp_path):
    build_grand_steel_fixture(tmp_path)
    manifest = cloud_bootstrap.bootstrap_lane(
        tmp_path, "grand-steel", validation_time=NOW, write=False
    )

    assert GS_HELDOUT_MANIFEST not in manifest.input_hashes
    for role in manifest.workspace.roles:
        assert GS_HELDOUT_MANIFEST not in role.read_paths
        assert not any(
            cloud_bootstrap._path_within(GS_HELDOUT_MANIFEST, scope)
            for scope in role.read_paths
        )
        assert not any(
            "heldout" in scope for scope in role.read_paths
        )


def test_terminal_criteria_match_plan(tmp_path):
    build_fixture_repo(tmp_path)
    ptc = cloud_bootstrap.bootstrap_lane(tmp_path, "ptc", validation_time=NOW, write=False)
    gs = cloud_bootstrap.bootstrap_lane(
        tmp_path, "grand-steel", validation_time=NOW, write=False
    )

    assert ptc.terminal_criteria == cloud_bootstrap.TERMINAL_CRITERIA["ptc"]
    assert gs.terminal_criteria == cloud_bootstrap.TERMINAL_CRITERIA["grand-steel"]
    assert "2,569-record rescope" in ptc.terminal_criteria[1]
    assert "BASE-1" in gs.terminal_criteria[1]


def test_input_hashes_cover_concrete_bytes(tmp_path):
    build_ptc_fixture(tmp_path)
    manifest = cloud_bootstrap.bootstrap_lane(
        tmp_path, "ptc", validation_time=NOW, write=False
    )
    sample = f"{cloud_bootstrap.PTC_CORPUS_ROOT}/assemble.py"
    expected = hashlib.sha256((tmp_path / sample).read_bytes()).hexdigest()
    assert manifest.input_hashes[sample] == expected


def test_owned_output_paths_are_pairwise_disjoint(tmp_path):
    build_fixture_repo(tmp_path)
    for lane in workspace.LANES:
        manifest = cloud_bootstrap.bootstrap_lane(
            tmp_path, lane, validation_time=NOW, write=False
        )
        owners = [path for role in manifest.workspace.roles for path in role.owned_output_paths]
        for left in owners:
            for right in owners:
                if left != right:
                    assert not cloud_bootstrap._path_within(left, right)
                    assert not cloud_bootstrap._path_within(right, left)
