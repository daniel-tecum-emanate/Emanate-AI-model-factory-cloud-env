"""check_fillability — catch #3 (unfillable / contract-forbidden classes)
both directions: gold plan passes, an unfillable dose fails naming the class,
an inadmissible class fails on the contract, missing inputs => suspect."""

import json

import pytest

from audit import check_fillability, testkit
from audit.audit_lib import RunContext
from audit.check_fillability import parse_dose_plan


@pytest.fixture()
def factory_root(tmp_path):
    return testkit.build_tree(tmp_path)


def _result(factory_root):
    ctx = RunContext(testkit.SLUG, factory_root=factory_root)
    return check_fillability.run(ctx)[0]


def test_dose_plan_parses_from_the_build_spec():
    plan = parse_dose_plan(testkit.GOLD_BUILD_SPEC)
    assert plan == {"classes": {"winback_enrollment": 1.0},
                    "floor": 2, "total": 4}


def test_gold_plan_is_fillable_and_admissible(factory_root):
    result = _result(factory_root)
    assert result["verdict"] == "pass"
    assert result["counts"]["unfillable_classes"] == 0
    assert result["counts"]["inadmissible_classes"] == 0
    assert result["counts"]["pool_rows_counted"] == 8  # train pool, not selection


def test_planted_unfillable_class_fails_naming_it(factory_root):
    testkit.plant_unfillable_class(factory_root)
    result = _result(factory_root)
    assert result["verdict"] == "fail"
    finding = next(f for f in result["findings"] if f["verdict"] == "fail")
    assert "unfillable class 'email_draft'" in finding["headline"]
    assert finding["counts"]["available"] == 0
    assert finding["counts"]["required"] == 2
    assert finding["class"] == "fillability"


def test_planted_inadmissible_class_fails_on_the_contract(factory_root):
    testkit.plant_inadmissible_class(factory_root)
    result = _result(factory_root)
    assert result["verdict"] == "fail"
    finding = next(f for f in result["findings"] if f["verdict"] == "fail")
    assert "custom" in finding["headline"]
    assert "L51" in finding["lesson_refs"]
    # fillable (4 rows planted) — the failure is admissibility alone
    assert finding["counts"]["inadmissible_classes"] == 1


def test_a2_snapshot_takes_precedence_over_the_envelope(factory_root):
    """When A2 has produced a served contract snapshot, admissibility keys on
    it — not on the local envelope pull."""
    audit_dir = factory_root / "runs" / testkit.SLUG / "audit"
    (audit_dir / "contract-snapshot.json").write_text(json.dumps(
        {"enabled_actions": ["no_action"], "pulled_at": "2026-07-31T13:00:00+00:00"}))
    result = _result(factory_root)
    assert result["verdict"] == "fail"
    finding = next(f for f in result["findings"] if f["verdict"] == "fail")
    assert "A2 contract snapshot" in finding["headline"]
    assert "winback_enrollment" in finding["headline"]


def test_no_dose_plan_is_suspect_never_pass(factory_root):
    spec = factory_root / "runs" / testkit.SLUG / "corpus" / "BUILD-SPEC.md"
    spec.write_text("# BUILD-SPEC with no dose table\n")
    result = _result(factory_root)
    assert result["verdict"] == "suspect"


def test_no_pool_is_suspect_never_pass(factory_root):
    inputs = factory_root / "runs" / testkit.SLUG / "corpus" / "inputs"
    (inputs / "selection-pool.json").unlink()
    (inputs / "train-pool.json").unlink()
    result = _result(factory_root)
    assert result["verdict"] == "suspect"


def test_no_contract_snapshot_degrades_admissibility_to_suspect(factory_root):
    inputs = factory_root / "runs" / testkit.SLUG / "corpus" / "inputs"
    env = json.loads((inputs / "envelope.json").read_text())
    del env["allowed_actions"]
    (inputs / "envelope.json").write_text(json.dumps(env))
    result = _result(factory_root)
    assert result["verdict"] == "suspect"
