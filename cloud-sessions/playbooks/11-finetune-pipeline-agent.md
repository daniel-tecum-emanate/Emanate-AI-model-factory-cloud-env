# 11 — The fine-tuning pipeline orchestrator agent

**Status: DOCS — design-only, drafted 2026-08-19 for
[`PRs/model-factory-finetune-launcher-v1/PRContext-phase2.md`](../../PRs/model-factory-finetune-launcher-v1/PRContext-phase2.md)
task T2-4.** Per ground rule 1 ([`../AGENT.md`](../AGENT.md)): this playbook has **not**
been exercised end-to-end. Dispatching a real cloud session needs a real interactive
terminal (`claude --cloud` refuses without a TTY — [`02-dispatch.md`](02-dispatch.md)), which
no non-interactive session, including the one that wrote this file, has. Everything below is
grounded in real, read code (`factory.py`, `factory_stages.py`, `run_pipeline.py`,
`cloud-heartbeat.sh`) and this folder's own verified mechanisms (`02`, `06`, `10`) — but the
playbook *as a whole run* is `UNVERIFIED`. Do not read "concrete" below as "tried."

Read [`02-dispatch.md`](02-dispatch.md) and [`10-fleet.md`](10-fleet.md) in full first — this
file assumes both. It is the third leg of
[`PRD.md`](../../PRs/model-factory-finetune-launcher-v1/PRD.md)'s Phase 2: T2-1 ports the
Fireworks launch/monitor loop to a Trigger.dev task, T2-2 wires its real cost into
`factory_runs.cost_actual_usd`, T2-3 connects `factory.py`'s `stage_launch`/`stage_train` to
that task. **This file is the dispatch brief for the fourth piece: a cloud Claude Code
session that drives one org through the pipeline, never touches Fireworks itself, and makes
its own liveness/blockers visible via the heartbeat bridge Phase 1 already built.**

---

## The shape of it, before the detail

```
YOU (real terminal)                 CLOUD VM (this playbook)              EXTERNAL
────────────────────                ───────────────────────                ────────
cs new --cloud --plan          →    clones emanate-tecum-workflow
  11-finetune-pipeline-agent.md     cd fine-tuning/factory-product/
  "orchestrate <org>"                │
                                     ├─ run_pipeline.py <org> --until project
                                     │    beat --phase <stage> after each stage
                                     │
                                     ├─ hits governance or launch unapproved
                                     │    blocked --kind needs-decision  ──────► you approve
                                     │    (Model Factory tab, or factory.py         (offline,
                                     │     <org> --stage <gate> --approved,          out-of-band)
                                     │     run by a HUMAN — never this agent)
                                     │    KEEPS BEATING while blocked
                                     │
                                     ├─ re-run --start-at <gate>, confirm approved
                                     │
                                     ├─ trigger T2-3's mechanism (TBD — see §4)  ──► Trigger.dev
                                     │    poll status                                 task ──► Fireworks
                                     │    beat --phase training --progress d/t        (the ONE place
                                     │                                                 the API key lives)
                                     ├─ terminal state
                                     │    done / blocked(dependency-missing|rate-limited)
                                     ▼
                              cloud-fleet/<session-id> ref  ──git push──►  origin
                                                                              │
                                                              T1-1's sync bridge (already built)
                                                              → factory_agents.blocker
                                                              → Graph tab (already built)
```

---

## 1 · Scoping the session

**What this playbook can say precisely, and what it can't yet.**

The pipeline's code lives at `fine-tuning/factory-product/` inside **this** repo
(`emanate-tecum-workflow`) — same repo `cloud-sessions/` lives in. `cs start`/`cs new --cloud`
clone the **one GitHub remote** of the repo you dispatch from
([`02-dispatch.md`](02-dispatch.md): "the VM clones your GitHub remote, not your disk"), so a
session dispatched from here gets the whole `emanate-tecum-workflow` tree, including
`fine-tuning/factory-product/` — nothing extra to configure for that half.

**`platform-alpha` is a separate GitHub repository** (`Emanate-AI/platform-alpha`, confirmed
via its own `origin` remote — it is nested on disk under `emanate-tecum-workflow/` but is not
tracked by this repo's git history at all: `git ls-files | grep platform-alpha` returns
nothing). A cloud VM dispatched against `emanate-tecum-workflow` will **not** have
`platform-alpha` checked out. Whatever T2-3 ends up building to call T2-1's Trigger.dev task
— an HTTP call to a platform-alpha API route wrapping `tasks.trigger()`, or Trigger.dev's own
REST API directly, both still under research per
[`PRContext-phase2.md`](../../PRs/model-factory-finetune-launcher-v1/PRContext-phase2.md)'s
T2-3 section — needs to be reachable **over the network** from this VM, not present on its
disk, for this single-repo dispatch model to work as written. **Open question, not guessed
here:** if T2-3 lands as something that instead needs a local `platform-alpha` checkout (e.g.
running its own scripts rather than calling a deployed endpoint), this playbook's scoping
section needs a rewrite — check `PRDone.md`'s T2-2/T2-3 entries once they exist before
assuming either shape.

**Network access** ([`06-environments.md`](06-environments.md)): the default **Trusted**
tier reaches package registries, GitHub, and cloud SDKs — it is not guaranteed to reach an
arbitrary platform-alpha Vercel deployment or `api.trigger.dev` unless one of those already
falls under "cloud SDKs." Until T2-3's real endpoint is known, treat this as unresolved:
`cs env` shows the pinned environment before you dispatch, and if the trigger call 403s with
`request blocked: no rule or allowlist entry allows host "..."`, that is this exact gap —
add the real domain to a **Custom** environment, don't switch to **Full** as a shortcut.

