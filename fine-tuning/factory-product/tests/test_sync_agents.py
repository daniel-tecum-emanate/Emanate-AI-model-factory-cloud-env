"""T13 — factory_sync.project_agents/push_agents: heartbeat+ledger -> factory_agents
+ factory_run_events projection.

PRTests.md's own naming for this file: "fixture heartbeat/STATE.md + ledger rows =>
correct factory_agents row count and running->finished status transitions", "agent_finished
event headline equals the agent's real closing-response text from the fixture", "redaction:
a fixture file path embedding a customer name never appears in files_touched", "an agent
present in heartbeat but absent from the ledger projects with ledger_session_id: null, no
crash."
"""

import json
import os
import re
import subprocess
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


RUN_ID = "22222222-2222-2222-2222-222222222222"
ORG_SLUG = "grand-steel"

# A fixture heartbeat/STATE.md snippet — same table shape as the real file
# (format v2, header row + "|---|" separator + prose above), two sessions
# matching this run's org_slug (one ACTIVE, one ENDED) and one that belongs
# to an unrelated run (must be excluded).
HEARTBEAT_FIXTURE = """# Heartbeat — coordination board

## Active Sessions
_Register on boot with your scope contract._

| session | goal | allowed_paths | risk | status | seq | last_update |
|---------|------|---------------|------|--------|-----|-------------|
| GS-BUILD | grand-steel corpus build (S4) | factory-automation/factory/runs/grand-steel/**, clients/grand-steel-corp/dossiers/** | code | ACTIVE | 3 | 2026-07-23 10:00 PT |
| GS-VERIFY | grand-steel verify pass, S5 | factory-automation/factory/runs/grand-steel/** | read-only | ENDED | 5 | 2026-07-23 09:00 PT |
| PTC-TRAIN | ptc-steel training run, S7 | factory-automation/factory/runs/ptc-steel/** | migration | ACTIVE | 2 | 2026-07-23 08:00 PT |

## File Locks
_Claim a file before editing it._

| file (repo-relative) | held_by | task | status |
|----------------------|---------|------|--------|
"""

# A fixture ledger snippet: one `tool` event for GS-VERIFY's mapped ledger
# session (touches a safe internal path and a path embedding a fake customer
# name), one unrelated event, and no tool events at all for GS-BUILD (so
# GS-BUILD is the "present in heartbeat, absent from the ledger" case even
# though it doesn't have a session_id_map entry either).
LEDGER_FIXTURE = [
    json.dumps(
        {
            "ts": "2026-07-23T09:30:00-07:00",
            "event": "tool",
            "session_id": "ledger-verify-uuid",
            "tool": "Read",
            "files": [
                "factory-automation/factory/runs/grand-steel/05-verify-report.json",
                "clients/grand-steel-corp/dossiers/acme-account-export.pdf",
            ],
            "ok": True,
        }
    ),
    json.dumps(
        {
            "ts": "2026-07-23T09:31:00-07:00",
            "event": "tool",
            "session_id": "ledger-verify-uuid",
            "tool": "Bash",
            "files": [],
            "ok": True,
        }
    ),
    json.dumps({"ts": "2026-07-23T09:32:00-07:00", "event": "session_end", "session_id": "ledger-verify-uuid"}),
    # noise: a different session entirely, must never leak into GS-VERIFY's aggregates
    json.dumps(
        {
            "ts": "2026-07-23T09:33:00-07:00",
            "event": "tool",
            "session_id": "some-other-session",
            "files": ["heartbeat/STATE.md"],
        }
    ),
]

SESSION_ID_MAP = {"GS-VERIFY": "ledger-verify-uuid"}
FINAL_RESPONSES = {"GS-VERIFY": "Verify pass: 412/412 checks green, 0 flagged, verdict PASS."}


def test_row_count_and_org_filter_only_includes_sessions_matching_this_runs_org_slug():
    agent_rows, _events = factory_sync.project_agents(HEARTBEAT_FIXTURE, RUN_ID, ORG_SLUG, ledger_lines=LEDGER_FIXTURE)
    titles = {row["title"] for row in agent_rows}
    assert titles == {"GS-BUILD", "GS-VERIFY"}  # PTC-TRAIN belongs to a different org, excluded
    assert len(agent_rows) == 2


