"""Redacted JSON logging.

Every log line is passed through `safe_log_dict` so credentials, cookies,
tokens, leases and session state can never reach stdout/CloudWatch.
"""
from __future__ import annotations

import json
import sys

from .security import safe_log_dict


def log(service: str, event: str, **fields) -> None:
    record = {"service": service, "event": event, **safe_log_dict(fields)}
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()
