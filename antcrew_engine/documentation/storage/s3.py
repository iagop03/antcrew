"""AWS S3 storage backend. Requires boto3."""
from __future__ import annotations

import json

from .base import BaseStorage


class S3Storage(BaseStorage):
    def __init__(self, bucket: str, prefix: str = "", region: str = "us-east-1") -> None:
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")
        try:
            import boto3
            self._s3 = boto3.client("s3", region_name=region)
        except ImportError:
            raise ImportError("boto3 is required for S3 storage: pip install boto3")

    def _key(self, doc_id: str) -> str:
        return f"{self.prefix}/{doc_id}".lstrip("/") if self.prefix else doc_id

    def _meta_key(self, doc_id: str) -> str:
        return self._key(f".meta/{doc_id}.json")

    def save(self, doc_id: str, content: bytes, metadata: dict) -> None:
        self._s3.put_object(Bucket=self.bucket, Key=self._key(doc_id), Body=content)
        self._s3.put_object(
            Bucket=self.bucket,
            Key=self._meta_key(doc_id),
            Body=json.dumps(metadata, default=str).encode(),
        )

    def load(self, doc_id: str) -> bytes:
        resp = self._s3.get_object(Bucket=self.bucket, Key=self._key(doc_id))
        return resp["Body"].read()

    def list_documents(self, prefix: str | None = None) -> list[str]:
        paginator = self._s3.get_paginator("list_objects_v2")
        s3_prefix = self._key(prefix or "")
        result: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=s3_prefix):
            for obj in page.get("Contents", []):
                key: str = obj["Key"]
                if "/.meta/" in key:
                    continue
                doc_id = key[len(self.prefix):].lstrip("/") if self.prefix else key
                result.append(doc_id)
        return sorted(result)

    def delete(self, doc_id: str) -> None:
        for key in (self._key(doc_id), self._meta_key(doc_id)):
            self._s3.delete_object(Bucket=self.bucket, Key=key)
