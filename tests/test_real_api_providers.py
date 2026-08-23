"""@pytest.mark.real_api — integration tests against live LLM providers.

These tests are skipped automatically in CI unless a provider API key is set
(ANTHROPIC_API_KEY, OPENAI_API_KEY, or GROQ_API_KEY).

Run manually with:
    ANTHROPIC_API_KEY=sk-ant-... pytest -m real_api -v
"""
from __future__ import annotations

import os

import pytest


def _anthropic_model():
    from antcrew.models.anthropic_model import AnthropicModel
    return AnthropicModel(model="claude-haiku-4-5-20251001")


def _openai_model():
    from antcrew.models.openai_model import OpenAIModel
    return OpenAIModel(model="gpt-4o-mini")


def _groq_model():
    from antcrew.models.groq_model import GroqModel
    return GroqModel(model="llama-3.3-70b-versatile")


@pytest.mark.real_api
def test_anthropic_simple_completion():
    """Anthropic: single-turn completion returns non-empty text."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    model = _anthropic_model()
    result = model.complete("Say the word 'antcrew' and nothing else.")
    assert isinstance(result, str)
    assert result.strip()
    assert "antcrew" in result.lower()


@pytest.mark.real_api
def test_openai_simple_completion():
    """OpenAI: single-turn completion returns non-empty text."""
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    model = _openai_model()
    result = model.complete("Say the word 'antcrew' and nothing else.")
    assert isinstance(result, str)
    assert result.strip()
    assert "antcrew" in result.lower()


@pytest.mark.real_api
def test_groq_simple_completion():
    """Groq: single-turn completion returns non-empty text."""
    if not os.environ.get("GROQ_API_KEY"):
        pytest.skip("GROQ_API_KEY not set")
    model = _groq_model()
    result = model.complete("Say the word 'antcrew' and nothing else.")
    assert isinstance(result, str)
    assert result.strip()
    assert "antcrew" in result.lower()


@pytest.mark.real_api
def test_anthropic_usage_tracking():
    """Anthropic: token usage is tracked after a real call."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    model = _anthropic_model()
    model.complete("What is 2 + 2?")
    summary = model.get_usage_summary()
    assert summary.get("total_input_tokens", 0) > 0
    assert summary.get("total_output_tokens", 0) > 0


@pytest.mark.real_api
def test_anthropic_json_output():
    """Anthropic: model can be prompted to return parseable JSON."""
    import json
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    model = _anthropic_model()
    result = model.complete(
        'Reply with ONLY a JSON object, no prose: {"answer": 42}'
    )
    assert isinstance(result, str)
    parsed = json.loads(result)
    assert parsed.get("answer") == 42
