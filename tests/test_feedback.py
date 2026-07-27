"""Tests for FeedbackRunner, FeedbackLoop, and FeatureTeam feedback integration (v0.11.9)."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from antcrew.core.feedback import FeedbackLoop, FeedbackResult, FeedbackRunner
from antcrew.models.simulated import SimulatedLLM

# ---------------------------------------------------------------------------
# FeedbackResult
# ---------------------------------------------------------------------------

class TestFeedbackResult:
    def test_ok_true(self):
        r = FeedbackResult(ok=True, output="3 passed", returncode=0, duration_ms=50)
        assert r.ok

    def test_ok_false(self):
        r = FeedbackResult(ok=False, output="FAILED", returncode=1, duration_ms=50)
        assert not r.ok

    def test_short_no_truncation(self):
        r = FeedbackResult(ok=False, output="short", returncode=1, duration_ms=1)
        assert r.short() == "short"

    def test_short_truncates_long_output(self):
        long_out = "x" * 5000
        r = FeedbackResult(ok=False, output=long_out, returncode=1, duration_ms=1)
        s = r.short(max_chars=100)
        assert len(s) <= 200  # head + separator + tail
        assert "truncated" in s


# ---------------------------------------------------------------------------
# FeedbackRunner
# ---------------------------------------------------------------------------

class TestFeedbackRunner:
    def test_successful_command(self):
        runner = FeedbackRunner([sys.executable, "-c", "print('ok')"])
        result = runner.run()
        assert result.ok
        assert "ok" in result.output
        assert result.returncode == 0

    def test_failing_command(self):
        runner = FeedbackRunner([sys.executable, "-c", "raise SystemExit(1)"])
        result = runner.run()
        assert not result.ok
        assert result.returncode != 0

    def test_output_captured(self):
        runner = FeedbackRunner([sys.executable, "-c", "print('hello world')"])
        result = runner.run()
        assert "hello world" in result.output

    def test_stderr_captured(self):
        runner = FeedbackRunner(
            [sys.executable, "-c", "import sys; sys.stderr.write('err msg')"]
        )
        result = runner.run()
        assert "err msg" in result.output

    def test_timeout_returns_failure(self):
        runner = FeedbackRunner(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout=0.1,
        )
        result = runner.run()
        assert not result.ok
        assert "timed out" in result.output.lower()

    def test_work_dir_applied(self, tmp_path):
        (tmp_path / "marker.txt").write_text("found")
        runner = FeedbackRunner(
            [sys.executable, "-c",
             "from pathlib import Path; print(Path('marker.txt').read_text())"],
            work_dir=tmp_path,
        )
        result = runner.run()
        assert result.ok
        assert "found" in result.output

    def test_duration_ms_positive(self):
        runner = FeedbackRunner([sys.executable, "-c", "pass"])
        result = runner.run()
        assert result.duration_ms >= 0

    def test_invalid_command_returns_failure(self):
        runner = FeedbackRunner(["no_such_command_xyz_abc"])
        result = runner.run()
        assert not result.ok


# ---------------------------------------------------------------------------
# FeedbackLoop
# ---------------------------------------------------------------------------

class TestFeedbackLoop:
    def _make_runner(self, *, ok_after: int = 0):
        """Return a FeedbackRunner mock that fails `ok_after - 1` times then passes."""
        runner = MagicMock(spec=FeedbackRunner)
        call_count = [0]

        def _run():
            call_count[0] += 1
            if call_count[0] > ok_after:
                return FeedbackResult(ok=True, output="passed", returncode=0, duration_ms=1)
            return FeedbackResult(ok=False, output=f"FAIL round {call_count[0]}", returncode=1, duration_ms=1)

        runner.run.side_effect = _run
        return runner

    def test_passes_first_round(self):
        runner = self._make_runner(ok_after=0)
        loop = FeedbackLoop(runner=runner, max_rounds=3)
        agent_fn = MagicMock(return_value={"feature_output": "code"})
        state = loop.run(agent_fn, {"request": "build"})
        assert state["feedback_ok"] is True
        assert state["feedback_rounds_used"] == 1
        assert agent_fn.call_count == 1

    def test_passes_second_round(self):
        runner = self._make_runner(ok_after=1)
        loop = FeedbackLoop(runner=runner, max_rounds=3)
        agent_fn = MagicMock(return_value={"feature_output": "code"})
        state = loop.run(agent_fn, {"request": "build"})
        assert state["feedback_ok"] is True
        assert state["feedback_rounds_used"] == 2
        assert agent_fn.call_count == 2

    def test_exhausts_budget(self):
        runner = self._make_runner(ok_after=99)  # always fails
        loop = FeedbackLoop(runner=runner, max_rounds=2)
        agent_fn = MagicMock(return_value={"feature_output": "code"})
        state = loop.run(agent_fn, {"request": "build"})
        assert state["feedback_ok"] is False
        assert state["feedback_rounds_used"] == 2
        assert agent_fn.call_count == 2

    def test_error_injected_into_state(self):
        runner = self._make_runner(ok_after=1)  # fails first, passes second
        loop = FeedbackLoop(runner=runner, max_rounds=3)
        calls_with_state = []

        def agent_fn(state):
            calls_with_state.append(dict(state))
            return {"feature_output": "fixed code"}

        loop.run(agent_fn, {"request": "build"})
        # first call: no error
        assert calls_with_state[0].get("_feedback_error", "") == ""
        # second call: error from first round
        assert "FAIL" in calls_with_state[1]["_feedback_error"]

    def test_round_number_injected(self):
        runner = self._make_runner(ok_after=1)
        loop = FeedbackLoop(runner=runner, max_rounds=3)
        rounds_seen = []

        def agent_fn(state):
            rounds_seen.append(state.get("_feedback_round"))
            return {}

        loop.run(agent_fn, {"request": "x"})
        assert rounds_seen[0] == 1
        assert rounds_seen[1] == 2

    def test_error_cleared_on_pass(self):
        runner = self._make_runner(ok_after=1)
        loop = FeedbackLoop(runner=runner, max_rounds=3)
        state = loop.run(MagicMock(return_value={}), {"request": "x"})
        assert state["_feedback_error"] == ""

    def test_max_rounds_one(self):
        runner = self._make_runner(ok_after=0)  # passes immediately
        loop = FeedbackLoop(runner=runner, max_rounds=1)
        state = loop.run(MagicMock(return_value={}), {"request": "x"})
        assert state["feedback_ok"] is True

    def test_invalid_max_rounds_raises(self):
        runner = MagicMock(spec=FeedbackRunner)
        with pytest.raises(ValueError):
            FeedbackLoop(runner=runner, max_rounds=0)

    def test_preserves_existing_state_keys(self):
        runner = self._make_runner(ok_after=0)
        loop = FeedbackLoop(runner=runner, max_rounds=2)
        state = loop.run(MagicMock(return_value={"new_key": "val"}), {"prior_key": "kept"})
        assert state["prior_key"] == "kept"
        assert state["new_key"] == "val"


# ---------------------------------------------------------------------------
# FeatureTeam with feedback
# ---------------------------------------------------------------------------

class TestFeatureTeamFeedback:
    def test_no_feedback_loop_by_default(self):
        from antcrew.agents.feature_agent import FeatureTeam
        team = FeatureTeam(llm=SimulatedLLM())
        assert team._feedback_loop is None

    def test_feedback_loop_created_when_configured(self):
        from antcrew.agents.feature_agent import FeatureTeam
        team = FeatureTeam(
            llm=SimulatedLLM(),
            max_feedback_rounds=2,
            validate_cmd=["echo", "ok"],
        )
        assert team._feedback_loop is not None

    def test_no_loop_without_validate_cmd(self):
        from antcrew.agents.feature_agent import FeatureTeam
        team = FeatureTeam(llm=SimulatedLLM(), max_feedback_rounds=3)
        assert team._feedback_loop is None

    def test_run_with_passing_validation(self, tmp_path):
        from antcrew.agents.feature_agent import FeatureTeam
        team = FeatureTeam(
            llm=SimulatedLLM(),
            project_dir=str(tmp_path),
            max_feedback_rounds=2,
            validate_cmd=[sys.executable, "-c", "pass"],
        )
        result = team.run("Write a file")
        assert result.state.get("feedback_ok") is True
        assert result.state.get("feedback_rounds_used") == 1

    def test_run_with_always_failing_validation(self, tmp_path):
        from antcrew.agents.feature_agent import FeatureTeam
        team = FeatureTeam(
            llm=SimulatedLLM(),
            project_dir=str(tmp_path),
            max_feedback_rounds=2,
            validate_cmd=[sys.executable, "-c", "raise SystemExit(1)"],
        )
        result = team.run("Write a file")
        assert result.state.get("feedback_ok") is False
        assert result.state.get("feedback_rounds_used") == 2

    def test_feedback_error_visible_in_agent_context(self, tmp_path):
        from antcrew.agents.feature_agent import FeatureAgent
        agent = FeatureAgent(SimulatedLLM())
        state_with_error = {
            "request": "Fix the bug",
            "_feedback_error": "ImportError: No module named 'foo'",
            "_feedback_round": 1,
        }
        result = agent.run(state_with_error)
        assert "feature_output" in result


# ---------------------------------------------------------------------------
# YAML config: feedback_rounds + validate_cmd
# ---------------------------------------------------------------------------

class TestConfigFeedbackYAML:
    def test_feedback_rounds_parsed(self, tmp_path):
        cfg = f"""
