# Lane 02 — lid-close suppression via `pmset disablesleep`

**Solves:** F3 — the lid closes and work continues.
**Does not solve:** F4 (shutdown). Needs the machine powered and on the network.

**Status: VERIFIED on this hardware, 2026-07-29.** macOS 26.5.1 / arm64, watchdog pid 45238,
`SleepDisabled=1`. This closes **V-096**, which had flagged it as the one unproven assumption in
the whole design.

**This is the only lane that helps the in-IDE Cursor agent**, which runs inside the Cursor app on
this Mac and cannot be relocated to any VM. It is therefore the direct answer to the original
request.

---

## How to use

```bash
./scripts/longrun.sh keepalive on --gui --max-hours 24 --min-battery 20
./scripts/longrun.sh keepalive status
./scripts/longrun.sh keepalive off
```

`--gui` pops the **native macOS authorisation dialog** (accepts Touch ID) via `osascript`. Use it
when there is no terminal to type a password into — which includes being driven by an agent.
`--sudo` forces the terminal route; the default `auto` tries cached sudo, then a TTY, then tells
you to use `--gui`.

## Why root, and why a watchdog

`pmset -a disablesleep 1` is **root-only and undocumented** — absent from `man pmset` on 26.5.1,
though `strings /usr/bin/pmset` contains both `disablesleep` and the `SleepDisabled` readback key.
It is also **global and persists across reboots until cleared**.

So the dangerous state is not "failed to set it" — it is "set it and never turned it off." That
is a MacBook that never sleeps again and flattens or cooks itself in a bag. The watchdog
(`scripts/longrun/keepalive_watchdog.sh`) **runs as root specifically so it can revert the flag
without re-prompting**, and reverts on:

- `keepalive off` or the stop sentinel
- `--max-hours` elapsing
- battery dropping below `--min-battery` while discharging
- process exit of any kind (EXIT trap)
- optionally, the last followed tmux session ending (`--follow-sessions`)

## What is proven, and how

Verified by **reading the flag back** after setting it, rather than assuming the write worked:

```
watchdog     : RUNNING (pid 45238)
lid close    : SURVIVES  (SleepDisabled=1)
idle sleep   : held off (longrun caffeinate)
last log     : 2026-07-29T19:36:21-0700 engaged: SleepDisabled=1 (lid-close sleep suppressed),
               min_battery=20% max_hours=24 follow_sessions=0
```

Independently confirmable without trusting `longrun` at all:

```bash
pmset -g | grep -i sleepdisabled   # must print 1
```

Also survived a full `verify_system.sh` run with the **same pid before and after** — the check
`stop spares operator keepalive` exists to guarantee that, after the suite was found tearing
operator keepalives down ([D-4](../decisions/DECISIONS.md)).

### What is NOT proven

**A physical lid-close cycle was never performed.** The flag is confirmed set, and Apple's
`IOPMrootDomain` honours it as a kernel-level veto per multiple secondary sources — but nobody
shut the lid for 15 minutes and checked for an unbroken timestamp series. The research agent
recommends exactly that acceptance test, and several of its sources are vendor blogs for paid
closed-lid apps, so they have a commercial interest.

**Suggested test, not yet run:** set the flag, start a process appending a timestamp every 30s,
close the lid 15 minutes, reopen, check the series has no gap.

## How it fails

| Failure | Detail |
|---|---|
| **Battery** | The practical limit. `--min-battery` reverts the flag while discharging. At 34% discharging that is roughly an hour, **not a night**. Plug in. |
| Low battery generally | A *forced* sleep — no assertion or flag prevents it. `disablesleep` keeps the machine awake right up until the battery empties, then it dies mid-task. |
| **Thermal** | Apple: ambient 10–35°C, and **"don't put anything over the keyboard"** — which is exactly what a closed lid does, and on several MacBook designs the keyboard is part of the intake path. Sustained agent load lid-shut is against Apple's guidance. Never in a bag. |
| macOS 26 wake defects | Tahoe has widely-reported deep-sleep/black-screen-on-wake bugs, and one report implicates `disablesleep` toggling as an aggravating factor. The report labels the link **inferred, not proven**. Treat as risk, not a free switch. |
| `powerd` reset | **UNVERIFIED**, one community source: on Apple Silicon `powerd` may reset the flag when the power source changes. A long run should re-assert and re-verify rather than set once. **The current watchdog does not re-assert.** See [OPEN-QUESTIONS](../decisions/OPEN-QUESTIONS.md). |

## Cost

$0.

## Bugs fixed in this lane

- `keepalive_on` called `die` when sudo was declined, aborting `start` after the tmux session
  already existed. Changed to `return 1` so `start` reports incomplete protection instead of
  crashing.
- Declining sudo leaked a `caffeinate` process, because `keepalive_off` only released it when the
  root watchdog was running. `keepalive_off` now releases `caffeinate` unconditionally first.
- `watchdog_running()` used `pgrep -f keepalive_watchdog.sh`, which **matched the `osascript`
  command line** (it contains the script path) and reported the watchdog as running while only
  the GUI dialog was pending. Now filters `osascript` and exposes a real `watchdog_pid()`.

## Alternatives ruled out — VERIFIED

There is **no Apple-supported way to stay awake lid-shut without an external display.**

- Clamshell mode requires an external display **plus** AC **plus** external keyboard and pointer.
- *Battery → Options → "Prevent automatic sleeping when the display is off"* addresses
  display-off idle sleep on AC, **not** lid close.
- No IOKit assertion type qualifies (see [`PROBLEM.md`](../PROBLEM.md)).

`pmset -a disablesleep 1` is the only public lever for the no-external-display case.
