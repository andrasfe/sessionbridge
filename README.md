# SessionBridge

A **chat-driven browser agent** running in an *isolated remote browser*. Tell the
agent what to do in natural language; it drives a real Chromium session you watch
live and can grab control of at any time (e.g. to solve a CAPTCHA). The component
you touch (web app) and the component that holds the live browser session (runner)
are **completely separated** — they never talk to each other directly. The control
plane is the only bridge, and the runner has no public inbound access.

```
 your browser ──HTTP/WS──> webapp ──HTTP/WS──> controlplane ──HTTP/WS──> runner
  (UI + viewer)            (public)            (orchestrator)      (Playwright)
                                                   │
                                                   └─────> llm (optional, vision agent brain)
```

## Why it's isolated
- **webapp** has no Playwright, no session, no runner address. It only proxies to the control plane.
- **controlplane** owns the job lifecycle and is the *only* party that knows where the runner is.
- **runner** is the only thing with Playwright and the live session. It streams frames out (CDP screencast)
  and accepts input in — but is never reachable from the user-facing side.
  - In Docker: the runner is on a backend network with **no published ports**.
  - In AWS: the runner runs in **private subnets**, **no public IP**, and its security group accepts
    inbound **only from the control plane's security group**.

## Security properties
- The user can grab control of the browser at any time; input is forwarded but **never logged**.
- Navigation is restricted to **allowed domains** (`ALLOWED_DOMAINS`; `*` opens browsing for the generic agent).
- **Fail closed:** anything uncertain → stop and hand control back (`requires_user_intervention`).
- Logs are redacted before they're written (no secrets in stdout/CloudWatch).

## Run locally (Docker — recommended)
```bash
cp .env.example .env          # add OPENROUTER_API_KEY for the agent brain
docker compose -f docker-compose.yml -f docker-compose.agent.yml up --build
# open http://localhost:8090
```
Only the web app port is published. The runner is unreachable from your host.

## Run locally (no Docker, for development)
```bash
pip install -r services/runner/requirements.txt   # heaviest set (includes the rest)
playwright install chromium
./run_local.sh        # starts all services; open the web app
./stop_local.sh
```

## LLM providers (pluggable)
The agent's brain and the classifier go through `services/llm/llm_providers/`, a
provider-agnostic abstraction (`LLMProvider` protocol + `Message` /
`CompletionResponse` + a factory). Switch backends with env only — no code change:

```bash
LLM_PROVIDER=openrouter  OPENROUTER_API_KEY=…  OPENROUTER_MODEL=openai/gpt-4o
LLM_PROVIDER=openai      OPENAI_API_KEY=…      OPENAI_MODEL=gpt-4o
LLM_PROVIDER=anthropic   ANTHROPIC_API_KEY=…   ANTHROPIC_MODEL=claude-sonnet-4-6
```

Add a new provider by implementing the `LLMProvider` protocol and registering it
(`register_provider`, or a `llm_providers.providers` entry point). `Message`
carries optional `images` (base64 JPEG) so the vision agent works across
providers. Use a **vision-capable** model — the agent reads screenshots.

## Agent harness (pluggable)
The loop that drives the remote browser is selectable at runtime via
`AGENT_HARNESS` (same Protocol + factory + entry-point pattern as the LLM
providers; lives in `services/runner/harness/`). Either way, SessionBridge stays
the isolated browser — only the brain changes:

| `AGENT_HARNESS` | Brain | Perception | Notes |
|---|---|---|---|
| `builtin` (default) | native vision loop via `services/llm` | screenshots | uses `LLM_PROVIDER`; needs a vision model |
| `openhands` | an OpenHands agent loop | text (DOM/elements/visible text) | LLM = `openrouter/z-ai/glm-5.2` by default |

The OpenHands harness runs an OpenHands agent that drives our browser through a
custom `browser` tool (navigate/click/type/key/scroll/observe). It uses **text**
perception, not screenshots — images in tool-result messages are dropped over
the OpenAI/OpenRouter transport, so text is the provider-agnostic choice (the
builtin harness is the vision one). It needs `OPENROUTER_API_KEY` and the heavy
`openhands-ai` dependency, which is **not** in the default image — build with the
overlay, which sets the `INSTALL_OPENHANDS=1` build arg and re-pins Playwright to
the base image's version:

```bash
docker compose -f docker-compose.yml \
               -f docker-compose.agent.yml \
               -f docker-compose.openhands.yml up --build
# override the model with OPENHANDS_MODEL=openrouter/<model>
```

If `openhands-ai` isn't installed, the factory **fails open** to the builtin
harness rather than break the agent. Add your own harness by implementing the
`AgentHarness` protocol and registering it (`register_harness`, or a
`sessionbridge.harnesses` entry point).

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
Note: some sites block by **IP reputation**, so requests from datacenter/cloud
IPs may get "Access Denied" regardless of headed/headless — run from a
residential connection for sites with aggressive bot walls.

## User flow
1. Open the web app — an isolated browser session starts automatically and the live viewer appears.
2. Type a URL to navigate yourself, or type a task in the chat and the agent drives the browser.
3. Grab control any time (click/type/scroll in the viewer) to solve a CAPTCHA or log in; the agent
   picks up from wherever you leave the page.

## Job states
`created → remote_browser_starting → ready → running → completed`
plus `failed`, `stopped_by_user`, `requires_user_intervention`. The chat is
multi-turn, so `completed` / `requires_user_intervention` return to `running` on
the next message.

## Deploy to AWS
`infra/terraform/` maps these boundaries to:
- public ALB → **webapp** (only public entry)
- **webapp/controlplane** in public subnets; **runner/llm** in private subnets (no public IP)
- runner SG: inbound only from the control-plane SG
- **Secrets Manager** OpenRouter key + **CloudWatch** logs
- internal service DNS via Cloud Map

```bash
cd infra/terraform
terraform init
terraform apply \
  -var image_webapp=<ECR_URI> -var image_controlplane=<ECR_URI> \
  -var image_runner=<ECR_URI> -var image_llm=<ECR_URI> \
  -var openrouter_api_key=<optional>
```

## Layout
```
services/
  webapp/        public UI + viewer (proxies to control plane only)
  controlplane/  job lifecycle + the bridge (jobs.py, app.py)
  runner/        Playwright + CDP screencast + agent harnesses (browser.py, app.py, harness/)
  llm/           optional vision-agent brain over a pluggable provider
shared/          schemas, state machine, security policy, redaction, lease, logging
infra/terraform/ AWS deployment (ECS/Fargate)
```
