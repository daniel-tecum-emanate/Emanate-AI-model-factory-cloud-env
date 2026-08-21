#!/usr/bin/env python3
"""compare_runs — A3 Cross-Run Comparator, deterministic core, v0.

The standing motivation (DESIGN.md §3): nothing mechanically compared a new
mixture against all predecessors' mixtures and verdicts — v4's failure was
"changed ~6 variables at once", v5's was a corpus that passed every preflight
while repeating a staged-evidence posture at scale (E21). Runs at S6 project
(pre-S6g) so the comparison is in the decision request the human reads.

Checks (v0 — predecessors are read from the run's own iteration digests,
`runs/<slug>/iterations/*.json`, the machine-readable per-iteration record):

  CMP-REC-01    recipe diff vs the latest predecessor digest: emit the
                changed-variable list with counts; more simultaneous changes
                than the hazard bound is itself a warn finding.
  CMP-FAIL-02   failed-recipe repeat: hazard tuples from the append-only
                `recipe-hazards.yaml` (seeded from MIXTURE-HISTORY §0)
                present in this run's BUILD-SPEC/config without the hazard's
                countermeasure => fail with the minted lesson attached.
  CMP-DOSE-03   dose-vs-evidence: every dose row in the BUILD-SPEC slice
                tables must carry an evidence citation (the CORPUS-EVIDENCE
                convention: every dose tied to a measured rate); an
                uncited dose is a suspect finding.
  CMP-CENSUS-04 config-vs-reality staleness: the config book's census
                numbers vs the live pool counts (v6 found 13,044
                trajectory-id'd reviews where the config said 1,497 —
                BUILD-SPEC §2a.4).

Standalone: python3 audit/compare_runs.py <slug> [--factory-root DIR]
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from audit.audit_lib import (  # noqa: E402
    RunContext, check_result, load_json_or_none, load_yaml_or_none,
    make_finding, normalize_pool_rows, verdict_from_findings,
)

AUDITOR = "comparator"
CHECK_IDS = ("CMP-REC-01", "CMP-FAIL-02", "CMP-DOSE-03", "CMP-CENSUS-04")

DEFAULT_HAZARDS_PATH = HERE / "recipe-hazards.yaml"
# Census drift beyond these factors of the recorded value = stale config.
CENSUS_RATIO_HIGH = 1.25
CENSUS_RATIO_LOW = 0.8

_CITATION_RE = re.compile(r"\b(D\d|L\d|E\d|CE|CP|§|\u00a7)")
_PCTS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _latest_digest(ctx):
    best = None
    if ctx.iterations_dir.is_dir():
        for path in sorted(ctx.iterations_dir.glob("*.json")):
            data = load_json_or_none(path)
            if not data or not isinstance(data.get("iteration"), int):
                continue
            if best is None or data["iteration"] > best["iteration"]:
                best = data
    return best


def _current_recipe(config):
    plan = (config or {}).get("model_plan") or {}
    training = (config or {}).get("training") or {}
    mixture = None
    for key, value in plan.items():
        if key.startswith("corpus_mixture") and isinstance(value, dict):
            mixture = sorted(
                (k, v) for k, v in value.items()
                if isinstance(v, (int, float)) and k.endswith("_pct"))
            break
    return {
        "base_model": str(plan.get("base_model") or "").split("/")[-1] or None,
        "lora_rank": training.get("lora_rank"),
        "epochs": training.get("epochs"),
        "learning_rate": _as_float(training.get("learning_rate")),
        "seed": plan.get("seed"),
        "mixture_pcts": [v for _, v in (mixture or [])] or None,
    }


def _predecessor_recipe(digest):
    training = (digest or {}).get("training") or {}
    recipe_prose = ((digest or {}).get("corpus") or {}).get("recipe") or ""
    pcts = [float(p) for p in _PCTS_RE.findall(recipe_prose)] or None
    return {
        "base_model": str(training.get("base_model") or ""),
        "lora_rank": training.get("lora_rank"),
        "epochs": training.get("epochs"),
        "learning_rate": _as_float(training.get("learning_rate")),
        "seed": None,  # digests do not record seed (v0 gap, noted)
        "mixture_pcts": pcts,
    }


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def diff_recipes(current, predecessor):
    """Changed-variable names, comparing only variables both sides record."""
    changed = []
    if current["base_model"] and predecessor["base_model"] \
            and current["base_model"] not in predecessor["base_model"]:
        changed.append("base_model")
    for key in ("lora_rank", "epochs", "learning_rate"):
        if current[key] is not None and predecessor[key] is not None \
                and current[key] != predecessor[key]:
            changed.append(key)
    if current["mixture_pcts"] and predecessor["mixture_pcts"] \
            and sorted(current["mixture_pcts"]) != sorted(predecessor["mixture_pcts"]):
        changed.append("mixture_shares")
    return changed


def check_cmp_rec_01(ctx, config, hazards):
    findings = []
    digest = _latest_digest(ctx)
    if config is None or digest is None:
        missing = "config" if config is None else "predecessor digest"
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="CMP-REC-01", klass="repeat-recipe",
            severity="warn", verdict="suspect", stage_scope="S6g",
            headline=(f"CMP-REC-01 has no {missing} to diff against — recipe "
                      "comparison unverified, suspect"),
            evidence=[ctx.rel(ctx.config_path), ctx.rel(ctx.iterations_dir)],
            counts={"digests_found": 0 if digest is None else 1}))
        return check_result("CMP-REC-01", "recipe diff vs predecessors",
                            AUDITOR, "suspect",
                            {"digests_found": 0 if digest is None else 1},
                            findings)

    changed = diff_recipes(_current_recipe(config), _predecessor_recipe(digest))
    bound = None
    for hz in hazards or []:
        if hz.get("kind") == "changed_variable_bound":
            bound = hz
            break
    max_changed = (bound or {}).get("max_changed_variables", 4)
    counts = {"changed_variables": len(changed),
              "predecessor_iteration": digest.get("iteration", 0)}

    if changed:
        over = len(changed) > max_changed
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="CMP-REC-01", klass="repeat-recipe",
            severity="warn" if over else "info",
            verdict="suspect" if over else "info", stage_scope="S6g",
            headline=(f"{len(changed)} recipe variables changed vs iteration "
                      f"{digest.get('iteration')}: {','.join(changed)}"
                      + (f" — exceeds the {max_changed}-variable attribution "
                         "bound (v4's lesson: un-attributable by design)"
                         if over else " (within the attribution bound)")),
            evidence=[ctx.rel(ctx.config_path), ctx.rel(ctx.iterations_dir),
                      (bound or {}).get("source",
                                        "company-brain/3-execution/MIXTURE-HISTORY.md#S0")],
            counts={"changed_variables": len(changed),
                    "bound": max_changed},
            lesson_refs=(bound or {}).get("lesson_refs", [])))
    return check_result("CMP-REC-01", "recipe diff vs predecessors", AUDITOR,
                        verdict_from_findings(findings), counts, findings)


def check_cmp_fail_02(ctx, config_text, hazards):
    findings = []
    spec_text = (ctx.build_spec_path.read_text()
                 if ctx.build_spec_path.exists() else "")
    haystack = (spec_text + "\n" + (config_text or "")).lower()
    counts = {"hazards_evaluated": 0}
    if not hazards:
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="CMP-FAIL-02", klass="repeat-recipe",
            severity="warn", verdict="suspect", stage_scope="S6g",
            headline=("CMP-FAIL-02 has no hazard table to evaluate "
                      "(recipe-hazards.yaml missing/empty) — unverified"),
            evidence=[ctx.rel(DEFAULT_HAZARDS_PATH)],
            counts={"hazards_loaded": 0}))
        return check_result("CMP-FAIL-02", "failed-recipe repeat", AUDITOR,
                            "suspect", {"hazards_loaded": 0}, findings)
    if not haystack.strip():
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="CMP-FAIL-02", klass="repeat-recipe",
            severity="warn", verdict="suspect", stage_scope="S6g",
            headline=("CMP-FAIL-02 has no BUILD-SPEC/config text to scan — "
                      "unverified"),
            evidence=[ctx.rel(ctx.build_spec_path)],
            counts={"chars_scanned": 0}))
        return check_result("CMP-FAIL-02", "failed-recipe repeat", AUDITOR,
                            "suspect", {"chars_scanned": 0}, findings)

    for hz in hazards:
        if hz.get("kind") != "pattern":
            continue
        counts["hazards_evaluated"] += 1
        triggers = [p.lower() for p in hz.get("trigger_patterns") or []]
        counters = [p.lower() for p in hz.get("countermeasure_patterns") or []]
        fired = [p for p in triggers if p in haystack]
        countered = [p for p in counters if p in haystack]
        if fired and not countered:
            findings.append(make_finding(
                ctx, auditor=AUDITOR, check_id="CMP-FAIL-02",
                klass="repeat-recipe", severity=hz.get("severity", "warn"),
                verdict="fail", stage_scope="S6g",
                headline=(f"failed-recipe repeat {hz['id']}: trigger(s) "
                          f"{','.join(repr(p) for p in fired)} present with NO "
                          f"countermeasure ({'/'.join(counters[:3])}) — "
                          f"repeats the recorded {'/'.join(hz.get('lesson_refs', []))} "
                          "failure"),
                evidence=[ctx.rel(ctx.build_spec_path),
                          hz.get("source", ctx.rel(DEFAULT_HAZARDS_PATH))],
                counts={"triggers_fired": len(fired),
                        "countermeasures_found": 0},
                lesson_refs=hz.get("lesson_refs", [])))
    return check_result("CMP-FAIL-02", "failed-recipe repeat", AUDITOR,
                        verdict_from_findings(findings), counts, findings)


def check_cmp_dose_03(ctx):
    findings = []
    spec_text = (ctx.build_spec_path.read_text()
                 if ctx.build_spec_path.exists() else "")
    if not spec_text:
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="CMP-DOSE-03", klass="repeat-recipe",
            severity="warn", verdict="suspect", stage_scope="S6g",
            headline="CMP-DOSE-03 has no BUILD-SPEC to lint — unverified",
            evidence=[ctx.rel(ctx.build_spec_path)],
            counts={"dose_rows_checked": 0}))
        return check_result("CMP-DOSE-03", "dose-vs-evidence citations",
                            AUDITOR, "suspect", {"dose_rows_checked": 0},
                            findings)

    dose_rows = [line for line in spec_text.splitlines()
                 if re.match(r"^\|\s*S-", line)]
    uncited = [line for line in dose_rows if not _CITATION_RE.search(line)]
    counts = {"dose_rows_checked": len(dose_rows),
              "uncited_dose_rows": len(uncited)}
    if uncited:
        first = uncited[0].split("|")[1].strip()[:40]
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="CMP-DOSE-03", klass="repeat-recipe",
            severity="warn", verdict="suspect", stage_scope="S6g",
            headline=(f"{len(uncited)}/{len(dose_rows)} dose rows carry no "
                      f"evidence citation (first: '{first}') — every dose must "
                      "tie to a measured rate (CORPUS-EVIDENCE convention)"),
            evidence=[ctx.rel(ctx.build_spec_path) + "#S1"],
            counts=counts))
    return check_result("CMP-DOSE-03", "dose-vs-evidence citations", AUDITOR,
                        verdict_from_findings(findings), counts, findings)


def check_cmp_census_04(ctx, config):
    findings = []
    book = (config or {}).get("book") or {}
    rows = normalize_pool_rows(
        load_json_or_none(ctx.inputs_dir / "selection-pool.json"))
    if not book or rows is None:
        missing = "config book census" if not book else "selection pool"
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="CMP-CENSUS-04", klass="efficiency",
            severity="warn", verdict="suspect", stage_scope="S6g",
            headline=(f"CMP-CENSUS-04 has no {missing} to compare — config "
                      "staleness unverified, suspect"),
            evidence=[ctx.rel(ctx.config_path), ctx.rel(ctx.inputs_dir)],
            counts={"census_pairs_checked": 0}))
        return check_result("CMP-CENSUS-04", "config-vs-reality staleness",
                            AUDITOR, "suspect", {"census_pairs_checked": 0},
                            findings)

    with_trajectory = sum(1 for r in rows if r.get("trajectory_id"))
    pairs = [("reviews_with_trajectory_id", book.get("reviews_with_trajectory_id"),
              with_trajectory)]
    counts = {"census_pairs_checked": 0, "pool_rows": len(rows)}
    for name, recorded, actual in pairs:
        if not isinstance(recorded, (int, float)) or recorded <= 0:
            continue
        counts["census_pairs_checked"] += 1
        ratio = actual / recorded
        if ratio > CENSUS_RATIO_HIGH or ratio < CENSUS_RATIO_LOW:
            findings.append(make_finding(
                ctx, auditor=AUDITOR, check_id="CMP-CENSUS-04",
                klass="efficiency", severity="warn", verdict="fail",
                stage_scope="S6g",
                headline=(f"stale config census: {name} recorded {recorded} "
                          f"but the live pool holds {actual} ({round(ratio, 2)}x) "
                          "— every config value is an INPUT the factory refuses "
                          "to guess; re-census before consumption (§2a.4 shape)"),
                evidence=[ctx.rel(ctx.config_path),
                          ctx.rel(ctx.inputs_dir / "selection-pool.json")],
                counts={"recorded": int(recorded), "actual": actual}))
    return check_result("CMP-CENSUS-04", "config-vs-reality staleness",
                        AUDITOR, verdict_from_findings(findings), counts,
                        findings)


def run(ctx, hazards_path=None):
    config = load_yaml_or_none(ctx.config_path)
    config_text = (ctx.config_path.read_text()
                   if ctx.config_path.exists() else "")
    hazards_doc = load_yaml_or_none(hazards_path or DEFAULT_HAZARDS_PATH) or {}
    hazards = hazards_doc.get("hazards") or []
    results = [check_cmp_rec_01(ctx, config, hazards),
               check_cmp_fail_02(ctx, config_text, hazards),
               check_cmp_dose_03(ctx),
               check_cmp_census_04(ctx, config)]
    # recipe-comparison.json: the artifact stage_project embeds in the S6g
    # decision request (DESIGN §6.3) — counts and pointers only.
    ctx.audit_dir.mkdir(parents=True, exist_ok=True)
    (ctx.audit_dir / "recipe-comparison.json").write_text(json.dumps({
        "slug": ctx.slug, "run_id": ctx.run_id, "iteration": ctx.iteration,
        "checks": [{"check_id": r["check_id"], "verdict": r["verdict"],
                    "counts": r["counts"],
                    "headlines": [f["headline"] for f in r["findings"]]}
                   for r in results],
    }, indent=2, sort_keys=True) + "\n")
    return results


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
