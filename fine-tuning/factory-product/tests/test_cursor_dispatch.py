"""C2 (factory-cursor-bridge-v1) — the Cursor SDK dispatch path. Release-blocking.

The load-bearing properties, and the specific failure each test prevents:

1. **`--dry-run` never creates a real Cursor agent.** Asserted on the mock never
   being invoked, not on the function returning quickly (PRTests C2, PRRules
   invariant 10). The branch lives AFTER `dispatch_node`'s dry-run return, so
   this is proven through `dispatch_node` itself, not the module in isolation.
2. **Startup failure and run failure never collapse.** A `CursorAgentError` from
   `Agent.create`/`agent.send` means "never executed, nothing spent" and carries
   `startup_error`; a terminal `status == "error"` means "executed and failed"
   and carries none. Conflating them would poison any future spec-reliability
   read (and the row a human sees).
3. **An external cancel mid-`wait()` IS observable** — verified against the
   installed SDK's source (`_run_base._TERMINAL_RUN_STATUSES` includes
   `"cancelled"`; `wait()` resolves with that status): the result must come back
   `stopped: True` and must NOT carry a fabricated `exit_code` (D7 — an
   operator's Stop is not a node failure).
4. **The IDs hit disk before `wait()` can trap us.** A crash while blocked in
   `wait()` must leave `cursor_agent_id`/`cursor_run_id` recoverable on disk,
   or the agent is an orphan nobody can find, let alone control.
5. **The secret never rides an error message.** Half B's result dicts get synced;
   invariant 15's "no key value anywhere client-visible" applies here too.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory_cursor_dispatch as fcd  # noqa: E402
import factory_node_run as fnr  # noqa: E402
import factory_sync as fs  # noqa: E402
from cursor_sdk.errors import CursorAgentError  # noqa: E402

RUN_ID = "dddddddd-0000-0000-0000-000000000001"
GRAPH_ID = "fine-tune-factory-v1"
ORG = "qa-dryrun-synth"
FAKE_KEY = "crsr_test0000fakekey0000"


def _spec(role_family="reader", runtime=None, slug="qa-cursor-synth"):
    """A synthetic opted-in spec, shaped like the real YAML after load — passed
    as `spec_override` so no fixture file has to exist on disk (the same
    mechanism the C4 gap-spawn path uses)."""
    return {
        "slug": slug,
        "role_family": role_family,
        "mission": "synthetic cursor-dispatch test node",
        "stages": ["S4"],
        "read_first": ["factory-automation/automation-graph/PLAN.md"],
        "mandate": [{"task": "do the synthetic thing", "stop_or_ask": "never"}],
        "laws": ["stay synthetic"],
        "done_when": "the test asserts",
        "final_response_shape": "one line",
        "workspace": {"worktree": "n/a", "branch_pattern": "n/a", "env_keys": []},
        "runtime": {"executor": "cursor_sdk", **(runtime or {})},
    }


def _terminal(status="finished", text="all read"):
    return SimpleNamespace(status=status, result=text)


def _mock_agent(wait_result=None, wait_side_effect=None, agent_id="agent-abc", run_id="run-xyz"):
    """A mock standing in for the SDK `Agent`: context manager + `send` returning
    a run whose `wait()` yields the given terminal result."""
    run = MagicMock()
    run.id = run_id
    if wait_side_effect is not None:
        run.wait.side_effect = wait_side_effect
    else:
        run.wait.return_value = wait_result or _terminal()
    agent = MagicMock()
    agent.agent_id = agent_id
    agent.send.return_value = run
    agent.__enter__ = MagicMock(return_value=agent)
    agent.__exit__ = MagicMock(return_value=False)
    return agent, run


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", FAKE_KEY)


@pytest.fixture
def synced():
    """Captures both sync halves so assertions land on pushed content, and so no
    test depends on the conftest's credential-blocking to stay silent."""
    calls = {"started": [], "final": []}
    with patch.object(fs, "push_node_agent_started", side_effect=lambda r, *a, **k: calls["started"].append(dict(r))), \
         patch.object(fs, "push_node_agent", side_effect=lambda r, *a, **k: calls["final"].append(dict(r))):
        yield calls


