"""Safe filesystem path utilities for artifact writes.

Any command that writes LLM-generated file_paths to disk must go through
safe_artifact_path() to prevent path traversal attacks — e.g. a generated
file_path of "../../.ssh/authorized_keys" resolving outside the intended root.
"""
from __future__ import annotations

from pathlib import Path


def safe_artifact_path(rel: str, root: Path) -> Path:
    """Resolve *rel* safely inside *root*.

    Strips leading slashes/backslashes so the path is always treated as
    relative, then verifies the resolved result stays inside *root*.

    Raises:
        ValueError: if the resolved path escapes *root* (path traversal).

    Example::

        dest = safe_artifact_path(artifact["file_path"], output_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    """
    resolved_root = root.resolve()
    # Strip leading / or \ so Path() doesn't treat the value as absolute
    stripped = rel.lstrip("/\\")
    target = (root / stripped).resolve()
    if not target.is_relative_to(resolved_root):
        raise ValueError(
            f"Path traversal blocked: {rel!r} resolves to {target}, "
            f"which is outside the allowed root {resolved_root}"
        )
    return target
