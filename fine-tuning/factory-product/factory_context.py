"""B4 (factory-graph-pr1, 2026-07-27): retrain context continuity.

`automation-graph/RETRAIN-AND-META-LEARNING.md` §1's problem, stated plainly: a
retrain re-enters the pipeline at S4 and **overwrites the same
`runs/<org>/NN-*-report.json` paths as the run before it.** So by the time
iteration 2's corpus planner starts work, iteration 1's recipe, hyperparameters,
and — most valuable of all — the human's reason for the last ship decision have
been destroyed. The system's own memory of what it already tried is deleted
precisely when it becomes useful.

Four pieces, all deterministic and $0:

1. **Node 0, the onboarding router** — is this a first run or a retrain? A thin
   wrapper over the terminal-status logic `get_or_create_run_id` already owns,
   NOT a second implementation. There must be exactly one definition of "the
   previous run is over" in this codebase.
2. **The iteration digest** — a small, bounded snapshot written at S9 and
   refreshed at S11, i.e. *before* the next iteration can overwrite the reports
   it is drawn from. Recipe, hyperparameters, and ship rationale only.
3. **`context_sources`** — run-kind-conditional reading on AgentSpecs, so a
   retrain's packager pulls the predecessor's material and a first run is not
   handed dangling pointers to files that do not exist.
4. **`factory_lessons.source_run_id`** — a column that has existed with no
   writer, so "which run taught us this?" has been unanswerable.

## What the digest deliberately does NOT contain

No corpus examples, no dossier text, no account names — nothing derived from
customer data. It carries the *shape* of what was tried (recipe, doses, caps,
seed, hyperparameters) and the *decision* about it, which is what a successor
needs to avoid repeating a mistake. Keeping customer content out is not a
side-effect of the field selection; it is the reason for it, and
`tests/test_retrain_context.py` asserts it against a PII-laden fixture.

## Known limitation, recorded rather than hidden (decision D5)

Digests live under `runs/`, which is gitignored. They therefore do not survive a
machine change, and a retrain run on a different machine silently gets
`first_run` context. That is a real durability gap. It is accepted for PR 1
because the alternative — committing per-run artifacts — contradicts how every
other run output is handled, and the honest fix is the `factory_runs` DB
projection in PR 2. Written down here so the next person meets it as a known
limitation rather than a surprise.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
# Resolved independently of HERE, not derived from it. Tests relocate the run
# tree by monkeypatching HERE (the same lever factory_sync uses), and deriving
# the repo root from a value that moves would make "where does this run's
# output go" and "where does this repo's canon live" the same question. They
# are not.
REPO = Path(__file__).resolve().parent.parent.parent

FIRST_RUN = "first_run"
RETRAIN = "retrain"

# Tokens a spec's `context_sources` may reference. A closed vocabulary on
# purpose: an unresolvable token in a prompt is worse than a missing section,
# because the agent reads a literal `$predecessor_digest` and treats it as a
# filename that ought to exist.
CONTEXT_TOKENS = ("$predecessor_digest", "$predecessor_ship_gate", "$org_mixture_history")


def _run_dir(slug, stream="per-account"):
    path = HERE / "runs" / slug
    return path if stream == "per-account" else path / stream


def iterations_dir(slug, stream="per-account"):
    return _run_dir(slug, stream=stream) / "iterations"


def classify_run_kind(slug, prior_status=None, state=None, stream="per-account"):
    """Node 0. Return `first_run` or `retrain`.

    `retrain` requires a prior run that is **actually over**. A prior run still
    in flight (`running`, `blocked_on_gate`) is not a predecessor — it is *this
    run*, mid-pipeline, and treating it as a predecessor would have a run
    inherit context from itself and mint lineage against its own id.

    Terminal-ness is read from `factory_sync.TERMINAL_RUN_STATUSES`, the same
    set `get_or_create_run_id` uses, so the two can never disagree about what
    "finished" means. Notably that set includes `no_go` — which only became
    reachable in B1 of this same PR. Before that fix, the single case where
    inheriting predecessor context matters most (the last run was rejected, and
    the reason why is the most useful thing anyone could tell the next one)
    would have classified as `first_run`.
    """
    from factory_sync import TERMINAL_RUN_STATUSES

    if prior_status is not None:
        return RETRAIN if prior_status in TERMINAL_RUN_STATUSES else FIRST_RUN

    # No status supplied: fall back to the LOCAL record, and note what that
    # record already means. `get_or_create_run_id` bumps `iteration` and sets
    # `predecessor_run_id` if and ONLY if it observed the prior run in a
    # terminal status. So `iteration > 1` is not a proxy for "this is a
    # retrain" — it is the persisted result of that exact check, made earlier.
    # Deriving it again from a second source would create a way for the two to
    # disagree.
    state = state if state is not None else _load_state(slug, stream=stream)
    if state.get("predecessor_run_id") or (state.get("iteration") or 1) > 1:
        return RETRAIN
    return FIRST_RUN


def _load_state(slug, stream="per-account"):
    try:
        import factory_sync
        return factory_sync._load_run_state(slug, stream=stream)
    except Exception:  # noqa: BLE001
        return {}


def _report(slug, index, stage, stream="per-account"):
    path = _run_dir(slug, stream=stream) / f"{index:02d}-{stage}-report.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("factory_context: unreadable report %s: %s", path, exc)
        return {}


def build_iteration_digest(
    slug, iteration=None, run_id=None, predecessor_run_id=None,
    stream="per-account",
):
    """Assemble the digest from the on-disk stage reports.

    Reads the FILES, not the DB projection. That is deliberate (decision D6):
    files are ground truth for run state (D-PI-7), and going to disk means
    `factory_sync.SAFE_REPORT_KEYS` — the PII allow-list — does not need
    widening to let `work_order`/`launch_body`/`training_config` through. Not
    widening an allow-list is worth a few lines of file reading.

    Every field below was verified to exist in the real report shapes emitted by
    `stage_build`, `stage_train`, and `stage_ship`; nothing here is aspirational.
    """
    state = _load_state(slug, stream=stream)
    build = _report(slug, 4, "build", stream=stream)
    train = _report(slug, 7, "train", stream=stream)
    ship = _report(slug, 9, "ship", stream=stream)

    work_order = build.get("work_order") or {}
    launch_body = train.get("launch_body") or {}
    # training_config carries the full configured hyperparameter set (added
    # alongside launch_body — Fireworks' own REST payload has no loraAlpha/
    # targetModules field, a real vendor limitation, so launch_body never had
    # them to give). Reports written before this field existed fall back to
    # launch_body for the three keys it did carry.
    training_config = train.get("training_config") or {}

    digest = {
        "org_slug": slug,
        "iteration": iteration if iteration is not None else state.get("iteration"),
        "run_id": run_id or state.get("run_id"),
        "predecessor_run_id": predecessor_run_id or state.get("predecessor_run_id"),
        "corpus": {
            "recipe": work_order.get("recipe"),
            "slices": work_order.get("slices"),
            "style_caps": work_order.get("style_caps"),
            "seed": (work_order.get("org_inputs") or {}).get("seed"),
            "hard_rules": work_order.get("hard_rules"),
        },
        "training": {
            "base_model": launch_body.get("baseModel"),
            "lora_rank": training_config.get("lora_rank", launch_body.get("loraRank")),
            "lora_alpha": training_config.get("lora_alpha"),
            "epochs": training_config.get("epochs", launch_body.get("epochs")),
            "learning_rate": training_config.get("learning_rate", launch_body.get("learningRate")),
            "weight_decay": training_config.get("weight_decay"),
            "target_modules": training_config.get("target_modules"),
        },
        "ship": {
            "status": ship.get("status"),
            "recommendation": (ship.get("decision_request") or {}).get("recommendation"),
            "no_go_path": (ship.get("verdict_contract") or {}).get("no_go_path"),
            # B1's field. The human's actual reason for a NO-GO is the single
            # highest-signal thing a successor run can be told.
            "no_go_reason": (ship.get("no_go_reason") or {}).get("note"),
        },
    }
    if stream != "per-account":
        digest["model_stream"] = stream
    return digest


def digest_path(slug, iteration, stream="per-account"):
    return iterations_dir(slug, stream=stream) / f"{int(iteration or 0):02d}-digest.json"


def write_iteration_digest(slug, iteration=None, stream="per-account", **kwargs):
    """Persist the digest. Called at S9 and refreshed at S11 — both BEFORE the
    next iteration re-enters at S4 and overwrites the reports it is built from.
    That timing is the entire point; a digest written lazily at the start of the
    next run would be reading files the next run has already clobbered."""
    digest = build_iteration_digest(
        slug, iteration=iteration, stream=stream, **kwargs
    )
    path = digest_path(
        slug, digest.get("iteration") or 1, stream=stream
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(digest, indent=2))
    return path


def _load_predecessor_digest_from_disk(
    slug, current_iteration, stream="per-account"
):
    directory = iterations_dir(slug, stream=stream)
    if not directory.is_dir():
        return None
    candidates = sorted(directory.glob("*-digest.json"))
    if not candidates:
        return None
    for path in reversed(candidates):
        try:
            digest = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if (digest.get("iteration") or 0) < current_iteration:
            return digest
    return None


def load_predecessor_digest(
    slug, current_iteration=None, sync_url=None, db_reader=None,
    stream="per-account",
):
    """The most recent digest strictly BEFORE the current iteration, or None.

    Immediate predecessor only (decision D4). The digest records
    `predecessor_run_id`/`iteration`, so walking the full chain later needs no
    schema change and no rewrite here — just a loop.

    ## Precedence: files, then the DB projection, then nothing

    **Files win** (D-PI-7 / PRRules rule 5). The local digest is the record this
    run's own predecessor actually wrote; the DB row is a projection of it, and
    when both exist they describe the same thing.

    The DB is consulted only when there is no local file — which is exactly the
    case decision D5 flagged and C1b exists to fix: a retrain running on a
    different machine than its predecessor used to find nothing here and classify
    itself `first_run`, discarding the corpus recipe, the hyperparameters, and the
    human's stated reason for the last ship decision. It did not fail. It just
    quietly became a worse version of Daniel's manual process, which defeats the
    point of automating the stages at all.

    Returning None still means "no predecessor" and is still legitimate — a first
    run has none. What changed is that it now means that only after both sources
    have been asked.

    `db_reader` is injectable so this stays testable without a network or a
    Supabase double; `factory_sync` is imported lazily for the same reason the
    rest of this codebase does it — the sync layer is optional and must never be
    the reason a read path fails to import.
    """
    if current_iteration is None:
        current_iteration = (
            _load_state(slug, stream=stream).get("iteration") or 0
        ) + 1

    digest = _load_predecessor_digest_from_disk(
        slug, current_iteration, stream=stream
    )
    if digest is not None:
        return digest

    default_db_reader = db_reader is None
    if db_reader is None:
        try:
            import factory_sync
            db_reader = factory_sync.fetch_predecessor_digest
        except Exception as exc:  # noqa: BLE001 - the sync layer is optional
            log.warning(
                "factory_context: no local predecessor digest for org=%s and the sync layer is "
                "unavailable (%s) — proceeding as a first run, which loses predecessor context "
                "if this org has in fact run before (decision D5)",
                slug,
                exc,
            )
            return None

    try:
        reader_kwargs = {"url": sync_url}
        if default_db_reader or stream != "per-account":
            reader_kwargs["stream"] = stream
        digest = db_reader(slug, current_iteration, **reader_kwargs)
    except Exception as exc:  # noqa: BLE001 - a projection read must not fail a run
        log.warning(
            "factory_context: predecessor-digest DB fallback failed for org=%s — proceeding "
            "without predecessor context: %s",
            slug,
            exc,
        )
        return None

    if digest is not None:
        log.info(
            "factory_context: no local digest for org=%s; recovered iteration %s from the DB "
            "projection (the D5 machine-change path)",
            slug,
            digest.get("iteration"),
        )
    return digest


def resolve_token(token, slug, current_iteration=None, stream="per-account"):
    """Resolve one `context_sources` token to concrete reading material, or
    `None` when there is nothing real behind it.

    `None` matters: a first run must get the section OMITTED, not an empty
    placeholder. An agent handed "Predecessor digest: (none)" will reason about
    the absence; an agent handed nothing correctly proceeds as a first run.
    """
    if token == "$predecessor_digest":
        digest = load_predecessor_digest(
            slug, current_iteration, stream=stream
        )
        if not digest:
            return None
        return f"predecessor iteration {digest.get('iteration')} digest: " + json.dumps(digest)
    if token == "$predecessor_ship_gate":
        digest = load_predecessor_digest(
            slug, current_iteration, stream=stream
        )
        ship = (digest or {}).get("ship") or {}
        if not any(ship.values()):
            return None
        return "predecessor ship gate: " + json.dumps(ship)
    if token == "$org_mixture_history":
        path = REPO / "company-brain" / "3-execution" / "MIXTURE-HISTORY.md"
        return str(path.relative_to(REPO)) if path.is_file() else None
    return None


def effective_read_first(
    spec, run_kind, slug, current_iteration=None, stream="per-account"
):
    """`read_first` (always) + the resolved `context_sources[run_kind]`.

    `read_first` stays the baseline rather than being replaced, which is what
    makes `context_sources` backward-compatible: a spec without the new field
    behaves exactly as it did before this PR. Unresolvable tokens are dropped
    silently — see `resolve_token` for why an omitted section beats an empty one.
    """
    items = list(spec.get("read_first") or [])
    for entry in (spec.get("context_sources") or {}).get(run_kind, []) or []:
        if entry in CONTEXT_TOKENS:
            resolved = resolve_token(
                entry, slug, current_iteration, stream=stream
            )
            if resolved:
                items.append(resolved)
        else:
            items.append(entry)
    return items
