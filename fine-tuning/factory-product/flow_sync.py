#!/usr/bin/env python3
"""Derive and project Model Factory information-flow edges.

Ground truth stays in heartbeat boards, the recent action/agent ledgers, handoff
files, factory_agents report pointers, and existing gate/event rows.  This
module projects only typed edges and redacted evidence pointers; prompts, tool
arguments, tool results, reasoning, and report bodies never enter a payload.

Storage is deliberately zero-DDL:

* agent-pair edges with a run project to ``factory_handoffs``;
* every run-scoped edge is mirrored to ``factory_run_events`` as
  ``kind=steering_event, ref.event_subtype=info_flow``;
* run-less edges are unsupported by both existing tables because both require a
  non-null run_id.  They are counted and reported, never attached to a made-up
  run.

The reconciled identity scheme uses the shared Model Factory uuid5 namespace
and one canonical logical key:

    info_flow:<TYPE>:<from_key>:<to_key>:<artifact>

The factory_handoffs id is uuid5(NS, ``factory_handoffs:<logical-key>``), as
research/03 requires.  The event mirror is uuid5(NS,
``factory_run_events:<logical-key>``).  Both therefore remain deterministic
without pretending that rows in two different tables have the same primary key.

Usage:
    python3 flow_sync.py                 # dry-run (default)
    python3 flow_sync.py --live          # explicit writes
    python3 flow_sync.py --days 2        # recent-ledger window
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKFLOW_ROOT = HERE.parent.parent
LEDGER_DIR = WORKFLOW_ROOT / "ledger"
AGENT_LEDGER_DIR = LEDGER_DIR / "agents"
HANDOFFS_DIR = WORKFLOW_ROOT / "handoffs"
HEARTBEAT_PATH = WORKFLOW_ROOT / "heartbeat" / "STATE.md"
ZONE_BOARDS = WORKFLOW_ROOT / "heartbeat" / "zones"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WORKFLOW_ROOT / "scripts" / "lib"))

from backfill_ptc_history import NAMESPACE  # noqa: E402
from factory_sync import _redact_path, parse_active_sessions  # noqa: E402
from redact import redact  # noqa: E402
from supabase_rest import SupabaseRestError, select, upsert  # noqa: E402

FLOW_TYPES = (
    "SPAWN",
    "REPORT",
    "ARTIFACT",
    "ADDENDUM",
    "HANDOFF",
    "GATE",
    "STEER",
    "PROJECTION",
)
CONFIDENCES = ("certain", "inferred", "heuristic")
PAIR_TYPES = {"SPAWN", "REPORT", "ARTIFACT", "ADDENDUM", "HANDOFF"}
SYNC_VERSION = 1

SESSION_TOKEN = r"[A-Z][A-Z0-9-]{2,}"
PARENT_RE = re.compile(
    rf"\b(?:sub-?agent|research subagent|planning subagent|design subagent|"
    rf"implementation subagent|execution subagent|stress subagent)\s+"
    rf"(?:of|under)\s+({SESSION_TOKEN})\b",
    re.IGNORECASE,
)
PROMPT_IDENT_RE = re.compile(
    rf"\bYou are\s+(?:subagent\s+)?({SESSION_TOKEN})\b.*?"
    rf"\b(?:sub-?agent|under)\s+(?:of\s+)?({SESSION_TOKEN})\b",
    re.IGNORECASE | re.DOTALL,
)
PROMPT_IDENT_SIMPLE_RE = re.compile(
    rf"\bYou are\s+(?:subagent\s+)?({SESSION_TOKEN})\b.*?\bunder\s+({SESSION_TOKEN})\b",
    re.IGNORECASE | re.DOTALL,
)
CONSUMES_RE = re.compile(r"(?im)^\s*(?:[-*]\s*)?consumes:\s*(.+?)\s*$")
CORRECTS_RE = re.compile(
    rf"(?im)^\s*(?:[-*]\s*)?corrects:\s*([^#\s·]+)(?:#([^·\n]+))?"
    rf"(?:\s*·\s*by:\s*({SESSION_TOKEN}))?"
)
PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"((?:factory-automation|PRs|handoffs|company-brain|memory|scripts|coding|"
    r"platform-alpha(?:-[A-Za-z0-9_.-]+)?)/[A-Za-z0-9_./*{}:+-]+)"
)
AUTHOR_RE = re.compile(
    rf"(?im)^(?:<!--\s*)?(?:_|\*\*)?(?:Authored|Written)(?:\s+[^<\n]*?)?\s+by\s+"
    rf"(?:a\s+documentation\s+subagent\s+of\s+)?({SESSION_TOKEN})\b"
)
HEADING_RE = re.compile(r"(?im)^#{2,6}\s+.*\b(ADDENDUM|ERRATUM|SUPERSEDE[DS]?)\b.*$")


def _clean_session(value):
    return (value or "").strip().upper()


def _relative_path(value):
    """Return a redacted, repo-relative evidence pointer."""
    if value is None:
        return None
    raw = str(value).strip().strip("`'\"").rstrip(".,;:)")
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        try:
            raw = str(path.relative_to(WORKFLOW_ROOT))
        except ValueError:
            return "[external path — redacted]"
    safe = _redact_path(raw)
    return redact(safe)


def _safe_text(value, limit=500):
    """Redact bounded metadata; never accepts prompt/report body passthrough."""
    return redact(str(value or ""))[:limit]


def _logical_key(edge_type, from_key, to_key, artifact):
    return f"info_flow:{edge_type}:{from_key}:{to_key}:{artifact or '-'}"


def _row_id(table, edge):
    return str(uuid.uuid5(NAMESPACE, f"{table}:{edge.logical_key}"))


@dataclass(frozen=True)
class FlowEdge:
    edge_type: str
    from_key: str
    to_key: str
    confidence: str
    artifact: str | None
    evidence: tuple[str, ...]
    run_id: str | None = None
    ts: str | None = None
    from_agent_id: str | None = None
    to_agent_id: str | None = None
    note: str | None = None

    def __post_init__(self):
        if self.edge_type not in FLOW_TYPES:
            raise ValueError(f"unknown flow type: {self.edge_type}")
        if self.confidence not in CONFIDENCES:
            raise ValueError(f"unknown confidence: {self.confidence}")

    @property
    def logical_key(self):
        return _logical_key(
            self.edge_type,
            self.from_key,
            self.to_key,
            self.artifact,
        )


@dataclass
class SourceBundle:
    root: Path = WORKFLOW_ROOT
    sessions: dict[str, dict] = field(default_factory=dict)
    ledger_events: list[dict] = field(default_factory=list)
    agent_records: list[dict] = field(default_factory=list)
    handoff_files: list[Path] = field(default_factory=list)
    agent_rows: list[dict] = field(default_factory=list)
    event_rows: list[dict] = field(default_factory=list)
    gate_requests: list[dict] = field(default_factory=list)
    gate_decisions: list[dict] = field(default_factory=list)
    remote_available: bool = False


def _read_jsonl(path):
    rows = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
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


def _recent_jsonl(directory, days):
    if not directory.is_dir():
        return []
    paths = sorted(p for p in directory.glob("*.jsonl") if p.name[:4].isdigit())
    rows = []
    for path in paths[-days:]:
        rows.extend(_read_jsonl(path))
    return rows


def load_sessions(root=WORKFLOW_ROOT):
    """Merge root + zone heartbeat rows without replacing another board's data."""
    boards = [Path(root) / "heartbeat" / "STATE.md"]
    zones = Path(root) / "heartbeat" / "zones"
    if zones.is_dir():
        boards.extend(sorted(zones.glob("*/STATE.md")))
    sessions = {}
    for board in boards:
        try:
            markdown = board.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        board_rel = _relative_path(board)
        for row in parse_active_sessions(markdown):
            label = _clean_session(row.get("session"))
            if not label:
                continue
            candidate = {**row, "_board": board_rel}
            prior = sessions.get(label)
            # Prefer the higher sequence, preserving the root/zone merge contract.
            try:
                candidate_seq = int(row.get("seq") or 0)
            except ValueError:
                candidate_seq = 0
            try:
                prior_seq = int((prior or {}).get("seq") or 0)
            except ValueError:
                prior_seq = 0
            if prior is None or candidate_seq >= prior_seq:
                sessions[label] = candidate
    return sessions


