"""Independent adversarial QA pass on the T11-T15 sync sidecar (model-factory-v1).

Written by a separate QA session, not the implementer, per the house's "fresh,
skeptical eyes" review process. Covers gaps the T11/T13/T15 suites do not:

  - PII allow-list robustness beyond the one fixture already tested
    (project_stage_report / _redact_path / _aggregate_ledger)
  - unicode/emoji/very-long-string round-trips against the REAL local
    Supabase REST endpoint (not mocked)
  - push_gate_request idempotency under a genuine concurrent race, against
    the REAL local Supabase (not mocked)
  - resolve_gate's fail-closed guarantee against HTTP response shapes beyond
    what test_sync_gates.py already covers
  - specs_sync malformed-file shapes beyond what test_specs_sync.py covers
  - a real end-to-end push_stage/push_gate_request/push_agents flow verified
    directly via psql, not just via the REST layer's own response

Real-Supabase tests skip cleanly (not fail) if no local Supabase REST
endpoint is reachable, so this file is safe to run standalone in any
environment. Every row this file writes uses a fresh `qa-adversarial-<hex>`
org slug, generated once per test-session run (see `QA_SLUG` below), so
re-running this file never collides with a previous run's rows and never
touches any other suite's or org's data. This file never DELETEs or
TRUNCATEs anything — QA rows are intentionally left in place (cheap,
obviously-fake-slugged, harmless on a local/disposable Supabase instance).

UPDATE (2026-07-23, same-session follow-up): 4 of the 5 genuine bugs this file
found were fixed directly in `factory_sync.py`/`supabase_rest.py` (see
`factory-automation/product-integration/DELTAS.md` D19 for the full
finding/fix/verification) and their tests below now assert the FIXED
behavior directly (no more `xfail`). One is a documented, ACCEPTED residual
risk rather than a fix — see
`test_redact_path_worktree_lookalike_directory_is_an_accepted_residual_gap_not_a_bug_fix`
(renamed from the original bug-report test) and DELTAS.md D19/F7 for why: it
is structurally unsolvable by a pure string-prefix allow-list without a real
worktree-name registry, which is a bigger design call than this pass makes
alone. Similarly, `test_unstructured_customer_name_inside_an_allow_listed_value_is_still_NOT_caught`
documents the one PII sub-case (an unstructured name, as opposed to a
structured email/phone/SSN) that remains genuinely un-catchable by a
mechanical filter — kept as `xfail(strict=True)` permanently, not because
it's an open bug to fix, but as a living regression-proof that nobody should
ever claim this module does NLP-level name detection.
"""

import concurrent.futures
import shutil
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

HERE = Path(__file__).resolve().parent
FACTORY_DIR = HERE.parent
sys.path.insert(0, str(FACTORY_DIR))

import factory_sync  # noqa: E402
import specs_sync  # noqa: E402
from supabase_rest import SupabaseRestError  # noqa: E402

REAL_SUPABASE_URL = "http://127.0.0.1:54321"
REAL_SERVICE_ROLE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vf"
    "cm9sZSIsImV4cCI6MTk4MzgxMjk5Nn0.EGIM96RAZx35lJzdJsyH-qQwv8Hdp7fsn3W0YpN81IU"
)
REAL_DB_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"

# Fresh per test-session run — never reused, never collides with a prior run.
QA_SLUG = f"qa-adversarial-{uuid.uuid4().hex[:10]}"


def _supabase_reachable():
    try:
        resp = requests.get(
            f"{REAL_SUPABASE_URL}/rest/v1/",
            headers={"apikey": REAL_SERVICE_ROLE_KEY},
            timeout=8,
        )
        return resp.status_code < 500
    except requests.RequestException:
        return False


requires_real_supabase = pytest.mark.skipif(
    not _supabase_reachable(),
    reason="local Supabase REST endpoint not reachable at 127.0.0.1:54321 — skipping real-endpoint QA tests",
)


@pytest.fixture(autouse=True)
def isolated_run_state(tmp_path, monkeypatch):
    """Every test in this file uses its own scratch `runs/` dir so
    `get_or_create_run_id`'s local state file never collides across tests
    or with a real developer's `runs/` tree.
    """
    monkeypatch.setattr(factory_sync, "HERE", tmp_path)
    yield


