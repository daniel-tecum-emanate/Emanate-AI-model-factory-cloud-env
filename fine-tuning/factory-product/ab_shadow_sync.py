#!/usr/bin/env python3
"""ab_shadow_sync — project nightly A/B shadow results into the Model Factory tab.

**Why this exists (MF-PTC-RETRAIN-EXEC, 2026-07-30).** Daniel: the A/B
comparison results — fine-tuned models vs the main production model (Claude) —
must be visible SIDE BY SIDE in the Model Factory tab. Today those results
live only in gitignored JSONLs and static HTML under
`platform-alpha/finetune-out/ab-shadow/<night>/` (per-account records for the
v3 / v5 / a1v1 shadow arms, `summary.json` where the a1v1 driver wrote one,
`comparison.html` always). Nothing in the product DB knows they happened.

**What it writes — and deliberately nothing else.** One `factory_run_events`
row per (night-directory, arm), `kind='steering_event'` with
`ref.event_subtype='ab_shadow_night'`:

  - `kind` must come from the table's closed CHECK vocabulary (13 values; no
    DDL is allowed here), and the established extension pattern is exactly
    this one — platform-alpha's `startFactoryRun` extends `steering_event`
    via `ref.event_subtype: "run_queued"` rather than adding a kind
    (data-model/factory_run_events.md). An A/B night verdict steers the
    S8/S9 promotion decision, so the semantic fits.
  - `headline` is the guaranteed-rendered surface (the Timeline renders
    headline for every kind), so each headline carries the side-by-side
    reading on its own: arm counts vs the Claude roster/comparison numbers.
  - `detail` carries the full per-arm aggregate + paired main-model context +
    a `model_ab_nights_preview` block shaped like model-ab-serving-v2
    PR-PLAN §4.4's future first-class table, so that PR promotes this data
    instead of migrating it.
  - NOT `factory_run_digests` (UNIQUE(run_id, iteration) — a real iteration
    digest for the same run would collide with anything parked there) and
    NOT `factory_models.report` (owned by models_backfill.py; re-running
    that script would clobber anything appended).

**Run placement.** Events attach to the arm's model lineage: v3 → the
iteration-3 run, v5 → the iteration-5 run (via `factory_models.source_run_id`,
resolved at runtime — never hardcoded uuids). `ptc-steel-a1-v1` has NO run row
by documented design (models_backfill.py: "different lanes entirely"), and
`run_id` is NOT NULL here — so a1v1 nights (and the roster-only night) anchor
on the newest ptc-steel run (iteration 6, the active retrain), which is the
run whose S9 ship decision consumes exactly this clean-night evidence
(PR-PLAN §8, V-036).

**The main-model arm.** Claude's side is the real production roster from
`account_agent_reviews` (production — the local DB's copy is empty). Its
per-night aggregate context is read from what the A/B driver already
extracted from it: `summary.json`'s claude* counts where present (a1v1
nights), else `comparison.html`'s roster/summary block (v3/v5 nights).

**Privacy: aggregates only.** Counts, rates, hashes and model/tool
identifiers. accountName / reasoning / payload / drafts /
fab.unsupportedExamples (contains account names) never leave the JSONLs.

**Idempotency.** Row ids are uuid5 from backfill_ptc_history's NAMESPACE
(imported, not recomputed), keyed on the night DIRECTORY name + arm — the
directory (not the date) because 2026-07-22 legitimately has both an
a1v1-live and an a1v1-retro replay of the same night. Upsert on id; a second
run rewrites the same rows and adds zero.

Usage (from fine-tuning/factory-product/):
    export FACTORY_SUPABASE_URL=http://127.0.0.1:54321
    export FACTORY_SUPABASE_SERVICE_ROLE_KEY=...
    python3 ab_shadow_sync.py            # dry-run (default): prints rows, writes nothing
    python3 ab_shadow_sync.py --commit   # upserts to factory_run_events
    python3 ab_shadow_sync.py --promote-to-model-ab-nights
                                         # dry-run: prints model_ab_nights rows, writes nothing
    python3 ab_shadow_sync.py --promote-to-model-ab-nights --commit
                                         # upserts model_ab_nights (and factory_run_events)

**T5 promotion (model-ab-unified-v1).** `--promote-to-model-ab-nights` maps challenger
event previews into `model_ab_nights` aggregate rows. Historical research/replay
imports always land as `verdict='not_evaluated'` and `promotion_eligible=false`.
Live-shadow vs retro-replay of the same physical observation (same night + arm +
JSONL content hash) collapse to one idempotency_key so promotion evidence is not
double-counted; both source dirs are retained in `source_refs`.
"""

