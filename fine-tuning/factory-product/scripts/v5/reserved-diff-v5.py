#!/usr/bin/env python3
"""Reserved-key / leakage diff -- check #4 of the S5 verify battery.

WHY THIS EXISTS: on 2026-08-25 the battery ran for real against grand-steel and
reported `script_on_disk: false` for five of its six checks. This one --
`scripts/v5/reserved-diff-v5.py`, catalogued as "reserved-key / leakage diff vs
EVAL_RESERVED" -- existed in neither repository. S5 correctly returned `suspect`
rather than `pass`, so nothing claimed the corpus was clean; but a run could
still reach the S6g human spend gate carrying a cost projection for a corpus
whose leakage had been checked by nothing at all.

WHAT FAILURE IT PREVENTS: this is the one check whose absence is invisible in
the thing it protects. Every other defect in a corpus eventually shows up as a
bad number. Leakage shows up as a *good* one. If the items reserved for grading
are also in the training data, the model has seen the test, the eval reports a
score, the score is high, and it is a measurement of memorisation rather than of
capability -- inflated in exactly the direction that gets a model shipped.
`factory_heldout.py` calls this the #1 anchor and says it plainly: "Nothing
errors. The eval reports a number, the number is good, and the number is wrong."

WHAT IT DIFFS. The reserve is sealed by two separate mechanisms in this
pipeline, and both are checked here because either one leaking is fatal:

  1. The **sealed held-out manifest** written by `factory_heldout.seal()` at
     `runs/<slug>/heldout/<NN>-manifest.json`. It stores `sha256(item_id)[:16]`
     and never raw ids -- item ids here are human-readable (`acct-<name>-...`),
     so a manifest of ids would be a manifest of account names. Membership stays
     testable because a checker hashes its own candidate and looks it up, which
     is precisely what this script does: it harvests every id-shaped token it can
     find in the training corpus (the `.meta.jsonl` sidecar's `account_id` /
     `candidate_id`, plus any UUID appearing anywhere in a record's text),
     fingerprints each with the same `sha256-16`, and intersects.
  2. **EVAL_RESERVED** -- `families-<slug>/1b-eval-reserved.jsonl`, the records
     set aside for the pre-S8 eval arms. Three surfaces are diffed against
     training: `cf_group` (the counterfactual-group key -- the unit the reserve
     is actually partitioned on), the normalized user turn (the GS analogue of
     `verify_corpus_gs.check_l39_reserved_diff`, catching a reserved question
     re-rendered into a training record under a different id), and
     `sha256_record` where both sides carry one.

WHY A CLEAN RESULT IS NOT AUTOMATICALLY A PASS. Two failure modes turn this
check into theatre, and both are handled explicitly rather than by omission:

  * **Nothing to check.** If no sealed set exists for the org, or no candidate id
    could be harvested from the corpus, the answer is exit 2 SUSPECT -- never 0.
    "No leakage found" and "nothing was checked" must never render identically on
    the report, because the second one is what the 2026-08-25 run actually had.
  * **A stale partition.** A held-out set only means something relative to the
    population it split. `factory_heldout` records a `population_fingerprint` for
    this reason. If the current population no longer matches, items exist that
    were never eligible for the split, and disjointness over the sealed 20% no
    longer implies the corpus as a whole is disjoint from a valid reserve. That
    is inconclusive, not clean, so it degrades to SUSPECT.

Deterministic, no model call, $0 -- every verdict here is a set intersection, a
count or a hash comparison, like every other gate in this pipeline.

    python3 scripts/v5/reserved-diff-v5.py --corpus <path.jsonl>
    python3 scripts/v5/reserved-diff-v5.py --corpus <path.jsonl> --population <dir>
    python3 scripts/v5/reserved-diff-v5.py --self-test

Exit 0 = PASS, 1 = FAIL, 2 = could not evaluate (which the caller must treat as
`suspect`, never as a pass -- `audit_lib`'s degradation lattice).
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys

HASH_ALGO = "sha256-16"

# Matches factory_heldout.item_fingerprint exactly. If these ever diverge the
# intersection silently becomes empty, which is the vacuous pass this whole file
# exists to prevent -- so the algo string on the manifest is asserted below.
def fp16(item_id: str) -> str:
    return hashlib.sha256(str(item_id).encode("utf-8")).hexdigest()[:16]


def population_fingerprint(items) -> str:
    """Order-independent fingerprint of a population. Mirrors
    factory_heldout.population_fingerprint -- sorted before hashing because
    enumeration order is an artifact of the walk, not a property of the set."""
    joined = "\n".join(sorted(fp16(i) for i in items))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
# Keys in the meta sidecar that carry an item identity. `candidate_id` is
# `<account_id>:<timestamp>`, so the prefix is harvested too -- the manifest
# hashed the bare account id, and hashing the composite verbatim would miss it.
ID_KEYS = ("item_id", "account_id", "candidate_id", "id")


def _norm_surface(text) -> str:
    """Whitespace- and case-insensitive fingerprint of a user turn.

    Same normalization as verify_corpus_gs.check_l39_reserved_diff, so a
    reserved question that was re-indented or re-wrapped on its way into a
    training record still matches.
    """
    return "".join((text or "").lower().split())


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_sealed(path: str) -> dict:
    """Normalize either sealed-manifest shape to a fingerprint set.

    Two exist in this repo and both are real artifacts, so both are read rather
    than one being declared canonical:
      * `factory_heldout.seal()` -- `heldout_fingerprints`, hashes only.
      * the PTC corpus seal -- a raw `heldout: [{id, class}]` list plus a
        `seal_sha256` over the sorted ids, which is re-derived here. A seal whose
        own checksum does not reproduce is reported as unusable rather than
        trusted, because a tampered partition proves nothing about disjointness.
    """
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    info = {"path": path, "shape": None, "fingerprints": set(), "problems": []}

    if isinstance(raw.get("heldout_fingerprints"), list):
        info["shape"] = "factory_heldout"
        if not raw.get("sealed"):
            info["problems"].append("manifest is not marked sealed")
        algo = raw.get("hash_algo")
        if algo != HASH_ALGO:
            # A different algo means our recomputed fingerprints cannot match
            # anything in the manifest, which would read as "no overlap".
            info["problems"].append(
                f"manifest hash_algo is {algo!r}, this check computes {HASH_ALGO!r} — "
                f"fingerprints are not comparable"
            )
        info["fingerprints"] = set(raw["heldout_fingerprints"])
        info["population_size"] = raw.get("population_size")
        info["population_fingerprint"] = raw.get("population_fingerprint")

    elif isinstance(raw.get("heldout"), list):
        info["shape"] = "raw-id-seal"
        ids = [str(e.get("id") if isinstance(e, dict) else e) for e in raw["heldout"]]
        expected = raw.get("seal_sha256")
        if expected:
            actual = hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()
            if actual != expected:
                info["problems"].append(
                    f"seal_sha256 does not re-derive (manifest {expected[:16]}…, "
                    f"recomputed {actual[:16]}…) — the sealed partition has been altered"
                )
        info["fingerprints"] = {fp16(i) for i in ids}
        info["population_size"] = raw.get("heldout_count", 0) + raw.get("train_count", 0) or None
        info["population_fingerprint"] = None
    else:
        info["problems"].append("file is not a recognizable held-out seal")

    if not info["fingerprints"] and not info["problems"]:
        info["problems"].append("seal contains zero held-out items")
    info["heldout_count"] = len(info["fingerprints"])
    return info


def _read_jsonl(path: str):
    rows = []
    unparseable = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                unparseable += 1
                rows.append(None)
    return rows, unparseable


def harvest_ids(corpus_rows, meta_rows) -> dict:
    """Every candidate item id in the training corpus, mapped to its line(s).

    Two sources, because either alone has a blind spot. The `.meta.jsonl`
    sidecar carries the authoritative id per record but only for families that
    have one; the raw record text catches an id that got rendered into a prompt
    or a tool result without ever appearing in the sidecar.
    """
    by_id: dict[str, set] = {}
    sources = {"meta": 0, "corpus_text": 0}

    def add(value, line, source):
        if not value:
            return
        value = str(value)
        by_id.setdefault(value, set()).add(line)
        sources[source] += 1
        if ":" in value:
            head = value.split(":", 1)[0]
            if head and head != value:
                by_id.setdefault(head, set()).add(line)

    for idx, m in enumerate(meta_rows or []):
        if not isinstance(m, dict):
            continue
        line = m.get("line") or idx + 1
        for key in ID_KEYS:
            if key in m:
                add(m[key], line, "meta")

    for idx, row in enumerate(corpus_rows):
        if row is None:
            continue
        for match in set(UUID_RE.findall(json.dumps(row))):
            add(match.lower(), idx + 1, "corpus_text")

    return {"by_id": by_id, "sources": sources}


def load_reserved(path: str) -> dict:
    """EVAL_RESERVED's three diffable surfaces."""
    rows, unparseable = _read_jsonl(path)
    cf_groups, surfaces, shas = {}, {}, {}
    for idx, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        if r.get("cf_group"):
            cf_groups.setdefault(str(r["cf_group"]), idx + 1)
        if r.get("sha256_record"):
            shas.setdefault(str(r["sha256_record"]), idx + 1)
        for msg in r.get("messages") or []:
            if msg.get("role") == "user":
                surface = _norm_surface(msg.get("content"))
                if surface:
                    surfaces.setdefault(surface, idx + 1)
    return {
        "path": path,
        "records": len([r for r in rows if r is not None]),
        "unparseable_records": unparseable,
        "cf_groups": cf_groups,
        "user_surfaces": surfaces,
        "record_hashes": shas,
    }


