"""DiscoveryAgent — conversational requirements gathering.

Usage as standalone interactive session (CLI or script):

    agent = DiscoveryAgent(llm)
    context = agent.run_interactive()          # asks questions via stdin/stdout
    prd = agent.finalize(context)

Or with a custom I/O adapter (e.g. for the platform WebSocket):

    agent = DiscoveryAgent(llm, human_interface=MyPlatformInterface())
    context = agent.run_interactive(project_name="Customer portal")

Usage as a pipeline step — two modes:

  a) No prior context → runs the interactive loop automatically:
       team = CustomTeam(steps=[DiscoveryAgent(llm), BackendDevAgent(llm)])
       team.run({})   # discovery loop fires first, then backend dev continues

  b) Pre-loaded context → converts discovery_context → prd without Q&A:
       team.run({"discovery_context": existing_ctx})
"""
from __future__ import annotations

import json
from typing import Optional, Protocol, runtime_checkable

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import (
    PRD,
    DiscoveryContext,
    DiscoveryQA,
)
from antcrew.core.state import TeamState

# ── Human interface protocol ─────────────────────────────────────────────────

@runtime_checkable
class HumanInterface(Protocol):
    """Pluggable I/O adapter for the discovery Q&A loop.

    Implement this to integrate with any UI (web socket, Telegram, Slack…).
    The default implementation (CliHumanInterface) reads from stdin / writes
    to stdout, making DiscoveryAgent work out-of-the-box in CLI mode.
    """

    def ask(self, question: str) -> str:
        """Present *question* to the human and return their answer."""
        ...

    def notify(self, message: str) -> None:
        """Display a non-interactive informational message."""
        ...


class CliHumanInterface:
    """Default HumanInterface: reads from stdin, writes to stdout."""

    def ask(self, question: str) -> str:
        print(f"\n{question}")
        try:
            return input("> ").strip()
        except EOFError:
            return ""

    def notify(self, message: str) -> None:
        print(message)


# ── LLM prompt templates ─────────────────────────────────────────────────────

# Uses {max_rounds} placeholder — formatted at runtime so the limit matches
# the DiscoveryContext's configured value.
_ASK_SYSTEM_TPL = """\
You are a product discovery facilitator. Your job is to gather enough information to write
a clear Product Requirements Document (PRD) by asking one focused question at a time.

Given the current discovery context, decide:
1. Do we already have enough information? (problem_statement + target_users + at least 3 key_features + tech_stack)
2. If yes → set is_complete=true and leave next_question empty.
3. If no → ask the single most important missing question.

After {max_rounds} Q&A rounds, always set is_complete=true regardless.

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
        "max_qa_rounds": context.max_rounds,
    }
    return f"Current structured context:\n{json.dumps(fields, indent=2)}{qa_text}"


# ── Agent ────────────────────────────────────────────────────────────────────

class DiscoveryAgent(BaseAgent):
    name = "discovery"
    role_description = "Conducts a conversational Q&A session to gather project requirements."
    consumes: list[str] = ["discovery_context"]
    produces: list[str] = ["prd"]

    def __init__(self, llm, *, human_interface: Optional[HumanInterface] = None, **kwargs):
        super().__init__(llm, **kwargs)
        self._human_interface: HumanInterface = human_interface or CliHumanInterface()

    # ── Interactive helpers ───────────────────────────────────────────────────

    def ask_next(self, context: DiscoveryContext) -> dict:
        """Given current context, return the next question or signal completion.

        Returns:
            {"next_question": str, "is_complete": bool, "rationale": str}
        """
        if context.is_complete or len(context.qa_pairs) >= context.max_rounds:
            return {"next_question": "", "is_complete": True, "rationale": "Discovery complete."}

        ask_system = _ASK_SYSTEM_TPL.format(max_rounds=context.max_rounds)
        data: dict = self.system_parsed(ask_system, _context_to_str(context), dict)
        return {
            "next_question": data.get("next_question", ""),
            "is_complete": bool(data.get("is_complete", False)),
            "rationale": data.get("rationale", ""),
        }

    def ingest(self, context: DiscoveryContext, question: str, answer: str) -> DiscoveryContext:
        """Add a Q&A pair and re-extract structured fields from the full conversation.

        Returns an updated DiscoveryContext (max_rounds is preserved).
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
            max_rounds=context.max_rounds,
            is_complete=len(updated_pairs) >= context.max_rounds,
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

    def run_interactive(
        self,
        context: Optional[DiscoveryContext] = None,
        *,
        project_name: str = "",
    ) -> DiscoveryContext:
        """Run the full Q&A loop using the configured HumanInterface.

        Works out-of-the-box in CLI mode (stdin/stdout).  Pass a custom
        *human_interface* at construction time to wire up a platform UI,
        WebSocket, or any other I/O channel.

        Args:
            context: existing DiscoveryContext to resume; creates a fresh one
                     when omitted.
            project_name: seed the project name when starting fresh.

        Returns:
            A completed DiscoveryContext (is_complete=True or max_rounds reached).
        """
        if context is None:
            context = DiscoveryContext(project_name=project_name)

        hi = self._human_interface
        rounds = context.max_rounds
        hi.notify(f"Starting discovery (up to {rounds} question{'s' if rounds != 1 else ''})…")

        while True:
            result = self.ask_next(context)
            if result["is_complete"]:
                hi.notify("Discovery complete — generating PRD…")
                break
            answer = hi.ask(result["next_question"])
            if not answer:
                # Empty answer on EOF or deliberate skip
                hi.notify("Skipping — ending discovery early.")
                context.is_complete = True
                break
            context = self.ingest(context, result["next_question"], answer)

        return context

    # ── Pipeline step ─────────────────────────────────────────────────────────

    def run(self, state: TeamState) -> dict:
        """Pipeline step: discovery_context → prd.

        When *discovery_context* is already present in state (e.g. loaded from
        a completed platform session), it is converted directly to a PRD.

        When *discovery_context* is absent, the agent runs the interactive Q&A
        loop via its HumanInterface before finalizing — this is the recommended
        way to use DiscoveryAgent as the first step of a pipeline:

            team = CustomTeam(steps=[DiscoveryAgent(llm), BackendDevAgent(llm)])
            team.run({})   # discovery fires first, then the rest of the pipeline
        """
        raw = state.get("discovery_context")

        if raw is None:
            context = self.run_interactive(
                project_name=state.get("request", ""),
            )
        elif isinstance(raw, dict):
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
            "discovery_context": context,
            "current_agent": self.name,
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[Discovery] PRD generated from {len(context.qa_pairs)} Q&A round(s): "
                        f"'{prd.title}' — "
                        f"{len(prd.functional_requirements)} requirement(s)."
                    ),
                }
            ],
        }
