#!/usr/bin/env python3
"""models_backfill — one-time (idempotent) historical seed for the Models tab.

**Why this exists.** `backfill_ptc_history.py` seeded 4 `factory_runs` rows
for PTC Steel's real pre-automation fine-tune history, but a *run* is a
pipeline execution attempt, not a model (all 4 are `status='no_go'` — no
model has ever shipped to product, per `MIXTURE-HISTORY.md` §0). Daniel
(2026-07-29): "make sure that we actually store information about previously
trained models as currently they do not provide much information... clicking
an older fine-tuned model... surfaces relevant training information and
allows the user to see the training distribution." This script is that
missing catalog: one `factory_models` row per real trained artifact
(`ptc-steel-v1`..`v4`), each linked via `source_run_id` to the matching
`factory_runs` row `backfill_ptc_history.py` already created (same
deterministic `uuid5` id — imported from that module, not recomputed, so the
two scripts can never drift into referencing different run ids).

**Every number below has a citation** in
`company-brain/3-execution/MIXTURE-HISTORY.md` — chiefly its §1 per-iteration
comparison table (base model / LoRA config / records / tokens / cost /
loss) and §1's per-corpus mixture breakdowns (the "training distribution"
tables). v1/v2 predate the loss-bearing-token accounting unit (introduced at
v3 per the file's own unit legend) — their `mixture` blocks are therefore
record-count/weighting breakdowns, not lb-token tables, and this script does
NOT fabricate a unit conversion MIXTURE-HISTORY.md itself never computed.

**The `report` sections** (PR-B addendum 2) mirror the human-facing HTML
iteration briefs in `company-brain/deliverables/` — same h2 section set
("what this iteration is, in one paragraph" / "exact identifiers" / "what it
was trained on" / "how it was evaluated" / "known open questions" / "how to
test it"), body as plain text (blank-line paragraph breaks, `- ` list items,
no HTML/markdown). Brief→model mapping was verified against the exact model
identifiers named INSIDE each brief, not the filenames:

  - ptc-steel-v1        <- ITERATION-2-TECHNICAL-REPORT.html §2 ("what
    iteration 1 taught us") + EVAL-V2-RESULTS-BRIEF.html §1 (v1's 8-gate
    table); full id `accounts/emanate/models/ptc-steel-v1` attested in
    ITERATION-3-RESULTS-AND-NEXT.html §7's model-name table.
  - ptc-steel-v2-agent  <- ITERATION-2-TECHNICAL-REPORT.html (32,568 records
    / $45.16 / Qwen3.6-27B match this row exactly) + EVAL-V2-RESULTS-BRIEF.html.
  - ptc-steel-v3-agent  <- ITERATION-3-BRIEF.html (names the model in its
    title) + ITERATION-3-RESULTS-AND-NEXT.html (results + test steps).
  - ptc-steel-v4-agent  <- ITERATION-4-AND-V5-PLAN.html §§1-3 (its v3-vs-v4
    numbers match MIXTURE-HISTORY.md §2.3's v4 column one-for-one). That
    brief publishes no manual-test procedure and no full serving path for
    v4, so this model's report omits "how to test it" rather than guessing.

**EXTRA_MODELS (2026-07-30, Daniel: "I dont see v5, the fine tuned models we
actually ended up using in production").** Three real trained artifacts exist
beyond the four PTC iterations, and the catalog must carry them — with their
true statuses, which are NOT "in production serving customers":

  - ptc-steel-v5-agent   <- 07-RUN-LEDGER.md v5 row + DATASETS.md
    combined-train-v6-agent row (SFT job erp9alb9). NO-GO: E21 tool
    avoidance. The 5th and last PTC per-account iteration.
  - ptc-steel-a1-v1      <- reports/A1-V1.md (trained 07-22 03:00 PT, rc=0,
    pod 7 2xH200 RunPod, self-run axolotl). NO-GO as an active arm; wired
    into the nightly per-account pipeline as a SHADOW arm only — send
    authority stays with Claude until the V-036 bar is met.
  - intv1-corpus-v1      <- INT-V1-STATE.md + reports/INT-V1.md (Together AI
    job ft-b32c0e5a-8d16, completed 07-22 00:40 PT, billed-token byte-match).
    Pilot-YES behind guardrails / launch-NO pending the K3 re-check; the
    Intelligence chat stays answered by gpt-5.6.

Ground truth stated in every source: NO fine-tuned model from any stream has
ever served a customer. The two "production" models Daniel remembers are the
two shadow lanes above — surfaced here with that status stated plainly.

**Consolidation (model-factory-production-catchup, 2026-08-14).** This is now
the single canonical writer for all 10 real trained models across every
stream (PTC iterations, PTC shadow-lane extras, Intelligence, Grand Steel) —
run once per model-completion event, regardless of stream. It folds in two
former one-off scripts: `runs/grand-steel/gs_corpus/record_model.py`
(grand-steel-v1-agent — real but stale, refreshed here) and
`runs/ptc-steel/corpus/record_model.py` (a stray, never-run byte-for-byte
duplicate of the Grand Steel script under a PTC Steel path) — both deleted.
The next model's completion should add a row here, not spawn a new script.

Usage:
    export FACTORY_SUPABASE_URL=...
    export FACTORY_SUPABASE_SERVICE_ROLE_KEY=...
    python3 models_backfill.py --dry-run   # prints the 10 model rows, writes nothing
    python3 models_backfill.py             # pushes to Supabase
"""

import argparse
import sys
import uuid

from backfill_ptc_history import ITERATIONS, NAMESPACE, ORG_NAME, ORG_SLUG, _run_id
from supabase_rest import SupabaseRestError, upsert

SOURCE = "company-brain/3-execution/MIXTURE-HISTORY.md §1 (per-iteration comparison table + mixture breakdowns)"

# ---------------------------------------------------------------------------
# Training-infrastructure provenance (PR-B addendum 3, Daniel: "we should be
# able to know if we used togetherai, fireworks, and how many gpus were rented
# and at what time").
#
# PROVIDER TRAP: the real platform for ALL FOUR models is FIREWORKS — the
# historical TOGETHER_BASE_URL/TOGETHER_API_KEY env names pointed at Fireworks
# values (factory.py documents this). Recorded as the true provider, never
# derived from an env-var name.
#
# JOB IDS are real, verbatim from company-brain/3-execution/DATASETS.md:
#   v1: `accounts/emanate/supervisedFineTuningJobs/mjbsx3uu` (the ptc-steel-v1
#       COMPLETED row gives the full resource name)
#   v2: `accounts/emanate/supervisedFineTuningJobs/npemi0j7` (full name in the
#       ptc-steel-v2-agent COMPLETED row)
#   v3: `accounts/emanate/supervisedFineTuningJobs/xhw6v0h7` — VERIFIED LIVE
#       against the Fireworks API (2026-07-29): the job list shows xhw6v0h7 as
#       the completed job whose outputModel is ptc-steel-v3-agent (created
#       2026-07-14T16:42:56Z). DATASETS.md's combined-v4d row says "SFT job
#       brnbzcg0", but that id 404s and appears nowhere in the account's job
#       list — the platform record wins over the hand-written registry.
#   v4: short id `pzc743aj` (DATASETS.md naming-decoder header: "iter 4, job
#       pzc743aj; the earlier h2m2mqpr was killed at 0% by the cost gate")
#
# GPU HONESTY: Fireworks supervised fine-tuning is serverless — the SFT job
# API exposes no accelerator fields and no rental happened, so every gpu_*
# column stays None here. Job-level timing, where the sources record it
# (v1/v2 createTime/completedTime in DATASETS.md), lives in `infra`; v3/v4
# timing was never published day-precise — provider_enrich.py can fetch it
# from the job API instead of this script guessing.
#
# LOSS CURVES contain ONLY real recorded numbers:
#   v1: [] — only a single final eval/loss (1.1324 @ step 922, DATASETS.md)
#       exists; a one-point curve is a scalar, and the scalar already renders
#       via train_eval_loss.
#   v2: eval/loss per epoch 2.583 -> 2.560 -> 2.542 (DATASETS.md
#       ptc-steel-v2-agent row, explicitly "per epoch", 3 epochs)
#   v3: eval/loss 2.530 -> 2.505 over 2 epochs (MIXTURE-HISTORY.md §1.1)
#   v4: eval/loss 2.740 -> 2.832 over 2 epochs, rising = the overfit signal
#       (MIXTURE-HISTORY.md §1.1; ITERATION-4-AND-V5-PLAN.html §3 confirms the
#       degradation happened "during epoch 2")
# ---------------------------------------------------------------------------
TRAINING_PROVIDER = "fireworks"
FIREWORKS_SFT_JOBS = "accounts/emanate/supervisedFineTuningJobs"

