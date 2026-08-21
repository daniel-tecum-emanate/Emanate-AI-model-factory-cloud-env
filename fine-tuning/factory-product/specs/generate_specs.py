#!/usr/bin/env python3
"""generate_specs.py — one-time (re-runnable) generator for factory/specs/*.yaml.

T10 (PRs/model-factory-v1): converts product-integration/AGENT-CATALOG.md's §B table
(~30 evidence-traced premade agents) into real, schema-valid AgentSpec YAML files.

This is real data entry, not documentation (per PRContext.md) — the structured fields
below are transcribed from the catalog table plus its §A (prompt anatomy) and §C
(packager rules) sections, not invented. Re-running this script regenerates every file
from this single source of truth; hand-edits to individual specs after generation are
expected (the Steering Chat, v1.5+, edits these files directly per DECISIONS.md D-PI-11)
and this script should not be re-run over hand-edited files without reconciling first.

Usage: python3 generate_specs.py
"""

import json
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent

# Shared laws every catalog entry inherits (AGENT-CATALOG.md §C's "hard-won transfer
# rules" + the house's universal invariants) plus a per-spec addendum list.
COMMON_LAWS = [
    "single-writer discipline: own report/branch/worktree only; shared files route through the coordinator",
    "create the report file first (status header) before doing any work — crash/resume safety",
    "never interrupt mid-transfer; liveness is measured by report mtime, not chat presence",
    "records outrank this prompt — if a canon file and this spec disagree, the canon file wins and the disagreement is flagged, not silently resolved",
]

COMMON_CRASH_RESUME = {
    "report_first": True,
    "resume_from_crash_fields": ["last_completed_step", "partial_output_path"],
    "paid": False,
}

COMMON_FINAL_RESPONSE = (
    "headline (one line) + counts + one top finding/prediction, so the coordinator can "
    "triage without opening files (AGENT-CATALOG.md §A.10 / MULTI-AGENT-REVIEW-PLAYBOOK §2.7)"
)


def spec(
    slug,
    role_family,
    mission,
    stages,
    done_when,
    typical_count,
    evidence_pointer,
    read_first,
    mandate,
    outputs,
    laws_extra=None,
    single_writer_owns=None,
    single_writer_avoid=None,
    workspace_env_keys=None,
    paid=False,
    status="draft",
):
    return {
        "slug": slug,
        "role_family": role_family,
        "mission": mission,
        "stages": stages,
        "status": status,
        "spec_version": "v1",
        "read_first": read_first,
        "mandate": mandate,
        "laws": COMMON_LAWS + (laws_extra or []),
        "single_writer": {
            "owns": single_writer_owns or [f"company-brain/3-execution/reports/{slug}.md"],
            "do_not_touch": single_writer_avoid or ["any file another live stream owns (check heartbeat/STATE.md File Locks first)"],
        },
        "workspace": {
            # Real convention, not invented: AGENT-CATALOG.md §A.6's own cited precedent is
            # V5-BUILD's `platform-alpha-v5` worktree — real linked worktrees on disk today
            # follow `platform-alpha-<run-slug>[-<descriptor>]` on branch `feat/<run-slug>[-<descriptor>]`
            # (run-slug FIRST, not role-slug first — QA audit, 2026-07-24). A spec whose
            # `outputs` never touch platform-alpha code (most census/audit/report specs) can
            # work directly in the main workflow-repo checkout instead — no worktree needed —
            # but must still check heartbeat/STATE.md File Locks before writing, same as any
            # shared-repo edit.
            "worktree": (
                "for platform-alpha code changes: own worktree named `platform-alpha-<run-slug>"
                "[-<descriptor>]` (never share a checkout with another live stream); for "
                "report-only outputs in this repo, work directly in the main checkout"
            ),
            "branch_pattern": "feat/<run-slug>[-<descriptor>]",
            "env_keys": workspace_env_keys or [],
        },
        "crash_resume": {**COMMON_CRASH_RESUME, "paid": paid},
        "outputs": outputs,
        "done_when": done_when,
        "final_response_shape": COMMON_FINAL_RESPONSE,
        "typical_count": typical_count,
        "evidence_pointer": evidence_pointer,
        "addenda_channel": "mid-flight changes arrive as self-contained ADDENDUM blocks appended to this agent's inbox file, applied at the next tick boundary (CONTEXT-DESIGN P7)",
    }


