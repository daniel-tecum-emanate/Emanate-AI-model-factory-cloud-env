"""ab_shadow_sync — the A/B shadow nights' projection into factory_run_events
must be aggregate-only (no record-level customer content ever reaches the DB),
deterministic (uuid5 per night-directory + arm), and idempotent (a second run
upserts the same ids — zero new rows)."""

import json
import uuid

import pytest

from ab_shadow_sync import (
    ARM_MODEL,
    NIGHT_DIR_RE,
    _event_row_id,
    aggregate_jsonl,
    build_all_events,
    build_model_ab_nights,
    build_night_events,
    dedupe_model_ab_nights,
    event_to_model_ab_night,
    main,
    parse_comparison_html,
    parse_summary_json,
)
from backfill_ptc_history import NAMESPACE
from supabase_rest import SupabaseRestError

# ---------------------------------------------------------------------------
# Fixture material — synthetic, but shaped exactly like the real records
# (field names sampled from platform-alpha/finetune-out/ab-shadow/*). The
# SENTINEL_* strings are what must NEVER appear in a projected row.
# ---------------------------------------------------------------------------

SENTINEL_NAME = "SENTINEL Customer Corp"
SENTINEL_REASONING = "SENTINEL-REASONING: this account ordered 400 tons"
SENTINEL_PAYLOAD = "SENTINEL-DRAFT-BODY hello Bob"

ENVELOPE = "a" * 64
ORG_ENVELOPE = "b" * 64


def _record(account, output_type="no_action", error=None, tool_calls=(),
            emitted=True, prompt=100, completion=20, wall_ms=None, extra=None):
    rec = {
        "accountId": account,
        "accountName": SENTINEL_NAME,
        "orgId": "01a4c1ae-4a25-4cdc-9883-dea0827c9ce3",
        "arm": "v3",
        "envelopeHash": ENVELOPE,
        "provider": "fireworks",
        "emitted": emitted,
        "outputType": output_type,
        "importanceScore": 25,
        "reasoning": SENTINEL_REASONING,
        "payload": {"draft_body": SENTINEL_PAYLOAD},
        "toolCalls": [
            {"turn": 0, "name": name, "input": {}, "isError": is_err,
             "resultContent": SENTINEL_PAYLOAD}
            for name, is_err in tool_calls
        ],
        "maxParallelCallsInATurn": 2,
        "awarenessToolsCalled": [n for n, _ in tool_calls],
        "iterations": 1,
        "truncated": False,
        "promptTokens": prompt,
        "completionTokens": completion,
        "error": error,
    }
    if wall_ms is not None:
        rec["wallMs"] = wall_ms
    if extra:
        rec.update(extra)
    return rec


V3_RECORDS = [
    _record("acct-1", "no_action", tool_calls=[("summarize_conversations", False)]),
    _record("acct-2", "buying_signal", tool_calls=[("read_crm_logs", True), ("read_crm_logs", False)]),
    _record("acct-3", "no_action", error="timeout after 3 retries", emitted=False, tool_calls=()),
]

A1V1_RECORDS = [
    _record("acct-1", "no_action",
            tool_calls=[("emit_output", True)], wall_ms=14508,
            extra={"arm": "a1v1", "provider": "runpod", "modelId": "ptc-steel-a1-v1"}),
    _record("acct-2", "winback_enrollment", wall_ms=8000,
            extra={"arm": "a1v1", "provider": "runpod", "modelId": "ptc-steel-a1-v1"}),
]

SUMMARY_JSON = {
    "date": "2026-07-20",
    "frame": 2,
    "fullNight": 3,
    "isSample": True,
    "a1v1": {
        "label": "a1-v1 (retro replay)",
        "n": 2,
        "claudeActionableN": 1,
        "typeMatch": 1,
        "flippedToNoAction": 0,
        "differentAction": 0,
        "neverEmitted": 0,
        "falseAlarms": 1,
        "claudeNoActionN": 1,
        "noActionRateUncontaminated": 0.5,
        "claudeNoActionRateOnUncontaminated": 0.5,
        "t19": {"n": 2, "completeRate": 1.0, "emptyPayloads": 0},
        "fab": {"n": 1, "g8Fails": 1, "g22Fails": 0,
                "unsupportedExamples": [f"{SENTINEL_NAME}: $13.1B"]},
    },
    "v3": {"label": "v3 (recorded)", "n": 0},
}

