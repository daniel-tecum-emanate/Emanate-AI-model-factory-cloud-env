#!/usr/bin/env python3
"""trigger_dev_rest — minimal Trigger.dev Management API client for `factory.py`.

T2-3 (model-factory-finetune-launcher-v1, PRContext-phase2.md): the mechanism
`stage_train` uses to dispatch platform-alpha's `factory-launch-monitor`
Trigger.dev task (T2-1) and get back a trackable run id, mirroring this
directory's own `supabase_rest.py` pattern — a small, dependency-light
(`requests` only) authenticated REST wrapper.

## Why a direct REST call instead of a new platform-alpha API route (model 1 only — see reversal below)

Researched before writing this (per PRContext-phase2.md T2-3's explicit
instruction not to guess): Trigger.dev publishes a documented Management API
under `https://api.trigger.dev/api/...`, authenticated with a project Secret
API key (`tr_prod_*`/`tr_dev_*`, from the Trigger.dev dashboard's API Keys
page) — the exact same "project has one secret, a plain HTTP call carries it"
shape this file's sibling `supabase_rest.py` already uses for Supabase. No new
platform-alpha route is needed to wrap `tasks.trigger()`; this file talks to
Trigger.dev directly, the same way `supabase_rest.py` talks to PostgREST
directly.

**This reasoning held for model (1) only** — Daniel's own laptop (or Mac
Mini), where `TRIGGER_SECRET_KEY` lives in `.env.local`. It does NOT hold for
a cloud-dispatched orchestration session (a claude.ai routine, zero human
present): that session has no confirmed way to receive `TRIGGER_SECRET_KEY`
either, so "no new platform-alpha route is needed" is no longer true without
qualification. A new route (`/api/model-factory/launch`) now exists for
exactly that case — see "Relay transport" below, which is the reversal this
file's fallback exists for. Direct REST (this section) is still what runs
whenever `TRIGGER_SECRET_KEY` is available; nothing about it changed.

Endpoints used (Trigger.dev Management API, verified 2026-08-19):
  - `POST /api/v1/tasks/{taskIdentifier}/trigger` — body `{"payload": {...},
    "options": {...}}`, header `Authorization: Bearer <secret key>`. Returns
    `{"id": "run_..."}` on success.
  - `GET /api/v3/runs/{runId}` — same auth header. Returns the run's
    `status` (`PENDING`/`EXECUTING`/`COMPLETED`/`FAILED`/...), timestamps,
    and (once terminal) `output`.

## Relay transport (model-factory-cloud-dispatch-v1)

A cloud-dispatched session never holds `TRIGGER_SECRET_KEY` (same hard
constraint as `FIREWORKS_API_KEY` and `FACTORY_SUPABASE_SERVICE_ROLE_KEY` —
see `cloud-sessions/playbooks/11-finetune-pipeline-agent.md`), so a direct
call to Trigger.dev's Management API is structurally unavailable to it.
`_resolve_transport()` is the one place that decision is made, mirroring
`supabase_rest.py`'s function of the same name: when `TRIGGER_SECRET_KEY`
resolves, `trigger_task()`/`get_run()` behave exactly as they always have
(zero change, zero new failure mode for the laptop/Mac-Mini path this file
was originally written for). Only when that key is absent AND
`FACTORY_SYNC_RELAY_URL`/`FACTORY_SYNC_RELAY_TOKEN` (the SAME two env vars
`supabase_rest.py` already checks) are both set does a call go to
platform-alpha's `/api/model-factory/launch` route instead — derived from
`FACTORY_SYNC_RELAY_URL`'s own `/sync` suffix (same origin, sibling path,
never a hardcoded URL; see `_relay_launch_url`). `trigger_task()` POSTs
there to dispatch (mapping the relay's `{"triggerRunId": ...}` response back
to this module's existing `return run_id` contract, so callers don't need to
change); `get_run()` GETs it with a `?triggerRunId=` query param to poll,
returning the relay's JSON body as-is since it's already shaped like the
direct path's own `status`/`output`/timestamps return value.

That route holds no new Trigger.dev secret either: it dispatches via
`tasks.trigger(...)`, the same zero-explicit-client-construction call every
other Trigger.dev task in platform-alpha already uses, relying on the SDK's
automatic `TRIGGER_SECRET_KEY`/`TRIGGER_ACCESS_TOKEN` env var pickup
(injected by Trigger.dev's own Vercel integration) rather than a secret this
file or the cloud session ever holds. The relay token authorizes exactly one
Trigger.dev task (`factory-launch-monitor`) server-side — see that route's
own module doc for the server-side half of this contract. Unlike
`supabase_rest.py`'s `RELAY_ALLOWED_TABLES`, there is no client-side
allow-list constant here: a single-task relay has nothing meaningful left to
enumerate, so this file trusts the route's own hardcoded behavior as the sole
enforcement point.

## What this file deliberately does NOT do

It never touches Fireworks. `FIREWORKS_API_KEY` lives ONLY in the Trigger.dev
dashboard's env vars, read by `factory-launch-monitor.ts` — never by this
process, never by any cloud Claude Code orchestrator. This file's own secret
(`TRIGGER_SECRET_KEY`) authorizes dispatching/polling a Trigger.dev task, not
spending money at a fine-tuning provider; the S6g human gate is what actually
authorizes spend, upstream of this module ever being called. That holds
whichever transport runs — the relay route fires the same single hardcoded
task and never receives `FIREWORKS_API_KEY` any more than this file does.
"""