import argparse
import hashlib
import json
import re
import sys
import uuid
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent            # fine-tuning/factory-product/
WORKFLOW_ROOT = HERE.parent.parent                # workflow repo root
DEFAULT_AB_ROOT = WORKFLOW_ROOT / "platform-alpha" / "finetune-out" / "ab-shadow"

sys.path.insert(0, str(HERE))

from backfill_ptc_history import NAMESPACE  # noqa: E402
from supabase_rest import SupabaseRestError, select, upsert  # noqa: E402

ORG_SLUG = "ptc-steel"
SURFACE = "per-account"
TIMEZONE = "America/Los_Angeles"
WRITER_VERSION = "ab_shadow_sync.promote.v1"
RUBRIC_VERSION = "historical-import-v1"
COHORT_KEY = "ab-shadow-historical"
INCUMBENT_MODEL_KEY = "claude-anthropic-live"
INCUMBENT_PROVIDER = "anthropic"
UNKNOWN_HASH = "unknown-historical"

# arm token in the JSONL filename / `arm` field → factory_models.model_id
ARM_MODEL = {
    "v3": "ptc-steel-v3-agent",
    "v5": "ptc-steel-v5-agent",
    "a1v1": "ptc-steel-a1-v1",
}

# Prefer live when collapsing byte-identical live/retro observations.
_MODE_RANK = {"live-shadow": 0, "shadow-replay": 1, "retro-replay": 2, "roster-only": 3}

NIGHT_DIR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(.+))?$")
_SENSITIVE_KEYS = frozenset({
    "accountName", "account_name", "reasoning", "payload", "draft_body",
    "prompt", "unsupportedExamples", "signedUrl", "signed_url", "apiKey", "api_key",
})
# Columns accepted by model_ab_nights (T3 migration) — never invent extras at top level.
_NIGHT_COLUMNS = frozenset({
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
})

# summary.json per-arm keys that are pure counts/rates — the allowlist IS the
# privacy boundary (fab.unsupportedExamples carries account names; label is
# free text; both excluded by not being here).
SUMMARY_ARM_KEYS = (
    "n", "uncontaminatedN", "contaminatedN", "payloadSignalN", "reasoningSignalN",
    "noActionRateUncontaminated", "claudeNoActionRateOnUncontaminated",
    "noActionRateContaminated", "claudeActionableN", "typeMatch",
    "flippedToNoAction", "differentAction", "neverEmitted", "falseAlarms",
    "claudeNoActionN", "actionableEmits", "zeroReadActionable",
    "terminals", "zeroReadAllTerminals",
)


def _event_row_id(dirname, arm):
    """Deterministic id per (night directory, arm). `arm` is 'roster' for the
    roster-only marker row of a night no challenger reached."""
    return str(uuid.uuid5(NAMESPACE, f"factory_run_events:ab-shadow:{dirname}:{arm}"))


def _mode_for(variant):
    if variant is None:
        return "shadow-replay"
    if variant.endswith("-live"):
        return "live-shadow"
    if variant.endswith("-retro"):
        return "retro-replay"
    return variant


def _pct(numerator, denominator):
    return round(numerator / denominator, 4) if denominator else None


def _percentile(sorted_values, fraction):
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, int(round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]


