"""Tests for FlexibleHITL strict mode (fail-closed on unregistered checkpoints)."""
from __future__ import annotations

import pytest

from antcrew.core.hitl import FlexibleHITL, HITLAction, HITLDecision


def _approve(_checkpoint, _state):
    return HITLDecision(action=HITLAction.APPROVE, reviewer_id="test")


def _reject(_checkpoint, _state):
    return HITLDecision(action=HITLAction.REJECT, reviewer_id="test")


class TestStrictModeConstruction:
    def test_strict_without_checkpoints_raises(self):
        with pytest.raises(ValueError, match="strict=True"):
            FlexibleHITL(callback=_approve, strict=True)

    def test_strict_with_checkpoints_ok(self):
        hitl = FlexibleHITL(callback=_approve, strict=True, checkpoints=["step1"])
        assert hitl is not None

    def test_non_strict_without_checkpoints_ok(self):
        hitl = FlexibleHITL(callback=_approve)
        assert hitl is not None


class TestStrictModeGate:
    def test_unlisted_checkpoint_fails_closed_in_strict_mode(self):
        hitl = FlexibleHITL(callback=_approve, strict=True, checkpoints=["step1"])
        # "unknown_step" is not in the registered list — must be rejected
        result = hitl.gate("unknown_step", {})
        assert result is False

    def test_listed_checkpoint_passes_in_strict_mode(self):
        hitl = FlexibleHITL(callback=_approve, strict=True, checkpoints=["step1"])
        result = hitl.gate("step1", {})
        assert result is True

    def test_unlisted_checkpoint_passes_in_non_strict_mode(self):
        hitl = FlexibleHITL(callback=_approve, checkpoints=["step1"])
        # Legacy behavior: unknown checkpoints pass through
        result = hitl.gate("unknown_step", {})
        assert result is True

    def test_strict_reject_from_callback_still_rejects(self):
        hitl = FlexibleHITL(callback=_reject, strict=True, checkpoints=["step1"])
        result = hitl.gate("step1", {})
        assert result is False

    def test_strict_unlisted_not_recorded_in_history(self):
        hitl = FlexibleHITL(callback=_approve, strict=True, checkpoints=["step1"])
        hitl.gate("unknown_step", {})
        # The early-exit path does not record history (same as non-strict pass-through)
        assert len(hitl.history) == 0

    def test_strict_with_multiple_checkpoints(self):
        hitl = FlexibleHITL(
            callback=_approve,
            strict=True,
            checkpoints=["triage_review", "analysis_review"],
        )
        assert hitl.gate("triage_review", {}) is True
        assert hitl.gate("analysis_review", {}) is True
        assert hitl.gate("injected_checkpoint", {}) is False

    def test_strict_auto_approve_listed(self):
        hitl = FlexibleHITL(auto_approve=True, strict=True, checkpoints=["step1"])
        result = hitl.gate("step1", {})
        assert result is True

    def test_strict_auto_approve_unlisted_fails_closed(self):
        hitl = FlexibleHITL(auto_approve=True, strict=True, checkpoints=["step1"])
        result = hitl.gate("unlisted_step", {})
        assert result is False