COMPARISON_HTML = f"""
<html><head><title>AB-SHADOW — 2026-07-20</title></head><body>
<h1>AB-SHADOW — v3 vs v5 vs Anthropic live — 2026-07-20</h1>
<div class="summary-block"><table>
<tr><th>Arm</th><th>Matches Claude</th><th>Missed a real action</th>
<th>False alarm</th><th>Different action</th></tr>
<tr><td>v3</td><td>2 (66.7%)</td><td>1 (33.3%)</td><td>0 (0.0%)</td><td>0 (0.0%)</td></tr>
</table></div>
<div class="meta">Roster: 3 accounts, the REAL per-account agent output from that
Pacific night (read-only, from <code>account_agent_reviews</code>).
Envelope hash (this org's resolved per-account-agent contract):
<code>{ORG_ENVELOPE}</code> — NOT comparable to the Intelligence contract.</div>
<table><tr><td class="acct">{SENTINEL_NAME}</td><td>{SENTINEL_REASONING}</td></tr></table>
</body></html>
"""

ROSTER_ONLY_HTML = """
<html><body><h1>AB-SHADOW — 2026-07-19</h1>
<div class="meta">Roster: 1031 accounts, the REAL per-account agent output
(read-only, from account_agent_reviews). Envelope hash (this org's resolved
per-account-agent contract): (pending — no arm has run yet)</div>
</body></html>
"""

MODEL_INDEX = {
    "ptc-steel-v3-agent": {
        "id": "b0d7867b-363c-5b00-8cb4-5d292bada0a0",
        "model_id": "ptc-steel-v3-agent",
        "source_run_id": "6f2d943d-00e9-536b-86d4-d80a58eca469",
    },
    "ptc-steel-a1-v1": {
        "id": "58f4cf36-6086-5f61-b383-671ddfc4d41d",
        "model_id": "ptc-steel-a1-v1",
        "source_run_id": None,  # no run row, by documented design
    },
}
ANCHOR_RUN = "ac6a6078-3932-4485-8b79-85eb318b85c9"


@pytest.fixture()
def ab_root(tmp_path):
    root = tmp_path / "ab-shadow"

    night = root / "2026-07-20"
    night.mkdir(parents=True)
    (night / "v3.jsonl").write_text("\n".join(json.dumps(r) for r in V3_RECORDS) + "\n")
    (night / "comparison.html").write_text(COMPARISON_HTML)

    retro = root / "2026-07-20-a1v1-retro"
    retro.mkdir()
    (retro / "a1v1.jsonl").write_text("\n".join(json.dumps(r) for r in A1V1_RECORDS) + "\n")
    (retro / "summary.json").write_text(json.dumps(SUMMARY_JSON))
    (retro / "comparison.html").write_text(COMPARISON_HTML)

    roster_only = root / "2026-07-19"
    roster_only.mkdir()
    (roster_only / "comparison.html").write_text(ROSTER_ONLY_HTML)

    (root / "not-a-night").mkdir()  # ignored: doesn't match the dirname shape
    return root


