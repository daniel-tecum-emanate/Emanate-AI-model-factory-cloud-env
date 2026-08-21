"""factory-graph-v2 artifact pipeline — ADVERSARIAL edge cases the base suite
(test_graph_artifacts.py, 27 tests) does not cover: cap boundary bytes and
multibyte UTF-8 straddling the head/tail split, malformed/unreadable transcript
files, per-kind secret redaction (sk-/crsr_/fw_/PEM), prompt-prefix collisions,
sha short-circuit behavior under a one-byte source mutation, the machine-wide
CoT never-even-extracted guarantee, unicode/RTL round-trips, non-404 REST
failure modes, and the backfill matrix across all four origin classes.
Mocked REST throughout — no live network, temp-dir fixtures only.
"""

import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import agents_sync_job  # noqa: E402
import artifacts_backfill  # noqa: E402
import graph_artifacts  # noqa: E402
from supabase_rest import SupabaseRestError  # noqa: E402

RUN_ID = "ac6a6078-3932-4485-8b79-85eb318b85c9"
CURSOR_UUID = "44444444-4444-4444-4444-444444444444"
CLAUDE_SESSION = "55555555-5555-5555-5555-555555555555"
AGENT_ID = "20000000-0000-0000-0000-000000000001"

CURSOR_PROMPT = (
    "You are EDGE-CURSOR, a research subagent of MF-PARENT. "
    "Read PRs/demo/PLAN.md and report."
)
CLAUDE_PROMPT = (
    "You are EDGE-CLAUDE, an execution subagent of MF-PARENT. "
    "Do the machine-wide thing."
)


def _jsonl(rows):
    return "\n".join(json.dumps(r) for r in rows) + "\n"


def _cursor_user_line(prompt):
    return {
        "role": "user",
        "message": {
            "content": [
                {
                    "type": "text",
                    "text": f"<user_query>\n{prompt}\n</user_query>",
                }
            ]
        },
    }


def _write_cursor_transcript(base_dir, session_uuid, rows):
    session = Path(base_dir) / session_uuid
    session.mkdir(parents=True, exist_ok=True)
    path = session / f"{session_uuid}.jsonl"
    path.write_text(_jsonl(rows))
    return path


@pytest.fixture()
def cursor_dir(tmp_path):
    _write_cursor_transcript(
        tmp_path / "cursor-transcripts",
        CURSOR_UUID,
        [
            _cursor_user_line(CURSOR_PROMPT),
            {
                "role": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"path": "PRs/demo/PLAN.md"},
                        },
                        {"type": "text", "text": "Working."},
                    ]
                },
            },
            {
                "role": "assistant",
                "message": {"content": [{"type": "text", "text": "FINAL CURSOR ANSWER."}]},
            },
        ],
    )
    return tmp_path / "cursor-transcripts"


@pytest.fixture()
def claude_dir(tmp_path):
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
                            {"type": "thinking", "thinking": "EDGE_PRIVATE_REASONING"},
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
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "FINAL CLAUDE ANSWER."}]
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
            "generation_uuid": "gen-edge-cursor",
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
        "title": "EDGE-CURSOR",
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


def _src(path, kind="cursor"):
    """A pre-resolved source dict, to drive extraction at a broken path."""
    return {
        "transcript_path": path,
        "transcript_kind": kind,
        "prompt_file": None,
        "ledger_prompt": None,
        "match_basis": None,
    }


# ---------------------------------------------------------------------------
# Caps (§2.4): exact boundary, boundary+1, multibyte straddle
# ---------------------------------------------------------------------------


def test_caps_exact_boundary_is_untouched():
    text = "A" * graph_artifacts.CONTENT_CAP_BYTES  # exactly 512 KB
    capped, truncated = graph_artifacts.apply_caps(text)
    assert truncated is False
    assert capped == text


