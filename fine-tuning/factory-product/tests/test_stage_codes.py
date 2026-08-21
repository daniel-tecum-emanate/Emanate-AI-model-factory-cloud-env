"""C0 (factory-graph-pr2, 2026-07-28): the displayed stage code is canonical, not positional.

`--status` used to print `f"S{i}"` from `enumerate(STAGES, 1)`. The canonical
sequence is not positional — the spend gate is `S6g` at position 7 — so every
stage after it printed one number too high. Concretely, it displayed:

    S 9 eval          <- but S9 is canonically the SHIP gate
    S10 ship [HUMAN GATE]

and therefore showed the three human-held gates at S2/S7/S10 when every planning
doc, the charter, and the kickoff prompt state them as **S2/S6g/S9**. No logic
keyed off the printed number (gate handling matches on stage *name*, and the
ledger already emitted canonical codes — a live row carried `node_id: "S2"`), so
nothing was corrupted. It misinformed the human, at a human-held gate, about
which stage they were approving. That is the surface where being wrong is worst.

The load-bearing test here is `test_every_stage_has_a_code`: without it, adding a
stage silently reintroduces the bug for every stage after the new one.
"""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory  # noqa: E402
import factory_stages  # noqa: E402
import factory_sync  # noqa: E402


def test_there_is_exactly_one_definition_of_the_vocabulary():
    """C0 collapsed two copies into one. `factory.py` and `factory_sync.py` must
    hold the *same object*, not equal copies — equal copies are what drift."""
    assert factory.STAGE_CODES is factory_stages.STAGE_CODES
    assert factory_sync.STAGE_CODES is factory_stages.STAGE_CODES
    assert factory.STAGES is factory_stages.STAGES
    assert factory_sync.HUMAN_GATE_STAGES is factory_stages.HUMAN_GATES


def test_every_stage_has_a_code():
    """A stage with no canonical code would KeyError at display time — which is
    the correct, loud failure. This test makes it fail at test time instead."""
    missing = [s for s in factory.STAGES if s not in factory.STAGE_CODES]
    assert missing == [], f"stages with no canonical code: {missing}"


def test_no_stray_codes():
    """The mapping must not describe stages that do not exist — a code for a
    removed stage reads as documentation of a stage that is still there."""
    stray = [s for s in factory.STAGE_CODES if s not in factory.STAGES]
    assert stray == [], f"codes for non-existent stages: {stray}"


def test_the_three_human_gates_are_S2_S6g_S9():
    """The invariant every doc states (`SCOPE.md` #14). If this ever fails, the
    displayed gate numbering has drifted from the vocabulary again."""
    gate_codes = sorted(factory.STAGE_CODES[s] for s in factory.HUMAN_GATES)
    assert gate_codes == ["S2", "S6g", "S9"]


@pytest.mark.parametrize(
    "stage,code",
    [
        ("census", "S1"),
        ("project", "S6"),
        # Position 7. Everything below here is where positional numbering broke.
        ("launch", "S6g"),
        ("train", "S7"),
        ("eval", "S8"),
        ("ship", "S9"),
        ("activate", "S10"),
        ("retrain", "S11"),
    ],
)
def test_canonical_codes_match_the_module_docstring(stage, code):
    assert factory.STAGE_CODES[stage] == code


def test_code_is_not_the_positional_index_after_the_spend_gate():
    """Guards the specific regression: re-deriving the code from `enumerate` would
    make every one of these equal, and they must not be."""
    positional = {s: f"S{i}" for i, s in enumerate(factory.STAGES, 1)}
    diverged = [s for s in factory.STAGES if positional[s] != factory.STAGE_CODES[s]]
    assert diverged == ["launch", "train", "eval", "ship", "activate", "retrain"], (
        "the set of stages where canonical != positional changed; if a stage was "
        "added or the S6g code was renumbered, update this expectation deliberately"
    )


def test_report_filename_prefix_stays_positional(tmp_path, monkeypatch):
    """Rule 14: C0 fixed the *display* only. The `NN-<stage>-report.json` prefix is
    a sort key, and report files already exist on disk under it — renaming it to
    match the canonical codes would orphan every prior run's artifacts to fix a
    cosmetic mismatch. `ship` is position 10, and its report must stay `10-`.
    """
    assert factory.STAGES.index("ship") + 1 == 10
    assert factory.STAGE_CODES["ship"] == "S9"


def test_status_output_renders_canonical_codes(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(factory, "run_dir", lambda slug: tmp_path)
    factory.print_status("qa-dryrun-synth")
    out = capsys.readouterr().out
    assert "S6g launch" in out
    assert "S9 ship" in out
    assert "S10 activate" in out
    # The exact former bug: `eval` must not be displayed as S9.
    assert "S9 eval" not in out
