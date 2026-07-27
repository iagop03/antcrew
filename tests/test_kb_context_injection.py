"""Verify that every pipeline agent injects _kb_context into its LLM system prompt.

Each test:
 1. Builds a state with _kb_context set to a sentinel string.
 2. Runs the agent with a mock LLM.
 3. Asserts the sentinel appears in the system prompt that was passed to the LLM.

This ensures that a refactor of any agent's run() cannot silently drop KB context.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from antcrew.agents.business import BusinessAnalystAgent
from antcrew.agents.devops import DevOpsAgent
from antcrew.agents.doc_writer import DocWriterAgent
from antcrew.agents.pm import PMAgent
from antcrew.agents.qa import QAAgent
from antcrew.agents.reviewer import ReviewerAgent
from antcrew.core.artifacts import (
    PRD,
    CodeArtifact,
    Priority,
    Ticket,
    TicketStatus,
)
from antcrew.core.state import TeamState

_KB_SENTINEL = "## Project Knowledge Base\nGET /health  [health_check]\n\n"

_VALID_PRD = {
    "title": "Auth Module",
    "summary": "JWT auth.",
    "goals": ["Secure login"],
    "out_of_scope": [],
    "functional_requirements": ["POST /login"],
    "non_functional_requirements": [],
    "open_questions": [],
}

_VALID_TICKETS = [
    {
        "title": "Login endpoint",
        "description": "POST /login",
        "priority": "high",
        "acceptance_criteria": ["Returns 200"],
        "dependencies": [],
    }
]

_VALID_REVIEW = {
    "verdict": "approve",
    "summary": "LGTM",
    "findings": [],
}

_VALID_DOC_ARTIFACTS = [
    {
        "file_path": "README.md",
        "title": "Project README",
        "doc_type": "readme",
        "format": "markdown",
        "content": "# Project",
    }
]

_VALID_DEVOPS_ARTIFACTS = [
    {
        "file_path": "Dockerfile",
        "description": "Production image",
        "language": "dockerfile",
        "content": "FROM python:3.12-slim\n",
    }
]

_VALID_TESTS = [
    {
        "ticket_id": "TICKET-001",
        "file_path": "tests/test_auth.py",
        "description": "Auth tests",
        "language": "python",
        "content": "def test_login(): pass",
        "coverage_areas": ["unit"],
    }
]

_BUG_RESULT_CLEAN = json.dumps(
    {"has_critical_bugs": False, "critical_bug_count": 0, "summary": "Clean."}
)


def _mock_llm(*return_values: str) -> MagicMock:
    llm = MagicMock()
    if len(return_values) == 1:
        llm.system.return_value = return_values[0]
    else:
        llm.system.side_effect = list(return_values)
    return llm


def _prd() -> PRD:
    return PRD(**_VALID_PRD)


def _ticket() -> Ticket:
    return Ticket(
        id="TICKET-001",
        title="Login endpoint",
        description="POST /login",
        priority=Priority.HIGH,
        status=TicketStatus.OPEN,
    )


def _code_artifact(file_path: str = "src/auth.py") -> CodeArtifact:
    return CodeArtifact(
        ticket_id="TICKET-001",
        file_path=file_path,
        description="Auth module",
        content="def login(): pass",
        language="python",
    )


def _base_state(**overrides) -> TeamState:
    state: TeamState = {
        "request": "Build JWT auth",
        "messages": [{"role": "user", "content": "Build JWT auth"}],
        "prd": None,
        "tickets": None,
        "code_artifacts": None,
        "test_artifacts": None,
        "test_results": None,
        "review": None,
        "devops_artifacts": None,
        "doc_artifacts": None,
        "research_document": None,
        "content_piece": None,
        "current_agent": "",
        "errors": [],
        "metadata": {},
        "_kb_context": _KB_SENTINEL,
    }
    state.update(overrides)
    return state


def _system_calls(llm: MagicMock) -> list[str]:
    """Return list of system prompt strings from all llm.system() calls."""
    return [c.args[0] for c in llm.system.call_args_list]


# ---------------------------------------------------------------------------
# BusinessAnalystAgent
# ---------------------------------------------------------------------------

def test_business_analyst_injects_kb_context():
    llm = _mock_llm(json.dumps(_VALID_PRD))
    BusinessAnalystAgent(llm).run(_base_state())

    prompts = _system_calls(llm)
    assert any(_KB_SENTINEL in p for p in prompts), (
        "BusinessAnalystAgent did not inject _kb_context into system prompt"
    )


# ---------------------------------------------------------------------------
# PMAgent
# ---------------------------------------------------------------------------

def test_pm_injects_kb_context():
    llm = _mock_llm(json.dumps(_VALID_TICKETS))
    PMAgent(llm).run(_base_state(prd=_prd()))

    prompts = _system_calls(llm)
    assert any(_KB_SENTINEL in p for p in prompts), (
        "PMAgent did not inject _kb_context into system prompt"
    )


# ---------------------------------------------------------------------------
# QAAgent
# ---------------------------------------------------------------------------

def test_qa_injects_kb_context():
    llm = _mock_llm(json.dumps(_VALID_TESTS), _BUG_RESULT_CLEAN)
    QAAgent(llm).run(_base_state(code_artifacts=[_code_artifact()]))

    prompts = _system_calls(llm)
    assert any(_KB_SENTINEL in p for p in prompts), (
        "QAAgent did not inject _kb_context into system prompt"
    )


# ---------------------------------------------------------------------------
# ReviewerAgent
# ---------------------------------------------------------------------------

def test_reviewer_injects_kb_context():
    llm = _mock_llm(json.dumps(_VALID_REVIEW))
    ReviewerAgent(llm).run(_base_state(code_artifacts=[_code_artifact()]))

    prompts = _system_calls(llm)
    assert any(_KB_SENTINEL in p for p in prompts), (
        "ReviewerAgent did not inject _kb_context into system prompt"
    )


# ---------------------------------------------------------------------------
# DocWriterAgent
# ---------------------------------------------------------------------------

def test_doc_writer_injects_kb_context():
    llm = _mock_llm(json.dumps(_VALID_DOC_ARTIFACTS))
    DocWriterAgent(llm).run(_base_state(prd=_prd(), tickets=[_ticket()]))

    prompts = _system_calls(llm)
    assert any(_KB_SENTINEL in p for p in prompts), (
        "DocWriterAgent did not inject _kb_context into system prompt"
    )


# ---------------------------------------------------------------------------
# DevOpsAgent
# ---------------------------------------------------------------------------

def test_devops_injects_kb_context():
    llm = _mock_llm(json.dumps(_VALID_DEVOPS_ARTIFACTS))
    DevOpsAgent(llm).run(_base_state(code_artifacts=[_code_artifact()]))

    prompts = _system_calls(llm)
    assert any(_KB_SENTINEL in p for p in prompts), (
        "DevOpsAgent did not inject _kb_context into system prompt"
    )


# ---------------------------------------------------------------------------
# Empty KB context → agents still run normally
# ---------------------------------------------------------------------------

def test_agents_work_without_kb_context():
    """_kb_context absent or empty must not break any agent."""
    state_no_kb = _base_state()
    del state_no_kb["_kb_context"]

    llm = _mock_llm(json.dumps(_VALID_PRD))
    result = BusinessAnalystAgent(llm).run(state_no_kb)
    assert result["prd"] is not None

    state_empty_kb = _base_state(_kb_context="")
    llm2 = _mock_llm(json.dumps(_VALID_PRD))
    result2 = BusinessAnalystAgent(llm2).run(state_empty_kb)
    assert result2["prd"] is not None
