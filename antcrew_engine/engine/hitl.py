"""HITL contract — shared types between antcrew-engine and antcrew-platform.

These TypedDicts define the boundary between the engine's HitlReviewer
capability and any external review channel (platform API, CLI prompt, Slack).

The engine emits ``hitl_requested`` / ``hitl_resolved`` events (see events.py)
carrying these payloads.  The platform (or any other channel) consumes the
request and produces the decision.

Canonical flow
--------------
1. HitlReviewer calls ``request_review(content) -> HitlDecision``.
2. The channel (platform, CLI, ...) creates a review row / prompt.
3. The human (or automation) submits a decision.
4. The channel resolves the blocking call with a ``HitlDecision`` dict.
5. HitlReviewer writes approval/feedback artifacts and unblocks the EngineLoop.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

try:
    from typing import NotRequired, Protocol, TypedDict, runtime_checkable
except ImportError:
    from typing_extensions import (  # type: ignore[assignment]
        NotRequired,
        Protocol,
        TypedDict,
        runtime_checkable,
    )


# ---------------------------------------------------------------------------
# Request — engine → channel
# ---------------------------------------------------------------------------

class HitlReviewRequest(TypedDict):
    """Payload the engine sends when a capability output needs human review.

    Produced by HitlReviewer and forwarded by EventBusBridge in the
    ``hitl_requested`` event payload.
    """
    review_id: str
    """Unique ID for this review.  Used to match the subsequent decision."""

    run_id: str
    """Engine / platform run identifier for correlation."""

    reviewed_capability: str
    """Short name of the capability whose output is under review (e.g. 'architect')."""

    artifact_id: str
    """ID of the artifact being reviewed (e.g. 'architecture')."""

    content: Any
    """The artifact content to show to the reviewer.
    May be a str, dict, or list — depends on the capability's artifact kind."""

    options: list[str]
    """Allowed decision verbs for this review.  Typical: ['approve', 'reject', 'edit']."""

    timeout_seconds: int
    """How many seconds HitlReviewer will block before treating the review as timed out."""


# ---------------------------------------------------------------------------
# Decision — channel → engine
# ---------------------------------------------------------------------------

class HitlDecision(TypedDict):
    """Decision returned by the review channel to HitlReviewer.

    This is the return value of the ``request_review`` callback passed to
    HitlReviewer.  Channels (platform, CLI, Slack) must produce this shape.
    """
    verdict: Literal["approve", "reject", "edit", "timeout"]
    """Human decision verb."""

    feedback: NotRequired[Optional[str]]
    """Free-text feedback.  Required when ``verdict == 'reject'``, optional otherwise."""

    new_content: NotRequired[Optional[Any]]
    """Replacement artifact content.  Required when ``verdict == 'edit'``."""


# ---------------------------------------------------------------------------
# ReviewChannel — protocol for adding new HITL delivery channels
# ---------------------------------------------------------------------------

@runtime_checkable
class ReviewChannel(Protocol):
    """Contract for any HITL delivery channel (CLI, platform queue, Slack, email, …).

    Implement this protocol to add a new channel without touching engine internals.
    The engine calls submit() to send the review request, then await_decision()
    to wait for the human response.

    Both methods are async.  Sync channels (e.g. terminal) should wrap their
    blocking calls with asyncio.to_thread().

    Timeout contract: await_decision() must return HitlDecision(verdict="timeout")
    when timeout_seconds elapses without a response — never raise TimeoutError.
    """

    async def submit(self, request: "HitlReviewRequest") -> None:
        """Deliver the review request to the human reviewer."""
        ...

    async def await_decision(
        self, review_id: str, timeout_seconds: int
    ) -> "HitlDecision":
        """Block until the human responds or the timeout fires."""
        ...


# ---------------------------------------------------------------------------
# Platform event payloads (sent via EventBusBridge → antcrew.core.events.bus)
# ---------------------------------------------------------------------------

class HitlRequestedPayload(TypedDict):
    """Payload of the ``hitl_requested`` bus event forwarded to the platform."""
    review_id: str
    run_id: str
    thread_id: str
    reviewed_capability: str
    artifact_id: str
    content: Any
    options: list[str]
    timeout_seconds: int


class HitlResolvedPayload(TypedDict):
    """Payload of the ``hitl_resolved`` bus event forwarded to the platform."""
    review_id: str
    run_id: str
    thread_id: str
    verdict: str
    feedback: NotRequired[Optional[str]]
