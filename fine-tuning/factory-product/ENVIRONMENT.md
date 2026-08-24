# The cloud environment — architecture, the credential wall, and how to change it

**Read this before touching anything under `cloud-sessions/`, `factory_node_run.py`,
`supabase_rest.py`, or the `model-factory-finetune-orchestrator-v1` routine's saved prompt.**
This is the living design doc for "what environment do we give a cloud-dispatched Claude
Code session so it can actually get fine-tuning work done" — written 2026-08-20, revised
2026-08-21 (self_dispatch built, relay-claim exchange + launch relay close V-310/V-312/V-313's
delivery halves, then — later the same day — the S4 corpus chain opted into self_dispatch **on
`cloud-dispatch/finetune-rehearsal-v1` only, not on the branch of record** — §4, §4b, §5), last
revised **2026-08-24** (§2 rewritten: the cloud VM no longer clones
`emanate-tecum-workflow` at all — V-316's real root cause turned out to be a GitHub App access
wall, and the environment now lives in its own repo). Expected to be revised every time this
environment changes.
Keep it current rather than writing a new one next time; that's the whole point of it existing.

Companion documents: `PRs/model-factory-cloud-environment-v1/00-HISTORY.md` (the full PR and
research-doc timeline this grew out of), `cloud-sessions/playbooks/11-finetune-pipeline-agent.md`
(a **historical design record**, NOT the dispatch prompt — its own 2026-08-21 status header says
"For the actual current dispatch prompt, read the live routine, not §8 below — it has diverged,"
and §8's literal templates are wrong on all three axes: wrong repo, wrong branch — line 522 still
instructs `git fetch origin cloud-dispatch/finetune-rehearsal-v1 && git checkout
cloud-dispatch/finetune-rehearsal-v1` against `emanate-tecum-workflow` — and wrong dispatch
mechanism. The live routine's saved prompt is the only authority for the current dispatch prompt),
`cloud-sessions/playbooks/12-fleet-claims.md` (the `cloud-claim.sh` reference).

---

## 1. Three different things this codebase calls "cloud," and why the difference matters

Before designing "the environment," it's worth being precise about which execution model is
actually in scope, because this codebase has built three, and they have different credential
realities:

1. **Daniel's own laptop/Mac Mini, running `factory.py` directly.** Has `.env.local` with real
   `FIREWORKS_API_KEY`, and (once V-311 is resolved) would have `FACTORY_SUPABASE_SERVICE_ROLE_KEY`
   too. Has a working `claude` CLI on `PATH`, authenticated as Daniel. **Every real fine-tune this
   company has ever shipped ran this way** — see §4.
2. **A supervised long-running session** (`cs new --cloud`, per `cloud-sessions/LONG-RUNNING-SESSIONS.md`)
   — a human deliberately started it and is checking in on it; closer to "my laptop, but the lid can
   close." Whether it holds real credentials is a per-session human decision, not a structural rule.
3. **A claude.ai *routine*, fired via API with zero human present at dispatch time** —
   `model-factory-finetune-orchestrator-v1` (`trig_01CVaqMWD3iCMZWy5VQAwXLW`), the thing this
   initiative built so the Model Factory UI's "Start a run" button can kick off real orchestration
   with nobody watching. **This is the only one of the three that is structurally, permanently
   barred from holding `FIREWORKS_API_KEY`, `FACTORY_SUPABASE_SERVICE_ROLE_KEY`, or any other
   paid-provider credential** (`11-finetune-pipeline-agent.md`'s HARD CONSTRAINT) — nobody is
   present to notice if it goes wrong, so the blast radius of a mistake has to be capped by
   construction, not by trust.

Everything below is about (3), because it's the newest, least-proven, and most credential-constrained
of the three. Don't assume something that works for (1) works for (3) — §4 and §5 are both examples
of exactly that assumption turning out false.

## 2. What's physically in the environment

`(3)`'s `session_context.sources` clones **`daniel-tecum-emanate/Emanate-AI-model-factory-cloud-env`**
on **`main`** — a dedicated repo that carries `fine-tuning/factory-product/` and `cloud-sessions/`
at its root. There is no branch to switch to and no `git checkout` step in **the live routine's**
dispatch prompt: what the VM has on clone is already the environment. That claim is scoped to the
live routine only — the in-repo playbook `11-finetune-pipeline-agent.md`, which this doc points
readers at, still carries a `git checkout cloud-dispatch/finetune-rehearsal-v1` step at its line
522. That step is diverged and must not be followed.

**Why it's a separate repo rather than a branch of `emanate-tecum-workflow` — this was a hard
constraint, not a preference.** The Claude GitHub App has **no access to
`daniel-tecum-emanate/emanate-tecum-workflow`**, because that is a *personal-account* repo. Nothing
about the routine, the account-level GitHub connection, or `/web-setup` can grant it — all three
were suspected first, and all three were wrong. That is the real root cause behind V-316: a session
that cannot clone its own environment never reaches step 1, which is what four fires of total
silence actually looked like from the outside. The fix was to stand up
`Emanate-AI-model-factory-cloud-env` somewhere the App can genuinely see, and content-copy in the
two trees the pipeline needs. **The rule this leaves behind**: before designing any cloud
environment around a repo, confirm the GitHub App can see *that specific repo* — a personal-account
repo is invisible to it no matter how healthy the connection looks from the account settings page.

**Superseded 2026-08-21, kept because the lesson isn't**: this section used to say `(3)` clones
`emanate-tecum-workflow` on branch `cloud-dispatch/finetune-rehearsal-v1`, hand-built by
content-copying `origin/main` plus `cloud-sessions/` — because `main` doesn't contain
`fine-tuning/factory-product/` at all (a `git mv` in `c425a98` happened on a different branch
lineage). That branch still exists and is still where this initiative's own commits landed (§5's
`fa399be`, for one); it is simply no longer what the cloud VM clones. The gap found on it is the
part that carries forward: for a while it ALSO carried the entire pre-rename
`factory-automation/factory/` tree in full — 184MB, including 182MB of stale `runs/` artifacts —
because content-copying from `origin/main` picked that up wholesale and nobody had removed it. It
was a genuine stale duplicate (confirmed by diffing `factory-automation/factory/factory.py` against
the real file — missing edits, clearly frozen at whatever `origin/main` last had), not a second
live copy of anything. Deleted; the branch went to 66MB. **The lesson, not just the fix**: a
"curated environment" built by content-copying an existing branch inherits that branch's own dead
weight silently. `Emanate-AI-model-factory-cloud-env` was built the same content-copy way, so this
recurs there — check for it the next time that repo is refreshed from `origin/main`.

The environment also needs `scripts/lib/cursor_guard.py` (found missing once already — anything
that imports `factory_cursor_dispatch.py` needs it present) and a
`fine-tuning/factory-product/requirements.txt` (`pyyaml`, `requests` — nothing had ever documented
factory.py's own dependencies before).

**That `requirements.txt` does not exist on the branch of record.** It was added 2026-08-20 in
commit `8576f43` ("close cloud-env gaps found in dependency-closure audit") on
`cloud-dispatch/finetune-rehearsal-v1` only, which is not an ancestor of
`model-training-swarm-cloud-v1`: `git cat-file -e HEAD:fine-tuning/factory-product/requirements.txt`
fails, and there is no `requirements.txt` anywhere in this working tree. So it carries exactly the
drop-on-refresh hazard flagged next, in its worst form — a cloud-env refresh sourced from this repo
cannot pick it up, because there is nothing here to pick up. Likewise `cursor_guard.py` lives at the
source repo's root `scripts/lib/`, i.e. *outside* both trees the cloud-env repo copies — so any
refresh that copies only `fine-tuning/factory-product/` + `cloud-sessions/` drops it again by
construction. Verify both are actually present in the cloud-env repo rather than assuming a refresh
carried them.

**Two gotchas about *setting* this environment, both of which cost real debugging time:**

- **A `RemoteTrigger` "update" call that touches `job_config` WITHOUT also re-sending
  `session_context.sources` silently CLEARS `sources`.** Reproduced twice. Worse, the update
  response never echoes `sources` back even when they are correctly set — so you cannot tell a
  successful set from a wipe by reading the response, and it produces false negatives in both
  directions. For `sources` specifically the claude.ai UI's own save is the more reliable path; if
  you must go through the API, re-send `sources` on every update and verify by firing, not by
  reading the response.
- **`add_repo` and `register_repo_root` are not real tools.** The original routine prompt called
  them at step 0, copied from an untested template routine (`opportunities-v8-implementation`,
  which has no `last_fired_at` — it had never fired, so nobody had ever found out). Any agent
  hitting them died before reaching step 1's heartbeat. Combined with the App-access wall above,
  that fully explains the four fires of total silence. **The lesson**: a template routine that has
  never fired is not a template, and step 0 of a dispatch prompt is precisely where a death is
  invisible — the only telemetry channel (the `cloud-fleet/<session-id>` heartbeat ref) doesn't
  start until step 1.

## 3. The two coordination primitives, and what each one is actually for

Both are git refs, not a database — deliberately, so no credential is needed to write one and
any git-capable sandbox can participate.

- **`cloud-heartbeat.sh`** — "is this *session* alive, and what is it doing." Per-session
  (`refs/heads/cloud-fleet/<session-id>`). Answers liveness/phase/blocker.
- **`cloud-claim.sh`** (new this session) — "is this *(org, model_stream)* already being worked,
  by whom, and is that claim still good." Per-resource (`refs/heads/cloud-claims/<org>/<stream>`).
  Mutual exclusion, not liveness — a session can be alive and still not hold the claim it needs.

**These are not the swarm mechanism, and don't replace it.** `fine-tuning/factory-product/swarm/`
(`protocol.py`, `runner.py`, `workspace.py`) is a separate, older, richer coordination layer — its
own manifest/lease/role/state-capsule protocol for running MANY roles against ONE org's fine-tune,
and per `PRs/model-factory-cloud-environment-v1/01-grand-steel-ptc-steel-archaeology.md` it's the
only mechanism that has ever actually produced a shipped model (twice). `cloud-claim.sh` solves a
narrower, different problem: keeping two *separate dispatches* (e.g., a stale retry and a fresh
fire) from colliding on the same org — wired into the dispatch prompt as claim/renew/release calls
at every heartbeat point (added this session), but still advisory only: nothing at the
`factory_node_run.dispatch_node()` level enforces it (§7's open question 3 — should it?).

**Important, and easy to miss**: read `swarm/runner.py`'s own docstring — "Fail-closed bridge from
swarm work orders to **the existing factory dispatcher**." The swarm layer does not have its own
independent agent-spawning mechanism; `runner.py:341` wires its dispatcher parameter straight to
`factory_node_run.dispatch_node` — the exact same function §5 is about. The swarm protocol adds
real value (validation, cost correlation, immutable attempt records, lease coordination) but
inherits §5's credential wall completely unchanged. Don't reach for "use the swarm mechanism
instead" as an escape from §5 — it isn't one.

## 4. What actually happens today when the pipeline needs to write to Supabase — the sync-relay

`factory_sync.py`'s every write (`factory_runs`, `factory_gate_requests`, stage detail — literally
everything the Runs/Graph tabs render) went, until this session, through `supabase_rest.py`'s
`upsert`/`insert`/`update`/`select`, which required `FACTORY_SUPABASE_SERVICE_ROLE_KEY` — a
credential (3) can never hold. The practical consequence, undiscovered until this session: a
cloud-dispatched run's on-disk reports were fine, but **nothing ever reached Supabase** — no gate
request row for S2/S6g, no stage detail, nothing for the Runs/Graph tabs to show. The only signal
ever *designed* to reach the UI without the relay is the coarse heartbeat→`factory_agents` bridge
(My Sessions/Needs Attention — liveness and phase text, `run_id` always null, no gate cards) —
**but it has never produced a row for a cloud-dispatched run.** `agents_sync_job.py` is run-scoped:
it only syncs sessions for a run that has a local `runs/<slug>/.sync_state.json` whose
`factory_runs.status` is `running`/`blocked_on_gate`. And per V-311 — **still OPEN** — this
machine's repo-root `.env.local` carries no `FACTORY_SUPABASE_URL` or
`FACTORY_SUPABASE_SERVICE_ROLE_KEY` at all, so the job cannot write to Supabase from here in the
first place. So the UI got nothing from a cloud-dispatched run: not a degraded signal, none.

**The fix — both halves now built and tested**: `supabase_rest.py`'s four primitives call
`_resolve_transport()` first. Direct PostgREST wins whenever a service-role key is available
(byte-for-byte the same behavior as before — every existing caller, every existing test, unchanged).
Only when that's absent AND `FACTORY_SYNC_RELAY_URL`/`FACTORY_SYNC_RELAY_TOKEN` are both set does a
call instead POST to platform-alpha's `POST /api/model-factory/sync` route (PR #2469,
`src/app/api/model-factory/sync/route.ts`) — one generic, allow-listed forwarder
(`RELAY_ALLOWED_TABLES`, defined identically in both this route and `supabase_rest.py` — the route
is the real enforcement boundary, the client-side copy is defense-in-depth) that holds the real
Supabase credential itself and never returns it. The relay token is a new, narrowly-scoped secret —
verified server-side with a constant-time compare, mirroring the existing
`FACTORY_DISPATCH_API_TOKEN`/`MERGE_WEBHOOK_SECRET` pattern (a scoped API token, not the underlying
infra credential) — **not** equivalent in blast radius to leaking the service-role key, since it
can only ever touch the 6 tables in the allow-list, which already have no RLS policies and no
anon/authenticated grants either way. 48 tests on the route, 15 on the Python transport.

**A second gap, found and fixed only by tracing the real call chain before firing again**:
`factory.py`'s own `_sync_url(args)` — the single truthiness gate every one of ~14 sync call sites
(gate push/poll, run/stage row writes, decision logs, node-dispatch sync) checks *before* ever
reaching `factory_sync.py`/`supabase_rest.py` — only checked `FACTORY_SUPABASE_URL`. A cloud session
only ever has the relay pair set, so every one of those call sites would have silently concluded
"no sync configured" and skipped, and the entire relay above would have been unreachable dead code.
Fixed with one added `or os.environ.get("FACTORY_SYNC_RELAY_URL")`; traced every real call site to
confirm they all thread through this one function before trusting that the fix cascades (5 new
tests, `test_sync_url.py`). **The lesson**: a transport-layer fix is not the same claim as
"the pipeline will actually call the transport layer" — the higher-level gate needs the same check,
separately, and nothing forces you to remember that.

**V-312/V-313 closed differently than either open question above anticipated (2026-08-21)**: rather
than resolve "how does a value reach a routine's sandbox environment" (still genuinely unconfirmed
— the `environment_id` question in §7 is unanswered), the fix sidesteps it entirely. `relay-claim.ts`
+ `sync/exchange/route.ts` (`platform-alpha`, same PR) mint a short-lived (30 min), HMAC-signed,
single-run claim code and put THAT in the routine's fire payload instead of the real token —
`dispatch-routine.ts`'s `fireFactoryDispatchRoutine` now does this automatically whenever
`FACTORY_SYNC_RELAY_TOKEN` is configured. The cloud session's first real step (dispatch prompt
step 2) POSTs that code to `/api/model-factory/sync/exchange` and gets back the real
`FACTORY_SYNC_RELAY_URL`/`FACTORY_SYNC_RELAY_TOKEN` to export into its own shell session — no
pre-configured env var needed on the cloud side at all, which is exactly what made V-312 stuck.
The tradeoff is stated honestly in `relay-claim.ts`'s own module doc: the claim code IS still
visible in routine run history for 30 minutes; what changes is that leaking it buys nothing after
it expires, unlike the permanent shared token. **Still needs a human**: `FACTORY_SYNC_RELAY_TOKEN`
itself needs a real value in platform-alpha's Vercel env (the exchange route can't verify a secret
that was never set) — that part of V-312 is unchanged, only the *delivery* half is solved.

