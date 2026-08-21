#!/usr/bin/env python3
"""Deterministically audit PTC twin-to-source lineage without changing corpus bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


class TwinAuditError(ValueError):
    pass


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def audit(meta_path: str | Path, corpus_path: str | Path) -> dict[str, Any]:
    meta_path, corpus_path = Path(meta_path), Path(corpus_path)
    meta, corpus = _jsonl(meta_path), _jsonl(corpus_path)
    if len(meta) != len(corpus):
        raise TwinAuditError("metadata and corpus line counts differ")
    real = [row for row in meta if row.get("block") == "real_trajectory"]
    twins = [row for row in meta if row.get("block") == "twins"]
    real_by_source: dict[str, dict[str, Any]] = {}
    duplicate_real_sources = []
    for row in real:
        source = row.get("source_row_id")
        if source in real_by_source:
            duplicate_real_sources.append(source)
        real_by_source[source] = row
    twin_sources = [row.get("twin_of") for row in twins]
    source_counts = Counter(twin_sources)
    missing_source = sorted(source for source in source_counts if source not in real_by_source)
    duplicate_twin_sources = sorted(source for source, count in source_counts.items() if count > 1)
    mismatches = []
    chain_differences = 0
    tool_call_records = 0
    tool_result_records = 0
    final_emit_records = 0
    for row in twins:
        source = real_by_source.get(row.get("twin_of"))
        if source:
            for field in ("source_row_id", "account_id", "night", "gold_class"):
                if row.get(field) != source.get(field):
                    mismatches.append(
                        {
                            "twin_line": row.get("line"),
                            "field": field,
                            "twin": row.get(field),
                            "source": source.get(field),
                        }
                    )
            if row.get("chain") != source.get("chain"):
                chain_differences += 1
        record = corpus[int(row["line"]) - 1]
        messages = record.get("messages", [])
        if any(message.get("role") == "assistant" and message.get("tool_calls") for message in messages):
            tool_call_records += 1
        if any(message.get("role") == "tool" for message in messages):
            tool_result_records += 1
        if any(
            call.get("function", {}).get("name") == "emit_output"
            for message in messages
            for call in message.get("tool_calls", [])
        ):
            final_emit_records += 1
    unique_paired_sources = len(source_counts)
    result = {
        "schema_version": 1,
        "meta_sha256": hashlib.sha256(meta_path.read_bytes()).hexdigest(),
        "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        "counts": {
            "corpus_records": len(corpus),
            "real_trajectories": len(real),
            "twins": len(twins),
            "unique_paired_sources": unique_paired_sources,
            "unpaired_real_trajectories": len(real) - unique_paired_sources,
            "duplicate_real_source_ids": len(duplicate_real_sources),
            "duplicate_twin_source_ids": len(duplicate_twin_sources),
            "missing_twin_sources": len(missing_source),
            "retained_real_sources_resolved": len(twins) - len(missing_source),
            "identity_mismatches_on_resolved_sources": len(mismatches),
            "intentional_or_unresolved_chain_differences": chain_differences,
            "twins_with_tool_calls": tool_call_records,
            "twins_with_tool_results": tool_result_records,
            "twins_with_emit_output": final_emit_records,
        },
        "coverage": {
            "selected_candidate_set": (
                unique_paired_sources / len(twins) if twins else None
            ),
            "all_real_trajectories": (
                unique_paired_sources / len(real) if real else None
            ),
            "twin_sources_resolvable_in_retained_real": (
                (len(twins) - len(missing_source)) / len(twins) if twins else None
            ),
        },
        "verdicts": {
            "source_identity_integrity": (
                "PASS"
                if not duplicate_real_sources
                and not duplicate_twin_sources
                and not mismatches
                and None not in source_counts
                else "FAIL"
            ),
            "one_to_one_for_selected_twins": (
                "PASS"
                if len(twins) == unique_paired_sources
                and not duplicate_twin_sources
                and None not in source_counts
                else "FAIL"
            ),
            "one_to_one_for_all_real_trajectories": (
                "PASS" if len(real) == unique_paired_sources else "FAIL"
            ),
            "shallow_probe_semantics": "NOT_ESTABLISHED_BY_CORPUS_LINEAGE",
        },
        "diagnostics": {
            "missing_source_ids": missing_source[:20],
            "duplicate_real_source_ids": sorted(set(duplicate_real_sources))[:20],
            "duplicate_twin_source_ids": duplicate_twin_sources[:20],
            "identity_mismatches_on_resolved_sources": mismatches[:20],
        },
        "interpretation": (
            "Unique twin_of values prove one-to-one selection identity. A twin source need "
            "not itself survive the independent real-trajectory cap, so retained-real "
            "resolvability is reported separately. Chain changes are expected counterfactual "
            "variation, not identity mismatches. Corpus lineage does not prove model "
            "call-vs-answer behavior; that requires a frozen evaluation probe."
        ),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meta", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = audit(args.meta, args.corpus)
    encoded = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(encoded)
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
