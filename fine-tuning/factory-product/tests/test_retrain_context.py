"""B4 (factory-graph-pr1, 2026-07-27): retrain context continuity.

The failure being prevented: a retrain re-enters at S4 and overwrites the same
`NN-*-report.json` paths as the run before it, so by the time iteration 2 starts
planning, iteration 1's recipe, hyperparameters, and the human's ship rationale
no longer exist anywhere. The system deletes its own memory of what it tried,
exactly when that memory becomes useful.

Four things carry weight here:

1. **Router correctness at the edges.** A still-running prior run is not a
   predecessor — it is this run. Getting that wrong makes a run inherit context
   from itself.
2. **Schema back-compat.** All 33 existing specs must still validate after
   `context_sources` and `runtime` are added, or a schema change quietly breaks
   every agent.
3. **The digest carries no customer content.** It records the SHAPE of what was
   tried, never the material.
4. **`source_run_id` is first-mint-wins.** Provenance that rewrites itself on
   every canon re-sync is worse than none, because it still looks trustworthy.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory_context as fc  # noqa: E402
import factory_sync  # noqa: E402
import lessons_sync  # noqa: E402

SLUG = "pytest-b4-synthetic-org"
RUN_ID = "eeeeeeee-0000-0000-0000-000000000001"
PRED_RUN_ID = "eeeeeeee-0000-0000-0000-000000000000"


@pytest.fixture
def run_root(tmp_path, monkeypatch):
    """Point both modules' run trees at a tmp dir — these tests write real files."""
    monkeypatch.setattr(fc, "HERE", tmp_path)
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    (tmp_path / "runs" / SLUG).mkdir(parents=True)
    return tmp_path


# --------------------------------------------------------------------------
# Node 0: the onboarding router
# --------------------------------------------------------------------------


def test_no_prior_run_is_a_first_run(run_root):
    assert fc.classify_run_kind(SLUG) == fc.FIRST_RUN


@pytest.mark.parametrize("status", ["shipped", "failed", "abandoned"])
def test_a_finished_prior_run_is_a_retrain(status):
    assert fc.classify_run_kind(SLUG, prior_status=status) == fc.RETRAIN


def test_a_no_go_prior_run_is_a_retrain():
    """This arm only works because of B1. Before it, `no_go` was unreachable, so
    the single case where predecessor context matters most — the last run was
    rejected and the reason is the most useful thing to pass forward — would
    have classified as a first run.
    """
    assert fc.classify_run_kind(SLUG, prior_status="no_go") == fc.RETRAIN


@pytest.mark.parametrize("status", ["running", "blocked_on_gate"])
def test_a_still_running_prior_run_is_not_a_predecessor(status):
    """It is not a previous run — it is THIS run, mid-pipeline."""
    assert fc.classify_run_kind(SLUG, prior_status=status) == fc.FIRST_RUN


def test_the_router_reuses_the_one_definition_of_terminal():
    """There must be exactly one definition of "the previous run is over". If
    someone adds a status to TERMINAL_RUN_STATUSES, the router must follow
    automatically rather than needing a parallel edit."""
    for status in factory_sync.TERMINAL_RUN_STATUSES:
        assert fc.classify_run_kind(SLUG, prior_status=status) == fc.RETRAIN


def test_local_state_alone_can_classify_a_retrain(run_root):
    """`get_or_create_run_id` only bumps `iteration`/sets `predecessor_run_id`
    after observing a terminal prior run, so the local state IS the persisted
    result of that check — not a second, drift-prone heuristic.
    """
    state = run_root / "runs" / SLUG / ".sync_state.json"
    state.write_text(json.dumps({"run_id": RUN_ID, "iteration": 2, "predecessor_run_id": PRED_RUN_ID}))
    assert fc.classify_run_kind(SLUG) == fc.RETRAIN


def test_iteration_one_with_no_predecessor_is_a_first_run(run_root):
    state = run_root / "runs" / SLUG / ".sync_state.json"
    state.write_text(json.dumps({"run_id": RUN_ID, "iteration": 1}))
    assert fc.classify_run_kind(SLUG) == fc.FIRST_RUN


