#!/usr/bin/env python3
"""run_pipeline — walk an org through `factory.py`'s stage graph automatically,
stopping ONLY at a genuine human/cost gate or a real failure.

## Why this exists (2026-07-29, Daniel's request this session)

Before this script, "click Start Run" queued a `factory_runs` row and nothing
ever advanced it — every single stage required a human to manually type the
next `factory.py <org> --stage <x>` command, INCLUDING stages that have no
gate at all and cost nothing to run (census, export, project). Approving a
gate in the product UI (`decide_factory_gate` RPC) only ever wrote the
decision row — nothing was listening for that decision to actually continue
the pipeline. Daniel: "I clicked run. Currently it is blocked on gate and I
cannot do anything in the UI... I dont see any agents actually running."

Verified while building this: `factory.py` ALREADY checks Supabase for a
tab-approved gate on every invocation (`_gate_approved_via_sync`) — so a human
clicking "Approve" in the product UI is already sufficient for this script to
detect and proceed past a gate on its next pass. The missing piece was never
the gate-check — it was that nothing was polling/advancing at all. This
script is that missing loop, nothing more: it does not reimplement any stage,
gate check, budget cap, or PII rule. It shells out to the exact same
`factory.py` every manual invocation used, in the exact same order defined in
`factory_stages.STAGES` — that module's own docstring says "there is nowhere
left to re-derive [stage order] from," so this script doesn't.

## What it will and will not do unattended

- Runs every non-gate stage automatically (census/export/build/verify/project/
  eval/activate/retrain) — this includes REAL agent dispatch at S4/S5/S7/S8/S10
  (`factory_node_run.py` -> `tier2_run.sh` -> real Claude/Cursor sessions,
  bounded by each spec's `ROLE_DEFAULTS` budget) unless `--dry-run` is passed.
- STOPS and prints exactly what's needed at any of the three HUMAN_GATES
  (governance/S2, launch/S6g, ship/S9) that isn't already approved — the S6g
  gate is specifically where GPU/training spend gets approved, so a stop
  there is Daniel's "should request my input if costs need to be ran" ask
  being honored, not a bug.
- STOPS on any stage that exits non-zero for a reason other than "blocked on
  upstream gate" (exit code 3) — a real failure is never silently skipped.
- Never invokes `--approved` itself. It can only detect an approval a human
  already made (CLI `--approved` flag or the product UI) — see D1 below.

## Decisions worth recording explicitly (not deferring to a PR — this is a
## dev-loop convenience script, not a product surface)

D1. This script is NOT a gate. It never sets `args.approved`. The only way a
    HUMAN_GATE stage shows "approved" is a human having actually approved it
    (CLI or tab) before or during this script's run. Re-running this script
    after clicking Approve in the tab is exactly the "resume" affordance the
    product UI is missing — until a product PR gives the tab itself a
    "Resume" button that does this, running this script IS that button.
D2. Stops --until a caller-given stage (default: run everything, stop at the
    first unapproved gate). This is what makes running it safe to leave
    unattended: it cannot spend real training/GPU money without a human
    having clicked Approve on S6g first, no matter how many times it's re-run.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from factory_stages import HUMAN_GATES, STAGE_CODES, STAGES

HERE = Path(__file__).resolve().parent


def _report_path(org, stage):
    i = STAGES.index(stage) + 1
    return HERE / "runs" / org / f"{i:02d}-{stage}-report.json"


def _read_report(org, stage):
    p = _report_path(org, stage)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def run_stage(org, stage, sync_url, live, dry_run):
    cmd = [sys.executable, str(HERE / "factory.py"), org, "--stage", stage]
    if sync_url:
        cmd += ["--sync-url", sync_url]
    if dry_run:
        cmd.append("--dry-run")
    if live and stage == "export":
        cmd.append("--live")
    print(f"\n=== {STAGE_CODES[stage]:>4} {stage} " + "=" * 40)
    proc = subprocess.run(cmd, cwd=HERE)
    return proc.returncode


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("org")
    p.add_argument("--sync-url", default=None)
    p.add_argument("--live", action="store_true", help="pass --live through to S3 export (real prod read)")
    p.add_argument("--dry-run", action="store_true", help="pass --dry-run through to every stage (no spend, no dispatch)")
    p.add_argument("--start-at", default=None, choices=STAGES, help="skip stages before this one (they already ran)")
    p.add_argument("--until", default=None, choices=STAGES, help="stop after this stage even if nothing blocks it")
    args = p.parse_args(argv)

    start_idx = STAGES.index(args.start_at) if args.start_at else 0
    stop_idx = STAGES.index(args.until) if args.until else len(STAGES) - 1

    for idx in range(start_idx, stop_idx + 1):
        stage = STAGES[idx]
        rc = run_stage(args.org, stage, args.sync_url, args.live, args.dry_run)

        if rc == 3:
            report = _read_report(args.org, stage)
            blocked_on = report.get("blocked_on") if report else "?"
            print(f"\nSTOPPED — '{stage}' is blocked on unapproved human gate '{blocked_on}'.")
            print(f"Approve it via the Model Factory tab (Run Detail -> gate card), or:")
            print(f"  python3 factory.py {args.org} --stage {blocked_on} --approved --sync-url <url>")
            print(f"then re-run this script with --start-at {stage} to resume from here.")
            return 3

        if rc != 0:
            print(f"\nSTOPPED — '{stage}' exited {rc} (real failure, not a gate). Check its report before re-running.")
            return rc

        if stage in HUMAN_GATES:
            report = _read_report(args.org, stage)
            status = (report or {}).get("status")
            if status != "approved":
                print(f"\nSTOPPED — '{stage}' ({STAGE_CODES[stage]}) is a human gate and is not yet approved "
                      f"(status: {status}).")
                if report and report.get("decision_request"):
                    print(json.dumps(report["decision_request"], indent=2))
                print(f"\nApprove it via the Model Factory tab, or:")
                print(f"  python3 factory.py {args.org} --stage {stage} --approved --sync-url <url>")
                print(f"then re-run this script with --start-at {stage} to resume from here.")
                return 2
            print(f"'{stage}' already approved — continuing.")

    print(f"\nReached the end of the requested range ({STAGES[stop_idx]}) with nothing blocking.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
