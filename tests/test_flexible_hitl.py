"""Tests for FlexibleHITL — fail-safe, TraceLog persistence, REQUEST_CHANGES."""
from __future__ import annotations

import pytest

from antcrew.core.hitl import FlexibleHITL, HITLAction, HITLDecision
from antcrew.core.state import TeamState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state(**kw) -> TeamState:
    return TeamState(kw)


# ---------------------------------------------------------------------------
# Fail-safe: exception → REJECT (never APPROVE)
# ---------------------------------------------------------------------------

class TestFailSafe:
    def test_callback_exception_defaults_to_reject(self):
        def bad_callback(checkpoint, state):
            raise RuntimeError("network timeout")

        hitl = FlexibleHITL(callback=bad_callback)
        result = hitl.gate("prd_review", _state())
        assert result is False

    def test_callback_exception_recorded_in_history(self):
        def bad_callback(checkpoint, state):
            raise ValueError("unexpected input")

        hitl = FlexibleHITL(callback=bad_callback)
        hitl.gate("arch_review", _state())
        assert len(hitl.history) == 1
        _, decision = hitl.history[0]
        assert decision.action == HITLAction.REJECT
        assert "callback_error" in decision.reason

    def test_successful_approve_still_approves(self):
        def good_callback(checkpoint, state):
            return HITLDecision(action=HITLAction.APPROVE, reviewer_id="alice")

        hitl = FlexibleHITL(callback=good_callback)
        assert hitl.gate("prd_review", _state()) is True

    def test_auto_approve_bypasses_callback(self):
        hitl = FlexibleHITL(auto_approve=True)
        assert hitl.gate("any_checkpoint", _state()) is True


# ---------------------------------------------------------------------------
# Fail-safe: timeout / None callback → auto only, not a security hole
# ---------------------------------------------------------------------------

class TestNoneCallback:
    def test_no_callback_rejects(self):
        """callback=None with auto_approve=False is a misconfiguration — must reject, not approve."""
        hitl = FlexibleHITL(callback=None, auto_approve=False)
        assert hitl.gate("x", _state()) is False
        assert hitl.history[0][1].action.value == "reject"
        assert hitl.history[0][1].reason == "no_callback_configured"

    def test_no_callback_auto_approve_true_still_approves(self):
        """auto_approve=True overrides everything — callback=None is fine here."""
        hitl = FlexibleHITL(callback=None, auto_approve=True)
        assert hitl.gate("x", _state()) is True


# ---------------------------------------------------------------------------
# REQUEST_CHANGES
# ---------------------------------------------------------------------------

class TestRequestChanges:
    def test_request_changes_blocks_execution(self):
        def reviewer(checkpoint, state):
            return HITLDecision(
                action=HITLAction.REQUEST_CHANGES,
                reason="Missing error handling",
                reviewer_id="bob",
            )

        hitl = FlexibleHITL(callback=reviewer)
        assert hitl.gate("code_review", _state()) is False

    def test_request_changes_not_in_approved(self):
        d = HITLDecision(action=HITLAction.REQUEST_CHANGES)
        assert d.approved is False

    def test_approve_in_approved(self):
        assert HITLDecision(action=HITLAction.APPROVE).approved is True

    def test_modify_in_approved(self):
        assert HITLDecision(action=HITLAction.MODIFY).approved is True

    def test_skip_in_approved(self):
        assert HITLDecision(action=HITLAction.SKIP).approved is True

    def test_reject_not_in_approved(self):
        assert HITLDecision(action=HITLAction.REJECT).approved is False


# ---------------------------------------------------------------------------
# reviewer_id carried through
# ---------------------------------------------------------------------------

class TestReviewerId:
    def test_reviewer_id_stored_in_history(self):
        def reviewer(checkpoint, state):
            return HITLDecision(action=HITLAction.APPROVE, reviewer_id="alice@acme.com")

        hitl = FlexibleHITL(callback=reviewer)
        hitl.gate("final_approval", _state())
        _, decision = hitl.history[0]
        assert decision.reviewer_id == "alice@acme.com"

    def test_reviewer_id_empty_by_default(self):
        d = HITLDecision(action=HITLAction.APPROVE)
        assert d.reviewer_id == ""


# ---------------------------------------------------------------------------
# TraceLog persistence
# ---------------------------------------------------------------------------

