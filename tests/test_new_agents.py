"""Unit tests for new agents (FrontendDev, QA, Reviewer, Researcher, Idea, Copywriter, Editor)."""
import json
from unittest.mock import MagicMock

from antcrew.agents.copywriter import CopywriterAgent
from antcrew.agents.editor import EditorAgent
from antcrew.agents.frontend_dev import FrontendDevAgent
from antcrew.agents.idea import IdeaAgent
from antcrew.agents.qa import QAAgent
from antcrew.agents.researcher import ResearcherAgent
from antcrew.agents.reviewer import ReviewerAgent
from antcrew.core.artifacts import (
    CodeArtifact,
    ContentPiece,
    Priority,
    Ticket,
    TicketStatus,
)
from antcrew.core.state import TeamState


def _state(request: str = "Build a login page", **overrides) -> TeamState:
    base: TeamState = {
        "request": request,
        "messages": [{"role": "user", "content": request}],
        "prd": None,
        "tickets": None,
        "code_artifacts": None,
        "test_artifacts": None,
        "review": None,
        "research_document": None,
        "content_piece": None,
        "current_agent": "",
        "errors": [],
        "metadata": {},
    }
    base.update(overrides)
    return base


def _mock_llm(return_value: str) -> MagicMock:
    llm = MagicMock()
    llm.system.return_value = return_value
    return llm


def _open_ticket(**kwargs) -> Ticket:
    defaults = dict(
        id="TICKET-001",
        title="Create login form",
        description="React form with email + password fields",
        priority=Priority.HIGH,
        status=TicketStatus.OPEN,
    )
    defaults.update(kwargs)
    return Ticket(**defaults)


def _code_artifact(**kwargs) -> CodeArtifact:
    defaults = dict(
        ticket_id="TICKET-001",
        file_path="src/login.tsx",
        description="Login component",
        content="export default function Login() { return <form /> }",
        language="typescript",
    )
    defaults.update(kwargs)
    return CodeArtifact(**defaults)


# ---------------------------------------------------------------------------
# FrontendDevAgent
# ---------------------------------------------------------------------------

_FRONTEND_ARTIFACTS = [
    {
        "file_path": "src/components/Login.tsx",
        "description": "Login form component",
        "language": "typescript",
        "content": "export default function Login() { return <form /> }",
    }
]


def test_frontend_dev_generates_artifacts():
    state = _state(tickets=[_open_ticket()])
    agent = FrontendDevAgent(_mock_llm(json.dumps(_FRONTEND_ARTIFACTS)))
    result = agent.run(state)

    assert len(result["code_artifacts"]) == 1
    assert result["code_artifacts"][0].ticket_id == "TICKET-001"
    assert result["current_agent"] == "frontend_dev"


def test_frontend_dev_processes_done_tickets():
    # FrontendDev now processes ALL tickets regardless of status
    ticket = _open_ticket(status=TicketStatus.DONE)
    state = _state(tickets=[ticket])
    agent = FrontendDevAgent(_mock_llm(json.dumps(_FRONTEND_ARTIFACTS)))
    result = agent.run(state)

    assert len(result["code_artifacts"]) == 1


# ---------------------------------------------------------------------------
# QAAgent
# ---------------------------------------------------------------------------

_VALID_TESTS = [
    {
        "ticket_id": "TICKET-001",
        "file_path": "tests/test_login.py",
        "description": "Tests for login endpoint",
        "language": "python",
        "content": "def test_login(): pass",
        "coverage_areas": ["unit"],
    }
]

_BUG_RESULT_CLEAN = json.dumps(
    {"has_critical_bugs": False, "critical_bug_count": 0, "summary": "No bugs found."}
)
_BUG_RESULT_CRITICAL = json.dumps(
    {"has_critical_bugs": True, "critical_bug_count": 1, "summary": "SQL injection risk."}
)


def test_qa_generates_test_artifacts():
    llm = MagicMock()
    # 3 calls: unit tests, e2e tests (login.tsx has JSX → triggers E2E branch), bug detector
    llm.system.side_effect = [json.dumps(_VALID_TESTS), "[]", _BUG_RESULT_CLEAN]
    state = _state(code_artifacts=[_code_artifact()])
    result = QAAgent(llm).run(state)

    assert len(result["test_artifacts"]) == 1
    assert result["test_artifacts"][0].ticket_id == "TICKET-001"
    assert result["metadata"]["has_critical_bugs"] is False
    assert result["metadata"]["no_critical_bugs"] is True


def test_qa_flags_critical_bugs():
    llm = MagicMock()
    llm.system.side_effect = [json.dumps(_VALID_TESTS), "[]", _BUG_RESULT_CRITICAL]
    state = _state(code_artifacts=[_code_artifact()])
    result = QAAgent(llm).run(state)

    assert result["metadata"]["has_critical_bugs"] is True
    assert result["metadata"]["no_critical_bugs"] is False


def test_qa_handles_no_artifacts():
    state = _state()
    result = QAAgent(_mock_llm("")).run(state)

    assert result["metadata"]["has_critical_bugs"] is False


