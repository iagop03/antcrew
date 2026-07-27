"""
Tests for:
- InteractiveMixin is present on all three teams
- ResearchTeam.run_interactive() and ContentTeam.run_interactive() exist and work
- SimulatedLLM._pick_fixture covers all refine() prompts
"""
from __future__ import annotations

import json

from antcrew_engine.models.simulated import SimulatedLLM, _pick_fixture

from antcrew.teams.base import InteractiveMixin

# ---------------------------------------------------------------------------
# InteractiveMixin presence
# ---------------------------------------------------------------------------

class TestMixinInheritance:
    def test_dev_team_has_run_interactive(self):
        from antcrew.teams.dev_team import DevTeam
        assert issubclass(DevTeam, InteractiveMixin)
        assert callable(DevTeam.run_interactive)

    def test_research_team_has_run_interactive(self):
        from antcrew.teams.research_team import ResearchTeam
        assert issubclass(ResearchTeam, InteractiveMixin)
        assert callable(ResearchTeam.run_interactive)

    def test_content_team_has_run_interactive(self):
        from antcrew.teams.content_team import ContentTeam
        assert issubclass(ContentTeam, InteractiveMixin)
        assert callable(ContentTeam.run_interactive)

    def test_all_teams_have_apply_edit(self):
        from antcrew.teams.content_team import ContentTeam
        from antcrew.teams.dev_team import DevTeam
        from antcrew.teams.research_team import ResearchTeam
        for cls in (DevTeam, ResearchTeam, ContentTeam):
            assert callable(getattr(cls, "_apply_edit", None)), f"{cls.__name__}._apply_edit missing"


# ---------------------------------------------------------------------------
# ResearchTeam and ContentTeam instantiation
# ---------------------------------------------------------------------------

class TestTeamInstantiation:
    def test_research_team_default_agents(self):
        from antcrew.models.simulated import SimulatedLLM
        from antcrew.teams.research_team import ResearchTeam
        team = ResearchTeam(model=SimulatedLLM())
        assert "researcher" in team._agents
        assert "writer" in team._agents

    def test_content_team_default_agents(self):
        from antcrew.models.simulated import SimulatedLLM
        from antcrew.teams.content_team import ContentTeam
        team = ContentTeam(model=SimulatedLLM())
        assert "idea" in team._agents
        assert "copywriter" in team._agents
        assert "editor" in team._agents

    def test_research_team_agent_override(self):
        from antcrew.agents.researcher import ResearcherAgent
        from antcrew.models.simulated import SimulatedLLM
        from antcrew.teams.research_team import ResearchTeam
        llm = SimulatedLLM()
        custom_researcher = ResearcherAgent(llm)
        team = ResearchTeam(model=llm, agents={"researcher": custom_researcher})
        assert team._agents["researcher"] is custom_researcher

    def test_content_team_agent_override(self):
        from antcrew.agents.copywriter import CopywriterAgent
        from antcrew.models.simulated import SimulatedLLM
        from antcrew.teams.content_team import ContentTeam
        llm = SimulatedLLM()
        custom = CopywriterAgent(llm)
        team = ContentTeam(model=llm, agents={"copywriter": custom})
        assert team._agents["copywriter"] is custom


# ---------------------------------------------------------------------------
# SimulatedLLM refine fixture detection
# ---------------------------------------------------------------------------

