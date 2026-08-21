"""factory._sync_url — the truthiness gate every sync attempt (gate push/poll,
run/stage rows, decisions, node dispatch) checks before trying to sync at all.

model-factory-cloud-environment-v1: a cloud session never has FACTORY_SUPABASE_URL,
only FACTORY_SYNC_RELAY_URL/_TOKEN. Without this fallback, every one of those
callers would see a falsy result here and skip sync entirely, before the
supabase_rest.py transport layer (which DOES know how to use the relay) ever
gets a chance to run.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for key in ("FACTORY_SUPABASE_URL", "FACTORY_SYNC_RELAY_URL"):
        monkeypatch.delenv(key, raising=False)


def _args(sync_url=None):
    return SimpleNamespace(sync_url=sync_url)


def test_none_configured_is_falsy():
    assert factory._sync_url(_args()) is None


def test_explicit_cli_flag_wins_over_everything(monkeypatch):
    monkeypatch.setenv("FACTORY_SUPABASE_URL", "https://direct.invalid")
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", "https://relay.invalid")
    assert factory._sync_url(_args(sync_url="https://explicit.invalid")) == "https://explicit.invalid"


def test_direct_env_wins_over_relay_when_both_set(monkeypatch):
    monkeypatch.setenv("FACTORY_SUPABASE_URL", "https://direct.invalid")
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", "https://relay.invalid")
    assert factory._sync_url(_args()) == "https://direct.invalid"


def test_relay_env_is_truthy_when_direct_is_absent(monkeypatch):
    """The real fix: a cloud session with only the relay pair configured must
    still get a truthy result here, or every sync call site skips before ever
    reaching supabase_rest.py's own, already-correct relay fallback."""
    monkeypatch.setenv("FACTORY_SYNC_RELAY_URL", "https://relay.invalid")
    assert factory._sync_url(_args()) == "https://relay.invalid"


def test_neither_env_var_set_and_no_flag_is_falsy(monkeypatch):
    assert not factory._sync_url(_args(sync_url=None))
