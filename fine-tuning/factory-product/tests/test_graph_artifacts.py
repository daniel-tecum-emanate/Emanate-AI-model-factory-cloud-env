"""factory-graph-v2 artifact pipeline: runtime classification, extraction per
the research/03 §1a matrix, redaction/withholding, caps, uuid5 idempotency,
fail-open on the not-yet-migrated table, the §5.3 machine-wide CoT policy
exclusion, and the backfill's dry-run default. Mocked REST throughout — no
live network, no real transcript/ledger reads (temp-dir fixtures only).
"""

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import agents_sync_job  # noqa: E402
import artifacts_backfill  # noqa: E402
import graph_artifacts  # noqa: E402
from backfill_ptc_history import NAMESPACE  # noqa: E402
from supabase_rest import SupabaseRestError  # noqa: E402

sys.path.insert(0, str(FACTORY_DIR.parent.parent / "scripts" / "lib"))
import redact as redact_module  # noqa: E402

RUN_ID = "ac6a6078-3932-4485-8b79-85eb318b85c9"
CURSOR_UUID = "11111111-1111-1111-1111-111111111111"
CLAUDE_SESSION = "22222222-2222-2222-2222-222222222222"
AGENT_ID = "10000000-0000-0000-0000-000000000001"

CURSOR_PROMPT = (
    "You are LABEL-CURSOR, a research subagent of MF-PARENT. "
    "Read PRs/demo/PLAN.md and report."
)
CLAUDE_PROMPT = (
    "You are LABEL-CLAUDE, an execution subagent of MF-PARENT. "
    "Do the machine-wide thing."
)


def _jsonl(rows):
    return "\n".join(json.dumps(r) for r in rows) + "\n"


@pytest.fixture()
def cursor_dir(tmp_path):
    """A Cursor agent-transcript tree: <uuid>/<uuid>.jsonl, blocks per the
    real format (text + tool_use only; NO thinking, NO tool_result — §1b).
    """
    session = tmp_path / "cursor-transcripts" / CURSOR_UUID
    session.mkdir(parents=True)
    (session / f"{CURSOR_UUID}.jsonl").write_text(
        _jsonl(
            [
                {
                    "role": "user",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "<timestamp>Mon, Aug 3</timestamp>\n"
                                    f"<user_query>\n{CURSOR_PROMPT}\n</user_query>"
                                ),
                            }
                        ]
                    },
                },
                {
                    "role": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Read",
                                "input": {
                                    "path": "/Users/danieltecum/emanate-tecum-workflow/PRs/demo/PLAN.md"
                                },
                            },
                            {"type": "text", "text": "Working on it."},
                        ]
                    },
                },
                {"type": "turn_ended"},
                {
                    "role": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "FINAL CURSOR ANSWER."}]
                    },
                },
            ]
        )
    )
    return tmp_path / "cursor-transcripts"


@pytest.fixture()
def claude_dir(tmp_path):
    """A Claude Code projects tree: <slug>/<session_id>.jsonl with all four
    block kinds (text, thinking, tool_use, tool_result — §1b).
    """
    slug = tmp_path / "claude-projects" / "-Users-x-emanate-tecum-workflow"
    slug.mkdir(parents=True)
    (slug / f"{CLAUDE_SESSION}.jsonl").write_text(
        _jsonl(
            [
                {"type": "user", "message": {"content": CLAUDE_PROMPT}},
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "PRIVATE_REASONING_ONE"},
                            {
                                "type": "tool_use",
                                "id": "tu_1",
                                "name": "Bash",
                                "input": {"command": "ls"},
                            },
                        ]
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "tu_1",
                                "content": [{"type": "text", "text": "file-a\nfile-b"}],
                            }
                        ]
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "PRIVATE_REASONING_TWO"},
                            {"type": "text", "text": "FINAL CLAUDE ANSWER."},
                        ]
                    },
                },
            ]
        )
    )
    return tmp_path / "claude-projects"


