"""HitlReviewer: blocks the engine loop until a human approves or rejects an artifact.

Usage in engine_runner.py / CLI:
    reviewer = HitlReviewer(
        reviewed_capability="architect",
        request_review=_make_review_callback(run_id, "architect", event_log),
    )
    registry.register(reviewer)

On approval  → writes '<cap>_approval' CONFIG artifact → satisfies '<cap>_approved' condition.
On rejection → deletes the reviewed artifact + writes '<cap>_feedback' CONFIG artifact
               → upstream capability re-runs and reads the feedback on its next attempt.
"""
from __future__ import annotations

from typing import Any, Callable

from antcrew.engine import (
    Artifact, ArtifactDelta, ArtifactId, ArtifactKind,
    CapabilityDescriptor, CapabilityResult, ConditionId,
)
from .base import BaseExecutor

_DEFAULT_TIMEOUT = 3600  # 1 hour


class HitlReviewer(BaseExecutor):
    """Capability that gates the engine on a human decision.

    Does not call an LLM.  Blocks its worker thread via the *request_review*
    callable until the platform resolves the review or the timeout fires.

    request_review(content: Any) -> {"verdict": "approve"|"reject"|"timeout",
                                     "feedback": str | None}
    """

    def __init__(
        self,
        *,
        reviewed_capability: str,
        request_review: Callable[[Any], dict],
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(llm=None)
        self._reviewed_art_id = ArtifactId(reviewed_capability)
        self._approval_art_id = ArtifactId(f"{reviewed_capability}_approval")
        self._feedback_art_id = ArtifactId(f"{reviewed_capability}_feedback")
        self._request_review  = request_review

        exists_cond   = ConditionId(f"{reviewed_capability}_exists")
        approved_cond = ConditionId(f"{reviewed_capability}_approved")

        # Instance attribute — shadows any class-level descriptor (OK for the Protocol)
        self.descriptor = CapabilityDescriptor(
            name        = f"hitl_{reviewed_capability}",
            description = (
                f"Sends the {reviewed_capability} artifact for human review "
                "and waits for approval or rejection."
            ),
            needs    = frozenset([exists_cond]),
            produces = frozenset([approved_cond]),
            emits    = frozenset(["config"]),
            cost     = 0.1,  # run immediately after the reviewed capability finishes
        )

    def _run(self, store, goal) -> CapabilityResult:
        artifact = store.read(self._reviewed_art_id)
        content  = artifact.content if artifact else {}

        verdict_data = self._request_review(content)
        verdict  = verdict_data.get("verdict", "timeout")
        feedback = (verdict_data.get("feedback") or "").strip()

        if verdict == "approve":
            approval = Artifact(
                id       = self._approval_art_id,
                kind     = ArtifactKind.CONFIG,
                content  = {
                    "approved":             True,
                    "reviewed_capability":  str(self._reviewed_art_id),
                },
                metadata = {
                    "file_path": f".antcrew/{self._reviewed_art_id}_approval.json",
                },
            )
            return CapabilityResult(delta=ArtifactDelta(created=(approval,)))

        # reject or timeout: delete the artifact so the upstream capability re-runs
        created: list[Artifact] = []
        if feedback or verdict == "reject":
            created.append(Artifact(
                id       = self._feedback_art_id,
                kind     = ArtifactKind.CONFIG,
                content  = {"feedback": feedback, "verdict": verdict},
                metadata = {
                    "file_path": f".antcrew/{self._feedback_art_id}.json",
                },
            ))

        return CapabilityResult(
            delta=ArtifactDelta(
                deleted = (self._reviewed_art_id,),
                created = tuple(created),
            ),
            warnings=[f"HITL {verdict}: {feedback}" if feedback else f"HITL {verdict}"],
        )
