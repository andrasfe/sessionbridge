"""Environment-driven configuration.

Every cross-service URL is configurable so the exact same code runs under
docker-compose (service DNS names) and AWS ECS (service discovery / ALB DNS).
"""
from __future__ import annotations

import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


class Settings:
    # Service-to-service base URLs.
    CONTROL_PLANE_URL = _env("CONTROL_PLANE_URL", "http://localhost:8081")
    RUNNER_URL = _env("RUNNER_URL", "http://localhost:8082")
    LLM_URL = _env("LLM_URL", "http://localhost:8083")

    # WS variants (derived from HTTP base unless overridden).
    CONTROL_PLANE_WS = _env("CONTROL_PLANE_WS", "") or CONTROL_PLANE_URL.replace("http", "ws", 1)
    RUNNER_WS = _env("RUNNER_WS", "") or RUNNER_URL.replace("http", "ws", 1)

    # Generic browser agent: starts blank and (by default) may browse anywhere.
    # Lock it down by setting ALLOWED_DOMAINS to a comma-separated suffix list.
    START_URL = _env("START_URL", "about:blank")
    ALLOWED_DOMAINS = _env("ALLOWED_DOMAINS", "*")

    # Lease lifetime (seconds).
    LEASE_TTL = int(_env("LEASE_TTL", "1800"))

    # LLM provider selection is handled entirely by services/llm/llm_providers
    # (LLM_PROVIDER + per-provider keys); nothing LLM-related lives here.

    # Browser runner.
    HEADLESS = _env("HEADLESS", "true").lower() == "true"
    VIEWPORT_W = int(_env("VIEWPORT_W", "1280"))
    VIEWPORT_H = int(_env("VIEWPORT_H", "800"))
    SCREENCAST_QUALITY = int(_env("SCREENCAST_QUALITY", "60"))
    # Seconds between viewer frames (screenshot stream). ~0.2 = 5 fps.
    STREAM_INTERVAL = float(_env("STREAM_INTERVAL", "0.2"))


settings = Settings()