def _mock_response(ok=True, status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status_code
    resp.text = text
    resp.json = MagicMock(return_value=json_data if json_data is not None else [])
    return resp


# =============================================================================
# 1. PII allow-list robustness beyond the one fixture in test_sync_pii.py
# =============================================================================


def test_email_address_under_an_unallowed_key_is_dropped():
    dirty = {
        "status": "ok",
        "customer_email": "jane.doe@acme-corp-real.com",
        "notification_recipients": ["ops@acme-corp-real.com"],
    }
    projected = factory_sync.project_stage_report(dirty)
    assert "jane.doe@acme-corp-real.com" not in str(projected)
    assert projected == {"status": "ok"}


def test_phone_number_under_an_unallowed_key_is_dropped():
    dirty = {"status": "ok", "contact_phone": "+1 (555) 019-2837", "escalation_contact": "555-019-2837"}
    projected = factory_sync.project_stage_report(dirty)
    assert "555-019-2837" not in str(projected)
    assert "5550192837" not in str(projected).replace(" ", "").replace("-", "").replace("(", "").replace(")", "")


def test_file_path_embedding_ssn_shaped_number_under_an_unallowed_key_is_dropped():
    dirty = {
        "status": "ok",
        "export_manifest": {"path": "/exports/acme-corp/customer-ssn-078-05-1120-w9.pdf"},
    }
    projected = factory_sync.project_stage_report(dirty)
    assert "078-05-1120" not in str(projected)
    assert projected == {"status": "ok"}


def test_freeform_notes_field_with_customer_name_mid_sentence_is_dropped():
    """A 'notes' key isn't in SAFE_REPORT_KEYS at all, so a customer's real
    name embedded mid-sentence in free text under that key must be dropped
    wholesale, same as any other unrecognized key — not because the name is
    detected, but because the whole key never survives the allow-list.
    """
    dirty = {
        "status": "ok",
        "notes": "Quick call with Priya Raghunathan today, she confirmed Meridian Steel Corp wants the retrain by Friday.",
    }
    projected = factory_sync.project_stage_report(dirty)
    assert "Priya Raghunathan" not in str(projected)
    assert "Meridian Steel Corp" not in str(projected)
    assert projected == {"status": "ok"}


def test_structured_pii_inside_an_allow_listed_keys_value_is_now_scrubbed():
    """FIXED (was a genuine bug — DELTAS.md D19): SAFE_REPORT_KEYS is a
    top-level-KEY allow-list only — it never inspected the *value* under an
    allow-listed key. `decision_request`/`recommendation`/`checklist`/
    `findings` are all allow-listed and documented (DELTAS.md D14) as "prose
    about methodology," but nothing stopped a human-authored free-text field
    inside one of them from embedding a real email/phone/SSN, and it sailed
    through byte-for-byte unchanged. `project_stage_report` now runs every
    surviving value through `_scrub_structured_pii` (a recursive regex pass
    for email/phone/SSN shapes specifically) as a second layer after the
    key-level allow-list. See the next test for the one sub-case (an
    unstructured NAME, as opposed to these structured shapes) this second
    layer deliberately does NOT and cannot mechanically catch.
    """
    dirty = {
        "status": "ok",
        "decision_request": {
            "recommendation": (
                "approve for Jane Doe (jane.doe@acme-corp-real.com, 555-019-2837), "
                "SSN 078-05-1120, per her verbal confirmation on the call"
            ),
            "cost": {"total_projection_usd": 14.2},
        },
    }
    projected = factory_sync.project_stage_report(dirty)
    dumped = str(projected)
    leaked = [s for s in ("jane.doe@acme-corp-real.com", "555-019-2837", "078-05-1120") if s in dumped]
    assert not leaked, f"structured PII (email/phone/SSN) survived the value-level scrub: {leaked!r} — payload: {dumped}"
    assert projected["decision_request"]["cost"]["total_projection_usd"] == 14.2, "non-PII values must survive unchanged"


def test_unstructured_customer_name_inside_an_allow_listed_value_is_still_NOT_caught():
    """ACCEPTED RESIDUAL RISK, not a bug to fix (DELTAS.md D19): the
    structured-PII scrub above catches email/phone/SSN shapes via regex, but
    a bare customer/contact NAME embedded in free-text prose (no distinctive
    structure to pattern-match on) is fundamentally an NLP problem, not a
    mechanical one — no regex can reliably distinguish "Jane Doe" from any
    other two-capitalized-words phrase without false-positiving constantly on
    legitimate prose. This is kept as a permanent `xfail(strict=True)`
    specifically so nobody can accidentally believe this module does
    name-level PII detection — the real mitigation is `factory.py` itself
    (or whatever writes into an allow-listed prose field) never putting a
    customer name there to begin with, per PLAN.md §2.10's own framing of
    these fields as "counts, verdicts, cost... methodology prose."
    """
    dirty = {
        "status": "ok",
        "decision_request": {"recommendation": "approve for Jane Doe, per her verbal confirmation on the call"},
    }
    projected = factory_sync.project_stage_report(dirty)
    assert "Jane Doe" not in str(projected), "if this ever passes, some future scrub started catching names too — see docstring"


test_unstructured_customer_name_inside_an_allow_listed_value_is_still_NOT_caught = pytest.mark.xfail(
    strict=True,
    reason=(
        "ACCEPTED RESIDUAL RISK, not an open bug: an unstructured name embedded in allow-listed "
        "prose cannot be mechanically distinguished from ordinary text by a regex-based scrub. "
        "See docstring and DELTAS.md D19."
    ),
)(test_unstructured_customer_name_inside_an_allow_listed_value_is_still_NOT_caught)


def test_redact_path_path_traversal_no_longer_bypasses_the_prefix_check():
    """FIXED (was a genuine bug — DELTAS.md D19): `_redact_path` used to do a
    naive `str.startswith(prefix)` check with no path normalization. A path
    containing '../' that LEXICALLY starts with a safe prefix but actually
    resolves outside it (e.g. escaping `factory-automation/` into a sibling
    `clients/` directory) was treated as safe and passed through unredacted.
    `_redact_path` now normalizes via `posixpath.normpath` first and rejects
    anything that still starts with '..' after normalization.
    """
    traversal_path = "factory-automation/../clients/acme-corp-real/customer-dossier.pdf"
    result = factory_sync._redact_path(traversal_path)
    assert result == factory_sync._REDACTED_PATH, (
        f"path traversal escaped the SAFE_PATH_PREFIXES check unredacted: {result!r}"
    )


def test_redact_path_worktree_lookalike_directory_is_an_accepted_residual_gap_not_a_bug_fix():
    """ACCEPTED RESIDUAL RISK, not a bug this pass fixes (DELTAS.md D19/F7):
    `_redact_path` now requires a real `/`, `-`, or end-of-string boundary
    right after "platform-alpha" (closing the "no boundary at all" case —
    a contiguous string like "platform-alphaXYZ" with no separator is
    correctly redacted). But this repo's OWN real worktree convention is
    `platform-alpha-<slug>` with a HYPHEN separator (e.g.
    `platform-alpha-model-factory-v1`, `platform-alpha-v5`) — free-text
    slugs mean this cannot mechanically distinguish a legitimate worktree
    path from a directory deliberately named to look like one. Fully closing
    this would need a real worktree-name registry (e.g. cross-checked
    against `git worktree list` or `PROJECTS.md`) — a bigger design change
    than this bounded QA-fix pass makes alone (flagged, not decided, per
    house process). Kept `xfail(strict=True)` permanently as documentation
    of the accepted tradeoff, not as an open TODO.
    """
    lookalike_path = "platform-alpha-jane-doe-account-export/customer-notes.txt"
    result = factory_sync._redact_path(lookalike_path)
    assert result == factory_sync._REDACTED_PATH, (
        f"a path sharing the 'platform-alpha-<slug>' worktree SHAPE (not necessarily a real worktree) "
        f"was treated as safe — see docstring for why this is an accepted tradeoff, not a bug: {result!r}"
    )


test_redact_path_worktree_lookalike_directory_is_an_accepted_residual_gap_not_a_bug_fix = pytest.mark.xfail(
    strict=True,
    reason=(
        "ACCEPTED RESIDUAL RISK, not an open bug: this repo's real worktree convention "
        "(platform-alpha-<free-text-slug>) is lexically indistinguishable from an adversarially-named "
        "lookalike directory without a real worktree-name registry. See docstring and DELTAS.md D19/F7."
    ),
)(test_redact_path_worktree_lookalike_directory_is_an_accepted_residual_gap_not_a_bug_fix)


def test_redact_path_contiguous_no_separator_lookalike_is_now_redacted():
    """Regression-proof for the part of the boundary bug that WAS fully
    fixed: a string that shares the "platform-alpha" prefix with literally no
    separator at all (not even a hyphen) has no plausible legitimate
    worktree interpretation and must be redacted.
    """
    assert factory_sync._redact_path("platform-alphaXYZsomethingelse/file.txt") == factory_sync._REDACTED_PATH


def test_redact_path_real_worktree_paths_still_pass_through_unredacted():
    """Regression-proof that the boundary fix didn't break the common,
    legitimate case this repo relies on every day: `files_touched` for
    ordinary PR work happening inside a `platform-alpha-<slug>` worktree
    (e.g. this very PR's own `platform-alpha-model-factory-v1` worktree)
    must still pass through unredacted, same as a plain `platform-alpha/`
    path.
    """
    assert factory_sync._redact_path("platform-alpha/src/features/foo.tsx") == "platform-alpha/src/features/foo.tsx"
    assert (
        factory_sync._redact_path("platform-alpha-model-factory-v1/src/features/model-factory/actions/access.ts")
        == "platform-alpha-model-factory-v1/src/features/model-factory/actions/access.ts"
    )


def test_aggregate_ledger_unicode_emoji_and_very_long_path_do_not_crash():
    """_aggregate_ledger must handle unicode/emoji file paths and very long
    (10,000+ char) path strings without crashing or corrupting the dedup
    logic, regardless of whether they end up redacted or not.
    """
    long_suffix = "x" * 10_000
    events = [
        {
            "session_id": "sess-unicode",
            "event": "tool",
            "files": [
                f"factory-automation/factory/runs/qa/emoji-report-🚀🔥📊.json",
                f"factory-automation/factory/runs/qa/{long_suffix}.json",
                "factory-automation/factory/runs/qa/ünïcödé-namé-测试文件.json",
            ],
        }
    ]
    files, commands = factory_sync._aggregate_ledger(events, "sess-unicode")
    assert commands == 1
    assert len(files) == 3
    assert any("🚀🔥📊" in f for f in files)
    assert any(len(f) > 10_000 for f in files)
    assert any("测试文件" in f for f in files)


def test_every_allow_listed_key_still_excludes_the_known_risk_vectors_and_a_few_more():
    """Extends test_sync_pii.py's own guard test with a few more plausible
    future free-text key names that would be just as risky as stdout_tail if
    someone ever widened the allow-list carelessly.
    """
    forbidden_keys = {
        "stdout_tail",
        "stderr_tail",
        "command",
        "cwd",
        "inputs",
        "cross_check",
        "raw_output",
        "transcript",
        "customer_notes",
        "account_details",
        "email",
        "phone",
    }
    assert not (factory_sync.SAFE_REPORT_KEYS & forbidden_keys)


# =============================================================================
# 2. Unicode / emoji / very-long-string round-trips against the REAL local
#    Supabase REST endpoint (not mocked)
# =============================================================================


@requires_real_supabase
def test_unicode_emoji_and_10k_char_report_round_trips_through_real_supabase():
    slug = f"{QA_SLUG}-unicode"
    cfg = {"org": {"id": None, "name": "QA Adversarial Unicode Org 🏭"}, "champion": {"model_id": None}}
    long_text = "A" * 12_000
    report = {
        "status": "ok",
        "dry_run": True,
        "recommendation": f"emoji test 🚀🔥📊 ünïcödé 测试 {long_text}",
        "findings": {"note": "normal ascii is fine here too"},
    }
    ok = factory_sync.push_stage(
        cfg, slug, "census", report, url=REAL_SUPABASE_URL, service_role_key=REAL_SERVICE_ROLE_KEY
    )
    assert ok is True

    run_id = factory_sync.get_or_create_run_id(slug)
    rows = factory_sync.select(
        "factory_run_stages",
        params={"run_id": f"eq.{run_id}", "stage": "eq.S1", "select": "report"},
        url=REAL_SUPABASE_URL,
        service_role_key=REAL_SERVICE_ROLE_KEY,
    )
    assert len(rows) == 1
    stored_recommendation = rows[0]["report"]["recommendation"]
    assert stored_recommendation == report["recommendation"], "unicode/emoji/long-string round-trip corrupted"
    assert len(stored_recommendation) == len(report["recommendation"]), "length mismatch — possible truncation"


@requires_real_supabase
def test_unicode_and_emoji_gate_decision_request_round_trips_through_real_supabase():
    slug = f"{QA_SLUG}-unicode-gate"
    cfg = {"org": {"id": None, "name": "QA Adversarial Unicode Gate Org"}, "champion": {"model_id": None}}
    factory_sync.push_stage(
        cfg, slug, "governance", {"status": "gate_pending"}, url=REAL_SUPABASE_URL, service_role_key=REAL_SERVICE_ROLE_KEY
    )
    decision_request = {
        "recommendation": "approve 🚀 — see ünïcödé notes 测试文件",
        "assumptions": ["emoji stress test 🔥📊✅"],
        "cost": {"total_projection_usd": 1.23},
    }
    result = factory_sync.push_gate_request(
        cfg, slug, "governance", decision_request, url=REAL_SUPABASE_URL, service_role_key=REAL_SERVICE_ROLE_KEY
    )
    assert result["status"] == "pending"
    run_id = factory_sync.get_or_create_run_id(slug)
    rows = factory_sync.select(
        "factory_gate_requests",
        params={"run_id": f"eq.{run_id}", "gate": "eq.S2", "select": "decision_request"},
        url=REAL_SUPABASE_URL,
        service_role_key=REAL_SERVICE_ROLE_KEY,
    )
    assert rows[0]["decision_request"] == decision_request


@requires_real_supabase
def test_unicode_agent_title_and_10k_char_final_response_round_trip_through_real_supabase():
    slug = f"{QA_SLUG}-unicode-agents"
    cfg = {"org": {"id": None, "name": "QA Adversarial Unicode Agents Org"}, "champion": {"model_id": None}}
    factory_sync.push_stage(
        cfg, slug, "build", {"status": "ok"}, url=REAL_SUPABASE_URL, service_role_key=REAL_SERVICE_ROLE_KEY
    )
    run_id = factory_sync.get_or_create_run_id(slug)

    long_response = "done. " + ("z" * 10_500)
    heartbeat_md = textwrap.dedent(
        f"""\
        # Heartbeat

        ## Active Sessions
        _Register on boot._

        | session | goal | allowed_paths | risk | status | seq | last_update |
        |---------|------|---------------|------|--------|-----|-------------|
        | QA-🚀-BUILD | {slug} unicode build test 测试 | factory-automation/factory/runs/{slug}/** | code | ENDED | 1 | 2026-07-23 10:00 PT |
        """
    )
    ok = factory_sync.push_agents(
        cfg,
        slug,
        run_id,
        heartbeat_md,
        final_responses={"QA-🚀-BUILD": long_response},
        url=REAL_SUPABASE_URL,
        service_role_key=REAL_SERVICE_ROLE_KEY,
    )
    assert ok is True

    rows = factory_sync.select(
        "factory_agents",
        params={"run_id": f"eq.{run_id}", "select": "title,final_response"},
        url=REAL_SUPABASE_URL,
        service_role_key=REAL_SERVICE_ROLE_KEY,
    )
    assert len(rows) == 1
    assert rows[0]["title"] == "QA-🚀-BUILD"
    assert rows[0]["final_response"] == long_response
    assert len(rows[0]["final_response"]) == len(long_response), "final_response truncated on write/read"


# =============================================================================
# 3. push_gate_request idempotency under a genuine concurrent race, against
#    the REAL local Supabase (not mocked)
# =============================================================================


@requires_real_supabase
def test_push_gate_request_race_never_produces_two_rows_for_the_same_run_and_gate():
    """The partial unique index (run_id, gate) WHERE status='pending' is the
    actual data-integrity backstop here — confirm it holds even when 8
    threads hit push_gate_request for the identical (run_id, gate) pair at
    once: exactly one row must exist afterward, queried back for real.
    """
    slug = f"{QA_SLUG}-race"
    cfg = {"org": {"id": None, "name": "QA Adversarial Race Org"}, "champion": {"model_id": None}}
    factory_sync.push_stage(
        cfg, slug, "governance", {"status": "gate_pending"}, url=REAL_SUPABASE_URL, service_role_key=REAL_SERVICE_ROLE_KEY
    )
    run_id = factory_sync.get_or_create_run_id(slug)  # pre-warm local state so threads don't race the JSON file
    decision_request = {"recommendation": "approve", "cost": {"total_projection_usd": 1.0}}

    def worker(_i):
        try:
            factory_sync.push_gate_request(
                cfg, slug, "governance", decision_request, url=REAL_SUPABASE_URL, service_role_key=REAL_SERVICE_ROLE_KEY
            )
            return None
        except SupabaseRestError as exc:
            return exc

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(worker, range(8)))

    rows = factory_sync.select(
        "factory_gate_requests",
        params={"run_id": f"eq.{run_id}", "gate": "eq.S2", "select": "id,status"},
        url=REAL_SUPABASE_URL,
        service_role_key=REAL_SERVICE_ROLE_KEY,
    )
    assert len(rows) == 1, f"expected exactly 1 gate_requests row after the race, found {len(rows)}: {rows}"
    assert rows[0]["status"] == "pending"


