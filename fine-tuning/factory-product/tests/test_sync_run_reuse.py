"""get_or_create_run_id's retrain-safety fix (QA subagent, 2026-07-24 — DELTAS.md D22).

Before this fix: a cached run_id was reused FOREVER for an org slug, with nothing
checking whether the run it pointed at had already finished. Two concrete, proven
consequences on a real retrain: (1) a finished run's `factory_runs`/`factory_run_stages`
history got silently overwritten in place as the retrain re-walked S1..S11, and (2) a
retrain's human gate resolved to `approved` purely because the OLD run's already-decided
`factory_gate_requests` row sat under the same `(run_id, gate)` key — zero new
`decide_factory_gate` call. This file proves both are now closed, and that the
common (non-retrain) case is completely unaffected.
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

OLD_RUN_ID = "aaaaaaaa-0000-0000-0000-000000000001"
SLUG = "grand-steel"


@pytest.fixture
def seeded_state(tmp_path, monkeypatch):
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    state_path = tmp_path / "runs" / SLUG / ".sync_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"run_id": OLD_RUN_ID, "iteration": 1}))
    return tmp_path


def _select_returning(status, iteration=1, run_id=OLD_RUN_ID):
    """Answers the TWO `factory_runs` queries `get_or_create_run_id` now makes.

    Routed on the params rather than on call order (2026-07-28, factory-graph-pr2
    C5): the cached run's status check is keyed on `id`, and the newer
    `claim_queued_run` lookup is keyed on `org_slug` + `status=eq.queued`. This
    stub answers the claim query with "nothing queued", which is the right default
    for every test in this file — they are about the runner acting alone, with the
    product not involved. The product-queued path is covered in
    `test_queued_run_claim.py`.
    """
    def _select(table, params=None, url=None, service_role_key=None):
        assert table == "factory_runs"
        if params.get("status") == "eq.queued":
            return []
        assert params["id"] == f"eq.{run_id}"
        return [{"status": status, "iteration": iteration}]

    return _select


def test_running_prior_run_reuses_the_same_id(seeded_state):
    """The overwhelmingly common case — a still-in-progress run's own later stage
    pushes must keep landing on the same row, completely unaffected by this fix.
    """
    with patch.object(factory_sync, "select", side_effect=_select_returning("running")):
        run_id = factory_sync.get_or_create_run_id(SLUG)
    assert run_id == OLD_RUN_ID


def test_blocked_on_gate_prior_run_also_reuses_the_same_id(seeded_state):
    with patch.object(factory_sync, "select", side_effect=_select_returning("blocked_on_gate")):
        run_id = factory_sync.get_or_create_run_id(SLUG)
    assert run_id == OLD_RUN_ID


@pytest.mark.parametrize("terminal_status", ["shipped", "failed", "no_go", "abandoned"])
def test_terminal_prior_run_mints_a_new_id_with_predecessor_and_bumped_iteration(
    seeded_state, terminal_status
):
    with patch.object(factory_sync, "select", side_effect=_select_returning(terminal_status, iteration=1)):
        run_id = factory_sync.get_or_create_run_id(SLUG)
    assert run_id != OLD_RUN_ID

    state = factory_sync._load_run_state(SLUG)
    assert state["run_id"] == run_id
    assert state["predecessor_run_id"] == OLD_RUN_ID
    assert state["iteration"] == 2


def test_a_third_retrain_chains_the_iteration_and_predecessor_correctly(seeded_state):
    """iteration 1 (shipped) -> iteration 2 (this fix mints it) -> iteration 2 shipped
    -> iteration 3. Proves the chain doesn't just always jump to 2.
    """
    with patch.object(factory_sync, "select", side_effect=_select_returning("shipped", iteration=1)):
        second_run_id = factory_sync.get_or_create_run_id(SLUG)
    assert factory_sync._load_run_state(SLUG)["iteration"] == 2

    with patch.object(
        factory_sync, "select",
        side_effect=_select_returning("shipped", iteration=2, run_id=second_run_id),
    ):
        third_run_id = factory_sync.get_or_create_run_id(SLUG)

    assert third_run_id not in (OLD_RUN_ID, second_run_id)
    state = factory_sync._load_run_state(SLUG)
    assert state["predecessor_run_id"] == second_run_id
    assert state["iteration"] == 3


def test_status_check_failure_fails_open_to_reusing_the_cached_id(seeded_state):
    """Matches push_stage's own fail-open direction — a transient Supabase outage
    must never mint a spurious extra run just because the status check couldn't run.
    """
    with patch.object(factory_sync, "select", side_effect=SupabaseRestError("connection refused")):
        run_id = factory_sync.get_or_create_run_id(SLUG)
    assert run_id == OLD_RUN_ID


def test_no_prior_state_at_all_mints_fresh_with_iteration_one_and_no_predecessor(tmp_path, monkeypatch):
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    with patch.object(factory_sync, "select", return_value=[]) as mock_select:
        run_id = factory_sync.get_or_create_run_id(SLUG)

    # Used to assert `select` was never called — "first-ever run for this org,
    # nothing to check yet". That stopped being true in factory-graph-pr2 (C5):
    # there IS now something to check on a cold start, namely whether the product
    # queued a run for this org that this invocation should adopt rather than
    # duplicate. Exactly one call, and it is that lookup — no status check, since
    # there is still no cached run to check the status of.
    assert mock_select.call_count == 1
    assert mock_select.call_args.kwargs.get("params", {}).get("status") == "eq.queued"

    state = factory_sync._load_run_state(SLUG)
    assert state["run_id"] == run_id
    assert state["iteration"] == 1
    assert "predecessor_run_id" not in state


def test_run_row_carries_iteration_and_predecessor_once_minted(seeded_state):
    """_run_row (T11's factory_runs upsert payload) must actually surface the new
    iteration/predecessor once get_or_create_run_id has minted them — otherwise the
    fix mints the right id but the row itself still looks like a bare iteration-1 run.
    """
    with patch.object(factory_sync, "select", side_effect=_select_returning("shipped", iteration=1)):
        new_run_id = factory_sync.get_or_create_run_id(SLUG)

    cfg = {"org": {"id": "org-1", "name": "Grand Steel"}, "champion": {"model_id": "ft:some-model"}}
    row = factory_sync._run_row(cfg, SLUG, "census", {"status": "ok"}, new_run_id)
    assert row["iteration"] == 2
    assert row["predecessor_run_id"] == OLD_RUN_ID
    assert row["run_kind"] == "monthly-retrain"  # champion set -> already-served org, matches


def test_run_row_omits_iteration_fields_for_a_still_fresh_first_run(tmp_path, monkeypatch):
    """A brand-new org's first-ever run must not carry a stray predecessor_run_id —
    proves the fields are genuinely conditional, not always injected.
    """
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    run_id = factory_sync.get_or_create_run_id("fresh-org")
    cfg = {"org": {"id": "org-2", "name": "Fresh Org"}, "champion": {"model_id": None}}
    row = factory_sync._run_row(cfg, "fresh-org", "census", {"status": "ok"}, run_id)
    assert row["iteration"] == 1
    assert "predecessor_run_id" not in row


def test_a_retrains_gate_request_no_longer_sees_the_predecessors_stale_approval(seeded_state):
    """The actual proof this closes the gate-carryover bug: once a new run_id is
    minted for the retrain, poll_gate_status for that NEW run_id must find nothing
    (fresh pending state) even though the OLD run_id has a real, approved gate row
    sitting in the fixture "DB" under the old (run_id, gate) key.
    """
    fake_db = {OLD_RUN_ID: {"S2": "approved"}}

    def _select_router(table, params=None, url=None, service_role_key=None):
        if table == "factory_runs":
            # Nothing queued for this org (C5's claim lookup) — this test is about
            # the runner minting a retrain id on its own.
            if params.get("status") == "eq.queued":
                return []
            return [{"status": "shipped", "iteration": 1}]
        assert table == "factory_gate_requests"
        run_id = params["run_id"].split(".", 1)[1]
        gate = params["gate"].split(".", 1)[1]
        status = fake_db.get(run_id, {}).get(gate)
        return [{"status": status}] if status else []

    with patch.object(factory_sync, "select", side_effect=_select_router):
        status = factory_sync.poll_gate_status(SLUG, "governance")

    # the new run_id is a fresh uuid, never a key in fake_db -> no stale carryover
    assert status is None
