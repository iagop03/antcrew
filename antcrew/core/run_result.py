"""RunResult — typed return value of team.run().

Backward-compatible with dict access: result['prd'], result.get('tickets'),
'prd' in result all work exactly as before.  New typed attributes expose
pipeline metadata: thread_id, cost_usd, state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
