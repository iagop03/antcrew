"""ManualActionCapability — pauses the pipeline until a human completes a manual step.

Unlike HitlReviewer (which asks for approval/rejection of an artifact), this
capability signals that the pipeline cannot continue until the human *does*
something: configures a third-party service, runs a deployment command, fills
in credentials, makes a business decision, etc.

Flow (when used with antcrew-platform):
  1. The platform injects a ``request_action`` callback at registry build time.
  2. When the Operator invokes ManualActionCapability, the callback fires:
       - emits a ``manual_action.required`` bus event
       - the platform listener creates a blocking Ticket in the DB
       - sets run.status = "blocked"
       - blocks the calling thread using a threading.Event
  3. The human sees the ticket in the dashboard, completes the step, marks the
     ticket "done" via PATCH /tickets/{ticket_id}/status.
  4. The platform calls resolve_manual_action(ticket_id), which fires the event.
  5. The callback returns, ManualActionCapability produces the
     ``manual_action_done`` condition, and the pipeline continues.

Without a callback (standalone / test use) the capability logs a warning and
continues immediately — no blocking.

Conditions gate:
  Set ``needs`` to control when the manual step happens relative to other
  capabilities. Default (empty) means the step runs before any other capability.
  Example — pause after code generation:
    needs=frozenset([ConditionId("implementation_exists")])
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any, Callable

from antcrew_engine.engine import (
    Artifact,
    ArtifactDelta,
    ArtifactId,
    ArtifactKind,
    CapabilityDescriptor,
    CapabilityResult,
    ConditionId,
)

from .base import BaseExecutor

log = logging.getLogger(__name__)


class ManualActionCapability(BaseExecutor):
    """Pauses the engine pipeline until a human completes a specified step."""

    descriptor = CapabilityDescriptor(
        name="manual_action",
        description=(
            "Pauses the pipeline until a human completes a specified manual step "
            "(configure a service, run a command, make a decision, etc.)"
        ),
        needs=frozenset(),
        produces=frozenset([ConditionId("manual_action_done")]),
        emits=frozenset(["ticket"]),
        cost=0.0,
    )

    def __init__(
        self,
        *,
        title: str = "Manual step required",
        description: str = "",
        assignee: str | None = None,
        needs: "frozenset[ConditionId] | None" = None,
        request_action: "Callable[[dict[str, Any]], None] | None" = None,
        llm=None,
    ):
        super().__init__(llm=llm)
        self._title = title
        self._description = description
        self._assignee = assignee
        self._request_action = request_action

        if needs is not None:
            self.descriptor = dataclasses.replace(self.descriptor, needs=needs)

    def _run(self, store, goal) -> CapabilityResult:
        content: dict[str, Any] = {
            "title": self._title,
            "description": self._description or goal.description,
            "assignee": self._assignee,
        }

        if self._request_action is not None:
            self._request_action(content)
        else:
            log.warning(
                "ManualActionCapability: no request_action callback provided — "
                "continuing without blocking. Title: %r", self._title,
            )

        signal = Artifact(
            id=ArtifactId("manual_action_done"),
            kind=ArtifactKind.MANUAL_ACTION,
            content=content,
            metadata={"resolved": True},
        )
        return CapabilityResult(delta=ArtifactDelta(created=(signal,)))
