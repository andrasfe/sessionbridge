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

    # LLM (optional). Disabled unless a key is present.
    OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL = _env("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    LLM_ENABLED = bool(OPENROUTER_API_KEY)

    # Browser runner.
    HEADLESS = _env("HEADLESS", "true").lower() == "true"
    VIEWPORT_W = int(_env("VIEWPORT_W", "1280"))
    VIEWPORT_H = int(_env("VIEWPORT_H", "800"))
    SCREENCAST_QUALITY = int(_env("SCREENCAST_QUALITY", "60"))


settings = Settings()
