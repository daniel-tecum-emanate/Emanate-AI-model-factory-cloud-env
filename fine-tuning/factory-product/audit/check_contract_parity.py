#!/usr/bin/env python3
"""check_contract_parity — A2 Contract-Parity Sentinel, deterministic core.

Catch #2 made permanent: BUILD-SPEC §2a declared the envelope "MATCHED, live"
while prod had deployed a newer agent (night 07-26: `query_account_orders`
tool + `_groundingGuardrail` stamp) — the check had validated a stale local
checkout against itself (CHAMPION-PROFILE §5; BUILD-SPEC §2b BLOCKING). The
lesson encoded as method: parity is derived from PRODUCTION BEHAVIOR, never
from a local reconstruction — a hash whose both sides derive from the same
repo can never see deployment drift.

Checks:
  PAR-ENV-01  served-envelope fingerprint: the observed tool-name set from
              the last N nights of `account_agent_reviews` (read-only
              PostgREST — the exact method CHAMPION-PROFILE used) diffed
              against the corpus envelope's tools array. A tool prod calls
              that the corpus doesn't offer = fail. Prod unreachable =>
              SUSPECT with a named reason — never pass.
  PAR-ACT-02  contract admissibility: observed prod output_types AND corpus
              gold output_types ⊆ the contract's enabled actions.
  PAR-EMIT-03 emit-shape lints from the live era (§2b/Δ6): archetype A/B
              only (D never), draft_subject/body empty, importance_score at
              the served value (0 since 07-17), rationale length band.
              No assembled corpus => suspect skip (a skipped check is not a
              passed check).
  PAR-AGE-04  staleness clock: the parity snapshot and the envelope pull
              each carry a pulled-at; older than the run's own S3 export =>
              suspect ("a goal you only verify once is an assumption with a
              timestamp").

Writes `runs/<slug>/audit/contract-snapshot.json` (tool set, output types,
payload stamp keys, pulled-at) — the artifact A1's admissibility keys on.
Aggregates/counts only; no customer content ever leaves the stream.

Prod creds (read-only SELECT): NEXT_PUBLIC_SUPABASE_URL +
SUPABASE_SERVICE_ROLE_KEY (the corpus/inputs convention), or PU + PK
(/tmp/pcreds.sh convention). Zero writes to prod, ever.

Standalone: python3 audit/check_contract_parity.py <slug> [--factory-root DIR]
"""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from audit.audit_lib import (  # noqa: E402
    RunContext, check_result, load_corpus_meta, load_json_or_none,
    make_finding, now_iso, verdict_from_findings,
)

AUDITOR = "parity"
CHECK_IDS = ("PAR-ENV-01", "PAR-ACT-02", "PAR-EMIT-03", "PAR-AGE-04")

OBSERVATION_WINDOW_DAYS = 5
MAX_ROWS = 4000
PAGE = 1000
# Winback gold-shape bands from the live era (CHAMPION-PROFILE §2.3/Δ6).
RATIONALE_MIN, RATIONALE_MAX = 200, 900
ALLOWED_ARCHETYPES = {"A", "B"}
SERVED_IMPORTANCE = 0


def _prod_creds():
    url = (os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
           or os.environ.get("PU"))
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
           or os.environ.get("PK"))
    return url, key


