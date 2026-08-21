# PROBLEM — what "keep running when I close my computer" actually means

Read this before proposing any new lane. Most wrong answers in this space come from solving one
of the four problems below and assuming the others came along for free.

---

## Four failure modes wearing one coat

The original request was *"set up a virtual machine or process so that cursor and claude sessions
remain running even when I close my computer."* Four distinct things can end a session, and they
need different mechanisms:

| # | Failure mode | What must be true to survive it |
|---|---|---|
| F1 | The **terminal or IDE window** closes | Process detached from that terminal's process group |
| F2 | The machine **idles** into sleep | A power assertion held (`caffeinate -i`) |
| F3 | The **lid closes** | A kernel-level sleep veto (`pmset disablesleep`) — no user-space assertion can do this |
| F4 | The machine is **shut down** or leaves the network | Work executes on hardware that is not this machine |

F1 and F2 are the easy ones and are what most advice on the internet addresses. F3 has exactly
one lever and it is undocumented. F4 cannot be solved locally by definition — no amount of
cleverness on this Mac keeps work running while the Mac is off.

## Why the obvious answers fail

### `caffeinate` cannot survive a lid close — VERIFIED, from Apple

This is not a limitation of how we invoked it. Apple's own docs close the door:

> "Neither this API nor any other API allow an application to prevent forced system sleep
> (e.g. lid close sleep, Apple Menu sleep, thermal emergency sleep, or low battery sleep)."
> — IOKit Power Management Release Notes

Two further primary sources say the same (QA1340; the
`kIOPMAssertionTypePreventUserIdleSystemSleep` reference). Full citations in
`PRs/longrun-remote/RESEARCH-FINDINGS.md` §1c.

**Extra trap:** `man caffeinate` on this machine says `-s` "is valid only when system is running
on AC power." So on battery, `-s` is a no-op. `longrun` uses `-ism`, and it is the `-i` (idle)
assertion that carries battery runs. Anything documenting `-s` as battery protection is wrong.

**Confirmed empirically on 2026-07-29**, not just from the man page. `dashboard_run.sh` holds a
`caffeinate` (pid 5330) asserting **both** `PreventUserIdleSystemSleep` and `PreventSystemSleep`,
continuously, for 39 hours:

```
pid 5330(caffeinate): 39:01:15 PreventSystemSleep named: "caffeinate command-line tool"
    Details: caffeinate asserting on behalf of '…/scripts/dashboard_run.sh' (pid 5309)
```

The machine nonetheless **slept at 20:25:26 while on battery at 24% charge**
(`pmset -g log`; see [`evidence/`](evidence/command-log-2026-07-29.md)). A held
`PreventSystemSleep` did not prevent sleep on battery — exactly as documented, now observed. Do not
treat a live assertion in `pmset -g assertions` as proof the machine will stay awake; check the
power source too.

Consequence: F3 needs `pmset -a disablesleep 1`, which is root-only, undocumented, global, and
persists across reboots until cleared. That is [lane 02](lanes/02-lid-close-pmset.md), and the
entire reason it ships with a watchdog whose main job is turning the flag back **off**.

### `nohup … & disown` does not detach a process — VERIFIED, reproduced locally

This one actively bit us, and the popular explanation for why the fix works is also wrong.

`nohup` sets SIGHUP to `SIG_IGN`. `disown` removes the job from the shell's job table. **Neither
changes the child's process group.** A `nohup`'d, `disown`'d child reparents to init — which is
why it *looks* detached in `ps` — but stays in the launching shell's process group, so any
group-addressed signal reaps it. An IDE-integrated shell, a CI wrapper, or an agent harness
cleaning up after a finished command all do exactly that.

Reproduced on this machine (launcher pid/pgid 14520):

```
  PID  PPID  PGID  COMMAND
14521     1 14520  worker.sh    <- nohup + disown: reparented, but PGID is still the LAUNCHER'S
14527     1 14526  worker.sh    <- double-fork + setsid: own session and process group

$ kill -TERM -- -14520
  pid=14521 DEAD     <- nohup + disown
  pid=14527 ALIVE    <- setsid
```

SIGHUP was never the killer: lane A died from a **group-addressed SIGTERM**, and `nohup` would
have ignored a SIGHUP anyway. `setsid` is what matters — new session, new process group, no
controlling terminal — and the second fork stops the child being a session leader, so it can
never reacquire one.

**Portability trap:** macOS ships **no `setsid(1)` binary** (`which setsid` → not found; it is a
util-linux tool). Any spawner that shells out to `setsid` works on Linux and silently fails on
the Mac. Hence `scripts/longrun/spawn_detached.py` uses Python's `os.setsid()` between two
`os.fork()` calls.

### Cloud VMs clone from origin, not from your disk — VERIFIED

Both cloud lanes (03, 04) run on a VM that clones your **git remote**. It cannot see uncommitted
work, and a branch that exists only locally fails at startup with an error that reads like a git
problem but is not. **Push before dispatching.** This cost debugging time in the
`factory-cursor-bridge-v1` C1 spike before it was understood, which is why
`scripts/longrun/longrun_cloud.py` pre-flights it and refuses with a specific message.

## The requirement that is still unmet

Lanes 02 and 03 do not cover one real case, and it is the one Daniel described:

> a **long, interactive** session he attaches to, steers, and detaches from repeatedly over
> days, from anywhere — including from a phone, with the laptop shut and in a bag.

- Lane 02 needs the laptop powered and on the network.
- Lane 03 is fire-and-steer: you can send follow-up messages, but there is no attach/detach loop.
- Lane 04 may close this via `--teleport`, but teleport is one-way (cloud → terminal) and has not
  been exercised yet.

Closing it properly wants a small always-on Linux box running tmux plus the CLIs, reachable over
SSH/Tailscale from a phone. That is [lane 07](lanes/07-always-on-vm-BLOCKED.md), unbuilt, and
blocked on recurring spend — which is Daniel's call, not an agent's (invariants I5/I9). Filed as
**V-095**.

## Constraints any new lane must respect

1. **The in-IDE Cursor agent cannot be relocated.** It runs inside the Cursor app on this Mac.
   For that specific agent, lane 02 is the only answer that will ever exist. Any proposal that
   "moves it to the cloud" has misunderstood the problem.
2. **Migration work needs local Supabase via Docker.** A remote box must be sized for it; the
   4GB tier is tight.
3. **An un-reverted `disablesleep` is a MacBook that never sleeps and cooks itself in a bag.**
   Every path that sets it must guarantee it comes back off, including on crash.
4. **Recurring spend is Daniel's decision** (I5/I9). File it to `validation/QUEUE.md`; never
   provision.
5. **Never write to `scripts/lib/` or `PROCESSES.md`** — integrity-guarded surfaces. `longrun`
   helpers live in `scripts/longrun/` for exactly this reason, after tripping the alarm once.
6. **Rate limits are a design input, not an edge case.** Cursor's `/v0/repositories` allows ~1
   request/user/minute. Treating a 429 as an empty result produces the *opposite* diagnosis of
   the truth. See [lane 03](lanes/03-cursor-cloud.md).