def aggregate_jsonl(path):
    """Stream one arm's JSONL and reduce it to counts/rates/hashes only —
    no record-level customer content survives this function."""
    records = errors = emitted = truncated = 0
    tool_calls_total = tool_errors_total = records_with_tool_error = 0
    zero_tool_call_records = 0
    max_parallel = 0
    iterations_total = 0
    prompt_tokens_total = completion_tokens_total = 0
    output_types = Counter()
    tool_names = Counter()
    accounts = set()
    envelope_hashes = set()
    providers = set()
    model_ids = set()
    org_ids = set()
    wall_ms = []
    importance = []

    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            records += 1
            if rec.get("error") is not None:
                errors += 1
            if rec.get("emitted"):
                emitted += 1
            if rec.get("truncated"):
                truncated += 1
            output_types[rec.get("outputType") or "(none)"] += 1
            calls = rec.get("toolCalls") or []
            tool_calls_total += len(calls)
            if not calls:
                zero_tool_call_records += 1
            call_errors = sum(1 for c in calls if c.get("isError"))
            tool_errors_total += call_errors
            if call_errors:
                records_with_tool_error += 1
            for call in calls:
                if call.get("name"):
                    tool_names[call["name"]] += 1
            max_parallel = max(max_parallel, rec.get("maxParallelCallsInATurn") or 0)
            iterations_total += rec.get("iterations") or 0
            prompt_tokens_total += rec.get("promptTokens") or 0
            completion_tokens_total += rec.get("completionTokens") or 0
            if rec.get("accountId"):
                accounts.add(rec["accountId"])
            if rec.get("envelopeHash"):
                envelope_hashes.add(rec["envelopeHash"])
            if rec.get("provider"):
                providers.add(rec["provider"])
            if rec.get("modelId"):
                model_ids.add(rec["modelId"])
            if rec.get("orgId"):
                org_ids.add(rec["orgId"])
            if rec.get("wallMs") is not None:
                wall_ms.append(rec["wallMs"])
            if rec.get("importanceScore") is not None:
                importance.append(rec["importanceScore"])

    no_action = output_types.get("no_action", 0)
    wall_sorted = sorted(wall_ms)
    importance_sorted = sorted(importance)
    aggregates = {
        "records": records,
        "accounts": len(accounts),
        "errors": errors,
        "error_rate": _pct(errors, records),
        "emitted": emitted,
        "never_emitted": records - emitted,
        "output_types": dict(output_types.most_common()),
        "no_action": no_action,
        "no_action_rate": _pct(no_action, records),
        "actionable_emits": sum(
            count for output_type, count in output_types.items()
            if output_type not in ("no_action", "(none)")
        ),
        "tool_calls_total": tool_calls_total,
        "tool_errors_total": tool_errors_total,
        "records_with_tool_error": records_with_tool_error,
        "zero_tool_call_records": zero_tool_call_records,
        "zero_tool_call_rate": _pct(zero_tool_call_records, records),
        "max_parallel_calls_in_a_turn": max_parallel,
        "iterations_mean": _pct(iterations_total, records),
        "truncated_records": truncated,
        "top_tools": tool_names.most_common(5),
        "prompt_tokens_total": prompt_tokens_total,
        "prompt_tokens_mean": _pct(prompt_tokens_total, records),
        "completion_tokens_total": completion_tokens_total,
        "completion_tokens_mean": _pct(completion_tokens_total, records),
        "importance_p50": _percentile(importance_sorted, 0.5),
        "providers": sorted(providers),
        "serving_model_ids": sorted(model_ids),
        "distinct_envelope_hashes": len(envelope_hashes),
        # Same night + same envelope_set_sha256 across two arms == the arms
        # ran the same inputs (the L51 comparability check, night-level).
        "envelope_set_sha256": hashlib.sha256(
            "\n".join(sorted(envelope_hashes)).encode()
        ).hexdigest() if envelope_hashes else None,
    }
    if wall_sorted:
        aggregates["wall_ms_mean"] = _pct(sum(wall_sorted), len(wall_sorted))
        aggregates["wall_ms_p50"] = _percentile(wall_sorted, 0.5)
        aggregates["wall_ms_p95"] = _percentile(wall_sorted, 0.95)
    return aggregates, sorted(org_ids)


def parse_summary_json(raw, arm):
    """Allowlisted extraction of one arm's block from summary.json — the
    driver already computed the paired-vs-Claude numbers there."""
    block = raw.get(arm) or {}
    if not block or not block.get("n"):
        return None
    out = {key: block[key] for key in SUMMARY_ARM_KEYS if key in block}
    t19 = block.get("t19") or {}
    if t19:
        out["t19"] = {k: t19[k] for k in ("n", "completeRate", "emptyPayloads") if k in t19}
    fab = block.get("fab") or {}
    if fab:
        # unsupportedExamples deliberately dropped — carries account names.
        out["fab"] = {k: fab[k] for k in ("n", "g8Fails", "g22Fails") if k in fab}
    out["night_frame"] = {k: raw[k] for k in ("date", "frame", "fullNight", "isSample") if k in raw}
    return out


