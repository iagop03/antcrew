"""Pluggable artifact storage backend for antcrew-platform.

Configure via the ARTIFACT_STORAGE_URL environment variable:

  (unset / empty)    — inline mode: content is embedded in Run.state (default, backward-compat)
  file:///abs/path   — local filesystem under /abs/path/{run_id}/{file_path}
  s3://bucket/prefix — Amazon S3, requires boto3 (pip install boto3)

Storage keys stored in Run.state use the same scheme as ARTIFACT_STORAGE_URL so they
are self-describing and portable across backend changes. An entry without a storage_key
field uses the old inline format (content embedded directly).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class ArtifactBackend(Protocol):
    async def put(self, run_id: str, file_path: str, content: str) -> str:
        """Store *content* and return an opaque storage key."""
        ...

    async def get(self, key: str) -> str:
        """Retrieve content by storage key."""
        ...


class _InlineBackend:
    """No-op sentinel — content stays embedded in Run.state."""

    async def put(self, run_id: str, file_path: str, content: str) -> str:
        return ""

    async def get(self, key: str) -> str:  # pragma: no cover
        raise RuntimeError("InlineBackend has no external storage")


class _LocalBackend:
    """Write artifacts to a directory tree on local disk."""

    def __init__(self, root: str) -> None:
        self._root = Path(root)

    async def put(self, run_id: str, file_path: str, content: str) -> str:
        dest = self._root / run_id / file_path
        await asyncio.to_thread(_write_file, dest, content)
        return f"file://{dest.as_posix()}"

    async def get(self, key: str) -> str:
        path = Path(key[len("file://"):])
        return await asyncio.to_thread(path.read_text, "utf-8")


class _S3Backend:
    """Store artifacts in an Amazon S3 bucket (requires boto3)."""

    def __init__(self, bucket: str, prefix: str) -> None:
        self._bucket = bucket
        self._prefix = prefix.rstrip("/")

    def _client(self):
        import boto3  # type: ignore[import]
        kw: dict = {}
        endpoint = os.environ.get("AWS_ENDPOINT_URL")
        if endpoint:
            import botocore.config  # type: ignore[import]
            kw["endpoint_url"] = endpoint
            # B2 and other S3-compatible APIs require path-style addressing.
            kw["config"] = botocore.config.Config(s3={"addressing_style": "path"})
        return boto3.client("s3", **kw)

    async def put(self, run_id: str, file_path: str, content: str) -> str:
        key = f"{self._prefix}/{run_id}/{file_path}"
        client = self._client()
        await asyncio.to_thread(
            client.put_object, Bucket=self._bucket, Key=key, Body=content.encode()
        )
        return f"s3://{self._bucket}/{key}"

    async def get(self, storage_key: str) -> str:
        rest = storage_key[len("s3://"):]
        bucket, _, s3_key = rest.partition("/")
        client = self._client()
        obj = await asyncio.to_thread(client.get_object, Bucket=bucket, Key=s3_key)
        return obj["Body"].read().decode()


def _write_file(dest: Path, content: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")


_BACKEND: ArtifactBackend | None = None


def get_backend() -> ArtifactBackend:
    """Return the configured backend, initialised once per process."""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    url = os.environ.get("ARTIFACT_STORAGE_URL", "").strip()
    if not url:
        _BACKEND = _InlineBackend()
    elif url.startswith("file://"):
        _BACKEND = _LocalBackend(url[len("file://"):])
    elif url.startswith("s3://"):
        rest = url[len("s3://"):]
        bucket, _, prefix = rest.partition("/")
        _BACKEND = _S3Backend(bucket, prefix or "artifacts")
    else:
        raise ValueError(f"Unknown ARTIFACT_STORAGE_URL scheme: {url!r}")
    return _BACKEND


def is_inline() -> bool:
    """True when the backend is the default inline (content-in-state) mode."""
    return isinstance(get_backend(), _InlineBackend)


# State keys whose list items carry a large ``content`` string field.
_CONTENT_BEARING_KEYS = ("code_artifacts", "test_artifacts", "devops_artifacts", "doc_artifacts")


async def externalize_artifacts(state: dict, run_id: str) -> dict:
    """Offload large artifact content from *state* to the configured backend.

    When ``ARTIFACT_STORAGE_URL`` is unset (inline mode) this is a no-op and
    the original *state* dict is returned unchanged — zero overhead.

    Otherwise each item in the known artifact list keys that has a non-empty
    ``content`` string (and no existing ``storage_key``) is uploaded to the
    backend.  The item is mutated in-place: ``content`` is cleared and
    ``storage_key`` is added so the record stays self-describing.

    The returned dict is a shallow copy of *state* (list values are new lists
    so callers can safely continue using the original).
    """
    backend = get_backend()
    if isinstance(backend, _InlineBackend):
        return state

    state = {**state}
    for key in _CONTENT_BEARING_KEYS:
        items = state.get(key)
        if not items or not isinstance(items, list):
            continue
        new_items = []
        for item in items:
            if not isinstance(item, dict):
                new_items.append(item)
                continue
            content = item.get("content") or ""
            file_path = item.get("file_path") or f"{key}/item"
            if content and not item.get("storage_key"):
                try:
                    storage_key = await backend.put(run_id, file_path, content)
                    item = {**item, "content": "", "storage_key": storage_key}
                except Exception:
                    pass  # best-effort; keep content inline on failure
            new_items.append(item)
        state[key] = new_items
    return state
