#!/usr/bin/env python3
"""factory_eval — S8 scoring logic: grade one arm's predictions against the
sealed held-out set that `factory_heldout.py` (C3) produces.

## Why this exists

Today `stage_eval` in `factory.py` dispatches AgentSpec nodes for S8 and, absent
a live eval harness, resolves to `needs_self_dispatch` — it scores nothing. The
code that actually calls a deployed model and grades its output lives in
platform-alpha, a different repository this cloud environment deliberately does
not clone (no `FIREWORKS_API_KEY`, no paid inference, no GPU rental reachable
from here). So the cloud harness structurally cannot answer "is this model any
good", and `factory_model_evals` has zero rows.

This module does NOT close that gap. It is scoring LOGIC against fixtures, not a
live eval: given predictions and ground-truth labels for a set of items (however
they were produced — a real deployed-model call, or a test fixture), it scores
the held-out subset correctly, refuses to be fooled by a constant predictor, and
refuses to store a number that would be compared against real scores forever
with nothing marking it as infrastructure failure. Wiring a real model's output
into this module — the actual S8 dispatch — is out of scope here: that needs
the paid inference this environment is barred from making.

## The failure this guards against

On the Grand Steel held-out set, 56 of 71 accounts are `no_action`. A predictor
that always answers `no_action` — one that has learned nothing — scores 78.9%
plain agreement. Reporting that number as the headline metric would make a
model that answers nothing look like it mostly works. Only the ~15 discriminating
accounts (the minority class) tell you anything about whether the model
learned. `balanced_agreement()` below is the unweighted mean of majority-class
and minority-class accuracy, which is exactly 0.5 for any constant predictor —
by construction, not by tuning against this one fixture.

This already went wrong the other direction too: a frontier arm scored 0.0000 on
an expired API key. A 0.0 stored as a real score is indistinguishable from a
model that tried and failed on every item — it gets averaged into history
forever as if it were a measurement. `MIN_VALID_EMIT_RATE` exists so that "could
not answer" and "answered badly" are never the same number.

## What this module refuses, and why each refusal is load-bearing

  * **Below-floor valid-emit rate** — an arm that answered fewer than 80% of the
    held-out items did not score badly, it did not score. `score_arm` raises
    `EvalRefused` rather than returning a number.
  * **Cross-seal comparison** — `population_fingerprint` (from the held-out
    manifest) is the only thing that makes two scores comparable. Diffing scores
    from different seals reports a change in the EVAL SET as a change in the
    MODEL — the exact self-inflation `factory_heldout.py`'s ordering guarantee
    exists to prevent. `compare_scores` refuses whenever the fingerprints differ;
    `compute_verdict` turns that specific refusal into an `incomparable` verdict
    rather than silence, so a real attempt to compare across a reseal leaves an
    audit trail instead of nothing.
  * **A verdict resting on one account** — with ~15 discriminating items, one
    flipped account swings balanced_agreement by roughly 1/(2*15) ≈ 3.3%.
    `_minimum_meaningful_margin` derives that swing from the ACTUAL
    discriminating (minority-class) subset being scored, and `compute_verdict`
    refuses to call a lift/regression when the observed delta does not clear it.
  * **`run_kind` disagreeing with the evidence** — a first train has no
    incumbent to lose to; a retrain has no meaning judged only against the
    untrained base (matching-or-beating base but losing to the incumbent ships a
    product regression that LOOKS like progress on a base-relative chart, per
    the charter). `compute_verdict` refuses to guess which comparison was meant
    when the caller's `run_kind` and the supplied scores disagree.
  * **A row the DB's own CHECK constraints would reject** — `write_score`
    validates `arm`/`run_kind`/`verdict` against the closed vocabularies below
    before ever calling `supabase_rest.upsert`, so a vocabulary mismatch is a
    local `EvalRefused`, not a live PostgREST 400 the caller has to decode.

## `factory_model_evals` schema

Its migration lives on platform-alpha, which this repo doesn't clone — this
module cannot read it directly. The shape below matches the schema as
described in review on PR #2 (2026-09-14, queried directly against
production): the upsert target is the unique index
`uq_factory_model_evals_arm_on_set (model_id, eval_set_id,
population_fingerprint)`; `arm` is checked against `{candidate, incumbent,
base}`; `run_kind` against `{first-train, monthly-retrain}` (note the exact
spelling — NOT `factory_context.py`'s pipeline-facing `first_run`/`retrain`,
a different vocabulary for a different table, and every value this module
emits is validated against the vocabulary below before being written); and
`verdict` against `{improved, regressed, inconclusive, no_lift_over_base,
incomparable}`. That description has not been independently verified against
the migration file itself. `write_score` refuses to send anything outside
these vocabularies rather than trust that description blindly.

Dependencies: Python standard library + `requests` only (via `supabase_rest`),
matching the rest of this package (`requirements.txt`). Writes go through the
existing `supabase_rest.upsert` — this module opens no second DB path.
"""

