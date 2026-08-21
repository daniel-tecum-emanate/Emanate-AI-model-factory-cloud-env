"""B1 (factory-observability-v1) — dispatched node-agents reach the product.

Since factory-graph-pr1/pr2 the factory has dispatched real headless Claude Code
sessions with real cost, model and session-id data, and **none of it reached
Supabase** (PRContext.md V1). This file covers the write path that closes that gap.

Every assertion here is on **pushed row content**, never on "the function returned
without raising" — LESSON-016, and doubly so because this push path is fail-open by
design, so a completely broken projection would otherwise leave a green suite.

`PRTests.md` marks this whole file release-blocking. Its named cases, and where:

  success path .................. test_a_successful_dispatch_pushes_one_agent_row_and_one_event
  failure path .................. test_a_failed_dispatch_still_pushes_both_rows*
  detail allow-list only ........ test_detail_drops_any_key_outside_the_allow_list*
  no raw stderr beyond the cap .. test_stderr_tail_is_capped_at_the_shared_constant*
  fail-open ..................... test_a_supabase_outage_does_not_raise_past_dispatch_node*
  D1 match — found .............. test_ledger_match_finds_the_row_and_yields_real_cost_and_session
  D1 match — no file ............ test_no_ledger_file_degrades_to_none
  D1 match — no matching row .... test_no_matching_label_degrades_to_none_and_picks_nothing
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory_node_run as fnr  # noqa: E402
import factory_sync as fs  # noqa: E402
from supabase_rest import SupabaseRestError  # noqa: E402

from _migration import columns_for as _columns_for  # noqa: E402
from _migration import read_migration  # noqa: E402

RUN_ID = "dddddddd-0000-0000-0000-000000000001"
GRAPH_ID = "fine-tune-factory-v1"
SLUG = "qa-dryrun-synth"
NODE_ID = "S4:corpus-planner"


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _tier2_spec(slug, role_family="builder", **runtime_overrides):
    """A minimal synthetic AgentSpec whose `runtime` omits `executor` — i.e.
    tier2, the still-current default — so a test of the tier2 dispatch ->
    sync-push pipeline does not depend on which real on-disk spec happens to
    use tier2 today. `corpus-planner` moved to `executor: self_dispatch`
    (model-factory-cloud-dispatch-v1); this fixture is what several tests that
    used to piggyback on it for exactly this purpose were redirected to.
    Mirrors `_builder_spec()`'s shape in `test_self_dispatch.py`.
    """
    return {
        "slug": slug,
        "role_family": role_family,
        "mission": "fixture spec for tier2 dispatch-sync tests",
        "read_first": [],
        "mandate": [{"task": "do the thing", "stop_or_ask": "ask if unsure"}],
        "laws": ["single-writer discipline"],
        "done_when": "done",
        "final_response_shape": "a summary",
        "runtime": {"budget_usd": 2.00, **runtime_overrides},
    }


@pytest.fixture(scope="module")
def migration_text():
    return read_migration()


class _Capture:
    """Records what would have been pushed, so assertions can be made on row
    CONTENT. `_upsert_agent_row` is patched rather than `select`/`insert`/`update`
    individually because the select-then-write dance inside it is existing,
    already-tested behaviour and not what this file is about.
    """

    def __init__(self):
        self.agents = []
        self.events = []

    def upsert_agent(self, row, **kwargs):
        self.agents.append(row)

    def insert(self, table, rows, **kwargs):
        if table == "factory_run_events":
            self.events.extend(rows)


@pytest.fixture
def cap():
    capture = _Capture()
    with patch.object(fs, "_upsert_agent_row", side_effect=capture.upsert_agent), patch.object(
        fs, "insert", side_effect=capture.insert
    ):
        yield capture


def _write_ledger(directory, rows, when=None):
    """A ledger file in `tier2_run.sh`'s exact on-disk shape and location."""
    stamp = (when or datetime.now()).strftime("%Y-%m-%d")
    path = Path(directory) / f"{stamp}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _tier2_row(node_id=NODE_ID, cost_usd=0.4231, session_id="sess-abc123", ts=None, **extra):
    """One `tier2` ledger row, field for field as `scripts/tier2_run.sh` writes it
    (that script's lines 153-159, read rather than guessed)."""
    row = {
        "ts": (ts or datetime.now().astimezone()).isoformat(timespec="seconds"),
        "event": "tier2",
        "label": fs.tier2_label_for(node_id),
        "model": "sonnet",
        "ok": True,
        "duration_s": 91,
        "max_turns": 30,
        "budget_usd": 2.0,
        "cost_usd": cost_usd,
        "usage": {
            "input_tokens": 3287,
            "cache_creation_input_tokens": 26629,
            "cache_read_input_tokens": 23624,
            "output_tokens": 3347,
            "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
            "service_tier": "standard",
            "inference_geo": "not_available",
            "iterations": [{"input_tokens": 1}, {"input_tokens": 2}, {"input_tokens": 3}],
        },
        "session_id": session_id,
        "error": None,
        "role": "tier2",
    }
    row.update(extra)
    return row


