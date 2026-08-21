# Lane 01 — local tmux session

**Solves:** F1 (terminal/IDE closes) and F2 (idle sleep).
**Does not solve:** F3 (lid close) or F4 (shutdown). Pair with [lane 02](02-lid-close-pmset.md)
for a closed lid.

**Status: WORKING**, covered by 5 regression checks in `verify_system.sh` section `━ 14`.

---

## How to use

```bash
./scripts/longrun.sh start "task description"    # detached tmux session + caffeinate + keepalive
./scripts/longrun.sh ls                          # list sessions
./scripts/longrun.sh attach <name>               # attach (Ctrl-b d to detach)
./scripts/longrun.sh stop <name>                 # tear down, release assertions
./scripts/longrun.sh doctor                      # which failure modes are survivable right now
```

`start --no-keepalive` skips the power layer. That flag exists for `verify_system.sh`, which
starts and stops a throwaway session daily and must not disturb a real keepalive — see
[D-4](../decisions/DECISIONS.md).

## What is proven

From `verify_system.sh` section `━ 14`, all passing:

- `spawn_detached outlives launcher` — the detachment actually holds
- `start creates detached session`
- `pane log captures unbuffered` — output lands in the log immediately
- `stop tears down + releases`
- `stop spares operator keepalive`

## Two bugs that shaped this lane

**The spawner.** `caffeinate -ism -w $$` was originally launched with `nohup … & disown` and
**died with its launching shell**, which would have left `keepalive` reporting an assertion that
nothing was holding — a silent false green, the worst possible failure for this feature. The
mechanism is process-group signal delivery, not SIGHUP; full reproduction in
[`PROBLEM.md`](../PROBLEM.md). Fixed by `scripts/longrun/spawn_detached.py` (double fork +
`os.setsid()` in Python, because macOS has no `setsid(1)` binary).

**The log.** Logging via `tmux pipe-pane … 'cat >> logfile'` block-buffers, so the log lagged the
live pane and appeared to be missing content. Changed to `exec tee -a logfile >/dev/null`. The
`pane log captures unbuffered` check exists to keep it that way.

## How it fails

| Failure | Result |
|---|---|
| Lid closes without lane 02 | Machine sleeps; session freezes until reopened |
| Machine shuts down | Session gone — tmux state is not persisted across boots |
| Battery empties | Forced sleep; no assertion can prevent it (Apple, per `PROBLEM.md`) |

## Cost

$0. Operator-invoked, no launchd label, no schedule — the `no launchd label (operator-only)`
check enforces that it never becomes a background job without being registered in `PROCESSES.md`.

## Possible improvement, not done

`claude --bg` ([lane 05](05-claude-local-background.md)) covers similar ground natively with
`claude agents` as the manager, and may be a better substrate than tmux for Claude specifically.
Unexercised. tmux remains the right answer for anything that is not Claude.