def parse_comparison_html(html):
    """Pull the roster size, the org envelope hash, and (where the builder
    emitted one) the per-arm 'Matches Claude' summary rows out of the static
    comparison page. Regex on known generated shapes, never a DOM walk."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    out = {}
    roster = re.search(r"Roster:\s*([\d,]+)\s*accounts", text)
    if roster:
        out["roster_accounts"] = int(roster.group(1).replace(",", ""))
    envelope = re.search(r"Envelope hash[^:]*:\s*([0-9a-f]{64})", text)
    if envelope:
        out["org_envelope_hash"] = envelope.group(1)
    if "Matches Claude" in text:
        rows = re.findall(
            r"\b(v3|v5|a1-?v1)\s+(\d+)\s+\(([\d.]+)%\)\s+(\d+)\s+\(([\d.]+)%\)"
            r"\s+(\d+)\s+\(([\d.]+)%\)\s+(\d+)\s+\(([\d.]+)%\)",
            text,
        )
        summary = {}
        for arm, tm, tm_pct, miss, miss_pct, fa, fa_pct, da, da_pct in rows:
            summary[arm.replace("a1-v1", "a1v1")] = {
                "type_match": int(tm), "type_match_pct": float(tm_pct),
                "missed_real_action": int(miss), "missed_real_action_pct": float(miss_pct),
                "false_alarms": int(fa), "false_alarms_pct": float(fa_pct),
                "different_action": int(da), "different_action_pct": float(da_pct),
            }
        if summary:
            out["summary_vs_claude"] = summary
    return out


def _comparison_block(arm, summary_ctx, html_ctx):
    """Normalize the arm-vs-Claude pairing numbers from whichever source this
    night has: summary.json (a1v1 driver) or the HTML summary block (v3/v5
    builder). Same four counts either way."""
    if summary_ctx:
        return {
            "basis": "summary.json (driver-computed, paired on claudeActionableN)",
            "type_match": summary_ctx.get("typeMatch"),
            "missed_real_action": summary_ctx.get("flippedToNoAction"),
            "different_action": summary_ctx.get("differentAction"),
            "false_alarms": summary_ctx.get("falseAlarms"),
            "never_emitted": summary_ctx.get("neverEmitted"),
            "claude_actionable_n": summary_ctx.get("claudeActionableN"),
            "claude_no_action_n": summary_ctx.get("claudeNoActionN"),
        }
    html_summary = (html_ctx or {}).get("summary_vs_claude", {}).get(arm)
    if html_summary:
        return dict({"basis": "comparison.html summary block"}, **html_summary)
    return None


def build_night_events(night_dir, model_index, anchor_run_id):
    """All event rows for one night directory. Pure — no I/O beyond reading
    the directory's own files; resolution inputs are passed in."""
    dirname = night_dir.name
    match = NIGHT_DIR_RE.match(dirname)
    if not match:
        return []
    night, variant = match.groups()
    mode = _mode_for(variant)
    ts = f"{night}T23:59:00-07:00"

    html_ctx = {}
    html_path = night_dir / "comparison.html"
    if html_path.exists():
        html_ctx = parse_comparison_html(html_path.read_text(encoding="utf-8"))

    summary_raw = None
    summary_path = night_dir / "summary.json"
    if summary_path.exists():
        summary_raw = json.loads(summary_path.read_text(encoding="utf-8"))

    rows = []
    jsonl_paths = sorted(night_dir.glob("*.jsonl"))

    if not jsonl_paths:
        # Roster-only night (e.g. 2026-07-19: CAPACITY-PENDING for every arm).
        # One marker row so the timeline explains the gap instead of hiding it.
        mode = "roster-only"
        roster = html_ctx.get("roster_accounts")
        rows.append({
            "id": _event_row_id(dirname, "roster"),
            "run_id": anchor_run_id,
            "ts": ts,
            "kind": "steering_event",
            "headline": (
                f"AB shadow {night} — roster only: {roster if roster is not None else '?'} accounts "
                f"(Claude live, from account_agent_reviews); no challenger arm ran (CAPACITY-PENDING)"
            ),
            "ref": {
                "event_subtype": "ab_shadow_night",
                "night": night, "variant_dir": dirname, "mode": mode, "arm": None,
            },
            "detail": {
                "night": night, "variant_dir": dirname, "mode": mode,
                "main_model": {
                    "model": "claude (anthropic live, production per-account agent)",
                    "roster_accounts": roster,
                    "source": "account_agent_reviews (production) via comparison.html meta",
                },
                "provenance": _provenance(night_dir, html_path if html_path.exists() else None, None, None),
            },
        })
        return rows

    for jsonl_path in jsonl_paths:
        arm = jsonl_path.stem
        model_id = ARM_MODEL.get(arm)
        model_row = model_index.get(model_id, {}) if model_id else {}
        run_id = model_row.get("source_run_id") or anchor_run_id
        aggregates, org_ids = aggregate_jsonl(jsonl_path)
        summary_ctx = parse_summary_json(summary_raw, arm) if summary_raw else None
        comparison = _comparison_block(arm, summary_ctx, html_ctx)
        roster = (
            (summary_ctx or {}).get("night_frame", {}).get("fullNight")
            or html_ctx.get("roster_accounts")
        )

        main_model = {
            "model": "claude (anthropic live, production per-account agent)",
            "roster_accounts": roster,
            "source": "account_agent_reviews (production) via "
                      + ("summary.json" if summary_ctx else "comparison.html meta"),
        }
        if summary_ctx:
            main_model["claude_no_action_n"] = summary_ctx.get("claudeNoActionN")
            main_model["claude_actionable_n"] = summary_ctx.get("claudeActionableN")
            main_model["claude_no_action_rate_on_uncontaminated"] = summary_ctx.get(
                "claudeNoActionRateOnUncontaminated"
            )

        headline = _headline(night, mode, arm, aggregates, roster, comparison)

        preview = {
            # PR-PLAN model-ab-serving-v2 §4.4 model_ab_nights: one row per
            # (night, org, challenger). `clean` stays null: the V-036 verdict
            # is computeNightlyGroundingVerdict's to make (live nights only),
            # and this projection records evidence, not verdicts.
            "night": night,
            "org_slug": ORG_SLUG,
            "org_id": org_ids[0] if org_ids else None,
            "challenger": model_id or arm,
            "mode": mode,
            "accounts_compared": aggregates["accounts"],
            "errors": aggregates["errors"],
            "fabrication_challenger": (
                (summary_ctx.get("fab", {}).get("g8Fails", 0)
                 + summary_ctx.get("fab", {}).get("g22Fails", 0))
                if summary_ctx and "fab" in summary_ctx else None
            ),
            "fabrication_main": None,
            "clean": None,
            "not_clean_reasons": [
                "verdict not computed by this projection (research replay evidence; "
                "V-036 counts live nights judged by computeNightlyGroundingVerdict)"
            ],
            "envelope_hash": html_ctx.get("org_envelope_hash"),
        }

        detail = {
            "night": night, "variant_dir": dirname, "mode": mode,
            "challenger": {
                "arm": arm,
                "model_id": model_id,
                "factory_model_id": model_row.get("id"),
                "serving_providers": aggregates["providers"],
            },
            "aggregates": aggregates,
            "main_model": main_model,
            "comparison": comparison,
            "envelope": {
                "org_envelope_hash": html_ctx.get("org_envelope_hash"),
                "distinct_envelope_hashes": aggregates["distinct_envelope_hashes"],
                "envelope_set_sha256": aggregates["envelope_set_sha256"],
            },
            "summary_context": summary_ctx,
            "model_ab_nights_preview": preview,
            "provenance": _provenance(
                night_dir,
                html_path if html_path.exists() else None,
                summary_path if summary_raw else None,
                jsonl_path,
            ),
        }

        rows.append({
            "id": _event_row_id(dirname, arm),
            "run_id": run_id,
            "ts": ts,
            "kind": "steering_event",
            "headline": headline,
            "ref": {
                "event_subtype": "ab_shadow_night",
                "night": night, "variant_dir": dirname, "mode": mode,
                "arm": arm, "model_id": model_id,
                "factory_model_id": model_row.get("id"),
            },
            "detail": detail,
        })
    return rows