# ===========================================================================
# The label contract between the two modules
# ===========================================================================


def test_the_matchers_label_is_byte_identical_to_what_build_tier2_argv_passes(tmp_path):
    """The whole of D1 rests on these two agreeing. If `build_tier2_argv`'s label
    format ever changes, this fails here rather than silently producing NULL costs
    forever.
    """
    spec = fnr.load_spec("corpus-planner")
    argv = fnr.build_tier2_argv(spec, NODE_ID, tmp_path / "p.md")
    assert argv[argv.index("--label") + 1] == fs.tier2_label_for(NODE_ID)


def test_the_label_replaces_the_colon_because_it_becomes_a_filename():
    assert fs.tier2_label_for("S4:corpus-planner") == "factory-node-S4-corpus-planner"
    assert ":" not in fs.tier2_label_for("S8:grader-auditor")


# ===========================================================================
# D1 — ledger matching. Release-blocking: all three degrade cases.
# ===========================================================================


def test_ledger_match_finds_the_row_and_yields_real_cost_and_session(tmp_path):
    now = datetime.now().astimezone()
    path = _write_ledger(tmp_path, [_tier2_row(ts=now)])
    row = fs.match_ledger_row(path, NODE_ID, window_start=now - timedelta(seconds=30), window_end=now)
    assert row is not None
    assert row["cost_usd"] == 0.4231
    assert row["session_id"] == "sess-abc123"


def test_no_ledger_file_degrades_to_none(tmp_path):
    """`cost_usd` must be NULL, never 0 (PRRules invariant 15) — and no exception,
    because this runs in the `finally` of a paid dispatch."""
    row = fs.match_ledger_row(tmp_path / "does-not-exist.jsonl", NODE_ID)
    assert row is None


def test_no_matching_label_degrades_to_none_and_picks_nothing(tmp_path):
    """A ledger full of OTHER dispatches must not yield one of them. This is the
    case where "pick the first tier2 row you find" would have looked like it worked.
    """
    path = _write_ledger(
        tmp_path,
        [
            _tier2_row(node_id="S5:preflight-linter", cost_usd=9.99, session_id="wrong-1"),
            _tier2_row(node_id="S8:grader-auditor", cost_usd=8.88, session_id="wrong-2"),
            {"ts": datetime.now().astimezone().isoformat(), "event": "tier1", "label": "something", "cost_usd": 7.77},
        ],
    )
    assert fs.match_ledger_row(path, NODE_ID) is None


def test_a_row_outside_the_window_is_not_matched(tmp_path):
    """Same node, but yesterday's attempt. Attributing its cost to today's dispatch
    is exactly the silent misattribution the window exists to prevent."""
    now = datetime.now().astimezone()
    stale = now - timedelta(hours=6)
    path = _write_ledger(tmp_path, [_tier2_row(ts=stale, cost_usd=5.55)])
    assert fs.match_ledger_row(path, NODE_ID, window_start=now - timedelta(seconds=10), window_end=now) is None


def test_two_matching_rows_in_the_window_degrade_to_none_rather_than_picking_one(tmp_path, caplog):
    """PRRules invariant 15's ambiguous case, stated explicitly: the tie-break rule
    is that there is NO tie-break. A cost attributed to the wrong attempt is worse
    than a missing one, so this refuses — and says so in the log.
    """
    now = datetime.now().astimezone()
    path = _write_ledger(
        tmp_path,
        [_tier2_row(ts=now, cost_usd=1.11, session_id="a"), _tier2_row(ts=now, cost_usd=2.22, session_id="b")],
    )
    with caplog.at_level("WARNING"):
        assert fs.match_ledger_row(path, NODE_ID, window_start=now - timedelta(seconds=5), window_end=now) is None
    assert "2 ledger rows match" in caplog.text