def read_population(path: str):
    """Enumerate the current population's item ids, for the drift comparison.

    A directory is read as one JSON object per file (the dossier layout); a
    `.jsonl` as one record per line; a `.json` as a list. Whatever the container,
    the id is pulled with the same `ID_KEYS` used against the corpus, so the two
    sides are guaranteed to be talking about the same identifier.
    """
    ids = []
    if os.path.isdir(path):
        files = sorted(glob.glob(os.path.join(path, "*.json")))
        for f in files:
            with open(f, "r", encoding="utf-8") as fh:
                try:
                    obj = json.load(fh)
                except json.JSONDecodeError:
                    continue
            for key in ID_KEYS:
                if obj.get(key):
                    ids.append(str(obj[key]))
                    break
        return ids
    if path.endswith(".jsonl"):
        rows, _ = _read_jsonl(path)
    else:
        with open(path, "r", encoding="utf-8") as fh:
            rows = json.load(fh)
    for obj in rows:
        if isinstance(obj, str):
            ids.append(obj)
        elif isinstance(obj, dict):
            for key in ID_KEYS:
                if obj.get(key):
                    ids.append(str(obj[key]))
                    break
    return ids


# ---------------------------------------------------------------------------
# The diff
# ---------------------------------------------------------------------------

def analyze(corpus, sealed_path=None, reserved_path=None, meta_path=None,
            population_path=None) -> dict:
    corpus_rows, corpus_unparseable = _read_jsonl(corpus)
    meta_rows = []
    if meta_path and os.path.isfile(meta_path):
        meta_rows, _ = _read_jsonl(meta_path)

    result = {
        "corpus": corpus,
        "records": len([r for r in corpus_rows if r is not None]),
        "unparseable_records": corpus_unparseable,
        "meta_sidecar": meta_path if meta_rows else None,
        "dimensions": {},
        "leaks": [],
        "blockers": [],
    }
    dims = result["dimensions"]
    sealed = load_sealed(sealed_path) if sealed_path and os.path.isfile(sealed_path) else None

    # -- D1: sealed held-out set vs every id in training -------------------
    if sealed is None:
        dims["sealed_heldout"] = {
            "status": "not-evaluated",
            "detail": "no sealed held-out manifest found for this org — "
                      "nothing was checked, which is not the same as nothing found",
        }
        result["blockers"].append("no sealed held-out set exists for this org")
    else:
        harvest = harvest_ids(corpus_rows, meta_rows)
        by_id = harvest["by_id"]
        if sealed["problems"]:
            dims["sealed_heldout"] = {
                "status": "not-evaluated",
                "manifest": sealed_path,
                "detail": "; ".join(sealed["problems"]),
            }
            result["blockers"].extend(sealed["problems"])
        elif not by_id:
            dims["sealed_heldout"] = {
                "status": "not-evaluated",
                "manifest": sealed_path,
                "heldout_count": sealed["heldout_count"],
                "detail": "no candidate item id could be harvested from the corpus "
                          "(no meta sidecar and no id-shaped token in any record) — "
                          "the intersection would be empty for want of anything to "
                          "intersect, which is not evidence of disjointness",
            }
            result["blockers"].append("no candidate item ids in corpus to test")
        else:
            overlap = sorted(i for i in by_id if fp16(i) in sealed["fingerprints"])
            dims["sealed_heldout"] = {
                "status": "leak" if overlap else "clean",
                "manifest": sealed_path,
                "manifest_shape": sealed["shape"],
                "heldout_count": sealed["heldout_count"],
                "candidate_ids_tested": len(by_id),
                "id_sources": harvest["sources"],
                "overlap_count": len(overlap),
                # Fingerprints, not ids: the manifest keeps item ids out of
                # committed artifacts and a leak report should not undo that.
                "overlap": [
                    {"fingerprint": fp16(i), "corpus_lines": sorted(by_id[i])[:20]}
                    for i in overlap[:50]
                ],
            }
            for i in overlap:
                result["leaks"].append(
                    f"held-out item {fp16(i)} appears in training at line(s) "
                    f"{sorted(by_id[i])[:5]}"
                )

    # -- D2/D3/D4: EVAL_RESERVED surfaces ----------------------------------
    if not reserved_path or not os.path.isfile(reserved_path):
        for name in ("reserved_cf_group", "reserved_user_surface", "reserved_record_hash"):
            dims[name] = {
                "status": "not-evaluated",
                "detail": "EVAL_RESERVED (1b-eval-reserved.jsonl) not found for this org",
            }
    else:
        reserved = load_reserved(reserved_path)

        train_cf = {}
        train_sha = {}
        for idx, m in enumerate(meta_rows):
            if not isinstance(m, dict):
                continue
            line = m.get("line") or idx + 1
            if m.get("cf_group"):
                train_cf.setdefault(str(m["cf_group"]), line)
            if m.get("sha256_record"):
                train_sha.setdefault(str(m["sha256_record"]), line)

        train_surface = {}
        for idx, row in enumerate(corpus_rows):
            if not isinstance(row, dict):
                continue
            for msg in row.get("messages") or []:
                if msg.get("role") == "user":
                    surface = _norm_surface(msg.get("content"))
                    if surface:
                        train_surface.setdefault(surface, idx + 1)

        def diff(name, reserved_map, train_map, label, unit):
            if not reserved_map or not train_map:
                dims[name] = {
                    "status": "not-evaluated",
                    "reserved": len(reserved_map),
                    "training": len(train_map),
                    "detail": f"one side carries no {unit} — nothing to intersect",
                }
                return
            shared = sorted(set(reserved_map) & set(train_map))
            dims[name] = {
                "status": "leak" if shared else "clean",
                "reserved": len(reserved_map),
                "training": len(train_map),
                "overlap_count": len(shared),
                "overlap": [
                    {"key": k if unit != "user surface" else fp16(k),
                     "reserved_line": reserved_map[k],
                     "corpus_line": train_map[k]}
                    for k in shared[:50]
                ],
            }
            for k in shared:
                result["leaks"].append(
                    f"{label}: reserved line {reserved_map[k]} also in training at "
                    f"corpus line {train_map[k]}"
                )

        diff("reserved_cf_group", reserved["cf_groups"], train_cf,
             "EVAL_RESERVED cf_group present in training", "cf_group")
        diff("reserved_user_surface", reserved["user_surfaces"], train_surface,
             "EVAL_RESERVED user surface present in training", "user surface")
        diff("reserved_record_hash", reserved["record_hashes"], train_sha,
             "EVAL_RESERVED record hash present in training", "record hash")
        dims["reserved_source"] = {"path": reserved_path, "records": reserved["records"]}

    # -- D5: population drift ----------------------------------------------
    sealed_pf = sealed["population_fingerprint"] if sealed and not sealed["problems"] else None

    if not population_path:
        dims["population_drift"] = {
            "status": "not-evaluated",
            "detail": "no --population given; the sealed partition's continued validity "
                      "was not confirmed. Disjointness below is asserted only over the "
                      "population that existed at seal time.",
        }
    elif not sealed_pf:
        dims["population_drift"] = {
            "status": "not-evaluated",
            "detail": "seal carries no population_fingerprint to compare against",
        }
    else:
        current = read_population(population_path)
        unique = sorted(set(current))
        actual = population_fingerprint(unique)
        drifted = actual != sealed_pf
        # Namespace-coherence control. The whole D1 result rests on the manifest
        # and the corpus hashing the SAME kind of identifier. If they do not, the
        # intersection is empty for a reason that has nothing to do with
        # disjointness, and a clean D1 means nothing. Resolving the sealed
        # fingerprints back into the current population is the cheapest available
        # proof that the two sides speak the same language.
        resolved = len(sealed["fingerprints"] & {fp16(i) for i in unique})
        dims["population_drift"] = {
            "status": "drifted" if drifted else "clean",
            "population_path": population_path,
            "sealed_population_size": sealed["population_size"],
            "current_population_size": len(unique),
            "sealed_fingerprint": sealed_pf,
            "current_fingerprint": actual,
            "heldout_resolved_in_population": f"{resolved}/{len(sealed['fingerprints'])}",
        }
        if resolved == 0:
            result["blockers"].append(
                f"none of the {len(sealed['fingerprints'])} sealed fingerprints resolve to "
                f"an item in {population_path} — the manifest and the corpus are hashing "
                f"different identifier namespaces, so an empty intersection says nothing "
                f"about leakage"
            )
        if drifted:
            result["blockers"].append(
                f"population has drifted since the seal "
                f"({sealed['population_size']} sealed vs "
                f"{len(unique)} now) — items exist that were never eligible for the "
                f"held-out split, so disjointness over the sealed set does not cover "
                f"the corpus as a whole"
            )

    # -- verdict ------------------------------------------------------------
    # Leakage is decisive and nothing excuses it, so it is tested first. Only
    # after ruling it out does an unevaluated dimension matter -- and then it
    # must degrade, never round up to clean.
    if result["leaks"]:
        result["verdict"] = "fail"
    elif result["blockers"]:
        result["verdict"] = "suspect"
        result["reason"] = "; ".join(result["blockers"])
    elif not any(d.get("status") == "clean" for d in dims.values()):
        result["verdict"] = "suspect"
        result["reason"] = "no dimension of the leakage diff could be evaluated"
    else:
        result["verdict"] = "pass"
    return result


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
FACTORY = os.path.abspath(os.path.join(HERE, "..", ".."))   # fine-tuning/factory-product
REPO = os.path.abspath(os.path.join(FACTORY, "..", ".."))   # emanate-tecum-workflow