def _dispatch(spec=None, dry_run=False, **kwargs):
    spec = spec or _spec()
    return fnr.dispatch_node(
        spec["slug"], "S4", RUN_ID, GRAPH_ID, ORG,
        dry_run=dry_run, spec_override=spec, **kwargs
    )


# --------------------------------------------------------------------------
# dry-run creates nothing
# --------------------------------------------------------------------------


def test_dry_run_never_creates_a_cursor_agent(synced):
    with patch.object(fcd, "Agent") as mock_agent_cls:
        result = _dispatch(dry_run=True)
    mock_agent_cls.create.assert_not_called()
    assert result["dispatched"] is False and result["ok"] is True
    assert synced["started"] == [] and synced["final"] == []


def test_a_tier2_spec_never_touches_the_sdk():
    """The branch defaults closed: a spec with no executor field dispatches
    through tier2 exactly as before, and the SDK is never imported into its path."""
    spec = _spec()
    del spec["runtime"]["executor"]
    spec["runtime"] = {}  # plain pre-PR-B spec
    runner_calls = []

    def _runner(argv):
        runner_calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="done", stderr="")

    with patch.object(fcd, "Agent") as mock_agent_cls, patch.object(fs, "push_node_agent"):
        result = _dispatch(spec=spec, runner=_runner)
    mock_agent_cls.create.assert_not_called()
    assert len(runner_calls) == 1
    assert result["ok"] is True
    assert "cursor_agent_id" not in result


# --------------------------------------------------------------------------
# happy path
# --------------------------------------------------------------------------


def test_happy_path_populates_the_three_cursor_keys_and_the_core_fields(synced):
    agent, _run = _mock_agent(wait_result=_terminal("finished", "read them all"))
    with patch.object(fcd, "Agent") as mock_agent_cls:
        mock_agent_cls.create.return_value = agent
        result = _dispatch()
    assert result["cursor_agent_id"] == "agent-abc"
    assert result["cursor_run_id"] == "run-xyz"
    assert result["cursor_runtime"] == "cloud"  # D2's default
    assert result["dispatched"] is True and result["ok"] is True
    assert result["exit_code"] == 0
    assert result["final_response"] == "read them all"
    assert "stopped" not in result and "startup_error" not in result


def test_spec_runtime_local_overrides_the_cloud_default():
    agent, _run = _mock_agent()
    with patch.object(fcd, "Agent") as mock_agent_cls, patch.object(fs, "push_node_agent"), \
         patch.object(fs, "push_node_agent_started"):
        mock_agent_cls.create.return_value = agent
        result = _dispatch(spec=_spec(runtime={"cursor_runtime": "local"}))
    assert result["cursor_runtime"] == "local"
    options = mock_agent_cls.create.call_args.args[0]
    assert options.cloud is None  # local = no cloud block at all, not an empty one


def test_cloud_options_carry_origin_repo_and_current_branch():
    agent, _run = _mock_agent()
    with patch.object(fcd, "Agent") as mock_agent_cls, patch.object(fs, "push_node_agent"), \
         patch.object(fs, "push_node_agent_started"):
        mock_agent_cls.create.return_value = agent
        _dispatch()
    options = mock_agent_cls.create.call_args.args[0]
    assert options.cloud is not None
    repo = options.cloud.repos[0]
    assert "emanate-tecum-workflow" in repo.url
    assert repo.starting_ref  # the current branch, whatever it is on this machine


# --------------------------------------------------------------------------
# startup failure vs run failure — must never collapse
# --------------------------------------------------------------------------


def test_startup_failure_never_executed(synced):
    with patch.object(fcd, "Agent") as mock_agent_cls:
        mock_agent_cls.create.side_effect = CursorAgentError("no such model")
        result = _dispatch()
    assert result["dispatched"] is False and result["ok"] is False
    assert "no such model" in result["startup_error"]
    assert "cursor_agent_id" not in result
    # Nothing started, so neither sync half fires — there is no agent to report.
    assert synced["started"] == [] and synced["final"] == []


