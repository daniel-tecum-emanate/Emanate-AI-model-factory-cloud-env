"""audit_lib — the swarm's core must enforce the §6.2 schema mechanically:
deterministic uuid5 finding ids, pointer-only evidence, the degradation
lattice (missing => suspect, never pass), and append-only blackboard writes."""

import uuid

import pytest

from audit import testkit
from audit.audit_lib import (
    AuditSchemaError, RunContext, append_findings, append_statuses,
    evidence_sha12, finding_id, latest_status_by_finding, load_findings,
    make_finding, status_row, verdict_from_findings, worst,
)
from backfill_ptc_history import NAMESPACE


@pytest.fixture()
def ctx(tmp_path):
    return RunContext(testkit.SLUG, factory_root=testkit.build_tree(tmp_path))


def _finding(ctx, **overrides):
    kwargs = dict(auditor="lineage", check_id="LIN-ORG-01",
                  klass="contamination", severity="blocking", verdict="fail",
                  stage_scope="S4", headline="synthetic finding, 3 rows",
                  evidence=["factory-automation/factory/audit/audit_lib.py"],
                  counts={"rows": 3})
    kwargs.update(overrides)
    return make_finding(ctx, **kwargs)


# ── ids ──────────────────────────────────────────────────────────────────────
def test_finding_ids_are_deterministic_uuid5(ctx):
    a = _finding(ctx)
    b = _finding(ctx)
    assert a["id"] == b["id"]
    sha = evidence_sha12(a["evidence"], a["counts"], a["headline"])
    assert a["id"] == str(uuid.uuid5(NAMESPACE, f"{ctx.run_id}:LIN-ORG-01:{sha}"))
    uuid.UUID(a["id"])


def test_changed_evidence_mints_a_new_finding_id(ctx):
    a = _finding(ctx)
    b = _finding(ctx, counts={"rows": 4})
    assert a["id"] != b["id"]


def test_distinct_violations_with_shared_pointers_get_distinct_ids(ctx):
    """Two marker findings shared pointers AND counts on the first live
    ptc-steel run and collided to one id — the headline is part of the
    finding's identity."""
    a = _finding(ctx, headline="marker 'x' appears 1x in pool rows")
    b = _finding(ctx, headline="marker 'y' appears 1x in pool rows")
    assert a["id"] != b["id"]


def test_run_context_resolves_identity(ctx):
    assert ctx.run_id == testkit.RUN_ID
    assert ctx.iteration == 2  # latest digest is iteration 1


# ── schema enforcement, both directions ──────────────────────────────────────
def test_gold_finding_carries_the_full_schema(ctx):
    f = _finding(ctx)
    assert f["schema_version"] == 1
    assert f["org_slug"] == testkit.SLUG
    assert f["detected_by"].startswith("audit_run.py@")
    assert f["detected_at"]


@pytest.mark.parametrize("mutation", [
    {"evidence": ["has whitespace pointer"]},
    {"evidence": ["line\nbreak"]},
    {"evidence": []},
    {"evidence": ["p"] * 9},
    {"counts": {}},
    {"verdict": "pass"},          # findings are fail|suspect|info only
    {"severity": "critical"},
    {"klass": "novel-class"},
    {"stage_scope": "S99"},
    {"headline": "two\nlines"},
])
def test_schema_violations_raise_loudly(ctx, mutation):
    with pytest.raises(AuditSchemaError):
        _finding(ctx, **mutation)


# ── lattice ──────────────────────────────────────────────────────────────────
def test_lattice_any_fail_is_fail():
    assert worst(["pass", "fail", "suspect"]) == "fail"


def test_lattice_missing_or_unknown_is_suspect_never_pass():
    assert worst(["pass", None]) == "suspect"
    assert worst(["pass", "banana"]) == "suspect"
    assert worst([]) == "pass"
    assert worst(["pass", "pass"]) == "pass"


def test_verdict_from_findings_info_does_not_degrade(ctx):
    info = _finding(ctx, verdict="info", severity="info")
    assert verdict_from_findings([info]) == "pass"
    assert verdict_from_findings([info, _finding(ctx, verdict="suspect")]) == "suspect"
    assert verdict_from_findings([info, _finding(ctx)]) == "fail"


# ── blackboard ───────────────────────────────────────────────────────────────
def test_append_findings_is_append_only_and_dedups(ctx):
    f = _finding(ctx)
    new, existing = append_findings(ctx.audit_dir, [f])
    assert [x["id"] for x in new] == [f["id"]] and not existing
    new2, existing2 = append_findings(ctx.audit_dir, [f])
    assert new2 == [] and f["id"] in existing2
    assert len(load_findings(ctx.audit_dir)) == 1


def test_append_findings_dedups_within_one_batch(ctx):
    f = _finding(ctx)
    new, _ = append_findings(ctx.audit_dir, [f, dict(f)])
    assert len(new) == 1
    assert len(load_findings(ctx.audit_dir)) == 1


def test_status_transitions_are_second_rows_never_edits(ctx):
    f = _finding(ctx)
    append_findings(ctx.audit_dir, [f])
    append_statuses(ctx.audit_dir, [status_row(f["id"], "open", "deterministic", "opened")])
    append_statuses(ctx.audit_dir, [status_row(f["id"], "resolved", "deterministic", "fixed")])
    assert latest_status_by_finding(ctx.audit_dir)[f["id"]] == "resolved"
    # both rows survive on disk — outcomes are second rows
    assert (ctx.audit_dir / "status.jsonl").read_text().count(f["id"]) == 2


def test_waived_requires_a_human_actor(ctx):
    f = _finding(ctx)
    with pytest.raises(AuditSchemaError):
        append_statuses(ctx.audit_dir,
                        [status_row(f["id"], "waived", "deterministic", "no")])
    append_statuses(ctx.audit_dir,
                    [status_row(f["id"], "waived", "human", "accepted risk")])
