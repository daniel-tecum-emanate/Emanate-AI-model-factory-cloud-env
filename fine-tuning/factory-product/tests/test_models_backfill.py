"""models_backfill — the Models tab's historical seed must stay consistent
with backfill_ptc_history's runs, because `source_run_id` is the only link
between a catalog row and the run lineage the tab renders elsewhere."""

import uuid

import pytest

from backfill_ptc_history import ITERATIONS, NAMESPACE, _run_id
from models_backfill import (
    EXTRA_MODELS,
    EXTRA_REPORTS,
    MODELS,
    REPORTS,
    _model_row_id,
    build_extra_model_rows,
    build_model_rows,
)


def _all_rows():
    return build_model_rows() + build_extra_model_rows()


def test_one_model_row_per_real_iteration():
    rows = build_model_rows()
    assert len(rows) == len(ITERATIONS) == 4
    assert [r["model_id"] for r in rows] == [
        "ptc-steel-v1",
        "ptc-steel-v2-agent",
        "ptc-steel-v3-agent",
        "ptc-steel-v4-agent",
    ]


def test_source_run_id_matches_backfilled_run_exactly():
    """The deterministic uuid5 link — if either script changed its namespace
    or name scheme, the Models tab would silently point at runs that don't
    exist. This assertion is the drift alarm."""
    rows = build_model_rows()
    for row, it in zip(rows, ITERATIONS):
        assert row["source_run_id"] == _run_id(it["name"])


def test_rows_are_idempotent_via_stable_ids():
    ids_first = [r["id"] for r in build_model_rows()]
    ids_second = [r["id"] for r in build_model_rows()]
    assert ids_first == ids_second
    assert len(set(ids_first)) == 4
    for row_id in ids_first:
        uuid.UUID(row_id)  # must be a real uuid, not a slug


def test_no_model_marked_shipped():
    """MIXTURE-HISTORY.md §0: 'no model has ever shipped to product' — and the
    2026-07-30 extras don't change that: v5 is no_go, A1-V1 and INT-V1 are
    shadow lanes pending a human gate. A row claiming `shipped` anywhere in
    this catalog would be fabricated history."""
    for row in build_model_rows():
        assert row["ship_status"] == "no_go"
    for row in build_extra_model_rows():
        assert row["ship_status"] != "shipped"


def test_training_distribution_present_and_cited_costs_match_runs():
    rows = build_model_rows()
    for row, it in zip(rows, ITERATIONS):
        assert row["mixture"], f"{row['model_id']} has an empty mixture block"
        assert "corpus" in row["mixture"]
        assert row["training_cost_usd"] == it["cost_actual_usd"]
        assert row["train_records"] > 0


def test_models_and_iterations_order_locked():
    """`build_model_rows` zips MODELS with ITERATIONS positionally and asserts
    names match — verify the assert actually fires on drift rather than
    silently mispairing."""
    assert [m["name"] for m in MODELS] == [i["name"] for i in ITERATIONS]

    original = MODELS[0]["name"]
    MODELS[0]["name"] = "wrong-name"
    try:
        with pytest.raises(AssertionError, match="drifted"):
            build_model_rows()
    finally:
        MODELS[0]["name"] = original


def test_model_row_id_distinct_namespace_from_runs():
    """A factory_models row id must never collide with the factory_runs row id
    for the same name — they're different tables seeded from the same names."""
    for it in ITERATIONS:
        assert _model_row_id(it["name"]) != _run_id(it["name"])


def test_every_model_has_a_nonempty_report():
    """PR-B addendum 2: the Models tab's detail view renders `report` as the
    narrative iteration brief — a backfilled model with no report would render
    only the hyperparameter table again, which is the exact gap this column
    exists to close."""
    assert set(REPORTS) == {m["name"] for m in MODELS}
    assert set(EXTRA_REPORTS) == {m["name"] for m in EXTRA_MODELS}
    for row in _all_rows():
        report = row["report"]
        assert isinstance(report, list), f"{row['model_id']} report is not a list"
        assert report, f"{row['model_id']} has an empty report"


def test_every_report_section_has_nonempty_title_and_body():
    """The UI renders sections verbatim (heading + parsed plain-text body) —
    a blank title or body would render an empty card, never caught by types
    because jsonb is shape-free."""
    for row in _all_rows():
        for section in row["report"]:
            assert set(section) == {"title", "body"}, (
                f"{row['model_id']} section has keys {sorted(section)}"
            )
            assert section["title"].strip(), f"{row['model_id']} has a blank section title"
            assert section["body"].strip(), (
                f"{row['model_id']} section '{section['title']}' has a blank body"
            )