def test_run_failure_executed_and_failed(synced):
    agent, _run = _mock_agent(wait_result=_terminal("error", "it broke"))
    with patch.object(fcd, "Agent") as mock_agent_cls:
        mock_agent_cls.create.return_value = agent
        result = _dispatch()
    assert result["dispatched"] is True and result["ok"] is False
    assert result["exit_code"] == 1
    assert "startup_error" not in result  # the distinguishing absence
    assert len(synced["final"]) == 1


def test_missing_api_key_refuses_before_any_sdk_call(monkeypatch, synced):
    monkeypatch.delenv("CURSOR_API_KEY")
    with patch.object(fcd, "Agent") as mock_agent_cls:
        result = _dispatch()
    mock_agent_cls.create.assert_not_called()
    assert result["ok"] is False and "CURSOR_API_KEY" in result["startup_error"]


def test_stop_sentinel_refuses_like_tier2_would(monkeypatch, tmp_path, synced):
    sentinel = tmp_path / "STOP"
    sentinel.write_text("halt")
    monkeypatch.setattr(fcd, "STOP_SENTINEL", sentinel)
    with patch.object(fcd, "Agent") as mock_agent_cls:
        result = _dispatch()
    mock_agent_cls.create.assert_not_called()
    assert result["ok"] is False and "STOP sentinel" in result["startup_error"]


def test_the_api_key_never_appears_in_a_result_dict(synced):
    """Invariant 15: the error text the SDK throws can embed the key (it goes
    into an Authorization header the SDK might echo). Both the literal value and
    the crsr_ pattern must come out scrubbed, because this dict gets synced."""
    with patch.object(fcd, "Agent") as mock_agent_cls:
        mock_agent_cls.create.side_effect = CursorAgentError(
            f"401 unauthorized for key {FAKE_KEY} (crsr_other111token222)"
        )
        result = _dispatch()
    assert FAKE_KEY not in result["startup_error"]
    assert "crsr_other111token222" not in result["startup_error"]
    assert "crsr_[redacted]" in result["startup_error"]


# --------------------------------------------------------------------------
# external cancel mid-wait — the stopped outcome (D7)
# --------------------------------------------------------------------------


def test_external_cancel_mid_wait_returns_stopped_not_failed(synced):
    agent, _run = _mock_agent(wait_result=_terminal("cancelled", ""))
    with patch.object(fcd, "Agent") as mock_agent_cls:
        mock_agent_cls.create.return_value = agent
        result = _dispatch()
    assert result["stopped"] is True
    assert result["ok"] is False  # the chain still halts (V8) ...
    assert "exit_code" not in result  # ... but nothing may read it as a failure
    assert result["cursor_status"] == "cancelled"
    # The finally-sync received the stopped shape, so the row/event render it.
    assert synced["final"][0]["stopped"] is True


def test_a_stopped_node_halts_the_stage_chain(synced):
    """D7: same halt as a failure — downstream work's input is now uncertain —
    proven through dispatch_stage's real loop."""
    stopped_result = {"ok": False, "stopped": True, "spec": "first"}
    with patch.object(fnr, "dispatch_node", side_effect=[stopped_result]) as mock_dispatch:
        with patch.object(fnr, "STAGE_CHAINS", {"S4": ("first", "second", "third")}):
            results = fnr.dispatch_stage("S4", RUN_ID, GRAPH_ID, ORG, dry_run=False)
    assert mock_dispatch.call_count == 1  # never reached "second"
    assert results == [stopped_result]


# --------------------------------------------------------------------------
# ID persistence + the mid-run active row
# --------------------------------------------------------------------------