def test_second_precision_truncation_does_not_lose_the_match(tmp_path):
    """`tier2_run.sh` writes `timespec="seconds"`, so a dispatch begun at
    10:00:00.7 produces a row stamped 10:00:00 — BEFORE its own window start.
    Without the grace window this real, correct match would be dropped on
    roughly 7 dispatches in 10.
    """
    start = datetime.now().astimezone().replace(microsecond=700000)
    truncated = start.replace(microsecond=0)
    path = _write_ledger(tmp_path, [_tier2_row(ts=truncated)])
    assert fs.match_ledger_row(path, NODE_ID, window_start=start, window_end=start + timedelta(seconds=1)) is not None


def test_malformed_ledger_lines_are_skipped_not_fatal(tmp_path):
    now = datetime.now().astimezone()
    path = Path(tmp_path) / f"{now.strftime('%Y-%m-%d')}.jsonl"
    path.write_text("not json at all\n\n" + json.dumps(_tier2_row(ts=now)) + "\n{ broken\n", encoding="utf-8")
    assert fs.match_ledger_row(path, NODE_ID, window_start=now - timedelta(seconds=5), window_end=now) is not None


def test_a_tier2_row_with_an_unparseable_ts_is_not_treated_as_in_window(tmp_path):
    now = datetime.now().astimezone()
    path = _write_ledger(tmp_path, [_tier2_row(ts=now) | {"ts": "not-a-timestamp"}])
    assert fs.match_ledger_row(path, NODE_ID, window_start=now - timedelta(seconds=5), window_end=now) is None
    # ...but with no window asked for, it still matches on label alone.
    assert fs.match_ledger_row(path, NODE_ID) is not None


def test_ledger_path_honours_the_test_isolation_env_var(monkeypatch, tmp_path):
    """Same precedence as `factory_decisions._ledger_path`. A second resolution
    rule here would defeat the isolation `conftest.py` installs."""
    monkeypatch.setenv("FACTORY_LEDGER_DIR", str(tmp_path))
    assert fs.ledger_path_for().parent == tmp_path
    assert fs.ledger_path_for(ledger_dir="/explicit").parent == Path("/explicit")


def test_ledger_path_never_creates_the_directory(tmp_path):
    """A read path. A missing ledger must degrade to None, not be papered over."""
    missing = tmp_path / "nope"
    fs.ledger_path_for(ledger_dir=missing)
    assert not missing.exists()


# ===========================================================================
# D2 — the coarse usage projection
# ===========================================================================


def test_usage_projection_keeps_only_coarse_counts():
    projected = fs.project_usage(_tier2_row()["usage"])
    assert projected == {
        "tokens_in": 3287,
        "tokens_out": 3347,
        "cache_read_tokens": 23624,
        "cache_creation_tokens": 26629,
        "turns_used": 3,
    }


def test_usage_projection_drops_the_provider_metadata_and_the_iterations_array():
    """A real `usage` block carries `service_tier`, `inference_geo`,
    `server_tool_use` and a full per-iteration array (verified against
    ledger/2026-07-28.jsonl). None of it belongs in a run-detail payload, and
    passing the block through verbatim would have shipped all of it.
    """
    projected = fs.project_usage(_tier2_row()["usage"])
    for leaked in ("service_tier", "inference_geo", "server_tool_use", "iterations", "cache_creation"):
        assert leaked not in projected


def test_usage_projection_returns_none_not_empty_dict_when_there_is_nothing():
    """`None` and `{}` mean different things: "no usage data" vs "zero tokens".
    Same reasoning PR 1 used for emitting no `scores` key rather than an empty one.
    """
    assert fs.project_usage(None) is None
    assert fs.project_usage({}) is None
    assert fs.project_usage({"service_tier": "standard"}) is None
    assert fs.project_usage("not a dict") is None


def test_turns_used_is_absent_rather_than_inferred_from_the_turn_CAP():
    """`tier2_run.sh` records `max_turns` (the cap it enforced) and never the turns
    consumed. Reading the cap as usage would report every cheap 2-turn node as
    having burned its full 30-turn budget.
    """
    projected = fs.project_usage({"input_tokens": 10, "output_tokens": 20})
    assert "turns_used" not in projected
    assert projected == {"tokens_in": 10, "tokens_out": 20}


# ===========================================================================
# detail allow-list — RELEASE-BLOCKING
# ===========================================================================