MODELS = [
    {
        "name": "ptc-steel-v1",
        "provider_job_id": f"{FIREWORKS_SFT_JOBS}/mjbsx3uu",
        "provider_model_path": "accounts/emanate/models/ptc-steel-v1",
        "infra": {
            "platform": "Fireworks supervised fine-tuning (serverless — no dedicated GPU rental)",
            "dataset": "accounts/emanate/datasets/qa-train-v2 (+ qa-eval-v1 eval split)",
            "fireworks_estimated_token_count": "3,484,700 (qa-train-v2 train; qa-eval-v1 eval 442,600)",
            "training_steps": 922,
            "job_create_time": "2026-07-10T00:04:43Z",
            "job_completed_time": "2026-07-10T01:54:43Z",
            "wall_clock": "1h 50m",
            "artifact": "HF_PEFT_ADDON, state READY — single checkpoint (Fireworks exposes no per-epoch checkpoints)",
        },
        "loss_curve": [],
        "base_model": "qwen3p5-35b-a3b (Qwen3.5-35B-A3B, MoE, 36B tot/3B act)",
        "lora_rank": 32,
        "lora_alpha": "not recorded (F2's record-alpha rule postdates v1)",
        "learning_rate": "1e-5",
        "epochs": 2,
        "weight_decay": "not recorded",
        "target_modules": "all-linear (12)",
        "train_records": 14732,
        "train_eval_loss": "3.54 → ~1.1; eval/loss 1.13 (ppl 3.10)",
        "mixture": {
            "corpus": "qa-train-v2 (14,732 records, by record count — lb-token unit introduced at v3)",
            "duplication_mechanic": [
                {"slice": "ranking", "base": 203, "duplicated": 1421, "mechanic": "x7"},
                {"slice": "delta_conflict", "base": 302, "duplicated": 604, "mechanic": "x2"},
                {"slice": "abstention_cutoff", "base": 711, "duplicated": 355, "mechanic": "drop-half (seed 42)"},
                {"slice": "everything_else", "base": 12352, "duplicated": 12352, "mechanic": "x1"},
            ],
            "weighting": "`weight` field stripped entirely — duplication IS the weighting mechanic for v1",
        },
    },
    {
        "name": "ptc-steel-v2-agent",
        "provider_job_id": f"{FIREWORKS_SFT_JOBS}/npemi0j7",
        "provider_model_path": "accounts/emanate/models/ptc-steel-v2-agent",
        "infra": {
            "platform": "Fireworks supervised fine-tuning (serverless — no dedicated GPU rental)",
            "dataset": "accounts/emanate/datasets/combined-v2-agent-train (+ qa-eval-v3 eval split)",
            "fireworks_estimated_token_count": "3,643,700/epoch; 15,053,142 trained tokens billed (multi-turn unroll ~1.38x)",
            "training_steps": 3054,
            "job_create_time": "2026-07-10T23:09:35Z",
            "job_completed_time": "2026-07-11T04:23:35Z",
            "wall_clock": "5h 14m",
            "artifact": "HF_PEFT_ADDON, state READY — single checkpoint",
            "serving_note": "FP8 live-merge only — the BF16 multi-LoRA addon path fails at replica init for this base",
        },
        "loss_curve": [
            {"step": 1, "loss": 2.583, "label": "epoch 1 (eval loss)"},
            {"step": 2, "loss": 2.560, "label": "epoch 2 (eval loss)"},
            {"step": 3, "loss": 2.542, "label": "epoch 3 (eval loss)"},
        ],
        "base_model": "qwen3p6-27b (dense 27.3B, FC-capable — base CHANGE forced by F1, no FC on Qwen3.5 family)",
        "lora_rank": 32,
        "lora_alpha": "alpha32 / scaling 1.0",
        "learning_rate": "3e-6",
        "epochs": 3,
        "weight_decay": "0",
        "target_modules": "all-linear (12)",
        "train_records": 32568,
        "train_eval_loss": "1.04 → 0.46; eval/loss 2.583→2.560→2.542 FLAT after ep1 (ppl ~12.7)",
        "mixture": {
            "corpus": "combined-v2-agent-train (32,568 records, by record count)",
            "weight_distribution": [
                {"weight": 3.0, "records": 23289},
                {"weight": 2.0, "records": 460},
                {"weight": 1.0, "records": 8596},
                {"weight": 0.5, "records": 223},
            ],
            "qa_breakdown": (
                "qa-train-v3 (29,168): numeric 21,843 / comparison 1,170 / other-name-robustness 2,910 / "
                "account cards 1,015 / hard negatives 800 / refusal 170 / abstention-fabricated 686 / "
                "abstention-cutoff 223 / delta-conflict 288 / positive-today 63"
            ),
            "tool_breakdown": (
                "agent-traj-v1 (3,000) + v1b (400): resolve+answer 1,200 / typo-alias 450 / "
                "unknown-ambiguous 450 / activity 450 / ranking-with-batch 450 / decision-boundary supplement 400"
            ),
        },
    },
    {
        "name": "ptc-steel-v3-agent",
        "provider_job_id": f"{FIREWORKS_SFT_JOBS}/xhw6v0h7",
        "provider_model_path": "accounts/emanate/models/ptc-steel-v3-agent",
        "infra": {
            "platform": "Fireworks supervised fine-tuning (serverless — no dedicated GPU rental)",
            "dataset": "accounts/emanate/datasets/combined-v4d-agent-train",
            "fireworks_estimated_token_count": "3,998,200/epoch; unroll-aware ~12.65M tokens/epoch (x1.627, tiktoken proxy)",
            "telemetry": "W&B telemetry on",
        },
        "loss_curve": [
            {"step": 1, "loss": 2.530, "label": "epoch 1 (eval loss)"},
            {"step": 2, "loss": 2.505, "label": "epoch 2 (eval loss)"},
        ],
        "base_model": "qwen3p6-27b (same as v2)",
        "lora_rank": 32,
        "lora_alpha": "alpha32 / scaling 1.0",
        "learning_rate": "3e-6",
        "epochs": 2,
        "weight_decay": "0",
        "target_modules": "all-linear (12)",
        "train_records": 35778,
        "train_eval_loss": "1.708 → 0.140; eval/loss 2.530→2.505 falling",
        "mixture": {
            "corpus": "combined-train-v4d-agent (35,778 records, by loss-bearing tokens — unit introduced this iteration)",
            "unit": "loss-bearing tokens (lb-tokens)",
            "breakdown": [
                {"group": "QA_factual", "records": 27908, "lb_tokens": 914756, "raw_pct": 46.9},
                {"group": "QA_rehearsal_dup", "records": 2873, "lb_tokens": 254691, "raw_pct": 13.1},
                {"group": "tool_v1", "records": 3000, "lb_tokens": 288116, "raw_pct": 14.8},
                {"group": "tool_v1b", "records": 400, "lb_tokens": 34114, "raw_pct": 1.8},
                {"group": "tool_s1_field_unavail", "records": 430, "lb_tokens": 56476, "raw_pct": 2.9},
                {"group": "tool_s2_resolve", "records": 250, "lb_tokens": 23767, "raw_pct": 1.2},
                {"group": "tool_s4_real (NEW — 357 real per-account trajectories)", "records": 357, "lb_tokens": 330474, "raw_pct": 17.0},
                {"group": "QA total", "records": 30781, "lb_tokens": 1169447, "raw_pct": 60.0},
                {"group": "TOOL total", "records": 4997, "lb_tokens": 779477, "raw_pct": 40.0},
            ],
        },
    },
    {
        "name": "ptc-steel-v4-agent",
        "provider_job_id": f"{FIREWORKS_SFT_JOBS}/pzc743aj",
        # No full serving path for v4 was ever published in the briefs or
        # registries — NULL is the honest value (see the report's identifiers
        # section, which says so in prose).
        "provider_model_path": None,
        "infra": {
            "platform": "Fireworks supervised fine-tuning (serverless — no dedicated GPU rental)",
            "dataset": "accounts/emanate/datasets/combined-v5-agent-train",
            "fireworks_estimated_token_count": "4,588,500",
            "predecessor_job_note": "earlier job h2m2mqpr killed at 0% by the cost-drift gate (~$0); pzc743aj is the run that trained this model",
        },
        "loss_curve": [
            {"step": 1, "loss": 2.740, "label": "epoch 1 (eval loss)"},
            {"step": 2, "loss": 2.832, "label": "epoch 2 (eval loss)"},
        ],
        "base_model": "qwen3p6-27b (same as v2/v3)",
        "lora_rank": 32,
        "lora_alpha": "alpha32 / scaling 1.0",
        "learning_rate": "3e-6",
        "epochs": 2,
        "weight_decay": "0",
        "target_modules": "all-linear (12)",
        "train_records": 19807,
        "train_eval_loss": "1.200 → 0.257; eval/loss 2.740→2.832 RISING (overfit signal)",
        "mixture": {
            "corpus": "combined-train-v5-agent (19,807 records, by loss-bearing/effective-lb tokens)",
            "unit": "loss-bearing / effective-lb tokens (integer-dup dosing)",
            "headline_change": (
                "Removed ALL 27,908 closed-book value-QA records (914,756 lb-tokens, 46.9% of v3's mass) "
                "and replaced them with a 3,000-record tool-form recognition anchor emitting no metric value "
                "(D-MEM-01 A') — the only iteration where raw QA share drops below tool share."
            ),
            "breakdown": [
                {"group": "delta_conflict (x4)", "records": 1148, "lb_tokens": 131192, "raw_pct": 7.1},
                {"group": "positive_today (x2)", "records": 126, "lb_tokens": 14186, "raw_pct": 0.8},
                {"group": "A1 post_cutoff (x2)", "records": 2240, "lb_tokens": 132204, "raw_pct": 9.3},
                {"group": "A2 cite_the_count (x2)", "records": 2560, "lb_tokens": 130664, "raw_pct": 7.3},
                {"group": "A4 fabricated (x2)", "records": 2490, "lb_tokens": 131294, "raw_pct": 7.1},
                {"group": "A5 whole_book_triage (x2)", "records": 2240, "lb_tokens": 134882, "raw_pct": 7.3},
                {"group": "tool_v1 / v1b", "records": 3400, "lb_tokens": 322230},
                {"group": "tool_anchor (D-MEM-01 A', NEW)", "records": 3000, "lb_tokens": 267062},
                {"group": "tool_a3_field_unavail (rebuilt)", "records": 1236, "lb_tokens": 147935},
                {"group": "QA-rehearsal total", "records": 10804, "lb_tokens": 717758, "raw_pct": 38.8},
                {"group": "TOOL total", "records": 8803, "lb_tokens": 1134065, "raw_pct": 61.2},
            ],
        },
    },
]


