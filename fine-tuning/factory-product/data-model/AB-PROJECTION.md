# AB-PROJECTION — nightly A/B shadow results in the Model Factory tab

**Written:** 2026-07-30 (MF-PTC-RETRAIN-EXEC subagent).
**Script:** `fine-tuning/factory-product/ab_shadow_sync.py` (`--dry-run` default, `--commit` to write).
**Tests:** `fine-tuning/factory-product/tests/test_ab_shadow_sync.py` (19 tests).
**Source data:** `platform-alpha/finetune-out/ab-shadow/<night>/` — gitignored JSONLs
(one record per account per arm), `summary.json` where the a1v1 driver wrote one,
`comparison.html` always. The main-model (Claude) arm is the real production roster
from `account_agent_reviews` (production DB; the local copy is empty — Claude context
is read from what the drivers already extracted into `summary.json`/`comparison.html`).

## What got projected where, and why

**One `factory_run_events` row per (night-directory, arm)**, `kind='steering_event'`,
`ref.event_subtype='ab_shadow_night'`. Nothing else is written — no other table, no
DDL, no product code.

Why this surface and not the alternatives:

- **`kind` is a closed CHECK vocabulary (13 values)** and no schema change was
  permitted. The established extension pattern is exactly this one:
  platform-alpha's `startFactoryRun` extends `steering_event` with
  `ref.event_subtype: "run_queued"` rather than adding a kind
  (`factory_run_events.md`). An A/B night is evidence steering the S8/S9
  promotion decision, so the semantic fits.
- **`headline` is the guaranteed-rendered surface** — the Timeline renders
  `headline` for every kind (per `factory_run_events.md`, `detail` is only
  rendered for `agent_dispatched` today). So every headline carries the
  side-by-side reading on its own, e.g.:
  `AB shadow 2026-07-20 [shadow-replay] — v3 vs Claude: 1403 replayed / Claude
  roster 1395; matches 1040, missed real action 326, false alarms 19, different 10;
  errors 0`. The full aggregate lives in `detail` (queryable now, renderable later).
- **NOT `factory_run_digests`**: `UNIQUE(run_id, iteration)` means a row parked
  there would collide with the real iteration digest `factory.py` writes for the
  same run.
- **NOT `factory_models.report`**: that column is owned by `models_backfill.py`;
  re-running it (it is designed to be re-runnable) would clobber anything appended.

**Run placement.** Events attach to the arm's model lineage via
`factory_models.source_run_id`, resolved at runtime (never hardcoded): v3 → the
iteration-3 run (`6f2d943d…`), v5 → the iteration-5 run (`9200efa1…`).
`ptc-steel-a1-v1` has **no run row by documented design** (`models_backfill.py`:
"different lanes entirely"), and `run_id` is NOT NULL — so a1v1 nights and the
roster-only night anchor on the newest ptc-steel run (iteration 6, `ac6a6078…`, the
active retrain), which is the run whose S9 decision consumes exactly this clean-night
evidence (model-ab-serving-v2 PR-PLAN §8 / V-036). `ref.factory_model_id` +
`ref.model_id` carry the arm's catalog identity on every row regardless of host run.

**Privacy.** Aggregates only: counts, rates, hashes, model/tool identifiers.
`accountName`, `reasoning`, `payload`/drafts, `userMessage`, tool `resultContent`,
and `fab.unsupportedExamples` (contains account names) never leave the JSONLs —
enforced by allowlist construction and by `test_no_raw_customer_content_anywhere`.
A post-commit scan of all 10 live rows found no content fields (the only "reasoning"
substring is the count key `reasoningSignalN`).

## Rows written (local Supabase, verified 2026-07-30)

| night | arm | mode | host run | records | errors | roster | matches Claude |
|---|---|---|---|---|---|---|---|
| 2026-07-19 | — | roster-only | ac6a6078 (iter 6) | — | — | 1031 | — |
| 2026-07-20 | v3 | shadow-replay | 6f2d943d (iter 3) | 1403 | 0 | 1395 | 1040 |
| 2026-07-20 | v5 | shadow-replay | 9200efa1 (iter 5) | 1403 | 0 | 1395 | 1021 |
| 2026-07-20 | a1v1 | retro-replay | ac6a6078 (iter 6) | 1397 | 26 | 1395 | 19 |
| 2026-07-21 | v3 | shadow-replay | 6f2d943d (iter 3) | 1403 | 0 | 1396 | (no summary block) |
| 2026-07-21 | v5 | shadow-replay | 9200efa1 (iter 5) | 1322 | 0 | 1396 | (no summary block) |
| 2026-07-21 | a1v1 | retro-replay | ac6a6078 (iter 6) | 1397 | 24 | 1396 | 55 |
| 2026-07-22 | a1v1 | live-shadow | ac6a6078 (iter 6) | 1396 | 289 | 1396 | 4 |
| 2026-07-22 | a1v1 | retro-replay | ac6a6078 (iter 6) | 1396 | 289 | 1396 | 4 |
| 2026-07-24 | v3 | shadow-replay | 6f2d943d (iter 3) | 16 | 0 | 10 | (partial night) |

10 rows total. Idempotency verified: three consecutive `--commit` runs left
`factory_run_events` at the same count (111 total, 10 of them ab_shadow_night).

