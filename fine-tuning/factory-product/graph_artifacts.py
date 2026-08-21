#!/usr/bin/env python3
"""graph_artifacts — the agent-artifact capture pass (factory-graph-v2, research/03).

Extracts each `factory_agents` row's four artifacts — PROMPT, TOOL-CALL LOG,
CHAIN OF THOUGHT (thinking), OUTPUT — from their real on-disk sources (Cursor
agent transcripts, Claude Code transcripts, dispatch prompt files, the agent
ledger) and projects them into `factory_agent_artifacts`, one row per
(agent, kind, seq).

The honesty mechanism (design §2.3): every processed agent gets ALL FOUR kinds
written — real content where the runtime produces it, or `content NULL` +
`unavailable_reason` where it does not. Absence of a row means "not yet
synced", never "not captured".

Privacy (design §5, decision V-126 D1/D2 approved 2026-08-04):
- redaction runs ON-LAPTOP, before upload, through the single shared
  `scripts/lib/redact.py`; its never-raises contract maps an internal failure
  to WITHHOLDING the content (`unavailable_reason='redaction_error_withheld'`)
  rather than shipping it;
- embedded filesystem paths go through the same `factory_sync._redact_path`
  allow-list the Fleet tab's `files_touched` already uses;
- machine-wide (`run_id` NULL) sessions never sync `thinking` (design §5.3
  allow-list, default-closed) — those rows get
  `unavailable_reason='policy_excluded_machine_wide'`, a reason value this
  module ADDS to the design §2.2 enum so the policy exclusion is
  distinguishable from a runtime that genuinely cannot produce CoT.

Fail direction: OPEN, unconditionally (PRRules rule 5 — telemetry). The
`factory_agent_artifacts` table ships from the platform repo in parallel; until
that migration lands, every push 404s (PostgREST 42P01) and this module logs
ONE warning per pass and returns, never blocking the poll.

Idempotency: deterministic uuid5 ids from the shared imported
`backfill_ptc_history.NAMESPACE` (never redefined), key format per design §4.3:
`factory_agent_artifacts:<agent_id>:<kind>:<seq>`. A `content_sha256`
short-circuit skips the upsert for unchanged artifacts.

Usage (also wired into `agents_sync_job.sync_all_open_runs`):
    python3 graph_artifacts.py            # dry-run (default): classify/extract/report
    python3 graph_artifacts.py --live     # extract AND upsert
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKFLOW_ROOT = HERE.parent.parent
LEDGER_AGENTS_DIR = WORKFLOW_ROOT / "ledger" / "agents"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WORKFLOW_ROOT / "scripts" / "lib"))

# The ONE uuid5 universe (design §4.3): imported, never redefined.
from backfill_ptc_history import NAMESPACE  # noqa: E402
from factory_sync import _redact_path  # noqa: E402
from redact import redact  # noqa: E402
from supabase_rest import SupabaseRestError, select, update, upsert  # noqa: E402

log = logging.getLogger("graph_artifacts")

EXTRACTION_VERSION = 1

ARTIFACT_KINDS = ("prompt", "tool_calls", "thinking", "output")

# Runtime classification (design §3 sync-projection discriminators, documented
# in data-model/factory_agents.md "three/four origins" tables).
RUNTIME_CURSOR_SDK = "cursor_sdk_dispatch"
RUNTIME_TIER2 = "tier2_dispatch"
RUNTIME_MACHINE_WIDE = "machine_wide_session"
RUNTIME_INTERACTIVE = "interactive_run_session"

# unavailable_reason vocabulary. The first four are design §2.2's enum;
# 'policy_excluded_machine_wide' is this module's documented addition (§5.3's
# machine-wide CoT default-OFF is a POLICY choice, not a runtime incapability,
# and collapsing the two into 'not_captured_for_runtime' would lie about which
# one applied).
REASON_NOT_CAPTURED = "not_captured_for_runtime"
REASON_NOT_APPLICABLE = "not_applicable_deterministic"
REASON_SOURCE_MISSING = "source_file_missing"
REASON_REDACTION_ERROR = "redaction_error_withheld"
REASON_POLICY_MACHINE_WIDE = "policy_excluded_machine_wide"

# `source` vocabulary (design §2.2). Two documented additions:
# - 'agent_row': content copied from the already-synced factory_agents row
#   (final_response/report_summary) when no better source exists;
# - 'none': marker rows where no source file was consulted at all.
SOURCE_CURSOR_TRANSCRIPT = "cursor_transcript"
SOURCE_CURSOR_LEDGER = "cursor_ledger"
SOURCE_CLAUDE_TRANSCRIPT = "claude_transcript"
SOURCE_DISPATCH_PROMPT = "dispatch_prompt_file"
SOURCE_TIER2_LEDGER = "tier2_ledger"
SOURCE_SCRIPT_CONFIG = "script_config"
SOURCE_AGENT_ROW = "agent_row"
SOURCE_NONE = "none"

# Caps (design §2.4): 512 KB redacted-content cap; over-cap keeps head 384 KB
# + tail 96 KB with an elision marker. v1 writes single-chunk (seq=0) rows —
# the key shape keeps `seq` for chunking headroom.
CONTENT_CAP_BYTES = 512 * 1024
HEAD_BYTES = 384 * 1024
TAIL_BYTES = 96 * 1024
ELISION_MARKER = (
    "\n\n[… ELIDED — content exceeded the 512 KB artifact cap; "
    "head 384 KB + tail 96 KB kept (design §2.4) …]\n\n"
)

# Correlation (design §4.2): prompt-prefix matching between a Cursor
# transcript's first user_query and the ledger record's (redacted, capped)
# prompt, inside a ±5-minute timestamp window when both sides carry a usable
# timestamp. Collisions resolve to NO-MATCH, conservatively.
PROMPT_PREFIX_CHARS = 200
MATCH_WINDOW_S = 300

# The redact.py never-raises contract's own failure marker — its presence in
# "redacted" output means the redaction itself failed, so the content must be
# withheld, never shipped (design §5.2 item 1).
_REDACTION_ERROR_MARK = "[REDACTION-ERROR"

_USER_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)

# Absolute filesystem paths embedded in artifact text (tool inputs are full of
# them). Each token is normalized (home → ~, repo-relative when inside the
# workflow root) and passed through the same `_redact_path` allow-list as
# `files_touched`; ~/.claude and ~/.cursor transcript homes are additionally
# allowed because they are this pipeline's own documented sources.
_ABS_PATH_RE = re.compile(r"/(?:Users|home)/[^\s\"'<>|)\]},]+")

_HOME = str(Path.home())


def default_cursor_transcripts_dir(workflow_root=WORKFLOW_ROOT):
    """Cursor keys its per-project folder by the flattened workspace path."""
    slug = str(Path(workflow_root)).strip("/").replace("/", "-")
    return Path(_HOME) / ".cursor" / "projects" / slug / "agent-transcripts"


def default_claude_projects_dir():
    return Path(_HOME) / ".claude" / "projects"


# =============================================================================
# Runtime classification + source location
# =============================================================================


def classify_runtime(agent_row):
    """The four writer-path discriminators (data-model/factory_agents.md):
    `cursor_agent_id` non-null → Cursor-SDK dispatch; `stage` non-null →
    tier2-dispatched node-agent; `run_id` NULL → machine-wide session; else
    interactive session on a run. (The design's fifth runtime — deterministic
    script "agents" — has no `factory_agents` writer today, so no row can
    classify to it; `project_findings.py` events are its record instead.)
    """
    if agent_row.get("cursor_agent_id"):
        return RUNTIME_CURSOR_SDK
    if agent_row.get("stage"):
        return RUNTIME_TIER2
    if agent_row.get("run_id") is None:
        return RUNTIME_MACHINE_WIDE
    return RUNTIME_INTERACTIVE


def _read_jsonl(path):
    rows = []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def load_agent_records(agents_dir=None, days=3):
    """Recent `ledger/agents/*.jsonl` records (Cursor generations + Claude Code
    subagent spawns) — the correlation substrate of design §4.2.
    """
    directory = Path(agents_dir) if agents_dir else LEDGER_AGENTS_DIR
    if not directory.is_dir():
        return []
    paths = sorted(p for p in directory.glob("*.jsonl") if p.name[:4].isdigit())
    records = []
    for path in paths[-days:]:
        records.extend(_read_jsonl(path))
    return records


def _parse_ts(raw):
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _normalize_prompt(text, limit=PROMPT_PREFIX_CHARS):
    return " ".join((text or "").split())[:limit]


def _claude_transcript_for(session_id, claude_projects_dir):
    """`~/.claude/projects/<slug>/<session_id>.jsonl` — the slug encodes the
    cwd the claude process ran under, so glob across all project slugs.
    """
    if not session_id:
        return None
    directory = Path(claude_projects_dir)
    if not directory.is_dir():
        return None
    matches = sorted(directory.glob(f"*/{session_id}.jsonl"))
    return matches[0] if matches else None


def _iter_cursor_transcripts(cursor_dir):
    """Top-level session transcripts plus the subagents/ folder (design §1b:
    `<parent>/subagents/<child>.jsonl` is folder-containment evidence).
    """
    directory = Path(cursor_dir)
    if not directory.is_dir():
        return
    yield from sorted(directory.glob("*/*.jsonl"))
    yield from sorted(directory.glob("*/subagents/*.jsonl"))


def _cursor_first_query(path):
    """First user turn's query text — cheap: reads only the first parseable
    user line, not the whole (possibly 500 KB+) transcript.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if row.get("role") != "user":
                    continue
                for block in (row.get("message") or {}).get("content") or []:
                    if block.get("type") == "text" and block.get("text"):
                        text = block["text"]
                        match = _USER_QUERY_RE.search(text)
                        return match.group(1) if match else text
                return None
    except OSError:
        return None
    return None


