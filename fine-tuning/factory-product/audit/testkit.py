"""testkit — planted-violation fixture builder for the audit test suite.

Every deterministic check ships gold + seeded-fault tests, BOTH directions,
before its verdict counts (RESEARCH R5 / DESIGN §11.3 — false-green ties
contract drift as the dominant recorded failure class). This module builds a
synthetic factory tree (configs/ + runs/<slug>/…) that passes every Phase 0
check, plus one mutator per recorded catch that plants exactly the violation
the check exists to find.

Deliberately generic org markers (no ptc constants): the checks must be
run-parameterized, and these fixtures prove it.
"""

import json
from pathlib import Path

SLUG = "test-org"
ORG_ID = "11111111-2222-3333-4444-555555555555"
RUN_ID = "0b6de407-9d38-45a2-9a63-1f1f5a3e0c11"
EXCLUDED_ORG_PREFIX = "deadbeef"
EXCLUDED_ENVELOPE_PREFIX = "eeeeeeee"
EXCLUDED_TOOL = "query_other_org"
ENVELOPE_HASH = "a" * 64
TOOLS = ["read_a", "read_b", "emit_output"]
ALLOWED_ACTIONS = ["no_action", "winback_enrollment"]

GOLD_BUILD_SPEC = """# BUILD-SPEC — test-org synthetic run
## 1. Slice plan
Slice T real trajectories, proportional to D4 shares (winback 100%).
Source pool: 07-20 has 5 real Claude actions (D6).
Staged prior transcript posture on every record; every staged-evidence
selection gets its must-call twin >=1:1 (D1).

| Block | Records | Spec |
|---|---|---|
| S-act (D4) | ~4 (2x mean nightly miss), floor 2/class | split per D5 |

## 4. Exit criteria
1. lints pass.
"""

CONFIG_TEMPLATE = """org:
  slug: {slug}
  id: {org_id}
  name: Test Org
book:
  reviews: 10
  reviews_with_trajectory_id: {trajectory_census}
model_plan:
  base_model: accounts/x/models/base-27b
  seed: 42
  corpus_mixture_v1:
    replay_pct: 15
    real_trajectories_pct: 50
    specialist_pct: 35
training:
  lora_rank: 32
  epochs: 1
  learning_rate: 3.0e-6
"""

GOLD_EXCLUSIONS = f"""excluded_orgs:
  - id_prefix: "{EXCLUDED_ORG_PREFIX}"
    envelope_hash_prefix: "{EXCLUDED_ENVELOPE_PREFIX}"
    tool_schema_markers: [{EXCLUDED_TOOL}]
    reason: synthetic excluded org
forbidden_envelope_prefixes:
  - prefix: "ffffffff"
    reason: synthetic forbidden contract
anomalous_nights: []
"""


def _pool_row(i, night, klass, trajectory=True):
    return {"id": f"row-{i}", "account_id": f"acct-{i % 3}", "night": night,
            "reviewed_at": f"{night}T06:00:00+00:00", "class": klass,
            "trajectory_id": f"traj-{i}" if trajectory else None,
            "n_reads": 2, "n_awareness_reads": 2, "read_tools": ["read_a"]}


def gold_rows():
    """5 winback actions on 07-20 + 5 no_action on 07-21; 6/10 rows carry a
    trajectory id (matches the config census)."""
    action = [_pool_row(i, "2026-07-20", "winback_enrollment", trajectory=True)
              for i in range(5)]
    no_action = [_pool_row(5 + i, "2026-07-21", "no_action_readchain",
                           trajectory=(i == 0)) for i in range(5)]
    return action, no_action


