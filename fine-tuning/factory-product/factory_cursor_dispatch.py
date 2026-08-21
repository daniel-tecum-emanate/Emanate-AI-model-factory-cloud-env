"""C2 (factory-cursor-bridge-v1, 2026-07-29): dispatch a factory node via the Cursor SDK.

The second executor next to `tier2_run.sh` (PRContext.md C2, decisions D1/D2). A spec
opts in through its `runtime` block — `executor: cursor_sdk` — and everything else
about `dispatch_node`'s contract is preserved: same pre-flight (held-out check, gate
snapshot), same dry-run guarantee (nothing created, $0), same result-dict shape, same
fail-open sync in a `finally`. Three keys are new on the result: `cursor_agent_id`,
`cursor_run_id`, `cursor_runtime` — the product's control path (Half A's
`request_factory_agent_control` RPC + `actions/control.ts`) is keyed entirely off
them.

## What the SDK actually does on an external cancel (C2's "find out; don't guess")

Verified against the installed `cursor-sdk` (1.0.26) source, not the docs:
`Run.wait()` drains the event stream / blocks on `Client.wait_live_run` and resolves
with a terminal `RunResult` whose `status` is one of `_run_base._TERMINAL_RUN_STATUSES
= {"finished", "error", "cancelled", "expired"}`. An external `run.cancel()` — e.g.
the product's Stop button, in a different process — therefore IS observable from
inside a blocked `wait()`: the stream terminates and `status == "cancelled"` comes
back. That is what lets this module return the `stopped: True` outcome D7 requires
instead of misreporting an operator's Stop as a node failure.

## Why the row is pushed TWICE (started + finished) — recorded deviation

`PRContext.md` C2 says "persist run.id/agent.agent_id immediately after send() into
local state", and that happens (`runs/<org>/cursor_dispatch/<node>.json`). But local
state alone leaves the product blind exactly when control matters: `dispatch_node` is
synchronous, so a row pushed only from the `finally` appears AFTER the run ended —
and Half A's Stop/Pause buttons render only on an `active` row with a non-null
`cursor_agent_id`. Without a mid-run row there is nothing to click Stop on, ever, and
the PR's headline capability is unreachable from its own dispatch path. So right
after `send()` a minimal `active` row is pushed (fail-open, like every other
telemetry push), and the `finally`'s full sync UPDATEs that same row — the upsert
matches on (run_id, title). Recorded as a deviation in `PRDone.md`, not made
silently.

## What tier2's guardrails do and do not map to

  * **No `--allowed-tools`.** The Cursor agent's tool surface is Cursor's own. The
    `Task`-strip rule protects tier2's budget cap; the spec's `allowed_tools` list is
    therefore not passed anywhere.
  * **Budget/turn cap, timeout, and cost visibility — CORRECTED 2026-08-12.** This
    docstring previously claimed none of tier2's guardrails had a Cursor SDK equivalent
    ("a cloud agent runs until it finishes... `timeout_s` is accepted but NOT enforced").
    That was true only because nobody had checked: the installed `cursor-sdk` (1.0.26)
    does expose `Run.cancel()` and `Agent.get_usage()` (real dollar cost). This is the
    documented root cause of the 2026-07-10 -> 2026-08-05 Cursor credit-usage incident
    (`incident-reports/cursor-credit-overrun-2026-08/`) — a month of parallel Cursor
    fan-outs ran with none of these controls wired up, on top of a separate written
    "Cursor is free" assumption, until Cursor cut the account off on an unpaid invoice.
    `execute()` below now routes through `scripts/lib/cursor_guard.py`: a pre-flight
    dispatch-rate check, a real wall-clock timeout via `run.cancel()`, and real per-run
    cost via `agent.get_usage()` recorded onto the result dict.

## STOP sentinel

`tier2_run.sh`'s STOP-sentinel check ("a single file halts all headless spend") is
reproduced here as a pre-flight refusal, because it is the one tier2 guardrail whose
absence would silently widen spend the day a spec opts in. Checked before any SDK
call — alongside the dispatch-rate check added 2026-08-12 (see above).
"""

