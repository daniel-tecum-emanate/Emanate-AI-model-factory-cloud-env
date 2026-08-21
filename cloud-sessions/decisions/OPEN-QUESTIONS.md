# OPEN QUESTIONS

Unresolved as of 2026-07-29. Anything answered should move into a lane file or `DECISIONS.md` with
its evidence, and be struck from here.

---

## Needs Daniel

| # | Question | Why it is his | Blocking |
|---|---|---|---|
| Q1 | **Re-open GCP billing, buy Hetzner/DigitalOcean, or skip the always-on box?** | Recurring spend (I5/I9) | **V-095**, [lane 07](../lanes/07-always-on-vm-BLOCKED.md) |
| Q2 | Upgrade `claude` 2.1.206 → 2.1.220? | `scripts/tier2_run.sh` and the nightly jobs run through this CLI; a bump can break Tier-2 enforcement | Nothing — lane 04 works on 2.1.206, so this is not urgent |
| Q3 | Is lid-shut operation acceptable given Apple's thermal guidance? | Hardware risk on his machine | Nothing, but see [lane 02](../lanes/02-lid-close-pmset.md) |

**Do Q1 after testing lane 04, not before.** Lane 04 is free and may remove the need for a box
entirely.

## Empirical tests not yet run

| # | Question | How to answer | Why it matters |
|---|---|---|---|
| Q4 | **Does the lid actually stay awake?** The flag is confirmed set; a physical lid-close cycle was never performed. | Set the flag, start a process appending a timestamp every 30s, close the lid 15 min, reopen, check for an unbroken series. | The whole of lane 02 rests on it. Several supporting sources are vendor blogs for paid closed-lid apps — commercial interest. |
| Q5 | **Does lane 04 work end-to-end?** | `/web-setup`, push a branch, `claude --cloud "…"`, confirm in `/tasks` and at claude.ai/code, then `claude --teleport <id>`. | The highest-value untested lane; may replace lanes 03 and 07. |
| Q6 | What is Cursor's cloud-agent **max runtime / idle expiry**? | Empirically, or ask Cursor. | **Undocumented.** The widely-repeated "24h" traces only to third-party skill files. Do not encode it. |
| Q7 | What is Claude's cloud-session **idle expiry**? | Empirically. | Documented to exist, no number published. Determines whether a long run survives a quiet period. |
| Q8 | Does `powerd` reset `disablesleep` on power-source change? | Switch AC↔battery with the flag set and re-read `SleepDisabled`. | One community source claims it does. **If true, the watchdog needs to re-assert — it currently does not.** Would be a real hole in lane 02. |
| Q9 | Do `claude --bg` sessions survive the launching terminal closing? | Same process-group test that caught `nohup`+`disown` ([D-2](DECISIONS.md)). | Determines whether [lane 05](../lanes/05-claude-local-background.md) can replace tmux in lane 01. Must not be assumed. |
| Q10 | Does `longrun stop` orphan `claude --bg` sessions? | Start one under a longrun session, stop it, check. | Cleanup correctness if lanes 01 and 05 are combined. |

## Design questions

| # | Question | Notes |
|---|---|---|
| Q11 | Move the local lane from a detached process to **`launchd`**? | Recommended by the research agent: `disablesleep` has **no crash recovery**, so a supervised job that re-asserts and re-verifies on wake is more robust than a one-shot spawn. Interacts with Q8. Would need a `PROCESSES.md` entry — a guarded file. |
| Q12 | Migrate the Cursor client from **v0 to v1**? | Cursor labels v0 legacy: "New integrations should use the current Cloud Agents API." v1 adds SSE streaming, reconnect, and lifecycle controls. The in-flight session-manager work is being built on v0. |
| Q13 | Use Cursor **webhooks** instead of polling? | `webhook.url` + `webhook.secret` (min 32 chars) gives push-based status. Cursor's docs prefer it, and given the rate limits, polling is the weaker design. |
| Q14 | Should **Routines** host this repo's nightly jobs? | They run on Anthropic infrastructure and "keep working when your laptop is closed" — which is what the nightly jobs want. Caps: Pro 5/day, Max 15/day; minimum interval one hour. Would need `PROCESSES.md` registration and an `OPERATIONS.md` tier decision. Not evaluated. |
| Q15 | Should the ad-hoc `/tmp/cursor-cloud-*.py` spikes be ported into `scripts/longrun/`? | They proved dispatch, separate-process status, and steering — then live in `/tmp`, so **they vanish on reboot.** Likely superseded by the in-flight session manager; check before discarding. |

## Known-unknown risks

- **macOS 26 wake defects.** Tahoe has widely-reported deep-sleep / black-screen-on-wake problems,
  and one report implicates `disablesleep` toggling. That report labels the causal link **inferred,
  not proven**. Unquantified.
- **Cursor `/v0/repositories` flakiness.** Two confirmed defects (a 401 regression; a picker sync
  bug needing a full disconnect/re-sync) produce a symptom identical to "no permissions". If the
  list ever goes empty again, suspect this before assuming a grant was revoked.
- **Org IP allowlisting kills Claude cloud entirely.** If enabled, **every** cloud session fails
  with an auth error, because they call the API from Anthropic infrastructure. Needs a support
  exemption. Unknown whether Emanate has it enabled — **worth checking before relying on lane 04.**
- **Claude cloud sessions can reach any repo the connecting GitHub account can see**, not just those
  the Claude GitHub App is installed on. App installation is not a session-level access control.
- **Subagent durability.** Three of five subagents were wiped by one DNS blip because they only
  wrote output at the end. Future long subagents should checkpoint to a file as they go.
