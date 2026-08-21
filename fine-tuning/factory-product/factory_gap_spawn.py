"""C4 — dynamic gap-closing: synthesize a subagent for a need no spec covers.

The catalogue is 33 reviewed AgentSpecs. It will not anticipate everything: a run
hits "the census says the corpus is dose-short but not why", nobody wrote a spec
for that, and today the run stops and waits for Daniel. This lets the coordinator
compose a scoped investigator on the spot.

That is a spawn capability the factory did not previously have, and PRRules rule
14 rates it the highest-risk item in PR 2 — an agent nobody reviewed, running
under a budget nobody approved per-instance. Everything below exists to make it
the *narrowest* possible version of that capability.

**Investigate and recommend. Never act.** Enforced structurally, by the tool
grant, not by prompt text:

    READ_ONLY_TOOLS = ("Read", "Grep", "Glob")

No `Write`, no `Edit`, no `Bash`, no `Task`. This single line is why a synthesized
agent cannot approve a human gate — approval is a file existing
(`factory.py:119`), so an agent that cannot write cannot approve. That is
prevention, and it is available here only because this capability is new and the
grant is still ours to pick; for the pre-existing broad grants the best available
primitive is `factory_gates`' after-the-fact detection. It is also why the
recommendation comes back as **stdout**, captured into the decision ledger, rather
than as a file the agent writes: an artifact path would require `Write`, and
`Write` is the whole hazard. A worse channel is the right trade for a structural
guarantee.

The refusals, each with the specific way this capability goes wrong without it:

1. **A spec already covering the gap.** Otherwise this becomes a way to route
   around code review: get an unreviewed ad-hoc agent to do work a reviewed spec
   exists for, with no PR and no diff.
2. **A mandate that implies action.** "Investigate the failed launch and restart
   it" is two requests, and the read-only grant makes the second half fail
   *silently and partway*. Refused at synthesis instead.
3. **Anything naming a human gate.** Rule 1 and SCOPE.md invariant 14. Belt and
   braces over the tool grant, because the tool grant is one edit away from
   changing and this refusal states the intent.
4. **Fan-out.** Capped per run. Unbounded synthesis is unbounded spend, and the
   per-node budget cap says nothing about how many nodes there are — the same
   arithmetic that makes `Task` forbidden for every other node.
5. **Nesting.** A synthesized agent may not synthesize. `Task` is absent from the
   grant and `resolve_allowed_tools` strips it regardless; this module additionally
   refuses to spawn from within a spawned node, because the depth check is the
   thing that stops a chain, not the tool list.

Specs are never written to `specs/`. An ad-hoc spec that lands on disk is
indistinguishable next week from a reviewed one, and the 33-spec catalogue would
grow by accretion without anyone approving an entry. It exists in memory, is
dispatched via `dispatch_node(spec_override=...)`, and survives only as the
prompt file and the decision-ledger record — which is the audit trail, and is
strictly better evidence than a YAML file with no review history.

Held-out material: a synthesized node runs under stage code `S-adhoc`, which is
not in C3's allow-list, so `factory_heldout.may_read_heldout` denies it by
default. That is inherited rather than re-implemented, and it is the payoff for
C3 having been written as an allow-list.
"""

import logging
import re

log = logging.getLogger("factory.gap_spawn")

# Read, search, report. See the header: this line is the gate-denial.
READ_ONLY_TOOLS = ("Read", "Grep", "Glob")

# `S-adhoc` deliberately is not a real stage code. It keeps ad-hoc nodes
# distinguishable in the ledger from the eleven graph stages, and it makes C3's
# default-deny apply to held-out material with no extra code.
ADHOC_STAGE_CODE = "S-adhoc"

# Cheap model, tight turn ceiling, small budget. An investigation that needs more
# than this is not a gap-closing spawn, it is a spec — which is a PR, with review.
ADHOC_RUNTIME = {"model": "sonnet", "max_turns": 12, "budget_usd": 0.75,
                 "allowed_tools": READ_ONLY_TOOLS}

# Refusal 4. Three is enough for a coordinator to triage a surprise and few enough
# that the worst case is bounded at roughly $2.25 of investigation per run.
MAX_SPAWNS_PER_RUN = 3

# Refusal 2. Verbs whose presence means the mandate is asking for an action. Word
# boundaries only — "deployment" in "investigate why the deployment failed" is a
# noun and must not trip this, while "deploy the model" must.
_ACTION_VERBS = (
    "approve", "approving", "authorise", "authorize", "sign off", "signoff",
    "ship", "launch", "deploy", "activate", "promote", "roll out", "rollout",
    "merge", "push", "commit", "revert", "delete", "drop", "truncate",
    "write", "edit", "modify", "patch", "apply", "fix", "implement", "refactor",
    "migrate", "train", "fine-tune", "finetune", "spend", "purchase", "provision",
    "restart", "retry", "resume", "unblock", "override", "bypass", "disable",
)

