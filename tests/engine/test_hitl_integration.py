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
    allowed_reviewers=None,
) -> HitlReviewer:
    return HitlReviewer(
        reviewed_capability=capability,
        request_review=callback,
        artifact_id=artifact_id,
        max_rejections=max_rejections,
        trace_log=trace_log,
        run_id=run_id,
        allowed_reviewers=allowed_reviewers,
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

    def test_execution_chain_intact_after_full_run(self, goal):
        """verify_execution_chain covers all events: run_started, hitl_decision, run_ended."""
        from antcrew.trace import TraceLog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog = TraceLog(db_path)
        run_id = tlog.begin_run(thread_id="chain-t1", request="test chain", team="test")
        r = _reviewer(
            lambda _: {"verdict": "approve", "reviewer_id": "alice"},
            trace_log=tlog,
            run_id=run_id,
        )
        r.execute(_store_with(), goal)
        tlog.end_run(run_id, cost_usd=0.01, status="done")

        result = tlog.verify_execution_chain(run_id)
        tlog.close()
        Path(db_path).unlink(missing_ok=True)

        assert result["valid"] is True
        assert result["total"] >= 3  # run_started + hitl_decision + run_ended
        assert result["chain_root"] != ""
        assert result["broken_at"] is None

    def test_execution_chain_detects_tampering(self, goal):
        """Modifying an event field must break the chain."""
        import sqlite3 as _sq
        from antcrew.trace import TraceLog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog = TraceLog(db_path)
        run_id = tlog.begin_run(thread_id="tamper-t1", request="test tamper", team="test")
        tlog.end_run(run_id, cost_usd=0.0, status="done")
        tlog.close()

        # Tamper with the first event
        conn = _sq.connect(db_path)
        conn.execute(
            "UPDATE execution_events SET event_type='tampered' WHERE run_id=? AND sequence=1",
            (run_id,),
        )
        conn.commit()
        conn.close()

        tlog2 = TraceLog(db_path)
        result = tlog2.verify_execution_chain(run_id)
        tlog2.close()
        Path(db_path).unlink(missing_ok=True)

        assert result["valid"] is False
        assert result["broken_at"] == 1

    def test_execution_chain_empty_for_missing_run(self):
        """verify_execution_chain returns None valid for a nonexistent run."""
        from antcrew.trace import TraceLog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog = TraceLog(db_path)
        result = tlog.verify_execution_chain("nonexistent-run-id")
        tlog.close()
        Path(db_path).unlink(missing_ok=True)

        assert result["valid"] is None
        assert result["total"] == 0


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

    def test_evidence_package_execution_chain_fields(self, goal):
        """EvidencePackage exposes event_count and chain_root from execution chain."""
        from antcrew.evidence import EvidencePackage
        from antcrew.trace import TraceLog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog = TraceLog(db_path)
        run_id = tlog.begin_run(thread_id="ev3", request="chain test", team="dev")
        r = _reviewer(
            lambda _: {"verdict": "approve", "reviewer_id": "bob"},
            trace_log=tlog,
            run_id=run_id,
        )
        r.execute(_store_with(), goal)
        tlog.end_run(run_id, cost_usd=0.02, status="done")

        pkg = EvidencePackage.from_trace(tlog, run_id)
        tlog.close()
        Path(db_path).unlink(missing_ok=True)

        assert pkg.chain_status == "intact"
        assert pkg.event_count >= 3  # run_started + hitl_decision + run_ended
        assert pkg.chain_root != ""
        d = pkg.to_dict()
        assert d["chain_root"] != ""
        assert d["event_count"] >= 3


# ---------------------------------------------------------------------------
# Governance guarantees — failure and edge-case contract
# ---------------------------------------------------------------------------

class TestGovernanceGuarantees:
    """Explicit tests that the gate contract holds under failure conditions.

    These complement the happy-path coverage above with scenarios the analysis
    flagged as critical: callback errors must not approve, reject must not leak
    approval artifacts, duplicate decisions must both be recorded.
    """

    def test_callback_error_never_produces_approval(self, goal):
        """A callback exception must result in fail-closed, never in an approval."""
        def _exploding(_):
            raise ConnectionError("review service unreachable")

        r = _reviewer(_exploding)
        result = r.execute(_store_with(), goal)

        approved = [a for a in result.delta.created if a.content.get("approved") is True]
        assert approved == [], "Callback exception must never produce an approval artifact"

        # Fail-closed: the verdict in the feedback artifact must be timeout, not approved
        feedback_artifacts = [a for a in result.delta.created if a.content.get("verdict")]
        assert feedback_artifacts, "Callback exception must produce a feedback artifact"
        assert all(a.content["verdict"] != "approved" for a in feedback_artifacts), (
            "Callback exception must never produce an approved verdict"
        )

    def test_reject_never_produces_approval_artifact(self, goal):
        """A reject verdict must produce zero artifacts with approved=True."""
        r = _reviewer(lambda _: {"verdict": "reject", "feedback": "too risky"})
        result = r.execute(_store_with(), goal)

        approved = [a for a in result.delta.created if a.content.get("approved") is True]
        assert approved == [], "Reject must never create an approved artifact"

    def test_timeout_never_produces_approval_artifact(self, goal):
        """A timeout verdict (fail-closed) must produce zero artifacts with approved=True."""
        r = _reviewer(lambda _: {"verdict": "timeout"})
        result = r.execute(_store_with(), goal)

        approved = [a for a in result.delta.created if a.content.get("approved") is True]
        assert approved == [], "Timeout must never create an approved artifact"

    def test_duplicate_decisions_both_recorded(self, goal):
        """Calling the reviewer twice on the same run_id records two independent decisions.

        Covers restart/replay: if the gate fires again after a process restart, both calls
        must be persisted — no silent dedup that could mask a replay attack or hide a second
        approval from an unauthorized reviewer.
        """
        from antcrew.trace import TraceLog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog = TraceLog(db_path)
        run_id = tlog.begin_run(thread_id="dup-t1", request="dup test", team="test")

        r = _reviewer(
            lambda _: {"verdict": "approve", "reviewer_id": "alice"},
            trace_log=tlog,
            run_id=run_id,
        )
        r.execute(_store_with(), goal)      # first gate
        r.execute(_store_with(), goal)      # second gate (simulates restart)

        tlog.end_run(run_id, cost_usd=0.0, status="done")
        decisions = tlog.get_hitl_decisions(run_id)
        tlog.close()
        Path(db_path).unlink(missing_ok=True)

        assert len(decisions) == 2, (
            "Both gate invocations must be recorded independently — "
            "silent dedup would mask replay attacks"
        )

    def test_reject_without_reviewer_id_still_persisted(self, goal):
        """An anonymous rejection (no reviewer_id) is still written to TraceLog.

        Missing reviewer_id degrades auditability but must not silently swallow
        the decision or fail the persistence path.
        """
        from antcrew.trace import TraceLog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog = TraceLog(db_path)
        run_id = tlog.begin_run(thread_id="anon-t1", request="anon test", team="test")

        r = _reviewer(
            lambda _: {"verdict": "reject", "feedback": "not acceptable"},
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
        assert decisions[0]["reviewer_id"] == ""   # empty, but recorded

    def test_coverage_manifest_populated_after_run(self, goal):
        """EvidencePackage.coverage reflects which event types were recorded."""
        from antcrew.evidence import EvidencePackage
        from antcrew.trace import TraceLog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog = TraceLog(db_path)
        run_id = tlog.begin_run(thread_id="cov-t1", request="coverage test", team="test")
        r = _reviewer(
            lambda _: {"verdict": "approve", "reviewer_id": "alice"},
            trace_log=tlog,
            run_id=run_id,
        )
        r.execute(_store_with(), goal)
        tlog.end_run(run_id, cost_usd=0.0, status="done")

        pkg = EvidencePackage.from_trace(tlog, run_id)
        tlog.close()
        Path(db_path).unlink(missing_ok=True)

        cov = pkg.coverage
        assert cov["level"] == "full", f"Expected full coverage, got: {cov}"
        assert cov["has_run_start"] is True
        assert cov["has_run_end"] is True
        assert cov["has_hitl_decisions"] is True
        assert "run_started" in cov["event_types"]
        assert "run_ended" in cov["event_types"]
        assert "hitl_decision" in cov["event_types"]
        # coverage is also in to_dict()
        d = pkg.to_dict()
        assert d["coverage"]["level"] == "full"


# ---------------------------------------------------------------------------
# Unauthorized reviewer — HitlReviewer.allowed_reviewers enforcement
# ---------------------------------------------------------------------------

class TestUnauthorizedReviewer:
    """HitlReviewer must reject decisions from reviewers not in allowed_reviewers."""

    def test_unauthorized_reviewer_is_rejected(self, goal):
        """A reviewer not in allowed_reviewers cannot produce an approval."""
        r = _reviewer(
            lambda _: {"verdict": "approve", "reviewer_id": "eve@evil.com"},
            allowed_reviewers={"alice@company.com"},
        )
        result = r.execute(_store_with(), goal)

        approved = [a for a in result.delta.created if a.content.get("approved") is True]
        assert approved == [], "Reviewer not in allowed_reviewers must not approve"

    def test_authorized_reviewer_is_accepted(self, goal):
        """A reviewer in allowed_reviewers can approve normally."""
        r = _reviewer(
            lambda _: {"verdict": "approve", "reviewer_id": "alice@company.com"},
            allowed_reviewers={"alice@company.com", "bob@company.com"},
        )
        result = r.execute(_store_with(), goal)

        approved = [a for a in result.delta.created if a.content.get("approved") is True]
        assert len(approved) == 1, "Authorized reviewer must produce an approval artifact"

    def test_empty_allowed_reviewers_rejects_all(self, goal):
        """allowed_reviewers=set() means no reviewer is authorized."""
        r = _reviewer(
            lambda _: {"verdict": "approve", "reviewer_id": "anyone@example.com"},
            allowed_reviewers=set(),
        )
        result = r.execute(_store_with(), goal)

        approved = [a for a in result.delta.created if a.content.get("approved") is True]
        assert approved == [], "Empty allowed_reviewers set must reject every reviewer"

    def test_no_restriction_accepts_any_reviewer(self, goal):
        """When allowed_reviewers is None (default), any reviewer_id is accepted."""
        r = _reviewer(
            lambda _: {"verdict": "approve", "reviewer_id": "anyone@anywhere.io"},
        )
        result = r.execute(_store_with(), goal)

        approved = [a for a in result.delta.created if a.content.get("approved") is True]
        assert len(approved) == 1, "No restriction: any reviewer_id should approve"

    def test_unauthorized_reviewer_feedback_names_reviewer(self, goal):
        """The rejection feedback must include the unauthorized reviewer_id for audit."""
        r = _reviewer(
            lambda _: {"verdict": "approve", "reviewer_id": "outsider@evil.io"},
            allowed_reviewers={"alice@company.com"},
        )
        result = r.execute(_store_with(), goal)

        feedback = [a for a in result.delta.created if "unauthorized_reviewer" in a.content.get("feedback", "")]
        assert feedback, "Feedback artifact must mention unauthorized_reviewer"


# ---------------------------------------------------------------------------
# Persistence failure — TraceLog write error must not affect gate enforcement
# ---------------------------------------------------------------------------

class TestPersistenceFailure:
    """A TraceLog write failure must never change the gate verdict."""

    def _reviewer_with_broken_trace(self, callback, verdict):
        """HitlReviewer wired to a TraceLog whose record_hitl always raises."""
        from unittest.mock import MagicMock
        broken_tlog = MagicMock()
        broken_tlog.record_hitl.side_effect = OSError("disk full")

        return _reviewer(callback, trace_log=broken_tlog, run_id="run-persist-fail")

    def test_tracelog_failure_does_not_block_approve(self, goal):
        """If TraceLog.record_hitl raises, the approve verdict still goes through."""
        r = self._reviewer_with_broken_trace(
            lambda _: {"verdict": "approve", "reviewer_id": "alice"},
            "approve",
        )
        result = r.execute(_store_with(), goal)

        approved = [a for a in result.delta.created if a.content.get("approved") is True]
        assert len(approved) == 1, (
            "TraceLog write failure must not block an approve — observability "
            "failure must not become a DoS on the gate"
        )

    def test_tracelog_failure_does_not_convert_reject_to_approve(self, goal):
        """If TraceLog.record_hitl raises on a reject, the reject still stands."""
        r = self._reviewer_with_broken_trace(
            lambda _: {"verdict": "reject", "feedback": "not acceptable"},
            "reject",
        )
        result = r.execute(_store_with(), goal)

        approved = [a for a in result.delta.created if a.content.get("approved") is True]
        assert approved == [], (
            "TraceLog write failure on a reject must never produce an approval"
        )


# ---------------------------------------------------------------------------
# Restart recovery — FlexibleHITL replays cached decisions from TraceLog
# ---------------------------------------------------------------------------

class TestRestartRecovery:
    """FlexibleHITL with replay_decisions=True must reuse TraceLog decisions on restart."""

    def test_gate_uses_cached_decision_on_restart(self):
        """After a restart the gate must replay the stored decision without re-asking."""
        import tempfile
        from pathlib import Path
        from antcrew.core.hitl import FlexibleHITL, HITLAction, HITLDecision
        from antcrew.core.state import TeamState
        from antcrew.trace import TraceLog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog = TraceLog(db_path)
        run_id = tlog.begin_run(thread_id="restart-t1", request="restart test", team="test")

        call_count = [0]

        def _callback(checkpoint, state):
            call_count[0] += 1
            return HITLDecision(action=HITLAction.APPROVE, reviewer_id="alice", reason="lgtm")

        # First run — callback fires once and records decision
        hitl1 = FlexibleHITL(callback=_callback, trace_log=tlog, run_id=run_id, replay_decisions=True)
        state = TeamState()
        hitl1.gate("plan_review", state)
        assert call_count[0] == 1

        # Simulate restart: new FlexibleHITL instance, same run_id, replay_decisions=True
        hitl2 = FlexibleHITL(callback=_callback, trace_log=tlog, run_id=run_id, replay_decisions=True)
        state2 = TeamState()
        result = hitl2.gate("plan_review", state2)

        tlog.close()
        Path(db_path).unlink(missing_ok=True)

        assert call_count[0] == 1, (
            "Callback must NOT fire again after restart — decision must be replayed from TraceLog"
        )
        assert result is True, "Replayed approve decision must return True"

    def test_restart_without_replay_calls_callback_again(self):
        """Without replay_decisions=True the gate calls the callback on every execution."""
        import tempfile
        from pathlib import Path
        from antcrew.core.hitl import FlexibleHITL, HITLAction, HITLDecision
        from antcrew.core.state import TeamState
        from antcrew.trace import TraceLog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog = TraceLog(db_path)
        run_id = tlog.begin_run(thread_id="no-replay-t1", request="no replay", team="test")

        call_count = [0]

        def _callback(checkpoint, state):
            call_count[0] += 1
            return HITLDecision(action=HITLAction.APPROVE, reviewer_id="alice")

        hitl1 = FlexibleHITL(callback=_callback, trace_log=tlog, run_id=run_id)
        hitl1.gate("plan_review", TeamState())

        hitl2 = FlexibleHITL(callback=_callback, trace_log=tlog, run_id=run_id)
        hitl2.gate("plan_review", TeamState())

        tlog.close()
        Path(db_path).unlink(missing_ok=True)

        assert call_count[0] == 2, "Without replay_decisions, each gate invocation calls the callback"

    def test_replayed_reject_still_rejects(self):
        """A cached reject replayed after restart must still return False."""
        import tempfile
        from pathlib import Path
        from antcrew.core.hitl import FlexibleHITL, HITLAction, HITLDecision
        from antcrew.core.state import TeamState
        from antcrew.trace import TraceLog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog = TraceLog(db_path)
        run_id = tlog.begin_run(thread_id="reject-replay-t1", request="reject replay", team="test")

        hitl1 = FlexibleHITL(
            callback=lambda cp, s: HITLDecision(action=HITLAction.REJECT, reason="too risky"),
            trace_log=tlog, run_id=run_id, replay_decisions=True,
        )
        hitl1.gate("plan_review", TeamState())

        callback_fired = [False]
        def _should_not_fire(cp, s):
            callback_fired[0] = True
            return HITLDecision(action=HITLAction.APPROVE)

        hitl2 = FlexibleHITL(callback=_should_not_fire, trace_log=tlog, run_id=run_id, replay_decisions=True)
        result = hitl2.gate("plan_review", TeamState())

        tlog.close()
        Path(db_path).unlink(missing_ok=True)

        assert not callback_fired[0], "Callback must not fire when replaying from TraceLog"
        assert result is False, "Replayed reject must return False"


# ---------------------------------------------------------------------------
# Chain integrity — insertion and reordering must be detected
# ---------------------------------------------------------------------------

class TestChainIntegrity:
    """verify_execution_chain must detect insertions and reordering, not only field edits."""

    def _populated_tlog(self, db_path):
        from antcrew.trace import TraceLog
        tlog = TraceLog(db_path)
        run_id = tlog.begin_run(thread_id="chain-t1", request="chain test", team="test")
        tlog.record_call(run_id=run_id, agent_name="agent_a", model_id="test", duration_ms=10, input_tokens=10, output_tokens=20, cost_usd=0.01)
        tlog.record_call(run_id=run_id, agent_name="agent_b", model_id="test", duration_ms=10, input_tokens=10, output_tokens=20, cost_usd=0.01)
        tlog.end_run(run_id, cost_usd=0.02, status="done")
        return tlog, run_id

    def test_chain_intact_on_unmodified_db(self):
        """verify_execution_chain returns valid=True on an untouched database."""
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog, run_id = self._populated_tlog(db_path)
        result = tlog.verify_execution_chain(run_id)
        tlog.close()
        Path(db_path).unlink(missing_ok=True)

        assert result["valid"] is True

    def test_chain_detects_injected_event(self):
        """Inserting an extra row with a fabricated hash breaks the chain."""
        import sqlite3
        import tempfile
        from pathlib import Path
        from antcrew.trace import TraceLog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog, run_id = self._populated_tlog(db_path)
        tlog.close()

        # Directly inject a row between sequence 2 and 3, renumbering sequence 3→4
        con = sqlite3.connect(db_path)
        con.execute(
            "UPDATE execution_events SET sequence = 4 WHERE run_id=? AND sequence=3",
            (run_id,),
        )
        con.execute(
            """INSERT INTO execution_events
               (run_id, sequence, event_type, actor, payload_hash, previous_hash, row_hash, recorded_at)
               VALUES (?, 3, 'agent_call', 'injected', 'fakehash', 'fakeprev', 'fakerow', datetime('now'))""",
            (run_id,),
        )
        con.commit()
        con.close()

        tlog2 = TraceLog(db_path)
        result = tlog2.verify_execution_chain(run_id)
        tlog2.close()
        Path(db_path).unlink(missing_ok=True)

        assert result["valid"] is False, "Injected row must break the chain"

    def test_chain_detects_reordered_events(self):
        """Swapping the sequence numbers of two events breaks the chain."""
        import sqlite3
        import tempfile
        from pathlib import Path
        from antcrew.trace import TraceLog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        tlog, run_id = self._populated_tlog(db_path)
        tlog.close()

        con = sqlite3.connect(db_path)
        # Swap sequences 2 and 3 (agent_a call and agent_b call)
        con.execute(
            "UPDATE execution_events SET sequence = 99 WHERE run_id=? AND sequence=2",
            (run_id,),
        )
        con.execute(
            "UPDATE execution_events SET sequence = 2 WHERE run_id=? AND sequence=3",
            (run_id,),
        )
        con.execute(
            "UPDATE execution_events SET sequence = 3 WHERE run_id=? AND sequence=99",
            (run_id,),
        )
        con.commit()
        con.close()

        tlog2 = TraceLog(db_path)
        result = tlog2.verify_execution_chain(run_id)
        tlog2.close()
        Path(db_path).unlink(missing_ok=True)

        assert result["valid"] is False, "Reordered events must break the chain"


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
