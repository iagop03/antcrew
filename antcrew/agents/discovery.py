"""DiscoveryAgent — conversational requirements gathering.

Usage as a standalone interactive session (CLI):

    agent = DiscoveryAgent(llm)
    context = DiscoveryContext()
    while not context.is_complete:
        result = agent.ask_next(context)
        if result["is_complete"]:
            break
        answer = input(result["next_question"] + " ")
        context = agent.ingest(context, result["next_question"], answer)
    prd = agent.finalize(context)

Usage as a pipeline step (converts existing discovery_context → prd):

    team = CustomTeam(steps=[DiscoveryAgent(llm), BackendDevAgent(llm)])
    team.run(...)
"""
from __future__ import annotations

import json

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import (
    PRD,
    DiscoveryContext,
    DiscoveryQA,
)
from antcrew.core.state import TeamState

_MAX_QA_ROUNDS = 7

_ASK_SYSTEM = f"""\
You are a product discovery facilitator. Your job is to gather enough information to write
a clear Product Requirements Document (PRD) by asking one focused question at a time.

Given the current discovery context, decide:
1. Do we already have enough information? (problem_statement + target_users + at least 3 key_features + tech_stack)
2. If yes → set is_complete=true and leave next_question empty.
3. If no → ask the single most important missing question.

After {_MAX_QA_ROUNDS} Q&A rounds, always set is_complete=true regardless.

Respond ONLY with valid JSON (no markdown fences):
{{
  "next_question": "<the question to ask, or empty string if is_complete=true>",
  "is_complete": <true|false>,
  "rationale": "<one-sentence reason for this question or for completing>"
}}

Question priority order:
1. What problem does this solve? (problem_statement)
2. Who are the primary users? (target_users)
3. What are the 3-5 core features? (key_features)
4. What tech stack or platform? (tech_stack)
5. Any constraints (budget, timeline, security, regulations)?
6. Anything explicitly out of scope?

Keep questions conversational. Ask ONE thing per turn.
"""

_INGEST_SYSTEM = """\
You are extracting structured project information from a Q&A conversation.
Given the full conversation history, extract what we know so far.

Respond ONLY with valid JSON (no markdown fences):
{
  "project_name": "<short name, or empty string if unknown>",
  "problem_statement": "<what problem this solves, in 1-3 sentences>",
  "target_users": "<who uses this product>",
  "tech_stack": ["<technology>", ...],
  "constraints": ["<constraint>", ...],
  "key_features": ["<feature>", ...],
  "out_of_scope": ["<explicitly excluded item>", ...]
}

Rules:
- Only include information that was actually stated in the conversation.
- Use empty string or empty list for fields not yet covered.
- Keep each feature/constraint as a concise phrase.
- Do NOT invent requirements not mentioned.
"""

_FINALIZE_SYSTEM = """\
You are a product manager writing a Product Requirements Document (PRD).
Given a completed discovery context (structured requirements gathered via Q&A), write a full PRD.

Respond ONLY with valid JSON (no markdown fences):
{
  "title": "<product name and short descriptor>",
  "summary": "<2-3 sentence overview of the product and its value>",
  "goals": ["<business or user goal>", ...],
  "functional_requirements": ["<specific capability the product must have>", ...],
  "non_functional_requirements": ["<performance, security, scalability constraints>", ...],
  "out_of_scope": ["<what this product will NOT do>", ...],
  "open_questions": ["<anything still unclear that needs a decision>", ...]
}

Base the PRD tightly on the discovery context. Do not invent requirements not mentioned.
Translate key_features into specific functional_requirements.
"""


def _context_to_str(context: DiscoveryContext) -> str:
    qa_text = ""
    if context.qa_pairs:
        qa_text = "\n\nConversation so far:\n" + "\n".join(
            f"Q: {qa.question}\nA: {qa.answer}" for qa in context.qa_pairs
        )
    fields = {
        "project_name": context.project_name,
        "problem_statement": context.problem_statement,
        "target_users": context.target_users,
        "tech_stack": context.tech_stack,
        "key_features": context.key_features,
        "constraints": context.constraints,
        "out_of_scope": context.out_of_scope,
        "qa_rounds_completed": len(context.qa_pairs),
        "max_qa_rounds": _MAX_QA_ROUNDS,
    }
    return f"Current structured context:\n{json.dumps(fields, indent=2)}{qa_text}"