import os

import requests

DEFAULT_TIMEOUT_S = 15
# `TRIGGER_DEV_API_BASE_URL` is a TEST-ONLY escape hatch (T2-5's local proof,
# mirroring `factory-launch-monitor.ts`'s own `FIREWORKS_API_BASE_URL`): lets
# a local stub HTTP server stand in for the real Trigger.dev Management API
# so `stage_train`'s dispatch can be exercised as a real local HTTP round
# trip. Unset in every real invocation — production always uses the real URL.
TRIGGER_API_BASE = os.environ.get("TRIGGER_DEV_API_BASE_URL") or "https://api.trigger.dev/api"


class TriggerDevRestError(RuntimeError):
    """Raised on any non-2xx response or transport failure talking to Trigger.dev."""


def _relay_launch_url(sync_relay_url):
    """Derive this deployment's `/api/model-factory/launch` URL from its
    `/api/model-factory/sync` sibling (`FACTORY_SYNC_RELAY_URL` — the SAME
    env var `supabase_rest.py` already reads for the sync relay). Same
    origin, sibling path — never a hardcoded full URL.
    """
    base = sync_relay_url.rstrip("/")
    parent, sep, last = base.rpartition("/")
    if not sep or last != "sync":
        raise TriggerDevRestError(
            f"FACTORY_SYNC_RELAY_URL {sync_relay_url!r} does not end in '/sync' — "
            "cannot derive its '/launch' sibling"
        )
    return f"{parent}/launch"


def _resolve_transport(secret_key=None):
    """Decide direct-REST vs. relay, once, for one call — mirrors
    `supabase_rest.py`'s function of the same name. Direct wins whenever a
    secret key resolves (explicit `secret_key` or `TRIGGER_SECRET_KEY`): this
    is what keeps `trigger_task()`/`get_run()`'s existing behavior
    byte-identical for every caller that already has one. Relay is the
    fallback, not an alternative a caller opts into — it activates only when
    direct is genuinely unavailable AND `FACTORY_SYNC_RELAY_URL`+
    `FACTORY_SYNC_RELAY_TOKEN` are both set.

    Returns `(mode, target, cred)`. For `"direct"`, `target` is `None` — the
    direct endpoint is `base_url`, a parameter of `trigger_task`/`get_run`
    themselves (with its own `TRIGGER_DEV_API_BASE_URL` test override), not
    resolved here. For `"relay"`, `target` is the derived
    `/api/model-factory/launch` URL both the dispatch (POST) and poll (GET)
    relay calls hit.
    """
    key = secret_key or os.environ.get("TRIGGER_SECRET_KEY")
    if key:
        return ("direct", None, key)
    relay_url = os.environ.get("FACTORY_SYNC_RELAY_URL")
    relay_token = os.environ.get("FACTORY_SYNC_RELAY_TOKEN")
    if relay_url and relay_token:
        return ("relay", _relay_launch_url(relay_url), relay_token)
    raise TriggerDevRestError(
        "no transport configured: neither TRIGGER_SECRET_KEY (direct) nor "
        "FACTORY_SYNC_RELAY_URL+FACTORY_SYNC_RELAY_TOKEN (relay) are set, "
        "and no key was passed explicitly"
    )