def test_detail_drops_any_key_outside_the_allow_list():
    """A synthetic extra key on the result object must be dropped, not passed
    through — the property that makes this safe against future fields nobody has
    thought of yet, which is the whole point of an allow-list over a block-list.
    """
    detail = fs.project_dispatch_detail(
        {
            "spec_slug": "corpus-planner",
            "model": "sonnet",
            "raw_tool_calls": [{"name": "Read", "args": {"path": "/Users/danieltecum/secrets.txt"}}],
            "stdout": "the entire on-disk report body",
            "allowed_tools": ["Read", "Bash"],
            "prompt_file": "factory-automation/factory/runs/x/prompts/y.md",
            "transcript": "customer conversation",
        }
    )
    assert set(detail) == {"spec_slug", "model"}


def test_every_documented_detail_key_actually_survives_the_allow_list():
    """The mirror of the test above: an allow-list that dropped a field the UI
    expects would be just as broken, and would present as an empty panel.
    """
    documented = {
        "spec_slug": "corpus-planner",
        "role_family": "builder",
        "model": "sonnet",
        "max_turns": 30,
        "budget_usd": 2.0,
        "cost_usd": 0.42,
        "verdict": "pass",
        "exit_code": 0,
        "final_response": "MIXTURE DECIDED: ...",
        "usage": {"tokens_in": 1},
        "stderr_tail": "warning: x",
        "ledger_session_id": "sess-1",
    }
    assert set(fs.project_dispatch_detail(documented)) == set(documented)
    assert set(documented) <= fs.SAFE_DISPATCH_DETAIL_KEYS


def test_none_valued_keys_are_omitted_rather_than_stored_as_json_null():
    detail = fs.project_dispatch_detail({"spec_slug": "x", "cost_usd": None, "usage": None, "verdict": None})
    assert detail == {"spec_slug": "x"}


def test_structured_pii_inside_an_allow_listed_value_is_scrubbed():
    """Layer 2. `final_response` and `stderr_tail` are agent-authored and
    subprocess-emitted text — neither has a reviewed shape the way a stage report's
    fields do, so the key allow-list alone is not enough here.
    """
    detail = fs.project_dispatch_detail(
        {
            "final_response": "contacted jane.doe@grandsteel.example at 415-555-0134",
            "stderr_tail": "ssn 123-45-6789 in row 4",
        }
    )
    assert "jane.doe@" not in detail["final_response"]
    assert "415-555-0134" not in detail["final_response"]
    assert "123-45-6789" not in detail["stderr_tail"]
    assert "[redacted-email]" in detail["final_response"]


# ===========================================================================
# stderr cap — RELEASE-BLOCKING (reuses the constant, never a new number)
# ===========================================================================


def test_stderr_tail_is_capped_at_the_shared_constant():
    detail = fs.project_dispatch_detail({"stderr_tail": "x" * 5000})
    assert len(detail["stderr_tail"]) == fs.STDERR_TAIL_MAX_CHARS


def test_the_dispatcher_and_the_sync_share_one_stderr_cap_constant():
    """`factory_node_run` imports the constant rather than repeating `500`, so the
    two cannot drift into disagreeing about what "the existing cap" is.
    """
    assert fnr.STDERR_TAIL_MAX_CHARS is fs.STDERR_TAIL_MAX_CHARS == 500


def test_the_cap_keeps_the_TAIL_not_the_head():
    """The tail is where a stack trace's actual error lives; truncating from the
    front would keep the useless preamble and drop the cause."""
    detail = fs.project_dispatch_detail({"stderr_tail": "PREAMBLE" + "x" * 5000 + "REAL_ERROR"})
    assert detail["stderr_tail"].endswith("REAL_ERROR")
    assert "PREAMBLE" not in detail["stderr_tail"]


def test_final_response_is_bounded_too():
    detail = fs.project_dispatch_detail({"final_response": "y" * 99999})
    assert len(detail["final_response"]) == fs.FINAL_RESPONSE_MAX_CHARS


# ===========================================================================
# Row projection — success and failure both push, with real content
# ===========================================================================

_SPEC = {"slug": "corpus-planner", "role_family": "builder", "mission": "Plan the corpus mixture"}


def _dispatch_result(ok=True, exit_code=0, verdict=None, stdout="MIXTURE DECIDED: 4 slices", stderr=None):
    return {
        "node_id": NODE_ID,
        "spec": "corpus-planner",
        "role_family": "builder",
        "runtime": {"model": "sonnet", "max_turns": 30, "budget_usd": 2.0},
        "dispatched": True,
        "ok": ok,
        "exit_code": exit_code,
        "verdict": verdict,
        "prompt_file": "factory-automation/factory/runs/qa-dryrun-synth/prompts/S4-corpus-planner.md",
        "stderr_tail": stderr,
        "final_response": stdout,
    }