# Refusal 3. Gate names and their codes, in every form a mandate might use them.
_GATE_TERMS = ("governance gate", "launch gate", "ship gate", "spend gate",
               "human gate", "s2", "s6g", "s9", "gate")

_INVESTIGATIVE_OPENERS = ("investigate", "diagnose", "explain", "identify", "find out",
                          "determine", "assess", "audit", "review", "analyse", "analyze",
                          "compare", "measure", "report", "summarise", "summarize",
                          "trace", "check whether", "establish", "quantify", "why")


class GapSpawnRefused(Exception):
    """The requested spawn is outside what this capability is permitted to do.

    Raised, not degraded to a no-op result: a refused spawn that returns
    `{"ok": False}` reads to the coordinator as "the investigation failed", and it
    would retry. The mandate needs rewriting, or a spec needs writing, and only an
    exception says so.
    """


def _normalise(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _contains_term(haystack, term):
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", haystack) is not None


def action_verbs_in(text):
    """Action verbs present in a mandate. Public because the refusal message names
    them — "rejected" without "because you said 'restart'" produces a coordinator
    that rewrites the mandate at random."""
    h = _normalise(text)
    return [v for v in _ACTION_VERBS if _contains_term(h, v)]


def names_a_gate(text):
    h = _normalise(text)
    return any(_contains_term(h, t) for t in _GATE_TERMS)


def is_investigative(text):
    """Whether the mandate asks a question rather than orders a change.

    An allow-list on the opening intent, on top of the verb deny-list, because a
    deny-list of verbs is inherently incomplete — the two failing in the same
    direction is much less likely than either failing alone.
    """
    h = _normalise(text)
    return any(h.startswith(o) or _contains_term(h, o) for o in _INVESTIGATIVE_OPENERS)


def find_covering_spec(gap, catalogue):
    """Refusal 1: the first catalogue spec whose mission already covers this gap.

    Token overlap on content words, which is crude and deliberately biased toward
    false positives: wrongly refusing costs the coordinator one round trip and a
    clear message, while wrongly allowing creates an unreviewed agent duplicating
    a reviewed one. `catalogue` is `{slug: mission}` — passed in rather than read
    from disk so this stays a pure function and the tests do not need the specs.
    """
    stop = {"the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "is", "are",
            "why", "what", "how", "this", "that", "it", "its", "with", "from", "by",
            "run", "factory", "org", "data", "check", "report", "investigate", "not"}
    gap_tokens = {t for t in re.findall(r"[a-z][a-z0-9-]{3,}", _normalise(gap)) if t not in stop}
    if not gap_tokens:
        return None
    best, best_score = None, 0.0
    for slug, mission in (catalogue or {}).items():
        m_tokens = {t for t in re.findall(r"[a-z][a-z0-9-]{3,}", _normalise(mission)) if t not in stop}
        if not m_tokens:
            continue
        score = len(gap_tokens & m_tokens) / len(gap_tokens)
        if score > best_score:
            best, best_score = slug, score
    return best if best_score >= 0.6 else None


def synthesize(gap, mandate, org_slug, read_first=None, spawn_index=0,
               catalogue=None, parent_node_id=None):
    """Build (and validate) an in-memory AgentSpec for one investigation.

    Every refusal fires here, before anything is rendered or spawned, so a
    rejected spawn costs zero dollars and zero turns.
    """
    if parent_node_id:
        # Refusal 5. Checked on depth, not on tool list: `Task` is already absent
        # from READ_ONLY_TOOLS, but a spawn chain does not need `Task` if the
        # coordinator's own dispatch path is reachable from a spawned node.
        raise GapSpawnRefused(
            f"a synthesized node ({parent_node_id}) may not synthesize another. Gap-closing "
            f"spawn is one level deep by design — nested spawn makes the per-node budget cap "
            f"meaningless, which is the same reason `Task` is stripped from every node."
        )
    if spawn_index >= MAX_SPAWNS_PER_RUN:
        raise GapSpawnRefused(
            f"this run has already synthesized {MAX_SPAWNS_PER_RUN} gap-closing agents, which is "
            f"the cap. A fourth unmet need is a signal that the graph is missing a real spec — "
            f"write one (reviewed, in specs/) rather than spawning past the cap."
        )
    if not _normalise(gap):
        raise GapSpawnRefused("gap description is empty — there is nothing to scope an agent to")
    if not _normalise(mandate):
        raise GapSpawnRefused("mandate is empty — an agent with no mandate has no done-when")

    # Gate first, deliberately. A mandate like "investigate whether the spend gate
    # can be cleared" trips the action deny-list too ("spend"), and reporting that
    # first is actively harmful: the coordinator would rewrite the wording to shed
    # the verb, resubmit, and only then learn the real answer. Refusals are ordered
    # most-fundamental first, so the message names the reason that will not move.
    if names_a_gate(gap) or names_a_gate(mandate):
        raise GapSpawnRefused(
            "gap or mandate references a human gate. The automation does not touch S2/S6g/S9 "
            "under any framing, including 'investigate' (SCOPE.md invariant 14, PRRules rule 1) "
            "— an investigation whose conclusion is a gate recommendation is how a gate becomes "
            "a formality. Take gate questions to Daniel directly."
        )

    verbs = action_verbs_in(mandate)
    if verbs:
        raise GapSpawnRefused(
            f"mandate implies action, not investigation (found: {', '.join(sorted(verbs))}). A "
            f"synthesized agent holds {list(READ_ONLY_TOOLS)} and cannot act — so an action "
            f"mandate does not fail cleanly, it half-runs and reports success on the readable "
            f"part. Re-scope to a question, and put the action in front of a human. Mandate: "
            f"{mandate!r}"
        )
    if not is_investigative(mandate):
        raise GapSpawnRefused(
            f"mandate does not read as an investigation. Say what should be found out (e.g. "
            f"'investigate why ...', 'determine whether ...'), because the deliverable is a "
            f"recommendation and nothing else. Mandate: {mandate!r}"
        )

    covering = find_covering_spec(gap, catalogue)
    if covering:
        raise GapSpawnRefused(
            f"'{covering}' already covers this gap. Dispatch the reviewed spec instead of "
            f"synthesizing an unreviewed one — otherwise this capability becomes a way around "
            f"spec review. If {covering} genuinely does not fit, amend it in a PR."
        )

    slug = f"adhoc-{re.sub(r'[^a-z0-9]+', '-', _normalise(gap))[:48].strip('-')}"
    return {
        "slug": slug,
        "role_family": "investigator",
        "adhoc": True,
        "synthesized_for_org": org_slug,
        "mission": f"Investigate and report. {gap}",
        "read_first": list(read_first or []),
        "mandate": [{
            "task": mandate,
            "stop_or_ask": (
                "STOP and report if closing this gap would require writing, editing, running a "
                "command, spending money, or a decision at S2/S6g/S9. You hold read-only tools: "
                "report what you found and what you recommend. Do not attempt the change."
            ),
        }],
        "laws": [
            "You are an investigator. Your deliverable is findings and a recommendation — nothing else.",
            "You may not approve, or recommend approving, any human gate (S2/S6g/S9).",
            "You may not spawn subagents.",
            "Report uncertainty as uncertainty. A confident guess is worse than 'could not determine', "
            "because the coordinator cannot tell them apart.",
        ],
        "done_when": "Findings and a single clear recommendation have been reported in the final response.",
        "final_response_shape": (
            "## Findings\n- ...\n\n## Recommendation\n- <one recommendation, or 'insufficient evidence'>\n\n"
            "## What I could not determine\n- ..."
        ),
        "runtime": dict(ADHOC_RUNTIME),
    }


def spawn(gap, mandate, org_slug, run_id=None, graph_id="fine-tune-factory-v1",
          read_first=None, spawn_index=0, catalogue=None, parent_node_id=None,
          dry_run=True, **dispatch_kwargs):
    """Synthesize and dispatch, through the ordinary node path.

    Routed through `dispatch_node` rather than calling `tier2_run.sh` directly so
    that the ad-hoc path inherits the held-out read check, the `Task` strip, the
    gate-forgery snapshot and the decision log. A second dispatch path is a second
    place for every one of those to be forgotten.
    """
    spec = synthesize(gap, mandate, org_slug, read_first=read_first,
                      spawn_index=spawn_index, catalogue=catalogue,
                      parent_node_id=parent_node_id)
    log.warning("factory_gap_spawn: synthesizing ad-hoc agent %s for org %s (gap: %s)",
                spec["slug"], org_slug, gap)

    import factory_node_run
    result = factory_node_run.dispatch_node(
        spec["slug"], ADHOC_STAGE_CODE, run_id, graph_id, org_slug,
        dry_run=dry_run, spec_override=spec, **dispatch_kwargs,
    )
    result["adhoc"] = True
    result["gap"] = gap
    return result