# Narrative report sections per model — the Models tab's brief-shaped detail
# content. Every claim below traces to the HTML briefs mapped in the module
# docstring or to MIXTURE-HISTORY.md; bodies are plain text (blank-line
# paragraph breaks, "- " list items). None of these models was ever adopted
# into the product, and no section below says otherwise.
REPORTS = {
    "ptc-steel-v1": [
        {
            "title": "What iteration 1 is, in one paragraph",
            "body": """\
Iteration 1 was the program's first fine-tune experiment: put the customer's whole account book directly into an open-weights model's weights. Qwen3.5-35B-A3B (MoE) with a rank-32 LoRA adapter was trained for 2 epochs on 14,732 QA pairs (~3 phrasings per fact) for $19.36. The loss curve looked smooth — and the model still failed 4 of 8 evaluation gates: every judgment gate (refusals, delta arbitration) passed while factual recall came in at 36% against an 80% bar. Verdict: NO-GO, never adopted. Its lesson set the program's whole architecture: exact facts do not survive in a rank-32 adapter at ~30k-fact scale — facts belong in tools, weights hold behavior.""",
        },
        {
            "title": "The model — exact identifiers",
            "body": """\
- Model: accounts/emanate/models/ptc-steel-v1 (Fireworks account: emanate) — historical, weights-only experiment
- Base: Qwen3.5-35B-A3B (qwen3p5-35b-a3b, MoE, 36B total / 3B active). The whole Qwen3.5 family later proved unable to serve function calls on the platform, which forced iteration 2's base change
- Config: LoRA rank 32, all-linear (12) target modules, learning rate 1e-5, 2 epochs; alpha/scaling not recorded (the record-alpha rule postdates v1)
- Training: $19.36, 1h50m, 922 steps; train loss 3.54 -> ~1.1, eval loss 1.13 (perplexity 3.10)""",
        },
        {
            "title": "What it was trained on",
            "body": """\
Corpus qa-train-v2: 14,732 records, counted by record count (the loss-bearing-token unit was introduced at iteration 3). The trained artifact is the amended QA set (13,568 train records) put through physical duplication — the weight field was stripped entirely; duplication IS the weighting mechanic for v1.

- ranking: 203 base records duplicated x7 to 1,421
- delta_conflict: 302 base records duplicated x2 to 604
- abstention_cutoff: 711 base records drop-half (seed 42) to 355
- everything else: 12,352 records x1""",
        },
        {
            "title": "How it was evaluated",
            "body": """\
8 automated quality gates, first live run July 10, 2026 (~$30.50). The results split cleanly in two — judgment passed, facts failed — and the gates caught every failure before any user saw the model:

- Grounded facts: 36.0% (needed >=80%) — FAIL
- Over-refusal (answers real accounts, incl. typos): 10% (needed >=98%) — FAIL
- Unsupported mentions (no invented account names): 37.9% (needed >=98%) — FAIL
- Ranking quality: 0/24 — later proven to be a broken test, not a broken model
- Fabricated-account refusal: 98.8% — PASS
- Post-cutoff refusal: 100% — PASS
- Delta arbitration (fresh context beats stale memory): 100% — PASS
- Positive-today (answers when fresh data IS provided): 100% — PASS""",
        },
        {
            "title": "What the forensic diagnosis found",
            "body": """\
A zero-cost forensic analysis of the artifacts confirmed the root causes:

- Training dose far too low: each fact appeared ~3 times; research indicates reliable recall needs tens of exposures. Recall of trained material (37.8%) matched paraphrase generalization (36.0%) — when these match, facts never entered the weights
- Importance-blind data generation: the biggest accounts got the fewest examples (the #1 customer, $68.6M, got 13 pairs; the model refused to recognize it by name)
- No entity anchoring, so facts bled across accounts: the model gave one account another account's revenue, matching to within 0.5%
- One "failed" gate was actually a broken test: ranking scored 0/24 because 81.6% of ranking training examples were open-ended and the eval graded against a batch the model never saw
- The refusal boundary was learned backwards: it refused one-character typos of real accounts and invented statistics for plausible fake companies
- The loss curve is not a quality signal for factual learning: cross-entropy falls as the model learns phrasing while the tokens encoding the actual numbers stay wrong""",
        },
        {
            "title": "How to test it",
            "body": """\
The model remains on Fireworks as accounts/emanate/models/ptc-steel-v1, listed as "historical (weights-only experiment)" in the iteration 3 test guide. Manual testing uses the same deploy/REPL/teardown flow as the later models (scripts/fw-deploy.py + scripts/ask-v2-agent.ts in the platform-alpha eval-tools worktree, FIREWORKS_API_KEY required). Always use the full model#deployment id and tear the endpoint down after — a dedicated window bills ~$0.23/GPU-minute. Its failure modes are already on record (36% grounded recall, backwards refusal boundary), so new testing effort is better spent on later iterations.""",
        },
    ],
    "ptc-steel-v2-agent": [
        {
            "title": "What iteration 2 is, in one paragraph",
            "body": """\
Iteration 2 was the combined run: factual QA plus tool-calling trajectories in one fine-tune, on a new base (Qwen3.6-27B dense — the whole Qwen3.5 family cannot serve function calls on the platform). It fixed iteration 1's measured dataset defects (24+ exposures per fact across 8 structurally different question forms, importance-weighted floors, entity anchoring, two-sided refusal training) and added 3,400 synthetic tool trajectories. Training completed July 11, 2026: 32,568 records, $45.16, 5h14m. The verdict split: NO-GO as a memory-only Q&A model (grounded recall regressed to 11.2% and the refusal gates collapsed) and STRONG PASS on the tool-calling agent path — near-perfect call mechanics, generalization to all 8 production tools it was never trained on, and the six real-world questions that failed v1 going 0/6 from memory to 5/6 with tools attached. Never adopted; its agent-path result set iteration 3's direction.""",
        },
        {
            "title": "The model — exact identifiers",
            "body": """\
- Model: accounts/emanate/models/ptc-steel-v2-agent (Fireworks account: emanate)
- Base: accounts/fireworks/models/qwen3p6-27b (dense 27.3B, function-calling capable, Apache-2.0, 262k context), chosen from a 30-model verified sweep. Never query the bare base name — it silently bills serverless
- Config: LoRA rank 32, alpha 32 / scaling 1.0, all-linear (12), learning rate 3e-6 cosine decay, 3 epochs, weight decay 0
- Training: 32,568 records, $45.16 (projected $32.79 — +38% from the multi-turn unroll effect), 5h14m; train loss 1.04 -> 0.46; eval loss 2.583 -> 2.560 -> 2.542, flat after epoch 1 (perplexity ~12.7)""",
        },
        {
            "title": "What it was trained on",
            "body": """\
Corpus combined-v2-agent-train: 32,568 records, counted by record count. Root-level record weights 3.0 x 23,289 / 2.0 x 460 / 1.0 x 8,596 / 0.5 x 223; tool-result turns masked at weight 0 so the model learns to read tool output, never to reproduce it.

QA side (qa-train-v3, 29,168 records):

- numeric facts 21,843
- comparison 1,170
- other/name-robustness 2,910
- account cards 1,015
- hard negatives 800
- refusal 170
- abstention-fabricated 686
- abstention-cutoff 223
- delta-conflict 288
- positive-today 63

Tool side (agent-traj-v1 3,000 + v1b 400):

- resolve+answer 1,200
- typo/alias 450
- unknown/ambiguous 450
- activity 450
- ranking-with-batch 450
- decision-boundary supplement 400

Every QA record was string-matched against its source dossier before training (numbers verbatim, canonical account name present, no probe leakage); 966 records were regenerated at the root, and exactly one real judge-confirmed error exposed corrupted source data (impossible year-5664 dates) that was scrubbed and flagged upstream.""",
        },
        {
            "title": "How it was evaluated",
            "body": """\
Five layers, built and self-tested before the model finished training (a perfect mock model passes all 745 gold scenarios; seeded faults are each caught by the metric that targets them). Total evaluation spend ~$6 against a <=$45 budget — grading ran on free subagent reviewers calibrated 93/100 against the paid judge before any comparison was published.

Memory-only gates, side-by-side with v1:

- Grounded facts: 36.0% -> 11.2% (>=80%) — FAIL, regressed
- Over-refusal: 10.0% -> 70% (>=98%) — FAIL, up 60 points but short
- Unsupported mentions: 37.9% -> 100% (n=5) — PASS
- Fabricated-account refusal: 98.8% -> 36.8% (>=95%) — FAIL, regressed
- Post-cutoff refusal: 100% -> 39.1% (>=95%) — FAIL, regressed
- Delta arbitration: 100% -> 100% — PASS
- Positive-today: 100% -> 90% (>=95%) — FAIL, narrow

Tool gates (new in v2): tool-name selection 99.8%, argument accuracy 99.5%, schema validity 100%, zero fabricated fields. Generalization verdict: GENERALIZES — 13/14 correct selections among the 8 production tools it was never trained on, 10/10 on trained tools, 6/6 correct no-call decisions. The six quarantined real-world probes: 0/6 from memory alone -> 5/6 with tools attached.""",
        },
        {
            "title": "Known open questions",
            "body": """\
- The refusal collapse and the recall regression share one mechanism: the two training objectives interfered, and the platform's multi-turn unroll silently doubled the tool slice's gradient share — the model became an agent that expects tools and, tested bare, fabricates instead of refusing. The next round had to rebalance the mixture, measured in loss-bearing tokens rather than record counts
- Base-vs-tuned attribution was never possible: the BF16 addon shape fails at replica init for this base, so the run went FP8 live-merge only — "what the base contributes" vs "what the fine-tune added" stayed unmeasured
- Part of the grounded regression is an eval-phrasing artifact; discounting it, grounded is ~15% — still far below both the bar and v1's 36%, so the conclusion held
- Whether the platform actually honors root-level record weights was never behaviorally proven; iteration 3 switched to physical duplication for the slice that mattered most""",
        },
        {
            "title": "How to test it",
            "body": """\
The model remains on Fireworks as accounts/emanate/models/ptc-steel-v2-agent — it is the baseline the iteration 3 test guide uses for comparisons, and the model the eval worktree's deploy script targets by default when no FW_ADDON_MODEL override is set.

- From the platform-alpha eval-tools worktree, with .env.local providing FIREWORKS_API_KEY: python3 scripts/fw-deploy.py deploy-merge (FP8 live-merge, ~$14/hr while up, ~6 min cold start)
- Chat via the REPL that executes the model's real tools against pilot account data: npx tsx scripts/ask-v2-agent.ts --repl --model "accounts/emanate/models/ptc-steel-v2-agent#accounts/emanate/deployments/<dep-id>"
- Tear down when done — the step that stops billing: python3 scripts/fw-deploy.py teardown --yes

Always use the full model#deployment id (the plain model name 404s on dedicated endpoints; the plain base name silently bills serverless); batch questions into one window (~$0.23/GPU-minute).""",
        },
    ],
    "ptc-steel-v3-agent": [
        {
            "title": "What iteration 3 is, in one paragraph",
            "body": """\
Iteration 3 is the production-targeted fine-tune of Qwen3.6-27B (LoRA, trained on Fireworks) into PTC Steel's account-intelligence agent. It does not try to make the model memorize every number — two experiments proved that fails at this scale. Instead it teaches a behavioral policy: resolve the account, call the right tool, copy exact values from tool results, decline gracefully when a field doesn't exist, refuse only after checking, ignore injected instructions, and apply per-org rules. Facts live in tools; judgment, procedure, and discipline live in the weights. The evaluated verdict (under the corrected grading of July 17): a strong agent that is not yet production-final — it passes the core agent behaviors decisively, with the remaining true gaps being refusal rehabilitation, positive-today handling, and whole-book triage. v3 became the program's champion baseline, the model every later run is judged against — while, like every model in this program, it was never adopted into the product.""",
        },
        {
            "title": "The model — exact identifiers",
            "body": """\
- Model: accounts/emanate/models/ptc-steel-v3-agent (Fireworks account: emanate)
- Base: accounts/fireworks/models/qwen3p6-27b (dense 27.3B, same as v2). Never query the bare base name — it silently bills serverless
- Config: LoRA rank 32, alpha 32 / scaling 1.0, all-linear (12), learning rate 3e-6, 2 epochs (v2's eval loss was flat after epoch 1), weight decay 0, W&B telemetry on
- Training: 35,778 records; ~$100 all-in (est. $50.73 sft-bucket, doubled by dual-bucket accounting, plus ~$40 sunk in a job saga); train loss 1.708 -> 0.140; eval loss 2.530 -> 2.505, falling""",
        },
        {
            "title": "What it was trained on",
            "body": """\
Corpus combined-train-v4d-agent: 35,778 records, measured in loss-bearing assistant tokens — the accounting unit introduced this iteration after unroll silently distorted v2's mixture. Raw split QA 60.0% / tool 40.0%, with tool share treated as a ceiling, never a target; the critical rehearsal slice was physically duplicated x2 instead of relying on the never-behaviorally-proven record-weight knob.

- Factual QA (v2 corpus): 27,908 records, 914,756 lb-tokens (46.9%) — domain knowledge, answer shape, judgment substrate
- Refusal/abstention rehearsal, duplicated x2: 2,873 records, 254,691 lb-tokens (13.1%) — repairing v2's collapsed refusal boundary
- Synthetic tool trajectories (from v2): 3,400 records, 322,230 lb-tokens — the 3-tool agent loop (resolve_account, get_account_activity, get_ranking_candidates)
- Seven new gap slices, each traced to a failure measured on the v2 model BEFORE generating any data (~$2 of probes twice avoided ~1,900 unnecessary examples): field-unavailability 430, resolve-before-refuse 250, vague/global grounding 200, injection adversarial 180, org watch-item policy 100, shared-token disambiguation 40, multi-turn wrappers 40
- Real production agent trajectories (new): 357 selected from a pool of 7,493, 330,474 lb-tokens (17.0%) — the autonomous per-account surface, capped so tool data stays <=40% of trained tokens""",
        },
        {
            "title": "How it was evaluated",
            "body": """\
All built and dry-run tested before training: 4 deterministic ship gates, 1,554 frozen held-out scenarios across 5 suites, and a full v2 side-by-side — the whole battery in one ~30-minute deployment window ($7.96, 34 min). A July 17 grading correction matters to every number: a blind re-audit of 601 graded answers found roughly 9 in 10 recorded "failures" on three tests were grader bugs, and everything was re-scored.

What passed:

- Resolve-before-refuse: 100% (v2: 80%)
- Tool selection / arguments / schema validity: 100 / 99.7 / 100%
- Grounded-claim precision: 86% (v2: 72%)
- Delta-conflict obedience: 100%
- All 5 frozen generalization suites (unseen-schema, overlap, injection, org-policy, multi-turn): PASS
- Field-unavailable abstention: 98% after the grading correction (originally scored 77% — mostly grader artifact)

What stayed below the bar (the real gaps):

- Refusal rehabilitation, post-cutoff class: 72.5% vs >=95
- positive_today: 83.3% vs >=95
- Whole-book triage: 60% vs >=90 (never trained as a class; exposed by a new judgment gate)""",
        },
        {
            "title": "Known open questions",
            "body": """\
- The per-account agent surface was effectively untested at gate level: the emit_output gate scored 0% on a broken fixture (a synthetic prompt instead of the real ~5KB serving prompt)
- Long-conversation stress with tools was untested — the multi-turn suite covers 2-6 turns
- Serving-quality equivalence (FP8 vs BF16) was blocked on the vendor's BF16 shape answer
- Injection safety passed at the model layer, but research shows training defenses alone are beaten by adaptive attacks — the binding defense is a runtime permission layer that was still queued product work
- The remaining behavior gaps (refusal rehab, positive_today, triage) each traced to a measurable training-data shortage, not a design flaw — they became iteration 4's targets""",
        },
        {
            "title": "How to test it",
            "body": """\
From the platform-alpha eval-tools worktree (branch feat/qa-eval-tools), with .env.local providing FIREWORKS_API_KEY (request it directly from the project owner — never share keys in chat):

- export FW_ADDON_MODEL="accounts/emanate/models/ptc-steel-v3-agent" and FW_DEP_ID="v3agent-manual", then python3 scripts/fw-deploy.py deploy-merge (FP8 live-merge, ~$14/hr while up, ~6 min cold start; without the env vars the script deploys v2)
- Chat via the REPL that executes the model's real tools against pilot account data: npx tsx scripts/ask-v2-agent.ts --repl --model "accounts/emanate/models/ptc-steel-v3-agent#accounts/emanate/deployments/v3agent-manual"
- Tear down when done — the step that stops billing: python3 scripts/fw-deploy.py teardown --yes

Cost rules: ~$0.23/GPU-minute (a 20-minute session is about $5); always use the full model#deployment id — the plain model name 404s on dedicated endpoints and the plain base name silently bills serverless. The known-broken list (post-cutoff refusals, positive_today, triage) is on record — the valuable findings are failures NOT already on that list.""",
        },
    ],
    "ptc-steel-v4-agent": [
        {
            "title": "What iteration 4 is, in one paragraph",
            "body": """\
Iteration 4 did not ship — the automated quality gates caught that it was worse than the champion (v3), which stayed in place. It was built to fix three specific gaps v3 left (declining metrics the tools don't track, refusing questions past the data cutoff, and whole-book triage), and it fixed those in isolation — but the fixes' blunt answer-style bled into unrelated behaviors and collapsed the model's handling of vague conversational questions from 90% to 50% (confirmed real under the July 17 corrected grading; the NO-GO verdict stands). The failure was diagnosed precisely and cheaply: a mixture-shape problem, not a "needs more examples" problem, amplified by a second training epoch that demonstrably overfit — and the recurring root cause across the program is that no corpus, v1 through v4, ever contained ordinary conversational practice to counterbalance a loud single-note skill.""",
        },
        {
            "title": "The model — exact identifiers",
            "body": """\
- Model: ptc-steel-v4-agent (per the program's mixture-history ledger; the iteration 4 brief did not publish a full serving path for it)
- Base: qwen3p6-27b (dense 27.3B, same as v2/v3)
- Config: LoRA rank 32, alpha 32 / scaling 1.0, all-linear (12), learning rate 3e-6, 2 epochs, weight decay 0
- Training: 19,807 records; ~$123.26 all-in (est. $61.63 — the projection under-predicted 1.3x); train loss 1.200 -> 0.257; eval loss 2.740 -> 2.832 RISING — the overfit signal""",
        },
        {
            "title": "What it was trained on",
            "body": """\
Corpus combined-train-v5-agent: 19,807 records, measured in loss-bearing / effective-lb tokens (integer-duplication dosing). The single largest mixture fact in the whole program: v4 removed ALL 27,908 closed-book value-QA records (914,756 lb-tokens, 46.9% of v3's mass) and replaced them with a 3,000-record tool-form recognition anchor that emits no metric value — the only iteration where raw QA share drops below tool share (38.8% QA-rehearsal / 61.2% tool).

- delta_conflict (x4): 1,148 records, 131,192 lb-tokens
- positive_today (x2): 126 records, 14,186 lb-tokens
- A1 post_cutoff (x2): 2,240 records, 132,204 lb-tokens
- A2 cite-the-count (x2): 2,560 records, 130,664 lb-tokens
- A4 fabricated (x2): 2,490 records, 131,294 lb-tokens
- A5 whole-book triage (x2, a new judgment class): 2,240 records, 134,882 lb-tokens
- tool_v1 / v1b: 3,400 records, 322,230 lb-tokens
- tool-form recognition anchor (new): 3,000 records, ~267,062 lb-tokens
- field-unavailability rebuild: 1,236 records, 147,935 lb-tokens
- real per-account trajectories (carried from v3): 357 records""",
        },
        {
            "title": "How it was evaluated",
            "body": """\
Side-by-side against v3, the champion, under the corrected grading of July 17:

- Vague/open questions ("how are we doing?"): 90% -> 50% — collapsed; the launch tripwire fired
- Whole-book triage: 60% -> 46.7% — regressed
- Decline unavailable fields: 98% -> 89% — regressed (real, but half the size originally reported)
- Post-cutoff refusals: 77% -> 67% — regressed
- Correct refusals (fake accounts + post-cutoff): 72.5% -> 80% — improved, still below the 95% bar
- Tool mechanics (name / args / schema): ~100% -> 99.7%+ — held
- Delta arbitration (trust fresh data over memory): 77% -> 87% — improved
- Speed / cost vs a frontier model: -91% latency / -99% cost — confirmed

Verdict: NO-GO — and that's the system working. Pre-committed gates stopped a worse model before it reached anyone, v3 stayed the champion, and ~$130 of spend bought a definitive diagnosis rather than a mystery.""",
        },
        {
            "title": "Known open questions",
            "body": """\
- The root cause, confirmed with hard evidence: a mixture-shape problem (one answer-style dominated the mix — adding more examples was not the issue), a second training pass that literally overfit (sharper on training data while worse on held-out data during epoch 2), and zero conversational practice in all four corpora to date — nothing counterbalanced a loud one-note skill
- Two slices dosed at the >=130K effective-lb-token target REGRESSED anyway — the dose rule derived from v3's clean curve was falsified; dose targets do not transfer across corpus shapes and must be re-measured per corpus
- The v3 -> v4 transition changed at least 7 variables at once, making the outcome un-attributable to any single change; the successor plan's guiding rule became "change as few variables as possible"
- What v4's diagnosis fed forward into the next design: nearly half the corpus becomes conversational replay, answer style becomes conditional on evidence rather than a blanket persona, and training drops to one epoch""",
        },
    ],
}


