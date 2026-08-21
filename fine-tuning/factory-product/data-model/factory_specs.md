# `factory_specs`

The premade AgentSpec library, made queryable — the Specs tab's data source. Unlike every
other table in this folder, this one has a genuine `UNIQUE` column (`slug`) to upsert
against, because the source files themselves are already uniquely named and versioned.

**Populated by:** `specs_sync.build_spec_rows()` → `push_spec_rows()` —
`fine-tuning/factory-product/specs_sync.py`, upserted `on_conflict=slug`.
**Real source of truth:** `fine-tuning/factory-product/specs/*.yaml` — one file per
AgentSpec, e.g. `corpus-planner.yaml`, `evidence-audit-hallu.yaml`. Currently ~30 spec files.

## Columns

| column | type | constraints | notes |
|---|---|---|---|
| `id` | uuid | PK | |
| `slug` | text | NOT NULL, **UNIQUE** | must equal the YAML filename's stem — enforced, not assumed |
| `role_family` | text | NOT NULL | from the YAML's own `role_family` field |
| `mission` | text | NOT NULL | from `mission` |
| `stages` | text[] | NOT NULL, default `{}` | from `stages` (a list of S-codes, e.g. `["S4"]`) |
| `done_when` | text | NOT NULL | from `done_when` |
| `typical_count` | text | nullable | from `typical_count`, coerced to a string (the YAML sometimes has it as a bare number) |
| `evidence_pointer` | text | nullable | from `evidence_pointer` |
| `spec_version` | text | NOT NULL, default `v1` | from `spec_version`, defaulted if absent |
| `spec_body` | jsonb | NOT NULL | **the entire parsed YAML document, verbatim** — the promoted columns above are a queryable projection of this, not a second source of truth |
| `status` | text | NOT NULL, default `draft`, IN `('draft','reviewed')` | from `status`, defaulted if absent |
| `updated_at` | timestamptz | NOT NULL, default `now()` | DB-managed |

## Fails loudly, not silently — the one exception to this repo's usual fail-open default

Unlike `push_stage()` (fail-open) and `resolve_gate()` (fail-closed), a malformed spec file
(YAML parse error, a required field missing, or a `slug`/filename mismatch) makes
`build_spec_rows()` **raise immediately** — it does not skip the bad file and quietly
produce "one fewer row." A catalog sync has no live pipeline stage riding on it either way,
so there's no fail-open/fail-closed direction to preserve; the only wrong outcome is a
broken file silently looking like the catalog just has fewer entries (`PRContext.md` T15).

Required fields, checked before any row is built: `slug`, `role_family`, `mission`,
`done_when`.

## Known gaps / accepted risks

None open — this is the most complete/faithful projection of the 9 (every field either
maps directly to a real YAML key or is `spec_body`'s verbatim copy).

## Example row

From the real spec file `fine-tuning/factory-product/specs/corpus-planner.yaml`:

```json
{
  "slug": "corpus-planner",
  "role_family": "builder",
  "mission": "Turn audits into family doses, LAWS, mixture bands, and the SEED for this run's corpus.",
  "stages": ["S4"],
  "done_when": "every family cites its evidence or is explicitly labeled untested",
  "typical_count": "1",
  "evidence_pointer": "A1 Phase-0-C; V5/V6 architecture",
  "spec_version": "v1",
  "status": "draft"
}
```
