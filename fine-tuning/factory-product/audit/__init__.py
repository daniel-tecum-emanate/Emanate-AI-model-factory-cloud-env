"""data-audit swarm — Phase 0 deterministic checks (SWARM-PHASE0, 2026-07-31).

Design of record: factory-automation/data-audit-swarm/DESIGN.md (§9 Phase 0,
§6 blackboard/schema, §11 reconciliation with RESEARCH.md). Everything in this
package is deterministic, $0, zero-LLM (V-107) and read-only over corpora,
manifests, anchors and prod (SELECT via PostgREST only); the only write
surface is the per-run blackboard `runs/<slug>/audit/`.
"""