def _identify_agent_record(record):
    prompt = record.get("prompt")
    if not isinstance(prompt, str):
        return None, None
    match = PROMPT_IDENT_RE.search(prompt) or PROMPT_IDENT_SIMPLE_RE.search(prompt)
    if match:
        return _clean_session(match.group(1)), _clean_session(match.group(2))
    child_match = re.search(
        rf"\bYou are\s+(?:subagent\s+)?({SESSION_TOKEN})\b", prompt, re.IGNORECASE
    )
    parent_match = PARENT_RE.search(prompt)
    return (
        _clean_session(child_match.group(1)) if child_match else None,
        _clean_session(parent_match.group(1)) if parent_match else None,
    )


def _parse_consumes(text):
    """Machine-readable C1 parser; accepts list, comma, or middle-dot forms."""
    paths = []
    for match in CONSUMES_RE.finditer(text or ""):
        raw = match.group(1).strip().strip("[]")
        tokens = re.split(r"\s*(?:·|,|\|)\s*", raw)
        for token in tokens:
            token = token.strip().strip("`'\"")
            # A prose suffix is not a path.  Extract a path token when possible.
            path_match = PATH_RE.search(token)
            value = path_match.group(1) if path_match else token.split()[0]
            value = _relative_path(value)
            if value and value != "[external path — redacted]" and value not in paths:
                paths.append(value)
    return paths