def test_caps_one_byte_over_truncates_within_budget():
    text = "A" * (graph_artifacts.CONTENT_CAP_BYTES + 1)
    capped, truncated = graph_artifacts.apply_caps(text)
    assert truncated is True
    assert graph_artifacts.ELISION_MARKER in capped
    body_bytes = len(capped.encode("utf-8")) - len(
        graph_artifacts.ELISION_MARKER.encode("utf-8")
    )
    assert body_bytes == graph_artifacts.HEAD_BYTES + graph_artifacts.TAIL_BYTES
    assert capped.startswith("A") and capped.endswith("A")


def test_caps_multibyte_straddling_the_split_never_yields_invalid_utf8():
    """4-byte emoji laid out so BOTH the head cut (byte 384K) and the tail cut
    fall mid-codepoint: 'x' prefix misaligns the head split, 'yz' suffix
    misaligns the tail split. errors='ignore' must drop the partial codepoint,
    never emit U+FFFD or a broken sequence.
    """
    emoji = "\U0001f389"  # 🎉, 4 UTF-8 bytes
    count = (graph_artifacts.CONTENT_CAP_BYTES // 4) + 200
    text = "x" + emoji * count + "yz"
    assert len(text.encode("utf-8")) > graph_artifacts.CONTENT_CAP_BYTES
    # Sanity: both cut points land mid-emoji on the codepoint grid.
    assert (graph_artifacts.HEAD_BYTES - 1) % 4 != 0
    tail_start = len(text.encode("utf-8")) - graph_artifacts.TAIL_BYTES
    assert (tail_start - 1) % 4 != 0

    capped, truncated = graph_artifacts.apply_caps(text)
    assert truncated is True
    # Round-trips as valid UTF-8 with zero replacement characters.
    assert "\ufffd" not in capped
    assert capped.encode("utf-8").decode("utf-8") == capped
    # Only whole expected codepoints survive — a split emoji would surface as
    # some other character here.
    allowed = set("xyz" + emoji) | set(graph_artifacts.ELISION_MARKER)
    assert set(capped) <= allowed
    # At most one partial codepoint (≤3 bytes) dropped per cut.
    body_bytes = len(capped.encode("utf-8")) - len(
        graph_artifacts.ELISION_MARKER.encode("utf-8")
    )
    budget = graph_artifacts.HEAD_BYTES + graph_artifacts.TAIL_BYTES
    assert budget - 6 <= body_bytes <= budget


# ---------------------------------------------------------------------------
# Malformed transcript sources — fail-open, never raise
# ---------------------------------------------------------------------------


def test_truncated_last_line_still_extracts_the_valid_turns(tmp_path):
    session = tmp_path / CURSOR_UUID
    session.mkdir()
    path = session / f"{CURSOR_UUID}.jsonl"
    path.write_text(
        json.dumps(_cursor_user_line(CURSOR_PROMPT))
        + "\n"
        + '{"role": "assistant", "message": {"content": [{"type": "te'  # truncated mid-write
    )
    rows = graph_artifacts.build_artifact_rows(
        _interactive_row(), _src(path), graph_artifacts.RUNTIME_INTERACTIVE
    )
    by_kind = {r["artifact_kind"]: r for r in rows}
    assert by_kind["prompt"]["content"] == CURSOR_PROMPT
    # No assistant turn survived → output honestly falls to a marker.
    assert by_kind["output"]["unavailable_reason"] == "source_file_missing"


def test_non_json_line_mid_file_is_skipped_and_extraction_continues(tmp_path):
    session = tmp_path / CURSOR_UUID
    session.mkdir()
    path = session / f"{CURSOR_UUID}.jsonl"
    path.write_text(
        json.dumps(_cursor_user_line(CURSOR_PROMPT))
        + "\n"
        + "GARBAGE — not json at all {{{]\n"
        + json.dumps(
            {
                "role": "assistant",
                "message": {"content": [{"type": "text", "text": "PAST THE GARBAGE."}]},
            }
        )
        + "\n"
    )
    rows = graph_artifacts.build_artifact_rows(
        _interactive_row(), _src(path), graph_artifacts.RUNTIME_INTERACTIVE
    )
    by_kind = {r["artifact_kind"]: r for r in rows}
    assert by_kind["prompt"]["content"] == CURSOR_PROMPT
    assert by_kind["output"]["content"] == "PAST THE GARBAGE."


def test_empty_transcript_file_yields_markers_never_raises(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("")
    rows = graph_artifacts.build_artifact_rows(
        _interactive_row(), _src(path), graph_artifacts.RUNTIME_INTERACTIVE
    )
    by_kind = {r["artifact_kind"]: r for r in rows}
    assert by_kind["prompt"]["unavailable_reason"] == "source_file_missing"
    assert by_kind["output"]["unavailable_reason"] == "source_file_missing"
    # An empty tool log is honestly empty JSON, never a crash.
    assert by_kind["tool_calls"]["content"] in (None, "[]")


def test_transcript_path_that_is_a_directory_fails_open(tmp_path):
    directory = tmp_path / "i-am-a-directory"
    directory.mkdir()
    rows = graph_artifacts.build_artifact_rows(
        _interactive_row(), _src(directory), graph_artifacts.RUNTIME_INTERACTIVE
    )
    by_kind = {r["artifact_kind"]: r for r in rows}
    assert by_kind["prompt"]["content"] is None
    assert by_kind["prompt"]["unavailable_reason"] == "source_file_missing"
    assert by_kind["output"]["unavailable_reason"] == "source_file_missing"


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_unreadable_transcript_fails_open(tmp_path):
    path = tmp_path / "locked.jsonl"
    path.write_text(json.dumps(_cursor_user_line(CURSOR_PROMPT)) + "\n")
    path.chmod(0)
    try:
        rows = graph_artifacts.build_artifact_rows(
            _interactive_row(), _src(path), graph_artifacts.RUNTIME_INTERACTIVE
        )
    finally:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    by_kind = {r["artifact_kind"]: r for r in rows}
    assert by_kind["prompt"]["unavailable_reason"] == "source_file_missing"
    # _cursor_first_query on the same unreadable file must also fail open.
    path.chmod(0)
    try:
        assert graph_artifacts._cursor_first_query(path) is None
    finally:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


# ---------------------------------------------------------------------------
# Secrets in EVERY artifact kind — redacted before any row ships
# ---------------------------------------------------------------------------

SK_KEY = "sk-abcdefghijklmnop123456"
CRSR_TOKEN = "crsr_abcDEF0123456789xyzt"
FW_KEY = "fw_0123456789abcdefXYab"
PEM_BLOCK = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MIIEvQIBADANBgkqFAKEFAKEFAKEFAKE\n"
    "-----END PRIVATE KEY-----"
)


@pytest.fixture()
def secret_claude_dir(tmp_path):
    """A claude transcript carrying a different secret shape in each of the
    four artifact kinds: sk- in the prompt, crsr_ inside a tool input, fw_
    inside thinking, a PEM block in the final output.
    """
    slug = tmp_path / "claude-secrets" / "-Users-x-workflow"
    slug.mkdir(parents=True)
    (slug / f"{CLAUDE_SESSION}.jsonl").write_text(
        _jsonl(
            [
                {
                    "type": "user",
                    "message": {"content": f"{CLAUDE_PROMPT} use key {SK_KEY}"},
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": f"remember the fireworks key {FW_KEY}",
                            },
                            {
                                "type": "tool_use",
                                "id": "tu_1",
                                "name": "Bash",
                                "input": {"command": f"curl -H 'X-Api: {CRSR_TOKEN}'"},
                            },
                        ]
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": f"Done. Cert was:\n{PEM_BLOCK}\nEOF"}
                        ]
                    },
                },
            ]
        )
    )
    return tmp_path / "claude-secrets"