@pytest.fixture()
def agent_records():
    return [
        {
            "ts": "2026-08-04T10:00:00-07:00",
            "source": "cursor",
            "generation_uuid": "gen-cursor-1",
            "prompt": CURSOR_PROMPT,
        },
        {
            "ts": "2026-08-04T10:05:00-07:00",
            "source": "claude-code",
            "session_id": CLAUDE_SESSION,
            "prompt": CLAUDE_PROMPT,
        },
    ]


def _interactive_row(**over):
    row = {
        "id": AGENT_ID,
        "run_id": RUN_ID,
        "stage": None,
        "title": "LABEL-CURSOR",
        "goal": "research subagent of MF-PARENT",
        "cursor_agent_id": None,
        "ledger_session_id": None,
        "report_path": None,
        "final_response": None,
        "report_summary": None,
    }
    row.update(over)
    return row


def _build(row, cursor_dir=None, claude_dir=None, records=None):
    runtime = graph_artifacts.classify_runtime(row)
    src = graph_artifacts.locate_sources(
        row,
        runtime,
        agent_records=records or [],
        cursor_transcripts_dir=cursor_dir or "/nonexistent-cursor",
        claude_projects_dir=claude_dir or "/nonexistent-claude",
    )
    return {
        r["artifact_kind"]: r
        for r in graph_artifacts.build_artifact_rows(row, src, runtime)
    }


# ---------------------------------------------------------------------------
# Runtime classification — the four writer-path discriminators
# ---------------------------------------------------------------------------


def test_classify_runtime_four_origins():
    assert (
        graph_artifacts.classify_runtime(_interactive_row(cursor_agent_id="bc-1"))
        == graph_artifacts.RUNTIME_CURSOR_SDK
    )
    assert (
        graph_artifacts.classify_runtime(_interactive_row(stage="S4"))
        == graph_artifacts.RUNTIME_TIER2
    )
    assert (
        graph_artifacts.classify_runtime(_interactive_row(run_id=None))
        == graph_artifacts.RUNTIME_MACHINE_WIDE
    )
    assert (
        graph_artifacts.classify_runtime(_interactive_row())
        == graph_artifacts.RUNTIME_INTERACTIVE
    )


# ---------------------------------------------------------------------------
# Extraction per the §1a matrix
# ---------------------------------------------------------------------------


def test_cursor_interactive_prompt_toolcalls_output_and_no_thinking(
    cursor_dir, agent_records
):
    rows = _build(_interactive_row(), cursor_dir=cursor_dir, records=agent_records)

    assert rows["prompt"]["content"].startswith("You are LABEL-CURSOR")
    assert rows["prompt"]["source"] == "cursor_transcript"

    calls = json.loads(rows["tool_calls"]["content"])
    assert [c["tool"] for c in calls] == ["Read"]
    # Cursor results are honestly null — tool_result blocks are not persisted.
    assert calls[0]["result"] is None
    assert rows["tool_calls"]["meta"]["tool_call_count"] == 1

    # No thinking blocks exist in the Cursor format, ever.
    assert rows["thinking"]["content"] is None
    assert rows["thinking"]["unavailable_reason"] == "not_captured_for_runtime"

    assert rows["output"]["content"] == "FINAL CURSOR ANSWER."
    assert rows["output"]["source"] == "cursor_transcript"


def test_claude_transcript_yields_all_four_kinds_with_paired_results(
    claude_dir, agent_records
):
    row = _interactive_row(title="LABEL-CLAUDE", ledger_session_id=CLAUDE_SESSION)
    rows = _build(row, claude_dir=claude_dir, records=agent_records)

    assert rows["prompt"]["content"] == CLAUDE_PROMPT
    calls = json.loads(rows["tool_calls"]["content"])
    assert calls[0]["tool"] == "Bash"
    assert calls[0]["result"] == "file-a\nfile-b"  # paired by tool_use id
    assert "PRIVATE_REASONING_ONE" in rows["thinking"]["content"]
    assert "PRIVATE_REASONING_TWO" in rows["thinking"]["content"]
    assert rows["thinking"]["meta"]["thinking_block_count"] == 2
    assert rows["output"]["content"] == "FINAL CLAUDE ANSWER."
    assert all(rows[k]["source"] == "claude_transcript" for k in rows)


