#!/usr/bin/env python3
"""fleet_ui.py — one self-contained HTML page showing what every cloud agent is doing.

    python3 cloud-sessions/bin/fleet_ui.py [--out PATH] [--open]

Reads the merged fleet view (`cs fleet --json`) and renders it. If that command is
not available it falls back to reading `state/sessions.jsonl` and `state/fleet/*.json`
directly, and SAYS SO on the page.

Design rules this file is bound by (cloud-sessions/AGENT.md, ground rule 4):
  * a degraded source is rendered as a visible warning, never as an empty section;
  * "nothing is blocked" is only ever said with the size of the view it is true of;
  * a self-reported ACTIVE row older than the lease is labelled a CLAIM, not proof;
  * nothing is invented — a missing field renders as "not reported", never as 0/blank.

No network, ever: the emitted page loads no external resource, has no fetch/XHR/
WebSocket, and renders correctly from a file:// URL on a machine with no network.
Standard library only. Python 3.8+, macOS and Linux.
"""

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys

LEASE_MIN_DEFAULT = 30          # matches heartbeat.py's lease
FLEET_CMD_TIMEOUT = 45

# ---------------------------------------------------------------- time helpers

NOW = dt.datetime.now(dt.timezone.utc)


def parse_ts(value):
    """-> aware datetime, or None. Accepts ISO-8601 (Z or offset), epoch seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return dt.datetime.fromtimestamp(float(value), dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if re.fullmatch(r"\d{9,13}(\.\d+)?", text):
        num = float(text)
        if num > 1e11:                       # milliseconds
            num /= 1000.0
        try:
            return dt.datetime.fromtimestamp(num, dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    iso = text
    if iso.endswith("Z") or iso.endswith("z"):
        iso = iso[:-1] + "+00:00"
    iso = re.sub(r"(\d{4}-\d{2}-\d{2})[ ](\d{2}:\d{2})", r"\1T\2", iso)
    try:
        stamp = dt.datetime.fromisoformat(iso)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=dt.timezone.utc)


def age_words(stamp, now=None):
    """Human age string. Mirrors the JS in the page so both agree."""
    if stamp is None:
        return "age unknown"
    secs = ((now or NOW) - stamp).total_seconds()
    if secs < -60:
        return "in the future"
    if secs < 45:
        return "just now"
    mins = secs / 60.0
    if mins < 90:
        return "%dm ago" % round(mins)
    hours = mins / 60.0
    if hours < 36:
        return "%dh ago" % round(hours)
    return "%dd ago" % round(hours / 24.0)


def abs_words(stamp, raw):
    if stamp is None:
        return str(raw) if raw not in (None, "") else "not reported"
    return stamp.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------- field access

def pick(record, names):
    for name in names:
        if name in record:
            value = record[name]
            if value not in (None, "", [], {}):
                return value, name
    return None, None


def as_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "  ".join(as_text(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return str(value)


LANE_KEYS = ["lane", "lane_id", "agent", "agent_id", "name", "session", "label"]
SID_KEYS = ["session_id", "sessionId", "cloud_session_id", "cse_id", "cse", "id"]
STATUS_KEYS = ["status", "state", "verdict", "phase_status"]
PHASE_KEYS = ["phase", "stage", "step_name", "activity", "current_step", "current", "step"]
PROGRESS_KEYS = ["progress", "progress_pct", "percent", "pct", "completion", "done_pct"]
TS_KEYS = ["heartbeat_at", "last_heartbeat", "last_update", "updated_at", "as_of",
           "ts", "timestamp", "time", "mtime"]
START_KEYS = ["started_at", "start", "started", "since", "created_at", "dispatched_at", "ts_start"]
GOAL_KEYS = ["goal", "title", "task", "brief", "description", "objective"]
NOTE_KEYS = ["note", "notes", "message", "last_message", "summary", "status_detail", "detail_note"]
OWNS_KEYS = ["owns", "owns_paths", "paths", "claims", "owned_paths"]
URL_KEYS = ["url", "session_url", "link", "href"]
BLOCK_KEYS = ["blocked", "is_blocked", "blocker", "blocked_on", "block"]
BLOCK_KIND_KEYS = ["blocker_kind", "blocked_on", "blocker_type", "block_kind", "blocker"]
BLOCK_DETAIL_KEYS = ["blocker_detail", "blocked_detail", "blocked_reason", "reason",
                     "detail", "blocker_message", "why"]
BLOCK_NEEDS_KEYS = ["needs", "needs_from", "unblock", "unblock_with", "requires",
                    "ask", "waiting_on", "action_needed"]
BLOCK_SINCE_KEYS = ["blocked_since", "blocked_at", "since", "block_started_at"]

BLOCKED_STATUSES = {"BLOCKED", "BLOCK", "WAITING", "WAITING_ON_HUMAN", "NEEDS_INPUT",
                    "NEEDS-INPUT", "AWAITING_APPROVAL", "AWAITING-APPROVAL", "STUCK"}
FAILED_STATUSES = {"FAILED", "FAIL", "ERROR", "CRASHED", "ABORTED", "TIMEOUT", "TIMED_OUT"}
DONE_STATUSES = {"ENDED", "DONE", "FINISHED", "COMPLETE", "COMPLETED", "SUCCEEDED",
                 "SUCCESS", "MERGED", "CLOSED", "EXITED"}
LIVE_STATUSES = {"ACTIVE", "RUNNING", "LIVE", "WORKING", "IN_PROGRESS", "IN-PROGRESS", "STARTED"}

CONSUMED = set(LANE_KEYS + SID_KEYS + STATUS_KEYS + PHASE_KEYS + PROGRESS_KEYS + TS_KEYS
               + START_KEYS + GOAL_KEYS + NOTE_KEYS + OWNS_KEYS + URL_KEYS + BLOCK_KEYS
               + BLOCK_KIND_KEYS + BLOCK_DETAIL_KEYS + BLOCK_NEEDS_KEYS + BLOCK_SINCE_KEYS)


def normalize(record, origin, mtime=None):
    """Map one raw record onto the fields the page renders. Never invents a value."""
    out = {"origin": origin, "raw_keys": sorted(record.keys())}

    lane, _ = pick(record, LANE_KEYS)
    sid, _ = pick(record, SID_KEYS)
    out["lane"] = as_text(lane) or as_text(sid) or "(unnamed agent)"
    out["session_id"] = as_text(sid)

    status_raw, _ = pick(record, STATUS_KEYS)
    out["status_raw"] = as_text(status_raw)
    status = out["status_raw"].strip().upper().replace(" ", "_")

    blocker_obj = record.get("blocker")
    if not isinstance(blocker_obj, dict):
        blocker_obj = {}

    kind, _ = pick(blocker_obj, ["kind", "type", "name"])
    if kind is None:
        kind, _ = pick(record, BLOCK_KIND_KEYS)
    if isinstance(kind, dict):
        kind = None
    detail, _ = pick(blocker_obj, ["detail", "message", "reason", "why", "text"])
    if detail is None:
        detail, _ = pick(record, BLOCK_DETAIL_KEYS)
    needs, _ = pick(blocker_obj, BLOCK_NEEDS_KEYS)
    if needs is None:
        needs, _ = pick(record, BLOCK_NEEDS_KEYS)
    since, _ = pick(blocker_obj, BLOCK_SINCE_KEYS)
    if since is None:
        since, _ = pick(record, BLOCK_SINCE_KEYS)

    flag, flag_key = pick(record, BLOCK_KEYS)
    flagged = False
    if flag_key in ("blocked", "is_blocked", "block"):
        flagged = bool(flag) and as_text(flag).strip().lower() not in ("false", "no", "0")
    elif flag is not None:
        flagged = True
    if blocker_obj:
        flagged = True

    out["blocked"] = bool(flagged or status in BLOCKED_STATUSES or kind or detail or needs)
    out["failed"] = status in FAILED_STATUSES
    out["blocker_kind"] = as_text(kind)
    out["blocker_detail"] = as_text(detail)
    out["blocker_needs"] = as_text(needs)
    out["blocked_since_raw"] = since
    out["blocked_since"] = parse_ts(since)

    phase, _ = pick(record, PHASE_KEYS)
    out["phase"] = as_text(phase)

    out["progress_pct"], out["progress_label"] = read_progress(record)

    ts_raw, ts_key = pick(record, TS_KEYS)
    stamp = parse_ts(ts_raw)
    out["ts_source"] = ts_key or ""
    if stamp is None and mtime is not None:
        stamp = dt.datetime.fromtimestamp(mtime, dt.timezone.utc)
        out["ts_source"] = "file mtime (record carried no timestamp)"
        ts_raw = None
    out["ts"] = stamp
    out["ts_raw"] = ts_raw

    start_raw, _ = pick(record, START_KEYS)
    out["started"] = parse_ts(start_raw)
    out["started_raw"] = start_raw

    goal, _ = pick(record, GOAL_KEYS)
    out["goal"] = as_text(goal)
    note, _ = pick(record, NOTE_KEYS)
    out["note"] = as_text(note)
    owns, _ = pick(record, OWNS_KEYS)
    out["owns"] = as_text(owns)
    url, _ = pick(record, URL_KEYS)
    out["url"] = as_text(url)

    extras = {}
    for key, value in record.items():
        if key in CONSUMED or key.startswith("_"):
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, sort_keys=True)
        if value in (None, ""):
            continue
        extras[key] = as_text(value)
    out["extras"] = extras

    if out["blocked"]:
        out["bucket"] = "blocked"
    elif out["failed"]:
        out["bucket"] = "failed"
    elif status in DONE_STATUSES:
        out["bucket"] = "done"
    else:
        out["bucket"] = "live"          # unknown status holds the claim; never silently dead
    out["status_known"] = status in (BLOCKED_STATUSES | FAILED_STATUSES | DONE_STATUSES | LIVE_STATUSES)
    return out


def read_progress(record):
    """-> (pct or None, label). A missing progress NEVER renders as 0%."""
    for key in ("step", "steps_done", "completed", "done"):
        total_key = {"step": "steps", "steps_done": "steps_total",
                     "completed": "total", "done": "total"}[key]
        if key in record and total_key in record:
            try:
                num, den = float(record[key]), float(record[total_key])
                if den > 0:
                    return max(0.0, min(100.0, num / den * 100.0)), "%g/%g" % (num, den)
            except (TypeError, ValueError):
                pass
    value, _ = pick(record, PROGRESS_KEYS)
    if value is None:
        return None, ""
    if isinstance(value, bool):
        return None, as_text(value)
    if isinstance(value, (int, float)):
        num = float(value)
        pct = num * 100.0 if 0.0 <= num <= 1.0 else num
        if 0.0 <= pct <= 100.0:
            return pct, "%d%%" % round(pct)
        return None, as_text(value)
    text = as_text(value).strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", text)
    if match:
        num, den = float(match.group(1)), float(match.group(2))
        if den > 0:
            return max(0.0, min(100.0, num / den * 100.0)), text
        return None, text
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%", text)
    if match:
        pct = float(match.group(1))
        if 0.0 <= pct <= 100.0:
            return pct, text
    return None, text


# ---------------------------------------------------------------- data sources

class Source(object):
    def __init__(self, name, status, detail, provenance=""):
        self.name = name
        self.status = status          # ok | degraded | absent
        self.detail = detail
        self.provenance = provenance


def run_fleet_cmd(cs_path, sources):
    """Run `cs fleet --json`. Returns parsed payload or None; appends a Source."""
    if not cs_path or not os.path.exists(cs_path):
        sources.append(Source("cs fleet --json", "absent",
                              "no cs executable at %s — fell back to reading state/ directly."
                              % (cs_path or "<unknown>"), cs_path or ""))
        return None
    try:
        proc = subprocess.run([cs_path, "fleet", "--json"], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=FLEET_CMD_TIMEOUT)
    except subprocess.TimeoutExpired:
        sources.append(Source("cs fleet --json", "degraded",
                              "timed out after %ds — fell back to reading state/ directly."
                              % FLEET_CMD_TIMEOUT, cs_path))
        return None
    except OSError as exc:
        sources.append(Source("cs fleet --json", "degraded",
                              "could not execute: %s — fell back to reading state/ directly." % exc,
                              cs_path))
        return None
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
    tail = err[-1] if err else "(no stderr)"
    if not out.strip():
        sources.append(Source("cs fleet --json", "absent",
                              "produced no JSON (exit %d: %s). This is expected if `cs fleet` "
                              "does not exist yet; the page below was built by reading state/ "
                              "directly." % (proc.returncode, tail), cs_path))
        return None
    try:
        payload = json.loads(out)
    except ValueError as exc:
        sources.append(Source("cs fleet --json", "degraded",
                              "exit %d but the output is not JSON (%s). First 200 bytes: %s"
                              % (proc.returncode, exc, out[:200]), cs_path))
        return None
    sources.append(Source("cs fleet --json", "ok" if proc.returncode == 0 else "degraded",
                          "parsed %d bytes of JSON (exit %d)%s"
                          % (len(out), proc.returncode,
                             "" if proc.returncode == 0 else " — a non-zero exit from cs means at "
                             "least one of ITS sources was degraded; see the rows it reported."),
                          "%s fleet --json" % cs_path))
    return payload


def load_json_file(path, sources):
    try:
        if path == "-":
            text = sys.stdin.read()
            where = "<stdin>"
        else:
            with open(path) as handle:
                text = handle.read()
            where = path
    except OSError as exc:
        sources.append(Source("--json %s" % path, "degraded", "could not read: %s" % exc, path))
        return None
    try:
        payload = json.loads(text)
    except ValueError as exc:
        sources.append(Source("--json %s" % path, "degraded",
                              "not JSON: %s. First 200 bytes: %s" % (exc, text[:200]), where))
        return None
    sources.append(Source("--json %s" % path, "ok", "parsed %d bytes" % len(text), where))
    return payload


AGENT_LIST_KEYS = ["agents", "fleet", "instances", "heartbeats", "cloud", "rows", "sessions"]


def split_payload(payload, sources):
    """-> (agent_records, ledger_records) pulled out of whatever shape cs fleet emits."""
    if payload is None:
        return None, None
    if isinstance(payload, list):
        return payload, None
    if not isinstance(payload, dict):
        sources.append(Source("merged JSON", "degraded",
                              "top level is %s, expected an object or a list"
                              % type(payload).__name__))
        return None, None

    inner = payload.get("sources")
    if isinstance(inner, dict):
        for name, info in sorted(inner.items()):
            if not isinstance(info, dict):
                continue
            status = str(info.get("status", "")).lower()
            sources.append(Source("cs fleet: %s" % name,
                                  "ok" if status in ("ok", "fine", "green") else "degraded",
                                  as_text(info.get("detail")) or as_text(info)))

    agents = None
    for key in AGENT_LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            agents = value
            break
    if agents is None:
        sources.append(Source("merged JSON", "degraded",
                              "no agent list found under any of %s — keys present: %s. "
                              "Read state/ directly instead of showing you an empty page."
                              % ("/".join(AGENT_LIST_KEYS), ", ".join(sorted(payload.keys())) or "(none)")))
    ledger = payload.get("ledger")
    if not isinstance(ledger, list):
        ledger = None
    return agents, ledger


def read_fleet_dir(fleet_dir, sources, unreadable):
    """Direct read of state/fleet/**. Returns list of (record, mtime, path)."""
    records = []
    if not os.path.isdir(fleet_dir):
        sources.append(Source("state/fleet/", "absent",
                              "%s does not exist. That means EITHER no cloud agent has emitted a "
                              "heartbeat yet, OR the emitter is not deployed. This page cannot "
                              "tell those two apart — do not read it as 'the fleet is idle'."
                              % fleet_dir, fleet_dir))
        return records
    files, skipped = [], []
    for dirpath, _dirnames, filenames in os.walk(fleet_dir):
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            full = os.path.join(dirpath, name)
            if name.endswith(".json") or name.endswith(".jsonl"):
                files.append(full)
            else:
                skipped.append(full)
    for path in sorted(files):
        try:
            with open(path) as handle:
                text = handle.read()
            mtime = os.path.getmtime(path)
        except OSError as exc:
            unreadable.append((path, "could not read: %s" % exc))
            continue
        if not text.strip():
            unreadable.append((path, "file is empty — an agent wrote a heartbeat file and put "
                                     "nothing in it, or wrote it non-atomically and this page "
                                     "caught it mid-write"))
            continue
        parsed = None
        try:
            parsed = json.loads(text)
        except ValueError as whole_exc:
            rows, bad = [], None
            for lineno, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError as line_exc:
                    bad = "line %d: %s" % (lineno, line_exc)
                    break
            if rows and bad is None:
                parsed = rows
            else:
                unreadable.append((path, "malformed JSON (%s)%s"
                                   % (whole_exc, "; as JSONL: " + bad if bad else "")))
                continue
        if isinstance(parsed, dict):
            records.append((parsed, mtime, path))
        elif isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    records.append((item, mtime, path))
                else:
                    unreadable.append((path, "list contains a %s, not an object"
                                       % type(item).__name__))
        else:
            unreadable.append((path, "top level is %s, expected an object or a list"
                               % type(parsed).__name__))
    detail = "%d file(s) read, %d record(s)" % (len(files), len(records))
    if skipped:
        detail += "; %d non-JSON file(s) NOT read: %s" % (
            len(skipped), ", ".join(os.path.basename(p) for p in skipped[:6]))
    if unreadable:
        detail += "; %d unreadable — listed below" % len(unreadable)
    sources.append(Source("state/fleet/", "degraded" if unreadable else "ok", detail, fleet_dir))
    return records


def read_ledger(path, sources):
    rows, bad = [], 0
    try:
        with open(path) as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    bad += 1
    except FileNotFoundError:
        sources.append(Source("state/sessions.jsonl", "absent",
                              "%s does not exist — nothing has been dispatched from this machine, "
                              "or this is not the machine that dispatched." % path, path))
        return rows
    except OSError as exc:
        sources.append(Source("state/sessions.jsonl", "degraded", "could not read: %s" % exc, path))
        return rows
    sources.append(Source("state/sessions.jsonl", "degraded" if bad else "ok",
                          "%d row(s)%s" % (len(rows), ", %d unparseable line(s)" % bad if bad else ""),
                          path))
    return rows


# ---------------------------------------------------------------- HTML helpers

def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


SAFE_URL = re.compile(r"^https?://", re.I)


def link_or_text(url, label=None):
    """Only http/https becomes a link. Anything else (javascript:, data:) renders as
    inert escaped text — a ledger is data, and data must not become script."""
    if not url:
        return ""
    text = esc(label or url)
    if SAFE_URL.match(url.strip()):
        return '<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>' % (esc(url.strip()), text)
    return '<span class="mono bad-url" title="not an http(s) URL — not linked">%s</span>' % text


def time_cell(stamp, raw, prefix=""):
    """Absolute + relative, with the relative half kept live by the page's own JS."""
    absolute = abs_words(stamp, raw)
    if stamp is None:
        return ('<span class="ts"><span class="mono">%s%s</span> '
                '<span class="rel unknown">(age unknown)</span></span>'
                % (esc(prefix), esc(absolute)))
    return ('<span class="ts"><span class="mono">%s%s</span> '
            '<time class="rel" data-ts="%d">(%s)</time></span>'
            % (esc(prefix), esc(absolute), int(stamp.timestamp()), esc(age_words(stamp))))


def field(label, value_html, cls=""):
    return ('<div class="f %s"><div class="fl">%s</div><div class="fv">%s</div></div>'
            % (cls, esc(label), value_html))


def wrapped(text, missing="not reported"):
    if text is None or str(text).strip() == "":
        return '<span class="missing">%s</span>' % esc(missing)
    return '<div class="wrap">%s</div>' % esc(text)


# ---------------------------------------------------------------- page render

CSS = """
:root{
  color-scheme: light dark;
  --bg:#f4f5f3; --panel:#ffffff; --panel2:#fafaf8; --ink:#14161a; --muted:#5b6270;
  --line:#dfe0da; --accent:#1f4fd8;
  --ok-bg:#e8f6ed; --ok-line:#1d7a4c; --ok-ink:#12603a;
  --warn-bg:#fdf3e0; --warn-line:#b5760a; --warn-ink:#7d5106;
  --stop-bg:#fdecea; --stop-line:#c62a1c; --stop-ink:#8e1a10;
  --live:#1d7a4c; --stale:#b5760a; --done:#6b7280; --nohb:#6d4bb5;
  --bar:#e5e6e1; --barfill:#1d7a4c;
  --shadow:0 1px 2px rgba(0,0,0,.06), 0 6px 18px rgba(0,0,0,.05);
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#111316; --panel:#181b1f; --panel2:#1e2227; --ink:#e8eaed; --muted:#9aa2ad;
    --line:#2b3037; --accent:#8fb0ff;
    --ok-bg:#10261b; --ok-line:#3fae76; --ok-ink:#8fe0b6;
    --warn-bg:#2a2113; --warn-line:#d99b2b; --warn-ink:#f2c778;
    --stop-bg:#2d1512; --stop-line:#ef5b4a; --stop-ink:#ffb3a8;
    --live:#3fae76; --stale:#d99b2b; --done:#9aa2ad; --nohb:#a58cf0;
    --bar:#2b3037; --barfill:#3fae76;
    --shadow:0 1px 2px rgba(0,0,0,.5), 0 8px 24px rgba(0,0,0,.4);
  }
}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,
  "Helvetica Neue",Arial,"Noto Sans",sans-serif;}
.mono,code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
  font-size:.86em}
a{color:var(--accent)}
.page{max-width:1180px;margin:0 auto;padding:20px 16px 72px}
h1{font-size:20px;margin:0;letter-spacing:.02em}
h2{font-size:15px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);
  margin:34px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h2 .n{color:var(--ink);font-variant-numeric:tabular-nums}
h2 .sub{text-transform:none;letter-spacing:0;font-weight:400;color:var(--muted);font-size:12px;
  display:block;margin-top:4px}
header.top{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:baseline;
  border-bottom:2px solid var(--line);padding-bottom:12px}
header.top .when{color:var(--muted);font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-left:auto}
.chip{border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:12px;
  background:var(--panel);white-space:nowrap}
.chip b{font-variant-numeric:tabular-nums}
.chip.blocked{border-color:var(--stop-line);color:var(--stop-ink);background:var(--stop-bg)}
.chip.live{border-color:var(--ok-line);color:var(--ok-ink);background:var(--ok-bg)}
.chip.stale{border-color:var(--warn-line);color:var(--warn-ink);background:var(--warn-bg)}
.banner{border:1px solid var(--line);border-left-width:6px;border-radius:8px;padding:12px 14px;
  margin:14px 0;background:var(--panel)}
.banner h3{margin:0 0 6px;font-size:14px;letter-spacing:.03em;text-transform:uppercase}
.banner p{margin:6px 0 0}
.banner.stop{background:var(--stop-bg);border-color:var(--stop-line);color:var(--stop-ink)}
.banner.warn{background:var(--warn-bg);border-color:var(--warn-line);color:var(--warn-ink)}
.banner.ok{background:var(--ok-bg);border-color:var(--ok-line);color:var(--ok-ink)}
.banner ul{margin:8px 0 0;padding-left:20px}
.banner li{margin:4px 0}
.blocked-band{border:3px solid var(--stop-line);background:var(--stop-bg);color:var(--stop-ink);
  border-radius:10px;padding:16px 18px;margin:18px 0 12px}
.blocked-band .hdl{font-size:clamp(22px,4.4vw,34px);font-weight:800;line-height:1.15;
  letter-spacing:-.01em;margin:0}
.blocked-band p{margin:8px 0 0;font-size:14px}
.cards{display:grid;gap:14px;grid-template-columns:1fr}
.cards.small{grid-template-columns:repeat(auto-fill,minmax(min(100%,330px),1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;
  box-shadow:var(--shadow);min-width:0}
.card.blocked{border:2px solid var(--stop-line);border-left-width:10px;background:var(--panel)}
.card.live{border-left:6px solid var(--live)}
.card.stale{border-left:6px solid var(--stale)}
.card.done{border-left:6px solid var(--done)}
.card.nohb{border-left:6px solid var(--nohb)}
.card.junk{border:2px dashed var(--stop-line)}
.cardhead{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:baseline;margin-bottom:8px}
.lane{font-weight:700;font-size:17px;overflow-wrap:anywhere}
.card.blocked .lane{font-size:22px}
.badge{font-size:11px;letter-spacing:.08em;text-transform:uppercase;font-weight:700;
  border-radius:4px;padding:2px 7px;border:1px solid currentColor;white-space:nowrap}
.badge.stop{color:var(--stop-line)} .badge.live{color:var(--live)}
.badge.stale{color:var(--stale)} .badge.done{color:var(--done)}
.badge.nohb{color:var(--nohb)} .badge.dim{color:var(--muted)}
.spacer{margin-left:auto}
.f{display:grid;grid-template-columns:120px minmax(0,1fr);gap:4px 12px;padding:4px 0;
  border-top:1px solid var(--line)}
.f:first-of-type{border-top:0}
.fl{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em;padding-top:2px}
.fv{min-width:0;overflow-wrap:anywhere}
.f.hero .fl{color:var(--stop-line)}
.f.hero .fv{font-size:17px;font-weight:600}
.wrap{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;
  max-height:15em;overflow:auto;background:var(--panel2);border:1px solid var(--line);
  border-radius:6px;padding:7px 9px;font-size:13.5px}
.missing{color:var(--muted);font-style:italic}
.needs-missing{color:var(--stop-line);font-weight:600}
.ts .mono{white-space:nowrap}
.rel{color:var(--muted)}
.rel.unknown{color:var(--stale)}
.bar{height:8px;background:var(--bar);border-radius:99px;overflow:hidden;margin-top:5px}
.bar span{display:block;height:100%;background:var(--barfill)}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:9px;background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;padding:8px 11px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);
  position:sticky;top:0;background:var(--panel)}
tr:last-child td{border-bottom:0}
td .wrap{max-height:7em}
.bad-url{color:var(--stop-line);text-decoration:line-through}
.filterbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:16px 0 0}
.filterbar input{flex:1 1 220px;min-width:0;padding:7px 10px;border:1px solid var(--line);
  border-radius:7px;background:var(--panel);color:var(--ink);font:inherit;font-size:14px}
.filterbar .hint{color:var(--muted);font-size:12px}
footer{margin-top:44px;padding-top:16px;border-top:1px solid var(--line);
  color:var(--muted);font-size:12.5px}
footer h3{font-size:12px;text-transform:uppercase;letter-spacing:.08em;margin:16px 0 6px;color:var(--muted)}
footer li{margin:3px 0}
.src{display:grid;grid-template-columns:minmax(0,180px) 90px minmax(0,1fr);gap:4px 12px;
  padding:6px 0;border-top:1px solid var(--line);font-size:12.5px}
.src:first-of-type{border-top:0}
.src .st{font-weight:700;letter-spacing:.06em;text-transform:uppercase;font-size:11px}
.src .st.ok{color:var(--live)} .src .st.degraded{color:var(--stop-line)}
.src .st.absent{color:var(--stale)}
.hidden{display:none !important}
@media (max-width:640px){
  .f{grid-template-columns:1fr;gap:2px}
  .fl{padding-top:6px}
  .page{padding:14px 11px 60px}
}
@media print{.filterbar{display:none}.card{break-inside:avoid}}
"""

JS = """
(function(){
  function words(sec){
    if(sec < -60) return '(in the future)';
    if(sec < 45)  return '(just now)';
    var m = sec/60; if(m < 90) return '(' + Math.round(m) + 'm ago)';
    var h = m/60;   if(h < 36) return '(' + Math.round(h) + 'h ago)';
    return '(' + Math.round(h/24) + 'd ago)';
  }
  var nodes = document.querySelectorAll('time.rel[data-ts]');
  var gen = document.getElementById('genrel');
  var genTs = gen ? parseInt(gen.getAttribute('data-ts'), 10) : 0;
  var age = document.getElementById('pageage');
  function tick(){
    var now = Date.now()/1000;
    for(var i=0;i<nodes.length;i++){
      var t = parseInt(nodes[i].getAttribute('data-ts'), 10);
      if(!isNaN(t)) nodes[i].textContent = words(now - t);
    }
    if(gen && genTs){
      gen.textContent = words(now - genTs);
      if(age && (now - genTs) > 300){
        age.className = 'banner warn';
        age.innerHTML = '<h3>this page is a snapshot, and it is now stale</h3><p>It was ' +
          'generated ' + words(now - genTs).replace(/[()]/g,'') + ' and does not refresh ' +
          'itself. Re-run <span class="mono">fleet_ui.py</span> before trusting anything ' +
          'below. The relative ages above keep counting up, which is the only honest thing ' +
          'a file on disk can do.';
      }
    }
  }
  tick(); setInterval(tick, 10000);

  var box = document.getElementById('filter');
  if(box){
    box.addEventListener('input', function(){
      var q = box.value.trim().toLowerCase();
      var cards = document.querySelectorAll('[data-filterable]');
      for(var i=0;i<cards.length;i++){
        var hit = !q || (cards[i].textContent || '').toLowerCase().indexOf(q) !== -1;
        cards[i].classList.toggle('hidden', !hit);
      }
      var secs = document.querySelectorAll('[data-section]');
      for(var j=0;j<secs.length;j++){
        var any = secs[j].querySelectorAll('[data-filterable]:not(.hidden)').length;
        var tot = secs[j].querySelectorAll('[data-filterable]').length;
        secs[j].classList.toggle('hidden', tot > 0 && any === 0 && !!q);
      }
    });
  }
})();
"""


def render_blocked_card(agent):
    parts = ['<article class="card blocked" data-filterable>']
    parts.append('<div class="cardhead"><span class="lane">%s</span>'
                 '<span class="badge stop">%s</span>' % (esc(agent["lane"]),
                                                         esc("blocked" if not agent["failed"] else "failed")))
    if agent["session_id"]:
        parts.append('<span class="spacer mono">%s</span>' % esc(agent["session_id"]))
    parts.append('</div>')

    kind = agent["blocker_kind"] or (agent["status_raw"] if agent["failed"] else "")
    parts.append(field("blocker", wrapped(kind, "kind NOT REPORTED — the agent flagged itself "
                                                "blocked without saying what kind"), "hero"))
    parts.append(field("needs", agent["blocker_needs"] and '<div class="wrap">%s</div>'
                       % esc(agent["blocker_needs"])
                       or '<span class="needs-missing">NOT REPORTED — this agent did not say what '
                          'would unblock it. Open the session to find out.</span>', "hero"))
    parts.append(field("detail", wrapped(agent["blocker_detail"], "no detail reported")))
    if agent["blocked_since"] or agent["blocked_since_raw"]:
        parts.append(field("blocked since", time_cell(agent["blocked_since"], agent["blocked_since_raw"])))
    parts.append(field("last heartbeat", time_cell(agent["ts"], agent["ts_raw"])))
    if agent["phase"]:
        parts.append(field("phase", esc(agent["phase"])))
    if agent["goal"]:
        parts.append(field("goal", wrapped(agent["goal"])))
    if agent["note"]:
        parts.append(field("note", wrapped(agent["note"])))
    if agent["owns"]:
        parts.append(field("owns", '<span class="mono">%s</span>' % esc(agent["owns"])))
    if agent["url"]:
        parts.append(field("session", link_or_text(agent["url"])))
    parts.append(render_extras(agent))
    parts.append(render_provenance(agent))
    parts.append("</article>")
    return "".join(parts)


def render_agent_card(agent, css_class, badge, badge_class, note=None):
    parts = ['<article class="card %s" data-filterable>' % css_class]
    parts.append('<div class="cardhead"><span class="lane">%s</span>'
                 '<span class="badge %s">%s</span>' % (esc(agent["lane"]), badge_class, esc(badge)))
    if agent["session_id"]:
        parts.append('<span class="spacer mono">%s</span>' % esc(agent["session_id"]))
    parts.append('</div>')
    if note:
        parts.append('<div class="f"><div class="fl">reading</div><div class="fv">%s</div></div>' % note)
    parts.append(field("phase", esc(agent["phase"]) if agent["phase"]
                       else '<span class="missing">not reported</span>'))
    if agent["progress_pct"] is not None:
        parts.append(field("progress", '%s<div class="bar"><span style="width:%.1f%%"></span></div>'
                           % (esc(agent["progress_label"] or "%d%%" % round(agent["progress_pct"])),
                              agent["progress_pct"])))
    else:
        parts.append(field("progress", ('<span class="missing">no progress reported</span>'
                                        if not agent["progress_label"] else
                                        '<span class="mono">%s</span> <span class="missing">'
                                        '(not a percentage — shown verbatim)</span>'
                                        % esc(agent["progress_label"]))))
    parts.append(field("last heartbeat", time_cell(agent["ts"], agent["ts_raw"])))
    if agent["started"] or agent["started_raw"]:
        parts.append(field("started", time_cell(agent["started"], agent["started_raw"])))
    if agent["status_raw"]:
        caveat = "" if agent["status_known"] else (
            ' <span class="missing">(status string not recognised \u2014 this page kept the row '
            'live rather than assume it is dead)</span>')
        parts.append(field("status", '<span class="mono">%s</span>%s'
                           % (esc(agent["status_raw"]), caveat)))
    if agent["goal"]:
        parts.append(field("goal", wrapped(agent["goal"])))
    if agent["note"]:
        parts.append(field("note", wrapped(agent["note"])))
    if agent["owns"]:
        parts.append(field("owns", '<span class="mono">%s</span>' % esc(agent["owns"])))
    if agent["url"]:
        parts.append(field("session", link_or_text(agent["url"])))
    parts.append(render_extras(agent))
    parts.append(render_provenance(agent))
    parts.append("</article>")
    return "".join(parts)


def render_extras(agent):
    if not agent["extras"]:
        return ""
    items = "".join('<div><span class="mono">%s</span> = <span class="mono">%s</span></div>'
                    % (esc(key), esc(value[:400] + ("…" if len(value) > 400 else "")))
                    for key, value in sorted(agent["extras"].items()))
    return field("also reported", '<div class="wrap">%s</div>' % items)


def render_provenance(agent):
    bits = [agent["origin"]]
    if agent["ts_source"]:
        bits.append("timestamp from `%s`" % agent["ts_source"])
    return field("source", '<span class="missing">%s</span>' % esc(" · ".join(bits)))


def build_page(ctx):
    agents = ctx["agents"]
    blocked = [a for a in agents if a["bucket"] in ("blocked", "failed")]
    live = [a for a in agents if a["bucket"] == "live" and not a["stale"]]
    stale = [a for a in agents if a["bucket"] == "live" and a["stale"]]
    done = [a for a in agents if a["bucket"] == "done"]
    orphan = ctx["ledger_only"]
    unreadable = ctx["unreadable"]
    degraded = [s for s in ctx["sources"] if s.status != "ok"]

    blocked.sort(key=lambda a: (a["blocked_since"] or a["ts"] or NOW))
    live.sort(key=lambda a: (a["ts"] or dt.datetime.fromtimestamp(0, dt.timezone.utc)))
    stale.sort(key=lambda a: (a["ts"] or dt.datetime.fromtimestamp(0, dt.timezone.utc)))
    done.sort(key=lambda a: (a["ts"] or dt.datetime.fromtimestamp(0, dt.timezone.utc)), reverse=True)

    title = "Fleet — %d blocked / %d live" % (len(blocked), len(live))
    out = ['<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
           '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
           '<title>%s</title>\n<style>%s</style>\n</head>\n<body>\n<div class="page">' % (esc(title), CSS)]

    out.append('<header class="top"><h1>CLOUD FLEET</h1>'
               '<span class="when">generated <span class="mono">%s</span> '
               '<time class="rel" id="genrel" data-ts="%d">(just now)</time> · %s</span>'
               '<div class="chips">'
               '<span class="chip blocked"><b>%d</b> blocked</span>'
               '<span class="chip live"><b>%d</b> live</span>'
               '<span class="chip stale"><b>%d</b> stale</span>'
               '<span class="chip"><b>%d</b> finished</span>'
               '<span class="chip"><b>%d</b> no heartbeat</span>'
               '</div></header>'
               % (esc(abs_words(NOW, None)), int(NOW.timestamp()), esc(ctx["root_label"]),
                  len(blocked), len(live), len(stale), len(done), len(orphan)))

    out.append('<div id="pageage"></div>')

    # ---- degraded sources, before anything that could read as calm ----------
    if degraded or unreadable:
        out.append('<div class="banner stop"><h3>this view is PARTIAL — %d source problem(s)</h3>'
                   '<p>Everything below is what these sources could see. A section that looks '
                   'empty may be empty <em>because the read failed</em>. Do not take absence '
                   'here as evidence of absence out there.</p><ul>' % (len(degraded) + len(unreadable)))
        for source in degraded:
            out.append("<li><b>%s</b> — %s: %s</li>"
                       % (esc(source.name), esc(source.status.upper()), esc(source.detail)))
        for path, why in unreadable:
            out.append("<li><b>unreadable heartbeat</b> — <span class=\"mono\">%s</span>: %s</li>"
                       % (esc(path), esc(why)))
        out.append("</ul></div>")

    # ---- the blocked band, top of the page, always ------------------------
    if blocked:
        out.append('<div class="blocked-band"><p class="hdl">%d AGENT%s BLOCKED — '
                   'NOTHING WILL MOVE UNTIL YOU ACT</p>'
                   '<p>Each card below names the blocker, what the agent needs, and how long it '
                   'has been waiting. They are ordered longest-waiting first.</p></div>'
                   % (len(blocked), "" if len(blocked) == 1 else "S"))
    else:
        caveat = ""
        if degraded or unreadable:
            caveat = (" — but %d source(s) are degraded above, so this is NOT a clean bill of "
                      "health, only the absence of a blocker in what could be read."
                      % (len(degraded) + len(unreadable)))
        elif not agents:
            caveat = (" — because this page can see no agents at all. Zero agents and zero "
                      "blocked agents look identical here; check that agents are actually running.")
        out.append('<div class="banner ok"><h3>nothing is blocked</h3>'
                   '<p>No agent among the <b>%d</b> this page can see has reported a blocker%s</p>'
                   '</div>' % (len(agents), esc(caveat) if caveat else "."))

    out.append('<div class="filterbar"><input id="filter" type="search" '
               'placeholder="filter by lane, phase, path, blocker text…" '
               'aria-label="filter agents"><span class="hint">filters cards only; counts above '
               'stay whole</span></div>')

    if blocked:
        out.append('<section data-section><h2><span class="n">%d</span> blocked'
                   '<span class="sub">a blocked agent is burning wall-clock and doing nothing. '
                   'Longest-waiting first.</span></h2><div class="cards">' % len(blocked))
        for agent in blocked:
            out.append(render_blocked_card(agent))
        out.append("</div></section>")

    out.append('<section data-section><h2><span class="n">%d</span> live'
               '<span class="sub">heartbeat newer than the %d-minute lease. Oldest heartbeat '
               'first — the top row is the one closest to going stale.</span></h2>' % (len(live), ctx["lease"]))
    if live:
        out.append('<div class="cards small">')
        for agent in live:
            out.append(render_agent_card(agent, "live", "live", "live"))
        out.append("</div>")
    else:
        out.append('<div class="banner warn"><p>No agent has a heartbeat newer than %d minutes. '
                   'That is either an idle fleet or a broken emitter — this page cannot '
                   'distinguish them.</p></div>' % ctx["lease"])
    out.append("</section>")

    if stale:
        out.append('<section data-section><h2><span class="n">%d</span> stale — claim, not proof'
                   '<span class="sub">these rows still say they are running, but their last '
                   'heartbeat is older than the %d-minute lease. A row is a claim; the heartbeat '
                   'is the proof, and the proof has expired. Treat as unknown, not as dead.'
                   '</span></h2><div class="cards small">' % (len(stale), ctx["lease"]))
        for agent in stale:
            out.append(render_agent_card(
                agent, "stale", "stale", "stale",
                note='<span class="missing">self-reported %s, last heard from %s</span>'
                     % (esc(agent["status_raw"] or "running"), esc(age_words(agent["ts"])))))
        out.append("</div></section>")

    if done:
        out.append('<section data-section><h2><span class="n">%d</span> finished'
                   '<span class="sub">reported a terminal status. Finished is not the same as '
                   'succeeded — read the session.</span></h2><div class="tablewrap">'
                   '<table><thead><tr><th>lane</th><th>status</th><th>last heartbeat</th>'
                   '<th>note</th><th>session</th></tr></thead><tbody>' % len(done))
        for agent in done:
            out.append('<tr data-filterable><td><b>%s</b></td><td class="mono">%s</td><td>%s</td>'
                       '<td>%s</td><td>%s</td></tr>'
                       % (esc(agent["lane"]), esc(agent["status_raw"] or "?"),
                          time_cell(agent["ts"], agent["ts_raw"]),
                          wrapped(agent["note"] or agent["goal"], "—"),
                          link_or_text(agent["url"], agent["session_id"] or "open")))
        out.append("</tbody></table></div></section>")

    if orphan:
        out.append('<section data-section><h2><span class="n">%d</span> dispatched, never heard from'
                   '<span class="sub">rows in the dispatch ledger with no heartbeat of any kind. '
                   'Either the session never emitted one, or it is not running the emitter. A '
                   'ledger row proves a dispatch happened — it proves nothing about now.'
                   '</span></h2><div class="tablewrap">'
                   '<table><thead><tr><th>title</th><th>dispatched</th><th>branch</th>'
                   '<th>mode</th><th>session</th></tr></thead><tbody>' % len(orphan))
        for row in orphan:
            out.append('<tr data-filterable><td>%s</td><td>%s</td><td class="mono">%s</td>'
                       '<td class="mono">%s</td><td>%s</td></tr>'
                       % (wrapped(row.get("title") or row.get("lane"), "(no title)"),
                          time_cell(parse_ts(row.get("ts")), row.get("ts")),
                          esc(row.get("branch") or "?"), esc(row.get("mode") or "?"),
                          link_or_text(row.get("url"), row.get("id") or "open")))
        out.append("</tbody></table></div></section>")

    if unreadable:
        out.append('<section data-section><h2><span class="n">%d</span> unreadable heartbeat file(s)'
                   '<span class="sub">these files exist and could not be parsed. They are shown '
                   'here instead of being dropped, because a dropped file is an invisible agent.'
                   '</span></h2><div class="cards">' % len(unreadable))
        for path, why in unreadable:
            out.append('<article class="card junk" data-filterable>'
                       '<div class="cardhead"><span class="lane mono">%s</span>'
                       '<span class="badge stop">unreadable</span></div>%s'
                       '<div class="f"><div class="fl">what it means</div><div class="fv">'
                       'An agent may be running, or blocked, behind this file. This page cannot '
                       'tell. Fix the emitter or read the file by hand.</div></div></article>'
                       % (esc(path), field("parse error", wrapped(why))))
        out.append("</div></section>")

    # ---- footer: provenance ------------------------------------------------
    out.append("<footer><h3>where every number on this page came from</h3>")
    for source in ctx["sources"]:
        out.append('<div class="src"><span class="mono">%s</span>'
                   '<span class="st %s">%s</span><span>%s</span></div>'
                   % (esc(source.name), esc(source.status), esc(source.status),
                      esc(source.detail)))
    out.append("<h3>how to read this page</h3><ul>"
               "<li><b>It is a snapshot.</b> It was written to a file and does not poll anything. "
               "The relative ages keep counting up on their own; the facts behind them do not "
               "change until you re-run the generator.</li>"
               "<li><b>Live means recent, not healthy.</b> A heartbeat proves a process wrote a "
               "file, not that the work is going well.</li>"
               "<li><b>Stale means unknown.</b> Not dead. The lease expiring makes the row a "
               "claim without proof; it does not make it false.</li>"
               "<li><b>An empty section is only meaningful if every source above says ok.</b></li>"
               "<li><b>No blocker reported is not the same as not blocked.</b> An agent that "
               "hangs without writing a heartbeat shows up as stale, never as blocked.</li>"
               "</ul>")
    out.append('<p>generated by <span class="mono">cloud-sessions/bin/fleet_ui.py</span> · '
               'no network access of any kind: this page loads no external resource and makes '
               'no requests. Links are click-through only.</p>')
    out.append("</footer></div>\n<script>%s</script>\n</body>\n</html>\n" % JS)
    return "".join(out)


# ---------------------------------------------------------------- assembly

def key_for(agent):
    return agent["session_id"] or agent["lane"]


def assemble(args):
    root = os.path.abspath(args.root)
    sources, unreadable = [], []

    payload = None
    if args.json:
        payload = load_json_file(args.json, sources)
    elif not args.no_cmd:
        payload = run_fleet_cmd(os.path.join(root, "bin", "cs"), sources)

    raw_agents, raw_ledger = split_payload(payload, sources)

    records = []
    if raw_agents is not None:
        origin = "cs fleet --json" if not args.json else "--json %s" % args.json
        for item in raw_agents:
            if isinstance(item, dict):
                records.append(normalize(item, origin))
            else:
                unreadable.append((origin, "agent list contains a %s, not an object"
                                   % type(item).__name__))
    else:
        for record, mtime, path in read_fleet_dir(os.path.join(root, "state", "fleet"),
                                                  sources, unreadable):
            records.append(normalize(record, "state/fleet/%s" % os.path.basename(path), mtime))

    ledger = raw_ledger
    if ledger is None:
        ledger = read_ledger(os.path.join(root, "state", "sessions.jsonl"), sources)
    else:
        sources.append(Source("ledger (from cs fleet)", "ok", "%d row(s)" % len(ledger)))

    # de-duplicate agents: last heartbeat for a given key wins, older ones dropped loudly
    by_key = {}
    for agent in records:
        key = key_for(agent)
        previous = by_key.get(key)
        if previous is None:
            by_key[key] = agent
            continue
        newer = agent if (agent["ts"] or NOW) >= (previous["ts"] or NOW) else previous
        older = previous if newer is agent else agent
        newer["origin"] = "%s (superseded %s)" % (newer["origin"], older["origin"])
        by_key[key] = newer
    agents = list(by_key.values())

    seen = set()
    for agent in agents:
        if agent["session_id"]:
            seen.add(agent["session_id"])
        seen.add(agent["lane"])

    ledger_only = []
    for row in ledger:
        if not isinstance(row, dict):
            continue
        ident = as_text(row.get("id"))
        lane = as_text(row.get("lane"))
        if (ident and ident in seen) or (lane and lane in seen):
            continue
        ledger_only.append(row)
    ledger_only.sort(key=lambda r: as_text(r.get("ts")), reverse=True)

    lease = args.stale_after
    for agent in agents:
        if agent["ts"] is None:
            agent["stale"] = True
        else:
            agent["stale"] = (NOW - agent["ts"]).total_seconds() > lease * 60

    return {"agents": agents, "ledger_only": ledger_only, "sources": sources,
            "unreadable": unreadable, "lease": lease, "root_label": root}


def open_in_browser(path):
    opener = None
    if sys.platform == "darwin" and shutil.which("open"):
        opener = "open"
    elif shutil.which("xdg-open"):
        opener = "xdg-open"
    elif shutil.which("open"):
        opener = "open"
    if not opener:
        print("no platform opener (open/xdg-open) on PATH — the page is at:\n  %s" % path)
        return
    try:
        subprocess.run([opener, path], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        print("opened with %s" % opener)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("%s failed (%s) — the page is at:\n  %s" % (opener, exc, path))


def main(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    default_root = os.path.dirname(here)

    parser = argparse.ArgumentParser(
        prog="fleet_ui.py",
        description="Render the cloud fleet as one self-contained HTML page. Reads "
                    "`cs fleet --json`; falls back to state/sessions.jsonl + state/fleet/*.json "
                    "and says so on the page. Never writes anything but the page.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="exit codes: 0 = page written, every source ok · 1 = page written but at least "
               "one source was DEGRADED or ABSENT (the page says which) · 2 = the page could not "
               "be written at all.")
    parser.add_argument("--out", default=None,
                        help="output path (default: <cloud-sessions>/state/fleet-ui.html)")
    parser.add_argument("--open", action="store_true", dest="open",
                        help="open the page with the platform opener (open/xdg-open)")
    parser.add_argument("--json", default=None, metavar="PATH",
                        help="read the merged JSON from a file (or - for stdin) instead of "
                             "running `cs fleet --json`")
    parser.add_argument("--root", default=default_root, metavar="DIR",
                        help="the cloud-sessions folder to read state from (default: %s)" % default_root)
    parser.add_argument("--stale-after", type=int, default=LEASE_MIN_DEFAULT, metavar="MIN",
                        help="minutes after which a live heartbeat becomes a stale claim "
                             "(default: %d, matching heartbeat.py's lease)" % LEASE_MIN_DEFAULT)
    parser.add_argument("--no-cmd", action="store_true",
                        help="never shell out to cs; read state/ directly")
    args = parser.parse_args(argv)

    ctx = assemble(args)
    page = build_page(ctx)

    out = args.out or os.path.join(os.path.abspath(args.root), "state", "fleet-ui.html")
    out = os.path.abspath(out)
    try:
        parent = os.path.dirname(out)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(out, "w") as handle:
            handle.write(page)
    except OSError as exc:
        print("could not write %s: %s" % (out, exc), file=sys.stderr)
        return 2

    blocked = len([a for a in ctx["agents"] if a["bucket"] in ("blocked", "failed")])
    live = len([a for a in ctx["agents"] if a["bucket"] == "live" and not a["stale"]])
    stale = len([a for a in ctx["agents"] if a["bucket"] == "live" and a["stale"]])
    bad = [s for s in ctx["sources"] if s.status != "ok"]
    print("wrote %s" % out)
    print("  %d blocked · %d live · %d stale · %d agent(s) total · %d ledger row(s) with no heartbeat"
          % (blocked, live, stale, len(ctx["agents"]), len(ctx["ledger_only"])))
    if ctx["unreadable"]:
        print("  %d UNREADABLE heartbeat file(s) — shown on the page, not dropped" % len(ctx["unreadable"]))
    for source in bad:
        print("  %-9s %s: %s" % (source.status.upper(), source.name, source.detail))
    if args.open:
        open_in_browser(out)
    if bad or ctx["unreadable"]:
        print("  exit 1: the page was written and it is PARTIAL — a degraded source is not an "
              "empty fleet.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
