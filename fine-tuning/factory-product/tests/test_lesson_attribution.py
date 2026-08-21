"""B2 (factory-observability-v1) — `attribute_lesson_to_run` wired into the S9 ship path.

`attribute_lesson_to_run` shipped in factory-graph-pr1 with **zero callers**
(PRContext.md V3). These tests prove the call site now exists and threads the right
arguments. They deliberately do NOT re-test the first-mint-wins guard itself —
`tests/test_lessons_sync.py` already covers that, and duplicating it here would
couple this file to that function's internals.

`PRContext.md` B2 allowed this task to narrow if `stage_ship`'s payload carried no
lesson code today. It does not — verified and asserted below by
`test_the_ship_report_still_carries_no_lesson_code_of_its_own`, which is what makes
the `--lesson` design a finding rather than a preference.
"""

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory  # noqa: E402
import lessons_sync  # noqa: E402
from supabase_rest import SupabaseRestError  # noqa: E402

SLUG = "qa-dryrun-synth"
RUN_ID = "dddddddd-0000-0000-0000-000000000009"
SYNC_URL = "http://fake.invalid"


def _args(**overrides):
    base = {
        "org": SLUG,
        "stage": "ship",
        "status": False,
        "dry_run": False,
        "approved": True,
        "live": False,
        "sync_url": SYNC_URL,
        "lesson": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture
def attributed():
    """Records (code, run_id, url) per call, and reports success like a real
    first-mint would."""
    calls = []

    def fake(code, run_id, url=None, service_role_key=None):
        calls.append({"code": code, "run_id": run_id, "url": url})
        return True

    with patch.object(lessons_sync, "attribute_lesson_to_run", side_effect=fake), patch.object(
        factory, "_run_id_for_decisions", return_value=RUN_ID
    ):
        yield calls


# ---------------------------------------------------------------------------
# The call site exists and threads the right arguments
# ---------------------------------------------------------------------------


def test_a_ship_approval_naming_a_lesson_calls_attribute_once_with_that_code_and_run(attributed):
    result = factory._attribute_lessons(SLUG, _args(lesson=["L41"]), "approved")
    assert result == ["L41"]
    assert attributed == [{"code": "L41", "run_id": RUN_ID, "url": SYNC_URL}]


def test_a_real_no_go_also_attributes_because_a_no_go_is_the_higher_signal_outcome(attributed):
    """RETRAIN-AND-META-LEARNING.md §1: "why the last one didn't ship" is the
    highest-signal input a retrain has. A NO-GO that taught something must be able
    to say so."""
    assert factory._attribute_lessons(SLUG, _args(approved=False, lesson=["E12"]), "no_go") == ["E12"]
    assert attributed[0]["code"] == "E12"


def test_multiple_lessons_are_each_attributed_once(attributed):
    factory._attribute_lessons(SLUG, _args(lesson=["L41", "E12", "L7"]), "approved")
    assert [c["code"] for c in attributed] == ["L41", "E12", "L7"]


def test_a_repeated_code_is_de_duplicated_rather_than_attributed_twice(attributed):
    """`--lesson L41 --lesson L41` is an operator typo, not two attributions."""
    factory._attribute_lessons(SLUG, _args(lesson=["L41", "L41", "E12"]), "approved")
    assert [c["code"] for c in attributed] == ["L41", "E12"]


def test_every_call_carries_the_same_run_id_and_it_is_never_minted_here(attributed):
    """Invariant 8 / `Lesson_RetrainRunIdReuse`: `_run_id_for_decisions` never mints
    a run id — `get_or_create_run_id` can legitimately START a new run when the
    prior one is terminal, and a logging call must never trigger that.
    """
    with patch.object(factory, "_run_id_for_decisions", return_value=RUN_ID) as run_id_fn:
        factory._attribute_lessons(SLUG, _args(lesson=["L1", "L2"]), "approved")
    assert run_id_fn.call_count == 1
    assert {c["run_id"] for c in attributed} == {RUN_ID}


def test_get_or_create_run_id_is_never_called_from_this_path():
    import factory_sync

    with patch.object(factory_sync, "get_or_create_run_id", side_effect=AssertionError("must not mint a run id")):
        with patch.object(lessons_sync, "attribute_lesson_to_run", return_value=True):
            factory._attribute_lessons(SLUG, _args(lesson=["L41"]), "approved")


# ---------------------------------------------------------------------------
# Does not fight first-mint-wins
# ---------------------------------------------------------------------------


def test_a_code_already_attributed_elsewhere_is_reported_but_not_retried(capsys):
    """The guard returns False; the call site must accept that as a normal outcome,
    not retry, not raise, and not report success. This proves the CALL SITE
    behaves — `test_lessons_sync.py` owns proving the guard itself.
    """
    with patch.object(lessons_sync, "attribute_lesson_to_run", return_value=False) as fn, patch.object(
        factory, "_run_id_for_decisions", return_value=RUN_ID
    ):
        assert factory._attribute_lessons(SLUG, _args(lesson=["L41"]), "approved") == []
    assert fn.call_count == 1
    assert "not attributed" in capsys.readouterr().out


def test_the_call_site_never_writes_statement_severity_or_mechanical_check():
    """PRRules rule 3: this is additive metadata on an existing row, never a second
    writer of the lesson's own content. The only keyword arguments it may pass are
    the code, the run id, and connection details.
    """
    with patch.object(lessons_sync, "attribute_lesson_to_run", return_value=True) as fn, patch.object(
        factory, "_run_id_for_decisions", return_value=RUN_ID
    ):
        factory._attribute_lessons(SLUG, _args(lesson=["L41"]), "approved")
    _, kwargs = fn.call_args
    assert set(kwargs) <= {"url", "service_role_key"}


# ---------------------------------------------------------------------------
# When it must NOT attribute
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["gate_pending", "REFUSED", "blocked", "error"])
def test_no_attribution_when_no_human_decision_was_actually_made(status, capsys):
    """`gate_pending` means nobody has decided; `REFUSED` is the CLI's own checklist
    logic with no human involved. Neither has taught anything to attribute.
    """
    with patch.object(lessons_sync, "attribute_lesson_to_run", side_effect=AssertionError("must not attribute")):
        assert factory._attribute_lessons(SLUG, _args(lesson=["L41"]), status) == []
    assert "no decision has been made" in capsys.readouterr().out


