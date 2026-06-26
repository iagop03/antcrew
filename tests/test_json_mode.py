"""Tests for v0.8.0 — native JSON mode / structured outputs."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, call

import pytest

from antcrew.models.simulated import SimulatedLLM
from antcrew.models.fallback import FallbackLLM


# ===========================================================================
# SimulatedLLM — accept and ignore json_mode
# ===========================================================================

def test_simulated_json_mode_false():
    llm = SimulatedLLM()
    msgs = [
        MagicMock(role="system", content="You are a QA engineer."),
        MagicMock(role="user", content="Build login"),
    ]
    result = llm.complete(msgs, json_mode=False)
    assert isinstance(result, str)


def test_simulated_json_mode_true():
    llm = SimulatedLLM()
    msgs = [
        MagicMock(role="system", content="You are a QA engineer."),
        MagicMock(role="user", content="Build login"),
    ]
    result = llm.complete(msgs, json_mode=True)
    assert isinstance(result, str)


def test_simulated_json_mode_default_is_false():
    llm = SimulatedLLM()
    msgs = [MagicMock(role="system", content="x"), MagicMock(role="user", content="y")]
    result_default = llm.complete(msgs)
    result_explicit = llm.complete(msgs, json_mode=False)
    assert result_default == result_explicit


# ===========================================================================
# OpenAIModel — blocking path uses response_format
# ===========================================================================

def _make_openai_llm(model="gpt-4o"):
    from antcrew.models.openai_model import OpenAIModel
    llm = OpenAIModel.__new__(OpenAIModel)
    llm._model = model
    llm._is_reasoning = False
    llm.on_token = None
    llm.timeout = 60.0
    llm.max_retries = 0
    llm.retry_delay = 1.0
    llm.max_retry_delay = 60.0
    llm.retry_jitter = 0.0
    llm.cache = None
    llm.max_cost_usd = None
    llm._cost_limit_offset = 0.0
    llm.trace = None
    llm._trace_run_id = None
    llm.current_agent = ""
    return llm


def _fake_oai_response(text="{}"):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    return resp


def test_openai_blocking_json_mode_passes_response_format():
    llm = _make_openai_llm()
    llm._client = MagicMock()
    llm._client.chat.completions.create.return_value = _fake_oai_response('{"ok": true}')

    chat_msgs = [{"role": "user", "content": "hi"}]
    llm._complete_blocking(chat_msgs, max_tokens=100, json_mode=True)

    _, kwargs = llm._client.chat.completions.create.call_args
    assert kwargs.get("response_format") == {"type": "json_object"}


def test_openai_blocking_no_json_mode_no_response_format():
    llm = _make_openai_llm()
    llm._client = MagicMock()
    llm._client.chat.completions.create.return_value = _fake_oai_response('{"ok": true}')

    chat_msgs = [{"role": "user", "content": "hi"}]
    llm._complete_blocking(chat_msgs, max_tokens=100, json_mode=False)

    _, kwargs = llm._client.chat.completions.create.call_args
    assert "response_format" not in kwargs


def test_openai_complete_routes_json_mode_to_blocking():
    llm = _make_openai_llm()
    llm._client = MagicMock()
    llm._client.chat.completions.create.return_value = _fake_oai_response('{"a": 1}')

    from antcrew.models.base import Message
    msgs = [Message(role="user", content="hi")]
    llm.complete(msgs, json_mode=True)

    _, kwargs = llm._client.chat.completions.create.call_args
    assert kwargs.get("response_format") == {"type": "json_object"}


def test_openai_reasoning_model_ignores_json_mode():
    llm = _make_openai_llm(model="o1-mini")
    llm._is_reasoning = True
    llm._client = MagicMock()
    llm._client.chat.completions.create.return_value = _fake_oai_response('{"r": 1}')

    from antcrew.models.base import Message
    msgs = [Message(role="user", content="hi")]
    llm.complete(msgs, json_mode=True)

    _, kwargs = llm._client.chat.completions.create.call_args
    assert "response_format" not in kwargs


# ===========================================================================
# GeminiModel — responseMimeType set when json_mode=True
# ===========================================================================

def _make_gemini_llm():
    from antcrew.models.gemini_model import GeminiModel
    llm = GeminiModel.__new__(GeminiModel)
    llm._model = "gemini-1.5-flash"
    llm._api_key = "fake-key"
    llm.on_token = None
    llm.timeout = 60.0
    llm.max_retries = 0
    llm.retry_delay = 1.0
    llm.max_retry_delay = 60.0
    llm.retry_jitter = 0.0
    llm.cache = None
    llm.max_cost_usd = None
    llm._cost_limit_offset = 0.0
    llm.trace = None
    llm._trace_run_id = None
    llm.current_agent = ""
    return llm


def test_gemini_json_mode_sets_response_mime_type():
    llm = _make_gemini_llm()
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": '{"x": 1}'}]}}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
    }

    posted_bodies: list[dict] = []

    def fake_post(url, params=None, json=None, timeout=None):
        posted_bodies.append(json or {})
        return fake_resp

    with patch("httpx.post", side_effect=fake_post):
        from antcrew.models.base import Message
        msgs = [Message(role="system", content="sys"), Message(role="user", content="hi")]
        llm.complete(msgs, json_mode=True)

    assert posted_bodies, "httpx.post was never called"
    assert posted_bodies[0]["generationConfig"]["responseMimeType"] == "application/json"


def test_gemini_no_json_mode_no_mime_type():
    llm = _make_gemini_llm()
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "hello"}]}}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
    }

    posted_bodies: list[dict] = []

    def fake_post(url, params=None, json=None, timeout=None):
        posted_bodies.append(json or {})
        return fake_resp

    with patch("httpx.post", side_effect=fake_post):
        from antcrew.models.base import Message
        msgs = [Message(role="system", content="sys"), Message(role="user", content="hi")]
        llm.complete(msgs, json_mode=False)

    assert "responseMimeType" not in posted_bodies[0].get("generationConfig", {})


# ===========================================================================
# AnthropicModel — accepts json_mode, ignores it
# ===========================================================================

def test_anthropic_accepts_json_mode():
    from antcrew.models.anthropic_model import AnthropicModel
    llm = AnthropicModel.__new__(AnthropicModel)
    llm.model = "claude-haiku-4-5-20251001"
    llm.prompt_caching = False
    llm.on_token = None
    llm.timeout = 60.0
    llm.max_retries = 0
    llm.retry_delay = 1.0
    llm.max_retry_delay = 60.0
    llm.retry_jitter = 0.0
    llm.cache = None
    llm.max_cost_usd = None
    llm._cost_limit_offset = 0.0
    llm.trace = None
    llm._trace_run_id = None
    llm.current_agent = ""

    fake_msg = MagicMock()
    fake_msg.content = [MagicMock(text='{"ok": true}')]
    fake_msg.usage = MagicMock(input_tokens=10, output_tokens=5,
                               cache_creation_input_tokens=0, cache_read_input_tokens=0)
    fake_msg.stop_reason = "end_turn"

    llm._client = MagicMock()
    llm._client.messages.create.return_value = fake_msg

    from antcrew.models.base import Message
    msgs = [Message(role="system", content="sys"), Message(role="user", content="hi")]
    result = llm.complete(msgs, json_mode=True)
    assert result == '{"ok": true}'


# ===========================================================================
# GroqModel — accepts json_mode, ignores it
# ===========================================================================

def test_groq_accepts_json_mode():
    from antcrew.models.groq_model import GroqModel
    llm = GroqModel.__new__(GroqModel)
    llm.model = "llama3-70b-8192"
    llm.on_token = None
    llm.timeout = 60.0
    llm.max_retries = 0
    llm.retry_delay = 1.0
    llm.max_retry_delay = 60.0
    llm.retry_jitter = 0.0
    llm.cache = None
    llm.max_cost_usd = None
    llm._cost_limit_offset = 0.0
    llm.trace = None
    llm._trace_run_id = None
    llm.current_agent = ""

    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock()]
    fake_resp.choices[0].message.content = '{"groq": true}'
    fake_resp.usage = MagicMock(prompt_tokens=5, completion_tokens=3)
    llm._client = MagicMock()
    llm._client.chat.completions.create.return_value = fake_resp

    from antcrew.models.base import Message
    msgs = [Message(role="user", content="hi")]
    result = llm.complete(msgs, json_mode=True)
    assert result == '{"groq": true}'


# ===========================================================================
# OllamaModel — accepts json_mode, ignores it
# ===========================================================================

def test_ollama_accepts_json_mode():
    from antcrew.models.ollama_model import OllamaModel
    llm = OllamaModel.__new__(OllamaModel)
    llm.model = "llama3"
    llm.base_url = "http://localhost:11434"
    llm.on_token = None
    llm.timeout = 60.0
    llm.max_retries = 0
    llm.retry_delay = 1.0
    llm.max_retry_delay = 60.0
    llm.retry_jitter = 0.0
    llm.cache = None
    llm.max_cost_usd = None
    llm._cost_limit_offset = 0.0
    llm.trace = None
    llm._trace_run_id = None
    llm.current_agent = ""

    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json.return_value = {
        "message": {"content": '{"ollama": true}'},
        "prompt_eval_count": 5,
        "eval_count": 3,
    }

    with patch("httpx.post", return_value=fake_resp):
        from antcrew.models.base import Message
        msgs = [Message(role="user", content="hi")]
        result = llm.complete(msgs, json_mode=True)

    assert result == '{"ollama": true}'


# ===========================================================================
# FallbackLLM — passes json_mode through to each model
# ===========================================================================

def test_fallback_passes_json_mode_to_primary():
    primary = MagicMock(spec=SimulatedLLM)
    primary.complete.return_value = '{"ok": 1}'
    primary._usage_log = []

    fallback_llm = FallbackLLM.__new__(FallbackLLM)
    object.__setattr__(fallback_llm, "_models", [primary])
    object.__setattr__(fallback_llm, "_fallback_events", [])

    from antcrew.models.base import Message
    msgs = [Message(role="user", content="hi")]
    fallback_llm.complete(msgs, json_mode=True)

    primary.complete.assert_called_once_with(msgs, max_tokens=16384, json_mode=True)


def test_fallback_passes_json_mode_to_secondary_on_primary_failure():
    primary = MagicMock(spec=SimulatedLLM)
    primary.complete.side_effect = RuntimeError("primary down")
    primary._usage_log = []

    secondary = MagicMock(spec=SimulatedLLM)
    secondary.complete.return_value = '{"secondary": true}'
    secondary._usage_log = []

    fallback_llm = FallbackLLM.__new__(FallbackLLM)
    object.__setattr__(fallback_llm, "_models", [primary, secondary])
    object.__setattr__(fallback_llm, "_fallback_events", [])

    from antcrew.models.base import Message
    msgs = [Message(role="user", content="hi")]
    result = fallback_llm.complete(msgs, json_mode=True)

    assert result == '{"secondary": true}'
    secondary.complete.assert_called_once_with(msgs, max_tokens=16384, json_mode=True)


# ===========================================================================
# BaseAgent.system_parsed — automatically passes json_mode=True
# ===========================================================================

class _CaptureLLM(SimulatedLLM):
    """Records every complete() call including json_mode."""
    calls: list[dict]

    def __init__(self):
        super().__init__()
        self.calls = []

    def complete(self, messages, *, max_tokens=16384, json_mode=False):
        self.calls.append({"json_mode": json_mode})
        return super().complete(messages, max_tokens=max_tokens, json_mode=json_mode)


def test_system_parsed_passes_json_mode_true():
    from antcrew.agents.pm import PMAgent
    llm = _CaptureLLM()
    agent = PMAgent(llm)

    result = agent.system_parsed(
        "You are a PM.",
        "Build a login system",
        schema=dict,
    )
    assert any(c["json_mode"] is True for c in llm.calls)


def test_system_parsed_retry_also_uses_json_mode():
    from antcrew.agents.pm import PMAgent

    call_count = 0

    class _BadThenGoodLLM(SimulatedLLM):
        calls: list[dict]

        def __init__(self):
            super().__init__()
            self.calls = []

        def complete(self, messages, *, max_tokens=16384, json_mode=False):
            nonlocal call_count
            self.calls.append({"json_mode": json_mode})
            call_count += 1
            if call_count == 1:
                return "not-json-at-all"
            return '{"ok": true}'

    llm = _BadThenGoodLLM()
    agent = PMAgent(llm)

    agent.system_parsed(
        "You are a PM.",
        "Build a login system",
        schema=dict,
        max_retries=1,
    )

    assert all(c["json_mode"] is True for c in llm.calls)
    assert len(llm.calls) == 2