# Narrative reports for the EXTRA_MODELS — same section shape as REPORTS.
# Statuses are stated plainly: none of these serves a customer.
EXTRA_REPORTS = {
    "ptc-steel-v5-agent": [
        {
            "title": "What iteration 5 is, in one paragraph",
            "body": """\
Iteration 5 was the challenger to v3, the champion — the first corpus built to the V5-ARCHITECTURE recipe (replay-heavy, envelope-conditioned, one epoch because v4 proved epoch 2 overfits). It passed every backward-looking safeguard: extended verifier 0 flags, contradiction linter 0 unmarked, reserved-diff clean, render-verify 0, style caps all inside bounds. And it still failed in a new way: E21 tool avoidance — the model answered from memory on 41% of must-call scenarios (293 of 745, where v3 had 0) and produced $1M placeholder figures traceable to zero corpus records. Verdict: NO-GO, never adopted; v3 stayed the champion. Its lesson reshaped the program's accounting unit again: what binds is the turn-1 first-action per record conditioned on the served envelope — not loss-bearing tokens.""",
        },
        {
            "title": "The model — exact identifiers",
            "body": """\
- Model: accounts/emanate/models/ptc-steel-v5-agent (Fireworks account: emanate)
- SFT job: erp9alb9; dataset accounts/emanate/datasets/combined-train-v6-agent (naming decoder: datasets run one version ahead of models — the v6-named corpus trains model v5)
- Base: qwen3p6-27b (dense 27.3B, same as v2-v4)
- Config: LoRA rank 32, alpha 32, all-linear (12), learning rate 3e-6, 1 epoch (deliberate — v4's epoch-2 overfit), weight decay 0
- Training: 16,185 records / ~2.0M loss-bearing tokens, $81.68; Fireworks estimatedTokenCount 11,358,900; immutable corpus manifest at finetune-out/v6-manifest.json""",
        },
        {
            "title": "What it was trained on",
            "body": """\
Corpus combined-train-v6-agent, mixture by loss-bearing tokens (V5-ARCHITECTURE §3, all four slices in-band):

- v3-on-policy replay 33%: full tool-to-grounded-answer trajectories from one v3 serving window ($6.30), EV-rubric curated (95% usable), deduplicated to 2,312 unique inputs, probe-guarded
- untuned-base broad replay 12%: general retention slice from the untuned base ($2.19)
- real tool trajectories 35%: B4 3-tool + Slice-4 8-tool per-account trajectories in the native envelope
- specialist counterfactuals 20%: 4,202 <state>-flip pairs, each citing a measured v3 failure; the 6 eval held-out fields excluded

Every replay and specialist record carried the full production envelope plus a delimited <state> (Ground/Scope/Decline) block. Preflights all green before launch: extended verifier, <state>-aware contradiction linter, reserved-diff (V4_EVAL_RESERVED + H4 + held-out fields + real-bank + vault), render-verify, target-audit.""",
        },
        {
            "title": "How it was evaluated, and what the failure taught",
            "body": """\
Paired battery against v3 (the champion) built to the discordance-pilot power plan: whole-book triage, delta arbitration, subset ranking and field-unavailable each scaled to 1,200 parents, plus ~70 vague-intent probes — all contamination-diffed clean against the training corpus.

The finding that decided the verdict: E21 tool avoidance. The model answered from memory on 41% of must-call scenarios (v3: 0) and emitted placeholder dollar figures that trace to zero corpus records. The mixture passed every dose target, which is exactly the lesson — dose targets and lb-token shares are backward-looking; the unit that binds behavior is the turn-1 first-action distribution conditioned on the served envelope. That lesson (L46-L56 minted alongside) is a direct input to the Grand Steel corpus design, which carries must-call twins and prints a must-call census before any launch.""",
        },
    ],
    "ptc-steel-a1-v1": [
        {
            "title": "What A1-V1 is, in one paragraph",
            "body": """\
A1-V1 is a different lane from the v1-v5 program: a LoRA fine-tune of InternScience/Agents-A1 (35B-A3B, hybrid GDN linear-attention MoE) aimed at the nightly per-account pipeline — the Trigger.dev cron that reviews every enabled PTC Steel account each weeknight and produces the Accounts-page cards and review-mode email drafts. That job is done today by claude-sonnet-5 at a measured ~$163/night; A1-V1's pitch is the same job at ~$4-6/night on a self-hosted nightly GPU window. Training completed July 22, 2026 (rc=0, eval loss 0.7811 to 0.4587, monotonic). The eval verdict was NO-GO as an active arm (18 PASS / 6 FAIL), so it entered the nightly pipeline as a SHADOW arm only: it runs the same accounts with the same tools, its side-effecting calls are structurally unreachable, and nothing it writes reaches a customer. Send authority stays with Claude until the V-036 bar — three clean live shadow nights including a weekend — and promotion is mechanically impossible without a sign-off token only Daniel can commit.""",
        },
        {
            "title": "The model — exact identifiers",
            "body": """\
- Model: ptc-steel-a1-v1 — a 7.55GB LoRA adapter (sha 1cceae5c…), self-hosted; no managed-platform serving path exists (Fireworks never recognized the qwen3_5_moe architecture — 37h stuck UPLOADING; Together lacks the base entirely)
- Base: InternScience/Agents-A1, pinned commit addff08 (35B total / 3B active, hybrid GDN linear attention + MoE)
- Config: LoRA rank 32, alpha 32; targets = 310 modules + 80 fused expert params via lora_target_parameters — the transformers 5.14.1 GDN projection split (in_proj_qkv/z/a/b) means the documented axolotl names would have silently missed all 30 linear-attention layers
- Training: self-run axolotl on RunPod, pod 7 (cuq3wp21hsgjcb), 2x H200 141GB, 489/489 steps in 73 minutes; ~$53 for the training run (about half of it debugging: 80GB H100s cannot train this corpus — Triton backward OOMs on the long-record tail)
- Eval-loss ladder: 0.7811 pre-train falling monotonically to 0.4587 final (perplexity 2.184 to 1.582); the overfit tripwire never fired""",
        },
        {
            "title": "What it was trained on",
            "body": """\
3,911 train / 75 eval records (deterministic split, seed 42), rendered against the real serving envelope with a maximum rendered length of 9,346 tokens vs a 16,384 sequence length. The corpus includes 1,633 A1-self ballast records (459K loss-bearing tokens, 34.7% share, mid-band) passed through the GAP-11 acceptance pipeline — the counterweight the v4 post-mortem said every corpus before it lacked. The full per-slice table lives in the a1-v1 RENDER-PREP evidence.""",
        },
        {
            "title": "Status — shadow arm, promotion interlocked",
            "body": """\
The 07-22 eval window ended NO-GO as an active arm: 18 PASS / 6 FAIL — pushback-hold was never trained in (G10 0.00), injection handling and tool-channel adoption regressed below base, and a fabricate-to-comply signature appeared (invented values with fabricated "re-checked the CRM" provenance). So the deployment design assumes the model is not trusted:

- Shadow arm only: same accounts, same envelope, same tools; side-effecting calls (send / CRM write) are structurally unreachable — a separate execution path, not an if-statement
- Promotion interlock: active mode hard-requires a sign-off token committed only when V-036 is met (3 clean live shadow nights incl. >=1 weekend); missing token = silent degrade to shadow + alert
- Nightly serve lifecycle: rent 1x H200 from a persistent network volume at 22:30 PT, health-poll, serve, teardown with GET-404 verification; budget guard skips the shadow arm above $15/night with Claude unaffected
- Nightly riders print fabrication counts for both arms plus a provenance tracer for any "re-checked" claim, feeding the V-036 clean-night counter""",
        },
    ],
    "intv1-corpus-v1": [
        {
            "title": "What INT-V1 is, in one paragraph",
            "body": """\
INT-V1 is the Intelligence-product fine-tune: Qwen3.6-35B-A3B trained on Together AI to answer the Intelligence chat surface's workload. It trained clean and cheap (job ft-b32c0e5a-8d16, one epoch, 2,936 steps, $24.23 — matching the naive projection exactly, with billed tokens byte-matching the pinned local render), and an independent audit Daniel requested recomputed every headline from raw artifacts and found no fabrication and no arithmetic errors. Its verdict of record: pilot-YES behind three guardrails, launch-NO until the K3 re-check and a second held-out labeler. It has never answered a user: the Intelligence chat is always answered by gpt-5.6, byte-identically, whether or not a shadow capture happens alongside it.""",
        },
        {
            "title": "The model — exact identifiers",
            "body": """\
- Output model: daniel_7cf9/Qwen3.6-35B-A3B-intv1-corpus-v1-6f460574 (Together AI)
- Job: ft-b32c0e5a-8d16, completed 2026-07-22 00:40 PT, 2,936/2,936 steps, 1 epoch; billed tokens 16,151,518 — byte-match against the pinned local render (the LAUNCH-PACKET §8.3 gate)
- Adapter: sha 623a3933…, LoRA rank 32 / alpha 32, targets q,k,v,o_proj
- Precision note: train-time base is bnb-4bit QLoRA (forced by Together); serving is FP8 — the only certified deploy profile for this base is FP8 2x H100 TP2 at $10.98/hr
- Serving: dedicated endpoint only (serverless custom returns HTTP 400 model_not_available). The full lifecycle is live-proven: endpoint intv1-tuned-0722 cold-started in 5.3 minutes, served the complete 1,342-probe battery, and was torn down verified-gone for $0.70. No endpoint exists now — Together v2 has no idle auto-stop, and an unattended endpoint would bill ~$264/day""",
        },
        {
            "title": "How it was evaluated",
            "body": """\
Full battery: 1,342 probes across 8 instruments in a single $0.70 window; all 162 automated fails hand-read by two reviewers (48 corrected, all upward).

- Do-not-regress F1-F6: 0/6 trips
- Gate-1 invariants composite: 100% (36/36); tool-fault recovery, pushback recovery/hold/injection all 100%
- Deficit lifts (base to tuned): multi-turn re-call 4.2% -> 66.7% (3.3pp under the 70 target); judgment stop-and-answer PASS; no-action 0% -> 52.5% (the 0% was a scorer-regex artifact); fluent fabrication 0% on arm 1 (<=10 target)
- Standing fails, real and characterized: verbatim-production-prompt arm shows round-number template fabrications and suppressed no-action; leakage of corpus mask sentinels in dial-OFF reasoning needs a serve-time output filter; the tools-absent arm is broken as pre-registered (product gate: never serve the prompt without tools)

Verdict of record: pilot-YES behind three guardrails (canonical envelope only / output filter / tools always on, dedicated scale-to-zero) — launch-NO until the 07-27 K3 re-check plus a second held-out labeler.""",
        },
    ],
    # ptc-steel-v7-agent (model-factory-production-catchup T3, 2026-08-14).
    # Source: fine-tuning/models/ptc-steel-v7-agent/MODEL-CARD.md, read live
    # at execution time (its own header says "evaluation pending as of
    # 2026-08-07" but the body documents real eval work through 2026-08-11 —
    # the header line is itself stale; the body is the current source of
    # truth and is what this report follows).
    "ptc-steel-v7-agent": [
        {
            "title": "What v7 is, in one paragraph",
            "body": """\
v7 is the output model of the ptc-rescope-2569 corpus lineage: eight candidate builds in ~30 hours (2026-08-05 to 2026-08-06), each fixing a real defect the previous build's rigor banner missed. It is a rank-32 LoRA on qwen3p6-27b, same frozen recipe as v5 (LR 3e-6, 1 epoch, wd 0), trained on 2,569 records across five families (real_trajectory 1,275 / twins 250 / stale_recall 300 / action_cf 314 / replay 430). Like every model in this program it has never served a customer, and training completing is a mechanical fact, not a behavior signal.""",
        },
        {
            "title": "The model — exact identifiers",
            "body": """\
- Model: accounts/emanate/models/ptc-steel-v7-agent (Fireworks account: emanate), HF_PEFT_ADDON, READY
- Job: accounts/emanate/supervisedFineTuningJobs/wmvseysm (canary accounts/emanate/supervisedFineTuningJobs/pzynw9c5, 257 records stratified 10% sample, $4.87)
- Base: qwen3p6-27b (dense 27.3B, Apache-2.0 — confirmed dense, not MoE, live via the Fireworks API; same base as v2-v5-agent and grand-steel-v1-agent)
- Config: LoRA r=32/alpha=32 (Fireworks forces alpha=rank), scaling 1.0, all-linear (12 projection types incl. the GDN-style split in_proj_{a,b,qkv,z}), learning rate 3e-6, 1 epoch, weight decay 0
- Training: 2,569 records, corpus sha 551b8ee0ed12742db3b352234afa22cb5de929c5135a1582651750f03ad7ade6; wall-clock 49 minutes (2026-08-06T21:01:26Z -> 21:50:27Z)
- Catalog row: this row did not exist before this PR — the factory's own $85/$100 hard-coded controls were unreconciled against this cost class at launch time, so the manual-REST launch never minted one until now
- No factory_runs row exists for this model (launched via the manual REST path, not the factory CLI) — source_run_id is intentionally null, not fabricated""",
        },
        {
            "title": "What it was trained on",
            "body": """\
Corpus ptc-rescope-2569-v7.jsonl, 2,569 records, unchanged family split since the rescope's first build (v1): real_trajectory 1,275 / twins 250 / stale_recall 300 / action_cf 314 / replay 430. Three real defects were found and fixed across the eight-build lineage:

- R1 (found v3, fixed v4): a date-arithmetic self-contradiction — 679/679 checked records disagreed between a real per-account order-cadence line and a tool result rendered off a stale single-day snapshot.
- R-leak (found and fixed same-day, v4 to v5): read_account_history leaked future-dated reviews into 100% of the 1,663 records calling that tool (13,441 leaked entries removed).
- Weight-1 fabrication in replay (found and fixed in v7, the most serious): 16/430 replay records (6.25% of the 240 v3-teacher records) had the gold assistant turn itself invent an unsupported name, company, or dollar figure with zero account data or tools present. Fixed at the source in replay_window.py's keep() filter.

Rigor suite for v7: 13/13 applicable checks PASS. Two known, confirmed-unfixable limitations remain: zero outbound-tool (search_web / match_inventory / research_buying_committee) coverage anywhere in the corpus (a render-path and data-absence limitation, not a selection bug), and a query_account_orders coverage gap specific to the real_trajectory family (0/1,275, though corpus-wide coverage is 315/2,569 via other families).""",
        },
        {
            "title": "How it was evaluated",
            "body": """\
A behavioral eval re-run 2026-08-10 to 11 with a fixed, verified-uncontaminated harness (BLIND_TEMPORAL_ORDERING, $3.53) confirmed the eval instrument itself is now trustworthy — but surfaced a SCOPE CEILING, confirmed independent of any funding block: v7's training corpus contains exactly 2 output classes (no_action, winback_enrollment) against the production champion's real ~10-class decision vocabulary. Checked against real recent data: 37 of 150 (24.7%) of a live cohort's actual champion decisions, and 736 of 2,219 (33.2%) across the full account population, are output types v7 cannot structurally produce at all, independent of behavior quality.

Decision-agreement itself remains genuinely UNINFORMATIVE: a single random 150-account nightly cohort produced only 7 non-trivial in-scope decision points (n<30, this program's own reporting floor) — reaching n>=30 needs roughly 5 nights of cohorts aggregated, not a bigger single pull. Emit-schema self-correction is not 100% reliable on fresh data (3/143, 2.1%, never resolved within the retry budget). No ship recommendation is made in either direction by this card; the S8 full eval window (adding v7 as a new ab_shadow_sync.py arm) is designed but not yet scheduled, pending Daniel's explicit go-ahead separate from the training spend already approved and spent.""",
        },
    ],
    # grand-steel-v1-agent (model-factory-production-catchup T4, 2026-08-14 —
    # REFRESH of a stale row, not a new model). Source:
    # fine-tuning/models/grand-steel-v1-agent/MODEL-CARD.md (identity/corpus/
    # cost, unchanged since 2026-07-31) plus
    # runs/grand-steel/swarm/cloud/progression/DIAGNOSTIC-RESULTS.md
    # (2026-08-06) and REMEDIATION-VERIFICATION.md (2026-08-08), which
    # resolved the card's own previously-OPEN G11 item (the missing
    # tuned-vs-TRUE-BASE comparison) and reached this program's
    # FINALIZED_NO_TRAIN verdict — the row this PR replaces was written the
    # day training completed, before either diagnostic wave ran.
    "grand-steel-v1-agent": [
        {
            "title": "What this model is, in one paragraph",
            "body": """\
Grand Steel's first fine-tune, and the first model the fine-tune factory produced end-to-end under its own pipeline (run 523ecbbf: census -> governance -> export -> corpus build -> verification battery -> cost projection -> human launch gate -> training). It is a rank-32 LoRA on Qwen3.6-27B trained for one epoch on 578 records (77,394 loss-bearing tokens) mixing real GS tool trajectories (53%), specialist counterfactuals borrowed from PTC shapes and explicitly marked unvalidated-for-GS (32%), and untuned-base replay for retention (15%). Training completed 2026-07-30 in 25 minutes for ~$18.15 all-in.""",
        },
        {
            "title": "The model — exact identifiers",
            "body": """\
- Model: accounts/emanate/models/grand-steel-v1-agent (Fireworks account: emanate), state READY, HF_PEFT_ADDON
- SFT job: accounts/emanate/supervisedFineTuningJobs/a7jm4e4b — created 2026-07-30T20:55:11Z, completed 21:20:12Z (25 min)
- Dataset: accounts/emanate/datasets/combined-train-grand-steel-v1 (578 examples, platform estimatedTokenCount 3,069,300)
- Config: LoRA rank 32, alpha 32 / scaling 1.0, learning rate 3e-6, 1 epoch, weight decay 0, warmup 10 steps, all-linear targets
- Cost: platform estimatedCost $9.07; all-in $18.15 (L34 2x dual-bucket rule)
- Immutable manifest: platform-alpha/finetune-out/gs-run/signed-manifest.json""",
        },
        {
            "title": "What it was trained on",
            "body": """\
Corpus combined-train-grand-steel-v1, measured in loss-bearing tokens, mixture 53.24/31.87/14.89 against 50/35/15 +/-5pp targets:
- 158 real GS tool trajectories (from 238 reader-KEEP candidates)
- 375 specialist counterfactuals: PTC v6 Ground/Scope/Decline shapes adapted to GS data, every record flagged unvalidated_for_org:true, 91 must-call twins holding the E21 census at 1.08
- 45 untuned-base replay completions (neutral system prompt, teacher = untuned base)
- 65 held-out accounts fully disjoint (L33); 308 eval-reserved 1b records never assembled (L39)""",
        },
        {
            "title": "Evaluation — concluded: FINALIZED_NO_TRAIN",
            "body": """\
The pre-S8 RunPod battery (2026-07-31) completed 772/772 with zero inference errors: G1 must-call PASSES CLEAN at 0/57 memory-answers; G2 staged-overcalling FAILS CLEAN at 54.2% (bar <=25%); G6 verdict-terminal-emit FAILS CLEAN at 2.5% (bar >=85%). G11 (tuned-vs-TRUE-BASE disambiguation) was OPEN at that point — a harness bug had aborted the base arm before inference.

A paid diagnostic wave (2026-08-06, DIAGNOSTIC-RESULTS.md) closed G11 with a real 772-scenario true-base battery: of the four failing gates (G2, G6, G7, G8), all four show tuned and base failing by the same mechanism on the same or near-identical scenarios (G2 Jaccard overlap 0.936; G6's exact same 2/80 scenario IDs terminate correctly in both, with near-byte-identical reasoning text) — runtime/harness-shared, not weight-attributable. G10 (decline collapse) is the one gate that separates, and it separates in tuned's favor. A follow-up harness-remediation verification wave (2026-08-08, REMEDIATION-VERIFICATION.md) tested three proposed fixes and found the evidence for runtime-shared attribution grew stronger, not weaker (G2 Jaccard rose to 0.974; G6 to 1.000 exactly), while G2's aggregate rate got worse under the new tool availability, not better.

Verdict of record: FINALIZED_NO_TRAIN — no product-relevant residual is attributable to the trained weights; retraining will not fix these gates. This does not reverse the original battery's G1 pass or G10's favorable result, and it does not imply Grand Steel should never be retrained for a different reason — grand-steel-v2-agent was trained afterward on a distinct, Daniel-authorized basis (a new corpus cell targeting a judgment v1 never trained for), not a reopening of this verdict.""",
        },
    ],
    # grand-steel-v2-agent (model-factory-production-catchup T2, 2026-08-14).
    # Source: fine-tuning/models/grand-steel-v2-agent/MODEL-CARD.md.
    "grand-steel-v2-agent": [
        {
            "title": "What this model is, in one paragraph",
            "body": """\
Grand Steel's second fine-tune: the same frozen recipe as v1 (LoRA r=32/alpha=32, 1 epoch, LR 3e-6, wd 0 — Fireworks forces alpha=rank), trained on a corpus that adds one new family (fam_staged_sufficient_twin, 80 records / 40 exact must-call/staged-sufficient pairs) to v1's unchanged 1a/1b/1c slices. Training was Daniel-authorized specifically because the new corpus cell was ready, not as a reopening of grand-steel-v1-agent's own FINALIZED_NO_TRAIN diagnostic verdict, which stands separately and unreversed.""",
        },
        {
            "title": "The model — exact identifiers",
            "body": """\
- Model: accounts/emanate/models/grand-steel-v2-agent (Fireworks account: emanate), HF_PEFT_ADDON, verified READY
- Full job: accounts/emanate/supervisedFineTuningJobs/ozhgeptn — created 2026-08-11T22:42:52Z, completed 2026-08-12T00:05:52Z (~83 min wall-clock, ~8 min actual training)
- Canary job: accounts/emanate/supervisedFineTuningJobs/lskfn38u (65 records, stratified ~10% sample, seed 42), COMPLETED, no divergence/NaN
- Train dataset: accounts/emanate/datasets/gs-v2-candidate-v1 (658 records, real Fireworks estimatedTokenCount 3,748,600)
- Base: qwen3p6-27b (dense 27.3B, Apache-2.0 — same base as grand-steel-v1-agent and ptc-steel-v2-agent...v7-agent)
- Cost: canary $1.1246 + training-only full job $11.2185 = $12.3431 total training-wave spend (real Fireworks numbers, not the ~$22.6 pre-launch estimate, which also included a $15 eval reserve that had not yet been spent)""",
        },
        {
            "title": "What it was trained on",
            "body": """\
Corpus gs-v2-candidate-v1.jsonl, 658 records / 86,279 loss-bearing tokens:
- 1a-real-trajectories, FIXED: 158 records, carried forward from v1's identical 158 identities, re-rendered with two real defects fixed before any training spend (a date-arithmetic self-contradiction and a future-information leak affecting up to 209 records, and a schema-drift defect where importance_score was still being emitted 238/658 times after production removed the field 2026-07-17)
- 1b-specialist (8 sub-families): 375 records, unchanged, byte-for-byte copy of v1's trained rows
- 1c-base-broad (replay): 45 records, unchanged, byte-for-byte copy of v1's trained rows
- 1d-staged-sufficient (fam_staged_sufficient_twin, NEW): 80 records (40 exact must-call/staged-sufficient pairs), grounded entirely in real GS account data

Mixture (lb-token basis, post-fix): real 46.66% / specialist 28.59% / replay 13.36% / staged-sufficient 11.4% — renormalizing the first three reproduces v1's own realized split almost exactly. Rigor check: 11/12 attacks PASS; the one residual (4/238 emit_output.reasoning strings mentioning a tool name in verbatim historical reviewer prose) is disclosed, not fixed, per this program's own copy-from-tool-verbatim invariant. No loss curve was published for this job — none is claimed here.""",
        },
        {
            "title": "Evaluation",
            "body": """\
Run 2026-08-12, reusing v1's own proven diagnostic harness (772-scenario frame) end to end for a real base-vs-v1-vs-v2 comparison, same session, same corrected grader:
- G1 must-call (n=57): 0/57 all three arms — unregressed
- G2 staged-overcalling (n=251): base 59.36%, v1 58.17%, v2 54.58% (bar <=25%, all FAIL) — v2 moved 3.6pp favorably, but Wilson 95% intervals heavily overlap across all three arms; not statistically separated
- G6 verdict-terminal-emit (n=80): 1.25% all three arms (bar >=85%, all FAIL) — a clean null; the exact same single scenario ID emits correctly in all three arms

Real result: a clean null on G6, and a real-but-statistically-unproven small favorable nudge on G2. Neither gate cleared its bar. This does not reopen grand-steel-v1-agent's FINALIZED_NO_TRAIN verdict and is not a case for shipping v2.""",
        },
    ],
}


