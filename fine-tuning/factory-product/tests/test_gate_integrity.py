"""C4 (factory-graph-pr2, 2026-07-28): a dispatched node may not approve a gate.

Found while building C4, not from the plan. C4 requires the synthesized subagent
be "gate-denied", and writing that guarantee meant establishing what an approval
is. It is one file existing (`factory.py:119`), checked with no content or
provenance validation, while every dispatched node holds
`Write`/`Edit`/`Bash` with `cwd=REPO`.

So the guarantee was already broken for the ~10 agents S4-S10 dispatch, S6g (the
spend gate) included. Not hypothetically: `runs/<org>/approvals/launch.json` is a
plain file in a repo where every other piece of run state is also a JSON file
under `runs/<org>/`, so an agent asked about gate status writing it is an ordinary
mistake rather than an attack.

These tests cover the detection guard. They are worth more than the guard's line
count suggests, because the guard's failure mode is silence: if the snapshot is
taken at the wrong moment, or skipped when a node fails, nothing looks wrong — a
gate is just approved, and the next stage spends money.
"""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory_gates as fg  # noqa: E402
import factory_node_run as fnr  # noqa: E402

SLUG = "qa-dryrun-synth"


@pytest.fixture
def approvals(tmp_path, monkeypatch):
    monkeypatch.setattr(fg, "HERE", tmp_path)
    d = tmp_path / "runs" / SLUG / "approvals"
    d.mkdir(parents=True)
    return d


def _approve(approvals_dir, gate, note="human did this"):
    (approvals_dir / f"{gate}.json").write_text(json.dumps({"gate": gate, "note": note}))


# --------------------------------------------------------------------------
# Snapshot / diff
# --------------------------------------------------------------------------


def test_snapshot_covers_every_human_gate(approvals):
    assert set(fg.snapshot(SLUG)) == {"governance", "launch", "ship"}


def test_an_unapproved_gate_snapshots_as_none(approvals):
    assert fg.snapshot(SLUG)["launch"] is None


def test_no_change_is_no_diff(approvals):
    _approve(approvals, "governance")
    before = fg.snapshot(SLUG)
    assert fg.diff(before, fg.snapshot(SLUG)) == {}


def test_a_created_approval_is_a_diff(approvals):
    before = fg.snapshot(SLUG)
    _approve(approvals, "launch")
    assert "launch" in fg.diff(before, fg.snapshot(SLUG))


def test_an_edited_approval_is_a_diff(approvals):
    """Content-digested, not existence-checked: editing an approval in place (its
    note, or which gate it claims to be) must not read as unchanged."""
    _approve(approvals, "ship", note="original")
    before = fg.snapshot(SLUG)
    _approve(approvals, "ship", note="tampered")
    assert "ship" in fg.diff(before, fg.snapshot(SLUG))


def test_a_deleted_approval_is_a_diff(approvals):
    """A node that deletes an approval grants itself nothing, but it has silently
    reverted a human decision, and the run must not carry on as if it stood."""
    _approve(approvals, "governance")
    before = fg.snapshot(SLUG)
    (approvals / "governance.json").unlink()
    assert "governance" in fg.diff(before, fg.snapshot(SLUG))


# --------------------------------------------------------------------------
# Quarantine — the revocation
# --------------------------------------------------------------------------


def test_quarantine_revokes_by_moving_and_keeps_the_evidence(approvals):
    """`gate_approved` is existence-based, so moving the file IS the revocation."""
    _approve(approvals, "launch")
    dest = fg.quarantine(SLUG, "launch")
    assert not (approvals / "launch.json").exists()
    assert dest.is_file() and json.loads(dest.read_text())["gate"] == "launch"


def test_quarantine_is_a_noop_when_there_is_nothing_there(approvals):
    assert fg.quarantine(SLUG, "ship") is None


def test_two_quarantines_do_not_overwrite_each_other(approvals):
    """Timestamped filenames: the second forgery of the same gate must not erase
    the record of the first."""
    _approve(approvals, "launch", note="first")
    first = fg.quarantine(SLUG, "launch")
    _approve(approvals, "launch", note="second")
    second = fg.quarantine(SLUG, "launch")
    assert first != second and first.is_file() and second.is_file()


# --------------------------------------------------------------------------
# assert_unchanged
# --------------------------------------------------------------------------


def test_assert_unchanged_passes_when_nothing_moved(approvals):
    _approve(approvals, "governance")
    fg.assert_unchanged(SLUG, fg.snapshot(SLUG), node_id="S7:train-launcher")


def test_assert_unchanged_raises_quarantines_and_logs(approvals):
    before = fg.snapshot(SLUG)
    _approve(approvals, "launch", note="approved by the agent, allegedly")

    with pytest.raises(fg.GateForgeryError) as exc:
        fg.assert_unchanged(SLUG, before, node_id="S7:train-launcher")

    message = str(exc.value)
    assert "launch" in message and "S7:train-launcher" in message
    # The forged approval no longer grants passage...
    assert not (approvals / "launch.json").exists()
    # ...the evidence survives...
    assert list((approvals / "quarantine").glob("launch.*.json"))
    # ...and it is attributable.
    entry = json.loads((approvals / "FORGERY-LOG.jsonl").read_text().strip())
    assert entry["gate"] == "launch" and entry["node_id"] == "S7:train-launcher"


