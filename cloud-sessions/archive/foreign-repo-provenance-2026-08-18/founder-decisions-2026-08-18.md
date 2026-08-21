# Founder decision sheet — 2026-08-18

**Status:** ACTIVE · compiled 2026-08-17 on this Mac · repo at `80fcf8c` · macOS 26.5.1 arm64.
**Purpose:** every open item that needs Daniel and nobody else, in one place, in the order
worth clearing them. Assembled from [`OPEN-GAPS.md`](OPEN-GAPS.md) section (b),
[`../../atlas-os/queue/QUEUE.md`](../../atlas-os/queue/QUEUE.md), and the code itself.

Claims are tagged per [`../AGENT.md`](../AGENT.md) ground rule 1: `VERIFIED` (run here,
output quoted) · `DOCS` · `UNVERIFIED` · `DISPROVEN`. **Every `file:line` on this sheet was
re-read today**, because several line numbers in `OPEN-GAPS.md` have since moved and one of
its central factual claims has been overtaken by a commit — see F5.

> **This sheet is a view, not an authority.** Founder decisions live in `QUEUE.md`, written
> only by `atlas-os/bin/queue.py`. Nothing here was filed to the queue; this lane owns one
> file. Where an item has a `V-` number, decide it with `queue.py decide`, not by editing
> anything.

**I10-guarded paths** — `atlas-os/reference/INVARIANTS.md`, `atlas-os/bin/**`,
`.claude/settings.json`, `atlas-os/telemetry/SCHEMA.md`
([`INVARIANTS.md:170-171`](../../atlas-os/reference/INVARIANTS.md)) — are human-only. Items
below marked **I10** can only be applied by you. The exact command is given for each.

---

## The sheet at a glance

| # | Decision | Group | Blocks |
|---|---|---|---|
| F1 | Apply the staged heartbeat patch | 1 · five minutes | concurrent lanes, `/cloud all` |
| F2 | Correct `atlas-os/README.md`'s false directory-`STOP` warning | 1 · five minutes | F5 (it corrupts the input to F5) |
| F3 | Repoint the nine stale deny rules (`V-010`, first half only) | 1 · five minutes | nothing |
| F4 | File the `mcp_connections` finding under its own number | 1 · five minutes | nothing |
| **F5** | **The `STOP` knot** — `V-019` + `V-025` + `V-030` + `V-033` + `V-034` + G04 | 2 · real decision | every spend claim in the repo |
| F6 | `refuses()` credits any non-zero exit — `V-035` | 2 · real decision | trust in `verify_system.sh` |
| F7 | Is I10 a lock or an intent? — `V-023`, `V-026`, `V-031` | 2 · real decision | the word "blocked" on this whole sheet |
| F8 | The append-only check `wc -c`s a directory — `V-013` | 2 · real decision | a green `verify_system.sh` |
| F9 | Heartbeat liveness oracle keys on the wrong id — `V-021` | 2 · real decision | cloud lane visibility |
| F10 | Telemetry `run_id` coerced to string | 2 · real decision | F9's oracle |
| F11 | `DECISION.md` reversal trigger fires on the wrong event — `V-020` | 2 · real decision | retiring Supabase |
| F12 | `mcp_connections: []` does not mean "none" | 3 · Anthropic | connector hygiene |
| F13 | `allowed_tools` is an auto-approval list, not a sandbox | 3 · Anthropic | cloud safety design |
| F14 | A fresh VM's `origin/main` is days stale | 3 · Anthropic | cloud session self-orientation |
| F15 | No secrets store, no telemetry persistence in cloud VMs | 3 · Anthropic | credentialed cloud work |
| F16 | Interactive terminal attach is gated off for this account | 3 · Anthropic | nothing |

**Counts: group 1 — 4 · group 2 — 7 · group 3 — 5.**

---

# Group 1 — five minutes and it is done

Ordered by what it costs to leave open. These are not judgement calls; they are mechanical,
and the only reason they are on your desk is that three of the four touch I10-guarded paths.

---

## F1 · Apply the staged heartbeat patch — **I10**

**The question.** Do you apply `heartbeat-defects.patch`, which is written, tested and
sitting in the repo unapplied, closing six defects in the gate that stops concurrent agents
from overwriting each other?

**What is true today — `VERIFIED` here, 2026-08-17, three independent ways:**

```
$ git apply --check atlas-os/tests/heartbeat/fixes/heartbeat-defects.patch
forward-apply rc=0                 # applies cleanly, therefore NOT yet applied

$ git apply --reverse --check atlas-os/tests/heartbeat/fixes/heartbeat-defects.patch
error: patch failed: atlas-os/bin/heartbeat.py:58
error: atlas-os/bin/heartbeat.py: patch does not apply

$ grep -c 'row_claims_for_gate' atlas-os/bin/heartbeat.py
0                                  # the patch introduces this symbol
```

The patch's own harness agrees —
[`fixes/RESULTS-fixes.md:8`](../../atlas-os/tests/heartbeat/fixes/RESULTS-fixes.md):
*"looks UNPATCHED (row_claims_for_gate absent)"*, and `:139`: `2 passed  7 failed  0 skipped`.

The six defects are recorded in
[`atlas-os/tests/heartbeat/RESULTS.md:612-622`](../../atlas-os/tests/heartbeat/RESULTS.md)
under the newest run block, **every one of them under a check the suite marks PASS** — the
suite asserts what the tool does, and what it does is wrong. Named, not numbered, because
**the numbering is not stable**: the block is regenerated per run and the previous block
recorded five (see the disclosure at the end of this sheet).

| Defect | What it does |
|---|---|
| case-sensitive claims | Claim matching is case-sensitive, the filesystem is not — two lanes granted the same file: *"I3 is violated with the gate reporting success"* |
| space in a path | A path containing a space is silently split into two unrelated claims — *"the file the operator meant is protected by NEITHER"* |
| reused session id | A session id reused on another board makes each lane invisible to the other — *"the gate prints success and I3 is broken"* |
| **FREEZE not enforced** | `cmd_register` reads `board.mode` only to flip IDLE→DISPATCH |
| unparseable claim | An unparseable claim on a live row contributes **zero** claims — `register` hands the path away and prints success |
| reviving a dead row | `update --status ACTIVE` re-arms a claim without re-running the overlap gate |

The FREEZE defect confirmed independently by reading the live script: the only `board.mode`
reference in the register path is
[`heartbeat.py:990`](../../atlas-os/bin/heartbeat.py) —
`if board.mode == "IDLE": set_mode(board, "DISPATCH")`. `cmd_register` begins at `:935`.
**Nothing between them tests for FREEZE.** `VERIFIED`.

**The patch does cover FREEZE**, contrary to `OPEN-GAPS.md` G03's framing that it is a
separate policy call — `heartbeat-defects.patch:149-155` adds
`if board.mode == "FREEZE": print("REFUSED: …")`. So applying it *is* the policy decision,
and that is the one judgement inside this otherwise-mechanical item: **after the patch,
registering a lane on a frozen board fails instead of succeeding silently.** That is what
freeze is supposed to mean.

