"""C3 (factory-graph-pr2, 2026-07-28): the Held-Out Eval anchor.

`CHARTER.md` §5 calls this the #1 anchor and `PLAN.md` Phase 2 records that it
had **zero infrastructure**. This module is that infrastructure.

## What the anchor is actually protecting against

If the set used to grade a fine-tune overlaps the set used to build its training
corpus, the model looks better than it is, by exactly the amount it memorised.
Nothing errors. The eval reports a number, the number is good, and the number is
wrong in the direction that gets a model shipped. That is the PostTrainBench
self-inflation failure mode the charter's anchors exist to prevent, and it is
why the ordering here is load-bearing rather than tidy:

    sample the held-out set  ->  THEN curate  ->  THEN train  ->  THEN grade
                                 (S4)             (S7)           (S8)

Sampling *after* curation cannot fix this retroactively. Once the curator has
seen the whole population, no subsequent partition of it is held out from
anything. So `seal` refuses to run once curation has started, and that refusal is
the anchor — not the file it writes.

## Why the manifest stores hashes and not ids

Item identifiers in this pipeline are frequently human-readable
(`acct-<name>-fact-N`), so a manifest of raw ids is a manifest of account names,
which is exactly what every other artifact here works to keep out of committed
and DB-projected state. It stores `sha256(item_id)[:16]` instead, plus the
algorithm and the count. Membership stays fully testable — a grader hashes its
candidate and looks it up — while the manifest carries no customer content. Same
discipline as the iteration digest: shape, not content.

It also records a `population_fingerprint`. If curation later adds or removes
items, that fingerprint stops matching, and a held-out set that no longer
partitions the corpus it was drawn from is detectably stale rather than quietly
meaningless.

## Judge/generator separation: three dimensions enforced, one recorded

`PLAN.md` Phase 1 asks to "confirm whichever agent grades Paired Eval does not
share a model instance/prompt/API key with whichever agent curated the training
data". Checked honestly, that is four properties, and only three of them are
achievable with today's dispatch:

  * **agent identity** — enforced. The S8 grading specs and the S4 curation specs
    must be disjoint sets.
  * **prompt** — enforced. A grading node is refused the curator's work order and
    context pack.
  * **model instance** — holds by construction. Every node is a separate
    `claude -p` process via `tier2_run.sh`, so no conversation state, no cache and
    no context is shared between a curator and a grader.
  * **credential** — **NOT separable today, and this module says so rather than
    implying otherwise.** `tier2_run.sh` invokes the local `claude` binary with no
    key argument at all, so every dispatched node runs on the same ambient
    credential. Reporting this dimension as satisfied would be worse than leaving
    it unimplemented, because the research flagged it as a necessary mitigation
    and a false "verified" is what stops anyone from revisiting it.

`separation_report()` returns all four with their real status. `PRRules` rule 12.
"""

import hashlib
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent

HASH_ALGO = "sha256-16"
DEFAULT_HELDOUT_FRACTION = 0.20

# Minimum usable size. Below this a "held-out set" is a rounding error that will
# pass or fail on one item, so it is refused rather than silently accepted — a
# tiny anchor reads as an anchor while providing no signal.
MIN_HELDOUT_ITEMS = 5


class HeldoutError(RuntimeError):
    """Raised when the anchor's ordering or read restrictions would be violated.

    Deliberately a hard error. Every other failure path in this pipeline is
    fail-open because the alternative is blocking a run over a projection or a
    log. This one is not: continuing past a broken anchor produces a *confident
    wrong number*, which is more expensive than stopping.
    """


def _run_dir(slug, stream="per-account"):
    path = HERE / "runs" / slug
    return path if stream == "per-account" else path / stream


def heldout_dir(slug, stream="per-account"):
    return _run_dir(slug, stream=stream) / "heldout"


def manifest_path(slug, iteration, stream="per-account"):
    return heldout_dir(slug, stream=stream) / f"{int(iteration or 1):02d}-manifest.json"


def item_fingerprint(item_id):
    """Stable, truncated hash of one item id. 16 hex chars is ~64 bits — ample
    against accidental collision at corpus scale, and short enough to read."""
    return hashlib.sha256(str(item_id).encode("utf-8")).hexdigest()[:16]