**What "scoping to `fine-tuning/factory-product/`" actually means here — precisely, not
loosely.** There is no cloud-side flag that sandboxes a VM to one subtree; the VM gets the
whole clone. Scoping is two real, weaker things, and the dispatch prompt in §8 should not
oversell either:
1. **The prompt says what not to touch** (per `02-dispatch.md`'s "say what not to touch" —
   it has full permissions and no approval prompts, so this is instruction, not enforcement).
2. **`cs track <session-id> --lane <lane> --owns fine-tuning/factory-product/`**, run by you
   after dispatch (per `10-fleet.md` §1 and `bin/README.md`'s `cs track` row), records the
   claim on the ledger so `cs board`/`cs fleet` can flag an overlapping claim from a
   concurrent session. This is bookkeeping for humans and other sessions to see, **not** a
   filesystem restriction — the same honesty this folder already applies to `allowed_tools`
   ("an auto-approval list, not a sandbox," `10-fleet.md` §1).

---

## 2 · Running the pipeline stages, with a heartbeat after each

**Don't reinvent a per-stage loop — `run_pipeline.py` already exists and does exactly this.**
Read it in full (`fine-tuning/factory-product/run_pipeline.py`, 100 lines): it walks
`factory_stages.STAGES` in order, shells out to `factory.py` for each one, and — this is the
part worth trusting — **never passes `--approved` itself** (its own docstring, D1: "This
script is NOT a gate"). It stops automatically at the first unapproved `HUMAN_GATE`
(`governance`/S2, `launch`/S6g, `ship`/S9 — `factory_stages.HUMAN_GATES`) and returns a
distinct exit code so a caller can tell "blocked on a gate" from "something actually broke":

| Exit code | Meaning |
|---|---|
| `0` | reached the requested range with nothing blocking |
| `2` | stopped at an unapproved `HUMAN_GATE` — check the report's `status` |
| `3` | the stage itself reported `blocked_on` an earlier unapproved gate |
| anything else | a real failure — the stage's own report/stderr has the detail |

Run it once per session, stopping deliberately **before** launch:

```bash
cd fine-tuning/factory-product
python3 run_pipeline.py <org-slug> --until project
rc=$?
```

`--until project` runs `census → governance → export → build → verify → project` and stops —
`project` itself is never a gate, it only writes the cost projection that the *next* stage
(`launch`, S6g) will need. After it returns, beat once per outcome:

```bash
# rc == 0: made it through project cleanly
bash ../../cloud-sessions/bin/cloud-heartbeat.sh beat \
  --phase "project complete for <org-slug>, ready to check the launch gate" --progress 6/6

# rc == 2 or 3: stopped at a HUMAN_GATE (see §3 — this is expected, not a bug)
# rc not in {0,2,3}: a real failure
bash ../../cloud-sessions/bin/cloud-heartbeat.sh blocked \
  --kind dependency-missing \
  --detail "run_pipeline.py <org-slug> --until project exited <rc> at stage <name>: <stderr/report excerpt>" \
  --needs "a human to read the <NN>-<stage>-report.json and decide whether to fix config or re-scope"
```

**A correction worth making explicit, since the task brief that produced this playbook names
only the S6g gate:** `governance` (S2) is *also* a real `HUMAN_GATE` —
`factory_stages.HUMAN_GATES = {"governance", "launch", "ship"}`, and `stage_governance`'s own
docstring says "Never auto-passes." If the org's config isn't already governance-cleared,
`run_pipeline.py` stops there first, before `launch` is ever reached. Apply the identical
stop-and-declare rule from §3 there too — don't treat this playbook's stage list as having
only one gate in it.

**Beating per stage, not just per `run_pipeline.py` call:** the loop above only reports once
per multi-stage batch. If a single stage (e.g. `export`, `build`) can run long, prefer
invoking `factory.py <org-slug> --stage <name>` directly in a shell loop instead of the
all-in-one `run_pipeline.py` call, beating `--phase <stage>` after each, so a human watching
`cs fleet` sees real per-stage progress rather than one long silence followed by a single
beat. Both are legitimate; pick per how long S3/S4 actually take for this org (S4/S5 dispatch
real coding-agent chains per `run_pipeline.py`'s own docstring, so they are not instant).

---

## 2b · `self_dispatch` — the S4/S5 credential wall's fix, built but not opted into by any
   real spec yet (read this before assuming step 5's `--dry-run` requirement below is stale)

`ENVIRONMENT.md` §5 (read it in full) documents a NEW `runtime.executor: self_dispatch` value
in the AgentSpec schema, built and tested on `cloud-dispatch/finetune-rehearsal-v1` (commit
`fa399be`) — it lets a node dispatch via YOUR OWN `Task` tool instead of `tier2_run.sh`'s local
`claude` CLI, which is exactly the credential this environment cannot hold. **As of this
writing, no real S4/S5/S7/S8/S10 spec on disk uses it** — every one of them still defaults to
`tier2` (or, for `corpus-reader.yaml` specifically, `cursor_sdk`), so step 5 below is still
correct: pass `--dry-run` through S4/S5 today. This section exists so that the moment a spec
IS opted in (a deliberate, reviewed edit to that spec's YAML — check its `runtime.executor`
field before assuming this doesn't apply to your run), you already know the protocol without
this playbook needing another edit first:

1. A real (non-`--dry-run`) dispatch of a `self_dispatch` node returns `ok: false` with a
   `self_dispatch_required` block: `{node_id, prompt_file, model, max_turns, budget_usd,
   allowed_tools}`. This is NOT a failure — it's the node asking YOU to run it.
2. Read `prompt_file` (relative to the repo root) and dispatch it yourself via your own `Task`
   tool, using the stated `model`/`allowed_tools`. Track what it actually cost.
3. Re-run the exact same stage command with
   `--self-dispatch-result <node_id>=<path-to-a-json-file>`, where that JSON file is
   `{"final_response": "<the sub-agent's final text>", "cost_usd": <real number>, "exit_code":
   0}`. **`cost_usd` is REQUIRED and hard-enforced against the spec's own `budget_usd`** — a
   missing or over-budget report is refused (`ok: false`), not trusted; do not omit it or guess
   low. `factory.py`'s own `--self-dispatch-result` flag accepts this directly (see its
   `--help`); `factory_node_run.py`'s standalone CLI has the identical flag if you're dispatching
   one node rather than a whole stage.
4. Everything else about the stage — gate-forgery detection, verdict parsing for verifier specs,
   decision logging, sync push — happens identically to a real `tier2` dispatch. You do not need
   to do anything special beyond steps 2-3 above.

If you hit a `self_dispatch_required` result and are unsure whether to proceed, heartbeat
`blocked --kind needs-decision` rather than guessing — this is new enough that a human should
confirm the first few real uses.

---

## 3 · The S6g (and S2) gate — STOP, this is correct, not a workaround

Once `project` is done, check the launch gate **without ever passing `--approved`** — that
flag is typed by Daniel, or set via the Model Factory tab; this agent is neither:

```bash
python3 factory.py <org-slug> --stage launch
status=$(python3 -c "import json,sys; print(json.load(open('runs/<org-slug>/07-launch-report.json'))['status'])")
```

`status` will be one of:

- **`approved`** — either a human already approved via the tab before this ran, or
  `budget.auto_approve_under_usd` policy cleared it (`stage_launch`'s own auto-approval path,
  Daniel 2026-07-29). Either way, proceed straight to §4 — no blocker to raise.
- **`gate_pending`** — the real, expected case. This is exactly the human decision point the
  blocker mechanism exists for:

  ```bash
  bash ../../cloud-sessions/bin/cloud-heartbeat.sh blocked \
    --kind needs-decision \
    --detail "S6g launch gate pending for <org-slug>: projected all-in cost \$<total> vs cap \$<cap> (see 07-launch-report.json for the full decision_request)" \
    --needs "Daniel's approval on the launch gate — via the Model Factory tab, or factory.py <org-slug> --stage launch --approved, run by a human"
  ```

  **Why this is the correct use of the mechanism, not something to work around:** S6g is a
  real spend gate on real money, deliberately held by a human — `PRD.md`'s decision record
  says so explicitly ("that safety rail is good and stays"), and `run_pipeline.py`'s own D1
  makes the same choice in a non-cloud context. An agent that invented a way past this,
  or silently retried until it found a config that auto-approved, would be exactly the
  "guesses, keeps going, produces confident output nobody can trust" failure `10-fleet.md`'s
  §4 closes on. **Keep beating while blocked** (`beat --phase "waiting on launch gate" ` on
  the normal cadence) — a blocked agent that goes silent reads as dead, not stuck.
- **`REFUSED`** — the projection is over-cap; `factory.py` refuses regardless of `--approved`
  (L17, `stage_launch`'s own comment). Still a human decision (re-scope the corpus and
  re-project, or accept the cost some other way), so still `needs-decision`, with the
  over-cap numbers in `--detail`.

**Resuming, once a human has approved it out-of-band:** re-run `--stage launch` (no flag) to
confirm `status` flipped to `approved`, then:

```bash
bash ../../cloud-sessions/bin/cloud-heartbeat.sh beat --unblocked --phase "resumed: launch gate approved"
```

**RESOLVED, Daniel, 2026-08-19: poll-based, not push-based.** The agent re-runs `--stage launch`
(no flag) **every 3 minutes** while blocked at a gate — cheap, stateless, and works whether the
approval came from the Model Factory UI's action button or a human running
`--stage launch --approved` directly. This requires no new plumbing: the UI's approval action
already writes to the real `factory_gate_decisions` table via the existing `decideGate` RPC, so
the agent's next scheduled re-check picks it up automatically. No `cs send`/steering message is
needed for this — that mechanism remains available for other uses (e.g. an operator wanting to
actively redirect a session), but is not part of the gate-approval path.

Concretely, while blocked on a gate:
```bash
while true; do
  sleep 180
  python3 factory.py <org-slug> --stage launch
  status=$(python3 -c "import json; print(json.load(open('runs/<org-slug>/07-launch-report.json'))['status'])")
  [ "$status" = "approved" ] && break
done
bash ../../cloud-sessions/bin/cloud-heartbeat.sh beat --unblocked --phase "resumed: launch gate approved"
```
Keep beating (`beat --phase "waiting on launch gate"`) on the normal cadence throughout this
loop — polling the gate does not replace the heartbeat, it runs alongside it.

---

## 4 · Once approved: trigger the real launch, then poll without going silent

T2-3 has landed (`fine-tuning/factory-product/factory.py`'s `stage_train` /
`_dispatch_launch_task`, `PRs/model-factory-finetune-launcher-v1/phase-2-credentialed-launch/
README.md`) and its wire contract to the TypeScript side has been independently re-verified
by chaining both real, unmodified sides through local stubs (`PRs/model-factory-cloud-
environment-v1/09-s7-launch-automation-investigation.md` §3) — no field mismatch. Run the
stage exactly as the wired pipeline already does:

```bash
python3 factory.py <org> --stage train --manifest <path/to/signed-manifest.json>
```

Read the `trigger_run_id` back out of the S7 report
(`runs/<org>/08-train-report.json`'s `trigger_dispatch.trigger_run_id`) — or from this
command's own stdout, `[train] DISPATCHED factory-launch-monitor trigger_run_id=...`.

Poll it with `trigger_dev_rest.get_run` (or `factory.py --check-run-id <trigger_run_id>`,
a read-only wrapper around the same call — no spend, no `--org`/`--stage` required):

```bash
python3 factory.py <org> --check-run-id <trigger_run_id>
```

Its `status` field is the Trigger.dev **task's own** lifecycle
(`EXECUTING`/`COMPLETED`/`FAILED`/`CRASHED`/...) — not the Fireworks job's state, which is
internal to the task and not separately exposed. Treat `COMPLETED` as "check
`factory_runs.cost_actual_usd` and the model registry for the real outcome," and anything
terminal-but-not-`COMPLETED` as a real failure needing a `blocked` heartbeat, not a retry
(the task's own `retry: { maxAttempts: 1 }` means Trigger.dev will not silently re-run it
and risk a second paid job on top of whatever this attempt already spent). Poll at a sane
interval (every few minutes — you only need to stay inside `cs fleet`'s staleness window,
15 minutes by default) and beat every poll, whether or not anything changed:

```bash
bash ../../cloud-sessions/bin/cloud-heartbeat.sh beat \
  --phase training --progress <done>/<total>
```

**`<done>/<total>` stays unresolved even with T2-3 landed** — confirmed, not just
predicted: the Trigger.dev task's own status vocabulary above is a lifecycle, not a
fraction, and the Fireworks job state it wraps
(`JOB_STATE_RUNNING`/`PAUSED`/etc.) doesn't expose a step count either. A reasonable
placeholder is elapsed-polls against a rough expected-duration estimate (if the org config
records one), or simply omitting `--progress` and relying on `--phase` text plus the beat
cadence itself — `--progress` is optional on `beat` (`cloud-heartbeat.sh`'s own usage).
Don't force a fraction that isn't real just to fill the flag.

---

## 5 · Real, unresolvable failure

Two genuinely different failure shapes, and they take different blocker kinds — resist
collapsing both into `dependency-missing` by default, per the task brief's own "or whichever
taxonomy value genuinely fits" instruction:

**Capacity exhaustion after all retries.** T2-1's own port of `fw_launch_monitor.py` already
retries a capacity-`FAILED` job up to 3 times (bills \$0 each time) before giving up — see
`fw_launch_monitor.py`'s real behavior, ported by T2-1. If it still can't get capacity after
that, this is a quota/throughput condition, not a missing file or service:

```bash
bash ../../cloud-sessions/bin/cloud-heartbeat.sh blocked \
  --kind rate-limited \
  --detail "Fireworks capacity-FAILED after 3 auto-retries for <org-slug>'s training job; no GPU capacity available" \
  --needs "wait and retry later, or a human decision to try a different base model/region"
```

**An unexpected API error surfaced back from the launch mechanism** — something T2-1's own
error handling didn't already classify (not `PAUSED`, not capacity-`FAILED`, not a clean
`COMPLETED`/cost-ceiling `CANCELLED`). This is the Trigger.dev task or the Fireworks API
itself behaving in a way nothing here anticipated:

```bash
bash ../../cloud-sessions/bin/cloud-heartbeat.sh blocked \
  --kind dependency-missing \
  --detail "T2-3's trigger mechanism / T2-1's task returned an unrecognized state for <org-slug>: <exact error text>" \
  --needs "a human to check the Trigger.dev dashboard/logs for this run and decide whether to retry or abandon"
```

If what's actually needed is a human clicking something in a dashboard rather than a
technical fix, `needs-human-action` fits better than `dependency-missing` — pick by what
`--needs` would honestly say, not by habit. In every case: **never invent a plausible
outcome and keep going** (`10-fleet.md` §1's own "if you are stuck: DECLARE IT" block) —
`fw_launch_monitor.py`'s whole design point is treating an unrecognized state as "reads
blind," not as inferred failure (HTTP 412 handling, L43). Match that here.

---

## 6 · Completion

```bash
bash ../../cloud-sessions/bin/cloud-heartbeat.sh done \
  --note "<org-slug> fine-tune COMPLETED: output model <id>, cost \$<actual>, see runs/<org-slug>/ reports"
```

Confirm — don't assume — that T2-2's write actually landed before declaring done: read back
`factory_runs.cost_actual_usd` for this run (however T2-2 exposes that; check its `PRDone.md`
entry) rather than trusting your own belief that the write happened.

---

## 7 · Hard constraint — read this even if you skipped everything else

> **This agent must NEVER be given `FIREWORKS_API_KEY`, or any other paid-provider
> credential, directly.** It triggers T2-1's Trigger.dev task and watches it; it never
> calls `api.fireworks.ai` itself, and it never should.

**Why, concretely, not just "because the rule says so":** cloud environments have no
secrets store — any value in an environment variable or setup script is readable by anyone
who uses that environment (`06-environments.md`: "There is no secrets store"). This exact
folder's own research already confirmed and recorded this as a standing constraint: F15 in
[`../archive/foreign-repo-provenance-2026-08-18/founder-decisions-2026-08-18.md`](../archive/foreign-repo-provenance-2026-08-18/founder-decisions-2026-08-18.md)
("No secrets store, no telemetry persistence in cloud VMs" — group 3, credentialed cloud
work) and `AGENT.md`'s own "Constraints that will bite you" list ("Cloud environments have
no secrets store... keep credentialed work out of cloud sessions entirely"). `PRD.md`'s own
architecture decision (Daniel, 2026-08-18) put the *one* Fireworks credential in Trigger.dev
specifically so this exact agent never needs it — "do not revisit it without asking again"
(`PRContext-phase2.md`).

**A second, equally real credential this pipeline touches that the task brief didn't name,
found by reading the code rather than assumed:** `factory.py`'s optional Supabase sync path
(`write_report` → `factory_sync.push_stage`, gated on `FACTORY_SUPABASE_URL` +
`FACTORY_SUPABASE_SERVICE_ROLE_KEY`) is how stage reports and gate decisions reach the Model
Factory tab live. **This is also a credential (a Supabase service-role key, which bypasses
RLS) and must also never live on this cloud VM**, for the identical F15 reason. The sync is
deliberately fail-open — `write_report`'s own docstring: "a developer running this CLI with
no Supabase env/flag configured gets identical behavior to before this hook existed" — so
running the pipeline **without** it does not crash anything. It does mean, honestly: **without
that key, the per-stage report content (cost projections, gate decisions, checklist state)
will not reach `factory_runs` live from this agent's own `factory.py` calls.** What *does*
reach the Model Factory tab without it is this agent's own heartbeat (liveness, phase,
blockers) via T1-1's already-built `cloud-fleet` → `factory_agents` bridge — that is a
coarser signal (is it alive, is it blocked) than the stage-by-stage detail a
Supabase-connected local run produces, and the two should not be assumed equivalent.

**Flagging, not solving:** whether "everything logs correctly onto the model factory"
(Daniel's original ask) requires the finer-grained sync too, and if so what carries it there
without putting the service-role key on a cloud VM, is a real open question for T2-5's local
test path and whoever validates this playbook for real — not something to resolve by
quietly adding the key to a cloud environment's variables, which is exactly the shortcut F15
exists to rule out.

---

## 8 · The dispatch prompt — paste this into `cs new --cloud --plan`

This file itself is written to double as the committed plan `--plan` points at — the whole
point of `--plan` is giving the session something it can re-read after compaction
(`02-dispatch.md`, "Plan locally, execute remotely"). To dispatch it for real, from a real
terminal, once T2-1/T2-2/T2-3 have landed and `<org-slug>` is a real, census-ready org:

```bash
cd /path/to/emanate-tecum-workflow
git add cloud-sessions/playbooks/11-finetune-pipeline-agent.md
git commit -m "docs: fine-tune pipeline orchestrator playbook"
git push

cs new --cloud --plan cloud-sessions/playbooks/11-finetune-pipeline-agent.md \
  "Orchestrate one fine-tuning pipeline run for org <org-slug> through fine-tuning/factory-product/factory.py on branch cloud-dispatch/finetune-rehearsal-v1 (NOT main, which does not contain this pipeline), following cloud-sessions/playbooks/11-finetune-pipeline-agent.md exactly — its step 2 exchanges your fire payload's relay claim code for real sync credentials, and step 3 is a mandatory branch/environment verification; run both first, in order, and heartbeat each result before anything else. Stop and declare a blocker at any HUMAN_GATE (governance/launch/ship) rather than guessing past it. Never touch FIREWORKS_API_KEY or FACTORY_SUPABASE_SERVICE_ROLE_KEY. When training completes or fails unresolvably, call cloud-heartbeat.sh done or blocked and stop."
```

The self-contained instruction block below is what the dispatched session should treat as
its own operating rules for the run — it restates §1–7 above in second person, the way
`10-fleet.md`'s own heartbeat block is written to be pasted verbatim:

````text
You are orchestrating one fine-tuning pipeline run for org <org-slug>, using
fine-tuning/factory-product/factory.py and run_pipeline.py in this repo
(emanate-tecum-workflow), on branch cloud-dispatch/finetune-rehearsal-v1 —
NOT main, which does not contain fine-tuning/factory-product/ at all. Full
detail for every step below is in
cloud-sessions/playbooks/11-finetune-pipeline-agent.md — read it in full before
starting, it is more precise than this summary.

1. Start your heartbeat:
   bash cloud-sessions/bin/cloud-heartbeat.sh start --lane finetune-<org-slug> \
     --note "orchestrating <org-slug> fine-tune pipeline"

2. Exchange your relay claim code for real sync credentials — added
   2026-08-20/21, do this before anything else touches factory.py. Your fire
   payload (the message that started this run) carries `relay_claim_code=...`
   and `app_base_url=...` when the relay is actually configured — read this
   literally out of your own fire payload, do not guess either value. If
   `relay_claim_code` is absent, skip to step 3 and heartbeat plainly that
   you're running without DB sync (same honest fallback as before). If it IS
   present:
     curl -s -X POST "<app_base_url>/api/model-factory/sync/exchange" \
       -H "Content-Type: application/json" \
       -d "{\"run_id\": \"<run_id from your payload>\", \"claim_code\": \"<relay_claim_code from your payload>\"}"
   A 200 response is `{"relay_token": "...", "relay_url": "..."}` — export
   both into THIS shell session's environment:
     export FACTORY_SYNC_RELAY_URL="<relay_url from the response>"
     export FACTORY_SYNC_RELAY_TOKEN="<relay_token from the response>"
   Every later `factory.py` call in this same shell session now syncs
   automatically (`_sync_url()` picks up `FACTORY_SYNC_RELAY_URL`) — no
   further action needed per stage. A non-200 response (401 = expired or
   tampered code; 503 = the relay isn't configured server-side) means real
   sync isn't available this run — heartbeat that plainly
   (`beat --phase "running without DB sync — relay exchange returned <code>"`)
   and continue; this is the SAME honest degraded-mode step 4 already
   describes, just detected earlier and for a more specific reason. The
   claim code is single-purpose and expires in 30 minutes — if you're
   re-reading this playbook well after the fire and the exchange fails, that
   may simply be why; it is not something to retry indefinitely.

3. Verify your environment before anything else — three real dispatch
   attempts on 2026-08-19/20 produced zero heartbeat signal, and the leading
   suspected cause is a clone that silently defaulted to main and never
   switched branches. Run these two commands literally, first, and heartbeat
   the result no matter what it is:
     git branch --show-current
     ls fine-tuning/factory-product/factory.py
   If the branch is not cloud-dispatch/finetune-rehearsal-v1, or factory.py is
   missing, do NOT guess or improvise a fix silently — run exactly:
     git fetch origin cloud-dispatch/finetune-rehearsal-v1 && \
     git checkout cloud-dispatch/finetune-rehearsal-v1
   then re-run both verification commands. If factory.py is still missing
   after that, call:
     bash cloud-sessions/bin/cloud-heartbeat.sh blocked --kind dependency-missing \
       --detail "cloud-dispatch/finetune-rehearsal-v1 checked out but fine-tuning/factory-product/factory.py still absent"
   and stop — do not attempt to reconstruct the pipeline from memory. Once
   verified, beat once more with the confirmed branch name in --note before
   moving on, so this step's result is never silent even on success.

4. Claim this org+stream before doing any real work — a second session (yours
   from a retry, or a genuinely different one) must never advance the same
   run at the same time:
     bash cloud-sessions/bin/cloud-claim.sh claim --org <org-slug> --stage S1 \
       --lane finetune-<org-slug>
   `claim`'s exit codes are narrower than `check`'s — there is no exit 1 here,
   a STALE prior claim is reclaimed automatically and folds into exit 0.
   Exit 0 = claimed (including a reclaim from STALE), proceed. Exit 3 (LIVE) =
   another session already holds this org+stream and is alive — do NOT
   proceed; call
     bash cloud-sessions/bin/cloud-heartbeat.sh blocked --kind needs-human-action \
       --detail "<org-slug>/<stream> is already claimed and live per cloud-claim.sh check>" \
       --needs "Daniel to confirm which session should own this run"
   and stop. Exit 2 = a refusal (a usage/precondition problem, or an internal
   error verifying) — nothing was recorded, and this is not something to route
   around silently — heartbeat blocked
   --kind dependency-missing and stop. Renew the claim (`cloud-claim.sh renew
   --org <org-slug> --stage <current stage>`) at the SAME points you beat the
   heartbeat below — the two are siblings, not substitutes for each other.

5. cd fine-tuning/factory-product && python3 run_pipeline.py <org-slug> --until project --dry-run
   **Pass `--dry-run` here, always, for now.** S4 (build) and S5 (verify) dispatch real
   sub-agents (`factory_node_run.py` -> `tier2_run.sh` -> a local `claude` CLI, or
   Cursor SDK for a handful of specs) that need a credential this cloud environment
   is structurally barred from holding — see `ENVIRONMENT.md` §5 for the full finding.
   A NEW `executor: self_dispatch` mechanism exists that closes this specific wall
   (§2b of this playbook has the full protocol) — but as of this writing, no real
   S4/S5/S7/S8/S10 spec has been opted into it, so it does not change anything you
   need to do here yet. Check each spec's own `runtime.executor` field if you're
   unsure whether this still applies to your run. Without `--dry-run` here, S4 will
   fail for real, for a reason that has nothing to do with this specific run, and the
   whole walk stops short of the gate you're actually trying to reach. `--dry-run`
   does NOT skip the gate-sync mechanism — `stage_launch`'s gate-request push/poll
   runs unconditionally, dry-run or not — so this still produces a real, clickable
   gate card for Daniel in step 6 (as long as step 2's exchange succeeded). It does
   mean no real corpus gets built this run; that's the honest, current scope, not a
   bug to route around.
   Beat --phase and renew the claim (`--stage project`) after it returns. Read its
   exit code (0/2/3/other) per the playbook's table in §2 before deciding what happened.

6. Check the launch gate: python3 factory.py <org-slug> --stage launch (NEVER pass
   --approved yourself — that is Daniel's action, via the CLI or the Model Factory
   tab, not yours). If status is gate_pending or REFUSED, call:
   bash cloud-sessions/bin/cloud-heartbeat.sh blocked --kind needs-decision \
     --detail "<the real projection numbers>" \
     --needs "Daniel's approval on the launch gate"
   Keep beating AND renewing the claim while blocked — the run is not finished,
   it is legitimately waiting on a human, and the claim must not go stale under
   it. Do not invent a way past this gate. The identical rule applies to the
   governance gate (S2) if it is reached unapproved first.

7. Once status is approved: read this before spending time debugging it as a bug —
   the picture changed 2026-08-21 and is now two SEPARATE things, not one wall.
   `stage_train` (S7) dispatches its own AgentSpec chain (`train-launcher`) BEFORE it
   ever attempts the real Trigger.dev launch, and THAT chain still hits the exact
   same credential wall step 5 does (`ENVIRONMENT.md` §5) — `train-launcher.yaml`
   has not been opted into `self_dispatch` either. `factory.py`'s own code refuses
   to even try the real launch when that chain fails ("S7 node chain failed — not
   attempting the real launch dispatch"). So: `python3 factory.py <org-slug> --stage
   train --manifest <path>` will very likely still report `status: blocked` for
   this reason, not because anything is missing or broken. Heartbeat this outcome as
   `blocked --kind dependency-missing --detail "S7's train-launcher AgentSpec chain
   needs a credential this environment can't hold (ENVIRONMENT.md §5) — the real
   Trigger.dev wire contract itself is proven working AND now reachable without
   TRIGGER_SECRET_KEY via the launch relay (ENVIRONMENT.md §4b), but this specific
   AgentSpec node hasn't been opted into self_dispatch yet"` and stop there — do not
   retry, do not attempt to run the chain yourself another way.
   **What DID close this session**: the actual Trigger.dev dispatch downstream of
   that chain (`_dispatch_launch_task` → `trigger_dev_rest.trigger_task`) no longer
   needs `TRIGGER_SECRET_KEY` in this environment at all — it falls back automatically
   to platform-alpha's `/api/model-factory/launch` relay using the SAME
   `FACTORY_SYNC_RELAY_TOKEN` step 2 already exchanged for you (`ENVIRONMENT.md`
   §4b). If the AgentSpec chain above ever does succeed (self_dispatch opt-in, or
   run locally by Daniel), the launch call itself is no longer the blocker. Read/
   reference: `runs/<org>/08-train-report.json`'s
   `trigger_dispatch.trigger_run_id` if a launch DID get far enough to dispatch —
   poll it with `python3 factory.py <org-slug> --check-run-id <trigger_run_id>`
   (read-only, no spend, works over either transport).

8. On an unresolvable real failure (capacity exhaustion after retries, an
   unrecognized error back from the launch mechanism), call blocked with --kind
   rate-limited or dependency-missing (or needs-human-action if a human must act
   somewhere you can't reach) — see the playbook's §5 for which fits which case.
   Then release the claim (`cloud-claim.sh release --org <org-slug> --reason failed`)
   — a real failure is terminal for THIS session's hold on the org, even though a
   human still needs to act on the heartbeat's blocked state.

9. On completion: bash cloud-sessions/bin/cloud-heartbeat.sh done --note "<what
   landed: model id, cost, reports>", then
   bash cloud-sessions/bin/cloud-claim.sh release --org <org-slug> --reason done.

HARD CONSTRAINT: you must never be given, request, or hardcode FIREWORKS_API_KEY,
FACTORY_SUPABASE_SERVICE_ROLE_KEY, or any other paid-provider/service-role credential.
If a step seems to require one, that is a blocker (needs-credential), not something to
work around.
````

---

## 8b · Dispatch via routine — the real Model Factory product path, no terminal at all

**Added 2026-08-19, supersedes §8 as the primary path.** §8's `cs new --cloud --plan` requires
a real interactive terminal — fine for a human testing this playbook by hand, but not how the
actual "Start fine-tune" button in Model Factory works, since a UI action has no TTY. The
mechanism that closes this gap is real and already confirmed working on this account: **claude.ai
routines** (`cloud-sessions/playbooks/05-routines.md`, VERIFIED 2026-08-12 — created via the API
and fired on demand, no TTY needed) — see
`../../PRs/model-factory-finetune-launcher-v1/ARCHITECTURE.md`'s "Dispatch mechanism" section for
the full design.

Shape:
1. **Create the routine once** (not per run) — its saved prompt is §8's own second-person
   instruction block above, verbatim, with one addition: an explicit instruction to read
   `<org-slug>`/`run_id` out of the `<routine-fire-payload>` block rather than a literal
   placeholder (the API wraps fire-time input this way deliberately — see the routines
   playbook's "API" section for exactly why, and why an unreferenced payload is otherwise
   inert).
2. **Scope its connectors down** to only what this pipeline needs — the API auto-attaches a
   `Claude_Code_Remote` connection and, by default, every claude.ai connector you have; both of
   this account's two existing routines were inspected 2026-08-19 and one has an unrelated Google
   Drive connector attached for exactly this reason. Don't repeat that here.
3. **Create it as an API-trigger type with no schedule** — a scheduled/one-off routine's "Run
   now does not consume the schedule" footgun (routines playbook, same section) doesn't apply to
   a pure on-demand trigger with nothing scheduled in the first place.
4. **Model Factory's backend fires it** per run with a small payload — `run_id`/`org` always,
   plus `relay_claim_code`/`app_base_url` when the sync relay is configured (`dispatch-routine.ts`,
   see §4b's own doc): `{"text": "run_id=<uuid> org=<slug> relay_claim_code=<...> app_base_url=<...>"}`.
   This is still the "very concise message" the whole mechanism is designed to make possible; the
   heavy instruction content lives in the routine's saved prompt, created once.

**Status (2026-08-21):** the routine (`model-factory-finetune-orchestrator-v1`,
`trig_01CVaqMWD3iCMZWy5VQAwXLW`) was created and fired three times (2026-08-19/20) — all three
produced zero confirmed `cloud-fleet/*` heartbeat signal, including a maximally minimal probe.
Since then: the branch-verification step (now step 3) was added and moved earlier, the relay-claim
exchange (step 2) and the launch relay (`ENVIRONMENT.md` §4b) were built, and `self_dispatch`
(§2b) exists as a mechanism (not yet opted into by any real spec). **None of this has been proven
by a real fire yet** — the saved prompt needs a `RemoteTrigger update` to this file's current §8
text before the next fire can exercise any of it, and that fire's outcome is the next concrete
step, not assumed. See `PRs/model-factory-cloud-environment-v1/06-synthesis-and-recommendations.md`
§1 for the fuller history.

---

## Honest limits

- **ROOT CAUSE FOUND 2026-08-21, blocked on Daniel: this routine cannot reach its own repo.**
  Step 0 told the agent to call `add_repo`/`register_repo_root` — not real tools (confirmed
  against official docs; copied from an untested template routine). The real mechanism is a
  structural `sources` field, set via `RemoteTrigger update`, not prompt text
  (`05-routines.md`). Attempting that fix was rejected: `403 "You don't have access to a
  repository this routine uses"` — this account isn't GitHub-connected for routines;
  `05-routines.md` names the fix as running `/web-setup`, an interactive flow only Daniel can
  do. Full writeup: `PRs/model-factory-cloud-dispatch-v1/RUN-LOG.md`. Until this is resolved,
  every command in this playbook is unreachable — the dispatched agent never gets past step 0.
- **Untested as a whole.** Every command above is real and independently grounded (read
  from `factory.py`, `factory_stages.py`, `run_pipeline.py`, `cloud-heartbeat.sh`,
  `06-environments.md`, `02-dispatch.md`), but no session has run this sequence end to end —
  T0-3 in this same PR's Phase 0 documents that even a trivial read-only cloud dispatch
  against this repo has not yet happened, for reasons unrelated to this playbook (dirty
  tree, unpushed commits, no TTY in the authoring session). Treat every command block above
  as `DOCS`, not `VERIFIED`, until someone runs it and updates this file with the real
  transcript.
- **T2-1/T2-2/T2-3 are now built** (2026-08-19, `platform-alpha` worktree
  `-model-factory-finetune-launcher-v2`, uncommitted, no PR yet) — §4's trigger step and §6's
  cost-confirmation step name real files/columns that now exist and have a real writer (see
  `../../PRs/model-factory-finetune-launcher-v1/phase-2-credentialed-launch/README.md`). §4's
  "TBD" framing below is stale as of this build landing — the mechanism is
  `trigger_dev_rest.trigger_task("factory-launch-monitor", ...)`, not actually unknown anymore.
- **The Supabase-sync-vs-heartbeat-bridge gap in §7 — the credential-delivery half is now
  closed** (2026-08-21, step 2 above / `ENVIRONMENT.md` §4): the cloud session gets
  `FACTORY_SYNC_RELAY_TOKEN` via the relay-claim exchange, not left to guess where it comes
  from. What's still genuinely unverified: whether a real fire actually performs the exchange
  correctly and the Graph tab shows live stage detail as a result — that's an empirical claim,
  not a code-review one, and nothing in this repo has fired the routine since these mechanisms
  landed. Don't upgrade this bullet to "resolved" until a real fire confirms it.
- **Single-repo dispatch may not be enough**, per §1 — if T2-3 needs `platform-alpha` on
  disk rather than reachable over the network, this playbook's dispatch model needs a
  second look (a second cloud session against `platform-alpha`, or something else not yet
  designed). **Narrower than it looked**: T2-3's actual real-launch dispatch (`_dispatch_launch_task`)
  and its cloud-environment fallback (the launch relay, `ENVIRONMENT.md` §4b) both stayed
  network calls FROM the emanate-tecum-workflow checkout — no `platform-alpha` disk access
  needed after all. This bullet's original worry didn't materialize for T2-3 specifically;
  it may still apply to something else not yet built.
- **`self_dispatch` (§2b) and the relay-claim exchange / launch relay (step 2, `ENVIRONMENT.md`
  §4/§4b) are real, tested code as of 2026-08-21 — none of it has been exercised by an actual
  routine fire.** The saved routine prompt must be updated (`RemoteTrigger update`) to this
  file's current §8 text before a fire can even reach the new step 2, and self_dispatch has no
  real spec opted in yet regardless. Treat every claim above about what these mechanisms DO as
  `DOCS`, exactly like the rest of this file, until a transcript says otherwise.

## Related

- [`02-dispatch.md`](02-dispatch.md) — the dispatch mechanics this playbook assumes
- [`06-environments.md`](06-environments.md) — network access tiers, why "Trusted" may not
  be enough for T2-3's endpoint
- [`08-concurrent-sessions-and-exodus.md`](08-concurrent-sessions-and-exodus.md) — what
  `--owns` actually does (bookkeeping, not a sandbox)
- [`10-fleet.md`](10-fleet.md) — the heartbeat contract, the blocker taxonomy, watching with
  `cs fleet`
- [`../bin/cloud-heartbeat.sh`](../bin/cloud-heartbeat.sh) — the exact command syntax this
  playbook uses throughout
- `fine-tuning/factory-product/factory_stages.py` — the canonical stage list and gate set
- `fine-tuning/factory-product/run_pipeline.py` — the existing stage-walking script this
  playbook wraps rather than reimplements
- [`PRs/model-factory-finetune-launcher-v1/PRD.md`](../../PRs/model-factory-finetune-launcher-v1/PRD.md)
  and
  [`PRContext-phase2.md`](../../PRs/model-factory-finetune-launcher-v1/PRContext-phase2.md) —
  the architecture decision and task breakdown this playbook implements (T2-4)