def _provenance(night_dir, html_path, summary_path, jsonl_path):
    def rel(p):
        try:
            return str(p.relative_to(WORKFLOW_ROOT))
        except ValueError:
            return str(p)
    sources = [rel(p) for p in (jsonl_path, summary_path, html_path) if p]
    return {
        "projected_by": "fine-tuning/factory-product/ab_shadow_sync.py",
        "sources": sources,
        "night_dir": rel(night_dir),
    }


def _headline(night, mode, arm, aggregates, roster, comparison):
    roster_part = f"Claude roster {roster}" if roster is not None else "Claude roster n/a"
    if comparison and comparison.get("type_match") is not None:
        return (
            f"AB shadow {night} [{mode}] — {arm} vs Claude: "
            f"{aggregates['records']} replayed / {roster_part}; "
            f"matches {comparison['type_match']}, "
            f"missed real action {comparison['missed_real_action']}, "
            f"false alarms {comparison['false_alarms']}, "
            f"different {comparison['different_action']}; "
            f"errors {aggregates['errors']}"
        )
    no_action_rate = aggregates["no_action_rate"]
    zero_tool_rate = aggregates["zero_tool_call_rate"]
    return (
        f"AB shadow {night} [{mode}] — {arm} vs {roster_part}: "
        f"{aggregates['records']} accounts, "
        f"no_action {round(no_action_rate * 100, 1) if no_action_rate is not None else '?'}%, "
        f"zero-tool-call {round(zero_tool_rate * 100, 1) if zero_tool_rate is not None else '?'}%, "
        f"tool errors {aggregates['tool_errors_total']}, "
        f"errors {aggregates['errors']}"
    )