class TestTraceLogPersistence:
    def test_decision_persisted_to_tracelog(self, tmp_path):
        from antcrew.trace import TraceLog

        tlog = TraceLog(tmp_path / "t.db")
        run_id = tlog.begin_run(thread_id="t1", request="r", team="DevTeam")

        def reviewer(checkpoint, state):
            return HITLDecision(
                action=HITLAction.APPROVE,
                reviewer_id="alice@acme.com",
                reason="LGTM",
            )

        hitl = FlexibleHITL(callback=reviewer, trace_log=tlog, run_id=run_id)
        hitl.gate("final_approval", _state())

        decisions = tlog.get_hitl_decisions(run_id)
        assert len(decisions) == 1
        d = decisions[0]
        assert d["decision"] == "approve"
        assert d["reviewer_id"] == "alice@acme.com"
        assert d["reason"] == "LGTM"
        assert d["step"] == "final_approval"
        tlog.close()

    def test_reject_persisted_to_tracelog(self, tmp_path):
        from antcrew.trace import TraceLog

        tlog = TraceLog(tmp_path / "t.db")
        run_id = tlog.begin_run(thread_id="t1", request="r", team="DevTeam")

        def reviewer(checkpoint, state):
            return HITLDecision(
                action=HITLAction.REJECT,
                reviewer_id="bob@acme.com",
                reason="Does not meet requirements",
            )

        hitl = FlexibleHITL(callback=reviewer, trace_log=tlog, run_id=run_id)
        hitl.gate("prd_gate", _state())

        decisions = tlog.get_hitl_decisions(run_id)
        assert decisions[0]["decision"] == "reject"
        assert decisions[0]["reviewer_id"] == "bob@acme.com"
        tlog.close()

    def test_exception_decision_persisted(self, tmp_path):
        from antcrew.trace import TraceLog

        tlog = TraceLog(tmp_path / "t.db")
        run_id = tlog.begin_run(thread_id="t1", request="r", team="DevTeam")

        def bad_callback(checkpoint, state):
            raise RuntimeError("platform unreachable")

        hitl = FlexibleHITL(callback=bad_callback, trace_log=tlog, run_id=run_id)
        hitl.gate("arch_review", _state())

        decisions = tlog.get_hitl_decisions(run_id)
        assert len(decisions) == 1
        assert decisions[0]["decision"] == "reject"
        assert "callback_error" in decisions[0]["reason"]
        tlog.close()

    def test_attach_trace_after_construction(self, tmp_path):
        from antcrew.trace import TraceLog

        tlog = TraceLog(tmp_path / "t.db")
        run_id = tlog.begin_run(thread_id="t1", request="r", team="DevTeam")

        hitl = FlexibleHITL(
            callback=lambda cp, st: HITLDecision(action=HITLAction.APPROVE, reviewer_id="carol")
        )
        hitl.attach_trace(tlog, run_id)
        hitl.gate("post_attach", _state())

        decisions = tlog.get_hitl_decisions(run_id)
        assert decisions[0]["reviewer_id"] == "carol"
        tlog.close()

    def test_run_id_override_in_gate(self, tmp_path):
        from antcrew.trace import TraceLog

        tlog = TraceLog(tmp_path / "t.db")
        run_id = tlog.begin_run(thread_id="t1", request="r", team="DevTeam")

        hitl = FlexibleHITL(
            callback=lambda cp, st: HITLDecision(action=HITLAction.APPROVE),
            trace_log=tlog,
        )
        hitl.gate("step", _state(), run_id=run_id)

        assert len(tlog.get_hitl_decisions(run_id)) == 1
        tlog.close()

    def test_no_tracelog_does_not_crash(self):
        hitl = FlexibleHITL(
            callback=lambda cp, st: HITLDecision(action=HITLAction.APPROVE)
        )
        # Should not raise even without trace_log
        assert hitl.gate("step", _state()) is True


# ---------------------------------------------------------------------------
# Checkpoints filter
# ---------------------------------------------------------------------------

class TestCheckpointsFilter:
    def test_unlisted_checkpoint_passes_through(self):
        called = []

        def reviewer(cp, st):
            called.append(cp)
            return HITLDecision(action=HITLAction.APPROVE)

        hitl = FlexibleHITL(callback=reviewer, checkpoints=["prd_gate"])
        hitl.gate("unlisted_step", _state())
        assert called == []

    def test_listed_checkpoint_invokes_callback(self):
        called = []

        def reviewer(cp, st):
            called.append(cp)
            return HITLDecision(action=HITLAction.APPROVE)

        hitl = FlexibleHITL(callback=reviewer, checkpoints=["prd_gate"])
        hitl.gate("prd_gate", _state())
        assert called == ["prd_gate"]


# ---------------------------------------------------------------------------
# MODIFY patches state
# ---------------------------------------------------------------------------

class TestModify:
    def test_modify_applies_patch(self):
        patch = {"prd": "updated PRD content"}

        def reviewer(cp, st):
            return HITLDecision(action=HITLAction.MODIFY, modified_state=patch)

        hitl = FlexibleHITL(callback=reviewer)
        state = _state(prd="original")
        result = hitl.gate("prd_review", state)
        assert result is True
        assert state["prd"] == "updated PRD content"
