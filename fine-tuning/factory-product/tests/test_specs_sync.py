"""T15 — specs_sync: factory/specs/*.yaml -> factory_specs rows.

PRTests.md's own naming for this file: "parsed row count equals T10's file count exactly"
+ "a deliberately malformed YAML file causes a loud failure, not a silent skip."
"""

import re
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
REPO = FACTORY_DIR.parent.parent
sys.path.insert(0, str(FACTORY_DIR))

import specs_sync  # noqa: E402
from supabase_rest import SupabaseRestError  # noqa: E402

from _migration import columns_for as _columns_for  # noqa: E402
from _migration import read_migration  # noqa: E402

EXPECTED_SPEC_COUNT = 41  # same real count T10's test_specs_schema.py verifies


@pytest.fixture(scope="module")
def migration_text():
    return read_migration()


def test_parsed_row_count_matches_t10s_real_spec_file_count():
    rows = specs_sync.build_spec_rows()
    assert len(rows) == EXPECTED_SPEC_COUNT == len(specs_sync.spec_files())


def test_every_row_has_a_slug_matching_a_real_spec_filename():
    rows = specs_sync.build_spec_rows()
    slugs = {row["slug"] for row in rows}
    filenames = {p.stem for p in specs_sync.spec_files()}
    assert slugs == filenames  # exact set match — no dropped, no duplicated, no renamed


def test_spec_row_payload_keys_are_all_real_factory_specs_columns(migration_text):
    columns = _columns_for("factory_specs", migration_text)
    rows = specs_sync.build_spec_rows()
    for row in rows:
        assert set(row.keys()) <= columns, f"unknown factory_specs column(s): {set(row.keys()) - columns}"


def test_spec_body_stores_the_whole_parsed_document_verbatim():
    rows = specs_sync.build_spec_rows()
    corpus_reader = next(r for r in rows if r["slug"] == "corpus-reader")
    assert corpus_reader["spec_body"]["role_family"] == "reader"
    assert corpus_reader["spec_body"]["mission"] == corpus_reader["mission"]
    assert "laws" in corpus_reader["spec_body"]  # a field NOT promoted to its own column


def test_malformed_yaml_fails_loudly_not_a_silent_skip(tmp_path):
    bad = tmp_path / "broken-spec.yaml"
    bad.write_text("slug: broken-spec\nmission: [unterminated flow sequence\n")
    with pytest.raises(specs_sync.SpecSyncError, match="broken-spec.yaml"):
        specs_sync.build_spec_rows(paths=[bad])


def test_spec_missing_a_required_field_fails_loudly(tmp_path):
    incomplete = tmp_path / "incomplete-spec.yaml"
    incomplete.write_text(
        textwrap.dedent(
            """
            slug: incomplete-spec
            role_family: reader
            # mission deliberately omitted
            done_when: n/a
            """
        )
    )
    with pytest.raises(specs_sync.SpecSyncError, match="missing required field"):
        specs_sync.build_spec_rows(paths=[incomplete])


def test_spec_with_slug_not_matching_filename_fails_loudly(tmp_path):
    mismatched = tmp_path / "real-filename.yaml"
    mismatched.write_text("slug: different-slug\nrole_family: reader\nmission: x\ndone_when: y\n")
    with pytest.raises(specs_sync.SpecSyncError, match="does not match filename"):
        specs_sync.build_spec_rows(paths=[mismatched])


def test_one_malformed_file_among_many_good_ones_stops_the_whole_batch_not_just_that_file(tmp_path):
    good = tmp_path / "good-spec.yaml"
    good.write_text("slug: good-spec\nrole_family: reader\nmission: x\ndone_when: y\n")
    bad = tmp_path / "bad-spec.yaml"
    bad.write_text("slug: bad-spec\nmission: [unterminated\n")
    with pytest.raises(specs_sync.SpecSyncError):
        specs_sync.build_spec_rows(paths=[good, bad])


def test_push_spec_rows_upserts_by_slug_a_real_unique_column():
    calls = []
    with patch.object(specs_sync, "upsert", side_effect=lambda table, rows, **kw: calls.append((table, kw.get("on_conflict")))):
        specs_sync.push_spec_rows([{"slug": "x"}])
    assert calls == [("factory_specs", "slug")]


def test_push_spec_rows_propagates_a_forced_http_failure_not_swallowed():
    # Unlike T11's push_stage, a catalog sync has no fail-open/fail-closed direction to
    # preserve — a broken push must be visible, not silently absorbed (mirrors T14's
    # lessons_sync framing).
    with patch.object(specs_sync, "upsert", side_effect=SupabaseRestError("boom")):
        with pytest.raises(SupabaseRestError):
            specs_sync.push_spec_rows([{"slug": "x"}])
