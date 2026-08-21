"""agents_sync_job — the scheduled poll wiring `factory_sync.push_agents()` (T13) up
to run periodically (Daniel's 2026-07-24 ask). Covers: only OPEN_STATUSES runs get
synced, a finished run is skipped (never re-activated by the poll), an unreachable
Supabase fails open (skip, never crash the whole poll), and a run with no local
`.sync_state.json` is skipped without error.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import agents_sync_job  # noqa: E402
from supabase_rest import SupabaseRestError  # noqa: E402

RUN_ID_OPEN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
RUN_ID_SHIPPED = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.fixture(autouse=True)
def _no_real_cloud_fleet_fetch(monkeypatch):
    """T1-1 (model-factory-finetune-launcher-v1): `sync_all_open_runs` now also
    runs the cloud-fleet heartbeat pass (`factory_sync.push_cloud_fleet_agents`),
    which shells out to real `git fetch`/`git show`. Patched out globally, for
    every test in this module, so the existing fixture-repo tests stay hermetic
    and fast — they must never touch the network or the real repo's git refs.
    Deliberately NOT part of `sync_all_open_runs`'s returned summary dict (same
    "not added to the summary" treatment as the artifacts pass), so this default
    stub needs no corresponding key in any of the `summary == {...}` assertions
    below.
    """
    monkeypatch.setattr(agents_sync_job, "push_cloud_fleet_agents", lambda **kwargs: 0)


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """Redirect every path agents_sync_job reads to a throwaway tmp tree, so this
    test never touches the real heartbeat/STATE.md, ledger/, or runs/ directories.
    """
    runs_dir = tmp_path / "runs"
    (runs_dir / "open-org").mkdir(parents=True)
    (runs_dir / "open-org" / ".sync_state.json").write_text(json.dumps({"run_id": RUN_ID_OPEN}))
    (runs_dir / "shipped-org").mkdir(parents=True)
    (runs_dir / "shipped-org" / ".sync_state.json").write_text(json.dumps({"run_id": RUN_ID_SHIPPED}))
    (runs_dir / "no-state-org").mkdir(parents=True)  # no .sync_state.json at all

    heartbeat = tmp_path / "heartbeat" / "STATE.md"
    heartbeat.parent.mkdir(parents=True)
    heartbeat.write_text("## Active Sessions\n\n(no sessions in this fixture)\n")

    ledger = tmp_path / "ledger"
    ledger.mkdir()
    (ledger / "2026-07-24.jsonl").write_text('{"event": "tool", "session_id": "x"}\n')

    monkeypatch.setattr(agents_sync_job, "RUNS_DIR", runs_dir)
    monkeypatch.setattr(agents_sync_job, "HEARTBEAT_PATH", heartbeat)
    monkeypatch.setattr(agents_sync_job, "LEDGER_DIR", ledger)
    return tmp_path


def _fake_select_status(status_by_id):
    def _select(table, params=None, url=None, service_role_key=None):
        assert table == "factory_runs"
        run_id = params["id"].split(".", 1)[1]
        if run_id not in status_by_id:
            return []
        return [{"status": status_by_id[run_id]}]

    return _select


def test_only_open_runs_are_synced(fake_repo):
    """A `running` run gets pushed; a `shipped` run is skipped entirely — the poll
    must never resurrect a finished run's activity feed.
    """
    with patch.object(
        agents_sync_job,
        "select",
        side_effect=_fake_select_status({RUN_ID_OPEN: "running", RUN_ID_SHIPPED: "shipped"}),
    ), patch.object(agents_sync_job, "push_agents", return_value=True) as mock_push:
        summary = agents_sync_job.sync_all_open_runs()

    # `sessions_synced: 0` is A4's machine-wide pass reporting honestly: this
    # fixture's heartbeat has no sessions at all, so there was nothing to push. It is
    # distinct from `None`, which is what a failed push returns.
    assert summary == {"runs_checked": 3, "runs_open": 1, "runs_synced": 1, "sessions_synced": 0}
    assert mock_push.call_count == 1
    (call_kwargs,) = [c.kwargs for c in mock_push.call_args_list]
    assert call_kwargs["run_id"] == RUN_ID_OPEN
    assert call_kwargs["slug"] == "open-org"


def test_blocked_on_gate_is_also_open(fake_repo):
    """`blocked_on_gate` is the other live status (mid-run, waiting on a human
    approval) — the Fleet tab should still reflect activity while a gate is pending.
    """
    with patch.object(
        agents_sync_job, "select", side_effect=_fake_select_status({RUN_ID_OPEN: "blocked_on_gate"})
    ), patch.object(agents_sync_job, "push_agents", return_value=True) as mock_push:
        agents_sync_job.sync_all_open_runs()
    assert mock_push.call_count == 1


def test_directory_with_no_sync_state_is_skipped_without_error(fake_repo):
    """`no-state-org` has a runs/ directory but never got past `get_or_create_run_id`
    (e.g. `factory.py` errored before its first stage push) — must not crash the poll.
    """
    with patch.object(
        agents_sync_job, "select", side_effect=_fake_select_status({RUN_ID_OPEN: "running"})
    ), patch.object(agents_sync_job, "push_agents", return_value=True):
        summary = agents_sync_job.sync_all_open_runs()
    assert summary["runs_checked"] == 3  # counted, but never queried/pushed for


def test_unreachable_supabase_fails_open_and_skips_that_run(fake_repo):
    """Fail-open (PRRules.md rule 5, telemetry direction): if the status check
    itself can't reach Supabase, that run is skipped for this poll — never raised,
    never crashes the other runs in the same pass.
    """

    def _raise(*a, **k):
        raise SupabaseRestError("connection refused")

    with patch.object(agents_sync_job, "select", side_effect=_raise), patch.object(
        agents_sync_job, "push_agents"
    ) as mock_push:
        summary = agents_sync_job.sync_all_open_runs()

    assert summary == {"runs_checked": 3, "runs_open": 0, "runs_synced": 0, "sessions_synced": 0}
    mock_push.assert_not_called()


def test_no_heartbeat_file_is_a_clean_noop(tmp_path, monkeypatch):
    """A machine that's never written heartbeat/STATE.md yet (fresh checkout) must
    not crash the scheduled job — just report nothing to do.
    """
    monkeypatch.setattr(agents_sync_job, "HEARTBEAT_PATH", tmp_path / "does-not-exist.md")
    summary = agents_sync_job.sync_all_open_runs()
    assert summary == {"runs_checked": 0, "runs_open": 0, "runs_synced": 0, "sessions_synced": 0}


def test_machine_wide_pass_pushes_unclaimed_sessions_and_skips_claimed_ones(fake_repo, monkeypatch):
    """A4 — the second pass, and the seam between the two.

    `open-org`'s session is already synced WITH a run_id by the run-scoped pass, so
    the machine-wide pass must skip it or the product would store the same session
    twice. `OTHER-PROJECT` is on nothing this factory knows about, which is exactly
    the case that could not reach the product before A4.
    """
    (fake_repo / "heartbeat" / "STATE.md").write_text(
        "## Active Sessions\n\n"
        "| session | goal | allowed_paths | risk | status | seq | last_update |\n"
        "|---|---|---|---|---|---|---|\n"
        "| OPEN-ORG-WORK | curate open-org corpus | factory-automation | code | ACTIVE | 1 | x |\n"
        "| OTHER-PROJECT | unrelated repo work | scripts | code | ACTIVE | 2 | x |\n"
    )

    with patch.object(
        agents_sync_job, "select", side_effect=_fake_select_status({RUN_ID_OPEN: "running"})
    ), patch.object(agents_sync_job, "push_agents", return_value=True), patch.object(
        agents_sync_job, "push_machine_sessions", return_value=1
    ) as mock_sessions:
        summary = agents_sync_job.sync_all_open_runs()

    assert summary["sessions_synced"] == 1
    skipped = mock_sessions.call_args.kwargs["skip_titles"]
    assert skipped == {"OPEN-ORG-WORK"}
    assert "OTHER-PROJECT" not in skipped


def test_machine_wide_pass_runs_even_when_no_run_is_open(fake_repo):
    """The whole point of the surface: a session on an unrelated project appears even
    when no fine-tune is running at all. Gating this pass on an open run would make
    the new tab empty exactly when it is most useful.
    """
    (fake_repo / "heartbeat" / "STATE.md").write_text(
        "## Active Sessions\n\n"
        "| session | goal | allowed_paths | risk | status | seq | last_update |\n"
        "|---|---|---|---|---|---|---|\n"
        "| OTHER-PROJECT | unrelated repo work | scripts | code | ACTIVE | 2 | x |\n"
    )

    with patch.object(
        agents_sync_job, "select", side_effect=_fake_select_status({RUN_ID_OPEN: "shipped"})
    ), patch.object(agents_sync_job, "push_machine_sessions", return_value=1) as mock_sessions:
        summary = agents_sync_job.sync_all_open_runs()

    assert summary["runs_open"] == 0
    assert mock_sessions.call_count == 1
    assert mock_sessions.call_args.kwargs["skip_titles"] == set()


def test_no_runs_dir_returns_empty_slug_list(tmp_path, monkeypatch):
    monkeypatch.setattr(agents_sync_job, "RUNS_DIR", tmp_path / "does-not-exist")
    assert agents_sync_job._discover_run_slugs() == []


def test_cloud_fleet_pass_runs_every_poll_and_never_touches_the_summary(fake_repo, monkeypatch):
    """T1-1 — wired into the main sync loop the same way `push_machine_sessions`
    is: called on every poll regardless of whether any run is open, and (like
    the artifacts pass) never appears as a key in the returned summary dict —
    so a real cloud-fleet push failure can never make this function's return
    value look like the run-scoped sync itself failed.
    """
    calls = []
    monkeypatch.setattr(
        agents_sync_job, "push_cloud_fleet_agents", lambda **kwargs: calls.append(kwargs) or 3
    )
    with patch.object(
        agents_sync_job, "select", side_effect=_fake_select_status({RUN_ID_OPEN: "shipped"})
    ), patch.object(agents_sync_job, "push_machine_sessions", return_value=0):
        summary = agents_sync_job.sync_all_open_runs()
    assert len(calls) == 1
    assert "cloud_fleet_agents_synced" not in summary


def test_main_exits_zero_and_prints_summary(fake_repo, capsys):
    with patch.object(agents_sync_job, "select", return_value=[]):
        rc = agents_sync_job.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "agents_sync_job: checked 3 known run(s)" in out
