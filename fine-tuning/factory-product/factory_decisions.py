"""B2 (factory-graph-pr1, 2026-07-27): the factory's structured decision log.

`automation-graph/RETRAIN-AND-META-LEARNING.md` §3 asks for the factory to record
not just *what* it produced but *what it chose and why* — so that the operating
history of the factory itself eventually becomes trainable material ("the factory
learns to run the factory"). PR 1 ships the **hooks only**: the events are
emitted and stored, nothing consumes them yet
(`Decision_FactoryDecisionLogHooksNowExportLater`). Building the export before
there is a corpus to export would be building on nothing.

Two event types, both append-only rows in `ledger/YYYY-MM-DD.jsonl`:

    {"event": "factory_decision", "decision_id": ..., ...}          # a choice was made
    {"event": "factory_decision_outcome", "decision_id": ..., ...}  # how it turned out

**The outcome is a SECOND ROW, never an edit of the first.** That is the same
rule `factory_gate_decisions` lives under, and it exists because
`Lesson_AuditTrailNeedsExplicitRevoke` established that an audit trail you can
rewrite is not an audit trail. For a Postgres table, append-only can at least be
argued from grants; for a JSONL file, nothing enforces it but discipline and the
test in `tests/test_factory_decisions.py` that asserts the original row's bytes
are unchanged after an outcome is recorded.

## Ledger, not database, is the durable record

The ledger append always happens and is the source of truth. The
`factory_run_events` mirror is a fail-open projection for the Model Factory tab:
if Supabase is unreachable the decision is still recorded, because a decision
that happened is a fact regardless of whether a UI heard about it. This is the
same fail direction as `push_stage` and the deliberate OPPOSITE of gate approval
polling, which is fail-closed. (PRRules.md rule 4 — getting these backwards is
the single most dangerous mistake available in this file: fail-open on a gate
would let an approval-less run ship.)

## The mirror's `kind`: `steering_event` in PR 1, `decision_recorded` now

RETRAIN-AND-META-LEARNING.md §3 proposed a new `decision_recorded` event kind.
It could not ship in PR 1: `factory_run_events.kind` carried a CHECK constraint
(`20260725003051_model_factory_tables.sql`) without it, and PR 1 deliberately
shipped **no migration**. So the mirror rode on `steering_event` (the nearest
honest fit) tagged `ref.event_subtype = "decision_recorded"` — finding F5 in
`automation-graph/PR1-FINDINGS.md`.

PR 2's migration (`20260728064545_factory_decision_kind_and_digests.sql`) then
widened the CHECK, re-mapped the accumulated rows by that tag, and expected new
rows to carry `decision_recorded` directly — which this module now does
(switched 2026-07-31, stress-test EVENT-COVERAGE F-03-4; it had been left on
`steering_event`, quietly re-creating the pre-re-map rows forever).
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

ACTORS = frozenset({"agent", "human", "deterministic", "policy"})
OUTCOMES = frozenset({"accepted", "rejected", "overridden", "regressed", "shipped"})

DECISION_EVENT = "factory_decision"
OUTCOME_EVENT = "factory_decision_outcome"

# `context_refs` must carry POINTERS, never content. RETRAIN-AND-META-LEARNING.md
# §3 is explicit about the reason and it is twofold: a decision log that inlines
# the material it looked at is (a) unboundedly expensive to store and later
# train on, and (b) a PII exfiltration path straight out of the allow-list —
# customer content would arrive inside a field nobody thought to scrub. A ref is
# a repo-relative path or an artifact id: no whitespace, no newlines, bounded
# length. That is mechanically checkable, which a "please don't paste content"
# comment is not.
_REF_RE = re.compile(r"^[A-Za-z0-9._/:@#=-]+$")
MAX_REF_LEN = 240
MAX_REFS = 64


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _ledger_path(ledger_dir=None):
    """Resolve today's ledger file. Precedence: explicit argument, then
    `FACTORY_LEDGER_DIR`, then the real `ledger/`.

    The env override exists so a test run can never append synthetic decisions
    to the production ledger — that file feeds the nightly metrics, digests, and
    cost rollups, so a stray `pytest-synthetic-org` row is not harmless noise,
    it is a corrupted measurement. `tests/conftest.py` sets it session-wide.
    """
    directory = Path(ledger_dir) if ledger_dir else Path(os.environ.get("FACTORY_LEDGER_DIR") or REPO / "ledger")
    directory.mkdir(parents=True, exist_ok=True)
    return directory / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")


def _append(row, ledger_dir=None):
    """One JSON object per line, appended — byte-for-byte the convention
    `scripts/tier2_run.sh` already writes with (there is no shared writer
    library to reuse; the ledger's only schema is this convention)."""
    path = _ledger_path(ledger_dir)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return path


def validate_context_refs(context_refs):
    """Raise `ValueError` unless every ref is a pointer rather than content."""
    if context_refs is None:
        return []
    if isinstance(context_refs, str):
        raise ValueError("context_refs must be a list of refs, not a single string")
    refs = list(context_refs)
    if len(refs) > MAX_REFS:
        raise ValueError(f"context_refs has {len(refs)} entries (max {MAX_REFS}) — that is content, not refs")
    for ref in refs:
        if not isinstance(ref, str) or not ref:
            raise ValueError(f"context_refs entries must be non-empty strings, got {ref!r}")
        if len(ref) > MAX_REF_LEN:
            raise ValueError(
                f"context_ref is {len(ref)} chars (max {MAX_REF_LEN}) — pass a path or artifact id, not file contents"
            )
        if not _REF_RE.match(ref):
            raise ValueError(
                f"context_ref {ref[:60]!r} is not a path/artifact-id shape (whitespace or newlines "
                f"mean this is content) — pass a reference, never the material itself"
            )
    return refs


def record_decision(
    *,
    run_id,
    graph_id,
    node_id,
    decision_point,
    options,
    chosen,
    rationale,
    actor,
    org_slug,
    context_refs=None,
    ledger_dir=None,
    extra=None,
):
    """Append one `factory_decision` row and return its `decision_id`.

    Field names are snake_case, matching the ledger's own convention
    (`session_id`, `cost_usd`). The TypeScript side of this same feature uses
    camelCase to match ITS convention (`orgId`, `trajectoryId`) — each side is
    internally consistent; the mapping is decision D3 in
    `automation-graph/DECISIONS-PR1.md`.

    `node_id` is a factory stage code, optionally agent-qualified: `"S4"` or
    `"S4:corpus-planner"` (decision D2). Charter node numbers 1-13 are NOT used
    here — they live in the AgentSpec's own `graph_node` field.
    """
    if actor not in ACTORS:
        raise ValueError(f"actor must be one of {sorted(ACTORS)}, got {actor!r}")
    if not decision_point:
        raise ValueError("decision_point is required — an unlabeled decision is not recoverable later")
    refs = validate_context_refs(context_refs)

    decision_id = str(uuid.uuid4())
    row = {
        "ts": _now(),
        "event": DECISION_EVENT,
        "decision_id": decision_id,
        "graph_id": graph_id,
        "run_id": run_id,
        "node_id": node_id,
        "org_slug": org_slug,
        "decision_point": decision_point,
        "options": list(options or []),
        "chosen": chosen,
        "rationale": rationale,
        "actor": actor,
        "context_refs": refs,
    }
    if extra:
        row.update(extra)
    _append(row, ledger_dir)
    return decision_id


def record_outcome(*, decision_id, outcome, ledger_dir=None, note=None, extra=None):
    """Append a `factory_decision_outcome` row keyed by `decision_id`.

    Never touches the original `factory_decision` row — see this module's
    docstring. Joining the two is the reader's job, which is the price of an
    audit trail that cannot be quietly revised.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(OUTCOMES)}, got {outcome!r}")
    if not decision_id:
        raise ValueError("decision_id is required — an outcome with nothing to join to is noise")
    row = {
        "ts": _now(),
        "event": OUTCOME_EVENT,
        "decision_id": decision_id,
        "outcome": outcome,
    }
    if note:
        row["note"] = note
    if extra:
        row.update(extra)
    _append(row, ledger_dir)
    return row


def project_decision_event(decision_row, run_id=None):
    """Project a ledger decision row into a `factory_run_events` row dict.

    Routed through `factory_sync._scrub_structured_pii` — the SAME value-level
    layer every other payload uses, not a parallel one. `rationale` is free text
    written by an agent or a human, so it is the likeliest place for an email or
    phone number to arrive by accident (`Lesson_PIIAllowlistValueLevelLeak`:
    key-level allow-listing alone did not stop a value-level leak, and this
    payload has no key-level allow-list at all).

    `kind` is `decision_recorded` since 2026-07-31. PR 1 rode on
    `steering_event` because the CHECK constraint did not admit the real kind
    and PR 1 shipped no migration (see the module docstring) — but
    `20260728064545_factory_decision_kind_and_digests.sql` (factory-graph-pr2)
    then widened the CHECK, re-mapped the accumulated rows to
    `decision_recorded`, and stated "new rows written after this migration
    carry kind = 'decision_recorded' directly". This emitter was never
    switched, so the kind sat dark while every new row still needed the
    migration's re-map applied to it (stress-test 2026-07-31, EVENT-COVERAGE
    finding F-03-4). `ref.event_subtype` stays, exactly as the migration
    chose for the re-mapped rows: one code path, and the tag remains the
    evidence of this row family's history.
    """
    from factory_sync import _scrub_structured_pii

    detail = {
        "decision_point": decision_row.get("decision_point"),
        "options": decision_row.get("options"),
        "chosen": decision_row.get("chosen"),
        "rationale": decision_row.get("rationale"),
        "actor": decision_row.get("actor"),
        "context_refs": decision_row.get("context_refs"),
    }
    # IDENTIFIERS PASS THROUGH UNSCRUBBED — same discipline `_digest_row` already
    # applies, and for the same reason it documents.
    #
    # This function used to scrub the WHOLE row, `run_id` included. `_PHONE_RE`
    # matches `\d{3}[-.\s]?\d{3}[-.\s]?\d{4}`, which a UUID segment satisfies
    # whenever its digit groups happen to carry no hex letters. `_digest_row`'s
    # docstring predicted this exactly and noted a real UUID "is only accidentally
    # safe because it usually contains letters".
    #
    # On 2026-08-25 a real one was not safe. Running S2 governance for grand-steel
    # against a live relay:
    #     relay insert factory_run_events failed: 400
    #     invalid input syntax for type uuid: "8a[redacted-phone]-4107-9ace-41e8321b540c"
    # PostgREST rejects the row, `mirror_decision_event` is fail-open, so the error
    # is logged and swallowed and the decision event is never recorded. The Model
    # Factory tab shows a run with no decision history and nothing reports a fault.
    #
    # So the scrub applies to the two sections that carry content — `headline` (an
    # agent-written choice) and `detail`, whose `rationale` is free text and the
    # likeliest place a stray email or phone actually arrives. `kind` is a fixed
    # literal and `ref` holds only identifiers.
    scrubbed = _scrub_structured_pii(
        {
            "headline": f"{decision_row.get('node_id')}: {decision_row.get('chosen')}",
            "detail": detail,
        }
    )
    return {
        "run_id": run_id or decision_row.get("run_id"),
        "kind": "decision_recorded",
        "headline": scrubbed["headline"],
        "ref": {
            "event_subtype": "decision_recorded",
            "decision_id": decision_row.get("decision_id"),
            "node_id": decision_row.get("node_id"),
            "graph_id": decision_row.get("graph_id"),
        },
        "detail": scrubbed["detail"],
    }


def push_decision(decision_row, run_id=None, url=None, service_role_key=None):
    """Mirror a decision into `factory_run_events`. FAIL-OPEN by construction.

    The broad `except Exception` is deliberate and matches `write_report`'s sync
    hook: this is a pipeline-blocking boundary, so it must return normally no
    matter what goes wrong in the sidecar — including failures `factory_sync`
    itself doesn't anticipate, such as `requests` not being installed. Returns
    True only if the row actually landed, so callers/tests can tell the
    difference without being able to be broken by it.
    """
    effective_url = url or os.environ.get("FACTORY_SUPABASE_URL")
    if not effective_url:
        return False
    try:
        from supabase_rest import insert
        insert(
            "factory_run_events",
            [project_decision_event(decision_row, run_id=run_id)],
            url=effective_url,
            service_role_key=service_role_key,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - deliberate, see docstring (fail-open)
        log.warning("factory_decisions: run-event mirror failed, continuing anyway (fail-open): %s", exc)
        return False


def record(
    *,
    run_id,
    graph_id,
    node_id,
    decision_point,
    options,
    chosen,
    rationale,
    actor,
    org_slug,
    context_refs=None,
    ledger_dir=None,
    sync_url=None,
):
    """Convenience: ledger append (durable) + DB mirror (best-effort), in that
    order. The order is the contract — the mirror can never be the reason a
    decision goes unrecorded."""
    decision_id = record_decision(
        run_id=run_id,
        graph_id=graph_id,
        node_id=node_id,
        decision_point=decision_point,
        options=options,
        chosen=chosen,
        rationale=rationale,
        actor=actor,
        org_slug=org_slug,
        context_refs=context_refs,
        ledger_dir=ledger_dir,
    )
    push_decision(
        {
            "decision_id": decision_id,
            "graph_id": graph_id,
            "run_id": run_id,
            "node_id": node_id,
            "decision_point": decision_point,
            "options": list(options or []),
            "chosen": chosen,
            "rationale": rationale,
            "actor": actor,
            "context_refs": list(context_refs or []),
        },
        run_id=run_id,
        url=sync_url,
    )
    return decision_id
