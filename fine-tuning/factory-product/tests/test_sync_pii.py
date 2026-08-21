"""T11 — factory_sync.project_stage_report: the PII-negative test PRRules.md rule 6
calls "release-blocking." PLAN.md §2.10 is an ALLOW-list (counts, verdicts, cost,
hashes, model ids, durations, L-/E- numbers) — everything else is dropped, on
purpose, even if it looks harmless (account names, transcript text, dossier
content, corpus examples, file paths embedding a customer name).

Per PRTests.md's own instruction, the meta-test at the bottom of this file was run
once against a temporarily-unfiltered version of `project_stage_report` (a stub
that returned `dict(report)` unchanged) to confirm the PII fixture test actually
goes RED without the allow-list filter — see PRDone.md's T11 task log entry for
that evidence. It is NOT left in as a permanent skip/xfail; the real, permanent
tests below are what CI runs.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

from factory_sync import SAFE_REPORT_KEYS, project_stage_report  # noqa: E402

FAKE_ACCOUNT_NAME = "Acme Rocketry Corp"
FAKE_CONTACT = "John Q. Smith"
FAKE_DOSSIER_TEXT = "customer disclosed their internal SKU pricing sheet and said 'call John at 555-0100'"
FAKE_CORPUS_EXAMPLE = "Q: What did Acme Rocketry order last week? A: 40 units of part #A-772, billed to John Q. Smith"


def test_pii_fixture_fields_never_survive_the_projection():
    """A fixture stage report carrying a fake account/contact name, dossier text,
    and a corpus example under plausible-but-not-allow-listed key names — none of
    those strings may appear anywhere in the projected payload.
    """
    dirty_report = {
        "status": "ok",
        "dry_run": True,
        # Realistic-looking but NOT allow-listed keys — this is exactly the shape
        # a `--live` export or a future stage could plausibly emit.
        "inputs": {
            "config_book": {"accounts": 42},
            "cross_check": {"account_name": FAKE_ACCOUNT_NAME, "contact": FAKE_CONTACT},
        },
        "dossier_excerpt": FAKE_DOSSIER_TEXT,
        "corpus_sample": FAKE_CORPUS_EXAMPLE,
        "stdout_tail": f"exported dossier for {FAKE_ACCOUNT_NAME} ({FAKE_CONTACT})",
        "stderr_tail": "",
        "command": f"npx tsx export.ts --org acme-rocketry --contact '{FAKE_CONTACT}'",
    }
    projected = project_stage_report(dirty_report)
    dumped = str(projected)
    for forbidden in (FAKE_ACCOUNT_NAME, FAKE_CONTACT, FAKE_DOSSIER_TEXT, FAKE_CORPUS_EXAMPLE):
        assert forbidden not in dumped, f"PII leaked through the projection: {forbidden!r}"
    # And the projection actually kept the one legitimately-safe field present.
    assert projected == {"status": "ok", "dry_run": True}


def test_allow_listed_only_report_passes_through_unchanged():
    """A report containing ONLY allow-listed fields (counts, verdicts, cost,
    hashes, model ids, durations, L-/E- numbers) must pass through unchanged —
    the filter must not be so aggressive it drops legitimate data.
    """
    clean_report = {
        "status": "ok",
        "dry_run": False,
        "exit_code": 0,
        "findings": {
            "tier_computed": "A",
            "tier_match": True,
            "fact_bearing_share": 0.42,
            "qa_dose_estimate_pairs": 1680,
        },
        "recommendation": "FULL",
        "decision_request": {
            "recommendation": "approve",
            "cost": {"total_projection_usd": 14.2, "sft_estimate_usd": 8.4},
            "assumptions": ["epochs = 3 (frozen recipe)"],
            "evidence": ["company-brain/grand-steel/readiness.md"],
        },
        "next": "launch",
        "todo_v1": "generalize the v5 generator chain",
    }
    projected = project_stage_report(clean_report)
    assert projected == clean_report


def test_every_allow_listed_key_is_intentional_not_a_placeholder():
    # Guards against someone widening SAFE_REPORT_KEYS to something like "*" or
    # adding an obviously-risky free-text key (e.g. "stdout_tail") by accident.
    forbidden_keys = {"stdout_tail", "stderr_tail", "command", "cwd", "inputs", "cross_check"}
    assert not (SAFE_REPORT_KEYS & forbidden_keys)


def test_unknown_report_is_entirely_dropped_not_partially_leaked():
    """A report shaped nothing like any real factory.py stage (e.g. a future,
    not-yet-allow-listed stage) projects to an empty dict rather than guessing at
    which fields might be safe — the allow-list's whole point.
    """
    from_the_future = {"brand_new_field": "could be anything, including PII"}
    assert project_stage_report(from_the_future) == {}