## Verification queries

```sql
-- the projection, side by side
select ref->>'night' as night, ref->>'arm' as arm, ref->>'mode' as mode,
       left(run_id::text,8) as run,
       detail->'aggregates'->>'records' as recs,
       detail->'aggregates'->>'errors' as errs,
       detail->'main_model'->>'roster_accounts' as roster,
       detail->'comparison'->>'type_match' as match
from factory_run_events
where ref->>'event_subtype' = 'ab_shadow_night'
order by ts, arm;

-- idempotency check (run the sync twice; both counts must be unchanged)
select count(*) as total_events,
       count(*) filter (where ref->>'event_subtype'='ab_shadow_night') as ab_rows
from factory_run_events;

-- the forward-compat block, ready for promotion (see below)
select detail->'model_ab_nights_preview'
from factory_run_events
where ref->>'event_subtype' = 'ab_shadow_night' and ref->>'arm' is not null;
```

## Known gaps

- **2026-07-22 live vs retro are byte-identical artifacts** (`cmp` on both
  `a1v1.jsonl` and `summary.json`): one run recorded under two directory names.
  Both rows are projected faithfully (the directory, not the date, is the
  idempotency key precisely because this night has two dirs) — but they are
  one observation, not two. De-duplication is a source-data question, not a
  projection one.
- **07-21 v3/v5 have no paired comparison numbers**: that night's
  `comparison.html` was built before the summary block existed and no
  `summary.json` exists for v3/v5 nights. Roster + envelope hash + full arm
  aggregates are projected; `detail.comparison` is `null`. Recomputing pairs
  would require re-reading Claude's per-account outputs (production
  `account_agent_reviews`) — out of scope for a laptop-side projection.
- **a1v1's `errors` are never-emitted terminals** (e.g. 289 on 07-22 —
  `error_rate` 20.7%, and `records_with_tool_error` 1010/1396 driven by
  `emit_output` schema rejections). The projection records them as-is; the
  interpretation lives in the corpus-evidence work, not here.
- **07-24 is a partial night**: 16 v3 records against a 10-account union
  roster (the driver's own page says "union of whatever accounts each arm's
  shadow capture covered"). Projected faithfully, flagged here.
- **`ts` is the night being compared** (`<night>T23:59:00-07:00`), not the
  moment the replay ran — retro replays ran days later. Consistent with the
  table's documented "ts is not a precise event timestamp" caveat.
- **`detail` is not rendered by the current Timeline UI** (only
  `agent_dispatched`'s is). The side-by-side reading is fully present in
  `headline` today; rendering `detail.model_ab_nights_preview` as a card is
  natural product follow-up (model-ab-serving-v2 task 7 covers this surface).
- **`clean` is never asserted.** These are research replays; the V-036
  clean-night verdict belongs to `computeNightlyGroundingVerdict` on live
  nights. `clean: null` + `not_clean_reasons` says so on every row.
- **Claude context is driver-computed, not recomputed**: roster counts and
  paired numbers come from `summary.json` / `comparison.html`. If those
  artifacts were wrong, this projection is wrong the same way (single source
  of truth, no silent re-derivation).

## Forward-compat: mapping onto model-ab-serving-v2's `model_ab_nights` (PR-PLAN §4.4)

Every challenger row carries `detail.model_ab_nights_preview` shaped onto the
future table — the future PR **promotes** this data instead of migrating it:

| `model_ab_nights` (§4.4) | preview key | note |
|---|---|---|
| night | `night` | date, not directory (dir preserved in `ref.variant_dir`) |
| org | `org_slug` + `org_id` | org_id read from the JSONL records |
| challenger | `challenger` | `factory_models.model_id`; uuid link in `ref.factory_model_id` |
| accounts compared | `accounts_compared` | distinct accountIds in the arm's JSONL |
| errors | `errors` | arm records with non-null `error` |
| fabrication per arm | `fabrication_challenger` / `fabrication_main` | challenger from `summary.json` fab g8+g22 where present; main never measured in these artifacts → null (gap H in the PR-PLAN, closes at the source there) |
| guardrail flags | — | not in these artifacts (guardrail rider was idle); null-by-absence |
| `clean` boolean | `clean` (always null) | verdicts belong to `computeNightlyGroundingVerdict`, live nights only |
| `not_clean_reasons` | `not_clean_reasons` | states why clean is null here |
| `envelope_hash` | `envelope_hash` | the org contract hash from `comparison.html`; per-arm input-identity check additionally available as `aggregates.envelope_set_sha256` |

Extra fields the future table should consider keeping: `mode`
(live-shadow / retro-replay / shadow-replay — V-036 counts live only), and
`envelope_set_sha256` (same value across two arms == same inputs, the L51
night-level comparability check).

Promotion sketch for that PR:

```sql
insert into model_ab_nights (night, org_id, challenger, ...)
select (detail->'model_ab_nights_preview'->>'night')::date,
       detail->'model_ab_nights_preview'->>'org_id',
       detail->'model_ab_nights_preview'->>'challenger',
       ...
from factory_run_events
where ref->>'event_subtype' = 'ab_shadow_night' and ref->>'arm' is not null;
```