def test_secrets_redacted_in_every_artifact_kind(secret_claude_dir):
    row = _interactive_row(title="EDGE-CLAUDE", ledger_session_id=CLAUDE_SESSION)
    rows = _build(row, claude_dir=secret_claude_dir)
    everything = json.dumps(rows)
    assert SK_KEY not in everything
    assert CRSR_TOKEN not in everything
    assert FW_KEY not in everything
    assert "BEGIN PRIVATE KEY" not in everything
    # Each kind extracted content AND carries the redaction mark in place.
    assert "[REDACTED]" in rows["prompt"]["content"]
    assert "[REDACTED]" in rows["tool_calls"]["content"]
    assert "[REDACTED]" in rows["thinking"]["content"]
    assert "[REDACTED]" in rows["output"]["content"]
    # The sha is computed over the REDACTED content, so the short-circuit can
    # never be keyed on secret-bearing bytes.
    import hashlib

    assert rows["output"]["content_sha256"] == hashlib.sha256(
        rows["output"]["content"].encode("utf-8")
    ).hexdigest()


def test_raising_redactor_withholds_every_kind_at_row_level(
    secret_claude_dir, monkeypatch
):
    def _boom(text):
        raise RuntimeError("internal redactor failure")

    monkeypatch.setattr(graph_artifacts, "redact", _boom)
    row = _interactive_row(title="EDGE-CLAUDE", ledger_session_id=CLAUDE_SESSION)
    rows = _build(row, claude_dir=secret_claude_dir)
    assert all(rows[k]["content"] is None for k in rows)
    assert all(
        rows[k]["unavailable_reason"] == "redaction_error_withheld" for k in rows
    )
    # Not one secret byte reaches any field of any row.
    everything = json.dumps(rows)
    for secret in (SK_KEY, CRSR_TOKEN, FW_KEY, "BEGIN PRIVATE KEY"):
        assert secret not in everything


