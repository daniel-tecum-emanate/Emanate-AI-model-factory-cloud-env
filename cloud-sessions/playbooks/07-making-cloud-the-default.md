# 07 — Making cloud the default

> Originally verified against a different repo (`daniel0tgc/internal-company-tool`);
> mechanism-level claims transfer, repo-specific paths (including the bookmark URLs below)
> corrected 2026-08-18.

**There is no "always run in the cloud" setting.** A session runs where you start it, and
nothing in Claude Code's settings, environment variables or config changes that. So
"default to cloud" is not one switch — it is a choice between five routes with different
properties, and one of them is not cloud at all despite appearing next to the others in
the same web UI.

Pick a row, do that thing, stop reading.

---

## If you want X, do Y

| You want | Do this | Runs in the cloud? |
|---|---|---|
| **Never to think about it.** No terminal, no laptop, nothing to remember | Start the task at [claude.ai/code](https://claude.ai/code), or the **Code** tab in the Claude mobile app. Bookmark `https://claude.ai/code?repositories=daniel-tecum-emanate/emanate-tecum-workflow` | **Always.** This is the only unconditionally-cloud route |
| Cloud, but from the terminal you are already sitting in | `cs new --cloud "<task>"` — or `cs handoff "<what to continue>"`, which is the same path without the question | Yes, if you have a TTY. Refuses otherwise |
| To be *asked* each time rather than to default | `cs new "<task>"` | Whichever you pick. `1` is local, `2` is cloud |
| Cloud work to start with **nobody present** | A routine: `/schedule` in any session | Yes. The only route that needs no human |
| The same job on every PR or push | A routine with a GitHub-event trigger | Yes |
| Your local Docker, `.env` and files — but drivable from the phone | `cs rc` | **No.** Remote Control runs on your Mac. See below |
| Typing bare `claude` in this repo to mean cloud | A shell wrapper. **Read the failure modes first** — this is the one route worth talking yourself out of | Conditionally, and it breaks other things |
| To push a session you already started locally *into* the cloud | Not possible from the CLI — handoff is one-way. The Desktop app's **Continue in** menu is the only documented path | `DOCS`, unexercised |

The rest of this file is why, and what each one costs.

## The switch does not exist — and here is what was actually looked for

Three probes against the 2.1.231 binary on this machine, all negative:

- **The settings schema.** Every settings key carries a zod `.describe()` string. Extracted
  all of them and filtered for `cloud`/`remote`/`environment`/`teleport`/`background`. Two
  keys are in this area and neither moves execution: `remote.defaultEnvironmentId`
  (*"Default environment ID to use for cloud sessions"*) and `remoteControlAtStartup`
  (*"Start Remote Control bridge automatically each session"*).
- **Identifiers.** No `defaultToCloud`, `alwaysCloud`, `cloudAtStartup` or equivalent exists
  anywhere in the binary.
- **Environment variables.** No `CLAUDE_*` variable forces a session remote. The ones in
  this space (`CLAUDE_CODE_REMOTE_*`, `CLAUDE_CODE_BRIDGE_SESSION_ID`) are set *by* a cloud
  or bridge session to describe itself, not read to route one.

`VERIFIED 2026-08-14` — commands and output are in this lane's return; they belong in
[`verified-facts.md`](../archive/foreign-repo-provenance-2026-08-18/verified-facts.md) and are not there yet.

`--cloud` is a per-invocation flag with no persistent equivalent. That is the whole finding.

## Route 1 — the browser and the phone

**The only route with no local machine in it at all.** You select the repository and branch
from a picker, type the task, press Enter. Anthropic clones the repo into a VM and starts
working. There is no CLI, no TTY, no git gate, and nothing to push first — the VM clones
what is on GitHub, so whatever is on the remote is what it gets.

Same thing in the Claude mobile app: the **Code** tab has the same repository selector and
the same input box. `DOCS` — Anthropic documents both surfaces as places to *submit* tasks,
not only monitor them. Neither has been exercised from this account, so it is `DOCS`, not
`VERIFIED`.

The closest thing to a real default lives here: **the URL takes query parameters**, so a
bookmark can pre-select the repository and even the prompt.

```
https://claude.ai/code?repositories=daniel-tecum-emanate/emanate-tecum-workflow
https://claude.ai/code?repositories=daniel-tecum-emanate/emanate-tecum-workflow&prompt=Review%20yesterday%27s%20PRs
```

`repositories` (alias `repo`), `prompt` (alias `q`), `prompt_url`, and `environment` are all
accepted. URL-encode the values. If you want one thing on the home screen of your phone and
the bookmark bar of your browser that always starts cloud work in this repo, that is it.

**What you lose here specifically:** cloud sessions offer **Auto**, **Accept edits** and
**Plan** permission modes only. There is no Manual and no Bypass. And sessions started this
way never reach `cs ls` — record them with `cs track <id> "title"` if you want the local
ledger to stay useful.

## Route 2 — `claude --cloud` and `cs` from a terminal

```bash
cs new --cloud "Finish the auth refactor and get the tests green"
cs handoff "Finish the auth refactor and get the tests green"
```

`VERIFIED` — the dispatch chain is proven end to end; see
[verified-facts](../archive/foreign-repo-provenance-2026-08-18/verified-facts.md#the-chain-that-matters). Mechanics are in
[02 — Dispatch](02-dispatch.md); this section is only about using it as a default.

**It needs a TTY, and that is a hard floor.** The CLI's own validation refuses `--cloud`
under `--print`, under a non-interactive invocation, and in combination with `--continue`,
`--resume`, `--from-pr` and `--teleport`. The non-interactive refusal exists because without
it the flag would be *silently ignored and the work would run on your Mac*. So this route
can never be automated: not from a hook, not from cron, not from another agent's Bash tool.

Two exceptions, both worth knowing so you do not go looking for a third:

- **Attaching** to an existing session with `-p` is unaffected. `claude -p "…" --cloud <id>`
  works headlessly, which is what `cs send` uses.
- **Self-hosted pool environments** (`--environment ccpool_…`) bypass the TTY check
  entirely — the CLI's own error text points at `claude -p '<task>' --environment <id>` as
  the headless form. Team and Enterprise only, so **not available on this account**. Noted
  so nobody rediscovers it and thinks the TTY problem is solved.

`cs new` does not remember your answer. There is no `cs config`, no stored preference — by
design, since guessing wrong toward the cloud spends money and guessing wrong toward local
ties the work to a laptop that is about to close. If you want cloud without the question,
type `--cloud`, or use `cs handoff`, which only has the one path.

## Route 3 — routines

```
/schedule daily PR review at 9am
```

`VERIFIED 2026-08-12` — created and fired. **The only way to start cloud work with no human
at a terminal**, precisely because it does not go through the TTY-gated CLI path. See
[05 — Routines](05-routines.md).

If "default to cloud" really means "this work should happen whether or not I sit down
today", a routine is the answer and none of the other routes are. Read 05 first for the two
footguns: routines inherit every connected claude.ai connector unless you pass an explicit
list, and "Run now" does not consume a one-off schedule.

## Route 4 — Remote Control is not this

Remote Control **runs on your Mac.** It is in this playbook only because it appears in the
same session list at claude.ai/code, with the same phone app pointed at it, and it is
therefore the easiest thing in this folder to mistake for a cloud session.

Two verified facts keep the distinction sharp:

- Starting Remote Control **registers a `bridge` environment** in the cloud-environment
  picker, named after your machine. Selecting a bridge environment for a routine or a cloud
  session routes that work **back to this Mac** — silently removing the one property you
  were defaulting to cloud for. `cs env` warns about this.
- Server mode prints a spawn URL from which you can start *new* sessions from your phone.
  Those sessions run **on the Mac**. "Started it from my phone" is not "it is in the cloud".

Both in
[verified-facts](../archive/foreign-repo-provenance-2026-08-18/verified-facts.md#remote-control-server-mode-registers-a-bridge-environment--verified).
The phone half of Remote Control is still untested here; see [04](04-remote-control.md).

## Route 5 — a shell wrapper, and why to think twice

The idea: make bare `claude` in this repo route through `cs new`, so the chooser appears
whether or not you remembered to ask for it.

It works, and it breaks four things. **This folder deliberately does not ship it** —
`~/.zshrc` is yours, and the tooling lane owns `bin/`. What follows is enough to decide.

### The two shapes, and only one of them is survivable

**A shell function or alias in `~/.zshrc`** is scoped to *interactive zsh only*. Verified on
this machine: a non-interactive tool shell does not load `~/.zshrc` at all — its `PATH`
lacks both entries `~/.zshrc` adds (`~/.local/bin` and `cloud-sessions/bin`). So scripts,
hooks, `cs` itself and anything Claude Code spawns are untouched. That is the correct blast
radius.

**A shim script named `claude` earlier on `PATH`** is seen by everything, and one of the
things it is seen by is `cs`. `resolve_claude` in [`bin/cs`](../bin/cs) takes
`command -v claude` as its second choice — so `cs start` would exec your shim, which calls
`cs new`, which calls `cs start`. **A fork bomb, from a two-line convenience.** `cs doctor`
would break the same way, since it runs `"$CLAUDE" --version` and `"$CLAUDE" auth status`.
If you build a shim anyway, set `CS_CLAUDE_BIN=$HOME/.local/bin/claude` — `cs` honours it
ahead of `PATH` — and understand that you are now maintaining a loop-breaker.

### What breaks either way

`cs new`'s argument parser dies on any unrecognised flag and swallows everything else as
task text. So a naive `claude() { cs new "$@"; }` turns:

| You type | What happens |
|---|---|
| `claude --version` | `error: unknown flag: --version` |
| `claude -p "…"` in a script | `error: unknown flag: -p` — every non-interactive use is dead |
| `claude doctor` | the chooser asks where to run a session whose *task* is the word "doctor" |
| `claude auth login` | same — you cannot log in any more |
| `claude update`, `claude mcp list` | same |
| `claude --resume`, `claude -c` | `error: unknown flag` |

The `-p` row is the expensive one. `cs send` itself shells out to `claude -p … --cloud`, and
so does anything else that talks to a session headlessly.

### If you build it anyway

Guard on all four, not one:

- **first argument only.** Pass through anything starting with `-`, and any known subcommand
  (`doctor`, `auth`, `mcp`, `update`, `install`, `agents`, `plugin`, `project`,
  `setup-token`, `ultrareview`, `gateway`, `import`).
- **`$CLAUDECODE`.** It is `1` inside a running session — verified in this one. A nested
  `claude` from a session's Bash tool must not hit the chooser, which has nothing to read
  from and would hang or refuse.
- **`[[ -t 0 && -t 1 ]]`.** No TTY, no chooser.
- **the repo.** Scope it to this checkout unless you want the chooser in every project.

That is roughly fifteen lines of shell standing between you and your own CLI. The cheaper
move is a *new* name — `alias cc='cs new'` — which shadows nothing and costs one character
more than the wrapper you were about to write.

## What you give up by defaulting to cloud

None of this is fixable with configuration. It is what a different machine means.

| Gone | Detail |
|---|---|
| **Your Docker and Supabase stacks** | Twenty is a local `docker compose` stack; the environment snapshot **does not capture running processes**, so even a cached image gives you an empty database, not your data |
| **`.env` and every secret** | Gitignored, and cloud environments have **no secrets store** — environment variables are readable by anyone who uses that environment. `mcp__supabase__*` is unreachable for the same reason |
| **Meeting capture** | `swiftc`, ScreenCaptureKit and `ffmpeg -f avfoundation` are macOS-only. Permanently local. *Reading* finished transcripts in the cloud is fine |
| **The machine itself** | **4 vCPU, 15 GB RAM, ~30 GB usable disk**, Ubuntu 24.04 on **x86_64** — not your Mac's arm64. Memory-hungry builds get killed; arm64 wheels do not install |
| **A TTY** | Dispatch is TTY-gated, so a cloud-by-default habit cannot be scripted. Routines are the escape hatch |
| **Manual and Bypass permission modes** | Cloud sessions offer Auto, Accept edits and Plan only |
| **Your working tree** | The VM clones the **GitHub remote**. Uncommitted and unpushed work does not exist to it. `cs start` gates on this rather than letting you find out from a confusing startup error |
| **`HEAD` on a branch** | Cloud checkouts start **detached**. Anything assuming `git rev-parse --abbrev-ref HEAD` returns a branch name gets `HEAD` |

Full accounting of what this repo specifically can and cannot do there:
[`this-repo-in-the-cloud.md`](../archive/foreign-repo-provenance-2026-08-18/this-repo-in-the-cloud.md). VM measurements:
[verified-facts](../archive/foreign-repo-provenance-2026-08-18/verified-facts.md#confirmed-and-worth-knowing).

## Settings keys that sound like the switch and are not

| Key | What it actually does |
|---|---|
| `remoteControlAtStartup` | Starts the **Remote Control bridge** each session — the work still runs on your Mac. `VERIFIED` the key exists and carries that description in 2.1.231; its behaviour is `DOCS`, unexercised. It is security-sensitive: **project and local settings can only turn it off, never on** — a `true` there is ignored with a warning, so it must live in `~/.claude/settings.json`. It can also be on from an org default without appearing in any settings file |
| `remote.defaultEnvironmentId` | Chooses **which** environment a cloud session lands in, not whether one is created. Worse, pointing it at a **bridge** environment routes dispatches back to this Mac. `cs env` reads and warns about it |
| `autoUploadSessions` | *"Mirror local sessions to claude.ai as view-only"* — your **local** session becomes visible in the web list. Nothing moves. This is the single most likely way to convince yourself you are running in the cloud when you are not |
| `defaultToAgentsView` | Opens the background-agents view by default. Background agents (`--bg`, `claude agents`) run **on your Mac** — the CLI is explicit that `--bg` and `--cloud` "are different backends" and refuses to combine them |

## Route status, honestly

| Route | Status here |
|---|---|
| `claude --cloud "<task>"` / `cs handoff` / `cs new --cloud` | **`VERIFIED`** — [the chain](../archive/foreign-repo-provenance-2026-08-18/verified-facts.md#the-chain-that-matters) |
| Routines (`/schedule`) | **`VERIFIED`** — [4b](../archive/foreign-repo-provenance-2026-08-18/verified-facts.md#4b-routines-fire-and-clone--verified) |
| `cs new` chooser and its refusal paths | **`VERIFIED`** — exercised by `tests/run.sh`, re-confirmed on Linux in [probe 6](../archive/foreign-repo-provenance-2026-08-18/verified-facts.md#cloud-sessions-are-logged--but-the-record-dies-with-the-vm--verified-2026-08-14). Not run while writing this file |
| Remote Control registers a **bridge** environment that routes work back to the Mac | **`VERIFIED`** |
| Starting a session in the browser at claude.ai/code | **`DOCS`** — documented, never exercised from this account |
| Starting a session from the Claude mobile app's Code tab | **`DOCS`** — same |
| `?repositories=` / `?prompt=` URL pre-fill | **`DOCS`** — documented parameters, untested |
| Desktop app **Continue in** → send a local session to the web | **`DOCS`** — the app is installed on this machine; the route is unexercised |
| Headless dispatch via `--environment ccpool_…` | **`DOCS`**, and **unavailable** — Team/Enterprise only |
| Shell wrapper or alias | **`UNVERIFIED`** — not built. The failure modes above come from reading [`bin/cs`](../bin/cs) and from measuring shell scoping on this machine |
| No settings key makes cloud the default | **`VERIFIED`** by negative search — method above |

## What to actually do

Two things, and neither is a wrapper:

1. **Bookmark `https://claude.ai/code?repositories=daniel-tecum-emanate/emanate-tecum-workflow`** in the
   browser and on the phone's home screen. It is the only route that is cloud
   unconditionally, and it removes the decision instead of automating it.
2. **Use `cs handoff "<what to continue>"` from the terminal**, not `cs new`. Same gates,
   same brief, no question to answer — the question is the thing you were trying to stop
   thinking about.

Then put anything genuinely recurring behind a routine, and leave `cs new` for the times
when the answer honestly might be "here".

## Sources

- [Get started with Claude Code on the web](https://code.claude.com/docs/en/web-quickstart) — repository picker, permission modes, URL pre-fill
- [Use Claude Code on the web](https://code.claude.com/docs/en/claude-code-on-the-web) — `--cloud`, `--teleport`, Desktop **Continue in**
- [Remote Control](https://code.claude.com/docs/en/remote-control)
- [Routines](https://code.claude.com/docs/en/routines)

## Next

→ [08 — Concurrent sessions and exodus](08-concurrent-sessions-and-exodus.md)
