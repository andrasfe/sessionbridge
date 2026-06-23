"""Smoke tests for the non-browser logic: state machine, security policy,
artifact validation, connector date parsing. Run from repo root with the venv:

    PYTHONPATH=. python tests/test_core.py
"""
import io
import sys

from fastapi.testclient import TestClient

from shared.states import JobState, can_transition, automation_allowed
from shared import security


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        check.failed = True


check.failed = False

# ---- state machine ----
check("forward transition allowed",
      can_transition(JobState.AWAITING_USER_LOGIN, JobState.LOGIN_CONFIRMED_BY_USER))
check("backward transition blocked",
      not can_transition(JobState.DOWNLOADING_PDF, JobState.AWAITING_USER_LOGIN))
check("fail-closed branch always allowed",
      can_transition(JobState.NAVIGATING_TO_BILLING, JobState.REQUIRES_USER_INTERVENTION))
check("no transition out of terminal",
      not can_transition(JobState.COMPLETED, JobState.FAILED))
check("automation paused during login",
      not automation_allowed(JobState.AWAITING_USER_LOGIN) and
      not automation_allowed(JobState.LOGIN_IN_PROGRESS))
check("automation allowed after login",
      automation_allowed(JobState.NAVIGATING_TO_BILLING))

# ---- security policy ----
check("allowed apex domain", security.host_allowed("t-mobile.com"))
check("allowed subdomain", security.host_allowed("account.t-mobile.com"))
check("blocked lookalike", not security.host_allowed("t-mobile.com.evil.com"))
check("blocked unknown", not security.host_allowed("example.com"))
check("url allow check", security.url_allowed("https://www.t-mobile.com/signin"))
check("forbidden: make a payment", security.is_forbidden_control("Make a payment"))
check("forbidden: change plan", security.is_forbidden_control("Change plan"))
check("allowed: download pdf", not security.is_forbidden_control("Download PDF"))

# ---- redaction ----
red = security.redact("Email me at john.doe@example.com or 555-123-4567, acct 1234567890123, $42.50")
check("email redacted", "john.doe@example.com" not in red and "[EMAIL]" in red)
check("phone redacted", "555-123-4567" not in red)
check("account redacted", "1234567890123" not in red)
check("amount redacted", "$42.50" not in red)

log_clean = security.safe_log_dict({"password": "hunter2", "lease_token": "abc", "note": "ok"})
check("password key redacted", log_clean["password"] == "[REDACTED]")
check("lease key redacted", log_clean["lease_token"] == "[REDACTED]")

# ---- connector date parsing (no playwright import needed) ----
import importlib.util
spec = importlib.util.spec_from_file_location(
    "tmod", "services/runner/connectors/tmobile.py")
# tmobile imports playwright; stub it so we can test _parse_date in isolation.
import types
pw = types.ModuleType("playwright"); api = types.ModuleType("playwright.async_api")
api.Page = object
class _TO(Exception):
    pass
api.TimeoutError = _TO
sys.modules["playwright"] = pw
sys.modules["playwright.async_api"] = api
tmod = importlib.util.module_from_spec(spec)
sys.modules["tmod"] = tmod  # so @dataclass can resolve annotations
spec.loader.exec_module(tmod)

d1 = tmod._parse_date("Statement: Jan 15, 2026")
d2 = tmod._parse_date("Bill date 02/03/2026")
d3 = tmod._parse_date("2026-03-20 period end")
check("parse 'Jan 15, 2026'", d1 and d1.year == 2026 and d1.month == 1 and d1.day == 15)
check("parse '02/03/2026'", d2 and d2.month == 2 and d2.day == 3)
check("parse ISO date", d3 and d3.month == 3 and d3.day == 20)
check("newest sorts last->first", d3 > d1)

# ---- artifact service via TestClient ----
import services.artifacts.app as art  # noqa
# point storage at a temp dir
import tempfile, os
tmp = tempfile.mkdtemp()
art.BLOBS.root = os.path.join(tmp, "blobs"); os.makedirs(art.BLOBS.root, exist_ok=True)
art.META.root = os.path.join(tmp, "meta"); os.makedirs(art.META.root, exist_ok=True)
client = TestClient(art.app)

pdf = b"%PDF-1.7\n" + b"x" * 100
r = client.post("/artifacts",
                files={"file": ("My Statement.pdf", pdf, "application/pdf")},
                data={"job_id": "job_1", "source_host": "www.t-mobile.com",
                      "statement_date": "2026-01-15"})
check("valid pdf accepted", r.status_code == 200)
meta = r.json()
check("safe filename", meta["filename"] == "My_Statement.pdf")
check("sha256 present", len(meta["sha256"]) == 64)
check("validation status valid", meta["validation_status"] == "valid")

# non-pdf rejected
r2 = client.post("/artifacts", files={"file": ("x.pdf", b"not a pdf", "application/pdf")},
                 data={"job_id": "j", "source_host": "www.t-mobile.com", "statement_date": ""})
check("non-pdf rejected", r2.status_code == 422)

# disallowed source host rejected
r3 = client.post("/artifacts", files={"file": ("x.pdf", pdf, "application/pdf")},
                 data={"job_id": "j", "source_host": "evil.com", "statement_date": ""})
check("bad source host rejected", r3.status_code == 422)

# download round-trips
r4 = client.get(f"/artifacts/{meta['artifact_id']}/download")
check("download returns pdf bytes", r4.status_code == 200 and r4.content == pdf)

# ---- llm service disabled by default ----
import services.llm.app as llm
lc = TestClient(llm.app)
r5 = lc.post("/classify", json={"job_id": "j", "redacted_text": "hello",
                                "candidate_labels": ["billing", "other"]})
check("llm disabled when no key", r5.status_code == 200 and r5.json()["enabled"] is False)

print()
print("RESULT:", "FAILURES PRESENT" if check.failed else "ALL TESTS PASSED")
sys.exit(1 if check.failed else 0)