def fetch_observed(org_id, url=None, key=None, window_days=OBSERVATION_WINDOW_DAYS,
                   max_rows=MAX_ROWS):
    """Streaming reduction of recent prod reviews to a served-surface
    fingerprint: tool-name counts, payload stamp keys, output types. Raises
    on any transport/credential failure — the caller owns the degrade-to-
    suspect direction. Read-only SELECTs; content reduced in-stream."""
    from supabase_rest import select  # noqa: PLC0415 (factory dir on sys.path)
    if url is None or key is None:
        env_url, env_key = _prod_creds()
        url = url or env_url
        key = key or env_key
    if not url or not key:
        raise RuntimeError("no prod read credentials in process env "
                           "(NEXT_PUBLIC_SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY "
                           "or PU/PK)")
    since = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    tool_names = Counter()
    stamp_keys = Counter()
    output_types = Counter()
    nights = set()
    rows_scanned = 0
    offset = 0
    while rows_scanned < max_rows:
        page = select(
            "account_agent_reviews",
            params={
                "org_id": f"eq.{org_id}",
                "reviewed_at": f"gte.{since}",
                "select": "output_type,tool_calls,payload,reviewed_at",
                "order": "reviewed_at.desc",
                "limit": str(min(PAGE, max_rows - rows_scanned)),
                "offset": str(offset),
            },
            url=url, service_role_key=key, timeout=30,
        )
        if not page:
            break
        for row in page:
            rows_scanned += 1
            output_types[row.get("output_type") or "(none)"] += 1
            if row.get("reviewed_at"):
                nights.add(row["reviewed_at"][:10])
            for call in row.get("tool_calls") or []:
                name = call.get("name") if isinstance(call, dict) else None
                if name:
                    tool_names[name] += 1
            payload = row.get("payload")
            if isinstance(payload, dict):
                for k in payload:
                    stamp_keys[k] += 1
        offset += len(page)
        if len(page) < PAGE:
            break
    return {
        "org_id": org_id,
        "pulled_at": now_iso(),
        "basis": "live (account_agent_reviews, read-only PostgREST)",
        "window_days": window_days,
        "rows_scanned": rows_scanned,
        "nights_observed": sorted(nights),
        "observed_tool_names": dict(tool_names.most_common()),
        "payload_stamp_keys": dict(stamp_keys.most_common()),
        "output_types": dict(output_types.most_common()),
        "enabled_actions": None,  # behavioral fingerprint only; the settings
                                  # row is a separate read (Phase 1)
    }


def _get_observed(ctx, observed, notes):
    """observed fingerprint via, in order: injected (tests), live pull,
    cached snapshot (named as stale basis). None = nothing available."""
    if observed is not None:
        return observed
    envelope = load_json_or_none(ctx.inputs_dir / "envelope.json") or {}
    org_id = envelope.get("org_id")
    if org_id:
        try:
            observed = fetch_observed(org_id)
            ctx.audit_dir.mkdir(parents=True, exist_ok=True)
            (ctx.audit_dir / "contract-snapshot.json").write_text(
                json.dumps(observed, indent=2, sort_keys=True) + "\n")
            notes.append(f"live fingerprint: {observed['rows_scanned']} rows / "
                         f"{len(observed['nights_observed'])} nights")
            return observed
        except Exception as exc:  # transport, creds, schema — all degrade
            notes.append(f"live pull unavailable: {exc}")
    cached = load_json_or_none(ctx.audit_dir / "contract-snapshot.json")
    if cached:
        cached = dict(cached)
        cached["basis"] = (f"CACHED snapshot pulled {cached.get('pulled_at')} "
                           "— prod unreachable this run")
        notes.append("using cached contract-snapshot.json")
        return cached
    return None


