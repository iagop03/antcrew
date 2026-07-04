"""RunResult — typed return value of team.run().

Backward-compatible with dict access: result['prd'], result.get('tickets'),
'prd' in result all work exactly as before.  New typed attributes expose
pipeline metadata: thread_id, cost_usd, state.

JSON serialization:
    result.to_dict()  → JSON-serializable dict (Pydantic models → dicts)
    result.to_json()  → JSON string
    result.to_json(include_private=True)  → includes _run_id, _kb_context, …
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


def _serialize(obj: Any) -> Any:
    """Recursively convert pipeline state values to JSON-serializable types.

    - Pydantic v2 BaseModel instances → .model_dump()
    - lists / tuples → serialize each element
    - dicts → serialize each value
    - str, int, float, bool, None → as-is
    - Everything else → str(obj) so serialization never fails
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    # Pydantic v2
    if hasattr(obj, "model_dump"):
        return _serialize(obj.model_dump())
    if isinstance(obj, (list, tuple)):
        return [_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return str(obj)


@dataclass
class RunResult:
    """Result of a single team pipeline run.

    Attributes:
        state:     Full pipeline output (TeamState dict).
        thread_id: LangGraph thread used for this run.
        cost_usd:  Estimated API cost; 0.0 when not tracked.
    """

    state: dict
    thread_id: str = "default"
    cost_usd: float = 0.0

    # ── Dict-like interface — backward compatibility ──────────────────────────

    def __getitem__(self, key: str) -> Any:
        return self.state[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.state[key] = value

    def __contains__(self, key: object) -> bool:
        return key in self.state

    def __iter__(self):
        return iter(self.state)

    def get(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, default)

    def keys(self):
        return self.state.keys()

    def values(self):
        return self.state.values()

    def items(self):
        return self.state.items()

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self, *, include_private: bool = False) -> dict:
        """Return a JSON-serializable representation of this run.

        Pydantic models (PRD, Ticket, CodeArtifact, …) are converted to plain
        dicts via ``.model_dump()``.  Any other non-serializable object falls
        back to ``str(obj)``.

        Args:
            include_private: When True, keys starting with ``_`` (e.g.
                ``_run_id``, ``_kb_context``, ``_thread_id``) are included.
                Default False keeps the payload lean for storage.
        """
        serialized_state = {
            k: _serialize(v)
            for k, v in self.state.items()
            if include_private or not k.startswith("_")
        }
        return {
            "run_id": self.state.get("_run_id"),
            "thread_id": self.thread_id,
            "cost_usd": self.cost_usd,
            "state": serialized_state,
        }

    def to_json(self, *, include_private: bool = False, **json_kwargs) -> str:
        """Return a JSON string.  Accepts any ``json.dumps`` keyword args."""
        return json.dumps(self.to_dict(include_private=include_private), **json_kwargs)
