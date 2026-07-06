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
