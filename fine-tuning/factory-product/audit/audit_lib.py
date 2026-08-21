"""audit_lib — shared core of the data-audit swarm's deterministic checks.

Implements DESIGN.md §6 exactly:
  - the closed, versioned finding schema (§6.2, schema_version 1) with
    mechanical pointer validation (evidence entries are pointers, never
    payloads — law §5 lines 269-275);
  - deterministic finding ids: uuid5(NAMESPACE, "<run_id>:<check_id>:
    <evidence_sha256_12>") so a re-run upserts the same finding and a
    *changed* evidence hash mints a new one (§6.4 dedup-is-structural);
  - the verdict degradation lattice: any fail -> fail; any missing /
    unparseable / unknown -> suspect, NEVER pass (law §5 lines 215-219);
  - the append-only blackboard writers (findings.jsonl + status.jsonl —
    outcomes are second rows, never edits).

NAMESPACE is imported from backfill_ptc_history (never recomputed) — the
same rule ab_shadow_sync.py follows, so every factory projection shares one
uuid5 universe.
"""

import hashlib
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent            # fine-tuning/factory-product/audit/
FACTORY_DIR = HERE.parent                          # fine-tuning/factory-product/
WORKFLOW_ROOT = FACTORY_DIR.parent.parent          # emanate-tecum-workflow root

if str(FACTORY_DIR) not in sys.path:
    sys.path.insert(0, str(FACTORY_DIR))

from backfill_ptc_history import NAMESPACE  # noqa: E402

SCHEMA_VERSION = 1

AUDITORS = ("lineage", "parity", "comparator", "lesson-compiler", "efficiency")
CLASSES = ("contamination", "parity", "repeat-recipe", "unenforced-lesson",
           "efficiency", "fillability")
SEVERITIES = ("blocking", "warn", "info")
FINDING_VERDICTS = ("fail", "suspect", "info")
CHECK_VERDICTS = ("pass", "fail", "suspect")
STAGE_SCOPES = ("S4", "S5", "S6g", "S7", "standing")
STATUSES = ("open", "acknowledged", "resolved", "escalated", "waived")
ACTORS = ("human", "deterministic", "policy")

# Evidence entries: repo-relative pointer + optional #anchor. No whitespace,
# no newlines, bounded length, bounded count (law §5: pointers never payloads).
_POINTER_RE = re.compile(r"^[A-Za-z0-9(][A-Za-z0-9._/§#:@=,()+-]{0,199}$")
MAX_EVIDENCE_POINTERS = 8

_VERDICT_ORDER = {"pass": 0, "info": 0, "suspect": 1, "fail": 2}


class AuditSchemaError(ValueError):
    """Named, loud schema violation — never degrade to a warning."""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cjson(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


_GIT_SHA = None


def git_sha():
    """Short sha for `detected_by` provenance; 'unknown' when git is absent
    (the finding is still valid — provenance degrades, evidence doesn't)."""
    global _GIT_SHA
    if _GIT_SHA is None:
        try:
            _GIT_SHA = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=WORKFLOW_ROOT, capture_output=True, text=True, timeout=5,
            ).stdout.strip() or "unknown"
        except Exception:
            _GIT_SHA = "unknown"
    return _GIT_SHA


def evidence_sha12(evidence, counts, headline=""):
    """The identity of a finding's evidence: pointers + counts + the
    (deterministic) headline, canonical JSON, first 12 hex. Changed evidence
    => changed hash => NEW finding id, and the old one gets
    status-transitioned (§6.4). The headline is part of the identity because
    two DISTINCT violations can legitimately share pointers and counts
    (observed 2026-07-31: two marker findings collided to one id on the
    first live ptc-steel run)."""
    return hashlib.sha256(
        cjson({"evidence": sorted(evidence), "counts": counts,
               "headline": headline}).encode("utf-8")
    ).hexdigest()[:12]


def finding_id(run_id, check_id, sha12):
    return str(uuid.uuid5(NAMESPACE, f"{run_id}:{check_id}:{sha12}"))


def validate_pointer(pointer):
    if not isinstance(pointer, str) or not _POINTER_RE.match(pointer):
        raise AuditSchemaError(
            f"EVIDENCE-POINTER: {pointer!r} is not a pointer (regex shape, "
            "no whitespace/newlines, <=200 chars) — evidence carries "
            "pointers, never payloads")
    return pointer


def worst(verdicts):
    """The degradation lattice. Unknown / None / unparseable degrade to
    suspect — 'missing is suspect, never pass'."""
    result = "pass"
    for verdict in verdicts:
        if verdict not in _VERDICT_ORDER:
            verdict = "suspect"
        if _VERDICT_ORDER[verdict] > _VERDICT_ORDER[result]:
            result = "suspect" if verdict == "info" else verdict
    # info maps to weight 0 above; normalize back into the check vocabulary
    return result if result in CHECK_VERDICTS else "suspect"