def test_ids_hit_disk_before_wait_and_survive_a_crash_in_it(tmp_path, monkeypatch, synced):
    # Keep runs/ out of the real repo; REPO must move with HERE or the
    # prompt-file relative_to() computation crosses filesystems.
    monkeypatch.setattr(fcd, "HERE", tmp_path)
    monkeypatch.setattr(fcd, "REPO", tmp_path)
    agent, _run = _mock_agent(wait_side_effect=RuntimeError("network died mid-wait"))
    with patch.object(fcd, "Agent") as mock_agent_cls:
        mock_agent_cls.create.return_value = agent
        with pytest.raises(RuntimeError):
            _dispatch()
    state = json.loads((tmp_path / "runs" / ORG / "cursor_dispatch" / "S4-qa-cursor-synth.json").read_text())
    assert state["cursor_agent_id"] == "agent-abc"
    assert state["cursor_run_id"] == "run-xyz"
    assert state["factory_run_id"] == RUN_ID
    # A crash inside wait() means the outcome is UNKNOWN — a cloud run may
    # still be executing server-side. The final sync is deliberately SKIPPED
    # (no row flipped to 'ended', which would lie AND remove the Stop button);
    # the mid-run 'active' row from the started-push stands, controllable.
    assert len(synced["started"]) == 1
    assert synced["final"] == []


def test_a_gate_forgery_after_wait_still_gets_the_full_final_sync(synced):
    """The run genuinely ended (wait() returned) before the forgery check blew
    up — so unlike the crash-in-wait case, the full outcome sync must happen
    and the exception must still propagate (it is one of the two hard
    failures in this module family)."""
    import factory_gates
    agent, _run = _mock_agent(wait_result=_terminal("finished", "done"))
    with patch.object(fcd, "Agent") as mock_agent_cls, \
         patch.object(factory_gates, "assert_unchanged",
                      side_effect=factory_gates.GateForgeryError("approval file appeared")):
        mock_agent_cls.create.return_value = agent
        with pytest.raises(factory_gates.GateForgeryError):
            _dispatch()
    assert len(synced["final"]) == 1
    assert synced["final"][0]["cursor_status"] == "finished"
    assert synced["final"][0]["ok"] is False  # an unset ok never reads as success


def test_an_active_row_is_pushed_before_wait_blocks(synced):
    """The recorded C2 deviation, asserted on ORDER: the started-push must have
    already happened by the time wait() runs, or the product has nothing to
    Stop while the run is in flight."""
    order = []

    def _wait():
        order.append("wait")
        return _terminal()

    agent, run = _mock_agent()
    run.wait.side_effect = _wait
    original_started = synced["started"]

    def _record_started(result, *a, **k):
        order.append("started_push")
        original_started.append(dict(result))

    with patch.object(fs, "push_node_agent_started", side_effect=_record_started), \
         patch.object(fcd, "Agent") as mock_agent_cls:
        mock_agent_cls.create.return_value = agent
        _dispatch()
    assert order == ["started_push", "wait"]
    assert synced["started"][0]["cursor_agent_id"] == "agent-abc"


# --------------------------------------------------------------------------
# verifier discipline carries over (LESSON-016)
# --------------------------------------------------------------------------


def test_a_verifier_that_finishes_without_a_verdict_is_not_ok(synced):
    agent, _run = _mock_agent(wait_result=_terminal("finished", "looks fine to me"))
    with patch.object(fcd, "Agent") as mock_agent_cls:
        mock_agent_cls.create.return_value = agent
        result = _dispatch(spec=_spec(role_family="verifier"))
    assert result["ok"] is False and result["verdict"] is None


def test_a_verifier_with_a_pass_verdict_is_ok(synced):
    agent, _run = _mock_agent(wait_result=_terminal("finished", "checks ran.\nVERDICT: pass"))
    with patch.object(fcd, "Agent") as mock_agent_cls:
        mock_agent_cls.create.return_value = agent
        result = _dispatch(spec=_spec(role_family="verifier"))
    assert result["ok"] is True and result["verdict"] == "pass"


# --------------------------------------------------------------------------
# the shared pre-flight is inherited, not reimplemented
# --------------------------------------------------------------------------


def test_a_heldout_violation_refuses_before_any_sdk_call():
    import factory_heldout
    with patch.object(fcd, "Agent") as mock_agent_cls, \
         patch.object(factory_heldout, "assert_not_visible",
                      side_effect=factory_heldout.HeldoutError("anchor material named")):
        with pytest.raises(factory_heldout.HeldoutError):
            _dispatch()
    mock_agent_cls.create.assert_not_called()
