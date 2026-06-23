PoC spec: Fetch latest T-Mobile PDF statement via isolated Playwright “remote browser”

Goal: Build a local PoC that retrieves the latest T-Mobile PDF statement from the user’s own account. The browser may run locally, but architecture must simulate a true remote-browser setup with strict separation between local app, control plane, browser runner, LLM service, session lease, connector, and artifact storage.

User flow:
1. User opens local app and clicks “Fetch latest T-Mobile statement.”
2. App starts isolated remote-browser session and shows an embedded browser viewer.
3. User logs into T-Mobile through that viewer. They do not get shell/container/browser-profile access.
4. Web app only streams the browser view and forwards mouse/keyboard/scroll/paste events to the browser runner.
5. During login: automation paused, LLM disabled, input logging disabled, screenshot persistence disabled.
6. User completes MFA/CAPTCHA manually if needed.
7. User clicks “Continue after login.”
8. System verifies allowed T-Mobile domain, navigates to billing, finds latest statement, downloads PDF, validates it, stores artifact, shows download link.
9. Browser session is destroyed/disconnected at end.

Architecture:
- Web app: UI, embedded remote-browser viewer, status, controls, final download link. No Playwright/session access.
- Control plane: orchestrates job/state, proxies events, coordinates browser/connector/LLM/artifacts. No raw session/credentials.
- Remote browser runner: only component with Playwright and authenticated T-Mobile session. Streams browser, receives user input, runs automation, enforces domains, downloads PDF.
- Session lease: short-lived task permission; session stays inside browser runner memory for PoC; destroyed at end.
- T-Mobile connector: deterministic logic to find Billing/Bill details/Statements/Download PDF, choose latest visible statement, download PDF. Conservative: stop if unsure.
- LLM service: optional via OpenRouter .env. May classify redacted visible post-login page info only. Never controls browser.
- Artifact service: validates/stores PDF and safe metadata; exposes final download link.

Security rules:
- Never collect/store T-Mobile credentials.
- User enters credentials only into embedded remote browser.
- Never bypass MFA/CAPTCHA.
- LLM never sees credentials, cookies, tokens, storage, headers, hidden fields, login screen, or PDF contents.
- Do not use local browser profile or mount user home/Downloads/SSH/cloud creds.
- Do not expose Playwright, cookies, DevTools, browser profile, raw files, or session state to user/web app/LLM.
- Unknown domains stop automation.
- Do not click payment, autopay, plan, profile, password, security, MFA, line/device, purchase, upgrade, order, delete/remove controls.
- Fail closed: if uncertain, stop and ask user to take control.

Job states:
created → remote_browser_starting → awaiting_user_login → login_in_progress → login_confirmed_by_user → checking_domain → navigating_to_billing → finding_latest_statement → downloading_pdf → validating_pdf → completed
Also support: failed, stopped_by_user, requires_user_intervention.

Latest statement logic:
Look for Billing, Bill, Bill details, Statements, Previous bills, View bill, Download PDF, Download bill, Download statement. Pick newest by statement date, bill date, billing-period end, current bill, or due date fallback. If only current bill has Download PDF, treat as latest. Prefer detailed/complete PDF if clearly offered.

Artifact success criteria:
Downloaded file exists, non-empty, within size limit, appears to be PDF, safe filename, SHA-256 hash, job association, source allowed T-Mobile session. Show filename, size, hash, statement date if known, source host, validation status, download button.

AWS-ready:
Same boundaries should map to web app + control plane + isolated ECS/Fargate browser runners + separate LLM service + S3 SSE-KMS artifacts + DynamoDB metadata + Secrets Manager/OpenRouter key + CloudWatch redacted logs. Browser runners must have no public inbound access and only be reachable by control plane.

Acceptance:
User can log into T-Mobile through embedded remote browser without direct host access; automation pauses during login; LLM sees no secrets; system downloads and validates latest PDF; final artifact is downloadable; no secrets in logs; session destroyed at job end.
