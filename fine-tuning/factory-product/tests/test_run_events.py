"""Stage/gate/cost timeline emitters (stress-test 2026-07-31, EVENT-COVERAGE).

Before this work 8 of the 12 `factory_run_events.kind` values had no live
emitter (data-model/factory_run_events.md "Known gaps"): the Timeline showed
agent moments but never a stage transition, gate request, gate decision, or
cost. This file covers the new emitters:

- `push_stage` -> `stage_started` / `stage_passed` / `stage_failed` /
  `cost_recorded`, uuid5-idempotent, `ts` = true event time.
- `push_gate_request` -> `gate_requested`, only on the fresh insert.
- `push_gate_decided` -> `gate_decided`, only for a RESOLVED gate.
- factory.py's `_record_gate_decision` wiring for the above.

Idempotency is proven by planted duplication throughout: fire the same code
path twice, assert the second pass produces byte-identical deterministic ids
(the `on_conflict="id"` upsert then makes the DB effect a re-assert, which
`test_qa_adversarial`-style live checks and the one allowed --live smoke
verify against a real endpoint).
"""

import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory_sync  # noqa: E402
from supabase_rest import SupabaseRestError  # noqa: E402

from _migration import columns_for as _columns_for  # noqa: E402
from _migration import read_migration  # noqa: E402

RUN_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def migration_text():
    return read_migration()


def _check_kinds(migration_text):
    """The CHECK constraint's closed `kind` vocabulary, parsed from the REAL
    migrations. The constraint has been widened twice (drop + re-add), so the
    LAST occurrence in timestamp-ordered migration text is the live list.
    """
    blocks = re.findall(r"kind IN \((.*?)\)\)?;", migration_text, re.DOTALL)
    assert blocks, "no factory_run_events kind CHECK found in the migrations"
    # Filter to the factory_run_events lists (they all contain stage_started;
    # factory_lessons' kind CHECK does not).
    event_blocks = [b for b in blocks if "stage_started" in b]
    assert event_blocks
    return set(re.findall(r"'([a-z_]+)'", event_blocks[-1]))


REPORT_BASE = {
    "org": "synthetic-test-org",
    "generated_at": "2026-07-31T10:00:05+00:00",
    "dry_run": False,
}

CENSUS_OK = {**REPORT_BASE, "stage": "census", "stage_index": 1, "status": "ok"}
VERIFY_ERROR = {**REPORT_BASE, "stage": "verify", "stage_index": 5, "status": "error",
                "nodes": [{"ok": True}, {"ok": False}],
                "findings": {"verifier_verdict": "fail"}}
EXPORT_EMPTY = {**REPORT_BASE, "stage": "export", "stage_index": 3, "status": "empty",
                "exported": 0, "exit_code": 0}
EXPORT_LIVE_OK = {**REPORT_BASE, "stage": "export", "stage_index": 3, "status": "ok",
                  "exported": 42, "exit_code": 0}
TRAIN_DISPATCHED = {**REPORT_BASE, "stage": "train", "stage_index": 7, "status": "dispatched",
                     "trigger_dispatch": {"ok": True, "trigger_run_id": "run_abc123"}}
GOVERNANCE_PENDING = {**REPORT_BASE, "stage": "governance", "stage_index": 2, "status": "gate_pending"}
BLOCKED = {**REPORT_BASE, "stage": "build", "stage_index": 4, "status": "BLOCKED_ON_GATE",
           "blocked_on": "governance"}
PROJECT_OK = {**REPORT_BASE, "stage": "project", "stage_index": 6, "status": "ok",
              "decision_request": {"decision": "approve training spend", "recommendation": "approve",
                                   "cost": {"sft_estimate_usd": 9.1, "total_projection_usd": 14.2,
                                            "eval_window_cap_usd": 5}}}


# --------------------------------------------------------------------------
# stage events — kinds, ts, detail
# --------------------------------------------------------------------------


def test_ok_stage_emits_started_and_passed():
    rows = factory_sync._stage_event_rows(RUN_ID, "census", CENSUS_OK)
    assert [r["kind"] for r in rows] == ["stage_started", "stage_passed"]
    assert rows[0]["headline"] == "S1 census started"
    assert rows[1]["headline"] == "S1 census passed"
    assert all(r["run_id"] == RUN_ID for r in rows)
    assert all(r["ref"]["stage"] == "S1" for r in rows)