def test_the_error_tells_daniel_how_to_recover(approvals):
    """The one legitimate way to trip this is Daniel approving in a second terminal
    mid-run. If the message does not say how to reinstate the approval, a correct
    guard reads as data loss and the next person removes it."""
    before = fg.snapshot(SLUG)
    _approve(approvals, "ship")
    with pytest.raises(fg.GateForgeryError, match="--approved"):
        fg.assert_unchanged(SLUG, before, node_id="S9-check")


def test_all_forged_gates_are_quarantined_not_just_the_first(approvals):
    before = fg.snapshot(SLUG)
    _approve(approvals, "governance")
    _approve(approvals, "launch")
    _approve(approvals, "ship")
    with pytest.raises(fg.GateForgeryError):
        fg.assert_unchanged(SLUG, before, node_id="greedy-node")
    assert not list(approvals.glob("*.json"))


def test_a_forged_approval_no_longer_reads_as_approved(approvals, monkeypatch):
    """End to end against the real predicate, because everything above tests this
    module's own view of the file. What matters is that `factory.py.gate_approved`
    — the function the run actually branches on — returns False afterwards."""
    import factory

    monkeypatch.setattr(factory, "HERE", approvals.parents[2])
    before = fg.snapshot(SLUG)
    _approve(approvals, "launch")
    assert factory.gate_approved(SLUG, "launch") is True  # the hole, demonstrated
    with pytest.raises(fg.GateForgeryError):
        fg.assert_unchanged(SLUG, before, node_id="S7:train-launcher")
    assert factory.gate_approved(SLUG, "launch") is False  # and closed


# --------------------------------------------------------------------------
# Wired into the dispatcher at the right moment
# --------------------------------------------------------------------------


@pytest.fixture
def live_dispatch(tmp_path, monkeypatch):
    """A live (non-dry-run) dispatch with a fake runner, sharing one run root with
    factory_gates so the guard watches the directory the fake node writes to."""
    monkeypatch.setattr(fnr, "HERE", tmp_path)
    # REPO too: dispatch_node reports the prompt path relative to it, so leaving it
    # pointing at the real checkout makes the relative_to fail on a tmp run root.
    monkeypatch.setattr(fnr, "REPO", tmp_path)
    monkeypatch.setattr(fg, "HERE", tmp_path)
    monkeypatch.setattr(fnr, "load_spec", lambda slug: {
        "slug": slug, "role_family": "builder", "mission": "m", "read_first": [],
        "done_when": "d", "final_response_shape": "s",
    })
    (tmp_path / "runs" / SLUG / "approvals").mkdir(parents=True)
    return tmp_path


class _Proc:
    def __init__(self, returncode=0):
        self.returncode, self.stdout, self.stderr = returncode, "done", ""


def test_the_dispatcher_catches_a_node_that_writes_an_approval(live_dispatch):
    approvals_dir = live_dispatch / "runs" / SLUG / "approvals"

    def runner(argv):
        _approve(approvals_dir, "launch")  # the node forges its own spend approval
        return _Proc(0)

    with pytest.raises(fg.GateForgeryError):
        fnr.dispatch_node("corpus-planner", "S4", None, "g", SLUG, dry_run=False, runner=runner)
    assert not (approvals_dir / "launch.json").exists()


def test_it_catches_a_node_that_forges_and_then_fails(live_dispatch):
    """The check runs before the exit code is consulted. A node that wrote an
    approval and then exited non-zero has still written it, and 'the node failed'
    is exactly when nobody looks closely at what it left behind."""
    approvals_dir = live_dispatch / "runs" / SLUG / "approvals"

    def runner(argv):
        _approve(approvals_dir, "ship")
        return _Proc(1)

    with pytest.raises(fg.GateForgeryError):
        fnr.dispatch_node("corpus-planner", "S4", None, "g", SLUG, dry_run=False, runner=runner)


def test_a_well_behaved_live_node_is_unaffected(live_dispatch):
    result = fnr.dispatch_node("corpus-planner", "S4", None, "g", SLUG,
                               dry_run=False, runner=lambda argv: _Proc(0))
    assert result["ok"] is True and result["dispatched"] is True


def test_a_preexisting_approval_is_not_mistaken_for_a_forgery(live_dispatch):
    """The guard compares against a snapshot taken at spawn time, so a gate Daniel
    approved before the run must survive it. A false positive here quarantines a
    real human decision, which is worse than useless."""
    approvals_dir = live_dispatch / "runs" / SLUG / "approvals"
    _approve(approvals_dir, "governance")
    result = fnr.dispatch_node("corpus-planner", "S4", None, "g", SLUG,
                               dry_run=False, runner=lambda argv: _Proc(0))
    assert result["ok"] is True
    assert (approvals_dir / "governance.json").exists()


def test_dry_run_needs_no_guard_and_still_spawns_nothing(live_dispatch):
    def runner(argv):
        pytest.fail("dry run spawned a subprocess")

    result = fnr.dispatch_node("corpus-planner", "S4", None, "g", SLUG, dry_run=True, runner=runner)
    assert result["dispatched"] is False