def _session_parent(label, session):
    match = PARENT_RE.search(session.get("goal") or "")
    if match:
        parent = _clean_session(match.group(1))
        if parent != label:
            return parent
    return None


def _agent_index(rows):
    """Title -> row, preferring run-scoped and most recently seen rows."""
    index = {}
    for row in rows:
        title = _clean_session(row.get("title"))
        if not title:
            continue
        prior = index.get(title)
        key = (bool(row.get("run_id")), row.get("last_seen_at") or "")
        prior_key = (
            bool((prior or {}).get("run_id")),
            (prior or {}).get("last_seen_at") or "",
        )
        if prior is None or key > prior_key:
            index[title] = row
    return index


def _resolve_edge(edge, agents):
    """Attach ids/run only when the existing rows support the join."""
    from_row = agents.get(_clean_session(edge.from_key))
    to_row = agents.get(_clean_session(edge.to_key))
    from_run = (from_row or {}).get("run_id")
    to_run = (to_row or {}).get("run_id")
    run_id = edge.run_id
    if not run_id:
        if from_run and to_run and from_run == to_run:
            run_id = from_run
        elif from_run and not to_run:
            run_id = from_run
        elif to_run and not from_run:
            run_id = to_run
    return FlowEdge(
        **{
            **edge.__dict__,
            "run_id": run_id,
            "from_agent_id": (from_row or {}).get("id"),
            "to_agent_id": (to_row or {}).get("id"),
        }
    )


def _evidence_pointer(source, suffix=None):
    pointer = _relative_path(source)
    if suffix:
        pointer = f"{pointer}#{_safe_text(suffix, 100)}"
    return pointer


def derive_spawn_report(bundle):
    edges = []
    seen = set()

    # A machine-written spawn record carrying both identities is the strongest
    # current source.  The prompt body itself is never copied into an edge.
    for record in bundle.agent_records:
        child, parent = _identify_agent_record(record)
        if not child or not parent or child == parent:
            continue
        record_key = record.get("generation_uuid") or record.get("session_id") or record.get("ts")
        evidence = f"ledger/agents:{_safe_text(record_key, 100)}"
        edge = FlowEdge(
            "SPAWN",
            parent,
            child,
            "certain",
            f"spawn:{child}",
            (evidence,),
            ts=record.get("ts"),
            note="machine-written dispatch record names parent and child",
        )
        edges.append(edge)
        seen.add((parent, child))

    # Heartbeat free text is inferred, never promoted merely because the regex matched.
    for child, session in bundle.sessions.items():
        parent = _session_parent(child, session)
        if not parent:
            continue
        if (parent, child) not in seen:
            edges.append(
                FlowEdge(
                    "SPAWN",
                    parent,
                    child,
                    "inferred",
                    f"spawn:{child}",
                    (_evidence_pointer(session.get("_board"), f"session={child}"),),
                    note="heartbeat goal uses the subagent-of convention",
                )
            )
        if (session.get("status") or "").upper() == "ENDED":
            edges.append(
                FlowEdge(
                    "REPORT",
                    child,
                    parent,
                    "certain",
                    f"report:{child}",
                    (_evidence_pointer(session.get("_board"), f"session={child}:ENDED"),),
                    note="heartbeat terminal row proves report existence, not body",
                )
            )
    return edges


