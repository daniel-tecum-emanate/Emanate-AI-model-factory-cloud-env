"""B3 (factory-observability-v1) — org_config_sync: factory/configs/*.yaml -> factory_org_configs.

`PRTests.md` names four cases for this file and marks two of them release-blocking
(`_template.yaml` is never included; a missing `org.id` fails loudly). Both are here,
plus the negative-PII test `PRRules.md` rule 6 requires of every new payload that
reaches a `factory_*` table.
"""

import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import org_config_sync as ocs  # noqa: E402
from supabase_rest import SupabaseRestError  # noqa: E402

from _migration import columns_for as _columns_for  # noqa: E402
from _migration import read_migration  # noqa: E402

REAL_CONFIGS_DIR = FACTORY_DIR / "configs"

# The real, non-template configs that exist today. Asserted as an exact set rather
# than a count so adding a config makes this test say WHICH org appeared. ptc-steel
# added 2026-07-30 by MF-PTC-RETRAIN-EXEC for the v6 retrain (run ac6a6078…).
EXPECTED_REAL_SLUGS = {"grand-steel", "qa-dryrun-synth", "ptc-steel"}


@pytest.fixture(scope="module")
def migration_text():
    return read_migration()


def _write(path, body):
    path.write_text(textwrap.dedent(body).lstrip())
    return path


def _minimal_config(slug="fixture-org", org_id="00000000-0000-4000-8000-0000000000ff"):
    return f"""
    org:
      slug: {slug}
      id: {org_id}
      name: Fixture Org
      customer_status: CONTRACTED
    governance:
      data_use_clearance: CLEARED
      dpa_signed: true
      training_rights_clause: true
      pii_egress_cleared: true
      permitted_uses: [sft, eval]
    """


# ---------------------------------------------------------------------------
# The real configs on disk today
# ---------------------------------------------------------------------------


def test_real_configs_parse_to_exactly_the_known_non_template_orgs():
    rows = ocs.build_rows(configs_dir=REAL_CONFIGS_DIR)
    assert {r["org_slug"] for r in rows} == EXPECTED_REAL_SLUGS


def test_grand_steel_row_matches_its_config_verbatim():
    """PRContext.md V6's verbatim content, asserted field by field.

    UPDATED 2026-07-29: grand-steel's governance block was flipped from
    UNCLEARED (the state this test originally pinned) to CLEARED after Daniel
    verbally confirmed data-use permission on the same basis as PTC Steel —
    the approval is audited via `factory.py grand-steel --stage governance
    --approved` (see `runs/grand-steel/approvals/governance.json` and
    `automation-graph/DANIEL-RAW-REQUESTS.md`, 2026-07-29 entry). This test
    keeps pinning the on-disk config verbatim so a sync that quietly mangles
    real governance values still fails loudly.
    """
    rows = {r["org_slug"]: r for r in ocs.build_rows(configs_dir=REAL_CONFIGS_DIR)}
    gs = rows["grand-steel"]
    assert gs["org_id"] == "10664bd2-79d2-49d2-8196-fcaf539d9eb3"
    assert gs["org_name"] == "Grand Steel"
    assert gs["customer_status"] == "CONTRACTED"
    assert gs["data_use_clearance"] == "CLEARED"
    assert gs["dpa_signed"] is True
    assert gs["training_rights_clause"] is True
    assert gs["pii_egress_cleared"] is True
    assert gs["permitted_uses"] == ["retrieval", "eval", "sft", "preference", "research"]


def test_ptc_steel_row_matches_its_config_verbatim():
    """Pins `configs/ptc-steel.yaml` (created 2026-07-30 for the v6 retrain).
    Governance is CLEARED on precedent: v1–v5 trained on this data, and
    grand-steel.yaml cites PTC as THE governance precedent — the S2 gate on run
    ac6a6078… is still Daniel's to approve; this test pins the on-disk values so
    a sync that mangles them fails loudly."""
    rows = {r["org_slug"]: r for r in ocs.build_rows(configs_dir=REAL_CONFIGS_DIR)}
    ptc = rows["ptc-steel"]
    assert ptc["org_id"] == "01a4c1ae-4a25-4cdc-9883-dea0827c9ce3"
    assert ptc["org_name"] == "PTC Steel"
    assert ptc["customer_status"] == "CONTRACTED"
    assert ptc["data_use_clearance"] == "CLEARED"
    assert ptc["dpa_signed"] is True
    assert ptc["training_rights_clause"] is True
    assert ptc["pii_egress_cleared"] is True
    assert ptc["permitted_uses"] == ["retrieval", "eval", "sft", "preference", "research"]


