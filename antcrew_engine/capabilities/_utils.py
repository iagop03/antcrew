"""Shared utilities for capability implementations."""
from __future__ import annotations

import json
import re
from typing import Any


def parse_json(raw: str) -> Any:
    """Parse JSON from LLM output, stripping markdown fences if present."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
    raw = re.sub(r"\n?```\s*$", "", raw)
    return json.loads(raw.strip())


def head(content: object, max_lines: int = 60) -> str:
    """Return up to max_lines of a source file — imports + top-level signatures."""
    text  = content if isinstance(content, str) else ""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + f"\n# ... ({len(lines) - max_lines} more lines)"