**What it costs to leave open.** Five of the six make the coordination gate hand one path to
two lanes while printing success. This is the exact mechanism the cloud workstream uses to
run concurrent agents — and lanes are running concurrently in this repo right now. Defect 2
is worse than a no-op: the lane ends up holding two paths it never asked for, one of which
is a subtree claim that will refuse unrelated lanes. Defect 3 means an operator who freezes
the board during an incident gets a board reading FREEZE, a registration exiting 0, and no
indication the two disagree.

**Options.**
- **(a) Apply it. — recommended.** It is written, it has a regression harness, it closes six
  defects including the incident brake, and every hour it sits unapplied is an hour of
  concurrent lanes trusting a gate that does not gate.
- (b) Apply it but revert the FREEZE hunk, if you want freeze to stay advisory. No reason to.
- (c) Leave it. Only defensible if you intend to stop running concurrent lanes.

**How to apply — you, because `atlas-os/bin/**` is I10:**

```bash
cd /Users/danieltecum/internal-company-tool
git apply atlas-os/tests/heartbeat/fixes/heartbeat-defects.patch
bash atlas-os/tests/heartbeat/fixes/run-fixes.sh     # expect 9/9, currently 2 passed 7 failed
bash atlas-os/tests/heartbeat/run.sh                 # expect 90 passed, defect count drops
```

**Not closed by this patch — the seventh defect.** An **empty** `owns_paths` cell on a live
row still protects nothing, before and after
([`fixes/RESULTS-fixes.md:133`](../../atlas-os/tests/heartbeat/fixes/RESULTS-fixes.md), and
its check `08.8` is deliberately recorded as PASS-as-observation). Applying the patch does
not finish the job; it finishes six sevenths of it.

**Blocked behind this:** `/cloud all`'s claim-gating, `cs exodus`'s board partition, and
`DISPATCH.md`'s whole concurrency story rest on this gate being real.

---

## F2 · Correct `atlas-os/README.md`'s directory-`STOP` warning — not I10

**The question.** Do you strike one false sentence in the spend-safety README that tells a
reader the mandated `STOP` form is the dangerous one?

**What is true today.** [`atlas-os/README.md:164-165`](../../atlas-os/README.md), inside the
`Corrected 2026-08-13` block at `:160-166`, says:

> On a read-only mount, a permissions failure, or a `STOP` that is a directory, the suite
> makes a live model call.

**The directory clause is wrong.** `DISPROVEN`, sandbox-reproduced here 2026-08-17 against
the real idiom at [`verify_system.sh:139-145`](../../atlas-os/bin/verify_system.sh)
(`touch STOP` … run real `nightly.sh` … `rm -f STOP`) and the real gate at
[`nightly.sh:101`](../../atlas-os/bin/nightly.sh) (`if [ -e "$STOP_FILE" ]`):

```
CASE A: STOP is a directory (the current repo state, the mandated form)
  touch rc=0
  SKIPPED — STOP sentinel present          <- no model call
  rm -f rc=1 (dir survives: yes)

CASE B: STOP absent, touch succeeds
  touch rc=0
  SKIPPED — STOP sentinel present          <- no model call

CASE C: STOP absent, touch FAILS (read-only parent)
  touch rc=1
  *** LIVE MODEL CALL WOULD HAPPEN HERE ***
```

`touch` on a directory succeeds (rc 0) and `[ -e ]` is true for a directory, so the
directory form **halts** `nightly.sh` — it is the *safest* of the three. The genuine
billable path is Case C alone: `touch` failing while `STOP` is absent. The README lists the
safe case among the dangerous ones.

**What it costs to leave open.** It corrupts the input to F5. A founder reading that line
today learns that the directory sentinel causes billing, and reasonably concludes the
directory form should be reverted — which is precisely the reversion the root
[`AGENT.md`](../../AGENT.md) STOP block exists to prevent, and which has already happened
three times in good faith.

**Options.**
- **(a) Strike the directory clause, keep the read-only/permissions clause, and add the real
  Case C condition. — recommended.** The warning is correct and valuable minus five words.
- (b) Leave it and rely on this sheet. Fails the moment anyone reads the README instead.

**How to apply.** `atlas-os/README.md` is **not** I10-guarded, so any lane can do it — but it
is on your sheet because it is spend-safety text and it feeds F5. The edit: on line `164`,
delete `, or a \`STOP\` that is a directory`, and append a sentence naming the real condition
— *"the billable path is `touch` failing while `STOP` is absent; a directory `STOP` halts
`nightly.sh` via `[ -e ]` and is the safe form."*

**Blocked behind this:** F5 — decide F2 first or you are deciding F5 on wrong information.

---

## F3 · Repoint the nine stale deny rules — **I10**, `V-010` first half only

**The question.** Do you repoint nine deny rules from `atlas-os/INVARIANTS.md` (a path that
has not existed since the 2026-08-13 reorganisation) to `atlas-os/reference/INVARIANTS.md`?

**What is true today — `VERIFIED` here, 2026-08-17:**

```
$ grep -c 'atlas-os/INVARIANTS.md' .claude/settings.json
9
$ grep -n 'atlas-os/INVARIANTS.md' .claude/settings.json | head -3
73:      "Write(/atlas-os/INVARIANTS.md)",
74:      "Edit(/atlas-os/INVARIANTS.md)",
75:      "MultiEdit(/atlas-os/INVARIANTS.md)",
```

Nine rules match nothing. [`INVARIANTS.md:48-52`](../../atlas-os/reference/INVARIANTS.md)
states the consequence itself: *"the file stating the laws is itself unguarded."* The
invariant's own status line, [`INVARIANTS.md:168`](../../atlas-os/reference/INVARIANTS.md),
reads `ENFORCED mechanism, INCOMPLETE coverage (V-010)`.

**What it costs to leave open.** The one file that defines what agents may not touch is
writable by any agent, while `INVARIANTS.md:54-55` instructs readers not to describe I10 as
fully enforced until `V-010` closes — so the gap is documented, unfixed, and load-bearing on
the wording of every other item on this sheet.

**Options.**
- **(a) Repoint the nine rules now; leave `guard.py` unwired. — recommended.** `V-010`
  bundles two things. The repoint is a path substitution with no behavioural risk. Wiring
  `guard.py` is not: `V-010`'s own evidence says a sweep of 167 real historical Bash calls
  from this repo shows it would block 36 of them, **21.6%**, and *"a guard that refuses one
  legitimate command in five gets switched off."* Do the safe half, decide the other half
  separately.
- (b) Do both. Not recommended — see the 21.6% figure.
- (c) Neither.

**How to apply — you, because `.claude/settings.json` is I10 and deny-listed:**

> **Corrected 2026-08-18.** The block first published here asserted on
> `"/atlas-os/INVARIANTS.md"` — the *leading-slash* form, which appears **3** times, not 9.
> Pasted as written it aborted on the assert, and forcing it past would have repointed 3
> rules and silently left 6 dead while the queue note recorded the job as done. The nine
> rules use two spellings: 3 with a leading slash, 6 without. Replacing the **no-slash
> substring** catches both, because every slash form contains it.