def _path_matches_scope(path, scope):
    if not path or not scope:
        return False
    path = path.rstrip("/")
    scope = scope.strip().strip("`")
    if not scope or scope == "[external path — redacted]":
        return False
    if any(ch in scope for ch in "*?["):
        return fnmatch.fnmatch(path, scope)
    if scope.endswith("/**"):
        return path.startswith(scope[:-3].rstrip("/") + "/")
    return path == scope.rstrip("/") or path.startswith(scope.rstrip("/") + "/")


def _declared_owner(path, sessions, exclude=None):
    matches = []
    for label, row in sessions.items():
        if label == exclude:
            continue
        scopes = [p.strip() for p in (row.get("allowed_paths") or "").split(",")]
        matching = [scope for scope in scopes if _path_matches_scope(path, scope)]
        if matching:
            specificity = max(len(scope.replace("*", "")) for scope in matching)
            matches.append((specificity, label))
    if not matches:
        return None
    matches.sort(reverse=True)
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        return None
    return matches[0][1]


def _record_identity_maps(agent_records):
    identity = {}
    consumes = {}
    timestamps = {}
    for record in agent_records:
        child, _parent = _identify_agent_record(record)
        if not child:
            continue
        for key in (record.get("session_id"), record.get("generation_uuid")):
            if key:
                identity[str(key)] = child
        prompt = record.get("prompt") or ""
        consumes.setdefault(child, set()).update(_parse_consumes(prompt))
        timestamps[child] = record.get("ts")
    return identity, consumes, timestamps


def derive_artifacts(bundle):
    """Derive write->read and explicit consumes flows without guessing readers."""
    edges = []
    identity, consumes, timestamps = _record_identity_maps(bundle.agent_records)
    agents = _agent_index(bundle.agent_rows)

    # Existing report_path is an exact writer pointer for dispatched agents.
    report_owners = {}
    for label, row in agents.items():
        path = _relative_path(row.get("report_path"))
        if path and path != "[external path — redacted]":
            report_owners[path] = label

    # Exact ledger write/read joins.  A session id must map to a named agent;
    # unmapped events are ignored rather than attributed heuristically.
    writes = {}
    reads = []
    for event in bundle.ledger_events:
        if event.get("event") != "tool" or not event.get("ok", True):
            continue
        label = identity.get(str(event.get("session_id")))
        if not label:
            continue
        tool = str(event.get("tool") or "").lower()
        for raw_path in event.get("files") or []:
            path = _relative_path(raw_path)
            if not path or path == "[external path — redacted]":
                continue
            if tool in {"write", "edit"}:
                writes[path] = (label, event.get("ts"))
            elif tool == "read":
                reads.append((path, label, event.get("ts")))
    for path, reader, ts in reads:
        writer_info = writes.get(path)
        if not writer_info or writer_info[0] == reader:
            continue
        writer, write_ts = writer_info
        if write_ts and ts and write_ts > ts:
            continue
        edges.append(
            FlowEdge(
                "ARTIFACT",
                writer,
                reader,
                "certain",
                path,
                (f"ledger:Write/Edit:{path}", f"ledger:Read:{path}"),
                ts=ts,
                note="same-path write then read with mapped session identities",
            )
        )

    # C1 consumes proves the read intent mechanically.  Ownership is certain only
    # when report_path names the producer; allowed_paths alone is an inferred writer.
    for reader, paths in consumes.items():
        for path in sorted(paths):
            writer = report_owners.get(path)
            confidence = "certain"
            evidence = [f"consumes:{path}", f"factory_agents.report_path:{path}"]
            if not writer:
                writer = _declared_owner(path, bundle.sessions, exclude=reader)
                confidence = "inferred"
                evidence = [
                    f"consumes:{path}",
                    f"heartbeat.allowed_paths:{writer or 'unresolved'}",
                ]
            if not writer or writer == reader:
                continue
            edges.append(
                FlowEdge(
                    "ARTIFACT",
                    writer,
                    reader,
                    confidence,
                    path,
                    tuple(evidence),
                    ts=timestamps.get(reader),
                    note="C1 consumes parser; confidence also reflects writer evidence",
                )
            )
    return edges


