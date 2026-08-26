#!/usr/bin/env python3
"""Style-concentration detector — check #5 of the S5 verify battery.

WHY THIS EXISTS: on 2026-08-25 the battery was run for real against
grand-steel and reported `script_on_disk: false` for five of its six checks.
This one referenced `scripts/preflight/style_concentration.py`, which existed
in NEITHER repository -- not the canonical cloud-env copy, not the workflow
copy. The stage correctly returned `suspect` rather than `pass`, so nothing
pretended the corpus was verified; but the practical consequence was that a run
could arrive at the S6g spend gate with a projection while its corpus had been
verified by nothing at all.

WHAT IT MEASURES: how concentrated the assistant's opening phrasing is across
the corpus. A fine-tune learns surface form long before it learns judgement, so
a corpus where a third of the answers start the same way teaches the opening,
not the reasoning. The thresholds are the battery's own, quoted from the S5
report: **top-1 opening >= 30%** or **top-3 combined >= 60%** fails.

The check is deterministic, needs no model, and costs $0 -- the same class as
every other gate in this pipeline ("every pass/fail is a count, rate, regex, or
schema check").

    python3 scripts/preflight/style_concentration.py --corpus <path.jsonl>
    python3 scripts/preflight/style_concentration.py --self-test

Exit 0 = PASS, 1 = FAIL, 2 = could not evaluate (which the caller must treat as
`suspect`, never as a pass -- `audit_lib`'s degradation lattice).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter

TOP1_MAX = 0.30
TOP3_MAX = 0.60
# How much of the opening to fingerprint. Long enough to catch a templated
# sentence stem, short enough that a shared first word is not a false positive.
OPENING_WORDS = 6


def _normalize_opening(text: str) -> str | None:
    """Fingerprint the first `OPENING_WORDS` words, case- and space-insensitive.

    Returns None for content that carries no prose opening (an empty message, or
    a turn that is purely a tool call) -- those are excluded from the
    denominator rather than counted as a shared empty opening, which would
    manufacture concentration that is not there.
    """
    if not text:
        return None
    stripped = re.sub(r"\s+", " ", str(text)).strip()
    if not stripped:
        return None
    words = re.findall(r"[a-z0-9']+", stripped.lower())
    if not words:
        return None
    return " ".join(words[:OPENING_WORDS])


def _assistant_openings(path: str) -> tuple[list[str], int, int]:
    """Every assistant opening in the corpus, plus (records, skipped) counts."""
    openings: list[str] = []
    records = 0
    skipped = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            records += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            for msg in row.get("messages") or []:
                if msg.get("role") != "assistant":
                    continue
                fingerprint = _normalize_opening(msg.get("content"))
                if fingerprint:
                    openings.append(fingerprint)
    return openings, records, skipped


def analyze(path: str) -> dict:
    openings, records, skipped = _assistant_openings(path)
    total = len(openings)
    if total == 0:
        return {
            "verdict": "suspect",
            "reason": "no assistant openings could be read — cannot evaluate concentration",
            "records": records,
            "openings": 0,
        }

    counts = Counter(openings)
    ranked = counts.most_common()
    top1 = ranked[0][1] / total
    top3 = sum(c for _, c in ranked[:3]) / total

    failures = []
    if top1 >= TOP1_MAX:
        failures.append(f"top-1 opening {top1:.1%} >= {TOP1_MAX:.0%}")
    if top3 >= TOP3_MAX:
        failures.append(f"top-3 openings {top3:.1%} >= {TOP3_MAX:.0%}")

    return {
        "verdict": "fail" if failures else "pass",
        "failures": failures,
        "records": records,
        "unparseable_records": skipped,
        "openings": total,
        "distinct_openings": len(counts),
        "top1_share": round(top1, 4),
        "top3_share": round(top3, 4),
        "top5": [{"opening": o, "count": c, "share": round(c / total, 4)} for o, c in ranked[:5]],
    }


def _self_test() -> int:
    """Prove the detector actually detects, rather than only that it runs.

    A checker that always passes is worse than no checker, because it converts
    "unverified" into "verified" on the report. So the self-test asserts a
    deliberately concentrated corpus FAILS and a varied one PASSES.
    """
    import tempfile
    import os

    def write(rows):
        fd, p = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        return p

    def row(text):
        return {"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": text}]}

    concentrated = write([row("Based on the available data I can confirm that item %d" % i) for i in range(20)])
    varied = write(
        [row(t) for t in [
            "Shipment 4471 is scheduled for Tuesday",
            "I do not have pricing for that grade",
            "The mill certificate lists heat number 88123",
            "Three coils remain on that order",
            "Freight was quoted separately last month",
            "That customer moved to net-30 terms",
            "Inventory shows zero on hand for 11ga",
            "The quote expired on the fourteenth",
        ]]
    )

    bad = analyze(concentrated)
    good = analyze(varied)
    os.unlink(concentrated)
    os.unlink(varied)

    ok = True
    if bad["verdict"] != "fail":
        print(f"SELF-TEST FAIL: concentrated corpus returned {bad['verdict']}, expected fail")
        ok = False
    if good["verdict"] != "pass":
        print(f"SELF-TEST FAIL: varied corpus returned {good['verdict']}, expected pass")
        ok = False
    if ok:
        print(f"self-test PASS — concentrated {bad['top1_share']:.0%} top-1 correctly failed; "
              f"varied {good['top1_share']:.0%} top-1 correctly passed")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Style-concentration detector (S5 battery check #5)")
    ap.add_argument("--corpus", help="path to the corpus .jsonl")
    ap.add_argument("--self-test", action="store_true", help="prove the detector detects")
    ap.add_argument("--json", action="store_true", help="emit the full result as JSON")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()
    if not args.corpus:
        ap.error("--corpus is required unless --self-test")

    try:
        result = analyze(args.corpus)
    except FileNotFoundError:
        print(f"SUSPECT: corpus not found: {args.corpus}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"records={result['records']} assistant_openings={result.get('openings')} "
              f"distinct={result.get('distinct_openings')}")
        print(f"top-1 {result.get('top1_share', 0):.1%} (max {TOP1_MAX:.0%})   "
              f"top-3 {result.get('top3_share', 0):.1%} (max {TOP3_MAX:.0%})")
        for e in result.get("top5", []):
            print(f"   {e['share']:6.1%}  {e['count']:4d}x  {e['opening'][:70]}")
        print(f"VERDICT: {result['verdict'].upper()}")
        for f in result.get("failures", []):
            print(f"   FAIL: {f}")

    return {"pass": 0, "fail": 1, "suspect": 2}[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