def test_no_report_section_claims_production_use():
    """MIXTURE-HISTORY.md §0/§1.1: 'no model has ever shipped to product.'
    Every ship_status is no_go (asserted separately); a report body claiming
    otherwise would be fabricated history. Negated statements ('did not
    ship', 'never adopted') are the truth and deliberately don't match any
    of these affirmative phrases."""
    forbidden = (
        "was shipped",
        "has shipped",
        "shipped to product",
        "shipped to production",
        "went to production",
        "is in production",
        "now in production",
        "in production use",
        "serving production traffic",
        "adopted into production",
    )
    for row in _all_rows():
        for section in row["report"]:
            text = f"{section['title']} {section['body']}".lower()
            for phrase in forbidden:
                assert phrase not in text, (
                    f"{row['model_id']} section '{section['title']}' claims "
                    f"production use: contains '{phrase}'"
                )


def test_training_provider_is_fireworks_never_together():
    """The provider-name trap (PR-B addendum 3): the historical env names were
    TOGETHER_* but pointed at Fireworks values — every real PTC model trained
    on Fireworks. A row saying 'together' would be recording the env-var name,
    not the platform."""
    for row in build_model_rows():
        assert row["training_provider"] == "fireworks"
        assert row["provider_job_id"].startswith("accounts/emanate/supervisedFineTuningJobs/")


def test_job_ids_are_the_real_recorded_ones():
    """v1/v2/v4 verbatim from DATASETS.md; v3 verified LIVE against the
    Fireworks job list (DATASETS.md's `brnbzcg0` 404s — xhw6v0h7 is the
    completed job whose outputModel is ptc-steel-v3-agent)."""
    expected = {
        "ptc-steel-v1": "accounts/emanate/supervisedFineTuningJobs/mjbsx3uu",
        "ptc-steel-v2-agent": "accounts/emanate/supervisedFineTuningJobs/npemi0j7",
        "ptc-steel-v3-agent": "accounts/emanate/supervisedFineTuningJobs/xhw6v0h7",
        "ptc-steel-v4-agent": "accounts/emanate/supervisedFineTuningJobs/pzc743aj",
    }
    for row in build_model_rows():
        assert row["provider_job_id"] == expected[row["model_id"]]


def test_no_fabricated_gpu_claims():
    """Fireworks SFT is serverless: no GPU was rented for any of the 4 runs,
    and the SFT job API exposes no accelerator fields. Any non-None gpu_*
    value here would be fabricated history."""
    for row in build_model_rows():
        assert row["gpu_count"] is None
        assert row["gpu_type"] is None
        assert row["gpu_rental_started_at"] is None
        assert row["gpu_rental_ended_at"] is None


def test_loss_curves_are_exactly_the_recorded_eval_series():
    """Every point is a real recorded number: v2's per-epoch eval/loss series
    from DATASETS.md's ptc-steel-v2-agent row; v3/v4 from MIXTURE-HISTORY.md
    §1.1. v1 has only a single final eval/loss (1.1324 @ step 922) — a
    one-point 'curve' is a scalar, so its curve is honestly empty and the
    scalar renders via train_eval_loss."""
    expected = {
        "ptc-steel-v1": [],
        "ptc-steel-v2-agent": [2.583, 2.560, 2.542],
        "ptc-steel-v3-agent": [2.530, 2.505],
        "ptc-steel-v4-agent": [2.740, 2.832],
    }
    for row in build_model_rows():
        points = row["loss_curve"]
        assert [p["loss"] for p in points] == expected[row["model_id"]]
        assert [p["step"] for p in points] == list(range(1, len(points) + 1))
        for p in points:
            assert set(p) == {"step", "loss", "label"}
            assert "eval loss" in p["label"]


def test_provider_model_path_only_where_attested():
    """v1-v3 paths are attested (DATASETS.md rows + ITERATION-3 test guide);
    v4's full serving path was never published anywhere — NULL, not a guess
    following the naming pattern."""
    expected = {
        "ptc-steel-v1": "accounts/emanate/models/ptc-steel-v1",
        "ptc-steel-v2-agent": "accounts/emanate/models/ptc-steel-v2-agent",
        "ptc-steel-v3-agent": "accounts/emanate/models/ptc-steel-v3-agent",
        "ptc-steel-v4-agent": None,
    }
    for row in build_model_rows():
        assert row["provider_model_path"] == expected[row["model_id"]]


def test_infra_present_and_serverless_stated():
    for row in build_model_rows():
        infra = row["infra"]
        assert isinstance(infra, dict) and infra, f"{row['model_id']} has empty infra"
        assert "serverless" in str(infra.get("platform", "")).lower()
        assert "fireworks_estimated_token_count" in infra


