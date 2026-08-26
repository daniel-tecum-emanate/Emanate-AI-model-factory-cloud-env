#!/usr/bin/env python3
"""Deterministic corpus verifier -- check #1 of the S5 verify battery.

WHY THIS EXISTS: on 2026-08-25 the battery was run for real against grand-steel
and reported `script_on_disk: false` for five of its six checks. This one
referenced `scripts/v5/verify-v5-corpus.py`, which existed in NEITHER repository
-- not the canonical cloud-env copy, not the workflow copy. It was never carried
over from the earlier PTC work. S5 correctly returned `suspect` rather than
`pass`, so nothing pretended the corpus was verified; but the practical
consequence was that a run could reach the S6g human spend gate holding a cost
projection for a training file that had been structurally verified by nothing at
all.

WHAT IT GUARDS: this is the structural gate on the training file itself -- the
last thing between a malformed corpus and a GPU bill. The failures it exists to
catch are the ones a trainer will accept in silence:

  * a truncated assistant target teaches the model to stop mid-sentence. The
    trainer sees a well-formed string and charges full price to learn it.
  * an inverted loss mask (weight on a `tool` message) teaches the model to
    emit retrieval output from memory -- a hallucination generator built on
    purpose, and invisible in the loss curve.
  * a tool call with no answering `tool` message mid-trajectory teaches a stall.
    The same shape at the END of a record is the legitimate label for "call this
    next", so position is what separates a defect from a design.
  * a number in an assistant answer that appears nowhere in that record's own
    retrieved context is ungrounded specificity -- the corpus teaching the model
    to invent plausible figures. This is scoped to records that carry a `tools`
    block, because a general-knowledge record has no retrieval to ground against
    and legitimately states world facts.
  * a record over the training context cap is right-truncated by the trainer,
    which silently drops the assistant label. Full token cost, zero signal.
  * a held-out probe leaking into training makes every downstream eval number a
    lie, and nothing later in the pipeline can detect it.

COVERAGE IS LITERAL. Every record is read and every applicable rule is applied to
every record. Nothing is sampled. The `records_checked` count in the output must
equal the record count of the file, and it is asserted, not asserted-by-comment.

The check is deterministic, needs no model, and costs $0 -- every verdict below
is a count, a rate, a regex or a schema check.

    python3 scripts/v5/verify-v5-corpus.py --corpus <path.jsonl>
    python3 scripts/v5/verify-v5-corpus.py --corpus <path.jsonl> --json
    python3 scripts/v5/verify-v5-corpus.py --self-test

Exit 0 = PASS, 1 = FAIL, 2 = could not evaluate (which the caller must treat as
`suspect`, never as a pass -- `audit_lib`'s degradation lattice).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter

# ---------------------------------------------------------------- thresholds
# Structural caps. These are guard rails against records the trainer would
# silently truncate, not style limits -- they sit far above anything a healthy
# corpus produces, so tripping one means something upstream went wrong.
CAP_MESSAGES_PER_RECORD = 64
CAP_TOOL_CALLS_PER_TURN = 16
CAP_TOOLS_PER_RECORD = 64
CAP_EST_TOKENS_PER_RECORD = 32768
# Rough chars-per-token. Deliberately generous: the cap exists to catch a record
# that is an order of magnitude too long, not to police the last 5%.
CHARS_PER_TOKEN = 4

# Record-level weight sanity. A weight <= 0 silently deletes the record from
# training while still counting toward the projected token spend.
WEIGHT_MIN_EXCLUSIVE = 0.0
WEIGHT_MAX = 10.0

LEGAL_ROLES = {"system", "user", "assistant", "tool"}

# Characters a complete assistant answer is allowed to end on. A letter, a
# digit, a comma or a dangling operator means the generation was cut off.
TERMINAL_CHARS = set(".!?\"'`)]}:;$*_%…»。？！")

# Markers that must never appear in a training file: they are how a held-out
# probe is labelled, and their presence means the eval set has leaked in.
PROBE_MARKER_KEYS = {
    "probe", "is_probe", "holdout", "is_holdout", "held_out",
    "split", "eval", "is_eval", "do_not_train",
}
PROBE_CANARY = re.compile(
    r"do[\s_-]*not[\s_-]*train|held[\s_-]out|probe[\s_-]only|canary[\s_-]?string",
    re.IGNORECASE,
)

# Number extraction for the grounding rule. Thousands separators are removed
# first so "554,936.68" and "554936.68" are the same fact. A token counts as a
# *specific* claim if it has three or more integer digits or any decimal part --
# below that it is prose ("two of the three coils") and grounding is meaningless.
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _significant_numbers(text) -> set[str]:
    if not text:
        return set()
    flat = _THOUSANDS.sub("", str(text))
    out = set()
    for m in _NUMBER.finditer(flat):
        tok = m.group(0)
        if "." in tok or len(tok.split(".")[0]) >= 3:
            out.add(tok)
    return out


def _looks_truncated(text: str) -> str | None:
    """Return the truncation signal that fired, or None if the text is whole.

    Three independent signals, checked in order of confidence. Unbalanced
    emphasis is checked before the terminal character because a legitimate
    bolded closing line ("**Low Demand = Low Price**") ends on `*` and must not
    be confused with a generation cut off inside an opening `**`.
    """
    s = text.rstrip()
    if not s:
        return "empty"
    if s.count("```") % 2:
        return "unclosed_code_fence"
    if s.count("**") % 2:
        return "unbalanced_bold_emphasis"
    if s[-1] not in TERMINAL_CHARS:
        return "no_terminal_punctuation"
    return None


# ------------------------------------------------------------------- rules
RULE_IDS = [
    "parse",
    "role_sequence",
    "weights",
    "loss_masks",
    "content_integrity",
    "duplicates",
    "tool_call_structure",
    "caps",
    "verbatim_grounding",
    "probe_guard",
    "manifest_integrity",
]


class Report:
    """Per-rule violation accumulator. A rule with zero violations passed."""

    def __init__(self) -> None:
        self.violations: dict[str, list[dict]] = {r: [] for r in RULE_IDS}
        self.stats: dict = {}
        self.not_run: dict[str, str] = {}

    def fail(self, rule: str, line: int, detail: str, **extra) -> None:
        entry = {"line": line, "detail": detail}
        entry.update(extra)
        self.violations[rule].append(entry)

    def counts(self) -> dict[str, int]:
        return {r: len(v) for r, v in self.violations.items()}


def _check_record(rec: dict, line_no: int, rep: Report, agg: dict) -> None:
    """Apply every record-scoped rule to one record. No sampling, no early exit."""
    messages = rec.get("messages")
    if not isinstance(messages, list) or not messages:
        rep.fail("parse", line_no, "record has no non-empty `messages` array")
        return
    if not all(isinstance(m, dict) for m in messages):
        rep.fail("parse", line_no, "`messages` contains a non-object element")
        return

    roles = [m.get("role") for m in messages]
    has_tools_block = bool(rec.get("tools"))

    # ---- role_sequence -----------------------------------------------------
    for i, r in enumerate(roles):
        if r not in LEGAL_ROLES:
            rep.fail("role_sequence", line_no, f"illegal role {r!r} at position {i}")
    if roles.count("system") != 1:
        rep.fail("role_sequence", line_no,
                 f"expected exactly 1 system message, found {roles.count('system')}")
    elif roles[0] != "system":
        rep.fail("role_sequence", line_no,
                 f"system message is at position {roles.index('system')}, must be position 0")
    if "user" not in roles:
        rep.fail("role_sequence", line_no, "no user message")
    if "assistant" not in roles:
        rep.fail("role_sequence", line_no, "no assistant message — nothing to train on")
    if roles[-1] != "assistant":
        rep.fail("role_sequence", line_no,
                 f"record ends on a {roles[-1]!r} message; the final turn must be the assistant label")
    for i in range(len(roles) - 1):
        if roles[i] == "assistant" and roles[i + 1] == "assistant":
            rep.fail("role_sequence", line_no, f"adjacent assistant turns at positions {i},{i + 1}")

    # ---- tool-call / tool-answer pairing ----------------------------------
    # `pending` holds ids issued by the most recent assistant turn. They must be
    # answered, in order, before anything else happens -- except at the very end
    # of the record, where an unanswered call IS the training label.
    pending: list[str] = []
    pending_owner = -1
    declared = set()
    for t in rec.get("tools") or []:
        if isinstance(t, dict) and isinstance(t.get("function"), dict):
            declared.add(t["function"].get("name"))

    for i, m in enumerate(messages):
        role = m.get("role")
        calls = m.get("tool_calls")
        if role == "assistant" and calls:
            if pending:
                rep.fail("role_sequence", line_no,
                         f"assistant issues new tool calls at position {i} while "
                         f"{len(pending)} earlier call(s) are unanswered")
                pending = []
            if not isinstance(calls, list) or not calls:
                rep.fail("tool_call_structure", line_no, f"`tool_calls` at position {i} is not a non-empty list")
                continue
            if len(calls) > CAP_TOOL_CALLS_PER_TURN:
                rep.fail("caps", line_no,
                         f"{len(calls)} tool calls in one turn exceeds cap {CAP_TOOL_CALLS_PER_TURN}")
            if not has_tools_block:
                rep.fail("tool_call_structure", line_no,
                         f"assistant calls a tool at position {i} but the record declares no `tools`")
            seen_ids = set()
            for tc in calls:
                if not isinstance(tc, dict):
                    rep.fail("tool_call_structure", line_no, f"tool_call at position {i} is not an object")
                    continue
                tcid = tc.get("id")
                fn = tc.get("function")
                if not tcid or not isinstance(tcid, str):
                    rep.fail("tool_call_structure", line_no, f"tool_call at position {i} has no string `id`")
                elif tcid in seen_ids:
                    rep.fail("tool_call_structure", line_no, f"duplicate tool_call id {tcid!r} within record")
                else:
                    seen_ids.add(tcid)
                    pending.append(tcid)
                if tc.get("type") != "function":
                    rep.fail("tool_call_structure", line_no,
                             f"tool_call {tcid!r} has type {tc.get('type')!r}, expected 'function'")
                if not isinstance(fn, dict):
                    rep.fail("tool_call_structure", line_no, f"tool_call {tcid!r} has no `function` object")
                    continue
                name = fn.get("name")
                if not name or not isinstance(name, str):
                    rep.fail("tool_call_structure", line_no, f"tool_call {tcid!r} has no function name")
                elif declared and name not in declared:
                    rep.fail("tool_call_structure", line_no,
                             f"tool_call invokes {name!r}, which is not in the record's declared tools")
                args = fn.get("arguments")
                if not isinstance(args, str):
                    rep.fail("tool_call_structure", line_no,
                             f"tool_call {tcid!r} arguments must be a JSON string, got {type(args).__name__}")
                else:
                    try:
                        parsed = json.loads(args)
                    except json.JSONDecodeError as exc:
                        rep.fail("tool_call_structure", line_no,
                                 f"tool_call {tcid!r} arguments are not valid JSON: {exc.msg}")
                    else:
                        if not isinstance(parsed, dict):
                            rep.fail("tool_call_structure", line_no,
                                     f"tool_call {tcid!r} arguments decode to {type(parsed).__name__}, expected object")
                agg["tool_names"][fn.get("name")] += 1
            pending_owner = i
        elif role == "tool":
            tcid = m.get("tool_call_id")
            if not pending:
                rep.fail("role_sequence", line_no,
                         f"tool message at position {i} answers no preceding assistant tool call")
            elif tcid not in pending:
                rep.fail("role_sequence", line_no,
                         f"tool message at position {i} carries tool_call_id {tcid!r}, "
                         f"which no pending assistant call issued")
            else:
                if pending[0] != tcid:
                    rep.fail("role_sequence", line_no,
                             f"tool results out of order at position {i}: got {tcid!r}, expected {pending[0]!r}")
                pending.remove(tcid)
        elif role in ("user", "system") and pending:
            rep.fail("role_sequence", line_no,
                     f"{len(pending)} tool call(s) left unanswered before a {role} message at position {i}")
            pending = []

    if pending:
        if pending_owner == len(messages) - 1:
            agg["terminal_tool_call_records"] += 1
        else:
            rep.fail("role_sequence", line_no,
                     f"{len(pending)} tool call(s) issued at position {pending_owner} are never answered "
                     "and are not the record's final turn")

    # ---- weights + loss masks ---------------------------------------------
    w = rec.get("weight")
    if w is None:
        rep.fail("weights", line_no, "record has no `weight`")
    elif isinstance(w, bool) or not isinstance(w, (int, float)):
        rep.fail("weights", line_no, f"record weight is {type(w).__name__}, expected a number")
    else:
        agg["record_weights"][float(w)] += 1
        if not (WEIGHT_MIN_EXCLUSIVE < float(w) <= WEIGHT_MAX):
            rep.fail("weights", line_no,
                     f"record weight {w} outside sane range ({WEIGHT_MIN_EXCLUSIVE}, {WEIGHT_MAX}]")

    for i, m in enumerate(messages):
        role = m.get("role")
        mw = m.get("weight")
        if mw is None:
            rep.fail("loss_masks", line_no, f"message at position {i} ({role}) has no `weight` mask")
            continue
        if isinstance(mw, bool) or not isinstance(mw, (int, float)):
            rep.fail("loss_masks", line_no,
                     f"message at position {i} ({role}) mask is {type(mw).__name__}, expected a number")
            continue
        agg["message_weights"][(role, float(mw))] += 1
        if role == "assistant":
            if float(mw) <= 0:
                rep.fail("loss_masks", line_no,
                         f"assistant message at position {i} is masked out (weight {mw}) — nothing is learned from it")
        elif float(mw) != 0.0:
            rep.fail("loss_masks", line_no,
                     f"{role} message at position {i} carries loss weight {mw}; non-assistant turns must be "
                     "masked to 0 or the model is trained to emit retrieval output from memory")

    # ---- content integrity -------------------------------------------------
    for i, m in enumerate(messages):
        role = m.get("role")
        content = m.get("content")
        if role == "assistant":
            has_calls = bool(m.get("tool_calls"))
            if content is None:
                if not has_calls:
                    rep.fail("content_integrity", line_no,
                             f"assistant message at position {i} has null content and no tool calls — empty label")
                continue
            if not isinstance(content, str):
                rep.fail("content_integrity", line_no,
                         f"assistant content at position {i} is {type(content).__name__}, expected string or null")
                continue
            if not content.strip():
                if not has_calls:
                    rep.fail("content_integrity", line_no,
                             f"assistant message at position {i} is empty")
                continue
            signal = _looks_truncated(content)
            if signal:
                agg["truncation_signals"][signal] += 1
                rep.fail("content_integrity", line_no,
                         f"assistant text at position {i} is truncated ({signal})",
                         tail=content.rstrip()[-60:])
        else:
            if not isinstance(content, str) or not content.strip():
                rep.fail("content_integrity", line_no,
                         f"{role} message at position {i} has empty or non-string content")

    # ---- caps --------------------------------------------------------------
    if len(messages) > CAP_MESSAGES_PER_RECORD:
        rep.fail("caps", line_no,
                 f"{len(messages)} messages exceeds cap {CAP_MESSAGES_PER_RECORD}")
    tools = rec.get("tools") or []
    if len(tools) > CAP_TOOLS_PER_RECORD:
        rep.fail("caps", line_no, f"{len(tools)} declared tools exceeds cap {CAP_TOOLS_PER_RECORD}")
    chars = 0
    for m in messages:
        chars += len(str(m.get("content") or ""))
        for tc in m.get("tool_calls") or []:
            if isinstance(tc, dict):
                chars += len(json.dumps(tc))
    chars += len(json.dumps(tools))
    est_tokens = chars // CHARS_PER_TOKEN
    agg["est_tokens"].append(est_tokens)
    if est_tokens > CAP_EST_TOKENS_PER_RECORD:
        rep.fail("caps", line_no,
                 f"~{est_tokens} tokens exceeds context cap {CAP_EST_TOKENS_PER_RECORD}; "
                 "the trainer would right-truncate this record and drop its label")

    # ---- verbatim grounding ------------------------------------------------
    # Scoped to the grounded lane: records that declare tools are answering from
    # retrieval, so every specific figure must be traceable to this record's own
    # context. Records with no tools block are general-knowledge retention data
    # with nothing to ground against; they are counted, not silently dropped.
    if has_tools_block:
        agg["grounded_records"] += 1
        context: set[str] = set()
        for m in messages:
            if m.get("role") in ("system", "user", "tool"):
                context |= _significant_numbers(m.get("content"))
            for tc in m.get("tool_calls") or []:
                if isinstance(tc, dict) and isinstance(tc.get("function"), dict):
                    context |= _significant_numbers(tc["function"].get("arguments"))
        for i, m in enumerate(messages):
            if m.get("role") != "assistant" or not isinstance(m.get("content"), str):
                continue
            for n in sorted(_significant_numbers(m["content"])):
                agg["grounded_numbers"] += 1
                if n not in context:
                    rep.fail("verbatim_grounding", line_no,
                             f"assistant at position {i} states {n!r}, which appears nowhere in this "
                             "record's own context — ungrounded specificity")
    else:
        agg["ungrounded_lane_records"] += 1

    # ---- probe guard (in-band markers) ------------------------------------
    for key in rec.keys():
        if key.lower().lstrip("_") in PROBE_MARKER_KEYS:
            rep.fail("probe_guard", line_no,
                     f"training record carries held-out marker key {key!r} = {rec[key]!r}")
    for i, m in enumerate(messages):
        c = m.get("content")
        if isinstance(c, str):
            hit = PROBE_CANARY.search(c)
            if hit:
                rep.fail("probe_guard", line_no,
                         f"probe canary {hit.group(0)!r} found in {m.get('role')} content at position {i}")


def _load_probe_prompts(path: str) -> set[str]:
    """Fingerprint every user prompt in a held-out probe file."""
    prompts: set[str] = set()
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            for m in row.get("messages") or []:
                if m.get("role") == "user" and isinstance(m.get("content"), str):
                    prompts.add(_fingerprint(m["content"]))
    return prompts


def _fingerprint(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().lower().encode("utf-8")).hexdigest()


def analyze(path: str, probe_set: str | None = None, manifest: str | None = None,
            manifest_required: bool = False, probe_required: bool = False) -> dict:
    if not os.path.isfile(path):
        return {"verdict": "suspect", "reason": f"corpus not found or not a file: {path}"}

    raw_lines: list[tuple[int, str]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, start=1):
            if line.strip():
                raw_lines.append((n, line.strip()))

    if not raw_lines:
        return {"verdict": "suspect", "reason": f"corpus is empty — nothing to verify: {path}"}

    rep = Report()
    agg = {
        "record_weights": Counter(),
        "message_weights": Counter(),
        "tool_names": Counter(),
        "truncation_signals": Counter(),
        "est_tokens": [],
        "grounded_records": 0,
        "ungrounded_lane_records": 0,
        "grounded_numbers": 0,
        "terminal_tool_call_records": 0,
    }

    records: list[dict | None] = []
    hashes = Counter()
    hash_first_line: dict[str, int] = {}
    user_prompts = Counter()
    checked = 0

    for line_no, text in raw_lines:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        hashes[digest] += 1
        hash_first_line.setdefault(digest, line_no)
        try:
            rec = json.loads(text)
        except json.JSONDecodeError as exc:
            rep.fail("parse", line_no, f"line is not valid JSON: {exc.msg}")
            records.append(None)
            continue
        if not isinstance(rec, dict):
            rep.fail("parse", line_no, f"line decodes to {type(rec).__name__}, expected an object")
            records.append(None)
            continue
        records.append(rec)
        _check_record(rec, line_no, rep, agg)
        checked += 1
        for m in rec.get("messages") or []:
            if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str):
                user_prompts[_fingerprint(m["content"])] += 1

    # ---- duplicates (whole-file scope) ------------------------------------
    dup_groups = [(h, c) for h, c in hashes.items() if c > 1]
    for h, c in dup_groups:
        rep.fail("duplicates", hash_first_line[h],
                 f"record appears {c} times byte-identically (sha256 {h[:16]}…)")

    # ---- manifest integrity ------------------------------------------------
    if manifest is None:
        sibling = path[:-6] + ".meta.jsonl" if path.endswith(".jsonl") else path + ".meta.jsonl"
        manifest = sibling if os.path.isfile(sibling) else None
        auto = manifest is not None
    else:
        auto = False
    manifest_stats = None
    if manifest:
        if not os.path.isfile(manifest):
            return {"verdict": "suspect", "reason": f"manifest supplied but not readable: {manifest}"}
        try:
            meta = [json.loads(l) for l in open(manifest, "r", encoding="utf-8") if l.strip()]
        except json.JSONDecodeError as exc:
            return {"verdict": "suspect", "reason": f"manifest is not valid JSONL: {manifest}: {exc.msg}"}
        manifest_stats = {"path": manifest, "auto_detected": auto, "rows": len(meta)}
        if len(meta) != len(raw_lines):
            rep.fail("manifest_integrity", 0,
                     f"manifest has {len(meta)} rows for {len(raw_lines)} corpus records")
        for idx, row in enumerate(meta[:len(raw_lines)]):
            line_no, text = raw_lines[idx]
            want = row.get("sha256_record")
            if want is None:
                continue
            got = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if got != want:
                rep.fail("manifest_integrity", line_no,
                         f"record hash {got[:16]}… does not match manifest {str(want)[:16]}… — "
                         "the corpus was mutated after export")
            declared_line = row.get("line")
            if declared_line is not None and declared_line != idx + 1:
                rep.fail("manifest_integrity", line_no,
                         f"manifest row {idx + 1} declares line {declared_line} — manifest is misaligned")
    else:
        rep.not_run["manifest_integrity"] = (
            "no `<corpus>.meta.jsonl` sidecar found and none supplied via --manifest; "
            "per-record export hashes were NOT verified"
        )
        if manifest_required:
            return {"verdict": "suspect", "reason": "--require-manifest set but no manifest found"}

    # ---- probe guard (cross-file contamination) ---------------------------
    contamination = 0
    if probe_set:
        if not os.path.isfile(probe_set):
            return {"verdict": "suspect", "reason": f"probe set supplied but not readable: {probe_set}"}
        try:
            probes = _load_probe_prompts(probe_set)
        except json.JSONDecodeError as exc:
            return {"verdict": "suspect", "reason": f"probe set is not valid JSONL: {probe_set}: {exc.msg}"}
        for idx, rec in enumerate(records):
            if rec is None:
                continue
            line_no = raw_lines[idx][0]
            for m in rec.get("messages") or []:
                if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str):
                    if _fingerprint(m["content"]) in probes:
                        contamination += 1
                        rep.fail("probe_guard", line_no,
                                 "user prompt is byte-equal to a held-out probe prompt — eval contamination")
    else:
        rep.not_run["probe_guard.contamination"] = (
            "no --probe-set supplied; only in-band probe markers were scanned. "
            "Cross-file held-out contamination was NOT verified"
        )
        if probe_required:
            return {"verdict": "suspect", "reason": "--require-probe-set set but no probe set supplied"}

    # ---- assemble ----------------------------------------------------------
    counts = rep.counts()
    failed_rules = {r: c for r, c in counts.items() if c}
    est = sorted(agg["est_tokens"])
    dup_user = sum(c - 1 for c in user_prompts.values() if c > 1)

    result = {
        "verdict": "fail" if failed_rules else "pass",
        "corpus": path,
        "records_in_file": len(raw_lines),
        "records_checked": checked,
        "coverage": "100%" if checked == len(raw_lines) else
                    f"{checked}/{len(raw_lines)} — {len(raw_lines) - checked} record(s) could not be parsed",
        "rule_violation_counts": counts,
        "failed_rules": sorted(failed_rules),
        "stats": {
            "record_weight_distribution": {str(k): v for k, v in sorted(agg["record_weights"].items())},
            "message_mask_distribution": {f"{r}@{w:g}": c for (r, w), c in sorted(
                agg["message_weights"].items(), key=lambda kv: (kv[0][0], kv[0][1]))},
            "exact_duplicate_groups": len(dup_groups),
            "exact_duplicate_extra_copies": sum(c - 1 for _, c in dup_groups),
            "duplicate_user_prompts_extra_copies": dup_user,
            "distinct_user_prompts": len(user_prompts),
            "grounded_lane_records": agg["grounded_records"],
            "grounded_lane_numbers_checked": agg["grounded_numbers"],
            "ungrounded_lane_records": agg["ungrounded_lane_records"],
            "records_ending_on_tool_call": agg["terminal_tool_call_records"],
            "distinct_tools_called": len(agg["tool_names"]),
            "truncation_signals": dict(agg["truncation_signals"]),
            "est_tokens_min": est[0] if est else 0,
            "est_tokens_median": est[len(est) // 2] if est else 0,
            "est_tokens_max": est[-1] if est else 0,
            "est_tokens_total": sum(est),
            "probe_contamination_hits": contamination,
        },
        "manifest": manifest_stats,
        "rules_not_run": rep.not_run,
        "violations": {r: v[:20] for r, v in rep.violations.items() if v},
        "violations_truncated_at": 20,
    }
    return result


# ------------------------------------------------------------------ self-test
def _self_test() -> int:
    """Prove the verifier detects, rather than only that it runs.

    A checker that always passes is worse than no checker, because it converts
    "unverified" into "verified" on the report. So this asserts a clean fixture
    PASSES and that each deliberately-broken fixture FAILS *on the specific rule
    that owns the defect* — a fixture failing for the wrong reason would not
    prove the rule works. It also asserts the two degradation cases return
    suspect rather than quietly passing.
    """
    import tempfile

    def clean_record(idx: int, with_tools: bool = True) -> dict:
        tools = [{
            "type": "function",
            "function": {"name": "read_account", "description": "read",
                         "parameters": {"type": "object", "properties": {}}},
        }]
        rec = {
            "messages": [
                {"role": "system", "content": "You are a steel service center agent.", "weight": 0},
                {"role": "user", "content": f"What is the status of order {4000 + idx}?", "weight": 0},
                {"role": "assistant", "content": None, "weight": 1,
                 "tool_calls": [{"id": f"call_{idx}", "type": "function",
                                 "function": {"name": "read_account", "arguments": "{}"}}]},
                {"role": "tool", "content": f"order {4000 + idx}: 12 coils, shipped", "weight": 0,
                 "tool_call_id": f"call_{idx}"},
                {"role": "assistant", "content": f"Order {4000 + idx} shipped with 12 coils.", "weight": 1},
            ],
            "weight": 1.0,
        }
        if with_tools:
            rec["tools"] = tools
        return rec

    def write(rows, raw=None) -> str:
        fd, p = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as fh:
            if raw is not None:
                fh.write(raw)
            else:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
        return p

    def mutate(fn):
        rows = [clean_record(i) for i in range(6)]
        fn(rows)
        return write(rows)

    cases: list[tuple[str, str, str | None]] = []
    paths: list[str] = []

    clean = write([clean_record(i) for i in range(6)])
    paths.append(clean)
    cases.append(("clean corpus", clean, None))

    def _two_systems(rows):
        rows[2]["messages"].insert(1, {"role": "system", "content": "extra", "weight": 0})

    def _system_not_first(rows):
        m = rows[3]["messages"]
        m[0], m[1] = m[1], m[0]

    def _adjacent_assistant(rows):
        rows[1]["messages"].append({"role": "assistant", "content": "And also this.", "weight": 1})

    def _orphan_tool(rows):
        rows[4]["messages"][3]["tool_call_id"] = "call_does_not_exist"

    def _unanswered_midway(rows):
        del rows[0]["messages"][3]

    def _bad_weight(rows):
        rows[2]["weight"] = 0

    def _missing_weight(rows):
        del rows[3]["weight"]

    def _mask_inverted(rows):
        rows[1]["messages"][3]["weight"] = 1

    def _assistant_masked_out(rows):
        rows[5]["messages"][4]["weight"] = 0

    def _truncated(rows):
        rows[2]["messages"][4]["content"] = "Order 4002 shipped with 12 coils and the balance is still"

    def _empty_label(rows):
        rows[3]["messages"][4]["content"] = None

    def _bad_tool_args(rows):
        rows[1]["messages"][2]["tool_calls"][0]["function"]["arguments"] = "{not json"

    def _undeclared_tool(rows):
        rows[2]["messages"][2]["tool_calls"][0]["function"]["name"] = "delete_everything"

    def _over_cap(rows):
        rows[0]["messages"][4]["content"] = "x " * (CAP_EST_TOKENS_PER_RECORD * CHARS_PER_TOKEN)

    def _ungrounded(rows):
        rows[4]["messages"][4]["content"] = "Order 4004 shipped with 12 coils worth $998877.12."

    def _probe_marker(rows):
        rows[2]["holdout"] = True

    def _probe_canary(rows):
        rows[3]["messages"][1]["content"] = "DO NOT TRAIN — held-out probe: what is the status?"

    for label, fn, rule in [
        ("two system messages", _two_systems, "role_sequence"),
        ("system not at position 0", _system_not_first, "role_sequence"),
        ("adjacent assistant turns", _adjacent_assistant, "role_sequence"),
        ("tool answers no call", _orphan_tool, "role_sequence"),
        ("mid-trajectory unanswered call", _unanswered_midway, "role_sequence"),
        ("record weight 0", _bad_weight, "weights"),
        ("record weight missing", _missing_weight, "weights"),
        ("loss mask on tool output", _mask_inverted, "loss_masks"),
        ("assistant masked out", _assistant_masked_out, "loss_masks"),
        ("truncated assistant text", _truncated, "content_integrity"),
        ("assistant label empty", _empty_label, "content_integrity"),
        ("tool arguments not JSON", _bad_tool_args, "tool_call_structure"),
        ("undeclared tool called", _undeclared_tool, "tool_call_structure"),
        ("record over context cap", _over_cap, "caps"),
        ("ungrounded number in answer", _ungrounded, "verbatim_grounding"),
        ("held-out marker key", _probe_marker, "probe_guard"),
        ("probe canary in prompt", _probe_canary, "probe_guard"),
    ]:
        p = mutate(fn)
        paths.append(p)
        cases.append((label, p, rule))

    dup_rows = [clean_record(i) for i in range(5)]
    dup_rows.append(json.loads(json.dumps(dup_rows[0])))
    dup = write(dup_rows)
    paths.append(dup)
    cases.append(("byte-identical duplicate", dup, "duplicates"))

    broken = write(None, raw=json.dumps(clean_record(0)) + "\n{ this is not json\n")
    paths.append(broken)
    cases.append(("unparseable line", broken, "parse"))

    ok = True
    for label, p, expect_rule in cases:
        res = analyze(p)
        if expect_rule is None:
            if res["verdict"] != "pass":
                print(f"SELF-TEST FAIL: {label} returned {res['verdict']}, expected pass "
                      f"({res.get('failed_rules') or res.get('reason')})")
                ok = False
            elif res["records_checked"] != res["records_in_file"]:
                print(f"SELF-TEST FAIL: {label} checked {res['records_checked']}/{res['records_in_file']}")
                ok = False
        else:
            if res["verdict"] != "fail":
                print(f"SELF-TEST FAIL: {label} returned {res['verdict']}, expected fail")
                ok = False
            elif expect_rule not in res["failed_rules"]:
                print(f"SELF-TEST FAIL: {label} failed on {res['failed_rules']}, "
                      f"expected rule {expect_rule!r}")
                ok = False

    # Degradation lattice: an input that cannot be evaluated must be suspect,
    # never a pass.
    empty = write([])
    paths.append(empty)
    for label, p in [("empty corpus", empty), ("missing corpus", "/nonexistent/corpus.jsonl")]:
        res = analyze(p)
        if res["verdict"] != "suspect":
            print(f"SELF-TEST FAIL: {label} returned {res['verdict']}, expected suspect")
            ok = False

    # A supplied-but-unreadable probe set must degrade, not silently skip.
    res = analyze(clean, probe_set="/nonexistent/probes.jsonl")
    if res["verdict"] != "suspect":
        print(f"SELF-TEST FAIL: unreadable probe set returned {res['verdict']}, expected suspect")
        ok = False

    # Contamination is actually detected when a probe set IS supplied.
    probe_file = write([clean_record(2)])
    paths.append(probe_file)
    res = analyze(clean, probe_set=probe_file)
    if res["verdict"] != "fail" or "probe_guard" not in res["failed_rules"]:
        print(f"SELF-TEST FAIL: probe contamination returned {res['verdict']} "
              f"{res.get('failed_rules')}, expected fail on probe_guard")
        ok = False

    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass

    if ok:
        print(f"self-test PASS — clean fixture passed; {len(cases) - 1} deliberately broken fixtures "
              "each failed on their own rule; empty/missing/unreadable inputs returned suspect; "
              "probe contamination detected")
    return 0 if ok else 1


# ----------------------------------------------------------------------- cli
def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic corpus verifier (S5 battery check #1)")
    ap.add_argument("--corpus", help="path to the training corpus .jsonl")
    ap.add_argument("--probe-set", help="held-out probe .jsonl to check for eval contamination")
    ap.add_argument("--manifest", help="export manifest .meta.jsonl (auto-detected as <corpus>.meta.jsonl)")
    ap.add_argument("--require-manifest", action="store_true",
                    help="return suspect if no export manifest is available")
    ap.add_argument("--require-probe-set", action="store_true",
                    help="return suspect if no probe set is supplied")
    ap.add_argument("--self-test", action="store_true", help="prove the verifier detects")
    ap.add_argument("--json", action="store_true", help="emit the full result as JSON")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()
    if not args.corpus:
        ap.error("--corpus is required unless --self-test")

    result = analyze(args.corpus, probe_set=args.probe_set, manifest=args.manifest,
                     manifest_required=args.require_manifest, probe_required=args.require_probe_set)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["verdict"] == "suspect":
            print(f"SUSPECT: {result['reason']}", file=sys.stderr)
            return 2
        s = result["stats"]
        print(f"corpus={result['corpus']}")
        print(f"records={result['records_in_file']} checked={result['records_checked']} "
              f"coverage={result['coverage']}")
        print(f"record weights: {s['record_weight_distribution']}")
        print(f"loss masks:     {s['message_mask_distribution']}")
        print(f"grounded lane:  {s['grounded_lane_records']} records, "
              f"{s['grounded_lane_numbers_checked']} numbers checked   "
              f"(non-grounded lane: {s['ungrounded_lane_records']})")
        print(f"duplicates:     {s['exact_duplicate_groups']} group(s), "
              f"{s['exact_duplicate_extra_copies']} extra copies   "
              f"(repeated user prompts: {s['duplicate_user_prompts_extra_copies']})")
        print(f"est tokens:     min {s['est_tokens_min']} / med {s['est_tokens_median']} / "
              f"max {s['est_tokens_max']} / total {s['est_tokens_total']}   "
              f"(cap {CAP_EST_TOKENS_PER_RECORD}/record)")
        print(f"tool calls:     {s['records_ending_on_tool_call']} records end on a tool-call label, "
              f"{s['distinct_tools_called']} distinct tools invoked")
        if result["manifest"]:
            m = result["manifest"]
            print(f"manifest:       {m['rows']} rows{' (auto-detected)' if m['auto_detected'] else ''} "
                  f"— {m['path']}")
        print("")
        print("per-rule violations:")
        for rule in RULE_IDS:
            n = result["rule_violation_counts"][rule]
            note = result["rules_not_run"].get(rule)
            if note is None and rule == "probe_guard":
                note = result["rules_not_run"].get("probe_guard.contamination")
            mark = "ok  " if n == 0 else "FAIL"
            print(f"   [{mark}] {rule:<20} {n} violation(s)")
            if note:
                print(f"          NOT RUN: {note}")
        for rule, entries in result["violations"].items():
            print("")
            print(f"{rule} — {result['rule_violation_counts'][rule]} violation(s), "
                  f"first {min(len(entries), 20)}:")
            for e in entries:
                extra = f"   tail={e['tail']!r}" if "tail" in e else ""
                print(f"   line {e['line']}: {e['detail']}{extra}")
        print("")
        print(f"VERDICT: {result['verdict'].upper()}")

    return {"pass": 0, "fail": 1, "suspect": 2}[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
