"""Feedback loop — execute-validate-retry for code-generating agents.

The loop runs an agent, executes a validation command against the written
files, and feeds the error output back into the agent's context for the next
round — repeating until validation passes or the round budget is exhausted.

Usage — Python::

    from antcrew.core.feedback import FeedbackRunner, FeedbackLoop
    from antcrew.agents.feature_agent import FeatureAgent
    from antcrew.models.anthropic_model import AnthropicModel

    runner = FeedbackRunner(["pytest", "-x", "--tb=short"], work_dir="./src")
    loop   = FeedbackLoop(runner=runner, max_rounds=3)
    agent  = FeatureAgent(AnthropicModel(), project_dir="./src")

    final_state = loop.run(agent.run, {"request": "Add JWT auth"})
    print(final_state["feedback_rounds_used"])   # 1, 2, or 3
    print(final_state["feedback_ok"])            # True if validation passed

Usage — FeatureTeam shorthand::

    from antcrew.agents.feature_agent import FeatureTeam

    team = FeatureTeam(
        llm=AnthropicModel(),
        project_dir="./src",
        max_feedback_rounds=3,
        validate_cmd=["pytest", "-x", "--tb=short"],
    )
    result = team.run("Add JWT auth")
    print(result.state["feedback_ok"])

YAML config::

    team: feature
    model: claude-sonnet-4-6
    project_dir: ./src
    feedback_rounds: 3
    validate_cmd: ["pytest", "-x", "--tb=short"]
"""
from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FeedbackResult
# ---------------------------------------------------------------------------

@dataclass
class FeedbackResult:
    """Outcome of one validation run."""

    ok: bool
    output: str
    returncode: int
    duration_ms: float

    def short(self, max_chars: int = 2000) -> str:
        """Truncated output suitable for injecting into an LLM context."""
        if len(self.output) <= max_chars:
            return self.output
        half = max_chars // 2
        return self.output[:half] + "\n...[truncated]...\n" + self.output[-half:]


# ---------------------------------------------------------------------------
# FeedbackRunner
# ---------------------------------------------------------------------------

class FeedbackRunner:
    """Runs a shell command and returns a :class:`FeedbackResult`.

    Args:
        cmd:      Command as a list (preferred) or shell string.
        work_dir: Working directory for the command.
        timeout:  Seconds before the subprocess is killed (default 60).
        env:      Extra environment variables merged with the current env.
    """

    def __init__(
        self,
        cmd: Union[list[str], str],
        *,
        work_dir: Optional[Union[str, Path]] = None,
        timeout: float = 60.0,
        env: Optional[dict[str, str]] = None,
    ) -> None:
        self.cmd = cmd
        self.work_dir = Path(work_dir) if work_dir else None
        self.timeout = timeout
        self._env = env

    def run(self) -> FeedbackResult:
        import os
        env = os.environ.copy()
        if self._env:
            env.update(self._env)

        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                self.cmd,
                cwd=self.work_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
                shell=isinstance(self.cmd, str),
            )
            output = (proc.stdout + proc.stderr).strip()
            ok = proc.returncode == 0
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            output = f"Command timed out after {self.timeout}s: {self.cmd}"
            ok = False
            rc = -1
        except Exception as exc:
            output = f"Failed to run command {self.cmd}: {exc}"
            ok = False
            rc = -2

        duration_ms = (time.monotonic() - t0) * 1000
        log.debug(
            "feedback_run ok=%s rc=%d duration_ms=%.0f cmd=%r",
            ok, rc, duration_ms, self.cmd,
        )
        return FeedbackResult(ok=ok, output=output, returncode=rc, duration_ms=duration_ms)


# ---------------------------------------------------------------------------
# FeedbackLoop
# ---------------------------------------------------------------------------

_ERROR_KEY = "_feedback_error"
_ROUND_KEY = "_feedback_round"


class FeedbackLoop:
    """Execute-validate-retry wrapper for any agent ``run()`` function.

    After each agent call the validation command runs.  On failure the
    error output is injected into the state so the agent sees it on the
    next round.  Agents that check ``state.get("_feedback_error")`` will
    receive the full error text and can self-correct.

    State keys written:
        ``feedback_ok``          — True when validation passed.
        ``feedback_rounds_used`` — how many rounds were consumed.
        ``_feedback_error``      — last error text (empty string when ok).
        ``_feedback_round``      — current round number (1-based).
    """

    def __init__(
        self,
        *,
        runner: FeedbackRunner,
        max_rounds: int = 3,
        max_error_chars: int = 2000,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")
        self._runner = runner
        self.max_rounds = max_rounds
        self.max_error_chars = max_error_chars

    def run(
        self,
        agent_fn: Callable[[dict], dict],
        state: dict,
    ) -> dict:
        """Run *agent_fn* with feedback until validation passes or budget runs out.

        Args:
            agent_fn: The agent's ``run(state) -> dict`` method.
            state:    Initial state dict; mutated in-place and returned.

        Returns:
            The final merged state dict.
        """
        state = dict(state)

        for round_num in range(1, self.max_rounds + 1):
            state[_ROUND_KEY] = round_num
            log.info("feedback_loop round=%d/%d", round_num, self.max_rounds)

            agent_result = agent_fn(state)
            state.update(agent_result)

            feedback = self._runner.run()

            if feedback.ok:
                log.info("feedback_loop passed round=%d", round_num)
                state["feedback_ok"] = True
                state["feedback_rounds_used"] = round_num
                state[_ERROR_KEY] = ""
                return state

            log.warning(
                "feedback_loop FAILED round=%d/%d rc=%d",
                round_num, self.max_rounds, feedback.returncode,
            )
            state[_ERROR_KEY] = feedback.short(self.max_error_chars)

        state["feedback_ok"] = False
        state["feedback_rounds_used"] = self.max_rounds
        return state