def _candidate_markdown(bundle):
    paths = set(bundle.handoff_files)
    for row in bundle.sessions.values():
        for raw in (row.get("allowed_paths") or "").split(","):
            raw = raw.strip()
            if raw.endswith(".md") and "*" not in raw:
                path = bundle.root / raw
                if path.is_file():
                    paths.add(path)
    for event in bundle.ledger_events:
        if str(event.get("tool") or "").lower() not in {"write", "edit"}:
            continue
        for raw in event.get("files") or []:
            path = Path(raw)
            if not path.is_absolute():
                path = bundle.root / path
            if path.suffix.lower() == ".md" and path.is_file():
                paths.add(path)
    return sorted(paths)


def _owner_for_file(path, bundle):
    rel = _relative_path(path)
    agents = _agent_index(bundle.agent_rows)
    for label, row in agents.items():
        if _relative_path(row.get("report_path")) == rel:
            return label, "certain"
    owner = _declared_owner(rel, bundle.sessions)
    return (owner, "inferred") if owner else (None, None)


def derive_addenda(bundle):
    edges = []
    for path in _candidate_markdown(bundle):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        source_owner, source_confidence = _owner_for_file(path, bundle)
        for match in CORRECTS_RE.finditer(text):
            target_path = _relative_path(match.group(1))
            target_owner = _declared_owner(target_path, bundle.sessions)
            finder = _clean_session(match.group(3)) or source_owner
            if not finder or not target_owner or finder == target_owner:
                continue
            confidence = "certain" if match.group(3) else source_confidence or "inferred"
            artifact = target_path + (f"#{match.group(2).strip()}" if match.group(2) else "")
            edges.append(
                FlowEdge(
                    "ADDENDUM",
                    finder,
                    target_owner,
                    confidence,
                    artifact,
                    (_evidence_pointer(path, f"corrects:{artifact}"),),
                    note="C2 corrects parser",
                )
            )

        # Legacy prose fallback: never stronger than heuristic.  It requires a
        # correction-shaped heading, a resolvable writer, and exactly one cited
        # target path owned by another session.
        for heading in HEADING_RE.finditer(text):
            if not source_owner:
                continue
            section = text[heading.start() : heading.start() + 2000]
            targets = []
            for target in PATH_RE.findall(section):
                rel = _relative_path(target)
                owner = _declared_owner(rel, bundle.sessions, exclude=source_owner)
                if owner:
                    targets.append((rel, owner))
            targets = list(dict.fromkeys(targets))
            if len(targets) != 1:
                continue
            target_path, target_owner = targets[0]
            edges.append(
                FlowEdge(
                    "ADDENDUM",
                    source_owner,
                    target_owner,
                    "heuristic",
                    target_path,
                    (_evidence_pointer(path, heading.group(0).strip()),),
                    note="legacy addendum-heading fallback; never upgraded to fact",
                )
            )
    return edges


def _handoff_author(text):
    match = AUTHOR_RE.search(text[:1000])
    return _clean_session(match.group(1)) if match else None


def derive_handoffs(bundle):
    edges = []
    _identity, consumes, timestamps = _record_identity_maps(bundle.agent_records)
    for path in bundle.handoff_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        author = _handoff_author(text)
        if not author:
            continue
        rel = _relative_path(path)
        slug = path.parent.name
        consumers = []
        for label, consumed_paths in consumes.items():
            if rel in consumed_paths or any(p.startswith(f"handoffs/{slug}/") for p in consumed_paths):
                consumers.append(label)
        # Prompt prose may cite a handoff even before C1 was adopted.
        for record in bundle.agent_records:
            child, _parent = _identify_agent_record(record)
            prompt = record.get("prompt") or ""
            if child and child != author and (
                rel in prompt or f"handoffs/{slug}/" in prompt
            ):
                consumers.append(child)
        for consumer in sorted(set(consumers)):
            edges.append(
                FlowEdge(
                    "HANDOFF",
                    author,
                    consumer,
                    "certain",
                    rel,
                    (_evidence_pointer(path, "authored-by"), f"consumes:{rel}"),
                    ts=timestamps.get(consumer),
                    note="authored-by header plus successor prompt path",
                )
            )
        if not consumers:
            edges.append(
                FlowEdge(
                    "HANDOFF",
                    author,
                    f"successor:{slug}",
                    "inferred",
                    rel,
                    (_evidence_pointer(path, "authored-by; successor not registered"),),
                    note="handoff exists, successor consumption is not yet evidenced",
                )
            )
    return edges


