"""Tests for BaseAgent.system_parsed() — parse+validate, log on failure, re-raise."""
from __future__ import annotations

import json
import logging

import pytest
from pydantic import BaseModel, ValidationError

from antcrew.agents.business import BusinessAnalystAgent
from antcrew.testing.llms import SequencedLLM


class _Simple(BaseModel):
    title: str
    count: int


def _agent(responses: list[str]) -> BusinessAnalystAgent:
    return BusinessAnalystAgent(SequencedLLM(responses))


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------

def test_system_parsed_returns_validated_model():
    payload = json.dumps({"title": "hello", "count": 3})
    agent = _agent([payload])
    result = agent.system_parsed("sys", "user", _Simple)
    assert isinstance(result, _Simple)
    assert result.title == "hello"
    assert result.count == 3


def test_system_parsed_handles_fenced_json():
    payload = "```json\n" + json.dumps({"title": "fenced", "count": 7}) + "\n```"
    agent = _agent([payload])
    result = agent.system_parsed("sys", "user", _Simple)
    assert result.title == "fenced"


def test_system_parsed_handles_generic_schema():
    payload = json.dumps([
        {"title": "a", "count": 1},
        {"title": "b", "count": 2},
    ])
    agent = _agent([payload])
    result = agent.system_parsed("sys", "user", list[_Simple])
    assert len(result) == 2
    assert all(isinstance(item, _Simple) for item in result)


def test_system_parsed_forwards_kwargs_to_system():
    payload = json.dumps({"title": "hi", "count": 1})
    llm = SequencedLLM([payload])
    agent = BusinessAnalystAgent(llm)
    agent.system_parsed("sys", "user", _Simple, max_tokens=1234)
    assert llm.last_max_tokens == 1234


# ---------------------------------------------------------------------------
# Failure paths — log warning + re-raise
# ---------------------------------------------------------------------------

def test_system_parsed_raises_on_invalid_json(caplog):
    agent = _agent(["this is not json"])
    with caplog.at_level(logging.WARNING, logger="antcrew.core.agent"):
        with pytest.raises(ValueError):
            agent.system_parsed("sys", "user", _Simple)
    assert "parse_failure" in caplog.text
    assert "business_analyst" in caplog.text


def test_system_parsed_raises_on_schema_mismatch(caplog):
    payload = json.dumps({"wrong_field": "x"})
    agent = _agent([payload])
    with caplog.at_level(logging.WARNING, logger="antcrew.core.agent"):
        with pytest.raises(ValidationError):
            agent.system_parsed("sys", "user", _Simple)
    assert "parse_failure" in caplog.text


# ---------------------------------------------------------------------------
# Retry-with-hint (max_retries > 0)
# ---------------------------------------------------------------------------

def test_system_parsed_retries_on_bad_json():
    """First response is bad JSON; second is valid — should succeed on retry."""
    good = json.dumps({"title": "ok", "count": 1})
    agent = _agent(["not json at all", good])
    result = agent.system_parsed("sys", "user", _Simple, max_retries=1)
    assert result.title == "ok"
    assert agent.llm.call_count == 2


def test_system_parsed_retries_on_schema_mismatch():
    """First response has wrong fields; second is valid."""
    bad = json.dumps({"wrong": "field"})
    good = json.dumps({"title": "retry", "count": 5})
    agent = _agent([bad, good])
    result = agent.system_parsed("sys", "user", _Simple, max_retries=1)
    assert result.count == 5
    assert agent.llm.call_count == 2


def test_system_parsed_exhausts_retries_and_raises(caplog):
    """All attempts return bad JSON — raises after max_retries and logs warning."""
    agent = _agent(["bad", "also bad", "still bad"])
    with caplog.at_level(logging.WARNING, logger="antcrew.core.agent"):
        with pytest.raises(ValueError):
            agent.system_parsed("sys", "user", _Simple, max_retries=2)
    assert "parse_failure" in caplog.text
    assert agent.llm.call_count == 3


def test_system_parsed_zero_retries_raises_immediately(caplog):
    """max_retries=0 (default) — no retry, raises on first failure."""
    agent = _agent(["bad json"])
    with caplog.at_level(logging.WARNING, logger="antcrew.core.agent"):
        with pytest.raises(ValueError):
            agent.system_parsed("sys", "user", _Simple, max_retries=0)
    assert agent.llm.call_count == 1


def test_system_parsed_succeeds_without_retry():
    """With max_retries=3 but valid first response — only one LLM call made."""
    payload = json.dumps({"title": "first-try", "count": 9})
    agent = _agent([payload])
    result = agent.system_parsed("sys", "user", _Simple, max_retries=3)
    assert result.title == "first-try"
    assert agent.llm.call_count == 1
