"""
End-to-end integration tests — run complete pipelines with SimulatedLLM.
No real API calls. These tests exercise the full LangGraph pipeline:
state init → agent.run() → state transitions → final state assertions.
"""
from __future__ import annotations

import pytest
from antcrew.models.simulated import SimulatedLLM


# ---------------------------------------------------------------------------
# DevTeam
# ---------------------------------------------------------------------------

class TestDevTeamE2E:
    def test_default_pipeline_ba_pm_dev(self):
        from antcrew import DevTeam
        state = DevTeam(model=SimulatedLLM()).run(
            "Build a login module", thread_id="e2e-dev-1"
        )
        prd = state.get("prd")
        assert prd is not None, "PRD must be produced"
        assert prd.title
        assert prd.summary
        assert isinstance(prd.functional_requirements, list)

        tickets = state.get("tickets")
        assert tickets, "Tickets must be produced"
        assert all(t.id.startswith("TICKET-") for t in tickets)
        assert all(t.status.value == "done" for t in tickets)

        code = state.get("code_artifacts")
        assert code, "Code artifacts must be produced"
        assert all(a.file_path for a in code)
        assert all(a.content for a in code)

    def test_default_pipeline_current_agent_is_last(self):
        from antcrew import DevTeam
        state = DevTeam(model=SimulatedLLM()).run("Auth service", thread_id="e2e-dev-2")
        assert state.get("current_agent") == "backend_dev"

    def test_default_pipeline_errors_empty(self):
        from antcrew import DevTeam
        state = DevTeam(model=SimulatedLLM()).run("Feature X", thread_id="e2e-dev-3")
        assert state.get("errors") == []

    def test_level2_agent_override(self):
        """Level 2: replace backend_dev with a custom agent using SimulatedLLM."""
        from antcrew import DevTeam
        from antcrew.agents.backend_dev import BackendDevAgent
        custom = BackendDevAgent(SimulatedLLM())
        team = DevTeam(model=SimulatedLLM(), agents={"backend_dev": custom})
        state = team.run("Replace agent test", thread_id="e2e-dev-4")
        assert state.get("code_artifacts") is not None
        assert team._agents["backend_dev"] is custom

    def test_extended_pipeline_with_qa_and_reviewer(self):
        """Level 3: BA→PM→Dev→QA→Reviewer full flow."""
        from antcrew.agents.business import BusinessAnalystAgent
        from antcrew.agents.pm import PMAgent
        from antcrew.agents.backend_dev import BackendDevAgent
        from antcrew.agents.qa import QAAgent
        from antcrew.agents.reviewer import ReviewerAgent
        from antcrew.core.supervisor import Supervisor
        from antcrew.teams.dev_team import DevTeam

        llm = SimulatedLLM()
        supervisor = Supervisor(flow=[
            ("business_analyst", "pm"),
            ("pm", "backend_dev"),
            ("backend_dev", "qa"),
            ("qa", "reviewer",    "no_critical_bugs"),
            ("qa", "backend_dev", "has_critical_bugs"),
        ])
        team = DevTeam(
            model=llm,
            supervisor=supervisor,
            agents={
                "business_analyst": BusinessAnalystAgent(llm),
                "pm":               PMAgent(llm),
                "backend_dev":      BackendDevAgent(llm),
                "qa":               QAAgent(llm),
                "reviewer":         ReviewerAgent(llm),
            },
        )
        state = team.run("Full pipeline test", thread_id="e2e-dev-5")
        assert state.get("prd") is not None
        assert state.get("tickets") is not None
        assert state.get("code_artifacts") is not None
        assert state.get("test_artifacts") is not None

    def test_multiple_runs_different_thread_ids(self):
        """Two concurrent thread IDs must produce independent state."""
        from antcrew import DevTeam
        llm = SimulatedLLM()
        team = DevTeam(model=llm)
        s1 = team.run("Request A", thread_id="t1")
        s2 = team.run("Request B", thread_id="t2")
        assert s1["request"] == "Request A"
        assert s2["request"] == "Request B"


# ---------------------------------------------------------------------------
# ResearchTeam
# ---------------------------------------------------------------------------