def derive_db_flows(bundle):
    edges = []
    requests = {row.get("id"): row for row in bundle.gate_requests}
    for decision in bundle.gate_decisions:
        request = requests.get(decision.get("gate_request_id")) or {}
        run_id = request.get("run_id")
        if not run_id:
            continue
        actor = decision.get("decided_by_email") or "human"
        decision_id = decision.get("id") or decision.get("gate_request_id")
        edges.append(
            FlowEdge(
                "GATE",
                f"human:{_safe_text(actor, 120)}",
                f"run:{run_id}",
                "certain",
                f"gate:{request.get('gate') or 'unknown'}",
                (f"factory_gate_decisions:{_safe_text(decision_id, 100)}",),
                run_id=run_id,
                ts=decision.get("decided_at"),
                note="gate request/decision foreign-key join",
            )
        )

    current_labels = set(bundle.sessions)
    projection_seen = set()
    for event in bundle.event_rows:
        ref = event.get("ref") or {}
        if ref.get("event_subtype") == "info_flow":
            continue
        run_id = event.get("run_id")
        event_id = event.get("id")
        subtype = ref.get("event_subtype")
        # steering_event is also the schema's extension carrier for A/B and
        # audit projections.  Those are not human redirects.  Only an explicit
        # human/operator subtype (or actor) is STEER evidence.
        steer_subtypes = {
            "steering_event",
            "operator_redirect",
            "human_redirect",
            "run_queued",
        }
        if event.get("kind") == "steering_event" and (
            subtype in steer_subtypes or ref.get("actor")
        ):
            edges.append(
                FlowEdge(
                    "STEER",
                    f"human:{_safe_text(ref.get('actor') or 'operator', 100)}",
                    _clean_session(ref.get("agent_title")) or f"run:{run_id}",
                    "certain",
                    f"steering_event:{subtype or event_id}",
                    (f"factory_run_events:{_safe_text(event_id, 100)}",),
                    run_id=run_id,
                    ts=event.get("ts"),
                    note="existing steering_event row; no prose reconstruction",
                )
            )
        label = _clean_session(ref.get("agent_title"))
        if label and label in current_labels:
            projection_key = (label, run_id, event.get("kind"))
            if projection_key not in projection_seen:
                projection_seen.add(projection_key)
                edges.append(
                    FlowEdge(
                        "PROJECTION",
                        label,
                        "table:factory_run_events",
                        "certain",
                        f"factory_run_events:{event.get('kind')}",
                        (f"factory_run_events:{_safe_text(event_id, 100)}",),
                        run_id=run_id,
                        ts=event.get("ts"),
                        note="existing event row proves projection",
                    )
                )

    # A factory_agents row is itself certain projection evidence.
    for row in bundle.agent_rows:
        label = _clean_session(row.get("title"))
        if label not in current_labels:
            continue
        edges.append(
            FlowEdge(
                "PROJECTION",
                label,
                "table:factory_agents",
                "certain",
                "factory_agents",
                (f"factory_agents:{_safe_text(row.get('id'), 100)}",),
                run_id=row.get("run_id"),
                ts=row.get("last_seen_at"),
                from_agent_id=row.get("id"),
                note="existing factory_agents row proves projection",
            )
        )
    return edges


