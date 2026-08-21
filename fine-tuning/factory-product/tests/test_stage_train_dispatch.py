"""T2-3 (model-factory-finetune-launcher-v1, PRContext-phase2.md) — the real
trigger from `stage_train` to platform-alpha's `factory-launch-monitor`
Trigger.dev task, via `trigger_dev_rest.trigger_task`.

SAFETY: this suite must never make a real network call to Trigger.dev or
Fireworks. `trigger_dev_rest.trigger_task` and `factory_sync.get_or_create_run_id`
are patched in every test that reaches `_dispatch_launch_task`; the
`--dry-run` and missing-manifest tests assert the dispatch function is never
even called, i.e. no fetch is possible.

Covers the accept criterion from PRContext-phase2.md T2-3: "a dry-run (no
real spend) integration test proves stage_launch approval -> stage_train
successfully enqueues T2-1's task with the right config, without making any
real Fireworks call."
"""

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory  # noqa: E402

SLUG = "pytest-stage-train-dispatch"


def _args(dry_run=False, manifest=None, approved=True):
    return SimpleNamespace(
        dry_run=dry_run, manifest=manifest, approved=approved,
        sync_url=None, live=False, stream="per-account",
    )


def _cfg():
    return {
        "org": {"slug": SLUG, "name": "Pytest Stage Train Org"},
        "model_plan": {
            "base_model": "accounts/fireworks/models/qwen3p6-27b",
            "output_model": f"accounts/emanate/models/{SLUG}-v1-agent",
        },
        "training": {
            "lora_rank": 32, "epochs": 1, "learning_rate": 3e-6, "weight_decay": 0.0,
        },
    }


def _write_signed_manifest(tmp_path, sha256=None, signed=True):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text('{"messages": []}\n')
    real_sha = hashlib.sha256(corpus.read_bytes()).hexdigest()
    manifest_path = tmp_path / "signed-manifest.json"
    manifest_path.write_text(json.dumps({
        "dataset_file": str(corpus),
        "sha256": sha256 or real_sha,
        "signed_by": "daniel@emanate.ai" if signed else None,
        "signed_at": "2026-08-19T00:00:00Z" if signed else None,
    }))
    return manifest_path, real_sha


def _patch_run_dir(run_path):
    def fake_run_dir(slug, stream="per-account"):
        d = run_path if stream == "per-account" else run_path / stream
        (d / "approvals").mkdir(parents=True, exist_ok=True)
        return d
    return fake_run_dir


def _write_launch_report(run_path, train_all_in_usd=17.5):
    idx = factory.STAGES.index("launch") + 1
    (run_path / f"{idx:02d}-launch-report.json").write_text(json.dumps({
        "status": "approved",
        "projection": {"train_all_in_usd": train_all_in_usd, "total_projection_usd": train_all_in_usd + 15},
    }))


class TestDryRunNeverDispatches:
    def test_dry_run_never_calls_trigger_task(self, tmp_path):
        with patch("trigger_dev_rest.trigger_task") as mock_trigger, \
             patch.object(factory, "write_report", lambda *a, **k: None), \
             patch.object(factory, "_dispatch_stage_nodes", lambda *a, **k: []):
            factory.stage_train(_cfg(), SLUG, _args(dry_run=True, manifest=None))
        mock_trigger.assert_not_called()


class TestMissingManifest:
    def test_refuses_without_manifest_flag(self, tmp_path):
        run_path = tmp_path / SLUG
        run_path.mkdir()
        reports = {}

        def fake_write_report(slug, stage, body, cfg=None, sync_url=None):
            reports["body"] = body
            return run_path / "report.json"

        with patch("trigger_dev_rest.trigger_task") as mock_trigger, \
             patch.object(factory, "run_dir", _patch_run_dir(run_path)), \
             patch.object(factory, "write_report", fake_write_report), \
             patch.object(factory, "_dispatch_stage_nodes", lambda *a, **k: [{"ok": True}]):
            factory.stage_train(_cfg(), SLUG, _args(dry_run=False, manifest=None))
        mock_trigger.assert_not_called()
        assert reports["body"]["status"] == "blocked"
        assert reports["body"]["trigger_dispatch"]["ok"] is False
        assert "no --manifest" in reports["body"]["trigger_dispatch"]["error"]


