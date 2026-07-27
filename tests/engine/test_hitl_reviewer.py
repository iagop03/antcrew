"""Tests for HitlReviewer capability."""
from __future__ import annotations

import pytest

from antcrew.capabilities.hitl_reviewer import HitlReviewer
from antcrew.engine import (
    Artifact,
    ArtifactId,
    ArtifactKind,
    ConditionId,
    MemoryStore,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def goal():
    from antcrew.engine import DesiredProjectState, Goal
    return Goal(description="Build a todo API", desired_state=DesiredProjectState(frozenset()))


def _make_reviewer(verdict: str, feedback: str | None = None):
    def _review(_content):
        return {"verdict": verdict, "feedback": feedback}
    return HitlReviewer(
        reviewed_capability="architect",
        request_review=_review,
    )


def _make_store(with_artifact: bool = True):
    store = MemoryStore()
    if with_artifact:
        store.write(Artifact(
            id=ArtifactId("architect"), kind=ArtifactKind.ARCHITECTURE,
            content={"summary": "This is the architecture"},
        ))
    return store


# ---------------------------------------------------------------------------
# Wiring — artifact_id / triggers_condition overrides
# ---------------------------------------------------------------------------

class TestHitlReviewerWiring:
    def test_custom_artifact_id_is_read(self, goal):
        """When artifact_id differs from reviewed_capability, the right artifact is read."""
        store = MemoryStore()
        store.write(Artifact(
            id=ArtifactId("architecture"), kind=ArtifactKind.ARCHITECTURE,
            content="the architecture",
        ))
        # Architect writes "architecture", not "architect"
        reviewer = HitlReviewer(
            reviewed_capability="architect",
            request_review=lambda _: {"verdict": "approve"},
            artifact_id="architecture",
            triggers_condition="architecture_exists",
        )
        result = reviewer.execute(store, goal)
        assert result.succeeded
        # Approval artifact uses reviewed_capability name
        assert result.delta.created[0].id == ArtifactId("architect_approval")

    def test_custom_triggers_condition_in_descriptor(self):
        reviewer = HitlReviewer(
            reviewed_capability="architect",
            request_review=lambda _: {"verdict": "approve"},
            artifact_id="architecture",
            triggers_condition="architecture_exists",
        )
        assert ConditionId("architecture_exists") in reviewer.descriptor.needs
        assert ConditionId("architect_approved")  in reviewer.descriptor.produces

    def test_reject_deletes_custom_artifact(self, goal):
        """On rejection, the artifact_id target is deleted, not reviewed_capability."""
        store = MemoryStore()
        store.write(Artifact(
            id=ArtifactId("architecture"), kind=ArtifactKind.ARCHITECTURE,
            content="the architecture",
        ))
        reviewer = HitlReviewer(
            reviewed_capability="architect",
            request_review=lambda _: {"verdict": "reject", "feedback": "Too abstract"},
            artifact_id="architecture",
            triggers_condition="architecture_exists",
        )
        result = reviewer.execute(store, goal)
        assert ArtifactId("architecture") in result.delta.deleted

    def test_defaults_when_no_overrides(self):
        """Without overrides, defaults match reviewed_capability name."""
        r = HitlReviewer(
            reviewed_capability="some_cap",
            request_review=lambda _: {"verdict": "approve"},
        )
        assert ConditionId("some_cap_exists") in r.descriptor.needs


# ---------------------------------------------------------------------------
# Descriptor
# ---------------------------------------------------------------------------

class TestHitlReviewerDescriptor:
    def test_name_includes_capability(self):
        r = _make_reviewer("approve")
        assert r.descriptor.name == "hitl_architect"

    def test_needs_existence_condition(self):
        r = _make_reviewer("approve")
        assert ConditionId("architect_exists") in r.descriptor.needs

    def test_produces_approved_condition(self):
        r = _make_reviewer("approve")
        assert ConditionId("architect_approved") in r.descriptor.produces

    def test_cost_is_low(self):
        r = _make_reviewer("approve")
        assert r.descriptor.cost <= 0.5  # runs immediately after the reviewed capability


# ---------------------------------------------------------------------------
# Approve path
# ---------------------------------------------------------------------------

class TestHitlReviewerApprove:
    def test_creates_approval_artifact(self, goal):
        result = _make_reviewer("approve").execute(_make_store(), goal)
        assert result.succeeded
        assert len(result.delta.created) == 1
        art = result.delta.created[0]
        assert art.id == ArtifactId("architect_approval")

    def test_approval_artifact_has_approved_true(self, goal):
        result = _make_reviewer("approve").execute(_make_store(), goal)
        art    = result.delta.created[0]
        assert art.content.get("approved") is True

    def test_no_deletions_on_approve(self, goal):
        result = _make_reviewer("approve").execute(_make_store(), goal)
        assert len(result.delta.deleted) == 0


# ---------------------------------------------------------------------------
# Reject path
# ---------------------------------------------------------------------------

class TestHitlReviewerReject:
    def test_deletes_reviewed_artifact(self, goal):
        result = _make_reviewer("reject", feedback="Needs more detail").execute(_make_store(), goal)
        assert ArtifactId("architect") in result.delta.deleted

    def test_creates_feedback_artifact_with_content(self, goal):
        result = _make_reviewer("reject", feedback="Too vague").execute(_make_store(), goal)
        arts   = result.delta.created
        assert len(arts) == 1
        assert arts[0].id == ArtifactId("architect_feedback")
        assert arts[0].content.get("feedback") == "Too vague"

    def test_reject_with_no_feedback_still_creates_feedback_artifact(self, goal):
        """A reject always writes a feedback artifact even when feedback text is empty."""
        result = _make_reviewer("reject", feedback=None).execute(_make_store(), goal)
        arts   = result.delta.created
        assert len(arts) == 1
        assert arts[0].id == ArtifactId("architect_feedback")

    def test_reject_emits_warning(self, goal):
        result = _make_reviewer("reject", feedback="Bad").execute(_make_store(), goal)
        assert any("reject" in w.lower() or "HITL" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Timeout path
# ---------------------------------------------------------------------------

class TestHitlReviewerTimeout:
    def test_timeout_deletes_reviewed_artifact(self, goal):
        result = _make_reviewer("timeout").execute(_make_store(), goal)
        assert ArtifactId("architect") in result.delta.deleted

    def test_timeout_with_no_feedback_creates_no_extra_artifact(self, goal):
        result = _make_reviewer("timeout").execute(_make_store(), goal)
        assert len(result.delta.created) == 0

    def test_timeout_emits_warning(self, goal):
        result = _make_reviewer("timeout").execute(_make_store(), goal)
        assert any("timeout" in w.lower() or "HITL" in w for w in result.warnings)