# --------------------------------------------------------------------------
# schema back-compat
# --------------------------------------------------------------------------


def test_all_existing_specs_still_validate_after_the_schema_change():
    """41 specs (33 original + 8 model-training swarm roles), none of which
    carry the new optional fields."""
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(FACTORY_DIR / "specs" / "validate_specs.py")],
        capture_output=True, text=True, cwd=str(FACTORY_DIR),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "41 spec files validate" in proc.stdout


def test_the_new_spec_fields_are_optional():
    schema = json.loads((FACTORY_DIR / "specs" / "schema.json").read_text())
    assert "context_sources" not in schema["required"]
    assert "runtime" not in schema["required"]
    assert "context_sources" in schema["properties"]


def test_schema_forbids_task_in_a_specs_tool_list():
    schema = json.loads((FACTORY_DIR / "specs" / "schema.json").read_text())
    tools = schema["properties"]["runtime"]["properties"]["allowed_tools"]
    assert tools["items"]["not"]["const"] == "Task"


def test_context_packager_declares_retrain_sources():
    import yaml
    spec = yaml.safe_load((FACTORY_DIR / "specs" / "context-packager.yaml").read_text())
    assert spec["context_sources"]["retrain"] == [
        "$predecessor_digest", "$predecessor_ship_gate", "$org_mixture_history",
    ]
    assert spec["context_sources"]["first_run"] == []


# --------------------------------------------------------------------------
# the digest
# --------------------------------------------------------------------------


def _seed_reports(root, *, pii=False):
    d = root / "runs" / SLUG
    (d / ".sync_state.json").write_text(
        json.dumps({"run_id": RUN_ID, "iteration": 2, "predecessor_run_id": PRED_RUN_ID})
    )
    (d / "04-build-report.json").write_text(json.dumps({
        "status": "todo",
        "work_order": {
            "recipe": "45% replay / 35% trajectories / 20% specialist",
            "slices": {"anchor": 200, "a1": 150},
            "style_caps": "semantic <=5-7%",
            "org_inputs": {
                "seed": 4242,
                "dossiers": "out/dossiers",
                # The PII-laden fields a naive "copy work_order" would drag in.
                "account_contact": "jane.doe@grandsteel.com",
                "account_name": "Grand Steel Fabrication LLC",
            },
            "hard_rules": ["SEED-deterministic", "regenerate-never-repair"],
            "corpus_examples": ["Q: who is Jane Doe at 415-555-0199? A: ..."] if pii else [],
        },
    }))
    (d / "07-train-report.json").write_text(json.dumps({
        "launch_body": {"baseModel": "qwen3-8b", "loraRank": 32, "epochs": 3,
                        "learningRate": 0.0001, "outputModel": "grand-steel-v1"},
        "training_config": {"lora_rank": 32, "lora_alpha": 16, "learning_rate": 0.0001,
                            "epochs": 3, "weight_decay": 0.01, "target_modules": "all-linear"},
    }))
    (d / "09-ship-report.json").write_text(json.dumps({
        "status": "no_go",
        "decision_request": {"recommendation": "pending-human-judgment"},
        "verdict_contract": {"no_go_path": "champion never overwritten"},
        "no_go_reason": {"note": "eval regression on safety anchors", "decided_by_email": "d@emanate.ai"},
    }))
    return d


def test_digest_contains_exactly_the_documented_keys(run_root):
    _seed_reports(run_root)
    digest = fc.build_iteration_digest(SLUG)
    assert set(digest) == {"org_slug", "iteration", "run_id", "predecessor_run_id",
                           "corpus", "training", "ship"}
    assert set(digest["corpus"]) == {"recipe", "slices", "style_caps", "seed", "hard_rules"}
    assert set(digest["training"]) == {"base_model", "lora_rank", "lora_alpha", "epochs",
                                       "learning_rate", "weight_decay", "target_modules"}
    assert set(digest["ship"]) == {"status", "recommendation", "no_go_path", "no_go_reason"}