class TestManifestVerification:
    def test_hash_mismatch_refuses_to_dispatch(self, tmp_path):
        manifest_path, _real_sha = _write_signed_manifest(tmp_path, sha256="deadbeef" * 8)
        run_path = tmp_path / SLUG
        run_path.mkdir()
        _write_launch_report(run_path)
        reports = {}

        def fake_write_report(slug, stage, body, cfg=None, sync_url=None):
            reports["body"] = body
            return run_path / "report.json"

        with patch("trigger_dev_rest.trigger_task") as mock_trigger, \
             patch.object(factory, "run_dir", _patch_run_dir(run_path)), \
             patch.object(factory, "write_report", fake_write_report), \
             patch.object(factory, "_dispatch_stage_nodes", lambda *a, **k: [{"ok": True}]):
            factory.stage_train(_cfg(), SLUG, _args(dry_run=False, manifest=str(manifest_path)))
        mock_trigger.assert_not_called()
        assert reports["body"]["status"] == "blocked"
        assert "MANIFEST MISMATCH" in reports["body"]["trigger_dispatch"]["error"]

    def test_unsigned_manifest_refuses_to_dispatch(self, tmp_path):
        manifest_path, _real_sha = _write_signed_manifest(tmp_path, signed=False)
        run_path = tmp_path / SLUG
        run_path.mkdir()
        _write_launch_report(run_path)
        reports = {}

        def fake_write_report(slug, stage, body, cfg=None, sync_url=None):
            reports["body"] = body
            return run_path / "report.json"

        with patch("trigger_dev_rest.trigger_task") as mock_trigger, \
             patch.object(factory, "run_dir", _patch_run_dir(run_path)), \
             patch.object(factory, "write_report", fake_write_report), \
             patch.object(factory, "_dispatch_stage_nodes", lambda *a, **k: [{"ok": True}]):
            factory.stage_train(_cfg(), SLUG, _args(dry_run=False, manifest=str(manifest_path)))
        mock_trigger.assert_not_called()
        assert "not signed" in reports["body"]["trigger_dispatch"]["error"]


