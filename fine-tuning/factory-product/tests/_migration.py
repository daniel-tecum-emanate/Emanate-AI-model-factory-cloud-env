"""Shared locator for the Model Factory migration the schema-drift guards read.

Four test modules (`test_sync_push`, `test_sync_agents`, `test_specs_sync`, and any
future one) cross-check their pushed payload's keys against the REAL column list in
`20260725003051_model_factory_tables.sql`. That is the only mechanical link between
this repo's Python sync sidecar and `platform-alpha`'s schema — without it, a renamed
column drifts silently until a live push 400s.

**Why this file exists (2026-07-27, factory-graph-pr1).** Each module used to hardcode
`REPO / "platform-alpha-model-factory-v1" / ...`. That worktree was pruned after PR #2125
merged (heartbeat marked it "safe to remove", validation/QUEUE.md V-072), so all five
guards started raising `FileNotFoundError` at fixture-setup time. pytest reports that as
an **ERROR, not a FAILURE** — it does not turn the suite red the way an assertion does,
so five schema-drift guards sat dead in a suite that still looked like it was passing.

The fix is not to hardcode a different worktree (the next one gets pruned too). Resolve
the migration across whatever checkouts actually exist, preferring a real one, and
`skip` with a loud reason if none has it — a skip is honest about lost coverage in a way
a setup error is not.
"""

import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent  # emanate-tecum-workflow root

# The table that anchors the search. Any migration mentioning a `factory_` table
# contributes to the schema, but this one must exist for the guards to mean
# anything — it is where the nine tables are created.
ANCHOR_MIGRATION = "20260725003051_model_factory_tables.sql"


def _factory_migrations_in(directory):
    """Filenames in `directory` that declare or alter a `factory_` table, sorted."""
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.glob("*.sql") if "factory_" in p.read_text())


def _migrations_dir():
    """The `supabase/migrations` dir carrying the MOST ADVANCED factory schema.

    Scans every `platform-alpha*` sibling rather than naming one, so pruning any
    single worktree can never silently disable the guards again (the failure this
    module was created for: five guards raised FileNotFoundError at fixture setup,
    which pytest reports as an ERROR rather than a FAILURE, so the suite stayed
    green while the coverage was gone).

    **Why "most advanced" and not "first found".** Taking the first match
    alphabetically picks the shared `platform-alpha` checkout, which sits on
    whatever unrelated branch it was last used for — so a session adding a factory
    migration in its own worktree got its guards validated against a schema that
    does not include the migration it is shipping. Observed while writing C1
    (factory-graph-pr2): `trigger_source` read as non-existent and
    `factory_run_digests` as missing, from a checkout that legitimately did not
    have them yet.

    "Most advanced" = the checkout whose newest factory migration has the greatest
    timestamp, tie-broken by how many factory migrations it has. Migrations are
    forward-only and timestamp-ordered in this repo, so the newest filename is a
    sound proxy for the furthest-along schema.
    """
    best = None
    for candidate in sorted(REPO.glob("platform-alpha*")):
        directory = candidate / "supabase" / "migrations"
        if not (directory / ANCHOR_MIGRATION).is_file():
            continue
        names = _factory_migrations_in(directory)
        key = (names[-1] if names else "", len(names))
        if best is None or key > best[0]:
            best = (key, directory)
    return best[1] if best else None


def find_migration():
    """Back-compat: the anchor migration's Path, or None."""
    directory = _migrations_dir()
    return (directory / ANCHOR_MIGRATION) if directory else None


def read_migration():
    """Fixture body: the factory schema as SQL, or a loud skip naming what was searched.

    **Returns EVERY migration that touches a `factory_` table, concatenated in
    timestamp order** — not just the anchor. As of factory-graph-pr2 the schema is
    defined by two files (`..._model_factory_tables.sql` creates the tables;
    `..._factory_decision_kind_and_digests.sql` adds `factory_run_digests` and five
    `factory_runs` columns), and naming one file would make the guards blind to
    everything the other declares — a payload key added against the newer
    migration would read as drift, and a genuinely misspelled one would not.

    Globbing rather than listing filenames is the same reasoning this module
    already applies to worktrees: the next migration gets picked up automatically
    instead of silently falling outside the guard.
    """
    directory = _migrations_dir()
    if directory is None:
        searched = ", ".join(p.name for p in sorted(REPO.glob("platform-alpha*"))) or "(none)"
        pytest.skip(
            f"schema-drift guard disabled: {ANCHOR_MIGRATION} not found in any "
            f"platform-alpha checkout (searched: {searched}). Create a worktree off "
            f"post-#2125 main to restore this coverage."
        )
    parts = []
    for path in sorted(directory.glob("*.sql")):
        text = path.read_text()
        if "factory_" in text:
            parts.append(f"-- ===== {path.name} =====\n{text}")
    return "\n".join(parts)


def columns_for(table, migration_text):
    """Every column `table` has, across CREATE TABLE and later ALTER TABLE ... ADD COLUMN.

    A lightweight parse (this is a test, not a SQL parser) — good enough to catch a
    genuinely renamed or removed column drifting from a sync payload. It must read
    `ALTER TABLE` too, or a column added by a follow-up migration looks like it
    does not exist, which turns the guard into a source of false drift reports.
    """
    m = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);", migration_text, re.DOTALL)
    assert m, f"could not find CREATE TABLE block for {table} in the factory migrations"
    columns = set()
    for line in m.group(1).splitlines():
        line = line.strip().rstrip(",")
        if not line or line.upper().startswith(("UNIQUE", "CHECK", "--", "PRIMARY KEY", "FOREIGN KEY")):
            continue
        columns.add(line.split()[0])

    # `ALTER TABLE <table> ADD COLUMN [IF NOT EXISTS] <name> ...`
    for match in re.finditer(
        rf"ALTER TABLE\s+{table}\s+ADD COLUMN\s+(?:IF NOT EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
        migration_text,
        re.IGNORECASE,
    ):
        columns.add(match.group(1))
    return columns