import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from cursor_sdk import (
    Agent,
    AgentOptions,
    CloudAgentOptions,
    CloudRepository,
)
from cursor_sdk.errors import CursorAgentError

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

sys.path.insert(0, str(REPO / "scripts" / "lib"))
import cursor_guard  # noqa: E402 — path must be set first

# Same sentinel tier2_run.sh checks (scripts/tier2_run.sh: `[ -f "$REPO/STOP" ]`).
STOP_SENTINEL = REPO / "STOP"

DEFAULT_CURSOR_RUNTIME = "cloud"  # D2 — cloud by default, local per-spec override.

_KEY_PATTERN = re.compile(r"crsr_[A-Za-z0-9]+")


def _scrub_secret(text):
    """Redact the Cursor API key (by value and by prefix pattern) from any error
    text that could end up in a result dict, a log line, or the DB. Mirrors Half
    A's `control.ts` scrubbing — invariant 15's "no key value in anything
    client-visible" applies to Half B's result dicts too, because they are synced.
    """
    text = _KEY_PATTERN.sub("crsr_[redacted]", str(text))
    key = os.environ.get("CURSOR_API_KEY")
    if key:
        text = text.replace(key, "crsr_[redacted]")
    return text


def _git(args):
    proc = subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def _cloud_repo():
    """This repo's origin URL + current branch, for the cloud VM's clone.

    C1's spike finding 5 applies to every cloud dispatch: the VM clones from
    ORIGIN, so it sees pushed commits only. A dispatch from a branch that was
    never pushed fails at startup with the SDK's (misleading) "failed to verify
    existence of branch" error — which is a startup refusal, not a hang, so it
    degrades to the `startup_error` path below rather than needing pre-flight
    remote checks here.
    """
    url = _git(["remote", "get-url", "origin"])
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    return url, branch


def _state_path(org_slug, node_id):
    return HERE / "runs" / org_slug / "cursor_dispatch" / f"{node_id.replace(':', '-')}.json"


def _persist_ids(org_slug, node_id, run_id, payload):
    """SDK skill best practice #3 / PRContext C2: the IDs hit disk immediately
    after `send()`, before any streaming or waiting, so a crash mid-dispatch
    leaves a recoverable agent id rather than an orphan nobody can find. Under
    `runs/`, which is gitignored — same treatment as `.sync_state.json`.
    """
    path = _state_path(org_slug, node_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"node_id": node_id, "factory_run_id": run_id,
                                "ts": datetime.now().astimezone().isoformat(), **payload}, indent=2))


def _build_options(runtime, cursor_runtime, api_key, env_keys=None):
    kwargs = {"api_key": api_key}
    if runtime.get("cursor_model"):
        kwargs["model"] = runtime["cursor_model"]
    if cursor_runtime == "cloud":
        url, branch = _cloud_repo()
        cloud_kwargs = {
            "repos": [CloudRepository(url=url, starting_ref=branch)],
            "skip_reviewer_request": True,
        }
        # C1 (b): the SDK's documented mechanism for handing a cloud agent the
        # env values a spec declares (workspace.env_keys). Only keys actually
        # present in this process's environment are forwarded — a missing one
        # is the agent's problem to report, not something to guess at here.
        # NOTE: unverified end-to-end while C1 is blocked on the GitHub
        # connector; the pilot spec (corpus-reader) declares none.
        env_vars = {k: os.environ[k] for k in (env_keys or []) if k in os.environ}
        if env_vars:
            cloud_kwargs["env_vars"] = env_vars
        kwargs["cloud"] = CloudAgentOptions(**cloud_kwargs)
    return AgentOptions(**kwargs)