def _match_cursor_transcript(record, cursor_dir):
    """Design §4.2 method 1: normalized prompt-prefix match between the ledger
    record's (redacted, 4,000-char-capped) prompt and a transcript's first
    user_query, disambiguated by the ±5-minute window on the transcript
    folder's birth/modify time when the record carries a timestamp. Collisions
    resolve to no-match. (`generation_uuid` never equals the transcript uuid —
    verified live 2026-08-04: 0/74 of today's records match a folder name —
    so the prefix match is the only join that exists.)
    """
    prefix = _normalize_prompt(record.get("prompt"))
    if not prefix or _REDACTION_ERROR_MARK in prefix:
        return None
    record_ts = _parse_ts(record.get("ts"))
    candidates = []
    for path in _iter_cursor_transcripts(cursor_dir):
        query = _cursor_first_query(path)
        if query is None:
            continue
        if _normalize_prompt(query) != prefix:
            continue
        candidates.append(path)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1 and record_ts is not None:
        # Secondary filter only: transcript files carry no start timestamp, so
        # the window uses the file's own stat times (birthtime ~ session start
        # on APFS; mtime = last write). Still ambiguous → no-match.
        window = timedelta(seconds=MATCH_WINDOW_S)
        narrowed = []
        for path in candidates:
            try:
                stat = path.stat()
            except OSError:
                continue
            born = datetime.fromtimestamp(
                getattr(stat, "st_birthtime", stat.st_mtime)
            ).astimezone()
            if abs(born - record_ts) <= window:
                narrowed.append(path)
        if len(narrowed) == 1:
            return narrowed[0]
    if len(candidates) > 1:
        log.warning(
            "graph_artifacts: %d transcripts share the same prompt prefix — "
            "refusing to guess (conservative no-match, design §4.2)",
            len(candidates),
        )
    return None


def _match_record_by_label(label, agent_records):
    """Design §4.2 method 3: the heartbeat label appears verbatim in the spawn
    prompt for every observed subagent ("You are PTC-S4-INPUTS…"). Exactly one
    matching record is a match; zero or several is honestly none.
    """
    if not label:
        return None
    matches = [
        record
        for record in agent_records
        if isinstance(record.get("prompt"), str) and label in record["prompt"]
    ]
    return matches[0] if len(matches) == 1 else None


