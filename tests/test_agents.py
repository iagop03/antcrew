"""Unit tests for agent logic — LLM is mocked, no API calls."""
import json
from unittest.mock import MagicMock

import pytest

from antcrew.agents.business import BusinessAnalystAgent
from antcrew.agents.pm import PMAgent
from antcrew.agents.backend_dev import BackendDevAgent
from antcrew.core.artifacts import Priority, TicketStatus
from antcrew.core.state import TeamState


def _state(request: str = "Build an auth module", **overrides) -> TeamState:
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


# ---------------------------------------------------------------------------
# BusinessAnalystAgent
# ---------------------------------------------------------------------------

_VALID_PRD = {
    "title": "Auth Module",
    "summary": "Implement JWT-based authentication.",
    "goals": ["Secure login"],
    "out_of_scope": ["OAuth"],
    "functional_requirements": ["POST /login endpoint"],
    "non_functional_requirements": ["< 200 ms P99"],
    "open_questions": [],
}


def test_business_analyst_returns_prd():
    agent = BusinessAnalystAgent(_mock_llm(json.dumps(_VALID_PRD)))
    result = agent.run(_state())

    assert result["prd"].title == "Auth Module"
    assert result["current_agent"] == "business_analyst"


def test_business_analyst_strips_markdown_fences():
    wrapped = f"```json\n{json.dumps(_VALID_PRD)}\n```"
    agent = BusinessAnalystAgent(_mock_llm(wrapped))
    result = agent.run(_state())

    assert result["prd"] is not None


# ---------------------------------------------------------------------------
# PMAgent
# ---------------------------------------------------------------------------

_VALID_TICKETS = [
    {
        "title": "Create /login endpoint",
        "description": "POST /login validates credentials and returns JWT.",
        "priority": "high",
        "acceptance_criteria": ["Returns 200 on valid credentials"],
        "dependencies": [],
    },
    {
        "title": "Add JWT middleware",
        "description": "Validates Authorization header on protected routes.",
        "priority": "high",
        "acceptance_criteria": ["Returns 401 on missing/invalid token"],
        "dependencies": [],
    },
]


def test_pm_creates_tickets_from_prd():
    from antcrew.core.artifacts import PRD

    prd = PRD.model_validate(_VALID_PRD)
    state = _state(prd=prd)

    agent = PMAgent(_mock_llm(json.dumps(_VALID_TICKETS)))
    result = agent.run(state)

    assert len(result["tickets"]) == 2
    assert result["tickets"][0].id == "TICKET-001"
    assert result["tickets"][0].priority == Priority.HIGH
    assert result["current_agent"] == "pm"


def test_pm_returns_error_when_no_prd():
    agent = PMAgent(_mock_llm("[]"))
    result = agent.run(_state())

    assert result.get("errors")


# ---------------------------------------------------------------------------
# BackendDevAgent
# ---------------------------------------------------------------------------

_VALID_ARTIFACTS = [
    {
        "file_path": "src/auth/login.py",
        "description": "Login endpoint",
        "language": "python",
        "content": "def login(): pass",
    }
]


def test_backend_dev_generates_code_artifacts():
    from antcrew.core.artifacts import PRD, Ticket

    prd = PRD.model_validate(_VALID_PRD)
    tickets = [
        Ticket(
            id="TICKET-001",
            title="Create /login endpoint",
            description="...",
            priority=Priority.HIGH,
            status=TicketStatus.OPEN,
        )
    ]
    state = _state(prd=prd, tickets=tickets)

    agent = BackendDevAgent(_mock_llm(json.dumps(_VALID_ARTIFACTS)))
    result = agent.run(state)

    assert len(result["code_artifacts"]) == 1
    assert result["code_artifacts"][0].file_path == "src/auth/login.py"
    assert result["code_artifacts"][0].ticket_id == "TICKET-001"
    assert result["tickets"][0].status == TicketStatus.DONE


def test_backend_dev_skips_done_tickets():
    from antcrew.core.artifacts import Ticket

    tickets = [
        Ticket(
            id="TICKET-001",
            title="Done ticket",
            description="...",
            status=TicketStatus.DONE,
        )
    ]
    state = _state(tickets=tickets)
    agent = BackendDevAgent(_mock_llm("[]"))
    result = agent.run(state)

    assert result.get("code_artifacts") is None