def test_failed_stage_emits_stage_failed_with_counts_detail():
    rows = factory_sync._stage_event_rows(RUN_ID, "verify", VERIFY_ERROR)
    assert [r["kind"] for r in rows] == ["stage_started", "stage_failed"]
    failed = rows[1]
    assert "FAILED" in failed["headline"]
    assert failed["detail"] == {"nodes_total": 2, "nodes_ok": 1, "verifier_verdict": "fail"}


def test_empty_export_is_a_failed_stage_event_and_a_failed_stage_row():
    """F-03-2: `status: "empty"` is S3's own zero-dossier REFUSAL, but
    `_stage_row` mapped it to `passed` — a green stage in the tab for the one
    outcome the stage invented a status to flag. Both projections now agree
    it failed."""
    rows = factory_sync._stage_event_rows(RUN_ID, "export", EXPORT_EMPTY)
    assert rows[1]["kind"] == "stage_failed"
    assert rows[1]["detail"]["exported"] == 0
    assert factory_sync._stage_row(RUN_ID, "export", EXPORT_EMPTY)["status"] == "failed"


def test_export_count_reaches_the_passed_events_detail():
    rows = factory_sync._stage_event_rows(RUN_ID, "export", EXPORT_LIVE_OK)
    assert rows[1]["kind"] == "stage_passed"
    assert rows[1]["detail"]["exported"] == 42


def test_dispatched_train_stage_is_running_not_passed():
    """S7 (train) firing a REAL Fireworks launch via `_dispatch_launch_task` is
    reported the instant the dispatch call succeeds — training itself runs for
    HOURS, entirely outside this process. Before this arm, `_stage_status_for`
    had no case for `dispatched` and fell through to `passed`, so the tab
    showed a completed, green S7 the moment the launch was fired."""
    row = factory_sync._stage_row(RUN_ID, "train", TRAIN_DISPATCHED)
    assert row["status"] == "running"
    # No terminal event either: the stage hasn't finished, so neither
    # `stage_passed` nor `stage_failed` would be honest — only `stage_started`.
    rows = factory_sync._stage_event_rows(RUN_ID, "train", TRAIN_DISPATCHED)
    assert [r["kind"] for r in rows] == ["stage_started"]


def test_dispatched_status_is_scoped_to_the_train_stage_only():
    """The fix must not leak beyond S7: `dispatched` on any other stage is not
    a status a real handler emits, but `_stage_status_for` must still fall
    through to the bare `passed` default for it."""
    for stage_name in factory_sync.STAGE_CODES:
        if stage_name == "train":
            continue
        report = {**REPORT_BASE, "stage": stage_name, "status": "dispatched"}
        assert factory_sync._stage_row(RUN_ID, stage_name, report)["status"] == "passed"


def test_every_other_train_status_still_defaults_to_passed():
    """The fix must be scoped to exactly ("train", "dispatched") — every other
    status the train stage can report keeps the bare default unchanged."""
    for status in ("ok", "blocked", "todo", "needs_self_dispatch"):
        report = {**REPORT_BASE, "stage": "train", "status": status}
        assert factory_sync._stage_row(RUN_ID, "train", report)["status"] == "passed"


def test_blocked_on_gate_report_emits_no_events_at_all():
    """The stage never ran — main() refused it before the handler — so a
    stage_started row would be a lie, and the gate's own gate_requested event
    already covers the wait."""
    assert factory_sync._stage_event_rows(RUN_ID, "build", BLOCKED) == []


def test_pending_gate_stage_emits_started_only():
    rows = factory_sync._stage_event_rows(RUN_ID, "governance", GOVERNANCE_PENDING)
    assert [r["kind"] for r in rows] == ["stage_started"]


def test_started_ts_uses_true_start_time_and_finished_ts_uses_report_write_time():
    """Research/04 flagged `ts` = sync clock as the v2 scrubber blocker: events
    must carry event time. The start time is stamped by factory.py before the
    handler runs; the finish time IS the report's generated_at."""
    rows = factory_sync._stage_event_rows(
        RUN_ID, "census", CENSUS_OK, started_at="2026-07-31T10:00:00+00:00"
    )
    assert rows[0]["ts"] == "2026-07-31T10:00:00+00:00"
    assert rows[1]["ts"] == CENSUS_OK["generated_at"]


def test_started_ts_falls_back_to_generated_at_when_no_start_was_recorded():
    rows = factory_sync._stage_event_rows(RUN_ID, "census", CENSUS_OK)
    assert rows[0]["ts"] == CENSUS_OK["generated_at"]