def test_digest_carries_the_real_values(run_root):
    _seed_reports(run_root)
    digest = fc.build_iteration_digest(SLUG)
    assert digest["corpus"]["seed"] == 4242
    assert digest["training"]["lora_rank"] == 32
    assert digest["training"]["lora_alpha"] == 16
    assert digest["training"]["weight_decay"] == 0.01
    assert digest["training"]["target_modules"] == "all-linear"
    assert digest["ship"]["status"] == "no_go"
    assert digest["ship"]["no_go_reason"] == "eval regression on safety anchors"
    assert digest["predecessor_run_id"] == PRED_RUN_ID
    assert digest["iteration"] == 2


def test_digest_carries_no_customer_content(run_root):
    """Field selection is an allow-list by construction: the digest names the
    keys it wants rather than copying `work_order` wholesale. This asserts the
    consequence against a fixture deliberately stuffed with the material a
    wholesale copy would have leaked.
    """
    _seed_reports(run_root, pii=True)
    blob = json.dumps(fc.build_iteration_digest(SLUG))
    for leaked in ("jane.doe@grandsteel.com", "Grand Steel Fabrication LLC",
                   "415-555-0199", "corpus_examples", "dossiers"):
        assert leaked not in blob, f"{leaked!r} leaked into the iteration digest"


def test_digest_survives_missing_reports(run_root):
    """A NO-GO at S9 means S7 may never have run. The digest must degrade to
    nulls, not explode — a crash here would take down the ship stage."""
    (run_root / "runs" / SLUG / ".sync_state.json").write_text(json.dumps({"run_id": RUN_ID, "iteration": 1}))
    digest = fc.build_iteration_digest(SLUG)
    assert digest["training"]["base_model"] is None
    assert digest["corpus"]["recipe"] is None


def test_digest_falls_back_to_launch_body_for_a_train_report_predating_training_config(run_root):
    """A real report written before training_config existed has only
    launch_body's 3 overlapping keys (lora_rank, epochs, learning_rate) — those
    must still resolve. lora_alpha/weight_decay/target_modules were never in
    launch_body (a real Fireworks API limitation, not a bug), so they stay None
    rather than being guessed."""
    d = run_root / "runs" / SLUG
    (d / ".sync_state.json").write_text(json.dumps({"run_id": RUN_ID, "iteration": 1}))
    (d / "07-train-report.json").write_text(json.dumps({
        "launch_body": {"baseModel": "qwen3-8b", "loraRank": 32, "epochs": 3, "learningRate": 0.0001},
    }))
    digest = fc.build_iteration_digest(SLUG)
    assert digest["training"]["lora_rank"] == 32
    assert digest["training"]["epochs"] == 3
    assert digest["training"]["learning_rate"] == 0.0001
    assert digest["training"]["lora_alpha"] is None
    assert digest["training"]["weight_decay"] is None
    assert digest["training"]["target_modules"] is None


def test_digest_survives_a_corrupt_report(run_root):
    (run_root / "runs" / SLUG / "04-build-report.json").write_text("{not json")
    assert fc.build_iteration_digest(SLUG)["corpus"]["recipe"] is None


def test_write_iteration_digest_lands_where_the_next_run_looks(run_root):
    _seed_reports(run_root)
    path = fc.write_iteration_digest(SLUG)
    assert path == run_root / "runs" / SLUG / "iterations" / "02-digest.json"
    assert json.loads(path.read_text())["iteration"] == 2


def test_digest_is_written_before_the_next_iteration_can_clobber_the_reports(run_root):
    """The timing IS the feature. Write the digest, then simulate the retrain
    overwriting `04-build-report.json`, and prove the recipe is still
    recoverable from the digest.
    """
    _seed_reports(run_root)
    fc.write_iteration_digest(SLUG)
    (run_root / "runs" / SLUG / "04-build-report.json").write_text(json.dumps({"work_order": {"recipe": "NEW"}}))
    recovered = fc.load_predecessor_digest(SLUG, current_iteration=3)
    assert recovered["corpus"]["recipe"] == "45% replay / 35% trajectories / 20% specialist"


# --------------------------------------------------------------------------
# predecessor resolution + token expansion
# --------------------------------------------------------------------------


