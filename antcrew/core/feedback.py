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
from typing import Any, Callable, Optional, Union

from antcrew.core.events import bus

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
                shell=isinstance(self.cmd, str),  # nosec B602
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
        _run_id = state.get("_run_id")
        _thread_id = state.get("_thread_id")

        bus.emit("feedback.start",
                 {"max_rounds": self.max_rounds},
                 run_id=_run_id, thread_id=_thread_id)

        for round_num in range(1, self.max_rounds + 1):
            state[_ROUND_KEY] = round_num
            log.info("feedback_loop round=%d/%d", round_num, self.max_rounds)

            agent_result = agent_fn(state)
            state.update(agent_result)

            feedback = self._runner.run()

            bus.emit("feedback.round",
                     {"round": round_num, "max_rounds": self.max_rounds,
                      "ok": feedback.ok, "returncode": feedback.returncode},
                     run_id=_run_id, thread_id=_thread_id)

            if feedback.ok:
                log.info("feedback_loop passed round=%d", round_num)
                state["feedback_ok"] = True
                state["feedback_rounds_used"] = round_num
                state[_ERROR_KEY] = ""
                bus.emit("feedback.end",
                         {"rounds_used": round_num, "success": True},
                         run_id=_run_id, thread_id=_thread_id)
                return state

            log.warning(
                "feedback_loop FAILED round=%d/%d rc=%d",
                round_num, self.max_rounds, feedback.returncode,
            )
            state[_ERROR_KEY] = feedback.short(self.max_error_chars)

        state["feedback_ok"] = False
        state["feedback_rounds_used"] = self.max_rounds
        bus.emit("feedback.end",
                 {"rounds_used": self.max_rounds, "success": False},
                 run_id=_run_id, thread_id=_thread_id)
        return state


# ---------------------------------------------------------------------------
# Test-failure feedback loop for DevTeam / FullStackTeam
# ---------------------------------------------------------------------------

def _run_lint(lint_cmd: "Union[list[str], str]", work_dir: "Optional[Union[str, Path]]" = None) -> FeedbackResult:
    """Run a lint/typecheck command and return a :class:`FeedbackResult`."""
    return FeedbackRunner(lint_cmd, work_dir=work_dir, timeout=60.0).run()


def run_test_feedback_loop(
    state: dict,
    backend_agent: "Any",
    runner: "Any",
    *,
    max_rounds: int = 3,
    max_error_chars: int = 2000,
    lint_cmd: "Optional[Union[list[str], str]]" = None,
    work_dir: "Optional[Union[str, Path]]" = None,
) -> dict:
    """Execute-validate-retry loop driven by a sandbox test runner.

    After the main pipeline finishes, this loop:
    1. (Optional) Runs *lint_cmd* first — cheap static checks (mypy, ruff, eslint).
       Lint failures are fed to the agent before running the full test suite.
    2. Runs the sandbox tests with the current code + test artifacts.
    3. If they pass → returns immediately.
    4. If they fail → injects the truncated error into ``_feedback_error``,
       calls ``backend_agent.fix_test_failures(state)`` to get updated code,
       and repeats.

    Writes to state:
        ``feedback_ok``          — True when all checks passed.
        ``feedback_rounds_used`` — number of fix rounds consumed.
        ``_feedback_error``      — last error text (empty when ok).
        ``test_results``         — always the most recent test run result.
        ``lint_ok``              — True when lint passed (only set when lint_cmd given).

    Args:
        state:           Shared pipeline state dict (mutated in-place).
        backend_agent:   An agent with ``fix_test_failures(state) -> dict|None``.
        runner:          A sandbox runner with
                         ``run(test_artifacts, code_artifacts) -> TestResults``.
        max_rounds:      Maximum number of fix iterations (default: 3).
        max_error_chars: How many chars of error output to inject (default: 2000).
        lint_cmd:        Optional lint/typecheck command run before tests each round.
                         E.g. ``["ruff", "check", "."]`` or ``"mypy src/"``.
        work_dir:        Working directory for *lint_cmd*.
    """
    state = dict(state)
    _run_id = state.get("_run_id")
    _thread_id = state.get("_thread_id")

    bus.emit("feedback.start",
             {"max_rounds": max_rounds, "has_lint": bool(lint_cmd)},
             run_id=_run_id, thread_id=_thread_id)

    for round_num in range(1, max_rounds + 1):
        log.info("test_feedback_loop round=%d/%d", round_num, max_rounds)
        state["_feedback_round"] = round_num

        # --- lint pre-pass (optional) -----------------------------------------
        if lint_cmd:
            lint_result = _run_lint(lint_cmd, work_dir=work_dir)
            if not lint_result.ok:
                snippet = lint_result.short(max_error_chars)
                log.warning(
                    "test_feedback_loop lint FAILED round=%d — feeding to backend_dev",
                    round_num,
                )
                bus.emit("feedback.round",
                         {"round": round_num, "max_rounds": max_rounds,
                          "ok": False, "type": "lint"},
                         run_id=_run_id, thread_id=_thread_id)
                state["lint_ok"] = False
                state["_feedback_error"] = f"[LINT ERRORS]\n{snippet}"
                patch = backend_agent.fix_test_failures(state)
                if patch:
                    state.update(patch)
                else:
                    log.warning("test_feedback_loop: fix returned no patch after lint failure")
                    break
                continue  # re-run lint next round before tests
            state["lint_ok"] = True
            log.info("test_feedback_loop lint OK round=%d", round_num)

        # --- test run ------------------------------------------------------------
        try:
            test_results = runner.run(
                state.get("test_artifacts") or [],
                code_artifacts=state.get("code_artifacts") or [],
            )
        except Exception as exc:
            log.warning("test_feedback_loop runner error: %s", exc)
            break

        state["test_results"] = test_results

        if test_results.success:
            log.info("test_feedback_loop PASSED round=%d", round_num)
            state["feedback_ok"] = True
            state["feedback_rounds_used"] = round_num
            state["_feedback_error"] = ""
            bus.emit("feedback.round",
                     {"round": round_num, "max_rounds": max_rounds,
                      "ok": True, "type": "test"},
                     run_id=_run_id, thread_id=_thread_id)
            bus.emit("feedback.end",
                     {"rounds_used": round_num, "success": True},
                     run_id=_run_id, thread_id=_thread_id)
            return state

        raw_output = getattr(test_results, "output", "") or ""
        error_snippet = raw_output[-max_error_chars:] if len(raw_output) > max_error_chars else raw_output
        log.warning(
            "test_feedback_loop tests FAILED round=%d/%d — feeding to backend_dev",
            round_num, max_rounds,
        )
        bus.emit("feedback.round",
                 {"round": round_num, "max_rounds": max_rounds,
                  "ok": False, "type": "test"},
                 run_id=_run_id, thread_id=_thread_id)
        state["_feedback_error"] = error_snippet

        patch = backend_agent.fix_test_failures(state)
        if patch:
            state.update(patch)
        else:
            log.warning("test_feedback_loop: fix_test_failures returned no patch — stopping")
            break

    state.setdefault("feedback_ok", False)
    state.setdefault("feedback_rounds_used", max_rounds)
    bus.emit("feedback.end",
             {"rounds_used": state["feedback_rounds_used"], "success": False},
             run_id=_run_id, thread_id=_thread_id)
    return state
