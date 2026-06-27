"""Tests for TemplateAgent (v0.8.2 – v0.8.7)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from antcrew.agents.template_agent import TemplateAgent, load_template_agent, _load_cfg
from antcrew.models.simulated import SimulatedLLM


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _llm():
    return SimulatedLLM()


def _basic_cfg(**overrides) -> dict:
    cfg = {
        "name": "summarizer",
        "system_prompt": "Summarize the request in one sentence.",
    }
    cfg.update(overrides)
    return cfg


class _CaptureLLM(SimulatedLLM):
    """Captures the (system_prompt, user_msg) pair from each call."""
    calls: list[tuple[str, str]]

    def __init__(self):
        super().__init__()
        self.calls = []

    def complete(self, messages, *, max_tokens=16384, json_mode=False):
        sys_p = next((m.content for m in messages if m.role == "system"), "")
        user_p = next((m.content for m in messages if m.role == "user"), "")
        self.calls.append((sys_p, user_p))
        return super().complete(messages, max_tokens=max_tokens)


# ===========================================================================
# Config loading helpers
# ===========================================================================

def test_load_cfg_from_dict():
    cfg = {"name": "x", "system_prompt": "y"}
    assert _load_cfg(cfg) is cfg


def test_load_cfg_from_json_file(tmp_path):
    p = tmp_path / "agent.json"
    p.write_text(json.dumps({"name": "x", "system_prompt": "y"}), encoding="utf-8")
    assert _load_cfg(p)["name"] == "x"


def test_load_cfg_from_yaml_file(tmp_path):
    p = tmp_path / "agent.yaml"
    p.write_text("name: x\nsystem_prompt: 'hello'\n", encoding="utf-8")
    assert _load_cfg(p)["name"] == "x"


def test_load_cfg_from_yaml_string():
    cfg = _load_cfg("name: inline\nsystem_prompt: do stuff\n")
    assert cfg["name"] == "inline"


def test_load_cfg_from_string_path(tmp_path):
    p = tmp_path / "agent.yaml"
    p.write_text("name: file_agent\nsystem_prompt: test\n", encoding="utf-8")
    cfg = _load_cfg(str(p))
    assert cfg["name"] == "file_agent"


def test_load_cfg_wrong_type_raises():
    with pytest.raises(TypeError):
        _load_cfg(42)  # type: ignore[arg-type]


# ===========================================================================
# Validation
# ===========================================================================

def test_missing_name_raises():
    with pytest.raises(ValueError, match="name"):
        TemplateAgent({"system_prompt": "hi"}, _llm())


def test_missing_system_prompt_raises():
    with pytest.raises(ValueError, match="system_prompt"):
        TemplateAgent({"name": "x"}, _llm())


def test_empty_name_raises():
    with pytest.raises(ValueError):
        TemplateAgent({"name": "", "system_prompt": "hi"}, _llm())


def test_empty_system_prompt_raises():
    with pytest.raises(ValueError):
        TemplateAgent({"name": "x", "system_prompt": ""}, _llm())


# ===========================================================================
# Attributes
# ===========================================================================

def test_name_set_from_config():
    agent = TemplateAgent(_basic_cfg(name="my_agent"), _llm())
    assert agent.name == "my_agent"


def test_role_description_defaults_to_empty():
    agent = TemplateAgent(_basic_cfg(), _llm())
    assert agent.role_description == ""


def test_role_description_from_config():
    agent = TemplateAgent(_basic_cfg(role_description="A cool agent"), _llm())
    assert agent.role_description == "A cool agent"


def test_default_output_key():
    agent = TemplateAgent(_basic_cfg(name="foo"), _llm())
    assert agent._output_key == "foo_output"


def test_custom_output_key():
    agent = TemplateAgent(_basic_cfg(output_key="my_key"), _llm())
    assert agent._output_key == "my_key"


def test_default_input_key():
    agent = TemplateAgent(_basic_cfg(), _llm())
    assert agent._input_key == "request"


def test_custom_input_key():
    agent = TemplateAgent(_basic_cfg(input_key="prd"), _llm())
    assert agent._input_key == "prd"


# ===========================================================================
# run()
# ===========================================================================

def test_run_returns_output_key():
    agent = TemplateAgent(_basic_cfg(name="checker"), _llm())
    result = agent.run({"request": "Build login"})
    assert "checker_output" in result


def test_run_custom_output_key():
    agent = TemplateAgent(_basic_cfg(output_key="verdict"), _llm())
    result = agent.run({"request": "Build login"})
    assert "verdict" in result


def test_run_reads_request_by_default():
    llm = _CaptureLLM()
    agent = TemplateAgent(_basic_cfg(), llm)
    agent.run({"request": "Build JWT auth"})
    _, user_msg = llm.calls[0]
    assert "Build JWT auth" in user_msg


def test_run_reads_custom_input_key():
    llm = _CaptureLLM()
    agent = TemplateAgent(_basic_cfg(input_key="prd"), llm)
    agent.run({"prd": "Title: Auth\nSummary: JWT login"})
    _, user_msg = llm.calls[0]
    assert "Auth" in user_msg


def test_run_uses_system_prompt():
    llm = _CaptureLLM()
    agent = TemplateAgent(
        _basic_cfg(system_prompt="You are a triage expert."), llm
    )
    agent.run({"request": "x"})
    sys_p, _ = llm.calls[0]
    assert "triage expert" in sys_p


def test_run_missing_input_key_uses_empty_string():
    llm = _CaptureLLM()
    agent = TemplateAgent(_basic_cfg(input_key="nonexistent"), llm)
    agent.run({"request": "ignored"})
    _, user_msg = llm.calls[0]
    assert user_msg == ""


def test_run_list_input_uses_last_element():
    llm = _CaptureLLM()
    agent = TemplateAgent(_basic_cfg(input_key="messages"), llm)
    agent.run({"messages": ["first", "second", "last"]})
    _, user_msg = llm.calls[0]
    assert "last" in user_msg


def test_run_non_string_input_converted():
    llm = _CaptureLLM()
    agent = TemplateAgent(_basic_cfg(input_key="count"), llm)
    agent.run({"count": 42})
    _, user_msg = llm.calls[0]
    assert "42" in user_msg


# ===========================================================================
# max_tokens from config
# ===========================================================================

def test_max_tokens_from_config():
    agent = TemplateAgent(_basic_cfg(max_tokens=2048), _llm())
    assert agent.max_tokens == 2048


def test_max_tokens_kwarg_overrides_config():
    agent = TemplateAgent(_basic_cfg(max_tokens=2048), _llm(), max_tokens=512)
    assert agent.max_tokens == 512


# ===========================================================================
# BaseAgent features still work
# ===========================================================================

def test_preset_kwarg_accepted():
    agent = TemplateAgent(_basic_cfg(), _llm(), preset="concise")
    from antcrew.presets import CONCISE
    assert agent.preset == CONCISE


def test_system_prompt_suffix_kwarg_accepted():
    agent = TemplateAgent(_basic_cfg(), _llm(), system_prompt_suffix="Addendum.")
    assert agent.system_prompt_suffix == "Addendum."


# ===========================================================================
# load_template_agent factory
# ===========================================================================

def test_load_template_agent_from_yaml(tmp_path):
    p = tmp_path / "triage.yaml"
    p.write_text(
        "name: triage\nsystem_prompt: Triage the request.\noutput_key: triage_result\n",
        encoding="utf-8",
    )
    agent = load_template_agent(p, _llm())
    assert agent.name == "triage"
    assert agent._output_key == "triage_result"


def test_load_template_agent_run(tmp_path):
    p = tmp_path / "agent.yaml"
    p.write_text("name: echo\nsystem_prompt: Repeat the request.\n", encoding="utf-8")
    agent = load_template_agent(p, _llm())
    result = agent.run({"request": "hello"})
    assert "echo_output" in result


# ===========================================================================
# config.py integration
# ===========================================================================

def test_config_inline_template_agent(tmp_path):
    """Inline system_prompt in YAML config creates a TemplateAgent."""
    import yaml  # type: ignore[import]
    from antcrew.config import load_context

    yaml_content = {
        "team": "dev",
        "model": "simulated",
        "agents": {
            "security_reviewer": {
                "system_prompt": "You are a security reviewer.",
                "output_key": "security_review",
            }
        },
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(yaml_content), encoding="utf-8")

    ctx = load_context(p)
    # The team is constructed; if no error, the template agent was created OK
    assert ctx.team is not None


# ===========================================================================
# System prompt interpolation — _interpolate helper
# ===========================================================================

def test_interpolate_replaces_known_key():
    from antcrew.agents.template_agent import _interpolate
    result = _interpolate("Hello {name}!", {"name": "world"})
    assert result == "Hello world!"


def test_interpolate_leaves_unknown_key_unchanged():
    from antcrew.agents.template_agent import _interpolate
    result = _interpolate("Plan: {plan}", {})
    assert result == "Plan: {plan}"


def test_interpolate_leaves_none_value_unchanged():
    from antcrew.agents.template_agent import _interpolate
    result = _interpolate("Plan: {plan}", {"plan": None})
    assert result == "Plan: {plan}"


def test_interpolate_multiple_keys():
    from antcrew.agents.template_agent import _interpolate
    result = _interpolate("{a} and {b}", {"a": "foo", "b": "bar"})
    assert result == "foo and bar"


def test_interpolate_leaves_json_syntax_unchanged():
    from antcrew.agents.template_agent import _interpolate
    template = 'Return JSON: {"key": "value"}'
    assert _interpolate(template, {}) == template


def test_interpolate_leaves_positional_unchanged():
    from antcrew.agents.template_agent import _interpolate
    assert _interpolate("Value: {0}", {}) == "Value: {0}"


def test_interpolate_leaves_format_spec_unchanged():
    from antcrew.agents.template_agent import _interpolate
    assert _interpolate("{:.2f}", {}) == "{:.2f}"


def test_interpolate_converts_non_string_to_str():
    from antcrew.agents.template_agent import _interpolate
    result = _interpolate("Count: {n}", {"n": 42})
    assert result == "Count: 42"


def test_interpolate_no_op_on_plain_text():
    from antcrew.agents.template_agent import _interpolate
    text = "No placeholders here."
    assert _interpolate(text, {"plan": "something"}) == text


# ===========================================================================
# System prompt interpolation — TemplateAgent behaviour
# ===========================================================================

def test_interpolation_enabled_by_default():
    agent = TemplateAgent(_basic_cfg(system_prompt="Review: {request}"), _llm())
    assert agent._interpolate is True


def test_interpolate_false_from_config():
    agent = TemplateAgent(_basic_cfg(system_prompt="Hello.", interpolate=False), _llm())
    assert agent._interpolate is False


def test_run_interpolates_system_prompt():
    llm = _CaptureLLM()
    agent = TemplateAgent(
        _basic_cfg(system_prompt="Review the code: {code}"),
        llm,
    )
    agent.run({"request": "task", "code": "def foo(): pass"})
    sys_p, _ = llm.calls[0]
    assert "def foo(): pass" in sys_p
    assert "{code}" not in sys_p


def test_run_unknown_placeholder_left_unchanged():
    llm = _CaptureLLM()
    agent = TemplateAgent(
        _basic_cfg(system_prompt="Context: {missing_key}"),
        llm,
    )
    agent.run({"request": "task"})
    sys_p, _ = llm.calls[0]
    assert "{missing_key}" in sys_p


def test_run_interpolate_false_leaves_prompt_unchanged():
    llm = _CaptureLLM()
    agent = TemplateAgent(
        _basic_cfg(system_prompt="Review: {code}", interpolate=False),
        llm,
    )
    agent.run({"request": "task", "code": "def foo(): pass"})
    sys_p, _ = llm.calls[0]
    assert "{code}" in sys_p
    assert "def foo(): pass" not in sys_p


def test_run_interpolates_multiple_keys():
    llm = _CaptureLLM()
    agent = TemplateAgent(
        _basic_cfg(system_prompt="Plan: {plan}\nCode: {code}"),
        llm,
    )
    agent.run({"request": "task", "plan": "step 1", "code": "x = 1"})
    sys_p, _ = llm.calls[0]
    assert "step 1" in sys_p
    assert "x = 1" in sys_p


def test_run_interpolate_none_value_unchanged():
    llm = _CaptureLLM()
    agent = TemplateAgent(
        _basic_cfg(system_prompt="Plan: {plan}"),
        llm,
    )
    agent.run({"request": "task", "plan": None})
    sys_p, _ = llm.calls[0]
    assert "{plan}" in sys_p


def test_run_interpolate_and_input_key_both_work():
    """Interpolation on system prompt + input_key for user message are independent."""
    llm = _CaptureLLM()
    agent = TemplateAgent(
        _basic_cfg(
            system_prompt="Context: {context}",
            input_key="query",
        ),
        llm,
    )
    agent.run({"query": "user question", "context": "background info"})
    sys_p, user_p = llm.calls[0]
    assert "background info" in sys_p
    assert "user question" in user_p


def test_interpolation_in_custom_team_pipeline():
    """Interpolation must work end-to-end through a CustomTeam."""
    from antcrew.testing import SequencedLLM
    from antcrew.teams.custom_team import CustomTeam

    llm = SequencedLLM(["step1 output", "review done"])
    steps = [
        {"name": "producer", "system_prompt": "Produce output.", "output_key": "produced"},
        {"name": "consumer",
         "system_prompt": "Consume this: {produced}",
         "output_key": "review"},
    ]
    team = CustomTeam(steps, llm)
    result = team.run("task")
    assert result["produced"] == "step1 output"
    assert result["review"] == "review done"
