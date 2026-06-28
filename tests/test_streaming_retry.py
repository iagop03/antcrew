"""Tests for streaming retry paths in all four LLM adapters.

Streaming retry: complete() wraps _stream_complete in _with_retry.
If the connection fails before/during streaming, the whole operation
retries from scratch. Tests go through complete() to exercise the full path.
"""
from __future__ import annotations

import json as _json
from unittest.mock import MagicMock, patch

import pytest

from antcrew.models.base import Message


# ── Anthropic streaming retry ─────────────────────────────────────────────────

class TestAnthropicStreamingRetry:
    def _make_llm(self, on_token=None):
        from antcrew.models.anthropic_model import AnthropicModel
        llm = AnthropicModel.__new__(AnthropicModel)
        llm.model = "claude-sonnet-4-6"
        llm.prompt_caching = False
        llm.max_retries = 2
        llm.retry_delay = 0.0
        llm.retry_jitter = 0.0
        llm.timeout = 30
        llm.on_token = on_token or (lambda t: None)
        llm.current_agent = "test"
        llm._client = MagicMock()
        return llm

    def _fake_stream_ctx(self, texts, stop_reason="end_turn"):
        final = MagicMock()
        final.usage = MagicMock(
            input_tokens=5, output_tokens=len(texts),
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )
        final.stop_reason = stop_reason
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.text_stream = iter(texts)
        ctx.get_final_message = MagicMock(return_value=final)
        return ctx

    def test_stream_retries_on_connection_error(self):
        llm = self._make_llm()
        call_count = []

        def flaky(**kwargs):
            call_count.append(1)
            if len(call_count) == 1:
                raise ConnectionError("refused")
            return self._fake_stream_ctx(["he", "llo"])

        llm._client.messages.stream = flaky
        result = llm.complete([Message(role="user", content="hello")])
        assert result == "hello"
        assert len(call_count) == 2

    def test_stream_retries_on_timeout(self):
        llm = self._make_llm()
        call_count = []

        def flaky(**kwargs):
            call_count.append(1)
            if len(call_count) < 3:
                raise TimeoutError("stream timeout")
            return self._fake_stream_ctx(["ok"])

        llm._client.messages.stream = flaky
        result = llm.complete([Message(role="user", content="hi")])
        assert result == "ok"
        assert len(call_count) == 3

    def test_stream_raises_after_max_retries(self):
        llm = self._make_llm()
        llm._client.messages.stream = MagicMock(side_effect=ConnectionError("refused"))
        with pytest.raises(ConnectionError):
            llm.complete([Message(role="user", content="hi")])

    def test_stream_tokens_received_on_success(self):
        tokens = []
        llm = self._make_llm(on_token=tokens.append)
        llm._client.messages.stream = MagicMock(
            return_value=self._fake_stream_ctx(["hello", " world"])
        )
        result = llm.complete([Message(role="user", content="hi")])
        assert result == "hello world"
        assert tokens == ["hello", " world"]


# ── OpenAI streaming retry ────────────────────────────────────────────────────

class TestOpenAIStreamingRetry:
    def _make_llm(self, on_token=None):
        pytest.importorskip("openai")
        from antcrew.models.openai_model import OpenAIModel
        llm = OpenAIModel.__new__(OpenAIModel)
        llm._model = "gpt-4o"
        llm._is_reasoning = False
        llm.max_retries = 2
        llm.retry_delay = 0.0
        llm.retry_jitter = 0.0
        llm.timeout = 30
        llm.on_token = on_token or (lambda t: None)
        llm.current_agent = "test"
        llm._client = MagicMock()
        return llm

    def _fake_stream(self, text: str):
        chunk = MagicMock()
        chunk.choices = [MagicMock(delta=MagicMock(content=text))]
        chunk.usage = None
        usage_chunk = MagicMock()
        usage_chunk.choices = []
        usage_chunk.usage = MagicMock(prompt_tokens=5, completion_tokens=3)
        return iter([chunk, usage_chunk])

    def test_stream_retries_on_timeout(self):
        llm = self._make_llm()
        call_count = []

        def flaky(**kwargs):
            call_count.append(1)
            if len(call_count) == 1:
                raise TimeoutError("stream timeout")
            return self._fake_stream("answer")

        llm._client.chat.completions.create = flaky
        result = llm.complete([Message(role="user", content="hi")])
        assert result == "answer"
        assert len(call_count) == 2

    def test_stream_retries_on_429(self):
        llm = self._make_llm()
        call_count = []
        exc = Exception("rate limited")
        exc.response = MagicMock(status_code=429)

        def flaky(**kwargs):
            call_count.append(1)
            if len(call_count) == 1:
                raise exc
            return self._fake_stream("ok")

        llm._client.chat.completions.create = flaky
        result = llm.complete([Message(role="user", content="hi")])
        assert result == "ok"
        assert len(call_count) == 2

    def test_stream_raises_after_max_retries(self):
        llm = self._make_llm()
        llm._client.chat.completions.create = MagicMock(
            side_effect=ConnectionError("refused")
        )
        with pytest.raises(ConnectionError):
            llm.complete([Message(role="user", content="hi")])