def test_label_in_prompt_join_reaches_claude_transcript_without_session_id(
    claude_dir, agent_records
):
    """§4.2 method 3: no ledger_session_id on the row — the label-in-prompt
    ledger record supplies it.
    """
    row = _interactive_row(title="LABEL-CLAUDE", run_id=RUN_ID)
    rows = _build(row, claude_dir=claude_dir, records=agent_records)
    assert rows["output"]["content"] == "FINAL CLAUDE ANSWER."
    assert rows["prompt"]["meta"]["match_basis"] == "label_in_prompt+session_id"


def test_unmatchable_row_gets_four_source_file_missing_markers():
    rows = _build(_interactive_row(title="NEVER-SEEN-LABEL"))
    assert all(rows[k]["content"] is None for k in rows)
    assert all(
        rows[k]["unavailable_reason"] == "source_file_missing" for k in rows
    )
    # Marker rows still carry the full uniform key set.
    assert all(rows[k]["content_sha256"] is None for k in rows)
    assert all(rows[k]["truncated"] is False for k in rows)


def test_ledger_prompt_is_the_fallback_when_transcript_is_unmatched(agent_records):
    """The record matched by label but no transcript exists on disk — the
    ledger's own (already redacted+capped) prompt copy syncs with
    source='cursor_ledger' (§4.6).
    """
    rows = _build(_interactive_row(), records=agent_records)
    assert rows["prompt"]["content"] == CURSOR_PROMPT
    assert rows["prompt"]["source"] == "cursor_ledger"
    assert rows["tool_calls"]["unavailable_reason"] == "source_file_missing"


def test_cursor_sdk_dispatch_prompt_file_and_row_output(tmp_path):
    prompt_file = tmp_path / "runs" / "demo" / "prompts" / "S4-node.md"
    prompt_file.parent.mkdir(parents=True)
    prompt_file.write_text("# Dispatch prompt\nDo the node.")
    row = _interactive_row(
        cursor_agent_id="bc-1",
        stage="S4",
        report_path=str(prompt_file),
        final_response="SDK terminal result.",
    )
    rows = _build(row)
    assert rows["prompt"]["content"].startswith("# Dispatch prompt")
    assert rows["prompt"]["source"] == "dispatch_prompt_file"
    # VERIFY-2 (SDK conversation endpoint) is unexercised → not captured.
    assert rows["tool_calls"]["unavailable_reason"] == "not_captured_for_runtime"
    assert rows["thinking"]["unavailable_reason"] == "not_captured_for_runtime"
    assert rows["output"]["content"] == "SDK terminal result."
    assert rows["output"]["source"] == "agent_row"


def test_tier2_dispatch_with_transcript_gets_full_tool_log_and_cot(claude_dir):
    """VERIFY-1 answered YES live (2026-08-04): tier2 `claude -p` sessions do
    write ~/.claude/projects transcripts — so a tier2 row with a matched
    session_id gets the full claude availability column of §1a.
    """
    row = _interactive_row(stage="S4", ledger_session_id=CLAUDE_SESSION)
    rows = _build(row, claude_dir=claude_dir)
    assert rows["tool_calls"]["content"] is not None
    assert rows["thinking"]["content"] is not None
    assert rows["output"]["content"] == "FINAL CLAUDE ANSWER."


# ---------------------------------------------------------------------------
# §5.3 policy exclusion — machine-wide CoT never syncs
# ---------------------------------------------------------------------------


