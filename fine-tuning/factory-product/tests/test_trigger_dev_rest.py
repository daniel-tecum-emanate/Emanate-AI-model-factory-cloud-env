"""trigger_dev_rest — transport resolution (model-factory-cloud-dispatch-v1).

Mirrors `test_supabase_rest.py`'s structure exactly: `_resolve_transport` is
the one branch point that decides direct Trigger.dev REST vs. the
platform-alpha launch relay. These tests exist to prove two things: the
existing direct path (`trigger_task`/`get_run`, using `TRIGGER_SECRET_KEY`) is
byte-for-byte unchanged — `test_stage_train_dispatch.py` and every other
caller in this suite already depends on that — and the new relay path only
ever activates when direct genuinely isn't available, never as a second,
competing route.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import trigger_dev_rest  # noqa: E402
from trigger_dev_rest import TriggerDevRestError  # noqa: E402

RELAY_SYNC_URL = "https://platform-alpha.invalid/api/model-factory/sync"
RELAY_LAUNCH_URL = "https://platform-alpha.invalid/api/model-factory/launch"
RELAY_TOKEN = "relay-token-for-tests"
DIRECT_KEY = "tr_dev_direct-key-for-tests"


def _mock_response(ok=True, status_code=200, json_data=None):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = "" if ok else "mock error body"
    return resp


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    # Session-level conftest already pops TRIGGER_SECRET_KEY, but this file's
    # own tests set/unset it (plus the relay pair) per-test, so clear all
    # three up front the same way test_supabase_rest.py clears its four.
    for key in ("TRIGGER_SECRET_KEY", "FACTORY_SYNC_RELAY_URL", "FACTORY_SYNC_RELAY_TOKEN"):
        monkeypatch.delenv(key, raising=False)


def test_neither_credential_set_raises_before_any_network_call():
    with patch("trigger_dev_rest.requests.post") as mock_post:
        with pytest.raises(TriggerDevRestError, match="no transport configured"):
            trigger_dev_rest.trigger_task("factory-launch-monitor", {"runId": "run-1"})
    mock_post.assert_not_called()


def test_direct_still_wins_when_both_direct_and_relay_are_configured(monkeypatch):
    """Direct is not one of two equal options — it is preferred whenever
    available, full stop. A misconfigured machine that happens to carry both
    must behave exactly as it did before relay existed."""
    monkeypatch.setenv("TRIGGER_SECRET_KEY", DIRECT_KEY)
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", RELAY_SYNC_URL)
    monkeypatch.setenv("FACTORY_SYNC_RELAY_TOKEN", RELAY_TOKEN)
    with patch(
        "trigger_dev_rest.requests.post", return_value=_mock_response(json_data={"id": "run_abc"})
    ) as mock_post:
        run_id = trigger_dev_rest.trigger_task("factory-launch-monitor", {"runId": "run-1"})
    assert run_id == "run_abc"
    mock_post.assert_called_once()
    endpoint, kwargs = mock_post.call_args.args[0], mock_post.call_args.kwargs
    assert endpoint == f"{trigger_dev_rest.TRIGGER_API_BASE}/v1/tasks/factory-launch-monitor/trigger"
    assert kwargs["headers"]["Authorization"] == f"Bearer {DIRECT_KEY}"


def test_direct_mode_trigger_task_is_completely_unchanged_shape(monkeypatch):
    """Byte-for-byte the pre-relay request shape, for every existing caller's
    sake (`factory.py`'s `_dispatch_launch_task`) — this is the regression
    this whole module change must not cause."""
    monkeypatch.setenv("TRIGGER_SECRET_KEY", DIRECT_KEY)
    with patch(
        "trigger_dev_rest.requests.post", return_value=_mock_response(json_data={"id": "run_abc"})
    ) as mock_post:
        run_id = trigger_dev_rest.trigger_task(
            "factory-launch-monitor",
            {"runId": "run-1", "baseModel": "accounts/fireworks/models/x"},
            options={"idempotencyKey": "factory-launch-run-1", "idempotencyKeyTTL": "1d"},
        )
    assert run_id == "run_abc"
    endpoint, kwargs = mock_post.call_args.args[0], mock_post.call_args.kwargs
    assert endpoint == f"{trigger_dev_rest.TRIGGER_API_BASE}/v1/tasks/factory-launch-monitor/trigger"
    assert kwargs["headers"] == {
        "Authorization": f"Bearer {DIRECT_KEY}",
        "Content-Type": "application/json",
    }
    assert kwargs["json"] == {
        "payload": {"runId": "run-1", "baseModel": "accounts/fireworks/models/x"},
        "options": {"idempotencyKey": "factory-launch-run-1", "idempotencyKeyTTL": "1d"},
    }


def test_direct_mode_get_run_is_completely_unchanged_shape(monkeypatch):
    monkeypatch.setenv("TRIGGER_SECRET_KEY", DIRECT_KEY)
    with patch(
        "trigger_dev_rest.requests.get",
        return_value=_mock_response(json_data={"id": "run_abc", "status": "COMPLETED"}),
    ) as mock_get:
        result = trigger_dev_rest.get_run("run_abc")
    assert result == {"id": "run_abc", "status": "COMPLETED"}
    endpoint, kwargs = mock_get.call_args.args[0], mock_get.call_args.kwargs
    assert endpoint == f"{trigger_dev_rest.TRIGGER_API_BASE}/v3/runs/run_abc"
    assert kwargs["headers"] == {"Authorization": f"Bearer {DIRECT_KEY}"}


def test_relay_used_when_direct_credential_is_absent(monkeypatch):
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", RELAY_SYNC_URL)
    monkeypatch.setenv("FACTORY_SYNC_RELAY_TOKEN", RELAY_TOKEN)
    with patch(
        "trigger_dev_rest.requests.post", return_value=_mock_response(json_data={"triggerRunId": "run_xyz"})
    ) as mock_post:
        run_id = trigger_dev_rest.trigger_task("factory-launch-monitor", {"runId": "run-1"})
    assert run_id == "run_xyz"
    mock_post.assert_called_once()
    endpoint, kwargs = mock_post.call_args.args[0], mock_post.call_args.kwargs
    assert endpoint == RELAY_LAUNCH_URL
    assert kwargs["headers"]["Authorization"] == f"Bearer {RELAY_TOKEN}"
    # The relay body IS the bare payload — no {"payload": ...} envelope, and
    # no `options` (the route computes idempotencyKey itself from runId).
    assert kwargs["json"] == {"runId": "run-1"}


def test_relay_derives_the_launch_url_from_the_sync_url(monkeypatch):
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", "https://app.example.com/api/model-factory/sync")
    monkeypatch.setenv("FACTORY_SYNC_RELAY_TOKEN", RELAY_TOKEN)
    with patch(
        "trigger_dev_rest.requests.post", return_value=_mock_response(json_data={"triggerRunId": "run_1"})
    ) as mock_post:
        trigger_dev_rest.trigger_task("factory-launch-monitor", {"runId": "run-1"})
    assert mock_post.call_args.args[0] == "https://app.example.com/api/model-factory/launch"


def test_relay_derives_the_launch_url_with_a_trailing_slash(monkeypatch):
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", "https://app.example.com/api/model-factory/sync/")
    monkeypatch.setenv("FACTORY_SYNC_RELAY_TOKEN", RELAY_TOKEN)
    with patch(
        "trigger_dev_rest.requests.post", return_value=_mock_response(json_data={"triggerRunId": "run_1"})
    ) as mock_post:
        trigger_dev_rest.trigger_task("factory-launch-monitor", {"runId": "run-1"})
    assert mock_post.call_args.args[0] == "https://app.example.com/api/model-factory/launch"


def test_relay_url_not_ending_in_sync_raises_rather_than_guessing(monkeypatch):
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", "https://app.example.com/api/model-factory/weird")
    monkeypatch.setenv("FACTORY_SYNC_RELAY_TOKEN", RELAY_TOKEN)
    with patch("trigger_dev_rest.requests.post") as mock_post:
        with pytest.raises(TriggerDevRestError, match="does not end in '/sync'"):
            trigger_dev_rest.trigger_task("factory-launch-monitor", {"runId": "run-1"})
    mock_post.assert_not_called()


def test_relay_partial_config_does_not_activate(monkeypatch):
    """URL with no token (or vice versa) must fail closed exactly like having
    neither — a half-configured relay must never look like a working one."""
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", RELAY_SYNC_URL)
    with patch("trigger_dev_rest.requests.post") as mock_post:
        with pytest.raises(TriggerDevRestError, match="no transport configured"):
            trigger_dev_rest.trigger_task("factory-launch-monitor", {"runId": "run-1"})
    mock_post.assert_not_called()


def test_relay_transport_failure_raises_trigger_dev_rest_error(monkeypatch):
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", RELAY_SYNC_URL)
    monkeypatch.setenv("FACTORY_SYNC_RELAY_TOKEN", RELAY_TOKEN)
    import requests as _requests
    with patch("trigger_dev_rest.requests.post", side_effect=_requests.ConnectionError("boom")):
        with pytest.raises(TriggerDevRestError, match="relay trigger factory-launch-monitor transport error"):
            trigger_dev_rest.trigger_task("factory-launch-monitor", {"runId": "run-1"})


def test_relay_non_2xx_raises_trigger_dev_rest_error(monkeypatch):
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", RELAY_SYNC_URL)
    monkeypatch.setenv("FACTORY_SYNC_RELAY_TOKEN", RELAY_TOKEN)
    with patch("trigger_dev_rest.requests.post", return_value=_mock_response(ok=False, status_code=502)):
        with pytest.raises(TriggerDevRestError, match="relay trigger factory-launch-monitor failed: 502"):
            trigger_dev_rest.trigger_task("factory-launch-monitor", {"runId": "run-1"})


def test_relay_response_with_no_trigger_run_id_raises(monkeypatch):
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", RELAY_SYNC_URL)
    monkeypatch.setenv("FACTORY_SYNC_RELAY_TOKEN", RELAY_TOKEN)
    with patch("trigger_dev_rest.requests.post", return_value=_mock_response(json_data={"ok": False})):
        with pytest.raises(TriggerDevRestError, match="response had no triggerRunId"):
            trigger_dev_rest.trigger_task("factory-launch-monitor", {"runId": "run-1"})


def test_relay_get_run_used_when_direct_credential_is_absent(monkeypatch):
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", RELAY_SYNC_URL)
    monkeypatch.setenv("FACTORY_SYNC_RELAY_TOKEN", RELAY_TOKEN)
    with patch(
        "trigger_dev_rest.requests.get",
        return_value=_mock_response(json_data={"status": "COMPLETED", "output": {"x": 1}}),
    ) as mock_get:
        result = trigger_dev_rest.get_run("run_abc")
    assert result == {"status": "COMPLETED", "output": {"x": 1}}
    mock_get.assert_called_once()
    endpoint, kwargs = mock_get.call_args.args[0], mock_get.call_args.kwargs
    assert endpoint == RELAY_LAUNCH_URL
    assert kwargs["headers"]["Authorization"] == f"Bearer {RELAY_TOKEN}"
    assert kwargs["params"] == {"triggerRunId": "run_abc"}


def test_relay_get_run_non_2xx_raises_trigger_dev_rest_error(monkeypatch):
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", RELAY_SYNC_URL)
    monkeypatch.setenv("FACTORY_SYNC_RELAY_TOKEN", RELAY_TOKEN)
    with patch("trigger_dev_rest.requests.get", return_value=_mock_response(ok=False, status_code=401)):
        with pytest.raises(TriggerDevRestError, match="relay get_run run_abc failed: 401"):
            trigger_dev_rest.get_run("run_abc")


def test_relay_get_run_transport_failure_raises_trigger_dev_rest_error(monkeypatch):
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", RELAY_SYNC_URL)
    monkeypatch.setenv("FACTORY_SYNC_RELAY_TOKEN", RELAY_TOKEN)
    import requests as _requests
    with patch("trigger_dev_rest.requests.get", side_effect=_requests.ConnectionError("boom")):
        with pytest.raises(TriggerDevRestError, match="relay get_run run_abc transport error"):
            trigger_dev_rest.get_run("run_abc")