# ---------------------------------------------------------------------------
# ReviewerAgent
# ---------------------------------------------------------------------------

_VALID_REVIEW = {
    "verdict": "approve",
    "summary": "Clean code, no major issues.",
    "findings": [
        {
            "severity": "info",
            "file_path": "src/login.tsx",
            "message": "Consider adding aria-label attributes.",
            "suggestion": "Add aria-label='Login form'",
        }
    ],
}

_REVIEW_REJECT = {
    "verdict": "reject",
    "summary": "SQL injection vulnerability found.",
    "findings": [
        {
            "severity": "critical",
            "file_path": "src/db.py",
            "message": "f-string used in raw SQL query.",
            "suggestion": "Use parameterised queries.",
        }
    ],
}


def test_reviewer_returns_approve_verdict():
    state = _state(code_artifacts=[_code_artifact()])
    result = ReviewerAgent(_mock_llm(json.dumps(_VALID_REVIEW))).run(state)

    assert result["review"].verdict == "approve"
    assert len(result["review"].findings) == 1
    assert result["metadata"]["review_approved"] is True
    assert result["current_agent"] == "reviewer"


def test_reviewer_sets_metadata_on_reject():
    state = _state(code_artifacts=[_code_artifact()])
    result = ReviewerAgent(_mock_llm(json.dumps(_REVIEW_REJECT))).run(state)

    assert result["review"].verdict == "reject"
    assert result["metadata"]["review_needs_changes"] is True
    assert result["metadata"]["review_approved"] is False


# ---------------------------------------------------------------------------
# ResearcherAgent
# ---------------------------------------------------------------------------

_VALID_RESEARCH = {
    "title": "Risks of Agentic AI",
    "topic": "AI safety in autonomous systems",
    "key_findings": ["Agents can take unintended actions"],
    "sections": [
        {"heading": "Background", "content": "Agentic AI refers to..."},
        {"heading": "Key Risks", "content": "Reward hacking, misalignment..."},
    ],
    "sources": ["Russell & Norvig, 2020"],
}


def test_researcher_returns_document():
    state = _state(request="Risks of agentic AI systems")
    result = ResearcherAgent(_mock_llm(json.dumps(_VALID_RESEARCH))).run(state)

    assert result["research_document"].title == "Risks of Agentic AI"
    assert len(result["research_document"].sections) == 2
    assert result["current_agent"] == "researcher"


# ---------------------------------------------------------------------------
# IdeaAgent
# ---------------------------------------------------------------------------

_VALID_BRIEF = {
    "title": "Multi-Agent Frameworks Explained",
    "target_audience": "Software engineers new to AI",
    "tone": "educational",
    "outline": [
        "Introduction: what are multi-agent frameworks?",
        "Core concepts: agents, state, channels",
        "Comparison: LangGraph vs CrewAI vs AutoGen",
        "When to use them",
        "Conclusion",
    ],
}


def test_idea_returns_content_brief():
    state = _state(request="Write a blog post about multi-agent frameworks")
    result = IdeaAgent(_mock_llm(json.dumps(_VALID_BRIEF))).run(state)

    brief = result["content_piece"]
    assert brief.title == "Multi-Agent Frameworks Explained"
    assert brief.tone == "educational"
    assert len(brief.outline) == 5
    assert result["current_agent"] == "idea"


# ---------------------------------------------------------------------------
# CopywriterAgent
# ---------------------------------------------------------------------------

_VALID_BODY = {
    "body": "## Introduction\nMulti-agent frameworks are...\n## Core Concepts\nAgents are...",
    "word_count": 42,
}


def test_copywriter_writes_body():
    brief = ContentPiece(**_VALID_BRIEF)
    state = _state(content_piece=brief)
    result = CopywriterAgent(_mock_llm(json.dumps(_VALID_BODY))).run(state)

    piece = result["content_piece"]
    assert piece.body.startswith("## Introduction")
    assert piece.word_count == 42
    assert result["current_agent"] == "copywriter"


def test_copywriter_errors_without_brief():
    state = _state()
    result = CopywriterAgent(_mock_llm("{}")).run(state)

    assert result.get("errors")


# ---------------------------------------------------------------------------
# EditorAgent
# ---------------------------------------------------------------------------

_VALID_EDIT = {
    "body": "## Introduction\nMulti-agent frameworks simplify orchestration.",
    "word_count": 7,
    "edit_notes": ["Tightened intro", "Removed repetition"],
}


def test_editor_refines_body():
    brief = ContentPiece(**_VALID_BRIEF, body="## Introduction\nOriginal draft.")
    state = _state(content_piece=brief)
    result = EditorAgent(_mock_llm(json.dumps(_VALID_EDIT))).run(state)

    piece = result["content_piece"]
    assert "simplify" in piece.body
    assert piece.word_count == 7
    assert result["current_agent"] == "editor"


def test_editor_errors_without_body():
    brief = ContentPiece(**_VALID_BRIEF)  # body="" by default
    state = _state(content_piece=brief)
    result = EditorAgent(_mock_llm("{}")).run(state)

    assert result.get("errors")
