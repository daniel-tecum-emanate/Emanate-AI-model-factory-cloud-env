# Troubleshooting

> **Provenance note, 2026-08-18:** originally written against a different repo
> (`daniel0tgc/internal-company-tool`) — mechanism-level content stands. Any `atlas-os/`
> mention below describes a dependency absent from this repo (`emanate-tecum-workflow`).

Error text → what it actually means → fix. Start with `cs doctor`; it catches most of
the first section before you hit it.

---

## Dispatch

**`--cloud requires an interactive terminal.`**
You piped the output, redirected it, or ran it from a script, a hook, or another agent's
Bash tool. The CLI refuses rather than silently running the work locally. Run `cs start`
from a real terminal. To steer an existing session from a script, use `cs send` — that
path needs no TTY.

**It hangs on "Is this a project you created or one you trust?"**
A freshly installed CLI has no trust record for that directory. Run `claude` once by hand
in the repo and accept. This blocks automation silently because nothing is typing.

**`Unable to get organization UUID`, or a message about API key authentication**
You are authenticated with an API key, or your cached account details are stale. Run
`/login` and sign in with claude.ai. Check for `ANTHROPIC_API_KEY` or
`ANTHROPIC_AUTH_TOKEN` in your shell — they take precedence over the claude.ai login.
`cs doctor` flags both.

**`Cloud sessions aren't available with <provider>`**
Claude Code is pointed at Bedrock, Vertex, or Foundry. Unset the provider variable
(`CLAUDE_CODE_USE_BEDROCK`, etc.) and sign in with an Anthropic account.

**`Cloud sessions are disabled by your organization's policy`**
The `allow_remote_sessions` policy is off. An Owner enables it at
claude.ai/admin-settings/claude-code.

