# `factory_lessons`

The Lessons tab's browse-only data source — every confirmed lesson and error class the
company has ever recorded, made queryable. `kind` distinguishes the two source documents.

**Populated by:** `lessons_sync.build_lesson_rows()` → `push_lesson_rows()` —
`fine-tuning/factory-product/lessons_sync.py`, upserted `on_conflict=code`.
**Real source of truth:**
- `company-brain/3-execution/PLAYBOOK-AND-LESSONS.md` → `kind='lesson'`, `code='L<N>'`
- `company-brain/3-execution/ERROR-REGISTER.md` → `kind='error_class'`, `code='E<N>'`

These are **hand-maintained canon files**, never edited by this sync — it is read-only
against them.

## Columns

| column | type | constraints | notes |
|---|---|---|---|
| `id` | uuid | PK | |
| `code` | text | NOT NULL, **UNIQUE** | `L<N>` or `E<N>`, the canon file's own numbering |
| `kind` | text | NOT NULL, IN `('lesson','error_class')` | |
| `statement` | text | NOT NULL | the lesson's bold-numbered headline / the error class name |
| `mechanical_check` | text | nullable | trailing body text (lessons) / root-cause+fix+status+disposition summary (error classes) |
| `stage` | text | nullable | **always NULL** — see below |
| `severity` | text | NOT NULL, IN `('critical','minor')` | heuristic, not an explicit source field — see below |
| `source_run_id` | uuid | FK → `factory_runs(id)`, nullable | not currently populated |
| `updated_at` | timestamptz | NOT NULL, default `now()` | DB-managed |

## Parsing rules (both scoped to avoid double-counting)

- `PLAYBOOK-AND-LESSONS.md`: only the `## Part 2*` sections are parsed — Part 1's pipeline
  table and Part 3's checklist merely *cite* lesson numbers inline (e.g. "(L18)") and are
  excluded by scoping the regex search to that range. A lesson's `code` is `L<N>` from the
  **leading integer only** — suffixes like `34-original`/`34a` fold as continuation text
  under the same code, not a new one. One genuine duplicate exists (two separate "**53.
  ...**" paragraphs) — on a duplicate code, both statements are concatenated (`" || "`)
  rather than the later one silently overwriting the earlier.
- `ERROR-REGISTER.md`: only the **first** table ("## The register — one row per DISTINCT
  error class ever observed") is parsed. The second table ("Daniel MUST SIGN") re-lists
  some of the same ids as open decisions, not new error classes, and is deliberately
  excluded — parsing it too would double-count several ids.

## Severity — a documented heuristic, not an explicit source field

Neither canon file has a severity column, so this is an implementer's call
(`DELTAS.md`), not a hard invariant:
- **error_class**: `minor` iff the register's own status column starts with `fixed` or
  `improved` (a bar the class actually cleared); everything else defaults to `critical`.
- **lesson**: `critical` if the statement/body contains any of a small set of markers the
  canon file itself uses to flag corrected/overriding guidance (`[Daniel directive`,
  `[CORRECTED`, `[FALSIFIED`, `[MANDATORY`, `NO-GO`, `regressed`); `minor` otherwise.

This is a **display-only** classification — the v1 Lessons tab is browse-only, with no
enforcement linkage (`DECISIONS.md`).

## Known gaps / accepted risks

- `stage` is deliberately left NULL for every row: neither source file maps its entries to
  a specific pipeline stage, and inventing that mapping would be a fabrication, not a
  parse. The Lessons tab's per-row stage filter degrades gracefully against nulls.
- `source_run_id` has a write path (`attribute_lesson_to_run`, "first mint wins," added
  PR "factory-graph-pr1") but zero callers today — nobody has attributed any of the 56
  historical lessons to the 4 backfilled PTC Steel runs that most likely minted many of
  them. That attribution pass has not been done; every row's `source_run_id` is still NULL.
- Fails loudly on push failure (raises) — this is a periodic canon-sync job, not part of
  the live gate pipeline, so there is no fail-open/fail-closed direction to preserve; a
  broken sync should be visible, not silently absorbed.
- **This sync was built at T14 (2026-07-23-ish) but never actually run against production
  until 2026-07-28** — the Lessons tab was empty in production the entire time despite the
  code being complete and tested. Run for real 2026-07-28: 76 rows (56 lessons + 20 error
  classes). It is a one-time/on-demand script, not scheduled (`PROCESSES.md` has no entry
  for it) — re-running it picks up any edits to the two canon files since, but nothing
  currently triggers that automatically. See `workspace/GAPS.md`.

## Example rows

```json
[
  { "code": "L22", "kind": "lesson", "severity": "minor", "statement": "trust platform estimatedTokenCount over tiktoken", "stage": null },
  { "code": "E5",  "kind": "error_class", "severity": "critical", "statement": "..." , "stage": null }
]
```