import datetime
import logging
from collections import Counter

import factory_heldout
import supabase_rest

log = logging.getLogger(__name__)

# `factory_model_evals.run_kind`'s CHECK constraint vocabulary. Deliberately
# NOT the same strings as `factory_context.FIRST_RUN`/`RETRAIN` ("first_run"/
# "retrain") — that module's vocabulary describes the pipeline's own run
# classification; this one describes this DB column. A caller wiring the two
# together is responsible for translating between them.
FIRST_RUN = "first-train"
RETRAIN = "monthly-retrain"
RUN_KINDS = (FIRST_RUN, RETRAIN)

# `factory_model_evals.arm`'s CHECK constraint vocabulary.
ARM_CANDIDATE = "candidate"
ARM_INCUMBENT = "incumbent"
ARM_BASE = "base"
ARMS = (ARM_CANDIDATE, ARM_INCUMBENT, ARM_BASE)

# `factory_model_evals.verdict`'s CHECK constraint vocabulary.
VERDICT_IMPROVED = "improved"
VERDICT_REGRESSED = "regressed"
VERDICT_INCONCLUSIVE = "inconclusive"
VERDICT_NO_LIFT_OVER_BASE = "no_lift_over_base"
VERDICT_INCOMPARABLE = "incomparable"
VERDICTS = (
    VERDICT_IMPROVED, VERDICT_REGRESSED, VERDICT_INCONCLUSIVE,
    VERDICT_NO_LIFT_OVER_BASE, VERDICT_INCOMPARABLE,
)

# Below this fraction of held-out items actually answered, a score is not
# trustworthy enough to store — see module docstring, "expired API key".
MIN_VALID_EMIT_RATE = 0.80

MODEL_EVALS_TABLE = "factory_model_evals"


class EvalError(RuntimeError):
    """Base class for factory_eval failures."""


class EvalRefused(EvalError):
    """Raised whenever scoring or verdict logic would otherwise have to guess —
    a low valid-emit rate, an unsealed/undersized held-out set, a `run_kind`
    the supplied evidence does not support, or a value the DB's own CHECK
    constraints would reject.

    Deliberately a hard error, same discipline as `factory_heldout.HeldoutError`:
    every one of these paths would otherwise produce a *confident wrong number*,
    which costs more than refusing to answer. (Cross-seal comparison is the one
    exception that does NOT surface this way from `compute_verdict` — see
    `compare_scores` and the `incomparable` verdict.)
    """


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def balanced_agreement(labels, predictions):
    """Unweighted mean of per-class accuracy over the classes present in
    `labels`. A missing/None prediction counts as incorrect for its item's
    class — it is still included in that class's denominator, so silently
    skipping unanswered items can never inflate the score.

    `labels`: dict item_id -> true label, for the set being scored.
    `predictions`: dict item_id -> predicted label (missing or None allowed).

    Returns `(score, per_class_accuracy)`. For exactly two classes this is the
    textbook "majority-class and minority-class accuracy, averaged" — a
    predictor that always answers the majority label scores exactly 0.5,
    regardless of the majority/minority split, because its majority-class
    accuracy is 1.0 and its minority-class accuracy is 0.0.
    """
    by_class = {}
    for item_id, true_label in labels.items():
        by_class.setdefault(true_label, []).append(predictions.get(item_id) == true_label)

    per_class_accuracy = {
        cls: (sum(hits) / len(hits)) for cls, hits in by_class.items()
    }
    if not per_class_accuracy:
        raise EvalRefused("balanced_agreement: no labelled items to score")
    score = sum(per_class_accuracy.values()) / len(per_class_accuracy)
    return score, per_class_accuracy


