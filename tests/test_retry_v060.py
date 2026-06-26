"""Tests for v0.6.0 rate-limit retry improvements.

Covers:
- _retry_delay_for: jitter, cap, Retry-After header
- _with_retry: warning log emission, max_retry_delay cap
- Non-retryable errors still propagate immediately
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from antcrew.models.base import BaseLLM, _is_retryable
from antcrew.models.simulated import SimulatedLLM


# ===========================================================================
# _retry_delay_for — jitter
# ===========================================================================

def test_retry_delay_for_includes_jitter():
    llm = SimulatedLLM()
    llm.retry_delay = 1.0
    llm.retry_jitter = 0.5
    llm.max_retry_delay = 60.0

    exc = TimeoutError("timeout")
    delays = [llm._retry_delay_for(0, exc) for _ in range(30)]

    # All delays should be in [base, base + jitter]
    for d in delays:
        assert 1.0 <= d <= 1.5 + 1e-9, f"Unexpected delay: {d}"

    # With 30 samples, jitter should produce some variation
    assert max(delays) - min(delays) > 0.0


def test_retry_delay_for_no_jitter():
    llm = SimulatedLLM()
    llm.retry_delay = 2.0
    llm.retry_jitter = 0.0
    llm.max_retry_delay = 60.0

    exc = ConnectionError("refused")
    delay = llm._retry_delay_for(0, exc)
    assert delay == pytest.approx(2.0, abs=1e-9)


def test_retry_delay_for_exponential_backoff():
    llm = SimulatedLLM()
    llm.retry_delay = 1.0
    llm.retry_jitter = 0.0
    llm.max_retry_delay = 60.0

    exc = TimeoutError()
    assert llm._retry_delay_for(0, exc) == pytest.approx(1.0)
    assert llm._retry_delay_for(1, exc) == pytest.approx(2.0)
    assert llm._retry_delay_for(2, exc) == pytest.approx(4.0)
    assert llm._retry_delay_for(3, exc) == pytest.approx(8.0)


# ===========================================================================
# _retry_delay_for — max_retry_delay cap
# ===========================================================================

def test_retry_delay_capped_at_max_retry_delay():
    llm = SimulatedLLM()
    llm.retry_delay = 1.0
    llm.retry_jitter = 0.0
    llm.max_retry_delay = 10.0

    exc = TimeoutError()
    # attempt=10 → raw = 1.0 * 2^10 = 1024, should be capped at 10
    delay = llm._retry_delay_for(10, exc)
    assert delay == pytest.approx(10.0)


def test_retry_delay_cap_default_is_60():
    llm = SimulatedLLM()
    assert llm.max_retry_delay == 60.0


# ===========================================================================
# _retry_delay_for — Retry-After header
# ===========================================================================

def _make_exc_with_retry_after(value: str | None) -> Exception:
    exc = Exception("rate limited")
    resp = MagicMock()
    resp.headers = {"Retry-After": value} if value is not None else {}
    exc.response = resp
    return exc


def test_retry_delay_uses_retry_after_header():
    llm = SimulatedLLM()
    llm.retry_delay = 1.0
    llm.retry_jitter = 0.5
    llm.max_retry_delay = 60.0

    exc = _make_exc_with_retry_after("30")
    delay = llm._retry_delay_for(0, exc)
    assert delay == pytest.approx(30.0)


def test_retry_delay_retry_after_lowercase_key():
    llm = SimulatedLLM()
    llm.retry_delay = 1.0
    llm.retry_jitter = 0.0
    llm.max_retry_delay = 60.0

    exc = Exception("rate limited")
    resp = MagicMock()
    resp.headers = {"retry-after": "45"}
    exc.response = resp
    delay = llm._retry_delay_for(0, exc)
    assert delay == pytest.approx(45.0)


def test_retry_delay_bad_retry_after_falls_back_to_backoff():
    llm = SimulatedLLM()
    llm.retry_delay = 2.0
    llm.retry_jitter = 0.0
    llm.max_retry_delay = 60.0

    exc = _make_exc_with_retry_after("not-a-number")
    delay = llm._retry_delay_for(0, exc)
    assert delay == pytest.approx(2.0)


def test_retry_delay_no_response_on_exc():
    llm = SimulatedLLM()
    llm.retry_delay = 3.0
    llm.retry_jitter = 0.0
    llm.max_retry_delay = 60.0

    exc = TimeoutError("no response attribute")
    delay = llm._retry_delay_for(0, exc)
    assert delay == pytest.approx(3.0)


def test_retry_delay_retry_after_ignored_if_none():
    """Missing Retry-After key should fall back to computed delay."""
    llm = SimulatedLLM()
    llm.retry_delay = 1.0
    llm.retry_jitter = 0.0
    llm.max_retry_delay = 60.0

    exc = _make_exc_with_retry_after(None)
    delay = llm._retry_delay_for(1, exc)
    assert delay == pytest.approx(2.0)


# ===========================================================================
# _with_retry — warning log
# ===========================================================================

def test_with_retry_logs_warning_on_retry(caplog):
    llm = SimulatedLLM()
    llm.max_retries = 1
    llm.retry_delay = 0.0
    llm.retry_jitter = 0.0
    llm.max_retry_delay = 60.0
    llm.current_agent = "backend_dev"

    call_n = [0]

    def fn():
        call_n[0] += 1
        if call_n[0] == 1:
            raise TimeoutError("blip")
        return "ok"

    with caplog.at_level(logging.WARNING, logger="antcrew.models.base"):
        with patch("time.sleep"):
            llm._with_retry(fn)

    assert any("llm_retry" in r.message for r in caplog.records)
    warning = next(r for r in caplog.records if "llm_retry" in r.message)
    assert "attempt=1/1" in warning.message
    assert "backend_dev" in warning.message


def test_with_retry_logs_agent_name(caplog):
    llm = SimulatedLLM()
    llm.max_retries = 1
    llm.retry_delay = 0.0
    llm.retry_jitter = 0.0
    llm.current_agent = "qa_engineer"

    attempt_n = [0]

    def fn():
        attempt_n[0] += 1
        if attempt_n[0] < 2:
            raise ConnectionError("refused")
        return "ok"

    with caplog.at_level(logging.WARNING, logger="antcrew.models.base"):
        with patch("time.sleep"):
            llm._with_retry(fn)

    assert any("qa_engineer" in r.message for r in caplog.records)


def test_with_retry_no_log_on_non_retryable(caplog):
    llm = SimulatedLLM()
    llm.max_retries = 3
    llm.retry_delay = 0.0

    def fn():
        raise ValueError("bad input")

    with caplog.at_level(logging.WARNING, logger="antcrew.models.base"):
        with pytest.raises(ValueError):
            llm._with_retry(fn)

    assert not any("llm_retry" in r.message for r in caplog.records)


def test_with_retry_no_log_on_first_success(caplog):
    llm = SimulatedLLM()
    llm.max_retries = 3
    llm.retry_delay = 0.0

    with caplog.at_level(logging.WARNING, logger="antcrew.models.base"):
        result = llm._with_retry(lambda: "immediate")

    assert result == "immediate"
    assert not any("llm_retry" in r.message for r in caplog.records)


# ===========================================================================
# _with_retry — delay is actually called (no real sleep)
# ===========================================================================

def test_with_retry_calls_sleep_with_computed_delay():
    llm = SimulatedLLM()
    llm.max_retries = 2
    llm.retry_delay = 5.0
    llm.retry_jitter = 0.0
    llm.max_retry_delay = 60.0

    call_n = [0]

    def fn():
        call_n[0] += 1
        if call_n[0] < 3:
            raise TimeoutError("retry me")
        return "done"

    sleep_calls: list[float] = []

    with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        result = llm._with_retry(fn)

    assert result == "done"
    assert len(sleep_calls) == 2
    assert sleep_calls[0] == pytest.approx(5.0)   # attempt 0 → 5 * 2^0
    assert sleep_calls[1] == pytest.approx(10.0)  # attempt 1 → 5 * 2^1


# ===========================================================================
# max_retry_delay attribute default
# ===========================================================================

def test_base_llm_default_attributes():
    llm = SimulatedLLM()
    assert llm.max_retry_delay == 60.0
    assert llm.retry_jitter == 0.5
    assert llm.max_retries == 3
    assert llm.retry_delay == 1.0
