"""supabase_rest — transport resolution (model-factory-cloud-environment-v1).

`_resolve_transport` is the one branch point that decides direct PostgREST vs.
the platform-alpha relay. These tests exist to prove two things: the existing
direct path is byte-for-byte unchanged (every other test file in this suite
already depends on that), and the new relay path only ever activates when
direct genuinely isn't available — never as a second, competing route.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import supabase_rest  # noqa: E402
from supabase_rest import SupabaseRestError  # noqa: E402

RELAY_URL = "https://platform-alpha.invalid/api/model-factory/sync"
RELAY_TOKEN = "relay-token-for-tests"


def _mock_response(ok=True, status_code=200, json_data=None):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else []
    resp.text = "" if ok else "mock error body"
    return resp


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for key in ("FACTORY_SUPABASE_URL", "FACTORY_SUPABASE_SERVICE_ROLE_KEY",
                "FACTORY_SYNC_RELAY_URL", "FACTORY_SYNC_RELAY_TOKEN"):
        monkeypatch.delenv(key, raising=False)


def test_neither_credential_set_raises_before_any_network_call():
    with patch("supabase_rest.requests.get") as mock_get:
        with pytest.raises(SupabaseRestError, match="no transport configured"):
            supabase_rest.select("factory_runs")
    mock_get.assert_not_called()


def test_direct_still_wins_when_both_direct_and_relay_are_configured(monkeypatch):
    """Direct is not one of two equal options — it is preferred whenever
    available, full stop. A misconfigured machine that happens to carry both
    must behave exactly as it did before relay existed."""
    monkeypatch.setenv("FACTORY_SUPABASE_URL", "https://direct.invalid")
    monkeypatch.setenv("FACTORY_SUPABASE_SERVICE_ROLE_KEY", "direct-key")
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", RELAY_URL)
    monkeypatch.setenv("FACTORY_SYNC_RELAY_TOKEN", RELAY_TOKEN)
    with patch("supabase_rest.requests.get", return_value=_mock_response(json_data=[{"id": "1"}])) as mock_get, \
         patch("supabase_rest.requests.post") as mock_post:
        rows = supabase_rest.select("factory_runs", params={"id": "eq.1"})
    assert rows == [{"id": "1"}]
    mock_get.assert_called_once()
    assert mock_get.call_args.args[0] == "https://direct.invalid/rest/v1/factory_runs"
    mock_post.assert_not_called()


def test_direct_mode_is_completely_unchanged_shape(monkeypatch):
    """Byte-for-byte the pre-relay request shape, for every existing caller's
    sake — this is the regression this whole module change must not cause."""
    monkeypatch.setenv("FACTORY_SUPABASE_URL", "https://direct.invalid/")
    monkeypatch.setenv("FACTORY_SUPABASE_SERVICE_ROLE_KEY", "direct-key")
    with patch("supabase_rest.requests.post", return_value=_mock_response(json_data=[{"id": "1"}])) as mock_post:
        supabase_rest.upsert("factory_runs", [{"id": "1"}], on_conflict="id")
    endpoint, kwargs = mock_post.call_args.args[0], mock_post.call_args.kwargs
    assert endpoint == "https://direct.invalid/rest/v1/factory_runs"
    assert kwargs["headers"]["apikey"] == "direct-key"
    assert kwargs["headers"]["Authorization"] == "Bearer direct-key"
    assert kwargs["params"] == {"on_conflict": "id"}
    assert kwargs["json"] == [{"id": "1"}]


def test_relay_used_when_direct_credential_is_absent(monkeypatch):
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", RELAY_URL)
    monkeypatch.setenv("FACTORY_SYNC_RELAY_TOKEN", RELAY_TOKEN)
    with patch("supabase_rest.requests.post", return_value=_mock_response(json_data=[{"id": "1"}])) as mock_post:
        rows = supabase_rest.upsert("factory_runs", [{"id": "1"}], on_conflict="id")
    assert rows == [{"id": "1"}]
    mock_post.assert_called_once()
    endpoint, kwargs = mock_post.call_args.args[0], mock_post.call_args.kwargs
    assert endpoint == RELAY_URL
    assert kwargs["headers"]["Authorization"] == f"Bearer {RELAY_TOKEN}"
    assert "apikey" not in kwargs["headers"]  # the relay token is not a Supabase apikey
    assert kwargs["json"] == {"op": "upsert", "table": "factory_runs", "rows": [{"id": "1"}], "on_conflict": "id"}


def test_relay_partial_config_does_not_activate(monkeypatch):
    """URL with no token (or vice versa) must fail closed exactly like having
    neither — a half-configured relay must never look like a working one."""
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", RELAY_URL)
    with patch("supabase_rest.requests.post") as mock_post:
        with pytest.raises(SupabaseRestError, match="no transport configured"):
            supabase_rest.insert("factory_gate_requests", [{"run_id": "1"}])
    mock_post.assert_not_called()


def test_relay_refuses_a_table_outside_the_allow_list(monkeypatch):
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", RELAY_URL)
    monkeypatch.setenv("FACTORY_SYNC_RELAY_TOKEN", RELAY_TOKEN)
    with patch("supabase_rest.requests.post") as mock_post:
        with pytest.raises(SupabaseRestError, match="not in RELAY_ALLOWED_TABLES"):
            supabase_rest.select("some_other_table")
    mock_post.assert_not_called()


@pytest.mark.parametrize("table", sorted(supabase_rest.RELAY_ALLOWED_TABLES))
def test_every_allow_listed_table_relays_successfully(monkeypatch, table):
    # Relay is ALWAYS a POST to the one relay endpoint, whatever the direct-mode
    # verb would have been — the op travels in the JSON body, not the HTTP verb.
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", RELAY_URL)
    monkeypatch.setenv("FACTORY_SYNC_RELAY_TOKEN", RELAY_TOKEN)
    with patch("supabase_rest.requests.post", return_value=_mock_response(json_data=[])):
        supabase_rest.select(table)  # must not raise


def test_relay_forwards_insert_update_select_with_their_own_shapes(monkeypatch):
    """All three still go over POST (the relay's one endpoint) — this checks
    the JSON body shape each op sends, not the HTTP verb, which is constant."""
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", RELAY_URL)
    monkeypatch.setenv("FACTORY_SYNC_RELAY_TOKEN", RELAY_TOKEN)

    with patch("supabase_rest.requests.post", return_value=_mock_response(json_data=[{"id": "1"}])) as mock_post:
        supabase_rest.insert("factory_gate_requests", [{"run_id": "1", "gate": "launch"}])
    assert mock_post.call_args.kwargs["json"] == {
        "op": "insert", "table": "factory_gate_requests",
        "rows": [{"run_id": "1", "gate": "launch"}],
    }

    with patch("supabase_rest.requests.post", return_value=_mock_response(json_data=[{"id": "1"}])) as mock_post:
        supabase_rest.update("factory_agents", {"id": "eq.1"}, {"status": "ended"})
    assert mock_post.call_args.kwargs["json"] == {
        "op": "update", "table": "factory_agents",
        "filters": {"id": "eq.1"}, "values": {"status": "ended"},
    }

    with patch("supabase_rest.requests.post", return_value=_mock_response(json_data=[{"id": "1"}])) as mock_post:
        supabase_rest.select("factory_run_events", params={"run_id": "eq.1"})
    assert mock_post.call_args.kwargs["json"] == {
        "op": "select", "table": "factory_run_events", "params": {"run_id": "eq.1"},
    }


def test_relay_transport_failure_raises_supabase_rest_error(monkeypatch):
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", RELAY_URL)
    monkeypatch.setenv("FACTORY_SYNC_RELAY_TOKEN", RELAY_TOKEN)
    import requests as _requests
    with patch("supabase_rest.requests.post", side_effect=_requests.ConnectionError("boom")):
        with pytest.raises(SupabaseRestError, match="relay upsert factory_runs transport error"):
            supabase_rest.upsert("factory_runs", [{"id": "1"}], on_conflict="id")


def test_relay_non_2xx_raises_supabase_rest_error(monkeypatch):
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", RELAY_URL)
    monkeypatch.setenv("FACTORY_SYNC_RELAY_TOKEN", RELAY_TOKEN)
    with patch("supabase_rest.requests.post", return_value=_mock_response(ok=False, status_code=401)):
        with pytest.raises(SupabaseRestError, match="relay upsert factory_runs failed: 401"):
            supabase_rest.upsert("factory_runs", [{"id": "1"}], on_conflict="id")
