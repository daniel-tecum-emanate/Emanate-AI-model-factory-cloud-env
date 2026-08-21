"""T12 — factory_sync gate push/poll + factory.py's wiring into the 3 human-gate
stages. **Fail-closed**, the opposite direction from T11's fail-open push
(PRRules.md rule 5) — every outcome except a literal `"approved"` status refuses
to advance, including a poll/push transport error.

PRTests.md's own scenarios for this file:
- pending response -> factory.py does not advance
- approved response -> advances
- rejected response -> does not advance, run marked accordingly
- network/HTTP error on poll -> does not advance (fail-closed, explicitly the
  opposite direction from T11's fail-open test)
- `factory.py --approved` with an unreachable sync URL still advances (fallback
  path regression — proves --approved never even touches the network)
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory_sync  # noqa: E402
from supabase_rest import SupabaseRestError  # noqa: E402

DECISION_REQUEST = {
    "decision": "clear Synthetic Test Org (synthetic-test-org) for data-use / SFT training",
    "recommendation": "approve",
    "checklist": {"customer_status_confirmed": True},
}


@pytest.fixture
def cfg():
    return {"org": {"id": "org-uuid-123", "name": "Synthetic Test Org"}, "champion": {"model_id": None}}


@pytest.fixture(autouse=True)
def isolated_run_state(tmp_path, monkeypatch):
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    yield


# --------------------------------------------------------------------------
# factory_sync.resolve_gate: the 4 fail-closed-direction cases
# --------------------------------------------------------------------------


def test_pending_status_does_not_resolve_approved(cfg):
    with patch.object(factory_sync, "push_gate_request", return_value={"status": "pending"}), \
         patch.object(factory_sync, "poll_gate_status", return_value="pending"):
        assert factory_sync.resolve_gate(cfg, "synthetic-test-org", "governance", DECISION_REQUEST) is False


def test_approved_status_resolves_approved(cfg):
    with patch.object(factory_sync, "push_gate_request", return_value={"status": "approved"}), \
         patch.object(factory_sync, "poll_gate_status", return_value="approved"):
        assert factory_sync.resolve_gate(cfg, "synthetic-test-org", "governance", DECISION_REQUEST) is True


def test_rejected_status_does_not_resolve_approved(cfg):
    with patch.object(factory_sync, "push_gate_request", return_value={"status": "rejected"}), \
         patch.object(factory_sync, "poll_gate_status", return_value="rejected"):
        assert factory_sync.resolve_gate(cfg, "synthetic-test-org", "governance", DECISION_REQUEST) is False


def test_network_error_on_poll_does_not_resolve_approved():
    """The opposite direction from T11's fail-open test, made explicit: a
    transport failure on the gate check must NEVER be read as approval.
    """
    with patch.object(factory_sync, "push_gate_request", return_value={"status": "pending"}), \
         patch("factory_sync.select", side_effect=SupabaseRestError("connection refused")):
        cfg = {"org": {"id": "x", "name": "X"}, "champion": {"model_id": None}}
        assert factory_sync.resolve_gate(cfg, "synthetic-test-org", "governance", DECISION_REQUEST) is False


def test_network_error_on_push_also_fails_closed(cfg):
    """A failure on the PUSH half (not just the poll half) must also refuse —
    resolve_gate's own try/except around push_gate_request.
    """
    with patch.object(factory_sync, "push_gate_request", side_effect=SupabaseRestError("boom")):
        assert factory_sync.resolve_gate(cfg, "synthetic-test-org", "governance", DECISION_REQUEST) is False


def test_no_gate_request_row_yet_does_not_resolve_approved(cfg):
    """poll_gate_status returning None (no row exists yet) must not be
    misread as approved either.
    """
    with patch.object(factory_sync, "push_gate_request", return_value={"status": "pending"}), \
         patch.object(factory_sync, "poll_gate_status", return_value=None):
        assert factory_sync.resolve_gate(cfg, "synthetic-test-org", "governance", DECISION_REQUEST) is False


# --------------------------------------------------------------------------
# push_gate_request idempotency (no duplicate pending row created)
# --------------------------------------------------------------------------


def test_push_gate_request_is_a_no_op_when_a_request_already_exists():
    with patch("factory_sync.select", return_value=[{"id": "existing-id", "status": "pending"}]), \
         patch("factory_sync.insert") as mock_insert:
        result = factory_sync.push_gate_request(
            {"org": {"name": "X"}}, "synthetic-test-org", "governance", DECISION_REQUEST
        )
    mock_insert.assert_not_called()
    assert result == {"id": "existing-id", "status": "pending"}


def test_push_gate_request_inserts_when_none_exists():
    with patch("factory_sync.select", return_value=[]), \
         patch("factory_sync.insert", return_value=[{"id": "new-id", "status": "pending"}]) as mock_insert:
        result = factory_sync.push_gate_request(
            {"org": {"name": "X"}}, "synthetic-test-org", "governance", DECISION_REQUEST
        )
    mock_insert.assert_called_once()
    assert result["status"] == "pending"


# --------------------------------------------------------------------------
# factory.py's own wiring: --approved is a full bypass that never calls sync
# --------------------------------------------------------------------------


@pytest.fixture
def cleanup_test_org_run_dir():
    """`factory.py`'s own `run_dir()`/`write_report()` write real files under
    `runs/<org>/` (gitignored) using the real `REPO` constant for its
    `relative_to()` print — easier to use a real, disposable org slug under the
    real tree and clean it up than to monkeypatch `REPO`/`run_dir` and fight
    that path-relativity assumption.
    """
    slug = "pytest-t12-synthetic-org"
    yield slug
    import shutil
    shutil.rmtree(FACTORY_DIR / "runs" / slug, ignore_errors=True)


def test_approved_flag_never_calls_gate_approved_via_sync(cleanup_test_org_run_dir):
    """Import factory.py itself (not just factory_sync) and prove `--approved`
    short-circuits before `_gate_approved_via_sync` is ever invoked — this is
    the actual regression the `--approved` + unreachable `--sync-url` CLI
    scenario in PRDebug.md exercises end-to-end; this unit test proves the
    same thing at the function level, faster and without a real subprocess.
    """
    sys.path.insert(0, str(FACTORY_DIR))
    import factory as factory_module

    slug = cleanup_test_org_run_dir

    class Args:
        approved = True
        dry_run = True
        live = False
        sync_url = "http://localhost:1/unreachable"

    cfg = {
        "org": {"name": "Synthetic Test Org", "id": "x", "customer_status": "CONTRACTED"},
        "governance": {
            "data_use_clearance": "CLEARED",
            "dpa_signed": True,
            "training_rights_clause": True,
            "pii_egress_cleared": True,
            "permitted_uses": ["sft"],
        },
    }
    with patch.object(factory_module, "_gate_approved_via_sync") as mock_sync_check:
        path = factory_module.stage_governance(cfg, slug, Args())
    mock_sync_check.assert_not_called()
    report = __import__("json").loads(Path(path).read_text())
    assert report["status"] == "approved"


def test_no_approved_flag_and_no_sync_url_auto_approves_a_clear_checklist_with_no_network_attempt(
    cleanup_test_org_run_dir, monkeypatch
):
    """2026-08-19 (Daniel): a clear governance checklist auto-approves by
    policy even with no `--approved`, no `--sync-url`, no
    `FACTORY_SUPABASE_URL` — and still reaches no network code path at all,
    since the auto-approve branch never calls `_gate_approved_via_sync`.
    """
    sys.path.insert(0, str(FACTORY_DIR))
    import factory as factory_module

    monkeypatch.delenv("FACTORY_SUPABASE_URL", raising=False)
    slug = cleanup_test_org_run_dir

    class Args:
        approved = False
        dry_run = True
        live = False
        sync_url = None

    cfg = {
        "org": {"name": "Synthetic Test Org", "id": "x", "customer_status": "CONTRACTED"},
        "governance": {
            "data_use_clearance": "CLEARED",
            "dpa_signed": True,
            "training_rights_clause": True,
            "pii_egress_cleared": True,
            "permitted_uses": ["sft"],
        },
    }
    with patch("factory_sync.resolve_gate") as mock_resolve:
        path = factory_module.stage_governance(cfg, slug, Args())
    mock_resolve.assert_not_called()
    report = __import__("json").loads(Path(path).read_text())
    assert report["status"] == "approved"
    assert report["approved_by"] == "policy"


def test_no_approved_flag_and_incomplete_checklist_stays_pending_with_no_network_attempt(
    cleanup_test_org_run_dir, monkeypatch
):
    """The fail-safe this gate exists for: an incomplete checklist still stays
    pending with no `--approved`/sync configured — auto-approval never fires
    for a checklist that isn't genuinely clear."""
    sys.path.insert(0, str(FACTORY_DIR))
    import factory as factory_module

    monkeypatch.delenv("FACTORY_SUPABASE_URL", raising=False)
    slug = cleanup_test_org_run_dir

    class Args:
        approved = False
        dry_run = True
        live = False
        sync_url = None

    cfg = {
        "org": {"name": "Synthetic Test Org", "id": "x", "customer_status": "CONTRACTED"},
        "governance": {
            "data_use_clearance": "CLEARED",
            "dpa_signed": False,
            "training_rights_clause": True,
            "pii_egress_cleared": True,
            "permitted_uses": ["sft"],
        },
    }
    with patch("factory_sync.resolve_gate") as mock_resolve:
        path = factory_module.stage_governance(cfg, slug, Args())
    mock_resolve.assert_not_called()
    report = __import__("json").loads(Path(path).read_text())
    assert report["status"] == "gate_pending"
