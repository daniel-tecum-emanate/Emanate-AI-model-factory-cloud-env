"""compare_runs — the cross-run comparator v0, both directions: the gold
recipe passes against its predecessor digest, each planted repeat/staleness
fires, and missing inputs degrade to suspect."""

import pytest

from audit import compare_runs, testkit
from audit.audit_lib import RunContext


@pytest.fixture()
def factory_root(tmp_path):
    return testkit.build_tree(tmp_path)


def _results(factory_root):
    ctx = RunContext(testkit.SLUG, factory_root=factory_root)
    return {r["check_id"]: r for r in compare_runs.run(ctx)}, ctx


def test_gold_recipe_passes_every_comparator_check(factory_root):
    results, ctx = _results(factory_root)
    assert {r_id: r["verdict"] for r_id, r in results.items()} == {
        "CMP-REC-01": "pass", "CMP-FAIL-02": "pass",
        "CMP-DOSE-03": "pass", "CMP-CENSUS-04": "pass"}
    # the S6g decision-request artifact is written on every run
    assert (ctx.audit_dir / "recipe-comparison.json").exists()


def test_planted_multivariable_change_warns(factory_root):
    testkit.plant_many_recipe_changes(factory_root)
    result, _ = _results(factory_root)
    rec = result["CMP-REC-01"]
    assert rec["verdict"] == "suspect"
    finding = rec["findings"][0]
    assert finding["counts"]["changed_variables"] == 5
    assert "attribution" in finding["headline"]
    assert "L38" in finding["lesson_refs"]


def test_small_change_is_info_not_warn(factory_root):
    config = factory_root / "configs" / f"{testkit.SLUG}.yaml"
    config.write_text(config.read_text().replace("epochs: 1", "epochs: 2"))
    result, _ = _results(factory_root)
    rec = result["CMP-REC-01"]
    assert rec["verdict"] == "pass"
    assert "within the attribution bound" in rec["findings"][0]["headline"]


def test_planted_hazard_without_countermeasure_fails(factory_root):
    testkit.plant_hazard_without_countermeasure(factory_root)
    result, _ = _results(factory_root)
    fail02 = result["CMP-FAIL-02"]
    assert fail02["verdict"] == "fail"
    finding = fail02["findings"][0]
    assert "HZ-E21-STAGED-EVIDENCE" in finding["headline"]
    assert "E21" in finding["lesson_refs"]
    assert finding["severity"] == "blocking"


def test_hazard_with_countermeasure_present_passes(factory_root):
    """Both directions: the gold spec stages evidence AND pairs twins."""
    result, _ = _results(factory_root)
    assert result["CMP-FAIL-02"]["verdict"] == "pass"
    assert result["CMP-FAIL-02"]["counts"]["hazards_evaluated"] == 2


def test_planted_uncited_dose_row_is_suspect(factory_root):
    testkit.plant_uncited_dose_row(factory_root)
    result, _ = _results(factory_root)
    dose = result["CMP-DOSE-03"]
    assert dose["verdict"] == "suspect"
    assert dose["counts"]["uncited_dose_rows"] == 1


def test_planted_stale_census_fails_with_both_numbers(factory_root):
    testkit.plant_stale_census(factory_root)
    result, _ = _results(factory_root)
    census = result["CMP-CENSUS-04"]
    assert census["verdict"] == "fail"
    finding = census["findings"][0]
    assert finding["counts"] == {"recorded": 1, "actual": 6}
    assert "stale config census" in finding["headline"]


def test_missing_digests_degrade_rec01_to_suspect(factory_root):
    digest = (factory_root / "runs" / testkit.SLUG / "iterations"
              / "01-digest.json")
    digest.unlink()
    result, _ = _results(factory_root)
    assert result["CMP-REC-01"]["verdict"] == "suspect"


def test_missing_hazard_table_is_suspect_never_pass(factory_root, tmp_path):
    ctx = RunContext(testkit.SLUG, factory_root=factory_root)
    results = {r["check_id"]: r for r in
               compare_runs.run(ctx, hazards_path=tmp_path / "absent.yaml")}
    assert results["CMP-FAIL-02"]["verdict"] == "suspect"