# ---------------------------------------------------------------------------
# EXTRA_MODELS — fully-specified rows for the six artifacts outside the
# 4-iteration PTC zip (v5, A1-V1, INT-V1, ptc-steel-v7-agent,
# grand-steel-v1-agent, grand-steel-v2-agent). source_run_id: v5's
# factory_runs row was backfilled 2026-07-30 by
# runs/ptc-steel/backfill_v5_run.py (MF-PTC-RETRAIN-EXEC, same uuid5
# namespace) so its link is real; grand-steel-v1-agent has a REAL live
# factory_runs row (523ecbbf, the first S7 the factory ever executed) linked
# directly, not via the backfilled uuid5 scheme. A1-V1, INT-V1,
# ptc-steel-v7-agent, and grand-steel-v2-agent are different lanes / manual
# launches entirely and have no run rows — source_run_id stays null, per the
# same nullable-FK honesty convention as A1-V1/INT-V1 below (never a
# fabricated or backfilled run id invented to satisfy the foreign key).
# Every number cites its source file.
#
# Consolidation note (model-factory-production-catchup T1, 2026-08-14): this
# is now the single canonical writer for all 10 real trained models,
# including Grand Steel. It folds in what were two separate one-off scripts
# — runs/grand-steel/gs_corpus/record_model.py (grand-steel-v1-agent, real
# but stale) and runs/ptc-steel/corpus/record_model.py (a stray, never-run
# byte-for-byte duplicate of the Grand Steel script that still referenced
# grand-steel-v1-agent's constants under a PTC Steel path) — both deleted by
# this consolidation. Every model-completion event, regardless of stream,
# should add its row here rather than writing a new one-off script.
# ---------------------------------------------------------------------------
EXTRA_MODELS = [
    {
        # 07-RUN-LEDGER.md v5 row + DATASETS.md combined-train-v6-agent row.
        "org_slug": ORG_SLUG,
        "org_name": ORG_NAME,
        "name": "ptc-steel-v5-agent",
        # Backfilled run row (runs/ptc-steel/backfill_v5_run.py, 2026-07-30) —
        # same deterministic uuid5 the run backfill mints, so the two scripts
        # can never drift into referencing different run ids.
        "source_run_id": _run_id("ptc-steel-v5-agent"),
        "iteration": 5,
        "ship_status": "no_go",
        "headline_verdict": (
            "NO-GO: E21 tool avoidance — answered from memory on 41% of must-call scenarios "
            "(293/745 vs v3's 0), $1M placeholders from ZERO corpus records — Envelope-correlation "
            "collapse: the binding unit is the turn-1 first-action per record CONDITIONED ON THE "
            "SERVED ENVELOPE, not lb-tokens. Passing every backward-looking safeguard != safe."
        ),
        "training_provider": "fireworks",
        "provider_job_id": f"{FIREWORKS_SFT_JOBS}/erp9alb9",
        "provider_model_path": "accounts/emanate/models/ptc-steel-v5-agent",
        "infra": {
            "platform": "Fireworks supervised fine-tuning (serverless — no dedicated GPU rental)",
            "dataset": "accounts/emanate/datasets/combined-train-v6-agent (+ qa-eval-v6 eval split, 273)",
            "fireworks_estimated_token_count": "11,358,900",
            "immutable_manifest": "finetune-out/v6-manifest.json (corpus SHA a40d6b07…bee75cd)",
            "epochs_note": "1 epoch deliberately — v4 proved epoch 2 overfits on this family",
        },
        # No per-epoch eval-loss series was recorded in the ledger for the
        # 1-epoch run; provider_enrich.py can fetch the real curve from the
        # Fireworks job API rather than this script guessing.
        "loss_curve": [],
        "base_model": "qwen3p6-27b (same as v2-v4)",
        "lora_rank": 32,
        "lora_alpha": "alpha32 / scaling 1.0",
        "learning_rate": "3e-6",
        "epochs": 1,
        "weight_decay": "0",
        "target_modules": "all-linear (12)",
        "train_records": 16185,
        "training_cost_usd": 81.68,
        "train_eval_loss": None,
        "mixture": {
            "corpus": "combined-train-v6-agent (16,185 records, ~2.0M lb-tokens; V5-ARCHITECTURE §3)",
            "unit": "loss-bearing tokens (lb-tokens)",
            "breakdown": [
                {"group": "v3-on-policy replay (EV-rubric curated, 2,312 unique inputs)", "lb_pct": 33},
                {"group": "untuned-base broad replay (general retention)", "lb_pct": 12},
                {"group": "real tool trajectories (B4 3-tool + Slice-4 8-tool, native envelope)", "lb_pct": 35},
                {"group": "specialist counterfactuals (4,202 <state>-flip pairs, each citing a measured v3 failure)", "lb_pct": 20},
            ],
            "envelope": "every replay+specialist record carries the full production envelope + a delimited <state> block",
        },
        "gpu_count": None,
        "gpu_type": None,
        "gpu_rental_started_at": None,
        "gpu_rental_ended_at": None,
        "trained_at": "2026-07-18T00:00:00Z",
    },
    {
        # reports/A1-V1.md (07-22 03:00 TRAINING COMPLETE entry + spend table)
        # and PRs/intelligence-production-v1/context/A1-V1-LANE.md.
        "org_slug": ORG_SLUG,
        "org_name": ORG_NAME,
        "name": "ptc-steel-a1-v1",
        "iteration": 1,
        # no_go, not pending: the active-arm ship decision WAS made (eval window
        # closed 07-22, 18 PASS / 6 FAIL). The shadow lane continuing per V-036
        # is a separate pilot, not an undecided ship verdict — the catalog was
        # under-stating a decided NO-GO (MODEL-LIBRARY discrepancy #2, 07-30).
        "ship_status": "no_go",
        "headline_verdict": (
            "NO-GO as an ACTIVE arm (eval 18 PASS / 6 FAIL: pushback-hold never trained in, "
            "injection + tool-channel adoption regressed, fabricate-to-comply signature) — wired "
            "into the nightly per-account pipeline as a SHADOW arm only. Send authority stays with "
            "Claude until V-036: 3 clean live shadow nights incl. >=1 weekend. Promotion is "
            "interlocked behind a Daniel sign-off token; the pitch is the same nightly job at "
            "~$4-6/night vs Claude's measured ~$163."
        ),
        "training_provider": "runpod (self-run axolotl)",
        # RunPod has no job resource; the pod id is the closest analogue and
        # is recorded verbatim from the 03:00 completion entry.
        "provider_job_id": "runpod pod cuq3wp21hsgjcb (pod 7 — the rc=0 run)",
        "provider_model_path": None,
        "infra": {
            "platform": "self-run axolotl on RunPod Secure (managed lanes NOT viable: Fireworks never recognized the qwen3_5_moe architecture — 37h stuck UPLOADING; Together lacks the base entirely)",
            "gpus": "2x H200 141GB — 80GB H100s proven unable to train this corpus (fla Triton backward OOMs on the long-record tail)",
            "wall_clock": "73 min, 489/489 steps, full epoch (07-22 03:00 PT complete, rc=0)",
            "adapter": "7.55GB, sha 1cceae5c…; mid-epoch checkpoint-245 kept as ladder evidence",
            "base_pin": "InternScience/Agents-A1 @ addff08 (35B-A3B, qwen3_5_moe hybrid GDN linear-attention MoE)",
            "spend": "training run ~$53 of which ~$27 was debugging pods 1-6 (80GB-OOM + venv-shadow failures); night all-in ~$58 vs the $150 gate",
            "serving_lane": "nightly RunPod rent-run-terminate 1xH200 + 150GB network volume ~$4-6/night (SERVE-PLAN; vLLM with L51 pins)",
        },
        # The recorded eval-loss ladder from the 03:00 completion entry —
        # pre-train eval plus 8 checkpoints to final. Step numbers are the
        # checkpoint sequence (the entry records the series, not per-step
        # global steps).
        "loss_curve": [
            {"step": 1, "loss": 0.7811, "label": "pre-train (eval loss)"},
            {"step": 2, "loss": 0.7077, "label": "checkpoint (eval loss)"},
            {"step": 3, "loss": 0.6429, "label": "checkpoint (eval loss)"},
            {"step": 4, "loss": 0.6009, "label": "checkpoint (eval loss)"},
            {"step": 5, "loss": 0.5643, "label": "checkpoint (eval loss)"},
            {"step": 6, "loss": 0.5358, "label": "checkpoint (eval loss)"},
            {"step": 7, "loss": 0.5063, "label": "checkpoint (eval loss)"},
            {"step": 8, "loss": 0.4765, "label": "checkpoint (eval loss)"},
            {"step": 9, "loss": 0.4587, "label": "final (eval loss)"},
        ],
        "base_model": "InternScience/Agents-A1 (35B-A3B MoE, hybrid GDN linear attention) @ addff08",
        "lora_rank": 32,
        "lora_alpha": "alpha32",
        # Recorded in reports/A1-V1.md (training config of record) — was NULL
        # here until the 2026-07-30 Phase 0 catalog audit flagged the gap.
        "learning_rate": "3e-6",
        "epochs": 1,
        "weight_decay": None,
        "target_modules": "310 modules + 80 fused expert params via lora_target_parameters (GDN in_proj_qkv/z/a/b + out_proj + MoE experts — transformers 5.14.1 splits GDN projections; the axolotl-docs names would have silently missed all 30 linear-attention layers)",
        # 3,908 is the SEALED rev-5 corpus of record (A1-V1.md 19:02 SEAL-RENDER);
        # 3,911 was the pre-seal rev-4 split before the 3 silent-omit drops —
        # corrected 2026-07-30 per the Phase 0 catalog audit (documents win).
        "train_records": 3908,
        "training_cost_usd": 53.0,
        "train_eval_loss": "eval loss 0.7811 -> 0.4587 (ppl 2.184 -> 1.582), monotonic; train_loss avg 0.5642; overfit tripwire never fired",
        "mixture": {
            "corpus": "3,908 train / 75 eval records (sealed rev-5, deterministic split, SEED=42), max rendered length 9,346 vs sequence_len 16,384",
            "ballast": "1,633 A1-self ballast records / 459K lb = 34.7% share (mid-band), via the GAP-11 acceptance pipeline",
            "note": "per-slice lb-token table lives in reports/a1-v1/RENDER-PREP evidence; not re-derived here",
        },
        "gpu_count": 2,
        "gpu_type": "H200 141GB (RunPod Secure)",
        # Exact rental clock times for pod 7 were not published in the report
        # (only the 73-min training wall-clock and the 03:00 PT completion) —
        # None, never reconstructed.
        "gpu_rental_started_at": None,
        "gpu_rental_ended_at": None,
        "trained_at": "2026-07-22T10:00:00Z",
    },
    {
        # PRs/intelligence-production-v1/context/INT-V1-STATE.md fact table
        # (verified by the Daniel-requested independent audit of 07-22 10:05).
        "org_slug": "emanate-intelligence",
        "org_name": "Emanate Intelligence (cross-org product surface)",
        "name": "intv1-corpus-v1",
        "iteration": 1,
        "ship_status": "pending",
        "headline_verdict": (
            "Pilot-YES behind three guardrails (canonical envelope only / RC-1 output filter / "
            "tools always on) — launch-NO until the 07-27 K3 re-check + second held-out labeler. "
            "Shadow-only: the Intelligence chat is always answered by gpt-5.6, byte-identically, "
            "whether or not a capture happens alongside it. Deficit lifts measured: multi-turn "
            "re-call 4.2% -> 66.7%; no-action 0% -> 52.5%; fluent fabrication 0% (arm 1); "
            "do-not-regress 0/6 trips."
        ),
        "training_provider": "together",
        "provider_job_id": "ft-b32c0e5a-8d16",
        "provider_model_path": "daniel_7cf9/Qwen3.6-35B-A3B-intv1-corpus-v1-6f460574",
        "infra": {
            "platform": "Together AI managed fine-tuning (no GPU rental by us; train-time base is bnb-4bit QLoRA — forced by Together — serving is FP8)",
            "steps": "2,936/2,936, 1 epoch, completed 07-22 00:40 PT",
            "billed_tokens": "16,151,518 — byte-match vs the pinned local render (LAUNCH-PACKET §8.3 gate PASSED)",
            "adapter": "sha 623a3933…, pins verified r32/alpha32, targets q,k,v,o_proj",
            "serving_lane": "Together v2 dedicated deployment of the merged FT model (serverless custom is dead — HTTP 400 model_not_available); live-proven 07-22: endpoint intv1-tuned-0722 cold-started 5.3 min, served the 1,342-probe battery, teardown VERIFIED-GONE, $0.70. NO endpoint exists now; $10.98/hr FP8 2xH100 TP2 when one does, and v2 has NO idle auto-stop (~$264/day if left running)",
            "spend": "training $24.23 (== naive projection exactly); run total ~$30.01 incl. micro-test $4.00, census $1.08, eval window $0.70",
        },
        # No eval-loss series was published in INT-V1's fact table — only job
        # completion facts. Empty, never invented.
        "loss_curve": [],
        "base_model": "Qwen3.6-35B-A3B (Together catalog; sole certified deploy profile FP8 2xH100 TP2)",
        "lora_rank": 32,
        "lora_alpha": "alpha32",
        # Recorded in INT-V1-STATE.md's fact table — was NULL here until the
        # 2026-07-30 Phase 0 catalog audit flagged the gap.
        "learning_rate": "3e-6",
        "epochs": 1,
        "weight_decay": None,
        "target_modules": "q,k,v,o_proj",
        "train_records": None,
        "training_cost_usd": 24.23,
        "train_eval_loss": None,
        "mixture": {
            "corpus": "intv1-corpus-v1 (corpus sealed 07-21 per LAUNCH-PACKET; 16,151,518 billed tokens byte-matched the pinned local render)",
            "note": "per-slice composition lives in reports/int-v1/LAUNCH-PACKET.md; not re-derived here",
        },
        "gpu_count": None,
        "gpu_type": None,
        "gpu_rental_started_at": None,
        "gpu_rental_ended_at": None,
        "trained_at": "2026-07-22T07:40:00Z",
    },
    {
        # fine-tuning/models/ptc-steel-v7-agent/MODEL-CARD.md. No
        # factory_runs row exists (manual REST launch, not the factory CLI)
        # — source_run_id stays null, the sharpest NULL-on-purpose proof
        # point in this consolidation (mirrors ptc-steel-v4-agent's null
        # provider_model_path convention).
        "org_slug": ORG_SLUG,
        "org_name": ORG_NAME,
        "name": "ptc-steel-v7-agent",
        "source_run_id": None,
        "iteration": 7,
        # pending, re-verified live at execution time (2026-08-14): the
        # card's own header line ("evaluation pending as of 2026-08-07") is
        # stale, but its body — current through 2026-08-11 — still declines
        # to make a ship call either way ("does not constitute a ship
        # recommendation in either direction"; S8 not yet scheduled, pending
        # Daniel's go-ahead). A confirmed SCOPE CEILING finding exists, but
        # it is a structural capability limit, not a terminal ship verdict —
        # 'pending' is the honest read, not 'no_go' by default.
        "ship_status": "pending",
        "headline_verdict": (
            "TRAINED (2026-08-06). Behavioral eval re-run 2026-08-10/11 with a fixed, "
            "verified-uncontaminated harness (BLIND_TEMPORAL_ORDERING) found a SCOPE CEILING: "
            "v7's corpus produces only 2 output classes (no_action / winback_enrollment) vs. the "
            "champion's real ~10-class vocabulary — 33.2% of live decisions are structurally "
            "impossible for this model to produce, independent of quality. Decision-agreement "
            "itself is UNINFORMATIVE (n=7 in-scope points per cohort; needs ~5 nights aggregated "
            "for n>=30). No ship recommendation made in either direction; S8 full eval window "
            "designed but not yet scheduled, pending Daniel's go-ahead."
        ),
        "training_provider": "fireworks",
        "provider_job_id": f"{FIREWORKS_SFT_JOBS}/wmvseysm",
        "provider_model_path": "accounts/emanate/models/ptc-steel-v7-agent",
        "infra": {
            "platform": "Fireworks supervised fine-tuning (serverless — no dedicated GPU rental)",
            "dataset": "ptc-rescope-2569-v7.jsonl (2,569 records, 66,981,280 bytes; sha 551b8ee0ed12742db3b352234afa22cb5de929c5135a1582651750f03ad7ade6)",
            "fireworks_estimated_token_count": "16,533,800 (1.05% under the tiktoken-calibrated pre-launch projection of 16,708,894)",
            "canary_job": f"{FIREWORKS_SFT_JOBS}/pzynw9c5 (ptc-steel-v7-canary, 257 rec stratified 10% sample, COMPLETED, $4.87)",
            "job_create_time": "2026-08-06T21:01:26Z",
            "job_completed_time": "2026-08-06T21:50:27Z",
            "wall_clock": "49 min",
        },
        # No per-step/per-epoch series was published for this job — empty,
        # never invented.
        "loss_curve": [],
        "base_model": "qwen3p6-27b (dense 27.3B, Apache-2.0, confirmed dense not MoE live via the Fireworks API; same base as v2-v5-agent and grand-steel-v1-agent)",
        "lora_rank": 32,
        "lora_alpha": "alpha32 / scaling 1.0 (Fireworks forces alpha=rank)",
        "learning_rate": "3e-6",
        "epochs": 1,
        "weight_decay": "0",
        "target_modules": "all-linear (12 projection types incl. the GDN-style split in_proj_{a,b,qkv,z})",
        "train_records": 2569,
        # All-in per TRAINING-RUNBOOK's dual-bucket convention (Fireworks
        # estimatedCost $48.80 x2, same L34 rule as grand-steel-v1-agent's
        # $18.15), plus the $4.87 canary — matches the card's own "All-in
        # ~$97.60" line.
        "training_cost_usd": 97.60,
        # No scalar train/eval loss was published for this job — None,
        # never invented (same convention as ptc-steel-v5-agent).
        "train_eval_loss": None,
        "mixture": {
            "corpus": "ptc-rescope-2569-v7.jsonl (2,569 records; family split unchanged since the rescope's first build)",
            "breakdown": [
                {"group": "real_trajectory", "records": 1275},
                {"group": "twins", "records": 250},
                {"group": "stale_recall", "records": 300},
                {"group": "action_cf", "records": 314},
                {"group": "replay", "records": 430},
            ],
            "lineage": "8 builds in ~30 hours (2026-08-05 to 2026-08-06); 3 real defects found and fixed (R1 date-arithmetic self-contradiction, R-leak future-info leak, weight-1 fabrication in replay)",
        },
        "gpu_count": None,
        "gpu_type": None,
        "gpu_rental_started_at": None,
        "gpu_rental_ended_at": None,
        "trained_at": "2026-08-06T21:50:27Z",
    },
    {
        # fine-tuning/models/grand-steel-v1-agent/MODEL-CARD.md +
        # runs/grand-steel/swarm/cloud/progression/DIAGNOSTIC-RESULTS.md +
        # REMEDIATION-VERIFICATION.md. REFRESH of a stale row (T4) — this is
        # the same real live factory_runs row and the same real training
        # facts as the row this replaces; only ship_status/headline_verdict/
        # report change, reflecting the diagnostic waves that ran after the
        # original row was written the day training completed.
        "org_slug": "grand-steel",
        "org_id": "10664bd2-79d2-49d2-8196-fcaf539d9eb3",
        "org_name": "Grand Steel",
        "name": "grand-steel-v1-agent",
        # REAL live factory_runs row — the first S7 the factory ever
        # executed — linked directly, not via the backfilled uuid5 scheme.
        "source_run_id": "523ecbbf-e049-405f-8354-779e2cd40307",
        "iteration": 1,
        # no_go, re-verified live at execution time (2026-08-14): the
        # committed row this replaces said 'pending' the day training
        # completed (2026-07-30), before either diagnostic wave ran. Two
        # later diagnostic waves (2026-08-06, 2026-08-08) closed the card's
        # own previously-OPEN G11 item and reached a FINALIZED_NO_TRAIN
        # verdict — a concluded, unfavorable result (see headline_verdict).
        "ship_status": "no_go",
        "headline_verdict": (
            "TRAINED 2026-07-30, evaluated 2026-07-31 through 2026-08-08. Pre-S8 battery: G1 "
            "must-call PASSES CLEAN at 0/57; G2 staged-overcalling FAILS CLEAN at 54.2% (bar "
            "<=25%); G6 verdict-terminal-emit FAILS CLEAN at 2.5% (bar >=85%). A paid diagnostic "
            "wave (2026-08-06) and a harness-remediation verification wave (2026-08-08) closed the "
            "originally-OPEN G11 item with a real tuned-vs-TRUE-BASE 772-scenario comparison: all "
            "four failing gates (G2/G6/G7/G8) fail by the same mechanism on the same or "
            "near-identical scenarios on the untrained base model too (G2 Jaccard overlap 0.936, "
            "rising to 0.974 after harness fixes; G6 identical on the exact same 2/80 scenario IDs "
            "with near-byte-identical reasoning) — runtime/harness-shared, not weight-attributable. "
            "G10 (decline collapse) is the one gate that separates, and it separates in tuned's "
            "favor. Verdict of record: FINALIZED_NO_TRAIN — no product-relevant residual is "
            "attributable to the trained weights; retraining will not fix these gates."
        ),
        "training_provider": "fireworks",
        "provider_job_id": "accounts/emanate/supervisedFineTuningJobs/a7jm4e4b",
        "provider_model_path": "accounts/emanate/models/grand-steel-v1-agent",
        "infra": {
            "platform": "Fireworks supervised fine-tuning (serverless — no dedicated GPU rental)",
            "dataset": "accounts/emanate/datasets/combined-train-grand-steel-v1 (578 examples; no eval split)",
            "fireworks_estimated_token_count": "3,069,300 (dataset; 2.28x the tiktoken/1.95 pre-upload estimate — tool-schema-heavy corpora tokenize ~dense in Qwen)",
            "job_create_time": "2026-07-30T20:55:11Z",
            "job_completed_time": "2026-07-30T21:20:12Z",
            "wall_clock": "25 min (19 steps)",
            "cost_story": "projection formula $48.18 (incl. $15 unspent eval reserve + x1.3 calibration) > $30 auto-approve -> Daniel approved via V-110; realistic decomposition $25.50; ACTUAL estimatedCost $9.07 -> all-in $18.15 (L34 2x)",
            "artifact": "HF_PEFT_ADDON, state READY; adapter_config.json + render_samples.jsonl + metrics.jsonl saved to platform-alpha/finetune-out/gs-run/",
        },
        # Real 19-point train-loss series read from the job monitor's saved
        # metrics.jsonl (no eval split on this job — this is the ONLY series
        # that exists; never summarized or interpolated).
        "loss_curve": [
            {"step": 1, "loss": 1.6295, "label": "train loss (no eval split on this job)"},
            {"step": 2, "loss": 1.5644, "label": "train loss (no eval split on this job)"},
            {"step": 3, "loss": 1.1186, "label": "train loss (no eval split on this job)"},
            {"step": 4, "loss": 1.6479, "label": "train loss (no eval split on this job)"},
            {"step": 5, "loss": 1.7487, "label": "train loss (no eval split on this job)"},
            {"step": 6, "loss": 1.5580, "label": "train loss (no eval split on this job)"},
            {"step": 7, "loss": 1.4600, "label": "train loss (no eval split on this job)"},
            {"step": 8, "loss": 1.6717, "label": "train loss (no eval split on this job)"},
            {"step": 9, "loss": 1.3580, "label": "train loss (no eval split on this job)"},
            {"step": 10, "loss": 1.2425, "label": "train loss (no eval split on this job)"},
            {"step": 11, "loss": 1.5807, "label": "train loss (no eval split on this job)"},
            {"step": 12, "loss": 1.4464, "label": "train loss (no eval split on this job)"},
            {"step": 13, "loss": 1.2580, "label": "train loss (no eval split on this job)"},
            {"step": 14, "loss": 1.1662, "label": "train loss (no eval split on this job)"},
            {"step": 15, "loss": 1.4465, "label": "train loss (no eval split on this job)"},
            {"step": 16, "loss": 1.4759, "label": "train loss (no eval split on this job)"},
            {"step": 17, "loss": 1.7172, "label": "train loss (no eval split on this job)"},
            {"step": 18, "loss": 1.4927, "label": "train loss (no eval split on this job)"},
            {"step": 19, "loss": 1.8471, "label": "train loss (no eval split on this job)"},
        ],
        "base_model": "qwen3p6-27b (dense 27.3B, same base as ptc-steel v2-v5)",
        "lora_rank": 32,
        "lora_alpha": "alpha32 / scaling 1.0 (platform: alpha=r)",
        "learning_rate": "3e-6",
        "epochs": 1,
        "weight_decay": "0",
        "target_modules": "all-linear (platform default)",
        "train_records": 578,
        # all-in per L34 dual-bucket: platform estimatedCost $9.07 x 2 = $18.15
        # (training cost only — unchanged by the later diagnostic/eval waves,
        # which are eval spend, not training spend).
        "training_cost_usd": 18.15,
        "train_eval_loss": (
            "train loss 1.629 -> 1.847 (min 1.119 @ step 3, 19 steps, ppl min 3.06); "
            "NO eval split on this job"
        ),
        "mixture": {
            "corpus": "combined-train-grand-steel-v1 (578 records, 77,394 lb-tokens; SHA-256 2f7368a87fa2aa966ed0ec65786a67a60db423f85913fa81cc40acb2dbc29312)",
            "unit": "loss-bearing tokens (lb-tokens)",
            "breakdown": [
                {"group": "1a real tool trajectories (238 reviewed KEEP pool, 25 CURATE-fixed in-selection)", "records": 158, "lb_pct": 53.24},
                {"group": "1b specialist counterfactuals (PTC Ground/Scope/Decline shapes, UNVALIDATED-FOR-GS)", "records": 375, "lb_pct": 31.87},
                {"group": "1c untuned-base broad replay (teacher: untuned qwen3p6-27b)", "records": 45, "lb_pct": 14.89},
            ],
            "targets": "50/35/15 +/-5pp — all inside",
            "e21_census": "tool-call-issuing : staged-answer = 1.08 (must-call priority selection)",
            "heldout": "65/65 manifest accounts DISJOINT",
        },
        "gpu_count": None,
        "gpu_type": None,
        "gpu_rental_started_at": None,
        "gpu_rental_ended_at": None,
        "trained_at": "2026-07-30T21:20:12Z",
    },
    {
        # fine-tuning/models/grand-steel-v2-agent/MODEL-CARD.md. No
        # factory_runs row exists for v2 (built via the manual candidate
        # pipeline, same as v7) — source_run_id stays null.
        "org_slug": "grand-steel",
        "org_id": "10664bd2-79d2-49d2-8196-fcaf539d9eb3",
        "org_name": "Grand Steel",
        "name": "grand-steel-v2-agent",
        "source_run_id": None,
        "iteration": 2,
        # no_go, not pending: per the card's own status line, the eval has
        # actually concluded (G6 clean null, G2 unproven nudge) — unlike
        # v1's original pre-refresh row, this is not a case of "training
        # done, eval not yet run."
        "ship_status": "no_go",
        "headline_verdict": (
            "TRAINED 2026-08-11, EVALUATED 2026-08-12. Real result: a clean null on G6 "
            "(1.25% all three arms vs. base/v1, bar >=85%) and a real-but-statistically-unproven "
            "small favorable nudge on G2 (base 59.36% -> v1 58.17% -> v2 54.58%, bar <=25% — "
            "Wilson 95% intervals heavily overlap, not statistically separated). G1 unregressed "
            "(0/57 all three arms). Neither gate cleared its bar — not a case for shipping. Does "
            "not reopen grand-steel-v1-agent's FINALIZED_NO_TRAIN diagnostic verdict, which stands "
            "separately: training v2 was a distinct, Daniel-authorized decision made with that "
            "finding already in hand, not a re-litigation of it."
        ),
        "training_provider": "fireworks",
        "provider_job_id": f"{FIREWORKS_SFT_JOBS}/ozhgeptn",
        "provider_model_path": "accounts/emanate/models/grand-steel-v2-agent",
        "infra": {
            "platform": "Fireworks supervised fine-tuning (serverless — no dedicated GPU rental)",
            "dataset": "accounts/emanate/datasets/gs-v2-candidate-v1 (658 records, real Fireworks estimatedTokenCount 3,748,600)",
            "canary_job": f"{FIREWORKS_SFT_JOBS}/lskfn38u (accounts/emanate/datasets/gs-v2-candidate-v1-canary, 65 records, real estimatedTokenCount 381,700, COMPLETED, no divergence/NaN)",
            "job_create_time": "2026-08-11T22:42:52Z",
            "job_completed_time": "2026-08-12T00:05:52Z",
            "wall_clock": "~83 min total (createTime to completedTime); actual training phase ~8 min (23:58:29Z-00:06:35Z), remainder is provisioning/queue time",
            "cost_story": "canary $1.1246 + training-only full job $11.2185 = $12.3431 total training-wave spend (real Fireworks numbers; pre-launch all-in estimate was ~$22.6, which included a $15 eval reserve not yet spent at training time)",
        },
        # No metrics.jsonl-equivalent file exists for this job (checked) —
        # empty, never invented.
        "loss_curve": [],
        "base_model": "qwen3p6-27b (dense 27.3B, Apache-2.0 — same base as grand-steel-v1-agent and ptc-steel-v2-agent...v7-agent)",
        "lora_rank": 32,
        "lora_alpha": "alpha32 / scaling 1.0 (Fireworks forces alpha=rank)",
        "learning_rate": "3e-6",
        "epochs": 1,
        "weight_decay": "0",
        "target_modules": "all-linear (platform default)",
        "train_records": 658,
        "training_cost_usd": 12.3431,
        # No scalar train/eval loss was published for this job — None, never
        # invented.
        "train_eval_loss": None,
        "mixture": {
            "corpus": "gs-v2-candidate-v1.jsonl (658 records, 86,279 lb-tokens)",
            "unit": "loss-bearing tokens (lb-tokens)",
            "breakdown": [
                {"group": "1a-real-trajectories, FIXED (carried forward from v1, re-rendered with 2 real defects fixed)", "records": 158},
                {"group": "1b-specialist (8 sub-families, unchanged byte-for-byte from v1)", "records": 375},
                {"group": "1c-base-broad replay (unchanged byte-for-byte from v1)", "records": 45},
                {"group": "1d-staged-sufficient (fam_staged_sufficient_twin, NEW)", "records": 80},
            ],
            "mixture_pct": "real 46.66% / specialist 28.59% / replay 13.36% / staged-sufficient 11.4% (lb-token basis, post-fix)",
            "rigor_check": "11/12 attacks PASS; one disclosed, not-fixed residual (4/238 emit_output.reasoning strings carry verbatim historical reviewer prose mentioning a tool name, per this program's copy-from-tool-verbatim invariant)",
        },
        "gpu_count": None,
        "gpu_type": None,
        "gpu_rental_started_at": None,
        "gpu_rental_ended_at": None,
        "trained_at": "2026-08-12T00:05:52Z",
    },
]