def test_push_gate_request_no_longer_raises_under_a_concurrent_race():
    """FIXED (was a genuine bug — DELTAS.md D19): `push_gate_request`'s own
    docstring promises "if one already exists (pending OR resolved), this is
    a no-op" — but its select-then-insert was NOT atomic. Under a real
    concurrent race (proven against the actual local Supabase in
    `test_push_gate_request_race_never_produces_two_rows_for_the_same_run_and_gate`
    above — data integrity DOES hold, thanks to the DB's own partial unique
    index), every caller except the winner used to get a raw
    `SupabaseRestError` (Postgres 23505 duplicate key) raised out of
    `push_gate_request` itself instead of a graceful "here's the existing
    row" return. `push_gate_request` now catches its own insert's
    unique-violation and re-selects/returns the winning row instead of
    propagating — this test uses mocks (not real Supabase) to force every
    concurrent caller through that exact code path deterministically.
    """
    slug_state = {}

    def fake_select(table, params=None, url=None, service_role_key=None):
        # Simulates: nothing exists yet during the race window, but a losing
        # caller's re-select (inside push_gate_request's own except-clause,
        # post-fix) finds the winner's row once it has "committed".
        key = (params.get("run_id"), params.get("gate"))
        if key in slug_state:
            return [{"id": "winner-id", "status": "pending"}]
        return []

    def fake_insert(table, rows, url=None, service_role_key=None):
        # Simulates Postgres's real 23505 on the second-and-later concurrent insert.
        key = (f"eq.{rows[0]['run_id']}", f"eq.{rows[0]['gate']}")
        if key in slug_state:
            raise SupabaseRestError(
                f'insert {table} failed: 409 duplicate key value violates unique constraint "uq_factory_gate_requests_open"'
            )
        slug_state[key] = rows[0]
        return [dict(rows[0], id="winner-id", status="pending")]

    cfg = {"org": {"name": "X"}}
    decision_request = {"recommendation": "approve"}
    errors = []
    with patch.object(factory_sync, "select", side_effect=fake_select), patch.object(
        factory_sync, "insert", side_effect=fake_insert
    ):
        # Pre-warm the run_id, same as the real-Supabase race test above, so
        # threads race push_gate_request's own select-then-insert (what this
        # test targets) rather than incidentally also racing
        # get_or_create_run_id's local JSON-file read/write (a separate,
        # not-yet-examined concern this test isn't about).
        factory_sync.get_or_create_run_id("race-org")
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            futures = [
                ex.submit(factory_sync.push_gate_request, cfg, "race-org", "governance", decision_request)
                for _ in range(6)
            ]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    fut.result()
                except SupabaseRestError as exc:
                    errors.append(exc)

    assert not errors, (
        f"push_gate_request raised for {len(errors)}/6 concurrent callers instead of idempotently "
        f"no-op'ing as its own docstring promises: {errors!r}"
    )


