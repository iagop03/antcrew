"""QuickAgent — define an agent with a role string, no class needed.

Mirrors the CrewAI Agent(role=..., goal=...) DX while staying on AntCrew's typed
architecture. QuickTeam is the companion team that chains QuickAgents sequentially.

CLI usage (antcrew quick):
    antcrew quick "Research AI agents" \\
        "Researcher: Find recent papers and report key findings" \\
        "Writer: Synthesize findings into a clear 300-word summary"

Python usage:
    from antcrew.agents.quick_agent import QuickAgent, QuickTeam
    from antcrew.models.anthropic_model import AnthropicModel

    llm = AnthropicModel()
    team = QuickTeam(
        specs=[
            "Researcher: Find recent papers on RAG",
            "Writer: Synthesize findings into a clear report",
        ],
        llm=llm,
    )
    result = team.run("What's new in vector retrieval?")
    print(result["result"])
"""
from __future__ import annotations

from typing import Any

from antcrew.core.agent import BaseAgent
from antcrew.core.state import TeamState
from antcrew.models.base import BaseLLM


class QuickAgent(BaseAgent):
    """An agent defined by a role+goal string pair — no subclass required.

    The agent produces its output under *output_key* (default "result") and
    threads a ``_quick_context`` key through state so subsequent agents see
    what previous agents wrote.
    """

    name: str = "quick_agent"
    role_description: str = ""
    stage: str = "inline"

    def __init__(
        self,
        llm: BaseLLM,
        *,
        role: str,
        goal: str,
        output_key: str = "result",
        **kwargs: Any,
    ) -> None:
        super().__init__(llm, **kwargs)
        slug = role.strip().lower().replace(" ", "_").replace("-", "_")
        self.name = slug or "quick_agent"
        self.role_description = goal.strip()
        self._output_key = output_key

    @classmethod
    def from_spec(
        cls,
        spec: str,
        llm: BaseLLM,
        output_key: str = "result",
    ) -> "QuickAgent":
        """Parse ``'Role: description of goal'`` or bare ``'Role'`` into a QuickAgent."""
        if ":" in spec:
            role, _, goal = spec.partition(":")
        else:
            role, goal = spec, spec
        return cls(llm, role=role.strip(), goal=goal.strip(), output_key=output_key)

    def run(self, state: TeamState) -> dict:
        request = state.get("request", "")
        prior: str = state.get("_quick_context", "")

        if prior:
            user_msg = f"{prior}\n\n---\nYour task: {self.role_description}\n\nOriginal request: {request}"
        else:
            user_msg = f"Task: {self.role_description}\n\nRequest: {request}"

        output = self.system(
            f"You are a {self.name.replace('_', ' ').title()}. {self.role_description}",
            user_msg,
        )
        return {
            self._output_key: output,
            "_quick_context": f"Output from {self.name}:\n{output}",
            "current_agent": self.name,
        }


class QuickTeam:
    """Sequential pipeline of QuickAgents — the fastest path to a working team.

    Each agent in *specs* sees the output of the previous one via ``_quick_context``.
    The final agent's output is stored under ``result`` in the returned state dict.

    Compatible with the platform runner (exposes ``_agents`` dict and ``run()``).
    """

    def __init__(self, specs: list[str], llm: BaseLLM) -> None:
        if not specs:
            raise ValueError("QuickTeam requires at least one agent spec")
        agents: list[QuickAgent] = []
        for i, spec in enumerate(specs):
            output_key = "result" if i == len(specs) - 1 else f"_quick_{i}"
            agents.append(QuickAgent.from_spec(spec, llm, output_key=output_key))
        self._agent_list: list[QuickAgent] = agents
        self._agents: dict[str, QuickAgent] = {a.name: a for a in agents}

    def run(self, request: str, **_kwargs: Any) -> dict:
        state: TeamState = {"request": request}
        for agent in self._agent_list:
            update = agent.run(state)
            state.update(update)
        return state
