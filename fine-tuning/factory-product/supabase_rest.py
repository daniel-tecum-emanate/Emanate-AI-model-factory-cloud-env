#!/usr/bin/env python3
"""supabase_rest — minimal PostgREST client shared by the factory sync sidecar.

One small, dependency-light (requests only) helper used by every `factory_*`
table writer (T11 run/stage push, T12 gate push/poll, T13 agents/events,
T14 lessons, T15 specs) so each of those stays a thin caller rather than
five copies of the same HTTP plumbing. This module never decides fail-open
vs fail-closed — callers own that (T11 wraps calls in try/except and logs;
T12 treats a raised `SupabaseRestError` as "no approval" per PRRules.md's
fail-closed direction for gates).

## Relay transport (model-factory-cloud-environment-v1)

A cloud-dispatched session never holds `FACTORY_SUPABASE_SERVICE_ROLE_KEY` (same
hard constraint as `FIREWORKS_API_KEY` — see `cloud-sessions/playbooks/
11-finetune-pipeline-agent.md`), so direct PostgREST calls are structurally
unavailable to it. `_resolve_transport()` is the one place that decision is
made: when a service-role key resolves, every function below behaves exactly
as it always has (zero change, zero new failure mode for the laptop/Mac-Mini
path this was written for). Only when that key is absent AND
`FACTORY_SYNC_RELAY_URL`/`FACTORY_SYNC_RELAY_TOKEN` are both set does a call
go to platform-alpha's `/api/model-factory/sync` route instead — a thin,
allow-listed forwarder that holds the real Supabase credential server-side
and never returns it. The relay token is scoped to exactly the tables in
`RELAY_ALLOWED_TABLES` below; it is not the service-role key and a leak of it
is not equivalent to leaking one (see that route's own docstring for the
server-side half of this contract).
"""

import os

import requests

DEFAULT_TIMEOUT_S = 10

# The complete, exhaustive set of tables anything in this codebase ever passes
# to upsert/insert/update/select (verified by grepping every call site in
# factory_sync.py, 2026-08-20). Checked in relay mode as defense-in-depth —
# the real enforcement boundary is the platform-alpha route's own allow-list,
# but a client that already knows better should never even send a request for
# a table outside this list, relay or not.
RELAY_ALLOWED_TABLES = frozenset({
    "factory_runs",
    "factory_run_stages",
    "factory_gate_requests",
    "factory_run_events",
    "factory_agents",
    "factory_run_digests",
})


class SupabaseRestError(RuntimeError):
    """Raised on any non-2xx response or transport failure talking to PostgREST."""


def _base_url(url=None):
    url = url or os.environ.get("FACTORY_SUPABASE_URL")
    if not url:
        raise SupabaseRestError("FACTORY_SUPABASE_URL not set and no url passed")
    return url.rstrip("/")


def _resolve_transport(url=None, service_role_key=None):
    """Decide direct-PostgREST vs. relay, once, for one call.

    Direct wins whenever it's available — this is what preserves every
    existing caller's behavior untouched. Relay is the fallback, not an
    alternative a caller opts into; nothing above this function needs to
    know which one actually ran.
    """
    key = service_role_key or os.environ.get("FACTORY_SUPABASE_SERVICE_ROLE_KEY")
    if key:
        return ("direct", _base_url(url), key)
    relay_url = os.environ.get("FACTORY_SYNC_RELAY_URL")
    relay_token = os.environ.get("FACTORY_SYNC_RELAY_TOKEN")
    if relay_url and relay_token:
        return ("relay", relay_url.rstrip("/"), relay_token)
    raise SupabaseRestError(
        "no transport configured: neither FACTORY_SUPABASE_SERVICE_ROLE_KEY "
        "(direct) nor FACTORY_SYNC_RELAY_URL+FACTORY_SYNC_RELAY_TOKEN (relay) "
        "are set, and neither was passed explicitly"
    )