def test_machine_wide_thinking_is_policy_excluded_even_when_transcript_has_cot(
    claude_dir, agent_records
):
    row = _interactive_row(
        title="LABEL-CLAUDE", run_id=None, ledger_session_id=CLAUDE_SESSION
    )
    rows = _build(row, claude_dir=claude_dir, records=agent_records)
    assert rows["thinking"]["content"] is None
    assert rows["thinking"]["unavailable_reason"] == "policy_excluded_machine_wide"
    # The exclusion is a policy fact, not a capture fact — the OTHER kinds
    # still sync for machine-wide sessions per the §5.3 allow-list.
    assert rows["prompt"]["content"] == CLAUDE_PROMPT
    assert rows["tool_calls"]["content"] is not None
    assert rows["output"]["content"] == "FINAL CLAUDE ANSWER."
    # And the CoT never left extraction: content of thinking is not merely
    # nulled after the fact — no reasoning text appears anywhere in the row.
    assert "PRIVATE_REASONING" not in json.dumps(rows["thinking"])


# ---------------------------------------------------------------------------
# Redaction: patterns, embedded paths, withholding on failure
# ---------------------------------------------------------------------------


def test_redact_patterns_cover_cursor_and_fireworks_keys():
    text = (
        "cursor key crsr_abcDEF0123456789xyz and fireworks key "
        "fw_0123456789abcdefXY and openai sk-abcdefghijklmnop123"
    )
    out = redact_module.redact(text)
    assert "crsr_abcDEF0123456789xyz" not in out
    assert "fw_0123456789abcdefXY" not in out
    assert "sk-abcdefghijklmnop123" not in out
    assert out.count("[REDACTED]") == 3


def test_secrets_in_transcripts_are_redacted_before_any_row_is_built(
    cursor_dir, agent_records
):
    transcript = cursor_dir / CURSOR_UUID / f"{CURSOR_UUID}.jsonl"
    content = transcript.read_text().replace(
        "FINAL CURSOR ANSWER.", "key is fw_0123456789abcdefXY done"
    )
    transcript.write_text(content)
    rows = _build(_interactive_row(), cursor_dir=cursor_dir, records=agent_records)
    assert "fw_0123456789abcdefXY" not in json.dumps(rows)
    assert "[REDACTED]" in rows["output"]["content"]


def test_embedded_external_paths_are_allowlisted_not_shipped(
    cursor_dir, agent_records
):
    transcript = cursor_dir / CURSOR_UUID / f"{CURSOR_UUID}.jsonl"
    content = transcript.read_text().replace(
        "FINAL CURSOR ANSWER.",
        "read /Users/danieltecum/customer-acme-export/data.csv and "
        "/Users/danieltecum/emanate-tecum-workflow/PRs/demo/PLAN.md",
    )
    transcript.write_text(content)
    rows = _build(_interactive_row(), cursor_dir=cursor_dir, records=agent_records)
    output = rows["output"]["content"]
    assert "customer-acme-export" not in output
    assert "[external path — redacted]" in output
    # Repo-internal allow-listed paths survive, repo-relative.
    assert "PRs/demo/PLAN.md" in output


def test_redaction_failure_withholds_content_never_ships_it(
    cursor_dir, agent_records, monkeypatch
):
    monkeypatch.setattr(
        graph_artifacts,
        "redact",
        lambda text: "[REDACTION-ERROR — content withheld]",
    )
    rows = _build(_interactive_row(), cursor_dir=cursor_dir, records=agent_records)
    assert all(rows[k]["content"] is None for k in rows)
    real_kinds = ("prompt", "tool_calls", "output")
    assert all(
        rows[k]["unavailable_reason"] == "redaction_error_withheld"
        for k in real_kinds
    )


def test_redactor_exception_also_withholds(monkeypatch):
    def _boom(text):
        raise RuntimeError("internal redactor failure")

    monkeypatch.setattr(graph_artifacts, "redact", _boom)
    assert graph_artifacts.redact_content("anything") is None


# ---------------------------------------------------------------------------
# Caps (§2.4)
# ---------------------------------------------------------------------------


def test_caps_keep_head_and_tail_with_elision_marker():
    text = "H" * (400 * 1024) + "M" * (300 * 1024) + "T" * (100 * 1024)
    capped, truncated = graph_artifacts.apply_caps(text)
    assert truncated is True
    assert capped.startswith("H" * 100)
    assert capped.endswith("T" * 100)
    assert graph_artifacts.ELISION_MARKER in capped
    body = len(capped.encode("utf-8")) - len(
        graph_artifacts.ELISION_MARKER.encode("utf-8")
    )
    assert body == graph_artifacts.HEAD_BYTES + graph_artifacts.TAIL_BYTES