class DiscoveryAgent(BaseAgent):
    name = "discovery"
    role_description = "Conducts a conversational Q&A session to gather project requirements."
    consumes: list[str] = ["discovery_context"]
    produces: list[str] = ["prd"]

    # ── Interactive helpers ───────────────────────────────────────────────────

    def ask_next(self, context: DiscoveryContext) -> dict:
        """Given current context, return the next question or signal completion.

        Returns:
            {"next_question": str, "is_complete": bool, "rationale": str}
        """
        if context.is_complete or len(context.qa_pairs) >= _MAX_QA_ROUNDS:
            return {"next_question": "", "is_complete": True, "rationale": "Discovery complete."}

        data: dict = self.system_parsed(_ASK_SYSTEM, _context_to_str(context), dict)
        return {
            "next_question": data.get("next_question", ""),
            "is_complete": bool(data.get("is_complete", False)),
            "rationale": data.get("rationale", ""),
        }

    def ingest(self, context: DiscoveryContext, question: str, answer: str) -> DiscoveryContext:
        """Add a Q&A pair and re-extract structured fields from the full conversation.

        Returns an updated DiscoveryContext.
        """
        new_pair = DiscoveryQA(question=question, answer=answer)
        updated_pairs = context.qa_pairs + [new_pair]

        qa_text = "\n".join(
            f"Q: {qa.question}\nA: {qa.answer}" for qa in updated_pairs
        )
        data: dict = self.system_parsed(_INGEST_SYSTEM, f"Conversation:\n{qa_text}", dict)

        return DiscoveryContext(
            project_name=data.get("project_name") or context.project_name,
            problem_statement=data.get("problem_statement") or context.problem_statement,
            target_users=data.get("target_users") or context.target_users,
            tech_stack=data.get("tech_stack") or context.tech_stack,
            constraints=data.get("constraints") or context.constraints,
            key_features=data.get("key_features") or context.key_features,
            out_of_scope=data.get("out_of_scope") or context.out_of_scope,
            qa_pairs=updated_pairs,
            is_complete=len(updated_pairs) >= _MAX_QA_ROUNDS,
        )

    def finalize(self, context: DiscoveryContext) -> PRD:
        """Convert a completed DiscoveryContext into a PRD."""
        ctx_str = _context_to_str(context)
        data: dict = self.system_parsed(_FINALIZE_SYSTEM, ctx_str, dict, max_tokens=4096)
        try:
            return PRD(
                title=data.get("title", context.project_name or "Untitled Product"),
                summary=data.get("summary", context.problem_statement),
                goals=data.get("goals") or [],
                functional_requirements=data.get("functional_requirements") or context.key_features,
                non_functional_requirements=data.get("non_functional_requirements") or [],
                out_of_scope=data.get("out_of_scope") or context.out_of_scope,
                open_questions=data.get("open_questions") or [],
            )
        except Exception:
            return PRD(
                title=context.project_name or "Discovered Product",
                summary=context.problem_statement or "Requirements gathered via discovery session.",
                functional_requirements=context.key_features,
            )

    # ── Pipeline step ─────────────────────────────────────────────────────────

    def run(self, state: TeamState) -> dict:
        """Pipeline step: convert discovery_context → prd.

        Use this when DiscoveryAgent is part of a pipeline that starts from a
        previously completed DiscoveryContext (e.g. loaded from a file or platform session).
        """
        raw = state.get("discovery_context")
        if raw is None:
            return {"errors": ["DiscoveryAgent: no discovery_context in state — run `antcrew discover` first."]}

        if isinstance(raw, dict):
            try:
                context = DiscoveryContext.model_validate(raw)
            except Exception as exc:
                return {"errors": [f"DiscoveryAgent: invalid discovery_context: {exc}"]}
        elif isinstance(raw, DiscoveryContext):
            context = raw
        else:
            return {"errors": [f"DiscoveryAgent: unexpected discovery_context type {type(raw).__name__}"]}

        prd = self.finalize(context)
        return {
            "prd": prd,
            "current_agent": self.name,
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[Discovery] PRD generated from {len(context.qa_pairs)} Q&A rounds: "
                        f"'{prd.title}' — "
                        f"{len(prd.functional_requirements)} requirement(s)."
                    ),
                }
            ],
        }
