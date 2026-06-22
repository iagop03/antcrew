from __future__ import annotations

from antcrew.core.agent import BaseAgent, _json_loads, _strip_fences
from antcrew.core.artifacts import PRD
from antcrew.core.state import TeamState

_SYSTEM = """\
You are a senior Business Analyst on a software development team.
Your job is to turn a high-level product request into a structured PRD (Product Requirements Document).

Respond ONLY with a valid JSON object matching this schema (no markdown fences, no prose):
{
  "title": "...",
  "summary": "...",
  "goals": ["...", ...],
  "out_of_scope": ["...", ...],
  "functional_requirements": ["...", ...],
  "non_functional_requirements": ["...", ...],
  "open_questions": ["...", ...]
}
"""

_REFINE_SYSTEM = """\
You are a senior Business Analyst. The reviewer provided feedback on the PRD you wrote.
Update the PRD to address the feedback while keeping everything else intact.

Current PRD:
{artifact_json}

Reviewer feedback:
{feedback}

Respond ONLY with the complete updated PRD JSON (no markdown fences, no prose).
"""


class BusinessAnalystAgent(BaseAgent):
    name = "business_analyst"
    role_description = "Converts a product request into a structured PRD."
    conversational = True

    def run(self, state: TeamState) -> dict:
        memory_ctx = self._recall(state["request"])
        analysis = state.get("codebase_analysis")
        if analysis:
            user_msg = (
                "EXISTING CODEBASE CONTEXT — this is a continuation, not a greenfield project:\n"
                f"Tech stack: {', '.join(analysis.tech_stack)}\n"
                f"What already exists: {analysis.what_exists}\n"
                f"What is missing / to build: {analysis.what_is_missing}\n"
                f"Context: {analysis.continuation_context}\n\n"
                f"REQUEST (extend the existing project):\n{state['request']}"
            )
        else:
            user_msg = state["request"]
        raw = self.system(_SYSTEM + memory_ctx, user_msg)
        prd = PRD.model_validate(_json_loads(_strip_fences(raw)))
        return {
            "prd": prd,
            "current_agent": self.name,
            "messages": [{"role": "assistant", "content": f"[BA] PRD created: {prd.title}"}],
        }

    def refine(self, state: TeamState, artifact: PRD, feedback: str) -> dict:
        raw = self.system(
            _REFINE_SYSTEM.format(
                artifact_json=artifact.model_dump_json(indent=2),
                feedback=feedback,
            ),
            "Revise the PRD based on the feedback.",
        )
        prd = PRD.model_validate(_json_loads(_strip_fences(raw)))
        return {"prd": prd}