def slug_from_corpus(corpus: str):
    m = re.match(r"combined-train-(.+?)-v\d+\.jsonl$", os.path.basename(corpus))
    return m.group(1) if m else None


def find_sealed(slug: str):
    """Newest sealed iteration for the org, in either known location."""
    if not slug:
        return None
    manifests = sorted(glob.glob(os.path.join(FACTORY, "runs", slug, "heldout", "*-manifest.json")))
    if manifests:
        return manifests[-1]
    ptc = os.path.join(FACTORY, "runs", slug, "corpus", "inputs", "heldout-seal.json")
    return ptc if os.path.isfile(ptc) else None


def find_reserved(slug: str):
    if not slug:
        return None
    path = os.path.join(REPO, "platform-alpha", "finetune-out",
                        f"families-{slug}", "1b-eval-reserved.jsonl")
    return path if os.path.isfile(path) else None


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _self_test() -> int:
    """Prove the diff actually detects, rather than only that it runs.

    A checker that always passes is worse than no checker: it converts
    "unverified" into "verified" on the report, which is the precise failure the
    2026-08-25 battery run avoided only because the script was missing outright.
    So each fixture below asserts a specific verdict, and the negative cases --
    a leak that must FAIL, a missing seal that must SUSPECT -- carry the weight.
    """
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="reserved-diff-")
    fails = []

    def jw(path, rows):
        with open(path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        return path

    def dump(path, obj):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        return path

    held_ids = [f"acct-heldout-{i}" for i in range(8)]
    train_ids = [f"acct-train-{i}" for i in range(20)]
    population = held_ids + train_ids

    manifest = dump(os.path.join(tmp, "manifest.json"), {
        "org_slug": "selftest", "iteration": 1, "sealed": True,
        "hash_algo": HASH_ALGO, "seed": 0, "fraction": 0.2,
        "population_size": len(population),
        "heldout_count": len(held_ids),
        "population_fingerprint": population_fingerprint(population),
        "heldout_fingerprints": sorted(fp16(i) for i in held_ids),
    })

    reserved = jw(os.path.join(tmp, "reserved.jsonl"), [
        {"cf_group": f"cf-reserved-{i}", "sha256_record": f"sha-reserved-{i}",
         "messages": [{"role": "system", "content": "sys"},
                      {"role": "user", "content": f"What is reserved question {i}?"}]}
        for i in range(10)
    ])

    def corpus_and_meta(name, ids, extra_rows=(), extra_meta=()):
        rows = [{"messages": [{"role": "system", "content": "sys"},
                              {"role": "user", "content": f"Clean training question {i}?"},
                              {"role": "assistant", "content": "ok"}]}
                for i in range(len(ids))]
        meta = [{"line": i + 1, "account_id": a, "cf_group": f"cf-train-{i}",
                 "sha256_record": f"sha-train-{i}"} for i, a in enumerate(ids)]
        rows = list(rows) + list(extra_rows)
        meta = list(meta) + list(extra_meta)
        for n, m in enumerate(meta):
            m["line"] = n + 1
        c = jw(os.path.join(tmp, f"{name}.jsonl"), rows)
        jw(os.path.join(tmp, f"{name}.meta.jsonl"), meta)
        return c, os.path.join(tmp, f"{name}.meta.jsonl")

    def run(name, **kw):
        return analyze(**kw)

    def expect(label, got, want, extra=""):
        if got != want:
            fails.append(f"SELF-TEST FAIL: {label} returned {got!r}, expected {want!r} {extra}")
            return False
        return True

    # 1. clean corpus -> pass
    c, m = corpus_and_meta("clean", train_ids)
    clean = run("clean", corpus=c, sealed_path=manifest, reserved_path=reserved,
                meta_path=m, population_path=None)
    expect("clean corpus", clean["verdict"], "pass")
    if clean["dimensions"]["sealed_heldout"].get("candidate_ids_tested", 0) < len(train_ids):
        fails.append("SELF-TEST FAIL: clean corpus tested fewer ids than it contains — "
                     "a pass over an empty candidate set is vacuous")

    # 2. one held-out account in training -> fail
    c, m = corpus_and_meta("leak_id", train_ids[:-1] + [held_ids[3]])
    leaked = run("leak_id", corpus=c, sealed_path=manifest, reserved_path=reserved,
                 meta_path=m, population_path=None)
    expect("held-out id in training", leaked["verdict"], "fail")
    expect("held-out id dimension", leaked["dimensions"]["sealed_heldout"]["status"], "leak")

    # 2b. held-out id present only in record TEXT, absent from the sidecar --
    # the sidecar is not the only way an id reaches the corpus.
    uuid_held = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
    text_manifest = dump(os.path.join(tmp, "manifest_uuid.json"), {
        "org_slug": "selftest", "sealed": True, "hash_algo": HASH_ALGO,
        "population_size": 30, "heldout_count": 1,
        "population_fingerprint": population_fingerprint(population + [uuid_held]),
        "heldout_fingerprints": [fp16(uuid_held)],
    })
    c, m = corpus_and_meta(
        "leak_text", train_ids,
        extra_rows=[{"messages": [
            {"role": "user", "content": "summarize"},
            {"role": "tool", "content": f'{{"account_id": "{uuid_held}"}}'},
            {"role": "assistant", "content": "done"}]}],
        extra_meta=[{"cf_group": "cf-train-x", "sha256_record": "sha-train-x"}])
    text_leak = run("leak_text", corpus=c, sealed_path=text_manifest,
                    reserved_path=reserved, meta_path=m, population_path=None)
    expect("held-out id in record text only", text_leak["verdict"], "fail")

    # 3. reserved user surface re-used in training -> fail
    c, m = corpus_and_meta(
        "leak_surface", train_ids,
        extra_rows=[{"messages": [
            {"role": "user", "content": "  WHAT is   Reserved Question 4?  "},
            {"role": "assistant", "content": "ok"}]}],
        extra_meta=[{"cf_group": "cf-train-y", "sha256_record": "sha-train-y"}])
    surf = run("leak_surface", corpus=c, sealed_path=manifest, reserved_path=reserved,
               meta_path=m, population_path=None)
    expect("reserved user surface in training", surf["verdict"], "fail")
    expect("user-surface dimension",
           surf["dimensions"]["reserved_user_surface"]["status"], "leak")

    # 4. reserved cf_group re-used in training -> fail
    c, m = corpus_and_meta("leak_cf", train_ids)
    meta_rows, _ = _read_jsonl(m)
    meta_rows[0]["cf_group"] = "cf-reserved-2"
    jw(m, meta_rows)
    cf = run("leak_cf", corpus=c, sealed_path=manifest, reserved_path=reserved,
             meta_path=m, population_path=None)
    expect("reserved cf_group in training", cf["verdict"], "fail")

    # 5. no sealed set -> suspect, NOT pass. The load-bearing case.
    c, m = corpus_and_meta("nosealed", train_ids)
    none = run("nosealed", corpus=c, sealed_path=None, reserved_path=reserved,
               meta_path=m, population_path=None)
    expect("missing sealed set", none["verdict"], "suspect",
           "— 'nothing was checked' must never exit 0")

    # 6. sealed set present but no ids to test -> suspect, not a vacuous pass
    bare = jw(os.path.join(tmp, "bare.jsonl"), [
        {"messages": [{"role": "user", "content": f"q{i}"},
                      {"role": "assistant", "content": "a"}]} for i in range(5)])
    vac = run("bare", corpus=bare, sealed_path=manifest, reserved_path=None,
              meta_path=None, population_path=None)
    expect("no candidate ids", vac["verdict"], "suspect",
           "— an empty intersection is not evidence of disjointness")

    # 7. population drift -> suspect, even with zero overlap
    pop_dir = os.path.join(tmp, "pop")
    os.makedirs(pop_dir, exist_ok=True)
    for i, a in enumerate(population + ["acct-added-after-seal"]):
        dump(os.path.join(pop_dir, f"{i:03d}.json"), {"account_id": a})
    c, m = corpus_and_meta("drift", train_ids)
    drift = run("drift", corpus=c, sealed_path=manifest, reserved_path=reserved,
                meta_path=m, population_path=pop_dir)
    expect("drifted population", drift["verdict"], "suspect")
    expect("drift dimension", drift["dimensions"]["population_drift"]["status"], "drifted")

    # 8. matching population -> drift clean, overall pass
    pop_ok = os.path.join(tmp, "pop_ok")
    os.makedirs(pop_ok, exist_ok=True)
    for i, a in enumerate(population):
        dump(os.path.join(pop_ok, f"{i:03d}.json"), {"account_id": a})
    ok = run("drift_ok", corpus=c, sealed_path=manifest, reserved_path=reserved,
             meta_path=m, population_path=pop_ok)
    expect("intact population", ok["dimensions"]["population_drift"]["status"], "clean")
    expect("intact population verdict", ok["verdict"], "pass")

    # 9. a tampered raw-id seal is unusable, not clean
    tampered = dump(os.path.join(tmp, "tampered.json"), {
        "heldout": [{"id": i} for i in held_ids],
        "seal_sha256": "0" * 64, "heldout_count": len(held_ids), "train_count": 20,
    })
    tam = run("tampered", corpus=c, sealed_path=tampered, reserved_path=None,
              meta_path=m, population_path=None)
    expect("tampered seal", tam["verdict"], "suspect")

    # 10. an intact raw-id seal is read and diffed
    intact = dump(os.path.join(tmp, "intact.json"), {
        "heldout": [{"id": i} for i in held_ids],
        "seal_sha256": hashlib.sha256("\n".join(sorted(held_ids)).encode()).hexdigest(),
        "heldout_count": len(held_ids), "train_count": 20,
    })
    raw_ok = run("intact", corpus=c, sealed_path=intact, reserved_path=None,
                 meta_path=m, population_path=None)
    expect("intact raw-id seal", raw_ok["dimensions"]["sealed_heldout"]["status"], "clean")

    # 11. a seal whose fingerprints belong to a different identifier namespace
    # resolves into nothing -- its empty intersection is not disjointness.
    alien = dump(os.path.join(tmp, "alien.json"), {
        "org_slug": "selftest", "sealed": True, "hash_algo": HASH_ALGO,
        "population_size": len(population), "heldout_count": 3,
        "population_fingerprint": population_fingerprint(population),
        "heldout_fingerprints": sorted(fp16(f"row-{i}-of-some-other-table") for i in range(3)),
    })
    mismatch = run("alien", corpus=c, sealed_path=alien, reserved_path=reserved,
                   meta_path=m, population_path=pop_ok)
    expect("namespace mismatch", mismatch["verdict"], "suspect",
           "— an empty intersection between unrelated id spaces is not disjointness")

    shutil.rmtree(tmp, ignore_errors=True)

    for f in fails:
        print(f)
    if not fails:
        print("self-test PASS — 11 fixtures: clean corpus passed; held-out id via "
              "sidecar and via record text both failed; reserved user-surface and "
              "cf_group re-use both failed; missing seal, empty candidate set, "
              "drifted population, tampered seal and a namespace mismatch all "
              "returned SUSPECT (never 0).")
    return 0 if not fails else 1


# ---------------------------------------------------------------------------

def _fmt(dim_name, dim):
    status = dim.get("status")
    mark = {"clean": "clean  ", "leak": "LEAK   ", "drifted": "DRIFT  ",
            "not-evaluated": "UNCHECKED"}.get(status, status or "")
    return f"  {mark:10s} {dim_name}"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Reserved-key / leakage diff vs EVAL_RESERVED (S5 battery check #4)")
    ap.add_argument("--corpus", help="path to the training corpus .jsonl")
    ap.add_argument("--slug", help="org slug (default: parsed from the corpus filename)")
    ap.add_argument("--heldout-manifest", help="override the sealed held-out manifest path")
    ap.add_argument("--reserved", help="override the EVAL_RESERVED .jsonl path")
    ap.add_argument("--meta", help="override the .meta.jsonl sidecar path")
    ap.add_argument("--population", help="current population (dir of dossier .json, or a "
                                         ".jsonl/.json of items) for the drift comparison")
    ap.add_argument("--self-test", action="store_true", help="prove the diff detects")
    ap.add_argument("--json", action="store_true", help="emit the full result as JSON")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()
    if not args.corpus:
        ap.error("--corpus is required unless --self-test")
    if not os.path.isfile(args.corpus):
        print(f"SUSPECT: corpus not found: {args.corpus}", file=sys.stderr)
        return 2

    slug = args.slug or slug_from_corpus(args.corpus)
    sealed_path = args.heldout_manifest or find_sealed(slug)
    reserved_path = args.reserved or find_reserved(slug)
    meta_path = args.meta or re.sub(r"\.jsonl$", ".meta.jsonl", args.corpus)

    result = analyze(args.corpus, sealed_path=sealed_path, reserved_path=reserved_path,
                     meta_path=meta_path, population_path=args.population)
    result["org_slug"] = slug

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"org={slug} records={result['records']} "
              f"meta_sidecar={'yes' if result['meta_sidecar'] else 'no'}")
        sh = result["dimensions"].get("sealed_heldout", {})
        if sh.get("status") in ("clean", "leak"):
            print(f"sealed held-out: {sh['heldout_count']} fingerprints "
                  f"({sh['manifest_shape']}) vs {sh['candidate_ids_tested']} candidate id(s) "
                  f"from the corpus -> {sh['overlap_count']} overlap")
        rs = result["dimensions"].get("reserved_source")
        if rs:
            print(f"EVAL_RESERVED: {rs['records']} records from {rs['path']}")
        for name in ("sealed_heldout", "reserved_cf_group", "reserved_user_surface",
                     "reserved_record_hash", "population_drift"):
            if name in result["dimensions"]:
                print(_fmt(name, result["dimensions"][name]))
        for leak in result["leaks"][:20]:
            print(f"   LEAK: {leak}")
        for blocker in result["blockers"]:
            print(f"   UNVERIFIED: {blocker}")
        print(f"VERDICT: {result['verdict'].upper()}")

    return {"pass": 0, "fail": 1, "suspect": 2}[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
