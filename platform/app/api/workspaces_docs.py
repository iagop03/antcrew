"""Workspace documentation — config, upload, list, download, delete, schema.

Storage backends (in priority order):
  1. S3  — when workspace.docs_s3_bucket is set.
  2. Local filesystem — when DOCS_LOCAL_PATH env var is set (self-hosted without S3).
  3. None — 422 on upload/download/list.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import WorkspaceContext, get_workspace_context, require_api_key, require_role, ws_accessible
from app.core.database import get_session
from app.core.exceptions import WorkspaceNotFoundError
from app.models.workspace import Workspace

router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
    dependencies=[Depends(require_api_key)],
)

# ---------------------------------------------------------------------------
# Encryption helpers (reuse the same Fernet key used for Slack tokens)
# ---------------------------------------------------------------------------

def _encrypt(value: str) -> str:
    import os

    from cryptography.fernet import Fernet
    key = os.environ.get("DOCS_ENCRYPTION_KEY") or os.environ.get("SLACK_TOKEN_ENCRYPTION_KEY", "")
    if not key:
        return value  # dev mode: store plaintext
    return Fernet(key.encode() if isinstance(key, str) else key).encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    import os

    from cryptography.fernet import Fernet
    key = os.environ.get("DOCS_ENCRYPTION_KEY") or os.environ.get("SLACK_TOKEN_ENCRYPTION_KEY", "")
    if not key:
        return value
    try:
        return Fernet(key.encode() if isinstance(key, str) else key).decrypt(value.encode()).decode()
    except Exception:
        return value


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class DocsS3Config(BaseModel):
    bucket: Optional[str] = None
    prefix: Optional[str] = None
    region: Optional[str] = "us-east-1"
    access_key: Optional[str] = None      # plaintext on write; masked on read
    secret_key: Optional[str] = None      # plaintext on write; masked on read


class DocsS3ConfigPublic(BaseModel):
    bucket: Optional[str]
    prefix: Optional[str]
    region: Optional[str]
    access_key_configured: bool
    secret_key_configured: bool


class DocsSchemaBody(BaseModel):
    schema_yaml: str


class DocEntry(BaseModel):
    doc_id: str
    doc_type: Optional[str] = None
    source_file: Optional[str] = None
    size_bytes: Optional[int] = None
    uploaded_at: Optional[str] = None


# ---------------------------------------------------------------------------
# S3 config (GET / PUT)
# ---------------------------------------------------------------------------

@router.get("/{workspace_id}/docs/config", response_model=DocsS3ConfigPublic,
            dependencies=[Depends(require_role("admin", "write", "read"))])
async def get_docs_config(
    workspace_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> DocsS3ConfigPublic:
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "Workspace not accessible")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    return DocsS3ConfigPublic(
        bucket=ws.docs_s3_bucket,
        prefix=ws.docs_s3_prefix,
        region=ws.docs_s3_region,
        access_key_configured=bool(ws.docs_s3_access_key_enc),
        secret_key_configured=bool(ws.docs_s3_secret_key_enc),
    )


@router.put("/{workspace_id}/docs/config", response_model=DocsS3ConfigPublic,
            dependencies=[Depends(require_role("admin"))])
async def set_docs_config(
    workspace_id: int,
    body: DocsS3Config,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> DocsS3ConfigPublic:
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "Workspace not accessible")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)

    if body.bucket is not None:
        ws.docs_s3_bucket = body.bucket or None
    if body.prefix is not None:
        ws.docs_s3_prefix = body.prefix or None
    if body.region is not None:
        ws.docs_s3_region = body.region or "us-east-1"
    if body.access_key is not None:
        ws.docs_s3_access_key_enc = _encrypt(body.access_key) if body.access_key else None
    if body.secret_key is not None:
        ws.docs_s3_secret_key_enc = _encrypt(body.secret_key) if body.secret_key else None

    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return DocsS3ConfigPublic(
        bucket=ws.docs_s3_bucket,
        prefix=ws.docs_s3_prefix,
        region=ws.docs_s3_region,
        access_key_configured=bool(ws.docs_s3_access_key_enc),
        secret_key_configured=bool(ws.docs_s3_secret_key_enc),
    )


@router.delete("/{workspace_id}/docs/config", status_code=204,
               dependencies=[Depends(require_role("admin"))])
async def clear_docs_config(
    workspace_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "Workspace not accessible")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    ws.docs_s3_bucket = None
    ws.docs_s3_prefix = None
    ws.docs_s3_region = None
    ws.docs_s3_access_key_enc = None
    ws.docs_s3_secret_key_enc = None
    session.add(ws)
    await session.commit()


# ---------------------------------------------------------------------------
# Schema YAML (GET / PUT)
# ---------------------------------------------------------------------------

@router.get("/{workspace_id}/docs/schema",
            dependencies=[Depends(require_role("admin", "write", "read"))])
async def get_docs_schema(
    workspace_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "Workspace not accessible")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    return {"schema_yaml": ws.docs_schema_yaml or ""}


@router.put("/{workspace_id}/docs/schema",
            dependencies=[Depends(require_role("admin"))])
async def set_docs_schema(
    workspace_id: int,
    body: DocsSchemaBody,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "Workspace not accessible")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)

    # Validate YAML is parseable before saving
    try:
        import yaml
        yaml.safe_load(body.schema_yaml)
    except Exception as exc:
        raise HTTPException(400, f"Invalid YAML: {exc}")

    ws.docs_schema_yaml = body.schema_yaml
    session.add(ws)
    await session.commit()
    return {"schema_yaml": ws.docs_schema_yaml}


# ---------------------------------------------------------------------------
# Document list (from S3)
# ---------------------------------------------------------------------------

@router.get("/{workspace_id}/docs",
            response_model=list[DocEntry],
            dependencies=[Depends(require_role("admin", "write", "read"))])
async def list_docs(
    workspace_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> list[DocEntry]:
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "Workspace not accessible")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)

    storage = _build_storage(ws)
    if storage is None:
        return []

    try:
        doc_ids = storage.list_documents()
    except Exception as exc:
        raise HTTPException(502, f"Storage error: {exc}")

    entries: list[DocEntry] = []
    for doc_id in doc_ids:
        meta: dict = {}
        if hasattr(storage, "load_metadata"):
            try:
                meta = storage.load_metadata(doc_id)
            except Exception:
                pass
        entries.append(DocEntry(
            doc_id=doc_id,
            doc_type=meta.get("doc_type"),
            source_file=meta.get("source_file"),
            size_bytes=meta.get("size_bytes"),
            uploaded_at=meta.get("uploaded_at"),
        ))
    return entries


# ---------------------------------------------------------------------------
# Upload a document to S3
# ---------------------------------------------------------------------------

@router.post("/{workspace_id}/docs/upload",
             dependencies=[Depends(require_role("admin", "write"))])
async def upload_doc(
    workspace_id: int,
    doc_type: str,
    file: UploadFile = File(...),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "Workspace not accessible")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)

    storage = _build_storage(ws)
    if storage is None:
        raise HTTPException(422, "No storage configured. Set S3 config or DOCS_LOCAL_PATH.")

    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")

    from datetime import datetime, timezone
    filename = file.filename or "document"
    doc_id = f"{doc_type}/{filename}"
    metadata = {
        "doc_type": doc_type,
        "source_file": filename,
        "size_bytes": len(content),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "content_type": file.content_type or "application/octet-stream",
    }

    try:
        storage.save(doc_id, content, metadata)
    except Exception as exc:
        raise HTTPException(502, f"Storage upload failed: {exc}")

    return {"doc_id": doc_id, "doc_type": doc_type, "filename": filename, "size_bytes": len(content)}


# ---------------------------------------------------------------------------
# Download a document
# ---------------------------------------------------------------------------

@router.get("/{workspace_id}/docs/{doc_id:path}/download",
            dependencies=[Depends(require_role("admin", "write", "read"))])
async def download_doc(
    workspace_id: int,
    doc_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> Response:
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "Workspace not accessible")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)

    storage = _build_storage(ws)
    if storage is None:
        raise HTTPException(422, "No storage configured")

    try:
        content = storage.load(doc_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Document not found: {doc_id}")
    except Exception as exc:
        raise HTTPException(502, f"Storage read failed: {exc}")

    meta: dict = {}
    if hasattr(storage, "load_metadata"):
        try:
            meta = storage.load_metadata(doc_id)
        except Exception:
            pass

    content_type = meta.get("content_type", "application/octet-stream")
    filename = meta.get("source_file", doc_id.split("/")[-1])
    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Delete a document
# ---------------------------------------------------------------------------

@router.delete("/{workspace_id}/docs/{doc_id:path}", status_code=204,
               dependencies=[Depends(require_role("admin"))])
async def delete_doc(
    workspace_id: int,
    doc_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "Workspace not accessible")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)

    storage = _build_storage(ws)
    if storage is None:
        raise HTTPException(422, "No storage configured")

    try:
        storage.delete(doc_id)
    except Exception as exc:
        raise HTTPException(502, f"Storage delete failed: {exc}")


# ---------------------------------------------------------------------------
# Index documents from S3 into the in-memory docs manager (trigger)
# ---------------------------------------------------------------------------

@router.post("/{workspace_id}/docs/index",
             dependencies=[Depends(require_role("admin", "write"))])
async def trigger_index(
    workspace_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-index all documents from this workspace's S3 bucket."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "Workspace not accessible")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    try:
        storage = _build_storage(ws)
        if storage is None:
            raise HTTPException(422, "No storage configured")
        doc_mgr = _build_doc_manager_from_storage(storage, ws.docs_schema_yaml)
        indexed = doc_mgr.index_from_storage()
        return {"indexed": len(indexed), "doc_ids": indexed}
    except Exception as exc:
        raise HTTPException(502, f"Indexing failed: {exc}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_storage(ws: Workspace):
    """Return the storage backend for this workspace, or None if unconfigured.

    Priority: S3 (when bucket set) → LocalFileStorage (when DOCS_LOCAL_PATH set).
    """
    if ws.docs_s3_bucket:
        from antcrew_engine.documentation.storage.s3 import S3Storage
        return S3Storage(
            bucket=ws.docs_s3_bucket,
            prefix=ws.docs_s3_prefix or "",
            region=ws.docs_s3_region or "us-east-1",
            aws_access_key_id=_decrypt(ws.docs_s3_access_key_enc) if ws.docs_s3_access_key_enc else None,
            aws_secret_access_key=_decrypt(ws.docs_s3_secret_key_enc) if ws.docs_s3_secret_key_enc else None,
        )
    local_root = os.environ.get("DOCS_LOCAL_PATH")
    if local_root:
        from antcrew_engine.documentation.storage.local import LocalFileStorage
        import os as _os
        workspace_root = _os.path.join(local_root, str(ws.id))
        return LocalFileStorage(root=workspace_root)
    return None


def build_doc_manager_for_workspace(ws: Workspace):
    """Build a DocumentationManager from workspace storage config + schema.

    Returns None when no storage backend is available.
    Used by engine_runner and pipeline runner to inject docs into agents.
    """
    storage = _build_storage(ws)
    if storage is None:
        return None
    return _build_doc_manager_from_storage(storage, ws.docs_schema_yaml)


def _build_doc_manager_from_storage(storage, schema_yaml: str | None = None):
    from antcrew_engine.documentation import DocumentationManager

    mgr = DocumentationManager()  # defaults to local dummy; we replace storage below
    mgr.storage = storage

    if schema_yaml:
        try:
            import yaml
            mgr.load_schema_from_dict(yaml.safe_load(schema_yaml))
        except Exception:
            pass

    return mgr
