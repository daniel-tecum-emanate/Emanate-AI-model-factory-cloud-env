#!/usr/bin/env python3
"""org_config_sync — PR-A (factory-observability-v1) B3. Projects each
`factory/configs/<slug>.yaml`'s `org:` and `governance:` blocks into the
`factory_org_configs` table.

## Why this table has to exist at all

`factory_runs.org_slug` (`ptc-steel`, `grand-steel`) and the product's real
`org_id` (`10664bd2-79d2-49d2-8196-fcaf539d9eb3`) are two different identity
systems, and until this sync there was **no table in Supabase bridging them**
(PRContext.md V6). The only place both appear together is these YAML files, on
one operator's laptop. That is why the Model Factory tab could never join a
factory run to the customer it is training a model for.

`factory_org_configs` is that bridge, and PRRules invariant 16 makes it the ONLY
one: no caller may fuzzy-match `org_name` against `organizations.name` to guess a
mapping. An org with no config row is "unmapped," rendered as such, never guessed.

## Fail direction: LOUD (and why that is not a contradiction)

`factory_sync.push_stage`/`push_agents` are fail-OPEN because a telemetry push
must never take down a real pipeline stage that has a report to write. This is
not that. It is a maintenance job invoked by hand or by a schedule, exactly like
`specs_sync.py` and `lessons_sync.py`, with no live stage riding on it — so a
push failure that got swallowed here would present as "governance data is
mysteriously stale," which is worse than a non-zero exit. PRRules rule 4 names
both directions specifically so this one is not confused for the other.

A config missing `org.id` therefore raises rather than upserting `org_id: null`:
a null there does not read as "missing," it reads as a real org that legitimately
has no product identity, which is a claim this file has no business making.

## `_template.yaml` is skipped by NAME, not by validation

`_template.yaml`'s `org.id` is the literal string `<org-uuid>`. It would fail a
real-UUID check, so relying on validation to reject it would "work" today — and
would silently start writing a row the moment someone made the template's
placeholders more realistic. It is excluded by filename (any `_`-prefixed file),
before parsing, so the exclusion cannot rot.

## PII

`factory_org_configs` carries an org slug, an org UUID, an org display name, and
six governance flags/enums. No account names, no transcript content, no revenue
figures — the `book:` block of each config (account counts, revenue) is
deliberately NOT projected here; the readiness view (A1) derives counts live from
the real product tables instead of trusting a hand-maintained snapshot.
`tests/test_org_config_sync.py` asserts this against a PII-laden fixture
(PRRules rule 6 — a comment does not satisfy it, a passing test does).
"""

import argparse
import sys
from pathlib import Path

import yaml

from supabase_rest import SupabaseRestError, upsert

HERE = Path(__file__).resolve().parent
CONFIGS_DIR = HERE / "configs"

# The exact column set this sync writes. Kept as an explicit tuple rather than
# "whatever the YAML happened to contain" so a new key added to a config file is
# invisible in the DB until someone deliberately adds it here — the same
# allow-list philosophy as `factory_sync.SAFE_REPORT_KEYS`, applied to a much
# smaller surface.
GOVERNANCE_FIELDS = (
    "data_use_clearance",
    "dpa_signed",
    "training_rights_clause",
    "pii_egress_cleared",
)


class OrgConfigSyncError(ValueError):
    """Raised on a malformed or incomplete config — never swallowed. Mirrors
    `specs_sync.SpecSyncError`'s framing: a broken config file must never look
    like the org simply isn't governed."""


def config_files(configs_dir=None):
    """Every real org config, sorted, with `_`-prefixed files excluded.

    Sorted so the row order is deterministic across machines — the
    `_template.yaml` exclusion is name-based and therefore order-independent, but
    a stable order makes `--dry-run` output diffable between runs.
    """
    directory = Path(configs_dir) if configs_dir is not None else CONFIGS_DIR
    return sorted(p for p in directory.glob("*.yaml") if not p.name.startswith("_"))


def _load_config(path):
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise OrgConfigSyncError(f"{path.name}: malformed YAML — {exc}") from exc
    if not isinstance(data, dict):
        raise OrgConfigSyncError(f"{path.name}: top-level YAML is not a mapping")
    return data


