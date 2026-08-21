"""A4 (factory-observability-v1) — the machine-wide session projection.

The Model Factory tab could only ever see agent sessions working on an open
fine-tune, because `agents_sync_job.py` is run-scoped by design (Daniel,
2026-07-24). A4 adds a second, separate pass so a session on anything else reaches
the product at all — as a `factory_agents` row with `run_id: NULL`, on its own
surface.

What these tests are really guarding is the seam between the two passes. The
interesting failure is not "does it push" but "does a session that IS on a fine-tune
end up stored twice", which is exactly what would happen without `skip_titles`.
"""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import factory_sync  # noqa: E402
from supabase_rest import SupabaseRestError  # noqa: E402


HEARTBEAT = """# STATE

## Active Sessions

Some prose the parser must ignore.

| session | goal | allowed_paths | risk | status | seq | last_update |
|---|---|---|---|---|---|---|
| GRAND-STEEL-CORPUS | curate grand-steel corpus | factory-automation/factory | code | ACTIVE | 4 | 2026-07-28 |
| OTHER-PROJECT | refactor an unrelated repo | platform-alpha-opps-identity | code | ACTIVE | 2 | 2026-07-28 |
| DONE-SESSION | finished something | scripts | read-only | ENDED | 9 | 2026-07-28 |

## Something Else
"""


def test_machine_wide_projection_takes_every_session_not_just_one_runs():
    rows, events = factory_sync.project_agents(HEARTBEAT, None, None)
    titles = sorted(r["title"] for r in rows)
    assert titles == ["DONE-SESSION", "GRAND-STEEL-CORPUS", "OTHER-PROJECT"]
    # Every row is run-less; that is what distinguishes this projection.
    assert all(r["run_id"] is None for r in rows)
    # `factory_run_events.run_id` is NOT NULL, so a run-less session has no
    # representable timeline row. Zero events is the honest answer, not a gap.
    assert events == []


def test_run_scoped_projection_is_unchanged_and_still_correlates_by_slug():
    rows, events = factory_sync.project_agents(HEARTBEAT, "run-uuid", "grand-steel")
    assert [r["title"] for r in rows] == ["GRAND-STEEL-CORPUS"]
    assert all(r["run_id"] == "run-uuid" for r in rows)
    # And it still emits its events, unlike the machine-wide mode.
    assert [e["kind"] for e in events] == ["agent_spawned"]


def test_ended_sessions_keep_their_status_in_the_machine_wide_pass():
    rows, _ = factory_sync.project_agents(HEARTBEAT, None, None)
    by_title = {r["title"]: r for r in rows}
    assert by_title["DONE-SESSION"]["status"] == "ended"
    assert by_title["OTHER-PROJECT"]["status"] == "active"


def test_sessions_claimed_by_uses_the_same_correlation_rule_as_the_scoped_push():
    assert factory_sync.sessions_claimed_by(HEARTBEAT, ["grand-steel"]) == {"GRAND-STEEL-CORPUS"}
    assert factory_sync.sessions_claimed_by(HEARTBEAT, []) == set()
    # A slug nothing mentions claims nothing — it does not fall back to claiming all.
    assert factory_sync.sessions_claimed_by(HEARTBEAT, ["ptc-steel"]) == set()


def test_claimed_sessions_are_not_pushed_twice(monkeypatch):
    """The duplication this seam exists to prevent.

    A session on an open fine-tune is already stored WITH a run_id by `push_agents`.
    Pushing it again here inserts a second, run-less copy (the upsert lookup filters
    on `run_id is.null`, so it cannot find the scoped row to update), and "My
    Sessions" — which reads every active row — would show it twice.
    """
    pushed = []
    monkeypatch.setattr(factory_sync, "_upsert_agent_row", lambda row, **kw: pushed.append(row))

    count = factory_sync.push_machine_sessions(
        heartbeat_md=HEARTBEAT,
        skip_titles=factory_sync.sessions_claimed_by(HEARTBEAT, ["grand-steel"]),
    )

    assert count == 2
    assert sorted(r["title"] for r in pushed) == ["DONE-SESSION", "OTHER-PROJECT"]
    assert "GRAND-STEEL-CORPUS" not in [r["title"] for r in pushed]