```bash
cd /Users/danieltecum/internal-company-tool
cp .claude/settings.json /tmp/settings.json.bak      # this file is the trust boundary
python3 - <<'PY'
import pathlib
p = pathlib.Path(".claude/settings.json")
s = p.read_text()
# The no-slash substring matches both spellings: "Write(/atlas-os/INVARIANTS.md)" contains
# "atlas-os/INVARIANTS.md" too. Expect 9 across both forms.
n = s.count("atlas-os/INVARIANTS.md")
assert n == 9, f"expected 9 dead rules, found {n} - stop and re-read before editing"
p.write_text(s.replace("atlas-os/INVARIANTS.md", "atlas-os/reference/INVARIANTS.md"))
PY
python3 -c "import json;json.load(open('.claude/settings.json'));print('valid json')"
grep -c 'atlas-os/reference/INVARIANTS.md' .claude/settings.json   # expect 9
```

**One rule is missing entirely, and repointing will not create it.** `MultiEdit` is denied
only in the leading-slash spelling, so after this fix the rule set still has
`MultiEdit(/atlas-os/reference/INVARIANTS.md)` but no no-slash twin, while `Write` and
`Edit` have both. Add it in the same sitting:

```bash
python3 - <<'PY'
import json, pathlib
p = pathlib.Path(".claude/settings.json")
d = json.loads(p.read_text())
deny = d["permissions"]["deny"]
want = "MultiEdit(atlas-os/reference/INVARIANTS.md)"
if want not in deny:
    deny.insert(deny.index("Edit(atlas-os/reference/INVARIANTS.md)") + 1, want)
    p.write_text(json.dumps(d, indent=2) + "\n")
    print("added", want)
else:
    print("already present")
PY
```

Restore with `cp /tmp/settings.json.bak .claude/settings.json` if anything looks wrong —
a malformed settings file disables every deny rule at once.

Then record the half-decision so the queue does not read as fully closed:

```bash
python3 atlas-os/bin/queue.py decide V-010 defer --days 30 --by Daniel \
  --note "Nine deny rules repointed to atlas-os/reference/INVARIANTS.md 2026-08-18. The guard.py half is NOT done and must not be: 36 of 167 real Bash calls (21.6%) would be blocked. Deferred as its own question."
```

**Caveat, stated plainly.** Deny rules re-arm on a settings write and take effect for the
*next* session, not the one that edits the file
([`INVARIANTS.md:22-24`](../../atlas-os/reference/INVARIANTS.md), and its own
correction-of-record at `:27-29` showing that rule is not absolute). And per F7, a deny rule
is a tool-layer matcher, not a lock — so this closes a coverage hole, not a bypass.

**Blocked behind this:** nothing. But F7 cannot be answered honestly while I10's own
coverage is visibly broken.

---

## F4 · File the `mcp_connections` finding under its own number — not I10, but queue-only

**The question.** Do you file the "empty `mcp_connections` does not prevent connector
inheritance" finding as its own queue item, given it is currently cited under a number that
belongs to a different item?

**What is true today — `VERIFIED` here, 2026-08-17.** Three files in `cloud-sessions/` cite
this finding as `V-041`:

```
cloud-sessions/AGENT.md:133          … Gmail, Drive and Calendar (`V-041`).
cloud-sessions/reference/verified-facts.md:960   Tracked as `V-041`.
cloud-sessions/tests/run.sh:2295     … does NOT prevent inheritance (V-041) …
```

But [`QUEUE.md:503`](../../atlas-os/queue/QUEUE.md) reads:

```
## V-041 — A self-perpetuating send_later chain ran unregistered for days
```

and:

```
$ grep -c 'mcp_connections' atlas-os/queue/QUEUE.md
0
```

**The queue has never heard of this finding.** `V-041` is the `send_later` chain. The
connector finding is cited as tracked, is not tracked, and points readers at an unrelated
item when they go looking.

**What it costs to leave open.** The mitigation is real and documented
([`verified-facts.md:932-960`](verified-facts.md)): a routine created with
`"mcp_connections": []` came back holding Gmail, Drive and Calendar, and a docs-only
workflow was handed Gmail. Three files instruct a reader to consult `V-041` for the standing
decision on that; `V-041` is about something else. This is a citation that fails silently —
the reader gets a real item, just not the right one, and has no signal anything is wrong.

**Options.**
- **(a) File it as a new item and let the three citations be corrected to the new number by
  whoever owns those files. — recommended.** One command, and it puts a genuine
  data-exposure question on the founder surface where it belongs.
- (b) Leave the citations as prose-only and strike the `V-041` references. Cheaper, but
  loses the founder decision about whether connector-bearing routines are acceptable at all.

**How to apply.** `QUEUE.md` is written only by `queue.py`; run it yourself so the item is
attributed to a founder:

```bash
cd /Users/danieltecum/internal-company-tool
python3 atlas-os/bin/queue.py add --source founder \
  --title "An explicit empty mcp_connections list does not prevent connector inheritance" \
  --claim "Should every routine-creating path be required to GET the routine back and read mcp_connections, and disable rather than fire when Gmail or Drive appear in work that has no business holding them?" \
  --evidence "cloud-sessions/reference/verified-facts.md" \
  --defer-default "Routines keep silently inheriting every connected claude.ai connector. An empty mcp_connections list reads as unspecified, not none, so a docs-only workflow runs holding Gmail and Drive. Three files in cloud-sessions/ cite this as V-041, which is a different item."
```

Then tell the `cloud-sessions/` owner to repoint `cloud-sessions/AGENT.md:133`,
`cloud-sessions/reference/verified-facts.md:960` and `cloud-sessions/tests/run.sh:2295` to
the number `queue.py` assigns.

**Blocked behind this:** nothing. See F12 for the Anthropic-side half.

---

# Group 2 — needs a real decision

---

## F5 · The `STOP` knot — the single most dangerous item on this sheet

**`V-019` · `V-025` · `V-030` · `V-033` · `V-034` · `OPEN-GAPS.md` G04 — one decision, six tracking numbers.**

**The question, in one sentence.** `STOP` is the only working brake on billable work; decide
in one pass what form it takes, whether it ships to clones and cloud VMs, and whether
`bin/atlas` and `verify_system.sh` are patched to match — because today the answer is "a
directory, no, and no," and a fresh clone has no kill switch at all.

### What is true today, established from scratch

**1. `STOP` is a directory in this working tree, and it is NOT tracked.** `VERIFIED` here,
2026-08-17:

```
$ git ls-files STOP
(empty)
$ git ls-tree HEAD STOP
(empty)
$ ls -ld STOP
drwxr-xr-x  2 danieltecum  staff  64 Aug 17 16:40 STOP
$ ls -a STOP/
.  ..
```

