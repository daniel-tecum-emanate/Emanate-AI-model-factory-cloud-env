# STATUS — what works right now

**Snapshot: 2026-07-29 20:00 PT.** Update this file at the end of every working session.

---

## The capability board

| Lane | Survives lid close | Survives shutdown | Status | Command |
|------|:---:|:---:|--------|---------|
| [01 local tmux](lanes/01-local-tmux.md) | no | no | **WORKING** — suite-tested | `./scripts/longrun.sh start "<task>"` |
| [02 lid-close](lanes/02-lid-close-pmset.md) | **yes** | no | **VERIFIED on this hardware** | `./scripts/longrun.sh keepalive on --gui` |
| [03 Cursor cloud](lanes/03-cursor-cloud.md) | yes | **yes** | **VERIFIED end-to-end**, incl. `ls`/`status`/`logs`/`send`/`stop` | `./scripts/longrun.sh cloud <prompt-file>` |
| [04 Claude Code cloud](lanes/04-claude-code-cloud.md) | yes | **yes** | **AVAILABLE, NOT YET RUN** | `claude --cloud "<task>"` then `claude --teleport` |
| [05 Claude local bg](lanes/05-claude-local-background.md) | with lane 02 | no | available, unexercised | `claude --bg` / `claude agents` |
| [06 GitHub Actions](lanes/06-github-actions-DROPPED.md) | — | — | **DROPPED — policy** | — |
| [07 always-on VM](lanes/07-always-on-vm-BLOCKED.md) | yes | yes | **BLOCKED — needs your decision** | — |

## Live right now on this machine

| | |
|---|---|
| keepalive watchdog | **RUNNING**, pid 88356, root — re-armed 21:51 after the 19:57 teardown |
| `pmset -g \| grep SleepDisabled` | **`1`** — lid close will not sleep |
| Bounds on the current run | `--max-hours 24`, `--min-battery 20` |
| Battery | **65%, charging** — comfortably clear of the 20% revert threshold |
| Cursor repos granted | `daniel-tecum-emanate/emanate-tecum-workflow`, `Emanate-AI/platform-alpha` |
| `verify_system.sh` | **83 passed, 0 failed** (16 of them longrun checks) |

**Protected as of 21:51.** To re-arm after any teardown (needs Touch ID), and note the **absolute
path** — `longrun.sh` exists only in the workflow repo, so a relative path fails from any
`platform-alpha` worktree even though the keepalive itself is machine-global:

```bash
/Users/danieltecum/emanate-tecum-workflow/scripts/longrun.sh keepalive on --gui --max-hours 24 --min-battery 20
pmset -g | grep -i sleepdisabled    # must print 1
```

---

## What each lane actually solves

The four failure modes are laid out in [`PROBLEM.md`](PROBLEM.md). Mapping:

- **"I closed the lid at my desk"** → lane 02. Solved and proven. This is the only lane that
  helps the **in-IDE Cursor agent**, because that agent runs inside the Cursor app on this Mac
  and cannot be relocated anywhere.
- **"I shut the laptop down / left the building"** → lanes 03, 04, 07. Work must execute
  somewhere else. Lanes 03 and 04 need the work pushed to a git remote first, because the cloud
  VM clones from origin and cannot see your uncommitted files.
- **"I want to attach, steer, detach, repeatedly, over days, from a phone"** → lane 07 only.
  This is the gap. Filed as **V-095**, awaiting a spend decision.

## Recommended default, given what is proven

1. **In-IDE Cursor agent, lid shut at your desk** — lane 02, plugged in. Nothing else covers it.
2. **Long autonomous coding task, machine off** — lane 04 (`claude --cloud`) is the strongest
   candidate on paper: first-party, no separate compute charge, and `--teleport` pulls the
   session back into your terminal. **It has not been run yet** — that is the top next action.
   Lane 03 is the proven fallback and works today.
3. **Multi-day interactive attach/detach from anywhere** — needs lane 07. Decide V-095.

## Corrections logged against this workstream

Recorded here because they were all made *and caught* within one session, which is the honest
signal about how much of this to trust on first reading.

