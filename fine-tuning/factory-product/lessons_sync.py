#!/usr/bin/env python3
"""lessons_sync — T14. Parses the two canon lesson files into `factory_lessons`
rows and upserts them (by `code`) into Supabase.

Sources (read-only, never edited by this script):
    company-brain/3-execution/PLAYBOOK-AND-LESSONS.md  -> kind='lesson',   code='L<N>'
    company-brain/3-execution/ERROR-REGISTER.md         -> kind='error_class', code='E<N>'

Convention (recorded in factory-automation/product-integration/DELTAS.md D6):
    - PLAYBOOK-AND-LESSONS.md lessons are bold numbered paragraphs, `**NN. text**` or
      `**NN<suffix>. text**` (e.g. `34-original`, `38-original`, `34a`), found only inside the
      "## Part 2*" sections (Part 1's pipeline table and Part 3's checklist merely cite lesson
      numbers inline like "(L18)" and are excluded by scoping the search to that range).
    - `code` is `L<N>` from the LEADING INTEGER only — a suffix does not mint a new code, it is
      folded as continuation text under the same code. The file also has one genuine duplicate
      (two separate "**53. ...**" paragraphs with different content) — on a duplicate code, both
      statements are concatenated (" || ") rather than the later one silently overwriting the
      earlier, so no real content is lost on upsert-by-code.
    - ERROR-REGISTER.md error classes are rows of the FIRST table only ("## The register — one row
      per DISTINCT error class ever observed"), `| **EN** | plain words | ... |`. The second table
      ("Daniel MUST SIGN") re-lists some of the same ids as open decisions, not new error classes,
      and is intentionally excluded — parsing it too would double-count E5/E17-risk/E18/E20*.

Severity is not an explicit field in either source file, so this parser applies a documented
heuristic (flagged in DELTAS.md as an implementer's call, not a hard invariant):
    - error_class: 'minor' iff the register's own status column starts with "fixed" or
      "improved" (a bar the class actually cleared); everything else (persists, regressed, split,
      never-remeasured) is 'critical' — a browse-only Lessons tab should default to caution on
      open/unmeasured classes rather than a false "minor".
    - lesson: 'critical' if the statement/body contains any of a small set of markers that the
      canon file itself uses to flag corrected/overriding guidance or a hard directive
      (`[Daniel directive`, `[CORRECTED`, `[FALSIFIED`, `[MANDATORY`, `NO-GO`, `regressed`);
      'minor' otherwise. This is a display-only classification (v1 Lessons tab is browse-only,
      no enforcement linkage per DECISIONS.md) — refining it later costs nothing.

`stage` (pipeline stage S1-S11) is deliberately left NULL for every row here: neither source file
maps its entries to a specific factory stage, and inventing that mapping would be a fabrication,
not a parse. The Lessons tab's per-row stage filter (T10-UI) degrades gracefully against nulls.
"""

import argparse
import re
import sys
from pathlib import Path

from supabase_rest import SupabaseRestError, select, update, upsert

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # emanate-tecum-workflow root

DEFAULT_PLAYBOOK_PATH = REPO / "company-brain" / "3-execution" / "PLAYBOOK-AND-LESSONS.md"
DEFAULT_ERROR_REGISTER_PATH = REPO / "company-brain" / "3-execution" / "ERROR-REGISTER.md"

_LESSON_LINE_RE = re.compile(
    r"^\*\*(?P<num>\d+)(?P<suffix>[a-zA-Z-]*(?:original)?)\.\s*"
    r"(?P<statement>.+?)\*\*(?P<body>.*)$"
)

_CRITICAL_LESSON_MARKERS = (
    "[Daniel directive",
    "[CORRECTED",
    "[FALSIFIED",
    "[MANDATORY",
    "NO-GO",
    "regressed",
)


def _lesson_severity(statement, body):
    haystack = f"{statement} {body}"
    for marker in _CRITICAL_LESSON_MARKERS:
        if marker in haystack:
            return "critical"
    return "minor"


