"""Tests for LLM adapters — no real API calls (instantiation + interface checks)."""
import os
import pytest
from unittest.mock import patch, MagicMock

from antcrew.models.base import BaseLLM, Message


# ---------------------------------------------------------------------------
# BaseLLM interface
# ---------------------------------------------------------------------------

def test_base_llm_is_abstract():
    with pytest.raises(TypeError):
        BaseLLM()  # cannot instantiate abstract class


def test_message_roles():
    for role in ("user", "assistant", "system"):
        msg = Message(role=role, content="hello")
        assert msg.role == role


def test_base_llm_system_helper():
    class _FakeLLM(BaseLLM):
        def complete(self, messages, *, max_tokens=4096):
            assert messages[0].role == "system"
            assert messages[1].role == "user"
            return "ok"

    llm = _FakeLLM()
    result = llm.system("You are helpful.", "What is 2+2?")
    assert result == "ok"


# ---------------------------------------------------------------------------
# AnthropicModel
# ---------------------------------------------------------------------------

def test_anthropic_model_instantiation():
    from antcrew.models.anthropic_model import AnthropicModel

    with patch("anthropic.Anthropic") as mock_client:
        model = AnthropicModel(model="claude-haiku-4-5", api_key="test-key")
        assert model.model == "claude-haiku-4-5"
        mock_client.assert_called_once_with(api_key="test-key")


def test_anthropic_model_complete():
    from antcrew.models.anthropic_model import AnthropicModel

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Hello!")]

    with patch("anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = mock_response
        model = AnthropicModel(api_key="test-key")
        result = model.complete([Message(role="user", content="Hi")])

    assert result == "Hello!"


# ---------------------------------------------------------------------------
# OllamaModel
# ---------------------------------------------------------------------------

def test_ollama_model_instantiation():
    from antcrew.models.ollama_model import OllamaModel

    model = OllamaModel("mistral", base_url="http://localhost:11434")
    assert model.model == "mistral"
    assert model.base_url == "http://localhost:11434"


def test_ollama_model_complete():
    import httpx
    from antcrew.models.ollama_model import OllamaModel

    mock_response = MagicMock()
    mock_response.json.return_value = {"message": {"content": "response from ollama"}}

    with patch("httpx.post", return_value=mock_response):
        model = OllamaModel("llama3")
        result = model.complete([Message(role="user", content="Hi")])

    assert result == "response from ollama"


# ---------------------------------------------------------------------------
# GroqModel
# ---------------------------------------------------------------------------

def test_groq_model_instantiation():
    from antcrew.models.groq_model import GroqModel

    with patch("antcrew.models.groq_model.Groq") as mock_groq:
        model = GroqModel("llama3-70b-8192", api_key="test-key")
        assert model.model == "llama3-70b-8192"
        mock_groq.assert_called_once_with(api_key="test-key")


def test_groq_model_complete():
    from antcrew.models.groq_model import GroqModel

    mock_choice = MagicMock()
    mock_choice.message.content = "response from groq"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with patch("antcrew.models.groq_model.Groq") as mock_groq:
        mock_groq.return_value.chat.completions.create.return_value = mock_response
        model = GroqModel(api_key="test-key")
        result = model.complete([Message(role="user", content="Hi")])

    assert result == "response from groq"


# ---------------------------------------------------------------------------
# CostLimitExceeded
# ---------------------------------------------------------------------------

def test_cost_limit_exceeded_importable():
    from antcrew.core.exceptions import CostLimitExceeded
    from antcrew import CostLimitExceeded as _top
    assert CostLimitExceeded is _top


def test_cost_limit_not_triggered_below_limit():
    class _FakeLLM(BaseLLM):
        def complete(self, messages, *, max_tokens=4096):
            self._record_usage(100, 50)
            return "ok"

    llm = _FakeLLM()
    llm.max_cost_usd = 10.0
    result = llm.system("sys", "user")
    assert result == "ok"


def test_cost_limit_raises_when_exceeded():
    from antcrew.core.exceptions import CostLimitExceeded

    class _FakeLLM(BaseLLM):
        def complete(self, messages, *, max_tokens=4096):
            self._record_usage(0, 0)
            return "ok"

    llm = _FakeLLM()
    llm.max_cost_usd = 0.001

    # Manually push cost over the limit
    llm._usage_log.append({
        "agent": "test", "model": "test",
        "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.002,
    })

    with pytest.raises(CostLimitExceeded) as exc_info:
        llm.system("sys", "user")

    assert exc_info.value.cost_usd >= exc_info.value.limit_usd
    assert exc_info.value.limit_usd == pytest.approx(0.001)


def test_cost_limit_offset_resets_per_run():
    """Second run starts with a fresh budget even if prior run accumulated cost."""
    from antcrew.core.exceptions import CostLimitExceeded
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.dev_team import DevTeam

    llm = SimulatedLLM()
    team = DevTeam(model=llm, max_cost_usd=999.0)

    # Artificially inflate usage log so it looks like prior runs spent money
    llm._usage_log.append({
        "agent": "prior", "model": "sim",
        "input_tokens": 0, "output_tokens": 0, "cost_usd": 998.0,
    })

    # Offset should be reset at start of run — so the $998 prior cost doesn't
    # count against the new $999 limit, and the run completes without raising.
    result = team.run("Build a login module")
    assert result is not None


def test_cost_limit_zero_aborts_immediately():
    """max_cost_usd=0.0 with 0-cost LLM: spent(0) >= limit(0) fires on first call."""
    from antcrew.core.exceptions import CostLimitExceeded
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.dev_team import DevTeam

    llm = SimulatedLLM()
    team = DevTeam(model=llm, max_cost_usd=0.0)

    with pytest.raises(CostLimitExceeded) as exc_info:
        team.run("Build a login module")

    assert exc_info.value.limit_usd == pytest.approx(0.0)
