"""FeatureAgent — vertical-slice agent that owns a feature end-to-end.

Unlike role-based pipelines (PM → backend_dev → frontend_dev → QA), the
FeatureAgent handles a complete feature in a single context window, using
tools to read existing code, write new files, and execute validation:

    from antcrew.agents.feature_agent import FeatureAgent, FeatureTeam
    from antcrew.models.anthropic_model import AnthropicModel

    llm = AnthropicModel()
    team = FeatureTeam(llm=llm, project_dir="./src")
    result = team.run("Add JWT authentication to the REST API")
    # result.state["files_written"] → list of paths the agent touched
    # result.state["feature_output"] → summary + implementation notes

Or as part of a CustomTeam pipeline::

    team = CustomTeam(
        steps=[
            {"name": "planner",  "system_prompt": "Plan: {request}",
             "output_key": "plan"},
            {"name": "feature",  "type": "feature_agent",
             "project_dir": "./src", "max_tool_steps": 12},
        ],
        llm=llm,
    )

YAML config::

    team: feature
    model: claude
    project_dir: ./src
    max_tool_steps: 12
    max_cost_usd: 3.0
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from antcrew.core.agent import BaseAgent
from antcrew.core.tools import (
    BaseTool, CodeExecutorTool, ListDirTool, ReadFileTool, WriteFileTool,
)
from antcrew.core.run_result import RunResult

if TYPE_CHECKING:
    from antcrew.models.base import BaseLLM
    from antcrew.core.agent import TeamState

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_FEATURE_SYSTEM = """\
You are a senior full-stack engineer responsible for implementing a complete \
feature from end to end. You have access to tools to read existing code, write \
new files, and execute Python to validate your work.

## Workflow

1. **Understand** — Read relevant existing files with `read_file` and `list_dir` \
to understand the project structure.
2. **Plan** — Write a short numbered plan (3-7 steps) before touching any files.
3. **Implement** — Write each file with `write_file`. Write the complete, \
production-ready content — no placeholders, no TODOs.
4. **Validate** — Use `execute_code` to run quick sanity checks (import tests, \
unit tests, syntax checks) where possible.
5. **Summarise** — After all tool calls, respond with:
   - A list of files written/modified
   - A brief description of what was implemented
   - Any follow-up steps (migrations, config changes, etc.)

## Rules
- Always read before writing so you understand existing conventions.
- Keep each file complete — never emit partial code.
- If a file already exists, preserve its style, imports, and formatting.
- Do not hallucinate file paths — use `list_dir` to discover the real structure.
"""

# ---------------------------------------------------------------------------
# FeatureAgent
# ---------------------------------------------------------------------------

_FILE_PATH_RE = re.compile(r"Written:\s*(.+)")


class FeatureAgent(BaseAgent):
    """Vertical-slice agent: single LLM, full context, tool-powered."""

    name = "feature"

    def __init__(
        self,
        llm: "BaseLLM",
        *,
        project_dir: Optional[str] = None,
        extra_tools: Optional[list[BaseTool]] = None,
        max_tool_steps: int = 10,
        system_prompt_override: Optional[str] = None,
        **kwargs,
    ) -> None:
        self._project_dir = Path(project_dir).resolve() if project_dir else None
        self._system_prompt_override = system_prompt_override

        tools: list[BaseTool] = [
            ReadFileTool(root=str(self._project_dir) if self._project_dir else None),
            WriteFileTool(root=str(self._project_dir) if self._project_dir else None),
        ]
        if self._project_dir:
            tools.append(ListDirTool(root=str(self._project_dir)))
        tools.append(CodeExecutorTool())
        if extra_tools:
            tools.extend(extra_tools)

        super().__init__(llm, tools=tools, max_tool_steps=max_tool_steps, **kwargs)

    def run(self, state: "TeamState") -> dict:
        request = state.get("request", "")
        plan = state.get("plan", "")
        feedback_error = state.get("_feedback_error", "")
        feedback_round = state.get("_feedback_round", 0)

        context_parts: list[str] = []
        if plan:
            context_parts.append(f"Project plan:\n{plan}")
        if self._project_dir:
            context_parts.append(f"Project root: {self._project_dir}")
        if feedback_error:
            context_parts.append(
                f"[Feedback round {feedback_round} — validation FAILED]\n"
                f"The following errors were produced when running the validation "
                f"command against your previously written files. Read the files "
                f"you wrote, diagnose the root cause, and fix them.\n\n"
                f"```\n{feedback_error}\n```"
            )

        if context_parts:
            user = "\n\n".join(context_parts) + f"\n\nFeature to implement: {request}"
        else:
            user = f"Feature to implement: {request}"

        system = self._system_prompt_override or _FEATURE_SYSTEM
        response = self.system_with_tools(system, user)

        files_written = _FILE_PATH_RE.findall(response)

        return {
            "feature_output": response,
            "files_written": files_written,
        }


# ---------------------------------------------------------------------------
# FeatureTeam — thin wrapper so FeatureAgent can be used as a team
# ---------------------------------------------------------------------------

class FeatureTeam:
    """Single-agent team that owns a feature end-to-end.

    Has the same ``run(request) -> RunResult`` interface as DevTeam /
    CustomTeam so it works everywhere a team is expected.

    When *max_feedback_rounds* > 0 and *validate_cmd* is set, the team runs a
    feedback loop: after the agent writes files the command executes; on
    failure the error output is injected back into the agent's context for the
    next round.

    Args:
        max_feedback_rounds: Max rounds of execute-validate-retry (0 = disabled).
        validate_cmd:        Command to run for validation, e.g.
                             ``["pytest", "-x", "--tb=short"]``.
        validate_timeout:    Seconds before the validation process is killed.
    """

    def __init__(
        self,
        *,
        llm: "BaseLLM",
        project_dir: Optional[str] = None,
        extra_tools: Optional[list[BaseTool]] = None,
        max_tool_steps: int = 10,
        max_cost_usd: Optional[float] = None,
        system_prompt_override: Optional[str] = None,
        max_feedback_rounds: int = 0,
        validate_cmd: Optional[list[str]] = None,
        validate_timeout: float = 60.0,
    ) -> None:
        self._agent = FeatureAgent(
            llm,
            project_dir=project_dir,
            extra_tools=extra_tools,
            max_tool_steps=max_tool_steps,
            max_cost_usd=max_cost_usd,
            system_prompt_override=system_prompt_override,
        )
        self.llm = llm
        self._feedback_loop = None
        if max_feedback_rounds > 0 and validate_cmd:
            from antcrew.core.feedback import FeedbackRunner, FeedbackLoop
            runner = FeedbackRunner(
                validate_cmd,
                work_dir=project_dir,
                timeout=validate_timeout,
            )
            self._feedback_loop = FeedbackLoop(runner=runner, max_rounds=max_feedback_rounds)

    def run(self, request: str) -> RunResult:
        state: dict = {"request": request}
        if self._feedback_loop is not None:
            state = self._feedback_loop.run(self._agent.run, state)
        else:
            result = self._agent.run(state)
            state.update(result)
        return RunResult(state=state)