def _model_row_id(name):
    return str(uuid.uuid5(NAMESPACE, f"factory_models:{name}"))


def build_model_rows():
    rows = []
    for spec, it in zip(MODELS, ITERATIONS):
        assert spec["name"] == it["name"], f"MODELS/ITERATIONS order drifted: {spec['name']} != {it['name']}"
        rows.append(
            {
                "id": _model_row_id(spec["name"]),
                "org_slug": ORG_SLUG,
                "org_id": None,
                "org_name": ORG_NAME,
                "model_id": spec["name"],
                "source_run_id": _run_id(it["name"]),
                "iteration": it["iteration"],
                "ship_status": "no_go",
                "headline_verdict": f"{it['verdict']} — {it['lesson']}",
                "base_model": spec["base_model"],
                "lora_rank": spec["lora_rank"],
                "lora_alpha": spec["lora_alpha"],
                "learning_rate": spec["learning_rate"],
                "epochs": spec["epochs"],
                "weight_decay": spec["weight_decay"],
                "target_modules": spec["target_modules"],
                "train_records": spec["train_records"],
                "training_cost_usd": it["cost_actual_usd"],
                "train_eval_loss": spec["train_eval_loss"],
                "mixture": spec["mixture"],
                "report": REPORTS[spec["name"]],
                "training_provider": TRAINING_PROVIDER,
                "provider_job_id": spec["provider_job_id"],
                "provider_model_path": spec["provider_model_path"],
                # Serverless honesty: no GPU rental existed for any Fireworks
                # SFT run — None across the board, never a fabricated count.
                "gpu_count": None,
                "gpu_type": None,
                "gpu_rental_started_at": None,
                "gpu_rental_ended_at": None,
                "infra": spec["infra"],
                "loss_curve": spec["loss_curve"],
                "lessons_minted": [],
                "trained_at": it["updated_at"],
            }
        )
    return rows


