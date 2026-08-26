#!/usr/bin/env python3
"""Render-verify pre-upload — check #6 of the S5 verify battery.

WHY THIS EXISTS: on 2026-08-25 the battery was run for real against grand-steel
and reported `script_on_disk: false` for five of its six checks. This one
referenced `scripts/v5/render-verify-upload.py`, which existed in NEITHER
repository -- not the canonical cloud-env copy, not the workflow copy. S5
correctly returned `suspect` rather than `pass`, so nothing pretended the corpus
was verified; but the practical consequence was that a run could arrive at the
S6g human spend gate holding a cost projection while its corpus had been
verified by nothing at all.

WHAT FAILURE IT PREVENTS: a fine-tune only learns from the tokens inside its
loss mask. If the mask is wrong once the provider renders the conversation into
its chat template, the model trains on text it should never have learned to
produce -- the user's own words, tool output it is supposed to *consume*, or the
system prompt it is supposed to *obey*. That failure is close to invisible
afterwards: the loss curve looks ordinary, the job succeeds, and the model comes
back subtly wrong -- parroting user phrasing, hallucinating tool results it was
taught to emit, reciting its own instructions. No eval tells you why. The only
cheap moment to catch it is before the upload, which is here.

WHAT THIS CHECKS, AND WHAT IT HONESTLY CANNOT
---------------------------------------------
There is no Fireworks call in this script. The battery is deterministic and
$0 -- every verdict here is a count, a schema check, or an arithmetic identity
-- so the *live* renderer is out of reach by construction. That boundary is not
papered over. What is checkable offline is checked and gated; what is not is
named, per run, in `not_verifiable_offline`, and a PASS from this script means
exactly:

    "the mask that this corpus ASKS the renderer for is internally consistent
     and puts loss only on assistant turns"

It does NOT mean "the Fireworks render was inspected". Anything that genuinely
requires the live renderer to decide returns exit 2 SUSPECT rather than a guess:
a corpus that carries no explicit masking at all (masking would then be whatever
the provider's template defaults to), or one that mixes explicit and implicit
records (the precedence between a per-message `weight` and the record-level
`weight` is the provider's business, not ours). Faking a renderer to turn those
into a green tick is the one outcome worse than having no check.

The gates:
  1. every `weight` -- record-level and per-message -- is a finite number >= 0
  2. loss lands ONLY on assistant turns; any weighted system/user/tool message
     is a mask leak and fails
  3. every record has at least one loss-bearing assistant turn, and that turn
     renders to a non-empty target (content, or a tool call to serialize)
  4. no record's training target is a single token -- estimated, see below
  5. tool results resolve to a tool call that actually precedes them, and tool
     calls name a function the record declares, since either break changes what
     the template emits and therefore where the mask lands

Token counts here are ESTIMATES (~4 chars/token) and are labelled as such
everywhere they appear. The real tokenizer lives behind the provider; a share
reported by this script is a sanity signal, never a token count.

    python3 scripts/v5/render-verify-upload.py --corpus <path.jsonl>
    python3 scripts/v5/render-verify-upload.py --self-test

Exit 0 = PASS, 1 = FAIL, 2 = could not evaluate (which the caller must treat as
`suspect`, never as a pass -- `audit_lib`'s degradation lattice). When a run
turns up both real violations and un-evaluable records, the verdict is FAIL: the
violation is the actionable fact, and the un-evaluable count rides along in the
report rather than downgrading it.
"""

from __future__ import annotations

import argparse
import json
import math
import sys

# Roles whose content the model must consume, never reproduce. Loss on any of
# these is a mask leak.
NON_LOSS_ROLES = ("system", "user", "tool")

# A target of one token is not a training example; it is almost always a
# truncation or an export bug that emptied the assistant turn.
MIN_TARGET_TOKENS_EST = 2

# Rough bytes-per-token used ONLY for the estimated shares in the report. Never
# presented as a token count.
CHARS_PER_TOKEN_EST = 4.0

NOT_VERIFIABLE_OFFLINE = [
    "exact chat-template special tokens (BOS/EOS/turn delimiters) and their placement",
    "whether the template's end-of-turn token falls inside or outside the assistant loss mask",
    "tokenizer-exact token counts, and therefore the true loss-token share",
    "whether Fireworks honours per-message `weight` for this base model, or only record-level `weight`",
]