**2. This changed on 2026-08-17 and it inverts `OPEN-GAPS.md` G04.** The register asserts, as
its number-one contradiction of this folder's documentation, that `git ls-files STOP` returns
`STOP` and the sentinel ships as a tracked empty **file**. That was true when the register
was compiled and is false now:

```
$ git ls-tree 767adf3 STOP          # the register's own commit
100644 blob e69de29bb2d1d6434b8b29ae775ad8c2e48c5391    STOP
$ git ls-tree f28e861 STOP          # after "Hand-run the bug sweep…", 2026-08-17T15:25
(empty)
$ git log --diff-filter=D --oneline -1 -- STOP
f28e861 Hand-run the bug sweep, kill the chain, pave the road to the cloud
```

`f28e861`'s own message says so: *"STOP was found silently absent and re-engaged as a
directory; the tracked file form is deleted here, recording what V-034 already documented —
the sentinel does not ship."*

**So `V-034` and the root `AGENT.md` are now correct, and `OPEN-GAPS.md` G04 is stale.** Its
"Where I contradict this folder's own documentation" item 1 should be superseded, not
deleted. The practical consequence has flipped with it: **every fresh clone and every cloud
VM now starts with no `STOP` at all** — not, as the register says, engaged in a self-deleting
form.

**3. The mechanics, all `VERIFIED` by sandbox reproduction here, 2026-08-17:**

| Operation | Code | On a directory `STOP` | Verdict |
|---|---|---|---|
| `touch STOP` | [`verify_system.sh:139`](../../atlas-os/bin/verify_system.sh), [`bin/atlas:82`](../../atlas-os/bin/atlas) | rc 0, mtime bump only | harmless on a dir; **creates the broken file form when `STOP` is absent** |
| `rm -f STOP` | [`verify_system.sh:145`](../../atlas-os/bin/verify_system.sh), [`bin/atlas:83`](../../atlas-os/bin/atlas) | `rm: STOP: is a directory`, rc 1 | dir survives — this is the whole point |
| `[[ -f STOP ]]` | [`bin/atlas:23`](../../atlas-os/bin/atlas) | **FALSE** | `atlas status` reports no freeze while frozen |
| `[ -e STOP ]` | [`nightly.sh:101`](../../atlas-os/bin/nightly.sh) | TRUE | correct — the only correct STOP test in `bin/` |
| `rmdir STOP` | documented unfreeze | rc 0 | works **only while the directory is empty** |
| `rmdir STOP` with `.gitkeep` inside | the proposed `V-030` fix | `rmdir: STOP: Directory not empty`, rc 1 | **the proposed fix breaks the documented unfreeze** |

**4. The four `bin/` bugs, confirmed by reading the live scripts:**

- [`bin/atlas:82`](../../atlas-os/bin/atlas) — `stop) touch "$ROOT/STOP"` — creates the
  self-deleting **file** form whenever `STOP` is absent, which is now the state of every
  fresh clone.
- [`bin/atlas:83`](../../atlas-os/bin/atlas) — `go) rm -f "$ROOT/STOP"` — silently disarms a
  file sentinel; fails loudly on a directory.
- [`bin/atlas:23`](../../atlas-os/bin/atlas) — `if [[ -f "$ROOT/STOP" ]]` — `atlas status`
  reports no freeze while a directory sentinel is engaged.
- [`bin/atlas:25`](../../atlas-os/bin/atlas) and
  [`nightly.sh:104`](../../atlas-os/bin/nightly.sh) — both print `rm …/STOP` as the unfreeze
  hint, which fails `Is a directory` on the mandated form.

**5. `verify_system.sh` is red while frozen, and that is `V-025`'s question.** With `STOP`
engaged, [`verify_system.sh:147`](../../atlas-os/bin/verify_system.sh) runs
`nightly.sh --dry-run`, which halts at `:101` before printing its caps, so
`nightly-declares-turn-and-walltime-caps` fails. The suite was previously green only because
`:145` deleted the sentinel first.

**6. `V-040` is already resolved by the same commit.**
[`atlas-os/tests/process/README.md:3-4`](../../atlas-os/tests/process/README.md) and
[`RESULTS.md:4-5`](../../atlas-os/tests/process/RESULTS.md) now carry
`headers restored 2026-08-17 (V-040)`. Close it when you clear this group.

### What it costs to leave open

Not an adjective — a sequence. A cloud session or a fresh clone starts today with **no
`STOP` present**. `nightly.sh`'s `[ -e ]` is false, so nothing halts. The root
[`AGENT.md`](../../AGENT.md) simultaneously asserts *"Nothing may spend without a founder
approval recorded in `QUEUE.md`"* and documents the kill switch as the enforcement. On that
machine the enforcement is absent, and the assertion is a belief. Meanwhile
[`bin/atlas:23`](../../atlas-os/bin/atlas) will tell an operator who *does* freeze that
machine that nothing is frozen, and [`bin/atlas:82`](../../atlas-os/bin/atlas) will hand a
first-time operator the file form that `verify_system.sh:145` deletes on its next run — the
exact failure that happened three times on 2026-08-13.

The four warnings in root `AGENT.md` are each individually correct and collectively
unresolvable, which is why they have produced six reversions rather than a fix.

### The options

The knot is really one question — *what is the durable form of the sentinel?* — and three
follow directly from the answer.

**(a) Patch `verify_system.sh` first, then go back to a tracked FILE.** `V-019` removes the
unconditional `rm -f`; once it is gone, the file form is safe again, ships in every clone,
survives `git status` without looking deleted, and `rmdir`-vs-`rm` confusion disappears
entirely. `V-030`'s own text names this: *"Note the V-019 patch removes the rm -f, after
which the file form becomes safe again and this commit can be reverted."*
*Cost:* one patch to `verify_system.sh` before anything else is safe. *Risk:* a file sentinel
is one `rm -f` away from silent removal by any future script.

**(b) Ship the directory with a `.gitkeep` and change the documented unfreeze to `rm -r`.**
`V-030` and `V-034`'s approve branch. *Cost:* the documented `rmdir STOP` unfreeze breaks —
`VERIFIED` above, `rmdir: Directory not empty`. Every doc, hint and habit that says `rmdir`
must change in the same commit. *Risk:* `rm -r STOP` is a more dangerous unfreeze idiom than
`rmdir`, and `rmdir`'s refusal-on-non-empty was itself a safety property.

**(c) Status quo — directory, local-only, undocumented in clones.** *Cost:* the current
state. Every clone and cloud VM unfrozen with no sentinel, `bin/atlas` actively misleading,
`AGENT.md` asserting a guarantee it cannot deliver off this one machine.

**Recommendation: (a), in this order.** `V-019` is the root cause of the entire knot — the
file form is only unsafe *because* `verify_system.sh` deletes it — and fixing it collapses
five tracking numbers into one patch, restores a sentinel that ships by construction, and
leaves the unfreeze idiom unchanged. (b) trades a self-deleting brake for a brake whose
documented release command fails; (c) is the only option under which cloud sessions have no
brake at all, which is the condition this repo's spend rule cannot survive.