def build_all_events(ab_root, model_index, anchor_run_id):
    rows = []
    for night_dir in sorted(Path(ab_root).iterdir()):
        if night_dir.is_dir() and NIGHT_DIR_RE.match(night_dir.name):
            rows.extend(build_night_events(night_dir, model_index, anchor_run_id))
    return rows


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nights_row_id(idempotency_key):
    return str(uuid.uuid5(NAMESPACE, f"model_ab_nights:{idempotency_key}"))


def _nights_idempotency_key(org_id, night, challenger, observation_hash):
    """Deterministic key for one physical observation — mode/variant_dir omitted so
    live-shadow + retro-replay of the same JSONL collapse to one upsert target."""
    material = "|".join([
        "model_ab_nights:v1", ORG_SLUG, org_id or "", SURFACE, night,
        challenger or "", observation_hash or "",
    ])
    return hashlib.sha256(material.encode()).hexdigest()


def _refuse_sensitive(obj, path="$"):
    """Fail closed if a projected night row still carries customer-content keys."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in _SENSITIVE_KEYS:
                raise ValueError(f"refused sensitive key {key!r} at {path}")
            _refuse_sensitive(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            _refuse_sensitive(value, f"{path}[{index}]")


def event_to_model_ab_night(event_row, ab_root):
    """Map one challenger factory_run_events row → a model_ab_nights-shaped dict.

    Research/historical only: verdict=not_evaluated, promotion_eligible=false,
    mode=research_replay. Roster-only marker rows (no arm) are skipped.
    Shape matches platform migration `*_model_ab_nights.sql` (T3).
    """
    ref = event_row.get("ref") or {}
    arm = ref.get("arm")
    if arm is None:
        return None
    detail = event_row.get("detail") or {}
    _refuse_sensitive(detail)
    _refuse_sensitive(ref)
    preview = detail.get("model_ab_nights_preview") or {}
    aggregates = detail.get("aggregates") or {}
    challenger = preview.get("challenger") or ARM_MODEL.get(arm) or arm
    night = preview.get("night") or ref.get("night")
    if not night:
        raise ValueError(f"malformed ab_shadow event missing night: {event_row.get('id')}")
    org_id = preview.get("org_id")
    if not org_id:
        raise ValueError(f"malformed ab_shadow event missing org_id: {event_row.get('id')}")
    variant_dir = ref.get("variant_dir") or detail.get("variant_dir")
    source_mode = ref.get("mode") or detail.get("mode") or "research_replay"
    jsonl_name = f"{arm}.jsonl"
    jsonl_path = Path(ab_root) / variant_dir / jsonl_name if variant_dir else None
    if jsonl_path is None or not jsonl_path.is_file():
        raise ValueError(f"malformed night missing {jsonl_name} under {variant_dir!r}")
    observation_hash = _file_sha256(jsonl_path)
    cohort_hash = aggregates.get("envelope_set_sha256") or observation_hash
    envelope = preview.get("envelope_hash") or UNKNOWN_HASH
    idempotency_key = _nights_idempotency_key(org_id, night, challenger, observation_hash)
    providers = aggregates.get("providers") or []
    attempted = int(aggregates.get("records") or preview.get("accounts_compared") or 0)
    failed = int(aggregates.get("errors") or preview.get("errors") or 0)
    succeeded = int(aggregates.get("emitted") or 0)
    metrics = {
        "accounts_compared": preview.get("accounts_compared"),
        "errors": failed,
        "no_action_rate": aggregates.get("no_action_rate"),
        "actionable_emits": aggregates.get("actionable_emits"),
        "fabrication_challenger": preview.get("fabrication_challenger"),
        "fabrication_main": preview.get("fabrication_main"),
        "type_match": (detail.get("comparison") or {}).get("type_match"),
        "distinct_envelope_hashes": aggregates.get("distinct_envelope_hashes"),
        "source_mode": source_mode,
        "observation_hash": observation_hash,
        "source_refs": [{
            "variant_dir": variant_dir,
            "mode": source_mode,
            "event_id": event_row.get("id"),
            "jsonl": _rel_or_abs(jsonl_path),
        }],
    }
    row = {
        "id": _nights_row_id(idempotency_key),
        "idempotency_key": idempotency_key,
        "evaluation_date": night,
        "timezone": TIMEZONE,
        "org_id": org_id,
        "org_slug": ORG_SLUG,
        "surface": SURFACE,
        "mode": "research_replay",
        "cohort_key": COHORT_KEY,
        "cohort_hash": cohort_hash,
        "rubric_version": RUBRIC_VERSION,
        "incumbent_model_key": INCUMBENT_MODEL_KEY,
        "incumbent_provider": INCUMBENT_PROVIDER,
        "incumbent_envelope_hash": envelope,
        "challenger_model_key": challenger,
        "challenger_provider": providers[0] if providers else "unknown",
        "challenger_envelope_hash": envelope,
        "factory_model_id": ref.get("factory_model_id"),
        "state": "sealed",
        "attempted_count": attempted,
        "valid_count": max(0, attempted - failed),
        "succeeded_count": max(0, succeeded),
        "failed_count": failed,
        "skipped_count": 0,
        "metrics": metrics,
        "incumbent_cost_usd": None,
        "challenger_cost_usd": None,
        "total_cost_usd": None,
        "incumbent_latency_p50_ms": None,
        "incumbent_latency_p95_ms": None,
        "challenger_latency_p50_ms": aggregates.get("wall_ms_p50"),
        "challenger_latency_p95_ms": aggregates.get("wall_ms_p95"),
        "verdict": "not_evaluated",
        "reasons": [
            {"code": "historical_research_import"},
            {"code": "verdict_not_computed_by_live_writer"},
        ],
        "invalid_reasons": [],
        "teardown_status": "not_applicable",
        "trust_label": "reconstructed",
        "writer_version": WRITER_VERSION,
        "promotion_eligible": False,
        "sealed_at": f"{night}T23:59:00-07:00",
    }
    extra = set(row) - _NIGHT_COLUMNS
    if extra:
        raise ValueError(f"refused unknown model_ab_nights columns: {sorted(extra)}")
    _refuse_sensitive(row)
    return row


def _rel_or_abs(path):
    try:
        return str(path.resolve().relative_to(WORKFLOW_ROOT))
    except ValueError:
        return str(path)


def dedupe_model_ab_nights(rows):
    """Collapse live-shadow vs retro-replay of the same physical observation.

    Group key = idempotency_key (night + challenger + JSONL content hash). Prefer
    live-shadow metrics; merge source_refs from every duplicate into metrics.
    """
    groups = {}
    order = []
    for row in rows:
        key = row["idempotency_key"]
        if key not in groups:
            groups[key] = row
            order.append(key)
            continue
        kept = groups[key]
        kept_mode = (kept.get("metrics") or {}).get("source_mode")
        new_mode = (row.get("metrics") or {}).get("source_mode")
        kept_rank = _MODE_RANK.get(kept_mode, 99)
        new_rank = _MODE_RANK.get(new_mode, 99)
        if new_rank < kept_rank:
            preferred = dict(row)
            preferred["metrics"] = dict(row.get("metrics") or {})
            preferred["metrics"]["source_refs"] = _unique_source_refs(
                list((row.get("metrics") or {}).get("source_refs") or [])
                + list((kept.get("metrics") or {}).get("source_refs") or [])
            )
            groups[key] = preferred
        else:
            kept_metrics = dict(kept.get("metrics") or {})
            kept_metrics["source_refs"] = _unique_source_refs(
                list(kept_metrics.get("source_refs") or [])
                + list((row.get("metrics") or {}).get("source_refs") or [])
            )
            kept["metrics"] = kept_metrics
    return [groups[key] for key in order]


def _unique_source_refs(refs):
    seen = set()
    out = []
    for ref in refs:
        marker = (ref.get("variant_dir"), ref.get("jsonl"))
        if marker in seen:
            continue
        seen.add(marker)
        out.append(ref)
    return out


def build_model_ab_nights(ab_root, model_index, anchor_run_id):
    """All deduped model_ab_nights rows for an ab-shadow root (pure besides file reads)."""
    events = build_all_events(ab_root, model_index, anchor_run_id)
    nights = []
    for event in events:
        try:
            night = event_to_model_ab_night(event, ab_root)
        except ValueError as exc:
            raise ValueError(f"refused promotion row: {exc}") from exc
        if night is not None:
            nights.append(night)
    return dedupe_model_ab_nights(nights)


def resolve_identity(url, key):
    """Resolve model ids / source runs / the anchor run from the live DB —
    never hardcoded, so a re-seeded local DB still links correctly."""
    models = select(
        "factory_models",
        params={"org_slug": f"eq.{ORG_SLUG}", "select": "id,model_id,source_run_id"},
        url=url, service_role_key=key,
    )
    model_index = {m["model_id"]: m for m in models}
    runs = select(
        "factory_runs",
        params={
            "org_slug": f"eq.{ORG_SLUG}", "select": "id,iteration",
            "order": "iteration.desc", "limit": "1",
        },
        url=url, service_role_key=key,
    )
    if not runs:
        raise SupabaseRestError(f"no factory_runs rows for org {ORG_SLUG} — nothing to anchor on")
    return model_index, runs[0]["id"], runs[0]["iteration"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true",
                        help="write rows (default is dry-run; target depends on --promote-to-model-ab-nights)")
    parser.add_argument("--dry-run", action="store_true",
                        help="explicit no-write mode (the default; kept for symmetry with the backfills)")
    parser.add_argument("--promote-to-model-ab-nights", action="store_true",
                        help="project/upsert into model_ab_nights (still dry-run unless --commit)")
    parser.add_argument("--root", default=str(DEFAULT_AB_ROOT),
                        help="ab-shadow output root (tests point this at fixtures)")
    parser.add_argument("--url", default=None)
    parser.add_argument("--service-role-key", default=None)
    args = parser.parse_args(argv)
    write = args.commit and not args.dry_run

    try:
        model_index, anchor_run_id, anchor_iteration = resolve_identity(args.url, args.service_role_key)
        print(f"ab_shadow_sync: anchor run {anchor_run_id} (org {ORG_SLUG}, iteration {anchor_iteration}); "
              f"{len(model_index)} catalog models resolved")
    except SupabaseRestError as exc:
        if write:
            print(f"ab_shadow_sync: identity resolution FAILED — {exc}", file=sys.stderr)
            return 1
        print(f"ab_shadow_sync: identity unresolved ({exc}) — dry-run continues with placeholders")
        model_index, anchor_run_id = {}, "(unresolved-anchor-run)"

    rows = build_all_events(args.root, model_index, anchor_run_id)
    print(f"ab_shadow_sync: {len(rows)} event rows from {args.root}")
    for row in rows:
        print(f"  {row['id']}  run={row['run_id']}  {row['headline']}")

    night_rows = []
    if args.promote_to_model_ab_nights:
        night_rows = build_model_ab_nights(args.root, model_index, anchor_run_id)
        print(f"ab_shadow_sync: {len(night_rows)} model_ab_nights rows "
              f"(after live/retro dedupe; all not_evaluated)")
        for night in night_rows:
            refs = (night.get("metrics") or {}).get("source_refs") or []
            print(
                f"  {night['idempotency_key'][:12]}…  "
                f"{night['evaluation_date']} {night['challenger_model_key']}  "
                f"verdict={night['verdict']} eligible={night['promotion_eligible']}  "
                f"sources={len(refs)}"
            )

    if not write:
        print("\ndry-run: no writes performed (pass --commit to write).")
        return 0

    try:
        pushed = upsert("factory_run_events", rows, on_conflict="id",
                        url=args.url, service_role_key=args.service_role_key)
        print(f"pushed factory_run_events OK ({len(pushed)} rows upserted)")
        if args.promote_to_model_ab_nights:
            pushed_nights = upsert(
                "model_ab_nights", night_rows, on_conflict="idempotency_key",
                url=args.url, service_role_key=args.service_role_key,
            )
            print(f"pushed model_ab_nights OK ({len(pushed_nights)} rows upserted)")
    except SupabaseRestError as exc:
        print(f"ab_shadow_sync: PUSH FAILED — {exc}", file=sys.stderr)
        return 1

    print("ab_shadow_sync: done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