# ---------------------------------------------------------------------------
# Prompt-prefix collision — conservative no-match, never a wrong match
# ---------------------------------------------------------------------------


def test_identical_prompt_collision_in_overlapping_window_resolves_to_no_match(
    tmp_path, caplog
):
    long_prompt = "You are EDGE-TWIN, a subagent of MF-PARENT. " + "A" * 3956  # 4000 chars
    base = tmp_path / "cursor-transcripts"
    for twin in (
        "66666666-6666-6666-6666-666666666666",
        "77777777-7777-7777-7777-777777777777",
    ):
        _write_cursor_transcript(base, twin, [_cursor_user_line(long_prompt)])
    record = {
        # Both transcript files were just created, so both sit inside the
        # ±5-minute window — the timestamp filter cannot disambiguate.
        "ts": datetime.now(timezone.utc).astimezone().isoformat(),
        "source": "cursor",
        "generation_uuid": "gen-twin",
        "prompt": long_prompt,
    }
    assert graph_artifacts._match_cursor_transcript(record, base) is None
    assert any("refusing to guess" in r.getMessage() for r in caplog.records)

    # Through locate_sources the row still gets the honest ledger fallback,
    # never one of the two colliding transcripts.
    row = _interactive_row(title="EDGE-TWIN")
    src = graph_artifacts.locate_sources(
        row,
        graph_artifacts.RUNTIME_INTERACTIVE,
        agent_records=[record],
        cursor_transcripts_dir=base,
        claude_projects_dir="/nonexistent",
    )
    assert src["transcript_path"] is None
    assert src["ledger_prompt"] == long_prompt
    assert src["match_basis"] == "label_in_prompt(ledger_only)"


# ---------------------------------------------------------------------------
# Idempotency: sha short-circuit, then one mutated byte → one re-upsert
# ---------------------------------------------------------------------------