### What it takes to apply — **all I10**, all you

**Step 1 — `V-019`, make the sentinel check restore what it found.** Replace the
`touch`/`rm -f` pair at [`verify_system.sh:139`](../../atlas-os/bin/verify_system.sh) and
`:145`:

```bash
# atlas-os/bin/verify_system.sh — replace line 139 (`  touch STOP`) with:
  _vs_stop_pre=none
  [[ -d STOP ]] && _vs_stop_pre=dir
  [[ -f STOP ]] && _vs_stop_pre=file
  [[ $_vs_stop_pre == none ]] && touch STOP

# …and replace line 145 (`  rm -f STOP`) with:
  [[ $_vs_stop_pre == none ]] && rm -f STOP
```

Never remove a sentinel the check did not create. Verify:
`mkdir -p STOP && bash atlas-os/bin/verify_system.sh >/dev/null; ls -ld STOP` — the directory
must still be there.

**Step 2 — `V-033`, make `bin/atlas` agree with `nightly.sh`.** Three one-line edits:

```bash
# atlas-os/bin/atlas:23   — status must see a directory sentinel
-  if [[ -f "$ROOT/STOP" ]]; then
+  if [[ -e "$ROOT/STOP" ]]; then

# atlas-os/bin/atlas:25   — the unfreeze hint must not print a command that fails
-    printf '  remove with: rm %s/STOP\n' "$ROOT"
+    printf '  remove with: rmdir %s/STOP   (or rm, if it is a file)\n' "$ROOT"

# atlas-os/bin/atlas:82-83 — freeze/unfreeze must produce the mandated form
-  stop)    touch "$ROOT/STOP"; echo "STOP sentinel created. Scheduled jobs will skip." ;;
-  go)      rm -f "$ROOT/STOP"; echo "STOP sentinel removed." ;;
+  stop)    mkdir -p "$ROOT/STOP"; echo "STOP sentinel created. Scheduled jobs will skip." ;;
+  go)      rmdir "$ROOT/STOP" 2>/dev/null || rm -f "$ROOT/STOP"; echo "STOP sentinel removed." ;;
```

Also fix the hint at [`nightly.sh:104`](../../atlas-os/bin/nightly.sh), same substitution.

**Step 3 — re-track `STOP` in whichever form step 1 makes safe.** Under recommendation (a),
after `V-019` lands: `printf '' > STOP_tmp` is not needed — just `git add -f STOP` once the
file form exists again, and update root `AGENT.md`'s STOP block to describe the file form
and `rm STOP` as the unfreeze. Under (b): `git add -f STOP/.gitkeep` and change every
`rmdir STOP` in the repo to `rm -r STOP` in the same commit.

**Step 4 — record the decisions:**

```bash
cd /Users/danieltecum/internal-company-tool
python3 atlas-os/bin/queue.py decide V-019 approve --by Daniel --note "…"
python3 atlas-os/bin/queue.py decide V-033 approve --by Daniel --note "…"
python3 atlas-os/bin/queue.py decide V-030 <approve|reject> --by Daniel --note "…"
python3 atlas-os/bin/queue.py decide V-034 <approve|reject> --by Daniel --note "…"
python3 atlas-os/bin/queue.py decide V-025 <approve|reject> --by Daniel --note "…"
python3 atlas-os/bin/queue.py decide V-040 approve --by Daniel --note "Headers restored 2026-08-17 by f28e861; verified present in tests/process/README.md:4 and RESULTS.md:4."
```

**Step 5 — supersede, do not delete.** Root [`AGENT.md`](../../AGENT.md)'s STOP block and
[`OPEN-GAPS.md`](OPEN-GAPS.md)'s G04 both need marking with what corrected them (ground rule
5). G04's rebuttal was correct at `767adf3` and was overtaken by `f28e861`; say that, rather
than rewriting it as though it had always been wrong.

**Blocked behind this:** every spend claim in the repository, `V-037` (whether
`meeting-recorder` should check `STOP` before its transcription call — its premise that
`DEEPGRAM_API_KEY` is set was **not reproduced** on 2026-08-16, see
[`gap-closure-2026-08-16.md:158-171`](gap-closure-2026-08-16.md)), and `V-053`'s two dispatch
holes, which are described as *masked* by the sentinel rather than fixed.

---

## F6 · `refuses()` credits any non-zero exit as a refusal — `V-035`, **I10**

**The question.** Do you fix the four-line helper that makes `verify_system.sh` report a
passed refusal whenever its target is broken or missing?

**What is true today.** [`verify_system.sh:30`](../../atlas-os/bin/verify_system.sh),
verbatim, one line:

```bash
refuses() { local name="$1"; shift; if "$@" >/dev/null 2>&1; then bad "$name" "command SUCCEEDED but should have refused: $*"; else ok "$name"; fi; }
```

`VERIFIED`: the `else` branch fires on **any** non-zero exit. It cannot tell rc 2 (a real
gate refusal) from rc 3 (a usage error) from rc 127 (the binary is not there). Four call
sites (`grep -c 'refuses ' atlas-os/bin/verify_system.sh` → `4`). `QUEUE.md:437-446` carries
it as OPEN, source founder; the e2e run records it independently as F-7
(`atlas-os/tests/results/e2e-2026-08-15.md:801`) and notes the same copy exists in
`proposed_checks.sh`.

**What it costs to leave open.** This is the *generator* of the false-green class, not an
instance of it. `verify_system.sh` is the suite every cloud probe ran to declare this
repository healthy — probes 1 through 5 all quote its result. Delete a check's target and
the check goes green. `V-035`'s own text: *"Patching individual instances has now failed five
times."*

**Options.**
- **(a) All three parts of `V-035`: assert the specific exit code, extend the existing
  mutation matrix in `atlas-os/tests/heartbeat-arch/` past `heartbeat.py`, and have
  `merge_graphs.py` import the validator's duplicate-id predicate. — recommended.** The
  countermeasure already exists in this repo and reaches exactly one script; the cost is
  pointing it at more scripts, not building it.
- (b) Fix `refuses()` alone. Cheap, closes the four call sites, leaves the class generator
  (no mutation proof) in place — which is how the previous five attempts failed.
- (c) Leave it, and stop quoting `verify_system.sh`'s pass count as evidence of anything.

**How to apply — you, `atlas-os/bin/**` is I10.** The minimal form:

```bash
# atlas-os/bin/verify_system.sh:30 — take the expected code as an argument
refuses() { local name="$1" want="$2"; shift 2; "$@" >/dev/null 2>&1; local rc=$?
  if [[ $rc -eq $want ]]; then ok "$name"
  else bad "$name" "expected rc=$want, got rc=$rc: $*"; fi; }
```

then update the four call sites to pass the code each gate actually returns. **Negative
control before you believe it:** rename one call site's target binary and confirm the check
goes **red**, not green — that is the whole point of the fix.