# =============================================================================
# 4. resolve_gate's fail-closed guarantee against more HTTP response shapes
# =============================================================================


@pytest.fixture
def gate_cfg():
    return {"org": {"id": "x", "name": "X"}, "champion": {"model_id": None}}


DECISION_REQUEST = {"recommendation": "approve"}


MOCK_URL = "http://mock-test-supabase.invalid"
MOCK_KEY = "mock-service-role-key"


def _resolve_with_mocked_poll_response(cfg, mock_get):
    """NOTE: `url`/`service_role_key` must be passed explicitly here — without
    them, `supabase_rest._resolve_transport` raises `SupabaseRestError` BEFORE
    `requests.get` is ever reached (since neither direct nor relay credentials
    are set in the test environment), which would make every test below pass
    for the wrong reason (fails closed because it's unconfigured, not because
    it actually exercised the mocked HTTP response shape under test).
    """
    with patch.object(factory_sync, "push_gate_request", return_value={"status": "pending"}), patch(
        "supabase_rest.requests.get", mock_get
    ):
        return factory_sync.resolve_gate(
            cfg, "synthetic-test-org", "governance", DECISION_REQUEST, url=MOCK_URL, service_role_key=MOCK_KEY
        )


def test_resolve_gate_fails_closed_on_200_with_empty_list(gate_cfg):
    mock_get = MagicMock(return_value=_mock_response(ok=True, status_code=200, json_data=[]))
    assert _resolve_with_mocked_poll_response(gate_cfg, mock_get) is False


