"""audit_run — the AG runner: hook registry, blackboard merge (append-only,
idempotent, auto-resolve on disappearance), lattice aggregation, exit codes.
Offline everywhere: parity has no creds here, so its verdict is suspect —
which is itself the behavior under test (missing is suspect, never pass)."""

import json

import pytest

from audit import audit_run, testkit
from audit.audit_lib import (
    RunContext, latest_status_by_finding, load_findings, load_statuses,
)


@pytest.fixture(autouse=True)
def _no_prod_creds(monkeypatch):
    for var in ("NEXT_PUBLIC_SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
                "PU", "PK"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def factory_root(tmp_path):
    return testkit.build_tree(tmp_path)


def _main(factory_root, hook="pre-s5"):
    return audit_run.main([testkit.SLUG, "--hook", hook,
                           "--factory-root", str(factory_root),
                           "--skip-machine-check"])


def test_registry_covers_the_phase0_hooks():
    assert set(audit_run.REGISTRY) == {"pre-s4", "pre-s5", "pre-s6g", "pre-s7"}


def test_gold_tree_offline_aggregates_suspect_not_pass(factory_root, capsys):
    """No corpus + no prod read => suspect checks exist => the hook verdict
    degrades. An all-green banner here would be a false green."""
    exit_code = _main(factory_root)
    out = capsys.readouterr().out
    assert exit_code == 0  # suspect never blocks (only a literal fail refuses)
    assert "VERDICT: suspect" in out
    # per-check lines are printed — the aggregate never substitutes for them
    for check_id in ("LIN-ORG-01", "LIN-SRC-02", "LIN-POOL-03", "LIN-FILL-04",
                     "PAR-ENV-01", "PAR-ACT-02", "PAR-EMIT-03", "PAR-AGE-04"):
        assert check_id in out


def test_planted_violation_fails_the_hook_and_exits_nonzero(factory_root, capsys):
    testkit.plant_unfillable_class(factory_root)
    exit_code = _main(factory_root)
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "VERDICT: fail" in out
    assert "unfillable class 'email_draft'" in out


def test_findings_land_on_the_blackboard_with_open_status(factory_root):
    testkit.plant_unfillable_class(factory_root)
    _main(factory_root)
    ctx = RunContext(testkit.SLUG, factory_root=factory_root)
    findings = load_findings(ctx.audit_dir)
    assert any("unfillable class" in f["headline"] for f in findings)
    statuses = latest_status_by_finding(ctx.audit_dir)
    fill = next(f for f in findings if "unfillable class" in f["headline"])
    assert statuses[fill["id"]] == "open"


def test_second_run_is_idempotent_on_the_blackboard(factory_root):
    testkit.plant_unfillable_class(factory_root)
    _main(factory_root)
    ctx = RunContext(testkit.SLUG, factory_root=factory_root)
    first = len(load_findings(ctx.audit_dir))
    first_statuses = len(load_statuses(ctx.audit_dir))
    _main(factory_root)
    assert len(load_findings(ctx.audit_dir)) == first  # zero new findings
    assert len(load_statuses(ctx.audit_dir)) == first_statuses


def test_fixed_violation_transitions_to_resolved_as_a_second_row(factory_root):
    testkit.plant_unfillable_class(factory_root)
    _main(factory_root)
    ctx = RunContext(testkit.SLUG, factory_root=factory_root)
    fill = next(f for f in load_findings(ctx.audit_dir)
                if "unfillable class" in f["headline"])
    # fix the plan back to gold and re-run: the finding is not re-observed
    spec = factory_root / "runs" / testkit.SLUG / "corpus" / "BUILD-SPEC.md"
    spec.write_text(testkit.GOLD_BUILD_SPEC)
    _main(factory_root)
    assert latest_status_by_finding(ctx.audit_dir)[fill["id"]] == "resolved"
    # append-only: the finding row itself is still on the board
    assert any(f["id"] == fill["id"] for f in load_findings(ctx.audit_dir))


def test_verdict_file_written_per_hook(factory_root):
    _main(factory_root)
    ctx = RunContext(testkit.SLUG, factory_root=factory_root)
    verdicts = list((ctx.audit_dir / "verdicts").glob("pre-s5-*.json"))
    assert len(verdicts) == 1
    doc = json.loads(verdicts[0].read_text())
    assert doc["verdict"] == "suspect"
    assert doc["run_id"] == testkit.RUN_ID
    assert {c["check_id"] for c in doc["checks"]} >= {"LIN-ORG-01", "PAR-ENV-01"}
    assert all(c["counts"] for c in doc["checks"])  # counts, not exit codes


def test_crashed_check_reports_suspect_not_pass(factory_root, monkeypatch, capsys):
    from audit import check_org_lineage

    def boom(_ctx):
        raise RuntimeError("synthetic crash")

    monkeypatch.setattr(check_org_lineage, "run", boom)
    exit_code = _main(factory_root)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "CRASHED" in out
    assert "VERDICT: suspect" in out


def test_pre_s6g_runs_the_comparator(factory_root, capsys):
    _main(factory_root, hook="pre-s6g")
    out = capsys.readouterr().out
    assert "CMP-REC-01" in out and "CMP-CENSUS-04" in out
