"""Session-wide safety rails for the factory test suite.

Added 2026-07-27 (factory-graph-pr1, B2) when `factory.py`'s human-gate handlers
started emitting decision events. Several existing tests drive those handlers
directly with a synthetic org slug, so without this the suite would append
`pytest-*-synthetic-org` rows to the REAL `ledger/YYYY-MM-DD.jsonl` on every
run. That file is not scratch space — the nightly reflection, the metrics
history, and the cost rollups all read it, so synthetic rows there are a
corrupted measurement rather than harmless noise.

Redirecting at the session level (autouse, not opt-in) is deliberate: a test
author should not have to know that the code under test writes to a ledger in
order to avoid polluting it.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

# V-364: `scripts/lib/cursor_guard.py` and `scripts/tier2_run.sh` are DELIBERATELY
# absent from this cloud environment — they resolve a local `claude` CLI backed by a
# machine's own Anthropic auth, which this environment is structurally barred from
# holding. Do NOT "fix" this by adding cursor_guard.py.
#
# The side effect is that `test_cursor_dispatch.py` imports `factory_cursor_dispatch`,
# which does a module-level `import cursor_guard`, so pytest raises during COLLECTION.
# Collection errors abort the whole run: before this hook, `pytest tests/` executed
# ZERO of the ~1100 tests here and printed "Interrupted: 1 error during collection",
# which reads like a broken suite rather than one intentionally-unavailable module.
#
# So skip that one module when — and only when — cursor_guard is genuinely unimportable.
# Where it IS present (the operator's machine) the module collects and runs as before,
# so this cannot silently hide a real regression in cursor dispatch.
_SCRIPTS_LIB = Path(__file__).resolve().parents[3] / "scripts" / "lib"
if str(_SCRIPTS_LIB) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_LIB))

collect_ignore = []
if importlib.util.find_spec("cursor_guard") is None:
    collect_ignore.append("test_cursor_dispatch.py")


@pytest.fixture(scope="session", autouse=True)
def _isolate_ledger_writes(tmp_path_factory):
    ledger_dir = tmp_path_factory.mktemp("ledger")
    os.environ["FACTORY_LEDGER_DIR"] = str(ledger_dir)
    yield ledger_dir
    os.environ.pop("FACTORY_LEDGER_DIR", None)


# Supabase credentials are unset for the whole session, for exactly the reason
# above generalized one step: `supabase_rest` falls back to
# `FACTORY_SUPABASE_URL`/`FACTORY_SUPABASE_SERVICE_ROLE_KEY` whenever a caller
# passes no explicit url, so on a machine where those are exported (the operator's
# machine — they have to be, for the real sync to work) any test that reaches a
# push path unpatched would write to the REAL project.
#
# Added 2026-07-28 (factory-observability-v1, B1) when `dispatch_node` gained a
# fail-open `push_node_agent` call in its `finally`. That made every existing
# dispatch test a potential writer of `factory_agents`/`factory_run_events` rows,
# and fail-open means it would have happened SILENTLY — the tests would still have
# passed. With the credentials unset, `_base_url` raises before any socket is
# opened, so an unpatched push is a no-op instead of a live write.
#
# A test that wants to observe push behaviour patches `insert`/`upsert`/`select`
# (or the push function) directly, which every test in this suite already does.
@pytest.fixture(scope="session", autouse=True)
def _block_real_supabase_calls():
    saved = {k: os.environ.pop(k, None) for k in ("FACTORY_SUPABASE_URL", "FACTORY_SUPABASE_SERVICE_ROLE_KEY")}
    yield
    for key, value in saved.items():
        if value is not None:
            os.environ[key] = value


# model-factory-finetune-launcher-v1 T2-3: `trigger_dev_rest.trigger_task` is
# the one call site that could enqueue a REAL platform-alpha Trigger.dev run
# (which would, in production, make the real paid Fireworks call this whole
# initiative treats as the single most safety-critical boundary). Same
# reasoning as `_block_real_supabase_calls` above, one level further down the
# chain: with the key unset, `trigger_dev_rest._secret_key` raises before any
# socket opens, so an unpatched `stage_train` dispatch test is a loud failure
# instead of a silent live call. Every test that exercises a real dispatch
# patches `trigger_dev_rest.trigger_task` directly (see
# `test_stage_train_dispatch.py`).
@pytest.fixture(scope="session", autouse=True)
def _block_real_trigger_dev_calls():
    saved = os.environ.pop("TRIGGER_SECRET_KEY", None)
    yield
    if saved is not None:
        os.environ["TRIGGER_SECRET_KEY"] = saved