def execute(
    spec,
    node_id,
    stage_code,
    run_id,
    graph_id,
    org_slug,
    result,
    timeout_s=900,
    context_pack=None,
    sync_url=None,
    ledger_dir=None,
):
    """Execute one already-resolved node dispatch through the Cursor SDK.

    Called by `dispatch_node` AFTER the shared pre-flight (spec load, held-out
    check, result seeding, dry-run early return) — so a dry run never reaches
    this function at all, which is what keeps the "dry-run creates no agent"
    guarantee in one place instead of two.

    Returns the same result dict `dispatch_node` seeded, updated in place, with
    the three cursor keys added. Outcome vocabulary:

      * `ok: True`                     — ran to `finished` (verifiers: + verdict pass)
      * `ok: False, stopped: True`     — an operator cancelled it (D7: halts the
                                         chain but is NOT a spec-reliability failure)
      * `ok: False, startup_error: …`  — never executed (`CursorAgentError` from
                                         create/send, missing key, STOP sentinel)
      * `ok: False` (neither key)      — executed and failed (`error`/`expired`)

    `timeout_s` is now actually enforced via `cursor_guard.enforce_timeout()` (module
    docstring, corrected 2026-08-12).
    """
    # Late import: factory_node_run imports this module lazily inside the
    # executor branch, and this module needs its helpers — top-level imports in
    # both directions would be a cycle.
    import factory_node_run as fnr

    runtime = result.get("runtime") or {}
    cursor_runtime = runtime.get("cursor_runtime") or DEFAULT_CURSOR_RUNTIME
    result["cursor_runtime"] = cursor_runtime

    prompt_dir = HERE / "runs" / org_slug / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = prompt_dir / f"{node_id.replace(':', '-')}.md"
    prompt_text = fnr.build_prompt(spec, node_id, run_id, graph_id, org_slug, context_pack)
    prompt_file.write_text(prompt_text)
    result["prompt_file"] = str(prompt_file.relative_to(REPO))

    if STOP_SENTINEL.is_file():
        result.update({
            "dispatched": False,
            "ok": False,
            "startup_error": f"STOP sentinel present at {STOP_SENTINEL} — refusing to dispatch (same rule as tier2_run.sh)",
        })
        return result

    api_key = os.environ.get("CURSOR_API_KEY")
    if not api_key:
        result.update({
            "dispatched": False,
            "ok": False,
            "startup_error": "CURSOR_API_KEY is not set — a cursor_sdk-executor spec cannot dispatch without it (workflow root .env.local has it; export it into this process's environment)",
        })
        return result

    try:
        cursor_guard.check_rate_limit(f"factory_cursor_dispatch:{node_id}")
    except cursor_guard.CursorRateLimitExceeded as exc:
        result.update({
            "dispatched": False,
            "ok": False,
            "startup_error": f"cursor_guard rate limit: {exc}",
        })
        return result

    import factory_gates
    gates_before = factory_gates.snapshot(org_slug)

    started_at = datetime.now().astimezone()
    try:
        try:
            agent = Agent.create(_build_options(
                runtime, cursor_runtime, api_key,
                env_keys=(spec.get("workspace") or {}).get("env_keys"),
            ))
        except CursorAgentError as exc:
            # Never executed, nothing spent, no agent exists. The one outcome
            # that must stay distinguishable from "ran and failed" (PRTests C2).
            result.update({"dispatched": False, "ok": False, "startup_error": _scrub_secret(exc)})
            return result

        with agent:
            try:
                run = agent.send(prompt_text)
            except CursorAgentError as exc:
                result.update({"dispatched": False, "ok": False, "startup_error": _scrub_secret(exc)})
                return result

            result["cursor_agent_id"] = agent.agent_id
            result["cursor_run_id"] = run.id
            _persist_ids(org_slug, node_id, run_id, {
                "cursor_agent_id": agent.agent_id,
                "cursor_run_id": run.id,
                "cursor_runtime": cursor_runtime,
            })
            cursor_guard.record_dispatch(f"factory_cursor_dispatch:{node_id}", agent.agent_id, run.id)
            # The recorded deviation (module docstring): a minimal `active` row
            # goes up NOW, fail-open, so the product's Stop/Pause buttons have a
            # controllable row to render against while wait() blocks below.
            try:
                import factory_sync
                factory_sync.push_node_agent_started(
                    result, run_id, stage_code, spec,
                    started_at=started_at, url=sync_url,
                )
            except Exception as exc:  # noqa: BLE001 — telemetry, never blocks the dispatch
                log.warning("factory_cursor_dispatch: started-row push failed for %s: %s", node_id, exc)

            terminal = cursor_guard.enforce_timeout(run, timeout_s=timeout_s)
            # Recorded BEFORE the gate assert below: cursor_status doubling as
            # the "outcome is known" marker the finally keys on. A
            # GateForgeryError must still produce a full final sync (the run IS
            # over), unlike a crash inside wait() (where it may not be).
            result["cursor_status"] = terminal.status
            cost = cursor_guard.fetch_cost(agent, run.id)
            result["cursor_cost_usd"] = cost
            cursor_guard.record_completed(
                agent.agent_id, run.id, status=terminal.status,
                cost_usd=(cost or {}).get("charged_usd"),
            )

        factory_gates.assert_unchanged(org_slug, gates_before, node_id=node_id)

        status = terminal.status
        text = (terminal.result or "").strip()
        verdict = fnr.parse_verifier_verdict(text) if spec.get("role_family") == "verifier" else None
        stopped = status == "cancelled"
        ok = status == "finished"
        if spec.get("role_family") == "verifier":
            ok = ok and verdict == "pass"

        result.update({
            "dispatched": True,
            "ok": ok,
            "verdict": verdict,
            "final_response": text or None,
        })
        if stopped:
            # D7's third outcome: the chain halts (same as a failure) but nothing
            # downstream may read this as the SPEC being unreliable.
            result["stopped"] = True
        else:
            # No subprocess here; the honest mapping is finished -> 0, else 1.
            # A stopped run gets NO exit_code at all — it neither succeeded nor
            # failed, and a fabricated 1 would feed exactly the reliability
            # misread D7 exists to prevent.
            result["exit_code"] = 0 if status == "finished" else 1

        try:
            import factory_decisions
            factory_decisions.record(
                run_id=run_id,
                graph_id=graph_id,
                node_id=node_id,
                decision_point=f"{stage_code}-node-dispatch",
                options=[result.get("spec")],
                chosen=result.get("spec"),
                rationale=(
                    "stopped by operator" if stopped
                    else (f"verdict={verdict}" if verdict else f"cursor_status={status}")
                ),
                actor="agent",
                org_slug=org_slug,
                context_refs=[result["prompt_file"]],
                ledger_dir=ledger_dir,
                sync_url=sync_url,
            )
        except Exception as exc:  # noqa: BLE001 - the log must never break the dispatch
            log.warning("factory_cursor_dispatch: decision log failed for %s: %s", node_id, exc)

        return result
    finally:
        # Same contract as dispatch_node's finally: on the exception path the
        # agent may exist and be spending, so "dispatched" defaults to True
        # (the startup_error paths already set it False explicitly before
        # returning) and an unset ok must never read as success.
        result.setdefault("dispatched", True)
        result.setdefault("ok", False)
        if result.get("dispatched") and "cursor_status" in result:
            # Outcome KNOWN (wait() returned — includes the GateForgeryError
            # path, whose run is genuinely over): full final sync.
            try:
                import factory_sync
                factory_sync.push_node_agent(
                    result,
                    run_id,
                    stage_code,
                    spec,
                    prompt_file=result.get("prompt_file"),
                    started_at=started_at,
                    ended_at=datetime.now().astimezone(),
                    ledger_dir=ledger_dir,
                    url=sync_url,
                )
            except Exception as exc:  # noqa: BLE001 — fail-open; the spend already happened
                log.warning("factory_cursor_dispatch: node-agent sync failed for %s: %s", node_id, exc)
        elif result.get("dispatched"):
            # Outcome UNKNOWN — this process died inside wait() (network blip,
            # exception) while a CLOUD run may well still be executing
            # server-side. Deliberately NOT flipping the row to 'ended': that
            # would both lie and strip the operator of the Stop button at the
            # exact moment it matters most. The mid-run 'active' row stands;
            # if the run actually died, the operator's next Stop click
            # self-heals it (control.ts treats cancel-on-terminal as success
            # and the row lands 'cancelled'). The recoverable IDs are on disk
            # (`runs/<org>/cursor_dispatch/`).
            log.warning(
                "factory_cursor_dispatch: %s crashed mid-wait — outcome unknown, the run may "
                "still be live; leaving the product row 'active' (control it from the product, "
                "or reconcile with the state file under runs/%s/cursor_dispatch/)",
                node_id,
                org_slug,
            )
