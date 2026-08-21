#!/usr/bin/env python3
"""factory_push_prod.py — push the local Model Factory catalog to a target Supabase.

Why this exists (2026-07-31): the factory pipeline writes every run to the
LOCAL Supabase (factory.py + factory_sync.py target whatever
FACTORY_SUPABASE_URL says, and during the grand-steel run that was
127.0.0.1). The product's Model Factory tab in production therefore never
saw the run: prod had 5 runs / 0 models while local held the whole story
(22 runs, 8 models, 79 stages). This script is the repeatable local→target
copy: idempotent REST upserts (on_conflict=id, merge-duplicates) over an
explicit table allowlist, in FK-safe order, with QA/test noise filtered out.

It deliberately does NOT delete anything on the target and never touches
non-factory tables (org_custom_models — serving activation — is out of
scope by design; models reach customers via S9/S10 decisions, not via sync).

Usage:
    # dry run — show what would be pushed
    python3 factory_push_prod.py --dry-run

    # self-test against the local REST endpoint (proves payloads round-trip)
    FACTORY_PUSH_URL=http://127.0.0.1:54321 \
    FACTORY_PUSH_KEY=<local service role key> \
    python3 factory_push_prod.py

    # production
    FACTORY_PUSH_URL=https://<ref>.supabase.co \
    FACTORY_PUSH_KEY=<prod service role key> \
    python3 factory_push_prod.py

Source DB is always the local factory Postgres (LOCAL_DSN below).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

LOCAL_DSN = os.environ.get(
    "FACTORY_LOCAL_DSN", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)

# Runs written by adversarial QA sessions and the product's smoke row are
# real rows locally but noise in the product catalog. Child tables inherit
# the filter through their run_id.
RUN_FILTER = "org_slug !~ '^qa-' AND org_slug <> 'test'"
KEPT_RUNS = f"SELECT id FROM factory_runs WHERE {RUN_FILTER}"

# (table, WHERE clause or None, append_only, conflict_key) in FK-safe insert
# order. append_only: factory_gate_decisions has no UPDATE grant even for
# service_role (the human-gate forgery protection) — existing rows are
# immutable, so we insert-if-missing instead of merge-upserting.
# conflict_key: tables with a natural unique key beyond the id PK must
# resolve on it — the target may hold the same lesson/spec/org row under a
# different id from an earlier independent sync.
TABLES: list[tuple[str, str | None, bool, str]] = [
    ("factory_runs", RUN_FILTER, False, "id"),
    ("factory_run_stages", f"run_id IN ({KEPT_RUNS})", False, "id"),
    ("factory_run_events", f"run_id IN ({KEPT_RUNS})", False, "id"),
    ("factory_run_digests", f"run_id IN ({KEPT_RUNS})", False, "id"),
    ("factory_gate_requests", f"run_id IN ({KEPT_RUNS})", False, "id"),
    (
        "factory_gate_decisions",
        f"gate_request_id IN (SELECT id FROM factory_gate_requests WHERE run_id IN ({KEPT_RUNS}))",
        True,
        "id",
    ),
    ("factory_agents", f"run_id IN ({KEPT_RUNS})", False, "id"),
    ("factory_handoffs", f"run_id IN ({KEPT_RUNS})", False, "id"),
    ("factory_models", None, False, "org_slug,model_id"),
    ("factory_lessons", None, True, "code"),
    ("factory_specs", None, False, "slug"),
    ("factory_org_configs", None, False, "org_slug"),
]

CHUNK = 200


def read_rows(table: str, where: str | None) -> list[dict]:
    clause = f" WHERE {where}" if where else ""
    sql = f"SELECT COALESCE(json_agg(t), '[]'::json) FROM (SELECT * FROM {table}{clause}) t;"
    out = subprocess.run(
        ["psql", LOCAL_DSN, "--no-psqlrc", "-t", "-A", "-c", sql],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return json.loads(out)


def upsert(
    url: str, key: str, table: str, rows: list[dict], append_only: bool, conflict_key: str
) -> None:
    endpoint = f"{url.rstrip('/')}/rest/v1/{table}?on_conflict={conflict_key}"
    resolution = "ignore-duplicates" if append_only else "merge-duplicates"
    for i in range(0, len(rows), CHUNK):
        body = json.dumps(rows[i : i + CHUNK]).encode()
        req = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": f"resolution={resolution},return=minimal",
            },
        )
        try:
            urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:  # surface PostgREST's error body
            detail = e.read().decode(errors="replace")[:500]
            raise SystemExit(f"{table}: HTTP {e.code} — {detail}") from e


def target_count(url: str, key: str, table: str) -> str:
    req = urllib.request.Request(
        f"{url.rstrip('/')}/rest/v1/{table}?select=id&limit=1",
        method="HEAD",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Prefer": "count=exact",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return (resp.headers.get("Content-Range") or "?").split("/")[-1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    url = os.environ.get("FACTORY_PUSH_URL")
    key = os.environ.get("FACTORY_PUSH_KEY")
    if not args.dry_run and (not url or not key):
        print("FACTORY_PUSH_URL / FACTORY_PUSH_KEY required (or use --dry-run)")
        return 2

    total = 0
    for table, where, append_only, conflict_key in TABLES:
        rows = read_rows(table, where)
        total += len(rows)
        print(f"{table}: {len(rows)} rows" + (" (dry-run)" if args.dry_run else ""))
        if not args.dry_run and rows:
            if conflict_key != "id":
                # resolving on a natural key: drop id so an existing target
                # row keeps its identity instead of colliding on the id PK
                rows = [{k: v for k, v in r.items() if k != "id"} for r in rows]
            upsert(url, key, table, rows, append_only, conflict_key)
            print(f"  -> target now reports {target_count(url, key, table)} rows")
    print(f"{'would push' if args.dry_run else 'pushed'} {total} rows total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