# --- Spawn-label transcript scan (coverage round 2, 2026-08-04) -------------
#
# Design §4.2 rule 3 applied DIRECTLY to transcripts, not only to ledger
# records: every observed subagent's spawn prompt names its label in the very
# first words ("You are LABEL-X…", "You are subagent LABEL-X under …",
# "You are the LABEL-X subagent…"). Two conservative discriminators keep this
# from being a guess:
#   1. the transcript's FIRST user query must start with "You are";
#   2. the label must appear (token-bounded) inside the first
#      SPAWN_LABEL_WINDOW characters — a label merely MENTIONED later in a
#      mission body never matches.
# Exactly one unique transcript (dedup by session-file stem — the same session
# file legitimately appears both top-level and under its parent's subagents/
# folder) is a match; anything else resolves to no-match.
SPAWN_QUERY_PREFIX = "You are"
SPAWN_LABEL_WINDOW = 160
SPAWN_INDEX_CHARS = 400

# Unicode format characters (zero-width space/joiners, bidi embeddings and
# overrides, word joiner, BOM): stripped before spawn matching so they can
# neither break a legitimate label token apart nor fabricate a token boundary
# inside a longer name ("GRAPH-V2<ZWSP>EXTENDED" is not GRAPH-V2). Added in
# rigor round 2, 2026-08-04.
_FORMAT_CHARS_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")

# Label-shaped token: the ALL-CAPS hyphen/underscore convention every observed
# heartbeat label follows (MF-PTC-RETRAIN-EXEC, CHILD-X, GRAPH-V2, …). Used
# only by the identity-position rule in `_is_spawn_query` to detect a spawn
# window whose identity is SOMEONE ELSE.
_LABEL_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)+(?![A-Za-z0-9_-])"
)


def _claude_first_user_text(path):
    """First user turn's text in a Claude Code transcript (the spawn/session
    prompt). Skips summary/meta lines; reads no further than the first user
    turn.
    """
    for role, blocks in _block_lists(path, "claude"):
        if role != "user":
            continue
        for block in blocks:
            if block.get("type") == "text" and block.get("text"):
                return block["text"]
        return None
    return None


def build_first_query_index(cursor_transcripts_dir=None, claude_projects_dir=None):
    """[{path, kind, stem, query}] over every on-disk transcript's first user
    query — the substrate of the spawn-label scan. Cursor transcripts come
    from the given per-project dir (top-level + subagents/); Claude transcripts
    are globbed ACROSS project slugs (the slug encodes the session's cwd, which
    is not knowable from the agent row — same rationale as
    `_claude_transcript_for`), including their subagents/ folders.
    """
    cursor_dir = (
        Path(cursor_transcripts_dir)
        if cursor_transcripts_dir
        else default_cursor_transcripts_dir()
    )
    claude_dir = (
        Path(claude_projects_dir) if claude_projects_dir else default_claude_projects_dir()
    )
    entries = []
    for path in _iter_cursor_transcripts(cursor_dir):
        query = _cursor_first_query(path)
        if query:
            entries.append(
                {
                    "path": path,
                    "kind": "cursor",
                    "stem": path.stem,
                    "query": " ".join(query.split())[:SPAWN_INDEX_CHARS],
                }
            )
    if claude_dir.is_dir():
        claude_paths = sorted(claude_dir.glob("*/*.jsonl")) + sorted(
            claude_dir.glob("*/*/subagents/*.jsonl")
        )
        for path in claude_paths:
            query = _claude_first_user_text(path)
            if query:
                entries.append(
                    {
                        "path": path,
                        "kind": "claude",
                        "stem": path.stem,
                        "query": " ".join(query.split())[:SPAWN_INDEX_CHARS],
                    }
                )
    return entries


def _is_spawn_query(query, label):
    """True when `query` reads as this label's spawn prompt (see the two
    discriminators documented above), with three hardening rules added by
    rigor round 2 (2026-08-04) after adversarial tests proved false matches:

    - Unicode format characters are stripped before matching — they must
      neither split a real label token nor splice a boundary into a longer
      name ("GRAPH-V2<ZWSP>EXTENDED" is not GRAPH-V2);
    - token boundaries are evaluated against the FULL query and the match
      must END inside the window — truncating at the window edge must never
      fabricate a boundary ("…GRAPH-V2|-EXTENDED" is not GRAPH-V2);
    - identity position: a spawn prompt names its OWN agent first, so any
      other label-shaped token BEFORE the target inside the window means
      this is someone else's spawn prompt that merely MENTIONS the target
      ("You are CHILD-X … subagent of MF-PARENT" must never match
      MF-PARENT).
    """
    query = _FORMAT_CHARS_RE.sub("", query)
    if not query.startswith(SPAWN_QUERY_PREFIX):
        return False
    match = re.search(
        r"(?<![A-Za-z0-9_-])" + re.escape(label) + r"(?![A-Za-z0-9_-])", query
    )
    if match is None or match.end() > SPAWN_LABEL_WINDOW:
        return False
    first_label = _LABEL_TOKEN_RE.search(query[:SPAWN_LABEL_WINDOW])
    return first_label is None or first_label.start() >= match.start()


def match_transcript_by_spawn_label(label, index):
    """Exactly one unique transcript whose first query is `label`'s spawn
    prompt → (path, kind); zero or several distinct sessions → None (a missing
    match is honest; a guessed one is not). When the one matched session file
    exists both top-level and under a parent's subagents/ folder, the
    subagents/ path is preferred — it carries the folder-containment evidence
    the parent-edge derivation reads.
    """
    if not label:
        return None
    hits = [entry for entry in index if _is_spawn_query(entry["query"], label)]
    stems = {entry["stem"] for entry in hits}
    if not stems:
        return None
    if len(stems) > 1:
        log.warning(
            "graph_artifacts: %d distinct transcripts read as spawn prompts for "
            "label %s — refusing to guess (conservative no-match, design §4.2)",
            len(stems),
            label,
        )
        return None
    hits.sort(key=lambda e: 0 if e["path"].parent.name == "subagents" else 1)
    return hits[0]["path"], hits[0]["kind"]


