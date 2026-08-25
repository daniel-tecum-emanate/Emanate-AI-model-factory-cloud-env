"""PR-A information-flow sync: truthful derivation, privacy, and idempotency."""

import json
import sys
import uuid
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import flow_sync  # noqa: E402
from backfill_ptc_history import NAMESPACE  # noqa: E402

RUN_ID = "ac6a6078-3932-4485-8b79-85eb318b85c9"
AGENT_IDS = {
    "PARENT": "10000000-0000-0000-0000-000000000001",
    "CHILD-A": "10000000-0000-0000-0000-000000000002",
    "CHILD-B": "10000000-0000-0000-0000-000000000003",
    "CHILD-C": "10000000-0000-0000-0000-000000000004",
}


def _session(label, goal, allowed, status="ENDED"):
    return {
        "session": label,
        "goal": goal,
        "allowed_paths": allowed,
        "risk": "code",
        "status": status,
        "seq": "1",
        "last_update": "2026-07-31 12:00 PT",
        "_board": "heartbeat/STATE.md",
    }


@pytest.fixture()
def today_tree(tmp_path):
    """A compact version of today's real fan-out/fan-in tree.

    The chain is intentionally multi-hop and changes direction:

      CHILD-A --ARTIFACT--> CHILD-B
          ^          |
          | ADDENDUM |
          +----------+
                     +--HANDOFF/ARTIFACT--> CHILD-C

    It exercises all eight types without reproducing the obsolete hand-counted
    15-node/50-edge target from research/05.
    """
    root = tmp_path
    handoff_dir = root / "handoffs" / "demo"
    handoff_dir.mkdir(parents=True)
    handoff = handoff_dir / "NEXT-PROMPT.md"
    handoff.write_text(
        "# NEXT-PROMPT — demo\n"
        "<!-- Authored by CHILD-B 2026-07-31 12:00 PT. -->\n"
        "## Mission\nContinue from the correction.\n"
    )

    addendum = root / "PRs" / "demo" / "ADDENDUM.md"
    addendum.parent.mkdir(parents=True)
    addendum.write_text(
        "# Correction\n"
        "corrects: PRs/demo/CORPUS.md#doses · by: CHILD-B · at: 2026-07-31T12:00:00-07:00\n"
    )

    sessions = {
        "PARENT": _session(
            "PARENT",
            "coordinate demo run",
            "PRs/demo/PLAN.md",
            status="ACTIVE",
        ),
        "CHILD-A": _session(
            "CHILD-A",
            "build subagent of PARENT: write corpus evidence",
            "PRs/demo/CORPUS.md",
        ),
        "CHILD-B": _session(
            "CHILD-B",
            "build subagent of PARENT: correct and hand off",
            "PRs/demo/ADDENDUM.md, handoffs/demo/NEXT-PROMPT.md",
        ),
        "CHILD-C": _session(
            "CHILD-C",
            "implementation subagent of PARENT: continue corrected work",
            "PRs/demo/RESULT.md",
        ),
    }
    agent_records = [
        {
            "ts": "2026-07-31T10:00:00-07:00",
            "generation_uuid": "gen-a",
            "prompt": "You are CHILD-A, a subagent of PARENT.",
        },
        {
            "ts": "2026-07-31T10:10:00-07:00",
            "generation_uuid": "gen-b",
            "prompt": (
                "You are CHILD-B, a subagent of PARENT.\n"
                "consumes: PRs/demo/CORPUS.md"
            ),
        },
        {
            "ts": "2026-07-31T10:20:00-07:00",
            "generation_uuid": "gen-c",
            "prompt": (
                "You are CHILD-C, a subagent of PARENT.\n"
                "consumes: handoffs/demo/NEXT-PROMPT.md"
            ),
        },
    ]
    ledger_events = [
        {
            "ts": "2026-07-31T10:05:00-07:00",
            "event": "tool",
            "session_id": "gen-a",
            "tool": "Write",
            "files": ["PRs/demo/CORPUS.md"],
            "ok": True,
        },
        {
            "ts": "2026-07-31T10:11:00-07:00",
            "event": "tool",
            "session_id": "gen-b",
            "tool": "Read",
            "files": ["PRs/demo/CORPUS.md"],
            "ok": True,
        },
    ]
    agent_rows = [
        {
            "id": AGENT_IDS[label],
            "run_id": RUN_ID,
            "title": label,
            "goal": sessions[label]["goal"],
            "status": "active" if label == "PARENT" else "ended",
            "report_path": {
                "CHILD-A": "PRs/demo/CORPUS.md",
                "CHILD-B": "handoffs/demo/NEXT-PROMPT.md",
            }.get(label),
            "last_seen_at": f"2026-07-31T10:0{index}:00-07:00",
        }
        for index, label in enumerate(AGENT_IDS)
    ]
    event_rows = [
        {
            "id": "20000000-0000-0000-0000-000000000001",
            "run_id": RUN_ID,
            "ts": "2026-07-31T10:30:00-07:00",
            "kind": "steering_event",
            "ref": {
                "event_subtype": "operator_redirect",
                "actor": "daniel",
                "agent_title": "PARENT",
            },
        },
        {
            "id": "20000000-0000-0000-0000-000000000002",
            "run_id": RUN_ID,
            "ts": "2026-07-31T10:01:00-07:00",
            "kind": "agent_spawned",
            "ref": {"agent_title": "CHILD-A"},
        },
    ]
    gate_requests = [
        {
            "id": "30000000-0000-0000-0000-000000000001",
            "run_id": RUN_ID,
            "gate": "S2",
            "status": "approved",
        }
    ]
    gate_decisions = [
        {
            "id": "40000000-0000-0000-0000-000000000001",
            "gate_request_id": gate_requests[0]["id"],
            "decision": "approve",
            "decided_by_email": "operator@example.com",
            "decided_at": "2026-07-31T09:29:00-07:00",
        }
    ]
    return flow_sync.SourceBundle(
        root=root,
        sessions=sessions,
        ledger_events=ledger_events,
        agent_records=agent_records,
        handoff_files=[handoff],
        agent_rows=agent_rows,
        event_rows=event_rows,
        gate_requests=gate_requests,
        gate_decisions=gate_decisions,
        remote_available=True,
    )


