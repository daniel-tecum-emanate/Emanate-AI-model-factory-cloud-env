"""Redaction filter applied before every ledger append (briefing §3c).

Two layers:
1. Pattern redaction — known secret shapes (API keys, tokens, JWTs, key=value
   assignments to sensitive names).
2. Literal redaction — every value loaded from .env / .env.local at the repo
   root, so a secret that leaks into a command is caught even if its shape is
   unrecognizable.

Never raises: on any internal error it returns the input with a marker so the
ledger append still succeeds (a logging failure must never break a session).
"""

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MARK = "[REDACTED]"

_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),                                  # OpenAI/Anthropic-style
    re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),               # GitHub tokens
    re.compile(r"AKIA[0-9A-Z]{16}"),                                       # AWS access key id
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),                           # Slack
    re.compile(r"crsr_[A-Za-z0-9_-]{16,}"),                                # Cursor API keys (gap found by GRAPH-DATA-PIPELINE 2026-07-31; was scrubbed only inside factory_cursor_dispatch.py)
    re.compile(r"fw_[A-Za-z0-9]{16,}"),                                    # Fireworks API keys (design §5.2 item 2, factory-graph-v2 artifact pipeline 2026-08-04)
    re.compile(r"eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{5,}"),  # JWT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]

# key=value / key: value where the key name is sensitive — redact the value only
_KV = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"client[_-]?secret|auth(?:orization)?)\b(\s*[=:]\s*|\s+Bearer\s+)(['\"]?)([^\s'\"]{8,})"
)


def _env_values():
    values = []
    for name in (".env", ".env.local"):
        path = os.path.join(_ROOT, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key.startswith("NEXT_PUBLIC_"):
                        continue  # public by definition
                    if len(val) < 8:
                        continue  # too short to be a secret; avoids over-redaction
                    if val.lower() in ("true", "false", "localhost"):
                        continue
                    values.append(val)
        except OSError:
            continue
    # longest first so substrings don't leave fragments
    return sorted(set(values), key=len, reverse=True)


_ENV_VALUES = _env_values()


def redact(text):
    if not isinstance(text, str) or not text:
        return text
    try:
        for val in _ENV_VALUES:
            if val in text:
                text = text.replace(val, _MARK)
        for pat in _PATTERNS:
            text = pat.sub(_MARK, text)
        text = _KV.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{_MARK}", text)
        return text
    except Exception:
        return "[REDACTION-ERROR — content withheld]"
