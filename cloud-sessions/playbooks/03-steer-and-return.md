# 03 — Steer it, then bring it home

A dispatched session is not fire-and-forget. There are three ways to talk to it, and one
way to pull it back onto your machine.

---

## Where your sessions are

```bash
cs ls          # dispatched from this machine, survives reboots
cs web         # the authoritative list at claude.ai/code
cs open last   # jump straight to the most recent one
```

`/tasks` inside any Claude Code session shows the same thing, and pressing `t` there
teleports into one.

`cs ls` reads a local ledger (`state/sessions.jsonl`) because there is no CLI command that
lists cloud sessions non-interactively. Sessions you start on the web or the phone will
not appear until you record them with `cs track <id> "title"`. claude.ai/code is always
the truth.

## Steering, three ways

### From a shell — works headless

```bash
cs send last "Skip the Redis migration for now, focus on Postgres"
cs send session_01ABC… "What's blocking you?"
```

Queues the message and exits. **No TTY required**, sends no local state, and works from
any machine logged into the account — including a shell on your phone. This is the only
part of the loop that is scriptable.

### From the web or the phone — the real conversation

Open the session at [claude.ai/code](https://claude.ai/code) or in the Claude mobile app
and talk to it like any other conversation: answer its questions, leave inline comments
on the diff, redirect it. This is where the interactive attach/detach loop actually
lives.

If a session asks a question and sits idle, your answer still lands when you come back —
up to environment expiry.

### From a terminal — not available here

`claude --cloud <session-id>` without `-p` is documented to attach your terminal directly
to the session. On this account it returns:

```
Error: Attaching to an existing cloud session is not enabled for your account.
```

It is behind a gradual rollout. Verified 2026-08-12: interactive terminal attach is not
available on this account.
Nothing else is affected — `cs send` works regardless.

## There is no stop

Nothing in the CLI or the API stops a running cloud session. `cs` has no `cs stop`, and that
is a gap in the platform rather than in the tool.

What you can actually do:

```bash
cs send last "Stop here. Commit what you have, summarise what is done and what is not."
```

…then archive the session from [claude.ai/code](https://claude.ai/code). Archived sessions are
read-only and accept no further messages.

`cs rm <id>` only removes the local ledger row — it does **not** stop, archive, or delete
anything. Use it when you have recorded the wrong id, not to end a session.

This matters because dispatch consumes account rate limits and
[02-dispatch](02-dispatch.md#parallel-work) encourages firing several at once. Three runaway
sessions cannot be killed; they can only be told to stop and then archived.

## Bringing it home

```bash
cs tp last          # or: cs tp session_01ABC…
```

Teleport pulls the cloud session into your terminal. It validates the session, fetches the
conversation logs, resolves the branch, checks it out, and drops you into the session with
its **full history** — including messages you sent headlessly with `cs send`.

**Requirements**, all checked before anything happens:

| | |
|---|---|
| Clean working tree | `cs tp` refuses rather than stashing behind your back |
| Same repository | a checkout of the session's repo, not a fork |
| Branch pushed | teleport fetches and checks it out for you |
| Same account | the claude.ai account that owns the session |

**Teleport is one-way.** Once local, the terminal copy is independent: new work there does
not flow back to the cloud session on claude.ai or the phone. If you want to keep steering
from your phone after teleporting, start Remote Control in the local session — see
[04](04-remote-control.md).

`--teleport` is not `--resume`. `--resume` reopens a conversation from this machine's local
history and never lists cloud sessions.

## Finishing up

From the web session you can create a PR directly. To have Claude keep watching that PR
for CI failures and review comments, turn on auto-fix — from the terminal, run
`/autofix-pr` while on the PR's branch.

Archive finished sessions from the sidebar to keep the list readable. Archived sessions
are read-only.

## Next

→ [04 — Keep it local, drive it from your phone](04-remote-control.md)
