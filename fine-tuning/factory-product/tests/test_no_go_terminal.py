"""B1 (factory-graph-pr1, 2026-07-27): a NO-GO'd ship gate is a TERMINAL run.

The bug this closes was confirmed live, not theorized. `TERMINAL_RUN_STATUSES`
has contained `"no_go"` since D22, and `test_sync_run_reuse.py` already proves
that a `no_go` run mints fresh retrain lineage — but **nothing in the codebase
could ever produce the string.** A rejected ship gate made `stage_ship` write
`status: "gate_pending"`, which `_run_status_for` mapped to `blocked_on_gate`:
non-terminal. So `get_or_create_run_id` reused the dead run's id forever and the
`Lesson_RetrainRunIdReuse` fix silently never fired on the NO-GO path — the exact
case where inheriting the predecessor's context matters most.

Two halves are tested here, and the second matters as much as the first:

1. A **decided rejection** now becomes terminal, end to end (report -> run
   status -> fresh run id with predecessor + bumped iteration).
2. An **undecided or unknown** gate still does NOT. Over-correcting so that any
   unresolved gate looked terminal would fragment every in-flight run into a new
   run id on its next stage push — a worse bug than the one being fixed. The
   `gate_pending`, `withdrawn`, and unreachable-Supabase cases below are the
   guard against "fixing" this by making the check too eager.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory_sync  # noqa: E402
from supabase_rest import SupabaseRestError  # noqa: E402

OLD_RUN_ID = "bbbbbbbb-0000-0000-0000-000000000001"
SLUG = "pytest-b1-synthetic-org"


# --------------------------------------------------------------------------
# _run_status_for: the mapping itself
# --------------------------------------------------------------------------


def test_no_go_report_maps_to_a_no_go_run_status():
    assert factory_sync._run_status_for("ship", {"status": "no_go"}) == "no_go"


def test_no_go_is_terminal():
    assert "no_go" in factory_sync.TERMINAL_RUN_STATUSES


def test_no_go_is_now_reachable_at_all():
    """The regression that actually bit: `no_go` existed only inside the
    TERMINAL_RUN_STATUSES set literal, with no mapping anywhere producing it.
    Membership in the set is not evidence a status can occur.
    """
    produced = {
        factory_sync._run_status_for(stage, {"status": status})
        for stage in factory_sync.STAGE_CODES
        for status in ("no_go", "gate_pending", "approved", "REFUSED", "runbook", "proposed")
    }
    assert "no_go" in produced, "no code path produces 'no_go' — the B1 bug has regressed"


@pytest.mark.parametrize(
    "status,expected",
    [
        ("gate_pending", "blocked_on_gate"),
        ("BLOCKED_ON_GATE", "blocked_on_gate"),
    ],
)
def test_regression_an_undecided_gate_is_still_not_terminal(status, expected):
    """The over-correction guard. A gate nobody has decided yet must stay
    non-terminal, or every in-flight run fragments into a new id mid-pipeline.
    """
    got = factory_sync._run_status_for("ship", {"status": status})
    assert got == expected
    assert got not in factory_sync.TERMINAL_RUN_STATUSES


def test_no_go_on_a_non_ship_stage_is_ignored():
    """`no_go` is a ship-gate verdict specifically. A stray `no_go` elsewhere
    must not silently terminate a run from an unrelated stage's report.
    """
    assert factory_sync._run_status_for("build", {"status": "no_go"}) == "running"


# --------------------------------------------------------------------------
# _stage_row: run-level terminal != stage-level value
# --------------------------------------------------------------------------


def test_stage_row_maps_no_go_to_failed_not_passed():
    """`factory_run_stages.status`'s CHECK constraint has no `no_go` value
    (verified against 20260725003051_model_factory_tables.sql). Before B1 a
    `no_go` report fell through to `passed`, rendering a rejected ship gate as a
    green stage in the tab.
    """
    row = factory_sync._stage_row("run-id", "ship", {"status": "no_go"})
    assert row["status"] == "failed"


def test_stage_row_no_go_uses_only_schema_legal_values():
    legal = {"pending", "running", "passed", "failed", "blocked_on_gate", "skipped"}
    assert factory_sync._stage_row("run-id", "ship", {"status": "no_go"})["status"] in legal


# --------------------------------------------------------------------------
# resolve_gate_status: the three-way answer, and its fail direction
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["pending", "approved", "rejected", "withdrawn"])
def test_resolve_gate_status_returns_the_literal_status(status):
    with patch.object(factory_sync, "push_gate_request"), \
         patch.object(factory_sync, "poll_gate_status", return_value=status):
        assert factory_sync.resolve_gate_status({}, SLUG, "ship", {}, url="http://x") == status


def test_resolve_gate_status_returns_none_not_rejected_on_outage():
    """Fail-closed means "never advance on doubt", NOT "declare failure on
    doubt". A transient outage returning `rejected` would mark a live run
    terminal and orphan it behind a spurious new run id.
    """
    with patch.object(factory_sync, "push_gate_request", side_effect=SupabaseRestError("refused")):
        assert factory_sync.resolve_gate_status({}, SLUG, "ship", {}, url="http://x") is None


@pytest.mark.parametrize(
    "status,expected",
    [("approved", True), ("pending", False), ("rejected", False), ("withdrawn", False), (None, False)],
)
def test_resolve_gate_contract_is_unchanged_by_the_refactor(status, expected):
    """`resolve_gate` is now a wrapper over `resolve_gate_status`. Its contract —
    True ONLY on a literal "approved" — must be byte-identical to before, since
    S2 and S6g still depend on it and this PR must not make any gate easier.
    """
    with patch.object(factory_sync, "resolve_gate_status", return_value=status):
        assert factory_sync.resolve_gate({}, SLUG, "ship", {}) is expected


def test_gate_rejection_note_is_fail_soft():
    with patch.object(factory_sync, "get_or_create_run_id", side_effect=SupabaseRestError("down")):
        assert factory_sync.gate_rejection_note(SLUG, "ship", url="http://x") is None


# --------------------------------------------------------------------------
# stage_ship: end to end through factory.py itself
# --------------------------------------------------------------------------


@pytest.fixture
def cleanup_run_dir():
    yield SLUG
    import shutil
    shutil.rmtree(FACTORY_DIR / "runs" / SLUG, ignore_errors=True)


class _Args:
    approved = False
    dry_run = True
    live = False
    sync_url = "http://localhost:1/unreachable"


CFG = {"org": {"name": "Synthetic B1 Org", "id": "x", "customer_status": "CONTRACTED"}}


def _ship(gate_status, note=None):
    import factory as factory_module
    with patch.object(factory_module, "_gate_status_via_sync", return_value=gate_status), \
         patch.object(factory_module, "_gate_rejection_note", return_value=note), \
         patch.object(factory_module, "write_report", wraps=factory_module.write_report) as _:
        path = factory_module.stage_ship(CFG, SLUG, _Args())
    return json.loads(Path(path).read_text())


def test_rejected_gate_writes_a_no_go_report(cleanup_run_dir):
    report = _ship("rejected")
    assert report["status"] == "no_go"
    assert report["next"] == "rigor-phase (L35)"


def test_pending_gate_still_writes_gate_pending(cleanup_run_dir):
    report = _ship("pending")
    assert report["status"] == "gate_pending"


@pytest.mark.parametrize("gate_status", ["withdrawn", None])
def test_undecided_and_unknown_gates_are_not_no_go(cleanup_run_dir, gate_status):
    """`withdrawn` is a retracted request, not a rejection; `None` is an outage.
    Neither is a human saying no, so neither may terminate the run.
    """
    assert _ship(gate_status)["status"] == "gate_pending"


def test_rejection_note_lands_in_the_report_but_not_in_the_db_projection(cleanup_run_dir):
    """The rationale is the highest-signal input a retrain has, so it is captured
    on disk — but `no_go_reason` is deliberately absent from SAFE_REPORT_KEYS, so
    it must not survive into the DB payload (decision D6: don't widen the PII
    allow-list to duplicate what the tab already reads from
    `factory_gate_decisions`).
    """
    note = {"note": "eval regression on the safety anchors", "decided_by_email": "daniel@emanate.ai"}
    report = _ship("rejected", note=note)
    assert report["no_go_reason"]["note"] == "eval regression on the safety anchors"

    projected = factory_sync.project_stage_report(report)
    assert "no_go_reason" not in projected
    assert "daniel@emanate.ai" not in json.dumps(projected)


def test_approved_flag_never_consults_the_gate_status(cleanup_run_dir):
    """D4: `--approved` remains a full local bypass that touches no network.
    B1's extra status lookup must not have quietly introduced one.
    """
    import factory as factory_module

    class Args(_Args):
        approved = True

    with patch.object(factory_module, "_gate_status_via_sync") as mock_status:
        path = factory_module.stage_ship(CFG, SLUG, Args())
    mock_status.assert_not_called()
    assert json.loads(Path(path).read_text())["status"] == "approved"


# --------------------------------------------------------------------------
# the whole point: lineage now mints on the NO-GO path
# --------------------------------------------------------------------------


def test_a_no_go_run_mints_fresh_retrain_lineage(tmp_path, monkeypatch):
    """The payoff. `test_sync_run_reuse.py` already proves a `no_go` run mints a
    successor — but until B1 no run could BE `no_go`, so that arm was dead code.
    This closes the loop: rejected gate -> `no_go` run status -> terminal ->
    new run id carrying `predecessor_run_id` and `iteration` 2.
    """
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    state_path = tmp_path / "runs" / SLUG / ".sync_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"run_id": OLD_RUN_ID, "iteration": 1}))

    derived = factory_sync._run_status_for("ship", {"status": "no_go"})

    def _select(table, params=None, url=None, service_role_key=None):
        # Nothing queued from the product (factory-graph-pr2 C5 added that lookup
        # to get_or_create_run_id) — this test is about the runner minting the
        # retrain lineage on its own after a no-go.
        if (params or {}).get("status") == "eq.queued":
            return []
        return [{"status": derived, "iteration": 1}]

    with patch.object(factory_sync, "select", side_effect=_select):
        new_run_id = factory_sync.get_or_create_run_id(SLUG)

    assert new_run_id != OLD_RUN_ID
    state = factory_sync._load_run_state(SLUG)
    assert state["predecessor_run_id"] == OLD_RUN_ID
    assert state["iteration"] == 2


def test_a_merely_pending_run_does_not_mint_lineage(tmp_path, monkeypatch):
    """Same setup, undecided gate. Must reuse the id — this is the guard that
    B1 didn't buy the fix by making the terminal check too eager.
    """
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    state_path = tmp_path / "runs" / SLUG / ".sync_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"run_id": OLD_RUN_ID, "iteration": 1}))

    derived = factory_sync._run_status_for("ship", {"status": "gate_pending"})

    with patch.object(factory_sync, "select", side_effect=lambda *a, **k: [{"status": derived, "iteration": 1}]):
        assert factory_sync.get_or_create_run_id(SLUG) == OLD_RUN_ID