def verdict_from_findings(findings):
    """A check's verdict from its own findings: any fail -> fail, any
    suspect -> suspect, info-only or none -> pass."""
    if any(f["verdict"] == "fail" for f in findings):
        return "fail"
    if any(f["verdict"] == "suspect" for f in findings):
        return "suspect"
    return "pass"


def make_finding(ctx, *, auditor, check_id, klass, severity, verdict,
                 stage_scope, headline, evidence, counts, lesson_refs=()):
    """One §6.2 finding, fully validated. Numbers live in `counts`, prose
    only in `headline` (single line, self-contained, counts included)."""
    if auditor not in AUDITORS:
        raise AuditSchemaError(f"auditor {auditor!r} not in {AUDITORS}")
    if klass not in CLASSES:
        raise AuditSchemaError(f"class {klass!r} not in {CLASSES}")
    if severity not in SEVERITIES:
        raise AuditSchemaError(f"severity {severity!r} not in {SEVERITIES}")
    if verdict not in FINDING_VERDICTS:
        raise AuditSchemaError(f"verdict {verdict!r} not in {FINDING_VERDICTS}")
    if stage_scope not in STAGE_SCOPES:
        raise AuditSchemaError(f"stage_scope {stage_scope!r} not in {STAGE_SCOPES}")
    if not headline or "\n" in headline:
        raise AuditSchemaError("headline must be one non-empty line")
    if not evidence or len(evidence) > MAX_EVIDENCE_POINTERS:
        raise AuditSchemaError(
            f"evidence must carry 1..{MAX_EVIDENCE_POINTERS} pointers")
    evidence = [validate_pointer(p) for p in evidence]
    if not isinstance(counts, dict) or not counts:
        raise AuditSchemaError(
            "counts must be a non-empty dict — counts, not exit codes (R4)")
    sha = evidence_sha12(evidence, counts, headline)
    return {
        "schema_version": SCHEMA_VERSION,
        "id": finding_id(ctx.run_id, check_id, sha),
        "run_id": ctx.run_id,
        "org_slug": ctx.slug,
        "iteration": ctx.iteration,
        "auditor": auditor,
        "check_id": check_id,
        "class": klass,
        "severity": severity,
        "verdict": verdict,
        "stage_scope": stage_scope,
        "headline": headline,
        "evidence": evidence,
        "counts": counts,
        "lesson_refs": list(lesson_refs),
        "detected_at": now_iso(),
        "detected_by": f"audit_run.py@{git_sha()}",
    }


def check_result(check_id, name, auditor, verdict, counts, findings, notes=()):
    """The per-check report the AG runner consumes. `counts` is mandatory
    and non-empty (R4: a check that scanned nothing must say so by name)."""
    if verdict not in CHECK_VERDICTS:
        raise AuditSchemaError(f"check verdict {verdict!r} not in {CHECK_VERDICTS}")
    if not isinstance(counts, dict) or not counts:
        raise AuditSchemaError(
            f"{check_id}: counts must be a non-empty dict (R4)")
    return {"check_id": check_id, "name": name, "auditor": auditor,
            "verdict": verdict, "counts": counts,
            "findings": list(findings), "notes": list(notes)}


# ── run context ──────────────────────────────────────────────────────────────
class RunContext:
    """Path/identity resolution for one org run. `factory_root` is
    overridable so planted-violation fixtures point at a tmp tree."""

    def __init__(self, slug, factory_root=None, iteration=None):
        self.slug = slug
        self.factory_root = Path(factory_root) if factory_root else FACTORY_DIR
        self.run_dir = self.factory_root / "runs" / slug
        self.corpus_dir = self.run_dir / "corpus"
        self.inputs_dir = self.corpus_dir / "inputs"
        self.build_dir = self.corpus_dir / "build"
        self.audit_dir = self.run_dir / "audit"
        self.config_path = self.factory_root / "configs" / f"{slug}.yaml"
        self.build_spec_path = self.corpus_dir / "BUILD-SPEC.md"
        self.exclusions_path = self.audit_dir / "exclusions.yaml"
        self.iterations_dir = self.run_dir / "iterations"
        self.run_id = self._resolve_run_id()
        self.iteration = iteration if iteration is not None else self._resolve_iteration()

    def _resolve_run_id(self):
        state = self.run_dir / ".sync_state.json"
        if state.exists():
            try:
                run_id = json.loads(state.read_text()).get("run_id")
                if run_id:
                    return run_id
            except (ValueError, OSError):
                pass
        # Deterministic placeholder: finding ids stay stable while the run
        # row is unresolved (a later resolve mints new ids — visible, not
        # silent — which is the correct behavior for identity changes).
        return f"unresolved-run:{self.slug}"

    def _resolve_iteration(self):
        """Latest iteration digest + 1 = the run being built. Digests record
        COMPLETED iterations; the active run is their successor."""
        best = None
        if self.iterations_dir.is_dir():
            for path in self.iterations_dir.glob("*.json"):
                try:
                    it = json.loads(path.read_text()).get("iteration")
                except (ValueError, OSError):
                    continue
                if isinstance(it, int) and (best is None or it > best):
                    best = it
        return (best + 1) if best is not None else None

    def rel(self, path):
        """Repo-relative pointer for evidence entries. Fixture trees outside
        the repo relativize against their own factory root instead, so the
        pointer regex holds everywhere."""
        resolved = Path(path).resolve()
        for base in (WORKFLOW_ROOT, self.factory_root.resolve().parent,
                     self.factory_root.resolve()):
            try:
                return str(resolved.relative_to(base))
            except ValueError:
                continue
        return str(resolved)