def test_resolve_gate_fails_closed_on_status_wrong_case_APPROVED(gate_cfg):
    mock_get = MagicMock(return_value=_mock_response(ok=True, status_code=200, json_data=[{"status": "APPROVED"}]))
    assert _resolve_with_mocked_poll_response(gate_cfg, mock_get) is False


def test_resolve_gate_fails_closed_on_status_null(gate_cfg):
    mock_get = MagicMock(return_value=_mock_response(ok=True, status_code=200, json_data=[{"status": None}]))
    assert _resolve_with_mocked_poll_response(gate_cfg, mock_get) is False


def test_resolve_gate_fails_closed_on_200_with_status_approved_but_truthy_typo_Approved(gate_cfg):
    mock_get = MagicMock(return_value=_mock_response(ok=True, status_code=200, json_data=[{"status": "Approved"}]))
    assert _resolve_with_mocked_poll_response(gate_cfg, mock_get) is False


def test_resolve_gate_fails_closed_on_http_500(gate_cfg):
    mock_get = MagicMock(return_value=_mock_response(ok=False, status_code=500, text="Internal Server Error"))
    assert _resolve_with_mocked_poll_response(gate_cfg, mock_get) is False


def test_resolve_gate_fails_closed_on_http_403_forbidden(gate_cfg):
    mock_get = MagicMock(return_value=_mock_response(ok=False, status_code=403, text="Forbidden"))
    assert _resolve_with_mocked_poll_response(gate_cfg, mock_get) is False