def test_projection_sets_stage_which_is_what_distinguishes_it_from_a_heartbeat_row():
    """`project_agents` (interactive sessions) never sets `stage` because heartbeat
    has no stage column. A non-null `stage` therefore means "the factory dispatched
    this", and platform-alpha's AgentsTable relies on exactly that.
    """
    agent_row, _ = fs.project_node_agent(_dispatch_result(), RUN_ID, "S4", _SPEC)
    assert agent_row["stage"] == "S4"

    interactive_rows, _ = fs.project_agents(
        "## Active Sessions\n| session | goal | allowed_paths | risk | status | seq | last_update |\n"
        "|---|---|---|---|---|---|---|\n| X | work on qa-dryrun-synth | a | code | ACTIVE | 1 | now |\n",
        RUN_ID,
        SLUG,
    )
    assert interactive_rows and "stage" not in interactive_rows[0]


def test_projection_fills_the_columns_that_had_no_write_path_before_this_pr():
    """`factory_agents.stage`/`report_path`/`report_summary`/`cost_usd` were created
    in model-factory-v1 with no writer at all (V2, and that table's own doc says
    so). This is the test that they now have one.
    """
    agent_row, _ = fs.project_node_agent(
        _dispatch_result(), RUN_ID, "S4", _SPEC, ledger_row=_tier2_row()
    )
    assert agent_row["stage"] == "S4"
    assert agent_row["report_path"].endswith("S4-corpus-planner.md")
    assert agent_row["report_summary"] == "MIXTURE DECIDED: 4 slices"
    assert agent_row["cost_usd"] == 0.4231
    assert agent_row["ledger_session_id"] == "sess-abc123"


# ===========================================================================
# Tool-call correlation — Daniel asked for "tool calls", not just token counts
# ===========================================================================


def _tool_event(session_id="sess-abc123", files=None):
    return {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "event": "tool",
        "session_id": session_id,
        "files": files if files is not None else ["factory-automation/factory/runs/x/06-report.json"],
    }


def test_dispatched_row_carries_the_agents_real_tool_calls_not_a_hardcoded_zero():
    """The whole point of B1 is that a dispatched agent's work is visible. The
    matched tier2 row gives us its real `session_id`, and every ledger `tool`
    event carries one, so files_touched/commands_run must be REAL.
    """
    events = [
        _tool_event(files=["factory-automation/factory/a.json"]),
        _tool_event(files=["factory-automation/factory/b.json"]),
        _tool_event(files=["factory-automation/factory/a.json"]),  # dedup
    ]
    agent_row, _ = fs.project_node_agent(
        _dispatch_result(), RUN_ID, "S4", _SPEC,
        ledger_row=_tier2_row(), ledger_events=events,
    )
    assert agent_row["commands_run"] == 3
    assert agent_row["files_touched"] == [
        "factory-automation/factory/a.json",
        "factory-automation/factory/b.json",
    ]


def test_tool_calls_from_a_different_session_are_never_attributed_to_this_agent():
    """Several agents can be active on the same day, writing to the same ledger
    file. Correlation is by session_id, so another session's tool calls must not
    inflate this agent's count."""
    events = [
        _tool_event(session_id="sess-abc123", files=["factory-automation/mine.json"]),
        _tool_event(session_id="sess-SOMEONE-ELSE", files=["factory-automation/theirs.json"]),
    ]
    agent_row, _ = fs.project_node_agent(
        _dispatch_result(), RUN_ID, "S4", _SPEC,
        ledger_row=_tier2_row(), ledger_events=events,
    )
    assert agent_row["commands_run"] == 1
    assert agent_row["files_touched"] == ["factory-automation/mine.json"]


def test_no_ledger_match_means_no_tool_calls_rather_than_someone_elses():
    """If the tier2 match failed there is no session_id, so there is no basis for
    attributing ANY tool call. Empty is the honest answer — the same rule
    `cost_usd: None` follows (PRRules invariant 15)."""
    agent_row, _ = fs.project_node_agent(
        _dispatch_result(), RUN_ID, "S4", _SPEC,
        ledger_row=None, ledger_events=[_tool_event()],
    )
    assert agent_row["commands_run"] == 0
    assert agent_row["files_touched"] == []
    assert agent_row["cost_usd"] is None


