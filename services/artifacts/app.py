"""Artifact service.

Validates and stores the downloaded PDF plus safe, non-secret metadata, and
exposes a download link. Storage is pluggable: local FS + JSON for the PoC,
S3 (SSE-KMS) + DynamoDB on AWS via env vars only.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import time

from fastapi import FastAPI, Form, HTTPException, UploadFile, File
from fastapi.responses import Response

from shared.config import settings
from shared.logging import log
from shared import security
from shared.schemas import ArtifactMetadata
from shared.storage import make_blob_store, make_metadata_store

app = FastAPI(title="sessionbridge-artifacts")

BLOBS = make_blob_store()
META = make_metadata_store()


def _safe_filename(name: str) -> str:
    name = os.path.basename(name or "statement.pdf")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name[:120]


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/artifacts", response_model=ArtifactMetadata)
async def create_artifact(
    file: UploadFile = File(...),
    job_id: str = Form(...),
    source_host: str = Form(...),
    statement_date: str = Form(""),
):
    data = await file.read()

    # ---- validation (artifact success criteria) ----
    if not data:
        raise HTTPException(422, "empty file")
    if len(data) > settings.MAX_ARTIFACT_BYTES:
        raise HTTPException(422, "file exceeds size limit")
    if not data[:5].startswith(b"%PDF-"):
        raise HTTPException(422, "not a PDF")
    if not security.host_allowed(source_host):
        raise HTTPException(422, "source host not on an allowed domain")

    sha = hashlib.sha256(data).hexdigest()
    filename = _safe_filename(file.filename)
    artifact_id = "art_" + secrets.token_hex(8)

    BLOBS.put(artifact_id, data, "application/pdf")
    meta = ArtifactMetadata(
        artifact_id=artifact_id, job_id=job_id, filename=filename,
        size_bytes=len(data), sha256=sha, content_type="application/pdf",
        source_host=source_host, statement_date=statement_date or None,
        validation_status="valid", created_at=time.time(),
    )
    META.put(artifact_id, meta.model_dump())
    log("artifacts", "stored", job_id=job_id, artifact_id=artifact_id,
        size=len(data), sha256=sha)
    return meta


@app.get("/artifacts/{artifact_id}", response_model=ArtifactMetadata)
async def get_meta(artifact_id: str):
    meta = META.get(artifact_id)
    if not meta:
        raise HTTPException(404, "no artifact")
    return ArtifactMetadata(**meta)


@app.get("/artifacts/{artifact_id}/download")
async def download(artifact_id: str):
    meta = META.get(artifact_id)
    if not meta:
        raise HTTPException(404, "no artifact")
    data = BLOBS.get(artifact_id)
    return Response(
        content=data, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{meta["filename"]}"'},
    )
