"""C3 (factory-cursor-bridge-v1) — factory_sync's cursor extensions. Release-blocking.

Three properties, each with the failure it prevents:

1. **The three cursor keys are absent, not null, on a tier2 row** (PR-A's
   established convention). A `cursor_agent_id: null` key on every tier2 row
   would be harmless in Postgres but poisonous in review: the next reader
   cannot tell "this executor never has one" from "the write path lost it".
2. **A stopped dispatch is `cancelled`, never `ended`, and never "FAILED".**
   The RPC (Half A) set the row `cancelled` when the operator clicked Stop; a
   final sync that flips it back to `ended` erases the operator's action, and a
   timeline headline reading FAILED counts a deliberate Stop against the spec's
   reliability — the exact misread D7 exists to prevent. `resumable` must not be
   written at all: the RPC owns it (pause vs stop), and a PATCH that includes it
   would overwrite that verdict.
3. **The mid-run `active` row (push_node_agent_started) is minimal, correct, and
   fail-open.** It is what the Stop button acts on; a raise here would take down
   a paid dispatch from inside its own telemetry.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory_sync as fs  # noqa: E402
from supabase_rest import SupabaseRestError  # noqa: E402

RUN_ID = "dddddddd-0000-0000-0000-000000000001"

SPEC = {
    "slug": "qa-cursor-synth",
    "role_family": "reader",
    "mission": "synthetic sync test node",
}


def _tier2_result(**extra):
    return {
        "node_id": "S4:qa-cursor-synth",
        "spec": "qa-cursor-synth",
        "role_family": "reader",
        "runtime": {"model": "haiku", "max_turns": 15, "budget_usd": 0.5},
        "dispatched": True,
        "ok": True,
        "exit_code": 0,
        "verdict": None,
        "final_response": "done",
        **extra,
    }


def _cursor_result(**extra):
    return _tier2_result(
        cursor_agent_id="agent-abc",
        cursor_run_id="run-xyz",
        cursor_runtime="cloud",
        **extra,
    )


# --------------------------------------------------------------------------
# the three fields: present when carried, absent (not null) when not
# --------------------------------------------------------------------------


def test_a_tier2_result_produces_a_row_with_no_cursor_keys_at_all():
    row, _event = fs.project_node_agent(_tier2_result(), RUN_ID, "S4", SPEC)
    for key in ("cursor_agent_id", "cursor_run_id", "cursor_runtime"):
        assert key not in row  # absent, not None-valued — PR-A's convention


def test_a_cursor_result_round_trips_all_three_fields_exactly():
    row, _event = fs.project_node_agent(_cursor_result(), RUN_ID, "S4", SPEC)
    assert row["cursor_agent_id"] == "agent-abc"
    assert row["cursor_run_id"] == "run-xyz"
    assert row["cursor_runtime"] == "cloud"
    assert row["status"] == "ended"


# --------------------------------------------------------------------------
# the stopped outcome (D7)
# --------------------------------------------------------------------------


def test_a_stopped_dispatch_lands_cancelled_and_never_writes_resumable():
    row, _event = fs.project_node_agent(
        _cursor_result(ok=False, stopped=True, exit_code=None), RUN_ID, "S4", SPEC
    )
    assert row["status"] == "cancelled"  # matches what the control RPC already set
    assert "resumable" not in row  # the RPC owns pause-vs-stop; a PATCH here would erase it


def test_a_stopped_dispatch_headline_says_stopped_not_failed():
    _row, event = fs.project_node_agent(
        _cursor_result(ok=False, stopped=True), RUN_ID, "S4", SPEC
    )
    assert "stopped by operator" in event["headline"]
    assert "FAILED" not in event["headline"]


def test_a_genuine_failure_still_reads_failed():
    """Regression guard on the other side of D7's line: softening real failures
    into something neutral would hide broken specs, the opposite mistake."""
    _row, event = fs.project_node_agent(
        _cursor_result(ok=False, exit_code=1), RUN_ID, "S4", SPEC
    )
    assert "FAILED" in event["headline"]


# --------------------------------------------------------------------------
# the mid-run active row
# --------------------------------------------------------------------------


def test_started_push_sends_a_minimal_active_row_with_the_control_keys():
    pushed = []
    with patch.object(fs, "_upsert_agent_row", side_effect=lambda row, **k: pushed.append(row)), \
         patch.object(fs, "insert") as mock_insert:
        ok = fs.push_node_agent_started(
            _cursor_result(prompt_file="factory-automation/factory/runs/x/prompts/S4.md"),
            RUN_ID, "S4", SPEC, started_at="2026-07-29T12:00:00-07:00",
        )
    assert ok is True
    (row,) = pushed
    assert row["status"] == "active"
    assert row["cursor_agent_id"] == "agent-abc"
    assert row["cursor_run_id"] == "run-xyz"
    assert row["run_id"] == RUN_ID and row["stage"] == "S4"
    assert row["started_at"] == "2026-07-29T12:00:00-07:00"
    # No event row: the timeline's agent_dispatched event belongs to the
    # outcome, which does not exist yet.
    mock_insert.assert_not_called()


def test_started_push_shares_the_final_pushs_upsert_identity():
    """(run_id, title) is the identity `_upsert_agent_row` matches on when there
    is no ledger_session_id — both pushes must agree or the run detail shows the
    same dispatch twice, once active forever and once ended."""
    rows = []
    with patch.object(fs, "_upsert_agent_row", side_effect=lambda row, **k: rows.append(row)), \
         patch.object(fs, "insert"):
        fs.push_node_agent_started(_cursor_result(), RUN_ID, "S4", SPEC)
        fs.push_node_agent(_cursor_result(), RUN_ID, "S4", SPEC)
    started, final = rows
    assert started["title"] == final["title"]
    assert started["run_id"] == final["run_id"]
    assert not started.get("ledger_session_id") and not final.get("ledger_session_id")


def test_started_push_is_fail_open():
    with patch.object(fs, "_upsert_agent_row", side_effect=SupabaseRestError("supabase down")):
        ok = fs.push_node_agent_started(_cursor_result(), RUN_ID, "S4", SPEC)
    assert ok is False  # logged and swallowed — never raises past a paid dispatch


def test_started_push_skips_without_a_run_id():
    with patch.object(fs, "_upsert_agent_row") as mock_upsert:
        ok = fs.push_node_agent_started(_cursor_result(), None, "S4", SPEC)
    assert ok is False
    mock_upsert.assert_not_called()
