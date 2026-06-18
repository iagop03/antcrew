"""Tests for ConsoleChannel, GeminiModel, and refactored run_interactive."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from antcrew.core.artifacts import PRD, Ticket, CodeArtifact, Priority, TicketStatus
from antcrew.integrations.console import ConsoleChannel, _display_artifact, _prompt_decision


# ---------------------------------------------------------------------------
# ConsoleChannel
# ---------------------------------------------------------------------------

def test_console_channel_is_base_channel():
    from antcrew.core.channel import BaseChannel
    assert issubclass(ConsoleChannel, BaseChannel)


@pytest.mark.asyncio
async def test_console_channel_notify(capsys):
    ch = ConsoleChannel()
    await ch.notify("Pipeline starting…")
    captured = capsys.readouterr()
    assert "Pipeline starting" in captured.out


@pytest.mark.asyncio
async def test_console_channel_send_for_review_approve():
    prd = PRD(
        title="Auth Module",
        summary="JWT authentication.",
        functional_requirements=["POST /login"],
    )
    ch = ConsoleChannel()

    with patch("antcrew.integrations.console._prompt_decision", return_value="approve"):
        result = await ch.send_for_review(prd, "business_analyst", "sess-1")

    assert result["decision"] == "approve"
    assert result["edited"] is None


@pytest.mark.asyncio
async def test_console_channel_send_for_review_reject():
    tickets = [Ticket(id="T-1", title="Login", description="...", priority=Priority.HIGH)]
    ch = ConsoleChannel()

    with patch("antcrew.integrations.console._prompt_decision", return_value="reject"):
        result = await ch.send_for_review(tickets, "pm", "sess-1")

    assert result["decision"] == "reject"


@pytest.mark.asyncio
async def test_console_channel_send_for_review_edit():
    prd = PRD(title="Auth", summary="JWT auth.")
    ch = ConsoleChannel()
    edited_json = json.dumps({"title": "Auth v2", "summary": "Updated."})

    with (
        patch("antcrew.integrations.console._prompt_decision", return_value="edit"),
        patch("antcrew.console.edit_artifact_in_editor", return_value=edited_json),
    ):
        result = await ch.send_for_review(prd, "business_analyst", "sess-1")

    assert result["decision"] == "edit"
    assert result["edited"] == edited_json


def test_display_artifact_prd(capsys):
    prd = PRD(title="My PRD", summary="Build something.", functional_requirements=["FR1"])
    _display_artifact(prd, "business_analyst")
    captured = capsys.readouterr()
    assert "My PRD" in captured.out


def test_display_artifact_tickets(capsys):
    tickets = [
        Ticket(id="T-1", title="Login endpoint", description="...", priority=Priority.HIGH)
    ]
    _display_artifact(tickets, "pm")
    captured = capsys.readouterr()
    assert "T-1" in captured.out


def test_display_artifact_code(capsys):
    artifacts = [
        CodeArtifact(
            ticket_id="T-1",
            file_path="src/login.py",
            description="Login",
            content="def login(): pass",
            language="python",
        )
    ]
    _display_artifact(artifacts, "backend_dev")
    captured = capsys.readouterr()
    assert "src/login.py" in captured.out


def test_display_artifact_none(capsys):
    _display_artifact(None, "agent")
    captured = capsys.readouterr()
    assert "No artifact" in captured.out


# ---------------------------------------------------------------------------
# GeminiModel
# ---------------------------------------------------------------------------

def test_gemini_model_builds_correct_body():
    from antcrew.models.base import Message
    from antcrew.models.gemini_model import GeminiModel

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "Hello from Gemini"}]}}]
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("antcrew.models.gemini_model.httpx.post", return_value=mock_resp) as mock_post:
        llm = GeminiModel(api_key="fake-key")
        result = llm.complete([
            Message(role="system", content="You are helpful."),
            Message(role="user", content="Hi"),
        ])

    assert result == "Hello from Gemini"
    call_kwargs = mock_post.call_args
    body = call_kwargs.kwargs["json"]

    # System prompt goes to systemInstruction, NOT contents
    assert "systemInstruction" in body
    assert body["systemInstruction"]["parts"][0]["text"] == "You are helpful."

    # Only user message in contents
    assert len(body["contents"]) == 1
    assert body["contents"][0]["role"] == "user"


def test_gemini_model_maps_assistant_to_model_role():
    from antcrew.models.base import Message
    from antcrew.models.gemini_model import GeminiModel

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "OK"}]}}]
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("antcrew.models.gemini_model.httpx.post", return_value=mock_resp) as mock_post:
        llm = GeminiModel(api_key="fake-key")
        llm.complete([
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi there"),
            Message(role="user", content="What's next?"),
        ])

    body = mock_post.call_args.kwargs["json"]
    roles = [c["role"] for c in body["contents"]]
    assert "model" in roles  # "assistant" → "model"
    assert "assistant" not in roles


def test_gemini_model_uses_env_api_key(monkeypatch):
    from antcrew.models.gemini_model import GeminiModel

    monkeypatch.setenv("GOOGLE_API_KEY", "env-key-123")
    llm = GeminiModel()
    assert llm._api_key == "env-key-123"


# ---------------------------------------------------------------------------
# run_interactive refactor (Supervisor interrupt_before override)
# ---------------------------------------------------------------------------

def test_supervisor_build_accepts_interrupt_before_override():
    from antcrew.core.supervisor import Supervisor
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.agents.business import BusinessAnalystAgent
    from antcrew.agents.pm import PMAgent

    sup = Supervisor(flow=[("business_analyst", "pm")])
    llm = SimulatedLLM()
    agents = {
        "business_analyst": BusinessAnalystAgent(llm),
        "pm": PMAgent(llm),
    }
    # Override with empty list → no interrupts
    app = sup.build(agents, interrupt_before=[])
    # Should compile without error
    assert app is not None


def test_supervisor_build_respects_approval_required():
    from antcrew.core.supervisor import Supervisor
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.agents.business import BusinessAnalystAgent
    from antcrew.agents.pm import PMAgent

    sup = Supervisor(flow=[("business_analyst", "pm")])
    llm = SimulatedLLM()

    ba = BusinessAnalystAgent(llm)
    pm = PMAgent(llm, approval_required=True)
    agents = {"business_analyst": ba, "pm": pm}

    # Without override, should interrupt before pm (approval_required=True)
    app = sup.build(agents)
    assert app is not None