class TestSuccessfulDispatch:
    def test_valid_manifest_dispatches_with_the_right_config(self, tmp_path):
        """The accept-criterion integration test: an approved S6g projection +
        a valid signed manifest -> stage_train enqueues T2-1's task with a
        config matching factory-launch-monitor.ts's FactoryLaunchConfig
        contract, and NO real Fireworks/Trigger.dev call is ever made (the
        trigger + run-id resolution are both mocked)."""
        manifest_path, real_sha = _write_signed_manifest(tmp_path)
        run_path = tmp_path / SLUG
        run_path.mkdir()
        _write_launch_report(run_path, train_all_in_usd=17.5)
        reports = {}

        def fake_write_report(slug, stage, body, cfg=None, sync_url=None):
            reports["body"] = body
            return run_path / "report.json"

        with patch("trigger_dev_rest.trigger_task", return_value="run_abc123") as mock_trigger, \
             patch("factory_sync.get_or_create_run_id", return_value="11111111-0000-0000-0000-000000000001") as mock_run_id, \
             patch.object(factory, "run_dir", _patch_run_dir(run_path)), \
             patch.object(factory, "write_report", fake_write_report), \
             patch.object(factory, "_dispatch_stage_nodes", lambda *a, **k: [{"ok": True}]):
            factory.stage_train(_cfg(), SLUG, _args(dry_run=False, manifest=str(manifest_path)))

        mock_trigger.assert_called_once()
        task_id, payload = mock_trigger.call_args.args[:2]
        assert task_id == "factory-launch-monitor"
        assert payload["runId"] == "11111111-0000-0000-0000-000000000001"
        assert payload["baseModel"] == "accounts/fireworks/models/qwen3p6-27b"
        assert payload["outputModelId"] == f"{SLUG}-v1-agent"
        assert payload["hyperparams"] == {
            "loraRank": 32, "epochs": 1, "learningRate": 3e-6, "optimizerWeightDecay": 0.0,
        }
        # Cost ceiling sourced from the S6g-approved projection, not a hardcoded default.
        assert payload["costCeilingUsd"] == 17.5
        assert payload["manifest"]["sha256"] == real_sha
        assert payload["datasetFileSha256"] == real_sha
        assert payload["manifest"]["signedBy"] == "daniel@emanate.ai"

        mock_run_id.assert_called_once()
        assert reports["body"]["status"] == "dispatched"
        assert reports["body"]["trigger_dispatch"]["ok"] is True
        assert reports["body"]["trigger_dispatch"]["trigger_run_id"] == "run_abc123"

    def test_falls_back_to_env_ceiling_when_launch_report_unreadable(self, tmp_path, monkeypatch):
        manifest_path, real_sha = _write_signed_manifest(tmp_path)
        run_path = tmp_path / SLUG
        run_path.mkdir()
        # No launch report written -> falls back to FW_COST_CEILING.
        monkeypatch.setenv("FW_COST_CEILING", "42")
        reports = {}

        def fake_write_report(slug, stage, body, cfg=None, sync_url=None):
            reports["body"] = body
            return run_path / "report.json"

        with patch("trigger_dev_rest.trigger_task", return_value="run_xyz") as mock_trigger, \
             patch("factory_sync.get_or_create_run_id", return_value="run-id-1"), \
             patch.object(factory, "run_dir", _patch_run_dir(run_path)), \
             patch.object(factory, "write_report", fake_write_report), \
             patch.object(factory, "_dispatch_stage_nodes", lambda *a, **k: [{"ok": True}]):
            factory.stage_train(_cfg(), SLUG, _args(dry_run=False, manifest=str(manifest_path)))

        _task_id, payload = mock_trigger.call_args.args[:2]
        assert payload["costCeilingUsd"] == 42.0

    def test_trigger_dev_transport_failure_is_reported_not_raised(self, tmp_path):
        """Mirrors `_dispatch_stage_nodes`'s resilience contract: a Trigger.dev
        outage must degrade the S7 report, never crash the CLI."""
        manifest_path, _real_sha = _write_signed_manifest(tmp_path)
        run_path = tmp_path / SLUG
        run_path.mkdir()
        _write_launch_report(run_path)
        reports = {}

        def fake_write_report(slug, stage, body, cfg=None, sync_url=None):
            reports["body"] = body
            return run_path / "report.json"

        import trigger_dev_rest

        with patch(
            "trigger_dev_rest.trigger_task",
            side_effect=trigger_dev_rest.TriggerDevRestError("boom"),
        ), patch("factory_sync.get_or_create_run_id", return_value="run-id-2"), \
             patch.object(factory, "run_dir", _patch_run_dir(run_path)), \
             patch.object(factory, "write_report", fake_write_report), \
             patch.object(factory, "_dispatch_stage_nodes", lambda *a, **k: [{"ok": True}]):
            factory.stage_train(_cfg(), SLUG, _args(dry_run=False, manifest=str(manifest_path)))

        assert reports["body"]["status"] == "blocked"
        assert "trigger.dev dispatch failed" in reports["body"]["trigger_dispatch"]["error"]


class TestCheckRunIdFlag:
    """--check-run-id: read-only poll of a previously dispatched Trigger.dev
    run, wired straight to trigger_dev_rest.get_run — no spend, and it must
    short-circuit main() before the org-slug/config-load path (§9.2,
    PRs/model-factory-cloud-environment-v1/09-s7-launch-automation-investigation.md)."""

    def test_prints_get_run_result_as_json_and_returns_before_loading_a_config(self, capsys):
        with patch("trigger_dev_rest.get_run", return_value={"id": "run_abc", "status": "COMPLETED"}) as mock_get_run, \
             patch.object(factory, "load_config", side_effect=AssertionError("must not load a config")), \
             patch.object(sys, "argv", ["factory.py", "any-org", "--check-run-id", "run_abc"]):
            factory.main()

        mock_get_run.assert_called_once_with("run_abc")
        assert json.loads(capsys.readouterr().out) == {"id": "run_abc", "status": "COMPLETED"}
