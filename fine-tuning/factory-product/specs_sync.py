#!/usr/bin/env python3
"""specs_sync — T15. Parses `factory/specs/*.yaml` (T10's generated AgentSpec catalog)
into `factory_specs` rows and upserts them (by `slug`, the table's real UNIQUE column —
unlike T12's/T13's tables, this one has a genuine on_conflict target) into Supabase.

A malformed YAML file (parse error) or a spec missing a field `factory_specs` requires
NOT NULL (slug/role_family/mission/done_when) fails LOUDLY — raises immediately, does not
skip the file and quietly produce "one fewer row" (PRContext.md T15's own accept line:
"a broken spec file should never look like the catalog just has fewer entries today").
This mirrors lessons_sync.py's own "fails loudly" framing (T14), not T11's fail-open or
T12's fail-closed — a catalog sync has no live pipeline stage riding on it either way, so
there is no direction to preserve except "never lie about the file count."
"""

import argparse
import sys
from pathlib import Path

import yaml

from supabase_rest import SupabaseRestError, upsert

HERE = Path(__file__).resolve().parent
SPECS_DIR = HERE / "specs"
sys.path.insert(0, str(SPECS_DIR))

from validate_specs import spec_files  # noqa: E402  (shared with T10's own file list)

REQUIRED_FIELDS = ("slug", "role_family", "mission", "done_when")


class SpecSyncError(ValueError):
    """Raised on a malformed spec file — never swallowed, per T15's Accept line."""


def _load_spec(path):
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise SpecSyncError(f"{path.name}: malformed YAML — {exc}") from exc
    if not isinstance(data, dict):
        raise SpecSyncError(f"{path.name}: top-level YAML is not a mapping")
    missing = [field for field in REQUIRED_FIELDS if not data.get(field)]
    if missing:
        raise SpecSyncError(f"{path.name}: missing required field(s) {missing}")
    if data["slug"] != path.stem:
        raise SpecSyncError(f"{path.name}: slug {data['slug']!r} does not match filename")
    return data


def spec_row(path):
    """Build one `factory_specs` row dict from a single real spec YAML file. The whole
    parsed document is stored verbatim in `spec_body` (jsonb) — the table's promoted
    columns (role_family/mission/stages/done_when/typical_count/evidence_pointer) are a
    queryable projection of it, not a second source of truth.
    """
    data = _load_spec(path)
    return {
        "slug": data["slug"],
        "role_family": data["role_family"],
        "mission": data["mission"],
        "stages": data.get("stages") or [],
        "done_when": data["done_when"],
        "typical_count": str(data["typical_count"]) if data.get("typical_count") is not None else None,
        "evidence_pointer": data.get("evidence_pointer"),
        "spec_version": data.get("spec_version", "v1"),
        "spec_body": data,
        "status": data.get("status", "draft"),
    }


def build_spec_rows(paths=None):
    """Parse every spec file into a `factory_specs` row. Raises `SpecSyncError` on the
    FIRST malformed file encountered — deliberately not a "collect all errors and
    continue" loop, so a broken file can never silently vanish from the result set.
    """
    paths = paths if paths is not None else spec_files()
    return [spec_row(path) for path in paths]


def push_spec_rows(rows, url=None, service_role_key=None):
    """Upsert all rows by `slug` — `factory_specs.slug` has a real UNIQUE constraint
    (verified against T1's migration), so this is a genuine PostgREST on_conflict
    upsert, unlike T12's/T13's tables which need a select-then-write workaround.
    Raises SupabaseRestError on failure — not swallowed, same "fails loudly" framing
    as the parse step above.
    """
    return upsert("factory_specs", rows, on_conflict="slug", url=url, service_role_key=service_role_key)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="parse and print, do not push")
    args = parser.parse_args(argv)

    try:
        rows = build_spec_rows()
    except SpecSyncError as exc:
        print(f"specs_sync: PARSE FAILED — {exc}", file=sys.stderr)
        return 1

    print(f"specs_sync: parsed {len(rows)} rows from {len(spec_files())} spec files")

    if args.dry_run:
        for row in rows:
            print(f"  {row['slug']:24s} [{row['status']:8s}] {row['mission'][:80]}")
        return 0

    try:
        push_spec_rows(rows)
    except SupabaseRestError as exc:
        print(f"specs_sync: PUSH FAILED — {exc}", file=sys.stderr)
        return 1
    print("specs_sync: pushed OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