# ── shared input loaders (tolerant: missing input => the CALLER degrades
#    to suspect with a named reason — never crashes the whole hook) ──────────
def load_json_or_none(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return None


def load_yaml_or_none(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        import yaml
        return yaml.safe_load(path.read_text())
    except Exception:
        return None


def normalize_pool_rows(raw):
    """Both recorded pool shapes -> [{id, night, class, account_id,
    trajectory_id, read_tools}]. Run-independent (the audit never imports a
    run's own corpus code — it audits it)."""
    if raw is None:
        return None
    rows = []
    if "rows" in raw:
        src = raw["rows"]
        for r in src:
            rows.append({
                "id": r.get("row_id") or r.get("id"),
                "night": r.get("night"),
                "class": r.get("class"),
                "account_id": r.get("account_id"),
                "trajectory_id": r.get("trajectory_id"),
                "read_tools": r.get("tools_called") or r.get("read_tools") or [],
            })
        return rows
    if "action_pool" in raw or "no_action_readchain_pool" in raw:
        for r in (raw.get("action_pool") or []) + (raw.get("no_action_readchain_pool") or []):
            rows.append({
                "id": r.get("id"),
                "night": r.get("night"),
                "class": r.get("class"),
                "account_id": r.get("account_id"),
                "trajectory_id": r.get("trajectory_id"),
                "read_tools": r.get("read_tools") or [],
            })
        return rows
    return None


def load_exclusions(ctx):
    """The run's exclusion-list input (runs/<slug>/audit/exclusions.yaml) —
    an INPUT FILE the checks consume, never a constant (DESIGN §4 A1)."""
    return load_yaml_or_none(ctx.exclusions_path)


def load_corpus_meta(ctx):
    """Every assembled corpus record's meta line, across all build outputs.
    Returns None when no corpus has been assembled yet."""
    if not ctx.build_dir.is_dir():
        return None
    metas = []
    paths = sorted(ctx.build_dir.glob("*.meta.jsonl"))
    if not paths:
        return None
    for path in paths:
        for line in path.read_text().splitlines():
            if line.strip():
                metas.append(json.loads(line))
    return metas


# ── blackboard (append-only; one JSON object per line) ───────────────────────
def _read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _append_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(cjson(row) + "\n")


def load_findings(audit_dir):
    return _read_jsonl(Path(audit_dir) / "findings.jsonl")


def load_statuses(audit_dir):
    return _read_jsonl(Path(audit_dir) / "status.jsonl")


def latest_status_by_finding(audit_dir):
    """Latest status per finding id. A finding with no status row is open
    (audit_run writes an explicit open row on first sight anyway)."""
    latest = {}
    for row in load_statuses(audit_dir):
        latest[row["finding_id"]] = row["status"]
    return latest


def append_findings(audit_dir, findings):
    """Dedup is structural: ids already on the blackboard are skipped, new
    ids appended — and the batch itself is deduped, so one emission can
    never write the same id twice. Returns (new_findings, existing_ids)."""
    existing = {f["id"] for f in load_findings(audit_dir)}
    new = []
    seen = set()
    for f in findings:
        if f["id"] in existing or f["id"] in seen:
            continue
        seen.add(f["id"])
        new.append(f)
    _append_jsonl(Path(audit_dir) / "findings.jsonl", new)
    return new, existing


def append_statuses(audit_dir, rows):
    for row in rows:
        if row["status"] not in STATUSES:
            raise AuditSchemaError(f"status {row['status']!r} not in {STATUSES}")
        if row["actor"] not in ACTORS:
            raise AuditSchemaError(f"actor {row['actor']!r} not in {ACTORS}")
        if row["actor"] != "human" and row["status"] == "waived":
            raise AuditSchemaError(
                "a waived status requires a HUMAN actor (§6.2)")
    _append_jsonl(Path(audit_dir) / "status.jsonl", rows)


def status_row(finding_id_, status, actor, note):
    return {"finding_id": finding_id_, "status": status, "actor": actor,
            "note": note, "ts": now_iso()}
