"""S8 scoring logic (`factory_eval.py`), built against fixtures.

`factory.py`'s `stage_eval` still dispatches AgentSpec nodes and resolves to
`needs_self_dispatch` — it never calls a real deployed model, and this test
module does not either. What's under test is the scoring LOGIC: given
predictions and labels for a set of items, does it grade honestly against the
sealed held-out set, refuse a constant predictor's inflated number, refuse a
low-answer-rate run, turn a cross-seal comparison into an `incomparable`
verdict rather than a number, and pick the right verdict for a first train vs.
a retrain without guessing — and does the row it would write to
`factory_model_evals` only ever use values the DB's own CHECK constraints
accept.

Mirrors `tests/test_heldout_anchor.py`'s fixture style: `factory_heldout.HERE`
is monkeypatched to a tmp_path so `seal()` writes real manifests to a scratch
directory instead of the repo's `runs/`.
"""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory_eval as fe  # noqa: E402
import factory_heldout as fh  # noqa: E402

SLUG = "grand-steel"
ITERATION = 1

# Mirrors the real Grand Steel held-out split named in the task: 71 accounts,
# 56 no_action (majority) and 15 discriminating (minority) accounts. Ids are
# deliberately human-readable, the way real corpus item ids are.
MAJORITY_IDS = [f"acct-Grand Steel-fact-{n}" for n in range(56)]
MINORITY_IDS = [f"acct-Grand Steel-fact-{n}" for n in range(56, 71)]
ALL_IDS = MAJORITY_IDS + MINORITY_IDS
LABELS = {i: "no_action" for i in MAJORITY_IDS}
LABELS.update({i: "escalate" for i in MINORITY_IDS})


@pytest.fixture
def sealed(tmp_path, monkeypatch):
    """Seal a real held-out manifest over ALL_IDS so every item above is held
    out (fraction=1.0), keeping the fixture simple: what's under test is
    scoring, not which items fall inside the anchor's sample."""
    monkeypatch.setattr(fh, "HERE", tmp_path)
    manifest = fh.seal(SLUG, ALL_IDS, iteration=ITERATION, fraction=1.0, seed=7)
    return manifest


def _predict_constant(label, ids=ALL_IDS):
    return {i: label for i in ids}


def _predict_perfect(ids=ALL_IDS):
    return dict(LABELS)


# --------------------------------------------------------------------------
# Balanced agreement — the headline metric
# --------------------------------------------------------------------------


def test_constant_predictor_scores_exactly_half(sealed):
    """78.9% of Grand Steel accounts are no_action; a predictor that always
    answers no_action must NOT be rewarded for that skew."""
    predictions = _predict_constant("no_action")
    result = fe.score_arm(SLUG, ITERATION, predictions, LABELS)
    assert result["status"] == "scored"
    assert result["balanced_agreement"] == pytest.approx(0.5)
    assert result["class_accuracy"]["no_action"] == pytest.approx(1.0)
    assert result["class_accuracy"]["escalate"] == pytest.approx(0.0)


def test_constant_predictor_of_the_minority_label_also_scores_half(sealed):
    """Symmetry check: the 0.5 result is a property of "constant", not of
    which label happens to be the majority."""
    predictions = _predict_constant("escalate")
    result = fe.score_arm(SLUG, ITERATION, predictions, LABELS)
    assert result["balanced_agreement"] == pytest.approx(0.5)


def test_a_perfect_predictor_scores_one(sealed):
    result = fe.score_arm(SLUG, ITERATION, _predict_perfect(), LABELS)
    assert result["balanced_agreement"] == pytest.approx(1.0)


def test_unanswered_items_count_as_incorrect_not_skipped(sealed):
    """An item with no prediction must not be dropped from its class's
    denominator — that would let silently skipping hard items inflate the
    score, the same hazard MIN_VALID_EMIT_RATE exists to catch at the door."""
    predictions = _predict_perfect()
    del predictions[MINORITY_IDS[0]]  # still >= 80% answered overall
    result = fe.score_arm(SLUG, ITERATION, predictions, LABELS)
    assert result["class_accuracy"]["escalate"] == pytest.approx(14 / 15)


# --------------------------------------------------------------------------
# No held-out anchor, no score
# --------------------------------------------------------------------------


def test_scoring_without_a_sealed_manifest_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(fh, "HERE", tmp_path)
    with pytest.raises(fe.EvalRefused, match="no sealed held-out manifest"):
        fe.score_arm(SLUG, ITERATION, _predict_perfect(), LABELS)


# --------------------------------------------------------------------------
# Valid-emit floor
# --------------------------------------------------------------------------


