"""C5 (factory-graph-pr2, 2026-07-28): the runner claims a product-queued run.

`startFactoryRun(orgId)` creates a `factory_runs` row with `status='queued'`. It
executes nothing — the stages run in `factory.py` on the operator's machine, a
process nobody has invoked at the moment of the click.

Without the claim, `get_or_create_run_id` does what it has always done: mint a
fresh uuid and cache it locally. That yields TWO non-terminal rows for one org —
the queued one nothing will ever advance, and the CLI's own. The tab lists both,
and gate cards key off `run_id`, so a human could approve a spend gate on the twin
that is not the run spending the money. That is the failure these tests exist for.

Fail-open is deliberate throughout and asserted: an unreachable Supabase must
leave the CLI working exactly as it did before this existed (decision D3 — the
sync layer is optional and must never become a hard dependency of the factory).
The cost is a stranded `queued` row, which is visible in the tab; the cost of the
other direction is the whole factory refusing to run during a network blip.
"""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory_sync  # noqa: E402
from supabase_rest import SupabaseRestError  # noqa: E402

SLUG = "qa-dryrun-synth"
QUEUED_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def run_root(tmp_path, monkeypatch):
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    return tmp_path


@pytest.fixture
def rest(monkeypatch):
    """Records calls and serves canned responses for select/update."""
    calls = {"select": [], "update": []}
    state = {"select": [], "update": [{"id": QUEUED_ID}]}

    def fake_select(table, params=None, **kwargs):
        calls["select"].append((table, params or {}))
        result = state["select"]
        return result(params) if callable(result) else result

    def fake_update(table, filters, values, **kwargs):
        calls["update"].append((table, filters, values))
        result = state["update"]
        return result(filters) if callable(result) else result

    monkeypatch.setattr(factory_sync, "select", fake_select)
    monkeypatch.setattr(factory_sync, "update", fake_update)
    return calls, state


# --------------------------------------------------------------------------
# The core behaviour: one org, one run
# --------------------------------------------------------------------------


def test_a_queued_run_is_claimed_instead_of_a_new_one_being_minted(run_root, rest):
    calls, state = rest
    state["select"] = [{"id": QUEUED_ID, "iteration": 1, "predecessor_run_id": None}]

    run_id = factory_sync.get_or_create_run_id(SLUG)

    assert run_id == QUEUED_ID, "the CLI minted its own id and orphaned the queued row"
    assert factory_sync._load_run_state(SLUG)["run_id"] == QUEUED_ID


def test_claiming_flips_the_status_to_running(run_root, rest):
    """The flip IS the claim. Left as `queued`, a second runner would claim the
    same row, and the tab would keep saying nothing is executing while it is."""
    calls, state = rest
    state["select"] = [{"id": QUEUED_ID, "iteration": 1, "predecessor_run_id": None}]

    factory_sync.get_or_create_run_id(SLUG)

    table, filters, values = calls["update"][0]
    assert table == "factory_runs"
    assert values == {"status": "running"}
    assert filters["id"] == f"eq.{QUEUED_ID}"


def test_the_claim_is_filtered_on_queued_so_two_runners_cannot_both_win(run_root, rest):
    """`status=eq.queued` in the PATCH filter, not just the SELECT. Postgres
    decides the winner; the loser updates zero rows and is told so."""
    calls, state = rest
    state["select"] = [{"id": QUEUED_ID, "iteration": 1, "predecessor_run_id": None}]

    factory_sync.get_or_create_run_id(SLUG)

    _, filters, _ = calls["update"][0]
    assert filters.get("status") == "eq.queued"


def test_losing_the_race_mints_a_local_id_rather_than_stealing_the_row(run_root, rest):
    calls, state = rest
    state["select"] = [{"id": QUEUED_ID, "iteration": 1, "predecessor_run_id": None}]
    state["update"] = []  # zero rows updated: another runner got there first

    run_id = factory_sync.get_or_create_run_id(SLUG)

    assert run_id != QUEUED_ID
    assert factory_sync._load_run_state(SLUG)["run_id"] == run_id


def test_it_only_claims_runs_for_this_org(run_root, rest):
    calls, state = rest
    state["select"] = [{"id": QUEUED_ID, "iteration": 1, "predecessor_run_id": None}]

    factory_sync.get_or_create_run_id(SLUG)

    _, params = calls["select"][0]
    assert params["org_slug"] == f"eq.{SLUG}"
    assert params["status"] == "eq.queued"


def test_the_oldest_queued_run_is_claimed_first(run_root, rest):
    calls, state = rest
    state["select"] = [{"id": QUEUED_ID, "iteration": 1, "predecessor_run_id": None}]

    factory_sync.get_or_create_run_id(SLUG)

    _, params = calls["select"][0]
    assert params["order"] == "started_at.asc"


def test_nothing_queued_mints_as_before(run_root, rest):
    """The CLI is still the ordinary way a run starts, and it queues nothing. That
    path must be untouched."""
    calls, state = rest
    state["select"] = []

    run_id = factory_sync.get_or_create_run_id(SLUG)

    assert run_id and run_id != QUEUED_ID
    assert calls["update"] == []


