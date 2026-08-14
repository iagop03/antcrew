"""FlexibleHITL — callback-based human-in-the-loop approval gate."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from antcrew.core.state import TeamState

logger = logging.getLogger(__name__)


class HITLAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"
    SKIP = "skip"


@dataclass
class HITLDecision:
    action: HITLAction
    reason: str = ""
    modified_state: dict | None = None

    @property
    def approved(self) -> bool:
        return self.action in (HITLAction.APPROVE, HITLAction.MODIFY, HITLAction.SKIP)


ApprovalCallback = Callable[[str, TeamState], HITLDecision]


class FlexibleHITL:
    """Human-in-the-loop gate with pluggable approval callbacks.

    Usage::

        def my_reviewer(checkpoint: str, state: TeamState) -> HITLDecision:
            print(f"Review {checkpoint}: {state.get('prd')}")
            choice = input("Approve? [y/n]: ")
            return HITLDecision(action=HITLAction.APPROVE if choice == "y" else HITLAction.REJECT)

        hitl = FlexibleHITL(callback=my_reviewer)

        # In a workflow:
        if not hitl.gate("prd_review", state):
            raise RuntimeError("PRD rejected by reviewer")
    """

    def __init__(
        self,
        callback: ApprovalCallback | None = None,
        *,
        auto_approve: bool = False,
        checkpoints: list[str] | None = None,
    ) -> None:
        self._callback = callback
        self._auto_approve = auto_approve
        self._checkpoints = set(checkpoints) if checkpoints else None
        self._history: list[tuple[str, HITLDecision]] = []

    def register(self, callback: ApprovalCallback) -> None:
        """Replace the approval callback at runtime."""
        self._callback = callback

    def gate(self, checkpoint: str, state: TeamState) -> bool:
        """Run the HITL gate for *checkpoint*.

        Returns True if execution should proceed, False to halt.
        Applies ``modified_state`` onto *state* in-place when action is MODIFY.
        """
        if self._checkpoints is not None and checkpoint not in self._checkpoints:
            return True

        if self._auto_approve or self._callback is None:
            decision = HITLDecision(action=HITLAction.APPROVE, reason="auto")
        else:
            try:
                decision = self._callback(checkpoint, state)
            except Exception as exc:
                logger.warning("HITL callback raised %r — defaulting to APPROVE", exc)
                decision = HITLDecision(action=HITLAction.APPROVE, reason=f"callback_error: {exc}")

        self._history.append((checkpoint, decision))

        if decision.action == HITLAction.MODIFY and decision.modified_state:
            state.update(decision.modified_state)

        if not decision.approved:
            logger.info("HITL gate '%s' REJECTED: %s", checkpoint, decision.reason)

        return decision.approved

    @property
    def history(self) -> list[tuple[str, HITLDecision]]:
        return list(self._history)

    def reset(self) -> None:
        self._history.clear()
