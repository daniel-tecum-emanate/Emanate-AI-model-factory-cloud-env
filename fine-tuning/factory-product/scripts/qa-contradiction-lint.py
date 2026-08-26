#!/usr/bin/env python3
"""Contradiction linter — check #2 of the S5 verify battery ("0 unmarked", MANDATORY, L37).

WHY THIS EXISTS: on 2026-08-25 the battery was run for real against grand-steel
and reported `script_on_disk: false` for five of its six checks. This one
referenced `scripts/qa-contradiction-lint.py`, which existed in NEITHER
repository -- not the canonical cloud-env copy, not the workflow copy. S5
correctly returned `suspect` rather than `pass`, so nothing pretended the corpus
was verified; but the practical consequence was that a run could arrive at the
S6g human spend gate carrying a cost projection while its corpus had been
verified by nothing at all.

WHAT FAILURE IT PREVENTS: a corpus that teaches two different answers to one
question. Show a fine-tune "yes, they ordered on the 26th" and "I can't see
their order history" for substantially the same prompt and it learns neither
stance -- it learns to sample between them. That failure is invisible at eval
time (both answers look plausible in isolation, and an averaged score hides the
split) and it is invisible in the loss curve. It is only visible by putting the
two records side by side, which is what this check does.

HOW IT DECIDES -- deterministic, no model call, $0, same class as every other
gate in this pipeline. Two records are compared only if they clear three gates
in order, each of which exists to kill a false positive:

  1. SAME SUBJECT.  Records are blocked into groups by their "subject key" --
     the named entities in the user turn: tokens that are written as names
     (ALL-CAPS, or capitalised somewhere other than the start of a sentence) and
     that are *rare* across the corpus (document frequency below 5%). "MILL
     STEEL CO." yields {mill}; "steel" and "co" are too common to identify
     anyone, and "Changes"/"Did" are only capitalised because a sentence started.
     Two questions about two different accounts can disagree all they like: that
     is two facts, not a contradiction.
  2. SAME QUESTION.  Jaccard similarity over the user turn's lightly stemmed
     tokens must reach 0.70 -- and so must the similarity of the interrogative
     sentence taken alone. Both, because either one on its own is fooled:
     whole-prompt similarity lets a long shared context carry two different asks
     over the line ("Anything from X after the snapshot date?" vs "When did X
     last order?" share every word of their preamble), while the ask alone
     ignores the context that the answer is grounded in. The stemming is crude
     on purpose -- it only has to be consistent, so that "order", "orders" and
     "ordered" stop counting as three different words.
  3. SAME GROUNDING.  The user turn in this corpus family carries the facts the
     answer is grounded in ("- SELECT STEEL: no order activity logged today").
     So the pair must also agree on every number in the prompt AND on every
     negation token. This gate is the single most important one here: the real
     grand-steel corpus contains sibling prompts that differ only by "no order
     activity logged today" vs "placed a new order today", and their answers
     *must* disagree. Flagging those would be a false contradiction report --
     expensive, because it sends a human to inspect a corpus that is fine.

Only then is the answer pair tested on a decidable axis:

  * STANCE (yes/no-shaped questions only): AFFIRM vs DENY vs DECLINE, read off
    the first sentence. An explicit leading "yes"/"no" dominates; a hedge like
    "I can't see that" with no leading yes/no is a DECLINE. Any two different
    stances on an identical question with identical grounding is a
    contradiction.
  * DATE CLAIM: both answers assert exactly one ISO date and the dates differ.
  * NUMERIC CLAIM: both answers attach exactly one value to the same unit word
    ("52 orders" vs "48 orders") and the values differ.

WHAT IT CANNOT DETECT, stated plainly rather than papered over:
  - Paraphrased contradictions. Two records that ask the same thing in wholly
    different words fall below the 0.70 gate and are never compared. This check
    trades recall for precision on purpose.
  - Subjects that are never written as names. Subject identity is read off
    capitalisation, so a corpus that refers to accounts only in lower case (or
    only by an id in a lower-case domain) gets weaker blocking, and leans
    entirely on gates 2 and 3.
  - Semantic disagreement inside free prose ("their volume is growing" vs
    "their volume has stalled"). Only stance, dates and unit-labelled numbers
    are decidable without a model.
  - Records whose final assistant turn is a tool call rather than an answer.
    Those end in a structured action, not a claim; inconsistency there is a
    different failure needing a different check. They are reported as
    out-of-scope and the coverage figure is printed on every run so a reader can
    see how much of the corpus the pass actually covers.

MARKED CONTRADICTIONS. The battery says "0 *unmarked*", which concedes that a
corpus may deliberately carry counter-instances -- a pair that teaches "here the
answer is yes, and here, with this one fact changed, it is no". A record
declares itself a deliberate counter-instance by any of:

    {"contradiction_marker": true}            # or a group id string
    {"tags": ["counter-instance"]}            # or "contradiction", "contradiction-ok"
    {"meta": {"contradiction_marker": true}}  # also "metadata", also "counter_instance"
    ... or the literal token [CONTRADICTION-OK] in the record's system message.

A detected pair counts as MARKED if *either* side is marked. Only unmarked pairs
fail. Marked pairs are counted and printed separately so that "we meant that
one" stays an auditable claim rather than a silence.

DEGRADATION. Exit 2 = SUSPECT = could not evaluate, and the caller must never
read it as a pass (`audit_lib`'s degradation lattice). This check claims
something about the whole corpus ("no unmarked contradictions"), so any line it
could not read invalidates that claim: unparseable JSON downgrades a would-be
pass to suspect. A FAIL, by contrast, stands -- a contradiction found is found
regardless of what else was unreadable.

    python3 scripts/qa-contradiction-lint.py --corpus <path.jsonl>
    python3 scripts/qa-contradiction-lint.py --self-test

Exit 0 = PASS, 1 = FAIL, 2 = could not evaluate.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict

# --- thresholds (see module docstring for the reasoning behind each) ----------
QUESTION_SIMILARITY_MIN = 0.70
RARE_DF_RATIO = 0.05
MAX_PAIR_COMPARISONS = 2_000_000

TOKEN_RE = re.compile(r"[a-z0-9']+")
WORD_SPAN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'&.]*")
# What can make a capital letter meaningless as a name signal. Note the absence
# of the dash: in this corpus family a bullet ("- Reliant Industries: updated a
# shipping address") introduces an account, so treating post-dash capitals as
# sentence-initial silently dropped whole accounts out of the subject key and let
# two different change-lists be compared as one question.
SENTENCE_BOUNDARY_CHARS = ".!?:;|("
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
UNIT_VALUE_RE = re.compile(r"(\d+(?:[.,]\d+)*)\s+([a-z]{3,})")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

NEGATION_TOKENS = {
    "no", "not", "never", "none", "nothing", "without", "zero", "cannot", "nor",
    "neither", "nobody", "nowhere",
}

# Stripped from the interrogative sentence before comparing two asks. "Did X
# order today?" and "Has X ordered today?" are one question wearing two
# auxiliaries; on a five-word question a single function word is enough to drop
# raw similarity below the gate and hide a real contradiction.
FUNCTION_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "am", "do",
    "does", "did", "has", "have", "had", "can", "could", "will", "would",
    "should", "shall", "i", "we", "you", "they", "it", "me", "my", "our", "us",
    "their", "them", "its", "of", "on", "in", "at", "to", "for", "from", "with",
    "by", "as", "that", "this", "these", "those", "there", "here", "and", "or",
    "so", "any", "anything", "anyone", "please", "just", "right", "now", "s",
}
YES_NO_OPENERS = {
    "do", "does", "did", "is", "are", "was", "were", "has", "have", "had",
    "can", "could", "will", "would", "should", "am", "any", "anything",
    "anyone", "shall", "must",
}

AFFIRM_RE = re.compile(r"^(yes|yep|yeah|correct|that'?s right|indeed|confirmed)\b")
DENY_RE = re.compile(r"^(no|nope|not quite|that'?s not right|incorrect|none)\b")
DECLINE_RE = re.compile(
    r"""(
        i\s+(can'?t|cannot|do\s+not|don'?t)\s+(see|find|confirm|have|know|tell|verify)
      | (isn'?t|is\s+not)\s+(something\s+)?i\s+can\s+see
      | not\s+(visible|something\s+i\s+can\s+see)
      | not\s+in\s+(the\s+)?(data|record|records|snapshot|log|logs)
      | my\s+view\s+(stops|ends|doesn'?t)
      | (the\s+)?snapshot\s+(ends|stops|doesn'?t)
      | no\s+(data|visibility|record|records|information)\s+(on|for|about)
      | outside\s+(what|the)\s+
      | all\s+i\s+can\s+see
      | that'?s\s+not\s+something\s+i\s+can
    )""",
    re.VERBOSE,
)

MARK_TAGS = {"contradiction", "contradiction-ok", "contradiction_ok",
             "counter-instance", "counter_instance", "counterinstance"}
MARK_KEYS = ("contradiction_marker", "counter_instance", "counter-instance",
             "contradiction")
SYSTEM_MARK_TOKEN = "[CONTRADICTION-OK]"


def _stem(token: str) -> str:
    """A deliberately crude, deterministic suffix strip.

    It does not need to be linguistically right, only applied identically to
    both sides of every comparison, so that "order" / "orders" / "ordered" stop
    counting as three different words when fingerprinting a question.
    """
    token = token.rstrip("'")
    if token.endswith("'s"):
        token = token[:-2]
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


FUNCTION_STEMS = {_stem(w) for w in FUNCTION_WORDS} | FUNCTION_WORDS


def _raw_tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def _tokens(text: str) -> list[str]:
    return [_stem(t) for t in _raw_tokens(text)]


def _name_like(text: str) -> set[str]:
    """Tokens the prompt writes as names: ALL-CAPS, or capitalised mid-sentence.

    A word that is capitalised only because it opened a sentence ("Changes
    today…", "Did they order?") identifies nobody, so position matters as much
    as case.
    """
    names: set[str] = set()
    for match in WORD_SPAN_RE.finditer(text):
        word = match.group(0).strip(".'")
        if len(word) < 2:
            continue
        letters = [c for c in word if c.isalpha()]
        all_caps = len(letters) >= 2 and all(c.isupper() for c in letters)
        if not all_caps:
            if not word[0].isupper():
                continue
            before = text[: match.start()].rstrip()
            if not before or before[-1] in SENTENCE_BOUNDARY_CHARS:
                continue  # sentence-initial capital — not a name signal
        for token in _tokens(word):
            names.add(token)
    return names


def _is_marked(row: dict) -> bool:
    """True if the record declares itself a deliberate counter-instance."""
    for key in MARK_KEYS:
        if row.get(key):
            return True
    tags = row.get("tags")
    if isinstance(tags, (list, tuple)):
        for tag in tags:
            if isinstance(tag, str) and tag.strip().lower() in MARK_TAGS:
                return True
    for container in ("meta", "metadata"):
        blob = row.get(container)
        if isinstance(blob, dict):
            for key in MARK_KEYS:
                if blob.get(key):
                    return True
    for msg in row.get("messages") or []:
        if msg.get("role") == "system" and isinstance(msg.get("content"), str):
            if SYSTEM_MARK_TOKEN in msg["content"]:
                return True
    return False


def _extract(row: dict, line_no: int) -> dict | None:
    """A record reduced to (question, answer) — or None if out of scope.

    Out of scope means the record does not end in a prose answer: it ends in a
    tool call, or carries no user turn to attribute an answer to. Those are
    reported, never silently dropped.
    """
    messages = row.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    final = messages[-1]
    if final.get("role") != "assistant":
        return None
    answer = final.get("content")
    if not isinstance(answer, str) or not answer.strip():
        return None
    question = None
    for msg in reversed(messages[:-1]):
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            question = msg["content"]
            break
    if not question or not question.strip():
        return None
    return {
        "line": line_no,
        "question": re.sub(r"\s+", " ", question).strip(),
        "answer": re.sub(r"\s+", " ", answer).strip(),
        "marked": _is_marked(row),
    }


def _load(path: str) -> tuple[list[dict], int, int, int]:
    """Returns (in-scope records, total lines, unparseable lines, out-of-scope)."""
    records: list[dict] = []
    total = 0
    unparseable = 0
    out_of_scope = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                unparseable += 1
                continue
            if not isinstance(row, dict):
                unparseable += 1
                continue
            rec = _extract(row, line_no)
            if rec is None:
                out_of_scope += 1
            else:
                records.append(rec)
    return records, total, unparseable, out_of_scope


# --- gate 1-3: is this pair asking the same question of the same subject? -----

def _question_sentence(question: str) -> str:
    """The interrogative part of the prompt — the last sentence, usually."""
    sentences = [s for s in SENTENCE_SPLIT_RE.split(question) if s.strip()]
    for sentence in reversed(sentences):
        if "?" in sentence:
            return sentence.strip()
    return sentences[-1].strip() if sentences else question


def _ask_tokens(question: str) -> set[str]:
    """The content words of the interrogative sentence — what is actually asked."""
    tokens = _tokens(_question_sentence(question))
    content = {t for t in tokens if t not in FUNCTION_STEMS}
    return content or set(tokens)


def _is_yes_no(question: str) -> bool:
    words = _raw_tokens(_question_sentence(question))
    return bool(words) and words[0] in YES_NO_OPENERS


def _grounding(question: str) -> tuple[frozenset, frozenset]:
    """The facts the prompt supplies: its numbers and its negations.

    Two prompts that differ here are not the same question, however similar the
    wording. This is what keeps "no order activity logged today" and "placed a
    new order today" from being reported as a contradiction.
    """
    numbers = frozenset(n.replace(",", "") for n in NUMBER_RE.findall(question))
    negations = frozenset(
        t for t in _raw_tokens(question) if t in NEGATION_TOKENS or t.endswith("n't")
    )
    return numbers, negations


def _jaccard(a: set, b: set) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


# --- the decidable axes ------------------------------------------------------

def _stance(answer: str) -> str:
    """AFFIRM / DENY / DECLINE / UNCLEAR, from the answer's opening sentence.

    An explicit leading yes dominates any later hedge, because "Yes — they
    ordered on the 24th, though I can't see the pricing" is an affirmation with
    a caveat, not a refusal. A decline phrase with no leading yes is a decline,
    even when it opens with "No" ("No visibility into that" is a decline, not a
    denial) -- that ordering stops two declines being read as a disagreement.
    """
    first = SENTENCE_SPLIT_RE.split(answer)[0].lower().strip()
    if AFFIRM_RE.match(first):
        return "AFFIRM"
    if DECLINE_RE.search(first):
        return "DECLINE"
    if DENY_RE.match(first):
        return "DENY"
    return "UNCLEAR"


def _sole_date(answer: str) -> str | None:
    dates = set(ISO_DATE_RE.findall(answer))
    return dates.pop() if len(dates) == 1 else None


def _unit_claims(answer: str) -> dict[str, str]:
    """unit word -> the single value this answer attaches to it."""
    buckets: dict[str, set] = defaultdict(set)
    for value, unit in UNIT_VALUE_RE.findall(answer.lower()):
        buckets[unit].add(value.replace(",", ""))
    return {unit: values.pop() for unit, values in buckets.items() if len(values) == 1}


def _disagreement(a: dict, b: dict) -> tuple[str, str] | None:
    """The decidable axis on which these two answers conflict, if any."""
    if _is_yes_no(a["question"]) and _is_yes_no(b["question"]):
        sa, sb = _stance(a["answer"]), _stance(b["answer"])
        if sa != "UNCLEAR" and sb != "UNCLEAR" and sa != sb:
            axis = "stance" if "DECLINE" not in (sa, sb) else "stance_vs_decline"
            return axis, f"{sa} vs {sb}"

    da, db = _sole_date(a["answer"]), _sole_date(b["answer"])
    if da and db and da != db:
        return "date_claim", f"{da} vs {db}"

    ca, cb = _unit_claims(a["answer"]), _unit_claims(b["answer"])
    for unit in sorted(set(ca) & set(cb)):
        if ca[unit] != cb[unit]:
            return "numeric_claim", f"{ca[unit]} {unit} vs {cb[unit]} {unit}"

    return None


# --- the check ---------------------------------------------------------------

def analyze(path: str) -> dict:
    records, total_lines, unparseable, out_of_scope = _load(path)
    n = len(records)

    base = {
        "records": total_lines,
        "unparseable_records": unparseable,
        "records_out_of_scope": out_of_scope,
        "records_in_scope": n,
        "coverage": round(n / total_lines, 4) if total_lines else 0.0,
    }

    if n < 2:
        return {
            **base,
            "verdict": "suspect",
            "reason": (
                f"only {n} record(s) carry a prose assistant answer — "
                "nothing to compare, so the corpus is unchecked, not clean"
            ),
        }

    df = Counter()
    for rec in records:
        df.update(set(_tokens(rec["question"])))
    rare_cutoff = max(2, RARE_DF_RATIO * n)

    for rec in records:
        rec["tokens"] = set(_tokens(rec["question"]))
        rec["ask_tokens"] = _ask_tokens(rec["question"])
        rec["subject"] = frozenset(
            t for t in _name_like(rec["question"]) if df[t] < rare_cutoff
        )
        rec["grounding"] = _grounding(rec["question"])

    blocks: dict[frozenset, list[dict]] = defaultdict(list)
    for rec in records:
        blocks[rec["subject"]].append(rec)

    comparisons = sum(len(b) * (len(b) - 1) // 2 for b in blocks.values())
    if comparisons > MAX_PAIR_COMPARISONS:
        return {
            **base,
            "verdict": "suspect",
            "reason": (
                f"{comparisons} candidate comparisons exceeds the "
                f"{MAX_PAIR_COMPARISONS} budget — the corpus was not fully compared"
            ),
        }

    same_question_pairs = 0
    grounding_divergent = 0
    unmarked: list[dict] = []
    marked: list[dict] = []

    for block in blocks.values():
        for i in range(len(block)):
            for j in range(i + 1, len(block)):
                a, b = block[i], block[j]
                if _jaccard(a["tokens"], b["tokens"]) < QUESTION_SIMILARITY_MIN:
                    continue
                # The prompt as a whole can be dominated by a long shared
                # context, so the interrogative sentence has to match on its
                # own too. "Anything from X after the snapshot date?" and "When
                # did X last order?" share every word of their preamble and are
                # not remotely the same ask.
                if _jaccard(a["ask_tokens"], b["ask_tokens"]) < QUESTION_SIMILARITY_MIN:
                    continue
                if a["grounding"] != b["grounding"]:
                    grounding_divergent += 1
                    continue
                same_question_pairs += 1
                conflict = _disagreement(a, b)
                if conflict is None:
                    continue
                axis, detail = conflict
                finding = {
                    "axis": axis,
                    "detail": detail,
                    "lines": [a["line"], b["line"]],
                    "question": a["question"][:180],
                    "answers": [a["answer"][:180], b["answer"][:180]],
                }
                (marked if (a["marked"] or b["marked"]) else unmarked).append(finding)

    failures = []
    if unmarked:
        failures.append(f"{len(unmarked)} unmarked contradiction pair(s) (limit 0)")

    verdict = "fail" if failures else "pass"
    if verdict == "pass" and unparseable:
        verdict = "suspect"
        base["reason"] = (
            f"{unparseable} line(s) could not be parsed — no unmarked contradiction was "
            "found among the readable records, but a whole-corpus claim cannot be made"
        )

    return {
        **base,
        "verdict": verdict,
        "failures": failures,
        "comparable_pairs": same_question_pairs,
        "grounding_divergent_pairs": grounding_divergent,
        "unmarked_contradictions": len(unmarked),
        "marked_contradictions": len(marked),
        "unmarked_examples": unmarked[:10],
        "marked_examples": marked[:10],
    }


# --- self-test ---------------------------------------------------------------

def _self_test() -> int:
    """Prove the linter detects, and prove it does not over-detect.

    A checker that always passes is worse than no checker, because it converts
    "unverified" into "verified" on the report. A contradiction linter has a
    second way to be worse than nothing: crying wolf on a corpus that is fine,
    which burns a human's inspection budget. So both directions are asserted,
    plus the marker convention and the degradation behaviour.
    """
    import os
    import tempfile

    def write(rows):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as fh:
            for row in rows:
                fh.write((row if isinstance(row, str) else json.dumps(row)) + "\n")
        return path

    def rec(question, answer, **extra):
        return {
            "messages": [
                {"role": "system", "content": "You are an account manager."},
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ],
            **extra,
        }

    filler = [
        rec(f"Did account {i} order anything today?", f"Yes — account {i} ordered today.")
        for i in range(20)
    ]

    q_yes_no = "Changes today (2026-07-29): - SELECT STEEL: order logged today. Did SELECT STEEL order today?"
    q_yes_no_alt = "Changes today (2026-07-29): - SELECT STEEL: order logged today. Has SELECT STEEL ordered today?"

    cases = {}

    # 1. stance contradiction, unmarked -> FAIL
    cases["stance"] = write(filler + [
        rec(q_yes_no, "Yes — SELECT STEEL placed an order today, per the change log."),
        rec(q_yes_no_alt, "I can't see any order history for SELECT STEEL from here."),
    ])

    # 2. same pair, one side marked -> PASS with marked=1
    cases["marked"] = write(filler + [
        rec(q_yes_no, "Yes — SELECT STEEL placed an order today, per the change log."),
        rec(q_yes_no_alt, "I can't see any order history for SELECT STEEL from here.",
            contradiction_marker="deliberate-counter-instance"),
    ])

    # 3. numeric contradiction -> FAIL
    q_num = "How many orders does SELECT STEEL have on record?"
    q_num_alt = "How many orders does SELECT STEEL have on record right now?"
    cases["numeric"] = write(filler + [
        rec(q_num, "SELECT STEEL has 52 orders on record."),
        rec(q_num_alt, "SELECT STEEL has 48 orders on record."),
    ])

    # 4. clean corpus -> PASS
    cases["clean"] = write(filler + [
        rec(q_yes_no, "Yes — SELECT STEEL placed an order today, per the change log."),
        rec(q_yes_no_alt, "Yes, an order was logged for SELECT STEEL today."),
    ])

    # 5. THE FALSE-POSITIVE TRAP, taken from the real grand-steel corpus: same
    #    question, opposite grounding. Answers must disagree. Must PASS.
    cases["grounding"] = write(filler + [
        rec("Changes today (2026-07-29): - SELECT STEEL: placed a new order today. "
            "Any fresh activity for SELECT STEEL I should know about?",
            "Yes — SELECT STEEL placed a new order today."),
        rec("Changes today (2026-07-29): - SELECT STEEL: no order activity logged today. "
            "Any fresh activity for SELECT STEEL I should know about?",
            "No new orders for SELECT STEEL today — the log shows a no-activity entry."),
    ])

    # 6. different accounts, opposite answers -> not a contradiction. Must PASS.
    cases["distinct_subjects"] = write(filler + [
        rec("Did CLINGAN STEEL order anything today?", "Yes — CLINGAN STEEL ordered today."),
        rec("Did KENWAL PICKLING order anything today?", "No — KENWAL PICKLING did not order today."),
    ])

    # 7. degradation: an unreadable line must not be reported as a clean pass.
    cases["unparseable"] = write([json.dumps(r) for r in filler] + ["{not json"])

    # 8. degradation: nothing to compare.
    cases["empty"] = write([])

    results = {name: analyze(path) for name, path in cases.items()}
    for path in cases.values():
        os.unlink(path)

    expected = {
        "stance": "fail",
        "marked": "pass",
        "numeric": "fail",
        "clean": "pass",
        "grounding": "pass",
        "distinct_subjects": "pass",
        "unparseable": "suspect",
        "empty": "suspect",
    }

    ok = True
    for name, want in expected.items():
        got = results[name]["verdict"]
        if got != want:
            print(f"SELF-TEST FAIL: fixture '{name}' returned {got}, expected {want}")
            ok = False

    if results["marked"].get("marked_contradictions") != 1:
        print("SELF-TEST FAIL: marked fixture did not record the pair as marked")
        ok = False
    if results["marked"].get("unmarked_contradictions") != 0:
        print("SELF-TEST FAIL: marked fixture reported an unmarked contradiction")
        ok = False
    if results["stance"].get("unmarked_contradictions") != 1:
        print(f"SELF-TEST FAIL: stance fixture found "
              f"{results['stance'].get('unmarked_contradictions')} pairs, expected 1")
        ok = False
    if not results["grounding"].get("grounding_divergent_pairs"):
        print("SELF-TEST FAIL: grounding fixture did not exercise the grounding guard — "
              "the pair was never compared, so the guard proved nothing")
        ok = False
    if not results["clean"].get("comparable_pairs"):
        print("SELF-TEST FAIL: clean fixture produced no comparable pairs — "
              "its pass is vacuous")
        ok = False

    if ok:
        print("self-test PASS — detects: stance contradiction, numeric contradiction; "
              "does not over-detect: identical question with opposite grounding, "
              "opposite answers about different subjects, clean near-duplicates; "
              "honours the marker convention; degrades to suspect on unreadable "
              "and on uncomparable corpora")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Contradiction linter (S5 battery check #2)")
    ap.add_argument("--corpus", help="path to the corpus .jsonl")
    ap.add_argument("--self-test", action="store_true", help="prove the linter detects")
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
    except OSError as exc:
        print(f"SUSPECT: corpus unreadable: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"records={result['records']} in_scope={result['records_in_scope']} "
              f"out_of_scope={result['records_out_of_scope']} "
              f"unparseable={result['unparseable_records']} "
              f"coverage={result['coverage']:.1%}")
        if "comparable_pairs" in result:
            print(f"comparable pairs (same subject, same question, same grounding)="
                  f"{result['comparable_pairs']}   "
                  f"rejected on grounding={result['grounding_divergent_pairs']}")
            print(f"contradictions: unmarked={result['unmarked_contradictions']} (limit 0)   "
                  f"marked={result['marked_contradictions']}")
            for f in result.get("unmarked_examples", []):
                print(f"   UNMARKED [{f['axis']}: {f['detail']}] lines {f['lines']}")
                print(f"      Q: {f['question']}")
                print(f"      A1: {f['answers'][0]}")
                print(f"      A2: {f['answers'][1]}")
            for f in result.get("marked_examples", []):
                print(f"   marked [{f['axis']}: {f['detail']}] lines {f['lines']}")
        print(f"VERDICT: {result['verdict'].upper()}")
        if result.get("reason"):
            print(f"   {result['reason']}")
        for f in result.get("failures", []):
            print(f"   FAIL: {f}")

    return {"pass": 0, "fail": 1, "suspect": 2}[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
