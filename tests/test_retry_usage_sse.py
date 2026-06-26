"""Tests for retry/timeout, token tracking, and SSE streaming."""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, call, patch

import pytest

from antcrew.models.base import BaseLLM, Message, _is_retryable
from antcrew.models.simulated import SimulatedLLM


# ===========================================================================
# _is_retryable
# ===========================================================================

def test_is_retryable_timeout_error():
    assert _is_retryable(TimeoutError("timeout"))


def test_is_retryable_connection_error():
    assert _is_retryable(ConnectionError("refused"))


def test_is_retryable_429():
    exc = Exception("rate limit")
    exc.status_code = 429
    assert _is_retryable(exc)


def test_is_retryable_500():
    exc = Exception("server error")
    exc.status_code = 500
    assert _is_retryable(exc)


def test_not_retryable_400():
    exc = Exception("bad request")
    exc.status_code = 400
    assert not _is_retryable(exc)


def test_not_retryable_401():
    exc = Exception("unauthorized")
    exc.status_code = 401
    assert not _is_retryable(exc)


def test_not_retryable_value_error():
    assert not _is_retryable(ValueError("bad value"))


# ===========================================================================
# BaseLLM._with_retry
# ===========================================================================

def test_with_retry_succeeds_first_try():
    llm = SimulatedLLM()
    llm.max_retries = 3
    llm.retry_delay = 0.0

    calls = [0]

    def fn():
        calls[0] += 1
        return "ok"

    result = llm._with_retry(fn)
    assert result == "ok"
    assert calls[0] == 1


def test_with_retry_retries_on_transient_error():
    llm = SimulatedLLM()
    llm.max_retries = 2
    llm.retry_delay = 0.0

    attempts = [0]

    def fn():
        attempts[0] += 1
        if attempts[0] < 3:
            raise TimeoutError("temporary")
        return "ok"

    result = llm._with_retry(fn)
    assert result == "ok"
    assert attempts[0] == 3


def test_with_retry_raises_after_max_retries():
    llm = SimulatedLLM()
    llm.max_retries = 2
    llm.retry_delay = 0.0

    def fn():
        raise TimeoutError("always fails")

    with pytest.raises(TimeoutError, match="always fails"):
        llm._with_retry(fn)


def test_with_retry_does_not_retry_non_transient():
    llm = SimulatedLLM()
    llm.max_retries = 3
    llm.retry_delay = 0.0

    attempts = [0]

    def fn():
        attempts[0] += 1
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        llm._with_retry(fn)

    assert attempts[0] == 1  # no retry


def test_system_uses_retry_when_no_streaming():
    """BaseLLM.system() wraps complete() in retry when on_token is None."""
    llm = SimulatedLLM()
    llm.max_retries = 1
    llm.retry_delay = 0.0
    llm.on_token = None

    attempts = [0]
    orig_complete = llm.complete

    def flaky_complete(messages, *, max_tokens=4096):
        attempts[0] += 1
        if attempts[0] == 1:
            raise TimeoutError("first call fails")
        return orig_complete(messages, max_tokens=max_tokens)

    llm.complete = flaky_complete
    result = llm.system("You are a BA.", "Build X")
    assert attempts[0] == 2
    assert isinstance(result, str)


def test_system_retries_when_streaming():
    """When on_token is set, transient failures are still retried."""
    llm = SimulatedLLM()
    llm.max_retries = 2
    llm.retry_delay = 0.0
    llm.on_token = lambda t: None

    attempts = [0]
    orig_complete = llm.complete

    def flaky(messages, *, max_tokens=4096):
        attempts[0] += 1
        if attempts[0] == 1:
            raise TimeoutError("streaming blip")
        return orig_complete(messages, max_tokens=max_tokens)

    llm.complete = flaky
    result = llm.system("sys", "user")
    assert attempts[0] == 2
    assert isinstance(result, str)


def test_system_streaming_disables_on_token_for_retries():
    """After the first streaming failure on_token is cleared for retry calls."""
    llm = SimulatedLLM()
    llm.max_retries = 1
    llm.retry_delay = 0.0
    tokens_during_retry: list[str | None] = []
    llm.on_token = lambda t: None

    attempts = [0]
    orig_complete = llm.complete

    def spy_complete(messages, *, max_tokens=4096):
        attempts[0] += 1
        if attempts[0] == 1:
            raise TimeoutError("first fails")
        # Record on_token state during the retry call
        tokens_during_retry.append(llm.on_token)
        return orig_complete(messages, max_tokens=max_tokens)

    llm.complete = spy_complete
    llm.system("sys", "user")
    assert tokens_during_retry == [None]  # on_token disabled for retry


# ===========================================================================
# Token usage tracking
# ===========================================================================

def test_simulated_llm_records_usage():
    llm = SimulatedLLM()
    llm.complete([Message(role="user", content="hi")])
    summary = llm.get_usage_summary()
    assert summary["total_input_tokens"] >= 0
    assert summary["total_output_tokens"] >= 0
    assert len(summary["by_agent"]) == 1


def test_usage_log_accumulates_across_calls():
    llm = SimulatedLLM()
    for _ in range(3):
        llm.complete([Message(role="user", content="hi")])
    summary = llm.get_usage_summary()
    assert len(summary["by_agent"]) == 3
    assert summary["total_input_tokens"] == sum(e["input_tokens"] for e in summary["by_agent"])