class TestSimulatedLLMRefineFictures:
    """Verify that all 10 refine() system prompts return parseable fixture JSON."""

    def _matches(self, phrase: str) -> dict | list:
        raw = _pick_fixture(phrase)
        return json.loads(raw)

    def test_ba_refine_returns_prd(self):
        result = self._matches(
            "You are a senior Business Analyst. "
            "The reviewer provided feedback on the PRD you wrote."
        )
        assert "title" in result
        assert "functional_requirements" in result

    def test_pm_refine_returns_tickets(self):
        result = self._matches(
            "You are a Product Manager. "
            "The reviewer provided feedback on the tickets you created."
        )
        assert isinstance(result, list)
        assert "title" in result[0]

    def test_backend_dev_refine_returns_code(self):
        result = self._matches(
            "You are a Senior Backend Developer. "
            "The reviewer provided feedback on your code."
        )
        assert isinstance(result, list)
        assert "file_path" in result[0]

    def test_qa_refine_returns_tests(self):
        result = self._matches(
            "You are a QA Engineer. "
            "The reviewer provided feedback on your test suite."
        )
        assert isinstance(result, list)
        assert "ticket_id" in result[0]

    def test_reviewer_refine_returns_review(self):
        result = self._matches(
            "You are a Senior Software Engineer. "
            "The reviewer of the review provided feedback on your assessment."
        )
        assert "verdict" in result
        assert "findings" in result

    def test_researcher_refine_returns_research_doc(self):
        result = self._matches(
            "You are a Research Analyst. "
            "The reviewer provided feedback on your research document."
        )
        assert "title" in result
        assert "sections" in result

    def test_idea_refine_returns_brief(self):
        result = self._matches(
            "You are a Creative Strategist. "
            "The reviewer provided feedback on your content brief."
        )
        assert "outline" in result
        assert "tone" in result

    def test_copywriter_refine_returns_body(self):
        result = self._matches(
            "You are a professional Copywriter. "
            "The reviewer provided feedback on your draft."
        )
        assert "body" in result
        assert "word_count" in result

    def test_editor_refine_returns_edit(self):
        result = self._matches(
            "You are a professional Editor. "
            "The reviewer provided feedback on your edited content."
        )
        assert "body" in result
        assert "edit_notes" in result

    def test_creative_strategist_keyword(self):
        """IdeaAgent._REFINE_SYSTEM starts with 'You are a Creative Strategist'."""
        result = self._matches("You are a Creative Strategist. Revise the brief.")
        assert "outline" in result


# ---------------------------------------------------------------------------
# SimulatedLLM.refine() end-to-end: agent.refine() with SimulatedLLM
# ---------------------------------------------------------------------------

class TestSimulatedLLMRefineE2E:
    """agent.refine() called with SimulatedLLM must not raise and return valid state."""

    def _ba_refine(self):
        from antcrew.agents.business import BusinessAnalystAgent
        from antcrew.core.artifacts import PRD
        agent = BusinessAnalystAgent(SimulatedLLM())
        original = PRD(
            title="Orig", summary="s", goals=[], out_of_scope=[],
            functional_requirements=[], non_functional_requirements=[], open_questions=[],
        )
        return agent.refine({}, original, "Add more goals")

    def test_ba_refine_returns_prd(self):
        result = self._ba_refine()
        assert "prd" in result
        from antcrew.core.artifacts import PRD
        assert isinstance(result["prd"], PRD)

    def test_researcher_refine_returns_doc(self):
        from antcrew.agents.researcher import ResearcherAgent
        from antcrew.core.artifacts import ResearchDocument, ResearchSection
        agent = ResearcherAgent(SimulatedLLM())
        original = ResearchDocument(
            title="Orig", topic="AI",
            key_findings=["f1"],
            sections=[ResearchSection(heading="H", content="c")],
            sources=[],
        )
        result = agent.refine({}, original, "Expand the analysis section")
        assert "research_document" in result
        assert isinstance(result["research_document"], ResearchDocument)

    def test_reviewer_refine_returns_review(self):
        from antcrew.agents.reviewer import ReviewerAgent
        from antcrew.core.artifacts import CodeReview, ReviewFinding
        agent = ReviewerAgent(SimulatedLLM())
        original = CodeReview(
            verdict="request_changes",
            summary="Needs work",
            findings=[
                ReviewFinding(
                    severity="warning", file_path="src/main.py",
                    message="Error", suggestion="Fix it", line=None,
                )
            ],
        )
        result = agent.refine({}, original, "Reconsider — all issues fixed")
        assert "review" in result
        assert "metadata" in result

    def test_idea_refine_returns_content_piece(self):
        from antcrew.agents.idea import IdeaAgent
        from antcrew.core.artifacts import ContentPiece
        agent = IdeaAgent(SimulatedLLM())
        original = ContentPiece(
            title="Draft Brief",
            target_audience="developers",
            tone="professional",
            outline=["Intro", "Section A"],
        )
        result = agent.refine({}, original, "Target a wider audience")
        assert "content_piece" in result
        assert isinstance(result["content_piece"], ContentPiece)