def dedupe_edges(edges):
    """One edge per logical key; retain the strongest evidence, never upgrade it."""
    rank = {"certain": 3, "inferred": 2, "heuristic": 1}
    chosen = {}
    for edge in edges:
        prior = chosen.get(edge.logical_key)
        if prior is None or rank[edge.confidence] > rank[prior.confidence]:
            chosen[edge.logical_key] = edge
        elif rank[edge.confidence] == rank[prior.confidence]:
            merged_evidence = tuple(dict.fromkeys((*prior.evidence, *edge.evidence)))
            chosen[edge.logical_key] = FlowEdge(
                **{**prior.__dict__, "evidence": merged_evidence}
            )
    return sorted(
        chosen.values(),
        key=lambda edge: (
            edge.edge_type,
            edge.from_key,
            edge.to_key,
            edge.artifact or "",
        ),
    )


def derive_edges(bundle):
    raw = []
    raw.extend(derive_spawn_report(bundle))
    raw.extend(derive_artifacts(bundle))
    raw.extend(derive_addenda(bundle))
    raw.extend(derive_handoffs(bundle))
    raw.extend(derive_db_flows(bundle))
    agents = _agent_index(bundle.agent_rows)
    return dedupe_edges(_resolve_edge(edge, agents) for edge in raw)


def _summary(edge):
    evidence = ",".join(_safe_text(item, 180) for item in edge.evidence[:5])
    parts = [
        f"type={edge.edge_type}",
        f"confidence={edge.confidence}",
        f"evidence={evidence}",
        f"sync_version={SYNC_VERSION}",
    ]
    if edge.note:
        parts.append(f"basis={_safe_text(edge.note, 220)}")
    return "; ".join(parts)[:1200]


def project_rows(edges):
    """Build handoff/event rows and return unsupported edges separately."""
    handoffs = []
    events = []
    unsupported = []
    for edge in edges:
        if not edge.run_id:
            unsupported.append(edge)
            continue
        if (
            edge.edge_type in PAIR_TYPES
            and edge.from_agent_id
            and edge.to_agent_id
        ):
            handoffs.append(
                {
                    "id": _row_id("factory_handoffs", edge),
                    "run_id": edge.run_id,
                    "from_agent_id": edge.from_agent_id,
                    "to_agent_id": edge.to_agent_id,
                    "artifact": _safe_text(edge.artifact or edge.logical_key, 500),
                    "summary": _summary(edge),
                }
            )
        event = {
            "id": _row_id("factory_run_events", edge),
            "run_id": edge.run_id,
            "kind": "steering_event",
            "headline": _safe_text(
                f"{edge.edge_type} [{edge.confidence}] {edge.from_key} → {edge.to_key}",
                500,
            ),
            "ref": {
                "event_subtype": "info_flow",
                "flow_type": edge.edge_type,
                "confidence": edge.confidence,
                "logical_edge_id": str(uuid.uuid5(NAMESPACE, edge.logical_key)),
                "from_key": _safe_text(edge.from_key, 160),
                "to_key": _safe_text(edge.to_key, 160),
                "artifact": _safe_text(edge.artifact, 500) if edge.artifact else None,
            },
            "detail": {
                "evidence": [_safe_text(item, 250) for item in edge.evidence[:5]],
                "sync_version": SYNC_VERSION,
            },
        }
        if edge.ts:
            event["ts"] = _safe_text(edge.ts, 80)
        events.append(event)
    return handoffs, events, unsupported


def _remote_rows(url=None, service_role_key=None):
    """Read only the columns needed for identity/evidence; no content blobs."""
    agent_rows = select(
        "factory_agents",
        params={
            "select": "id,run_id,title,goal,status,report_path,last_seen_at",
            "limit": "5000",
        },
        url=url,
        service_role_key=service_role_key,
    )
    event_rows = select(
        "factory_run_events",
        params={
            "select": "id,run_id,ts,kind,ref",
            "order": "ts.desc",
            "limit": "5000",
        },
        url=url,
        service_role_key=service_role_key,
    )
    requests = select(
        "factory_gate_requests",
        params={"select": "id,run_id,gate,status", "limit": "1000"},
        url=url,
        service_role_key=service_role_key,
    )
    decisions = select(
        "factory_gate_decisions",
        params={
            "select": "id,gate_request_id,decision,decided_by_email,decided_at",
            "limit": "1000",
        },
        url=url,
        service_role_key=service_role_key,
    )
    return agent_rows, event_rows, requests, decisions


