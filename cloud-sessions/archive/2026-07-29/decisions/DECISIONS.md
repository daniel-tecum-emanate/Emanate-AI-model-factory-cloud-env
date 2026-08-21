# DECISIONS

Locked calls, why, and what would reverse each one. Add to this file rather than relitigating.

---

## D-1 — `pmset disablesleep` is the mechanism for lid close, and it must self-revert

**Decided 2026-07-29.** No user-space power assertion can survive a lid close — Apple states this
in three primary sources. `pmset -a disablesleep 1` is the only public lever for the
no-external-display case.

Because the flag is **global and persists across reboots until cleared**, the dangerous failure is
not "failed to set" but "set and never cleared" — a MacBook that never sleeps and cooks itself in a
bag. So the watchdog **runs as root specifically so it can revert without re-prompting**, reverts
on five separate conditions including an EXIT trap, and **reads the flag back** rather than assuming
the write worked.

**Reverses if:** Apple ships a supported closed-lid API without an external display, or macOS stops
honouring `disablesleep` (the read-back will report `WILL SLEEP`, loudly).

## D-2 — Detach with double-fork + `os.setsid()` in Python, never `nohup … & disown`

**Decided 2026-07-29, after a real failure.** `caffeinate` launched with `nohup`+`disown` **died
with its launching shell**, which would have left `keepalive` reporting an assertion nothing was
holding — a silent false green.

The mechanism is **process-group signal delivery, not SIGHUP**. `nohup` only sets SIGHUP to
`SIG_IGN`; `disown` only edits the shell's job table; neither changes the process group.
Reproduction in [`PROBLEM.md`](../PROBLEM.md).

Python's `os.setsid()`, not `setsid(1)` — **macOS ships no `setsid` binary**, so a shell-out would
work on Linux and silently fail here.

**Reverses if:** the local lane moves to `launchd`, which is the idiomatic macOS answer and adds
crash recovery that `disablesleep` currently lacks. Recommended by the research agent; not done.

## D-3 — `longrun` helpers live in `scripts/longrun/`, never `scripts/lib/`

**Decided 2026-07-29, after tripping the integrity alarm.** `scripts/lib/` and `PROCESSES.md` are
integrity-guarded surfaces. Helpers were initially placed in `scripts/lib/` and set off the alarm.
Moved rather than rebaselined the guard — rebaselining to accommodate new files defeats the point
of having it.

Also: `longrun` is **operator-invoked** with no launchd label and no schedule, so it needs no
`PROCESSES.md` entry. The `no launchd label (operator-only)` check enforces that it cannot quietly
become a background job without registration.

## D-4 — Keepalive has an owner, and `stop` only releases what it owns

**Decided 2026-07-29, after finding a live footgun.** `verify_system.sh` starts and stops a
throwaway `longrun` session daily. `stop` called `keepalive off`, so **the daily verify run would
silently disable Daniel's lid-close protection** — turning a passing test suite into the cause of a
sleeping machine.

Fixed with an ownership file (`.longrun/keepalive.owner`): `stop` auto-releases only
session-owned keepalives, never operator-initiated ones. `start --no-keepalive` exists for the
suite. Regression test: `stop spares operator keepalive`.

**Verified:** the watchdog kept the **same pid** across a full suite run.

### D-4a — amendment, same evening: the first fix was not enough

**This footgun fired again at 19:57 the same day**, and the "verified" line above is exactly why it
was missed — the pid survived one suite run, so the fix looked complete.

The second route: section 14's cleanup **deletes `.longrun/keepalive.owner`**, and an absent owner
file fell through to the *release* branch. So every later `stop` read "nobody owns this" and
released a **root watchdog it had never started** — killing a keepalive Daniel had authorised by
Touch ID. With `caffeinate` gone the Mac then idle-slept under a running verify suite, stretching it
from 125 seconds to **75 minutes**.

Now fixed: release only on a **positive** `owner == session` claim. **Unknown provenance means
leave it alone.** The asymmetry is the point — a stray assertion costs some battery; a wrong release
sleeps the machine and kills whatever was running on it.

**The lesson worth carrying:** D-4 guarded the *decision* ("is this owned?") but not the *input*
("what if the ownership record is missing?"). A deleted record was read as permission. Any
ownership or lock check needs a defined answer for "the record is absent", and that answer should be
refusal. Found by the session-manager subagent, which diagnosed it correctly after I had assumed a
different cause (a direct `keepalive off` call).

**Generalised, and pending as a KG entity** (`Lesson_OwnershipMarkerDeletion`, `type:lesson`
`severity:critical` `area:git`): *a cleanup step that deletes an ownership marker silently converts
a safe conditional into an unconditional teardown.* Two compounding details make it worth a lesson
rather than a footnote:

1. **The verify suite destroyed the state it was verifying.** Check 172 deletes
   `.longrun/keepalive.owner` as cleanup; every later `stop` then read a Touch-ID-authorised root
   watchdog as unowned. The suite is now required to **snapshot and restore** that file instead.
2. **The guard comment was wrong about its own mechanism.** It claimed `--no-keepalive` prevented
   this. It did not — the protection people believed in was not the protection that existed, which
   is why the bug survived a fix and a green suite.

Both halves are fixed. Recorded here because this Cursor session has no knowledge-graph tools
(`mcp__knowledgegraph__*` is Claude Code only) and `memory/` is the reflection agent's to write —
so a Claude Code session should create the entity. Tracked in
[`../STATUS.md`](../STATUS.md) next actions.

## D-5 — Every Cursor repo-list read goes through the cache; a 429 is UNKNOWN, never empty

**Decided 2026-07-29, fixing a defect introduced earlier the same session.**
`GET /v0/repositories` allows ~**1 request/user/minute**. `longrun doctor` called it on every
invocation, and the dispatch refusal keys off an **empty list** — so a rate-limited response could
render as "no repositories connected", the **opposite** diagnosis, sending you to the dashboard to
fix nothing.

`scripts/longrun/cursor_repos.py` is now the only reader: 900s TTL cache, and **429 → UNKNOWN,
never `[]`**. Any new subcommand must use it.

**Generalises to:** never let a rate-limit or an unreachable dependency collapse into a
*meaningful* value. UNKNOWN must stay distinguishable from empty, absent, or finished.

## D-6 — Verify a claim the way the claim says to verify it

**Decided 2026-07-29, after getting this wrong.** A research agent reported `claude --cloud`
exists. It was declared non-existent on the basis of `claude --help | grep cloud` returning
nothing — but **the research doc had already stated the flags are hidden from `--help`**. The test
chosen was the one the source predicted would fail.

The follow-up probe was also invalid: `claude --cloud --version` exits 0, and so does
`claude --definitelynotaflag --version`, because `--version` short-circuits option validation. **A
probe that the control also passes is not a probe.**

What settled it: `strings` on the installed binary — `teleport` 191 hits, `web-setup` 9,
`CCR_FORCE_BUNDLE` 5. The lane is real, and it is now the **best-fit** lane
([lane 04](../lanes/04-claude-code-cloud.md)).

Corrections: **V-102** supersedes the wrong claim in **V-101**.

**Two habits from this:** (1) absence from `--help` is not absence of a feature; (2) always run a
negative control before believing a probe.

## D-7 — GitHub Actions dropped on policy, not capability

**Decided 2026-07-29.** Hosted-runner terms forbid "any other activity unrelated to the
production, testing, deployment, or publication of the software project associated with the
repository," with a penalty ladder ending at **account termination**. A general-purpose agent host
is squarely inside that clause; an agent working on *its own* repo is not.

Cost also failed independently: ~**$259/mo** for four 6-hour jobs a day, more than a dedicated box
for a worse experience.

**Still fine:** `anthropics/claude-code-action@v1` for repo-scoped automation. **Also fine:** a
**self-hosted** runner — explicitly carved out of that clause, free, and it raises the per-job
ceiling from 6 hours to 5 days. That is a variant of [lane 07](../lanes/07-always-on-vm-BLOCKED.md).

## D-8 — Do not provision paid infrastructure; file it and wait

**Standing, per invariants I5/I9.** Recurring spend is Daniel's decision. An untested provisioning
script is the exact false green this repo keeps writing lessons about, so the VM lane was **not**
built — it was filed as **V-095** with costed options.

Reinforced by ordering: **test lane 04 first.** It is free and may make the box unnecessary. Do not
spend before that test.

## D-9 — Corrections are appended, never edited in place

**Standing, and structural.** `validation/QUEUE.md` has exactly one write path
(`scripts/lib/validate_core.py`) and no amend operation, and its `add()` **rejects items with no
evidence pointer into ground truth** — which it did, correctly, on the first attempt at V-101 when
the evidence was pasted command output rather than a file path.

So a wrong queue item is superseded by a new one (V-095 → V-101 → V-102), and deciding is Daniel's,
not an agent's. Same convention applies in this folder: mark and supersede, never delete. The
record of how a wrong belief was formed is the part worth keeping.

**Corollary:** `PRs/longrun-remote/FACT-CHECK-2026-07-29.md` **must not move into this folder** —
V-101 cites it as an evidence pointer, and relocating it breaks that pointer silently.