def test_acceptance_tree_derives_all_eight_types_and_multihop_chain(today_tree):
    edges = flow_sync.derive_edges(today_tree)
    by_type = Counter(edge.edge_type for edge in edges)
    assert by_type == {
        "SPAWN": 3,
        "REPORT": 3,
        "ARTIFACT": 2,
        "ADDENDUM": 1,
        "HANDOFF": 1,
        "GATE": 1,
        "STEER": 1,
        "PROJECTION": 6,
    }
    assert len(edges) == 18

    triples = {(e.from_key, e.edge_type, e.to_key) for e in edges}
    assert ("CHILD-A", "ARTIFACT", "CHILD-B") in triples
    assert ("CHILD-B", "ADDENDUM", "CHILD-A") in triples  # direction reversal
    assert ("CHILD-B", "HANDOFF", "CHILD-C") in triples
    assert ("CHILD-B", "ARTIFACT", "CHILD-C") in triples


def test_confidence_is_source_specific_and_heuristic_never_becomes_fact(today_tree):
    edges = flow_sync.derive_edges(today_tree)
    by_key = {(e.edge_type, e.from_key, e.to_key, e.artifact): e for e in edges}
    assert by_key[("SPAWN", "PARENT", "CHILD-A", "spawn:CHILD-A")].confidence == "certain"
    assert by_key[("ARTIFACT", "CHILD-A", "CHILD-B", "PRs/demo/CORPUS.md")].confidence == "certain"
    assert by_key[("ADDENDUM", "CHILD-B", "CHILD-A", "PRs/demo/CORPUS.md#doses")].confidence == "certain"

    heuristic = flow_sync.FlowEdge(
        "ARTIFACT",
        "CHILD-A",
        "CHILD-B",
        "heuristic",
        "PRs/demo/weak.md",
        ("temporal-join",),
    )
    same = flow_sync.FlowEdge(
        "ARTIFACT",
        "CHILD-A",
        "CHILD-B",
        "heuristic",
        "PRs/demo/weak.md",
        ("prose-citation",),
    )
    merged = flow_sync.dedupe_edges([heuristic, same])
    assert merged[0].confidence == "heuristic"
    assert set(merged[0].evidence) == {"temporal-join", "prose-citation"}


def test_consumes_and_corrects_conventions_parse():
    consumes = flow_sync._parse_consumes(
        "consumes: [PRs/a.md, factory-automation/x/y.json] · handoffs/demo/current.md"
    )
    assert consumes == [
        "PRs/a.md",
        "factory-automation/x/y.json",
        "handoffs/demo/current.md",
    ]
    match = flow_sync.CORRECTS_RE.search(
        "corrects: PRs/a.md#section-2 · by: CHILD-A · at: 2026-07-31"
    )
    assert match.groups() == ("PRs/a.md", "section-2 ", "CHILD-A")


def test_projection_rows_are_zero_ddl_shapes_and_deterministic(today_tree):
    edges = flow_sync.derive_edges(today_tree)
    handoffs, events, unsupported = flow_sync.project_rows(edges)
    assert len(handoffs) == 10
    assert len(events) == 18
    assert unsupported == []
    assert all(set(row) <= {
        "id", "run_id", "from_agent_id", "to_agent_id", "artifact", "summary"
    } for row in handoffs)
    assert all(row["kind"] == "steering_event" for row in events)
    assert all(row["ref"]["event_subtype"] == "info_flow" for row in events)

    again = flow_sync.project_rows(flow_sync.derive_edges(today_tree))
    assert [row["id"] for row in handoffs] == [row["id"] for row in again[0]]
    assert [row["id"] for row in events] == [row["id"] for row in again[1]]
    sample = next(row for row in handoffs if "type=SPAWN" in row["summary"])
    edge = next(
        e for e in edges
        if e.edge_type == "SPAWN" and e.from_key == "PARENT" and e.to_key == "CHILD-A"
    )
    assert sample["id"] == str(
        uuid.uuid5(NAMESPACE, f"factory_handoffs:{edge.logical_key}")
    )