team: feature
model: simulated
feedback_rounds: 3
validate_cmd:
  - {sys.executable}
  - -c
  - pass
"""
        cfg_file = tmp_path / "t.yaml"
        cfg_file.write_text(cfg, encoding="utf-8")
        from antcrew.agents.feature_agent import FeatureTeam
        from antcrew.config import load
        team = load(cfg_file)
        assert isinstance(team, FeatureTeam)
        assert team._feedback_loop is not None
        assert team._feedback_loop.max_rounds == 3

    def test_no_feedback_rounds_no_loop(self, tmp_path):
        cfg = "team: feature\nmodel: simulated\n"
        cfg_file = tmp_path / "t.yaml"
        cfg_file.write_text(cfg, encoding="utf-8")
        from antcrew.agents.feature_agent import FeatureTeam
        from antcrew.config import load
        team = load(cfg_file)
        assert isinstance(team, FeatureTeam)
        assert team._feedback_loop is None


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

class TestPublicExports:
    def test_feedback_runner_exported(self):
        from antcrew import FeedbackRunner
        assert FeedbackRunner is not None

    def test_feedback_loop_exported(self):
        from antcrew import FeedbackLoop
        assert FeedbackLoop is not None

    def test_feedback_result_exported(self):
        from antcrew import FeedbackResult
        assert FeedbackResult is not None