def test_qa_dryrun_synth_row_matches_its_config_verbatim():
    rows = {r["org_slug"]: r for r in ocs.build_rows(configs_dir=REAL_CONFIGS_DIR)}
    qa = rows["qa-dryrun-synth"]
    assert qa["org_id"] == "00000000-0000-4000-8000-000000000001"
    assert qa["org_name"] == "QA Dryrun Synth Org"
    assert qa["customer_status"] == "CONTRACTED"
    assert qa["data_use_clearance"] == "CLEARED"
    assert qa["dpa_signed"] is True
    assert qa["training_rights_clause"] is True
    assert qa["pii_egress_cleared"] is True
    assert qa["permitted_uses"] == ["retrieval", "eval", "sft", "preference", "research"]


# ---------------------------------------------------------------------------
# `_template.yaml` exclusion — RELEASE-BLOCKING
# ---------------------------------------------------------------------------


def test_template_yaml_is_never_included_from_the_real_configs_dir():
    assert (REAL_CONFIGS_DIR / "_template.yaml").is_file(), "precondition: the real template still exists"
    rows = ocs.build_rows(configs_dir=REAL_CONFIGS_DIR)
    assert not any("template" in r["org_slug"].lower() for r in rows)
    assert all("<" not in str(v) for r in rows for v in r.values()), "a placeholder value leaked into a row"


@pytest.mark.parametrize("template_name", ["_template.yaml", "_TEMPLATE.yaml", "_zzz-template.yaml"])
def test_template_excluded_regardless_of_file_ordering(tmp_path, template_name):
    """`PRTests.md`: "regardless of file ordering". The names are chosen to sort
    before, after, and around the real config, so a glob that happened to work
    only because the template sorted first cannot pass this.
    """
    _write(tmp_path / template_name, "org:\n  slug: whatever\n  id: <org-uuid>\n  name: <Org Name>\n")
    _write(tmp_path / "fixture-org.yaml", _minimal_config())
    rows = ocs.build_rows(configs_dir=tmp_path)
    assert [r["org_slug"] for r in rows] == ["fixture-org"]


def test_template_is_excluded_by_name_before_parsing_not_by_failing_validation(tmp_path):
    """The exclusion must not depend on `<org-uuid>` failing a validity check.
    Here the template carries a perfectly well-formed org block — if exclusion were
    really "it happens to fail validation", this would produce a bogus row.
    """
    _write(tmp_path / "_template.yaml", _minimal_config(slug="_template", org_id="11111111-1111-4111-8111-111111111111"))
    rows = ocs.build_rows(configs_dir=tmp_path)
    assert rows == []


# ---------------------------------------------------------------------------
# Missing `org.id` fails loudly — RELEASE-BLOCKING
# ---------------------------------------------------------------------------


def test_missing_org_id_raises_instead_of_upserting_null(tmp_path):
    _write(
        tmp_path / "no-id.yaml",
        """
        org:
          slug: no-id
          name: No Id Org
        governance:
          data_use_clearance: CLEARED
        """,
    )
    with pytest.raises(ocs.OrgConfigSyncError, match=r"missing `org\.id`"):
        ocs.build_rows(configs_dir=tmp_path)


def test_empty_string_org_id_also_raises(tmp_path):
    """`''` is falsy but would pass an `is not None` check — the guard has to be
    truthiness, because an empty string in `org_id` is the same lie as a NULL.
    """
    _write(tmp_path / "blank-id.yaml", "org:\n  slug: blank-id\n  id: ''\n  name: Blank\n")
    with pytest.raises(ocs.OrgConfigSyncError, match=r"missing `org\.id`"):
        ocs.build_rows(configs_dir=tmp_path)


def test_one_bad_config_among_good_ones_stops_the_whole_batch(tmp_path):
    """Mirrors `specs_sync`'s framing: a broken file must not be able to present as
    "the catalog just has one fewer org today"."""
    _write(tmp_path / "good-org.yaml", _minimal_config(slug="good-org"))
    _write(tmp_path / "zz-bad-org.yaml", "org:\n  slug: zz-bad-org\n  name: No Id\n")
    with pytest.raises(ocs.OrgConfigSyncError):
        ocs.build_rows(configs_dir=tmp_path)


def test_missing_org_name_raises_because_the_column_is_not_null(tmp_path):
    _write(tmp_path / "no-name.yaml", "org:\n  slug: no-name\n  id: abc-123\n")
    with pytest.raises(ocs.OrgConfigSyncError, match=r"missing `org\.name`"):
        ocs.build_rows(configs_dir=tmp_path)