def test_the_claimed_iteration_and_lineage_are_adopted_not_reset(run_root, rest):
    """A queued retrain carries its own iteration and predecessor. Overwriting
    them with 1/None would make iteration 4 of an org look like a first train, and
    the predecessor digest would never be found."""
    calls, state = rest
    predecessor = "22222222-2222-2222-2222-222222222222"
    state["select"] = [{"id": QUEUED_ID, "iteration": 4, "predecessor_run_id": predecessor}]

    factory_sync.get_or_create_run_id(SLUG)

    saved = factory_sync._load_run_state(SLUG)
    assert saved["iteration"] == 4
    assert saved["predecessor_run_id"] == predecessor


# --------------------------------------------------------------------------
# Fail-open — the sync layer stays optional
# --------------------------------------------------------------------------


def test_an_unreachable_supabase_still_yields_a_run_id(run_root, monkeypatch):
    def boom(*a, **kw):
        raise SupabaseRestError("connection refused")

    monkeypatch.setattr(factory_sync, "select", boom)
    run_id = factory_sync.get_or_create_run_id(SLUG)
    assert run_id, "a network blip must not stop the factory (decision D3)"


def test_a_failing_claim_patch_still_yields_a_run_id(run_root, rest):
    calls, state = rest
    state["select"] = [{"id": QUEUED_ID, "iteration": 1, "predecessor_run_id": None}]

    def boom(*a, **kw):
        raise SupabaseRestError("timeout")

    state["update"] = boom
    run_id = factory_sync.get_or_create_run_id(SLUG)
    assert run_id and run_id != QUEUED_ID


def test_claim_queued_run_returns_none_rather_than_raising(run_root, monkeypatch):
    def boom(*a, **kw):
        raise SupabaseRestError("nope")

    monkeypatch.setattr(factory_sync, "select", boom)
    assert factory_sync.claim_queued_run(SLUG) is None


@pytest.mark.parametrize("row", [{}, {"status": "running"}, {"id": None}, None])
def test_an_unexpected_row_shape_fails_open_instead_of_crashing(run_root, rest, row):
    """A row without an `id` is not a shape this function understands, and reading
    it with `[...]` would raise a KeyError that escapes the SupabaseRestError
    handler and takes down the CLI — the exact opposite of fail-open. Treated like
    an unreachable Supabase: mint locally and carry on."""
    calls, state = rest
    state["select"] = [row]

    assert factory_sync.claim_queued_run(SLUG) is None
    assert calls["update"] == [], "must not attempt to claim a row it could not identify"


# --------------------------------------------------------------------------
# Interaction with the existing retrain-lineage logic (D22)
# --------------------------------------------------------------------------


def test_a_live_cached_run_is_not_replaced_by_a_queued_one(run_root, rest):
    """The common case, and the one that must not regress: a run already in flight
    keeps its id. Adopting a queued row mid-run would split one run across two
    rows — the exact D22 regression, from the other direction."""
    calls, state = rest
    factory_sync._save_run_state(SLUG, {"run_id": "aaaaaaaa-0000-0000-0000-000000000000", "iteration": 2})
    state["select"] = [{"status": "running", "iteration": 2}]

    run_id = factory_sync.get_or_create_run_id(SLUG)

    assert run_id == "aaaaaaaa-0000-0000-0000-000000000000"
    assert calls["update"] == []


def test_a_terminal_cached_run_adopts_a_queued_retrain(run_root, rest):
    """The retrain flow this deliverable describes: the prior run shipped, Daniel
    clicks Start in the tab, then runs the CLI. Minting here regardless would
    strand the queued row forever."""
    calls, state = rest
    old = "aaaaaaaa-0000-0000-0000-000000000000"
    factory_sync._save_run_state(SLUG, {"run_id": old, "iteration": 2})

    def by_params(params):
        # First call is the status check on the cached run; second is the claim.
        if params.get("id") == f"eq.{old}":
            return [{"status": "shipped", "iteration": 2}]
        return [{"id": QUEUED_ID, "iteration": 3, "predecessor_run_id": old}]

    state["select"] = by_params

    run_id = factory_sync.get_or_create_run_id(SLUG)

    assert run_id == QUEUED_ID
    saved = factory_sync._load_run_state(SLUG)
    assert saved["predecessor_run_id"] == old
    assert saved["iteration"] == 3


def test_a_terminal_cached_run_with_nothing_queued_still_mints_a_retrain(run_root, rest):
    """D22's behaviour, unchanged when the product is not involved."""
    calls, state = rest
    old = "aaaaaaaa-0000-0000-0000-000000000000"
    factory_sync._save_run_state(SLUG, {"run_id": old, "iteration": 2})

    def by_params(params):
        return [{"status": "no_go", "iteration": 2}] if params.get("id") else []

    state["select"] = by_params

    run_id = factory_sync.get_or_create_run_id(SLUG)

    assert run_id not in (old, QUEUED_ID)
    saved = factory_sync._load_run_state(SLUG)
    assert saved["predecessor_run_id"] == old
    assert saved["iteration"] == 3


def test_queued_is_not_a_terminal_status():
    """If `queued` were terminal, `get_or_create_run_id` would mint a new run every
    invocation for an org whose run it had claimed."""
    assert "queued" not in factory_sync.TERMINAL_RUN_STATUSES