def test_active_and_ended_heartbeat_status_map_to_active_and_ended_factory_agents_status():
    agent_rows, _events = factory_sync.project_agents(HEARTBEAT_FIXTURE, RUN_ID, ORG_SLUG, ledger_lines=LEDGER_FIXTURE)
    by_title = {row["title"]: row for row in agent_rows}
    assert by_title["GS-BUILD"]["status"] == "active"
    assert by_title["GS-VERIFY"]["status"] == "ended"
    # an agent_finished event exists only for the ended session
    _agent_rows, event_rows = factory_sync.project_agents(
        HEARTBEAT_FIXTURE, RUN_ID, ORG_SLUG, ledger_lines=LEDGER_FIXTURE
    )
    finished_titles = {e["ref"]["agent_title"] for e in event_rows if e["kind"] == "agent_finished"}
    assert finished_titles == {"GS-VERIFY"}
    spawned_titles = {e["ref"]["agent_title"] for e in event_rows if e["kind"] == "agent_spawned"}
    assert spawned_titles == {"GS-BUILD", "GS-VERIFY"}


def test_agent_finished_headline_equals_the_real_closing_response_text_from_the_fixture():
    _agent_rows, event_rows = factory_sync.project_agents(
        HEARTBEAT_FIXTURE,
        RUN_ID,
        ORG_SLUG,
        ledger_lines=LEDGER_FIXTURE,
        session_id_map=SESSION_ID_MAP,
        final_responses=FINAL_RESPONSES,
    )
    finished = [e for e in event_rows if e["kind"] == "agent_finished"][0]
    assert finished["headline"] == FINAL_RESPONSES["GS-VERIFY"]


def test_agent_finished_headline_falls_back_when_no_final_response_supplied():
    _agent_rows, event_rows = factory_sync.project_agents(HEARTBEAT_FIXTURE, RUN_ID, ORG_SLUG, ledger_lines=LEDGER_FIXTURE)
    finished = [e for e in event_rows if e["kind"] == "agent_finished"][0]
    assert finished["headline"] == "GS-VERIFY finished"


def test_customer_name_path_never_survives_into_files_touched():
    agent_rows, _events = factory_sync.project_agents(
        HEARTBEAT_FIXTURE, RUN_ID, ORG_SLUG, ledger_lines=LEDGER_FIXTURE, session_id_map=SESSION_ID_MAP
    )
    by_title = {row["title"]: row for row in agent_rows}
    files = by_title["GS-VERIFY"]["files_touched"]
    assert "clients/grand-steel-corp/dossiers/acme-account-export.pdf" not in "".join(files)
    assert "acme" not in "".join(files).lower()
    # the safe internal path DOES survive unredacted
    assert "factory-automation/factory/runs/grand-steel/05-verify-report.json" in files
    # commands_run counted 2 real tool events for this session
    assert by_title["GS-VERIFY"]["commands_run"] == 2


def test_agent_present_in_heartbeat_but_absent_from_ledger_projects_null_session_id_no_crash():
    # GS-BUILD has no session_id_map entry at all — the untracked-agent case.
    agent_rows, _events = factory_sync.project_agents(HEARTBEAT_FIXTURE, RUN_ID, ORG_SLUG, ledger_lines=LEDGER_FIXTURE)
    by_title = {row["title"]: row for row in agent_rows}
    build_row = by_title["GS-BUILD"]
    assert build_row["ledger_session_id"] is None
    assert build_row["files_touched"] == []
    assert build_row["commands_run"] == 0


def test_role_derived_from_risk_when_no_richer_signal_exists():
    agent_rows, _events = factory_sync.project_agents(HEARTBEAT_FIXTURE, RUN_ID, ORG_SLUG, ledger_lines=LEDGER_FIXTURE)
    by_title = {row["title"]: row for row in agent_rows}
    assert by_title["GS-BUILD"]["role"] == "builder"  # risk: code
    assert by_title["GS-VERIFY"]["role"] == "reader"  # risk: read-only


def test_agent_row_payload_keys_are_all_real_factory_agents_columns(migration_text):
    columns = _columns_for("factory_agents", migration_text)
    agent_rows, _events = factory_sync.project_agents(
        HEARTBEAT_FIXTURE, RUN_ID, ORG_SLUG, ledger_lines=LEDGER_FIXTURE, final_responses=FINAL_RESPONSES,
        session_id_map=SESSION_ID_MAP,
    )
    for row in agent_rows:
        assert set(row.keys()) <= columns, f"unknown factory_agents column(s): {set(row.keys()) - columns}"