def test_dry_run_attributes_nothing_but_says_what_it_would_have_done(capsys):
    with patch.object(lessons_sync, "attribute_lesson_to_run", side_effect=AssertionError("dry-run must not push")):
        assert factory._attribute_lessons(SLUG, _args(dry_run=True, lesson=["L41"]), "approved") == []
    assert "would attribute" in capsys.readouterr().out


def test_no_lesson_flag_is_a_silent_no_op(capsys):
    with patch.object(lessons_sync, "attribute_lesson_to_run", side_effect=AssertionError("must not attribute")):
        assert factory._attribute_lessons(SLUG, _args(lesson=None), "approved") == []
    assert capsys.readouterr().out == ""


def test_no_sync_url_skips_rather_than_failing_the_gate(capsys):
    with patch.object(lessons_sync, "attribute_lesson_to_run", side_effect=AssertionError("must not attribute")):
        assert factory._attribute_lessons(SLUG, _args(sync_url=None, lesson=["L41"]), "approved") == []
    assert "no run id available" in capsys.readouterr().out


def test_no_run_id_skips_rather_than_failing_the_gate():
    with patch.object(factory, "_run_id_for_decisions", return_value=None), patch.object(
        lessons_sync, "attribute_lesson_to_run", side_effect=AssertionError("must not attribute")
    ):
        assert factory._attribute_lessons(SLUG, _args(lesson=["L41"]), "approved") == []


# ---------------------------------------------------------------------------
# Fail-soft: a transport error must not break a human gate
# ---------------------------------------------------------------------------


def test_a_supabase_failure_does_not_raise_past_the_call_site(capsys):
    """`attribute_lesson_to_run` raises by design (it is normally a maintenance
    job). Reached from S9 it must not: the ship decision is already made and
    written to disk, and attribution is additive metadata. Same fail-soft reasoning
    `_gate_rejection_note` states for this same stage.
    """
    with patch.object(lessons_sync, "attribute_lesson_to_run", side_effect=SupabaseRestError("503")), patch.object(
        factory, "_run_id_for_decisions", return_value=RUN_ID
    ):
        assert factory._attribute_lessons(SLUG, _args(lesson=["L41"]), "approved") == []
    assert "non-fatal" in capsys.readouterr().out


def test_one_failing_code_does_not_stop_the_others(attributed):
    seen = []

    def flaky(code, run_id, url=None, service_role_key=None):
        seen.append(code)
        if code == "L41":
            raise SupabaseRestError("503")
        return True

    with patch.object(lessons_sync, "attribute_lesson_to_run", side_effect=flaky), patch.object(
        factory, "_run_id_for_decisions", return_value=RUN_ID
    ):
        assert factory._attribute_lessons(SLUG, _args(lesson=["L41", "E12"]), "approved") == ["E12"]
    assert seen == ["L41", "E12"]


# ---------------------------------------------------------------------------
# The finding that justifies the `--lesson` design
# ---------------------------------------------------------------------------


def test_the_ship_report_still_carries_no_lesson_code_of_its_own():
    """PRContext.md B2's narrowing condition, asserted rather than asserted-in-prose.

    If a future change starts putting an `L<N>`/`E<N>` code into `stage_ship`'s
    report or `decision_request`, this test fails and the narrowing no longer
    applies — at which point automatic extraction becomes legitimate, because there
    would finally be a real field to read it from instead of prose to guess at.
    """
    source = (FACTORY_DIR / "factory.py").read_text()
    start = source.index("def stage_ship(")
    body = source[start : source.index("\ndef ", start + 10)]
    payload_lines = [
        line for line in body.splitlines()
        if '"' in line and not line.strip().startswith("#")
    ]
    joined = "\n".join(payload_lines)
    assert "lesson" not in joined.lower() or "--lesson" in joined, (
        "stage_ship's payload now mentions a lesson — re-read PRContext.md B2's narrowing clause"
    )


def test_the_lesson_flag_is_repeatable_on_the_real_parser():
    parsed = factory.build_parser().parse_args([SLUG, "--stage", "ship", "--lesson", "L41", "--lesson", "E12"])
    assert parsed.lesson == ["L41", "E12"]


def test_the_lesson_flag_defaults_to_none_not_an_empty_list():
    """`action="append"` with no default gives None, which `_attribute_lessons`
    treats as "not asked for" — distinct from an explicit empty list."""
    assert factory.build_parser().parse_args([SLUG, "--stage", "ship"]).lesson is None
