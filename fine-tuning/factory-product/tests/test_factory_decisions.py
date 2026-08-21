"""B2 (factory-graph-pr1, 2026-07-27): the factory decision log.

Four properties carry real weight here, and each has a specific failure this
suite exists to prevent:

1. **Append-only.** An outcome must be a second row, never an edit of the first.
   A rewritable audit trail is not an audit trail
   (`Lesson_AuditTrailNeedsExplicitRevoke`). Nothing but this test enforces it
   for a JSONL file.
2. **`context_refs` carries pointers, not content.** Inlining the material a
   decision looked at is both an unbounded storage cost and a PII exfiltration
   path around the allow-list.
3. **No PII survives into the DB projection.** `rationale` is free text, so it
   is where an email or phone arrives by accident
   (`Lesson_PIIAllowlistValueLevelLeak` — key-level filtering alone already
   failed once at value level).
4. **Fail-open, in the right direction.** A Supabase outage must not lose a
   decision or raise — the ledger is the durable record, the DB is a projection.
   The mirror image of this (gate polling) is fail-CLOSED, and confusing the two
   is the most dangerous mistake available in this area (PRRules.md rule 4).
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory_decisions as fd  # noqa: E402
import factory_sync  # noqa: E402

RUN_ID = "cccccccc-0000-0000-0000-000000000001"
SLUG = "qa-dryrun-synth"


def _base(**overrides):
    kwargs = dict(
        run_id=RUN_ID,
        graph_id="fine-tune-factory-v1",
        node_id="S4",
        decision_point="corpus-recipe",
        options=["recipe-a", "recipe-b"],
        chosen="recipe-a",
        rationale="higher coverage on the gap slices",
        actor="deterministic",
        org_slug=SLUG,
        context_refs=["factory-automation/factory/runs/qa-dryrun-synth/04-build-report.json"],
    )
    kwargs.update(overrides)
    return kwargs


def _rows(ledger_dir):
    path = list(Path(ledger_dir).glob("*.jsonl"))[0]
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# --------------------------------------------------------------------------
# event shape
# --------------------------------------------------------------------------


def test_decision_row_carries_every_documented_field(tmp_path):
    decision_id = fd.record_decision(ledger_dir=tmp_path, **_base())
    row = _rows(tmp_path)[0]
    for key in (
        "ts", "event", "decision_id", "graph_id", "run_id", "node_id", "org_slug",
        "decision_point", "options", "chosen", "rationale", "actor", "context_refs",
    ):
        assert key in row, f"decision row is missing {key}"
    assert row["event"] == "factory_decision"
    assert row["decision_id"] == decision_id


def test_ledger_target_matches_tier2_run_sh_convention(tmp_path):
    """One JSON object per line in `ledger/YYYY-MM-DD.jsonl`. There is no shared
    writer library — the convention IS the schema, so it is worth asserting."""
    from datetime import datetime
    fd.record_decision(ledger_dir=tmp_path, **_base())
    expected = tmp_path / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
    assert expected.is_file()
    assert len(expected.read_text().strip().splitlines()) == 1


def test_env_var_redirects_the_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_LEDGER_DIR", str(tmp_path))
    fd.record_decision(**_base())
    assert _rows(tmp_path)[0]["event"] == "factory_decision"


@pytest.mark.parametrize("actor", ["agent", "human", "deterministic"])
def test_valid_actors_accepted(tmp_path, actor):
    assert fd.record_decision(ledger_dir=tmp_path, **_base(actor=actor))


@pytest.mark.parametrize("actor", ["robot", "", None, "Human", "system"])
def test_actor_vocabulary_is_closed(tmp_path, actor):
    """An open actor field would make "was this a human's call?" unanswerable
    later, which is the single most important thing this log records."""
    with pytest.raises(ValueError):
        fd.record_decision(ledger_dir=tmp_path, **_base(actor=actor))


def test_decision_point_is_required(tmp_path):
    with pytest.raises(ValueError):
        fd.record_decision(ledger_dir=tmp_path, **_base(decision_point=""))


# --------------------------------------------------------------------------
# append-only
# --------------------------------------------------------------------------


def test_outcome_appends_and_leaves_the_original_bytes_untouched(tmp_path):
    decision_id = fd.record_decision(ledger_dir=tmp_path, **_base())
    path = list(tmp_path.glob("*.jsonl"))[0]
    before = path.read_text()

    fd.record_outcome(ledger_dir=tmp_path, decision_id=decision_id, outcome="accepted")
    after = path.read_text()

    assert after.startswith(before), "the original decision row was mutated — append-only violated"
    rows = _rows(tmp_path)
    assert [r["event"] for r in rows] == ["factory_decision", "factory_decision_outcome"]
    assert rows[1]["decision_id"] == decision_id


def test_multiple_outcomes_all_append(tmp_path):
    """A decision can be revisited — `accepted` then later `regressed`. Both are
    kept; the log records history, not a current-value field."""
    decision_id = fd.record_decision(ledger_dir=tmp_path, **_base())
    fd.record_outcome(ledger_dir=tmp_path, decision_id=decision_id, outcome="accepted")
    fd.record_outcome(ledger_dir=tmp_path, decision_id=decision_id, outcome="regressed")
    rows = _rows(tmp_path)
    assert len(rows) == 3
    assert [r.get("outcome") for r in rows[1:]] == ["accepted", "regressed"]


@pytest.mark.parametrize("outcome", ["accepted", "rejected", "overridden", "regressed", "shipped"])
def test_valid_outcomes(tmp_path, outcome):
    assert fd.record_outcome(ledger_dir=tmp_path, decision_id="d1", outcome=outcome)


@pytest.mark.parametrize("outcome", ["approved", "ok", "", None])
def test_outcome_vocabulary_is_closed(tmp_path, outcome):
    with pytest.raises(ValueError):
        fd.record_outcome(ledger_dir=tmp_path, decision_id="d1", outcome=outcome)


def test_outcome_requires_a_decision_id(tmp_path):
    with pytest.raises(ValueError):
        fd.record_outcome(ledger_dir=tmp_path, decision_id="", outcome="accepted")


# --------------------------------------------------------------------------
# context_refs: pointers, never content
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "refs",
    [
        ["factory-automation/factory/runs/x/04-build-report.json"],
        ["artifact:corpus-v4", "runs/x/07-launch-report.json"],
        ["https://api.braintrust.dev/v1/project/abc123"],
        [],
    ],
)
def test_real_refs_accepted(tmp_path, refs):
    assert fd.record_decision(ledger_dir=tmp_path, **_base(context_refs=refs))


@pytest.mark.parametrize(
    "refs,why",
    [
        (["the corpus contained 4102 examples and the planner chose"], "prose, not a ref"),
        (["line one\nline two"], "newline-laden content"),
        (["x" * 500], "over the length bound"),
        (["a/b.json" for _ in range(200)], "too many refs to be pointers"),
        ("runs/x/04-build-report.json", "a bare string instead of a list"),
        ([""], "empty ref"),
        ([{"path": "x"}], "non-string ref"),
    ],
)
def test_content_shaped_refs_rejected(tmp_path, refs, why):
    with pytest.raises(ValueError):
        fd.record_decision(ledger_dir=tmp_path, **_base(context_refs=refs))


# --------------------------------------------------------------------------
# PII never survives into the DB projection
# --------------------------------------------------------------------------


def test_pii_in_a_rationale_is_scrubbed_from_the_run_event(tmp_path):
    row = {
        "decision_id": "d1",
        "graph_id": "fine-tune-factory-v1",
        "run_id": RUN_ID,
        "node_id": "S9",
        "decision_point": "ship-gate",
        "options": ["approve", "reject"],
        "chosen": "reject",
        "rationale": "flagged by daniel@emanate.ai, call 415-555-0199, ssn 123-45-6789",
        "actor": "human",
        "context_refs": ["runs/x/09-ship-report.json"],
    }
    event = fd.project_decision_event(row)
    blob = json.dumps(event)
    assert "daniel@emanate.ai" not in blob
    assert "415-555-0199" not in blob
    assert "123-45-6789" not in blob
    assert "[redacted-email]" in blob


def test_projection_uses_the_shared_scrubber_not_a_parallel_one(tmp_path):
    """If someone reimplements redaction here, this catches the divergence: the
    projection must go through `factory_sync._scrub_structured_pii`."""
    with patch.object(factory_sync, "_scrub_structured_pii", side_effect=lambda v: v) as mock_scrub:
        fd.project_decision_event({"decision_id": "d1", "rationale": "x"})
    mock_scrub.assert_called_once()


def test_projected_event_is_schema_legal(tmp_path):
    """PR 1 rode on `steering_event` because the CHECK constraint lacked
    `decision_recorded` and PR 1 shipped no migration (PR1-FINDINGS.md F5).
    `20260728064545` then widened the CHECK, re-mapped the old rows, and
    expected new rows to carry the real kind directly — which the mirror now
    does (2026-07-31, stress-test EVENT-COVERAGE F-03-4). The subtype tag
    stays, matching the re-mapped rows exactly (one row family, one shape).
    """
    legal_kinds = {
        "stage_started", "stage_passed", "stage_failed", "agent_spawned", "agent_finished",
        "gate_requested", "gate_decided", "steering_event", "cost_recorded", "alert",
        "decision_recorded", "agent_dispatched",
    }
    event = fd.project_decision_event({"decision_id": "d1", "run_id": RUN_ID, "node_id": "S4", "chosen": "a"})
    assert event["kind"] == "decision_recorded"
    assert event["kind"] in legal_kinds
    assert event["ref"]["event_subtype"] == "decision_recorded"
    assert set(event) == {"run_id", "kind", "headline", "ref", "detail"}


# --------------------------------------------------------------------------
# fail-open
# --------------------------------------------------------------------------


def test_supabase_outage_does_not_raise_and_the_ledger_row_still_lands(tmp_path):
    with patch("supabase_rest.insert", side_effect=RuntimeError("connection refused")):
        decision_id = fd.record(ledger_dir=tmp_path, sync_url="http://localhost:1/nope", **_base())
    assert decision_id
    assert _rows(tmp_path)[0]["decision_id"] == decision_id


def test_push_decision_reports_failure_without_raising():
    with patch("supabase_rest.insert", side_effect=RuntimeError("boom")):
        assert fd.push_decision({"decision_id": "d1"}, url="http://localhost:1/nope") is False


def test_push_decision_is_a_no_op_with_no_sync_configured(monkeypatch):
    monkeypatch.delenv("FACTORY_SUPABASE_URL", raising=False)
    with patch("supabase_rest.insert") as mock_insert:
        assert fd.push_decision({"decision_id": "d1"}) is False
    mock_insert.assert_not_called()


def test_ledger_write_happens_before_the_mirror(tmp_path):
    """Order is the contract: the mirror can never be the reason a decision goes
    unrecorded. Asserted by having the mirror observe an already-written file.
    """
    seen = {}

    def _spy(*args, **kwargs):
        seen["rows_at_mirror_time"] = len(_rows(tmp_path))
        raise RuntimeError("down")

    with patch("supabase_rest.insert", side_effect=_spy):
        fd.record(ledger_dir=tmp_path, sync_url="http://x", **_base())
    assert seen["rows_at_mirror_time"] == 1


# --------------------------------------------------------------------------
# factory.py's gate wiring
# --------------------------------------------------------------------------


@pytest.fixture
def gate_slug(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_LEDGER_DIR", str(tmp_path))
    slug = "pytest-b2-synthetic-org"
    yield slug, tmp_path
    import shutil
    shutil.rmtree(FACTORY_DIR / "runs" / slug, ignore_errors=True)


def _governance_cfg():
    return {
        "org": {"name": "Synthetic B2 Org", "id": "x", "customer_status": "CONTRACTED"},
        "governance": {
            "data_use_clearance": "CLEARED",
            "dpa_signed": True,
            "training_rights_clause": True,
            "pii_egress_cleared": True,
            "permitted_uses": ["sft"],
        },
    }


def test_governance_gate_emits_a_decision_with_the_right_node_id(gate_slug):
    import factory as factory_module
    slug, ledger_dir = gate_slug

    class Args:
        approved = True
        dry_run = True
        live = False
        sync_url = None

    factory_module.stage_governance(_governance_cfg(), slug, Args())
    rows = [r for r in _rows(ledger_dir) if r["event"] == "factory_decision"]
    assert len(rows) == 1
    assert rows[0]["node_id"] == "S2"
    assert rows[0]["chosen"] == "approve"
    assert rows[0]["actor"] == "human"


def test_a_cli_refusal_is_recorded_as_deterministic_not_human(gate_slug):
    """`--approved` with an incomplete checklist is refused by the CLI's own
    logic. Labeling that a human decision would poison the dataset this log
    exists to build — the human asked for approval and did not get it.
    """
    import factory as factory_module
    slug, ledger_dir = gate_slug
    cfg = _governance_cfg()
    cfg["governance"]["dpa_signed"] = False

    class Args:
        approved = True
        dry_run = True
        live = False
        sync_url = None

    factory_module.stage_governance(cfg, slug, Args())
    row = [r for r in _rows(ledger_dir) if r["event"] == "factory_decision"][0]
    assert row["chosen"] == "reject"
    assert row["actor"] == "deterministic"


def test_a_clear_checklist_auto_approves_by_policy_with_no_human_flag(gate_slug, monkeypatch):
    """2026-08-19 (Daniel): governance's checklist is a boolean compliance fact,
    not a graduated risk — once it's genuinely clear, no human needs to also
    click approve. No `--approved`, no sync/tab click, checklist clear: this
    must still auto-approve, attributed to `policy` (see stage_launch's
    equivalent `budget.auto_approve_under_usd` test for the same shape)."""
    import factory as factory_module
    slug, ledger_dir = gate_slug
    monkeypatch.delenv("FACTORY_SUPABASE_URL", raising=False)

    class Args:
        approved = False
        dry_run = True
        live = False
        sync_url = None

    factory_module.stage_governance(_governance_cfg(), slug, Args())
    row = [r for r in _rows(ledger_dir) if r["event"] == "factory_decision"][0]
    assert row["chosen"] == "approve"
    assert row["actor"] == "policy"


def test_a_pending_gate_records_defer(gate_slug, monkeypatch):
    """The fail-safe this whole gate exists for: an INCOMPLETE checklist, no
    `--approved`, no sync — must still stay pending. Auto-approval only ever
    fires when the checklist is genuinely clear (see the test above)."""
    import factory as factory_module
    slug, ledger_dir = gate_slug
    monkeypatch.delenv("FACTORY_SUPABASE_URL", raising=False)
    cfg = _governance_cfg()
    cfg["governance"]["dpa_signed"] = False

    class Args:
        approved = False
        dry_run = True
        live = False
        sync_url = None

    factory_module.stage_governance(cfg, slug, Args())
    row = [r for r in _rows(ledger_dir) if r["event"] == "factory_decision"][0]
    assert row["chosen"] == "defer"


def test_a_broken_decision_log_never_breaks_a_gate(gate_slug):
    """The gate is the product; the log is instrumentation. If the log explodes,
    the gate must still resolve correctly."""
    import factory as factory_module
    slug, _ = gate_slug

    class Args:
        approved = True
        dry_run = True
        live = False
        sync_url = None

    with patch.object(fd, "record", side_effect=RuntimeError("ledger disk full")):
        path = factory_module.stage_governance(_governance_cfg(), slug, Args())
    assert json.loads(Path(path).read_text())["status"] == "approved"


def test_decision_logging_never_mints_a_run_id(gate_slug):
    """`get_or_create_run_id` can START A NEW RUN when the prior one is terminal
    — it is a lineage operation, not a getter. A logging call triggering that
    would be a genuinely destructive side effect, so the decision path reads
    cached state instead and accepts `None`.

    Scoped to `_run_id_for_decisions` deliberately: `write_report`'s sync hook
    legitimately mints, and asserting over the whole stage handler would just be
    re-testing that, then failing for the wrong reason.
    """
    import factory as factory_module
    slug, _ = gate_slug

    class Args:
        approved = True
        dry_run = True
        live = False
        sync_url = "http://localhost:1/unreachable"

    with patch.object(factory_sync, "get_or_create_run_id") as mock_mint:
        factory_module._run_id_for_decisions(slug, Args())
    mock_mint.assert_not_called()


def test_gate_decision_carries_the_run_id_once_one_exists(gate_slug):
    """The reason the emission happens after `write_report`: by then the sync
    hook has established a run, so the decision joins to it instead of
    stamping a null."""
    import factory as factory_module
    slug, ledger_dir = gate_slug

    class Args:
        approved = True
        dry_run = True
        live = False
        sync_url = "http://localhost:1/unreachable"

    with patch.object(factory_module, "_run_id_for_decisions", return_value=RUN_ID):
        factory_module.stage_governance(_governance_cfg(), slug, Args())
    row = [r for r in _rows(ledger_dir) if r["event"] == "factory_decision"][0]
    assert row["run_id"] == RUN_ID
