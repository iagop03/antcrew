"""Tests for streaming (on_token callback) across all LLM adapters."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from antcrew.models.base import BaseLLM, Message
from antcrew.models.simulated import SimulatedLLM


# ---------------------------------------------------------------------------
# BaseLLM + SimulatedLLM
# ---------------------------------------------------------------------------

def test_simulated_llm_calls_on_token():
    llm = SimulatedLLM()
    tokens: list[str] = []
    llm.on_token = tokens.append

    result = llm.complete([
        Message(role="system", content="You are a business analyst. Write a PRD. functional_requirements"),
        Message(role="user", content="Build X"),
    ])

    assert len(tokens) == 1
    assert tokens[0] == result


def test_simulated_llm_no_on_token_by_default():
    llm = SimulatedLLM()
    assert llm.on_token is None

    # Should not raise even with no callback
    result = llm.complete([Message(role="user", content="hi")])
    assert isinstance(result, str)


def test_on_token_not_called_after_cleared():
    llm = SimulatedLLM()
    tokens: list[str] = []
    llm.on_token = tokens.append
    llm.on_token = None

    llm.complete([Message(role="user", content="hi")])
    assert tokens == []


def test_current_agent_defaults_to_empty_string():
    llm = SimulatedLLM()
    assert llm.current_agent == ""


# ---------------------------------------------------------------------------
# BaseAgent.system() tags llm.current_agent
# ---------------------------------------------------------------------------

def test_base_agent_system_sets_current_agent():
    from antcrew.agents.business import BusinessAnalystAgent

    llm = SimulatedLLM()
    agent = BusinessAnalystAgent(llm=llm)

    recorded: list[str] = []

    def on_token(token: str) -> None:
        recorded.append(llm.current_agent)

    llm.on_token = on_token
    agent.system("You are a BA.", "Write a PRD for X")

    assert recorded == ["business_analyst"]


def test_different_agents_set_different_current_agent():
    from antcrew.agents.pm import PMAgent
    from antcrew.agents.backend_dev import BackendDevAgent

    llm = SimulatedLLM()
    pm = PMAgent(llm=llm)
    dev = BackendDevAgent(llm=llm)

    seen: list[str] = []
    llm.on_token = lambda _: seen.append(llm.current_agent)

    pm.system("You are a PM.", "Create tickets")
    dev.system("You are a dev.", "Write code")

    assert seen == ["pm", "backend_dev"]


# ---------------------------------------------------------------------------
# AnthropicModel
# ---------------------------------------------------------------------------

def test_anthropic_model_streaming():
    from antcrew.models.anthropic_model import AnthropicModel

    llm = AnthropicModel.__new__(AnthropicModel)
    llm.on_token = None
    llm.current_agent = ""
    llm.model = "claude-sonnet-4-6"

    mock_stream = MagicMock()
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)
    mock_stream.text_stream = iter(["Hello", ", ", "world"])

    mock_client = MagicMock()
    mock_client.messages.stream.return_value = mock_stream
    llm._client = mock_client

    tokens: list[str] = []
    llm.on_token = tokens.append

    result = llm.complete([Message(role="user", content="Hi")])

    assert result == "Hello, world"
    assert tokens == ["Hello", ", ", "world"]
    mock_client.messages.stream.assert_called_once()


def test_anthropic_model_no_stream_when_on_token_none():
    from antcrew.models.anthropic_model import AnthropicModel

    llm = AnthropicModel.__new__(AnthropicModel)
    llm.on_token = None
    llm.current_agent = ""
    llm.model = "claude-sonnet-4-6"

    mock_response = MagicMock()
    mock_response.content[0].text = "Hello world"

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response
    llm._client = mock_client

    result = llm.complete([Message(role="user", content="Hi")])

    assert result == "Hello world"
    mock_client.messages.create.assert_called_once()
    mock_client.messages.stream.assert_not_called()


# ---------------------------------------------------------------------------
# OpenAIModel
# ---------------------------------------------------------------------------

def _make_openai_chunk(text: str):
    chunk = MagicMock()
    chunk.choices[0].delta.content = text
    return chunk


def test_openai_model_streaming():
    from antcrew.models.openai_model import OpenAIModel

    llm = OpenAIModel.__new__(OpenAIModel)
    llm.on_token = None
    llm.current_agent = ""
    llm._model = "gpt-4o"

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = iter([
        _make_openai_chunk("Hello"),
        _make_openai_chunk(", "),
        _make_openai_chunk("world"),
    ])
    llm._client = mock_client

    tokens: list[str] = []
    llm.on_token = tokens.append

    result = llm.complete([Message(role="user", content="Hi")])

    assert result == "Hello, world"
    assert tokens == ["Hello", ", ", "world"]
    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs.get("stream") is True


def test_openai_model_no_stream_when_on_token_none():
    from antcrew.models.openai_model import OpenAIModel

    llm = OpenAIModel.__new__(OpenAIModel)
    llm.on_token = None
    llm.current_agent = ""
    llm._model = "gpt-4o"

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Hello world"

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response
    llm._client = mock_client

    result = llm.complete([Message(role="user", content="Hi")])

    assert result == "Hello world"
    _, kwargs = mock_client.chat.completions.create.call_args
    assert not kwargs.get("stream")


# ---------------------------------------------------------------------------
# GroqModel
# ---------------------------------------------------------------------------

def test_groq_model_streaming():
    from antcrew.models.groq_model import GroqModel

    llm = GroqModel.__new__(GroqModel)
    llm.on_token = None
    llm.current_agent = ""
    llm.model = "llama3-70b-8192"

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = iter([
        _make_openai_chunk("Hi"),
        _make_openai_chunk(" there"),
    ])
    llm._client = mock_client

    tokens: list[str] = []
    llm.on_token = tokens.append

    result = llm.complete([Message(role="user", content="Hello")])

    assert result == "Hi there"
    assert tokens == ["Hi", " there"]
    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs.get("stream") is True


# ---------------------------------------------------------------------------
# OllamaModel
# ---------------------------------------------------------------------------

def test_ollama_model_streaming():
    from antcrew.models.ollama_model import OllamaModel

    llm = OllamaModel()
    tokens: list[str] = []
    llm.on_token = tokens.append

    lines = [
        json.dumps({"message": {"content": "Hello"}}),
        json.dumps({"message": {"content": ", world"}}),
        json.dumps({"message": {"content": ""}, "done": True}),
    ]

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.iter_lines.return_value = iter(lines)
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("httpx.stream", return_value=mock_resp):
        result = llm.complete([Message(role="user", content="Hi")])

    assert result == "Hello, world"
    assert tokens == ["Hello", ", world"]


def test_ollama_model_no_stream_when_on_token_none():
    from antcrew.models.ollama_model import OllamaModel

    llm = OllamaModel()
    assert llm.on_token is None

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"message": {"content": "Hello world"}}

    with patch("httpx.post", return_value=mock_resp):
        result = llm.complete([Message(role="user", content="Hi")])

    assert result == "Hello world"


# ---------------------------------------------------------------------------
# GeminiModel
# ---------------------------------------------------------------------------

def _sse_line(text: str) -> str:
    return "data: " + json.dumps({
        "candidates": [{"content": {"parts": [{"text": text}]}}]
    })


def test_gemini_model_streaming():
    from antcrew.models.gemini_model import GeminiModel

    llm = GeminiModel.__new__(GeminiModel)
    llm.on_token = None
    llm.current_agent = ""
    llm._model = "gemini-1.5-flash"
    llm._api_key = "fake-key"

    tokens: list[str] = []
    llm.on_token = tokens.append

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.iter_lines.return_value = iter([
        _sse_line("Hello"),
        ":",          # SSE comment line — should be ignored
        _sse_line(", world"),
        "data: [DONE]",
    ])
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("httpx.stream", return_value=mock_resp):
        result = llm.complete([Message(role="user", content="Hi")])

    assert result == "Hello, world"
    assert tokens == ["Hello", ", world"]


def test_gemini_model_no_stream_when_on_token_none():
    from antcrew.models.gemini_model import GeminiModel

    llm = GeminiModel.__new__(GeminiModel)
    llm.on_token = None
    llm.current_agent = ""
    llm._model = "gemini-1.5-flash"
    llm._api_key = "fake-key"

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "Hello world"}]}}]
    }

    with patch("httpx.post", return_value=mock_resp):
        result = llm.complete([Message(role="user", content="Hi")])

    assert result == "Hello world"
