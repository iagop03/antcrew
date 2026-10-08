"""HITL integration tests.

Covers the full governed execution contract:
  - approve / reject / timeout / request_changes / edit paths
  - callback exception → fail closed (timeout)
  - request_changes does NOT halt (vs reject which does)
  - max_rejections limit
  - TraceLog persistence (decision written to DB)
  - EvidencePackage built from trace reflects decisions
  - hitl_decision_from_flexible bridge mapping
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from antcrew_engine.capabilities.hitl_reviewer import HitlReviewer
from antcrew_engine.engine import (
    Artifact,
    ArtifactDelta,
    ArtifactId,
    ArtifactKind,
    MemoryStore,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def goal():
    from antcrew_engine.engine import DesiredProjectState, Goal
    return Goal(description="Test goal", desired_state=DesiredProjectState(frozenset()))


def _store_with(artifact_id: str = "architect", content=None) -> MemoryStore:
    store = MemoryStore()
    store.write(Artifact(
        id=ArtifactId(artifact_id),
        kind=ArtifactKind.ARCHITECTURE,
        content=content or {"summary": "Architecture draft"},
    ))
    return store


def _reviewer(
    callback,
    *,
    artifact_id: str = "architect",
    capability: str = "architect",
    max_rejections: int = 3,
    trace_log=None,
    run_id: str = "",
) -> HitlReviewer:
    return HitlReviewer(
        reviewed_capability=capability,
        request_review=callback,
        artifact_id=artifact_id,
        max_rejections=max_rejections,
        trace_log=trace_log,
        run_id=run_id,
    )


# ---------------------------------------------------------------------------
# Approve path
# ---------------------------------------------------------------------------

class TestApprove:
    def test_approve_produces_approval_artifact(self, goal):
        r = _reviewer(lambda _: {"verdict": "approve"})
        result = r.execute(_store_with(), goal)
        assert result.succeeded
        arts = result.delta.created
        assert len(arts) == 1
        assert arts[0].id == ArtifactId("architect_approval")
        assert arts[0].content["approved"] is True

    def test_approve_does_not_delete_reviewed_artifact(self, goal):
        r = _reviewer(lambda _: {"verdict": "approve"})
        result = r.execute(_store_with(), goal)
        assert len(result.delta.deleted) == 0

    def test_approve_with_reviewer_id(self, goal):
        r = _reviewer(lambda _: {"verdict": "approve", "reviewer_id": "alice@example.com"})
        result = r.execute(_store_with(), goal)
        assert result.succeeded


# ---------------------------------------------------------------------------
# Reject path
# ---------------------------------------------------------------------------

class TestReject:
    def test_reject_deletes_reviewed_artifact(self, goal):
        r = _reviewer(lambda _: {"verdict": "reject", "feedback": "Too vague"})
        result = r.execute(_store_with(), goal)
        assert ArtifactId("architect") in result.delta.deleted

    def test_reject_creates_feedback_artifact(self, goal):
        r = _reviewer(lambda _: {"verdict": "reject", "feedback": "Needs more detail"})
        result = r.execute(_store_with(), goal)
        assert len(result.delta.created) == 1
        fb = result.delta.created[0]
        assert fb.id == ArtifactId("architect_feedback")
        assert fb.content["feedback"] == "Needs more detail"
        assert fb.content["verdict"] == "reject"

    def test_reject_halts_after_max_rejections(self, goal):
        r = _reviewer(lambda _: {"verdict": "reject", "feedback": "x"}, max_rejections=2)
        store = _store_with()
        r.execute(store, goal)         # rejection 1 — returns feedback artifact (deletions require re-insert)
        # re-insert the artifact so the reviewer can read it again
        store.write(Artifact(id=ArtifactId("architect"), kind=ArtifactKind.ARCHITECTURE, content={}))
        r.execute(store, goal)         # rejection 2
        store.write(Artifact(id=ArtifactId("architect"), kind=ArtifactKind.ARCHITECTURE, content={}))
        result = r.execute(store, goal)  # rejection 3 → over limit
        assert not result.succeeded
        assert result.errors
        assert "max" in result.errors[0].lower() or "Aborting" in result.errors[0]


# ---------------------------------------------------------------------------
# Timeout path — treated as reject (fail closed)
# ---------------------------------------------------------------------------

class TestTimeout:
    def test_timeout_deletes_reviewed_artifact(self, goal):
        r = _reviewer(lambda _: {"verdict": "timeout"})
        result = r.execute(_store_with(), goal)
        assert ArtifactId("architect") in result.delta.deleted

    def test_timeout_creates_feedback_artifact(self, goal):
        r = _reviewer(lambda _: {"verdict": "timeout"})
        result = r.execute(_store_with(), goal)
        fb = result.delta.created[0]
        assert fb.content["verdict"] == "timeout"

    def test_missing_verdict_defaults_to_timeout(self, goal):
        """A callback that returns {} (missing verdict) is treated as timeout."""
        r = _reviewer(lambda _: {})
        result = r.execute(_store_with(), goal)
        assert ArtifactId("architect") in result.delta.deleted


# ---------------------------------------------------------------------------
# Callback exception → fail closed (timeout)
# ---------------------------------------------------------------------------

class TestCallbackFailClosed:
    def test_exception_in_callback_defaults_to_timeout(self, goal):
        def _exploding(_):
            raise RuntimeError("review service unreachable")

        r = _reviewer(_exploding)
        result = r.execute(_store_with(), goal)
        # Fail closed: exception → timeout → artifact deleted, feedback written
        assert ArtifactId("architect") in result.delta.deleted
        fb = result.delta.created[0]
        assert "review_callback_error" in fb.content.get("feedback", "")

    def test_exception_does_not_propagate(self, goal):
        def _exploding(_):
            raise ValueError("boom")

        r = _reviewer(_exploding)
        result = r.execute(_store_with(), goal)
        assert result is not None  # no unhandled exception


# ---------------------------------------------------------------------------
# Request changes path — causes retry, NOT halt
# ---------------------------------------------------------------------------

class TestRequestChanges:
    def test_request_changes_deletes_artifact_like_reject(self, goal):
        r = _reviewer(lambda _: {"verdict": "request_changes", "feedback": "Add auth details"})
        result = r.execute(_store_with(), goal)
        assert ArtifactId("architect") in result.delta.deleted

    def test_request_changes_verdict_in_feedback_artifact(self, goal):
        r = _reviewer(lambda _: {"verdict": "request_changes", "feedback": "Add auth details"})
        result = r.execute(_store_with(), goal)
        fb = result.delta.created[0]
        assert fb.content["verdict"] == "request_changes"

    def test_request_changes_is_distinct_from_reject(self, goal):
        """request_changes and reject produce different verdict values in the feedback artifact.

        The engine uses this to decide whether to retry (request_changes) or halt (reject).
        """
        store = _store_with()

        r_rc = _reviewer(lambda _: {"verdict": "request_changes", "feedback": "x"})
        fb_rc = r_rc.execute(store, goal).delta.created[0]

        store.write(Artifact(id=ArtifactId("architect"), kind=ArtifactKind.ARCHITECTURE, content={}))
        r_rej = _reviewer(lambda _: {"verdict": "reject", "feedback": "y"})
        fb_rej = r_rej.execute(store, goal).delta.created[0]

        assert fb_rc.content["verdict"] == "request_changes"
        assert fb_rej.content["verdict"] == "reject"


# ---------------------------------------------------------------------------
# Edit path
# ---------------------------------------------------------------------------

class TestEdit:
    def test_edit_replaces_artifact_content(self, goal):
        new_content = {"summary": "Updated architecture", "edited": True}
        r = _reviewer(lambda _: {"verdict": "edit", "new_content": new_content})
        result = r.execute(_store_with(), goal)
        assert result.succeeded
        modified = result.delta.modified
        assert len(modified) == 1
        assert modified[0].content == new_content

    def test_edit_creates_approval_artifact_with_edited_flag(self, goal):
        r = _reviewer(lambda _: {"verdict": "edit", "new_content": {"x": 1}})
        result = r.execute(_store_with(), goal)
        approval = result.delta.created[0]
        assert approval.content["approved"] is True
        assert approval.content.get("edited") is True

    def test_edit_without_content_falls_back_to_request_changes(self, goal):
        """edit verdict with no new_content should not silently approve."""
        r = _reviewer(lambda _: {"verdict": "edit", "new_content": None})
        result = r.execute(_store_with(), goal)
        # Falls back to request_changes flow: artifact deleted, feedback created
        assert ArtifactId("architect") in result.delta.deleted


# ---------------------------------------------------------------------------
# TraceLog persistence
# ---------------------------------------------------------------------------

class TestTraceLogPersistence:
    def test_approve_decision_written_to_tracelog(self, goal):
        from antcrew.trace import TraceLog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog = TraceLog(db_path)
        run_id = tlog.begin_run(thread_id="t1", request="test", team="test_team")

        r = _reviewer(
            lambda _: {"verdict": "approve", "reviewer_id": "tester"},
            trace_log=tlog,
            run_id=run_id,
        )
        r.execute(_store_with(), goal)
        tlog.end_run(run_id, cost_usd=0.0, status="done")

        decisions = tlog.get_hitl_decisions(run_id)
        tlog.close()
        Path(db_path).unlink(missing_ok=True)

        assert len(decisions) == 1
        assert decisions[0]["decision"] == "approve"
        assert decisions[0]["reviewer_id"] == "tester"

    def test_reject_decision_persisted_with_reason(self, goal):
        from antcrew.trace import TraceLog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog = TraceLog(db_path)
        run_id = tlog.begin_run(thread_id="t2", request="test2", team="test_team")

        r = _reviewer(
            lambda _: {"verdict": "reject", "feedback": "Not good enough"},
            trace_log=tlog,
            run_id=run_id,
        )
        r.execute(_store_with(), goal)
        tlog.end_run(run_id, cost_usd=0.0, status="rejected")

        decisions = tlog.get_hitl_decisions(run_id)
        tlog.close()
        Path(db_path).unlink(missing_ok=True)

        assert len(decisions) == 1
        assert decisions[0]["decision"] == "reject"
        assert decisions[0]["reason"] == "Not good enough"

    def test_tracelog_not_required(self, goal):
        """Reviewer works correctly when no trace_log is provided."""
        r = _reviewer(lambda _: {"verdict": "approve"})
        result = r.execute(_store_with(), goal)
        assert result.succeeded

    def test_hash_chain_intact_after_decisions(self, goal):
        from antcrew.trace import TraceLog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog = TraceLog(db_path)

        for i in range(3):
            run_id = tlog.begin_run(thread_id=f"t{i}", request=f"req{i}", team="test")
            store = _store_with(f"cap{i}")
            r = HitlReviewer(
                reviewed_capability=f"cap{i}",
                request_review=lambda _: {"verdict": "approve"},
                artifact_id=f"cap{i}",
                trace_log=tlog,
                run_id=run_id,
            )
            r.execute(store, goal)
            tlog.end_run(run_id, cost_usd=0.0, status="done")

        chain = tlog.verify_hitl_chain()
        tlog.close()
        Path(db_path).unlink(missing_ok=True)

        assert chain["valid"] is True


# ---------------------------------------------------------------------------
# EvidencePackage integration
# ---------------------------------------------------------------------------

class TestEvidencePackage:
    def test_evidence_package_built_from_trace(self, goal):
        from antcrew.evidence import EvidencePackage
        from antcrew.trace import TraceLog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog = TraceLog(db_path)
        run_id = tlog.begin_run(thread_id="ev1", request="Build auth module", team="dev")

        r = _reviewer(
            lambda _: {"verdict": "approve", "reviewer_id": "alice"},
            trace_log=tlog,
            run_id=run_id,
        )
        r.execute(_store_with(), goal)
        tlog.end_run(run_id, cost_usd=0.05, status="done")

        pkg = EvidencePackage.from_trace(tlog, run_id)
        tlog.close()
        Path(db_path).unlink(missing_ok=True)

        assert pkg.run_id == run_id
        assert pkg.status == "done"
        assert pkg.cost_usd == pytest.approx(0.05)
        assert pkg.hitl_count == 1
        assert pkg.approved_count == 1
        assert pkg.chain_status == "intact"

    def test_evidence_package_not_found(self):
        from antcrew.evidence import EvidencePackage
        from antcrew.trace import TraceLog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog = TraceLog(db_path)
        pkg = EvidencePackage.from_trace(tlog, "nonexistent-run-id")
        tlog.close()
        Path(db_path).unlink(missing_ok=True)

        assert pkg.status == "not_found"

    def test_evidence_package_to_dict_has_document_hash(self, goal):
        from antcrew.evidence import EvidencePackage
        from antcrew.trace import TraceLog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog = TraceLog(db_path)
        run_id = tlog.begin_run(thread_id="ev2", request="req", team="dev")
        tlog.end_run(run_id, cost_usd=0.0, status="done")

        pkg = EvidencePackage.from_trace(tlog, run_id)
        tlog.close()
        Path(db_path).unlink(missing_ok=True)

        d = pkg.to_dict()
        assert "document_hash" in d
        assert d["document_hash"].startswith("sha256:")


# ---------------------------------------------------------------------------
# hitl_decision_from_flexible bridge
# ---------------------------------------------------------------------------

class TestFlexibleBridge:
    def _make_flexible_decision(self, action_str: str, reason: str = "", reviewer_id: str = ""):
        decision = MagicMock()
        action = MagicMock()
        action.value = action_str
        decision.action = action
        decision.reason = reason
        decision.reviewer_id = reviewer_id
        decision.modified_state = None
        return decision

    def test_approve_maps_to_approve(self):
        from antcrew_engine.engine.hitl import hitl_decision_from_flexible
        d = hitl_decision_from_flexible(self._make_flexible_decision("approve"))
        assert d["verdict"] == "approve"

    def test_modify_maps_to_edit(self):
        from antcrew_engine.engine.hitl import hitl_decision_from_flexible
        d = hitl_decision_from_flexible(self._make_flexible_decision("modify"))
        assert d["verdict"] == "edit"

    def test_skip_maps_to_approve(self):
        from antcrew_engine.engine.hitl import hitl_decision_from_flexible
        d = hitl_decision_from_flexible(self._make_flexible_decision("skip"))
        assert d["verdict"] == "approve"

    def test_request_changes_maps_to_request_changes_not_reject(self):
        """request_changes must NOT map to reject — it has retry semantics."""
        from antcrew_engine.engine.hitl import hitl_decision_from_flexible
        d = hitl_decision_from_flexible(self._make_flexible_decision("request_changes"))
        assert d["verdict"] == "request_changes"
        assert d["verdict"] != "reject"

    def test_reject_maps_to_reject(self):
        from antcrew_engine.engine.hitl import hitl_decision_from_flexible
        d = hitl_decision_from_flexible(self._make_flexible_decision("reject"))
        assert d["verdict"] == "reject"

    def test_unknown_action_defaults_to_reject(self):
        from antcrew_engine.engine.hitl import hitl_decision_from_flexible
        d = hitl_decision_from_flexible(self._make_flexible_decision("unknown_thing"))
        assert d["verdict"] == "reject"

    def test_reviewer_id_and_reason_preserved(self):
        from antcrew_engine.engine.hitl import hitl_decision_from_flexible
        d = hitl_decision_from_flexible(
            self._make_flexible_decision("approve", reason="LGTM", reviewer_id="bob")
        )
        assert d.get("reviewer_id") == "bob"
        assert d.get("feedback") == "LGTM"