def population_fingerprint(population):
    """Order-independent fingerprint of the whole population, so a later change to
    the corpus is detectable. Sorted before hashing because the population's
    order is an artifact of however it was enumerated, not a property of it."""
    joined = "\n".join(sorted(item_fingerprint(i) for i in population))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _deterministic_selection(population, count, seed):
    """Pick `count` items, deterministically for a given seed.

    Ranked by `hash(seed + item)` rather than `random.sample`, because this
    repo's corpora are SEED-deterministic and must be reproducible from the
    recorded seed alone — `random`'s sequence is an implementation detail of the
    interpreter, whereas a hash ranking is reproducible by anything that can
    compute sha256, including a reviewer checking the sample by hand.
    """
    ranked = sorted(population, key=lambda item: hashlib.sha256(f"{seed}:{item}".encode("utf-8")).hexdigest())
    return ranked[:count]


def curation_has_started(slug, stream="per-account"):
    """True once S4 has written a report for this org.

    This is the test that makes the anchor real: after S4 runs, the curator has
    seen the population, and nothing drawn from it afterwards is held out.
    """
    return (_run_dir(slug, stream=stream) / "04-build-report.json").exists()


def load_manifest(slug, iteration, stream="per-account"):
    path = manifest_path(slug, iteration, stream=stream)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        # Not swallowed to None: an unreadable manifest and an absent one mean
        # very different things, and treating a corrupt anchor as "no anchor yet"
        # would let the next `seal` overwrite it.
        raise HeldoutError(f"held-out manifest for {slug} iteration {iteration} is unreadable: {exc}")


def seal(
    slug, population, iteration=1, fraction=DEFAULT_HELDOUT_FRACTION,
    seed=0, force=False, stream="per-account",
):
    """Sample the held-out set and write its manifest. Refuses after curation.

    Returns the manifest dict. Idempotent for a given (slug, iteration): calling
    it again returns the existing manifest untouched, because re-sampling an
    already-sealed iteration is indistinguishable from quietly moving the goal
    posts.
    """
    existing = load_manifest(slug, iteration, stream=stream)
    if existing is not None:
        log.info("factory_heldout: iteration %s for %s is already sealed; keeping it", iteration, slug)
        return existing

    if curation_has_started(slug, stream=stream) and not force:
        raise HeldoutError(
            f"refusing to sample a held-out set for {slug}: S4 curation has already run "
            f"(runs/{slug}/04-build-report.json exists). A set drawn after the curator has seen "
            f"the population is not held out from anything, and sampling it now would produce an "
            f"anchor that reads as valid while measuring nothing. Start a new iteration instead."
        )

    population = [str(i) for i in population]
    if not population:
        raise HeldoutError(f"refusing to seal an empty population for {slug} — there is nothing to hold out")

    unique = sorted(set(population))
    if len(unique) != len(population):
        # Duplicates would inflate the population count and let the same item be
        # both held out and trained on.
        log.warning(
            "factory_heldout: population for %s had %s duplicate item id(s); de-duplicated before sampling",
            slug,
            len(population) - len(unique),
        )

    count = max(MIN_HELDOUT_ITEMS, int(round(len(unique) * fraction)))
    if count > len(unique) or len(unique) < MIN_HELDOUT_ITEMS:
        raise HeldoutError(
            f"population for {slug} has {len(unique)} item(s); cannot hold out a usable set "
            f"(minimum {MIN_HELDOUT_ITEMS}). A held-out set this small passes or fails on a single "
            f"item, which is not a measurement."
        )

    selected = _deterministic_selection(unique, count, seed)
    manifest = {
        "org_slug": slug,
        "iteration": int(iteration or 1),
        "sealed": True,
        "hash_algo": HASH_ALGO,
        "seed": seed,
        "fraction": fraction,
        "population_size": len(unique),
        "heldout_count": len(selected),
        "population_fingerprint": population_fingerprint(unique),
        # Hashes only. See the module docstring — item ids in this pipeline are
        # often human-readable and would carry account names into a committed
        # artifact.
        "heldout_fingerprints": sorted(item_fingerprint(i) for i in selected),
    }
    if stream != "per-account":
        manifest["model_stream"] = stream
    path = manifest_path(slug, iteration, stream=stream)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2))
    log.info(
        "factory_heldout: sealed %s of %s items for %s iteration %s",
        len(selected), len(unique), slug, iteration,
    )
    return manifest


def is_held_out(slug, iteration, item_id, stream="per-account"):
    """Membership test for a grader. False when no manifest exists — an unsealed
    iteration holds nothing out, and pretending otherwise would be the same
    self-inflation by a different route."""
    manifest = load_manifest(slug, iteration, stream=stream)
    if not manifest:
        return False
    return item_fingerprint(item_id) in set(manifest.get("heldout_fingerprints") or [])