def _minimum_meaningful_margin(labels):
    """Derive the smallest score delta that could NOT be explained by a single
    account flipping in the smallest (most discriminating) class.

    Flipping one item in a class of size n changes that class's accuracy by
    1/n; `balanced_agreement` averages classes unweighted, so a single flip in
    the smallest class moves the overall score by 1/(2n). Below that, an
    observed delta is indistinguishable from noise in one account and must not
    drive a verdict — see module docstring.
    """
    counts = Counter(labels.values())
    if len(counts) < 2:
        raise EvalRefused(
            f"cannot derive a margin floor from {len(counts)} class(es) — balanced_agreement "
            "needs at least a majority and a minority class to mean anything"
        )
    smallest = min(counts.values())
    if smallest < 1:
        raise EvalRefused("cannot derive a margin floor from an empty class")
    return 1.0 / (2 * smallest)


def score_arm(slug, iteration, predictions, labels, stream="per-account"):
    """Score one arm's `predictions` against the sealed held-out set for
    `(slug, iteration)`, restricted to items with a label.

    `predictions`/`labels`: dict item_id -> label. Only items BOTH labelled AND
    a member of the sealed held-out manifest (`factory_heldout.is_held_out`)
    are scored — this is the only membership test used; the manifest itself is
    never reimplemented here.

    Raises `EvalRefused`:
      * if no held-out manifest is sealed for `(slug, iteration)`,
      * if fewer than `factory_heldout.MIN_HELDOUT_ITEMS` labelled items land in
        the held-out set (same floor the anchor itself enforces, for the same
        reason — a smaller set passes or fails on one item),
      * if the valid-emit rate over those items is below `MIN_VALID_EMIT_RATE`.

    Returns a dict on success (`status == "scored"`) carrying everything a
    verdict or a DB row needs: `population_fingerprint`, `balanced_agreement`,
    `margin_floor`, `class_accuracy`, and the emit-rate accounting.
    """
    manifest = factory_heldout.load_manifest(slug, iteration, stream=stream)
    if not manifest:
        raise EvalRefused(
            f"no sealed held-out manifest for {slug} iteration {iteration} — refusing to score "
            "without one; see factory_heldout.seal()"
        )

    heldout_labels = {
        item_id: label
        for item_id, label in labels.items()
        if factory_heldout.is_held_out(slug, iteration, item_id, stream=stream)
    }
    if len(heldout_labels) < factory_heldout.MIN_HELDOUT_ITEMS:
        raise EvalRefused(
            f"only {len(heldout_labels)} labelled item(s) fall inside the sealed held-out set for "
            f"{slug} iteration {iteration} (minimum {factory_heldout.MIN_HELDOUT_ITEMS}) — too few "
            "to measure anything"
        )

    total = len(heldout_labels)
    answered = sum(1 for item_id in heldout_labels if predictions.get(item_id) is not None)
    valid_emit_rate = answered / total

    if valid_emit_rate < MIN_VALID_EMIT_RATE:
        raise EvalRefused(
            f"valid-emit rate {valid_emit_rate:.1%} for {slug} iteration {iteration} is below the "
            f"{MIN_VALID_EMIT_RATE:.0%} floor ({answered}/{total} held-out items answered) — an arm "
            "that could not answer did not score badly, it did not score. Refusing to store a score."
        )

    score, per_class_accuracy = balanced_agreement(heldout_labels, predictions)
    margin = _minimum_meaningful_margin(heldout_labels)

    return {
        "status": "scored",
        "org_slug": slug,
        "iteration": iteration,
        "population_fingerprint": manifest["population_fingerprint"],
        "n_heldout": total,
        "n_answered": answered,
        "valid_emit_rate": valid_emit_rate,
        "balanced_agreement": score,
        "class_accuracy": per_class_accuracy,
        "margin_floor": margin,
    }