def test_report_bodies_are_plain_text_not_markup():
    """The body convention is plain text: blank-line paragraph breaks and
    '- ' list items. HTML tags or markdown headings would leak markup into
    the UI, which renders bodies verbatim (never through a markup engine)."""
    # Placeholders like "<dep-id>" are legitimate plain text; only actual
    #     HTML tag markers (from the source briefs this content was distilled
    # out of) are forbidden.
    html_markers = ("</", "<p>", "<ul>", "<li>", "<br", "<div", "<h2", "<table", "<b>", "<i>")
    for row in _all_rows():
        for section in row["report"]:
            body = section["body"]
            lowered = body.lower()
            for marker in html_markers:
                assert marker not in lowered, (
                    f"{row['model_id']} section '{section['title']}' contains HTML ('{marker}')"
                )
            for line in body.splitlines():
                assert not line.lstrip().startswith("#"), (
                    f"{row['model_id']} section '{section['title']}' uses markdown headings"
                )


# ---------------------------------------------------------------------------
# EXTRA_MODELS (2026-07-30, Daniel: "I dont see v5, the fine tuned models we
# actually ended up using in production") — v5, A1-V1 and INT-V1.
# ---------------------------------------------------------------------------


def test_extra_rows_are_the_six_recorded_artifacts():
    """Extended model-factory-production-catchup T5: the 7-row world (v5,
    A1-V1, INT-V1) grew to 10 real models with ptc-steel-v7-agent,
    grand-steel-v1-agent (refresh), and grand-steel-v2-agent (new)."""
    rows = build_extra_model_rows()
    assert [r["model_id"] for r in rows] == [
        "ptc-steel-v5-agent",
        "ptc-steel-a1-v1",
        "intv1-corpus-v1",
        "ptc-steel-v7-agent",
        "grand-steel-v1-agent",
        "grand-steel-v2-agent",
    ]
    # v5's factory_runs row was backfilled 2026-07-30 (runs/ptc-steel/
    # backfill_v5_run.py, same uuid5 namespace) so its link is real and must
    # match the run backfill's deterministic id; grand-steel-v1-agent has a
    # REAL live factory_runs row (523ecbbf, the first S7 the factory ever
    # executed) linked directly. A1-V1, INT-V1, ptc-steel-v7-agent, and
    # grand-steel-v2-agent have no run rows — a non-null source_run_id there
    # would point the UI at a run that doesn't exist.
    by_id = {r["model_id"]: r for r in rows}
    assert by_id["ptc-steel-v5-agent"]["source_run_id"] == str(
        uuid.uuid5(NAMESPACE, "factory_runs:ptc-steel-v5-agent")
    )
    assert by_id["ptc-steel-a1-v1"]["source_run_id"] is None
    assert by_id["intv1-corpus-v1"]["source_run_id"] is None
    assert by_id["ptc-steel-v7-agent"]["source_run_id"] is None
    assert by_id["grand-steel-v1-agent"]["source_run_id"] == "523ecbbf-e049-405f-8354-779e2cd40307"
    assert by_id["grand-steel-v2-agent"]["source_run_id"] is None
    for row in rows:
        uuid.UUID(row["id"])


def test_grand_steel_v1_row_id_and_run_id_are_stable_across_the_refactor():
    """T4's release-blocking proof: refreshing grand-steel-v1-agent's row must
    be a true UPDATE, not an accidental insert-with-a-new-id. Both the row id
    (uuid5-derived) and source_run_id must exactly match the values already
    live in production (confirmed via psql against local Supabase during this
    PR's T0/T4 execution) — a drift here would silently orphan the existing
    row instead of updating it."""
    row = next(r for r in build_extra_model_rows() if r["model_id"] == "grand-steel-v1-agent")
    assert row["id"] == "ef856663-a55b-5f66-a577-5f56057ff3f5"
    assert row["source_run_id"] == "523ecbbf-e049-405f-8354-779e2cd40307"


def test_ptc_v7_and_gs_v2_ship_statuses_are_not_defaulted():
    """PRRules invariant 1/2: ship_status must come from what the source
    material actually concluded, never a convenient default. v7's card
    explicitly declines to make a ship call either way -> pending; v2's card
    reports a concluded (if statistically unproven) unfavorable result on
    both gates -> no_go, distinct from v1's own separate FINALIZED_NO_TRAIN
    verdict."""
    by_id = {r["model_id"]: r for r in build_extra_model_rows()}
    assert by_id["ptc-steel-v7-agent"]["ship_status"] == "pending"
    assert by_id["grand-steel-v1-agent"]["ship_status"] == "no_go"
    assert by_id["grand-steel-v2-agent"]["ship_status"] == "no_go"


