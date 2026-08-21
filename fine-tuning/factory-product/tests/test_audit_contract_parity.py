"""check_contract_parity — catch #2 (stale envelope / deployment drift) both
directions: a matching served fingerprint passes, a drifted one fails naming
the tool, and OFFLINE (no creds, no cache) degrades to suspect — never pass.
Tests always inject `observed` or force-offline; they never touch a network."""

import json

import pytest

from audit import check_contract_parity, testkit
from audit.audit_lib import RunContext


@pytest.fixture(autouse=True)
def _no_prod_creds(monkeypatch):
    """Force the offline path unless a test injects `observed` — a developer
    machine with prod creds exported must not make this suite hit prod."""
    for var in ("NEXT_PUBLIC_SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
                "PU", "PK"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def factory_root(tmp_path):
    return testkit.build_tree(tmp_path)


def _results(factory_root, observed=None):
    ctx = RunContext(testkit.SLUG, factory_root=factory_root)
    return {r["check_id"]: r for r in check_contract_parity.run(ctx, observed=observed)}


# ── PAR-ENV-01 both directions ───────────────────────────────────────────────
def test_matching_fingerprint_passes(factory_root):
    result = _results(factory_root, observed=testkit.gold_observed())["PAR-ENV-01"]
    assert result["verdict"] == "pass"
    assert result["counts"]["observed_rows"] == 100
    assert result["counts"]["unoffered_tools_called_by_prod"] == 0


def test_drifted_fingerprint_fails_naming_the_tool(factory_root):
    result = _results(factory_root, observed=testkit.drifted_observed())["PAR-ENV-01"]
    assert result["verdict"] == "fail"
    finding = next(f for f in result["findings"] if f["verdict"] == "fail")
    assert "query_account_orders_x(x37)" in finding["headline"]
    assert "stale envelope/contract drift" in finding["headline"]
    assert finding["severity"] == "blocking"
    assert "L51" in finding["lesson_refs"]


def test_offline_with_no_cache_is_suspect_never_pass(factory_root):
    result = _results(factory_root)["PAR-ENV-01"]
    assert result["verdict"] == "suspect"
    assert "NOT verified" in result["findings"][0]["headline"]


def test_offline_falls_back_to_the_cached_snapshot(factory_root):
    audit_dir = factory_root / "runs" / testkit.SLUG / "audit"
    (audit_dir / "contract-snapshot.json").write_text(
        json.dumps(testkit.drifted_observed()))
    result = _results(factory_root)["PAR-ENV-01"]
    assert result["verdict"] == "fail"  # cached drift still fails loudly


def test_zero_row_pull_is_a_named_failure_not_a_pass(factory_root):
    observed = testkit.gold_observed()
    observed["rows_scanned"] = 0
    observed["observed_tool_names"] = {}
    result = _results(factory_root, observed=observed)["PAR-ENV-01"]
    assert result["verdict"] == "suspect"
    assert "0-row success is a named failure" in result["findings"][0]["headline"]


def test_unobserved_offered_tools_are_info_not_fail(factory_root):
    observed = testkit.gold_observed()
    del observed["observed_tool_names"]["read_b"]
    result = _results(factory_root, observed=observed)["PAR-ENV-01"]
    assert result["verdict"] == "pass"
    assert any("unobserved" in f["headline"] for f in result["findings"])


# ── PAR-ACT-02 ───────────────────────────────────────────────────────────────
def test_forbidden_corpus_gold_class_fails(factory_root):
    testkit.write_corpus(factory_root, gold_class="custom")
    result = _results(factory_root, observed=testkit.gold_observed())["PAR-ACT-02"]
    assert result["verdict"] == "fail"
    finding = next(f for f in result["findings"] if f["verdict"] == "fail")
    assert "train/serve contradiction" in finding["headline"]
    assert finding["counts"]["forbidden_gold_records"] == 1


def test_observed_rogue_output_type_fails(factory_root):
    observed = testkit.gold_observed()
    observed["output_types"]["custom"] = 3
    result = _results(factory_root, observed=observed)["PAR-ACT-02"]
    assert result["verdict"] == "fail"


def test_admissible_corpus_passes(factory_root):
    testkit.write_corpus(factory_root)
    result = _results(factory_root, observed=testkit.gold_observed())["PAR-ACT-02"]
    assert result["verdict"] == "pass"


# ── PAR-EMIT-03 ──────────────────────────────────────────────────────────────
def test_emit_lints_pass_on_live_era_shapes(factory_root):
    testkit.write_corpus(factory_root)
    result = _results(factory_root, observed=testkit.gold_observed())["PAR-EMIT-03"]
    assert result["verdict"] == "pass"
    assert result["counts"]["corpus_records"] == 1


@pytest.mark.parametrize("mutation, count_key", [
    ({"archetype": "D"}, "bad_archetype"),
    ({"importance": 55}, "importance_drift"),
    ({"draft_subject": "Hello"}, "nonempty_drafts"),
    ({"rationale_len": 5}, "rationale_out_of_band"),
])
def test_emit_shape_violations_fail(factory_root, mutation, count_key):
    testkit.write_corpus(factory_root, **mutation)
    result = _results(factory_root, observed=testkit.gold_observed())["PAR-EMIT-03"]
    assert result["verdict"] == "fail"
    assert result["counts"][count_key] == 1


def test_no_corpus_means_emit_lints_suspect_not_pass(factory_root):
    result = _results(factory_root, observed=testkit.gold_observed())["PAR-EMIT-03"]
    assert result["verdict"] == "suspect"
    assert "skipped check is not a passed check" in result["findings"][0]["headline"]


# ── PAR-AGE-04 ───────────────────────────────────────────────────────────────
def test_fresh_snapshots_pass_the_staleness_clock(factory_root):
    result = _results(factory_root, observed=testkit.gold_observed())["PAR-AGE-04"]
    assert result["verdict"] == "pass"


def test_envelope_older_than_s3_export_is_suspect(factory_root):
    testkit.plant_stale_envelope_pull(factory_root)
    result = _results(factory_root, observed=testkit.gold_observed())["PAR-AGE-04"]
    assert result["verdict"] == "suspect"
    assert "predates the run's own S3 export" in result["findings"][0]["headline"]
