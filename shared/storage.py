"""Pluggable artifact + metadata storage.

Local backends are used for the PoC; S3 (SSE-KMS) and DynamoDB backends make the
same artifact service deployable to AWS by changing env vars only. boto3 is
imported lazily so the local PoC has no AWS dependency.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod

from .config import settings


# ---------------------------------------------------------------------------
# Blob storage (the PDF bytes)
# ---------------------------------------------------------------------------
class BlobStore(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...


class LocalBlobStore(BlobStore):
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, key: str) -> str:
        return os.path.join(self.root, key)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        with open(self._path(key), "wb") as f:
            f.write(data)

    def get(self, key: str) -> bytes:
        with open(self._path(key), "rb") as f:
            return f.read()


class S3BlobStore(BlobStore):
    """Server-side encryption with KMS is mandatory for artifacts."""

    def __init__(self, bucket: str, kms_key_id: str):
        import boto3  # lazy

        self.bucket = bucket
        self.kms_key_id = kms_key_id
        self.s3 = boto3.client("s3")

    def put(self, key: str, data: bytes, content_type: str) -> None:
        extra = {"ServerSideEncryption": "aws:kms", "ContentType": content_type}
        if self.kms_key_id:
            extra["SSEKMSKeyId"] = self.kms_key_id
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)

    def get(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()


# ---------------------------------------------------------------------------
# Metadata storage (safe, non-secret artifact metadata)
# ---------------------------------------------------------------------------
class MetadataStore(ABC):
    @abstractmethod
    def put(self, artifact_id: str, meta: dict) -> None: ...

    @abstractmethod
    def get(self, artifact_id: str) -> dict | None: ...


class LocalMetadataStore(MetadataStore):
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, aid: str) -> str:
        return os.path.join(self.root, f"{aid}.json")

    def put(self, artifact_id: str, meta: dict) -> None:
        with open(self._path(artifact_id), "w") as f:
            json.dump(meta, f, indent=2)

    def get(self, artifact_id: str) -> dict | None:
        try:
            with open(self._path(artifact_id)) as f:
                return json.load(f)
        except FileNotFoundError:
            return None


class DynamoMetadataStore(MetadataStore):
    def __init__(self, table: str):
        import boto3  # lazy

        self.table = boto3.resource("dynamodb").Table(table)

    def put(self, artifact_id: str, meta: dict) -> None:
        self.table.put_item(Item={"artifact_id": artifact_id, **meta})

    def get(self, artifact_id: str) -> dict | None:
        resp = self.table.get_item(Key={"artifact_id": artifact_id})
        return resp.get("Item")


def make_blob_store() -> BlobStore:
    if settings.ARTIFACT_BACKEND == "s3":
        return S3BlobStore(settings.ARTIFACT_S3_BUCKET, settings.ARTIFACT_KMS_KEY_ID)
    return LocalBlobStore(settings.ARTIFACT_LOCAL_DIR)


def make_metadata_store() -> MetadataStore:
    if settings.METADATA_BACKEND == "dynamodb":
        return DynamoMetadataStore(settings.METADATA_DDB_TABLE)
    return LocalMetadataStore(settings.METADATA_LOCAL_DIR)