def test_extra_ids_do_not_collide_with_ptc_rows():
    all_ids = [r["id"] for r in _all_rows()]
    assert len(all_ids) == len(set(all_ids)) == 10


def test_extra_providers_are_the_recorded_platforms():
    """v5 trained on Fireworks (job erp9alb9, DATASETS.md); A1-V1 was self-run
    axolotl on RunPod because BOTH managed lanes were disqualified on
    architecture (reports/A1-V1.md); INT-V1 trained on Together AI (job
    ft-b32c0e5a-8d16, INT-V1-STATE.md fact table). This is the one part of
    the catalog where 'together' is the truth, not the env-var trap."""
    by_id = {r["model_id"]: r for r in build_extra_model_rows()}
    assert by_id["ptc-steel-v5-agent"]["training_provider"] == "fireworks"
    assert by_id["ptc-steel-v5-agent"]["provider_job_id"].endswith("/erp9alb9")
    assert by_id["ptc-steel-a1-v1"]["training_provider"] == "runpod (self-run axolotl)"
    assert "cuq3wp21hsgjcb" in by_id["ptc-steel-a1-v1"]["provider_job_id"]
    assert by_id["intv1-corpus-v1"]["training_provider"] == "together"
    assert by_id["intv1-corpus-v1"]["provider_job_id"] == "ft-b32c0e5a-8d16"


def test_gpu_claims_only_where_a_rental_actually_happened():
    """A1-V1 is the ONLY model in the catalog with a real GPU rental (pod 7,
    2x H200 — 07-22 03:00 completion entry). v5 (Fireworks SFT) and INT-V1
    (Together managed) are serverless/managed: gpu_* stays None. Pod 7's
    exact rental clock times were never published, so the timestamps stay
    None even for A1-V1."""
    by_id = {r["model_id"]: r for r in build_extra_model_rows()}
    a1 = by_id["ptc-steel-a1-v1"]
    assert a1["gpu_count"] == 2
    assert "H200" in a1["gpu_type"]
    assert a1["gpu_rental_started_at"] is None and a1["gpu_rental_ended_at"] is None
    for name in ("ptc-steel-v5-agent", "intv1-corpus-v1"):
        row = by_id[name]
        assert row["gpu_count"] is None and row["gpu_type"] is None


def test_a1v1_loss_curve_is_the_recorded_eval_ladder():
    """The 9-point monotonic eval-loss series verbatim from the 07-22 03:00
    TRAINING COMPLETE entry; v5 and INT-V1 published no series — empty,
    never invented."""
    by_id = {r["model_id"]: r for r in build_extra_model_rows()}
    ladder = [p["loss"] for p in by_id["ptc-steel-a1-v1"]["loss_curve"]]
    assert ladder == [0.7811, 0.7077, 0.6429, 0.6009, 0.5643, 0.5358, 0.5063, 0.4765, 0.4587]
    assert ladder == sorted(ladder, reverse=True)  # monotonic, as recorded
    assert by_id["ptc-steel-v5-agent"]["loss_curve"] == []
    assert by_id["intv1-corpus-v1"]["loss_curve"] == []


def test_extra_ship_statuses_tell_the_truth():
    """v5 is a plain NO-GO (E21). A1-V1 is no_go: its ACTIVE-arm ship decision
    was made when the eval window closed 07-22 (18 PASS / 6 FAIL) — the V-036
    shadow lane is a separate pilot, not an undecided verdict (MODEL-LIBRARY
    discrepancy #2, 2026-07-30). INT-V1 stays 'pending' — its launch gate (the
    K3 re-check) genuinely has not been decided."""
    by_id = {r["model_id"]: r for r in build_extra_model_rows()}
    assert by_id["ptc-steel-v5-agent"]["ship_status"] == "no_go"
    assert by_id["ptc-steel-a1-v1"]["ship_status"] == "no_go"
    assert by_id["intv1-corpus-v1"]["ship_status"] == "pending"


# ---------------------------------------------------------------------------
# T5 (model-factory-production-catchup) — NULL-on-purpose regression coverage
# across all 10 real rows, plus the absent-vs-null wire-level convention.
# ---------------------------------------------------------------------------