def _relay_trigger_task(relay_url, relay_token, task_identifier, payload, timeout):
    """POST to the launch relay's one endpoint — the relay-mode body of
    `trigger_task()`. `task_identifier` is only used in error messages here:
    the relay route itself always fires `factory-launch-monitor` regardless
    of what's passed (see that route's own module doc), and this file does
    not duplicate that as a client-side allow-list — a single-task relay has
    nothing meaningful left to enumerate.
    """
    headers = {"Authorization": f"Bearer {relay_token}", "Content-Type": "application/json"}
    try:
        resp = requests.post(relay_url, json=payload, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise TriggerDevRestError(f"relay trigger {task_identifier} transport error: {exc}") from exc
    if not resp.ok:
        raise TriggerDevRestError(
            f"relay trigger {task_identifier} failed: {resp.status_code} {resp.text[:500]}"
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise TriggerDevRestError(f"relay trigger {task_identifier}: non-JSON response") from exc
    run_id = data.get("triggerRunId")
    if not run_id:
        raise TriggerDevRestError(f"relay trigger {task_identifier}: response had no triggerRunId: {data}")
    return run_id


def _relay_get_run(relay_url, relay_token, run_id, timeout):
    """GET the launch relay's polling side-channel (`?triggerRunId=<run_id>`
    on the same URL `_relay_trigger_task` POSTs to). Returns the relay's JSON
    body as-is: it's already shaped like the direct path's own `resp.json()`
    return (`status`/`output`/timestamps), so `get_run()`'s callers don't
    need to know which transport ran.
    """
    headers = {"Authorization": f"Bearer {relay_token}"}
    params = {"triggerRunId": run_id}
    try:
        resp = requests.get(relay_url, headers=headers, params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise TriggerDevRestError(f"relay get_run {run_id} transport error: {exc}") from exc
    if not resp.ok:
        raise TriggerDevRestError(f"relay get_run {run_id} failed: {resp.status_code} {resp.text[:500]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise TriggerDevRestError(f"relay get_run {run_id}: non-JSON response") from exc


def trigger_task(
    task_identifier,
    payload,
    options=None,
    secret_key=None,
    base_url=TRIGGER_API_BASE,
    timeout=DEFAULT_TIMEOUT_S,
):
    """`POST /api/v1/tasks/{taskIdentifier}/trigger` — dispatches a Trigger.dev
    task with `payload` as its typed input (here, T2-1's `FactoryLaunchConfig`)
    and returns the new run's id (e.g. `"run_abc123"`).

    `options` mirrors the Management API's own shape (`idempotencyKey`,
    `tags`, `machine`, etc.) — passed through untouched, never guessed at
    here, when calling Trigger.dev directly. Raises `TriggerDevRestError` on
    any failure; never swallows (T2-3's caller, `stage_train`, decides
    fail-open vs fail-closed for itself, same division of responsibility as
    `supabase_rest.py`'s module docstring).

    Falls back to the platform-alpha launch relay (see the module doc's
    "Relay transport" section) when `TRIGGER_SECRET_KEY` is absent and the
    relay env vars are set — direct REST stays byte-identical otherwise.
    `options` is NOT forwarded over the relay: the route computes the same
    `idempotencyKey` itself, deterministically, from `payload["runId"]`, so
    sending it would just add an unexpected key to `factory-launch-monitor`'s
    payload for no benefit.
    """
    mode, target, cred = _resolve_transport(secret_key)
    if mode == "relay":
        return _relay_trigger_task(target, cred, task_identifier, payload, timeout)

    endpoint = f"{base_url}/v1/tasks/{task_identifier}/trigger"
    headers = {
        "Authorization": f"Bearer {cred}",
        "Content-Type": "application/json",
    }
    body = {"payload": payload}
    if options:
        body["options"] = options
    try:
        resp = requests.post(endpoint, json=body, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise TriggerDevRestError(f"trigger {task_identifier} transport error: {exc}") from exc
    if not resp.ok:
        raise TriggerDevRestError(
            f"trigger {task_identifier} failed: {resp.status_code} {resp.text[:500]}"
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise TriggerDevRestError(f"trigger {task_identifier}: non-JSON response") from exc
    run_id = data.get("id")
    if not run_id:
        raise TriggerDevRestError(f"trigger {task_identifier}: response had no run id: {data}")
    return run_id


def get_run(run_id, secret_key=None, base_url=TRIGGER_API_BASE, timeout=DEFAULT_TIMEOUT_S):
    """`GET /api/v3/runs/{runId}` — polls a run's status. Returns the parsed
    JSON body (`status`, `output`, timestamps, ...). Raises
    `TriggerDevRestError` on any failure — a poller that cannot distinguish
    "transient network blip" from "the run doesn't exist" should not guess;
    callers decide their own retry policy.

    Falls back to the platform-alpha launch relay's GET side-channel under
    the same conditions as `trigger_task()` — see the module doc's "Relay
    transport" section.
    """
    mode, target, cred = _resolve_transport(secret_key)
    if mode == "relay":
        return _relay_get_run(target, cred, run_id, timeout)

    endpoint = f"{base_url}/v3/runs/{run_id}"
    headers = {"Authorization": f"Bearer {cred}"}
    try:
        resp = requests.get(endpoint, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise TriggerDevRestError(f"get_run {run_id} transport error: {exc}") from exc
    if not resp.ok:
        raise TriggerDevRestError(f"get_run {run_id} failed: {resp.status_code} {resp.text[:500]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise TriggerDevRestError(f"get_run {run_id}: non-JSON response") from exc