**Blocked behind this:** every "the suite is green" claim in `cloud-sessions/`, and F8's
verification (you cannot tell a fixed append-only check from a vacuous one until `refuses()`
is honest).

---

## F7 · Is I10 a lock or an intent? — `V-023`, with `V-026` and `V-031`, **I10**

**The question.** Do you enforce `atlas-os/bin/**` below the tool layer, or restate I10
honestly as an intent that binds cooperating agents?

**What is true today.** `permissions.deny` matches tool names and Bash command prefixes.
Probe 7 ran six write attempts against `atlas-os/bin/**` from a cloud VM
([`cloud-vm-probe-7-2026-08-14.md:136-138`](cloud-vm-probe-7-2026-08-14.md)):

```
$ python3 -c "open('atlas-os/bin/probe7-canary.py','w').write('# canary')"
→ (no output)
EXIT:0
```

The other five (Write, Edit, `cp`, `echo >`, `tee`) were blocked; a control write to a
non-guarded path succeeded, so the probe had teeth. Filed as `V-023` (`QUEUE.md:304-313`,
OPEN). [`verified-facts.md:761`](verified-facts.md) heads its own section
*"I10 is enforced at the tool layer, not the filesystem — `DISPROVEN 2026-08-14`"*, and its
not-verified table at `:1034` carries the row *"Whether `atlas-os/bin/**` is human-only in
any enforceable sense — `DISPROVEN` as stated."* Both are right to.
[`INVARIANTS.md:168`](../../atlas-os/reference/INVARIANTS.md) already hedges to
`ENFORCED mechanism, INCOMPLETE coverage`.

**What it costs to leave open.** Every other item on this sheet that says "blocked on a
human because I10" means "blocked by convention". That is a materially different statement
and it should be said out loud rather than assumed. Probe 7's own framing is the one to
keep: **a gap in enforcement is not permission.**

**Options.**
- **(a) Restate I10 as an intent with the matcher's scope named, and keep the deny rules. —
  recommended.** It is true, it is free, and it makes every "blocked" on this sheet honest.
  A filesystem-level lock (immutable attributes, a read-only mount, a pre-commit hook) is
  real work, would fight `git apply` for the very patches on this sheet, and buys little
  against an agent that is not adversarial — which is the actual threat model.
- (b) Enforce below the tool layer. Highest assurance, highest friction, and note that it
  would block F1, F3, F5 and F6 from being applied by *you* without first disarming it.
- (c) Adopt `V-026` as well — extend the guarded list to the *detection* substrate
  (`atlas-os/telemetry/**`, the check bodies in `verify_system.sh`, `outcome.py`,
  `freshness.py`), on the argument that our guarded list covers what an agent could break
  and omits what would notice. This is orthogonal to (a)/(b) and is worth taking with either.
- (d) `V-031` — generate per-lane deny rules from each lane's registered `--owns` globs, so
  a lane physically cannot write outside its claim. Note its own caveat: the rules re-arm on
  any settings write and would take effect mid-session, the same mechanism that locked
  agents out of `bin/`. Design for it or discover it.

**How to apply — (a) is a documentation edit to an I10-guarded file, so it is yours:** amend
[`INVARIANTS.md:168-174`](../../atlas-os/reference/INVARIANTS.md) to say the guard is a
tool-name and Bash-prefix matcher, that writes reaching the filesystem by other means
(an interpreter, a redirect from a shell the matcher did not parse) are not intercepted, and
that the invariant binds by intent. Then:

```bash
python3 atlas-os/bin/queue.py decide V-023 <approve|reject> --by Daniel --note "…"
python3 atlas-os/bin/queue.py decide V-026 <approve|reject|defer> --by Daniel --note "…"
python3 atlas-os/bin/queue.py decide V-031 <approve|reject|defer> --by Daniel --note "…"
```

**Blocked behind this:** the word "blocked" on this entire sheet, and `V-010`'s remaining
half (F3).

---

## F8 · The append-only check `wc -c`s a directory — `V-013`, **I10**

**The question.** Do you fix the check that has been unable to evaluate its own subject since
it was written, and is therefore permanently red for a known-bad reason?

**What is true today.** [`verify_system.sh:180`](../../atlas-os/bin/verify_system.sh):

```bash
APPEND_ONLY=(atlas-os/metrics/history.jsonl atlas-os/telemetry/runs)
```

`atlas-os/telemetry/runs` is a **directory**, and `:185` does `NEW=$(wc -c < "$p" …)`.
Reproduced live here, 2026-08-17:

```
$ git ls-files --error-unmatch atlas-os/telemetry/runs   # rc=0, tracked
$ git show "HEAD:atlas-os/telemetry/runs" | wc -c
71                                  # a tree listing
$ wc -c < atlas-os/telemetry/runs
wc: stdin: read: Is a directory     # NEW is empty
```

`:186` then evaluates `[[ "${NEW:-0}" -lt "${OLD:-0}" ]]` → `0 -lt 71` → true → `bad`.
**The check can only ever fail.** Probe 2 states it is not a portability bug and would
reproduce identically on macOS.

**What it costs to leave open.** A permanently-red check is read as furniture. While it is
red for a known-bad reason it cannot go red for a real one — so the append-only protection
on the telemetry ledger is unenforced *and* looks enforced-but-noisy. It is also one of the
two reds that make "is the suite green?" unanswerable, which is `V-025`'s premise in F5.

**Options.**
- **(a) Glob the directory to its `.jsonl` members and compare per file. — recommended.** It
  is what the invariant actually means, and it makes the check capable of going red for a
  real violation.
- (b) Guard file-vs-directory and skip directories. Removes the false red, protects nothing.
- (c) Drop the directory from `APPEND_ONLY`. Honest, and abandons I6 on the ledger.

**How to apply — you, I10:**

```bash
# atlas-os/bin/verify_system.sh:181 — expand a directory entry to its members
for p in "${APPEND_ONLY[@]}"; do
  if [[ -d "$p" ]]; then
    while IFS= read -r f; do APPEND_ONLY_FILES+=("$f"); done < <(git ls-files "$p")
  else
    APPEND_ONLY_FILES+=("$p")
  fi
done
# …then run the existing OLD/NEW comparison over "${APPEND_ONLY_FILES[@]}"
```

**Negative control, required before believing it:** delete one line from a
`atlas-os/telemetry/runs/*.jsonl` file in a scratch clone and confirm the check goes **red**.
A check that stops being red is not the same as a check that works — that is exactly the
mistake F6 is about.

**Also owed, and closable by an agent:** `OPEN-GAPS.md` G08's underlying wording fix in
`verified-facts.md` was already made on 2026-08-16
([`gap-closure-2026-08-16.md:209-230`](gap-closure-2026-08-16.md)). Only the check itself is
still open.

**Blocked behind this:** a green `verify_system.sh`, and therefore `V-025`.

---

## F9 · Heartbeat lanes go STALE while working — `V-021`, **I10**

**The question.** Should agents register heartbeat lanes with
`--session "$CLAUDE_CODE_SESSION_ID"` and carry the lane name in `--goal`, so the liveness
oracle can actually see them?

