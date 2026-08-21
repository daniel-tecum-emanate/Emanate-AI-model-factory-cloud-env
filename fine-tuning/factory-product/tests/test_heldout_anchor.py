"""C3 (factory-graph-pr2, 2026-07-28): the Held-Out Eval anchor.

`CHARTER.md` §5 calls this the #1 anchor; `PLAN.md` Phase 2 recorded that it had
zero infrastructure. What it protects against is specific: if the set used to
grade a fine-tune overlaps the set used to build its training corpus, the model
looks better than it is by exactly the amount it memorised. Nothing errors. A
number is produced, the number is good, and it is wrong in the direction that
gets a model shipped.

Four properties, each with the failure it prevents:

1. **Ordering.** Sampling after curation cannot hold anything out retroactively —
   the curator has already seen the population. `seal` refuses once S4 has run,
   and that refusal IS the anchor, not the file it writes.
2. **Read restriction, enforced in the dispatcher.** A curation or training node
   that can read the manifest makes the anchor decorative. Enforced where nodes
   are launched, not requested in prompt text (PRRules rule 11).
3. **No customer content.** Item ids here are frequently human-readable
   (`acct-<name>-fact-N`), so a manifest of raw ids is a manifest of account
   names.
4. **Honest separation reporting.** Three of the four judge/generator dimensions
   are enforceable today; the credential one is not. Reporting it as satisfied
   would be worse than leaving it unimplemented, because a false "verified" is
   what stops anyone revisiting it (PRRules rule 12).
"""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory_heldout as fh  # noqa: E402
import factory_node_run as fnr  # noqa: E402

SLUG = "qa-dryrun-synth"

# Deliberately human-readable, the way real corpus item ids are — so property 3
# is tested against the realistic hazard rather than against opaque uuids.
POPULATION = [f"acct-Acme Steel Co-fact-{n}" for n in range(40)]


@pytest.fixture
def run_root(tmp_path, monkeypatch):
    monkeypatch.setattr(fh, "HERE", tmp_path)
    return tmp_path


def _mark_curation_started(root, slug=SLUG):
    directory = root / "runs" / slug
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "04-build-report.json").write_text("{}")


# --------------------------------------------------------------------------
# 1. Ordering — the anchor itself
# --------------------------------------------------------------------------


def test_seal_writes_a_manifest_before_curation(run_root):
    manifest = fh.seal(SLUG, POPULATION, iteration=1, seed=41)
    assert manifest["sealed"] is True
    assert manifest["population_size"] == 40
    assert manifest["heldout_count"] == 8  # 20% of 40
    assert fh.manifest_path(SLUG, 1).is_file()


def test_seal_refuses_once_curation_has_started(run_root):
    """Property 1. The whole anchor is this refusal."""
    _mark_curation_started(run_root)
    with pytest.raises(fh.HeldoutError) as exc:
        fh.seal(SLUG, POPULATION, iteration=1)
    assert "not held out from anything" in str(exc.value)


def test_seal_is_idempotent_and_never_resamples(run_root):
    """Re-sampling an already-sealed iteration is indistinguishable from quietly
    moving the goal posts, so the existing manifest wins."""
    first = fh.seal(SLUG, POPULATION, iteration=1, seed=41)
    second = fh.seal(SLUG, POPULATION, iteration=1, seed=99999)
    assert first["heldout_fingerprints"] == second["heldout_fingerprints"]


def test_a_sealed_iteration_survives_curation_starting_afterwards(run_root):
    """Sealing first, then curating, is the correct order and must keep working."""
    fh.seal(SLUG, POPULATION, iteration=1, seed=41)
    _mark_curation_started(run_root)
    assert fh.load_manifest(SLUG, 1)["sealed"] is True


def test_selection_is_deterministic_for_a_seed(run_root, tmp_path):
    """This repo's corpora are SEED-deterministic; an anchor that cannot be
    reproduced from its recorded seed is not auditable."""
    a = fh.seal(SLUG, POPULATION, iteration=1, seed=41)
    fh.manifest_path(SLUG, 1).unlink()
    b = fh.seal(SLUG, POPULATION, iteration=1, seed=41)
    assert a["heldout_fingerprints"] == b["heldout_fingerprints"]


def test_different_seeds_select_differently(run_root):
    a = fh.seal(SLUG, POPULATION, iteration=1, seed=1)
    fh.manifest_path(SLUG, 1).unlink()
    b = fh.seal(SLUG, POPULATION, iteration=2, seed=2)
    assert a["heldout_fingerprints"] != b["heldout_fingerprints"]


