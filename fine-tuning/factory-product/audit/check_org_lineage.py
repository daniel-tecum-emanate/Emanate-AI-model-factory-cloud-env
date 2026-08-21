#!/usr/bin/env python3
"""check_org_lineage — A1 Lineage Auditor, deterministic core (DESIGN.md §4).

Catch #1 made permanent: the excluded org `10664bd2…` silently mixed into PTC
dose aggregates (CORPUS-EVIDENCE §8 erratum / BUILD-SPEC §2a.2) — caught this
week only because a subagent re-pulled the live contract.

Checks (100% coverage, run-parameterized — org id from the run's config,
exclusion list from `runs/<slug>/audit/exclusions.yaml`, never constants):

  LIN-ORG-01  org partition. (a) every pool/corpus surface is free of the
              excluded orgs' id prefixes, envelope-hash prefixes and tool
              schemas; pool org_id equals the run's org. (b) dose-anchor
              census: every "<night> has <N> …" anchor the BUILD-SPEC cites
              is recomputed against the PTC-only pool — a claimed count far
              off the partition's actual count is the mixed-org
              contamination signature (352-claimed vs 82-actual is exactly
              how catch #1 read).
  LIN-SRC-02  row traceability: every assembled corpus record's
              meta.source_row_id resolves against the selection/train pool
              (a miss orders regeneration at the root, never an in-place
              fix — L18/L19). No corpus yet => suspect, never pass.
  LIN-POOL-03 pool hygiene: anomalous source windows named by the run's
              evidence docs (exclusions.yaml) — corpus records sourced from
              them fail; their presence in the selection pool is a warn
              finding (filter-at-selection is the documented plan).

Standalone: python3 audit/check_org_lineage.py <slug> [--factory-root DIR]
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from audit.audit_lib import (  # noqa: E402
    RunContext, check_result, load_corpus_meta, load_exclusions,
    load_json_or_none, make_finding, normalize_pool_rows, verdict_from_findings,
)

AUDITOR = "lineage"
CHECK_IDS = ("LIN-ORG-01", "LIN-SRC-02", "LIN-POOL-03")

# "<MM-DD> has <N> …" — the anchor convention CORPUS-EVIDENCE/BUILD-SPEC use
# for night dose sources ("07-20 has 352 real Claude actions").
_ANCHOR_RE = re.compile(r"\b(\d{2}-\d{2}) has ([\d,]+)\b")
# Claimed within [0.8x - 2, 1.25x + 2] of the partition's actual passes; the
# +/-2 absolute slack keeps tiny fixture counts from flapping. Thresholds are
# slow-cadence-owned (ENG §1) — change via review, never inline.
ANCHOR_RATIO_HIGH = 1.25
ANCHOR_RATIO_LOW = 0.8
ANCHOR_SLACK = 2


def _pool_texts_and_rows(ctx):
    """(raw text, normalized rows, pointer) per pool input that exists."""
    out = []
    for name in ("selection-pool.json", "train-pool.json"):
        path = ctx.inputs_dir / name
        raw = load_json_or_none(path)
        if raw is None:
            continue
        out.append((name, path.read_text(), normalize_pool_rows(raw), raw))
    return out


def _corpus_data_text(path):
    """JSONL text excluding immutable served envelope surfaces.

    L51 requires the deployed system prompt and tool schemas byte-for-byte.
    The prompt may name a globally-known tool while explaining restrictions;
    that is not a row from another org. User/tool/assistant data and metadata
    remain fully scanned, so an actual query_inventory call/result still
    fails LIN-ORG-01.
    """
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            rows.append(line)
            continue
        if not isinstance(obj, dict) or "messages" not in obj:
            rows.append(json.dumps(obj, sort_keys=True))
            continue
        messages = [
            msg for msg in obj.get("messages") or []
            if isinstance(msg, dict) and msg.get("role") != "system"
        ]
        rows.append(json.dumps({"messages": messages}, sort_keys=True))
    return "\n".join(rows)


def check_lin_org_01(ctx, exclusions):
    findings = []
    notes = []
    envelope = load_json_or_none(ctx.inputs_dir / "envelope.json")
    pools = _pool_texts_and_rows(ctx)
    build_texts = []
    if ctx.build_dir.is_dir():
        for path in sorted(ctx.build_dir.glob("*.jsonl")):
            build_texts.append((path.name, _corpus_data_text(path)))

    counts = {"pool_files_scanned": len(pools),
              "corpus_files_scanned": len(build_texts),
              "rows_scanned": sum(len(rows or []) for _, _, rows, _ in pools),
              "anchors_checked": 0}

    if exclusions is None:
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="LIN-ORG-01", klass="contamination",
            severity="warn", verdict="suspect", stage_scope="S4",
            headline=("LIN-ORG-01 cannot verify the org partition: exclusion "
                      f"list missing at runs/{ctx.slug}/audit/exclusions.yaml "
                      "— unverified is suspect, never pass"),
            evidence=[ctx.rel(ctx.exclusions_path)],
            counts={"exclusion_files_found": 0}))
        return check_result("LIN-ORG-01", "org partition", AUDITOR,
                            verdict_from_findings(findings), counts, findings, notes)

    if not pools:
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="LIN-ORG-01", klass="contamination",
            severity="warn", verdict="suspect", stage_scope="S4",
            headline=("LIN-ORG-01 found no pool inputs under "
                      f"runs/{ctx.slug}/corpus/inputs/ — 0 rows scanned is a "
                      "named failure, not a pass (R4)"),
            evidence=[ctx.rel(ctx.inputs_dir)],
            counts={"pool_files_found": 0}))

    expected_org = (envelope or {}).get("org_id")

    # (a) partition scan: markers + per-file org identity
    markers = []
    for org in exclusions.get("excluded_orgs") or []:
        if org.get("id_prefix"):
            markers.append((org["id_prefix"], "excluded-org id"))
        if org.get("envelope_hash_prefix"):
            markers.append((org["envelope_hash_prefix"], "excluded-org envelope hash"))
        for tool in org.get("tool_schema_markers") or []:
            markers.append((tool, "excluded-org tool schema"))
    for extra in exclusions.get("forbidden_envelope_prefixes") or []:
        markers.append((extra["prefix"], "forbidden envelope contract"))

    # Pools are scanned ROW BY ROW, not as whole files: the landed pool shape
    # carries verification metadata that legitimately NAMES the excluded org
    # ("excluded_org_prefix": "10664bd2", read_tools_counted incl. the
    # excluded schema) — a whole-file scan false-reds on the pool's own
    # self-verification. Corpus rows also carry the deployed system prompt
    # verbatim; that prompt legitimately names globally-known tools that are
    # not offered to this org. `_corpus_data_text` therefore scans only
    # data-bearing messages/meta, excluding system bytes and attached schemas.
    def _pool_row_text(raw):
        rows = (raw.get("rows") or []) + (raw.get("action_pool") or []) \
            + (raw.get("no_action_readchain_pool") or [])
        return "\n".join(json.dumps(r, sort_keys=True) for r in rows)

    surfaces = ([(name, _pool_row_text(raw), ctx.rel(ctx.inputs_dir / name))
                 for name, _, _, raw in pools]
                + [(name, text, ctx.rel(ctx.build_dir / name))
                   for name, text in build_texts])
    for name, text, pointer in surfaces:
        for marker, label in markers:
            hits = text.count(marker)
            if hits:
                findings.append(make_finding(
                    ctx, auditor=AUDITOR, check_id="LIN-ORG-01",
                    klass="contamination", severity="blocking", verdict="fail",
                    stage_scope="S4",
                    headline=(f"org partition violated: {label} marker "
                              f"'{marker}' appears {hits}x in {name} rows "
                              "(P3/D9)"),
                    evidence=[pointer, ctx.rel(ctx.exclusions_path)],
                    counts={"marker_hits": hits}))

    for name, _, rows, raw in pools:
        pool_org = raw.get("org_id")
        if expected_org and pool_org and pool_org != expected_org:
            findings.append(make_finding(
                ctx, auditor=AUDITOR, check_id="LIN-ORG-01",
                klass="contamination", severity="blocking", verdict="fail",
                stage_scope="S4",
                headline=(f"pool {name} declares org {str(pool_org)[:12]}… but "
                          f"the run's envelope is org {str(expected_org)[:12]}…"),
                evidence=[ctx.rel(ctx.inputs_dir / name),
                          ctx.rel(ctx.inputs_dir / "envelope.json")],
                counts={"rows_in_pool": len(rows or [])}))

    # (b) dose-anchor census against the partition's own counts
    spec_text = ctx.build_spec_path.read_text() if ctx.build_spec_path.exists() else ""
    selection = next((rows for name, _, rows, _ in pools
                      if name == "selection-pool.json"), None)
    if spec_text and selection:
        per_night_actions = Counter(
            r["night"][5:] for r in selection
            if r.get("night") and r.get("class")
            and not str(r["class"]).startswith("no_action"))
        anchors = _ANCHOR_RE.findall(spec_text)
        counts["anchors_checked"] = len(anchors)
        for mmdd, claimed_s in anchors:
            claimed = int(claimed_s.replace(",", ""))
            actual = per_night_actions.get(mmdd, 0)
            high = actual * ANCHOR_RATIO_HIGH + ANCHOR_SLACK
            low = actual * ANCHOR_RATIO_LOW - ANCHOR_SLACK
            if claimed > high or claimed < low:
                findings.append(make_finding(
                    ctx, auditor=AUDITOR, check_id="LIN-ORG-01",
                    klass="contamination", severity="blocking", verdict="fail",
                    stage_scope="S4",
                    headline=(f"mixed-org contamination signature: dose anchor "
                              f"claims '{mmdd} has {claimed}' actions but the "
                              f"{ctx.slug}-only pool holds {actual} for that "
                              "night — the anchor was derived outside the org "
                              "partition (catch #1, CORPUS-EVIDENCE §8)"),
                    evidence=[ctx.rel(ctx.build_spec_path) + "#S1",
                              ctx.rel(ctx.inputs_dir / "selection-pool.json"),
                              "PRs/ptc-retrain-v6/CORPUS-EVIDENCE.md#S8-erratum"],
                    counts={"anchor_claimed": claimed, "pool_actual": actual,
                            "night": int(mmdd.replace("-", ""))}))
    elif spec_text and not selection:
        notes.append("anchor census skipped: no selection pool to census against")

    return check_result("LIN-ORG-01", "org partition + dose-anchor census",
                        AUDITOR, verdict_from_findings(findings), counts,
                        findings, notes)


def check_lin_src_02(ctx):
    findings = []
    metas = load_corpus_meta(ctx)
    if metas is None:
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="LIN-SRC-02", klass="contamination",
            severity="warn", verdict="suspect", stage_scope="S5",
            headline=("LIN-SRC-02 has no assembled corpus to trace "
                      f"(runs/{ctx.slug}/corpus/build/*.meta.jsonl absent) — "
                      "traceability unverified, suspect until assemble runs"),
            evidence=[ctx.rel(ctx.build_dir)],
            counts={"meta_files_found": 0}))
        return check_result("LIN-SRC-02", "row traceability", AUDITOR,
                            "suspect", {"records_traced": 0}, findings)

    pool_ids = set()
    for name in ("selection-pool.json", "train-pool.json"):
        rows = normalize_pool_rows(load_json_or_none(ctx.inputs_dir / name))
        for r in rows or []:
            pool_ids.add(r["id"])

    misses = 0
    traced = 0
    for meta in metas:
        src = meta.get("source_row_id")
        if src is None:
            continue  # synthetic/replay records carry no source row
        traced += 1
        if src not in pool_ids:
            misses += 1
    if misses:
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="LIN-SRC-02", klass="contamination",
            severity="blocking", verdict="fail", stage_scope="S5",
            headline=(f"{misses}/{traced} corpus records cite source rows "
                      "absent from the selection/train pool — regenerate at "
                      "the root, never repair in place (L18/L19)"),
            evidence=[ctx.rel(ctx.build_dir),
                      ctx.rel(ctx.inputs_dir / "selection-pool.json")],
            counts={"untraceable_records": misses, "records_traced": traced},
            lesson_refs=["L18", "L19"]))
    return check_result("LIN-SRC-02", "row traceability", AUDITOR,
                        verdict_from_findings(findings),
                        {"records_traced": traced, "untraceable_records": misses,
                         "pool_ids": len(pool_ids)},
                        findings)


def check_lin_pool_03(ctx, exclusions):
    findings = []
    if exclusions is None:
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="LIN-POOL-03", klass="contamination",
            severity="warn", verdict="suspect", stage_scope="S4",
            headline=("LIN-POOL-03 cannot verify pool hygiene: exclusion list "
                      f"missing at runs/{ctx.slug}/audit/exclusions.yaml"),
            evidence=[ctx.rel(ctx.exclusions_path)],
            counts={"exclusion_files_found": 0}))
        return check_result("LIN-POOL-03", "pool hygiene", AUDITOR, "suspect",
                            {"nights_checked": 0}, findings)

    anomalous = {row["night"]: row.get("reason", "")
                 for row in exclusions.get("anomalous_nights") or []}
    selection = normalize_pool_rows(
        load_json_or_none(ctx.inputs_dir / "selection-pool.json")) or []
    per_night = Counter(r["night"] for r in selection if r.get("night"))

    pool_hits = {night: per_night[night] for night in anomalous if per_night.get(night)}
    if pool_hits:
        total = sum(pool_hits.values())
        nights = ",".join(sorted(pool_hits))
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="LIN-POOL-03", klass="contamination",
            severity="warn", verdict="suspect", stage_scope="S4",
            headline=(f"anomalous nights {nights} sit in the selection pool "
                      f"({total} rows) — poisonous as read-policy gold sources; "
                      "must be filtered at selection (CHAMPION-PROFILE Δ8)"),
            evidence=[ctx.rel(ctx.inputs_dir / "selection-pool.json"),
                      ctx.rel(ctx.exclusions_path),
                      "PRs/ptc-retrain-v6/CHAMPION-PROFILE.md#S3.5"],
            counts={"anomalous_rows_in_pool": total,
                    "anomalous_nights": len(pool_hits)}))

    metas = load_corpus_meta(ctx) or []
    corpus_hits = Counter(m.get("night") for m in metas
                          if m.get("night") in anomalous)
    if corpus_hits:
        total = sum(corpus_hits.values())
        nights = ",".join(sorted(corpus_hits))
        findings.append(make_finding(
            ctx, auditor=AUDITOR, check_id="LIN-POOL-03", klass="contamination",
            severity="blocking", verdict="fail", stage_scope="S5",
            headline=(f"{total} assembled corpus records source from excluded "
                      f"anomalous nights {nights} — the exclusion window was "
                      "not applied at selection"),
            evidence=[ctx.rel(ctx.build_dir), ctx.rel(ctx.exclusions_path)],
            counts={"corpus_records_from_anomalous_nights": total}))

    return check_result(
        "LIN-POOL-03", "pool hygiene (anomalous windows)", AUDITOR,
        verdict_from_findings(findings),
        {"nights_checked": len(anomalous), "pool_rows_scanned": len(selection),
         "corpus_records_scanned": len(metas)},
        findings)


def run(ctx):
    exclusions = load_exclusions(ctx)
    return [check_lin_org_01(ctx, exclusions),
            check_lin_src_02(ctx),
            check_lin_pool_03(ctx, exclusions)]


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