## 4b. The S7 launch dispatch's OWN credential wall — a simpler problem, closed in code the same session

`_dispatch_launch_task` (factory.py) → `trigger_dev_rest.trigger_task()` makes ONE direct REST call
to Trigger.dev's Management API, authenticated with `TRIGGER_SECRET_KEY` — a credential a cloud
session has no more claim to than `FACTORY_SUPABASE_SERVICE_ROLE_KEY`. This is a narrower problem
than §5's sub-agent dispatch wall (one HTTP call, not a spawned agent), and it closed the same way
the sync-relay did, with one added fact that made it easy: **platform-alpha's own Vercel deployment
already fires Trigger.dev tasks successfully today** — confirmed by reading real code, not assumed
— every one of its ~30 existing `tasks.trigger(...)` call sites works with zero explicit client
construction, because the `@trigger.dev/sdk/v3`'s `apiClientManager` reads
`TRIGGER_SECRET_KEY`/`TRIGGER_ACCESS_TOKEN` from `process.env` automatically and Trigger.dev's own
Vercel integration injects it on deploy. A NEW call site needs no new secret provisioning.

That made the fix simple: `POST/GET /api/model-factory/launch` (platform-alpha,
`src/app/api/model-factory/launch/route.ts`) fires `factory-launch-monitor` via `tasks.trigger()`
server-side, authenticated with the SAME `FACTORY_SYNC_RELAY_TOKEN` the exchange above already
hands the cloud session — no second credential to deliver. `trigger_dev_rest.py`'s `trigger_task()`/
`get_run()` mirror `supabase_rest.py`'s own `_resolve_transport()` pattern exactly: direct REST,
byte-identical, when `TRIGGER_SECRET_KEY` is available (Daniel's laptop); the relay only when it's
absent and the sync-relay pair is set. **What this does NOT close**: `FIREWORKS_API_KEY` still
lives only in the Trigger.dev dashboard's own env, read only by `factory-launch-monitor.ts` — this
route dispatches that task, it never touches or needs the Fireworks key itself, same boundary as
before. And it does not touch §5's separate wall: `stage_train`'s AgentSpec chain
(`train-launcher`) still runs BEFORE this dispatch is ever attempted, and that chain hits §5's wall
unchanged — `train-launcher.yaml` hasn't been opted into `self_dispatch` either (§5's own note on
why no spec was flipped this round). Closing this specific wall only matters once something gets
past that chain.