def population_has_drifted(
    slug, iteration, population, stream="per-account"
):
    """True when the corpus no longer matches what was sealed.

    A held-out set only means something relative to the population it partitioned.
    If curation added or dropped items, the split no longer covers the corpus
    being trained on, and the eval is measuring a different thing than it claims.
    """
    manifest = load_manifest(slug, iteration, stream=stream)
    if not manifest:
        return False
    return manifest.get("population_fingerprint") != population_fingerprint([str(i) for i in population])


# ---------------------------------------------------------------------------
# Read restrictions — enforced in the dispatcher, not requested in a prompt
# ---------------------------------------------------------------------------

def _heldout_markers(slug, stream="per-account"):
    """Path fragments that indicate held-out material."""
    stream_part = "" if stream == "per-account" else f"{stream}/"
    return (
        str(heldout_dir(slug, stream=stream)),
        f"runs/{slug}/{stream_part}heldout",
        "heldout/",
    )


def may_read_heldout(stage_code):
    """Only grading stages may see the held-out set.

    Note this is an allow-list keyed on the GRADING stages, not a deny-list of
    curation stages. A deny-list would silently admit every stage added later,
    which is the wrong default for the one boundary whose failure is invisible.
    """
    import factory_node_run

    return stage_code in factory_node_run.GRADING_STAGES


def assert_not_visible(
    stage_code, slug, paths, spec_slug=None, stream="per-account"
):
    """Raise if a non-grading node would be handed held-out material.

    Called from the dispatcher for every node, so the restriction is a property of
    how nodes are launched rather than an instruction inside a prompt that an
    agent may or may not honour (PRRules rule 11).
    """
    if may_read_heldout(stage_code):
        return
    markers = _heldout_markers(slug, stream=stream)
    for path in paths or ():
        text = str(path)
        if any(marker in text for marker in markers):
            raise HeldoutError(
                f"refusing to dispatch {spec_slug or stage_code} ({stage_code}): it would be given "
                f"{text!r}, which is held-out evaluation material. Only {', '.join(GRADING_STAGES_HINT)} "
                f"may read it — a curation or training node that can see the held-out set makes the "
                f"anchor decorative and the eval self-congratulatory."
            )


GRADING_STAGES_HINT = ("S8",)


# ---------------------------------------------------------------------------
# Judge/generator separation
# ---------------------------------------------------------------------------

def separation_report():
    """The four dimensions of judge/generator separation, with honest statuses.

    Returned as data (and recorded on every S8 report) so a reviewer can see that
    the anchor held for a specific run, instead of trusting that it holds in
    general.
    """
    import factory_node_run as fnr

    curators = set()
    for stage in fnr.CURATION_STAGES:
        curators |= set(fnr.STAGE_CHAINS.get(stage, ()))
    graders = set()
    for stage in fnr.GRADING_STAGES:
        graders |= set(fnr.STAGE_CHAINS.get(stage, ()))
    overlap = sorted(curators & graders)

    return {
        "agent_identity": {
            "status": "enforced" if not overlap else "VIOLATED",
            "curation_specs": sorted(curators),
            "grading_specs": sorted(graders),
            "overlap": overlap,
            "detail": "S8's grading specs must be disjoint from S4's curation specs.",
        },
        "prompt": {
            "status": "enforced",
            "detail": (
                "A grading node is built from its own AgentSpec only; it is not handed the "
                "curator's work order or context pack."
            ),
        },
        "model_instance": {
            "status": "enforced-by-construction",
            "detail": (
                "Every node is a separate `claude -p` process launched by tier2_run.sh, so no "
                "conversation state, prompt cache or context is shared between curator and grader."
            ),
        },
        "credential": {
            "status": "NOT-SEPARABLE",
            "detail": (
                "tier2_run.sh invokes the local `claude` binary with no API-key argument, so every "
                "dispatched node runs on the same ambient credential. Recorded as an open "
                "limitation rather than reported as satisfied (PRRules rule 12). Closing it needs a "
                "per-role credential in the dispatch layer, which is not this PR's scope."
            ),
        },
    }


def separation_holds():
    """True when every dimension that CAN be enforced is enforced.

    `credential` is excluded deliberately — it is not enforceable today, so
    including it would make this permanently False and therefore ignored, which
    is how a red light stops being a signal.
    """
    report = separation_report()
    return all(
        report[dimension]["status"] in ("enforced", "enforced-by-construction")
        for dimension in ("agent_identity", "prompt", "model_instance")
    )