def check_par_env_01(ctx, observed, notes):
    findings = []
    envelope = load_json_or_none(ctx.inputs_dir / "envelope.json")
    offered = set((envelope or {}).get("tool_names") or [])
    counts = {"offered_tools": len(offered)}

    if observed is None:
        reason = notes[-1] if notes else "no observation source"
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="PAR-ENV-01", klass="parity",
            severity="warn", verdict="suspect", stage_scope="S4",
            headline=("served-envelope fingerprint UNAVAILABLE "
                      f"({reason}) — parity NOT verified; a local checkout "
                      "cannot self-certify (L51/§2b), suspect never pass"),
            evidence=[ctx.rel(ctx.inputs_dir / "envelope.json"),
                      ctx.rel(ctx.audit_dir / "contract-snapshot.json")],
            counts={"observed_rows": 0}, lesson_refs=["L51"]))
        return check_result("PAR-ENV-01", "served-envelope fingerprint",
                            AUDITOR, "suspect", counts, findings, notes)

    counts["observed_rows"] = observed.get("rows_scanned", 0)
    if not envelope:
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="PAR-ENV-01", klass="parity",
            severity="warn", verdict="suspect", stage_scope="S4",
            headline=("no corpus envelope recorded (inputs/envelope.json "
                      "absent) — nothing to hold parity against"),
            evidence=[ctx.rel(ctx.inputs_dir / "envelope.json")],
            counts={"envelope_files_found": 0}))
        return check_result("PAR-ENV-01", "served-envelope fingerprint",
                            AUDITOR, "suspect", counts, findings, notes)
    if observed.get("rows_scanned", 0) == 0:
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="PAR-ENV-01", klass="parity",
            severity="warn", verdict="suspect", stage_scope="S4",
            headline=("served-surface pull returned 0 rows in the "
                      f"{observed.get('window_days')}d window — 0-row success "
                      "is a named failure (R4), parity unverified"),
            evidence=[ctx.rel(ctx.audit_dir / "contract-snapshot.json")],
            counts={"observed_rows": 0}))
        return check_result("PAR-ENV-01", "served-envelope fingerprint",
                            AUDITOR, "suspect", counts, findings, notes)

    observed_tools = observed.get("observed_tool_names") or {}
    unoffered = {t: n for t, n in observed_tools.items() if t not in offered}
    unobserved = sorted(offered - set(observed_tools))
    counts["observed_tools"] = len(observed_tools)
    counts["unoffered_tools_called_by_prod"] = len(unoffered)

    if unoffered:
        listing = ",".join(f"{t}(x{n})" for t, n in
                           sorted(unoffered.items(), key=lambda kv: -kv[1]))
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="PAR-ENV-01", klass="parity",
            severity="blocking", verdict="fail", stage_scope="S4",
            headline=(f"stale envelope/contract drift: production called "
                      f"{len(unoffered)} tool(s) the corpus envelope does not "
                      f"offer — {listing} over {observed.get('rows_scanned')} "
                      f"rows/{len(observed.get('nights_observed') or [])} nights; "
                      f"the recorded envelope hash cannot be the deployed one "
                      "(catch #2, L51 re-anchor required)"),
            evidence=[ctx.rel(ctx.inputs_dir / "envelope.json"),
                      ctx.rel(ctx.audit_dir / "contract-snapshot.json"),
                      "PRs/ptc-retrain-v6/CHAMPION-PROFILE.md#S5"],
            counts={"unoffered_tools": len(unoffered),
                    "unoffered_calls": sum(unoffered.values()),
                    "rows_observed": observed.get("rows_scanned", 0)},
            lesson_refs=["L51"]))
    if unobserved:
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="PAR-ENV-01", klass="parity",
            severity="info", verdict="info", stage_scope="S4",
            headline=(f"{len(unobserved)} offered tools unobserved in the "
                      f"window ({','.join(unobserved)}) — legitimate for "
                      "rare tools; recorded for the drift ledger, not a fail"),
            evidence=[ctx.rel(ctx.audit_dir / "contract-snapshot.json")],
            counts={"unobserved_tools": len(unobserved)}))

    return check_result("PAR-ENV-01", "served-envelope fingerprint", AUDITOR,
                        verdict_from_findings(findings), counts, findings, notes)


