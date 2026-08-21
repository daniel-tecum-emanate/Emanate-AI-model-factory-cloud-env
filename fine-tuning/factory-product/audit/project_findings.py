#!/usr/bin/env python3
"""project_findings — the blackboard's projection into the Model Factory tab.

The ab_shadow_sync.py CLI shape verbatim (DESIGN §5.2/§9): dry-run by
default, `--commit` (alias `--live`) to write; one `factory_run_events` row
per finding and one per hook verdict, `kind='steering_event'` with
`ref.event_subtype='audit_finding'` / `'audit_verdict'` — the closed CHECK
vocabulary untouched, the Timeline renders `headline` for every kind.

Idempotency: row ids are uuid5 from backfill_ptc_history's NAMESPACE
(imported, not recomputed) keyed on the finding id (itself uuid5 over
run/check/evidence-hash) or the verdict file name — a second run upserts the
same rows and adds zero.

Privacy: findings are counts/pointers/hashes by schema (§6.2 pointer
validation); `detail` carries the finding's own fields, never file contents.

Run placement: the run id from `runs/<slug>/.sync_state.json` (the local
map of record), falling back to the newest `factory_runs` row for the org —
never hardcoded uuids.

Usage (from fine-tuning/factory-product/):
    python3 audit/project_findings.py ptc-steel            # dry-run (default)
    python3 audit/project_findings.py ptc-steel --commit   # upsert rows
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from audit.audit_lib import RunContext, load_findings  # noqa: E402
from backfill_ptc_history import NAMESPACE  # noqa: E402
from supabase_rest import SupabaseRestError, select, upsert  # noqa: E402

EVENT_COLUMNS = ("id", "run_id", "ts", "kind", "headline", "ref", "detail")


def _finding_event_id(finding_id):
    return str(uuid.uuid5(NAMESPACE, f"factory_run_events:audit:{finding_id}"))


def _verdict_event_id(slug, verdict_filename):
    return str(uuid.uuid5(
        NAMESPACE, f"factory_run_events:audit_verdict:{slug}:{verdict_filename}"))


def resolve_run_id(ctx, url=None, key=None):
    """Local sync-state first (works offline), live factory_runs second."""
    if not ctx.run_id.startswith("unresolved-run:"):
        return ctx.run_id
    runs = select(
        "factory_runs",
        params={"org_slug": f"eq.{ctx.slug}", "select": "id,iteration",
                "order": "iteration.desc", "limit": "1"},
        url=url, service_role_key=key,
    )
    if not runs:
        raise SupabaseRestError(
            f"no factory_runs rows for org {ctx.slug} — nothing to anchor on")
    return runs[0]["id"]


def finding_row(ctx, run_id, finding):
    evidence_path = ctx.rel(ctx.audit_dir / "findings.jsonl")
    return {
        "id": _finding_event_id(finding["id"]),
        "run_id": run_id,
        "ts": finding["detected_at"],
        "kind": "steering_event",
        "headline": (f"AUDIT {finding['check_id']} "
                     f"{finding['verdict'].upper()} — {finding['headline']}"),
        "ref": {
            "event_subtype": "audit_finding",
            "check_id": finding["check_id"],
            "auditor": finding["auditor"],
            "severity": finding["severity"],
            "finding_id": finding["id"],
            "evidence_path": evidence_path,
        },
        "detail": {
            "class": finding["class"],
            "stage_scope": finding["stage_scope"],
            "verdict": finding["verdict"],
            "counts": finding["counts"],
            "evidence": finding["evidence"],
            "lesson_refs": finding["lesson_refs"],
            "detected_by": finding["detected_by"],
            "schema_version": finding["schema_version"],
        },
    }


def verdict_row(ctx, run_id, path, verdict_doc):
    checks = verdict_doc.get("checks", [])
    fails = sum(1 for c in checks if c["verdict"] == "fail")
    suspects = sum(1 for c in checks if c["verdict"] == "suspect")
    findings_n = sum(len(c.get("finding_ids", [])) for c in checks)
    ts_raw = verdict_doc.get("ts", "")
    ts = (f"{ts_raw[0:4]}-{ts_raw[4:6]}-{ts_raw[6:8]}T"
          f"{ts_raw[9:11]}:{ts_raw[11:13]}:{ts_raw[13:15]}+00:00"
          if len(ts_raw) == 16 else ts_raw)
    return {
        "id": _verdict_event_id(ctx.slug, path.name),
        "run_id": run_id,
        "ts": ts,
        "kind": "steering_event",
        "headline": (f"AUDIT verdict {verdict_doc['hook']} "
                     f"{verdict_doc['verdict'].upper()} — "
                     f"{len(checks)} checks ({fails} fail / {suspects} "
                     f"suspect), {findings_n} findings (audit_run.py)"),
        "ref": {
            "event_subtype": "audit_verdict",
            "hook": verdict_doc["hook"],
            "verdict": verdict_doc["verdict"],
            "evidence_path": ctx.rel(path),
        },
        "detail": {
            "checks": [{"check_id": c["check_id"], "verdict": c["verdict"],
                        "counts": c["counts"]} for c in checks],
            "machine_check": verdict_doc.get("machine_check"),
            "schema_version": verdict_doc.get("schema_version"),
        },
    }


def build_rows(ctx, run_id):
    rows = [finding_row(ctx, run_id, f) for f in load_findings(ctx.audit_dir)]
    verdict_dir = ctx.audit_dir / "verdicts"
    if verdict_dir.is_dir():
        for path in sorted(verdict_dir.glob("*.json")):
            try:
                doc = json.loads(path.read_text())
            except ValueError:
                continue
            rows.append(verdict_row(ctx, run_id, path, doc))
    for row in rows:
        assert set(row) == set(EVENT_COLUMNS), row  # schema guard
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug")
    parser.add_argument("--commit", "--live", action="store_true", dest="commit",
                        help="write to factory_run_events (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true",
                        help="explicit no-write mode (the default)")
    parser.add_argument("--factory-root", default=None)
    parser.add_argument("--url", default=None)
    parser.add_argument("--service-role-key", default=None)
    args = parser.parse_args(argv)
    write = args.commit and not args.dry_run

    ctx = RunContext(args.slug, factory_root=args.factory_root)
    try:
        run_id = resolve_run_id(ctx, url=args.url, key=args.service_role_key)
    except SupabaseRestError as exc:
        if write:
            print(f"project_findings: run resolution FAILED — {exc}",
                  file=sys.stderr)
            return 1
        print(f"project_findings: run unresolved ({exc}) — dry-run continues "
              "with the placeholder")
        run_id = ctx.run_id

    rows = build_rows(ctx, run_id)
    print(f"project_findings: {len(rows)} event rows from {ctx.audit_dir}")
    for row in rows:
        print(f"  {row['id']}  run={row['run_id']}  {row['headline'][:120]}")

    if not write:
        print("\ndry-run: no writes performed (pass --commit to write).")
        return 0

    try:
        pushed = upsert("factory_run_events", rows, on_conflict="id",
                        url=args.url, service_role_key=args.service_role_key)
        print(f"pushed factory_run_events OK ({len(pushed)} rows upserted)")
    except SupabaseRestError as exc:
        print(f"project_findings: PUSH FAILED — {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