def test_one_byte_mutation_reupserts_exactly_the_affected_artifact(
    cursor_dir, agent_records
):
    store = {}
    upsert_batches = []

    def fake_select(table, params=None, url=None, service_role_key=None):
        return [
            {"id": r["id"], "content_sha256": r["content_sha256"]}
            for r in store.values()
        ]

    def fake_upsert(table, rows, on_conflict=None, url=None, service_role_key=None):
        upsert_batches.append(rows)
        store.update({r["id"]: r for r in rows})
        return rows

    kwargs = dict(
        agent_records=agent_records,
        cursor_transcripts_dir=cursor_dir,
        claude_projects_dir="/nonexistent",
    )
    with patch.object(graph_artifacts, "select", side_effect=fake_select), patch.object(
        graph_artifacts, "upsert", side_effect=fake_upsert
    ):
        first = graph_artifacts.push_agent_artifacts(_interactive_row(), **kwargs)
        second = graph_artifacts.push_agent_artifacts(_interactive_row(), **kwargs)
        assert len(upsert_batches) == 1  # second pass fully short-circuited
        first_ids = {r["id"] for r in upsert_batches[0]}

        # Mutate exactly one source byte: the final answer's period → bang.
        transcript = cursor_dir / CURSOR_UUID / f"{CURSOR_UUID}.jsonl"
        transcript.write_text(
            transcript.read_text().replace("FINAL CURSOR ANSWER.", "FINAL CURSOR ANSWER!")
        )
        third = graph_artifacts.push_agent_artifacts(_interactive_row(), **kwargs)

    assert first["output"] == "content"
    assert second == {k: "unchanged" for k in first}
    # Third pass: only the output artifact re-upserts; ids are stable.
    assert len(upsert_batches) == 2
    assert [r["artifact_kind"] for r in upsert_batches[1]] == ["output"]
    assert upsert_batches[1][0]["id"] in first_ids
    assert upsert_batches[1][0]["content"] == "FINAL CURSOR ANSWER!"
    assert third["output"] == "content"
    assert third["prompt"] == third["tool_calls"] == third["thinking"] == "unchanged"
    assert len(store) == 4  # never a fifth row


# ---------------------------------------------------------------------------
# Machine-wide: thinking is never EXTRACTED (not merely not pushed)
# ---------------------------------------------------------------------------


def test_machine_wide_thinking_never_reaches_the_extractor(
    claude_dir, agent_records, monkeypatch
):
    def _forbidden(path, kind):
        raise AssertionError(
            "machine-wide CoT must be excluded BEFORE extraction (§5.3)"
        )

    monkeypatch.setattr(graph_artifacts, "_extract_thinking", _forbidden)
    row = _interactive_row(
        title="EDGE-CLAUDE", run_id=None, ledger_session_id=CLAUDE_SESSION
    )
    rows = _build(row, claude_dir=claude_dir, records=agent_records)
    assert rows["thinking"]["content"] is None
    assert rows["thinking"]["unavailable_reason"] == "policy_excluded_machine_wide"
    assert rows["thinking"]["source"] == "none"
    # The §5.3 allow-list still syncs the other three kinds.
    assert rows["prompt"]["content"] == CLAUDE_PROMPT
    assert rows["tool_calls"]["content"] is not None
    assert rows["output"]["content"] == "FINAL CLAUDE ANSWER."


# ---------------------------------------------------------------------------
# Unicode / emoji / RTL round-trip
# ---------------------------------------------------------------------------


def test_unicode_emoji_rtl_content_survives_extraction_and_caps(tmp_path):
    fancy = "مرحبا بالعالم — שלום עולם — 日本語テスト — 🎉🚀🧪 — café naïve"
    session_uuid = "88888888-8888-8888-8888-888888888888"
    path = _write_cursor_transcript(
        tmp_path / "cursor-transcripts",
        session_uuid,
        [
            _cursor_user_line(fancy),
            {
                "role": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Write",
                            "input": {"text": fancy},
                        },
                        {"type": "text", "text": f"echoed: {fancy}"},
                    ]
                },
            },
        ],
    )
    rows = graph_artifacts.build_artifact_rows(
        _interactive_row(), _src(path), graph_artifacts.RUNTIME_INTERACTIVE
    )
    by_kind = {r["artifact_kind"]: r for r in rows}
    assert by_kind["prompt"]["content"] == fancy
    assert by_kind["output"]["content"] == f"echoed: {fancy}"
    calls = json.loads(by_kind["tool_calls"]["content"])
    assert calls[0]["input"]["text"] == fancy  # ensure_ascii=False round-trip
    assert "🎉" in by_kind["tool_calls"]["content"]  # stored unescaped, not \uXXXX
    assert "\ufffd" not in json.dumps(rows, ensure_ascii=False)
    # size_bytes counts UTF-8 bytes, not characters.
    assert by_kind["prompt"]["size_bytes"] == len(fancy.encode("utf-8"))


