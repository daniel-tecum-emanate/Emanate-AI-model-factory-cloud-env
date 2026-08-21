"""T11 — factory_sync.push_stage: payload-shape (schema-drift) + fail-open tests.

PRTests.md's own naming for this file: "pushed factory_runs/factory_run_stages
payload shape matches the migration's column list exactly (a schema-drift test —
fails if T1's migration and T11's payload ever disagree)" + "a forced HTTP failure
does not raise out of the caller (fail-open proven)."
"""

import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
REPO = FACTORY_DIR.parent.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory_sync  # noqa: E402
from supabase_rest import SupabaseRestError  # noqa: E402

from _migration import columns_for as _columns_for  # noqa: E402
from _migration import read_migration  # noqa: E402


@pytest.fixture(scope="module")
def migration_text():
    return read_migration()


@pytest.fixture
def cfg():
    return {
        "org": {"id": "org-uuid-123", "name": "Synthetic Test Org"},
        "champion": {"model_id": None},
    }


CENSUS_REPORT = {
    "org": "synthetic-test-org",
    "stage": "census",
    "stage_index": 1,
    "generated_at": "2026-07-23T00:00:00+00:00",
    "status": "ok",
    "dry_run": True,
    "findings": {"tier_computed": "B", "tier_match": True},
    "recommendation": "FULL",
    "next": "governance",
}

PROJECT_REPORT = {
    "org": "synthetic-test-org",
    "stage": "project",
    "stage_index": 6,
    "generated_at": "2026-07-23T00:00:00+00:00",
    "status": "ok",
    "dry_run": True,
    "decision_request": {
        "decision": "approve training spend",
        "recommendation": "approve",
        "cost": {"total_projection_usd": 14.2},
    },
    "next": "launch",
}


def test_run_row_payload_keys_are_all_real_factory_runs_columns(cfg, migration_text):
    columns = _columns_for("factory_runs", migration_text)
    run_id = "11111111-1111-1111-1111-111111111111"
    row = factory_sync._run_row(cfg, "synthetic-test-org", "census", CENSUS_REPORT, run_id)
    assert set(row.keys()) <= columns, f"unknown factory_runs column(s): {set(row.keys()) - columns}"
    # And the required NOT NULL columns (no DB default) are always present.
    for required in ("org_slug", "org_name", "run_kind", "model_stream", "status", "current_stage"):
        assert required in row


def test_stage_row_payload_keys_are_all_real_factory_run_stages_columns(migration_text):
    columns = _columns_for("factory_run_stages", migration_text)
    run_id = "11111111-1111-1111-1111-111111111111"
    row = factory_sync._stage_row(run_id, "project", PROJECT_REPORT)
    assert set(row.keys()) <= columns, f"unknown factory_run_stages column(s): {set(row.keys()) - columns}"
    for required in ("run_id", "stage", "attempt", "status", "report"):
        assert required in row


def test_run_row_stage_code_uses_s_number_convention_not_factory_pys_lowercase_name(cfg):
    run_id = "11111111-1111-1111-1111-111111111111"
    row = factory_sync._run_row(cfg, "synthetic-test-org", "launch", {"status": "approved"}, run_id)
    assert row["current_stage"] == "S6g"


def test_push_stage_forced_http_failure_does_not_raise(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    with patch.object(factory_sync, "upsert", side_effect=SupabaseRestError("boom: connection refused")):
        result = factory_sync.push_stage(cfg, "synthetic-test-org", "census", CENSUS_REPORT)
    assert result is False  # informational only — no exception escaped


def test_push_stage_success_calls_upsert_for_all_three_tables(cfg, tmp_path, monkeypatch):
    """`factory_run_events` joined the push on 2026-07-31 (stress-test
    EVENT-COVERAGE): a stage transition now also asserts its timeline rows,
    AFTER `factory_runs` (the events FK needs the run row to exist first).
    """
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    calls = []
    with patch.object(factory_sync, "upsert", side_effect=lambda table, rows, **kw: calls.append(table)):
        result = factory_sync.push_stage(cfg, "synthetic-test-org", "census", CENSUS_REPORT)
    assert result is True
    assert calls == ["factory_runs", "factory_run_stages", "factory_run_events"]


def test_get_or_create_run_id_is_stable_across_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    first = factory_sync.get_or_create_run_id("synthetic-test-org")
    second = factory_sync.get_or_create_run_id("synthetic-test-org")
    assert first == second
