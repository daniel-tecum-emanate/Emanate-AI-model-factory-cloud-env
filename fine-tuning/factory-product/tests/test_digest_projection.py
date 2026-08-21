"""C1b (factory-graph-pr2, 2026-07-28): the iteration-digest DB projection.

The defect being closed (decision D5): iteration digests live under `runs/`,
which is gitignored, so a retrain running on a different machine than its
predecessor found no digest and classified itself `first_run`. It did not error.
It silently discarded the corpus recipe, the hyperparameters, and the human's
stated reason for the previous ship decision — which is the single
highest-signal thing a successor run can be told — and then proceeded to
possibly repeat a mistake that had already been paid for once.

Four properties carry the weight here:

1. **Precedence is files, then DB, then nothing.** Files are ground truth for run
   state (D-PI-7 / PRRules rule 5); the DB row is a projection. If the DB ever
   started winning, a stale projection could override the digest this run's own
   predecessor actually wrote.
2. **The fallback is loud when it fails.** The original bug was silence. A
   fallback that swallows its own failure reproduces the defect one layer down,
   so an unreachable projection must warn even though it must not raise.
3. **Both sources return the same shape.** A read path whose shape depends on
   which source answered guarantees that the less-tested branch is the broken one.
4. **No customer content reaches the table.** `ship.no_go_reason` is free text a
   human typed, which is exactly where an email or phone arrives by accident
   (`Lesson_PIIAllowlistValueLevelLeak` — key-level filtering already failed at
   value level once).
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
from supabase_rest import SupabaseRestError  # noqa: E402

SLUG = "qa-dryrun-synth"
RUN_ID = "dddddddd-0000-0000-0000-000000000001"
PRED_RUN_ID = "dddddddd-0000-0000-0000-000000000000"


@pytest.fixture
def run_root(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "HERE", tmp_path)
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    return tmp_path


def _digest(iteration=1, no_go_reason=None, run_id=RUN_ID):
    return {
        "org_slug": SLUG,
        "iteration": iteration,
        "run_id": run_id,
        "predecessor_run_id": PRED_RUN_ID,
        "corpus": {"recipe": "v5-mixture", "seed": 41, "slices": ["a", "b"]},
        "training": {"base_model": "qwen-9b", "lora_rank": 16, "epochs": 2},
        "ship": {"status": "no_go", "recommendation": "hold", "no_go_reason": no_go_reason},
    }


def _write_digest_file(root, iteration, **kwargs):
    directory = root / "runs" / SLUG / "iterations"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{iteration:02d}-digest.json"
    path.write_text(json.dumps(_digest(iteration=iteration, **kwargs)))
    return path


# --------------------------------------------------------------------------
# 1. The row projection
# --------------------------------------------------------------------------


def test_row_mirrors_the_digest_shape():
    row = factory_sync._digest_row(_digest(iteration=3))
    assert row["run_id"] == RUN_ID
    assert row["org_slug"] == SLUG
    assert row["iteration"] == 3
    assert row["predecessor_run_id"] == PRED_RUN_ID
    assert row["corpus"]["recipe"] == "v5-mixture"
    assert row["training"]["base_model"] == "qwen-9b"
    assert row["ship"]["status"] == "no_go"


def test_row_has_no_columns_the_table_does_not_have():
    """The migration defines exactly these. A stray key becomes a PostgREST 400
    at runtime, which fail-open would then swallow — so it must fail here."""
    allowed = {"run_id", "org_slug", "iteration", "predecessor_run_id",
               "corpus", "training", "ship"}
    assert set(factory_sync._digest_row(_digest())) <= allowed


def test_row_keys_exist_in_the_real_migration():
    """The schema-drift guard, checked against the actual SQL rather than the list
    above — the mechanical link between this sync payload and `platform-alpha`'s
    real column list (`_migration.py`). A payload key that is not a real column
    400s on a live push, and `push_iteration_digest` is fail-open, so without this
    the failure would only ever appear as a projection that quietly never happens.
    """
    from _migration import columns_for, read_migration

    columns = columns_for("factory_run_digests", read_migration())
    assert set(factory_sync._digest_row(_digest())) <= columns, (
        "the digest payload has keys factory_run_digests does not declare"
    )


def test_a_missing_iteration_defaults_to_one_not_to_null():
    """`iteration` is NOT NULL in the table. Defaulting here keeps a digest
    written before the state file existed from failing the insert."""
    d = _digest()
    d["iteration"] = None
    assert factory_sync._digest_row(d)["iteration"] == 1


# --------------------------------------------------------------------------
# 2. PII (PRRules rule 6 — every new payload reaching a factory_* table)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "leak,redacted",
    [
        ("buyer bob@acme.com pushed back", "[redacted-email]"),
        ("he called 415-555-0134 to complain", "[redacted-phone]"),
        ("ref 123-45-6789 on file", "[redacted-ssn]"),
    ],
)
def test_free_text_no_go_reason_is_scrubbed(leak, redacted):
    """The human's NO-GO rationale is the one genuinely free-text field in the
    digest, so it is the realistic PII vector — a person explaining why they
    rejected a run naturally quotes the customer."""
    row = factory_sync._digest_row(_digest(no_go_reason=leak))
    assert redacted in row["ship"]["no_go_reason"]
    assert "bob@acme.com" not in json.dumps(row)
    assert "415-555-0134" not in json.dumps(row)
    assert "123-45-6789" not in json.dumps(row)


def test_pii_is_scrubbed_at_any_nesting_depth():
    d = _digest()
    d["corpus"]["hard_rules"] = ["never email ceo@bigco.com directly"]
    row = factory_sync._digest_row(d)
    assert "ceo@bigco.com" not in json.dumps(row)


@pytest.mark.parametrize(
    "uuid",
    [
        "dddddddd-0000-0000-0000-000000000000",
        # All-numeric groups: satisfies the phone pattern end to end.
        "12345678-1234-1234-1234-123456789012",
        "e03f5d35-e24e-4c14-a7d1-352508c547a7",
    ],
)
def test_identifier_columns_are_never_scrubbed(uuid):
    """Regression guard for a bug found while writing this file: scrubbing the
    whole row let `_PHONE_RE` eat part of a UUID, because a digit-only group
    sequence matches `\\d{3}-\\d{3}-\\d{4}`. The result is an invalid uuid,
    PostgREST rejects the insert, and `push_iteration_digest` is fail-open — so
    the projection silently never happens and D5 stays open while appearing
    closed. Identifiers must pass through byte-for-byte."""
    row = factory_sync._digest_row(_digest(run_id=uuid))
    assert row["run_id"] == uuid
    assert row["predecessor_run_id"] == PRED_RUN_ID


def test_org_slug_survives_intact():
    """A config slug, not customer data, and already stored unscrubbed in
    `factory_runs.org_slug` — mangling it here would break the lookup index."""
    assert factory_sync._digest_row(_digest())["org_slug"] == SLUG


# --------------------------------------------------------------------------
# 3. Fail direction — the push is fail-OPEN (a projection is not the record)
# --------------------------------------------------------------------------


def test_push_does_not_raise_on_transport_failure(run_root):
    with patch.object(factory_sync, "upsert", side_effect=SupabaseRestError("boom")):
        assert factory_sync.push_iteration_digest(SLUG, _digest()) is False


def test_push_targets_the_right_table_and_conflict_key(run_root):
    """(run_id, iteration) is the conflict target because the digest is written at
    S9 and REFRESHED at S11 — the same pair is written twice by design, and the
    refresh must upsert rather than duplicate."""
    seen = {}

    def fake_upsert(table, rows, on_conflict=None, **kw):
        seen["table"], seen["on_conflict"], seen["rows"] = table, on_conflict, rows

    with patch.object(factory_sync, "upsert", side_effect=fake_upsert):
        assert factory_sync.push_iteration_digest(SLUG, _digest()) is True
    assert seen["table"] == "factory_run_digests"
    assert seen["on_conflict"] == "run_id,iteration"


def test_push_is_skipped_without_a_run_id(run_root, caplog):
    """No Supabase run id means no `factory_runs` row for the FK to reference.
    Skipped loudly — silently dropping it is how D5 stays open."""
    with patch.object(factory_sync, "upsert") as up:
        assert factory_sync.push_iteration_digest(SLUG, _digest(run_id=None)) is False
    up.assert_not_called()
    assert "no run_id" in caplog.text


def test_read_does_not_raise_but_does_warn(run_root, caplog):
    """Property 2. Returning None on failure is required (a projection must never
    block a run); doing it silently is the original bug."""
    with patch.object(factory_sync, "select", side_effect=SupabaseRestError("unreachable")):
        assert factory_sync.fetch_predecessor_digest(SLUG, 2) is None
    assert "WITHOUT predecessor context" in caplog.text


def test_read_asks_for_the_immediate_predecessor_only(run_root):
    """Decision D4: immediate predecessor, not the whole chain."""
    seen = {}

    def fake_select(table, params=None, **kw):
        seen["table"], seen["params"] = table, params
        return []

    with patch.object(factory_sync, "select", side_effect=fake_select):
        factory_sync.fetch_predecessor_digest(SLUG, 4)
    assert seen["table"] == "factory_run_digests"
    assert seen["params"]["org_slug"] == f"eq.{SLUG}"
    assert seen["params"]["iteration"] == "lt.4"
    assert seen["params"]["order"] == "iteration.desc"
    assert seen["params"]["limit"] == "1"


# --------------------------------------------------------------------------
# 4. Precedence — files, then DB, then nothing
# --------------------------------------------------------------------------


def test_local_file_wins_over_the_db_projection(run_root):
    """Property 1. The DB must not even be consulted when a file answers."""
    _write_digest_file(run_root, 1)
    reader = lambda *a, **k: pytest.fail("the DB was consulted despite a local digest existing")
    digest = fc.load_predecessor_digest(SLUG, current_iteration=2, db_reader=reader)
    assert digest["iteration"] == 1
    assert digest.get("_source") is None


def test_db_answers_when_no_local_digest_exists(run_root, caplog):
    """The D5 fix itself: this is a retrain on a machine that never saw iteration 1."""
    called = {}

    def reader(slug, current_iteration, url=None):
        called["args"] = (slug, current_iteration)
        return {"org_slug": slug, "iteration": 1, "run_id": PRED_RUN_ID,
                "predecessor_run_id": None, "corpus": {"recipe": "v5-mixture"},
                "training": {}, "ship": {}, "_source": "db_projection"}

    digest = fc.load_predecessor_digest(SLUG, current_iteration=2, db_reader=reader)
    assert called["args"] == (SLUG, 2)
    assert digest["iteration"] == 1
    assert digest["corpus"]["recipe"] == "v5-mixture"
    assert digest["_source"] == "db_projection"


def test_both_sources_empty_is_still_a_first_run(run_root):
    """None must remain a legitimate answer — a genuine first run has no
    predecessor. What changed is that it now means that only after asking both."""
    assert fc.load_predecessor_digest(SLUG, current_iteration=1, db_reader=lambda *a, **k: None) is None


def test_a_failing_db_reader_does_not_break_the_run(run_root, caplog):
    def boom(*a, **k):
        raise RuntimeError("connection reset")

    assert fc.load_predecessor_digest(SLUG, current_iteration=2, db_reader=boom) is None
    assert "without predecessor context" in caplog.text


def test_db_sourced_digest_has_the_same_keys_as_a_file_sourced_one(run_root):
    """Property 3. Compared key-for-key against the on-disk shape, ignoring the
    provenance marker, so the two branches cannot diverge unnoticed."""
    _write_digest_file(run_root, 1)
    from_file = fc.load_predecessor_digest(SLUG, current_iteration=2, db_reader=lambda *a, **k: None)

    row = factory_sync._digest_row(_digest(iteration=1))
    with patch.object(factory_sync, "select", side_effect=lambda *a, **k: [row]):
        from_db = factory_sync.fetch_predecessor_digest(SLUG, 2)

    assert set(from_file) == set(from_db) - {"_source"}


def test_the_projection_round_trips_through_the_table_shape(run_root):
    """Write the digest, project it, read it back through the DB path, and confirm
    the values a retrain actually depends on survived."""
    original = _digest(iteration=2, no_go_reason="tone drift on short replies")
    row = factory_sync._digest_row(original)
    with patch.object(factory_sync, "select", side_effect=lambda *a, **k: [row]):
        recovered = factory_sync.fetch_predecessor_digest(SLUG, 3)
    assert recovered["corpus"] == original["corpus"]
    assert recovered["training"] == original["training"]
    assert recovered["ship"]["no_go_reason"] == "tone drift on short replies"
    assert recovered["predecessor_run_id"] == PRED_RUN_ID