**Closed at the code and credential-design level only — this relay has never been invoked.** No
gate has ever been approved on a real run, so S7 has never been reached in the cloud, and no cloud
session has ever called `/api/model-factory/launch`. Unexercised end to end.
`PRs/model-factory-cloud-dispatch-v1/ARCHITECTURE-STATUS.csv` marks the launch-relay row
BUILT-UNVERIFIED ("no cloud session has ever called the relay"), and that is the accurate status.

## 5. The credential wall for sub-agent dispatch — the mechanism is built and tested; no chain has run it for real yet

Read this before promising anyone "the cloud session distributes corpus-building work across
agents" — **on the branch of record (`model-training-swarm-cloud-v1`) nothing has opted into the
fix below.** `grep -rn executor fine-tuning/factory-product/specs/*.yaml` returns exactly two hits,
both in `corpus-reader.yaml` (`executor: cursor_sdk`); `corpus-planner.yaml` has no `runtime:`
block at all. All 41 specs except `corpus-reader.yaml` still default to tier2. The three-spec S4
opt-in described later in this section exists ONLY on `cloud-dispatch/finetune-rehearsal-v1`, and
even there it has never executed outside `--dry-run`. So every real S4/S5/S7/S8/S10 dispatch still
hits the wall exactly as described. What changed 2026-08-20 (same round as the sync-relay): the fix
is real, tested code rather than a design — though the mechanism itself is also still uncommitted
on the branch of record (`_dispatch_self` is absent from HEAD's `factory_node_run.py`; it exists
here only as a working-tree modification).