def build_tree(root):
    """A gold factory tree under `root`; returns the factory_root Path."""
    factory_root = Path(root) / "factory"
    run_dir = factory_root / "runs" / SLUG
    inputs = run_dir / "corpus" / "inputs"
    inputs.mkdir(parents=True)
    (factory_root / "configs").mkdir(parents=True)
    (run_dir / "audit").mkdir(parents=True)
    (run_dir / "iterations").mkdir(parents=True)

    (factory_root / "configs" / f"{SLUG}.yaml").write_text(
        CONFIG_TEMPLATE.format(slug=SLUG, org_id=ORG_ID, trajectory_census=6))
    (run_dir / ".sync_state.json").write_text(json.dumps({"run_id": RUN_ID}))
    (run_dir / "corpus" / "BUILD-SPEC.md").write_text(GOLD_BUILD_SPEC)
    (run_dir / "audit" / "exclusions.yaml").write_text(GOLD_EXCLUSIONS)
    (run_dir / "03-export-report.json").write_text(json.dumps(
        {"generated_at": "2026-07-31T10:00:00+00:00", "status": "ok"}))
    (run_dir / "iterations" / "01-digest.json").write_text(json.dumps({
        "run_id": "b" * 8, "org_slug": SLUG, "iteration": 1,
        "corpus": {"recipe": "synthetic v1: replay 15% / real 50% / "
                             "specialist 35%"},
        "training": {"base_model": "base-27b (dense)", "lora_rank": 32,
                     "epochs": 1, "learning_rate": "3e-6"},
        "ship": {"status": "no_go"},
    }))

    (inputs / "envelope.json").write_text(json.dumps({
        "hash": ENVELOPE_HASH,
        "pulled_at": "2026-07-31T12:00:00+00:00",
        "tool_names": TOOLS,
        "org_id": ORG_ID,
        "matches_expected": True,
        "allowed_actions": ALLOWED_ACTIONS,
    }))

    action, no_action = gold_rows()
    # The landed pool shape legitimately NAMES the excluded org in its own
    # verification metadata — the org scan must key on rows, never on this.
    (inputs / "selection-pool.json").write_text(json.dumps({
        "org_id": ORG_ID,
        "excluded_org_prefix": EXCLUDED_ORG_PREFIX,
        "excluded_org_rows_found": False,
        "read_tools_counted": ["read_a", "read_b", EXCLUDED_TOOL],
        "action_pool": action,
        "no_action_readchain_pool": no_action}))
    # Train pool: 4 of the 5 actions + 4 of the 5 no_action (one of each
    # held out).
    (inputs / "train-pool.json").write_text(json.dumps({
        "org_id": ORG_ID, "action_pool": action[:4],
        "no_action_readchain_pool": no_action[:4]}))
    return factory_root


def gold_observed():
    """A served-surface fingerprint that MATCHES the recorded envelope."""
    return {
        "org_id": ORG_ID,
        "pulled_at": "2026-07-31T13:00:00+00:00",
        "basis": "test fixture",
        "window_days": 5,
        "rows_scanned": 100,
        "nights_observed": ["2026-07-29", "2026-07-30"],
        "observed_tool_names": {"read_a": 60, "read_b": 40, "emit_output": 100},
        "payload_stamp_keys": {"sequence": 10},
        "output_types": {"no_action": 90, "winback_enrollment": 10},
    }


def drifted_observed():
    """Catch #2 planted: prod calls a tool the corpus envelope doesn't offer."""
    observed = gold_observed()
    observed["observed_tool_names"]["query_account_orders_x"] = 37
    observed["payload_stamp_keys"]["_groundingGuardrail"] = 100
    return observed


def write_corpus(factory_root, *, source_row_id="row-0", night="2026-07-20",
                 gold_class="winback_enrollment", importance=0,
                 archetype="A", rationale_len=300, draft_subject=""):
    """One assembled corpus record + meta line under corpus/build/."""
    build = Path(factory_root) / "runs" / SLUG / "corpus" / "build"
    build.mkdir(parents=True, exist_ok=True)
    emit_args = {
        "output_type": gold_class,
        "importance_score": importance,
        "reasoning": "Grounded in fresh reads.",
        "payload": {"sequence": {"rationale": "r" * rationale_len,
                                 "archetype": archetype}},
    }
    if draft_subject:
        emit_args["draft_subject"] = draft_subject
    record = {"messages": [
        {"role": "system", "content": f"<<ENVELOPE sha256={ENVELOPE_HASH}>>",
         "weight": 0},
        {"role": "user", "content": "Nightly review.", "weight": 0},
        {"role": "assistant", "content": None, "weight": 1, "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "emit_output",
                          "arguments": json.dumps(emit_args)}}]},
    ]}
    meta = {"block": "T", "envelope_hash": ENVELOPE_HASH,
            "source_row_id": source_row_id, "night": night,
            "gold_class": gold_class}
    (build / "test.jsonl").write_text(json.dumps(record) + "\n")
    (build / "test.meta.jsonl").write_text(json.dumps(meta) + "\n")
    return build


# ── mutators: one per planted violation ─────────────────────────────────────
def _run_dir(factory_root):
    return Path(factory_root) / "runs" / SLUG


def plant_mixed_org_row(factory_root):
    """Catch #1a: a row from the excluded org leaks into the selection pool."""
    path = _run_dir(factory_root) / "corpus" / "inputs" / "selection-pool.json"
    pool = json.loads(path.read_text())
    bad = _pool_row(99, "2026-07-20", "winback_enrollment")
    bad["account_id"] = EXCLUDED_ORG_PREFIX + "-account-99"
    bad["read_tools"] = [EXCLUDED_TOOL]
    pool["action_pool"].append(bad)
    path.write_text(json.dumps(pool))


