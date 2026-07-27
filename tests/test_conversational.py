"""Tests for conversational mode: agent.refine(), ConsoleChannel feedback, run_interactive loop."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from antcrew.core.agent import BaseAgent, _strip_fences
from antcrew.core.artifacts import (
    PRD,
    CodeReview,
    ContentPiece,
    Priority,
    ResearchDocument,
    ResearchSection,
    ReviewFinding,
    Ticket,
    TicketStatus,
)
from antcrew.integrations.console import ConsoleChannel, _prompt_decision

# ---------------------------------------------------------------------------
# _strip_fences
# ---------------------------------------------------------------------------

class TestStripFences:
    def test_plain_text_unchanged(self):
        assert _strip_fences('{"key": "val"}') == '{"key": "val"}'

    def test_json_fence_stripped(self):
        raw = "```json\n{\"x\": 1}\n```"
        assert _strip_fences(raw) == '{"x": 1}'

    def test_generic_fence_stripped(self):
        raw = "```\n{\"y\": 2}\n```"
        result = _strip_fences(raw)
        assert "{" in result

    def test_leading_whitespace_stripped(self):
        raw = "  \n```json\n{\"z\": 3}\n```"
        result = _strip_fences(raw)
        assert result == '{"z": 3}'


# ---------------------------------------------------------------------------
# conversational = True on all concrete agents
# ---------------------------------------------------------------------------

class TestConversationalFlag:
    def test_all_agents_are_conversational(self):
        from antcrew.agents.backend_dev import BackendDevAgent
        from antcrew.agents.business import BusinessAnalystAgent
        from antcrew.agents.copywriter import CopywriterAgent
        from antcrew.agents.editor import EditorAgent
        from antcrew.agents.frontend_dev import FrontendDevAgent
        from antcrew.agents.idea import IdeaAgent
        from antcrew.agents.pm import PMAgent
        from antcrew.agents.qa import QAAgent
        from antcrew.agents.researcher import ResearcherAgent
        from antcrew.agents.reviewer import ReviewerAgent

        for cls in [
            BusinessAnalystAgent, PMAgent, BackendDevAgent, FrontendDevAgent,
            QAAgent, ReviewerAgent, ResearcherAgent, IdeaAgent,
            CopywriterAgent, EditorAgent,
        ]:
            assert cls.conversational is True, f"{cls.__name__}.conversational must be True"

    def test_base_agent_conversational_defaults_false(self):
        class MinimalAgent(BaseAgent):
            name = "minimal"
            def run(self, state):
                return {}
        assert MinimalAgent.conversational is False


# ---------------------------------------------------------------------------
# BusinessAnalystAgent.refine()
# ---------------------------------------------------------------------------

def _prd(**kwargs):
    defaults = dict(
        title="T", summary="S", goals=[], out_of_scope=[],
        functional_requirements=[], non_functional_requirements=[], open_questions=[],
    )
    defaults.update(kwargs)
    return PRD(**defaults)


class TestBusinessAnalystRefine:
    def _agent(self, response: dict):
        mock_llm = MagicMock()
        mock_llm.system.return_value = json.dumps(response)
        from antcrew.agents.business import BusinessAnalystAgent
        return BusinessAnalystAgent(mock_llm)

    def test_refine_returns_prd(self):
        updated = dict(
            title="Revised PRD", summary="Better summary", goals=["g1"],
            out_of_scope=[], functional_requirements=["r1"],
            non_functional_requirements=[], open_questions=[],
        )
        agent = self._agent(updated)
        result = agent.refine({}, _prd(title="Old PRD"), "Add more goals")

        assert "prd" in result
        assert result["prd"].title == "Revised PRD"
        assert "tickets" not in result  # only partial state

    def test_feedback_is_in_prompt(self):
        mock_llm = MagicMock()
        mock_llm.system.return_value = json.dumps(
            dict(title="X", summary="s", goals=[], out_of_scope=[],
                 functional_requirements=[], non_functional_requirements=[], open_questions=[])
        )
        from antcrew.agents.business import BusinessAnalystAgent
        agent = BusinessAnalystAgent(mock_llm)
        agent.refine({}, _prd(), "Please add acceptance criteria")

        prompt = mock_llm.system.call_args[0][0]
        assert "Please add acceptance criteria" in prompt

    def test_artifact_json_is_in_prompt(self):
        mock_llm = MagicMock()
        mock_llm.system.return_value = json.dumps(
            dict(title="X", summary="s", goals=[], out_of_scope=[],
                 functional_requirements=[], non_functional_requirements=[], open_questions=[])
        )
        from antcrew.agents.business import BusinessAnalystAgent
        agent = BusinessAnalystAgent(mock_llm)
        original = _prd(title="My PRD")
        agent.refine({}, original, "feedback")

        prompt = mock_llm.system.call_args[0][0]
        assert "My PRD" in prompt


# ---------------------------------------------------------------------------
# PMAgent.refine()
# ---------------------------------------------------------------------------

def _ticket(id="TICKET-001", title="T", **kwargs):
    defaults = dict(
        description="desc", priority=Priority.MEDIUM,
        acceptance_criteria=["ac1"], dependencies=[], status=TicketStatus.OPEN,
    )
    defaults.update(kwargs)
    return Ticket(id=id, title=title, **defaults)


class TestPMAgentRefine:
    def _agent(self, response):
        mock_llm = MagicMock()
        mock_llm.system.return_value = json.dumps(response)
        from antcrew.agents.pm import PMAgent
        return PMAgent(mock_llm)

    def test_refine_returns_tickets(self):
        agent = self._agent([
            {"id": "TICKET-001", "title": "Auth login", "description": "d",
             "priority": "high", "acceptance_criteria": ["ac"], "dependencies": [], "status": "open"},
        ])
        result = agent.refine({}, [_ticket()], "Make title clearer")
        assert "tickets" in result
        assert result["tickets"][0].title == "Auth login"
        assert result["tickets"][0].id == "TICKET-001"

    def test_refine_preserves_ticket_ids(self):
        agent = self._agent([
            {"id": "TICKET-002", "title": "Register", "description": "d",
             "priority": "medium", "acceptance_criteria": [], "dependencies": [], "status": "open"},
        ])
        result = agent.refine({}, [_ticket(id="TICKET-002")], "feedback")
        assert result["tickets"][0].id == "TICKET-002"

    def test_fallback_id_when_missing(self):
        agent = self._agent([
            {"title": "No ID ticket", "description": "d",
             "priority": "low", "acceptance_criteria": [], "dependencies": [], "status": "open"},
        ])
        result = agent.refine({}, [_ticket()], "feedback")
        assert result["tickets"][0].id == "TICKET-001"


# ---------------------------------------------------------------------------
# ResearcherAgent.refine()
# ---------------------------------------------------------------------------

class TestResearcherRefine:
    def _agent(self, response):
        mock_llm = MagicMock()
        mock_llm.system.return_value = json.dumps(response)
        from antcrew.agents.researcher import ResearcherAgent
        return ResearcherAgent(mock_llm)

    def _doc(self):
        return ResearchDocument(
            title="Original", topic="AI",
            key_findings=["f1"],
            sections=[ResearchSection(heading="Intro", content="text")],
            sources=["src1"],
        )

    def test_refine_returns_research_document(self):
        agent = self._agent({
            "title": "Revised Doc", "topic": "AI",
            "key_findings": ["f1", "f2"],
            "sections": [{"heading": "Background", "content": "bg text"}],
            "sources": ["src1", "src2"],
        })
        result = agent.refine({}, self._doc(), "Add more key findings")
        assert "research_document" in result
        assert result["research_document"].title == "Revised Doc"
        assert len(result["research_document"].key_findings) == 2

    def test_refine_falls_back_to_original_on_missing_keys(self):
        agent = self._agent({
            "sections": [{"heading": "H", "content": "c"}],
        })
        doc = self._doc()
        result = agent.refine({}, doc, "feedback")
        assert result["research_document"].title == doc.title
        assert result["research_document"].topic == doc.topic


# ---------------------------------------------------------------------------
# ReviewerAgent.refine()
# ---------------------------------------------------------------------------

class TestReviewerRefine:
    def _agent(self, response):
        mock_llm = MagicMock()
        mock_llm.system.return_value = json.dumps(response)
        from antcrew.agents.reviewer import ReviewerAgent
        return ReviewerAgent(mock_llm)

    def _review(self):
        return CodeReview(
            verdict="request_changes",
            summary="Needs work",
            findings=[
                ReviewFinding(
                    severity="warning", file_path="src/main.py",
                    message="Missing error handling", suggestion="Add try/except", line=None,
                )
            ],
        )

    def test_refine_returns_review_with_metadata(self):
        agent = self._agent({
            "verdict": "approve",
            "summary": "Looks good now",
            "findings": [
                {"severity": "info", "file_path": "src/main.py",
                 "message": "Good pattern", "suggestion": "", "line": None}
            ],
        })
        result = agent.refine({}, self._review(), "Please re-evaluate — errors were fixed")
        assert "review" in result
        assert "metadata" in result
        assert result["review"].verdict == "approve"
        assert result["metadata"]["review_approved"] is True
        assert result["metadata"]["review_needs_changes"] is False

    def test_refine_verdict_reject_sets_needs_changes(self):
        agent = self._agent({
            "verdict": "reject",
            "summary": "Security flaw",
            "findings": [],
        })
        result = agent.refine({}, self._review(), "SQL injection found")
        assert result["metadata"]["review_approved"] is False
        assert result["metadata"]["review_needs_changes"] is True


# ---------------------------------------------------------------------------
# ContentPiece agents: IdeaAgent, CopywriterAgent, EditorAgent
# ---------------------------------------------------------------------------

class TestContentAgentsRefine:
    def _llm(self, response):
        m = MagicMock()
        m.system.return_value = json.dumps(response)
        return m

    def _brief(self):
        return ContentPiece(
            title="10 Tips for Python",
            target_audience="junior devs",
            tone="educational",
            outline=["Intro", "Tip 1", "Tip 2"],
        )

    def test_idea_refine_returns_content_piece(self):
        from antcrew.agents.idea import IdeaAgent
        agent = IdeaAgent(self._llm({
            "title": "Revised Title",
            "target_audience": "senior devs",
            "tone": "professional",
            "outline": ["Intro", "Section A", "Section B", "Conclusion"],
        }))
        result = agent.refine({}, self._brief(), "Target senior devs instead")
        assert result["content_piece"].title == "Revised Title"
        assert result["content_piece"].target_audience == "senior devs"

    def test_copywriter_refine_updates_body(self):
        from antcrew.agents.copywriter import CopywriterAgent
        piece = self._brief().model_copy(update={"body": "Original body", "word_count": 100})
        agent = CopywriterAgent(self._llm({"body": "Updated body", "word_count": 150}))
        result = agent.refine({}, piece, "Make it longer")
        assert result["content_piece"].body == "Updated body"
        assert result["content_piece"].word_count == 150

    def test_editor_refine_updates_body(self):
        from antcrew.agents.editor import EditorAgent
        piece = self._brief().model_copy(update={"body": "Draft body", "word_count": 80})
        agent = EditorAgent(self._llm({"body": "Polished body", "word_count": 90, "edit_notes": []}))
        result = agent.refine({}, piece, "Fix grammar in intro")
        assert result["content_piece"].body == "Polished body"


# ---------------------------------------------------------------------------
# ConsoleChannel.send_for_review(): feedback for free-text
# ---------------------------------------------------------------------------

class TestConsoleChannelFeedback:
    def _artifact(self):
        return _prd(title="Test PRD")

    @pytest.mark.asyncio
    async def test_free_text_returns_feedback_decision(self):
        channel = ConsoleChannel()
        with patch("antcrew.integrations.console._display_artifact"), \
             patch("antcrew.integrations.console.Prompt.ask", return_value="Make goals more specific"):
            result = await channel.send_for_review(self._artifact(), "business_analyst", "s1")
        assert result["decision"] == "feedback"
        assert result["feedback"] == "Make goals more specific"
        assert result["edited"] is None

    @pytest.mark.asyncio
    async def test_a_returns_approve(self):
        channel = ConsoleChannel()
        with patch("antcrew.integrations.console._display_artifact"), \
             patch("antcrew.integrations.console.Prompt.ask", return_value="a"):
            result = await channel.send_for_review(self._artifact(), "business_analyst", "s1")
        assert result["decision"] == "approve"
        assert result["feedback"] is None

    @pytest.mark.asyncio
    async def test_r_returns_reject(self):
        channel = ConsoleChannel()
        with patch("antcrew.integrations.console._display_artifact"), \
             patch("antcrew.integrations.console.Prompt.ask", return_value="r"):
            result = await channel.send_for_review(self._artifact(), "business_analyst", "s1")
        assert result["decision"] == "reject"

    @pytest.mark.asyncio
    async def test_approve_word_returns_approve(self):
        channel = ConsoleChannel()
        with patch("antcrew.integrations.console._display_artifact"), \
             patch("antcrew.integrations.console.Prompt.ask", return_value="approve"):
            result = await channel.send_for_review(self._artifact(), "qa", "s1")
        assert result["decision"] == "approve"

    @pytest.mark.asyncio
    async def test_empty_input_defaults_to_approve(self):
        channel = ConsoleChannel()
        with patch("antcrew.integrations.console._display_artifact"), \
             patch("antcrew.integrations.console.Prompt.ask", return_value=""):
            result = await channel.send_for_review(self._artifact(), "pm", "s1")
        assert result["decision"] == "approve"


# ---------------------------------------------------------------------------
# _prompt_decision
# ---------------------------------------------------------------------------

class TestPromptDecision:
    def test_a_maps_to_approve(self):
        with patch("antcrew.integrations.console.Prompt.ask", return_value="a"):
            assert _prompt_decision("agent", ["approve", "edit", "reject"]) == "approve"

    def test_r_maps_to_reject(self):
        with patch("antcrew.integrations.console.Prompt.ask", return_value="r"):
            assert _prompt_decision("agent", ["approve", "edit", "reject"]) == "reject"

    def test_e_maps_to_edit(self):
        with patch("antcrew.integrations.console.Prompt.ask", return_value="e"):
            assert _prompt_decision("agent", ["approve", "edit", "reject"]) == "edit"

    def test_free_text_returned_as_is(self):
        with patch("antcrew.integrations.console.Prompt.ask", return_value="Add tests for edge cases"):
            result = _prompt_decision("qa", ["approve", "edit", "reject"])
        assert result == "Add tests for edge cases"

    def test_empty_defaults_to_approve(self):
        with patch("antcrew.integrations.console.Prompt.ask", return_value=""):
            assert _prompt_decision("agent", ["approve"]) == "approve"


# ---------------------------------------------------------------------------
# BaseAgent.refine() default is a no-op
# ---------------------------------------------------------------------------

def test_base_agent_refine_noop():
    class MinimalAgent(BaseAgent):
        name = "minimal"
        def run(self, state):
            return {}

    agent = MinimalAgent(MagicMock())
    result = agent.refine({}, None, "some feedback")
    assert result == {}