def _error_severity(status_field):
    normalized = status_field.strip().lstrip("*").lower()
    if normalized.startswith("fixed") or normalized.startswith("improved"):
        return "minor"
    return "critical"


def _clean(text):
    """Strip markdown bold/backtick noise and collapse whitespace for a display field."""
    text = text.strip()
    text = text.strip("*").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def parse_playbook_lessons(text):
    """Parse PLAYBOOK-AND-LESSONS.md into factory_lessons rows (kind='lesson').

    Scoped to the "## Part 2" family (from the first "## Part 2" heading up to the
    "## Part 3" heading) per DELTAS.md D6 — Part 1/Part 3 only ever cite lesson numbers
    inline and must not be parsed as entries.
    """
    part2_start = text.find("## Part 2")
    part3_start = text.find("## Part 3")
    if part2_start == -1:
        raise ValueError("PLAYBOOK-AND-LESSONS.md: no '## Part 2' section found")
    scoped = text[part2_start : part3_start if part3_start != -1 else len(text)]

    by_code = {}
    order = []
    for line in scoped.splitlines():
        m = _LESSON_LINE_RE.match(line.strip())
        if not m:
            continue
        code = f"L{m.group('num')}"
        statement = _clean(m.group("statement"))
        body = _clean(m.group("body"))
        if code in by_code:
            existing = by_code[code]
            existing["statement"] = f"{existing['statement']} || {statement}"
            existing["mechanical_check"] = (
                f"{existing['mechanical_check']} || {body}" if body else existing["mechanical_check"]
            )
        else:
            by_code[code] = {
                "code": code,
                "kind": "lesson",
                "statement": statement,
                "mechanical_check": body or None,
                "stage": None,
            }
            order.append(code)

    rows = []
    for code in order:
        row = by_code[code]
        row["severity"] = _lesson_severity(row["statement"], row["mechanical_check"] or "")
        rows.append(row)
    return rows


def parse_error_register(text):
    """Parse ERROR-REGISTER.md's primary register table into factory_lessons rows
    (kind='error_class'). Only "## The register" table is parsed — the later
    "Daniel MUST SIGN" table re-lists some of the same ids as open decisions and is
    excluded (see module docstring).
    """
    register_start = text.find("## The register")
    if register_start == -1:
        raise ValueError("ERROR-REGISTER.md: no '## The register' section found")
    next_section = text.find("\n## ", register_start + 1)
    scoped = text[register_start : next_section if next_section != -1 else len(text)]

    row_re = re.compile(r"^\|\s*\*\*(E\d+)\*\*\s*\|(.*)\|\s*$")
    rows = []
    seen = set()
    for line in scoped.splitlines():
        m = row_re.match(line.strip())
        if not m:
            continue
        code = m.group(1)
        if code in seen:
            continue  # defensive: the primary table has no real duplicates today
        seen.add(code)
        cells = [c.strip() for c in m.group(2).split("|")]
        # Columns after id: error class, first seen, root cause, fix attempted, status, disposition
        cells += [""] * (5 - len(cells))
        error_class, first_seen, root_cause, fix_attempted, status = cells[:5]
        disposition = cells[5] if len(cells) > 5 else ""
        statement = _clean(error_class)
        mechanical_check = _clean(
            f"root cause: {root_cause} | fix attempted: {fix_attempted} | "
            f"status: {status} | v5 disposition: {disposition}"
        )
        rows.append(
            {
                "code": code,
                "kind": "error_class",
                "statement": statement,
                "mechanical_check": mechanical_check,
                "stage": None,
                "severity": _error_severity(status),
            }
        )
    return rows


def build_lesson_rows(playbook_path=None, error_register_path=None):
    playbook_path = Path(playbook_path or DEFAULT_PLAYBOOK_PATH)
    error_register_path = Path(error_register_path or DEFAULT_ERROR_REGISTER_PATH)
    lessons = parse_playbook_lessons(playbook_path.read_text())
    errors = parse_error_register(error_register_path.read_text())
    return lessons + errors