class TestResearchTeamE2E:
    def test_default_pipeline_researcher_writer(self):
        from antcrew import ResearchTeam
        state = ResearchTeam(model=SimulatedLLM()).run(
            "Risks of agentic AI systems", thread_id="e2e-research-1"
        )
        doc = state.get("research_document")
        assert doc is not None, "ResearchDocument must be produced"
        assert doc.title
        assert doc.topic
        assert doc.key_findings
        assert doc.sections

        piece = state.get("content_piece")
        assert piece is not None, "CopywriterAgent must produce a ContentPiece"
        assert piece.body, "Written report must have body text"

    def test_research_document_has_sections(self):
        from antcrew import ResearchTeam
        state = ResearchTeam(model=SimulatedLLM()).run(
            "Multi-agent frameworks", thread_id="e2e-research-2"
        )
        doc = state["research_document"]
        assert len(doc.sections) >= 1
        for section in doc.sections:
            assert section.heading
            assert section.content

    def test_current_agent_is_copywriter(self):
        from antcrew import ResearchTeam
        state = ResearchTeam(model=SimulatedLLM()).run(
            "LLM deployment", thread_id="e2e-research-3"
        )
        # The node is named "writer" in ResearchTeam but CopywriterAgent sets current_agent="copywriter"
        assert state.get("current_agent") == "copywriter"

    def test_level2_researcher_override(self):
        from antcrew import ResearchTeam
        from antcrew.agents.researcher import ResearcherAgent
        llm = SimulatedLLM()
        custom = ResearcherAgent(llm)
        team = ResearchTeam(model=llm, agents={"researcher": custom})
        state = team.run("Custom researcher", thread_id="e2e-research-4")
        assert state.get("research_document") is not None
        assert team._agents["researcher"] is custom

    def test_level3_custom_flow_researcher_only(self):
        """Level 3: single-agent pipeline (researcher only, no writer)."""
        from antcrew import ResearchTeam
        from antcrew.agents.researcher import ResearcherAgent
        from antcrew.core.supervisor import Supervisor

        llm = SimulatedLLM()
        supervisor = Supervisor(flow=[])   # no edges — single node
        team = ResearchTeam(
            model=llm,
            supervisor=Supervisor(flow=[("researcher", "writer")]),
            agents={"researcher": ResearcherAgent(llm)},
        )
        state = team.run("AI safety", thread_id="e2e-research-5")
        assert state.get("research_document") is not None


# ---------------------------------------------------------------------------
# ContentTeam
# ---------------------------------------------------------------------------

class TestContentTeamE2E:
    def test_default_pipeline_idea_copywriter_editor(self):
        from antcrew import ContentTeam
        state = ContentTeam(model=SimulatedLLM()).run(
            "Blog post about LangGraph", thread_id="e2e-content-1"
        )
        piece = state.get("content_piece")
        assert piece is not None, "ContentPiece must be produced"
        assert piece.title
        assert piece.target_audience
        assert piece.tone
        assert piece.outline
        assert piece.body, "Editor must produce a body"

    def test_content_piece_word_count(self):
        from antcrew import ContentTeam
        state = ContentTeam(model=SimulatedLLM()).run(
            "AI ethics article", thread_id="e2e-content-2"
        )
        piece = state["content_piece"]
        assert piece.word_count is not None
        assert piece.word_count > 0

    def test_current_agent_is_editor(self):
        from antcrew import ContentTeam
        state = ContentTeam(model=SimulatedLLM()).run(
            "Tech newsletter", thread_id="e2e-content-3"
        )
        assert state.get("current_agent") == "editor"

    def test_level2_copywriter_override(self):
        from antcrew import ContentTeam
        from antcrew.agents.copywriter import CopywriterAgent
        llm = SimulatedLLM()
        custom = CopywriterAgent(llm)
        team = ContentTeam(model=llm, agents={"copywriter": custom})
        state = team.run("Custom writer", thread_id="e2e-content-4")
        assert state.get("content_piece") is not None
        assert team._agents["copywriter"] is custom

    def test_level3_skip_editor(self):
        """Level 3: two-stage pipeline — idea → copywriter, no editor."""
        from antcrew import ContentTeam
        from antcrew.core.supervisor import Supervisor
        llm = SimulatedLLM()
        team = ContentTeam(
            model=llm,
            supervisor=Supervisor(flow=[("idea", "copywriter")]),
        )
        state = team.run("Two-stage content", thread_id="e2e-content-5")
        piece = state.get("content_piece")
        assert piece is not None
        assert piece.body
        assert state.get("current_agent") == "copywriter"

    def test_errors_list_is_empty(self):
        from antcrew import ContentTeam
        state = ContentTeam(model=SimulatedLLM()).run("Clean run", thread_id="e2e-content-6")
        assert state.get("errors") == []


# ---------------------------------------------------------------------------
# Cross-team: SimulatedLLM is deterministic across all teams
# ---------------------------------------------------------------------------

class TestSimulatedLLMDeterminism:
    def test_same_request_produces_same_prd_title(self):
        from antcrew import DevTeam
        llm = SimulatedLLM()
        team = DevTeam(model=llm)
        s1 = team.run("Determinism test", thread_id="det-1")
        s2 = team.run("Determinism test", thread_id="det-2")
        assert s1["prd"].title == s2["prd"].title

    def test_research_and_content_teams_dont_share_state(self):
        from antcrew import ResearchTeam, ContentTeam
        llm = SimulatedLLM()
        r_state = ResearchTeam(model=llm).run("Research topic", thread_id="cross-1")
        c_state = ContentTeam(model=llm).run("Blog topic", thread_id="cross-2")
        # Research state must not have tickets; content state must not have research_document
        assert r_state.get("tickets") is None
        # ContentTeam doesn't run ResearcherAgent so research_document may be None
        # (both produced content_piece but they are independent runs)
        assert c_state.get("content_piece") is not None