def test_event_row_payload_keys_are_all_real_factory_run_events_columns(migration_text):
    columns = _columns_for("factory_run_events", migration_text)
    _agent_rows, event_rows = factory_sync.project_agents(HEARTBEAT_FIXTURE, RUN_ID, ORG_SLUG, ledger_lines=LEDGER_FIXTURE)
    for row in event_rows:
        assert set(row.keys()) <= columns, f"unknown factory_run_events column(s): {set(row.keys()) - columns}"


def test_push_agents_forced_http_failure_does_not_raise():
    with patch.object(factory_sync, "select", side_effect=SupabaseRestError("boom: connection refused")):
        result = factory_sync.push_agents({}, ORG_SLUG, RUN_ID, HEARTBEAT_FIXTURE, ledger_lines=LEDGER_FIXTURE)
    assert result is False  # fail-open — informational only, no exception escaped


def test_push_agents_success_inserts_new_agent_and_appends_events():
    calls = []
    with patch.object(factory_sync, "select", return_value=[]), patch.object(
        factory_sync, "insert", side_effect=lambda table, rows, **kw: calls.append(table)
    ), patch.object(
        factory_sync, "upsert", side_effect=lambda table, rows, **kw: calls.append(table)
    ):
        result = factory_sync.push_agents({}, ORG_SLUG, RUN_ID, HEARTBEAT_FIXTURE, ledger_lines=LEDGER_FIXTURE)
    assert result is True
    # 2 agent inserts (GS-BUILD, GS-VERIFY, no existing rows) + 1 batched events
    # UPSERT (deterministic ids since 2026-07-31 — see the idempotency test below)
    assert calls.count("factory_agents") == 2
    assert calls.count("factory_run_events") == 1


def test_agent_event_rows_carry_deterministic_ids_and_repeat_polls_reassert_not_duplicate():
    """F-03-1 (stress-test 2026-07-31): these events used to carry no `id` and
    were INSERTed by every agents_sync_job poll — one active session accumulated
    one duplicate `agent_spawned` row per poll cycle. Deterministic uuid5 ids +
    an `on_conflict=id` upsert make a second poll re-assert the same rows.
    """
    _agents1, events1 = factory_sync.project_agents(HEARTBEAT_FIXTURE, RUN_ID, ORG_SLUG, ledger_lines=LEDGER_FIXTURE)
    _agents2, events2 = factory_sync.project_agents(HEARTBEAT_FIXTURE, RUN_ID, ORG_SLUG, ledger_lines=LEDGER_FIXTURE)
    ids1 = [e["id"] for e in events1]
    ids2 = [e["id"] for e in events2]
    assert ids1 == ids2  # planted duplication: a second poll produces identical ids
    assert len(set(ids1)) == len(ids1)  # and the ids are distinct within one poll

    upserts = []
    with patch.object(factory_sync, "select", return_value=[]), patch.object(factory_sync, "insert"), patch.object(
        factory_sync, "upsert", side_effect=lambda table, rows, **kw: upserts.append((table, kw.get("on_conflict")))
    ):
        factory_sync.push_agents({}, ORG_SLUG, RUN_ID, HEARTBEAT_FIXTURE, ledger_lines=LEDGER_FIXTURE)
    assert upserts == [("factory_run_events", "id")]


def test_push_agents_updates_existing_agent_row_instead_of_inserting_a_duplicate():
    existing_row = {"id": "existing-agent-uuid"}
    calls = []
    with patch.object(factory_sync, "select", return_value=[existing_row]), patch.object(
        factory_sync, "update", side_effect=lambda table, filters, values, **kw: calls.append((table, filters))
    ), patch.object(factory_sync, "insert", side_effect=lambda table, rows, **kw: calls.append((table, "insert"))), \
            patch.object(factory_sync, "upsert"):
        factory_sync.push_agents({}, ORG_SLUG, RUN_ID, HEARTBEAT_FIXTURE, ledger_lines=LEDGER_FIXTURE)
    agent_calls = [c for c in calls if c[0] == "factory_agents"]
    assert len(agent_calls) == 2  # one update per matched session, never a duplicate insert
    assert all(filters == {"id": "eq.existing-agent-uuid"} for _table, filters in agent_calls)