def test_push_is_fail_open_like_every_other_telemetry_push(monkeypatch):
    def boom(row, **kw):
        raise SupabaseRestError("supabase unreachable")

    monkeypatch.setattr(factory_sync, "_upsert_agent_row", boom)

    # Returns None rather than raising: this is telemetry, and a Supabase outage must
    # not break the poll (or, transitively, anything the poll runs alongside).
    assert factory_sync.push_machine_sessions(heartbeat_md=HEARTBEAT) is None


def test_upsert_filters_a_runless_row_with_is_null_not_eq_none(monkeypatch):
    """`eq.None` would filter for the literal string "None", match nothing, and make
    every poll INSERT a duplicate instead of updating the row already there.
    """
    seen = {}

    def fake_select(table, params=None, **kw):
        seen["params"] = params
        return []

    inserted = []
    monkeypatch.setattr(factory_sync, "select", fake_select)
    monkeypatch.setattr(factory_sync, "insert", lambda t, rows, **kw: inserted.extend(rows))

    factory_sync._upsert_agent_row({"run_id": None, "title": "OTHER-PROJECT", "role": "contributor"})

    assert seen["params"]["run_id"] == "is.null"
    assert seen["params"]["title"] == "eq.OTHER-PROJECT"
    assert len(inserted) == 1


def test_upsert_updates_rather_than_duplicates_on_a_second_poll(monkeypatch):
    monkeypatch.setattr(factory_sync, "select", lambda t, params=None, **kw: [{"id": "existing-row"}])
    updates = []
    monkeypatch.setattr(factory_sync, "update", lambda t, filters, row, **kw: updates.append((filters, row)))
    monkeypatch.setattr(
        factory_sync,
        "insert",
        lambda *a, **kw: pytest.fail("a second poll must UPDATE the existing row, never insert another"),
    )

    factory_sync._upsert_agent_row({"run_id": None, "title": "OTHER-PROJECT", "role": "contributor"})

    assert updates[0][0] == {"id": "eq.existing-row"}


def test_files_touched_reuses_the_existing_redaction_allow_list(monkeypatch):
    """PRRules invariant 12 — no new redaction policy for a new surface. A path
    outside the allow-list is redacted here by exactly the same `_redact_path` the
    Fleet tab's rows already go through.
    """
    ledger = [
        '{"event": "tool", "session_id": "sess-1", "files": '
        '["factory-automation/factory/factory_sync.py", "/Users/someone/private/customer-book.csv"]}'
    ]
    rows, _ = factory_sync.project_agents(
        HEARTBEAT, None, None, ledger_lines=ledger, session_id_map={"OTHER-PROJECT": "sess-1"}
    )
    other = next(r for r in rows if r["title"] == "OTHER-PROJECT")
    assert "factory-automation/factory/factory_sync.py" in other["files_touched"]
    assert "/Users/someone/private/customer-book.csv" not in other["files_touched"]
    assert factory_sync._REDACTED_PATH in other["files_touched"]


def test_no_cost_is_invented_for_an_interactive_session():
    """Heartbeat sessions have never had a per-session cost source (DELTAS.md D16),
    and B1's ledger matching keys on a dispatched node's `--label`, which an
    interactive session does not have. Absent, not zero.
    """
    rows, _ = factory_sync.project_agents(HEARTBEAT, None, None)
    assert all("cost_usd" not in r or r["cost_usd"] is None for r in rows)
    # And no `stage`, which is what the product uses to tell a dispatched node-agent
    # apart from an interactive session (A2's `isDispatched`).
    assert all(r.get("stage") is None for r in rows)
