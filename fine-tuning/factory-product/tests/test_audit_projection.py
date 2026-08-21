"""project_findings — the blackboard's factory_run_events projection must be
schema-safe (only real columns, kind from the closed vocabulary, subtype
extension pattern), deterministic (uuid5), idempotent, and pointer-only (no
file contents in any row). Dry-run is the default; nothing here writes."""

import json
import uuid

import pytest

from _migration import columns_for, read_migration
from audit import audit_run, project_findings, testkit
from audit.audit_lib import RunContext
from backfill_ptc_history import NAMESPACE


@pytest.fixture(autouse=True)
def _no_prod_creds(monkeypatch):
    for var in ("NEXT_PUBLIC_SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
                "PU", "PK", "FACTORY_SUPABASE_URL",
                "FACTORY_SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def populated(tmp_path):
    """A factory tree whose blackboard holds real findings + a hook verdict
    (produced by the actual runner, not hand-written rows)."""
    factory_root = testkit.build_tree(tmp_path)
    testkit.plant_unfillable_class(factory_root)
    audit_run.main([testkit.SLUG, "--hook", "pre-s5",
                    "--factory-root", str(factory_root),
                    "--skip-machine-check"])
    return factory_root


def _rows(factory_root):
    ctx = RunContext(testkit.SLUG, factory_root=factory_root)
    return project_findings.build_rows(ctx, testkit.RUN_ID)


def test_rows_use_only_real_factory_run_events_columns(populated):
    real_columns = columns_for("factory_run_events", read_migration())
    for row in _rows(populated):
        assert set(row) <= real_columns, sorted(set(row) - real_columns)
        assert set(row) == set(project_findings.EVENT_COLUMNS)


def test_kind_stays_in_the_closed_vocabulary_with_subtype_extension(populated):
    rows = _rows(populated)
    assert rows, "projection produced no rows from a populated blackboard"
    subtypes = set()
    for row in rows:
        assert row["kind"] == "steering_event"
        subtypes.add(row["ref"]["event_subtype"])
        assert row["headline"].startswith("AUDIT ")
    assert subtypes == {"audit_finding", "audit_verdict"}


def test_ids_are_deterministic_uuid5(populated):
    first = [r["id"] for r in _rows(populated)]
    second = [r["id"] for r in _rows(populated)]
    assert first == second
    assert len(set(first)) == len(first)
    ctx = RunContext(testkit.SLUG, factory_root=populated)
    finding_id = ctx and _rows(populated)[0]["ref"].get("finding_id")
    if finding_id:
        assert _rows(populated)[0]["id"] == str(uuid.uuid5(
            NAMESPACE, f"factory_run_events:audit:{finding_id}"))


def test_second_projection_upserts_zero_new_rows(populated):
    store = {}

    def fake_upsert(rows):
        new = sum(1 for r in rows if r["id"] not in store)
        store.update({r["id"]: r for r in rows})
        return new

    first = fake_upsert(_rows(populated))
    assert first > 0
    assert fake_upsert(_rows(populated)) == 0


def test_rows_carry_pointers_and_counts_never_payloads(populated):
    dumped = json.dumps(_rows(populated))
    # nothing from the fixture pool/corpus content may leak into the rows
    assert "acct-0" not in dumped
    assert "Nightly review." not in dumped
    for row in _rows(populated):
        if row["ref"]["event_subtype"] == "audit_finding":
            assert "evidence_path" in row["ref"]
            assert isinstance(row["detail"]["counts"], dict)
            for pointer in row["detail"]["evidence"]:
                assert "\n" not in pointer and " " not in pointer


def test_finding_rows_reference_the_blackboard_finding(populated):
    ctx = RunContext(testkit.SLUG, factory_root=populated)
    from audit.audit_lib import load_findings
    finding_ids = {f["id"] for f in load_findings(ctx.audit_dir)}
    rows = [r for r in _rows(populated)
            if r["ref"]["event_subtype"] == "audit_finding"]
    assert {r["ref"]["finding_id"] for r in rows} == finding_ids


def test_dry_run_is_the_default_and_writes_nothing(populated, capsys):
    exit_code = project_findings.main(
        [testkit.SLUG, "--factory-root", str(populated)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "dry-run: no writes performed" in out


def test_verdict_row_summarizes_checks(populated):
    verdict_rows = [r for r in _rows(populated)
                    if r["ref"]["event_subtype"] == "audit_verdict"]
    assert len(verdict_rows) == 1
    row = verdict_rows[0]
    assert row["ref"]["hook"] == "pre-s5"
    assert row["ref"]["verdict"] == "fail"
    assert all(c["counts"] for c in row["detail"]["checks"])