def build_session_id_map(labels, agent_records):
    """heartbeat label → ledger session key, via the label-in-prompt join.

    This is the confirmed mapping `factory_sync.project_agents`' existing
    `session_id_map` overlay was built to accept and that `agents_sync_job`
    never fed it (the known gap this PR closes): with it, `_aggregate_ledger`
    can attribute ledger `tool` events to interactive sessions. Claude records
    map by `session_id`; Cursor records by `generation_uuid` (the key Cursor
    `tool` events carry as their `session_id`). Ambiguity maps to absence.
    """
    mapping = {}
    for label in labels:
        record = _match_record_by_label(label, agent_records)
        if not record:
            continue
        key = record.get("session_id") or record.get("generation_uuid")
        if key:
            mapping[label] = str(key)
    return mapping


def locate_sources(
    agent_row,
    runtime,
    agent_records=None,
    cursor_transcripts_dir=None,
    claude_projects_dir=None,
    repo_root=WORKFLOW_ROOT,
    index_cache=None,
):
    """Resolve this agent's on-disk artifact sources (design §4.2).

    Returns a dict:
      transcript_path / transcript_kind ('cursor'|'claude') — when matched;
      prompt_file — the persisted dispatch prompt (runs/<org>/prompts/<node>.md);
      ledger_prompt — capped fallback prompt from the matched Cursor ledger record;
      match_basis — how the join was made, recorded into `meta` for honesty.

    `index_cache` (a plain dict, shared across one sync/backfill pass) memoizes
    the spawn-label first-query index so a whole pass scans the transcript
    homes at most once instead of once per unmatched agent.
    """
    cursor_dir = (
        Path(cursor_transcripts_dir)
        if cursor_transcripts_dir
        else default_cursor_transcripts_dir()
    )
    claude_dir = (
        Path(claude_projects_dir) if claude_projects_dir else default_claude_projects_dir()
    )
    agent_records = agent_records if agent_records is not None else []
    src = {
        "transcript_path": None,
        "transcript_kind": None,
        "prompt_file": None,
        "ledger_prompt": None,
        "match_basis": None,
    }

    # Dispatch prompt file: push_node_agent stores the repo- or factory-relative
    # prompt file path in report_path.
    report_path = agent_row.get("report_path")
    if runtime in (RUNTIME_CURSOR_SDK, RUNTIME_TIER2) and report_path:
        for base in (Path(repo_root), HERE):
            candidate = base / report_path
            if candidate.is_file():
                src["prompt_file"] = candidate
                break
        else:
            candidate = Path(report_path)
            if candidate.is_file():
                src["prompt_file"] = candidate

    # Claude transcript via a real session_id (§4.2 method 2 — HIGH).
    session_id = agent_row.get("ledger_session_id")
    if session_id:
        path = _claude_transcript_for(session_id, claude_dir)
        if path:
            src["transcript_path"] = path
            src["transcript_kind"] = "claude"
            src["match_basis"] = "ledger_session_id"
            return src

    if runtime in (RUNTIME_INTERACTIVE, RUNTIME_MACHINE_WIDE):
        record = _match_record_by_label(agent_row.get("title"), agent_records)
        if record:
            if record.get("session_id"):
                path = _claude_transcript_for(record["session_id"], claude_dir)
                if path:
                    src["transcript_path"] = path
                    src["transcript_kind"] = "claude"
                    src["match_basis"] = "label_in_prompt+session_id"
                    return src
            path = _match_cursor_transcript(record, cursor_dir)
            if path:
                src["transcript_path"] = path
                src["transcript_kind"] = "cursor"
                src["match_basis"] = "label_in_prompt+prompt_prefix"
            else:
                src["match_basis"] = "label_in_prompt(ledger_only)"
            # Ledger prompt is a legitimate capped fallback either way
            # (design §4.6, source='cursor_ledger').
            if isinstance(record.get("prompt"), str):
                src["ledger_prompt"] = record["prompt"]

        # Coverage round 2 fallback: the ledger join failed (no record — the
        # ledger does not capture Task-tool subagent spawns — or several
        # records mention the label) but the transcript itself may still name
        # this agent unambiguously in its spawn prompt. Same §4.2 rule 3, one
        # hop shorter; ambiguity still resolves to no-match inside
        # `match_transcript_by_spawn_label`.
        if src["transcript_path"] is None and agent_row.get("title"):
            index = None
            if index_cache is not None:
                index = index_cache.get("index")
            if index is None:
                index = build_first_query_index(cursor_dir, claude_dir)
                if index_cache is not None:
                    index_cache["index"] = index
            matched = match_transcript_by_spawn_label(agent_row["title"], index)
            if matched:
                src["transcript_path"], src["transcript_kind"] = matched
                src["match_basis"] = "spawn_label_in_transcript"
    return src


# =============================================================================
# Extraction (design §1a matrix)
# =============================================================================


def _block_lists(path, kind):
    """Yield each transcript line's content-block list. Both formats store
    blocks under message.content; claude sometimes stores a plain string for
    user turns (normalized to one text block here).
    """
    del kind
    for row in _read_jsonl(path):
        content = (row.get("message") or {}).get("content")
        role = row.get("role") or row.get("type")
        if isinstance(content, str):
            yield role, [{"type": "text", "text": content}]
        elif isinstance(content, list):
            yield role, content


def _extract_prompt_from_transcript(path, kind):
    for role, blocks in _block_lists(path, kind):
        if role != "user":
            continue
        for block in blocks:
            if block.get("type") == "text" and block.get("text"):
                text = block["text"]
                match = _USER_QUERY_RE.search(text)
                return match.group(1) if match else text
        return None
    return None


