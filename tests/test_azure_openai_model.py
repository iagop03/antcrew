"""Tests for AzureOpenAIModel."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from antcrew.models.azure_openai_model import AzureOpenAIModel
from antcrew.models.base import Message

_ENDPOINT = "https://my-corp.openai.azure.com"
_KEY = "az-test-key"


def _make_llm(deployment: str = "gpt-4o", **kwargs) -> AzureOpenAIModel:
    with patch("antcrew_engine.models.azure_openai_model.AzureOpenAI"):
        llm = AzureOpenAIModel(
            deployment,
            azure_endpoint=_ENDPOINT,
            api_key=_KEY,
            **kwargs,
        )
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
    final = MagicMock()
    final.choices = []
    final.usage = MagicMock()
    final.usage.prompt_tokens = 5
    final.usage.completion_tokens = len(tokens)
    chunks.append(final)
    return chunks


# ===========================================================================
# Constructor
# ===========================================================================

def test_constructor_stores_deployment():
    llm = _make_llm("gpt-4o-prod")
    assert llm._model == "gpt-4o-prod"


def test_constructor_stores_endpoint():
    llm = _make_llm()
    assert llm._azure_endpoint == _ENDPOINT


def test_constructor_default_api_version():
    llm = _make_llm()
    assert llm._api_version == AzureOpenAIModel._DEFAULT_API_VERSION


def test_constructor_custom_api_version():
    llm = _make_llm(api_version="2024-05-01-preview")
    assert llm._api_version == "2024-05-01-preview"


def test_constructor_reasoning_model_detected():
    llm = _make_llm("o3-mini-deployment")
    assert llm._is_reasoning is True


def test_constructor_non_reasoning_model():
    llm = _make_llm("gpt-4o-prod")
    assert llm._is_reasoning is False


def test_constructor_raises_without_endpoint():
    with patch("antcrew_engine.models.azure_openai_model.AzureOpenAI"):
        with pytest.raises(EnvironmentError, match="endpoint"):
            AzureOpenAIModel("gpt-4o", api_key=_KEY)


def test_constructor_reads_endpoint_from_env(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", _ENDPOINT)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", _KEY)
    with patch("antcrew_engine.models.azure_openai_model.AzureOpenAI"):
        llm = AzureOpenAIModel("gpt-4o")
    assert llm._azure_endpoint == _ENDPOINT


def test_constructor_reads_api_key_from_env(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "env-key")
    with patch("antcrew_engine.models.azure_openai_model.AzureOpenAI") as mock_cls:
        AzureOpenAIModel("gpt-4o", azure_endpoint=_ENDPOINT)
    call_kwargs = mock_cls.call_args[1]
    assert call_kwargs["api_key"] == "env-key"


def test_constructor_reads_api_version_from_env(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-01-01")
    llm = _make_llm()
    assert llm._api_version == "2025-01-01"


def test_constructor_passes_azure_client_kwargs():
    with patch("antcrew_engine.models.azure_openai_model.AzureOpenAI") as mock_cls:
        AzureOpenAIModel(
            "gpt-4o",
            azure_endpoint=_ENDPOINT,
            api_key=_KEY,
            api_version="2024-02-01",
        )
    kwargs = mock_cls.call_args[1]
    assert kwargs["azure_endpoint"] == _ENDPOINT
    assert kwargs["api_version"] == "2024-02-01"


# ===========================================================================
# Blocking completion (inherited from OpenAIModel)
# ===========================================================================

def test_complete_blocking():
    llm = _make_llm()
    llm._client.chat.completions.create.return_value = _mock_response("Azure result")
    result = llm.complete([Message(role="user", content="hi")])
    assert result == "Azure result"


def test_complete_records_usage():
    llm = _make_llm()
    llm._client.chat.completions.create.return_value = _mock_response("ok", 30, 60)
    llm.complete([Message(role="user", content="hi")])
    summary = llm.get_usage_summary()
    assert summary["total_input_tokens"] == 30
    assert summary["total_output_tokens"] == 60


# ===========================================================================
# Streaming (inherited from OpenAIModel)
# ===========================================================================

def test_complete_streaming():
    llm = _make_llm()
    received: list[str] = []
    llm.on_token = received.append
    llm._client.chat.completions.create.return_value = iter(
        _mock_stream_chunks(["Hello", " ", "Azure"])
    )
    result = llm.complete([Message(role="user", content="hi")])
    assert result == "Hello Azure"
    assert received == ["Hello", " ", "Azure"]


# ===========================================================================
# Reasoning model (inherited from OpenAIModel)
# ===========================================================================

def test_reasoning_deployment_uses_max_completion_tokens():
    llm = _make_llm("o3-mini-prod")
    llm._client.chat.completions.create.return_value = _mock_response("reasoned")
    llm.complete([Message(role="user", content="think")], max_tokens=4096)
    kwargs = llm._client.chat.completions.create.call_args[1]
    assert "max_completion_tokens" in kwargs
    assert "max_tokens" not in kwargs


def test_reasoning_deployment_no_streaming():
    llm = _make_llm("o1-prod")
    llm.on_token = lambda t: None
    llm._client.chat.completions.create.return_value = _mock_response("ok")
    llm.complete([Message(role="user", content="hi")])
    kwargs = llm._client.chat.completions.create.call_args[1]
    assert not kwargs.get("stream", False)


# ===========================================================================
# build_llm routing
# ===========================================================================

def test_build_llm_azure_prefix(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", _ENDPOINT)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", _KEY)
    from antcrew.config import build_llm
    with patch("antcrew_engine.models.azure_openai_model.AzureOpenAI"):
        llm = build_llm("azure:gpt-4o-deployment")
    assert isinstance(llm, AzureOpenAIModel)
    assert llm._model == "gpt-4o-deployment"


# ===========================================================================
# Import guard
# ===========================================================================

def test_raises_without_openai_package(monkeypatch):
    import antcrew.models.azure_openai_model as m
    original = m.AzureOpenAI
    try:
        m.AzureOpenAI = None
        with pytest.raises(ImportError, match="openai package"):
            AzureOpenAIModel("gpt-4o", azure_endpoint=_ENDPOINT)
    finally:
        m.AzureOpenAI = original


# ===========================================================================
# Top-level export
# ===========================================================================

def test_exported_from_antcrew():
    import antcrew
    assert hasattr(antcrew, "AzureOpenAIModel")