def test_resolve_gate_fails_closed_on_timeout(gate_cfg):
    mock_get = MagicMock(side_effect=requests.exceptions.Timeout("connect timed out"))
    assert _resolve_with_mocked_poll_response(gate_cfg, mock_get) is False


def test_resolve_gate_fails_closed_on_connection_error(gate_cfg):
    mock_get = MagicMock(side_effect=requests.exceptions.ConnectionError("connection refused"))
    assert _resolve_with_mocked_poll_response(gate_cfg, mock_get) is False


def test_resolve_gate_fails_closed_on_malformed_json_body(gate_cfg):
    """FIXED (was a genuine bug — DELTAS.md D19): `supabase_rest.select()` did
    NOT wrap `resp.json()` in a try/except (unlike `upsert`/`insert`/
    `update`, which all have `except ValueError: return []`) — a malformed
    JSON body from the poll GET propagated as a raw `ValueError` that neither
    `poll_gate_status` nor `resolve_gate` caught, violating `resolve_gate`'s
    own documented contract ("every other outcome ... returns False"). This
    was mitigated in production today only because `factory.py`'s one real
    call site (`_gate_approved_via_sync`) separately wraps the call in a
    broad `except Exception` — but `resolve_gate`'s OWN contract was still
    broken for any other/future direct caller. `select()` now mirrors
    `upsert`/`insert`/`update`'s existing `except ValueError: return []`
    pattern, so a malformed poll body now surfaces as "no rows" and
    `resolve_gate` fails closed on its own, without relying on a caller's
    broad exception handling as a safety net.
    """
    resp = _mock_response(ok=True, status_code=200)
    resp.json = MagicMock(side_effect=ValueError("not valid json"))
    mock_get = MagicMock(return_value=resp)
    with patch.object(factory_sync, "push_gate_request", return_value={"status": "pending"}), patch(
        "supabase_rest.requests.get", mock_get
    ):
        result = factory_sync.resolve_gate(
            gate_cfg, "synthetic-test-org", "governance", DECISION_REQUEST, url=MOCK_URL, service_role_key=MOCK_KEY
        )
    assert result is False