**What is true today.** `heartbeat.py` registers rows under a human-readable lane name; the
liveness oracle keys on the telemetry ledger's `session_id`, a UUID. A lane name can never
appear in the ledger, so such a row reports `no ledger entry` however hard the agent is
working ([`verified-facts.md:729-748`](verified-facts.md), `VERIFIED` with a clock override —
`:732` states it outright: *"A human lane name can never appear in the ledger"*).
`QUEUE.md:282-291`, OPEN — measurement `VERIFIED`, fix `PROPOSED`.

The board is currently quiet, so the symptom is not visible right now — `VERIFIED` here,
2026-08-17:

```
$ python3 atlas-os/bin/heartbeat.py validate
note:  liveness oracle: telemetry ledger OK — 4 ledger file(s), 94 session(s), 0 unparseable line(s)
heartbeat/STATE.md: 0 error(s), 0 warning(s)
```

That is a clean board, not a working oracle. The defect appears 45+ minutes into an active
lane.

**What it costs to leave open.** `QUEUE.md:287`: *"lanes keep going STALE while actively
working, so a stale row and a dead lane stay indistinguishable."* For the cloud half there is
**no fix at any keying** — a cloud session's ledger writes never leave the VM (F11), so a
cloud lane is invisible to the oracle however it is keyed. Decide the local half knowing the
cloud half stays open.

**Options.**
- **(a) Adopt the proposed keying: `--session` takes the session id, `--goal` carries the
  lane name. — recommended.** It makes the oracle able to work at all locally, and it costs
  a convention change plus a `DISPATCH.md` edit.
- (b) Change the oracle to accept either key. More code in an I10 file, same outcome.
- (c) Leave it and stop treating STALE as meaningful. Defensible only if you also delete the
  STALE signal, because a signal nobody trusts is worse than none.

**How to apply.** The convention change is in `atlas-os/playbooks/DISPATCH.md` (not
I10-guarded, delegable); any oracle change is `atlas-os/bin/heartbeat.py` and yours. Record:

```bash
python3 atlas-os/bin/queue.py decide V-021 <approve|reject> --by Daniel --note "…"
```

**Verify it closed:** `heartbeat.py validate` 45+ minutes into an active lane reports a dated
ledger sighting rather than `NO LEDGER ENTRY`. Not a code review — a timed observation.

**Blocked behind this:** nothing hard, but F10 makes it worse.

---

## F10 · Telemetry `run_id` coerced to string — **I10**

**The question.** Do you fix the ledger writer so a non-string session id becomes a typed
string or a refusal, rather than landing in `run_id` untyped?

**What is true today.**
[`atlas-os/tests/telemetry/RESULTS.md:209`](../../atlas-os/tests/telemetry/RESULTS.md),
repeated once per run block, verbatim:

> | known-defect | `degraded/run_id-coerced-to-string` | a non-string session_id lands in run_id untyped (got 12345); SCHEMA section 2 requires a string. bin/ is I10-guarded — human fix |

Latest run,
[`atlas-os/tests/telemetry/RESULTS.md:2140`](../../atlas-os/tests/telemetry/RESULTS.md):
**`155 passed · 0 failed · 1 skipped · 1 known-defect`**, 2026-08-17T23:45:06Z. The suite's own category note at `:19` is exactly right about why it is
counted separately: *"the check ran, found a real defect, and the defect is in a file this
lane may not edit (I10). Counted and named separately so it can never be mistaken for a
pass."*

**What it costs to leave open.** A field the schema requires to be a string can silently hold
a non-string. Every consumer that string-matches `run_id` gets a **miss rather than an
error** — including F9's liveness oracle, and `outcome.py`'s join from a founder's
APPROVED/REJECTED back to the session that filed it. `QUEUE.md`'s own attribution section
says *"a wrong attribution is worse than a null, because it silently mislabels a training
example and no later reader can tell."* A type-confused `run_id` is that, one level down.

**Options.**
- **(a) Refuse a non-string at write time. — recommended.** Consistent with the queue's
  stated principle that a null with a reason beats a wrong value, and it makes the failure
  loud at the only moment anyone can act on it.
- (b) Coerce explicitly with `str()` and record that coercion happened. Keeps data flowing,
  and a coerced `12345` still will not match anything.
- (c) Relax `SCHEMA.md` to allow either type. Note `SCHEMA.md` is itself I10-guarded, so this
  is also yours, and it makes every downstream string-match a latent bug.

**How to apply — you, I10.** The writer is under `atlas-os/bin/`; the check that already
detects it is `degraded/run_id-coerced-to-string`, so the acceptance test exists. Verify by
re-running `bash atlas-os/tests/telemetry/run.sh` and confirming the line moves from
`known-defect` to `ok` — and feed it `12345` once by hand to confirm you get a typed string
or a refusal, not a silent pass.

**Blocked behind this:** F9's oracle, and `outcome.py`'s attribution join.

---

## F11 · `DECISION.md` reversal trigger fires on the wrong event — `V-020`

**The question.** Should `atlas-os/backend/DECISION.md` reversal trigger 2 be rewritten to
fire on cloud telemetry **arriving in `runs/`**, rather than on a hook mechanism becoming
available inside cloud sessions?

**What is true today.** Hooks *do* fire in cloud sessions — probe 6 proved it with matching
session ids. But the trace cannot leave the VM: `atlas-os/telemetry/raw/` is gitignored
([`.gitignore:11`](../../.gitignore) — `atlas-os/telemetry/raw/*`) and nothing calls
`promote.py` on any automatic path. `VERIFIED` here, 2026-08-17:

```
$ grep -c 'promote' atlas-os/bin/nightly.sh
0
$ grep -rn 'promote.py' atlas-os/bin/ .claude/
atlas-os/bin/export_finetune.py:308:  print("promote raw/ first: python3 atlas-os/telemetry/promote.py")
atlas-os/bin/outcome.py:155:          continue     # torn line; promote.py counts these
```

One print string and one comment. No caller.

**This is deliberate and must not be "fixed" by an agent.** The obvious fix — have the
session commit its own trace — **was shipped and reverted within the hour** (`b71e0c6` →
`1ad5228`) for two reasons worth more than the fix
([`verified-facts.md:686-703`](verified-facts.md)): it converts an involuntary record into a
cooperative one, and `Stop`/`SessionEnd` fire after the last commit so the trajectory is
*always* truncated, identically and invisibly. Partial data that looks complete is worse than
absent data.

**What it costs to leave open.** `QUEUE.md:276`, verbatim: *"a future reader may retire the
Supabase backend on the strength of hooks firing in the cloud, when the problem it exists to
solve is still unsolved."* The trigger as written has already been satisfied by an event that
solved nothing.

**Options.**
- **(a) Rewrite trigger 2 to fire on telemetry arriving in `runs/`. — recommended.** It names
  the outcome instead of a mechanism, which is the difference between the trigger being a
  measurement and being a coincidence.
