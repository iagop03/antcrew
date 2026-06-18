"""
BaseChannel — minimum contract for any communication channel.

A channel handles two concerns:
  1. notify()          — fire-and-forget status messages
  2. send_for_review() — send an artifact and wait for human approval/edit/rejection

Concrete implementations: TelegramChannel, SlackChannel, TerminalChannel, …
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class BaseChannel(ABC):
    """
    Minimum interface that any notification + HITL channel must implement.

    Usage on an agent:
        agent = BackendDevAgent(
            llm=AnthropicModel(),
            channel=TelegramChannel(token="...", chat_id=CTO_CHAT),
            approval_required=True,
        )

    Custom channel example:
        class SlackChannel(BaseChannel):
            async def notify(self, message, **kwargs): ...
            async def send_for_review(self, artifact, ...): ...
    """

    # ------------------------------------------------------------------
    # Notifications (no response expected)
    # ------------------------------------------------------------------

    @abstractmethod
    async def notify(self, message: str, **kwargs) -> None:
        """Send a status or informational message. No reply expected."""

    # ------------------------------------------------------------------
    # Human-in-the-loop
    # ------------------------------------------------------------------

    @abstractmethod
    async def send_for_review(
        self,
        artifact,
        agent_name: str,
        session_id: str,
        response_options: Optional[list[str]] = None,
    ) -> dict:
        """
        Present ``artifact`` to a human reviewer and wait for their decision.

        Args:
            artifact:         The artifact to review (PRD, list[Ticket], …).
            agent_name:       Name of the agent that produced the artifact.
            session_id:       Unique identifier for this pipeline run.
            response_options: Allowed choices (default: ["approve","edit","reject"]).

        Returns:
            {"decision": "approve" | "reject" | <custom>, "edited": str | None}
        """