def build_extra_model_rows():
    """The six artifacts outside the 4-iteration PTC zip (v5, A1-V1, INT-V1,
    ptc-steel-v7-agent, grand-steel-v1-agent, grand-steel-v2-agent). Fully
    specified in EXTRA_MODELS; v5 and grand-steel-v1-agent carry a real
    source_run_id (v5's factory_runs row was backfilled 2026-07-30;
    grand-steel-v1-agent's is a real live run, 523ecbbf) — the other four
    have none. org_id defaults to None (most of these predate the factory's
    org_id/org_slug bridge) except where spec provides a real one (Grand
    Steel)."""
    rows = []
    for spec in EXTRA_MODELS:
        rows.append(
            {
                "id": _model_row_id(spec["name"]),
                "org_slug": spec["org_slug"],
                "org_id": spec.get("org_id"),
                "org_name": spec["org_name"],
                "model_id": spec["name"],
                "source_run_id": spec.get("source_run_id"),
                "iteration": spec["iteration"],
                "ship_status": spec["ship_status"],
                "headline_verdict": spec["headline_verdict"],
                "base_model": spec["base_model"],
                "lora_rank": spec["lora_rank"],
                "lora_alpha": spec["lora_alpha"],
                "learning_rate": spec["learning_rate"],
                "epochs": spec["epochs"],
                "weight_decay": spec["weight_decay"],
                "target_modules": spec["target_modules"],
                "train_records": spec["train_records"],
                "training_cost_usd": spec["training_cost_usd"],
                "train_eval_loss": spec["train_eval_loss"],
                "mixture": spec["mixture"],
                "report": EXTRA_REPORTS[spec["name"]],
                "training_provider": spec["training_provider"],
                "provider_job_id": spec["provider_job_id"],
                "provider_model_path": spec["provider_model_path"],
                "gpu_count": spec["gpu_count"],
                "gpu_type": spec["gpu_type"],
                "gpu_rental_started_at": spec["gpu_rental_started_at"],
                "gpu_rental_ended_at": spec["gpu_rental_ended_at"],
                "infra": spec["infra"],
                "loss_curve": spec["loss_curve"],
                "lessons_minted": [],
                "trained_at": spec["trained_at"],
            }
        )
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print what would be pushed, write nothing")
    parser.add_argument("--url", default=None)
    parser.add_argument("--service-role-key", default=None)
    args = parser.parse_args(argv)

    rows = build_model_rows() + build_extra_model_rows()
    print(
        f"models_backfill: {len(rows)} model rows (source: {SOURCE}; extras: 07-RUN-LEDGER.md, "
        "reports/A1-V1.md, INT-V1-STATE.md, ptc-steel-v7-agent/MODEL-CARD.md, "
        "grand-steel-{v1,v2}-agent/MODEL-CARD.md)"
    )
    for row in rows:
        print(f"  {row['model_id']} base={row['base_model']} cost=${row['training_cost_usd']} "
              f"records={row['train_records']} source_run_id={row['source_run_id']}")

    if args.dry_run:
        print("\n--dry-run: no writes performed.")
        return 0

    try:
        upsert("factory_models", rows, on_conflict="org_slug,model_id", url=args.url, service_role_key=args.service_role_key)
        print("pushed factory_models OK")
    except SupabaseRestError as exc:
        print(f"models_backfill: PUSH FAILED — {exc}", file=sys.stderr)
        return 1

    print("models_backfill: done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