def _relay_call(relay_url, relay_token, op, table, timeout=DEFAULT_TIMEOUT_S, **payload):
    if table not in RELAY_ALLOWED_TABLES:
        raise SupabaseRestError(
            f"relay {op} {table} refused: {table!r} is not in RELAY_ALLOWED_TABLES "
            "— this is a client-side check; if a real table needs relay support, "
            "add it here AND to the route's own allow-list, deliberately, not "
            "as a side effect of a typo"
        )
    body = {"op": op, "table": table, **payload}
    headers = {"Authorization": f"Bearer {relay_token}", "Content-Type": "application/json"}
    try:
        resp = requests.post(relay_url, json=body, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise SupabaseRestError(f"relay {op} {table} transport error: {exc}") from exc
    if not resp.ok:
        raise SupabaseRestError(f"relay {op} {table} failed: {resp.status_code} {resp.text[:500]}")
    try:
        return resp.json()
    except ValueError:
        return []


def upsert(table, rows, on_conflict, url=None, service_role_key=None, timeout=DEFAULT_TIMEOUT_S):
    """Upsert `rows` into `table` via PostgREST, keyed by `on_conflict` (a
    comma-separated column list matching a unique index, e.g. "code" or
    "run_id,gate"). Returns the parsed JSON response body on success.

    Raises SupabaseRestError on any failure — never swallows. Callers that
    need fail-open behavior (T11) catch this at the call site; callers that
    need fail-closed behavior (T12) treat this exception as "no approval."
    """
    if not rows:
        return []
    mode, base, cred = _resolve_transport(url, service_role_key)
    if mode == "relay":
        return _relay_call(base, cred, "upsert", table, rows=rows, on_conflict=on_conflict, timeout=timeout)
    endpoint = f"{base}/rest/v1/{table}"
    headers = {
        "apikey": cred,
        "Authorization": f"Bearer {cred}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=representation",
    }
    params = {"on_conflict": on_conflict}
    try:
        resp = requests.post(endpoint, json=rows, headers=headers, params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise SupabaseRestError(f"upsert {table} transport error: {exc}") from exc
    if not resp.ok:
        raise SupabaseRestError(f"upsert {table} failed: {resp.status_code} {resp.text[:500]}")
    try:
        return resp.json()
    except ValueError:
        return []


def insert(table, rows, url=None, service_role_key=None, timeout=DEFAULT_TIMEOUT_S):
    """Plain POST insert — no `on_conflict`/upsert semantics. Used for tables
    whose only uniqueness is a PARTIAL index (e.g. `factory_gate_requests`'s
    "one pending row per (run_id, gate)" constraint), which PostgREST's
    `on_conflict` query param cannot target (Postgres requires `ON CONFLICT`'s
    target to match a full, non-partial constraint, or the exact same WHERE
    predicate — not expressible through this param). Callers that need
    "insert only if one doesn't already exist" check first via `select`.
    """
    if not rows:
        return []
    mode, base, cred = _resolve_transport(url, service_role_key)
    if mode == "relay":
        return _relay_call(base, cred, "insert", table, rows=rows, timeout=timeout)
    endpoint = f"{base}/rest/v1/{table}"
    headers = {
        "apikey": cred,
        "Authorization": f"Bearer {cred}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    try:
        resp = requests.post(endpoint, json=rows, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise SupabaseRestError(f"insert {table} transport error: {exc}") from exc
    if not resp.ok:
        raise SupabaseRestError(f"insert {table} failed: {resp.status_code} {resp.text[:500]}")
    try:
        return resp.json()
    except ValueError:
        return []


def update(table, filters, values, url=None, service_role_key=None, timeout=DEFAULT_TIMEOUT_S):
    """PATCH rows in `table` matching `filters` (PostgREST query params, e.g.
    `{"id": "eq.<uuid>"}`) with `values`. Used for tables with no usable
    `on_conflict` upsert target — e.g. `factory_agents`, whose only real
    identity key (`ledger_session_id`) has no unique constraint (nullable,
    and null for every untracked/Cursor session) — so the caller selects the
    existing row's id first (via `select`), then calls this to update it in
    place, falling back to `insert` when no matching row exists yet.
    """
    if not values:
        return []
    mode, base, cred = _resolve_transport(url, service_role_key)
    if mode == "relay":
        return _relay_call(base, cred, "update", table, filters=filters or {}, values=values, timeout=timeout)
    endpoint = f"{base}/rest/v1/{table}"
    headers = {
        "apikey": cred,
        "Authorization": f"Bearer {cred}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    try:
        resp = requests.patch(endpoint, json=values, headers=headers, params=filters or {}, timeout=timeout)
    except requests.RequestException as exc:
        raise SupabaseRestError(f"update {table} transport error: {exc}") from exc
    if not resp.ok:
        raise SupabaseRestError(f"update {table} failed: {resp.status_code} {resp.text[:500]}")
    try:
        return resp.json()
    except ValueError:
        return []


def select(table, params=None, url=None, service_role_key=None, timeout=DEFAULT_TIMEOUT_S):
    """GET rows from `table` via PostgREST. `params` is passed through as
    PostgREST query params (e.g. {"run_id": "eq.<uuid>", "select": "*"}).
    """
    mode, base, cred = _resolve_transport(url, service_role_key)
    if mode == "relay":
        return _relay_call(base, cred, "select", table, params=params or {}, timeout=timeout)
    endpoint = f"{base}/rest/v1/{table}"
    headers = {
        "apikey": cred,
        "Authorization": f"Bearer {cred}",
    }
    try:
        resp = requests.get(endpoint, headers=headers, params=params or {}, timeout=timeout)
    except requests.RequestException as exc:
        raise SupabaseRestError(f"select {table} transport error: {exc}") from exc
    if not resp.ok:
        raise SupabaseRestError(f"select {table} failed: {resp.status_code} {resp.text[:500]}")
    try:
        return resp.json()
    except ValueError:
        # QA finding (DELTAS.md D19): unlike upsert/insert/update, this never
        # guarded against a malformed-JSON 2xx body — it propagated a raw
        # `ValueError` instead of failing the same way those three do.
        # Callers that treat "no rows" as the fail-closed case (T12's
        # `poll_gate_status`) depend on this returning `[]`, not raising an
        # unexpected exception type they don't catch.
        return []