# ---------------------------------------------------------------------------
# Comparability + verdicts
# ---------------------------------------------------------------------------


def compare_scores(reference, candidate):
    """`candidate`'s balanced_agreement minus `reference`'s — ONLY when both were
    scored against the same sealed population.

    Raises `EvalRefused` when `population_fingerprint`s differ: scores from
    different seals are incomparable, and differencing them anyway would report
    a change in the eval set as a change in the model — the exact error sealing
    exists to prevent.
    """
    ref_fp = reference.get("population_fingerprint")
    cand_fp = candidate.get("population_fingerprint")
    if ref_fp != cand_fp:
        raise EvalRefused(
            f"refusing to compare scores from different seals ({ref_fp!r} vs {cand_fp!r}) — "
            "comparability is keyed on population_fingerprint; these are incomparable"
        )
    return candidate["balanced_agreement"] - reference["balanced_agreement"]


def compute_verdict(run_kind, score, base_score=None, incumbent_score=None):
    """Decide the verdict for `score` (this arm's result) given `run_kind`.

    Returns a dict: `{"verdict": ..., "lift_over_base": float|None,
    "lift_over_incumbent": float|None}` — the two deltas are `None` whenever
    the corresponding comparison score was not supplied, or was incomparable.

    `FIRST_RUN`: judged against `base_score` (the untrained base). Merely
    matching the base is `no_lift_over_base` — a null result, not a pass.
    Supplying an `incumbent_score` for a first run means `run_kind` disagrees
    with the evidence (a first train has no incumbent), so this refuses rather
    than guessing which comparison was intended.

    `RETRAIN`: judged against `incumbent_score` (the currently-shipped model).
    Beating the base but losing to the incumbent is `regressed` — shipping it
    would make the product worse while looking like progress on a
    base-relative chart. A `RETRAIN` with no `incumbent_score` supplied is the
    same evidence/run_kind mismatch as above, refused the same way. If
    `base_score` is also supplied its delta is recorded for context but never
    drives the verdict.

    Both branches require a delta larger than `score["margin_floor"]` before
    calling a direction at all, so no verdict rests on a single account.

    A `base_score`/`incumbent_score` from a different seal does NOT raise here
    — `compare_scores`'s refusal is caught and turned into the `incomparable`
    verdict, so a genuine attempt to compare across a reseal is recorded
    rather than silently dropped. A `run_kind`/evidence mismatch is a caller
    bug, not a seal mismatch, and still raises `EvalRefused`.
    """
    if run_kind not in RUN_KINDS:
        raise EvalRefused(f"unknown run_kind {run_kind!r} — refusing to guess a verdict")

    margin = score["margin_floor"]

    if run_kind == FIRST_RUN:
        if incumbent_score is not None:
            raise EvalRefused(
                f"run_kind={FIRST_RUN!r} but an incumbent_score was supplied — a first train has no "
                "incumbent to compare against; run_kind disagrees with the evidence"
            )
        if base_score is None:
            raise EvalRefused(f"run_kind={FIRST_RUN!r} requires base_score to judge lift over the untrained base")
        try:
            delta = compare_scores(base_score, score)
        except EvalRefused:
            return {"verdict": VERDICT_INCOMPARABLE, "lift_over_base": None, "lift_over_incumbent": None}
        if delta > margin:
            verdict = VERDICT_IMPROVED
        elif delta < -margin:
            verdict = VERDICT_REGRESSED
        else:
            verdict = VERDICT_NO_LIFT_OVER_BASE
        return {"verdict": verdict, "lift_over_base": delta, "lift_over_incumbent": None}

    # RETRAIN
    if incumbent_score is None:
        raise EvalRefused(
            f"run_kind={RETRAIN!r} requires incumbent_score — there is no incumbent to retrain "
            "against; run_kind disagrees with the evidence"
        )
    try:
        delta = compare_scores(incumbent_score, score)
    except EvalRefused:
        return {"verdict": VERDICT_INCOMPARABLE, "lift_over_base": None, "lift_over_incumbent": None}
    if delta > margin:
        verdict = VERDICT_IMPROVED
    elif delta < -margin:
        verdict = VERDICT_REGRESSED
    else:
        verdict = VERDICT_INCONCLUSIVE

    lift_over_base = None
    if base_score is not None:
        try:
            lift_over_base = compare_scores(base_score, score)
        except EvalRefused:
            lift_over_base = None  # informational only; never blocks the incumbent-driven verdict above

    return {"verdict": verdict, "lift_over_base": lift_over_base, "lift_over_incumbent": delta}


