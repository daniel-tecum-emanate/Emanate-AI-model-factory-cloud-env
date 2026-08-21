import json
import sys
from pathlib import Path

FACTORY_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FACTORY_DIR / "swarm"))

from audit_ptc_twins import audit  # noqa: E402


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def test_twin_audit_distinguishes_selected_pairing_from_full_coverage(tmp_path):
    meta = [
        {
            "line": 1,
            "block": "real_trajectory",
            "source_row_id": "r1",
            "account_id": "a",
            "night": "n",
            "gold_class": "no_action",
            "chain": ["read"],
        },
        {
            "line": 2,
            "block": "real_trajectory",
            "source_row_id": "r2",
            "account_id": "b",
            "night": "n",
            "gold_class": "no_action",
            "chain": ["read"],
        },
        {
            "line": 3,
            "block": "twins",
            "source_row_id": "r1",
            "twin_of": "r1",
            "account_id": "a",
            "night": "n",
            "gold_class": "no_action",
            "chain": ["read"],
        },
    ]
    corpus = [
        {"messages": []},
        {"messages": []},
        {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"function": {"name": "read"}},
                        {"function": {"name": "emit_output"}},
                    ],
                },
                {"role": "tool", "content": "{}"},
            ]
        },
    ]
    meta_path, corpus_path = tmp_path / "meta.jsonl", tmp_path / "corpus.jsonl"
    _write_jsonl(meta_path, meta)
    _write_jsonl(corpus_path, corpus)

    result = audit(meta_path, corpus_path)
    assert result["verdicts"]["source_identity_integrity"] == "PASS"
    assert result["verdicts"]["one_to_one_for_selected_twins"] == "PASS"
    assert result["verdicts"]["one_to_one_for_all_real_trajectories"] == "FAIL"
    assert result["coverage"]["all_real_trajectories"] == 0.5
    assert result["verdicts"]["shallow_probe_semantics"] == "NOT_ESTABLISHED_BY_CORPUS_LINEAGE"


def test_source_outside_retained_real_cap_is_reported_not_mislabeled(tmp_path):
    meta = [
        {
            "line": 1,
            "block": "twins",
            "source_row_id": "staged-only",
            "twin_of": "staged-only",
            "account_id": "a",
            "night": "n",
            "gold_class": "no_action",
            "chain": ["read"],
        }
    ]
    corpus = [{"messages": []}]
    meta_path, corpus_path = tmp_path / "meta.jsonl", tmp_path / "corpus.jsonl"
    _write_jsonl(meta_path, meta)
    _write_jsonl(corpus_path, corpus)

    result = audit(meta_path, corpus_path)
    assert result["verdicts"]["source_identity_integrity"] == "PASS"
    assert result["verdicts"]["one_to_one_for_selected_twins"] == "PASS"
    assert result["counts"]["retained_real_sources_resolved"] == 0
    assert result["coverage"]["twin_sources_resolvable_in_retained_real"] == 0