def test_below_80_percent_valid_emit_is_refused(sealed):
    """An arm that answered well under 80% of the held-out set — e.g. because
    its API key had expired — must not produce a storable score at all."""
    predictions = _predict_perfect()
    # Answer only 10 of 71 held-out items (~14%), well under the floor.
    keep = set(ALL_IDS[:10])
    predictions = {k: v for k, v in predictions.items() if k in keep}
    with pytest.raises(fe.EvalRefused, match="valid-emit rate"):
        fe.score_arm(SLUG, ITERATION, predictions, LABELS)


def test_exactly_at_the_floor_is_accepted(sealed):
    """80% is the floor, not the cutoff for refusal — >=80% must score."""
    predictions = _predict_perfect()
    total = len(ALL_IDS)
    keep_n = -(-total * 80 // 100)  # ceil(total * 0.8)
    keep = set(ALL_IDS[:keep_n])
    predictions = {k: v for k, v in predictions.items() if k in keep}
    result = fe.score_arm(SLUG, ITERATION, predictions, LABELS)
    assert result["status"] == "scored"
    assert result["valid_emit_rate"] >= fe.MIN_VALID_EMIT_RATE


def test_just_under_the_floor_is_refused(sealed):
    predictions = _predict_perfect()
    total = len(ALL_IDS)
    keep_n = -(-total * 80 // 100) - 1  # one item short of the ceiling
    keep = set(ALL_IDS[:keep_n])
    predictions = {k: v for k, v in predictions.items() if k in keep}
    with pytest.raises(fe.EvalRefused, match="valid-emit rate"):
        fe.score_arm(SLUG, ITERATION, predictions, LABELS)


def test_a_refused_score_never_reaches_write_score(sealed, monkeypatch):
    """Even if a caller ignores the raised exception and hand-builds a dict, the
    write path refuses anything not carrying status == 'scored'."""
    calls = []
    monkeypatch.setattr(fe.supabase_rest, "upsert", lambda *a, **k: calls.append((a, k)))
    fake = {"status": "refused_low_valid_emit", "iteration": ITERATION}
    with pytest.raises(fe.EvalRefused, match="not a row"):
        fe.write_score(
            SLUG, "candidate-v2-agent", fe.ARM_CANDIDATE, fe.FIRST_RUN, fake,
            {"verdict": fe.VERDICT_IMPROVED, "lift_over_base": 0.3, "lift_over_incumbent": None},
        )
    assert calls == []


# --------------------------------------------------------------------------
# Comparability — population_fingerprint
# --------------------------------------------------------------------------


def test_cross_fingerprint_comparison_is_refused(sealed):
    a = fe.score_arm(SLUG, ITERATION, _predict_perfect(), LABELS)
    b = dict(a)
    b["population_fingerprint"] = "a-different-seal-entirely"
    with pytest.raises(fe.EvalRefused, match="incomparable"):
        fe.compare_scores(a, b)


def test_same_fingerprint_compares_cleanly(sealed):
    base = fe.score_arm(SLUG, ITERATION, _predict_constant("no_action"), LABELS)
    candidate = fe.score_arm(SLUG, ITERATION, _predict_perfect(), LABELS)
    delta = fe.compare_scores(base, candidate)
    assert delta == pytest.approx(candidate["balanced_agreement"] - base["balanced_agreement"])


def test_cross_fingerprint_verdict_is_incomparable_not_an_exception(sealed):
    """`compute_verdict` must not blow up on a reseal — it records the attempt
    as a real `incomparable` verdict instead of silently producing nothing."""
    base = fe.score_arm(SLUG, ITERATION, _predict_constant("no_action"), LABELS)
    candidate = fe.score_arm(SLUG, ITERATION, _predict_perfect(), LABELS)
    candidate = dict(candidate)
    candidate["population_fingerprint"] = "a-different-seal-entirely"

    result = fe.compute_verdict(fe.FIRST_RUN, candidate, base_score=base)
    assert result["verdict"] == fe.VERDICT_INCOMPARABLE
    assert result["lift_over_base"] is None
    assert result["lift_over_incumbent"] is None


# --------------------------------------------------------------------------
# Verdicts — first train vs. retrain
# --------------------------------------------------------------------------


def test_first_train_matching_base_is_no_lift_not_a_pass(sealed):
    """Merely matching the base is a null result, never a pass."""
    base = fe.score_arm(SLUG, ITERATION, _predict_constant("no_action"), LABELS)
    candidate = fe.score_arm(SLUG, ITERATION, _predict_constant("no_action"), LABELS)
    result = fe.compute_verdict(fe.FIRST_RUN, candidate, base_score=base)
    assert result["verdict"] == fe.VERDICT_NO_LIFT_OVER_BASE


def test_first_train_clearing_the_margin_is_improved(sealed):
    base = fe.score_arm(SLUG, ITERATION, _predict_constant("no_action"), LABELS)
    candidate = fe.score_arm(SLUG, ITERATION, _predict_perfect(), LABELS)
    result = fe.compute_verdict(fe.FIRST_RUN, candidate, base_score=base)
    assert result["verdict"] == fe.VERDICT_IMPROVED
    assert result["lift_over_base"] == pytest.approx(0.5)


def test_retrain_beating_base_but_losing_to_incumbent_is_regressed(sealed):
    """The charter's exact failure mode: progress on a base-relative chart that
    is actually a regression against what's shipped today."""
    base = fe.score_arm(SLUG, ITERATION, _predict_constant("escalate"), LABELS)  # 0.5
    incumbent = fe.score_arm(SLUG, ITERATION, _predict_perfect(), LABELS)  # 1.0
    # Candidate beats base (0.5) but is well below incumbent (1.0).
    candidate_predictions = _predict_perfect()
    for item_id in MINORITY_IDS[:6]:  # wrong on 6 of 15 minority items
        candidate_predictions[item_id] = "no_action"
    candidate = fe.score_arm(SLUG, ITERATION, candidate_predictions, LABELS)
    assert candidate["balanced_agreement"] > base["balanced_agreement"]
    result = fe.compute_verdict(
        fe.RETRAIN, candidate, base_score=base, incumbent_score=incumbent
    )
    assert result["verdict"] == fe.VERDICT_REGRESSED
    assert result["lift_over_incumbent"] < 0
    assert result["lift_over_base"] > 0  # recorded for context, did not drive the verdict


def test_retrain_beating_incumbent_is_improved(sealed):
    incumbent = fe.score_arm(SLUG, ITERATION, _predict_constant("no_action"), LABELS)  # 0.5
    candidate = fe.score_arm(SLUG, ITERATION, _predict_perfect(), LABELS)  # 1.0
    result = fe.compute_verdict(fe.RETRAIN, candidate, incumbent_score=incumbent)
    assert result["verdict"] == fe.VERDICT_IMPROVED


def test_first_run_with_an_incumbent_score_is_refused(sealed):
    """run_kind disagreeing with the evidence: a first train has no incumbent."""
    base = fe.score_arm(SLUG, ITERATION, _predict_constant("no_action"), LABELS)
    incumbent = fe.score_arm(SLUG, ITERATION, _predict_perfect(), LABELS)
    with pytest.raises(fe.EvalRefused, match="disagrees with the evidence"):
        fe.compute_verdict(fe.FIRST_RUN, base, base_score=base, incumbent_score=incumbent)


def test_retrain_without_an_incumbent_score_is_refused(sealed):
    base = fe.score_arm(SLUG, ITERATION, _predict_constant("no_action"), LABELS)
    with pytest.raises(fe.EvalRefused, match="disagrees with the evidence"):
        fe.compute_verdict(fe.RETRAIN, base, base_score=base)


def test_unknown_run_kind_is_refused(sealed):
    score = fe.score_arm(SLUG, ITERATION, _predict_perfect(), LABELS)
    with pytest.raises(fe.EvalRefused, match="unknown run_kind"):
        fe.compute_verdict("sideways_retrain", score)


# --------------------------------------------------------------------------
# Margin floor — no verdict rests on a single account
# --------------------------------------------------------------------------


def test_margin_floor_rejects_a_single_account_swing(sealed):
    """15 discriminating (minority) items -> a single flipped account swings
    balanced_agreement by 1/(2*15) ~= 3.3%. A delta of exactly one flip must
    not be called a lift or a regression."""
    base_predictions = _predict_perfect()
    candidate_predictions = _predict_perfect()
    candidate_predictions[MINORITY_IDS[0]] = "no_action"  # exactly one flip

    base = fe.score_arm(SLUG, ITERATION, base_predictions, LABELS)
    candidate = fe.score_arm(SLUG, ITERATION, candidate_predictions, LABELS)

    assert base["balanced_agreement"] != candidate["balanced_agreement"]
    assert abs(base["balanced_agreement"] - candidate["balanced_agreement"]) == pytest.approx(
        base["margin_floor"]
    )
    result = fe.compute_verdict(fe.RETRAIN, candidate, incumbent_score=base)
    assert result["verdict"] == fe.VERDICT_INCONCLUSIVE


def test_margin_floor_allows_a_swing_that_clears_two_accounts(sealed):
    base_predictions = _predict_perfect()
    candidate_predictions = _predict_perfect()
    candidate_predictions[MINORITY_IDS[0]] = "no_action"
    candidate_predictions[MINORITY_IDS[1]] = "no_action"

    base = fe.score_arm(SLUG, ITERATION, base_predictions, LABELS)
    candidate = fe.score_arm(SLUG, ITERATION, candidate_predictions, LABELS)
    result = fe.compute_verdict(fe.RETRAIN, candidate, incumbent_score=base)
    assert result["verdict"] == fe.VERDICT_REGRESSED


def test_margin_floor_needs_at_least_two_classes(sealed):
    single_class_labels = {i: "no_action" for i in ALL_IDS}
    fh.seal("single-class-org", ALL_IDS, iteration=1, fraction=1.0, seed=1)
    predictions = {i: "no_action" for i in ALL_IDS}
    with pytest.raises(fe.EvalRefused, match="at least a majority and a minority class"):
        fe.score_arm("single-class-org", 1, predictions, single_class_labels)


# --------------------------------------------------------------------------
# Closed vocabularies — what the DB's CHECK constraints will actually accept
# --------------------------------------------------------------------------


def test_verdicts_emitted_by_compute_verdict_are_all_in_the_closed_vocabulary(sealed):
    """factory_model_evals.verdict is CHECK-constrained to exactly five values.
    Every branch compute_verdict can take must land inside that set."""
    base = fe.score_arm(SLUG, ITERATION, _predict_constant("no_action"), LABELS)  # 0.5
    incumbent = fe.score_arm(SLUG, ITERATION, _predict_constant("escalate"), LABELS)  # 0.5
    perfect = fe.score_arm(SLUG, ITERATION, _predict_perfect(), LABELS)  # 1.0
    reseal = dict(perfect)
    reseal["population_fingerprint"] = "different-seal"

    seen = {
        fe.compute_verdict(fe.FIRST_RUN, perfect, base_score=base)["verdict"],
        fe.compute_verdict(fe.FIRST_RUN, base, base_score=base)["verdict"],
        fe.compute_verdict(fe.RETRAIN, perfect, incumbent_score=incumbent)["verdict"],
        fe.compute_verdict(fe.RETRAIN, incumbent, incumbent_score=incumbent)["verdict"],
        fe.compute_verdict(fe.FIRST_RUN, reseal, base_score=base)["verdict"],
    }
    assert seen <= set(fe.VERDICTS)
    # And the fixture actually exercised more than one branch, not a tautology.
    assert len(seen) > 1


def test_write_score_refuses_an_arm_outside_the_closed_vocabulary(sealed):
    score = fe.score_arm(SLUG, ITERATION, _predict_perfect(), LABELS)
    verdict = fe.compute_verdict(fe.FIRST_RUN, score, base_score=score)
    with pytest.raises(fe.EvalRefused, match="is not one of"):
        fe.write_score(SLUG, "candidate-v2-agent", "champion", fe.FIRST_RUN, score, verdict)


def test_write_score_refuses_a_verdict_outside_the_closed_vocabulary(sealed):
    score = fe.score_arm(SLUG, ITERATION, _predict_perfect(), LABELS)
    with pytest.raises(fe.EvalRefused, match="is not one of"):
        fe.write_score(
            SLUG, "candidate-v2-agent", fe.ARM_CANDIDATE, fe.FIRST_RUN, score,
            {"verdict": "lift_over_base", "lift_over_base": 0.5, "lift_over_incumbent": None},
        )


# --------------------------------------------------------------------------
# Persistence — through supabase_rest only, never a second DB path
# --------------------------------------------------------------------------


def test_write_score_calls_the_existing_supabase_rest_upsert(sealed, monkeypatch):
    calls = []
    monkeypatch.setattr(
        fe.supabase_rest, "upsert",
        lambda table, rows, on_conflict, **kw: calls.append((table, rows, on_conflict, kw)) or rows,
    )
    base = fe.score_arm(SLUG, ITERATION, _predict_constant("no_action"), LABELS)
    score = fe.score_arm(SLUG, ITERATION, _predict_perfect(), LABELS)
    verdict = fe.compute_verdict(fe.FIRST_RUN, score, base_score=base)
    fe.write_score(SLUG, "candidate-v2-agent", fe.ARM_CANDIDATE, fe.FIRST_RUN, score, verdict)

    assert len(calls) == 1
    table, rows, on_conflict, kw = calls[0]
    assert table == fe.MODEL_EVALS_TABLE
    assert on_conflict == "model_id,eval_set_id,population_fingerprint"
    row = rows[0]
    assert row["org_slug"] == SLUG
    assert row["model_id"] == "candidate-v2-agent"
    assert row["arm"] == fe.ARM_CANDIDATE
    assert row["run_kind"] == fe.FIRST_RUN
    assert row["population_fingerprint"] == score["population_fingerprint"]
    assert row["score"] == score["balanced_agreement"]
    assert row["metrics"]["balanced_agreement"] == score["balanced_agreement"]
    assert row["n_items"] == score["n_heldout"]
    assert row["verdict"] == verdict["verdict"]
    assert row["lift_over_base"] == verdict["lift_over_base"]
    assert row["lift_over_incumbent"] is None
    assert row["scored_at"]  # non-empty; NOT NULL in the real schema
