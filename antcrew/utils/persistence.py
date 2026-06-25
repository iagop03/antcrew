"""
State persistence — save / load a LangGraph TeamState to / from JSON.

Usage:
    from antcrew.utils.persistence import save_state, load_state

    # After a run:
    state = team.run("Build auth module")
    save_state(state, "output/run_2024-01-01.json")

    # Later, inspect without running again:
    raw = load_state("output/run_2024-01-01.json")
    print(raw["prd"]["title"])
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _encode(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, list):
        return [_encode(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _encode(v) for k, v in obj.items()}
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def save_state(state, path: str | Path, *, indent: int = 2) -> None:
    """
    Serialize a TeamState or RunResult to a JSON file.

    All Pydantic models are converted via ``model_dump()``.
    Unknown types fall back to ``str()``.
    """
    raw = getattr(state, "state", None)
    if isinstance(raw, dict):
        state = raw
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(_encode(state), indent=indent, ensure_ascii=False),
        encoding="utf-8",
    )


def load_state(path: str | Path) -> dict:
    """
    Load a JSON file saved by :func:`save_state`.

    Returns a plain ``dict`` — Pydantic objects are **not** re-hydrated.
    To get Pydantic objects back, validate each field manually:

        from antcrew.core.artifacts import PRD
        prd = PRD.model_validate(raw["prd"])
    """
    return json.loads(Path(path).read_text(encoding="utf-8"))