def plant_contaminated_anchor(factory_root, claimed=352):
    """Catch #1b: the dose anchor claims a night count the org's own pool
    cannot produce (the mixed-org derivation signature)."""
    path = _run_dir(factory_root) / "corpus" / "BUILD-SPEC.md"
    spec = path.read_text().replace(
        "07-20 has 5 real Claude actions",
        f"07-20 has {claimed} real Claude actions")
    path.write_text(spec)


def plant_unfillable_class(factory_root):
    """Catch #3a: the plan doses a class the pool has zero real rows for
    (admissible class — isolates fillability from admissibility)."""
    path = _run_dir(factory_root) / "corpus" / "BUILD-SPEC.md"
    spec = path.read_text().replace(
        "(winback 100%)", "(winback 50%, email 50%)")
    path.write_text(spec)


def plant_inadmissible_class(factory_root):
    """Catch #3b: the plan doses a class the live contract forbids — and the
    pool HAS rows for it (isolates admissibility from fillability)."""
    inputs = _run_dir(factory_root) / "corpus" / "inputs"
    for name in ("selection-pool.json", "train-pool.json"):
        pool = json.loads((inputs / name).read_text())
        pool["action_pool"].extend(
            _pool_row(200 + i, "2026-07-20", "custom") for i in range(4))
        (inputs / name).write_text(json.dumps(pool))
    path = _run_dir(factory_root) / "corpus" / "BUILD-SPEC.md"
    path.write_text(path.read_text().replace(
        "(winback 100%)", "(winback 50%, custom 50%)"))


def plant_anomalous_night(factory_root):
    """LIN-POOL-03: an evidence-doc-named anomalous window sits in the pool."""
    path = _run_dir(factory_root) / "audit" / "exclusions.yaml"
    path.write_text(path.read_text().replace(
        "anomalous_nights: []",
        'anomalous_nights:\n  - night: "2026-07-21"\n    reason: stuck runs'))


def plant_untraceable_corpus_row(factory_root):
    """LIN-SRC-02: a corpus record citing a source row no pool contains."""
    write_corpus(factory_root, source_row_id="ghost-row-1")


def plant_hazard_without_countermeasure(factory_root):
    """CMP-FAIL-02: the E21 staged-evidence posture with the twins line gone."""
    path = _run_dir(factory_root) / "corpus" / "BUILD-SPEC.md"
    spec = path.read_text().replace(
        "every staged-evidence\nselection gets its must-call twin >=1:1 (D1).",
        "no pairing required.")
    path.write_text(spec)


def plant_many_recipe_changes(factory_root):
    """CMP-REC-01: five recipe variables changed at once vs the predecessor
    digest (the v4 attribution failure shape)."""
    path = Path(factory_root) / "configs" / f"{SLUG}.yaml"
    text = (path.read_text()
            .replace("base_model: accounts/x/models/base-27b",
                     "base_model: accounts/x/models/other-70b")
            .replace("lora_rank: 32", "lora_rank: 64")
            .replace("epochs: 1", "epochs: 2")
            .replace("learning_rate: 3.0e-6", "learning_rate: 1.0e-5")
            .replace("replay_pct: 15", "replay_pct: 40")
            .replace("real_trajectories_pct: 50", "real_trajectories_pct: 25"))
    path.write_text(text)


def plant_stale_census(factory_root):
    """CMP-CENSUS-04: the config census is far off the live pool count."""
    path = Path(factory_root) / "configs" / f"{SLUG}.yaml"
    path.write_text(path.read_text().replace(
        "reviews_with_trajectory_id: 6", "reviews_with_trajectory_id: 1"))


def plant_uncited_dose_row(factory_root):
    path = _run_dir(factory_root) / "corpus" / "BUILD-SPEC.md"
    path.write_text(path.read_text().replace(
        "| S-act (D4) | ~4 (2x mean nightly miss), floor 2/class | split per D5 |",
        "| S-act | ~4, floor 2/class | trust me |"))


def plant_stale_envelope_pull(factory_root):
    """PAR-AGE-04: the envelope pull predates the run's own S3 export."""
    path = _run_dir(factory_root) / "corpus" / "inputs" / "envelope.json"
    env = json.loads(path.read_text())
    env["pulled_at"] = "2026-07-30T00:00:00+00:00"
    path.write_text(json.dumps(env))
