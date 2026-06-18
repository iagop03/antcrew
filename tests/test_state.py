"""Tests for TeamState and core artifacts."""
import pytest

from antcrew.core.artifacts import PRD, Ticket, CodeArtifact, Priority, TicketStatus
from antcrew.core.state import TeamState


def _make_state(**kwargs) -> TeamState:
    base: TeamState = {
        "request": "test",
        "messages": [],
        "prd": None,
        "tickets": None,
        "code_artifacts": None,
        "current_agent": "",
        "errors": [],
    }
    base.update(kwargs)
    return base


def test_state_has_required_keys():
    state = _make_state()
    assert "request" in state
    assert "prd" in state
    assert "tickets" in state
    assert "code_artifacts" in state


def test_prd_validation():
    prd = PRD(
        title="My PRD",
        summary="Summary",
        goals=["Goal 1"],
        functional_requirements=["Req 1"],
    )
    assert prd.title == "My PRD"
    assert prd.out_of_scope == []


def test_prd_serialization_round_trip():
    prd = PRD(title="Auth", summary="Auth module", goals=["Secure login"])
    restored = PRD.model_validate_json(prd.model_dump_json())
    assert restored == prd


def test_ticket_defaults():
    ticket = Ticket(id="T-001", title="Do X", description="Implement X")
    assert ticket.priority == Priority.MEDIUM
    assert ticket.status == TicketStatus.OPEN
    assert ticket.acceptance_criteria == []


def test_ticket_status_transition():
    ticket = Ticket(id="T-001", title="Do X", description="...")
    done = ticket.model_copy(update={"status": TicketStatus.DONE})
    assert done.status == TicketStatus.DONE
    assert ticket.status == TicketStatus.OPEN  # original unchanged


def test_code_artifact_fields():
    artifact = CodeArtifact(
        ticket_id="T-001",
        file_path="src/x.py",
        description="Module X",
        content="def x(): pass",
        language="python",
    )
    assert artifact.language == "python"
    assert artifact.ticket_id == "T-001"
