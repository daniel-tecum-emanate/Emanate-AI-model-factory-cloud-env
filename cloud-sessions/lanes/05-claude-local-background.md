# Lane 05 — Claude local background agents (`--bg` / `claude agents`)

**Solves:** F1 (terminal closes). With [lane 02](02-lid-close-pmset.md), also F3.
**Does not solve:** F4 — these run **on this machine** and die with it.

**Status: available on the installed CLI, unexercised.** Discovered while checking whether a
first-party cloud lane existed; it is the *local* sibling of [lane 04](04-claude-code-cloud.md).

---

## What it is — VERIFIED from `claude --help` on 2.1.206

```
--bg, --background   Start the session as a background agent and return immediately
                     (manage with `claude agents`)
```

```
claude agents --help
  --cwd <path>                            Show only background sessions started under <path>
  --all                                   With --json: include completed sessions
  --json                                  Print active sessions as a JSON array (for scripting)
  --add-dir <directory>                   Additional directory for dispatched sessions
  --agent <agent>                         Default agent for sessions dispatched from agent view
  --effort <level>                        Default effort for sessions dispatched from agent view
  --allow-dangerously-skip-permissions    Make bypass mode available to dispatched sessions
```

## Why it is local, not cloud — and why that matters

The wording is decisive: `--cwd` filters "background sessions started **under** `<path>`", and the
options configure "**dispatched** sessions." These are child sessions on this filesystem. There is
no remote VM.

This distinction was briefly muddled during the session — `--bg` was found while looking for
`--cloud`, and it is easy to read "background agent" as "runs somewhere else." It does not. A
powered-off Mac takes these with it.

## Where it might beat tmux

`claude agents --json` is a **scriptable, first-party** session list. [Lane 01](01-local-tmux.md)
reimplements roughly this with tmux plus a pane-log plus `.longrun/` bookkeeping — including a
buffering bug that had to be fixed by hand. For Claude sessions specifically, `--bg` may simply be
better, and `--json` would make `longrun ls` trivial.

**Not evaluated.** Open questions before switching anything:

- Do `--bg` sessions survive the *launching terminal* closing? (Lane 01's whole spawner exists
  because `nohup`+`disown` did not — see [`PROBLEM.md`](../PROBLEM.md). Same test applies here and
  must not be assumed.)
- Do they survive a `claude` process upgrade or restart?
- Is output retrievable after the fact, comparably to lane 01's pane log?
- Do they interact correctly with lane 02's keepalive, or does `longrun stop` orphan them?

Until those are answered, tmux stays the substrate — it is the one that has regression tests.

## Cost

$0 beyond normal Claude usage.

## Recommendation

Treat as a **candidate simplification of lane 01**, not a cloud lane. Do not let its name imply
it addresses "computer closed" — it does not, on its own.