def _stringify_result(content):
    """A claude tool_result's content may be a string or a block list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text") for b in content if isinstance(b, dict) and b.get("text")
        ]
        return "\n".join(parts) if parts else json.dumps(content)
    return None if content is None else json.dumps(content)


def _extract_tool_calls(path, kind):
    """Structured JSON array [{ts?, tool, input, result?}] — never raw
    transcript slices (design §4.4). Cursor entries carry result=None honestly
    (tool_result blocks are NOT persisted in that format — design §1b);
    claude entries pair results by tool_use id.
    """
    calls = []
    by_id = {}
    for role, blocks in _block_lists(path, kind):
        for block in blocks:
            btype = block.get("type")
            if btype == "tool_use":
                entry = {
                    "tool": block.get("name"),
                    "input": block.get("input"),
                    "result": None,
                }
                calls.append(entry)
                if block.get("id"):
                    by_id[block["id"]] = entry
            elif btype == "tool_result" and kind == "claude":
                entry = by_id.get(block.get("tool_use_id"))
                if entry is not None:
                    entry["result"] = _stringify_result(block.get("content"))
    return calls


def _extract_thinking(path, kind):
    """Concatenated `thinking` blocks with turn separators — claude only
    (the Cursor format persists zero thinking blocks, design §1b). Returns
    (text_or_None, block_count).
    """
    if kind != "claude":
        return None, 0
    chunks = []
    for role, blocks in _block_lists(path, kind):
        if role != "assistant":
            continue
        for block in blocks:
            if block.get("type") == "thinking" and block.get("thinking"):
                chunks.append(block["thinking"])
    if not chunks:
        return None, 0
    return "\n\n--- [turn] ---\n\n".join(chunks), len(chunks)


def _extract_output(path, kind):
    """Last assistant text block in the transcript."""
    last = None
    for role, blocks in _block_lists(path, kind):
        if role != "assistant":
            continue
        for block in blocks:
            if block.get("type") == "text" and block.get("text"):
                last = block["text"]
    return last


def extract(kind, runtime, src, agent_row):
    """One artifact kind per the §1a runtime×artifact matrix.

    Returns (raw_content, unavailable_reason, source, meta_extra) — exactly one
    of raw_content/unavailable_reason is non-None.
    """
    path = src.get("transcript_path")
    tkind = src.get("transcript_kind")
    transcript_source = (
        SOURCE_CLAUDE_TRANSCRIPT if tkind == "claude" else SOURCE_CURSOR_TRANSCRIPT
    )

    if kind == "prompt":
        if path:
            text = _extract_prompt_from_transcript(path, tkind)
            if text:
                return text, None, transcript_source, {}
        if src.get("prompt_file"):
            try:
                return (
                    Path(src["prompt_file"]).read_text(encoding="utf-8", errors="replace"),
                    None,
                    SOURCE_DISPATCH_PROMPT,
                    {},
                )
            except OSError:
                pass
        if src.get("ledger_prompt"):
            # The ledger's own copy: already redacted + 4,000-char-capped at
            # append time (design §4.6 fallback).
            return src["ledger_prompt"], None, SOURCE_CURSOR_LEDGER, {}
        return None, REASON_SOURCE_MISSING, SOURCE_NONE, {}

    if kind == "tool_calls":
        if path:
            calls = _extract_tool_calls(path, tkind)
            return (
                json.dumps(calls, ensure_ascii=False, indent=1),
                None,
                transcript_source,
                {"tool_call_count": len(calls)},
            )
        if runtime == RUNTIME_CURSOR_SDK:
            # SDK conversation endpoint remains unverified (design VERIFY-2,
            # blocked on the GitHub connector) — the runtime cannot produce a
            # tool log through any exercised path today.
            return None, REASON_NOT_CAPTURED, SOURCE_NONE, {}
        return None, REASON_SOURCE_MISSING, SOURCE_NONE, {}

    if kind == "thinking":
        # §5.3 policy exclusion is enforced BEFORE this function in
        # push_agent_artifacts; reaching here means the scope allows CoT.
        if path and tkind == "claude":
            text, count = _extract_thinking(path, tkind)
            if text:
                return text, None, transcript_source, {"thinking_block_count": count}
            # A real transcript with zero thinking blocks: extended thinking
            # was off for this session — the runtime did not capture CoT.
            return (
                None,
                REASON_NOT_CAPTURED,
                transcript_source,
                {"thinking_block_count": 0},
            )
        if path and tkind == "cursor":
            # The Cursor format persists no thinking blocks, ever (§1b).
            return None, REASON_NOT_CAPTURED, transcript_source, {}
        if runtime == RUNTIME_CURSOR_SDK:
            return None, REASON_NOT_CAPTURED, SOURCE_NONE, {}
        return None, REASON_SOURCE_MISSING, SOURCE_NONE, {}

    if kind == "output":
        if path:
            text = _extract_output(path, tkind)
            if text:
                return text, None, transcript_source, {}
        row_output = agent_row.get("final_response") or agent_row.get("report_summary")
        if row_output:
            # Copied from the already-synced factory_agents row (design §4.6:
            # "already on row → copy into artifact").
            return row_output, None, SOURCE_AGENT_ROW, {}
        return None, REASON_SOURCE_MISSING, SOURCE_NONE, {}

    raise ValueError(f"unknown artifact kind: {kind}")


# =============================================================================
# Redaction + caps
# =============================================================================


def _redact_embedded_path_token(token):
    """One absolute path token → its allow-listed rendering. The two transcript
    homes this pipeline itself reads (~/.claude, ~/.cursor) render home-relative;
    paths inside the workflow root go repo-relative through `_redact_path`'s
    allow-list; everything else is redacted outright.
    """
    if token.startswith(_HOME):
        rel = token[len(_HOME) :].lstrip("/")
        if rel.startswith(".claude/") or rel.startswith(".cursor/"):
            return "~/" + rel
    root = str(WORKFLOW_ROOT)
    if token == root or token.startswith(root + "/"):
        rel = token[len(root) :].lstrip("/")
        return _redact_path(rel) if rel else root
    return "[external path — redacted]"


def _redact_embedded_paths(text):
    return _ABS_PATH_RE.sub(lambda m: _redact_embedded_path_token(m.group(0)), text)


def redact_content(text):
    """The full on-laptop redaction pipeline: shared secret redactor + embedded
    path allow-list. Never raises; any internal failure returns None so the
    caller withholds the artifact (`redaction_error_withheld`) — content that
    skipped redaction must NEVER ship (design §5's top rule).
    """
    try:
        redacted = redact(text)
        if not isinstance(redacted, str) or _REDACTION_ERROR_MARK in redacted:
            return None
        return _redact_embedded_paths(redacted)
    except Exception:  # noqa: BLE001 — withhold on ANY failure, by contract
        return None


def apply_caps(text):
    """(capped_text, truncated) per design §2.4 — head 384 KB + tail 96 KB with
    an elision marker when the redacted content exceeds 512 KB.
    """
    data = text.encode("utf-8")
    if len(data) <= CONTENT_CAP_BYTES:
        return text, False
    head = data[:HEAD_BYTES].decode("utf-8", errors="ignore")
    tail = data[-TAIL_BYTES:].decode("utf-8", errors="ignore")
    return head + ELISION_MARKER + tail, True


def _artifact_id(agent_id, kind, seq):
    """Design §4.3's exact key format, in the ONE shared uuid5 namespace."""
    return str(uuid.uuid5(NAMESPACE, f"factory_agent_artifacts:{agent_id}:{kind}:{seq}"))


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _local_source_pointer(src):
    """`meta.local_source`: the redacted pointer back to full local fidelity."""
    for key in ("transcript_path", "prompt_file"):
        value = src.get(key)
        if value:
            return _redact_embedded_path_token(str(value))
    return None


# =============================================================================
# Row build + push
# =============================================================================


def build_artifact_rows(agent_row, src, runtime):
    """All four kinds' rows for one agent — pure (no network), so tests assert
    on what would be pushed. Every row carries the identical key set
    (PostgREST bulk upserts require uniform shapes, PGRST102).
    """
    agent_id = agent_row.get("id")
    rows = []
    for kind in ARTIFACT_KINDS:
        if kind == "thinking" and runtime == RUNTIME_MACHINE_WIDE:
            # §5.3 allow-list: machine-wide CoT is default-OFF by POLICY.
            # Never extracted, never redacted, never shipped.
            raw, reason, source, extra = None, REASON_POLICY_MACHINE_WIDE, SOURCE_NONE, {}
        else:
            raw, reason, source, extra = extract(kind, runtime, src, agent_row)

        truncated = False
        if raw is not None:
            redacted = redact_content(raw)
            if redacted is None:
                raw, reason, source = None, REASON_REDACTION_ERROR, source
            else:
                raw, truncated = apply_caps(redacted)

        meta = {
            "runtime": runtime,
            "extraction_version": EXTRACTION_VERSION,
            **extra,
        }
        local_source = _local_source_pointer(src)
        if local_source:
            meta["local_source"] = local_source
        if src.get("match_basis"):
            meta["match_basis"] = src["match_basis"]

        rows.append(
            {
                "id": _artifact_id(agent_id, kind, 0),
                "agent_id": agent_id,
                "artifact_kind": kind,
                "seq": 0,
                "content": raw,
                "unavailable_reason": reason,
                "content_sha256": _sha256(raw) if raw is not None else None,
                "size_bytes": len(raw.encode("utf-8")) if raw is not None else None,
                "truncated": truncated,
                "source": source,
                "meta": meta,
            }
        )
    return rows


def _is_missing_table_error(exc):
    """PostgREST's signature for the not-yet-migrated table: 404 with Postgres
    code 42P01 ("relation does not exist"). The migration ships from the
    platform repo in parallel — until then this pass is dormant, not broken.
    """
    text = str(exc)
    return "42P01" in text or "does not exist" in text or " 404 " in text or text.endswith("404")


class MissingTableError(RuntimeError):
    """Raised internally so the per-pass loop can warn ONCE and stop."""


def push_agent_artifacts(
    agent_row,
    agent_records=None,
    cursor_transcripts_dir=None,
    claude_projects_dir=None,
    url=None,
    service_role_key=None,
    dry_run=False,
    index_cache=None,
    src_sink=None,
):
    """Extract + push all four artifact kinds for one agent (design §4.4).

    Idempotent whole-artifact re-upsert with a `content_sha256` short-circuit:
    unchanged artifacts issue no write at all. Raises MissingTableError so the
    caller can stop the pass after one warning; every OTHER failure is the
    caller's to swallow (fail-open telemetry).

    `src_sink`, when given, collects {agent_id: resolved src dict} so the
    caller can feed the parent-edge derivation without re-running correlation.

    Returns {kind: 'content' | 'marker:<reason>' | 'unchanged'} — or None when
    the row has no id yet (nothing to key artifacts on).
    """
    if not agent_row.get("id"):
        return None
    runtime = classify_runtime(agent_row)
    src = locate_sources(
        agent_row,
        runtime,
        agent_records=agent_records,
        cursor_transcripts_dir=cursor_transcripts_dir,
        claude_projects_dir=claude_projects_dir,
        index_cache=index_cache,
    )
    if src_sink is not None:
        src_sink[agent_row["id"]] = src
    rows = build_artifact_rows(agent_row, src, runtime)

    summary = {}
    remote_shas = {}
    if not dry_run:
        try:
            existing = select(
                "factory_agent_artifacts",
                params={
                    "agent_id": f"eq.{agent_row['id']}",
                    "select": "id,content_sha256",
                },
                url=url,
                service_role_key=service_role_key,
            )
        except SupabaseRestError as exc:
            if _is_missing_table_error(exc):
                raise MissingTableError(str(exc)) from exc
            raise
        remote_shas = {r["id"]: r.get("content_sha256") for r in existing or []}

    to_push = []
    for row in rows:
        kind = row["artifact_kind"]
        if row["content"] is not None:
            summary[kind] = "content"
        else:
            summary[kind] = f"marker:{row['unavailable_reason']}"
        if (
            not dry_run
            and row["id"] in remote_shas
            and remote_shas[row["id"]] == row["content_sha256"]
        ):
            summary[kind] = "unchanged"
            continue
        to_push.append(row)

    if not dry_run and to_push:
        try:
            upsert(
                "factory_agent_artifacts",
                to_push,
                on_conflict="id",
                url=url,
                service_role_key=service_role_key,
            )
        except SupabaseRestError as exc:
            if _is_missing_table_error(exc):
                raise MissingTableError(str(exc)) from exc
            raise
    return summary


# =============================================================================
# Parent edges — factory_agents.parent_agent_id (design research/03 §3
# spawned-by row; the column shipped in migration 20260804231807 with zero
# writers until this pass)
# =============================================================================

# Design §3 row (b): free-text goal convention "… subagent of MF-PTC-RETRAIN-EXEC …".
# The regex is the design's own, verbatim. A failed parse means NO edge.
PARENT_GOAL_RE = re.compile(r"\bsub-?agent of ([A-Z0-9-]+)\b")

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"

RUNS_DIR = HERE / "runs"


def _derive_containment_edges(agent_rows, transcript_matches):
    """Design §3 derivation (a), HIGH: a matched child transcript living at
    `<parent-session>/subagents/<child>.jsonl` names its parent session; when
    that parent session's transcript is itself matched to an agent row, the
    edge is folder-containment fact, not inference. Works for both transcript
    homes (Cursor `<uuid>/subagents/` and Claude `<session>/subagents/`).
    """
    stem_to_agent = {}
    for agent_id, path in transcript_matches.items():
        stem = Path(path).stem
        if stem in stem_to_agent and stem_to_agent[stem] != agent_id:
            stem_to_agent[stem] = None  # two agents claim one session: refuse
        else:
            stem_to_agent[stem] = agent_id
    edges = {}
    known_ids = {row.get("id") for row in agent_rows}
    for agent_id, path in transcript_matches.items():
        path = Path(path)
        if path.parent.name != "subagents":
            continue
        parent_id = stem_to_agent.get(path.parent.parent.name)
        if parent_id and parent_id != agent_id and parent_id in known_ids:
            edges[agent_id] = {
                "parent_id": parent_id,
                "confidence": CONFIDENCE_HIGH,
                "basis": "transcript_folder_containment",
            }
    return edges


def _derive_dispatch_edges(agent_rows, runs_dir=None):
    """Design §3 derivation (c), HIGH: `runs/<org>/cursor_dispatch/*.json`.
    Today's records identify only the DISPATCHED agent (`cursor_agent_id`) —
    they carry no dispatcher identity — so this yields an edge only when a
    record explicitly names its dispatcher (`dispatcher_session_id` /
    `dispatched_by` title). No dispatcher named → no edge, honestly.
    """
    directory = Path(runs_dir) if runs_dir else RUNS_DIR
    if not directory.is_dir():
        return {}
    by_cursor_id = {
        row["cursor_agent_id"]: row["id"]
        for row in agent_rows
        if row.get("cursor_agent_id") and row.get("id")
    }
    by_session = {
        row["ledger_session_id"]: row["id"]
        for row in agent_rows
        if row.get("ledger_session_id") and row.get("id")
    }
    by_title = {}
    for row in agent_rows:
        if row.get("title") and row.get("id"):
            by_title.setdefault(row["title"], []).append(row["id"])
    edges = {}
    for path in sorted(directory.glob("*/cursor_dispatch/*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        child_id = by_cursor_id.get(record.get("cursor_agent_id"))
        if not child_id:
            continue
        parent_id = by_session.get(record.get("dispatcher_session_id"))
        if not parent_id:
            titles = by_title.get(record.get("dispatched_by")) or []
            parent_id = titles[0] if len(titles) == 1 else None
        if parent_id and parent_id != child_id:
            edges[child_id] = {
                "parent_id": parent_id,
                "confidence": CONFIDENCE_HIGH,
                "basis": "dispatch_record",
            }
    return edges


def _derive_goal_text_edges(agent_rows):
    """Design §3 derivation (b), MEDIUM: goal text parsed with the design's
    regex, matched against another row's exact title. Zero or several title
    matches → no edge (a missing edge is honest; a guessed edge is not).
    """
    by_title = {}
    for row in agent_rows:
        if row.get("title") and row.get("id"):
            by_title.setdefault(row["title"], []).append(row["id"])
    edges = {}
    for row in agent_rows:
        agent_id = row.get("id")
        if not agent_id:
            continue
        match = PARENT_GOAL_RE.search(row.get("goal") or "")
        if not match:
            continue
        label = match.group(1)
        candidates = [i for i in by_title.get(label, []) if i != agent_id]
        if len(candidates) == 1:
            edges[agent_id] = {
                "parent_id": candidates[0],
                "confidence": CONFIDENCE_MEDIUM,
                "basis": f"goal_text:{label}",
            }
        elif len(candidates) > 1:
            log.warning(
                "graph_artifacts: goal text of %s names parent %s but %d rows "
                "carry that title — refusing to guess (no edge)",
                row.get("title") or agent_id,
                label,
                len(candidates),
            )
    return edges


def derive_parent_edges(agent_rows, transcript_matches=None, runs_dir=None):
    """{child_agent_id: {parent_id, confidence, basis}} in design §3
    confidence order — the first (highest-confidence) derivation per child
    wins; lower-confidence rules never displace it.
    """
    transcript_matches = transcript_matches or {}
    edges = _derive_containment_edges(agent_rows, transcript_matches)
    for child_id, edge in _derive_dispatch_edges(agent_rows, runs_dir=runs_dir).items():
        edges.setdefault(child_id, edge)
    for child_id, edge in _derive_goal_text_edges(agent_rows).items():
        edges.setdefault(child_id, edge)
    return edges


def push_parent_edges(edges, agent_rows, url=None, service_role_key=None, dry_run=False):
    """PATCH `factory_agents.parent_agent_id` for each derived edge, fail-open.

    Never-downgrade rule: a non-null parent_agent_id is NEVER overwritten with
    a different value — the stored value may have been recorded at dispatch
    time (HIGH) and no re-derivation outranks a recording. Same value →
    no-op. The match basis has no column on factory_agents, so it is recorded
    as a log line per the mission instruction.
    """
    summary = {"high": 0, "medium": 0, "unchanged": 0, "conflict_skipped": 0, "patch_failed": 0}
    current = {row["id"]: row.get("parent_agent_id") for row in agent_rows if row.get("id")}
    for child_id in sorted(edges):
        edge = edges[child_id]
        existing = current.get(child_id)
        if existing:
            if existing == edge["parent_id"]:
                summary["unchanged"] += 1
            else:
                log.warning(
                    "graph_artifacts: parent_agent_id of %s already set (%s) — "
                    "refusing to overwrite with %s [%s/%s] (never-downgrade)",
                    child_id,
                    existing,
                    edge["parent_id"],
                    edge["confidence"],
                    edge["basis"],
                )
                summary["conflict_skipped"] += 1
            continue
        log.info(
            "graph_artifacts: parent edge %s -> %s (%s, basis=%s)%s",
            child_id,
            edge["parent_id"],
            edge["confidence"],
            edge["basis"],
            " [dry-run]" if dry_run else "",
        )
        summary[edge["confidence"]] += 1
        if dry_run:
            continue
        try:
            update(
                "factory_agents",
                {"id": f"eq.{child_id}"},
                {"parent_agent_id": edge["parent_id"]},
                url=url,
                service_role_key=service_role_key,
            )
        except SupabaseRestError as exc:
            # One warning, then stop the whole edges pass: the dominant cause
            # is the parent_agent_id column not existing on this target yet
            # (remote pre-migration), where every further PATCH fails the
            # same way.
            log.warning(
                "graph_artifacts: parent_agent_id PATCH failed — edges pass "
                "stopped for this run (fail-open): %s",
                exc,
            )
            summary[edge["confidence"]] -= 1
            summary["patch_failed"] += 1
            break
    return summary


def sync_parent_edges(
    transcript_matches=None,
    url=None,
    service_role_key=None,
    runs_dir=None,
    dry_run=False,
    limit=1000,
):
    """The parent-edge pass: read factory_agents (title/goal/parent lookup
    needs the full population, not just the artifacts pass's recency window),
    derive edges, PATCH. **Fail-open, unconditionally** — including the target
    not having the parent_agent_id column yet (select 400s → one warning).
    Returns the push summary, or None on a swallowed failure.
    """
    try:
        agent_rows = select(
            "factory_agents",
            params={
                "select": (
                    "id,title,goal,parent_agent_id,cursor_agent_id,ledger_session_id"
                ),
                "limit": str(limit),
            },
            url=url,
            service_role_key=service_role_key,
        )
    except SupabaseRestError as exc:
        log.warning(
            "graph_artifacts: could not read factory_agents for the parent-edge "
            "pass — skipped (fail-open): %s",
            exc,
        )
        return None
    edges = derive_parent_edges(
        agent_rows or [], transcript_matches=transcript_matches, runs_dir=runs_dir
    )
    return push_parent_edges(
        edges, agent_rows or [], url=url, service_role_key=service_role_key, dry_run=dry_run
    )


def sync_agent_artifacts(
    url=None,
    service_role_key=None,
    limit=50,
    agent_records=None,
    cursor_transcripts_dir=None,
    claude_projects_dir=None,
):
    """The scheduled artifacts pass: read back the most recently seen
    `factory_agents` rows (they carry the DB ids artifacts key on) and push
    each one's four artifacts.

    **Fail-open, unconditionally** (PRRules rule 5): any failure — including
    the table not existing yet — logs and returns; the poll never blocks.
    Returns the number of agents whose artifacts were processed, or None on a
    swallowed failure.
    """
    try:
        agent_rows = select(
            "factory_agents",
            params={
                "select": (
                    "id,run_id,stage,title,goal,status,cursor_agent_id,"
                    "ledger_session_id,report_path,final_response,report_summary"
                ),
                "order": "last_seen_at.desc",
                "limit": str(limit),
            },
            url=url,
            service_role_key=service_role_key,
        )
    except SupabaseRestError as exc:
        log.warning(
            "graph_artifacts: could not read factory_agents — artifacts pass "
            "skipped this poll (fail-open): %s",
            exc,
        )
        return None

    if agent_records is None:
        agent_records = load_agent_records()

    processed = 0
    index_cache = {}
    src_by_agent = {}
    for row in agent_rows or []:
        try:
            result = push_agent_artifacts(
                row,
                agent_records=agent_records,
                cursor_transcripts_dir=cursor_transcripts_dir,
                claude_projects_dir=claude_projects_dir,
                url=url,
                service_role_key=service_role_key,
                index_cache=index_cache,
                src_sink=src_by_agent,
            )
        except MissingTableError as exc:
            # ONE warning for the whole pass, then stop — the table ships from
            # the platform repo in parallel; until the migration lands this is
            # dormant by design, not an error loop.
            log.warning(
                "graph_artifacts: factory_agent_artifacts table not deployed yet "
                "— artifacts pass dormant until the platform migration lands "
                "(fail-open): %s",
                exc,
            )
            return processed
        except Exception as exc:  # noqa: BLE001 — telemetry; one agent never kills the pass
            log.warning(
                "graph_artifacts: artifact push failed for agent %s — continuing "
                "(fail-open): %s",
                row.get("title") or row.get("id"),
                exc,
            )
            continue
        if result is not None:
            processed += 1

    # The parent-edge pass rides the same poll (design §3): matched transcript
    # paths feed folder containment; the pass reads the full agent population
    # itself and is wholly fail-open inside.
    transcript_matches = {
        agent_id: src["transcript_path"]
        for agent_id, src in src_by_agent.items()
        if src.get("transcript_path")
    }
    sync_parent_edges(
        transcript_matches=transcript_matches,
        url=url,
        service_role_key=service_role_key,
    )
    return processed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="extract AND upsert (default is dry-run: classify/extract/report only)",
    )
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--url", default=None)
    parser.add_argument("--service-role-key", default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.live:
        try:
            agent_rows = select(
                "factory_agents",
                params={
                    "select": (
                        "id,run_id,stage,title,cursor_agent_id,ledger_session_id,"
                        "report_path,final_response,report_summary"
                    ),
                    "order": "last_seen_at.desc",
                    "limit": str(args.limit),
                },
                url=args.url,
                service_role_key=args.service_role_key,
            )
        except SupabaseRestError as exc:
            print(f"graph_artifacts: could not read factory_agents — {exc}")
            return 0
        agent_records = load_agent_records()
        index_cache = {}
        for row in agent_rows or []:
            summary = push_agent_artifacts(
                row, agent_records=agent_records, dry_run=True, index_cache=index_cache
            )
            print(f"{row.get('title')}: {classify_runtime(row)} → {summary}")
        print("dry-run: no writes performed (pass --live to write).")
        return 0

    processed = sync_agent_artifacts(
        url=args.url, service_role_key=args.service_role_key, limit=args.limit
    )
    print(
        "graph_artifacts: "
        + ("push failed (fail-open)" if processed is None else f"{processed} agent(s) processed")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