def test_under_cap_content_is_untouched():
    capped, truncated = graph_artifacts.apply_caps("small")
    assert (capped, truncated) == ("small", False)


def test_truncated_flag_reaches_the_row(tmp_path):
    prompt_file = tmp_path / "big-prompt.md"
    prompt_file.write_text("X" * (600 * 1024))
    row = _interactive_row(cursor_agent_id="bc-1", report_path=str(prompt_file))
    rows = _build(row)
    assert rows["prompt"]["truncated"] is True
    assert rows["prompt"]["size_bytes"] <= graph_artifacts.CONTENT_CAP_BYTES + len(
        graph_artifacts.ELISION_MARKER.encode("utf-8")
    )


# ---------------------------------------------------------------------------
# uuid5 determinism + idempotent re-run (design §4.3)
# ---------------------------------------------------------------------------


def test_ids_are_deterministic_uuid5_on_agent_kind_and_seq(cursor_dir, agent_records):
    rows = _build(_interactive_row(), cursor_dir=cursor_dir, records=agent_records)
    for kind, row in rows.items():
        assert row["id"] == str(
            uuid.uuid5(NAMESPACE, f"factory_agent_artifacts:{AGENT_ID}:{kind}:0")
        )
    again = _build(_interactive_row(), cursor_dir=cursor_dir, records=agent_records)
    assert {k: r["id"] for k, r in rows.items()} == {
        k: r["id"] for k, r in again.items()
    }
    assert {k: r["content_sha256"] for k, r in rows.items()} == {
        k: r["content_sha256"] for k, r in again.items()
    }


def test_rerun_upserts_identical_rows_and_sha_short_circuit_skips_writes(
    cursor_dir, agent_records
):
    store = {}

    def fake_select(table, params=None, url=None, service_role_key=None):
        assert table == "factory_agent_artifacts"
        return [
            {"id": r["id"], "content_sha256": r["content_sha256"]}
            for r in store.values()
        ]

    def fake_upsert(table, rows, on_conflict=None, url=None, service_role_key=None):
        assert table == "factory_agent_artifacts"
        assert on_conflict == "id"
        store.update({r["id"]: r for r in rows})
        return rows

    kwargs = dict(
        agent_records=agent_records,
        cursor_transcripts_dir=cursor_dir,
        claude_projects_dir="/nonexistent-claude",
    )
    with patch.object(graph_artifacts, "select", side_effect=fake_select), patch.object(
        graph_artifacts, "upsert", side_effect=fake_upsert
    ) as mock_upsert:
        first = graph_artifacts.push_agent_artifacts(_interactive_row(), **kwargs)
        assert mock_upsert.call_count == 1
        assert len(store) == 4
        second = graph_artifacts.push_agent_artifacts(_interactive_row(), **kwargs)

    assert len(store) == 4  # re-run added zero rows
    assert first == {
        "prompt": "content",
        "tool_calls": "content",
        "thinking": "marker:not_captured_for_runtime",
        "output": "content",
    }
    # Second pass: every artifact unchanged → sha short-circuit, no second upsert.
    assert second == {k: "unchanged" for k in first}
    assert mock_upsert.call_count == 1


# ---------------------------------------------------------------------------
# Fail-open on the not-yet-migrated table
# ---------------------------------------------------------------------------


def test_missing_table_logs_one_warning_and_never_raises(caplog):
    agent_rows = [_interactive_row(), _interactive_row(id="10000000-0000-0000-0000-000000000002")]

    def fake_select(table, params=None, url=None, service_role_key=None):
        if table == "factory_agents":
            return agent_rows
        raise SupabaseRestError(
            'select factory_agent_artifacts failed: 404 {"code":"42P01",'
            '"message":"relation \\"public.factory_agent_artifacts\\" does not exist"}'
        )

    with patch.object(graph_artifacts, "select", side_effect=fake_select), patch.object(
        graph_artifacts, "upsert"
    ) as mock_upsert:
        result = graph_artifacts.sync_agent_artifacts(agent_records=[])

    assert result == 0  # stopped at the first missing-table signal, no crash
    mock_upsert.assert_not_called()
    warnings = [
        r for r in caplog.records if "not deployed yet" in r.getMessage()
    ]
    assert len(warnings) == 1  # ONE warning per pass, not one per agent