# ---------------------------------------------------------------------------
# Persistence — through the existing supabase_rest transport only
# ---------------------------------------------------------------------------


def _utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def write_score(
    slug, model_id, arm, run_kind, score, verdict_result, *,
    run_id=None, base_model=None, compared_to_model_id=None, notes=None,
    scored_at=None, url=None, service_role_key=None,
):
    """Persist one scored arm to `factory_model_evals` via `supabase_rest.upsert`.

    `verdict_result` is the dict `compute_verdict` returns (or, for a caller
    that computed a verdict some other way, any dict/string with the same
    `verdict`/`lift_over_base`/`lift_over_incumbent` shape).

    Refuses (raises `EvalRefused`, writes nothing) when:
      * `score["status"] != "scored"` — a refusal from `score_arm` must never
        reach the DB layer, by construction, not by the caller remembering to
        check first;
      * `arm` is not one of `ARMS`, `run_kind` is not one of `RUN_KINDS`, or
        the verdict is not one of `VERDICTS` — the DB's own CHECK constraints
        would reject the row anyway; refusing locally turns that into a clear
        message instead of a live PostgREST 400.
    """
    if score.get("status") != "scored":
        raise EvalRefused(
            f"refusing to write a non-scored result (status={score.get('status')!r}) to "
            f"{MODEL_EVALS_TABLE} — a refused score is not a row"
        )
    if arm not in ARMS:
        raise EvalRefused(f"arm {arm!r} is not one of {ARMS} — the DB's CHECK constraint would reject this row")
    if run_kind not in RUN_KINDS:
        raise EvalRefused(
            f"run_kind {run_kind!r} is not one of {RUN_KINDS} — the DB's CHECK constraint would reject this row"
        )

    verdict = verdict_result["verdict"] if isinstance(verdict_result, dict) else verdict_result
    lift_over_base = verdict_result.get("lift_over_base") if isinstance(verdict_result, dict) else None
    lift_over_incumbent = verdict_result.get("lift_over_incumbent") if isinstance(verdict_result, dict) else None
    if verdict not in VERDICTS:
        raise EvalRefused(
            f"verdict {verdict!r} is not one of {VERDICTS} — the DB's CHECK constraint would reject this row"
        )

    row = {
        "org_slug": slug,
        "model_id": model_id,
        "arm": arm,
        # No separate eval-set registry exists yet; derived deterministically
        # from the sealed manifest's own identity so re-scoring the same
        # (slug, iteration) is the same eval_set_id every time.
        "eval_set_id": f"{slug}:heldout:{score['iteration']}",
        "population_fingerprint": score["population_fingerprint"],
        "n_items": score["n_heldout"],
        "metrics": {
            "balanced_agreement": score["balanced_agreement"],
            "class_accuracy": score["class_accuracy"],
            "margin_floor": score["margin_floor"],
            "valid_emit_rate": score["valid_emit_rate"],
            "n_answered": score["n_answered"],
        },
        "scored_at": scored_at or _utc_now_iso(),
        "run_id": run_id,
        "base_model": base_model,
        "heldout_iteration": score["iteration"],
        "run_kind": run_kind,
        "score": score["balanced_agreement"],
        "verdict": verdict,
        "compared_to_model_id": compared_to_model_id,
        "lift_over_base": lift_over_base,
        "lift_over_incumbent": lift_over_incumbent,
        "notes": notes,
    }
    return supabase_rest.upsert(
        MODEL_EVALS_TABLE, [row], on_conflict="model_id,eval_set_id,population_fingerprint",
        url=url, service_role_key=service_role_key,
    )