def test_tool_call_paths_inherit_the_same_redaction_as_every_other_path():
    """Redaction is inherited, not reinvented: this must go through the same
    `_redact_path` allow-list the heartbeat path uses, so a customer-identifying
    path outside the repo is redacted here too."""
    agent_row, _ = fs.project_node_agent(
        _dispatch_result(), RUN_ID, "S4", _SPEC,
        ledger_row=_tier2_row(),
        ledger_events=[_tool_event(files=["/Users/danieltecum/Desktop/jane-doe-export.csv"])],
    )
    assert agent_row["files_touched"] == ["[external path — redacted]"]


def test_read_ledger_events_degrades_to_empty_on_a_missing_file(tmp_path):
    """This runs in the `finally` of a paid dispatch — a missing ledger must not
    raise, exactly like `match_ledger_row`."""
    assert fs.read_ledger_events(tmp_path / "nope.jsonl") == []


def test_read_ledger_events_skips_malformed_lines_without_losing_good_ones(tmp_path):
    path = tmp_path / "2026-07-28.jsonl"
    path.write_text(
        '{"event": "tool", "session_id": "s1"}\n'
        "not json at all\n"
        '{"event": "tier2", "label": "x"}\n',
        encoding="utf-8",
    )
    events = fs.read_ledger_events(path)
    assert [e.get("event") for e in events] == ["tool", "tier2"]


def test_a_dispatched_row_is_born_ended_because_dispatch_is_synchronous():
    agent_row, _ = fs.project_node_agent(_dispatch_result(), RUN_ID, "S4", _SPEC)
    assert agent_row["status"] == "ended"


def test_cost_is_none_not_zero_when_the_ledger_match_failed():
    """The single most important line in this file. `or 0` here would make every
    unmatched dispatch look free, and a $0 cost is indistinguishable from a real
    cheap run — so the error would never surface.
    """
    agent_row, event_row = fs.project_node_agent(_dispatch_result(), RUN_ID, "S4", _SPEC, ledger_row=None)
    assert agent_row["cost_usd"] is None
    assert agent_row["ledger_session_id"] is None
    assert "cost_usd" not in event_row["detail"]


def test_agent_row_keys_are_all_real_factory_agents_columns(migration_text):
    columns = _columns_for("factory_agents", migration_text)
    agent_row, _ = fs.project_node_agent(
        _dispatch_result(), RUN_ID, "S4", _SPEC,
        started_at=datetime.now().astimezone(), ended_at=datetime.now().astimezone(),
        ledger_row=_tier2_row(),
    )
    unknown = set(agent_row) - columns
    assert not unknown, f"unknown factory_agents column(s): {unknown}"


def test_event_row_keys_are_all_real_factory_run_events_columns(migration_text):
    columns = _columns_for("factory_run_events", migration_text)
    _, event_row = fs.project_node_agent(_dispatch_result(), RUN_ID, "S4", _SPEC)
    assert set(event_row) <= columns


def test_the_event_kind_is_admitted_by_the_migrations_check_constraint(migration_text):
    """A1 widens the CHECK. If that migration is missing or reverted, this push
    would 400 at runtime — caught here instead."""
    _, event_row = fs.project_node_agent(_dispatch_result(), RUN_ID, "S4", _SPEC)
    assert event_row["kind"] == "agent_dispatched"
    assert "'agent_dispatched'" in migration_text


# ===========================================================================
# Through dispatch_node: success, failure, dry-run, fail-open
# ===========================================================================


def test_a_successful_dispatch_pushes_one_agent_row_and_one_event(cap, tmp_path):
    """Generic tier2-dispatch -> sync-push mechanic — a synthetic fixture, not
    `corpus-planner` (now `executor: self_dispatch`, whose first call returns
    before this module's push path is ever reached)."""
    spec = _tier2_spec("dispatch-sync-fixture")
    node_id = f"S4:{spec['slug']}"
    _write_ledger(tmp_path, [_tier2_row(node_id=node_id)])
    result = fnr.dispatch_node(
        spec["slug"], "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=spec,
        runner=lambda argv: _Proc(returncode=0, stdout="MIXTURE DECIDED: 4 slices"),
        ledger_dir=tmp_path,
    )
    assert result["ok"] is True
    assert len(cap.agents) == 1
    assert len(cap.events) == 1

    agent_row = cap.agents[0]
    assert agent_row["run_id"] == RUN_ID
    assert agent_row["stage"] == "S4"
    assert agent_row["title"] == spec["slug"]
    assert agent_row["role"] == "builder"
    assert agent_row["goal"]  # the spec's real mission, not a placeholder
    assert agent_row["status"] == "ended"
    assert agent_row["cost_usd"] == 0.4231
    assert agent_row["ledger_session_id"] == "sess-abc123"
    assert agent_row["started_at"] and agent_row["ended_at"]

    event = cap.events[0]
    assert event["kind"] == "agent_dispatched"
    assert node_id in event["headline"]
    assert event["ref"]["node_id"] == node_id
    assert event["detail"]["model"] == "sonnet"
    assert event["detail"]["budget_usd"] == 2.0
    assert event["detail"]["cost_usd"] == 0.4231
    assert event["detail"]["usage"]["tokens_in"] == 3287


