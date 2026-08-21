#!/usr/bin/env python3
"""artifacts_backfill — one-shot backfill of `factory_agent_artifacts` for the
existing `factory_agents` rows (factory-graph-v2 research/03 §4.6).

**Dry-run by default** (the `project_findings.py` / `flow_sync.py` CLI
precedent): prints the per-origin extraction matrix — runtime × artifact kind
→ content vs unavailable-marker reason — and performs zero writes. Pass
`--live` (alias `--commit`) to actually upsert.

Per design §4.6, unmatchable historical rows are NOT skipped: they get four
unavailable-marker rows (`source_file_missing`) so the product renders them
clickable, honest, and empty — never ambiguous.

Idempotent: every row id is the same deterministic uuid5 the live sync mints
(`graph_artifacts._artifact_id`, shared `backfill_ptc_history.NAMESPACE`), so
re-running the backfill — or the live sync running afterwards — upserts the
identical rows and adds zero.

Usage:
    python3 artifacts_backfill.py                 # dry-run (default)
    python3 artifacts_backfill.py --live          # write
    python3 artifacts_backfill.py --limit 200
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import graph_artifacts  # noqa: E402
from graph_artifacts import (  # noqa: E402
    MissingTableError,
    classify_runtime,
    load_agent_records,
    push_agent_artifacts,
)
from supabase_rest import SupabaseRestError, select  # noqa: E402


def fetch_agent_rows(url=None, service_role_key=None, limit=1000):
    return select(
        "factory_agents",
        params={
            "select": (
                "id,run_id,stage,title,goal,status,cursor_agent_id,"
                "ledger_session_id,report_path,final_response,report_summary"
            ),
            "order": "started_at.asc",
            "limit": str(limit),
        },
        url=url,
        service_role_key=service_role_key,
    )


def backfill(
    agent_rows,
    agent_records=None,
    cursor_transcripts_dir=None,
    claude_projects_dir=None,
    url=None,
    service_role_key=None,
    live=False,
    src_sink=None,
):
    """Process every row; returns (matrix, processed, failed).

    `matrix` is a Counter keyed (runtime, kind, outcome) where outcome is
    'content', 'unchanged', or 'marker:<reason>' — the §4.6 per-origin matrix,
    reported rather than assumed. `src_sink`, when given, collects each row's
    resolved sources so the caller can run the parent-edge pass afterwards.
    """
    matrix = Counter()
    processed = 0
    failed = 0
    index_cache = {}
    for row in agent_rows:
        runtime = classify_runtime(row)
        try:
            summary = push_agent_artifacts(
                row,
                agent_records=agent_records,
                cursor_transcripts_dir=cursor_transcripts_dir,
                claude_projects_dir=claude_projects_dir,
                url=url,
                service_role_key=service_role_key,
                dry_run=not live,
                index_cache=index_cache,
                src_sink=src_sink,
            )
        except MissingTableError:
            raise
        except (SupabaseRestError, OSError, ValueError) as exc:
            print(f"  FAILED {row.get('title')}: {exc}", file=sys.stderr)
            failed += 1
            continue
        if summary is None:
            failed += 1
            continue
        processed += 1
        for kind, outcome in summary.items():
            matrix[(runtime, kind, outcome)] += 1
    return matrix, processed, failed


def print_matrix(matrix):
    runtimes = sorted({key[0] for key in matrix})
    for runtime in runtimes:
        print(f"\n  {runtime}:")
        kinds = ("prompt", "tool_calls", "thinking", "output")
        for kind in kinds:
            outcomes = {
                key[2]: count for key, count in matrix.items() if key[0] == runtime and key[1] == kind
            }
            rendered = ", ".join(f"{o}={c}" for o, c in sorted(outcomes.items())) or "—"
            print(f"    {kind:<10} {rendered}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit", "--live", action="store_true", dest="live",
        help="write to factory_agent_artifacts (default is dry-run)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="explicit no-write mode (also overrides --live)",
    )
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--ledger-agents-days", type=int, default=30,
                        help="how far back to read ledger/agents for correlation "
                             "(backfill wants a wider window than the live poll)")
    parser.add_argument("--url", default=None)
    parser.add_argument("--service-role-key", default=None)
    args = parser.parse_args(argv)
    live = args.live and not args.dry_run

    try:
        agent_rows = fetch_agent_rows(
            url=args.url, service_role_key=args.service_role_key, limit=args.limit
        )
    except SupabaseRestError as exc:
        print(f"artifacts_backfill: could not read factory_agents — {exc}", file=sys.stderr)
        return 1

    agent_records = load_agent_records(days=args.ledger_agents_days)
    print(
        f"artifacts_backfill: {len(agent_rows)} factory_agents row(s), "
        f"{len(agent_records)} ledger/agents record(s) for correlation"
    )

    src_by_agent = {}
    try:
        matrix, processed, failed = backfill(
            agent_rows,
            agent_records=agent_records,
            url=args.url,
            service_role_key=args.service_role_key,
            live=live,
            src_sink=src_by_agent,
        )
    except MissingTableError as exc:
        print(
            "artifacts_backfill: factory_agent_artifacts table does not exist yet "
            f"— apply the platform-repo migration first: {exc}",
            file=sys.stderr,
        )
        return 1

    print_matrix(matrix)
    print(f"\nartifacts_backfill: {processed} processed, {failed} failed")

    # Parent-edge pass (design §3, factory-graph-v2 coverage round 2):
    # fail-open inside; dry-run derives + logs the edges without a PATCH.
    transcript_matches = {
        agent_id: src["transcript_path"]
        for agent_id, src in src_by_agent.items()
        if src.get("transcript_path")
    }
    edge_summary = graph_artifacts.sync_parent_edges(
        transcript_matches=transcript_matches,
        url=args.url,
        service_role_key=args.service_role_key,
        dry_run=not live,
    )
    if edge_summary is None:
        print("parent edges: pass skipped (fail-open)")
    else:
        print(f"parent edges: {edge_summary}")

    if not live:
        print("dry-run: no writes performed (pass --live to write).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