def check_par_act_02(ctx, observed):
    findings = []
    envelope = load_json_or_none(ctx.inputs_dir / "envelope.json") or {}
    allowed = set(envelope.get("allowed_actions") or [])
    counts = {"enabled_actions": len(allowed)}
    if not allowed:
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="PAR-ACT-02", klass="parity",
            severity="warn", verdict="suspect", stage_scope="S4",
            headline=("no enabled-actions snapshot available — contract "
                      "admissibility unverified, suspect"),
            evidence=[ctx.rel(ctx.inputs_dir / "envelope.json")],
            counts={"snapshots_found": 0}))
        return check_result("PAR-ACT-02", "contract admissibility", AUDITOR,
                            "suspect", counts, findings)

    if observed and observed.get("output_types"):
        observed_types = {t for t in observed["output_types"] if t != "(none)"}
        rogue = sorted(observed_types - allowed)
        counts["observed_output_types"] = len(observed_types)
        if rogue:
            findings.append(make_finding(
                ctx, auditor=AUDITOR, check_id="PAR-ACT-02", klass="parity",
                severity="blocking", verdict="fail", stage_scope="S4",
                headline=(f"production emitted output types outside the "
                          f"recorded contract: {','.join(rogue)} — the "
                          "enabled-actions snapshot is stale"),
                evidence=[ctx.rel(ctx.audit_dir / "contract-snapshot.json"),
                          ctx.rel(ctx.inputs_dir / "envelope.json")],
                counts={"rogue_output_types": len(rogue)}))

    metas = load_corpus_meta(ctx)
    if metas is None:
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="PAR-ACT-02", klass="parity",
            severity="info", verdict="info", stage_scope="S5",
            headline=("no assembled corpus yet — gold-class admissibility "
                      "re-checked at assemble (plan-level admissibility is "
                      "LIN-FILL-04's finding)"),
            evidence=[ctx.rel(ctx.build_dir)],
            counts={"corpus_records": 0}))
    else:
        gold = Counter(m.get("gold_class") for m in metas if m.get("gold_class"))
        rogue = sorted(set(gold) - allowed)
        counts["corpus_records"] = len(metas)
        if rogue:
            n = sum(gold[c] for c in rogue)
            findings.append(make_finding(
                ctx, auditor=AUDITOR, check_id="PAR-ACT-02", klass="parity",
                severity="blocking", verdict="fail", stage_scope="S5",
                headline=(f"{n} corpus gold records emit classes the live "
                          f"contract forbids ({','.join(rogue)}) — train/serve "
                          "contradiction (L51, catch #3)"),
                evidence=[ctx.rel(ctx.build_dir),
                          ctx.rel(ctx.inputs_dir / "envelope.json")],
                counts={"forbidden_gold_records": n,
                        "forbidden_classes": len(rogue)},
                lesson_refs=["L51"]))

    return check_result("PAR-ACT-02", "contract admissibility", AUDITOR,
                        verdict_from_findings(findings), counts, findings)


def check_par_emit_03(ctx):
    findings = []
    metas = load_corpus_meta(ctx)
    if metas is None:
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="PAR-EMIT-03", klass="parity",
            severity="warn", verdict="suspect", stage_scope="S5",
            headline=("emit-shape lints did not run: no assembled corpus "
                      "under corpus/build/ — a skipped check is not a passed "
                      "check"),
            evidence=[ctx.rel(ctx.build_dir)],
            counts={"corpus_records": 0}))
        return check_result("PAR-EMIT-03", "emit-shape lints (live era)",
                            AUDITOR, "suspect", {"corpus_records": 0}, findings)

    # Lint the rendered emit arguments in the record files themselves.
    bad_archetype = bad_draft = bad_importance = bad_rationale = 0
    records = 0
    for path in sorted(ctx.build_dir.glob("*.jsonl")):
        if path.name.endswith(".meta.jsonl"):
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            records += 1
            emit_args = None
            for msg in rec.get("messages", []):
                for tc in msg.get("tool_calls") or []:
                    if tc.get("function", {}).get("name") == "emit_output":
                        try:
                            emit_args = json.loads(tc["function"]["arguments"])
                        except (ValueError, TypeError):
                            emit_args = None
            if not isinstance(emit_args, dict):
                continue
            if emit_args.get("output_type") == "winback_enrollment":
                seq = (emit_args.get("payload") or {}).get("sequence") \
                    if isinstance(emit_args.get("payload"), dict) else None
                archetype = (seq or {}).get("archetype") or emit_args.get("archetype")
                if archetype is not None and archetype not in ALLOWED_ARCHETYPES:
                    bad_archetype += 1
                rationale = (seq or {}).get("rationale") or ""
                if rationale and not (RATIONALE_MIN <= len(rationale) <= RATIONALE_MAX):
                    bad_rationale += 1
            if (emit_args.get("draft_subject") or emit_args.get("draft_body")):
                bad_draft += 1
            imp = emit_args.get("importance_score")
            if imp is not None and imp != SERVED_IMPORTANCE:
                bad_importance += 1

    counts = {"corpus_records": records, "bad_archetype": bad_archetype,
              "nonempty_drafts": bad_draft, "importance_drift": bad_importance,
              "rationale_out_of_band": bad_rationale}
    violations = bad_archetype + bad_draft + bad_importance + bad_rationale
    if violations:
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="PAR-EMIT-03", klass="parity",
            severity="warn", verdict="fail", stage_scope="S5",
            headline=(f"{violations} emit-shape violations vs the live era "
                      f"(archetype {bad_archetype}, non-empty drafts "
                      f"{bad_draft}, importance!={SERVED_IMPORTANCE} "
                      f"{bad_importance}, rationale band {bad_rationale}) "
                      f"over {records} records (§2b/Δ6)"),
            evidence=[ctx.rel(ctx.build_dir),
                      "PRs/ptc-retrain-v6/CHAMPION-PROFILE.md#S2.3"],
            counts=counts))
    return check_result("PAR-EMIT-03", "emit-shape lints (live era)", AUDITOR,
                        verdict_from_findings(findings), counts, findings)


