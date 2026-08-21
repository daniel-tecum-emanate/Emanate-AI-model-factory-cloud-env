"""check_org_lineage — catch #1 (mixed-org contamination) both directions:
the gold tree passes, each planted violation fails with a named finding, and
missing inputs degrade to suspect, never pass."""

import json

import pytest

from audit import check_org_lineage, testkit
from audit.audit_lib import RunContext


@pytest.fixture()
def factory_root(tmp_path):
    return testkit.build_tree(tmp_path)


def _results(factory_root):
    ctx = RunContext(testkit.SLUG, factory_root=factory_root)
    return {r["check_id"]: r for r in check_org_lineage.run(ctx)}


# ── gold direction ───────────────────────────────────────────────────────────
def test_gold_tree_passes_org_and_pool_checks(factory_root):
    testkit.write_corpus(factory_root)
    results = _results(factory_root)
    assert results["LIN-ORG-01"]["verdict"] == "pass"
    assert results["LIN-POOL-03"]["verdict"] == "pass"
    assert results["LIN-SRC-02"]["verdict"] == "pass"
    # counts, not exit codes: the pass enumerates what was scanned
    assert results["LIN-ORG-01"]["counts"]["rows_scanned"] == 18
    assert results["LIN-ORG-01"]["counts"]["anchors_checked"] == 1


def test_pool_verification_metadata_does_not_false_red(factory_root):
    """The real pool file names the excluded org in its own verification
    metadata (excluded_org_prefix / read_tools_counted) — observed as a
    false red on the first live ptc-steel run, 2026-07-31. The scan keys on
    rows; the gold fixture carries the metadata, and must pass."""
    result = _results(factory_root)["LIN-ORG-01"]
    assert result["verdict"] == "pass"


def test_served_system_can_name_global_tool_but_data_cannot(factory_root):
    build = testkit.write_corpus(factory_root)
    path = build / "test.jsonl"
    record = json.loads(path.read_text())
    record["messages"][0]["content"] += f" Global catalog: {testkit.EXCLUDED_TOOL}."
    path.write_text(json.dumps(record) + "\n")
    assert _results(factory_root)["LIN-ORG-01"]["verdict"] == "pass"

    record["messages"][1]["content"] += f" Call {testkit.EXCLUDED_TOOL}."
    path.write_text(json.dumps(record) + "\n")
    assert _results(factory_root)["LIN-ORG-01"]["verdict"] == "fail"


# ── planted violations ───────────────────────────────────────────────────────
def test_planted_mixed_org_row_fails(factory_root):
    testkit.plant_mixed_org_row(factory_root)
    result = _results(factory_root)["LIN-ORG-01"]
    assert result["verdict"] == "fail"
    headlines = " | ".join(f["headline"] for f in result["findings"])
    assert testkit.EXCLUDED_ORG_PREFIX in headlines
    assert testkit.EXCLUDED_TOOL in headlines
    assert all(f["class"] == "contamination" for f in result["findings"])


def test_planted_contaminated_anchor_fails_with_both_counts(factory_root):
    """The catch #1 signature: a dose anchor derived outside the partition."""
    testkit.plant_contaminated_anchor(factory_root, claimed=352)
    result = _results(factory_root)["LIN-ORG-01"]
    assert result["verdict"] == "fail"
    finding = next(f for f in result["findings"]
                   if "mixed-org contamination signature" in f["headline"])
    assert finding["counts"] == {"anchor_claimed": 352, "pool_actual": 5,
                                 "night": 720}
    assert finding["severity"] == "blocking"


def test_matching_anchor_passes(factory_root):
    """Both directions: the same census must NOT fire on a truthful anchor."""
    result = _results(factory_root)["LIN-ORG-01"]
    assert not [f for f in result["findings"]
                if "contamination signature" in f["headline"]]


def test_planted_anomalous_night_in_pool_is_suspect(factory_root):
    testkit.plant_anomalous_night(factory_root)
    result = _results(factory_root)["LIN-POOL-03"]
    assert result["verdict"] == "suspect"
    assert "2026-07-21" in result["findings"][0]["headline"]


def test_corpus_sourced_from_anomalous_night_fails(factory_root):
    testkit.plant_anomalous_night(factory_root)
    testkit.write_corpus(factory_root, source_row_id="row-5", night="2026-07-21")
    result = _results(factory_root)["LIN-POOL-03"]
    assert result["verdict"] == "fail"


def test_planted_untraceable_corpus_row_fails(factory_root):
    testkit.plant_untraceable_corpus_row(factory_root)
    result = _results(factory_root)["LIN-SRC-02"]
    assert result["verdict"] == "fail"
    assert result["counts"]["untraceable_records"] == 1
    assert "L18" in result["findings"][0]["lesson_refs"]


# ── degradation direction: missing input is suspect, never pass ─────────────
def test_no_corpus_means_src02_suspect_not_pass(factory_root):
    result = _results(factory_root)["LIN-SRC-02"]
    assert result["verdict"] == "suspect"


def test_missing_exclusion_list_is_suspect(factory_root):
    (factory_root / "runs" / testkit.SLUG / "audit" / "exclusions.yaml").unlink()
    results = _results(factory_root)
    assert results["LIN-ORG-01"]["verdict"] == "suspect"
    assert results["LIN-POOL-03"]["verdict"] == "suspect"


def test_missing_pools_are_a_named_failure_not_a_pass(factory_root):
    inputs = factory_root / "runs" / testkit.SLUG / "corpus" / "inputs"
    (inputs / "selection-pool.json").unlink()
    (inputs / "train-pool.json").unlink()
    result = _results(factory_root)["LIN-ORG-01"]
    assert result["verdict"] == "suspect"
    assert "0 rows scanned is a named failure" in result["findings"][0]["headline"]