def load_sources(days=2, root=WORKFLOW_ROOT, url=None, service_role_key=None):
    root = Path(root)
    bundle = SourceBundle(
        root=root,
        sessions=load_sessions(root),
        ledger_events=_recent_jsonl(root / "ledger", days),
        agent_records=_recent_jsonl(root / "ledger" / "agents", days),
        handoff_files=sorted((root / "handoffs").glob("*/*.md"))
        if (root / "handoffs").is_dir()
        else [],
    )
    try:
        (
            bundle.agent_rows,
            bundle.event_rows,
            bundle.gate_requests,
            bundle.gate_decisions,
        ) = _remote_rows(url=url, service_role_key=service_role_key)
        bundle.remote_available = True
    except SupabaseRestError as exc:
        print(f"flow_sync: remote evidence unavailable — {exc}", file=sys.stderr)
    return bundle


def summarize(edges, handoffs, events, unsupported):
    return {
        "derived": len(edges),
        "by_type": dict(sorted(Counter(edge.edge_type for edge in edges).items())),
        "by_confidence": dict(
            sorted(Counter(edge.confidence for edge in edges).items())
        ),
        "handoff_rows": len(handoffs),
        "event_rows": len(events),
        "unsupported_runless": len(unsupported),
    }


def push_rows(handoffs, events, url=None, service_role_key=None):
    """Fail loudly to the caller; the scheduled wrapper owns fail-open isolation."""
    if handoffs:
        upsert(
            "factory_handoffs",
            handoffs,
            on_conflict="id",
            url=url,
            service_role_key=service_role_key,
        )
    # PostgREST bulk objects must have identical keys (PGRST102). Some source
    # records carry a truthful event timestamp; heartbeat-only edges do not and
    # intentionally rely on the DB's first-observation default. Keep those in
    # separate upserts rather than fabricating a timestamp just to equalize shape.
    timestamped = [row for row in events if "ts" in row]
    observed_now = [row for row in events if "ts" not in row]
    for event_batch in (timestamped, observed_now):
        if not event_batch:
            continue
        upsert(
            "factory_run_events",
            event_batch,
            on_conflict="id",
            url=url,
            service_role_key=service_role_key,
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="upsert rows (default is dry-run and performs no writes)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="explicit no-write mode (also overrides --live)",
    )
    parser.add_argument("--days", type=int, default=2, help="recent ledger day-files")
    parser.add_argument("--root", default=str(WORKFLOW_ROOT), help="workflow root")
    parser.add_argument("--url", default=None)
    parser.add_argument("--service-role-key", default=None)
    args = parser.parse_args(argv)
    live = args.live and not args.dry_run

    bundle = load_sources(
        days=max(1, args.days),
        root=args.root,
        url=args.url,
        service_role_key=args.service_role_key,
    )
    edges = derive_edges(bundle)
    handoffs, events, unsupported = project_rows(edges)
    summary = summarize(edges, handoffs, events, unsupported)

    print(
        "flow_sync: "
        f"{summary['derived']} edge(s); "
        f"types={json.dumps(summary['by_type'], sort_keys=True)}; "
        f"confidence={json.dumps(summary['by_confidence'], sort_keys=True)}"
    )
    print(
        "flow_sync: projection "
        f"factory_handoffs={len(handoffs)}, "
        f"factory_run_events={len(events)}, "
        f"unsupported_runless={len(unsupported)}"
    )
    if unsupported:
        print(
            "flow_sync: run-less edges are not writable: both existing "
            "factory_handoffs.run_id and factory_run_events.run_id are NOT NULL; "
            "no placeholder run was fabricated."
        )

    if not live:
        print("dry-run: no writes performed (pass --live to write).")
        return 0
    if not bundle.remote_available:
        print(
            "flow_sync: LIVE FAILED — remote identity/evidence could not be read",
            file=sys.stderr,
        )
        return 1
    try:
        push_rows(
            handoffs,
            events,
            url=args.url,
            service_role_key=args.service_role_key,
        )
    except SupabaseRestError as exc:
        print(f"flow_sync: LIVE FAILED — {exc}", file=sys.stderr)
        return 1
    print(
        f"flow_sync: live upsert OK ({len(handoffs)} handoff rows, "
        f"{len(events)} event rows)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