# ---------------------------------------------------------------------------
# REST failure modes beyond the covered 404-table-missing case
# ---------------------------------------------------------------------------

_FAILURES = [
    'select factory_agent_artifacts failed: 401 {"message":"Invalid API key"}',
    'select factory_agent_artifacts failed: 500 {"message":"internal error"}',
    (
        "select factory_agent_artifacts transport error: "
        "HTTPSConnectionPool(host='x.supabase.co', port=443): "
        "Read timed out. (read timeout=10)"
    ),
]


@pytest.mark.parametrize("error_text", _FAILURES, ids=["401", "500", "timeout"])
def test_non_404_rest_failure_warns_and_continues_to_next_agent(
    error_text, cursor_dir, agent_records, caplog
):
    bad = _interactive_row()
    good = _interactive_row(id="20000000-0000-0000-0000-000000000002")

    def fake_select(table, params=None, url=None, service_role_key=None):
        if table == "factory_agents":
            return [bad, good]
        if params["agent_id"] == f"eq.{bad['id']}":
            raise SupabaseRestError(error_text)
        return []

    pushed = []

    def fake_upsert(table, rows, on_conflict=None, url=None, service_role_key=None):
        pushed.extend(rows)
        return rows

    with patch.object(graph_artifacts, "select", side_effect=fake_select), patch.object(
        graph_artifacts, "upsert", side_effect=fake_upsert
    ):
        processed = graph_artifacts.sync_agent_artifacts(
            agent_records=agent_records,
            cursor_transcripts_dir=cursor_dir,
            claude_projects_dir="/nonexistent",
        )

    # The failure took the per-agent warning path — not the missing-table stop,
    # not a raise — and the NEXT agent still synced.
    assert processed == 1
    assert {r["agent_id"] for r in pushed} == {good["id"]}
    warnings = [r for r in caplog.records if "artifact push failed" in r.getMessage()]
    assert len(warnings) == 1
    assert not any("not deployed yet" in r.getMessage() for r in caplog.records)