**`Session creation failed`, or it stalls at provisioning**
A VM could not be allocated. Check [status.claude.com](https://status.claude.com), wait a
minute and retry, and confirm the connecting GitHub account can actually see the repo.

**The session starts but complains about the branch**
Almost always "you did not push", not a git problem. The VM clones **origin**; a
local-only branch or uncommitted work does not exist as far as it is concerned. `cs start`
refuses before dispatch for exactly this reason — if you bypassed it, push and re-dispatch.

**`403 You don't have access to a repository this routine uses`** — but `cs start` works
GitHub is not connected to your **Claude account**. `cs start` hides this by silently
bundling the local repo; a routine has no local machine to bundle from, so it fails
honestly. Run `/web-setup` in a session and accept the *"Connect Claude on the web to
GitHub?"* prompt, or install the [Claude GitHub App](https://github.com/apps/claude).

**The session ran but there is no branch and no PR**
Same cause. A bundled session cannot push back to a remote. Connect GitHub, or retrieve
the work with `cs tp` instead.

**Every session fails with an authentication error**
If the organization has IP allowlisting enabled, all Anthropic-hosted cloud sessions fail,
because they call the API from Anthropic's infrastructure rather than your network.
Requires a support exemption. This one looks like a credentials problem and is not.

## Steering

**`Attaching to an existing cloud session is not enabled for your account.`**
Interactive terminal attach — `claude --cloud <id>` with no `-p` — is behind a gradual
rollout, and it is **off for this account** (verified 2026-08-12). Use
[claude.ai/code](https://claude.ai/code) or the mobile app to converse with the session,
and `cs send` to queue messages from a shell. Contact your Anthropic account team if you
want the terminal form.

**`Session not found: <id>`**
Wrong ID, or the session belongs to a different account. Check it against the session's
claude.ai/code URL. `cs send` accepts a full URL as well as a bare ID.

**`cloud session <id> is archived and cannot accept new messages`**
Archived sessions are read-only. Start a new one.

**The session went quiet and the web UI says it expired**
Idle expiry reclaimed the VM. Reopen it from claude.ai/code — you get a fresh VM with the
conversation history restored. Anything that was *running* (a compose stack, a dev
server) is gone; ask Claude to restart it.

## Teleport

**`teleport requires a clean working tree`** (from `cs tp`)
Commit or stash first. The bare CLI offers to stash for you; `cs tp` refuses instead,
because a surprise stash is a bad thing to discover later.

**It errors naming two different repositories**
You ran teleport from a checkout of the wrong repo, or from a fork. It must be the same
repository. If your remote is an SSH host alias (`git@work:owner/repo.git`) that cannot
be parsed into a hostname, you will be asked to confirm.

**`Remote Control session expired` / `Access denied` during teleport**
Teleport rides on the Remote Control session infrastructure, so its errors surface with
Remote Control wording. Run `/login` to refresh credentials and confirm you are on the
account that owns the session.

**`--teleport` is unavailable**
Requires claude.ai subscription auth. If you are on an API key, run `/login`. If you are
already signed in via claude.ai and it is still unavailable, cloud sessions are disabled
for your organization.

## Remote Control

**`Remote Control requires a full-scope login token`**
You are on a long-lived token from `claude setup-token` or `CLAUDE_CODE_OAUTH_TOKEN`.
Those can only make model requests. Run `claude auth login`.

**`Remote Control requires feature-flag evaluation`**
One of `DISABLE_TELEMETRY`, `DO_NOT_TRACK`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, or
`DISABLE_GROWTHBOOK` is set — each disables the feature-flag check Remote Control depends
on. Unset it in your shell and in the `env` block of any `settings.json`.

**`Remote Control is only available when using Claude via api.anthropic.com`**
`ANTHROPIC_BASE_URL` points somewhere else (a gateway or proxy), or a cloud provider
variable is set. Unset it.

**The session died while I was away**
The Mac was offline for more than ~10 minutes, or the terminal running it was closed.
Remote Control is a local process — if you need it to survive an SSH disconnect, start it
inside `tmux`. If you need it to survive the Mac being *shut*, you want a cloud session
instead.

## Routines

**`/schedule` returns "Unknown command"**
Usually one of: API-key or cloud-provider auth instead of claude.ai; one of the
telemetry-disabling variables above is set; you are inside a cloud session (manage them
on the web instead); or an Owner turned routines off. You can always manage routines at
claude.ai/code/routines regardless of CLI configuration.

**The routine ran green but did nothing**
Green means the session started and exited without an infrastructure error. It does not
mean the task succeeded. Open the run and read the transcript — blocked network requests,
missing connector tools, and task failures all surface there and nowhere else.

**A GitHub trigger did not fire**
During the research preview there are per-routine and per-account hourly caps, and events
beyond the limit are **silently dropped**. Also confirm the Claude GitHub App is installed
on the repository — `/web-setup` grants cloning access but does **not** install the App or
enable webhooks.

## `cs` itself

These are `cs`'s own refusals, not the CLI's. They are all deliberate: the tool would
rather stop than guess.

**`cs new needs a terminal to ask in. Use --here or --cloud, or set CS_DEFAULT.`**
You ran the chooser somewhere with no TTY — a script, a hook, another agent's Bash tool, or
inside a cloud session. It refuses rather than picking a side, because guessing *cloud*
dispatches real work and guessing *here* ties the work to a laptop that is about to close.
Say which explicitly, or set `CS_DEFAULT=cloud|here|rc|ask` once in your shell.

**`CS_DEFAULT is '<x>' — expected here, cloud, rc, or ask`**
A typo in that variable. It refuses instead of falling back to a default, for the same
reason.

**`a cloud session runs unattended, so it needs a task.`**
`cs new --cloud` with no task. A local session can start empty and wait for you; a cloud
one has nobody to wait for.

**`flags must come before the task.`**
`cs start "…" --bundle` would otherwise swallow the flag inside the task text and dispatch
the *opposite* of what you asked — a clone recorded as a clone. If the task text genuinely
contains that word, put it after `--`.

**`not a session id: <what you typed>`**
`cs` validates ids rather than accepting anything. It takes `session_…`, `cse_…` (routine
sessions use that prefix), or a full `claude.ai/code` URL. A typo used to be accepted
silently and every later command then targeted nothing.

**`NOT IN EFFECT: <id> in project settings outranks the user layer.`** (from `cs env set`)
The write succeeded, but it went to the lowest-precedence settings file and something
higher up still decides. Edit the file it names. This message exists because the command
used to report success while changing nothing.

**`dispatched, but no session ID was found in the output.`**
The dispatch may well have worked — `cs` just could not find the `View:` line to record.
Get the id from [claude.ai/code](https://claude.ai/code) and `cs track` it. If this starts
happening every time, suspect that the CLI's output shape changed; the regression suite
cannot catch that, by construction.

**`no browser opener on this system. Open it yourself: <url>`**
`cs open` / `cs web` on a machine with neither `open` nor `xdg-open` — a Linux box or a
cloud session. Not an error; it prints the URL and exits 0.

**`mktemp: too few X's in template 'cs-dispatch'`, or `script: unexpected number of
arguments`, on Linux**
You are running a copy of `cs` from before 2026-08-14. Both are BSD-only constructs that
GNU rejects; the second one failed *silently* because it was swallowed by `|| true`. Pull
the current `bin/cs` — `run_under_pty`, `cs_mktemp` and `open_url` probe the platform at
startup now.

**The cloud session finished, but there is no telemetry from it**
Working as intended. Hooks do fire in cloud sessions, but they write to
`atlas-os/telemetry/raw/`, which is gitignored and lives on a VM that gets reclaimed. A
session is **told not to** commit its own trace: that fix shipped on 2026-08-14 and was
reverted the same hour, because a self-committed record is cooperative rather than
involuntary, and is silently truncated at the last commit.
What survives is the dispatch row in `state/sessions.jsonl` and the branch or PR.

## When nothing above fits

1. `cs doctor` — it checks auth method, conflicting env vars, git state, and TTY.
2. `claude doctor` — installation health.
3. `/doctor` inside a session — the fuller checkup that can also fix things.
4. [status.claude.com](https://status.claude.com).
5. [Error reference](https://code.claude.com/docs/en/errors) for runtime API errors
   (`429`, `529 Overloaded`, `Prompt is too long`) — those are shared with the local CLI
   and are not cloud-specific.
