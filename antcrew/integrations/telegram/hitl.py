"""
Async bridge between Telegram callbacks and the LangGraph pipeline.

The pipeline coroutine calls `create()` + `wait()` to pause at a checkpoint.
Telegram callback/message handlers call `resolve()` to resume it.

Thread safety: all methods are called from the same asyncio event loop,
so a plain dict is sufficient (no locks needed).
"""
from __future__ import annotations

import asyncio
from typing import Literal, Optional

HitlDecision = Literal["approve", "reject", "edit"]


class HitlManager:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue] = {}
        self._edit_waiting: set[str] = set()

    # ------------------------------------------------------------------
    # Core checkpoint flow
    # ------------------------------------------------------------------

    def create(self, session_id: str) -> None:
        """Open a checkpoint for session_id. Must be called before sending the artifact."""
        self._queues[session_id] = asyncio.Queue(maxsize=1)

    async def wait(self, session_id: str) -> dict:
        """Block until `resolve()` is called for session_id."""
        q = self._queues.get(session_id)
        if q is None:
            raise RuntimeError(f"No pending HITL checkpoint for session {session_id!r}")
        result = await q.get()
        self._queues.pop(session_id, None)
        return result

    async def resolve(
        self,
        session_id: str,
        decision: HitlDecision,
        edited_json: Optional[str] = None,
    ) -> None:
        """Signal the waiting pipeline coroutine with the user's decision."""
        q = self._queues.get(session_id)
        if q:
            await q.put({"decision": decision, "edited": edited_json})

    # ------------------------------------------------------------------
    # Edit-text flow
    # ------------------------------------------------------------------

    def set_edit_waiting(self, session_id: str) -> None:
        """Mark session as expecting a follow-up text message with edited JSON."""
        self._edit_waiting.add(session_id)

    def pop_edit_waiting(self, session_id: str) -> bool:
        """Return True and remove if session is waiting for an edit reply."""
        if session_id in self._edit_waiting:
            self._edit_waiting.discard(session_id)
            return True
        return False

    def is_pending(self, session_id: str) -> bool:
        return session_id in self._queues