def test_no_predecessor_digest_on_a_first_run(run_root):
    assert fc.load_predecessor_digest(SLUG, current_iteration=1) is None


def test_predecessor_is_the_immediate_prior_iteration(run_root):
    """Decision D4: immediate predecessor only. The digest still records
    `predecessor_run_id`, so a later full chain-walk needs no schema change."""
    d = run_root / "runs" / SLUG / "iterations"
    d.mkdir(parents=True)
    for i in (1, 2, 3):
        (d / f"{i:02d}-digest.json").write_text(json.dumps({"iteration": i}))
    assert fc.load_predecessor_digest(SLUG, current_iteration=4)["iteration"] == 3
    assert fc.load_predecessor_digest(SLUG, current_iteration=3)["iteration"] == 2


def test_the_current_iterations_own_digest_is_not_its_predecessor(run_root):
    d = run_root / "runs" / SLUG / "iterations"
    d.mkdir(parents=True)
    (d / "02-digest.json").write_text(json.dumps({"iteration": 2}))
    assert fc.load_predecessor_digest(SLUG, current_iteration=2) is None


# --- cloud-environment guard, added 2026-08-24 (post-trim verification) ---
# Both tests below depend on canon files under `company-brain/3-execution/` that live in
# `emanate-tecum-workflow` and were never imported into this cloud environment.
# `$org_mixture_history` resolves to None when MIXTURE-HISTORY.md is absent (by design —
# `resolve_token` drops unresolvable tokens silently), and `build_lesson_rows()` parses
# PLAYBOOK-AND-LESSONS.md directly. Neither file is on the pipeline path: `factory.py`
# only calls `lessons_sync.attribute_lesson_to_run()`, which is a DB update. Guarded on
# real absence, so these run normally wherever the canon exists.
_CANON = fc.REPO / "company-brain" / "3-execution"
_needs_canon = pytest.mark.skipif(
    not (_CANON / "MIXTURE-HISTORY.md").is_file()
    or not (_CANON / "PLAYBOOK-AND-LESSONS.md").is_file(),
    reason="company-brain/3-execution canon files are not in this repo (they live in emanate-tecum-workflow)",
)


@_needs_canon
def test_all_three_tokens_resolve_for_a_retrain(run_root):
    import yaml
    _seed_reports(run_root)
    fc.write_iteration_digest(SLUG)
    spec = yaml.safe_load((FACTORY_DIR / "specs" / "context-packager.yaml").read_text())
    items = fc.effective_read_first(spec, fc.RETRAIN, SLUG, current_iteration=3)

    assert all(base in items for base in spec["read_first"]), "read_first must stay the baseline"
    joined = " ".join(items)
    assert "predecessor iteration 2 digest" in joined
    assert "predecessor ship gate" in joined
    assert "MIXTURE-HISTORY.md" in joined


def test_a_first_run_gets_no_predecessor_section_at_all(run_root):
    """Omitted, not empty. An agent handed "Predecessor: (none)" reasons about
    the absence; an agent handed nothing correctly proceeds as a first run."""
    import yaml
    spec = yaml.safe_load((FACTORY_DIR / "specs" / "context-packager.yaml").read_text())
    items = fc.effective_read_first(spec, fc.FIRST_RUN, SLUG, current_iteration=1)
    assert items == spec["read_first"]
    assert not any("predecessor iteration" in i for i in items)


def test_unresolvable_tokens_are_dropped_not_passed_through_literally(run_root):
    """A literal `$predecessor_digest` in a prompt is read by the agent as a
    filename it should go find. Worse than omitting the line."""
    spec = {"read_first": ["base"], "context_sources": {"retrain": ["$predecessor_digest"]}}
    items = fc.effective_read_first(spec, fc.RETRAIN, SLUG, current_iteration=5)
    assert items == ["base"]


def test_a_spec_without_context_sources_behaves_exactly_as_before():
    spec = {"read_first": ["a", "b"]}
    assert fc.effective_read_first(spec, fc.RETRAIN, SLUG) == ["a", "b"]
    assert fc.effective_read_first(spec, fc.FIRST_RUN, SLUG) == ["a", "b"]


