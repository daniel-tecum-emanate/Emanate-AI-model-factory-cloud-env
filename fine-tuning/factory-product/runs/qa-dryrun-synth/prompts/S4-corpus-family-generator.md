# corpus-family-generator — Over-generate then deterministically select one risk-class family under the locked LAWS.

Node: S4:corpus-family-generator | run: dddddddd-0000-0000-0000-000000000001 | graph: fine-tune-factory-v1 | org: qa-dryrun-synth

## Read first
- the mixture plan's LAWS for this family
- the deterministic-generator precedent (L19) if this family has hard invariants

## Mandate
- over-generate ~2x, deterministically select the best half
  - STOP OR ASK: never hand-pick — selection must be reproducible by re-running the same script
- use a deterministic generator (not free-form) wherever a hard invariant exists (L19)
  - STOP OR ASK: ask before free-forming a slice with a hard invariant

## Laws (binding)
- single-writer discipline: own report/branch/worktree only; shared files route through the coordinator
- create the report file first (status header) before doing any work — crash/resume safety
- never interrupt mid-transfer; liveness is measured by report mtime, not chat presence
- records outrank this prompt — if a canon file and this spec disagree, the canon file wins and the disagreement is flagged, not silently resolved

## Done when
the self-audit is reproducible by an independent linter run

## Final response shape
headline (one line) + counts + one top finding/prediction, so the coordinator can triage without opening files (AGENT-CATALOG.md §A.10 / MULTI-AGENT-REVIEW-PLAYBOOK §2.7)