SPECS = [
    spec(
        "factory-coordinator", "coordinator",
        "Own the board, write spawn prompts, adjudicate findings, fold results forward; never owns paid execution alone.",
        ["S1", "S2", "S3", "S4", "S5", "S6", "S6g", "S7", "S8", "S9", "S10", "S11"],
        "every finding dispositioned (accept/reject/escalate); the board is the single source of truth for run state",
        "1", "board architecture; 08-SESSION-ORCHESTRATION.md §1; V6-REVIEW",
        ["heartbeat/STATE.md", "the run's org config", "the current stage's report", "PRDone.md's task log"],
        [
            {"task": "adjudicate every subagent finding into accept/reject/escalate", "stop_or_ask": "escalate to Daniel on any finding that touches spend, a signed gate, or a frozen surface"},
            {"task": "write the next spawn prompt from the AgentSpec + this run's context", "stop_or_ask": "stop if the spec's read_first paths don't exist yet — do not spawn against a stale bundle"},
            {"task": "fold every subagent's final_response into the board", "stop_or_ask": "ask before overwriting another session's board row"},
        ],
        ["updated heartbeat/STATE.md board row", "the next spawn prompt (paste-ready)"],
        laws_extra=["never executes a paid stage directly — paid stages are their own spec (train-launcher, eval-window-runner, shadow-ab-runner)"],
    ),
    spec(
        "context-packager", "packager",
        "Assemble the next agent's self-contained context pack so spawn is mechanical, not improvised.",
        ["cross-cut"],
        "board updated with \"safe to spawn\"; every volatile number (budget, SHA, count) is single-sourced, never inlined stale",
        "1 per handoff", "CONTEXT-DESIGN.md P1-P3, P11; handoffs/",
        ["CONTEXT-DESIGN.md", "the target agent's AgentSpec", "the predecessor's handoff artifact", "the run's briefing file"],
        [
            {"task": "assemble the 11-item pack from AGENT-CATALOG.md §C (identity, read-first, mandate, laws, single-writer, workspace, crash block, outputs/done_when, volatile pointers, addendum slot, next-paste line)", "stop_or_ask": "stop if the board prompt this bundle depends on isn't final yet (B4 rule)"},
            {"task": "point at volatile numbers by reference (e.g. \"see reconciliation §Budget\"), never inline a number that can drift", "stop_or_ask": "ask the coordinator if two sources disagree on a volatile number"},
        ],
        ["the assembled context bundle (paste-ready + daemon-spawnable)", "a factory_handoffs edge recording what moved"],
    ),
    spec(
        "org-readiness-census", "forensics",
        "Determine whether an org is trainable and which mode, from prod data floors.",
        ["S1"],
        "tier (A/B/C) + axis verdict (full/behavioral-dominant/factual-dominant) delivered with counts, no PII persisted",
        "1", "GS1 (Grand Steel), TD (trajectory-data census)",
        ["the org's live book counts (read-only prod)", "ORG-READINESS-CENSUS methodology doc", "the org config template"],
        [
            {"task": "pull book counts (accounts, transcripts, reviews, fact-bearing share) read-only", "stop_or_ask": "never write to prod; read-only always"},
            {"task": "classify tier A/B/C and axis verdict against the documented floors", "stop_or_ask": "ask if the org sits exactly on a tier boundary"},
        ],
        ["census report with tier + axis verdict + counts, no customer PII"],
    ),
    spec(
        "trajectory-data-census", "forensics",
        "Locate the real trainable tool-loop source vs unusable logging for this org.",
        ["S1", "S3"],
        "usable-trajectory count delivered with capture gaps explicitly named",
        "1", "TD (trajectory-data census stream)",
        ["the org's agent-run logs", "the trajectory schema this org's tools actually emit"],
        [
            {"task": "distinguish real multi-turn tool trajectories from single-shot logging noise", "stop_or_ask": "flag (don't guess) if the capture format is ambiguous"},
        ],
        ["trajectory-data census report: usable count + named capture gaps"],
    ),
    spec(
        "platform-capability-census", "platform",
        "Verify function-calling / tunability / billing / LR support against live vendor APIs — never from memory.",
        ["S1", "S6", "S7"],
        "every capability question answered with a live GET or a dated URL, never reputation",
        "1", "F1, F2, V5-PLATFORM, R15",
        ["the exact model-id page on the target serving vendor", "the vendor's fine-tuning API docs"],
        [
            {"task": "verify FC support on the EXACT model id + platform, live, dated", "stop_or_ask": "never trust family-level reputation (L16) — check the specific model page"},
            {"task": "verify tunability, min GPU floor, and billing mode (serverless vs dedicated)", "stop_or_ask": "escalate if the vendor page contradicts a prior assumption in the org config"},
        ],
        ["capability report with dated URLs per claim"],
    ),
    spec(
        "phase0-base-census", "eval-prep",
        "Run the untuned-base probe battery before corpus design starts.",
        ["S1", "S8"],
        "instruments offline-tested before the paid window; the paid window is pure execution, no debugging",
        "1", "A1 Phase-0-A",
        ["the eval battery spec", "the base model's serving envelope"],
        [
            {"task": "dry-run every probe instrument against a cheap/offline stand-in before the paid window", "stop_or_ask": "stop before spending if any instrument fails its offline self-test"},
        ],
        ["probe battery report + offline self-test results"],
    ),
    spec(
        "template-parity", "platform",
        "Prove the chat-template, loss-span, and stop tokens match between train and serve.",
        ["S4", "S7", "S10"],
        "label-dump / token-id identity check passes exactly",
        "1", "A1 Phase-0-B (L51/TP2)",
        ["the training render_samples output", "the serving harness's exact rendered payload"],
        [
            {"task": "diff the exact position-0 system message + tokenization between train and serve, per candidate", "stop_or_ask": "block the deploy on any mismatch (L51's mandatory pre-spend check)"},
        ],
        ["parity report: PASS/FAIL with the exact diff if any"],
    ),
    spec(
        "corpus-planner", "builder",
        "Turn audits into family doses, LAWS, mixture bands, and the SEED for this run's corpus.",
        ["S4"],
        "every family cites its evidence or is explicitly labeled untested",
        "1", "A1 Phase-0-C; V5/V6 architecture",
        ["all upstream audit reports (retro, hallucination, prod-fit)", "the prior iteration's mixture history", "V5-ARCHITECTURE.md style caps"],
        [
            {"task": "size every family's dose from measured evidence, never intuition", "stop_or_ask": "label a family untested rather than guess a dose"},
            {"task": "set nested style caps (semantic/skeleton/unpaired-decisive) per the corpus-shape rules", "stop_or_ask": "ask before deviating from the frozen cap percentages"},
        ],
        ["mixture plan doc: families, doses, LAWS, SEED"],
    ),
    spec(
        "product-policy-drafter", "builder",
        "Draft org-specific policy from code + empirical evidence, labeled by provenance.",
        ["S4"],
        "every policy line traces to CODE, EMPIRICAL, or PROPOSED; human decisions reduced to one-liners",
        "1", "F3",
        ["the org's live tool schemas", "prior orgs' policy docs for pattern reuse"],
        [
            {"task": "draft policy with every line labeled CODE/EMPIRICAL/PROPOSED", "stop_or_ask": "never label a guess as CODE or EMPIRICAL"},
        ],
        ["org policy draft doc"],
    ),
    spec(
        "evidence-audit-retro", "forensics",
        "Tie prior corpora to their outcomes; quarantine anti-training gold that taught the wrong lesson.",
        ["S4"],
        "anti-training families and their reuse rules are explicitly named",
        "1", "A1 RETRO-DATA",
        ["all prior iterations' corpora and eval verdicts", "ERROR-REGISTER.md"],
        [
            {"task": "trace each corpus family forward to its measured outcome", "stop_or_ask": "quarantine (don't silently drop) any family implicated in a regression"},
        ],
        ["retro audit report: anti-training families + reuse rules"],
    ),
    spec(
        "evidence-audit-hallu", "forensics",
        "Map every hallucination channel to covered / partial / gap.",
        ["S4", "S8"],
        "every gap becomes a named family or a named probe — no silent gaps",
        "1", "A1 HALLU-GATE-AUDIT",
        ["ERROR-REGISTER.md's fabrication-class rows", "the current corpus plan"],
        [
            {"task": "enumerate every known hallucination channel and its current coverage state", "stop_or_ask": "escalate any gap with no assigned owner (family or probe)"},
        ],
        ["hallucination coverage matrix"],
    ),
    spec(
        "evidence-audit-prod-fit", "forensics",
        "Compare training fixtures against real production bytes — payload realism and outcome distribution.",
        ["S4", "S8"],
        "method artifacts are called out explicitly; LAW targets are set from measured production shape",
        "1", "A1 PROD-FIT",
        ["a sample of real production payloads (read-only)", "the current fixture-generation scripts"],
        [
            {"task": "diff fixture shape vs live production payload shape", "stop_or_ask": "flag any systematic fixture artifact rather than silently correcting it"},
        ],
        ["prod-fit audit report"],
    ),
    spec(
        "corpus-family-generator", "builder",
        "Over-generate then deterministically select one risk-class family under the locked LAWS.",
        ["S4"],
        "the self-audit is reproducible by an independent linter run",
        "N (by risk class)", "A1 GEN-A/B; INT Builders A-D; V5-BUILD",
        ["the mixture plan's LAWS for this family", "the deterministic-generator precedent (L19) if this family has hard invariants"],
        [
            {"task": "over-generate ~2x, deterministically select the best half", "stop_or_ask": "never hand-pick — selection must be reproducible by re-running the same script"},
            {"task": "use a deterministic generator (not free-form) wherever a hard invariant exists (L19)", "stop_or_ask": "ask before free-forming a slice with a hard invariant"},
        ],
        ["the generated family's records + regeneration command + git SHA"],
    ),
    spec(
        "corpus-reader", "reader",
        "100% per-record checklist read on an assigned line range — no sampling.",
        ["S4", "S5"],
        "every record in the assigned range gets a verdict; writes are incremental (crash-safe)",
        "12 (or 4)", "V6-REVIEW readers; A1 Phase-0-D-READ-*",
        ["the review checklist template", "the assigned line range + output path"],
        [
            {"task": "read every record in the assigned range against the checklist", "stop_or_ask": "never skip a record — flag ambiguous ones instead of guessing"},
            {"task": "write verdicts incrementally, not only at the end", "stop_or_ask": "n/a — this is mandatory, not a judgment call (L47)"},
        ],
        ["per-record verdict file (KEEP/STRIP/CURATE/CUT/REWRITE), incrementally written"],
        single_writer_owns=["this reader's own output file (own line range only)"],
    ),
    spec(
        "expert-lens", "expert-lens",
        "Zero-overlap persona stress-test with falsifiable predictions, each backed by a named probe.",
        ["S4", "S6g"],
        "output is deltas-only vs the baseline; every HIGH-severity finding has a named probe",
        "4-6", "V6-ARCH panels; MULTI-AGENT-REVIEW-PLAYBOOK L2/L7",
        ["this lens's assigned persona brief", "the corpus/architecture under review"],
        [
            {"task": "review from the assigned persona only — no overlap with sibling lenses", "stop_or_ask": "flag (don't silently absorb) a finding that belongs to another lens's persona"},
            {"task": "every finding above LOW severity gets a falsifiable, named probe", "stop_or_ask": "n/a — unfalsifiable findings are dropped, not escalated"},
        ],
        ["persona review report: deltas-only findings + probes"],
    ),
    spec(
        "devils-advocate", "expert-lens",
        "Must argue for failure; also audits the review program itself for blind spots.",
        ["S4", "S6g"],
        "names the most-likely failure mode AND at least one hole in the review program",
        "1", "V6-REVIEW finding #17",
        ["every other lens's findings so far", "the review program's own design doc"],
        [
            {"task": "argue the strongest case for why this corpus/decision fails", "stop_or_ask": "n/a — the mandate is adversarial by design"},
            {"task": "name at least one blind spot in the review program's own coverage", "stop_or_ask": "escalate if no blind spot can be honestly found — say so, don't invent one"},
        ],
        ["devil's-advocate report: failure case + program blind spots"],
    ),
    spec(
        "calibrated-predictor", "expert-lens",
        "Blind retro-calibrate against a held-out sample, then predict forward with named probes.",
        ["S4", "S6g"],
        "every prediction has a named probe; confidence bands are derived from the measured retro-calibration precision, not guessed",
        "1+2", "PREDICT-RETRO, PASS4-*",
        ["a held-out sample for blind calibration", "the current architecture/corpus decision to predict against"],
        [
            {"task": "blind-predict on the held-out sample first, then compare to ground truth to calibrate", "stop_or_ask": "never predict forward before calibrating on the blind sample"},
            {"task": "set confidence bands from the measured calibration precision", "stop_or_ask": "flag if calibration precision is too low to trust a forward prediction"},
        ],
        ["calibration report + forward predictions with named probes"],
    ),
    spec(
        "curation-consolidator", "consolidator",
        "Merge every reader's line-verdict lists into one deterministic work queue.",
        ["S4"],
        "every line has exactly one verdict; both-ways disagreement count (L52-style) is reported",
        "1", "CURATION-MANIFEST",
        ["every corpus-reader's output file for this run", "the disposition-precedence rule (which verdict wins a tie)"],
        [
            {"task": "merge all readers' verdicts into one queue with a deterministic tie-break rule", "stop_or_ask": "escalate systematic disagreement between readers rather than silently averaging"},
        ],
        ["CURATION-MANIFEST: one verdict per line + disagreement count"],
    ),
    spec(
        "review-aggregator", "consolidator",
        "Family-dedupe every lens's findings and disposition each as gate / data-change / accept.",
        ["S4"],
        "signing is held until the aggregate report lands — no partial sign-off",
        "1", "V6-REVIEW aggregation",
        ["every expert-lens's report for this run", "the devils-advocate report"],
        [
            {"task": "dedupe findings by family (not by raw count) and disposition each", "stop_or_ask": "escalate any finding two lenses disagree on the disposition of"},
        ],
        ["aggregate review report: one disposition per family"],
    ),
    spec(
        "preflight-linter", "verifier",
        "Build and run the binding pre-upload linters; machine-verify every review claim.",
        ["S5"],
        "the linter self-tests on gold+seeded-fault fixtures; agree/disagree with the review program is explicit",
        "1", "V6-LINTERS, PREFLIGHT-DIAG, V5-SHORTCUT-CHECK",
        ["the corpus under review", "the review program's claimed findings", "prior linter scripts as a base"],
        [
            {"task": "self-test every linter on a gold sample and a seeded-fault sample before running it for real", "stop_or_ask": "never ship a linter verdict without a passing self-test"},
            {"task": "cross-check the linter's verdict against the review program's claims", "stop_or_ask": "escalate any disagreement rather than silently trusting one over the other"},
        ],
        ["linter report + self-test results"],
    ),
    spec(
        "eval-instrument-builder", "eval-prep",
        "Build eval gates, suites, and vaults before the model exists; dry-run with seeded faults.",
        ["S5", "S8"],
        "the instrument rediscovers every known failure unaided, or the build stops",
        "1 per generation", "V5/V6-EVAL-PREP, H3/H4-prep, EVAL-ONE",
        ["ERROR-REGISTER.md's known failure classes", "the prior generation's eval suite as a base"],
        [
            {"task": "build the gate/suite before the model exists, from known failure classes", "stop_or_ask": "stop the build if a seeded-fault dry-run fails to rediscover a known failure"},
        ],
        ["eval instrument (gates + suites) + seeded-fault dry-run results"],
    ),
    spec(
        "gate-audit", "verifier",
        "Re-verify every launch-gate claim against live bytes before spend is approved.",
        ["S6g"],
        "every gate claim is marked VERIFIED or FAIL by actual re-execution, never by trusting the report prose",
        "1", "A1 GATE-AUDIT",
        ["the S6g decision-request draft", "every gate's underlying evidence artifact"],
        [
            {"task": "re-execute (not re-read) every gate claim against the live artifact", "stop_or_ask": "mark FAIL rather than guess PASS on any claim that can't be re-executed"},
        ],
        ["gate-audit report: VERIFIED/FAIL per claim"],
    ),
    spec(
        "go-redteam", "expert-lens",
        "Adversarially review the GO decision itself, with a pre-committed escalation ladder.",
        ["S6g"],
        "the escalation ladder and the drop-list are both pre-committed and complete before Daniel reads the recommendation",
        "1", "A1 GO-REDTEAM",
        ["the S6g decision-request draft", "the gate-audit report"],
        [
            {"task": "argue against the GO recommendation with a pre-committed escalation ladder", "stop_or_ask": "n/a — pre-commitment is the point, not a judgment call"},
        ],
        ["go-redteam report: escalation ladder + drop-list"],
    ),
    spec(
        "launch-rehearsal", "platform",
        "Dry-run launch day end to end — keys, pods, uploads, kill commands — before real spend.",
        ["S6g", "S7"],
        "every blocking ops issue is found before rent is paid",
        "1", "A1 LAUNCH-REHEARSAL (caught a missing-SSH-key blocker)",
        ["the exact launch-day runbook", "every credential/key the runbook references"],
        [
            {"task": "dry-run every step of the launch runbook without spending", "stop_or_ask": "block launch on any step that can't be dry-run cleanly"},
        ],
        ["rehearsal report: blocking issues found + fixed"],
    ),
    spec(
        "train-launcher", "platform",
        "Assemble, upload, launch, and monitor one SFT job under the Daniel-signed body.",
        ["S7"],
        "the launch manifest is signed before upload; a PAUSED job is resumed, never deleted; the agent stops before eval",
        "1", "D2, D3, V4-TRAIN; fw_autorun pattern",
        ["the signed training manifest", "TRAINING-RUNBOOK.md", "fw_monitor.py's resume-on-pause pattern"],
        [
            {"task": "sign and store the immutable dataset manifest before launch", "stop_or_ask": "never launch against an unsigned manifest"},
            {"task": "monitor and auto-resume on JOB_STATE_PAUSED (never delete+relaunch, L34a)", "stop_or_ask": "escalate before any delete — it is irreversible"},
            {"task": "stop after training completes — eval is a separate spec's job", "stop_or_ask": "n/a — do not chain into eval"},
        ],
        ["training job launch record + monitor log"],
        laws_extra=["a PAUSED fine-tune is RESUMED, never deleted+relaunched (L34a)"],
        paid=True,
    ),
    spec(
        "eval-window-runner", "verifier",
        "Run one dedicated eval window: FC smoke first, then the full battery, then verified teardown.",
        ["S8"],
        "teardown is verified GET->404 (not fire-and-forget); dedicated routing is asserted on every call",
        "1", "H, EVAL-ONE, A1 EVAL-WINDOW",
        ["the eval instrument from eval-instrument-builder", "the deployment/teardown runbook"],
        [
            {"task": "run the FC smoke first; teardown immediately on smoke failure", "stop_or_ask": "n/a — smoke-fail is an automatic teardown, not a judgment call (L28)"},
            {"task": "assert dedicated routing on every live call", "stop_or_ask": "abort rather than let a call silently fall back to serverless (L34-routing)"},
            {"task": "verify teardown with a retrying GET until 0 live, never fire-and-forget", "stop_or_ask": "escalate a CRITICAL alert if teardown can't be confirmed after N retries (L46)"},
        ],
        ["eval battery results + verified-zero teardown confirmation"],
        laws_extra=["teardown must VERIFY-ZERO via retrying GET, never fire-and-forget (L46)"],
        paid=True,
    ),
    spec(
        "grader-auditor", "grader",
        "Hunt grader false negatives; re-baseline offline; wire GRADER-SUSPECT escalation.",
        ["S8"],
        "historical controls still fire correctly; zero over-correction introduced",
        "1", "GRADER-FIX, GRADER-FIX-2",
        ["the current grader's source", "a sample of its recorded failures", "historical control cases it must still pass"],
        [
            {"task": "blind re-adjudicate a sample of recorded failures independently of the grader", "stop_or_ask": "escalate (GRADER-SUSPECT) if disagreement exceeds 10% on any gate"},
            {"task": "ship only both-direction regression tests (false-negative flips AND seeded true-failures still fail)", "stop_or_ask": "never ship a grader fix without both directions tested"},
        ],
        ["grader-fix report + both-direction regression tests"],
    ),
    spec(
        "error-register-keeper", "forensics",
        "Ensure every error class ever observed has EXACTLY ONE disposition — gate, corpus slice, guardrail, or accepted.",
        ["S4", "S8", "S9"],
        "every class has exactly one disposition; any class with none is a named coverage hole",
        "1", "ERROR-REGISTER.md",
        ["ERROR-REGISTER.md", "the current gate enumeration", "the current corpus mixture plan"],
        [
            {"task": "cross-check every historical error class against the current gate/slice/guardrail enumeration", "stop_or_ask": "name a coverage hole rather than assume implicit coverage"},
        ],
        ["updated ERROR-REGISTER.md with dispositions + a hole list"],
        # QA audit, 2026-07-24: the default `owns` (this spec's own report file only) directly
        # contradicted `outputs`/`done_when`, both of which require writing the real, shared
        # `company-brain/3-execution/ERROR-REGISTER.md` — a file 3+ other specs read as input.
        # Without this, an agent following the spec literally has no declared exclusive claim
        # on the one shared file it's actually mandated to update.
        single_writer_owns=[
            "company-brain/3-execution/ERROR-REGISTER.md",
            "company-brain/3-execution/reports/error-register-keeper.md",
        ],
    ),
    spec(
        "postmortem-forensics", "forensics",
        "Root-cause a NO-GO from saved evidence only, at $0, with no redeploy.",
        ["S8", "S9"],
        "ranked hypotheses delivered with a don't-disturb list for anything the postmortem didn't rule out",
        "1", "DX, VX-FORENSICS, VX2-FORENSICS",
        ["every saved artifact from the failed run (reports, logs, corpus manifests)", "the eval verdict"],
        [
            {"task": "root-cause from saved evidence only — no new spend, no redeploy", "stop_or_ask": "escalate if the saved evidence is genuinely insufficient rather than guessing"},
        ],
        ["forensics report: ranked hypotheses + don't-disturb list"],
    ),
    spec(
        "shadow-context-researcher", "shadow",
        "Document the exact A/B shadow-serving mechanics, win axes, and a complete handoff.",
        ["S8", "S9", "S10"],
        "win conditions and uncovered axes are both explicitly listed",
        "1", "AB-SHADOW context; A1 AB-NIGHTLY-CONTEXT",
        ["the shadow-serving runbook", "prior A/B comparison HTML outputs"],
        [
            {"task": "document win axes and mechanics precisely enough for a runner to execute unaided", "stop_or_ask": "name any axis the current design doesn't cover"},
        ],
        ["shadow-context handoff (7-artifact bundle per AB-SHADOW precedent)"],
    ),
    spec(
        "shadow-ab-runner", "shadow",
        "Replay a live night against the tuned arm(s) and render the comparison HTML.",
        ["S9", "S10"],
        "arms complete or capacity-pending is explicit; the dollar cap holds",
        "1", "AB-SHADOW; A1 AB-RETRO / NIGHT-RUN",
        ["the shadow-context handoff", "the night's live traffic replay source"],
        [
            {"task": "replay live traffic against each arm under the dollar cap", "stop_or_ask": "stop at the cap, mark remaining arms capacity-pending rather than overspend"},
        ],
        ["comparison HTML + per-arm completion status"],
        paid=True,
    ),
    spec(
        "serve-wire", "platform",
        "Wire production serving behind flags/guardrails with a drilled rollback.",
        ["S10"],
        "smoke test passes end to end; the kill switch is real; no accidental promotion",
        "1", "INT-PILOT-WIRE, A1-STAGED-SHIP",
        ["the activation runbook", "the org's serving config", "the rollback drill script"],
        [
            {"task": "wire behind a flag that defaults off; drill the rollback before flipping the flag on", "stop_or_ask": "never flip the flag on without a drilled rollback first"},
        ],
        ["serve-wire report: smoke results + rollback drill confirmation"],
    ),
    spec(
        "gpu-broker", "platform",
        "Own GPU acquisition across providers as a standing service; sessions submit jobs, never deploy directly.",
        ["S7", "S8", "S10"],
        "self-tests are green; single-owner slot guards hold; no silent idle billing",
        "1 standing", "GPU-BROKER-BUILD",
        ["GPU-LESSONS.md (49 categorized rules from the capacity-drought incident)", "the broker's own self-test suite"],
        [
            {"task": "own the acquisition slot exclusively — sessions request, never deploy directly", "stop_or_ask": "escalate rather than let two callers race for one slot"},
            {"task": "verify liveness from ps/lockfiles, never from log lines alone", "stop_or_ask": "n/a — mandatory per GPU-LESSONS.md (e)"},
        ],
        ["broker self-test results + acquisition log"],
        laws_extra=["single-owner slot semantics — one broker owns acquisition, never N independent waiters (GPU-LESSONS.md (c))"],
    ),
]


def main():
    out_dir = HERE
    written = []
    for s in SPECS:
        path = out_dir / f"{s['slug']}.yaml"
        with open(path, "w") as f:
            yaml.safe_dump(s, f, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100)
        written.append(path.name)
    print(f"wrote {len(written)} spec files to {out_dir}")
    return written


if __name__ == "__main__":
    main()