def test_resolve_gate_fails_closed_on_200_with_extra_unexpected_status_value(gate_cfg):
    """A status value outside the documented enum entirely (schema drift /
    a future status the client doesn't know about yet) must still fail
    closed, not be treated as approved by accident.
    """
    mock_get = MagicMock(return_value=_mock_response(ok=True, status_code=200, json_data=[{"status": "escalated"}]))
    assert _resolve_with_mocked_poll_response(gate_cfg, mock_get) is False


# =============================================================================
# 5. specs_sync.py — malformed-file shapes beyond test_specs_sync.py
# =============================================================================


def test_spec_file_where_top_level_yaml_is_a_list_not_a_mapping_fails_loudly(tmp_path):
    """Already has a defensive check in _load_spec (`if not isinstance(data, dict)`)
    but test_specs_sync.py never actually exercises it — closing that gap.
    """
    bad = tmp_path / "list-not-mapping.yaml"
    bad.write_text("- slug: list-not-mapping\n- role_family: reader\n")
    with pytest.raises(specs_sync.SpecSyncError, match="not a mapping"):
        specs_sync.build_spec_rows(paths=[bad])


def test_spec_with_done_when_present_but_empty_string_fails_loudly(tmp_path):
    """`done_when: ""` is present as a key but falsy — must be treated the
    same as a missing field (a NOT NULL column with meaningless empty
    content is just as dangerous as an absent one), not silently accepted
    as "technically present."
    """
    spec = tmp_path / "empty-done-when.yaml"
    spec.write_text(
        textwrap.dedent(
            """\
            slug: empty-done-when
            role_family: reader
            mission: does something
            done_when: ""
            """
        )
    )
    with pytest.raises(specs_sync.SpecSyncError, match="missing required field"):
        specs_sync.build_spec_rows(paths=[spec])


def test_spec_with_null_mission_fails_loudly_not_treated_as_present(tmp_path):
    """A YAML explicit `null` (parses to Python None) under a required field
    must also fail loudly — `data.get(field)` is falsy for None too, so this
    should already work, but it's untested and easy to regress if `_load_spec`
    ever switches to an `field in data` presence check instead of a truthiness
    check.
    """
    spec = tmp_path / "null-mission.yaml"
    spec.write_text(
        textwrap.dedent(
            """\
            slug: null-mission
            role_family: reader
            mission: null
            done_when: something
            """
        )
    )
    with pytest.raises(specs_sync.SpecSyncError, match="missing required field"):
        specs_sync.build_spec_rows(paths=[spec])


def test_spec_file_with_unicode_and_emoji_content_parses_and_pushes_cleanly(tmp_path):
    """Not a failure case — confirms specs_sync doesn't choke on legitimate
    unicode/emoji content in a spec's free-text fields (mission statements
    are human-written prose, plausibly containing non-ASCII text).
    """
    spec = tmp_path / "unicode-spec.yaml"
    spec.write_text(
        textwrap.dedent(
            """\
            slug: unicode-spec
            role_family: reader
            mission: "ünïcödé mission 测试 🚀 — verify emoji doesn't break the parser"
            done_when: "when done 完成 ✅"
            """
        ),
        encoding="utf-8",
    )
    rows = specs_sync.build_spec_rows(paths=[spec])
    assert len(rows) == 1
    assert "🚀" in rows[0]["mission"]
    assert "完成" in rows[0]["done_when"]

    calls = []
    with patch.object(specs_sync, "upsert", side_effect=lambda table, rows, **kw: calls.append((table, rows))):
        specs_sync.push_spec_rows(rows)
    assert calls[0][1][0]["mission"] == rows[0]["mission"]


@requires_real_supabase
def test_spec_row_with_emoji_and_long_mission_round_trips_through_real_supabase():
    long_mission = "mission stress test 🚀 " + ("m" * 10_000)
    row = {
        "slug": f"{QA_SLUG}-spec",
        "role_family": "reader",
        "mission": long_mission,
        "stages": ["S4"],
        "done_when": "when the QA adversarial pass is done ✅",
        "typical_count": "1",
        "evidence_pointer": None,
        "spec_version": "v1",
        "spec_body": {"slug": f"{QA_SLUG}-spec", "mission": long_mission},
        "status": "draft",
    }
    specs_sync.push_spec_rows([row], url=REAL_SUPABASE_URL, service_role_key=REAL_SERVICE_ROLE_KEY)
    rows = factory_sync.select(
        "factory_specs",
        params={"slug": f"eq.{QA_SLUG}-spec", "select": "mission,done_when"},
        url=REAL_SUPABASE_URL,
        service_role_key=REAL_SERVICE_ROLE_KEY,
    )
    assert len(rows) == 1
    assert rows[0]["mission"] == long_mission
    assert len(rows[0]["mission"]) == len(long_mission)


# =============================================================================
# 6. Full real end-to-end flow: push_stage -> push_gate_request -> push_agents
#    against the REAL local Supabase, verified directly via psql
# =============================================================================


