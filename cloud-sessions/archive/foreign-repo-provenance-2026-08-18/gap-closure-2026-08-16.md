# Gap closure — 2026-08-16

**Status:** ACTIVE · run on a cloud VM, root, unattended · repo at `1d99660`, branch
`claude/lane-gapclose`. Follows [`OPEN-GAPS.md`](OPEN-GAPS.md)'s ranking. Scope: the five
items marked "closable by an agent right now" in section (a). Nothing in `atlas-os/bin/**`
was touched, per I10.

---

## Summary table

| Gap | Attempted | Outcome | Evidence | Regression check |
|---|---|---|---|---|
| G06 | yes | **CLOSED** | `billing.md`'s `cs doctor` sentence rescoped; `V-037` reproduced (not reproduced today) | [`doc-drift.sh`](../tests/doc-drift.sh) |
| G07 | yes | **CLOSED** | `verified-facts.md`'s "last open item" claim corrected; recurrence risk stated on the `View:` row | [`doc-drift.sh`](../tests/doc-drift.sh) |
| G08 | yes | **CLOSED** | `verified-facts.md:598` no longer conflates "check is broken" with "invariant is I10-gated" | [`doc-drift.sh`](../tests/doc-drift.sh) |
| G09 | yes | **CLOSED** | `verified-facts.md`'s stale "gap worth naming" section cross-references its own later closure | [`doc-drift.sh`](../tests/doc-drift.sh) |
| G11 | yes | **CLOSED** | `D3a`/`D3b` now defeat root with a read-only bind mount instead of skipping | new `D3z` positive control in `cloud-sessions/tests/run.sh` |
| G13 | verified, hardened | **already closed** (found closed on arrival) | `--plan` refusal landed in `a1b05ef`, before `OPEN-GAPS.md` was compiled; register was stale | rewrote 3 loose `has "plan"` checks into 8 checks asserting exit code + exact message + non-dispatch |

Six items attempted (`OPEN-GAPS.md`'s own ranked-list table lists six rows under "(a)" —
G06, G07, G08, G09, G11, G13 — though its prose header says "5"; that off-by-one is itself
now moot since all six are accounted for below). All six closed or reverified. None required
touching `atlas-os/bin/**`.

---

## G11 · `D3a`/`D3b` can never run on a cloud VM, and every cloud VM is root

**What was wrong.** The injection for "a failed `cs env set` must not truncate
`settings.json`" was `chmod 500` on the parent directory. Root ignores mode bits, and every
cloud VM runs as root, so on the one host this defect actually matters most, both checks
silently **skipped** rather than passed — the exact shape this folder's ground rules call
out as worse than a visible failure.

**What changed.** `cloud-sessions/tests/run.sh`'s D3 block now tries a read-only bind mount
first when running as root, falling back to `chmod` only for a non-root operator and to a
genuine `_skip` only if the bind mount itself is unavailable (no `CAP_SYS_ADMIN`). A
read-only mount is enforced by the VFS layer, not by the permission check root is exempt
from:

```
$ mount --bind "$DIR" "$DIR" && mount -o remount,ro,bind "$DIR"
$ python3 -c "import tempfile; tempfile.mkstemp(dir='$DIR')"
FAILED [Errno 30] Read-only file system: '.../tmpXXXXXX'
```

A new check, `D3z/env-set-injection-genuinely-fired`, is a positive control: it attempts a
plain write into the directory *during* the injection and asserts that write itself failed,
so a PASS on `D3a`/`D3b` means a real failure was exercised — not merely that nothing
errored.

**Proof, this run, as root:**

```
$ id -u
0
$ bash cloud-sessions/tests/run.sh 2>&1 | grep -E 'D3|passed|failed|skip'
  ok  D3z/env-set-injection-genuinely-fired
  ok  D3a/env-set-write-failure-preserves-file
  ok  D3b/env-set-reports-write-failure
  ...
342 passed  0 failed  0 xfail(known-open)  4 skipped
```