def test_literal_non_token_entries_pass_through(run_root):
    spec = {"read_first": ["a"], "context_sources": {"retrain": ["some/real/file.md"]}}
    assert fc.effective_read_first(spec, fc.RETRAIN, SLUG) == ["a", "some/real/file.md"]


# --------------------------------------------------------------------------
# factory_lessons.source_run_id — first mint wins
# --------------------------------------------------------------------------


def test_source_run_id_is_written_when_null():
    with patch.object(lessons_sync, "select", return_value=[{"code": "L35", "source_run_id": None}]), \
         patch.object(lessons_sync, "update", return_value=[{"code": "L35"}]) as mock_update:
        assert lessons_sync.attribute_lesson_to_run("L35", RUN_ID) is True
    filters, values = mock_update.call_args[0][1], mock_update.call_args[0][2]
    assert values == {"source_run_id": RUN_ID}
    assert filters["source_run_id"] == "is.null", "the write must be guarded atomically, not just checked"


def test_source_run_id_is_never_overwritten():
    """The one that matters. A canon re-sync must not silently re-attribute the
    whole lesson history to whatever run happens to be open."""
    with patch.object(lessons_sync, "select",
                      return_value=[{"code": "L35", "source_run_id": PRED_RUN_ID}]), \
         patch.object(lessons_sync, "update") as mock_update:
        assert lessons_sync.attribute_lesson_to_run("L35", RUN_ID) is False
    mock_update.assert_not_called()


def test_attributing_an_unknown_lesson_is_a_no_op():
    with patch.object(lessons_sync, "select", return_value=[]), \
         patch.object(lessons_sync, "update") as mock_update:
        assert lessons_sync.attribute_lesson_to_run("NOPE", RUN_ID) is False
    mock_update.assert_not_called()


@pytest.mark.parametrize("code,run_id", [("", RUN_ID), ("L35", ""), (None, RUN_ID), ("L35", None)])
def test_attribution_requires_both_ids(code, run_id):
    with pytest.raises(ValueError):
        lessons_sync.attribute_lesson_to_run(code, run_id)


@_needs_canon
def test_the_canon_parse_is_untouched_by_b4():
    """Invariant 3: `source_run_id` is additive metadata, never a second writer
    of the canon fields. If B4 had altered the parse, these rows would differ.
    """
    rows = lessons_sync.build_lesson_rows()
    assert rows, "canon parse produced nothing — something is broken upstream"
    for row in rows:
        assert "source_run_id" not in row, "the canon sync must not write provenance"
        assert set(row) >= {"code", "statement", "severity"}


# --------------------------------------------------------------------------
# factory.py wiring
# --------------------------------------------------------------------------


def test_ship_writes_the_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_LEDGER_DIR", str(tmp_path))
    import factory as factory_module
    slug = "pytest-b4-ship-org"

    class Args:
        approved = True
        dry_run = True
        live = False
        sync_url = None

    try:
        factory_module.stage_ship(
            {"org": {"name": "B4 Org", "id": "x", "customer_status": "CONTRACTED"}}, slug, Args()
        )
        digests = list((FACTORY_DIR / "runs" / slug / "iterations").glob("*-digest.json"))
        assert digests, "S9 must snapshot the iteration before a retrain can overwrite the reports"
    finally:
        import shutil
        shutil.rmtree(FACTORY_DIR / "runs" / slug, ignore_errors=True)


def test_a_digest_failure_never_fails_the_ship_stage(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_LEDGER_DIR", str(tmp_path))
    import factory as factory_module
    slug = "pytest-b4-ship-fail-org"

    class Args:
        approved = True
        dry_run = True
        live = False
        sync_url = None

    try:
        with patch.object(fc, "write_iteration_digest", side_effect=OSError("disk full")):
            path = factory_module.stage_ship(
                {"org": {"name": "B4 Org", "id": "x", "customer_status": "CONTRACTED"}}, slug, Args()
            )
        assert json.loads(Path(path).read_text())["status"] == "approved"
    finally:
        import shutil
        shutil.rmtree(FACTORY_DIR / "runs" / slug, ignore_errors=True)
