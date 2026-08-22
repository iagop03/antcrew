"""HitlReviewer: blocks the engine loop until a human approves or rejects an artifact.

Usage in engine_runner.py / CLI:
    reviewer = HitlReviewer(
        reviewed_capability="architect",
        request_review=_make_review_callback(run_id, "architect", event_log),
        artifact_id="architecture",          # actual artifact ID written by Architect
        triggers_condition="architecture_exists",  # condition that gates the reviewer
        channel="architecture-review",       # optional: route to a named HITL channel
        feedback_schema=ArchFeedback,        # optional: Pydantic model for structured feedback
    )
    registry.register(reviewer)

On approval  → writes '<cap>_approval' CONFIG artifact → satisfies '<cap>_approved' condition.
On rejection → deletes the reviewed artifact + writes '<cap>_feedback' CONFIG artifact
               → upstream capability re-runs and reads the feedback on its next attempt.

artifact_id and triggers_condition default to reviewed_capability and
f"{reviewed_capability}_exists" respectively for cases where the capability name
matches its artifact/condition names exactly.

channel:
    Named routing hint for the platform layer (Slack, Telegram, web dashboard).
    Defaults to ``"default"``.  Platform subscribers can filter ``hitl.pending``
    events by ``event.payload["channel"]`` to route approvals to the right team.

feedback_schema:
    Optional Pydantic BaseModel class.  When set, structured feedback must be
    a JSON object matching the schema; the reviewer stores it as a dict in the
    feedback artifact's ``"structured_feedback"`` key.  Unstructured text
    feedback is still accepted (``"feedback"`` key) and the schema is
    advertised to the UI via the artifact's metadata.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional, Type

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

_DEFAULT_TIMEOUT = 3600  # 1 hour
_log = logging.getLogger(__name__)


class HitlReviewer(BaseExecutor):
    """Capability that gates the engine on a human decision.

    Does not call an LLM.  Blocks its worker thread via the *request_review*
    callable until the platform resolves the review or the timeout fires.

    request_review(content: Any) -> {"verdict": "approve"|"reject"|"timeout",
                                     "feedback": str | None}

    Parameters
    ----------
    reviewed_capability:
        Short name used for naming the approval/feedback artifacts and conditions
        (e.g. "architect" → writes "architect_approval", produces "architect_approved").
    artifact_id:
        The ArtifactId to read for display and to delete on rejection.
        Defaults to *reviewed_capability* but must be set explicitly when the
        upstream capability writes under a different name (e.g. Architect writes
        "architecture", not "architect").
    triggers_condition:
        The ConditionId whose satisfaction gates this reviewer.
        Defaults to f"{reviewed_capability}_exists" but must match the actual
        condition the upstream capability produces (e.g. "architecture_exists").
    """

    def __init__(
        self,
        *,
        reviewed_capability: str,
        request_review: Callable[[Any], dict],
        artifact_id: str | None = None,
        triggers_condition: str | None = None,
        timeout: int = _DEFAULT_TIMEOUT,
        channel: str = "default",
        feedback_schema: "Optional[Type]" = None,
    ) -> None:
        super().__init__(llm=None)
        self._reviewed_art_id = ArtifactId(artifact_id or reviewed_capability)
        self._approval_art_id = ArtifactId(f"{reviewed_capability}_approval")
        self._feedback_art_id = ArtifactId(f"{reviewed_capability}_feedback")
        self._request_review  = request_review
        self.channel          = channel
        self._feedback_schema = feedback_schema

        exists_cond   = ConditionId(triggers_condition or f"{reviewed_capability}_exists")
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

    def _feedback_schema_json(self) -> Optional[dict]:
        """Return the feedback schema as a JSON Schema dict, or None."""
        schema = self._feedback_schema
        if schema is None:
            return None
        try:
            return schema.model_json_schema()  # Pydantic v2
        except AttributeError:
            try:
                return schema.schema()  # Pydantic v1
            except Exception:
                return None

    def _parse_structured_feedback(self, raw: str) -> Optional[dict]:
        """Parse *raw* as structured feedback against the schema, or return None."""
        if not self._feedback_schema:
            return None
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            obj = self._feedback_schema.model_validate(data)  # Pydantic v2
            return obj.model_dump()
        except AttributeError:
            try:
                obj = self._feedback_schema(**data)  # Pydantic v1
                return obj.dict()
            except Exception:
                return None
        except Exception:
            return None

    def _run(self, store, goal) -> CapabilityResult:
        artifact = store.read(self._reviewed_art_id)
        content  = artifact.content if artifact else {}

        # Advertise channel + schema in the review request so the UI can route and render it.
        review_request = dict(content) if isinstance(content, dict) else {"content": content}
        review_request["_hitl_channel"] = self.channel
        if schema := self._feedback_schema_json():
            review_request["_feedback_schema"] = schema

        verdict_data = self._request_review(review_request)
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

        if verdict == "edit":
            new_content = verdict_data.get("new_content")
            if new_content is not None and artifact is not None:
                edited = Artifact(
                    id=artifact.id,
                    kind=artifact.kind,
                    content=new_content,
                    metadata=artifact.metadata,
                )
                approval = Artifact(
                    id=self._approval_art_id,
                    kind=ArtifactKind.CONFIG,
                    content={
                        "approved":            True,
                        "edited":              True,
                        "reviewed_capability": str(self._reviewed_art_id),
                    },
                    metadata={"file_path": f".antcrew/{self._reviewed_art_id}_approval.json"},
                )
                return CapabilityResult(delta=ArtifactDelta(modified=(edited,), created=(approval,)))
            # edit with no content → fall through to approve
            approval = Artifact(
                id=self._approval_art_id,
                kind=ArtifactKind.CONFIG,
                content={"approved": True, "reviewed_capability": str(self._reviewed_art_id)},
                metadata={"file_path": f".antcrew/{self._reviewed_art_id}_approval.json"},
            )
            return CapabilityResult(delta=ArtifactDelta(created=(approval,)))

        # reject or timeout: delete the artifact so the upstream capability re-runs
        created: list[Artifact] = []
        if feedback or verdict == "reject":
            structured = self._parse_structured_feedback(feedback) if feedback else None
            feedback_content: dict = {"feedback": feedback, "verdict": verdict, "channel": self.channel}
            if structured is not None:
                feedback_content["structured_feedback"] = structured
            if schema := self._feedback_schema_json():
                feedback_content["feedback_schema"] = schema
            created.append(Artifact(
                id       = self._feedback_art_id,
                kind     = ArtifactKind.CONFIG,
                content  = feedback_content,
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
