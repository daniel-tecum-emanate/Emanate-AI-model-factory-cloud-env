"""factory-graph-v2 RIGOR round 2 (workflow) — ADVERSARIAL tests for the
coverage-round-2 additions the base parent-edge suite (test_parent_edges.py,
16 tests) does not cover: label spoofing via a mentioned (non-identity) label,
token-boundary attacks incl. the window-cut fabricating a boundary,
unicode zero-width/bidi obfuscation, cross-transcript-home double matches,
goal-text regex case/punctuation variants, never-downgrade under HIGH
confidence, full-pass idempotency of the PATCH set, and 401/500 PATCH
failures mid-pass. Mocked REST throughout — no live network, temp-dir
fixtures only.

Three of these tests exposed real bugs in `_is_spawn_query` (fixed in the
same session, 2026-08-04): (1) a spawn window naming ANOTHER agent first
still matched a merely-mentioned target label; (2) cutting the query at
SPAWN_LABEL_WINDOW fabricated a token boundary, so "…GRAPH-V2|-EXTENDED"
matched GRAPH-V2; (3) zero-width characters adjacent to the label acted as
token boundaries, so "GRAPH-V2<ZWSP>EXTENDED" matched GRAPH-V2.
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
PARENT_ID = "40000000-0000-0000-0000-000000000001"
CHILD_ID = "40000000-0000-0000-0000-000000000002"
OTHER_ID = "40000000-0000-0000-0000-000000000003"

UUID_A = "11111111-2222-3333-4444-555555555555"
UUID_B = "66666666-7777-8888-9999-aaaaaaaaaaaa"

WINDOW = graph_artifacts.SPAWN_LABEL_WINDOW


def _jsonl(rows):
    return "\n".join(json.dumps(r) for r in rows) + "\n"


def _cursor_rows(prompt, answer="DONE."):
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


def _write_cursor(base, session_uuid, prompt, subdir=None):
    d = Path(base) / session_uuid if subdir is None else Path(base) / subdir / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{session_uuid}.jsonl"
    path.write_text(_jsonl(_cursor_rows(prompt)))
    return path


def _write_claude(base, slug, session_id, first_text, answer="CLAUDE DONE."):
    d = Path(base) / slug
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{session_id}.jsonl"
    path.write_text(
        _jsonl(
            [
                {"type": "user", "message": {"content": first_text}},
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": answer}]},
                },
            ]
        )
    )
    return path


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


# ---------------------------------------------------------------------------
# Label spoofing — a mentioned label is not an identity
# ---------------------------------------------------------------------------


def test_label_spoofing_never_attributes_a_mentioned_label(tmp_path):
    """One transcript, two labels inside the 160-char spawn window: it is
    GRAPH-AUDIT's own spawn prompt and merely MENTIONS GRAPH-V2. Scanning for
    GRAPH-V2 must refuse — the identity position (the first label-shaped
    token after "You are") belongs to someone else.
    """
    base = tmp_path / "cursor-transcripts"
    _write_cursor(
        base,
        UUID_A,
        "You are GRAPH-AUDIT, a review subagent. Coordinate with GRAPH-V2 "
        "and report back.",
    )
    index = graph_artifacts.build_first_query_index(base, "/nonexistent-claude")
    assert len(index) == 1
    assert "GRAPH-V2" in index[0]["query"][:WINDOW]  # attack precondition

    for _ in range(3):  # stable across runs, never nondeterministic
        assert (
            graph_artifacts.match_transcript_by_spawn_label("GRAPH-V2", index) is None
        )
    # The identity label itself still matches — it IS that agent's transcript.
    matched = graph_artifacts.match_transcript_by_spawn_label("GRAPH-AUDIT", index)
    assert matched is not None and matched[1] == "cursor"

    # Through locate_sources the spoofed row honestly stays source-less.
    src = graph_artifacts.locate_sources(
        _row(title="GRAPH-V2", goal="graph work"),
        graph_artifacts.RUNTIME_INTERACTIVE,
        agent_records=[],
        cursor_transcripts_dir=base,
        claude_projects_dir="/nonexistent-claude",
    )
    assert src["transcript_path"] is None


def test_crossed_pair_of_spawn_prompts_attributes_each_to_its_identity(tmp_path):
    """Two transcripts each naming BOTH labels (the real corpus shape:
    'You are CHILD under PARENT'): each label resolves to exactly the
    transcript whose identity position it holds — never the other one.
    """
    base = tmp_path / "cursor-transcripts"
    _write_cursor(
        base, UUID_A, "You are GRAPH-AUDIT, working with GRAPH-V2 on the graph."
    )
    _write_cursor(
        base, UUID_B, "You are GRAPH-V2, a subagent spawned by GRAPH-AUDIT."
    )
    index = graph_artifacts.build_first_query_index(base, "/nonexistent-claude")
    audit = graph_artifacts.match_transcript_by_spawn_label("GRAPH-AUDIT", index)
    v2 = graph_artifacts.match_transcript_by_spawn_label("GRAPH-V2", index)
    assert audit[0].stem == UUID_A
    assert v2[0].stem == UUID_B


# ---------------------------------------------------------------------------
# Token-boundary attacks
# ---------------------------------------------------------------------------


def test_token_boundaries_reject_superset_prefix_and_separator_variants():
    # Superset name: GRAPH-V2 is not GRAPH-V2-EXTENDED.
    q = "You are GRAPH-V2-EXTENDED, the extended graph subagent."
    assert not graph_artifacts._is_spawn_query(q, "GRAPH-V2")
    assert graph_artifacts._is_spawn_query(q, "GRAPH-V2-EXTENDED")
    # Prefixed name: MYGRAPH-V2 is not GRAPH-V2.
    assert not graph_artifacts._is_spawn_query(
        "You are MYGRAPH-V2, something else.", "GRAPH-V2"
    )
    # Hyphen/underscore are NOT interchangeable, in either direction.
    q_underscore = "You are GRAPH_V2, the underscore variant."
    assert not graph_artifacts._is_spawn_query(q_underscore, "GRAPH-V2")
    assert graph_artifacts._is_spawn_query(q_underscore, "GRAPH_V2")
    assert not graph_artifacts._is_spawn_query(
        "You are GRAPH-V2, the hyphen variant.", "GRAPH_V2"
    )
    # Underscore-suffixed superset: GRAPH-V2_SUB is not GRAPH-V2.
    assert not graph_artifacts._is_spawn_query(
        "You are GRAPH-V2_SUB, suffixed.", "GRAPH-V2"
    )


def test_spawn_window_boundary_at_char_160_vs_161():
    label = "EDGE-B1"
    prefix = "You are the agent "
    filler = "f" * (WINDOW - len(prefix) - len(label) - 1)
    q_in = f"{prefix}{filler} {label} plus trailing mission text."
    assert q_in.index(label) + len(label) == WINDOW  # ends exactly at char 160
    assert graph_artifacts._is_spawn_query(q_in, label)

    q_out = f"{prefix}f{filler} {label} plus trailing mission text."
    assert q_out.index(label) + len(label) == WINDOW + 1  # ends at char 161
    assert not graph_artifacts._is_spawn_query(q_out, label)


def test_window_truncation_cannot_fabricate_a_token_boundary():
    """The window cut landing exactly after "…GRAPH-V2" of GRAPH-V2-EXTENDED
    must not turn the truncated prefix into a token match — boundaries are
    facts of the full text, not of the cut.
    """
    label = "GRAPH-V2"
    prefix = "You are "
    filler = "p" * (WINDOW - len(prefix) - len(label) - 1)
    q = f"{prefix}{filler} {label}-EXTENDED, more mission text."
    assert q[:WINDOW].endswith(label)  # attack precondition: cut mid-name
    assert not graph_artifacts._is_spawn_query(q, label)


def test_unicode_zero_width_and_bidi_characters_cannot_split_or_splice(tmp_path):
    # Zero-width space splicing two names: GRAPH-V2<ZWSP>EXTENDED is ONE
    # (obfuscated) name, not a GRAPH-V2 token.
    assert not graph_artifacts._is_spawn_query(
        "You are GRAPH-V2\u200bEXTENDED, spliced.", "GRAPH-V2"
    )
    # Zero-width characters INSIDE the label are stripped before matching —
    # an obfuscated occurrence of the label is still that label.
    assert graph_artifacts._is_spawn_query(
        "You are GRA\u200bPH-V2, obfuscated.", "GRAPH-V2"
    )
    # RTL override / pop wrapped around the token: format characters never
    # break the match or crash the scan, and behavior is deterministic.
    q_rtl = "You are \u202eGRAPH-V2\u202c, the wrapped one."
    for _ in range(3):
        assert graph_artifacts._is_spawn_query(q_rtl, "GRAPH-V2")
    # End-to-end through the index: the spliced name never matches.
    base = tmp_path / "cursor-transcripts"
    _write_cursor(base, UUID_A, "You are GRAPH-V2\u200bEXTENDED, spliced.")
    index = graph_artifacts.build_first_query_index(base, "/nonexistent-claude")
    assert graph_artifacts.match_transcript_by_spawn_label("GRAPH-V2", index) is None


# ---------------------------------------------------------------------------
# Cross-home double matches (Cursor + Claude project slugs)
# ---------------------------------------------------------------------------


def test_two_homes_both_matching_refuse_deterministically(tmp_path, caplog):
    """The same label spawn-matches one Cursor transcript AND one Claude
    transcript (different sessions): two distinct stems → refusal, stable
    across repeated index rebuilds. (Rule as implemented: dedup is by session
    stem; distinct stems are distinct sessions regardless of home.)
    """
    cursor_base = tmp_path / "cursor"
    claude_base = tmp_path / "claude"
    text = "You are CROSS-X, a dual-home subagent."
    _write_cursor(cursor_base, UUID_A, text)
    _write_claude(claude_base, "-Users-x-somewhere", UUID_B, text)
    results = set()
    for _ in range(3):
        index = graph_artifacts.build_first_query_index(cursor_base, claude_base)
        results.add(graph_artifacts.match_transcript_by_spawn_label("CROSS-X", index))
    assert results == {None}
    assert any("refusing to guess" in r.getMessage() for r in caplog.records)


def test_same_stem_across_homes_dedups_and_prefers_cursor_deterministically(tmp_path):
    """When the two homes carry the SAME session stem, the dedup rule counts
    them as one session and the preference is deterministic (index order:
    cursor entries precede claude; the subagents-first sort is stable) —
    asserted stable across repeated rebuilds.
    """
    cursor_base = tmp_path / "cursor"
    claude_base = tmp_path / "claude"
    text = "You are CROSS-Y, a twin-stem subagent."
    _write_cursor(cursor_base, UUID_A, text)
    _write_claude(claude_base, "-Users-x-somewhere", UUID_A, text)
    outcomes = set()
    for _ in range(5):
        index = graph_artifacts.build_first_query_index(cursor_base, claude_base)
        outcomes.add(
            graph_artifacts.match_transcript_by_spawn_label("CROSS-Y", index)
        )
    assert len(outcomes) == 1  # never nondeterministic
    path, kind = outcomes.pop()
    assert kind == "cursor"


# ---------------------------------------------------------------------------
# Goal-text regex — case variants, trailing punctuation, duplicate titles
# ---------------------------------------------------------------------------


def test_goal_text_case_variants_trailing_punctuation_and_duplicate_titles():
    parent = _row(id=PARENT_ID, title="MF-PARENT", goal="orchestrate")

    def edges_for(goal):
        return graph_artifacts.derive_parent_edges(
            [parent, _row(goal=goal)], runs_dir="/nonexistent-runs"
        )

    # The design regex is the lowercase machine-written convention, verbatim —
    # case variants of the keyword never parse (conservative, documented).
    assert edges_for("build Subagent of MF-PARENT") == {}
    assert edges_for("BUILD SUBAGENT OF MF-PARENT") == {}
    # The optional hyphen IS part of the design regex.
    assert edges_for("a sub-agent of MF-PARENT")[CHILD_ID]["parent_id"] == PARENT_ID
    # Trailing punctuation backtracks out of the label capture.
    for goal in ("subagent of MF-PARENT.", "subagent of MF-PARENT), phase 2"):
        assert edges_for(goal)[CHILD_ID]["parent_id"] == PARENT_ID
    # Two rows carrying the named title → no-write, both directions of order.
    dup = [
        _row(id=PARENT_ID, title="MF-PARENT", goal="a"),
        _row(id=OTHER_ID, title="MF-PARENT", goal="b"),
        _row(goal="cleanup subagent of MF-PARENT."),
    ]
    assert graph_artifacts.derive_parent_edges(dup, runs_dir="/nonexistent-runs") == {}
    assert (
        graph_artifacts.derive_parent_edges(dup[::-1], runs_dir="/nonexistent-runs")
        == {}
    )


# ---------------------------------------------------------------------------
# Never-downgrade under concurrency shapes
# ---------------------------------------------------------------------------


def test_high_confidence_edge_still_never_overwrites_existing_parent(caplog):
    """The module's rule is UNCONDITIONAL never-overwrite: even a HIGH
    containment edge does not displace an existing different parent (the
    stored value may be a dispatch-time recording). HIGH-over-MEDIUM
    overwrite is NOT implemented — this asserts the actual conservative
    behavior.
    """
    rows = [
        _row(id=PARENT_ID, title="MF-PARENT", goal="x"),
        _row(id=OTHER_ID, title="MF-OTHER", goal="y"),
        _row(parent_agent_id=OTHER_ID),  # already set — to someone else
    ]
    edges = {
        CHILD_ID: {
            "parent_id": PARENT_ID,
            "confidence": "high",
            "basis": "transcript_folder_containment",
        }
    }
    with patch.object(graph_artifacts, "update") as mock_update:
        summary = graph_artifacts.push_parent_edges(edges, rows)
    mock_update.assert_not_called()
    assert summary == {
        "high": 0,
        "medium": 0,
        "unchanged": 0,
        "conflict_skipped": 1,
        "patch_failed": 0,
    }
    assert any("never-downgrade" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Idempotency of the full edges pass
# ---------------------------------------------------------------------------


def test_edges_pass_twice_identical_patch_set_then_zero():
    parent = _row(id=PARENT_ID, title="MF-PARENT", goal="orchestrate")
    c1 = _row(id=CHILD_ID, title="CHILD-1", goal="subagent of MF-PARENT — worker one")
    c2 = _row(id=OTHER_ID, title="CHILD-2", goal="subagent of MF-PARENT — worker two")
    base_rows = [parent, c1, c2]

    # Derivation itself is deterministic on identical fixtures.
    d1 = graph_artifacts.derive_parent_edges(base_rows, runs_dir="/nonexistent-runs")
    d2 = graph_artifacts.derive_parent_edges(base_rows, runs_dir="/nonexistent-runs")
    assert d1 == d2

    state = {r["id"]: None for r in base_rows}
    patches = []

    def fake_select(table, params=None, url=None, service_role_key=None):
        return [dict(r, parent_agent_id=state[r["id"]]) for r in base_rows]

    def fake_update(table, filters, payload, url=None, service_role_key=None):
        child = filters["id"][len("eq.") :]
        patches.append((child, payload["parent_agent_id"]))
        state[child] = payload["parent_agent_id"]

    with patch.object(graph_artifacts, "select", side_effect=fake_select), patch.object(
        graph_artifacts, "update", side_effect=fake_update
    ):
        s1 = graph_artifacts.sync_parent_edges(runs_dir="/nonexistent-runs")
        first_patches = list(patches)
        s2 = graph_artifacts.sync_parent_edges(runs_dir="/nonexistent-runs")

    # First pass: exactly the derived edge set, in deterministic (sorted) order.
    assert first_patches == [(CHILD_ID, PARENT_ID), (OTHER_ID, PARENT_ID)]
    assert s1["medium"] == 2 and s1["patch_failed"] == 0
    # Second pass on the now-identical state: ZERO new PATCHes.
    assert patches == first_patches
    assert s2 == {
        "high": 0,
        "medium": 0,
        "unchanged": 2,
        "conflict_skipped": 0,
        "patch_failed": 0,
    }


# ---------------------------------------------------------------------------
# REST 401/500 mid-pass
# ---------------------------------------------------------------------------

_PATCH_FAILURES = [
    'update factory_agents failed: 401 {"message":"Invalid API key"}',
    'update factory_agents failed: 500 {"message":"internal error"}',
]


@pytest.mark.parametrize("error_text", _PATCH_FAILURES, ids=["401", "500"])
def test_patch_401_500_one_warning_and_artifacts_pass_unaffected(error_text, caplog):
    """A 401/500 on the parent-edge PATCH mid-pass: exactly ONE warning, the
    edges pass stops (the module's stop-after-first-failure rule — the
    dominant cause is target-wide), and the artifacts pass that already ran
    for every agent is untouched.
    """
    parent = _row(id=PARENT_ID, title="MF-PARENT", goal="orchestrate")
    c1 = _row(id=CHILD_ID, title="CHILD-1", goal="subagent of MF-PARENT — one")
    c2 = _row(id=OTHER_ID, title="CHILD-2", goal="subagent of MF-PARENT — two")

    def fake_select(table, params=None, url=None, service_role_key=None):
        if table == "factory_agents":
            return [parent, c1, c2]
        return []  # empty artifacts table

    pushed = []

    def fake_upsert(table, rows, on_conflict=None, url=None, service_role_key=None):
        pushed.extend(rows)
        return rows

    def boom(*a, **k):
        raise SupabaseRestError(error_text)

    with patch.object(graph_artifacts, "select", side_effect=fake_select), patch.object(
        graph_artifacts, "upsert", side_effect=fake_upsert
    ), patch.object(graph_artifacts, "update", side_effect=boom) as mock_update:
        processed = graph_artifacts.sync_agent_artifacts(
            agent_records=[],
            cursor_transcripts_dir="/nonexistent-cursor",
            claude_projects_dir="/nonexistent-claude",
        )

    # Every agent's artifacts were processed despite the edges-pass failure.
    assert processed == 3
    assert {r["agent_id"] for r in pushed} == {PARENT_ID, CHILD_ID, OTHER_ID}
    # One PATCH attempt, one warning, then stop — no retry spam, no raise.
    assert mock_update.call_count == 1
    warnings = [
        r for r in caplog.records if "parent_agent_id PATCH failed" in r.getMessage()
    ]
    assert len(warnings) == 1