def test_a_failed_dispatch_still_pushes_both_rows_with_the_failure_visible(cap, tmp_path):
    """"Not silently dropped" is the requirement. An agent that burned budget and
    died is precisely the row a human needs to see; absence reads as "no agent ran".

    Generic tier2 mechanic — a synthetic fixture, not `corpus-planner` (now
    `executor: self_dispatch`, which never reaches this push path on a first call).
    """
    spec = _tier2_spec("dispatch-sync-failure-fixture")
    node_id = f"S4:{spec['slug']}"
    _write_ledger(tmp_path, [_tier2_row(node_id=node_id, cost_usd=1.87, ok=False, error="boom")])
    result = fnr.dispatch_node(
        spec["slug"], "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        spec_override=spec,
        runner=lambda argv: _Proc(returncode=2, stdout="", stderr="Traceback: exploded"),
        ledger_dir=tmp_path,
    )
    assert result["ok"] is False
    assert len(cap.agents) == 1 and len(cap.events) == 1
    assert cap.events[0]["detail"]["exit_code"] == 2
    assert "FAILED" in cap.events[0]["headline"]
    # The real spend still lands on the row — a failed run is not a free run.
    assert cap.agents[0]["cost_usd"] == 1.87
    assert cap.events[0]["detail"]["stderr_tail"] == "Traceback: exploded"


def test_a_failing_verifier_verdict_is_reflected_in_the_pushed_detail(cap, tmp_path):
    fnr.dispatch_node(
        "preflight-linter", "S5", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
        runner=lambda argv: _Proc(returncode=0, stdout="VERDICT: fail"),
        ledger_dir=tmp_path,
    )
    assert cap.events[0]["detail"]["verdict"] == "fail"
    assert "FAILED" in cap.events[0]["headline"]


def test_an_exception_mid_dispatch_still_pushes_a_row_and_still_propagates(cap, tmp_path):
    """The reason for the try/finally. A GateForgeryError-class failure must still
    reach the caller (it is one of this module's two hard failures) AND must still
    leave evidence in the product that an agent was spawned and money was spent.

    Generic tier2 mechanic — a synthetic fixture, not `corpus-planner` (now
    `executor: self_dispatch`, which never calls `runner` at all, so
    `exploding_runner` would never raise and `pytest.raises` would fail with
    "DID NOT RAISE").
    """
    spec = _tier2_spec("dispatch-sync-exception-fixture")

    def exploding_runner(argv):
        raise RuntimeError("the runner itself died")

    with pytest.raises(RuntimeError, match="the runner itself died"):
        fnr.dispatch_node(
            spec["slug"], "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
            spec_override=spec,
            runner=exploding_runner, ledger_dir=tmp_path,
        )
    assert len(cap.agents) == 1
    assert cap.agents[0]["stage"] == "S4"
    assert len(cap.events) == 1
    # An unset `ok` must never read as success.
    assert "FAILED" in cap.events[0]["headline"]


def test_a_supabase_outage_does_not_raise_past_dispatch_node_and_leaves_the_result_intact(tmp_path):
    """Fail-open, PRRules rule 4 / invariant 10's neighbour: the spend has already
    happened when this push runs, so a Supabase outage must not become a dispatch
    failure. Both push calls are exercised, not just the first.

    Generic tier2 mechanic — a synthetic fixture, not `corpus-planner` (now
    `executor: self_dispatch`, which never reaches this push path on a first call).
    """
    spec = _tier2_spec("dispatch-sync-outage-fixture")
    node_id = f"S4:{spec['slug']}"
    _write_ledger(tmp_path, [_tier2_row(node_id=node_id)])
    with patch.object(fs, "_upsert_agent_row", side_effect=SupabaseRestError("503 upstream")), patch.object(
        fs, "insert", side_effect=SupabaseRestError("503 upstream")
    ):
        result = fnr.dispatch_node(
            spec["slug"], "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
            spec_override=spec,
            runner=lambda argv: _Proc(returncode=0, stdout="MIXTURE DECIDED"),
            ledger_dir=tmp_path,
        )
    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert result["verdict"] is None


