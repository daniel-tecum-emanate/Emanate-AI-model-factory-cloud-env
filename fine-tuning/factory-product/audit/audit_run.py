#!/usr/bin/env python3
"""audit_run — AG, the Auditor-General. A deterministic RUNNER, not a brain
(DESIGN.md §4 AG): it re-implements no check logic, never passes any
approval, and stops at real failures with per-check lines printed — the
aggregate banner never substitutes for them (the FA-11 lesson).

For the requested hook it: runs the deterministic checks registered for that
hook (serialized — never two corpus scans concurrently), merges their
findings into the per-run blackboard (`runs/<slug>/audit/findings.jsonl` +
`status.jsonl`, append-only), computes the hook-level aggregate by the
degradation lattice (any fail -> fail; any missing/suspect -> suspect, never
pass), and writes `verdicts/<hook>-<ts>.json`.

Exit codes: 1 when the aggregate is fail, else 0 — but counts, not exit
codes, are the signal (R4): every check line enumerates its own counts, and
a check that crashed or reported no counts is SUSPECT by construction.

Machine health: runs machine-health/check.sh first (warn-only here — these
scans are light JSON reads; the one-heavy-job rule targets tsc/build/vitest
class jobs). The BUSY verdict is recorded in the hook verdict file.

Usage (from fine-tuning/factory-product/):
    python3 audit/audit_run.py ptc-steel --hook pre-s5
"""

import argparse
import json
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from audit import check_contract_parity, check_fillability, check_org_lineage, compare_runs  # noqa: E402
from audit.audit_lib import (  # noqa: E402
    RunContext, WORKFLOW_ROOT, append_findings, append_statuses,
    latest_status_by_finding, load_findings, status_row, worst,
)

# Hook -> check modules (DESIGN §7.1 trigger map; contract parity also runs
# pre-S5 because the corpus render + A1's admissibility both key on it, and
# the render->launch window is where deploys land).
REGISTRY = {
    "pre-s4": [check_contract_parity],
    "pre-s5": [check_org_lineage, check_fillability, check_contract_parity],
    "pre-s6g": [compare_runs, check_contract_parity],
    "pre-s7": [check_contract_parity],
}


def machine_check():
    script = WORKFLOW_ROOT / "machine-health" / "check.sh"
    if not script.exists():
        return {"ran": False, "verdict": "absent"}
    try:
        proc = subprocess.run([str(script)], capture_output=True, text=True,
                              timeout=30)
        return {"ran": True,
                "verdict": "clear" if proc.returncode == 0 else "busy",
                "detail": (proc.stdout or proc.stderr).strip()[:300]}
    except Exception as exc:
        return {"ran": False, "verdict": f"error: {exc}"}


def run_hook(ctx, hook):
    """Run every check registered for `hook`; returns (results, aggregate).
    A crashed check is a SUSPECT result with the error named — missing is
    suspect, never pass."""
    results = []
    for module in REGISTRY[hook]:
        try:
            module_results = module.run(ctx)
        except Exception as exc:
            module_results = [{
                "check_id": getattr(module, "CHECK_IDS", ("?",))[0],
                "name": f"{module.__name__} (CRASHED)",
                "auditor": getattr(module, "AUDITOR", "lineage"),
                "verdict": "suspect", "counts": {"crashed": 1},
                "findings": [],
                "notes": [f"check crashed: {exc.__class__.__name__}: {exc}",
                          traceback.format_exc(limit=3)],
            }]
        for result in module_results:
            if not result.get("counts"):
                result["verdict"] = "suspect"
                result.setdefault("notes", []).append(
                    "no counts reported — degraded to suspect (R4)")
            results.append(result)
    aggregate = worst(r["verdict"] for r in results)
    return results, aggregate


def merge_to_blackboard(ctx, hook, results):
    """Append new findings + their open-status rows; status-transition to
    resolved any previously-open finding of a check that ran this hook and
    did not re-emit it (§6.4 — outcomes are second rows, never edits)."""
    emitted = [f for r in results for f in r["findings"]]
    new, _existing = append_findings(ctx.audit_dir, emitted)
    statuses = [status_row(f["id"], "open", "deterministic",
                           f"opened by audit_run --hook {hook}")
                for f in new]

    ran_check_ids = {r["check_id"] for r in results}
    emitted_ids = {f["id"] for f in emitted}
    latest = latest_status_by_finding(ctx.audit_dir)
    for finding in load_findings(ctx.audit_dir):
        if finding["check_id"] not in ran_check_ids:
            continue
        if finding["id"] in emitted_ids:
            continue
        if latest.get(finding["id"], "open") == "open":
            statuses.append(status_row(
                finding["id"], "resolved", "deterministic",
                f"not re-observed by {finding['check_id']} at --hook {hook}"))
    append_statuses(ctx.audit_dir, statuses)
    return new, statuses


def write_verdict(ctx, hook, results, aggregate, machine):
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    verdict_dir = ctx.audit_dir / "verdicts"
    verdict_dir.mkdir(parents=True, exist_ok=True)
    path = verdict_dir / f"{hook}-{ts}.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "hook": hook, "slug": ctx.slug, "run_id": ctx.run_id,
        "iteration": ctx.iteration, "ts": ts,
        "verdict": aggregate,
        "machine_check": machine,
        "checks": [{"check_id": r["check_id"], "name": r["name"],
                    "auditor": r["auditor"], "verdict": r["verdict"],
                    "counts": r["counts"],
                    "finding_ids": [f["id"] for f in r["findings"]],
                    "notes": r["notes"]}
                   for r in results],
    }, indent=2, sort_keys=True) + "\n")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug")
    parser.add_argument("--hook", required=True, choices=sorted(REGISTRY))
    parser.add_argument("--factory-root", default=None)
    parser.add_argument("--skip-machine-check", action="store_true")
    args = parser.parse_args(argv)

    ctx = RunContext(args.slug, factory_root=args.factory_root)
    machine = ({"ran": False, "verdict": "skipped"}
               if args.skip_machine_check else machine_check())
    if machine.get("verdict") == "busy":
        print(f"WARN machine-health: BUSY ({machine.get('detail', '')}) — "
              "audit scans are light JSON reads; continuing, recorded in the "
              "verdict file")

    print(f"audit_run: {args.slug} --hook {args.hook} "
          f"(run {ctx.run_id}, iteration {ctx.iteration})")
    results, aggregate = run_hook(ctx, args.hook)
    new, statuses = merge_to_blackboard(ctx, args.hook, results)
    verdict_path = write_verdict(ctx, args.hook, results, aggregate, machine)

    for result in results:
        print(f"  [{result['verdict'].upper():7s}] {result['check_id']} — "
              f"{result['name']}; counts {result['counts']}")
        for note in result["notes"]:
            print(f"            note: {note.splitlines()[0]}")
        for f in result["findings"]:
            print(f"      {f['verdict'].upper()}: {f['headline']}")

    fails = sum(1 for r in results if r["verdict"] == "fail")
    suspects = sum(1 for r in results if r["verdict"] == "suspect")
    print(f"\naudit_run: {len(results)} checks ({fails} fail / {suspects} "
          f"suspect); {len(new)} new finding(s) appended, "
          f"{len(statuses)} status row(s); verdict file {verdict_path.name}")
    print(f"VERDICT: {aggregate}")
    return 1 if aggregate == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