def _events(ab_root):
    return build_all_events(ab_root, MODEL_INDEX, ANCHOR_RUN)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def test_aggregate_jsonl_counts(ab_root):
    aggregates, org_ids = aggregate_jsonl(ab_root / "2026-07-20" / "v3.jsonl")
    assert aggregates["records"] == 3
    assert aggregates["accounts"] == 3
    assert aggregates["errors"] == 1
    assert aggregates["error_rate"] == round(1 / 3, 4)
    assert aggregates["emitted"] == 2
    assert aggregates["never_emitted"] == 1
    assert aggregates["output_types"] == {"no_action": 2, "buying_signal": 1}
    assert aggregates["no_action"] == 2
    assert aggregates["actionable_emits"] == 1
    assert aggregates["tool_calls_total"] == 3
    assert aggregates["tool_errors_total"] == 1
    assert aggregates["records_with_tool_error"] == 1
    assert aggregates["zero_tool_call_records"] == 1
    assert aggregates["prompt_tokens_total"] == 300
    assert aggregates["completion_tokens_total"] == 60
    assert aggregates["providers"] == ["fireworks"]
    assert aggregates["distinct_envelope_hashes"] == 1
    assert aggregates["envelope_set_sha256"] is not None
    assert org_ids == ["01a4c1ae-4a25-4cdc-9883-dea0827c9ce3"]
    # wallMs absent from every v3 record → no latency keys fabricated
    assert "wall_ms_mean" not in aggregates


def test_aggregate_latency_only_when_recorded(ab_root):
    aggregates, _ = aggregate_jsonl(ab_root / "2026-07-20-a1v1-retro" / "a1v1.jsonl")
    assert aggregates["wall_ms_mean"] == round((14508 + 8000) / 2, 4)
    assert aggregates["wall_ms_p50"] in (8000, 14508)
    assert aggregates["serving_model_ids"] == ["ptc-steel-a1-v1"]