def test_cost_recorded_emitted_only_when_the_report_carries_a_real_projection():
    project_rows = factory_sync._stage_event_rows(RUN_ID, "project", PROJECT_OK)
    assert [r["kind"] for r in project_rows] == ["stage_started", "stage_passed", "cost_recorded"]
    cost = project_rows[-1]
    assert cost["detail"] == {"sft_estimate_usd": 9.1, "total_projection_usd": 14.2,
                              "eval_window_cap_usd": 5}
    assert "14.2" in cost["headline"]
    census_kinds = [r["kind"] for r in factory_sync._stage_event_rows(RUN_ID, "census", CENSUS_OK)]
    assert "cost_recorded" not in census_kinds


# --------------------------------------------------------------------------
# idempotency — planted duplication
# --------------------------------------------------------------------------


def test_rerunning_a_stage_produces_byte_identical_event_ids():
    first = factory_sync._stage_event_rows(RUN_ID, "project", PROJECT_OK)
    second = factory_sync._stage_event_rows(RUN_ID, "project", PROJECT_OK)
    assert [r["id"] for r in first] == [r["id"] for r in second]
    assert len({r["id"] for r in first}) == len(first)


def test_a_rerun_that_flips_failed_to_passed_replaces_the_terminal_event_not_adds():
    """One id for the terminal event regardless of outcome: a stage cannot have
    both passed and failed at the same attempt, and two rows would say it did."""
    failed = factory_sync._stage_event_rows(RUN_ID, "verify", VERIFY_ERROR)[1]
    passed = factory_sync._stage_event_rows(RUN_ID, "verify", {**VERIFY_ERROR, "status": "ok"})[1]
    assert failed["id"] == passed["id"]
    assert (failed["kind"], passed["kind"]) == ("stage_failed", "stage_passed")


def test_push_stage_upserts_events_on_id_after_the_run_row_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    cfg = {"org": {"id": "org-uuid-123", "name": "Synthetic Test Org"}, "champion": {"model_id": None}}
    calls = []
    with patch.object(factory_sync, "upsert",
                      side_effect=lambda table, rows, **kw: calls.append((table, kw.get("on_conflict"), rows))):
        assert factory_sync.push_stage(cfg, "synthetic-test-org", "project", PROJECT_OK) is True
        assert factory_sync.push_stage(cfg, "synthetic-test-org", "project", PROJECT_OK) is True
    event_calls = [c for c in calls if c[0] == "factory_run_events"]
    assert len(event_calls) == 2
    assert all(on_conflict == "id" for _t, on_conflict, _r in event_calls)
    # planted duplication: the second push re-asserts the exact same ids
    assert [r["id"] for r in event_calls[0][2]] == [r["id"] for r in event_calls[1][2]]
    # FK ordering: the run row lands before its events, both times
    tables = [t for t, _oc, _r in calls]
    assert tables.index("factory_runs") < tables.index("factory_run_events")


def test_event_namespace_matches_the_backfill_namespace_without_importing_it_at_runtime():
    """factory_sync deliberately restates the uuid5 namespace rather than
    importing the one-time backfill script; this is the drift guard."""
    import backfill_ptc_history
    assert factory_sync.EVENT_NAMESPACE == backfill_ptc_history.NAMESPACE


# --------------------------------------------------------------------------
# schema legality — columns and the CHECK vocabulary, against the REAL migration
# --------------------------------------------------------------------------


def _all_new_event_rows():
    rows = []
    for stage_name, report in (("census", CENSUS_OK), ("verify", VERIFY_ERROR),
                               ("export", EXPORT_EMPTY), ("project", PROJECT_OK),
                               ("governance", GOVERNANCE_PENDING)):
        rows.extend(factory_sync._stage_event_rows(RUN_ID, stage_name, report))
    return rows


def test_stage_event_rows_use_only_real_factory_run_events_columns(migration_text):
    columns = _columns_for("factory_run_events", migration_text)
    for row in _all_new_event_rows():
        assert set(row) <= columns, f"unknown factory_run_events column(s): {set(row) - columns}"


def test_every_emitted_kind_is_in_the_check_constraints_closed_vocabulary(migration_text):
    legal = _check_kinds(migration_text)
    emitted = {r["kind"] for r in _all_new_event_rows()} | {"gate_requested", "gate_decided"}
    assert emitted <= legal, f"kind(s) outside the CHECK vocabulary: {emitted - legal}"