def _parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def check_par_age_04(ctx, observed):
    findings = []
    s3 = load_json_or_none(ctx.run_dir / "03-export-report.json") or {}
    s3_ts = _parse_ts(s3.get("generated_at"))
    envelope = load_json_or_none(ctx.inputs_dir / "envelope.json") or {}
    counts = {"snapshots_checked": 0}
    if s3_ts is None:
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="PAR-AGE-04", klass="parity",
            severity="info", verdict="info", stage_scope="S4",
            headline=("no S3 export report to clock parity snapshots against "
                      "— staleness clock idles until S3 runs"),
            evidence=[ctx.rel(ctx.run_dir / "03-export-report.json")],
            counts={"s3_reports_found": 0}))
        return check_result("PAR-AGE-04", "parity staleness clock", AUDITOR,
                            "pass", {"s3_reports_found": 0}, findings)

    for label, ts_value, pointer in (
        ("envelope pull", envelope.get("pulled_at"),
         ctx.rel(ctx.inputs_dir / "envelope.json")),
        ("contract snapshot", (observed or {}).get("pulled_at"),
         ctx.rel(ctx.audit_dir / "contract-snapshot.json")),
    ):
        ts = _parse_ts(ts_value)
        if ts is None:
            continue
        counts["snapshots_checked"] += 1
        if ts < s3_ts:
            age_h = round((s3_ts - ts).total_seconds() / 3600, 1)
            findings.append(make_finding(
                ctx, auditor=AUDITOR, check_id="PAR-AGE-04", klass="parity",
                severity="warn", verdict="suspect", stage_scope="S4",
                headline=(f"{label} predates the run's own S3 export by "
                          f"{age_h}h — a goal you only verify once is an "
                          "assumption with a timestamp; re-pull before render"),
                evidence=[pointer, ctx.rel(ctx.run_dir / "03-export-report.json")],
                counts={"stale_by_hours": age_h}))
    return check_result("PAR-AGE-04", "parity staleness clock", AUDITOR,
                        verdict_from_findings(findings), counts, findings)


def run(ctx, observed=None):
    """`observed` lets tests/other callers inject the served fingerprint;
    None means live pull, then cached snapshot, then suspect."""
    notes = []
    observed = _get_observed(ctx, observed, notes)
    return [check_par_env_01(ctx, observed, notes),
            check_par_act_02(ctx, observed),
            check_par_emit_03(ctx),
            check_par_age_04(ctx, observed)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug")
    parser.add_argument("--factory-root", default=None)
    args = parser.parse_args(argv)
    ctx = RunContext(args.slug, factory_root=args.factory_root)
    worst_exit = 0
    for result in run(ctx):
        print(f"[{result['verdict'].upper()}] {result['check_id']} — "
              f"{result['name']}; counts {result['counts']}")
        for f in result["findings"]:
            print(f"    {f['verdict'].upper()}: {f['headline']}")
        if result["verdict"] == "fail":
            worst_exit = 1
    return worst_exit


if __name__ == "__main__":
    sys.exit(main())