Skips dropped from 6 to 4 in this same run (the two `D3a`/`D3b` root-skips are gone; the
remaining 4 are the 3 zsh-absent skips plus `gate/ssh-origin-fixture-really-is-ssh`, both
named in this task's own instructions as expected on this VM).

**Also corrected while here:** `verified-facts.md`'s "the two skips on Linux are `D3a`/`D3b`,
correctly skipped because... root" line was a live claim this same fix falsified, so it now
says what actually closed it and points at this report.

**Regression check.** `D3z` in `cloud-sessions/tests/run.sh`, part of the existing four
required suites — no separate suite needed.

---

## G13 · `--plan` silently dropped unless the destination is cloud

**What the register said.** `OPEN-GAPS.md` describes this as open, "held by three xfails,"
needing a founder-free fix in `bin/cs` that either refuses or honours the flag.

**What was actually found.** The fix already exists. `cmd_new` (`cloud-sessions/bin/cs:798`)
refuses `--plan` outright for any non-cloud destination:

```
798	  # `--plan` only means anything to a cloud session: it names a committed file the VM will
799	  # read. For `here` and `rc` it was accepted, never validated, and silently dropped - you
800	  # would pass a plan and watch a session start without it. Refuse instead. Ground rule 4.
801	  if [[ -n "$plan" && "$where" != "cloud" ]]; then
802	    die "--plan applies only to a cloud session; '$where' would ignore it.
803	       Drop --plan, or choose cloud."
804	  fi
```

```
$ git log -1 -S"applies only to a cloud session" --oneline -- cloud-sessions/bin/cs
a1b05ef Add cs sessions; enrich the dispatch ledger; refuse a dropped --plan
$ git log -1 --format=%cI a1b05ef
2026-08-15T18:11:33-07:00
$ git log -1 --format=%cI cloud-sessions/reference/OPEN-GAPS.md
2026-08-15T18:18:54-07:00
```

`a1b05ef` landed 7 minutes before the register that calls this "open" was compiled — a lane
timing gap, not a code gap. Confirmed live: `0 xfail(known-open)` in every run this session.

**What was still actually wrong, and fixed here.** The three regression checks
(`new/plan-with-here-is-silently-dropped`, `new/plan-with-rc-is-silently-dropped`,
`new/plan-with-here-is-not-even-validated`) still used a plain `has(...,"plan")` substring
check and comments describing the *old, fixed* defect. That is a weak assertion — it would
pass whether the flag were refused-and-named or silently accepted-and-mentioned somewhere —
and their names actively mis-describe current behaviour. Replaced with 8 checks per
destination that assert the real contract: non-zero exit, the exact refusal message, and
(for the `--here` case) that `claude` was never invoked at all:

```
$ bash cloud-sessions/tests/run.sh 2>&1 | grep -E 'plan-with|plan-reaches'
  ok  new/plan-reaches-a-cloud-session
  ok  new/plan-with-here-is-refused
  ok  new/plan-with-here-names-the-destination
  ok  new/plan-with-here-never-dispatched
  ok  new/plan-with-rc-is-refused
  ok  new/plan-with-rc-names-the-destination
  ok  new/plan-with-here-refused-before-file-is-checked
  ok  new/plan-with-here-refused-before-file-is-checked-names-it
346 passed  0 failed  0 xfail(known-open)  4 skipped
```

**Regression check.** The 8 checks above, in `cloud-sessions/tests/run.sh`.

---

## G06 · `billing.md` proves "no API key can bill you" with a probe that cannot see the key

**What was already fixed** (by an earlier lane, commit `2379c4d`, before this session
started): the table now shows both the environment *and* the `~/.zshrc` probe for every key,
and names `_get_env_or_zshrc`. `OPEN-GAPS.md`'s account of this gap was itself already
partly stale.

**What was still wrong, and fixed here.** Two things `OPEN-GAPS.md`'s own "how to verify it
closed" named and the earlier fix missed:

1. `billing.md:65` still said *"`cs doctor` fails loudly if any of those variables
   appear"* — unscoped, where "those variables" spans a five-item list including
   `OPENAI_API_KEY` and `DEEPGRAM_API_KEY`. Checked what `cs doctor` actually reads:

   ```
   $ sed -n '243p' cloud-sessions/bin/cs
   for v in ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX; do
   ```

   `cs doctor` checks five env vars — not `OPENAI_API_KEY`, not `DEEPGRAM_API_KEY` — and
   reads only the environment, never `~/.zshrc`. The sentence overclaimed on two axes at
   once. Rewritten to name exactly what is and isn't covered.

2. `atlas-os/queue/QUEUE.md:464` (`V-037`) claims `DEEPGRAM_API_KEY` is set. Re-ran the
   named probe:

   ```
   $ printf '%-24s env=%s  zshrc=%s\n' "DEEPGRAM_API_KEY" \
       "$([ -n "${DEEPGRAM_API_KEY:-}" ] && echo SET || echo unset)" \
       "$(grep -qE '^\s*export\s+DEEPGRAM_API_KEY=' ~/.zshrc 2>/dev/null && echo PRESENT || echo absent)"
   DEEPGRAM_API_KEY         env=unset  zshrc=absent
   ```

   **Not reproduced.** Documented in `billing.md` as such — this does not withdraw `V-037`
   (only `queue.py` may edit the queue, and the underlying question about `STOP` gating
   `record.py` is still open), it only corrects the evidence line for the date it was
   checked.

**Regression check.** `doc-drift.sh` (new, see below) asserts the scoped sentence and the
`V-037` note stay in place.

---

## G07 · The not-verified table contradicts the body of its own file

**What was wrong.** `verified-facts.md:770` said Probe 7 *"closed the **last** open item in
this file's own 'not verified' table"* while that table, 60 lines later, still carried
(at the time) eight rows, most `UNVERIFIED` — including the very `View:`-line row the
surrounding prose was implicitly about.

**What changed.**
- The sentence at `:770` no longer claims to have closed "the last" item; it names the one
  row Probe 7 actually closed (whether a cloud session can open a pull request) and says
  outright that the earlier wording was wrong.
- The `View:`-line row in the not-verified table now states plainly that it *was* proven
  once, on 2026-08-14 (cross-referenced to the section above that proves it), and that the
  row's real content is a **standing recurrence risk** — the regression suite stubs that
  output shape forever, so nothing keeps the 2026-08-14 proof current. That distinction
  (G07 vs. the separate standing-risk gap named G17 in `OPEN-GAPS.md`) is now stated inline
  rather than left for a reader to infer.

**Proof.**

```
$ grep -c "closed the last open item" cloud-sessions/reference/verified-facts.md
0
$ grep -n "one specific row" cloud-sessions/reference/verified-facts.md
801:Probe 7 actually closed was **one specific row**: whether a cloud session can open a pull
```

**Regression check.** `doc-drift.sh`.

---

## G08 · The `append-only` failure is described as an I10 artifact; it is a bug in the check

**What was wrong.** `verified-facts.md:598` called the one `verify_system.sh` failure
*"correctly left alone as I10 human-only"* — framing that says the check itself is fine and
only its fix is gated.

**What changed.** The table cell now states only the bare fact (the failure is the known
`append-only:...` check); a new paragraph right after it names `V-013`, quotes the probe
evidence (`wc -c` on a directory), and explicitly separates *"the check is broken"* from
*"the invariant is violated"* — the latter is unproven, not merely gated, because a
permanently-red check for a known-bad reason can never go red for a real violation.

**Proof.**

```
$ grep -c "correctly left alone as I10 human-only" cloud-sessions/reference/verified-facts.md
0
$ grep -n "V-013" cloud-sessions/reference/verified-facts.md | head -1
616:subject since it was written. Filed as `V-013`. **The check is broken; that is a distinct
```

**Regression check.** `doc-drift.sh`.

---

## G09 · The proof of `cs start`'s record path was deleted from the ledger after it was taken

**What was wrong.** `verified-facts.md:414`'s *"gap worth naming"* section states all nine
ledger rows are `mode: tracked`, framed as still-open. A later section in the same file
(`:777` area) shows the gap was closed on 2026-08-14 by a real dispatch — whose one
non-`tracked` ledger row was then deliberately removed with `cs rm`. The ledger today reads
identically to the state that motivated the "gap worth naming" section, so a reader relying
on the ledger, or stopping at the top of the file, cannot tell the gap was ever closed.

**What changed.** Added a paragraph to the "gap worth naming" section stating plainly that
the gap was closed on 2026-08-14, that the closing proof's ledger row was removed by design
("a scratch repo is not worth remembering" — a deliberate choice, not an oversight), and
that the quoted terminal output in the later section is the entire surviving record.

**Proof, re-run today:**

```
$ python3 -c "
import json
modes=[]
with open('cloud-sessions/state/sessions.jsonl') as f:
    for line in f: modes.append(json.loads(line).get('mode'))
print(modes)"
['tracked', 'tracked', 'tracked', 'tracked', 'tracked', 'tracked', 'tracked', 'tracked', 'tracked', 'tracked']
```

All ten rows (nine at the time `OPEN-GAPS.md` was written, ten now) are still `tracked` —
confirming the ledger alone would mislead a reader exactly as G09 describes, which is why
the cross-reference belongs in the doc rather than left implicit.

**Regression check.** `doc-drift.sh`.

---

## New regression check: `cloud-sessions/tests/doc-drift.sh`

G06/G07/G08/G09 are wording fixes to reference docs — there is no application behaviour for
the four required suites to exercise. Added a small standalone script that greps for the
specific stale sentences these four gaps disprove, and for the corrected replacements,
failing loudly if either regresses:

```
$ bash cloud-sessions/tests/doc-drift.sh
doc-drift: cloud-sessions/reference wording regressions

ok    G06/billing-doctor-sentence-not-overclaimed
ok    G06/billing-doctor-sentence-scoped
ok    G06/billing-names-V-037
ok    G07/last-open-item-not-overclaimed
ok    G07/last-open-item-names-the-row
ok    G08/append-only-not-conflated-with-I10
ok    G08/append-only-names-V-013
ok    G09/ledger-gap-cross-referenced

green.
```

Verified it has teeth (negative control, not committed): piping the corrected billing.md
sentence through a substitution that reverts it to the stale wording made the
`G06/billing-doctor-sentence-scoped` check fail, confirming the check does not just verify
its own inputs vacuously. Not wired into the four mandatory suites — it is a doc-consistency
guard, not a `cs`/heartbeat/worktree/process behaviour check, and this task's instructions
name exactly those four as the acceptance bar. Anyone can run it directly, or fold it into
CI as a fifth check later; that is a five-minute addition, not a founder decision, and is
left to whoever wires this repo's CI.

---

## Required-suite tallies, this run

```
$ bash cloud-sessions/tests/run.sh 2>&1 | tail -3
346 passed  0 failed  0 xfail(known-open)  4 skipped
4 skipped check(s) — a skipped check is not a passed check.
green.  No regression in the ten reviewed defects.

$ bash atlas-os/tests/process/run.sh 2>&1 | tail -3
18 passed  0 failed  0 skipped
green.  The lifecycle contract holds: enabled decides, unknown is not safe.

$ bash atlas-os/tests/heartbeat/run.sh 2>&1 | tail -3
90 passed  0 failed  0 skipped  5 defect(s) recorded
green.

$ bash atlas-os/tests/worktree/run_tests.sh 2>&1 | tail -3
53 passed  0 failed  0 skipped
green.
```

`cloud-sessions/tests/run.sh` shows **4** skips, not the 5 the task brief anticipated for
"the cs suite" (2 root-related + 3 zsh-absent) — because this run fixed the 2 root-related
skips (G11). The 3 zsh-absent skips and the 1 `gate/ssh-origin-fixture-really-is-ssh` skip
(this environment rewrites GitHub URLs) remain and are exactly as expected. `0 failed` on
all four suites both before and after every change in this report — no regression was
introduced at any point; each fix was verified green before moving to the next.

The heartbeat suite's `5 defect(s) recorded` is `atlas-os/bin/heartbeat.py`'s **existing**,
unmodified state (G01/G03, human-only under I10) — untouched by this session, quoted here
only so the tally is legible against the task's own "green before, green after" bar.

---

## What was not attempted, and why

- **G01–G05, G10, G12, G14, G15** (`OPEN-GAPS.md` section (b)): every one sits behind
  `atlas-os/bin/**` or a founder decision. Not touched, not routed around. `V-013` (G08's
  underlying defect) and the `--plan` fix's own file (`bin/cs`, already fixed) are named for
  context only, never opened for writing in the guarded case.
- **G16–G23** (section (c)): genuine unmeasured unknowns requiring a phone, a week of real
  shell use, or a dispatch this task's constraints forbid (`cs start`/`handoff`/etc. are
  disallowed here). Left as-is.
- **Filing to the queue.** `V-037`'s reconciliation, and the `V-030`/`V-034` STOP
  contradiction G04 raises, both warrant a founder queue item. `QUEUE.md` is written only by
  `atlas-os/bin/queue.py`; nothing here attempts it.
- **`OPEN-GAPS.md` itself.** Not edited. It is dated 2026-08-15, "this Mac," and several of
  its own claims (the G13 "still open" call, the "5" vs. six-row count in section (a)) are
  now stale per this report. Correcting the register is a natural next step but was not
  explicitly in scope for this task, which named the five gaps by number rather than the
  file that lists them; left for a human or a future lane to reconcile against this report.

## Commits on `claude/lane-gapclose`

Pushed incrementally, not batched at the end:

1. `ff10fe7` — G11 fix + G13 test hardening (`cloud-sessions/tests/run.sh`)
2. (this commit) — G06/G07/G08/G09 doc fixes, `doc-drift.sh`, this report