def _psql(sql):
    result = subprocess.run(
        ["psql", REAL_DB_URL, "-t", "-A", "-F", "|", "-c", sql],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, f"psql failed: {result.stderr}"
    return [line for line in result.stdout.strip().splitlines() if line]


@requires_real_supabase
def test_full_end_to_end_flow_lands_expected_shape_verified_via_psql():
    slug = f"{QA_SLUG}-e2e"
    cfg = {
        "org": {"id": None, "name": "QA Adversarial E2E Org"},
        "champion": {"model_id": None},
    }

    ok1 = factory_sync.push_stage(
        cfg, slug, "census", {"status": "ok", "findings": {"tier_computed": "B"}, "recommendation": "FULL"},
        url=REAL_SUPABASE_URL, service_role_key=REAL_SERVICE_ROLE_KEY,
    )
    assert ok1 is True

    run_id = factory_sync.get_or_create_run_id(slug)

    ok2 = factory_sync.push_stage(
        cfg, slug, "governance", {"status": "gate_pending"},
        url=REAL_SUPABASE_URL, service_role_key=REAL_SERVICE_ROLE_KEY,
    )
    assert ok2 is True

    gate_result = factory_sync.push_gate_request(
        cfg, slug, "governance", {"recommendation": "approve", "cost": {"total_projection_usd": 5.0}},
        url=REAL_SUPABASE_URL, service_role_key=REAL_SERVICE_ROLE_KEY,
    )
    assert gate_result["status"] == "pending"

    heartbeat_md = textwrap.dedent(
        f"""\
        # Heartbeat

        ## Active Sessions
        _Register on boot._

        | session | goal | allowed_paths | risk | status | seq | last_update |
        |---------|------|---------------|------|--------|-----|-------------|
        | QA-E2E-BUILD | {slug} e2e build | factory-automation/factory/runs/{slug}/** | code | ACTIVE | 1 | 2026-07-23 10:00 PT |
        """
    )
    ok3 = factory_sync.push_agents(
        cfg, slug, run_id, heartbeat_md, url=REAL_SUPABASE_URL, service_role_key=REAL_SERVICE_ROLE_KEY
    )
    assert ok3 is True

    # --- direct psql verification, bypassing PostgREST entirely ---
    run_rows = _psql(f"SELECT org_slug, org_name, run_kind, model_stream, status, current_stage FROM factory_runs WHERE id = '{run_id}';")
    assert len(run_rows) == 1
    org_slug, org_name, run_kind, model_stream, status, current_stage = run_rows[0].split("|")
    assert org_slug == slug
    assert org_name == "QA Adversarial E2E Org"
    assert run_kind in ("first-train", "monthly-retrain")
    assert model_stream == "per-account"
    assert status  # NOT NULL, must be non-empty
    assert current_stage  # NOT NULL, must be non-empty

    stage_rows = _psql(f"SELECT stage, status, attempt FROM factory_run_stages WHERE run_id = '{run_id}' ORDER BY stage;")
    assert len(stage_rows) == 2  # census (S1) + governance (S2)
    stages_seen = {line.split("|")[0] for line in stage_rows}
    assert stages_seen == {"S1", "S2"}
    for line in stage_rows:
        _stage, stage_status, attempt = line.split("|")
        assert stage_status  # NOT NULL column
        assert attempt == "1"

    gate_rows = _psql(f"SELECT gate, status, decision_request IS NULL FROM factory_gate_requests WHERE run_id = '{run_id}';")
    assert len(gate_rows) == 1
    gate, gate_status, decision_request_is_null = gate_rows[0].split("|")
    assert gate == "S2"
    assert gate_status == "pending"
    assert decision_request_is_null == "f", "decision_request is NOT NULL in the schema but landed NULL"

    agent_rows = _psql(f"SELECT title, role, status, goal FROM factory_agents WHERE run_id = '{run_id}';")
    assert len(agent_rows) == 1
    title, role, agent_status, goal = agent_rows[0].split("|")
    assert title == "QA-E2E-BUILD"
    assert role  # NOT NULL
    assert agent_status == "active"
    assert goal  # NOT NULL

    event_rows = _psql(f"SELECT kind, headline FROM factory_run_events WHERE run_id = '{run_id}' ORDER BY kind;")
    assert len(event_rows) >= 1
    for line in event_rows:
        kind, headline = line.split("|", 1)
        assert kind in (
            "stage_started", "stage_passed", "stage_failed", "agent_spawned", "agent_finished",
            "gate_requested", "gate_decided", "steering_event", "cost_recorded", "alert",
        )
        assert headline  # NOT NULL, must be non-empty

    # No unexpected NULLs in any NOT NULL column across the whole flow — spot
    # checked directly against the schema's real NOT NULL columns via psql.
    null_check = _psql(
        f"SELECT count(*) FROM factory_runs WHERE id = '{run_id}' "
        f"AND (org_slug IS NULL OR org_name IS NULL OR run_kind IS NULL OR model_stream IS NULL "
        f"OR status IS NULL OR current_stage IS NULL);"
    )
    assert null_check == ["0"]