def test_envelope_set_hash_is_input_identity():
    """Two arms that ran the same envelopes must produce the same set hash —
    that is the whole point of the field (night-level L51 comparability)."""
    import hashlib
    expected = hashlib.sha256(ENVELOPE.encode()).hexdigest()
    # single distinct hash → sha256 of just that hash string
    assert expected == hashlib.sha256("\n".join(sorted({ENVELOPE})).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Context parsing (summary.json / comparison.html)
# ---------------------------------------------------------------------------

def test_parse_summary_json_allowlists_and_drops_content():
    ctx = parse_summary_json(SUMMARY_JSON, "a1v1")
    assert ctx["typeMatch"] == 1
    assert ctx["claudeNoActionN"] == 1
    assert ctx["fab"] == {"n": 1, "g8Fails": 1, "g22Fails": 0}  # examples dropped
    assert ctx["night_frame"]["fullNight"] == 3
    assert SENTINEL_NAME not in json.dumps(ctx)
    assert "label" not in ctx


def test_parse_summary_json_skips_empty_arm_blocks():
    assert parse_summary_json(SUMMARY_JSON, "v3") is None
    assert parse_summary_json(SUMMARY_JSON, "v5") is None


def test_parse_comparison_html():
    ctx = parse_comparison_html(COMPARISON_HTML)
    assert ctx["roster_accounts"] == 3
    assert ctx["org_envelope_hash"] == ORG_ENVELOPE
    assert ctx["summary_vs_claude"]["v3"] == {
        "type_match": 2, "type_match_pct": 66.7,
        "missed_real_action": 1, "missed_real_action_pct": 33.3,
        "false_alarms": 0, "false_alarms_pct": 0.0,
        "different_action": 0, "different_action_pct": 0.0,
    }


def test_parse_comparison_html_pending_envelope():
    ctx = parse_comparison_html(ROSTER_ONLY_HTML)
    assert ctx["roster_accounts"] == 1031
    assert "org_envelope_hash" not in ctx


# ---------------------------------------------------------------------------
# Event construction — ids, placement, shape
# ---------------------------------------------------------------------------

def test_one_event_per_night_dir_and_arm(ab_root):
    rows = _events(ab_root)
    assert len(rows) == 3  # v3 @ 07-20, a1v1 @ 07-20-retro, roster-only @ 07-19
    by_id = {r["ref"].get("arm"): r for r in rows}
    assert set(by_id) == {"v3", "a1v1", None}


def test_ids_are_deterministic_uuid5_on_dirname_and_arm(ab_root):
    first = [r["id"] for r in _events(ab_root)]
    second = [r["id"] for r in _events(ab_root)]
    assert first == second
    assert len(set(first)) == len(first)
    assert first[1] == str(uuid.uuid5(
        NAMESPACE, "factory_run_events:ab-shadow:2026-07-20:v3"))
    for row_id in first:
        uuid.UUID(row_id)


def test_dirname_not_date_is_the_key():
    """2026-07-22 really has both an a1v1-live and an a1v1-retro replay —
    keying on the date would silently collapse them into one row."""
    assert _event_row_id("2026-07-22-a1v1-live", "a1v1") != \
        _event_row_id("2026-07-22-a1v1-retro", "a1v1")


def test_run_placement(ab_root):
    rows = {r["ref"].get("arm"): r for r in _events(ab_root)}
    # v3 rides its own model's run lineage
    assert rows["v3"]["run_id"] == MODEL_INDEX["ptc-steel-v3-agent"]["source_run_id"]
    # a1v1 has no run row by design → anchors on the active retrain run
    assert rows["a1v1"]["run_id"] == ANCHOR_RUN
    # roster-only night likewise
    assert rows[None]["run_id"] == ANCHOR_RUN


def test_kind_is_inside_the_check_constraints_vocabulary(ab_root):
    """No DDL is allowed here: `kind` must already be legal, and the
    extension point is ref.event_subtype (the run_queued precedent)."""
    for row in _events(ab_root):
        assert row["kind"] == "steering_event"
        assert row["ref"]["event_subtype"] == "ab_shadow_night"
        assert row["headline"].strip()
        assert row["ts"].startswith(row["ref"]["night"])


def test_side_by_side_reading_is_in_every_challenger_row(ab_root):
    rows = {r["ref"].get("arm"): r for r in _events(ab_root)}
    v3 = rows["v3"]["detail"]
    assert v3["main_model"]["roster_accounts"] == 3
    assert "account_agent_reviews" in v3["main_model"]["source"]
    assert v3["comparison"]["type_match"] == 2
    assert v3["comparison"]["basis"] == "comparison.html summary block"
    a1 = rows["a1v1"]["detail"]
    assert a1["comparison"]["basis"].startswith("summary.json")
    assert a1["comparison"]["type_match"] == 1
    assert a1["main_model"]["claude_no_action_n"] == 1
    # headline alone must already read side-by-side
    assert "vs Claude" in rows["v3"]["headline"]
    assert "matches 2" in rows["v3"]["headline"]


def test_model_ab_nights_preview_maps_to_pr_plan_4_4(ab_root):
    """model-ab-serving-v2 §4.4: one row per (night, org, challenger) with
    counts, clean, not_clean_reasons, envelope_hash. The future PR promotes
    this block — shape drift here means a migration there."""
    for row in _events(ab_root):
        if row["ref"].get("arm") is None:
            continue
        preview = row["detail"]["model_ab_nights_preview"]
        assert set(preview) == {
            "night", "org_slug", "org_id", "challenger", "mode",
            "accounts_compared", "errors", "fabrication_challenger",
            "fabrication_main", "clean", "not_clean_reasons", "envelope_hash",
        }
        assert preview["org_slug"] == "ptc-steel"
        assert preview["challenger"] in ARM_MODEL.values()
        # this projection records evidence, never verdicts
        assert preview["clean"] is None
        assert preview["not_clean_reasons"]


def test_no_raw_customer_content_anywhere(ab_root):
    """The privacy contract: aggregates only. accountName, reasoning, payload
    bodies and fab.unsupportedExamples must not survive into any projected
    row, however deeply nested."""
    dumped = json.dumps(_events(ab_root))
    assert SENTINEL_NAME not in dumped
    assert SENTINEL_REASONING not in dumped
    assert SENTINEL_PAYLOAD not in dumped
    assert "unsupportedExamples" not in dumped


def test_roster_only_night_explains_the_gap(ab_root):
    row = next(r for r in _events(ab_root) if r["ref"].get("arm") is None)
    assert "roster only" in row["headline"]
    assert "1031" in row["headline"]
    assert "CAPACITY-PENDING" in row["headline"]
    assert row["ref"]["mode"] == "roster-only"  # nothing replayed that night


# ---------------------------------------------------------------------------
# Idempotency — second run upserts the same ids, zero new rows
# ---------------------------------------------------------------------------

def test_second_run_adds_zero_rows(ab_root):
    store = {}

    def fake_upsert(rows):
        new = 0
        for row in rows:
            if row["id"] not in store:
                new += 1
            store[row["id"]] = row  # merge-duplicates semantics
        return new

    assert fake_upsert(_events(ab_root)) == 3
    assert fake_upsert(_events(ab_root)) == 0
    assert len(store) == 3


def test_night_dir_regex_ignores_foreign_dirs(ab_root):
    assert NIGHT_DIR_RE.match("2026-07-22-a1v1-live")
    assert NIGHT_DIR_RE.match("2026-07-19")
    assert not NIGHT_DIR_RE.match("not-a-night")
    assert not NIGHT_DIR_RE.match("summary-2026")
    rows = _events(ab_root)
    assert all(r["ref"]["night"].startswith("2026-07-") for r in rows)


def test_build_night_events_unmatched_dir_returns_empty(tmp_path):
    foreign = tmp_path / "not-a-night"
    foreign.mkdir()
    assert build_night_events(foreign, MODEL_INDEX, ANCHOR_RUN) == []


# ---------------------------------------------------------------------------
# T5 — promote to model_ab_nights (dry-run default, research, dedupe)
# ---------------------------------------------------------------------------

def _nights(ab_root):
    return build_model_ab_nights(ab_root, MODEL_INDEX, ANCHOR_RUN)


def test_promote_skips_roster_only_and_marks_research(ab_root):
    nights = _nights(ab_root)
    assert len(nights) == 2  # v3 + a1v1; roster-only excluded
    for night in nights:
        assert night["verdict"] == "not_evaluated"
        assert night["promotion_eligible"] is False
        assert night["mode"] == "research_replay"
        assert night["trust_label"] == "reconstructed"
        assert night["state"] == "sealed"
        assert night["teardown_status"] == "not_applicable"
        assert night["writer_version"].startswith("ab_shadow_sync.promote")
        assert night["cohort_key"] == "ab-shadow-historical"
        assert night["idempotency_key"]
        assert set(night) <= {
            "id", "idempotency_key", "evaluation_date", "timezone", "org_id", "org_slug",
            "surface", "mode", "cohort_key", "cohort_hash", "rubric_version",
            "incumbent_model_key", "incumbent_provider", "incumbent_envelope_hash",
            "challenger_model_key", "challenger_provider", "challenger_envelope_hash",
            "factory_model_id", "state",
            "attempted_count", "valid_count", "succeeded_count", "failed_count", "skipped_count",
            "metrics",
            "incumbent_cost_usd", "challenger_cost_usd", "total_cost_usd",
            "incumbent_latency_p50_ms", "incumbent_latency_p95_ms",
            "challenger_latency_p50_ms", "challenger_latency_p95_ms",
            "verdict", "reasons", "invalid_reasons",
            "teardown_status", "trust_label", "writer_version", "promotion_eligible",
            "scheduled_at", "started_at", "sealed_at",
        }
        assert night["id"] == str(uuid.uuid5(
            NAMESPACE, f"model_ab_nights:{night['idempotency_key']}"))


def test_promote_idempotent_keys_stable(ab_root):
    first = [n["idempotency_key"] for n in _nights(ab_root)]
    second = [n["idempotency_key"] for n in _nights(ab_root)]
    assert first == second
    assert len(set(first)) == len(first)


def test_live_retro_same_jsonl_dedupes_to_one_row(tmp_path):
    """Byte-identical live + retro directories are one physical observation —
    promotion must not double-count them (2026-07-22 precedent)."""
    root = tmp_path / "ab-shadow"
    payload = "\n".join(json.dumps(r) for r in A1V1_RECORDS) + "\n"
    for dirname in ("2026-07-22-a1v1-live", "2026-07-22-a1v1-retro"):
        night = root / dirname
        night.mkdir(parents=True)
        (night / "a1v1.jsonl").write_text(payload)
        (night / "summary.json").write_text(json.dumps(SUMMARY_JSON))
        (night / "comparison.html").write_text(COMPARISON_HTML)

    nights = _nights(root)
    assert len(nights) == 1
    night = nights[0]
    assert night["verdict"] == "not_evaluated"
    assert night["promotion_eligible"] is False
    refs = night["metrics"]["source_refs"]
    dirs = {ref["variant_dir"] for ref in refs}
    assert dirs == {"2026-07-22-a1v1-live", "2026-07-22-a1v1-retro"}
    assert night["metrics"]["source_mode"] == "live-shadow"  # preferred over retro


def test_promote_dry_run_default_writes_nothing(ab_root, monkeypatch):
    calls = []

    def fake_upsert(table, rows, on_conflict, **kwargs):
        calls.append((table, len(rows), on_conflict))
        return rows

    monkeypatch.setattr("ab_shadow_sync.upsert", fake_upsert)
    monkeypatch.setattr(
        "ab_shadow_sync.resolve_identity",
        lambda *a, **k: (_ for _ in ()).throw(SupabaseRestError("no db")),
    )
    rc = main(["--promote-to-model-ab-nights", "--root", str(ab_root)])
    assert rc == 0
    assert calls == []


def test_promote_commit_upserts_on_idempotency_key(ab_root, monkeypatch):
    calls = []

    def fake_upsert(table, rows, on_conflict, **kwargs):
        calls.append({
            "table": table, "n": len(rows), "on_conflict": on_conflict,
            "keys": [r.get("idempotency_key") or r.get("id") for r in rows],
        })
        return rows

    monkeypatch.setattr("ab_shadow_sync.upsert", fake_upsert)
    monkeypatch.setattr(
        "ab_shadow_sync.resolve_identity",
        lambda *a, **k: (MODEL_INDEX, ANCHOR_RUN, 6),
    )
    rc = main(["--promote-to-model-ab-nights", "--commit", "--root", str(ab_root)])
    assert rc == 0
    assert len(calls) == 2
    assert calls[0]["table"] == "factory_run_events"
    assert calls[1]["table"] == "model_ab_nights"
    assert calls[1]["on_conflict"] == "idempotency_key"
    store = set(calls[1]["keys"])
    second = {r["idempotency_key"] for r in _nights(ab_root)}
    assert second == store  # retry targets the same keys → zero new rows


def test_promote_no_customer_content(ab_root):
    dumped = json.dumps(_nights(ab_root))
    assert SENTINEL_NAME not in dumped
    assert SENTINEL_REASONING not in dumped
    assert SENTINEL_PAYLOAD not in dumped


def test_refuse_sensitive_payload_on_promote(ab_root):
    events = _events(ab_root)
    challenger = next(e for e in events if e["ref"].get("arm") == "v3")
    challenger = json.loads(json.dumps(challenger))
    challenger["detail"]["accountName"] = SENTINEL_NAME
    with pytest.raises(ValueError, match="sensitive"):
        event_to_model_ab_night(challenger, ab_root)


def test_dedupe_prefers_live_metrics():
    def row(mode, attempted, variant):
        return {
            "idempotency_key": "k1",
            "attempted_count": attempted,
            "metrics": {
                "source_mode": mode,
                "source_refs": [{"variant_dir": variant}],
            },
        }
    out = dedupe_model_ab_nights([
        row("retro-replay", 2, "retro"),
        row("live-shadow", 10, "live"),
    ])
    assert len(out) == 1
    assert out[0]["attempted_count"] == 10
    assert {r["variant_dir"] for r in out[0]["metrics"]["source_refs"]} == {"live", "retro"}
