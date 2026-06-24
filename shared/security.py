"""Centralised security policy: domains, forbidden controls, redaction.

These rules implement the spec's "Security rules" section and are imported by
the runner/connector (enforcement) and the LLM service (redaction defence).
"""
from __future__ import annotations

import re

from .config import settings

# ---------------------------------------------------------------------------
# Allowed domains. Unknown domains stop automation (fail closed).
# Configurable via ALLOWED_DOMAINS (comma-separated suffixes); defaults to
# T-Mobile.
# ---------------------------------------------------------------------------
ALLOWED_DOMAIN_SUFFIXES = tuple(
    d.strip().lower() for d in settings.ALLOWED_DOMAINS.split(",") if d.strip()
)

# Start URL for new sessions (configurable via START_URL).
START_URL = settings.START_URL
TMOBILE_LOGIN_URL = START_URL  # backwards-compatible alias


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
# Forbidden controls. The connector must never click anything whose accessible
# text/label matches these — even if it appears on an allowed billing page.
# ---------------------------------------------------------------------------
FORBIDDEN_CONTROL_PATTERNS = [
    r"\bpay\b", r"payment", r"autopay", r"pay now", r"make a payment",
    r"\bplan\b", r"change plan", r"upgrade", r"\border\b", r"purchase", r"buy ",
    r"profile", r"password", r"security", r"\bmfa\b", r"two-?factor",
    r"\bline\b", r"\bdevice\b", r"add a line", r"manage line",
    r"delete", r"remove", r"cancel", r"\bclose\b account",
]
_FORBIDDEN_RE = re.compile("|".join(FORBIDDEN_CONTROL_PATTERNS), re.IGNORECASE)


def is_forbidden_control(text: str | None) -> bool:
    if not text:
        return False
    return bool(_FORBIDDEN_RE.search(text))


# Billing-related navigation/download text the connector is allowed to follow.
BILLING_NAV_PATTERNS = [
    "bill details", "bill history", "previous bills", "view bill",
    "statements", "billing", "bill",
]
DOWNLOAD_PATTERNS = [
    "download pdf", "download bill", "download statement", "download",
    "view pdf", "pdf",
]


# ---------------------------------------------------------------------------
# Redaction. The LLM may only ever see redacted, non-secret visible text.
# ---------------------------------------------------------------------------
_REDACTIONS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
    (re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"), "[PHONE]"),
    (re.compile(r"\b\d{12,19}\b"), "[ACCOUNT]"),          # long account/card-ish numbers
    (re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?"), "[AMOUNT]"),
    (re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"), "[DATE]"),  # keep coarse; dates handled separately
]


def redact(text: str | None, limit: int = 8000) -> str:
    if not text:
        return ""
    out = text
    for pattern, repl in _REDACTIONS:
        out = pattern.sub(repl, out)
    # Collapse whitespace and clamp length.
    out = re.sub(r"\s+", " ", out).strip()
    return out[:limit]


# ---------------------------------------------------------------------------
# Log redaction. Applied to every structured log line before it leaves a
# service so secrets never reach CloudWatch/stdout.
# ---------------------------------------------------------------------------
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