def test_slug_not_matching_filename_raises(tmp_path):
    """The runner resolves a config by filename while this table's natural key is
    `org.slug`; a mismatch means one org with two identities."""
    _write(tmp_path / "real-name.yaml", _minimal_config(slug="different-slug"))
    with pytest.raises(ocs.OrgConfigSyncError, match="does not match filename"):
        ocs.build_rows(configs_dir=tmp_path)


def test_duplicate_org_id_across_two_slugs_raises(tmp_path):
    """Greptile review (platform-alpha#2169): the migration's
    `uq_factory_org_configs_org_id` partial unique index enforces this at the DB
    layer too, but that would surface as an opaque PostgREST 409 with no slug
    names in it — worse for an operator to debug than this parse-time error,
    which names both files. Table comment: factory_org_configs is "the ONLY
    org_slug <-> org_id bridge" (PRRules invariant 16) — that claim only holds if
    the mapping is 1:1.
    """
    shared_id = "00000000-0000-4000-8000-0000000000ab"
    _write(tmp_path / "first-org.yaml", _minimal_config(slug="first-org", org_id=shared_id))
    _write(tmp_path / "second-org.yaml", _minimal_config(slug="second-org", org_id=shared_id))

    with pytest.raises(ocs.OrgConfigSyncError, match="already used by"):
        ocs.build_rows(configs_dir=tmp_path)


def test_same_org_id_reused_by_only_one_slug_is_fine(tmp_path):
    """The guard is about TWO DIFFERENT slugs sharing an org_id — re-parsing the
    same file, or two slugs with two distinct ids, must not trip it."""
    _write(tmp_path / "solo-org.yaml", _minimal_config(slug="solo-org", org_id="id-1"))
    _write(tmp_path / "other-org.yaml", _minimal_config(slug="other-org", org_id="id-2"))

    rows = ocs.build_rows(configs_dir=tmp_path)
    assert {r["org_slug"] for r in rows} == {"solo-org", "other-org"}


def test_malformed_yaml_raises_with_the_filename_in_the_message(tmp_path):
    _write(tmp_path / "broken.yaml", "org:\n  slug: [unterminated flow\n")
    with pytest.raises(ocs.OrgConfigSyncError, match="broken.yaml"):
        ocs.build_rows(configs_dir=tmp_path)


def test_permitted_uses_absent_becomes_empty_list_not_none(tmp_path):
    """`NOT NULL DEFAULT '[]'` on the column — sending None would 400 the push."""
    _write(tmp_path / "no-uses.yaml", "org:\n  slug: no-uses\n  id: x\n  name: X\ngovernance:\n  dpa_signed: true\n")
    (row,) = ocs.build_rows(configs_dir=tmp_path)
    assert row["permitted_uses"] == []


def test_permitted_uses_wrong_type_raises(tmp_path):
    _write(
        tmp_path / "bad-uses.yaml",
        "org:\n  slug: bad-uses\n  id: x\n  name: X\ngovernance:\n  permitted_uses: sft\n",
    )
    with pytest.raises(ocs.OrgConfigSyncError, match="permitted_uses must be a list"):
        ocs.build_rows(configs_dir=tmp_path)


def test_governance_block_absent_yields_all_null_flags_not_a_crash(tmp_path):
    """A config authored before governance was tracked is unrecorded, not
    ungoverned-and-cleared. Every flag must come back None."""
    _write(tmp_path / "no-gov.yaml", "org:\n  slug: no-gov\n  id: x\n  name: X\n")
    (row,) = ocs.build_rows(configs_dir=tmp_path)
    for field in ocs.GOVERNANCE_FIELDS:
        assert row[field] is None, field


# ---------------------------------------------------------------------------
# PII — PRRules rule 6 (a comment does not satisfy this; a passing test does)
# ---------------------------------------------------------------------------


def test_book_block_and_arbitrary_extra_keys_never_reach_the_row(tmp_path):
    """The row shape is a fixed allow-list, so the configs' `book:` block (account
    counts, revenue) and any future key are invisible in the DB until someone
    deliberately projects them. Proven with a fixture carrying real PII shapes in
    keys this sync must ignore entirely.
    """
    _write(
        tmp_path / "pii-org.yaml",
        """
        org:
          slug: pii-org
          id: abc-123
          name: PII Org
          customer_status: CONTRACTED
        book:
          accounts: 327
          book_revenue_usd: 112751
          top_account_contact: jane.doe@grandsteel.example
        contacts:
          - name: Jane Doe
            email: jane.doe@grandsteel.example
            phone: 415-555-0134
            ssn: 123-45-6789
        governance:
          data_use_clearance: CLEARED
          permitted_uses: [sft]
        """,
    )
    (row,) = ocs.build_rows(configs_dir=tmp_path)

    assert set(row.keys()) == {
        "org_slug",
        "org_id",
        "org_name",
        "customer_status",
        "permitted_uses",
        *ocs.GOVERNANCE_FIELDS,
    }
    blob = repr(row)
    for leak in ("jane.doe@", "Jane Doe", "415-555-0134", "123-45-6789", "112751", "327"):
        assert leak not in blob, f"{leak!r} leaked into the pushed row"