def test_pii_in_a_recommendation_is_scrubbed_but_identifier_columns_are_untouched():
    report = {**PROJECT_OK,
              "decision_request": {**PROJECT_OK["decision_request"],
                                   "recommendation": "ping daniel@emanate.ai before approving"}}
    rows = factory_sync._stage_event_rows(RUN_ID, "project", report)
    import json
    blob = json.dumps(rows)
    assert "daniel@emanate.ai" not in blob
    # and the scrub never rewrites the uuid columns (the _digest_row lesson)
    assert all(row["run_id"] == RUN_ID for row in rows)


# --------------------------------------------------------------------------
# gate_requested — fresh insert only, and telemetry failure never breaks the gate
# --------------------------------------------------------------------------

DECISION_REQUEST = {"decision": "clear Synthetic Test Org for SFT", "recommendation": "approve"}


def test_gate_requested_event_emitted_on_the_fresh_insert(tmp_path, monkeypatch):
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    events = []
    with patch("factory_sync.select", return_value=[]), \
            patch("factory_sync.insert", return_value=[{"id": "new-id", "status": "pending"}]), \
            patch.object(factory_sync, "upsert",
                         side_effect=lambda table, rows, **kw: events.extend(rows)):
        factory_sync.push_gate_request({"org": {"name": "X"}}, "synthetic-test-org", "governance", DECISION_REQUEST)
    assert len(events) == 1
    assert events[0]["kind"] == "gate_requested"
    assert events[0]["ref"]["gate"] == "S2"
    assert "recommendation: approve" in events[0]["headline"]


def test_no_gate_requested_event_when_the_request_already_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    with patch("factory_sync.select", return_value=[{"id": "existing", "status": "pending"}]), \
            patch.object(factory_sync, "upsert") as mock_upsert, \
            patch("factory_sync.insert") as mock_insert:
        factory_sync.push_gate_request({"org": {"name": "X"}}, "synthetic-test-org", "governance", DECISION_REQUEST)
    mock_insert.assert_not_called()
    mock_upsert.assert_not_called()


def test_gate_requested_event_failure_does_not_fail_the_gate_push(tmp_path, monkeypatch):
    """The gate push is fail-CLOSED; the event is telemetry and fail-OPEN. A
    timeline hiccup must never read back as 'gate push failed' -> 'not
    approved' when the request row itself landed."""
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    with patch("factory_sync.select", return_value=[]), \
            patch("factory_sync.insert", return_value=[{"id": "new-id", "status": "pending"}]), \
            patch.object(factory_sync, "upsert", side_effect=SupabaseRestError("events table down")):
        result = factory_sync.push_gate_request(
            {"org": {"name": "X"}}, "synthetic-test-org", "governance", DECISION_REQUEST
        )
    assert result == {"id": "new-id", "status": "pending"}


def test_gate_requested_id_is_deterministic_per_run_and_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    captured = []
    with patch("factory_sync.select", return_value=[]), \
            patch("factory_sync.insert", return_value=[{"id": "n", "status": "pending"}]), \
            patch.object(factory_sync, "upsert", side_effect=lambda table, rows, **kw: captured.append(rows[0]["id"])):
        factory_sync.push_gate_request({"org": {"name": "X"}}, "synthetic-test-org", "governance", DECISION_REQUEST)
        # simulate the row having been deleted server-side; a re-request must mint the SAME event id
        factory_sync.push_gate_request({"org": {"name": "X"}}, "synthetic-test-org", "governance", DECISION_REQUEST)
    assert captured[0] == captured[1]


# --------------------------------------------------------------------------
# gate_decided
# --------------------------------------------------------------------------


@pytest.fixture
def cached_run_state(tmp_path, monkeypatch):
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    factory_sync._save_run_state("synthetic-test-org", {"run_id": RUN_ID, "iteration": 1})


@pytest.mark.parametrize("status,decision", [("approved", "approved"), ("no_go", "rejected"),
                                             ("REFUSED", "refused")])
def test_gate_decided_emitted_for_each_resolved_status(cached_run_state, status, decision):
    captured = []
    with patch.object(factory_sync, "upsert", side_effect=lambda table, rows, **kw: captured.extend(rows)):
        assert factory_sync.push_gate_decided("synthetic-test-org", "ship", status, actor="human") is True
    assert captured[0]["kind"] == "gate_decided"
    assert captured[0]["ref"] == {"gate": "S9", "decision": decision, "actor": "human"}
    assert decision in captured[0]["headline"]


