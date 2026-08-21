"""T14 — lessons_sync parses the real canon files (not fixtures — PRTests.md is explicit
this is the actual data T2/T9 depend on) into factory_lessons rows.

Expected row count (recorded 2026-07-23, per PRTests.md's "record count here" instruction):
56 lessons (L1-L56, distinct leading integers in PLAYBOOK-AND-LESSONS.md's "## Part 2*"
sections — L34/L34a fold to L34, L38/L38-original fold to L38, the two "**53." paragraphs
fold to one L53 with concatenated statements) + 20 error classes (E1-E19, E21 — E20 was never
assigned a bare id in the register, only E20a-d sub-items in the separate "Daniel MUST SIGN"
table, which this parser intentionally does not read) = 76 total.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

from lessons_sync import (  # noqa: E402
    DEFAULT_ERROR_REGISTER_PATH,
    DEFAULT_PLAYBOOK_PATH,
    build_lesson_rows,
    parse_error_register,
    parse_playbook_lessons,
)

EXPECTED_LESSON_COUNT = 56
EXPECTED_ERROR_COUNT = 20
EXPECTED_TOTAL = EXPECTED_LESSON_COUNT + EXPECTED_ERROR_COUNT


def test_playbook_lesson_count_matches_manual_count():
    rows = parse_playbook_lessons(DEFAULT_PLAYBOOK_PATH.read_text())
    assert len(rows) == EXPECTED_LESSON_COUNT
    codes = [r["code"] for r in rows]
    assert len(codes) == len(set(codes)), "duplicate L-codes emitted"


def test_error_register_count_matches_manual_count():
    rows = parse_error_register(DEFAULT_ERROR_REGISTER_PATH.read_text())
    assert len(rows) == EXPECTED_ERROR_COUNT
    codes = {r["code"] for r in rows}
    assert codes == {f"E{n}" for n in list(range(1, 20)) + [21]}


def test_combined_row_count_matches_manual_count():
    rows = build_lesson_rows()
    assert len(rows) == EXPECTED_TOTAL


def test_duplicate_l53_concatenated_not_dropped():
    rows = parse_playbook_lessons(DEFAULT_PLAYBOOK_PATH.read_text())
    l53 = next(r for r in rows if r["code"] == "L53")
    assert "||" in l53["statement"], "expected both duplicate L53 statements concatenated"
    assert "Passing every designed safeguard" in l53["statement"]
    assert "Daniel directive 2026-07-20" in l53["statement"]


def test_every_row_has_required_fields():
    for row in build_lesson_rows():
        assert row["code"]
        assert row["kind"] in ("lesson", "error_class")
        assert row["statement"]
        assert row["severity"] in ("critical", "minor")


def test_idempotent_reparse_produces_identical_rows():
    first = build_lesson_rows()
    second = build_lesson_rows()
    assert first == second, "re-running the parser must produce identical rows (no drift)"
    codes_first = [r["code"] for r in first]
    assert len(codes_first) == len(set(codes_first)), "no duplicate codes within one parse"
