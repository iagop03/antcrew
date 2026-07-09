"""Tests for OpenAIModel — gpt-4o, o1/o3 reasoning support, cost tracking."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from antcrew.models.base import Message
from antcrew_engine.models.openai_model import OpenAIModel, _is_reasoning_model


# ===========================================================================
# _is_reasoning_model helper
# ===========================================================================

@pytest.mark.parametrize("model", ["o1", "o1-mini", "o3", "o3-mini", "o1-preview"])
def test_is_reasoning_model_true(model):
    assert _is_reasoning_model(model)


@pytest.mark.parametrize("model", ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo", "claude-sonnet-4-6"])
def test_is_reasoning_model_false(model):
    assert not _is_reasoning_model(model)


# ===========================================================================
# Fixtures
# ===========================================================================

def _make_llm(model: str = "gpt-4o") -> OpenAIModel:
    with patch("antcrew_engine.models.openai_model.OpenAI"):
        llm = OpenAIModel(model, api_key="sk-test")
    return llm


def _mock_response(content: str, prompt_tokens: int = 10, completion_tokens: int = 20):
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens

    choice = MagicMock()
    choice.message.content = content

    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


def _mock_stream_chunks(tokens: list[str]):
    chunks = []
    for tok in tokens:
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = tok
        chunk.usage = None
        chunks.append(chunk)

    # Final chunk with usage
    final = MagicMock()
    final.choices = []
    final.usage = MagicMock()
    final.usage.prompt_tokens = 10
    final.usage.completion_tokens = len(tokens)
    chunks.append(final)
    return chunks


# ===========================================================================
# Blocking (non-streaming) gpt-4o
# ===========================================================================

def test_complete_blocking_returns_content():
    llm = _make_llm()
    llm._client.chat.completions.create.return_value = _mock_response("Hello world")
    result = llm.complete([Message(role="user", content="hi")])
    assert result == "Hello world"


def test_complete_blocking_records_usage():
    llm = _make_llm()
    llm._client.chat.completions.create.return_value = _mock_response("ok", 5, 15)
    llm.complete([Message(role="user", content="hi")])
    summary = llm.get_usage_summary()
    assert summary["total_input_tokens"] == 5
    assert summary["total_output_tokens"] == 15


def test_complete_blocking_passes_max_tokens():
    llm = _make_llm()
    llm._client.chat.completions.create.return_value = _mock_response("ok")
    llm.complete([Message(role="user", content="hi")], max_tokens=512)
    call_kwargs = llm._client.chat.completions.create.call_args[1]
    assert call_kwargs["max_tokens"] == 512


def test_complete_blocking_no_streaming_flag():
    llm = _make_llm()
    llm._client.chat.completions.create.return_value = _mock_response("ok")
    llm.complete([Message(role="user", content="hi")])
    call_kwargs = llm._client.chat.completions.create.call_args[1]
    assert "stream" not in call_kwargs or not call_kwargs.get("stream")


# ===========================================================================
# Streaming gpt-4o
# ===========================================================================

def test_complete_streaming_calls_on_token():
    llm = _make_llm()
    llm.on_token = lambda t: None
    llm._client.chat.completions.create.return_value = iter(
        _mock_stream_chunks(["Hello", " ", "world"])
    )
    result = llm.complete([Message(role="user", content="hi")])
    assert result == "Hello world"


def test_complete_streaming_fires_callback():
    llm = _make_llm()
    received: list[str] = []
    llm.on_token = received.append
    llm._client.chat.completions.create.return_value = iter(
        _mock_stream_chunks(["A", "B", "C"])
    )
    llm.complete([Message(role="user", content="hi")])
    assert received == ["A", "B", "C"]


def test_complete_streaming_records_usage():
    llm = _make_llm()
    llm.on_token = lambda t: None
    llm._client.chat.completions.create.return_value = iter(
        _mock_stream_chunks(["tok1", "tok2"])
    )
    llm.complete([Message(role="user", content="hi")])
    summary = llm.get_usage_summary()
    assert summary["total_input_tokens"] == 10
    assert summary["total_output_tokens"] == 2


def test_complete_streaming_passes_stream_flag():
    llm = _make_llm()
    llm.on_token = lambda t: None
    llm._client.chat.completions.create.return_value = iter(
        _mock_stream_chunks(["ok"])
    )
    llm.complete([Message(role="user", content="hi")])
    call_kwargs = llm._client.chat.completions.create.call_args[1]
    assert call_kwargs.get("stream") is True


# ===========================================================================
# Reasoning model (o1 / o3-mini)
# ===========================================================================

def test_reasoning_model_uses_max_completion_tokens():
    llm = _make_llm("o3-mini")
    llm._client.chat.completions.create.return_value = _mock_response("reasoned")
    llm.complete([Message(role="user", content="solve this")], max_tokens=8192)
    call_kwargs = llm._client.chat.completions.create.call_args[1]
    assert "max_completion_tokens" in call_kwargs
    assert "max_tokens" not in call_kwargs
    assert call_kwargs["max_completion_tokens"] == 8192


def test_reasoning_model_does_not_stream():
    llm = _make_llm("o1")
    llm.on_token = lambda t: None  # would normally trigger streaming
    llm._client.chat.completions.create.return_value = _mock_response("output")
    llm.complete([Message(role="user", content="think")])
    call_kwargs = llm._client.chat.completions.create.call_args[1]
    assert not call_kwargs.get("stream", False)


def test_reasoning_model_returns_content():
    llm = _make_llm("o1-mini")
    llm._client.chat.completions.create.return_value = _mock_response("deep answer")
    result = llm.complete([Message(role="user", content="hi")])
    assert result == "deep answer"


def test_reasoning_model_records_usage():
    llm = _make_llm("o3-mini")
    llm._client.chat.completions.create.return_value = _mock_response("ok", 100, 200)
    llm.complete([Message(role="user", content="hi")])
    summary = llm.get_usage_summary()
    assert summary["total_input_tokens"] == 100
    assert summary["total_output_tokens"] == 200


# ===========================================================================
# Cost estimation (o3-mini via _COST_TABLE)
# ===========================================================================

def test_o3_mini_cost_estimation():
    llm = _make_llm("o3-mini")
    cost = llm._estimate_cost(1_000_000, 1_000_000)
    # o3-mini: 1.10 in + 4.40 out
    assert cost == pytest.approx(1.10 + 4.40, rel=0.01)


def test_o1_cost_estimation():
    llm = _make_llm("o1")
    cost = llm._estimate_cost(1_000_000, 1_000_000)
    assert cost == pytest.approx(15.0 + 60.0, rel=0.01)


def test_gpt4o_cost_estimation():
    llm = _make_llm("gpt-4o")
    cost = llm._estimate_cost(1_000_000, 1_000_000)
    assert cost == pytest.approx(2.50 + 10.00, rel=0.01)


def test_gpt4o_mini_cost_estimation():
    llm = _make_llm("gpt-4o-mini")
    cost = llm._estimate_cost(1_000_000, 1_000_000)
    assert cost == pytest.approx(0.15 + 0.60, rel=0.01)


# ===========================================================================
# build_llm routing
# ===========================================================================

def test_build_llm_gpt4o():
    from antcrew.config import build_llm
    with patch("antcrew_engine.models.openai_model.OpenAI"):
        llm = build_llm("gpt-4o")
    assert isinstance(llm, OpenAIModel)
    assert llm._model == "gpt-4o"


def test_build_llm_o3_mini():
    from antcrew.config import build_llm
    with patch("antcrew_engine.models.openai_model.OpenAI"):
        llm = build_llm("o3-mini")
    assert isinstance(llm, OpenAIModel)
    assert llm._is_reasoning


def test_build_llm_o1():
    from antcrew.config import build_llm
    with patch("antcrew_engine.models.openai_model.OpenAI"):
        llm = build_llm("o1")
    assert isinstance(llm, OpenAIModel)
    assert llm._is_reasoning


def test_build_llm_openai_prefix():
    from antcrew.config import build_llm
    with patch("antcrew_engine.models.openai_model.OpenAI"):
        llm = build_llm("openai:gpt-4o-mini")
    assert isinstance(llm, OpenAIModel)
    assert llm._model == "gpt-4o-mini"


# ===========================================================================
# OpenAI import guard
# ===========================================================================

def test_openai_model_raises_without_package(monkeypatch):
    import antcrew.models.openai_model as m
    original = m.OpenAI
    try:
        m.OpenAI = None
        with pytest.raises(ImportError, match="openai package"):
            OpenAIModel("gpt-4o")
    finally:
        m.OpenAI = original


# ===========================================================================
# Top-level export
# ===========================================================================

def test_openai_model_exported_from_antcrew():
    import antcrew
    # OpenAIModel is None only when openai package isn't installed;
    # but the attribute must exist either way.
    assert hasattr(antcrew, "OpenAIModel")