def test_artifacts_pass_404_leaves_job_summary_untouched(tmp_path, monkeypatch, caplog):
    """The wrapper's staleness sentinel parses the summary line — a dormant
    artifacts pass (table missing) must leave the dict byte-identical.
    """
    heartbeat = tmp_path / "heartbeat" / "STATE.md"
    heartbeat.parent.mkdir(parents=True)
    heartbeat.write_text("## Active Sessions\n\n(no table)\n")
    monkeypatch.setattr(agents_sync_job, "RUNS_DIR", tmp_path / "runs")  # absent
    monkeypatch.setattr(agents_sync_job, "HEARTBEAT_PATH", heartbeat)
    monkeypatch.setattr(agents_sync_job, "LEDGER_DIR", tmp_path / "ledger")  # absent

    def artifact_select(table, params=None, url=None, service_role_key=None):
        if table == "factory_agents":
            return [_interactive_row()]
        raise SupabaseRestError(
            'select factory_agent_artifacts failed: 404 {"code":"PGRST205",'
            '"message":"Could not find the table"}'
        )

    with patch.object(
        agents_sync_job, "push_machine_sessions", return_value=0
    ), patch.object(
        graph_artifacts, "select", side_effect=artifact_select
    ), patch.object(graph_artifacts, "upsert") as mock_upsert:
        summary = agents_sync_job.sync_all_open_runs()

    mock_upsert.assert_not_called()
    assert summary == {
        "runs_checked": 0,
        "runs_open": 0,
        "runs_synced": 0,
        "sessions_synced": 0,
    }
    assert any("not deployed yet" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Backfill across ALL FOUR origin classes
# ---------------------------------------------------------------------------


@pytest.fixture()
def four_origin_rows(tmp_path):
    prompt_file = tmp_path / "runs" / "demo" / "prompts" / "S4-node.md"
    prompt_file.parent.mkdir(parents=True)
    prompt_file.write_text("# Dispatch prompt\nDo the node.")
    return [
        _interactive_row(),  # interactive_run_session (cursor transcript)
        _interactive_row(  # machine_wide_session (claude via ledger record)
            id="20000000-0000-0000-0000-000000000002",
            title="EDGE-CLAUDE",
            run_id=None,
        ),
        _interactive_row(  # cursor_sdk_dispatch (prompt file + row output)
            id="20000000-0000-0000-0000-000000000003",
            title="SDK-NODE",
            cursor_agent_id="bc-1",
            report_path=str(prompt_file),
            final_response="SDK terminal result.",
        ),
        _interactive_row(  # tier2_dispatch (full claude transcript)
            id="20000000-0000-0000-0000-000000000004",
            title="TIER2-NODE",
            stage="S4",
            ledger_session_id=CLAUDE_SESSION,
        ),
    ]


def test_backfill_dry_run_four_origins_full_matrix_zero_writes(
    four_origin_rows, cursor_dir, claude_dir, agent_records
):
    with patch.object(graph_artifacts, "select") as mock_select, patch.object(
        graph_artifacts, "upsert"
    ) as mock_upsert:
        matrix, processed, failed = artifacts_backfill.backfill(
            four_origin_rows,
            agent_records=agent_records,
            cursor_transcripts_dir=cursor_dir,
            claude_projects_dir=claude_dir,
            live=False,
        )
    mock_select.assert_not_called()
    mock_upsert.assert_not_called()
    assert (processed, failed) == (4, 0)

    interactive = graph_artifacts.RUNTIME_INTERACTIVE
    machine = graph_artifacts.RUNTIME_MACHINE_WIDE
    sdk = graph_artifacts.RUNTIME_CURSOR_SDK
    tier2 = graph_artifacts.RUNTIME_TIER2
    assert {key[0] for key in matrix} == {interactive, machine, sdk, tier2}
    assert matrix[(interactive, "prompt", "content")] == 1
    assert matrix[(interactive, "thinking", "marker:not_captured_for_runtime")] == 1
    assert matrix[(machine, "thinking", "marker:policy_excluded_machine_wide")] == 1
    assert matrix[(machine, "output", "content")] == 1
    assert matrix[(sdk, "prompt", "content")] == 1
    assert matrix[(sdk, "tool_calls", "marker:not_captured_for_runtime")] == 1
    assert matrix[(tier2, "thinking", "content")] == 1
    assert matrix[(tier2, "tool_calls", "content")] == 1
    # Every (runtime, kind) cell accounted for: 4 origins × 4 kinds.
    assert sum(matrix.values()) == 16


def test_backfill_live_four_origins_pushes_the_expected_rows(
    four_origin_rows, cursor_dir, claude_dir, agent_records
):
    pushed = []

    def fake_select(table, params=None, url=None, service_role_key=None):
        return []  # fresh table: nothing to short-circuit against

    def fake_upsert(table, rows, on_conflict=None, url=None, service_role_key=None):
        assert table == "factory_agent_artifacts"
        assert on_conflict == "id"
        pushed.extend(rows)
        return rows

    with patch.object(graph_artifacts, "select", side_effect=fake_select), patch.object(
        graph_artifacts, "upsert", side_effect=fake_upsert
    ):
        matrix, processed, failed = artifacts_backfill.backfill(
            four_origin_rows,
            agent_records=agent_records,
            cursor_transcripts_dir=cursor_dir,
            claude_projects_dir=claude_dir,
            live=True,
        )

    assert (processed, failed) == (4, 0)
    assert len(pushed) == 16  # 4 agents × 4 kinds, no skips on a fresh table
    assert len({r["id"] for r in pushed}) == 16  # all ids distinct
    by_agent = {}
    for r in pushed:
        by_agent.setdefault(r["agent_id"], []).append(r)
    assert all(len(rows) == 4 for rows in by_agent.values())
    machine_thinking = [
        r
        for r in pushed
        if r["agent_id"] == "20000000-0000-0000-0000-000000000002"
        and r["artifact_kind"] == "thinking"
    ]
    assert machine_thinking[0]["unavailable_reason"] == "policy_excluded_machine_wide"
    assert machine_thinking[0]["content"] is None