# ── Groq streaming retry ──────────────────────────────────────────────────────

class TestGroqStreamingRetry:
    def _make_llm(self, on_token=None):
        from antcrew.models.groq_model import GroqModel
        llm = GroqModel.__new__(GroqModel)
        llm.model = "llama3-70b-8192"
        llm.max_retries = 2
        llm.retry_delay = 0.0
        llm.retry_jitter = 0.0
        llm.timeout = 30
        llm.on_token = on_token or (lambda t: None)
        llm.current_agent = "test"
        llm._client = MagicMock()
        return llm

    def _fake_stream(self, text: str):
        chunk = MagicMock()
        chunk.choices = [MagicMock(delta=MagicMock(content=text))]
        return iter([chunk])

    def test_stream_retries_on_429(self):
        llm = self._make_llm()
        call_count = []
        exc = Exception("rate limited")
        exc.response = MagicMock(status_code=429)

        def flaky(**kwargs):
            call_count.append(1)
            if len(call_count) == 1:
                raise exc
            return self._fake_stream("pong")

        llm._client.chat.completions.create = flaky
        result = llm.complete([Message(role="user", content="ping")])
        assert result == "pong"
        assert len(call_count) == 2

    def test_stream_retries_on_timeout(self):
        llm = self._make_llm()
        call_count = []

        def flaky(**kwargs):
            call_count.append(1)
            if len(call_count) == 1:
                raise TimeoutError("timeout")
            return self._fake_stream("ok")

        llm._client.chat.completions.create = flaky
        result = llm.complete([Message(role="user", content="hi")])
        assert result == "ok"
        assert len(call_count) == 2

    def test_stream_raises_after_max_retries(self):
        llm = self._make_llm()
        llm._client.chat.completions.create = MagicMock(
            side_effect=TimeoutError("timeout")
        )
        with pytest.raises(TimeoutError):
            llm.complete([Message(role="user", content="hi")])


# ── Gemini streaming retry ────────────────────────────────────────────────────

class TestGeminiStreamingRetry:
    def _make_llm(self, on_token=None):
        from antcrew.models.gemini_model import GeminiModel
        llm = GeminiModel.__new__(GeminiModel)
        llm._model = "gemini-1.5-flash"
        llm._api_key = "test-key"
        llm.max_retries = 2
        llm.retry_delay = 0.0
        llm.retry_jitter = 0.0
        llm.timeout = 30
        llm.on_token = on_token or (lambda t: None)
        llm.current_agent = "test"
        return llm

    def _sse_line(self, text: str) -> str:
        return "data: " + _json.dumps({
            "candidates": [{"content": {"parts": [{"text": text}]}}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
        })

    def _fake_stream_ctx(self, text: str):
        sse = self._sse_line(text)
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.raise_for_status = MagicMock()
        ctx.iter_lines = MagicMock(return_value=iter([sse]))
        return ctx

    def test_stream_retries_on_connection_error(self):
        llm = self._make_llm()
        call_count = []

        def flaky(*args, **kwargs):
            call_count.append(1)
            if len(call_count) == 1:
                raise ConnectionError("refused")
            return self._fake_stream_ctx("pong")

        with patch("httpx.stream", side_effect=flaky):
            result = llm.complete([Message(role="user", content="ping")])

        assert result == "pong"
        assert len(call_count) == 2

    def test_stream_retries_on_timeout(self):
        llm = self._make_llm()
        call_count = []

        def flaky(*args, **kwargs):
            call_count.append(1)
            if len(call_count) == 1:
                raise TimeoutError("timeout")
            return self._fake_stream_ctx("ok")

        with patch("httpx.stream", side_effect=flaky):
            result = llm.complete([Message(role="user", content="hi")])

        assert result == "ok"
        assert len(call_count) == 2

    def test_stream_raises_after_max_retries(self):
        llm = self._make_llm()
        with patch("httpx.stream", side_effect=ConnectionError("refused")):
            with pytest.raises(ConnectionError):
                llm.complete([Message(role="user", content="hi")])


# ── instantiate_agent max_cost_usd from cfg ───────────────────────────────────

class TestInstantiateAgentMaxCostUsd:
    def test_max_cost_usd_from_cfg(self):
        from antcrew.agents.registry import instantiate_agent
        llm = MagicMock()
        llm._usage_log = []
        agent = instantiate_agent("doc_writer", llm, agent_cfg={"max_cost_usd": "0.25"})
        assert agent.max_cost_usd == pytest.approx(0.25)

    def test_absent_cfg_defaults_none(self):
        from antcrew.agents.registry import instantiate_agent
        llm = MagicMock()
        llm._usage_log = []
        agent = instantiate_agent("doc_writer", llm, agent_cfg={})
        assert agent.max_cost_usd is None

    def test_zero_allowed(self):
        from antcrew.agents.registry import instantiate_agent
        llm = MagicMock()
        llm._usage_log = []
        agent = instantiate_agent("pm", llm, agent_cfg={"max_cost_usd": "0"})
        assert agent.max_cost_usd == 0.0
