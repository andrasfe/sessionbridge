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
    ARTIFACT_URL = _env("ARTIFACT_URL", "http://localhost:8084")

    # WS variants (derived from HTTP base unless overridden).
    CONTROL_PLANE_WS = _env("CONTROL_PLANE_WS", "") or CONTROL_PLANE_URL.replace("http", "ws", 1)
    RUNNER_WS = _env("RUNNER_WS", "") or RUNNER_URL.replace("http", "ws", 1)

    # Target site. Defaults to T-Mobile but overridable so the same remote
    # browser can be pointed at any site the user has a legitimate account on.
    START_URL = _env("START_URL", "https://www.t-mobile.com/signin")
    ALLOWED_DOMAINS = _env("ALLOWED_DOMAINS", "t-mobile.com,tmobile.com")
    # Which deterministic connector runs after login: tmobile | researchgate
    CONNECTOR = _env("CONNECTOR", "tmobile")

    # Lease lifetime (seconds).
    LEASE_TTL = int(_env("LEASE_TTL", "1800"))

    # Artifact limits / storage.
    MAX_ARTIFACT_BYTES = int(_env("MAX_ARTIFACT_BYTES", str(50 * 1024 * 1024)))
    ARTIFACT_BACKEND = _env("ARTIFACT_BACKEND", "local")  # local | s3
    ARTIFACT_LOCAL_DIR = _env("ARTIFACT_LOCAL_DIR", "/data/artifacts")
    ARTIFACT_S3_BUCKET = _env("ARTIFACT_S3_BUCKET", "")
    ARTIFACT_KMS_KEY_ID = _env("ARTIFACT_KMS_KEY_ID", "")
    METADATA_BACKEND = _env("METADATA_BACKEND", "local")  # local | dynamodb
    METADATA_LOCAL_DIR = _env("METADATA_LOCAL_DIR", "/data/metadata")
    METADATA_DDB_TABLE = _env("METADATA_DDB_TABLE", "")

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