# =============================================================================
# T1-1 (model-factory-finetune-launcher-v1) — cloud-fleet heartbeat bridge
# =============================================================================
# A hand-built fixture matching cloud-heartbeat.sh's REAL schema verbatim
# (schema, kind, session_id, lane, status, phase, note, progress, blocker
# {kind,detail,needs,since}, started_at/updated_at/ended_at, beats, last_verb,
# history, host {hostname,os,user}, git {branch,head}, emitter) — copied field
# names from the script's own Python state-file writer, not guessed.

BLOCKED_FLEET_FIXTURE = {
    "schema": 1,
    "kind": "cloud-heartbeat",
    "session_id": "cloud-sess-abc123",
    "lane": "finetune-launcher-cloud-test",
    "status": "BLOCKED",
    "phase": "waiting on Fireworks API key",
    "note": "paused mid-launch, needs a credential",
    "progress": {"done": 2, "total": 7},
    "blocker": {
        "kind": "needs-credential",
        "detail": "no FIREWORKS_API_KEY in this cloud session's environment",
        "needs": "a scoped, short-lived Fireworks key from Daniel",
        "since": "2026-08-18T20:00:00Z",
    },
    "failure": None,
    "started_at": "2026-08-18T19:00:00Z",
    "updated_at": "2026-08-18T20:05:00Z",
    "ended_at": None,
    "beats": 4,
    "last_verb": "blocked",
    "history": [],
    "host": {"hostname": "cloud-vm-7", "os": "Linux 6.8 x86_64", "user": "claude"},
    "git": {"branch": "feat/model-factory-finetune-launcher-v1", "head": "a1b2c3d4"},
    "emitter": {"name": "cloud-heartbeat.sh", "version": 1},
}


def test_project_cloud_fleet_agent_maps_a_blocked_session_correctly():
    """T1-1's own accept criteria: a fixture fleet-JSON with `status: BLOCKED`
    and a `blocker` object projects to a `factory_agents` row with the blocker
    fields correctly populated, alongside the existing fields (title, status).
    """
    row = factory_sync.project_cloud_fleet_agent(BLOCKED_FLEET_FIXTURE)

    assert row["run_id"] is None  # machine-wide — see this section's header comment
    assert row["title"] == "finetune-launcher-cloud-test"  # lane
    assert row["status"] == "active"  # BLOCKED maps to active; blocker carries the detail
    assert row["environment"] == "cloud_vm"
    assert row["ledger_session_id"] == "cloud-sess-abc123"
    assert row["blocker"] == {
        "kind": "needs-credential",
        "detail": "no FIREWORKS_API_KEY in this cloud session's environment",
        "needs": "a scoped, short-lived Fireworks key from Daniel",
        "since": "2026-08-18T20:00:00Z",
    }
    assert "phase: waiting on Fireworks API key" in row["report_summary"]
    assert "2/7" in row["report_summary"]
    assert "host: cloud-vm-7" in row["report_summary"]
    # Still mid-run: no closing text yet, so no final_response.
    assert "final_response" not in row


def test_project_cloud_fleet_agent_drops_an_unknown_blocker_kind():
    """Matches cloud-heartbeat.sh's own "unknown kinds are refused" discipline
    (its `refuse "unknown blocker kind"` path) — a blocker this bridge cannot
    validate is dropped rather than stored or guessed.
    """
    fixture = json.loads(json.dumps(BLOCKED_FLEET_FIXTURE))
    fixture["blocker"]["kind"] = "made-up-kind"
    row = factory_sync.project_cloud_fleet_agent(fixture)
    assert row["blocker"] is None


def test_project_cloud_fleet_agent_done_session_carries_final_response_and_ended_status():
    fixture = json.loads(json.dumps(BLOCKED_FLEET_FIXTURE))
    fixture["status"] = "DONE"
    fixture["blocker"] = None
    fixture["note"] = "shipped the launcher UI; see PR #123"
    fixture["ended_at"] = "2026-08-18T21:00:00Z"
    row = factory_sync.project_cloud_fleet_agent(fixture)
    assert row["status"] == "ended"
    assert row["blocker"] is None
    assert row["final_response"] == "shipped the launcher UI; see PR #123"
    assert row["ended_at"] == "2026-08-18T21:00:00Z"


def test_project_cloud_fleet_agent_active_session_with_no_blocker_has_none_blocker():
    fixture = json.loads(json.dumps(BLOCKED_FLEET_FIXTURE))
    fixture["status"] = "ACTIVE"
    fixture["blocker"] = None
    row = factory_sync.project_cloud_fleet_agent(fixture)
    assert row["status"] == "active"
    assert row["blocker"] is None