# --------------------------------------------------------------------------
# Refusals that keep the anchor meaningful
# --------------------------------------------------------------------------


def test_an_empty_population_is_refused(run_root):
    with pytest.raises(fh.HeldoutError, match="nothing to hold out"):
        fh.seal(SLUG, [], iteration=1)


def test_a_population_too_small_to_measure_is_refused(run_root):
    """A held-out set of two passes or fails on a single item. Refused rather than
    accepted, because it would read as an anchor while providing no signal."""
    with pytest.raises(fh.HeldoutError, match="not a measurement"):
        fh.seal(SLUG, ["a", "b", "c"], iteration=1)


def test_duplicates_are_deduplicated_not_counted_twice(run_root, caplog):
    """A duplicate would inflate the population count and could let the same item
    be both held out and trained on."""
    manifest = fh.seal(SLUG, POPULATION + POPULATION, iteration=1, seed=41)
    assert manifest["population_size"] == 40
    assert "duplicate" in caplog.text


def test_a_corrupt_manifest_raises_rather_than_reading_as_absent(run_root):
    """"No anchor yet" and "the anchor is broken" must not be the same answer —
    otherwise the next `seal` silently overwrites a damaged one."""
    path = fh.manifest_path(SLUG, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    with pytest.raises(fh.HeldoutError, match="unreadable"):
        fh.load_manifest(SLUG, 1)


# --------------------------------------------------------------------------
# 3. No customer content in the manifest
# --------------------------------------------------------------------------


def test_manifest_contains_no_raw_item_ids_or_account_names(run_root):
    """Property 3, against the realistic hazard: the fixture ids embed a company
    name, exactly as real corpus item ids do."""
    fh.seal(SLUG, POPULATION, iteration=1, seed=41)
    raw = fh.manifest_path(SLUG, 1).read_text()
    assert "Acme Steel" not in raw
    assert "acct-" not in raw
    for item in POPULATION:
        assert item not in raw


def test_membership_is_still_testable_from_hashes_alone(run_root):
    """The hashing must not cost the manifest its function — a grader has to be
    able to ask "is this item held out?"."""
    manifest = fh.seal(SLUG, POPULATION, iteration=1, seed=41)
    held = [i for i in POPULATION if fh.is_held_out(SLUG, 1, i)]
    assert len(held) == manifest["heldout_count"]
    assert all(fh.item_fingerprint(i) in manifest["heldout_fingerprints"] for i in held)


def test_an_unsealed_iteration_holds_nothing_out(run_root):
    """False, not an exception and not True. Claiming membership without a manifest
    would be the same self-inflation by another route."""
    assert fh.is_held_out(SLUG, 1, POPULATION[0]) is False


# --------------------------------------------------------------------------
# Population drift
# --------------------------------------------------------------------------


def test_population_drift_is_detected(run_root):
    """A held-out set only means something relative to the population it split. If
    curation added or dropped items, the eval measures a different thing than it
    claims to."""
    fh.seal(SLUG, POPULATION, iteration=1, seed=41)
    assert fh.population_has_drifted(SLUG, 1, POPULATION) is False
    assert fh.population_has_drifted(SLUG, 1, POPULATION + ["acct-New Co-fact-1"]) is True
    assert fh.population_has_drifted(SLUG, 1, POPULATION[:-1]) is True


def test_population_fingerprint_ignores_enumeration_order(run_root):
    """Order is an artifact of however the population was listed, not a property
    of it — otherwise a reordered corpus would look like drift."""
    assert fh.population_fingerprint(POPULATION) == fh.population_fingerprint(list(reversed(POPULATION)))


# --------------------------------------------------------------------------
# 2. Read restriction, enforced in the dispatcher
# --------------------------------------------------------------------------


@pytest.mark.parametrize("stage", ("S4", "S7"))
def test_curation_and_training_stages_may_not_read_the_heldout_set(run_root, stage):
    assert fh.may_read_heldout(stage) is False
    with pytest.raises(fh.HeldoutError, match="held-out evaluation material"):
        fh.assert_not_visible(stage, SLUG, [f"runs/{SLUG}/heldout/01-manifest.json"])


def test_the_grading_stage_may_read_it():
    assert fh.may_read_heldout("S8") is True
    fh.assert_not_visible("S8", SLUG, [f"runs/{SLUG}/heldout/01-manifest.json"])


def test_an_unknown_stage_is_denied_by_default(run_root):
    """Allow-list, not deny-list. A stage added later must not inherit read access
    to the held-out set just because nobody remembered to exclude it."""
    assert fh.may_read_heldout("S99") is False
    with pytest.raises(fh.HeldoutError):
        fh.assert_not_visible("S99", SLUG, [f"runs/{SLUG}/heldout/01-manifest.json"])


def test_ordinary_paths_are_unaffected(run_root):
    fh.assert_not_visible("S4", SLUG, [f"runs/{SLUG}/04-build-report.json", "company-brain/x.md"])


def test_the_dispatcher_refuses_a_curation_node_that_would_see_it(run_root, monkeypatch):
    """Property 2 at the real enforcement point. Deliberately propagates rather
    than degrading to ok=False: every other failure here returns data so a broken
    dispatch cannot kill a stage, but continuing past a violated anchor yields a
    confident wrong number instead of an error."""
    monkeypatch.setattr(fnr, "load_spec", lambda slug: {
        "slug": "corpus-planner", "role_family": "builder", "mission": "m",
        "read_first": [f"runs/{SLUG}/heldout/01-manifest.json"],
    })
    with pytest.raises(fh.HeldoutError):
        fnr.dispatch_node("corpus-planner", "S4", None, "g", SLUG, dry_run=True)


def test_the_dispatcher_refuses_via_the_context_pack_too(run_root, monkeypatch):
    """The manifest could arrive through the context pack rather than `read_first`,
    so both inputs are checked."""
    monkeypatch.setattr(fnr, "load_spec", lambda slug: {
        "slug": "corpus-planner", "role_family": "builder", "mission": "m", "read_first": [],
    })
    with pytest.raises(fh.HeldoutError):
        fnr.dispatch_node("corpus-planner", "S4", None, "g", SLUG, dry_run=True,
                          context_pack=[f"runs/{SLUG}/heldout/01-manifest.json"])


def test_a_clean_curation_node_still_dispatches(run_root, monkeypatch):
    monkeypatch.setattr(fnr, "load_spec", lambda slug: {
        "slug": "corpus-planner", "role_family": "builder", "mission": "m",
        "read_first": ["company-brain/3-execution/V5-ARCHITECTURE.md"],
    })
    result = fnr.dispatch_node("corpus-planner", "S4", None, "g", SLUG, dry_run=True)
    assert result["ok"] is True and result["dispatched"] is False


# --------------------------------------------------------------------------
# 4. Judge/generator separation — enforced where possible, honest where not
# --------------------------------------------------------------------------


def test_curation_and_grading_specs_are_disjoint():
    """The charter's §5.3 check, mechanised. An agent that both curated the corpus
    and grades the result is the single failure this anchor exists to prevent."""
    report = fh.separation_report()
    assert report["agent_identity"]["status"] == "enforced"
    assert report["agent_identity"]["overlap"] == []
    assert report["agent_identity"]["curation_specs"]
    assert report["agent_identity"]["grading_specs"]


def test_the_separation_check_would_actually_catch_an_overlap(monkeypatch):
    """A check that cannot fail is not a check. This proves the assertion above
    has teeth by introducing the violation it is meant to catch."""
    monkeypatch.setitem(fnr.STAGE_CHAINS, "S8", ("corpus-planner", "grader-auditor"))
    report = fh.separation_report()
    assert report["agent_identity"]["status"] == "VIOLATED"
    assert report["agent_identity"]["overlap"] == ["corpus-planner"]
    assert fh.separation_holds() is False


def test_the_credential_dimension_is_reported_as_not_separable():
    """Property 4, and the reason this test exists at all: `tier2_run.sh` invokes
    the local `claude` binary with no key argument, so curator and grader share the
    ambient credential. The research flagged this as a necessary mitigation, so
    reporting it as satisfied would stop anyone from ever revisiting it."""
    report = fh.separation_report()
    assert report["credential"]["status"] == "NOT-SEPARABLE"
    assert "ambient credential" in report["credential"]["detail"]


def test_separation_holds_ignores_the_unenforceable_dimension():
    """Including `credential` would make this permanently False, and a red light
    that is always red stops being a signal."""
    assert fh.separation_holds() is True


def test_all_four_dimensions_are_reported():
    """None of them may be quietly omitted — an absent dimension reads as a
    non-issue."""
    assert set(fh.separation_report()) == {"agent_identity", "prompt", "model_instance", "credential"}
