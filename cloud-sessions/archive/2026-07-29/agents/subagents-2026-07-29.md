# Subagents spawned — 2026-07-29

Five subagents. **One succeeded, three died on the same transient network error, one is still
running.** Recorded in full — including what the successful one got *wrong* — because a
confidently wrong subagent result is more dangerous than a failed one.

---

## Summary

| Agent | Task | Outcome |
|---|---|---|
| `01de80ba-6b3a-4625-8263-b8ce05ae37ab` | Verify pmset / Actions / Cursor / Claude facts + costs | **SUCCESS** — the most valuable output of the session |
| `e0407fb7-8b4b-4dad-b39b-0926a6d41d43` | Cursor cloud session manager (`ls`/`status`/`logs`/`send`/`stop`) | **FAILED** — network error, nothing written |
| `f7925a60-ab54-49d2-86d3-73e2aa79ca2f` | GitHub Actions remote lane for Claude sessions | **FAILED** — network error. **Not resumed by choice** |
| `8dc32620-0bd9-4dca-baf7-f6aa231bf108` | Always-on VM provisioning + ssh attach driver | **FAILED** — network error |
| `fced0e40-9237-4a98-8a04-99bd027f3b5b` | Cursor cloud session manager (re-spawn of the first) | **SUCCESS** — all five subcommands proven live; suite 76 → **83** |

## The shared failure

Three of the four died with:

```
getaddrinfo ENOTFOUND agentn.global.api5.cursor.sh
```

A DNS resolution failure against Cursor's agent API — infrastructure, unrelated to the tasks. All
three had produced **no durable output**, so the work was lost entirely rather than partially.

**Operational lesson:** three parallel subagents on long build tasks were wiped by one transient
DNS blip. Long-running subagents should checkpoint findings to a file **as they go** rather than
only in a final report. Had they done so, two of three would have been resumable. The re-spawn
prompt for `fced0e40` was written with explicit retry instructions.

## `01de80ba` — research/verification — SUCCESS

Asked to verify the load-bearing assumptions behind every lane against **primary sources**, and to
tag each claim VERIFIED / PARTIALLY VERIFIED / UNVERIFIED.

Output: **`PRs/longrun-remote/RESEARCH-FINDINGS.md`** (40KB) with a C1–C11 contradiction table and
a full source index. It is the single most useful artifact produced and should be read before any
new work in this area.

**What it got right, and materially changed:**

| Finding | Effect |
|---|---|
| Apple: **no** API can prevent lid-close sleep, with three primary citations | Confirmed the design's core premise instead of leaving it assumed |
| `caffeinate -s` is **AC-power-only** | Docs corrected; `-i` is what carries battery runs |
| Actions hosted-runner AUP forbids unrelated long-running work | **Killed [lane 06](../lanes/06-github-actions-DROPPED.md)** |
| `/v0/repositories` is rate-limited to **1 req/user/minute** | Exposed a real defect in `longrun doctor` — see [D-5](../decisions/DECISIONS.md) |
| Cursor's "24h max runtime" is unsourced; v0 API is **legacy** | Stopped a false assumption being encoded |
| Empty `/v0/repositories` also matches **two known Cursor defects** | Changed the debug order for that symptom |
| **`claude --cloud` / `--teleport` / Routines exist** | Surfaced [lane 04](../lanes/04-claude-code-cloud.md), the best-fit lane, which had been missed entirely |
| Reproduced the `nohup`+`disown` failure and corrected the **mechanism** | Process-group signal, not SIGHUP; also: **macOS has no `setsid(1)`** |
| Actions overage is **$0.006**/min, not $0.008; larger runners bill differently | Cost model corrected |

**What it got wrong or overstated:**

- **`caffeinate` claim framing.** Listed as contradiction C1 ("assumption was already correct"),
  which is a confirmation, not a contradiction. Minor, but it inflates the apparent error count of
  the prior work.
- **C10, on the spawner comment.** Claimed the code's stated mechanism was wrong. The
  `spawn_detached.py` comment already said *process group*, not SIGHUP, so the code was correct —
  the criticism applied to a paraphrase, not the source. The underlying reproduction is still
  valuable and was worth having.
- **`setsid(1)` portability warning** was correct in general but did not apply: the implementation
  already used Python's `os.setsid()`, not the missing binary.
