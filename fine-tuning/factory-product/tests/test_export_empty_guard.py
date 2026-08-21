"""S3 export empty-guard — a zero-dossier export must never record a green stage.

Found 2026-07-31 on the ptc-steel v6 run: platform-alpha/.env.local pointed at the
empty LOCAL Supabase, `export-account-dossiers.ts` printed `"exported": 0` and
exited 0, and stage_export recorded status ok for a refresh that refreshed
nothing. The exporter's own JSON summary is the real signal, not the exit code.
"""

import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory  # noqa: E402

SLUG = "fixture-org"

EXPORTER_SUMMARY_EMPTY = (
    '[export-dossiers] exporting dossiers for 1 org(s)\n'
    '  org=abc: 0 candidates\n\n[export-dossiers] done\n'
    '{\n  "generated_at": "2026-07-31T16:34:21.449Z",\n  "exported": 0,\n'
    '  "failed": 0,\n  "token_p50": null\n}\n'
)
EXPORTER_SUMMARY_REAL = EXPORTER_SUMMARY_EMPTY.replace('"exported": 0', '"exported": 1031')


def test_exported_count_parses_the_summary():
    assert factory._exported_count_from_stdout(EXPORTER_SUMMARY_EMPTY) == 0
    assert factory._exported_count_from_stdout(EXPORTER_SUMMARY_REAL) == 1031


def test_no_summary_returns_none_and_does_not_trip_the_guard():
    # Older exporter output with no JSON block: unknown, not zero.
    assert factory._exported_count_from_stdout("done, no json here") is None
    assert factory._exported_count_from_stdout("") is None
    assert factory._exported_count_from_stdout(None) is None


def _cfg(tmp_path):
    platform = tmp_path / "platform"
    platform.mkdir()
    return {
        "org": {"id": "abc", "name": "Fixture Org"},
        "book": {"accounts": 1031},
        "paths": {
            "platform_repo": str(platform.relative_to(factory.REPO)) if str(platform).startswith(str(factory.REPO)) else str(platform),
            "export_script": "scripts/export-account-dossiers.ts",
            "dossiers_out": "finetune-out/dossiers-pilot",
        },
    }


def _run_export(tmp_path, stdout, returncode=0):
    cfg = {
        "org": {"id": "abc", "name": "Fixture Org"},
        "book": {"accounts": 1031},
        "paths": {"platform_repo": ".", "export_script": "x.ts", "dossiers_out": "out"},
    }
    args = types.SimpleNamespace(dry_run=False, live=True, approved=False, sync_url=None)
    reports = {}

    def fake_write_report(slug, stage, payload, cfg_=None, sync_url=None):
        reports["body"] = payload
        return tmp_path / "report.json"

    fake_result = types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")
    with patch.object(factory, "write_report", fake_write_report), \
         patch.object(factory.subprocess, "run", lambda *a, **k: fake_result):
        factory.stage_export(cfg, SLUG, args)
    return reports["body"]


def test_zero_export_is_refused_not_ok(tmp_path):
    body = _run_export(tmp_path, EXPORTER_SUMMARY_EMPTY)
    assert body["status"] == "empty"
    assert body["exported"] == 0
    assert "wrong" in body["error"] and "database" in body["error"]


def test_real_export_stays_ok(tmp_path):
    body = _run_export(tmp_path, EXPORTER_SUMMARY_REAL)
    assert body["status"] == "ok"
    assert body["exported"] == 1031
    assert "error" not in body


def test_nonzero_exit_stays_error_regardless_of_summary(tmp_path):
    body = _run_export(tmp_path, EXPORTER_SUMMARY_REAL, returncode=3)
    assert body["status"] == "error"