def test_a_non_supabase_exception_in_the_push_path_is_also_swallowed(tmp_path):
    """Deliberately broader than `except SupabaseRestError`: the ledger read and the
    row projection are in this path too, and a TypeError from an unexpected result
    shape must not take down a paid dispatch either.

    Generic tier2 mechanic — a synthetic fixture, not `corpus-planner` (now
    `executor: self_dispatch`, which never reaches this push path on a first call).
    """
    spec = _tier2_spec("dispatch-sync-push-exception-fixture")
    with patch.object(fs, "project_node_agent", side_effect=TypeError("unexpected shape")):
        result = fnr.dispatch_node(
            spec["slug"], "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False,
            spec_override=spec,
            runner=lambda argv: _Proc(returncode=0, stdout="ok"),
            ledger_dir=tmp_path,
        )
    assert result["ok"] is True


def test_push_returns_false_rather_than_inventing_a_run_id_when_there_is_none():
    """A bare `factory_node_run` CLI invocation with no run is a real case."""
    with patch.object(fs, "_upsert_agent_row", side_effect=AssertionError("must not push")):
        assert fs.push_node_agent(_dispatch_result(), None, "S4", _SPEC) is False


# ===========================================================================
# --dry-run: no subprocess, no spend, and now also NO push
# ===========================================================================


def test_dry_run_pushes_nothing_at_all(tmp_path):
    """Invariant 10: this task instruments `dispatch_node` around the existing
    dispatch call and must not change when or whether anything is spawned. A dry
    run has no real agent, no cost and no session — a row for it would be fiction.
    """
    with patch.object(fs, "push_node_agent", side_effect=AssertionError("dry-run must not sync")):
        result = fnr.dispatch_node(
            "corpus-planner", "S4", RUN_ID, GRAPH_ID, SLUG, dry_run=True, ledger_dir=tmp_path
        )
    assert result["dispatched"] is False


def test_dry_run_still_spawns_no_subprocess_after_the_try_finally_rewrite(tmp_path):
    """Guards the rewrite itself: wrapping the body in try/finally must not have
    moved the dry-run early return."""
    def forbidden_runner(argv):
        raise AssertionError("--dry-run must never spawn a subprocess")

    fnr.dispatch_stage("S4", RUN_ID, GRAPH_ID, SLUG, dry_run=True, runner=forbidden_runner, ledger_dir=tmp_path)


# ===========================================================================
# Whole-stage behaviour is unchanged
# ===========================================================================


def test_a_stage_chain_pushes_one_row_per_node_it_actually_ran(cap, tmp_path, monkeypatch):
    """A stage chain stops at the first failure. Two rows means the second node
    failed and the chain stopped — and both are still visible.

    Uses two synthetic tier2 fixtures rather than the real S4_CHAIN: this is a
    test of the generic per-node-push + chain-stop mechanic via the tier2
    subprocess-mock pattern, and the real chain's first node (`corpus-planner`)
    is now `executor: self_dispatch`, which never calls `runner` and would stop
    the chain on its own pending pause before a second node is ever reached.
    """
    spec_a = _tier2_spec("stage-push-fixture-a")
    spec_b = _tier2_spec("stage-push-fixture-b")
    specs = {spec_a["slug"]: spec_a, spec_b["slug"]: spec_b}
    monkeypatch.setattr(fnr, "load_spec", lambda slug: specs[slug])
    monkeypatch.setattr(fnr, "STAGE_CHAINS", {**fnr.STAGE_CHAINS, "S4": (spec_a["slug"], spec_b["slug"])})

    calls = []

    def runner(argv):
        calls.append(argv)
        return _Proc(returncode=0 if len(calls) == 1 else 3, stdout="done")

    fnr.dispatch_stage("S4", RUN_ID, GRAPH_ID, SLUG, dry_run=False, runner=runner, ledger_dir=tmp_path)
    assert len(calls) == 2
    assert len(cap.agents) == 2
    assert len(cap.events) == 2
    assert [a["title"] for a in cap.agents] == [spec_a["slug"], spec_b["slug"]]


def test_every_stage_chain_spec_slug_is_unique_across_chains():
    """`_upsert_agent_row` keys an unmatched-ledger row on (run_id, title), and
    `title` is the spec slug — so two chains sharing a slug would silently collapse
    two dispatched nodes into one row. They do not share any today; this fails if
    that ever changes, rather than losing a row quietly.
    """
    slugs = [slug for chain in fnr.STAGE_CHAINS.values() for slug in chain]
    assert len(slugs) == len(set(slugs))
