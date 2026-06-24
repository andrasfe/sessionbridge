# SessionBridge

Fetch your latest **T-Mobile PDF statement** through an *isolated remote browser*.
The component you touch (web app) and the component that holds the authenticated
T-Mobile session (browser runner) are **completely separated** — they never talk
to each other directly. The control plane is the only bridge, and the runner has
no public inbound access.

```
 your browser ──HTTP/WS──> webapp ──HTTP/WS──> controlplane ──HTTP/WS──> runner
  (UI + viewer)            (public)            (orchestrator)   (Playwright + T-Mobile)
                                                   │   │
                                                   │   └─> artifacts (validate/store PDF → S3/FS)
                                                   └─────> llm (optional, redacted text only)
```

## Why it's isolated
- **webapp** has no Playwright, no session, no runner address. It only proxies to the control plane.
- **controlplane** owns the job state machine and is the *only* party that knows where the runner is.
- **runner** is the only thing with Playwright and the live session. It streams frames out (CDP screencast)
  and accepts input in — but is never reachable from the user-facing side.
  - In Docker: the runner is on an `internal: true` network with **no published ports**.
  - In AWS: the runner runs in **private subnets**, **no public IP**, and its security group accepts
    inbound **only from the control plane's security group**.

## Security properties (from the spec)
- Credentials are entered only into the embedded remote browser; we never collect or store them.
- During login: **automation paused, LLM disabled, input logging off, screenshots never persisted.**
- The LLM only ever receives **redacted, post-login visible text** — never credentials, cookies, tokens,
  the login page, or PDF contents. It is fully disabled unless an OpenRouter key is set, and it never controls the browser.
- Allowed domains only (`*.t-mobile.com`); unknown domains stop automation.
- The connector never clicks payment/autopay/plan/profile/password/security/MFA/line/device/purchase/delete controls.
- **Fail closed:** anything uncertain → stop and hand control back (`requires_user_intervention`).
- Logs are redacted before they're written (no secrets in stdout/CloudWatch).

## Run locally (Docker — recommended)
```bash
cp .env.example .env          # optional: add OPENROUTER_API_KEY
docker compose up --build
# open http://localhost:8080
```
Only port 8080 (the web app) is published. The runner is unreachable from your host.

## Run locally (no Docker, for development)
```bash
pip install -r services/runner/requirements.txt   # heaviest set (includes the rest)
playwright install chromium
./run_local.sh        # starts all five services; open http://localhost:8080
./stop_local.sh
```

## Tear everything down
Remove every container/network (and optionally volumes/images) created by any
stack launched from this repo — matched by the compose `working_dir` label, so
it catches the default project and any `-p` test stacks:
```bash
./kill-dockers.sh            # containers + networks
./kill-dockers.sh --all      # also volumes + images
./kill-dockers.sh --dry-run  # preview, change nothing
```
Each docker call is timeout-guarded, so a misbehaving daemon can't hang it; if
the daemon refuses removal (e.g. snap-Docker under AppArmor) it says so and
suggests restarting the daemon.

## Browser mode (headed vs headless)
The runner runs Chromium **headed under a virtual display (Xvfb)** by default — a
real browser sends a normal user-agent and tends to fare better against site bot
walls. This is a real display, not fingerprint spoofing. For a lighter headless
runner (e.g. CI), set `RUNNER_HEADLESS=true`:
```bash
RUNNER_HEADLESS=true docker compose up --build
```
Note: some sites (T-Mobile uses Akamai) block by **IP reputation**, so requests
from datacenter/cloud IPs may get "Access Denied" regardless of headed/headless —
run from a residential connection for the real login page.

## User flow
1. Click **Fetch latest T-Mobile statement** — an isolated session starts and the viewer appears.
2. Log into T-Mobile in the viewer (complete MFA/CAPTCHA yourself). Automation is paused.
3. Click **Continue after login** — the system verifies the domain, finds the latest statement,
   downloads + validates the PDF, and shows a download link. The session is then destroyed.

## Job states
`created → remote_browser_starting → awaiting_user_login → login_in_progress →
login_confirmed_by_user → checking_domain → navigating_to_billing →
finding_latest_statement → downloading_pdf → validating_pdf → completed`
plus `failed`, `stopped_by_user`, `requires_user_intervention`.

## Deploy to AWS
`infra/terraform/` maps these exact boundaries to:
- public ALB → **webapp** (only public entry)
- **webapp/controlplane** in public subnets; **runner/llm/artifacts** in private subnets (no public IP)
- runner SG: inbound only from the control-plane SG
- **S3 (SSE-KMS)** artifacts + **DynamoDB** metadata + **Secrets Manager** OpenRouter key + **CloudWatch** logs
- internal service DNS via Cloud Map

```bash
cd infra/terraform
terraform init
terraform apply \
  -var image_webapp=<ECR_URI> -var image_controlplane=<ECR_URI> \
  -var image_runner=<ECR_URI> -var image_llm=<ECR_URI> -var image_artifacts=<ECR_URI> \
  -var openrouter_api_key=<optional>
```
The artifact service switches from local FS/JSON to S3/DynamoDB purely via env vars — no code change.

## Layout
```
services/
  webapp/        public UI + viewer (proxies to control plane only)
  controlplane/  job state machine + the bridge (jobs.py, app.py)
  runner/        Playwright + CDP screencast + connector (browser.py, app.py)
    connectors/tmobile.py   deterministic, conservative billing/statement logic
  llm/           optional OpenRouter classifier over redacted text
  artifacts/     validate/store PDF + safe metadata (local FS or S3/DynamoDB)
shared/          schemas, state machine, security policy, redaction, storage, lease, logging
infra/terraform/ AWS deployment (ECS/Fargate)
```