def test_real_config_rows_carry_no_revenue_or_account_counts():
    """The same guarantee, asserted against the REAL configs rather than a fixture —
    both carry a populated `book:` block with real revenue figures today."""
    blob = repr(ocs.build_rows(configs_dir=REAL_CONFIGS_DIR))
    for leak in ("112751", "book_revenue", "accounts", "fact_bearing", "892M"):
        assert leak not in blob, f"{leak!r} leaked from a book: block into the pushed rows"


# ---------------------------------------------------------------------------
# Schema drift — the payload's keys must be real factory_org_configs columns
# ---------------------------------------------------------------------------


def test_row_keys_are_all_real_factory_org_configs_columns(migration_text):
    columns = _columns_for("factory_org_configs", migration_text)
    for row in ocs.build_rows(configs_dir=REAL_CONFIGS_DIR):
        unknown = set(row.keys()) - columns
        assert not unknown, f"unknown factory_org_configs column(s): {unknown}"


def test_every_not_null_column_without_a_default_is_populated(migration_text):
    """`org_slug` and `org_name` are NOT NULL with no default — a row omitting
    either would 400 at push time rather than at parse time."""
    assert "org_slug               text          NOT NULL UNIQUE" in migration_text
    for row in ocs.build_rows(configs_dir=REAL_CONFIGS_DIR):
        assert row["org_slug"] and row["org_name"]


# ---------------------------------------------------------------------------
# Push behaviour: upsert by the natural key, fail LOUDLY
# ---------------------------------------------------------------------------


def test_push_upserts_by_org_slug_the_tables_real_unique_column():
    calls = []
    with patch.object(ocs, "upsert", side_effect=lambda table, rows, **kw: calls.append((table, kw.get("on_conflict")))):
        ocs.push_rows([{"org_slug": "x"}])
    assert calls == [("factory_org_configs", "org_slug")]


def test_push_failure_propagates_and_is_not_swallowed():
    """The OPPOSITE direction from `factory_sync.push_stage`/`push_agents`, on
    purpose (PRRules rule 4): this is a maintenance job with no live stage riding
    on it, so a swallowed failure would present as "governance data is
    mysteriously stale" — worse than a non-zero exit.
    """
    with patch.object(ocs, "upsert", side_effect=SupabaseRestError("boom")):
        with pytest.raises(SupabaseRestError):
            ocs.push_rows([{"org_slug": "x"}])


# ---------------------------------------------------------------------------
# --dry-run
# ---------------------------------------------------------------------------


def test_dry_run_prints_without_any_network_call(capsys):
    with patch.object(ocs, "upsert", side_effect=AssertionError("--dry-run must not push")):
        rc = ocs.main(["--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "grand-steel" in out and "qa-dryrun-synth" in out and "ptc-steel" in out
    # Row lines are the indented ones; the summary line above them legitimately
    # names `_template.yaml` to state that it was excluded, so assert on the rows
    # rather than on the whole of stdout.
    row_lines = [line for line in out.splitlines() if line.startswith("  ")]
    assert len(row_lines) == len(EXPECTED_REAL_SLUGS)
    assert not any("template" in line.lower() for line in row_lines)


def test_main_returns_1_on_a_parse_failure_rather_than_pushing_a_partial_batch(tmp_path, capsys):
    _write(tmp_path / "bad.yaml", "org:\n  slug: bad\n  name: No Id\n")
    with patch.object(ocs, "upsert", side_effect=AssertionError("must not push after a parse failure")):
        rc = ocs.main(["--configs-dir", str(tmp_path)])
    assert rc == 1
    assert "PARSE FAILED" in capsys.readouterr().err


def test_main_returns_1_and_reports_a_push_failure(capsys):
    with patch.object(ocs, "push_rows", side_effect=SupabaseRestError("503")):
        rc = ocs.main([])
    assert rc == 1
    assert "PUSH FAILED" in capsys.readouterr().err
