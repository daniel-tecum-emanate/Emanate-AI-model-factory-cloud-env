"""factory-graph-v2 coverage round 2 (2026-08-04): the parent_agent_id writer
(design research/03 §3 spawned-by derivations — folder containment HIGH,
dispatch records HIGH, goal-text regex MEDIUM, never-downgrade, PATCH
fail-open) plus the Part B capture fixes (the spawn-label transcript scan for
Cursor and Claude transcripts, dedup by session stem, ambiguity refusal).
Mocked REST throughout — no live network, temp-dir fixtures only.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import graph_artifacts  # noqa: E402
from supabase_rest import SupabaseRestError  # noqa: E402

RUN_ID = "ac6a6078-3932-4485-8b79-85eb318b85c9"
PARENT_ID = "30000000-0000-0000-0000-000000000001"
CHILD_ID = "30000000-0000-0000-0000-000000000002"
OTHER_ID = "30000000-0000-0000-0000-000000000003"

PARENT_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CHILD_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _jsonl(rows):
    return "\n".join(json.dumps(r) for r in rows) + "\n"


def _cursor_transcript_rows(prompt, answer="DONE."):
    return [
        {
            "role": "user",
            "message": {
                "content": [
                    {"type": "text", "text": f"<user_query>\n{prompt}\n</user_query>"}
                ]
            },
        },
        {
            "role": "assistant",
            "message": {"content": [{"type": "text", "text": answer}]},
        },
    ]


def _row(**over):
    row = {
        "id": CHILD_ID,
        "run_id": RUN_ID,
        "stage": None,
        "title": "CHILD-X",
        "goal": "build subagent of MF-PARENT (demo)",
        "cursor_agent_id": None,
        "ledger_session_id": None,
        "report_path": None,
        "final_response": None,
        "report_summary": None,
        "parent_agent_id": None,
    }
    row.update(over)
    return row


@pytest.fixture()
def spawn_cursor_dir(tmp_path):
    """A parent session transcript plus its subagents/ child, both with
    spawn-style first queries the label scan can resolve.
    """
    base = tmp_path / "cursor-transcripts"
    parent = base / PARENT_UUID
    parent.mkdir(parents=True)
    (parent / f"{PARENT_UUID}.jsonl").write_text(
        _jsonl(_cursor_transcript_rows("You are MF-PARENT, the orchestrating session."))
    )
    sub = parent / "subagents"
    sub.mkdir()
    (sub / f"{CHILD_UUID}.jsonl").write_text(
        _jsonl(
            _cursor_transcript_rows(
                "You are subagent CHILD-X under MF-PARENT. Do the thing.",
                answer="CHILD FINAL.",
            )
        )
    )
    return base


# ---------------------------------------------------------------------------
# Part B — spawn-label transcript scan
# ---------------------------------------------------------------------------


def _locate(row, cursor_dir=None, claude_dir=None):
    runtime = graph_artifacts.classify_runtime(row)
    return graph_artifacts.locate_sources(
        row,
        runtime,
        agent_records=[],
        cursor_transcripts_dir=cursor_dir or "/nonexistent-cursor",
        claude_projects_dir=claude_dir or "/nonexistent-claude",
    )


def test_spawn_label_scan_matches_cursor_subagent_without_any_ledger_record(
    spawn_cursor_dir,
):
    """The ledger never records Task-tool subagent spawns — the transcript's
    own spawn prompt is the join (§4.2 rule 3 applied directly).
    """
    src = _locate(_row(), cursor_dir=spawn_cursor_dir)
    assert src["transcript_kind"] == "cursor"
    assert src["match_basis"] == "spawn_label_in_transcript"
    # The subagents/ path is preferred — it carries containment evidence.
    assert src["transcript_path"].parent.name == "subagents"

    rows = {
        r["artifact_kind"]: r
        for r in graph_artifacts.build_artifact_rows(
            _row(), src, graph_artifacts.RUNTIME_INTERACTIVE
        )
    }
    assert rows["prompt"]["content"].startswith("You are subagent CHILD-X")
    assert rows["output"]["content"] == "CHILD FINAL."


def test_spawn_label_duplicate_stems_count_as_one_match(tmp_path):
    """The same session file legitimately exists both top-level and under its
    parent's subagents/ folder — one session, one match, subagents preferred.
    """
    base = tmp_path / "cursor-transcripts"
    top = base / CHILD_UUID
    top.mkdir(parents=True)
    body = _jsonl(_cursor_transcript_rows("You are CHILD-X, a research subagent."))
    (top / f"{CHILD_UUID}.jsonl").write_text(body)
    sub = base / PARENT_UUID / "subagents"
    sub.mkdir(parents=True)
    (sub / f"{CHILD_UUID}.jsonl").write_text(body)

    index = graph_artifacts.build_first_query_index(base, "/nonexistent-claude")
    matched = graph_artifacts.match_transcript_by_spawn_label("CHILD-X", index)
    assert matched is not None
    path, kind = matched
    assert kind == "cursor"
    assert path.parent.name == "subagents"


def test_spawn_label_two_distinct_sessions_refuse(tmp_path, caplog):
    base = tmp_path / "cursor-transcripts"
    for twin_uuid in (PARENT_UUID, CHILD_UUID):
        d = base / twin_uuid
        d.mkdir(parents=True)
        (d / f"{twin_uuid}.jsonl").write_text(
            _jsonl(_cursor_transcript_rows("You are CHILD-X, a research subagent."))
        )
    index = graph_artifacts.build_first_query_index(base, "/nonexistent-claude")
    assert graph_artifacts.match_transcript_by_spawn_label("CHILD-X", index) is None
    assert any("refusing to guess" in r.getMessage() for r in caplog.records)
    # Through locate_sources the row honestly stays source-less.
    src = _locate(_row(), cursor_dir=base)
    assert src["transcript_path"] is None


def test_spawn_label_needs_you_are_prefix_and_early_position(tmp_path):
    base = tmp_path / "cursor-transcripts"
    d1 = base / PARENT_UUID
    d1.mkdir(parents=True)
    # Query does not start with "You are" — a mention is not a spawn.
    (d1 / f"{PARENT_UUID}.jsonl").write_text(
        _jsonl(_cursor_transcript_rows("Please review what CHILD-X produced today."))
    )
    d2 = base / CHILD_UUID
    d2.mkdir(parents=True)
    # Starts with "You are" but the label only appears deep in the mission
    # body, past the spawn window — also not a spawn for CHILD-X.
    (d2 / f"{CHILD_UUID}.jsonl").write_text(
        _jsonl(
            _cursor_transcript_rows(
                "You are REVIEWER-1, an audit subagent. " + "x" * 300 + " CHILD-X"
            )
        )
    )
    index = graph_artifacts.build_first_query_index(base, "/nonexistent-claude")
    assert graph_artifacts.match_transcript_by_spawn_label("CHILD-X", index) is None
    # Token boundaries: "CHILD-X2" is not "CHILD-X".
    d3 = base / "cccccccc-cccc-cccc-cccc-cccccccccccc"
    d3.mkdir(parents=True)
    (d3 / "cccccccc-cccc-cccc-cccc-cccccccccccc.jsonl").write_text(
        _jsonl(_cursor_transcript_rows("You are CHILD-X2, a different subagent."))
    )
    index = graph_artifacts.build_first_query_index(base, "/nonexistent-claude")
    assert graph_artifacts.match_transcript_by_spawn_label("CHILD-X", index) is None


def test_spawn_label_scan_reaches_claude_transcripts_across_slugs(tmp_path):
    """Claude sessions live under a slug encoding their cwd — the scan globs
    all slugs. Machine-wide rows still get the §5.3 CoT policy exclusion.
    """
    slug = tmp_path / "claude-projects" / "-"  # the root-cwd slug seen live
    slug.mkdir(parents=True)
    session = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    (slug / f"{session}.jsonl").write_text(
        _jsonl(
            [
                {
                    "type": "user",
                    "message": {"content": "You are CHILD-X, a machine-wide session."},
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "PRIVATE_REASONING"},
                            {"type": "text", "text": "CLAUDE FINAL."},
                        ]
                    },
                },
            ]
        )
    )
    row = _row(run_id=None)
    src = _locate(row, claude_dir=tmp_path / "claude-projects")
    assert src["transcript_kind"] == "claude"
    assert src["match_basis"] == "spawn_label_in_transcript"
    rows = {
        r["artifact_kind"]: r
        for r in graph_artifacts.build_artifact_rows(
            row, src, graph_artifacts.RUNTIME_MACHINE_WIDE
        )
    }
    assert rows["prompt"]["content"] == "You are CHILD-X, a machine-wide session."
    assert rows["output"]["content"] == "CLAUDE FINAL."
    assert rows["thinking"]["unavailable_reason"] == "policy_excluded_machine_wide"
    assert "PRIVATE_REASONING" not in json.dumps(rows)


def test_index_cache_builds_the_index_exactly_once(spawn_cursor_dir):
    cache = {}
    kwargs = dict(
        agent_records=[],
        cursor_transcripts_dir=spawn_cursor_dir,
        claude_projects_dir="/nonexistent-claude",
        index_cache=cache,
    )
    with patch.object(
        graph_artifacts,
        "build_first_query_index",
        wraps=graph_artifacts.build_first_query_index,
    ) as mock_build:
        graph_artifacts.locate_sources(
            _row(), graph_artifacts.RUNTIME_INTERACTIVE, **kwargs
        )
        graph_artifacts.locate_sources(
            _row(id=OTHER_ID, title="NEVER-SEEN"),
            graph_artifacts.RUNTIME_INTERACTIVE,
            **kwargs,
        )
    assert mock_build.call_count == 1


# ---------------------------------------------------------------------------
# Part A — derivations
# ---------------------------------------------------------------------------


def test_folder_containment_derives_high_edge(spawn_cursor_dir):
    """Both ends matched to transcripts; the child's file lives under the
    parent session's subagents/ folder → HIGH edge, no goal text needed.
    """
    matches = {
        PARENT_ID: spawn_cursor_dir / PARENT_UUID / f"{PARENT_UUID}.jsonl",
        CHILD_ID: spawn_cursor_dir / PARENT_UUID / "subagents" / f"{CHILD_UUID}.jsonl",
    }
    rows = [_row(id=PARENT_ID, title="MF-PARENT", goal="orchestrate"), _row()]
    edges = graph_artifacts.derive_parent_edges(
        rows, transcript_matches=matches, runs_dir="/nonexistent-runs"
    )
    assert edges[CHILD_ID]["parent_id"] == PARENT_ID
    assert edges[CHILD_ID]["confidence"] == "high"
    assert edges[CHILD_ID]["basis"] == "transcript_folder_containment"
    assert PARENT_ID not in edges  # the parent has no parent


def test_containment_outranks_goal_text(spawn_cursor_dir):
    """The child's goal names a DIFFERENT (wrong) label; folder containment is
    the higher-confidence derivation and wins.
    """
    matches = {
        PARENT_ID: spawn_cursor_dir / PARENT_UUID / f"{PARENT_UUID}.jsonl",
        CHILD_ID: spawn_cursor_dir / PARENT_UUID / "subagents" / f"{CHILD_UUID}.jsonl",
    }
    rows = [
        _row(id=PARENT_ID, title="MF-PARENT", goal="orchestrate"),
        _row(id=OTHER_ID, title="MF-OTHER", goal="unrelated"),
        _row(goal="build subagent of MF-OTHER (demo)"),
    ]
    edges = graph_artifacts.derive_parent_edges(
        rows, transcript_matches=matches, runs_dir="/nonexistent-runs"
    )
    assert edges[CHILD_ID]["parent_id"] == PARENT_ID
    assert edges[CHILD_ID]["confidence"] == "high"


def test_goal_text_positive_garbage_and_ambiguous():
    rows = [
        _row(id=PARENT_ID, title="MF-PARENT", goal="orchestrate"),
        _row(),  # "build subagent of MF-PARENT (demo)" → MEDIUM edge
        _row(id=OTHER_ID, title="NO-PARSE", goal="just some goal text"),
    ]
    edges = graph_artifacts.derive_parent_edges(rows, runs_dir="/nonexistent-runs")
    assert edges == {
        CHILD_ID: {
            "parent_id": PARENT_ID,
            "confidence": "medium",
            "basis": "goal_text:MF-PARENT",
        }
    }

    # Garbage: lowercase label, or a truncated convention, never parses.
    for goal in ("subagent of nobody-lowercase", "sub agent MF-PARENT", ""):
        assert graph_artifacts.derive_parent_edges(
            [_row(goal=goal), _row(id=PARENT_ID, title="MF-PARENT", goal="x")],
            runs_dir="/nonexistent-runs",
        ) == {}

    # Ambiguous: two rows carry the named title → no write.
    dup = [
        _row(id=PARENT_ID, title="MF-PARENT", goal="x"),
        _row(id=OTHER_ID, title="MF-PARENT", goal="y"),
        _row(),
    ]
    assert graph_artifacts.derive_parent_edges(dup, runs_dir="/nonexistent-runs") == {}


def test_dispatch_record_edge_only_when_dispatcher_is_named(tmp_path):
    runs = tmp_path / "runs"
    d = runs / "demo-org" / "cursor_dispatch"
    d.mkdir(parents=True)
    (d / "S4-node.json").write_text(
        json.dumps(
            {
                "node_id": "S4:node",
                "cursor_agent_id": "bc-child",
                "dispatched_by": "MF-PARENT",
            }
        )
    )
    # Today's real records: no dispatcher identity at all → no edge.
    (d / "S4-anon.json").write_text(
        json.dumps({"node_id": "S4:anon", "cursor_agent_id": "bc-anon"})
    )
    rows = [
        _row(id=PARENT_ID, title="MF-PARENT", goal="orchestrate"),
        _row(id=CHILD_ID, title="node", goal="", cursor_agent_id="bc-child"),
        _row(id=OTHER_ID, title="anon", goal="", cursor_agent_id="bc-anon"),
    ]
    edges = graph_artifacts.derive_parent_edges(rows, runs_dir=runs)
    assert edges[CHILD_ID] == {
        "parent_id": PARENT_ID,
        "confidence": "high",
        "basis": "dispatch_record",
    }
    assert OTHER_ID not in edges


# ---------------------------------------------------------------------------
# Part A — push semantics: never-downgrade, fail-open PATCH
# ---------------------------------------------------------------------------


def test_never_overwrites_a_differing_non_null_parent(caplog):
    rows = [
        _row(id=PARENT_ID, title="MF-PARENT", goal="x"),
        _row(id=OTHER_ID, title="MF-OTHER", goal="y"),
        _row(parent_agent_id=OTHER_ID),  # already set — to someone else
    ]
    edges = graph_artifacts.derive_parent_edges(rows, runs_dir="/nonexistent-runs")
    assert edges[CHILD_ID]["parent_id"] == PARENT_ID
    with patch.object(graph_artifacts, "update") as mock_update:
        summary = graph_artifacts.push_parent_edges(edges, rows)
    mock_update.assert_not_called()
    assert summary["conflict_skipped"] == 1
    assert summary["medium"] == 0
    assert any("never-downgrade" in r.getMessage() for r in caplog.records)


def test_same_value_is_a_noop_not_a_patch():
    rows = [
        _row(id=PARENT_ID, title="MF-PARENT", goal="x"),
        _row(parent_agent_id=PARENT_ID),
    ]
    edges = graph_artifacts.derive_parent_edges(rows, runs_dir="/nonexistent-runs")
    with patch.object(graph_artifacts, "update") as mock_update:
        summary = graph_artifacts.push_parent_edges(edges, rows)
    mock_update.assert_not_called()
    assert summary["unchanged"] == 1


def test_patch_writes_the_edge_and_dry_run_does_not():
    rows = [_row(id=PARENT_ID, title="MF-PARENT", goal="x"), _row()]
    edges = graph_artifacts.derive_parent_edges(rows, runs_dir="/nonexistent-runs")
    with patch.object(graph_artifacts, "update") as mock_update:
        summary = graph_artifacts.push_parent_edges(edges, rows)
    mock_update.assert_called_once_with(
        "factory_agents",
        {"id": f"eq.{CHILD_ID}"},
        {"parent_agent_id": PARENT_ID},
        url=None,
        service_role_key=None,
    )
    assert summary["medium"] == 1
    with patch.object(graph_artifacts, "update") as mock_update:
        dry = graph_artifacts.push_parent_edges(edges, rows, dry_run=True)
    mock_update.assert_not_called()
    assert dry["medium"] == 1  # derived and reported, just not written


def test_patch_failure_fails_open_and_stops_the_pass(caplog):
    rows = [
        _row(id=PARENT_ID, title="MF-PARENT", goal="x"),
        _row(),
        _row(id=OTHER_ID, goal="build subagent of MF-PARENT (two)", title="CHILD-2"),
    ]
    edges = graph_artifacts.derive_parent_edges(rows, runs_dir="/nonexistent-runs")
    assert len(edges) == 2

    def _boom(*a, **k):
        raise SupabaseRestError(
            'update factory_agents failed: 400 {"code":"42703",'
            '"message":"column factory_agents.parent_agent_id does not exist"}'
        )

    with patch.object(graph_artifacts, "update", side_effect=_boom) as mock_update:
        summary = graph_artifacts.push_parent_edges(edges, rows)  # must not raise
    assert mock_update.call_count == 1  # one warning, then stop — no 50x spam
    assert summary["patch_failed"] == 1
    assert summary["medium"] == 0  # the pass stopped; nothing counted as written
    assert any("fail-open" in r.getMessage() for r in caplog.records)


def test_sync_parent_edges_fails_open_when_select_fails(caplog):
    def _boom(*a, **k):
        raise SupabaseRestError("connection refused")

    with patch.object(graph_artifacts, "select", side_effect=_boom):
        assert graph_artifacts.sync_parent_edges() is None
    assert any("parent-edge" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Wiring — the edges pass rides the artifacts pass
# ---------------------------------------------------------------------------


def test_sync_agent_artifacts_runs_the_edges_pass(spawn_cursor_dir):
    parent = _row(id=PARENT_ID, title="MF-PARENT", goal="orchestrate")
    child = _row()

    def fake_select(table, params=None, url=None, service_role_key=None):
        if table == "factory_agents":
            return [parent, child]
        return []  # empty artifacts table

    with patch.object(graph_artifacts, "select", side_effect=fake_select), patch.object(
        graph_artifacts, "upsert"
    ), patch.object(graph_artifacts, "update") as mock_update:
        processed = graph_artifacts.sync_agent_artifacts(
            agent_records=[],
            cursor_transcripts_dir=spawn_cursor_dir,
            claude_projects_dir="/nonexistent-claude",
        )
    assert processed == 2
    mock_update.assert_called_once_with(
        "factory_agents",
        {"id": f"eq.{CHILD_ID}"},
        {"parent_agent_id": PARENT_ID},
        url=None,
        service_role_key=None,
    )