def test_cloud_fleet_agent_row_payload_keys_are_all_real_factory_agents_columns(migration_text):
    """Same schema-drift guard as `project_agents`' own row above — cross-checked
    against the T1-2 migration's real column list (`environment`, `blocker`
    included), not merely against this bridge's own idea of the schema.
    """
    columns = _columns_for("factory_agents", migration_text)
    row = factory_sync.project_cloud_fleet_agent(BLOCKED_FLEET_FIXTURE)
    assert set(row.keys()) <= columns, f"unknown factory_agents column(s): {set(row.keys()) - columns}"


def test_push_cloud_fleet_agents_fetches_lists_reads_and_upserts_one_row_per_session(monkeypatch):
    """The full pass, with the git-fetch step stubbed out (per T1-1's accept
    criteria: "mock/stub the git-fetch step so the test doesn't need real
    network/refs") — asserts the fixture's session reaches `_upsert_agent_row`
    with its blocker fields intact.
    """
    monkeypatch.setattr(factory_sync, "fetch_cloud_fleet_refs", lambda **kw: True)
    monkeypatch.setattr(
        factory_sync, "list_cloud_fleet_session_ids", lambda **kw: ["cloud-sess-abc123"]
    )
    monkeypatch.setattr(
        factory_sync,
        "read_cloud_fleet_state",
        lambda session_id, **kw: BLOCKED_FLEET_FIXTURE if session_id == "cloud-sess-abc123" else None,
    )
    upserted = []
    with patch.object(
        factory_sync, "_upsert_agent_row", side_effect=lambda row, **kw: upserted.append(row)
    ):
        pushed = factory_sync.push_cloud_fleet_agents()

    assert pushed == 1
    assert len(upserted) == 1
    assert upserted[0]["blocker"]["kind"] == "needs-credential"
    assert upserted[0]["environment"] == "cloud_vm"


def test_push_cloud_fleet_agents_skips_an_unreadable_session_without_crashing(monkeypatch):
    """One session's ref being gone/corrupt must not stop the rest of the
    fleet from syncing — fail-open per session, matching every other writer
    in this module.
    """
    monkeypatch.setattr(factory_sync, "fetch_cloud_fleet_refs", lambda **kw: True)
    monkeypatch.setattr(
        factory_sync,
        "list_cloud_fleet_session_ids",
        lambda **kw: ["cloud-sess-abc123", "cloud-sess-corrupt"],
    )

    def _read(session_id, **kw):
        return BLOCKED_FLEET_FIXTURE if session_id == "cloud-sess-abc123" else None

    monkeypatch.setattr(factory_sync, "read_cloud_fleet_state", _read)
    upserted = []
    with patch.object(
        factory_sync, "_upsert_agent_row", side_effect=lambda row, **kw: upserted.append(row)
    ):
        pushed = factory_sync.push_cloud_fleet_agents()
    assert pushed == 1
    assert len(upserted) == 1


def test_push_cloud_fleet_agents_fails_open_on_a_supabase_error_for_one_session(monkeypatch):
    monkeypatch.setattr(factory_sync, "fetch_cloud_fleet_refs", lambda **kw: True)
    monkeypatch.setattr(
        factory_sync, "list_cloud_fleet_session_ids", lambda **kw: ["cloud-sess-abc123"]
    )
    monkeypatch.setattr(factory_sync, "read_cloud_fleet_state", lambda session_id, **kw: BLOCKED_FLEET_FIXTURE)
    with patch.object(
        factory_sync, "_upsert_agent_row", side_effect=SupabaseRestError("boom")
    ):
        pushed = factory_sync.push_cloud_fleet_agents()
    assert pushed == 0  # swallowed, never raised


def test_list_cloud_fleet_session_ids_parses_for_each_ref_output(tmp_path):
    """No mocking of git itself here — a real (throwaway) repo with two local
    refs under the remote-tracking namespace, asserting the real `git
    for-each-ref` parse strips the namespace prefix correctly.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-q", "-m", "root"],
        check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.invalid",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.invalid"},
    )
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(tmp_path), "update-ref", "refs/remotes/origin/cloud-fleet/sess-1", head],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "update-ref", "refs/remotes/origin/cloud-fleet/sess-2", head],
        check=True,
    )
    ids = factory_sync.list_cloud_fleet_session_ids(repo_dir=tmp_path)
    assert sorted(ids) == ["sess-1", "sess-2"]