**The mechanism**: `factory.py`'s S4 (build), S5 (verify), S7's `train-launcher` node, S8 (eval),
and S10 (activate) all call `_dispatch_stage_nodes()` → `factory_node_run.dispatch_node()`. Under
`dry_run=True` (the default), this is completely safe and needs nothing — it renders a prompt and
returns, "nothing spawned, $0." **Under a real, non-dry-run dispatch**, `dispatch_node()` branches
on the AgentSpec's `runtime.executor`:

- `cursor_sdk` (opted into by a handful of specs, e.g. `corpus-reader.yaml`) → `factory_cursor_dispatch.execute()`, which needs `CURSOR_API_KEY` — a real, paid, server-only credential (`platform-alpha`'s own `CLAUDE.md` documents it as such).
- anything else (the default; most of the 41 specs, including the ones S4's real corpus-building chain — `corpus-planner`, `corpus-swarm-architect`, `corpus-family-generator` — actually uses) → `scripts/tier2_run.sh`, which resolves a local `claude` CLI binary on `PATH` and spawns it as a subprocess with its own model/turn/budget flags, backed by *that machine's own* Anthropic authentication.

**Both paths need a credential that (3) — the fully autonomous, unsupervised routine session — is
structurally barred from holding**, by the exact same reasoning that bars `FIREWORKS_API_KEY`. This
is not a gap in what got built; it's a gap in what CAN be built without a real design decision,
because the existing mechanism was written for (1) — Daniel's own laptop, where holding these
credentials is normal and already how every real fine-tune has been produced (§1, §3's swarm note).

**What this means concretely**: point the routine at a real org today, and it can walk S1 (census),
S2 (governance), S3 (export), S6 (project), and the S6g human gate with zero problem — none of
those call `dispatch_node()` for real work. It will genuinely stall the moment it reaches S4 in
anything other than `--dry-run` mode, because there is no credentialed way for it to run
`corpus-planner` for real. This is why this round's proven-working claim is scoped to the cloud
VM's dry-run walk S1–S6 and its correct stop at the S6g gate — nothing more, and certainly not "a
cloud session produced a real corpus." The sync-relay and the human-gate loop are code-complete and
unit-tested but **have never been exercised by a real run**: the one live fire (2026-08-21, session
`cse_01Dv58pfbxJJLKKiHHJQrxPx`) was fired manually with no relay claim code, so it synced not one
row and no gate card was ever produced — and `FACTORY_SYNC_RELAY_TOKEN` is still not confirmed set
in platform-alpha's Vercel production env, without which the exchange route fails closed with 503.

**Built and tested this session**: the routine session dispatched via the claude.ai API *is
itself* a real Claude Code agent with `Task` in its own `allowed_tools` (confirmed directly —
`RemoteTrigger get` on the live routine shows it). That means IT can dispatch sub-agents natively,
the same way this very session has used the `Agent` tool all day, with zero extra credential.
`executor: self_dispatch` (new AgentSpec `runtime.executor` value, `specs/schema.json`) is exactly
this — landed on `cloud-dispatch/finetune-rehearsal-v1` **only** (commit `fa399be`, 16 tests,
`tests/test_self_dispatch.py`). `fa399be` is **not** an ancestor of the branch of record, and
`_dispatch_self` is absent from HEAD's committed `factory_node_run.py`. Whether the
`Emanate-AI-model-factory-cloud-env` copy the VM actually clones (§2) carries this code at all is
unverified — check that repo directly before assuming the cloud VM has it:

1. `dispatch_node()`, on `executor: self_dispatch`, does everything it already does up through
   writing the rendered prompt file (`build_prompt()` — the same one tier2/cursor_sdk both use) —
   then, instead of shelling out, persists a small pause-state file (the pre-dispatch gate
   snapshot, the start time — `factory_node_run._self_dispatch_state_paths()`) and **returns a
   structured `self_dispatch_required` result** (prompt file path, model, max_turns, budget_usd,
   node_id) instead of `ok`/`dispatched`.
2. The *orchestrating* Claude Code session (never `factory.py`'s own Python process, which cannot
   call `Task` — only a live agent turn can) reads that prompt file, dispatches it itself via its
   own `Task` tool, then re-invokes the exact same `factory.py --stage <x>` (or
   `factory_node_run.py`'s own CLI) with `--self-dispatch-result NODE_ID=PATH` — a JSON file
   shaped `{final_response, cost_usd, exit_code}` — so `dispatch_node()` picks the pause state back
   up, runs the identical gate-forgery check / verdict parsing / decision log / sync push a real
   tier2 dispatch would have, and finalizes.
3. **The safety question ENVIRONMENT.md previously flagged as unanswered — answered, honestly, not
   solved to parity**: `tier2_run.sh`'s hard `--max-budget-usd` kills the process mid-flight;
   nothing here can kill a `Task` call mid-flight, so cost enforcement is a hard **refusal on a
   missing or over-budget self-report**, not independent metering. A resume that omits `cost_usd`
   or reports more than the spec's `budget_usd` is refused (`ok: False`) regardless of what the
   sub-agent actually produced. This is weaker than tier2's guarantee and the code says so in its
   own docstring — don't oversell it as equivalent.
4. Existing chain-stop semantics (`dispatch_stage`'s "stop at the first `not ok`") now correctly
   pause — not fail — at a pending `self_dispatch_required` node; `factory.py`'s five stage
   functions (S4/S5/S7/S8/S10) each report a `needs_self_dispatch` status distinct from
   `error`/`blocked`/`todo`.

**What's still true today, updated 2026-08-21 later the same day**: the first attempt — flipping
all of S4/S5/S7/S8/S10 at once — was tried and reverted this same session: it silently broke ~24
existing tests in `test_cursor_dispatch.py`/`test_node_dispatch.py`/`test_node_dispatch_sync.py`/
`test_graph_artifacts.py`/`test_launch_auto_approve.py` that use those exact specs as fixtures to
verify tier2's own mechanics (subprocess mocking, decision events, verdict parsing, chain-stop
behavior) — those tests needed real, deliberate updates, not a silent YAML edit.

**On `cloud-dispatch/finetune-rehearsal-v1` only** — not on the branch of record, where all three
still default to tier2 (grep-verified, see the top of this section) — a second, narrower round
opted in exactly the S4 corpus chain's builder/consolidator specs — `corpus-planner.yaml`,
`corpus-family-generator.yaml`, `curation-consolidator.yaml` (`runtime.executor: self_dispatch`) —
and fixed what broke instead of reverting again. That broke
10 tests this time (not ~24), all in `test_node_dispatch.py` (4) and `test_node_dispatch_sync.py`
(6), all using `corpus-planner` as a convenient real `builder`-role spec to exercise tier2's own
generic mechanics (chain-stop-on-first-failure, verdict gating, decision-event emission, the
paid-spec guard, sync-push row/event shape on success/failure/exception/outage) rather than
anything about `corpus-planner`'s own content. Each was redirected to a synthetic fixture
(`spec_override`, or a monkeypatched `load_spec` for the two stage-chain tests) whose `runtime`
omits `executor` — i.e., explicit tier2 — mirroring `_builder_spec()`'s shape in
`test_self_dispatch.py`, rather than rewritten to assert self_dispatch behavior instead, which would
have quietly deleted tier2's only coverage of whatever each test actually checked.
`test_gate_integrity.py`/`test_heldout_anchor.py` also dispatch "corpus-planner" by name but needed
no changes — both already monkeypatch `fnr.load_spec` to a synthetic dict on every call, so neither
ever reads the real file's `runtime.executor`. Suite confirmed back to the pre-existing (unrelated,
~20-failure) baseline after the fix, not baseline-plus-breakage.

**Still not opted in, deliberately**: `corpus-reader.yaml` (unchanged, still `cursor_sdk` — a
different, already-live mechanism, outside this round's scope) and every S5/S7/S8/S10 spec —
`preflight-linter`, `launch-rehearsal`, `train-launcher`, `eval-instrument-builder`,
`eval-window-runner`, `grader-auditor`, `serve-wire` (all still `tier2`, unchanged). This round
scoped itself to the one chain whose flip-everything failure mode had already been diagnosed, so the
fix-forward approach could be verified on a bounded blast radius rather than repeated blind on a
wider one; S5/S7/S8/S10 get the same treatment in a future, separately-decided round (matching
schema.json's own "never a flag-day default" framing for `cursor_sdk`). The dispatch prompt
(`11-finetune-pipeline-agent.md`) is unchanged by this round — it still tells the orchestrating agent
to pass `--dry-run` through S4/S5, so a real (non-dry-run) self_dispatch pause/resume loop for the
S4 chain is separate follow-up work, not done here.

**A second, narrower credential wall, found and closed the same session (not part of §5's
original finding)**: S7's *actual* Trigger.dev launch call (`_dispatch_launch_task` →
`trigger_dev_rest.trigger_task`) is a separate, simpler problem from the AgentSpec dispatch wall
above — it's one direct REST call needing `TRIGGER_SECRET_KEY`, not a sub-agent spawn. See §4b.

## 6. Lessons from real prior iterations that should shape this environment specifically

Pulled from `PRs/model-factory-cloud-environment-v1/01-grand-steel-ptc-steel-archaeology.md` and
`02-alt-model-archaeology.md` — read those in full for the evidence; this is what's load-bearing
for environment design specifically, not a re-telling.

- **Neither real shipped model (Grand Steel, PTC Steel) was ever produced by `factory.py`'s own
  automated S7-S10 chain.** Both went through the swarm mechanism, hand-orchestrated, on a
  credential-holding machine. This environment's dry-run-first, gate-respecting design is the
  RIGHT shape for S1-S6g (deterministic, credential-free, safe to fully automate); it is
  deliberately NOT yet the shape that gets a real model trained, and shouldn't claim to be until
  §5 has a real answer.
- **Two different infra shapes exist for the eventual training call, and an environment built for
  only one will silently fail the other.** Fireworks-managed (Grand Steel/PTC Steel/DeepSeek — API
  credentials only) vs. self-hosted RunPod rental (a1-v1, Muse-Glimmer — forced there because
  Fireworks couldn't train or affordably serve their base model; needs `RUNPOD_API_KEY`, a real SSH
  keypair, and a specific trainer, axolotl or Unsloth, not interchangeable). Whatever eventually
  handles S7 for real needs to know which shape a given org/model needs, or it works for roughly
  half of future attempts and silently doesn't for the other half.
- **A pre-rename path bug was found independently in two different places this initiative already
  audited** (`runs/ptc-steel/swarm/cloud/manifest.json`'s `read_paths`, and — new this round —
  `factory-automation/factory/` itself as a stale 184MB duplicate). The pattern repeats because the
  underlying cause repeats: things get moved (`c425a98`), and every reference to the old location
  has to be found by hand, one incident at a time, rather than by a single mechanical check. Worth
  building that check once rather than re-discovering the next instance of this same bug a third
  time.

## 7. Open questions, deliberately not decided here

1. Should `cloud-claim.sh` become a *hard*, code-level gate inside `dispatch_node()` itself (refuse
   a real dispatch when a live claim exists for the org/stream), or stay advisory (the dispatch
   prompt is told to check, nothing enforces it)? Currently advisory only.
2. Should a *local* (laptop-run) `factory.py` invocation also participate in the claim system, so a
   local run and a cloud run can't collide on the same org+stream either? Currently, only the cloud
   dispatch prompt is told to claim; nothing on the local path calls `cloud-claim.sh` at all.
3. **RESOLVED, built 2026-08-21**: §5's self-dispatch design — `executor: self_dispatch`,
   `factory_node_run._dispatch_self`, 16 tests. Budget enforcement answered honestly, not to
   parity: a hard refusal on a missing/over-budget self-reported cost, not tier2_run.sh's
   independent metering — see §5's own text. Both `fa399be` and the `_dispatch_self` code are on
   `cloud-dispatch/finetune-rehearsal-v1`, not on the branch of record. FOLLOW-ON **partially
   resolved on that branch only, same day**: the S4 corpus chain (`corpus-planner`,
   `corpus-family-generator`, `curation-consolidator`) opted in *there*, with the 10 tests that
   broke fixed by redirecting to synthetic tier2 fixtures rather than weakened — see §5's "What's
   still true today" note. On `model-training-swarm-cloud-v1` those three specs are still tier2,
   and the opt-in has never run outside `--dry-run` on any branch. Still open: whether/when
   `corpus-reader` (`cursor_sdk`) or any S5/S7/S8/S10 spec should follow.
4. **PARTIALLY RESOLVED, 2026-08-21, by avoiding the question rather than answering it**: the
   relay-claim exchange (§4) means the cloud session never needs `FACTORY_SYNC_RELAY_TOKEN`
   pre-configured in its sandbox at all — it gets it at runtime via the fire payload + one HTTP
   exchange. The ORIGINAL question — does a routine's `environment_id` support setting real env
   vars, and if so how — is still genuinely unconfirmed; it just no longer blocks this specific
   credential. It would still matter for any FUTURE credential that can't be bootstrapped the same
   way (a per-run claim-code exchange only works because there's a live platform-alpha route
   willing to hand back a secret on presentation of a short-lived proof — not every credential has
   an equivalent verifier available).