def config_row(path):
    """Build one `factory_org_configs` row from a single real config file.

    `permitted_uses` is copied verbatim as a list (jsonb). An empty list is a
    real, meaningful value here — `grand-steel.yaml` has `permitted_uses: []`
    because nothing has been cleared yet — so it must not be coerced to NULL.
    """
    data = _load_config(path)
    org = data.get("org") or {}
    if not isinstance(org, dict):
        raise OrgConfigSyncError(f"{path.name}: `org:` block is not a mapping")

    slug = org.get("slug")
    if not slug:
        raise OrgConfigSyncError(f"{path.name}: missing `org.slug`")
    if slug != path.stem:
        raise OrgConfigSyncError(
            f"{path.name}: org.slug {slug!r} does not match filename — "
            "the slug is this table's natural key and the runner resolves configs by filename, "
            "so a mismatch means two different identities for one org"
        )
    if not org.get("id"):
        # Deliberately NOT a null upsert. See the module docstring.
        raise OrgConfigSyncError(
            f"{path.name}: missing `org.id` — refusing to upsert org_id NULL, which would read as "
            "'this org has no product identity' rather than 'nobody has recorded it yet' "
            "(PRRules invariant 16: factory_org_configs is the only org_slug<->org_id bridge)"
        )
    if not org.get("name"):
        raise OrgConfigSyncError(f"{path.name}: missing `org.name` (factory_org_configs.org_name is NOT NULL)")

    governance = data.get("governance") or {}
    if not isinstance(governance, dict):
        raise OrgConfigSyncError(f"{path.name}: `governance:` block is not a mapping")

    permitted = governance.get("permitted_uses")
    if permitted is None:
        permitted = []
    if not isinstance(permitted, list):
        raise OrgConfigSyncError(
            f"{path.name}: governance.permitted_uses must be a list, got {type(permitted).__name__}"
        )

    row = {
        "org_slug": slug,
        "org_id": str(org["id"]),
        "org_name": str(org["name"]),
        "customer_status": org.get("customer_status"),
        "permitted_uses": list(permitted),
    }
    for field in GOVERNANCE_FIELDS:
        row[field] = governance.get(field)
    return row


def build_rows(configs_dir=None, paths=None):
    """Parse every real config into a row. Raises on the FIRST bad file rather
    than collecting errors and continuing — same reason `specs_sync` does: a
    broken file must not be able to silently vanish from the result set.

    Also refuses two different `org_slug`s that carry the same `org_id`
    (Greptile review, platform-alpha#2169): the table this writes to is
    documented as "the ONLY org_slug <-> org_id bridge" (PRRules invariant 16),
    and a stale copy-pasted or renamed config would break that 1:1 claim
    silently — the DB's own `uq_factory_org_configs_org_id` partial unique
    index would catch it too, but only as an opaque PostgREST 409 with no
    slug names in it, which is a worse failure to hand an operator than this.
    """
    paths = paths if paths is not None else config_files(configs_dir)
    rows = [config_row(Path(p)) for p in paths]

    seen_org_ids = {}
    for row in rows:
        prior_slug = seen_org_ids.get(row["org_id"])
        if prior_slug is not None:
            raise OrgConfigSyncError(
                f"{row['org_slug']}.yaml: org.id {row['org_id']!r} is already used by "
                f"{prior_slug}.yaml — factory_org_configs.org_id must be unique "
                "(PRRules invariant 16: the org_slug<->org_id bridge is 1:1)"
            )
        seen_org_ids[row["org_id"]] = row["org_slug"]

    return rows


def push_rows(rows, url=None, service_role_key=None):
    """Upsert by `org_slug`, which has a real UNIQUE constraint in A1's
    migration — a genuine PostgREST on_conflict upsert, not the
    select-then-write workaround `factory_sync._upsert_agent_row` needs.
    Raises `SupabaseRestError` on failure (see the module docstring on fail
    direction).
    """
    return upsert(
        "factory_org_configs",
        rows,
        on_conflict="org_slug",
        url=url,
        service_role_key=service_role_key,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sync factory/configs/*.yaml governance into factory_org_configs")
    parser.add_argument("--dry-run", action="store_true", help="parse and print, do not push")
    parser.add_argument("--configs-dir", default=None, help="override the configs directory (testing)")
    args = parser.parse_args(argv)

    try:
        rows = build_rows(configs_dir=args.configs_dir)
    except OrgConfigSyncError as exc:
        print(f"org_config_sync: PARSE FAILED — {exc}", file=sys.stderr)
        return 1

    files = config_files(args.configs_dir)
    print(f"org_config_sync: parsed {len(rows)} rows from {len(files)} config files (_template.yaml excluded)")

    if args.dry_run:
        for row in rows:
            print(
                f"  {row['org_slug']:20s} {str(row['customer_status']):12s} "
                f"clearance={str(row['data_use_clearance']):8s} dpa={row['dpa_signed']!s:5s} "
                f"pii_cleared={row['pii_egress_cleared']!s:5s} uses={row['permitted_uses']}"
            )
        return 0

    try:
        push_rows(rows)
    except SupabaseRestError as exc:
        print(f"org_config_sync: PUSH FAILED — {exc}", file=sys.stderr)
        return 1
    print("org_config_sync: pushed OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