def test_usage_log_per_instance():
    """Each LLM instance has its own usage log."""
    llm_a = SimulatedLLM()
    llm_b = SimulatedLLM()
    llm_a.complete([Message(role="user", content="hi")])
    assert len(llm_a._usage_log) == 1
    assert len(llm_b._usage_log) == 0


def test_usage_summary_empty_when_no_calls():
    llm = SimulatedLLM()
    summary = llm.get_usage_summary()
    assert summary == {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost_usd": 0.0,
        "by_agent": [],
    }


def test_usage_records_current_agent():
    llm = SimulatedLLM()
    llm.current_agent = "backend_dev"
    llm.complete([Message(role="user", content="build something")])
    assert llm._usage_log[0]["agent"] == "backend_dev"


def test_estimate_cost_claude_sonnet():
    llm = SimulatedLLM()
    llm.__dict__["model"] = "claude-sonnet-4-6"
    cost = llm._estimate_cost(1_000_000, 1_000_000)
    assert cost == pytest.approx(3.0 + 15.0)


def test_estimate_cost_unknown_model():
    llm = SimulatedLLM()
    llm.__dict__["model"] = "some-unknown-model"
    cost = llm._estimate_cost(1_000_000, 1_000_000)
    assert cost == 0.0


def test_anthropic_model_records_usage():
    from antcrew.models.anthropic_model import AnthropicModel

    llm = AnthropicModel.__new__(AnthropicModel)
    llm.on_token = None
    llm.current_agent = "pm"
    llm.model = "claude-sonnet-4-6"
    llm.timeout = 120.0

    mock_usage = MagicMock()
    mock_usage.input_tokens = 100
    mock_usage.output_tokens = 200
    mock_usage.cache_creation_input_tokens = 0
    mock_usage.cache_read_input_tokens = 0

    mock_response = MagicMock()
    mock_response.content[0].text = "result"
    mock_response.usage = mock_usage

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response
    llm._client = mock_client

    llm.complete([Message(role="user", content="hi")])

    assert llm._usage_log[0]["input_tokens"] == 100
    assert llm._usage_log[0]["output_tokens"] == 200
    assert llm._usage_log[0]["agent"] == "pm"


def test_ollama_model_records_usage():
    from antcrew.models.ollama_model import OllamaModel

    llm = OllamaModel()
    llm.current_agent = "backend_dev"

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "message": {"content": "hello"},
        "prompt_eval_count": 50,
        "eval_count": 30,
    }

    with patch("httpx.post", return_value=mock_resp):
        llm.complete([Message(role="user", content="hi")])

    assert llm._usage_log[0]["input_tokens"] == 50
    assert llm._usage_log[0]["output_tokens"] == 30


# ===========================================================================
# SSE server endpoint
# ===========================================================================

def test_sse_endpoint_streams_tokens_and_done():
    """GET /run/{id}/stream yields token events then a done event."""
    from fastapi.testclient import TestClient
    from antcrew.server import app, _runs

    client = TestClient(app)

    # Pre-populate a completed run with buffered events
    run_id = "test-sse-001"
    state_payload = {"code_artifacts": []}
    _runs[run_id] = {
        "status": "done",
        "request": "build X",
        "team": "dev",
        "state": state_payload,
        "error": None,
        "events": [
            ("token", "business_analyst", "Hello"),
            ("token", "business_analyst", " world"),
            ("done", None, json.dumps({"state": state_payload, "usage": {}})),
        ],
        "usage": {},
    }

    with client.stream("GET", f"/run/{run_id}/stream") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        lines = list(resp.iter_lines())

    # Parse events from lines
    data_lines = [l for l in lines if l.startswith("data:")]
    assert len(data_lines) >= 2  # at least 2 tokens

    first = json.loads(data_lines[0][len("data:"):].strip())
    assert first["token"] == "Hello"
    assert first["agent"] == "business_analyst"

    del _runs[run_id]


def test_sse_endpoint_404_for_unknown_run():
    from fastapi.testclient import TestClient
    from antcrew.server import app

    client = TestClient(app)
    resp = client.get("/run/does-not-exist/stream")
    assert resp.status_code == 404


def test_run_record_has_events_key():
    """POST /run creates a run record with an events list."""
    from fastapi.testclient import TestClient
    from antcrew.server import app, _runs

    client = TestClient(app)
    resp = client.post("/run", json={"request": "test", "team": "dev", "model": "simulated"})
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    assert run_id in _runs
    assert "events" in _runs[run_id]
    assert isinstance(_runs[run_id]["events"], list)

    del _runs[run_id]


def test_get_run_includes_usage_when_done():
    """GET /run/{id} includes usage field when the run is done."""
    from fastapi.testclient import TestClient
    from antcrew.server import app, _runs

    client = TestClient(app)
    run_id = "test-usage-001"
    _runs[run_id] = {
        "status": "done",
        "request": "build X",
        "team": "dev",
        "state": {},
        "error": None,
        "events": [],
        "usage": {"total_input_tokens": 100, "total_output_tokens": 50},
    }

    resp = client.get(f"/run/{run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["usage"]["total_input_tokens"] == 100

    del _runs[run_id]
