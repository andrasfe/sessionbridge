"""Centralised security policy: allowed domains and redaction.

Imported by the runner/control plane (navigation enforcement) and the LLM
service (redaction defence for log lines and any text shown to the model).
"""
from __future__ import annotations

import re

from .config import settings

# ---------------------------------------------------------------------------
# Allowed domains. Unknown domains are blocked (fail closed). Configurable via
# ALLOWED_DOMAINS (comma-separated suffixes); "*" opens browsing to any domain
# for the generic agent.
# ---------------------------------------------------------------------------
ALLOWED_DOMAIN_SUFFIXES = tuple(
    d.strip().lower() for d in settings.ALLOWED_DOMAINS.split(",") if d.strip()
)

# Start URL for new sessions (configurable via START_URL).
START_URL = settings.START_URL


def host_allowed(host: str | None) -> bool:
    # "*" opens browsing to any domain (agent mode); otherwise enforce the list.
    if "*" in ALLOWED_DOMAIN_SUFFIXES:
        return True
    if not host:
        return False
    host = host.lower().strip()
    return any(host == s or host.endswith("." + s) for s in ALLOWED_DOMAIN_SUFFIXES)


def url_allowed(url: str | None) -> bool:
    if not url:
        return False
    from urllib.parse import urlparse

    try:
        return host_allowed(urlparse(url).hostname)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Redaction. Any free text shown to the model is redacted; so is every
# structured log line, so secrets never reach stdout/CloudWatch.
# ---------------------------------------------------------------------------
_REDACTIONS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
    (re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"), "[PHONE]"),
    (re.compile(r"\b\d{12,19}\b"), "[ACCOUNT]"),          # long account/card-ish numbers
    (re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?"), "[AMOUNT]"),
    (re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"), "[DATE]"),
]


def redact(text: str | None, limit: int = 8000) -> str:
    if not text:
        return ""
    out = text
    for pattern, repl in _REDACTIONS:
        out = pattern.sub(repl, out)
    out = re.sub(r"\s+", " ", out).strip()
    return out[:limit]


_SECRET_KEYS = re.compile(
    r"(password|cookie|token|authorization|set-cookie|secret|session|lease)",
    re.IGNORECASE,
)


def safe_log_dict(d: dict) -> dict:
    clean = {}
    for k, v in d.items():
        if _SECRET_KEYS.search(k):
            clean[k] = "[REDACTED]"
        elif isinstance(v, str):
            clean[k] = redact(v, limit=500)
        elif isinstance(v, dict):
            clean[k] = safe_log_dict(v)
        else:
            clean[k] = v
    return clean