def push_lesson_rows(rows, url=None, service_role_key=None):
    """Upsert all rows by `code`. Fails loudly (raises) on any push error — this is a
    periodic canon-sync job, not part of the live gate pipeline, so there is no
    fail-open/fail-closed direction to preserve here; a broken sync should be visible,
    not silently absorbed (mirrors T15's "malformed spec fails loudly" spirit).
    """
    return upsert("factory_lessons", rows, on_conflict="code", url=url, service_role_key=service_role_key)


# --------------------------------------------------------------------------
# B4 (factory-graph-pr1, 2026-07-27): lesson provenance.
#
# Everything ABOVE this line is the canon sync and is deliberately unchanged.
# It parses PLAYBOOK-AND-LESSONS.md / ERROR-REGISTER.md and upserts by `code`,
# and it must stay the single writer of `statement`/`severity`/`mechanical_check`.
#
# `factory_lessons.source_run_id` has existed since the Model Factory migration
# with NO writer anywhere, so "which run taught us this?" has been unanswerable
# — which is precisely the link a meta-learning pass over the factory's own
# history would need. This is that writer, and it is deliberately separate: it
# is additive metadata on an existing row, never part of the canon parse.
# --------------------------------------------------------------------------


def attribute_lesson_to_run(code, run_id, url=None, service_role_key=None):
    """Stamp `source_run_id` on an existing lesson — **first mint wins.**

    A lesson is minted BY the run that first surfaced it. The canon files are
    re-synced routinely, and a lesson's text may be edited months later; neither
    changes which run discovered it. So this writes only when the column is
    currently NULL and never overwrites an existing value. Getting that
    backwards would mean every canon re-sync silently re-attributes the entire
    lesson history to whatever run happened to be open — provenance that
    rewrites itself is worse than no provenance, because it looks trustworthy.

    Returns True if this call set the value, False if it was already set or the
    lesson doesn't exist. Raises on transport errors, matching this module's
    fail-loudly stance (it is a maintenance job, not a live gate path).
    """
    if not code or not run_id:
        raise ValueError("both code and run_id are required to attribute a lesson")

    existing = select(
        "factory_lessons",
        params={"code": f"eq.{code}", "select": "code,source_run_id", "limit": "1"},
        url=url,
        service_role_key=service_role_key,
    )
    if not existing:
        return False
    if existing[0].get("source_run_id"):
        return False

    # `source_run_id=is.null` in the filter makes the guard atomic rather than
    # merely check-then-write: a concurrent attribution between the select above
    # and this update loses, instead of both appearing to succeed.
    updated = update(
        "factory_lessons",
        {"code": f"eq.{code}", "source_run_id": "is.null"},
        {"source_run_id": run_id},
        url=url,
        service_role_key=service_role_key,
    )
    return bool(updated)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--playbook", default=None, help="override PLAYBOOK-AND-LESSONS.md path")
    parser.add_argument("--error-register", default=None, help="override ERROR-REGISTER.md path")
    parser.add_argument("--dry-run", action="store_true", help="parse and print, do not push")
    args = parser.parse_args(argv)

    rows = build_lesson_rows(args.playbook, args.error_register)
    print(f"lessons_sync: parsed {len(rows)} rows "
          f"({sum(1 for r in rows if r['kind'] == 'lesson')} lessons, "
          f"{sum(1 for r in rows if r['kind'] == 'error_class')} error classes)")

    if args.dry_run:
        for row in rows:
            print(f"  {row['code']:10s} [{row['severity']:8s}] {row['statement'][:100]}")
        return 0

    try:
        push_lesson_rows(rows)
    except SupabaseRestError as exc:
        print(f"lessons_sync: PUSH FAILED — {exc}", file=sys.stderr)
        return 1
    print("lessons_sync: pushed OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