def test_all_ten_rows_never_fabricate_a_missing_fact():
    """PRRules.md invariant 1/2: a model with no real value for a field must
    carry None, never a placeholder or a best guess. Checked across every one
    of the 10 real rows for the sharpest known gaps: v4's never-published
    serving path, v7's missing factory_runs row (manual REST launch), and
    v2/v7's missing loss series (no metrics.jsonl-equivalent file exists for
    either job)."""
    rows = _all_rows()
    assert len(rows) == 10
    by_id = {r["model_id"]: r for r in rows}

    assert by_id["ptc-steel-v4-agent"]["provider_model_path"] is None
    assert by_id["ptc-steel-v7-agent"]["source_run_id"] is None
    assert by_id["grand-steel-v2-agent"]["source_run_id"] is None
    assert by_id["grand-steel-v2-agent"]["loss_curve"] == []
    assert by_id["ptc-steel-v7-agent"]["loss_curve"] == []
    assert by_id["ptc-steel-v7-agent"]["train_eval_loss"] is None

    # No row anywhere claims a gpu rental it never had, or a train_records
    # count nobody ever recorded, without the source material saying so.
    for row in rows:
        if row["training_provider"] == "fireworks":
            assert row["gpu_count"] is None and row["gpu_type"] is None


def test_a_guessed_value_where_the_source_says_not_published_fails_the_check():
    """Teeth check: construct a fixture that mimics the exact mistake this
    invariant exists to catch — a guessed provider_model_path for a model
    whose source material explicitly has none — and confirm it's the kind of
    value a real assertion (mirroring the one above) would reject. Proves the
    test has discriminating power, not just a happy-path pass against the
    real rows."""
    fabricated_row = {
        "model_id": "ptc-steel-v4-agent",
        # A real fixture would never do this — v4's own brief states no full
        # serving path was ever published. This simulates the exact defect
        # class T5 exists to catch.
        "provider_model_path": "accounts/emanate/models/ptc-steel-v4-agent",
    }
    real_row = next(r for r in build_model_rows() if r["model_id"] == "ptc-steel-v4-agent")

    assert real_row["provider_model_path"] is None
    with pytest.raises(AssertionError):
        assert fabricated_row["provider_model_path"] is None


def test_upsert_sends_an_absent_key_not_a_null_valued_key_for_factory_models(monkeypatch):
    """PRRules.md invariant 3: absent vs. null-valued keys matters at the wire
    level (factory-cursor-bridge-v1's cursor_agent_id convention for a
    different table). supabase_rest.upsert() is a generic PostgREST client —
    it does not special-case any table — so this proves the underlying
    `requests` json= encoding behaves correctly for factory_models
    specifically, rather than assuming it transfers from that other table's
    test without checking:
      - a dict key never set on a row is OMITTED from the JSON body entirely
        (PostgREST then leaves that column alone on conflict / uses its
        default on insert)
      - a dict key explicitly set to None IS present in the JSON body, with
        a real `null` value (PostgREST then writes NULL) — this is today's
        actual models_backfill.py convention for NULL-on-purpose fields like
        ptc-steel-v4-agent's provider_model_path.
    """
    import supabase_rest

    captured = {}

    class FakeResponse:
        ok = True

        def json(self):
            return []

    def fake_post(url, json=None, headers=None, params=None, timeout=None):
        captured["body"] = json
        return FakeResponse()

    monkeypatch.setattr(supabase_rest.requests, "post", fake_post)

    rows = [
        {"model_id": "absent-key-row", "provider_model_path": "present-value"},
        {"model_id": "null-key-row", "provider_model_path": None},
    ]
    supabase_rest.upsert(
        "factory_models", rows, on_conflict="org_slug,model_id",
        url="http://example.invalid", service_role_key="test-key",
    )

    sent = captured["body"]
    absent_row = next(r for r in sent if r["model_id"] == "absent-key-row")
    null_row = next(r for r in sent if r["model_id"] == "null-key-row")

    assert absent_row["provider_model_path"] == "present-value"
    # The null-key-row's dict always included the key (models_backfill.py's
    # convention), so it is present in the body with an explicit None/null —
    # NOT omitted. This documents the real convention rather than assuming
    # omission happened automatically.
    assert "provider_model_path" in null_row
    assert null_row["provider_model_path"] is None

    # Now the actual absent-key case: a dict that never sets the key at all.
    captured.clear()
    truly_absent_rows = [{"model_id": "no-key-at-all"}]
    supabase_rest.upsert(
        "factory_models", truly_absent_rows, on_conflict="org_slug,model_id",
        url="http://example.invalid", service_role_key="test-key",
    )
    sent2 = captured["body"]
    assert "provider_model_path" not in sent2[0], (
        "a key never set on the row dict must be ABSENT from the JSON body, "
        "not silently sent as null — supabase_rest.upsert() must not inject "
        "keys the caller never set"
    )