- (b) Leave it and add a caveat beside it. Cheaper; relies on the next reader reading the
  caveat.

**How to apply.** `atlas-os/backend/DECISION.md` is **not** I10-guarded — this is a decision,
not an edit you must make personally. Record it and delegate the wording:

```bash
python3 atlas-os/bin/queue.py decide V-020 <approve|reject> --by Daniel --note "…"
```

**Verify it closed:** a founder decision recorded in the queue. Not a measurement — there is
nothing to measure until the underlying problem is solved.

**Blocked behind this:** any decision to retire the Supabase backend.

---

# Group 3 — needs Anthropic, or is out of our hands

Nothing here is fixable in this repository. They are on the sheet so they stop being
rediscovered, and because two of them change how we must *write* things.

## F12 · `mcp_connections: []` does not mean "none" — `VERIFIED 2026-08-17`

A routine created with an explicit empty list came back holding four connectors —
Google_Calendar, Gmail, Google_Drive, and the expected `Claude_Code_Remote` infrastructure
entry ([`verified-facts.md:932-960`](verified-facts.md)). An empty list reads as
*unspecified*, not *none*. A docs-only workflow was handed Gmail and Drive.

**Ours to do:** nothing but mitigate. The only proven control is to `get` the routine back
after creating it and read `mcp_connections`; disable rather than fire if it holds connectors
the work has no business with. **`UNVERIFIED`:** whether a *non-empty* list restricts rather
than adds, and whether any value means "none at all" — each costs a dispatch to settle.
**Theirs to fix:** a field that looks like a control and silently does nothing. See F4 for
filing it.

## F13 · `allowed_tools` is an auto-approval list, not a sandbox — `VERIFIED 2026-08-17`

A routine payload carried `["Bash","Read","Write","Edit","Glob","Grep","Task"]` and the
dispatched session then used `ToolSearch`, `TaskCreate` and `TaskUpdate` — none of them
listed ([`verified-facts.md:891-908`](verified-facts.md)). Listing a tool spares it a
permission prompt; omitting one does not take it away.

**Consequence for how we design:** do not treat `allowed_tools` as a boundary. The only
controls that bind a cloud session are this repo's `.claude/settings.json` deny rules (which
travel, since the file is tracked) and the prompt itself — and per F7 the first of those is a
matcher, not a lock. **`UNVERIFIED`:** whether an *empty* `allowed_tools` behaves differently.

## F14 · A fresh VM's `origin/main` is days stale — `VERIFIED 2026-08-17`

`HEAD` is correct at dispatch, but the remote-tracking ref is not
([`verified-facts.md:910-928`](verified-facts.md)). A session that reasons about
`origin/main` gets a wrong answer: `git diff origin/main HEAD` produced a 59.8KB diff of
other people's already-merged work.

**Ours to do:** every cloud brief must say *diff against `HEAD` at dispatch, or
`git fetch origin` first*. That is a wording rule for `/cloud` and `cs handoff` briefs, not a
fix.

## F15 · No secrets store, and cloud telemetry cannot persist

Cloud environments have no secrets store — anything in an environment variable or setup
script is readable by anyone using that environment
([`limits.md:98`](limits.md), [`networking.md:249`](networking.md)). Codex removes secrets
before the agent phase and Cursor scopes build secrets to the build step; we have neither.
**The standing rule stays: keep credentialed work out of cloud sessions entirely.** The
telemetry half is F11's underlying constraint and is the same shape — there is no
Anthropic-side persistence surface for a VM's hook output.

## F16 · Interactive terminal attach is gated off for this account — `VERIFIED`

[`verified-facts.md:198`](verified-facts.md), *"Interactive terminal attach is NOT
available."* Per ground rule 6 this is **not permitted**, not *not possible* — the
distinction matters if it is ever ungated. Nothing to decide; recorded so it is not
re-probed.

---

## Not on this sheet, and why

- **Six gaps the register still lists as open are closed.** G06, G07, G08, G09, G11 and G13
  were closed by a cloud session on 2026-08-16 —
  [`gap-closure-2026-08-16.md`](gap-closure-2026-08-16.md). G13 was found *already* closed on
  arrival (`a1b05ef` landed 7 minutes before the register was compiled).
  **[`OPEN-GAPS.md`](OPEN-GAPS.md) has not been updated and still lists all six as open.** An
  agent can fix that; it is not your decision. Two off-by-ones travel with it: section (a)'s
  prose says "5" over six rows (known), and **section (b)'s header and the counts line both
  say "8" over nine rows** (`awk` over the section → `count: 9`) — the same error, twice, in
  the same file, uncaught.
- **G24 — the Linux/macOS check-count divergence** (627 vs 421 with only 6 declared skipped)
  is being diagnosed on `claude/lane-linux-gapclose`. Not yours, and not reproducible here.
- **`V-032` is already APPROVED** (`QUEUE.md:403`, decided 2026-08-15) and is not open.
- **The porting audit's live defects** —
  [`porting-audit-2026-08-17.md`](porting-audit-2026-08-17.md) — are real and mostly belong to
  the `cloud-sessions/bin` owner, not to you. The sharpest is that the documented install
  (`ln -sf … ~/.local/bin/cs`) mis-roots the tool, so `~/.local/bin/cs ls` today prints
  `no dispatches recorded` over a 12-row ledger, exit 0. Not I10, so it is delegable — but it
  is a ground-rule-4 violation live in the shipping repo.
- **50 items are OPEN in the queue** (`grep -c '^- status: OPEN'` → 50). This sheet covers the
  ~15 that are infrastructure and safety. The remainder are company-state decisions
  (`V-001`–`V-004`, `V-015`–`V-017`, `V-022`, `V-042`–`V-048` staleness items) and belong to a
  different sitting.

## What this lane could not do

- **Nothing was filed to the queue and nothing was decided.** `queue.py` is the only writer
  and the decisions are yours. Every command above is written for you to paste.
- **`verify_system.sh` was not run.** It does `touch STOP` / `rm -f STOP` against the live
  sentinel and invokes the real `nightly.sh`. The mechanics in F2 and F5 were reproduced in a
  scratch directory with a stub `nightly.sh` instead — quoted in full so you can re-run them.
- **No dispatch, no routine, no `cs start`/`handoff`/`new`/`send`/`tp`/`rc`.** $0.00 spent.
- **One side effect, disclosed:** `bash atlas-os/tests/heartbeat/run.sh` was run (sanctioned
  as a safe read-only suite) and appended a dated run block to
  `atlas-os/tests/heartbeat/RESULTS.md`, which is append-only by design. That run reported
  **`90 passed  0 failed  0 skipped  6 defect(s) recorded`**, where the previous block
  (2026-08-17T23:45:05Z) recorded **5** — because defect 1 is case-sensitivity on a
  case-insensitive filesystem, so it fires on this Mac and not on a Linux VM. **The heartbeat
  suite's defect count is platform-dependent and does not say so** — the same family as G24,
  found independently. Worth handing to the `lane-linux-gapclose` lane.