- **macOS 26 `disablesleep` risk (C11)** rests on one GitHub issue that itself labels the causal
  link inferred. Recorded as a risk, correctly hedged by the agent, but it should not be read as
  established.

**Net:** its headline finding (lane 04) was **correct and initially dismissed** by this session —
see [D-6](../decisions/DECISIONS.md). The lesson is not "trust subagents"; it is "check the claim
the way the claim says to check it."

## `e0407fb7` → re-spawned as `fced0e40` — cloud session manager

Goal: `longrun cloud ls` / `status <id>` / `logs <id>` / `send <id> <msg>` / `stop <id>`. Today
`longrun cloud` dispatches and exits with no way to observe or steer a run.

**Outcome: succeeded, and did better work than the brief asked for.** All five subcommands were
proven against a real agent from separate processes, and it went beyond the task in three ways worth
noting:

- **It proved the offline claim rather than asserting it** — re-ran the 7 new checks with all HTTP
  forced through an unroutable proxy; 7/7 still passed. That is the standard this folder wants.
- **It found the keepalive root cause I had wrong.** I assumed a direct `keepalive off`; the real
  cause was check 172 deleting the owner file. See [D-4a](../decisions/DECISIONS.md).
- **It found the `76 passed` figure was already stale.** Two pre-existing section-14 checks were
  failing on a subtle interaction: `grep -q` exits at the first match, the writer takes SIGPIPE, and
  `set -o pipefail` then fails the check *even though the string was found*. Worth remembering — that
  pattern fails in a way that looks like a genuine assertion failure.

It also declined the obvious implementation and said why: raw `urllib` instead of `cursor_sdk`,
because the SDK spawns a local `cursor-agent` bridge per call and collapses a 429 into an
indistinguishable error string — which would have destroyed the UNKNOWN-vs-empty distinction the
module exists to preserve. That is the reasoning [D-5](../decisions/DECISIONS.md) asks for, applied
without being told.

The re-spawn prompt carries the constraints the first attempt lacked:

- Read `cursor_repos.py` first and **read through its cache** — a 429 must never render as "no
  agents running" or "finished."
- **Persist dispatched agent IDs** to `.longrun/cloud-agents.jsonl`, or `ls` cannot work at all
  after a reboot.
- New `verify_system.sh` checks must be **$0 and network-independent** — the suite runs daily and
  cannot flake when Cursor is down. Test parsing, refusals, cache behaviour; not live API calls.
- Update the hardcoded `76` in `verify_system.sh`, `CLAUDE.md`, and `WORKFLOW-SYSTEM.md`.
- **Do not** run `keepalive off` or bare `stop` — a root watchdog (pid 45238) is holding
  `disablesleep` for Daniel's live session.
- Never write to `scripts/lib/` or `PROCESSES.md`.

## `f7925a60` — Actions lane — failed, and deliberately not resumed

Killed by policy rather than by the network. Reasoning in
[lane 06](../lanes/06-github-actions-DROPPED.md). Resuming it would have built something that
risks account suspension.

## `8dc32620` — VM provisioning — failed, blocked anyway

Would have produced `provision_remote.sh` + `ssh_remote.sh`. Left an untested
`.longrun/provision.rendered.sh` — **a sketch, not a script.** Not resumed: the lane is blocked on
Daniel's spend decision (V-095), and lane 04 may make it unnecessary. See
[lane 07](../lanes/07-always-on-vm-BLOCKED.md).

## Guidance for future subagents in this area

1. **Checkpoint to a file as you go.** Three agents proved that a final-report-only pattern loses
   everything to one DNS blip.
2. **Give them the rate limit up front.** Cursor's API punishes polling; an agent that debugs by
   re-polling `/v0/repositories` will self-inflict failures.
3. **Warn them about the live keepalive.** A `longrun stop` without `--no-keepalive` from a
   subagent would sleep Daniel's machine and kill running work.
4. **Name the guarded surfaces** (`scripts/lib/`, `PROCESSES.md`) — one earlier attempt tripped
   the integrity alarm by putting helpers in `scripts/lib/`.
5. **Demand the verification method, not the conclusion.** `01de80ba` was right and was
   disbelieved because its claim was checked the wrong way.
