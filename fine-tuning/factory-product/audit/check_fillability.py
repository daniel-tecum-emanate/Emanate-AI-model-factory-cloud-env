#!/usr/bin/env python3
"""check_fillability — LIN-FILL-04, A1's fillability + admissibility check.

Catch #3 made permanent: the original S-act plan gave `custom` a 21% share
and a 25-record floor while the PTC-only pool holds ZERO `custom` rows in all
history (BUILD-SPEC §2a items 1-2, inputs/README.md A3) — and gold-emitted 7
action classes the live contract's hashed prompt forbids (L51 shape).

Two predicates over the declared dose plan (parsed from BUILD-SPEC §1):

  fillability   every gold class dose has >= its floor of REAL source rows
                in the filtered train pool (share x planned-total, floored).
  admissibility every planned gold class is inside the live contract's
                enabled actions — taken from A2's contract-snapshot.json
                when present, else the envelope pull's `allowed_actions`
                (a local snapshot: named as such in the finding).

The dose plan is parsed from the design doc itself rather than a hand-copied
fixture, so the check audits what the build will actually read. Class tokens
are restricted to the known class vocabulary — no free-token percent matches.

Standalone: python3 audit/check_fillability.py <slug> [--factory-root DIR]
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from audit.audit_lib import (  # noqa: E402
    RunContext, check_result, load_json_or_none, make_finding,
    normalize_pool_rows, verdict_from_findings,
)

AUDITOR = "lineage"
CHECK_IDS = ("LIN-FILL-04",)

# Short token (as dose tables write them) -> canonical class name.
CLASS_TOKENS = {
    "winback": "winback_enrollment", "winback_enrollment": "winback_enrollment",
    "custom": "custom",
    "lookalike": "lookalike_search", "lookalike_search": "lookalike_search",
    "inventory": "inventory_match", "inventory_match": "inventory_match",
    "contact": "contact_suggestion", "contact_suggestion": "contact_suggestion",
    "email": "email_draft", "email_draft": "email_draft",
    "internal": "internal_brief", "internal_brief": "internal_brief",
    "buying": "buying_signal", "buying_signal": "buying_signal",
}
_SHARE_RE = re.compile(r"\b([a-z_]+)\s+(\d+(?:\.\d+)?)%")
_FLOOR_RE = re.compile(r"floor\s+(\d+)\s*/\s*class")
_TOTAL_RE = re.compile(r"S-act[^|]*\|\s*~?([\d,]+)")


def parse_dose_plan(spec_text):
    """{classes: {name: share_fraction}, floor, total} or None when the
    BUILD-SPEC carries no parseable dose plan."""
    classes = {}
    for token, pct in _SHARE_RE.findall(spec_text):
        name = CLASS_TOKENS.get(token)
        if name and name not in classes:
            classes[name] = float(pct) / 100.0
    floor_m = _FLOOR_RE.search(spec_text)
    total_m = _TOTAL_RE.search(spec_text)
    if not classes and not floor_m:
        return None
    return {
        "classes": classes,
        "floor": int(floor_m.group(1)) if floor_m else 0,
        "total": int(total_m.group(1).replace(",", "")) if total_m else None,
    }


def _allowed_actions(ctx):
    """(allowed set | None, basis pointer, basis label). Prefers A2's served
    snapshot; falls back to the envelope pull — a LOCAL snapshot of the
    settings row, honest about being one."""
    snapshot = load_json_or_none(ctx.audit_dir / "contract-snapshot.json")
    if snapshot and snapshot.get("enabled_actions"):
        return (set(snapshot["enabled_actions"]),
                ctx.rel(ctx.audit_dir / "contract-snapshot.json"),
                "A2 contract snapshot")
    envelope = load_json_or_none(ctx.inputs_dir / "envelope.json")
    if envelope and envelope.get("allowed_actions"):
        return (set(envelope["allowed_actions"]),
                ctx.rel(ctx.inputs_dir / "envelope.json"),
                "envelope pull (local snapshot of the live settings row)")
    return None, None, None


def check_lin_fill_04(ctx):
    findings = []
    notes = []
    spec_text = (ctx.build_spec_path.read_text()
                 if ctx.build_spec_path.exists() else "")
    plan = parse_dose_plan(spec_text) if spec_text else None
    counts = {"classes_planned": len(plan["classes"]) if plan else 0}

    if plan is None:
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="LIN-FILL-04", klass="fillability",
            severity="warn", verdict="suspect", stage_scope="S4",
            headline=("LIN-FILL-04 found no parseable dose plan in the "
                      "BUILD-SPEC — fillability unverified, suspect"),
            evidence=[ctx.rel(ctx.build_spec_path)],
            counts={"dose_plans_parsed": 0}))
        return check_result("LIN-FILL-04", "dose fillability + admissibility",
                            AUDITOR, "suspect", counts, findings)

    # Prefer the train pool (what generation may consume — held-out already
    # excluded); fall back to the selection pool with a note.
    pool_name = "train-pool.json"
    rows = normalize_pool_rows(load_json_or_none(ctx.inputs_dir / pool_name))
    if rows is None:
        pool_name = "selection-pool.json"
        rows = normalize_pool_rows(load_json_or_none(ctx.inputs_dir / pool_name))
        if rows is not None:
            notes.append("train pool absent — counted against the selection "
                         "pool (held-out NOT excluded)")
    if rows is None:
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="LIN-FILL-04", klass="fillability",
            severity="warn", verdict="suspect", stage_scope="S4",
            headline=("LIN-FILL-04 has no pool input to count real rows "
                      "against — fillability unverified, suspect"),
            evidence=[ctx.rel(ctx.inputs_dir)],
            counts={"pool_files_found": 0}))
        return check_result("LIN-FILL-04", "dose fillability + admissibility",
                            AUDITOR, "suspect", counts, findings, notes)

    per_class = Counter(r["class"] for r in rows if r.get("class"))
    counts["pool_rows_counted"] = len(rows)

    total = plan["total"]
    floor = plan["floor"]
    unfillable = 0
    for name, share in sorted(plan["classes"].items()):
        required = floor
        if total:
            required = max(floor, round(share * total))
        available = per_class.get(name, 0)
        if available < required:
            unfillable += 1
            share_part = (f"share {round(share * 100)}% of ~{total}, "
                          if total else "")
            findings.append(make_finding(
                ctx, auditor=AUDITOR, check_id="LIN-FILL-04",
                klass="fillability", severity="blocking", verdict="fail",
                stage_scope="S4",
                headline=(f"unfillable class '{name}': dose requires >={required} "
                          f"real rows ({share_part}floor {floor}) but the "
                          f"filtered pool holds {available} (catch #3 — "
                          "custom's 21% share had n=0 in all PTC history)"),
                evidence=[ctx.rel(ctx.build_spec_path) + "#S1",
                          ctx.rel(ctx.inputs_dir / pool_name)],
                counts={"required": required, "available": available,
                        "floor": floor}))
    counts["unfillable_classes"] = unfillable

    allowed, basis_pointer, basis_label = _allowed_actions(ctx)
    if allowed is None:
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="LIN-FILL-04", klass="fillability",
            severity="warn", verdict="suspect", stage_scope="S4",
            headline=("LIN-FILL-04 has no contract snapshot to test class "
                      "admissibility against — unverified, suspect"),
            evidence=[ctx.rel(ctx.audit_dir / "contract-snapshot.json")],
            counts={"contract_snapshots_found": 0}))
    else:
        inadmissible = sorted(set(plan["classes"]) - allowed)
        counts["inadmissible_classes"] = len(inadmissible)
        if inadmissible:
            findings.append(make_finding(
                ctx, auditor=AUDITOR, check_id="LIN-FILL-04",
                klass="fillability", severity="blocking", verdict="fail",
                stage_scope="S4",
                headline=(f"{len(inadmissible)} planned gold classes are "
                          "forbidden by the live contract "
                          f"({basis_label}: enabled={sorted(allowed)}): "
                          f"{','.join(inadmissible)} — gold-emitting them is a "
                          "train/serve contradiction (L51, catch #3)"),
                evidence=[ctx.rel(ctx.build_spec_path) + "#S1", basis_pointer],
                counts={"inadmissible_classes": len(inadmissible),
                        "enabled_actions": len(allowed)},
                lesson_refs=["L51"]))

    return check_result("LIN-FILL-04", "dose fillability + admissibility",
                        AUDITOR, verdict_from_findings(findings), counts,
                        findings, notes)


def run(ctx):
    return [check_lin_fill_04(ctx)]


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