def test_unreachable_supabase_fails_open_to_none():
    def _raise(*a, **k):
        raise SupabaseRestError("connection refused")

    with patch.object(graph_artifacts, "select", side_effect=_raise):
        assert graph_artifacts.sync_agent_artifacts(agent_records=[]) is None


def test_one_bad_agent_never_kills_the_pass(cursor_dir, agent_records):
    good = _interactive_row()
    bad = _interactive_row(id="10000000-0000-0000-0000-000000000002", title=None)

    def fake_select(table, params=None, url=None, service_role_key=None):
        if table == "factory_agents":
            return [bad, good]
        return []

    calls = []

    def fake_upsert(table, rows, on_conflict=None, url=None, service_role_key=None):
        calls.append(rows)
        return rows

    with patch.object(graph_artifacts, "select", side_effect=fake_select), patch.object(
        graph_artifacts, "upsert", side_effect=fake_upsert
    ), patch.object(
        graph_artifacts,
        "build_artifact_rows",
        side_effect=[RuntimeError("boom"), graph_artifacts.build_artifact_rows(
            good,
            graph_artifacts.locate_sources(
                good,
                graph_artifacts.RUNTIME_INTERACTIVE,
                agent_records=agent_records,
                cursor_transcripts_dir=cursor_dir,
                claude_projects_dir="/nonexistent",
            ),
            graph_artifacts.RUNTIME_INTERACTIVE,
        )],
    ):
        processed = graph_artifacts.sync_agent_artifacts(agent_records=agent_records)
    assert processed == 1
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# session_id_map — the agents_sync_job gap, closed
# ---------------------------------------------------------------------------


def test_session_id_map_joins_labels_and_refuses_ambiguity(agent_records):
    ambiguous = agent_records + [
        {
            "ts": "2026-08-04T11:00:00-07:00",
            "source": "claude-code",
            "session_id": "33333333-3333-3333-3333-333333333333",
            "prompt": "Second record also mentioning LABEL-CLAUDE verbatim.",
        }
    ]
    mapping = graph_artifacts.build_session_id_map(
        ["LABEL-CURSOR", "LABEL-CLAUDE", "UNKNOWN-LABEL"], ambiguous
    )
    assert mapping == {"LABEL-CURSOR": "gen-cursor-1"}  # claude label now ambiguous → absent


def test_sync_job_passes_session_id_map_and_runs_artifacts_pass(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    (runs_dir / "open-org").mkdir(parents=True)
    (runs_dir / "open-org" / ".sync_state.json").write_text(json.dumps({"run_id": RUN_ID}))
    heartbeat = tmp_path / "heartbeat" / "STATE.md"
    heartbeat.parent.mkdir(parents=True)
    heartbeat.write_text(
        "## Active Sessions\n\n"
        "| session | goal | allowed_paths | risk | status | seq | last_update |\n"
        "|---|---|---|---|---|---|---|\n"
        "| LABEL-CURSOR | open-org corpus work | PRs/ | code | ACTIVE | 1 | x |\n"
    )
    ledger = tmp_path / "ledger"
    (ledger / "agents").mkdir(parents=True)
    (ledger / "agents" / "2026-08-04.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-08-04T10:00:00-07:00",
                "source": "cursor",
                "generation_uuid": "gen-cursor-1",
                "prompt": CURSOR_PROMPT,
            }
        )
        + "\n"
    )
    monkeypatch.setattr(agents_sync_job, "RUNS_DIR", runs_dir)
    monkeypatch.setattr(agents_sync_job, "HEARTBEAT_PATH", heartbeat)
    monkeypatch.setattr(agents_sync_job, "LEDGER_DIR", ledger)

    def fake_select(table, params=None, url=None, service_role_key=None):
        return [{"status": "running"}]

    with patch.object(agents_sync_job, "select", side_effect=fake_select), patch.object(
        agents_sync_job, "push_agents", return_value=True
    ) as mock_push, patch.object(
        agents_sync_job, "push_machine_sessions", return_value=0
    ) as mock_machine, patch.object(
        agents_sync_job, "sync_agent_artifacts", return_value=1
    ) as mock_artifacts:
        summary = agents_sync_job.sync_all_open_runs()

    # The gap fix: the map reaches BOTH pushes.
    assert mock_push.call_args.kwargs["session_id_map"] == {"LABEL-CURSOR": "gen-cursor-1"}
    assert mock_machine.call_args.kwargs["session_id_map"] == {"LABEL-CURSOR": "gen-cursor-1"}
    # The artifacts pass ran, with the same ledger records, after the pushes.
    assert mock_artifacts.call_count == 1
    assert len(mock_artifacts.call_args.kwargs["agent_records"]) == 1
    # The summary contract is unchanged (wrapper sentinel keys off these).
    assert summary == {
        "runs_checked": 1,
        "runs_open": 1,
        "runs_synced": 1,
        "sessions_synced": 0,
    }


