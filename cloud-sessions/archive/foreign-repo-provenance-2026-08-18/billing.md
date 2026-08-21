# Billing — every way money could move

Daniel's requirement, verbatim: *"make sure i dont get billed for anything and if we must
explain the metrics."* This file is the answer, with the evidence.

Measured **2026-08-14**. Status tags follow [`../AGENT.md`](../AGENT.md).

---

## The short answer

**Nothing here bills you today, and one specific setting is the only thing that could change
that.** Cloud sessions consume your Max **subscription rate limits**, not dollars. The
boundary is *usage credits*: with them off, hitting a limit is a **refusal**; with them on,
it becomes **metered overage — real money**.

| Question | Answer | Status |
|---|---|---|
| Does a cloud session cost money? | No separate compute charge for the VM | `DOCS` |
| What does it consume then? | Your account's rate limits, same pool as any Claude usage | `DOCS` |
| Can that become a dollar charge? | **Only if usage credits are enabled** | `DOCS` — check at [claude.ai/settings/usage](https://claude.ai/settings/usage) |
| Is any API key set that could bill separately? | **No** — all unset | `VERIFIED` |
| Is any non-Claude LLM used anywhere in this path? | **No** | `VERIFIED` |

## Verified: no API key can bill you

**Corrected 2026-08-15.** This section first proved the claim by listing environment
variables. That was the wrong probe, and an audit caught it: `meeting-recorder/lib/config.py`
defines `_get_env_or_zshrc()`, which falls back to **parsing `~/.zshrc`** when the
environment is empty. An empty `env` therefore proves nothing about what that code can
resolve. The conclusion survives; the method did not, and a conclusion reached by a method
that cannot establish it is exactly what this folder's first ground rule forbids.

The check that does establish it covers all three resolution paths:

```
                       env      ~/.zshrc
ANTHROPIC_API_KEY      unset    absent
ANTHROPIC_AUTH_TOKEN   unset    absent
ANTHROPIC_BASE_URL     unset    absent
OPENAI_API_KEY         unset    absent
DEEPGRAM_API_KEY       unset    absent
CLAUDE_CODE_USE_BEDROCK / _VERTEX   unset

$ claude auth status  →  claude.ai / max
```

`HF_TOKEN` *is* present in `~/.zshrc` — nothing in this repo reads it, and Hugging Face is
not a billing path here, but it is a live credential in a shell rc file and worth moving.

Re-run the real check with:

```bash
for k in ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL OPENAI_API_KEY DEEPGRAM_API_KEY; do
  printf '%-24s env=%s  zshrc=%s\n' "$k" \
    "$([ -n "${!k:-}" ] && echo SET || echo unset)" \
    "$(grep -qE "^\s*export\s+$k=" ~/.zshrc 2>/dev/null && echo PRESENT || echo absent)"
done
```

A `.env` at the meeting-recorder package root is a third path; it is gitignored and absent.

Auth is the **claude.ai subscription**, not an API key. That matters twice over: it is what
makes `--cloud` and `--teleport` work at all, and it is why there is no metered API bill to
receive.

**Scoped claim, corrected 2026-08-16 (G06):** `cs doctor` only fails loudly for the three
Claude-auth-redirecting variables it actually reads —
`ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_BASE_URL` — plus
`CLAUDE_CODE_USE_BEDROCK`/`CLAUDE_CODE_USE_VERTEX` (`cloud-sessions/bin/cs:243`). It does
**not** check `OPENAI_API_KEY` or `DEEPGRAM_API_KEY`, and it does not read `~/.zshrc` at
all — both are outside the check's scope, not covered by "if any of those variables appear."
If either of those two is what could bill you, `cs doctor` reporting `ready.` proves nothing
about them; only the two probes above do.

### `V-037` — reconciled 2026-08-16

`atlas-os/queue/QUEUE.md:464` (`V-037`) claims *"DEEPGRAM_API_KEY is set, so any record.py
stop transcribes and bills even with spending frozen."* Re-run today with the exact command
above:

```
DEEPGRAM_API_KEY          env=unset  zshrc=absent
```

**Not reproduced.** `DEEPGRAM_API_KEY` is unset by both routes today, so `V-037`'s premise
does not currently hold. This does not withdraw `V-037` — only `queue.py` may edit the
queue, and the underlying question it raises (should the pipeline check `STOP` before a
paid call, independent of whether the key happens to be set today) is still open and still a
founder call. It says only that the *evidence* line for `V-037` was wrong on the date it was
written, by the same method this section itself used to be wrong: reading the environment
without also reading `~/.zshrc`.

### The two direct API callers in the repo, and why they are inert

`meeting-recorder/lib/` is the only code that talks to a paid API directly:

| File | Would bill | Fails closed? |
|---|---|---|
| `summarize.py` | Anthropic API | **Yes** — `_api_available()` returns `False, "ANTHROPIC_API_KEY missing"` before any call |
| `transcribe.py` | Deepgram | **Yes** — the Deepgram branch is gated on `if DEEPGRAM_API_KEY:` |

Neither is reachable from anything in `cloud-sessions/`, and both keys are unset. `VERIFIED`.

## Standing rule: all LLM work goes through Claude Code

Daniel's instruction, 2026-08-14: **all LLM work is done by Claude Code, never another API,
unless he explicitly says otherwise.**

That is now a constraint on this folder, not a preference. Concretely:

- Do not add a dependency that calls OpenAI, Gemini, Deepgram, Hugging Face, or any hosted
  model API.
- Do not "helpfully" wire up `ANTHROPIC_API_KEY` — an API key bills per token *and* disables
  `--cloud`, `--teleport`, Remote Control and routines, all of which require subscription
  auth. It would cost money and remove capability simultaneously.
- A cloud session inherits this rule. The `cs handoff` brief and `/cloud` both operate inside
  the repo, whose `AGENT.md` carries it.

## The metrics, explained

Four different counters, commonly confused:

| Metric | What it measures | Where you see it | Runs out how? |
|---|---|---|---|
| **Subscription rate limit** | all Claude usage — this session, cloud sessions, routines, the phone | [claude.ai/settings/usage](https://claude.ai/settings/usage) | resets on a rolling window |
| **Routine daily run cap** | number of routine *runs started* per account per day | [claude.ai/code/routines](https://claude.ai/code/routines) | resets daily. **One-off runs are exempt** |
| **Usage credits** | metered overage past the above | claude.ai/settings/usage | **this is the one that costs dollars** |
| **Cloud VM compute** | — | — | **not billed** ("no separate compute charge for the cloud VM") |

Parallel sessions consume the first proportionately: three at once burns limits roughly three
times as fast. That is a *speed* cost, not a money cost, until credits are on.

## Incident, 2026-08-14 — nine routines left armed

Recorded because it is exactly the failure this file exists to prevent, and because the
author of the warning committed it nine times.

`playbooks/05-routines.md` documents the footgun: **firing a routine with "Run now" does not
consume its `run_once_at` schedule.** The routine stays armed and fires again.

Every verification probe this week was created as a one-off, fired immediately via the API,
and — for probes 3 through 7 — **never disabled**. A `RemoteTrigger list` audit found five
armed routines with future `next_run_at` values:

```
probe 3  next_run 2026-08-17T09:00Z    probe 6  next_run 2026-08-20T09:00Z
probe 4  next_run 2026-08-18T09:00Z    probe 7  next_run 2026-08-21T09:00Z
probe 5  next_run 2026-08-19T09:00Z
```

Left alone, each would have run unattended on its date, consuming rate limits with nobody
watching. Probe 7 would additionally have re-attempted writes into `atlas-os/bin/**` and
opened a second pull request.

**All nine are now `enabled: false`**, confirmed by re-listing. No money was spent — the
account has no usage credits path proven active — but rate limits would have been.

Two lessons, both now enforced elsewhere:

1. **`enabled` is the deciding field, not `next_run_at`.** A disabled routine still displays
   a future `next_run_at`; reading that field as "will it fire" is the mistake.
2. **Disable in the same breath as firing.** The `/cloud` command now instructs
   create → run → **disable** as one sequence, and says why.

## What to check yourself

Two things only you can see:

1. **[claude.ai/settings/usage](https://claude.ai/settings/usage)** — is **usage credits**
   on? If it is off, nothing in this repo can produce a bill; you get refusals instead. If it
   is on, cloud sessions and routines can meter into real charges once limits are exhausted.
2. **[claude.ai/code/routines](https://claude.ai/code/routines)** — your two legitimate
   recurring routines are live and were left untouched:

   | Routine | Schedule | Repo |
   |---|---|---|
   | Monday Week Ahead Briefing | `47 14 * * 1` | `tecum-knowledge-framework` |
   | Friday Week in Review Briefing | `47 14 * * 5` | `tecum-knowledge-framework` |

   Both draw down the routine daily cap and your subscription usage. They are yours; this
   folder does not manage them and did not modify them.

## Things that were considered and rejected on cost

| Option | Cost | Why not |
|---|---|---|
| Vercel Sandbox | ~$0.80 per 8h session + Anthropic tokens | Forfeits subscription auth, teleport and mobile access — see [`surfaces.md`](surfaces.md#vercel-sandbox) |
| Always-on Linux box | €16–24/mo | Cloud sessions do the job for free — [archive lane 07](../archive/2026-07-29/lanes/07-always-on-vm-BLOCKED.md) |
| GitHub Actions as a session host | ~$259/mo | Also against GitHub's terms — [archive lane 06](../archive/2026-07-29/lanes/06-github-actions-DROPPED.md) |

Standing rule from the archive, still in force: **never provision paid infrastructure.**
Recurring spend is Daniel's decision. Cost the options, write them down, stop.