| What was claimed | Reality | Where |
|---|---|---|
| `caffeinate -ism -w $$` keeps the machine up | Died with its launching shell — process group, not SIGHUP | [D-2](decisions/DECISIONS.md) |
| gcloud is not authenticated → GCP not a shortcut | Authenticated; the real blocker is **billing closed** | [V-101](lanes/07-always-on-vm-BLOCKED.md) |
| `pmset disablesleep` unverifiable on macOS 26 | **Verified working**, pid 45238 | [V-101](lanes/02-lid-close-pmset.md) |
| `claude --cloud` does not exist (absent from `--help`) | **Wrong** — hidden, but compiled in | [V-102](lanes/04-claude-code-cloud.md) |
| Cursor cloud agents cap at 24h | Unverified anywhere in Cursor's docs; do not encode it | [lane 03](lanes/03-cursor-cloud.md) |
| The D-4 ownership fix closed the teardown footgun | **It did not.** It reoccurred the same evening through a second path and killed a Touch-ID-authorised keepalive | [incident below](#incident-2026-07-29-1957--the-suite-killed-the-keepalive-again) |

## Incident 2026-07-29 19:57 — the suite killed the keepalive again

Worth reading before trusting any "protected" claim in this folder.

[D-4](decisions/DECISIONS.md) added keepalive ownership so `verify_system.sh` could not tear down
an operator keepalive. **The same failure happened again four hours later**, by a different route:

`verify_system.sh` section 14's cleanup **deletes `.longrun/keepalive.owner`**. An absent owner file
fell through to the *release* branch — so every subsequent `stop` in the suite read "nobody owns
this" and released a **root watchdog it had never started**. Ledger evidence:

```
{"ts": "2026-07-29T19:36:22-07:00", "event": "longrun", "phase": "keepalive-on",  "detail": "min_battery=20 max_hours=24 follow=0"}
{"ts": "2026-07-29T19:57:00-07:00", "event": "longrun", "phase": "keepalive-off"}
```

Not a battery revert and not max-hours — an explicit release. **Knock-on effect, confirmed in
`pmset -g log`:** with `caffeinate` released, the Mac slept **20:25:26 → 21:15:04** (~50 minutes, on
battery at 24%) *underneath a running verify suite*, which took **75 minutes** instead of 125
seconds. It woke on lid/user activity, not on anything the job did.

**And the suite still reported `83 passed, 0 failed`.** A suite that sleeps halfway through still
passes — wall-clock time was the only signal that anything was wrong.

**Fixed** in `cmd_stop`: release only on a *positive* `owner == session` claim. Unknown provenance
now means leave it alone — the cost of a stray assertion is some battery, the cost of a wrong
release is a sleeping Mac that kills whatever was running. Regression check: `stop never releases a
keepalive it does not own`.

**The transferable lesson:** the first fix guarded the *decision* ("is it owned?") but not the
*input* ("what if the owner record is gone?"). A missing record read as permission. Default to
refusing when provenance is unknown.
| Empty `/v0/repositories` = permissions problem | Also matches two known Cursor defects, and the endpoint is rate-limited to 1/min | [lane 03](lanes/03-cursor-cloud.md) |

## Next actions

| # | Action | Owner |
|---|--------|-------|
| 1 | **Plug in** if the lid stays shut | Daniel |
| 2 | Run lane 04 once end-to-end: `claude --cloud` → `/tasks` → `claude --teleport` | either |
| 3 | Decide **V-095** (Hetzner ~€16/mo vs DigitalOcean ~$24/mo vs re-open GCP billing vs skip) | Daniel |
| 4 | Decide whether to upgrade the CLI 2.1.206 → 2.1.220 (`tier2_run.sh` depends on it) | Daniel |
| 5 | ~~Finish `longrun cloud ls/status/logs/send/stop`~~ — **done**, all five proven live | ✅ |
| 6 | Empirically find Cursor's and Claude's cloud idle/runtime bounds — both undocumented | either |
| 7 | **Create KG entity `Lesson_OwnershipMarkerDeletion`** (`type:lesson severity:critical`) — see [D-4a](decisions/DECISIONS.md). Needs a **Claude Code** session; Cursor has no `mcp__knowledgegraph__*` tools | next Claude Code session |
| 8 | Physically close the lid for 15 min and check for an unbroken timestamp series (Q4) — still nobody's done it | either |