def _est_tokens(text: str) -> int:
    """Estimated token count. An estimate — see the module docstring."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN_EST))


def _is_weight(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _rendered_target(msg: dict) -> str:
    """What the renderer would actually emit for an assistant turn.

    Content may be null on a pure tool-calling turn, but the turn is not empty:
    the function name and its arguments are serialized into the target. Counting
    only `content` would flag every tool call in the corpus as an empty target.
    """
    parts = []
    content = msg.get("content")
    if content:
        parts.append(str(content))
    for call in msg.get("tool_calls") or []:
        fn = call.get("function") or {}
        parts.append(str(fn.get("name") or ""))
        parts.append(str(fn.get("arguments") or ""))
    return "".join(parts).strip()


def _mask_convention(messages: list) -> str:
    """How this record asks to be masked: explicit, implicit, or partial."""
    weighted = sum(1 for m in messages if "weight" in m)
    if weighted == 0:
        return "implicit"
    if weighted == len(messages):
        return "explicit"
    return "partial"


def _check_record(row: dict, index: int) -> dict:
    """Evaluate one record. Returns failures, un-evaluable reasons, and stats."""
    failures: list[str] = []
    unevaluable: list[str] = []

    messages = row.get("messages")
    if not isinstance(messages, list) or not messages:
        return {"failures": [f"record {index}: no `messages` array"], "unevaluable": [], "stats": None}

    record_weight = row.get("weight", 1)
    if not _is_weight(record_weight):
        failures.append(f"record {index}: record-level weight is not a finite number ({record_weight!r})")
    elif record_weight < 0:
        failures.append(f"record {index}: negative record-level weight ({record_weight})")
    elif record_weight == 0:
        failures.append(f"record {index}: record-level weight 0 — the whole record renders no loss")

    convention = _mask_convention(messages)
    if convention == "implicit":
        unevaluable.append(
            f"record {index}: no per-message `weight` anywhere — which turns carry loss is "
            "decided by the provider's template default and cannot be determined offline"
        )
    elif convention == "partial":
        unevaluable.append(
            f"record {index}: only some messages carry `weight` — precedence between per-message "
            "and record-level weight is provider-specific and cannot be resolved offline"
        )

    declared_tools = set()
    for tool in row.get("tools") or []:
        fn = tool.get("function") or {}
        if fn.get("name"):
            declared_tools.add(fn["name"])
    has_tools_block = row.get("tools") is not None

    seen_call_ids: set = set()
    loss_chars = 0
    total_chars = 0
    loss_bearing_turns = 0

    for position, msg in enumerate(messages):
        role = msg.get("role")
        weight = msg.get("weight")
        explicit = "weight" in msg

        if explicit and not _is_weight(weight):
            failures.append(f"record {index} msg {position} ({role}): weight is not a finite number ({weight!r})")
            explicit = False
        elif explicit and weight < 0:
            failures.append(f"record {index} msg {position} ({role}): negative weight ({weight})")

        if role == "assistant":
            target = _rendered_target(msg)
            total_chars += len(target)
            for call in msg.get("tool_calls") or []:
                if call.get("id"):
                    seen_call_ids.add(call["id"])
                name = (call.get("function") or {}).get("name")
                if not has_tools_block:
                    failures.append(
                        f"record {index} msg {position}: calls `{name}` but the record declares no `tools`"
                    )
                elif name not in declared_tools:
                    failures.append(
                        f"record {index} msg {position}: calls undeclared function `{name}`"
                    )
            if explicit and weight > 0:
                loss_bearing_turns += 1
                loss_chars += len(target)
                if not target:
                    failures.append(
                        f"record {index} msg {position}: loss-bearing assistant turn renders an empty target"
                    )
                elif _est_tokens(target) < MIN_TARGET_TOKENS_EST:
                    failures.append(
                        f"record {index} msg {position}: training target is ~1 token "
                        f"({len(target)} chars) — below the {MIN_TARGET_TOKENS_EST}-token floor"
                    )
        else:
            content = str(msg.get("content") or "")
            total_chars += len(content)
            if explicit and weight > 0:
                loss_chars += len(content)
                failures.append(
                    f"record {index} msg {position}: MASK LEAK — {role} content carries weight "
                    f"{weight}; the model would train on text it must only consume"
                )
            if role == "tool":
                call_id = msg.get("tool_call_id")
                if not call_id:
                    failures.append(f"record {index} msg {position}: tool result has no tool_call_id")
                elif call_id not in seen_call_ids:
                    failures.append(
                        f"record {index} msg {position}: tool result `{call_id}` matches no preceding "
                        "tool call — the renderer would emit an unanchored block, shifting the mask"
                    )

    if convention == "explicit" and loss_bearing_turns == 0:
        failures.append(f"record {index}: no loss-bearing assistant turn — the record trains on nothing")

    return {
        "failures": failures,
        "unevaluable": unevaluable,
        "stats": {
            "convention": convention,
            "loss_chars": loss_chars,
            "total_chars": total_chars,
            "loss_bearing_turns": loss_bearing_turns,
        },
    }


def analyze(path: str) -> dict:
    failures: list[str] = []
    unevaluable: list[str] = []
    records = 0
    unparseable = 0
    conventions: dict = {}
    shares: list[float] = []
    loss_turns_total = 0
    est_loss_tokens = 0
    est_total_tokens = 0

    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            records += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                unparseable += 1
                unevaluable.append(f"record {records}: unparseable JSON — masking could not be inspected")
                continue

            result = _check_record(row, records)
            failures.extend(result["failures"])
            unevaluable.extend(result["unevaluable"])
            stats = result["stats"]
            if not stats:
                continue
            conventions[stats["convention"]] = conventions.get(stats["convention"], 0) + 1
            loss_turns_total += stats["loss_bearing_turns"]
            est_loss_tokens += _est_tokens("x" * stats["loss_chars"])
            est_total_tokens += _est_tokens("x" * stats["total_chars"])
            if stats["total_chars"]:
                shares.append(stats["loss_chars"] / stats["total_chars"])

    if records == 0:
        return {
            "verdict": "suspect",
            "reason": "corpus contains no records — nothing could be evaluated",
            "records": 0,
            "not_verifiable_offline": NOT_VERIFIABLE_OFFLINE,
        }

    if failures:
        verdict = "fail"
    elif unevaluable:
        verdict = "suspect"
    else:
        verdict = "pass"

    shares_sorted = sorted(shares)
    result = {
        "verdict": verdict,
        "records": records,
        "unparseable_records": unparseable,
        "mask_conventions": conventions,
        "loss_bearing_assistant_turns": loss_turns_total,
        "failures": failures[:40],
        "failure_count": len(failures),
        "unevaluable": unevaluable[:40],
        "unevaluable_count": len(unevaluable),
        "not_verifiable_offline": NOT_VERIFIABLE_OFFLINE,
    }
    if shares_sorted:
        result["est_loss_token_share"] = {
            "_note": "ESTIMATE from character counts (~%.0f chars/token) — NOT a token count"
                     % CHARS_PER_TOKEN_EST,
            "corpus_wide": round(est_loss_tokens / est_total_tokens, 4) if est_total_tokens else 0.0,
            "per_record_min": round(shares_sorted[0], 4),
            "per_record_median": round(shares_sorted[len(shares_sorted) // 2], 4),
            "per_record_max": round(shares_sorted[-1], 4),
        }
        result["est_loss_tokens"] = est_loss_tokens
        result["est_total_tokens"] = est_total_tokens
    return result


def _self_test() -> int:
    """Prove the check actually detects, rather than only that it runs.

    A checker that always passes is worse than no checker, because it converts
    "unverified" into "verified" on the report. So every gate gets a fixture
    that must trip it, and a clean fixture that must not.
    """
    import os
    import tempfile

    def write(rows, raw=None):
        fd, p = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
            if raw:
                fh.write(raw + "\n")
        return p

    def msg(role, content, weight, **extra):
        m = {"role": role, "content": content, "weight": weight}
        m.update(extra)
        return m

    def clean_record():
        return {
            "weight": 1.0,
            "tools": [{"type": "function", "function": {"name": "read_crm_logs"}}],
            "messages": [
                msg("system", "You are an account manager.", 0),
                msg("user", "What should I do with this account today?", 0),
                msg("assistant", None, 1, tool_calls=[
                    {"id": "c1", "type": "function",
                     "function": {"name": "read_crm_logs", "arguments": "{\"id\": 44}"}}]),
                msg("tool", "last order 2026-05-02, cadence 30d", 0, tool_call_id="c1"),
                msg("assistant", "They are 60 days past cadence — worth an email today.", 1),
            ],
        }

    def mutate(fn):
        r = clean_record()
        fn(r)
        return r

    def leak_user(r):
        r["messages"][1]["weight"] = 1

    def leak_tool(r):
        r["messages"][3]["weight"] = 1

    def leak_system(r):
        r["messages"][0]["weight"] = 1

    def no_loss_turn(r):
        for m in r["messages"]:
            m["weight"] = 0

    def empty_target(r):
        r["messages"][4]["content"] = ""

    def single_token(r):
        r["messages"][4]["content"] = "ok"

    def orphan_tool(r):
        r["messages"][3]["tool_call_id"] = "does-not-exist"

    def undeclared(r):
        r["messages"][2]["tool_calls"][0]["function"]["name"] = "delete_everything"

    def zero_record_weight(r):
        r["weight"] = 0

    def bad_weight(r):
        r["messages"][4]["weight"] = "high"

    def implicit(r):
        for m in r["messages"]:
            m.pop("weight", None)

    def partial(r):
        r["messages"][1].pop("weight", None)

    cases_fail = [
        ("loss leaked onto user text", leak_user),
        ("loss leaked onto tool output", leak_tool),
        ("loss leaked onto system prompt", leak_system),
        ("no loss-bearing assistant turn", no_loss_turn),
        ("empty rendered target", empty_target),
        ("single-token target", single_token),
        ("orphan tool_call_id", orphan_tool),
        ("undeclared function call", undeclared),
        ("record-level weight 0", zero_record_weight),
        ("non-numeric weight", bad_weight),
    ]
    cases_suspect = [
        ("implicit masking (renderer default)", implicit),
        ("partial masking (provider precedence)", partial),
    ]

    paths = []
    ok = True

    clean_path = write([clean_record() for _ in range(5)])
    paths.append(clean_path)
    clean = analyze(clean_path)
    if clean["verdict"] != "pass":
        print(f"SELF-TEST FAIL: clean corpus returned {clean['verdict']}, expected pass")
        print(f"   {clean.get('failures')} {clean.get('unevaluable')}")
        ok = False

    for label, mutation in cases_fail:
        p = write([clean_record(), mutate(mutation)])
        paths.append(p)
        got = analyze(p)["verdict"]
        if got != "fail":
            print(f"SELF-TEST FAIL: '{label}' returned {got}, expected fail")
            ok = False

    for label, mutation in cases_suspect:
        p = write([clean_record(), mutate(mutation)])
        paths.append(p)
        got = analyze(p)["verdict"]
        if got != "suspect":
            print(f"SELF-TEST FAIL: '{label}' returned {got}, expected suspect")
            ok = False

    broken = write([clean_record()], raw="{not json")
    paths.append(broken)
    if analyze(broken)["verdict"] != "suspect":
        print("SELF-TEST FAIL: unparseable record did not return suspect")
        ok = False

    empty = write([])
    paths.append(empty)
    if analyze(empty)["verdict"] != "suspect":
        print("SELF-TEST FAIL: empty corpus did not return suspect")
        ok = False

    # A real violation alongside an un-evaluable record must still FAIL, not
    # hide behind suspect.
    mixed = write([mutate(leak_user), mutate(implicit)])
    paths.append(mixed)
    if analyze(mixed)["verdict"] != "fail":
        print("SELF-TEST FAIL: fail+suspect corpus did not return fail")
        ok = False

    for p in paths:
        os.unlink(p)

    if ok:
        print(f"self-test PASS — clean corpus passed; {len(cases_fail)} mask defects correctly "
              f"failed; {len(cases_suspect) + 2} un-evaluable corpora correctly returned suspect; "
              "fail takes precedence over suspect")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Render-verify pre-upload (S5 battery check #6)")
    ap.add_argument("--corpus", help="path to the corpus .jsonl")
    ap.add_argument("--self-test", action="store_true", help="prove the check detects")
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
        print(f"records={result['records']} unparseable={result.get('unparseable_records', 0)} "
              f"loss_bearing_assistant_turns={result.get('loss_bearing_assistant_turns', 0)}")
        print(f"mask conventions: {result.get('mask_conventions', {})}")
        share = result.get("est_loss_token_share")
        if share:
            print(f"est. loss-token share (ESTIMATE, not a token count): "
                  f"corpus {share['corpus_wide']:.1%}  per-record min {share['per_record_min']:.1%} / "
                  f"median {share['per_record_median']:.1%} / max {share['per_record_max']:.1%}")
            print(f"est. tokens: {result['est_loss_tokens']:,} loss-bearing of "
                  f"{result['est_total_tokens']:,} total")
        print("NOT verifiable offline (no Fireworks call is made):")
        for item in result.get("not_verifiable_offline", []):
            print(f"   - {item}")
        print(f"VERDICT: {result['verdict'].upper()}")
        if result.get("reason"):
            print(f"   {result['reason']}")
        for f in result.get("failures", []):
            print(f"   FAIL: {f}")
        if result.get("failure_count", 0) > len(result.get("failures", [])):
            print(f"   ... and {result['failure_count'] - len(result['failures'])} more failures")
        for u in result.get("unevaluable", []):
            print(f"   SUSPECT: {u}")
        if result.get("unevaluable_count", 0) > len(result.get("unevaluable", [])):
            print(f"   ... and {result['unevaluable_count'] - len(result['unevaluable'])} more un-evaluable")

    return {"pass": 0, "fail": 1, "suspect": 2}[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