# ---------------------------------------------------------------------------
# Backfill — dry-run default, per-origin matrix, unmatchable → markers
# ---------------------------------------------------------------------------


def test_backfill_dry_run_writes_nothing_and_reports_matrix(cursor_dir, agent_records):
    rows = [
        _interactive_row(),
        _interactive_row(
            id="10000000-0000-0000-0000-000000000002",
            title="NEVER-SEEN",
            run_id=None,
        ),
    ]
    with patch.object(graph_artifacts, "select") as mock_select, patch.object(
        graph_artifacts, "upsert"
    ) as mock_upsert:
        matrix, processed, failed = artifacts_backfill.backfill(
            rows,
            agent_records=agent_records,
            cursor_transcripts_dir=cursor_dir,
            claude_projects_dir="/nonexistent",
            live=False,
        )
    mock_select.assert_not_called()
    mock_upsert.assert_not_called()
    assert (processed, failed) == (2, 0)
    interactive = graph_artifacts.RUNTIME_INTERACTIVE
    machine = graph_artifacts.RUNTIME_MACHINE_WIDE
    assert matrix[(interactive, "prompt", "content")] == 1
    assert matrix[(machine, "prompt", "marker:source_file_missing")] == 1
    assert matrix[(machine, "thinking", "marker:policy_excluded_machine_wide")] == 1


def test_backfill_cli_is_dry_run_by_default(capsys):
    with patch.object(
        artifacts_backfill, "fetch_agent_rows", return_value=[_interactive_row()]
    ), patch.object(
        artifacts_backfill, "load_agent_records", return_value=[]
    ), patch.object(graph_artifacts, "upsert") as mock_upsert:
        assert artifacts_backfill.main([]) == 0
    mock_upsert.assert_not_called()
    out = capsys.readouterr().out
    assert "dry-run: no writes performed" in out


def test_backfill_live_pushes_markers_for_unmatchable_rows(cursor_dir, agent_records):
    pushed = []

    def fake_select(table, params=None, url=None, service_role_key=None):
        return []

    def fake_upsert(table, rows, on_conflict=None, url=None, service_role_key=None):
        pushed.extend(rows)
        return rows

    rows = [_interactive_row(title="NEVER-SEEN")]
    with patch.object(graph_artifacts, "select", side_effect=fake_select), patch.object(
        graph_artifacts, "upsert", side_effect=fake_upsert
    ):
        matrix, processed, failed = artifacts_backfill.backfill(
            rows, agent_records=agent_records, live=True,
            cursor_transcripts_dir=cursor_dir, claude_projects_dir="/nonexistent",
        )
    assert processed == 1
    assert len(pushed) == 4
    assert all(r["unavailable_reason"] == "source_file_missing" for r in pushed)
    assert all(r["content"] is None for r in pushed)