def test_three_upserts_add_zero_after_first_pass(today_tree):
    edges = flow_sync.derive_edges(today_tree)
    handoffs, events, _ = flow_sync.project_rows(edges)
    stores = {"factory_handoffs": {}, "factory_run_events": {}}

    def apply(table, rows):
        added = sum(row["id"] not in stores[table] for row in rows)
        stores[table].update({row["id"]: row for row in rows})
        return added

    assert (apply("factory_handoffs", handoffs), apply("factory_run_events", events)) == (10, 18)
    assert (apply("factory_handoffs", handoffs), apply("factory_run_events", events)) == (0, 0)
    assert (apply("factory_handoffs", handoffs), apply("factory_run_events", events)) == (0, 0)


def test_runless_edges_are_reported_unsupported_not_given_fake_run():
    edge = flow_sync.FlowEdge(
        "SPAWN",
        "MACHINE-PARENT",
        "MACHINE-CHILD",
        "inferred",
        "spawn:MACHINE-CHILD",
        ("heartbeat/STATE.md",),
    )
    handoffs, events, unsupported = flow_sync.project_rows([edge])
    assert handoffs == []
    assert events == []
    assert unsupported == [edge]


def test_payloads_redact_secrets_and_never_contain_prompt_or_tool_arguments(today_tree):
    today_tree.sessions["CHILD-A"]["goal"] = "secret sk-abcdefghijklmnop"
    today_tree.agent_records[0]["prompt"] += (
        "\nTOOL ARGUMENTS: password=supersecretvalue\n"
        "CHAIN OF THOUGHT: SENTINEL_PRIVATE_REASONING"
    )
    handoffs, events, _ = flow_sync.project_rows(flow_sync.derive_edges(today_tree))
    dumped = json.dumps([handoffs, events])
    assert "sk-abcdefghijklmnop" not in dumped
    assert "supersecretvalue" not in dumped
    assert "SENTINEL_PRIVATE_REASONING" not in dumped
    assert "TOOL ARGUMENTS" not in dumped
    assert "CHAIN OF THOUGHT" not in dumped


def test_live_push_upserts_both_tables_by_id(today_tree):
    edges = flow_sync.derive_edges(today_tree)
    handoffs, events, _ = flow_sync.project_rows(edges)
    with patch.object(flow_sync, "upsert", return_value=[]) as mock_upsert:
        flow_sync.push_rows(handoffs, events, url="http://test", service_role_key="key")
    tables = [call.args[0] for call in mock_upsert.call_args_list]
    assert tables == [
        "factory_handoffs",
        "factory_run_events",
        "factory_run_events",
    ]
    assert all(call.kwargs["on_conflict"] == "id" for call in mock_upsert.call_args_list)
    event_batches = [
        call.args[1] for call in mock_upsert.call_args_list
        if call.args[0] == "factory_run_events"
    ]
    assert all("ts" in row for row in event_batches[0])
    assert all("ts" not in row for row in event_batches[1])


def test_cli_is_dry_run_by_default(today_tree, capsys):
    with patch.object(flow_sync, "load_sources", return_value=today_tree), patch.object(
        flow_sync, "push_rows"
    ) as mock_push:
        assert flow_sync.main([]) == 0
    mock_push.assert_not_called()
    output = capsys.readouterr().out
    assert "18 edge(s)" in output
    assert "dry-run: no writes performed" in output


def test_cli_live_is_explicit_and_failure_is_loud(today_tree, capsys):
    with patch.object(flow_sync, "load_sources", return_value=today_tree), patch.object(
        flow_sync, "push_rows", side_effect=flow_sync.SupabaseRestError("offline")
    ):
        assert flow_sync.main(["--live"]) == 1
    assert "LIVE FAILED" in capsys.readouterr().err


# Cloud-environment guard, added 2026-08-24 (post-trim verification): the scheduled
# wrapper `scripts/factory_agents_sync.sh` is workflow-layer scheduling that lives in
# `emanate-tecum-workflow`; this repo's `scripts/` carries only `lib/redact.py`. Nothing
# in the pipeline shells out to it — a cloud run drives stages through factory.py directly.
@pytest.mark.skipif(
    not (flow_sync.WORKFLOW_ROOT / "scripts" / "factory_agents_sync.sh").is_file(),
    reason="scripts/factory_agents_sync.sh is workflow-layer scheduling, not part of this cloud environment",
)
def test_scheduled_wrapper_invokes_live_only_after_agents_sync():
    wrapper = (flow_sync.WORKFLOW_ROOT / "scripts" / "factory_agents_sync.sh").read_text()
    agents_pos = wrapper.find("python3 agents_sync_job.py")
    flow_pos = wrapper.find("python3 flow_sync.py --live")
    # This assertion goes green only after the post-idempotency integration edit.
    assert agents_pos >= 0
    assert flow_pos > agents_pos