def test_gate_decided_skips_undecided_statuses_without_touching_the_network(cached_run_state):
    with patch.object(factory_sync, "upsert") as mock_upsert:
        assert factory_sync.push_gate_decided("synthetic-test-org", "ship", "gate_pending") is False
    mock_upsert.assert_not_called()


def test_gate_decided_never_mints_a_run_id(tmp_path, monkeypatch):
    """No cached sync state -> no event. A logging call must never trigger
    `get_or_create_run_id`'s lineage semantics (same rule as
    `_run_id_for_decisions`)."""
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    with patch.object(factory_sync, "upsert") as mock_upsert:
        assert factory_sync.push_gate_decided("synthetic-test-org", "ship", "approved") is False
    mock_upsert.assert_not_called()
    assert factory_sync._load_run_state("synthetic-test-org") == {}


def test_gate_decided_is_fail_open_and_idempotent(cached_run_state):
    with patch.object(factory_sync, "upsert", side_effect=SupabaseRestError("down")):
        assert factory_sync.push_gate_decided("synthetic-test-org", "launch", "approved") is False  # no raise

    captured = []
    with patch.object(factory_sync, "upsert", side_effect=lambda table, rows, **kw: captured.append(rows[0]["id"])):
        factory_sync.push_gate_decided("synthetic-test-org", "launch", "approved")
        factory_sync.push_gate_decided("synthetic-test-org", "launch", "approved")
    assert captured[0] == captured[1]


def test_gate_decided_ts_uses_the_true_decision_time_when_known(cached_run_state):
    captured = []
    with patch.object(factory_sync, "upsert", side_effect=lambda table, rows, **kw: captured.extend(rows)):
        factory_sync.push_gate_decided("synthetic-test-org", "ship", "no_go",
                                       decided_at="2026-07-30T18:30:00+00:00")
    assert captured[0]["ts"] == "2026-07-30T18:30:00+00:00"


# --------------------------------------------------------------------------
# factory.py wiring — _record_gate_decision routes resolved gates to the event
# --------------------------------------------------------------------------


def _gate_args(sync_url="http://localhost:1/sync"):
    class Args:
        approved = False
        dry_run = True
        live = False
        lesson = None
    Args.sync_url = sync_url
    return Args()


def test_record_gate_decision_emits_gate_decided_for_resolved_statuses():
    import factory as factory_module
    with patch("factory_sync.push_gate_decided") as mock_push, \
            patch("factory_decisions.record"):
        factory_module._record_gate_decision(
            {}, "synthetic-test-org", "ship", _gate_args(), "no_go",
            {"recommendation": "reject"}, decided_at="2026-07-30T18:30:00+00:00",
        )
    mock_push.assert_called_once()
    kwargs = mock_push.call_args.kwargs
    assert mock_push.call_args.args == ("synthetic-test-org", "ship", "no_go")
    assert kwargs["actor"] == "human"
    assert kwargs["decided_at"] == "2026-07-30T18:30:00+00:00"


def test_record_gate_decision_does_not_emit_for_a_pending_gate():
    import factory as factory_module
    with patch("factory_sync.push_gate_decided") as mock_push, \
            patch("factory_decisions.record"):
        factory_module._record_gate_decision(
            {}, "synthetic-test-org", "governance", _gate_args(), "gate_pending", {}
        )
    mock_push.assert_not_called()


def test_record_gate_decision_does_not_emit_without_a_sync_url(monkeypatch):
    import factory as factory_module
    monkeypatch.delenv("FACTORY_SUPABASE_URL", raising=False)
    with patch("factory_sync.push_gate_decided") as mock_push, \
            patch("factory_decisions.record"):
        factory_module._record_gate_decision(
            {}, "synthetic-test-org", "launch", _gate_args(sync_url=None), "approved", {}
        )
    mock_push.assert_not_called()


def test_record_gate_decision_event_failure_never_breaks_the_gate():
    import factory as factory_module
    with patch("factory_sync.push_gate_decided", side_effect=RuntimeError("sidecar exploded")), \
            patch("factory_decisions.record") as mock_record:
        factory_module._record_gate_decision(
            {}, "synthetic-test-org", "ship", _gate_args(), "approved", {"recommendation": "approve"}
        )
    mock_record.assert_called_once()  # the decision ledger still ran
