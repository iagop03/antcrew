"""Tests for TemplateAgent (v0.8.2 – v0.8.7)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from antcrew.agents.template_agent import TemplateAgent, load_template_agent, _load_cfg
from antcrew.models.simulated import SimulatedLLM
from antcrew.testing import SequencedLLM


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


# ===========================================================================
# JSON output mode — output_json / output_parse_retries
# ===========================================================================

def test_output_json_default_false():
    agent = TemplateAgent(_basic_cfg(), _llm())
    assert agent._output_json is False


def test_output_json_true_from_config():
    agent = TemplateAgent(_basic_cfg(output_json=True), _llm())
    assert agent._output_json is True


def test_output_parse_retries_default_zero():
    agent = TemplateAgent(_basic_cfg(), _llm())
    assert agent._output_parse_retries == 0


def test_output_parse_retries_from_config():
    agent = TemplateAgent(_basic_cfg(output_parse_retries=3), _llm())
    assert agent._output_parse_retries == 3


def test_run_output_json_returns_dict():
    """output_json:true → run() stores a parsed dict in state."""
    llm = SequencedLLM(['{"steps": ["a", "b"], "hours": 4}'])
    agent = TemplateAgent(_basic_cfg(output_json=True, output_key="plan"), llm)
    out = agent.run({"request": "plan task"})
    assert out["plan"] == {"steps": ["a", "b"], "hours": 4}


def test_run_output_json_false_returns_string():
    """Default mode (output_json omitted) still returns a plain string."""
    llm = SequencedLLM(['{"key": "value"}'])
    agent = TemplateAgent(_basic_cfg(output_key="raw"), llm)
    out = agent.run({"request": "task"})
    assert isinstance(out["raw"], str)


def test_run_output_json_invalid_raises():
    """Bad JSON with no retries must raise a parsing error."""
    import pytest
    llm = SequencedLLM(["not valid json"])
    agent = TemplateAgent(_basic_cfg(output_json=True), llm)
    with pytest.raises(Exception):
        agent.run({"request": "task"})


def test_run_output_json_with_retries_recovers():
    """With output_parse_retries>0, the agent retries and recovers."""
    llm = SequencedLLM(["bad json", '{"ok": true}'])
    agent = TemplateAgent(
        _basic_cfg(output_json=True, output_parse_retries=1, output_key="result"),
        llm,
    )
    out = agent.run({"request": "task"})
    assert out["result"] == {"ok": True}


def test_output_json_in_custom_team_pipeline():
    """Structured output from one step is available to the next in state."""
    from antcrew.teams.custom_team import CustomTeam

    planner_out = '{"steps": ["build", "test"], "hours": 2}'
    llm = SequencedLLM([planner_out, "done"])
    steps = [
        {"name": "planner", "system_prompt": "Plan.", "output_key": "plan", "output_json": True},
        {"name": "executor", "system_prompt": "Execute.", "output_key": "result"},
    ]
    team = CustomTeam(steps, llm)
    result = team.run("task")
    assert isinstance(result["plan"], dict)
    assert result["plan"]["hours"] == 2
    assert result["result"] == "done"


# ===========================================================================
# system_prompt_file
# ===========================================================================

def test_system_prompt_file_loads_content(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("You are a helpful assistant.", encoding="utf-8")
    cfg = {"name": "a", "system_prompt_file": str(prompt_file)}
    agent = TemplateAgent(cfg, _llm())
    assert agent._system_prompt == "You are a helpful assistant."


def test_system_prompt_file_relative_to_config_file(tmp_path):
    prompt_file = tmp_path / "prompts" / "agent.md"
    prompt_file.parent.mkdir()
    prompt_file.write_text("Prompt from file.", encoding="utf-8")
    cfg_file = tmp_path / "team.yaml"
    cfg_file.write_text(
        f"name: a\nsystem_prompt_file: prompts/agent.md\n",
        encoding="utf-8"
    )
    agent = TemplateAgent(cfg_file, _llm())
    assert agent._system_prompt == "Prompt from file."


def test_system_prompt_file_missing_raises():
    cfg = {"name": "a", "system_prompt_file": "/nonexistent/prompt.md"}
    with pytest.raises(FileNotFoundError, match="system_prompt_file"):
        TemplateAgent(cfg, _llm())


def test_system_prompt_and_system_prompt_file_mutual_exclusive():
    cfg = {
        "name": "a",
        "system_prompt": "Inline prompt.",
        "system_prompt_file": "/some/file.md",
    }
    with pytest.raises(ValueError, match="not both"):
        TemplateAgent(cfg, _llm())


def test_system_prompt_file_used_in_run(tmp_path):
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("Do the task.", encoding="utf-8")
    llm = _CaptureLLM()
    agent = TemplateAgent({"name": "a", "system_prompt_file": str(prompt_file)}, llm)
    agent.run({"request": "go"})
    sys_p, _ = llm.calls[0]
    assert "Do the task." in sys_p


def test_system_prompt_file_with_interpolation(tmp_path):
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("Plan: {plan}", encoding="utf-8")
    llm = _CaptureLLM()
    agent = TemplateAgent({"name": "a", "system_prompt_file": str(prompt_file)}, llm)
    agent.run({"request": "go", "plan": "build it"})
    sys_p, _ = llm.calls[0]
    assert "build it" in sys_p


def test_system_prompt_file_in_custom_team(tmp_path):
    from antcrew.teams.custom_team import CustomTeam
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("Execute the task.", encoding="utf-8")
    llm = SequencedLLM(["done"])
    steps = [
        {"name": "a", "system_prompt_file": str(prompt_file), "output_key": "out"},
    ]
    team = CustomTeam(steps, llm)
    result = team.run("task")
    assert result["out"] == "done"


# ===========================================================================
# save_output
# ===========================================================================

def test_save_output_default_none():
    agent = TemplateAgent(_basic_cfg(), _llm())
    assert agent._save_output is None


def test_save_output_stored_as_path():
    agent = TemplateAgent(_basic_cfg(save_output="out/result.md"), _llm())
    from pathlib import Path
    assert agent._save_output == Path("out/result.md")


def test_save_output_writes_file(tmp_path):
    out_file = tmp_path / "result.txt"
    llm = SequencedLLM(["hello world"])
    agent = TemplateAgent(
        _basic_cfg(save_output=str(out_file), output_key="out"),
        llm,
    )
    agent.run({"request": "task"})
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8") == "hello world"


def test_save_output_creates_parent_dirs(tmp_path):
    out_file = tmp_path / "deep" / "nested" / "result.txt"
    llm = SequencedLLM(["content"])
    agent = TemplateAgent(
        _basic_cfg(save_output=str(out_file), output_key="out"),
        llm,
    )
    agent.run({"request": "task"})
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8") == "content"


def test_save_output_json_writes_json(tmp_path):
    out_file = tmp_path / "data.json"
    llm = SequencedLLM(['{"key": "value"}'])
    agent = TemplateAgent(
        _basic_cfg(save_output=str(out_file), output_key="out", output_json=True),
        llm,
    )
    agent.run({"request": "task"})
    import json as _json
    assert _json.loads(out_file.read_text(encoding="utf-8")) == {"key": "value"}


def test_save_output_in_custom_team(tmp_path):
    from antcrew.teams.custom_team import CustomTeam
    out_file = tmp_path / "plan.md"
    llm = SequencedLLM(["the plan", "done"])
    steps = [
        {"name": "planner", "system_prompt": "Plan.", "output_key": "plan",
         "save_output": str(out_file)},
        {"name": "executor", "system_prompt": "Execute.", "output_key": "result"},
    ]
    team = CustomTeam(steps, llm)
    team.run("task")
    assert out_file.read_text(encoding="utf-8") == "the plan"


# ---------------------------------------------------------------------------
# validate CLI: system_prompt_file acceptance
# ---------------------------------------------------------------------------

def test_validate_accepts_system_prompt_file(tmp_path):
    import yaml
    from typer.testing import CliRunner
    from antcrew.cli import app
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("You are an agent.", encoding="utf-8")
    cfg = {
        "team": "custom",
        "steps": [{"name": "a", "system_prompt_file": str(prompt_file), "output_key": "out"}],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    r = CliRunner().invoke(app, ["validate", str(p)])
    assert r.exit_code == 0


def test_validate_warns_missing_system_prompt_file(tmp_path):
    import yaml
    from typer.testing import CliRunner
    from antcrew.cli import app
    cfg = {
        "team": "custom",
        "steps": [{"name": "a", "system_prompt_file": "nonexistent.md", "output_key": "out"}],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    r = CliRunner().invoke(app, ["validate", str(p)])
    # Missing file → warning (not error), still exits 0
    assert r.exit_code == 0
    assert "warning" in r.output.lower() or "not found" in r.output.lower()


def test_validate_errors_on_both_prompt_fields(tmp_path):
    import yaml
    from typer.testing import CliRunner
    from antcrew.cli import app
    cfg = {
        "team": "custom",
        "steps": [{
            "name": "a",
            "system_prompt": "Inline.",
            "system_prompt_file": "p.md",
            "output_key": "out",
        }],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    r = CliRunner().invoke(app, ["validate", str(p)])
    assert r.exit_code == 1


# ===========================================================================
# user_template
# ===========================================================================

def test_user_template_default_none():
    agent = TemplateAgent(_basic_cfg(), _llm())
    assert agent._user_template is None


def test_user_template_stored_from_config():
    agent = TemplateAgent(_basic_cfg(user_template="Plan: {plan}"), _llm())
    assert agent._user_template == "Plan: {plan}"


def test_user_template_and_input_key_mutual_exclusive():
    cfg = _basic_cfg(user_template="Use {plan}.", input_key="plan")
    with pytest.raises(ValueError, match="not both"):
        TemplateAgent(cfg, _llm())


def test_user_template_sent_as_user_message():
    llm = _CaptureLLM()
    agent = TemplateAgent(
        _basic_cfg(user_template="Plan: {plan}\nCode: {code}"),
        llm,
    )
    agent.run({"request": "task", "plan": "step 1", "code": "x = 1"})
    _, user_p = llm.calls[0]
    assert "step 1" in user_p
    assert "x = 1" in user_p


def test_user_template_unknown_key_left_unchanged():
    llm = _CaptureLLM()
    agent = TemplateAgent(_basic_cfg(user_template="Plan: {ghost}"), llm)
    agent.run({"request": "task"})
    _, user_p = llm.calls[0]
    assert "{ghost}" in user_p


def test_user_template_overrides_input_key():
    """When user_template is set, input_key is ignored (not read from state)."""
    llm = _CaptureLLM()
    agent = TemplateAgent(
        _basic_cfg(user_template="Template message: {plan}"),
        llm,
    )
    agent.run({"request": "should not appear", "plan": "the plan"})
    _, user_p = llm.calls[0]
    assert "the plan" in user_p
    # input_key="request" is not consulted — request value absent from user_p
    assert "should not appear" not in user_p


def test_user_template_with_output_json():
    llm = SequencedLLM(['{"score": 9}'])
    agent = TemplateAgent(
        _basic_cfg(
            user_template="Code: {code}",
            output_key="score",
            output_json=True,
        ),
        llm,
    )
    result = agent.run({"request": "task", "code": "def f(): pass"})
    assert result["score"] == {"score": 9}


def test_user_template_interpolation_disabled_by_interpolate_false():
    """interpolate:false only affects the system prompt, not user_template."""
    # user_template always uses _interpolate regardless of self._interpolate
    llm = _CaptureLLM()
    agent = TemplateAgent(
        _basic_cfg(
            system_prompt="System: {request}",
            user_template="User: {plan}",
            interpolate=False,
        ),
        llm,
    )
    agent.run({"request": "req_val", "plan": "plan_val"})
    sys_p, user_p = llm.calls[0]
    # system_prompt NOT interpolated (interpolate=False)
    assert "{request}" in sys_p
    # user_template ALWAYS interpolated
    assert "plan_val" in user_p


def test_user_template_in_custom_team_pipeline():
    """user_template should compose cleanly in a multi-step pipeline."""
    llm = SequencedLLM(["the plan", "the review"])
    steps = [
        {"name": "planner", "system_prompt": "Plan.", "output_key": "plan"},
        {
            "name": "reviewer",
            "system_prompt": "Review the following artifacts.",
            "user_template": "Plan:\n{plan}\nRequest: {request}",
            "output_key": "review",
        },
    ]
    from antcrew.teams.custom_team import CustomTeam
    team = CustomTeam(steps, llm)
    result = team.run("Build auth")
    assert result["plan"] == "the plan"
    assert result["review"] == "the review"


# ---------------------------------------------------------------------------
# validate CLI: user_template checks
# ---------------------------------------------------------------------------

def test_validate_accepts_user_template(tmp_path):
    import yaml
    from typer.testing import CliRunner
    from antcrew.cli import app
    cfg = {
        "team": "custom",
        "steps": [
            {"name": "a", "system_prompt": "Do.", "output_key": "out"},
            {
                "name": "b",
                "system_prompt": "Review.",
                "user_template": "Input: {out}",
                "output_key": "review",
            },
        ],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    r = CliRunner().invoke(app, ["validate", str(p)])
    assert r.exit_code == 0
    assert "user_tmpl" in r.output


def test_validate_warns_unknown_user_template_key(tmp_path):
    import yaml
    from typer.testing import CliRunner
    from antcrew.cli import app
    cfg = {
        "team": "custom",
        "steps": [
            {
                "name": "a",
                "system_prompt": "Do.",
                "user_template": "Input: {ghost_key}",
                "output_key": "out",
            },
        ],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    r = CliRunner().invoke(app, ["validate", str(p)])
    assert r.exit_code == 0
    assert "warning" in r.output.lower()


def test_validate_errors_user_template_and_input_key(tmp_path):
    import yaml
    from typer.testing import CliRunner
    from antcrew.cli import app
    cfg = {
        "team": "custom",
        "steps": [{
            "name": "a",
            "system_prompt": "Do.",
            "user_template": "Use {out}.",
            "input_key": "out",
            "output_key": "result",
        }],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    r = CliRunner().invoke(app, ["validate", str(p)])
    assert r.exit_code == 1


# ===========================================================================
# post_process transforms
# ===========================================================================

def test_post_process_default_empty():
    agent = TemplateAgent(_basic_cfg(), _llm())
    assert agent._post_process == []


def test_post_process_string_stored_as_list():
    agent = TemplateAgent(_basic_cfg(post_process="strip"), _llm())
    assert agent._post_process == ["strip"]


def test_post_process_list_stored():
    agent = TemplateAgent(_basic_cfg(post_process=["strip_fences", "strip"]), _llm())
    assert agent._post_process == ["strip_fences", "strip"]


def test_post_process_unknown_raises_at_runtime():
    llm = SequencedLLM(["output"])
    agent = TemplateAgent(_basic_cfg(post_process="nonexistent_transform"), llm)
    with pytest.raises(ValueError, match="Unknown post_process"):
        agent.run({"request": "task"})


# --- strip ---

def test_post_process_strip():
    llm = SequencedLLM(["  hello world  "])
    agent = TemplateAgent(_basic_cfg(post_process="strip", output_key="out"), llm)
    result = agent.run({"request": "task"})
    assert result["out"] == "hello world"


# --- lower / upper ---

def test_post_process_lower():
    llm = SequencedLLM(["HELLO"])
    agent = TemplateAgent(_basic_cfg(post_process="lower", output_key="out"), llm)
    assert agent.run({"request": "task"})["out"] == "hello"


def test_post_process_upper():
    llm = SequencedLLM(["hello"])
    agent = TemplateAgent(_basic_cfg(post_process="upper", output_key="out"), llm)
    assert agent.run({"request": "task"})["out"] == "HELLO"


# --- first_line / last_line ---

def test_post_process_first_line():
    llm = SequencedLLM(["first\nsecond\nthird"])
    agent = TemplateAgent(_basic_cfg(post_process="first_line", output_key="out"), llm)
    assert agent.run({"request": "task"})["out"] == "first"


def test_post_process_last_line():
    llm = SequencedLLM(["first\nsecond\nthird"])
    agent = TemplateAgent(_basic_cfg(post_process="last_line", output_key="out"), llm)
    assert agent.run({"request": "task"})["out"] == "third"


def test_post_process_first_line_skips_blank():
    llm = SequencedLLM(["\n\nactual first\nsecond"])
    agent = TemplateAgent(_basic_cfg(post_process="first_line", output_key="out"), llm)
    assert agent.run({"request": "task"})["out"] == "actual first"


# --- strip_fences ---

def test_post_process_strip_fences_removes_code_fence():
    llm = SequencedLLM(["```python\nprint('hello')\n```"])
    agent = TemplateAgent(_basic_cfg(post_process="strip_fences", output_key="out"), llm)
    assert agent.run({"request": "task"})["out"] == "print('hello')"


def test_post_process_strip_fences_no_fence_unchanged():
    llm = SequencedLLM(["plain text"])
    agent = TemplateAgent(_basic_cfg(post_process="strip_fences", output_key="out"), llm)
    assert agent.run({"request": "task"})["out"] == "plain text"


def test_post_process_strip_fences_language_agnostic():
    llm = SequencedLLM(["```json\n{\"key\": 1}\n```"])
    agent = TemplateAgent(_basic_cfg(post_process="strip_fences", output_key="out"), llm)
    assert agent.run({"request": "task"})["out"] == '{"key": 1}'


# --- chaining ---

def test_post_process_chain_fences_then_strip():
    llm = SequencedLLM(["```\n  content  \n```"])
    agent = TemplateAgent(
        _basic_cfg(post_process=["strip_fences", "strip"], output_key="out"), llm
    )
    assert agent.run({"request": "task"})["out"] == "content"


def test_post_process_not_applied_to_dict_output():
    """post_process is skipped when output_json is True (result is a dict)."""
    llm = SequencedLLM(['{"key": "value"}'])
    agent = TemplateAgent(
        _basic_cfg(post_process="upper", output_key="out", output_json=True), llm
    )
    result = agent.run({"request": "task"})["out"]
    assert isinstance(result, dict)  # dict, NOT uppercased string


def test_post_process_save_output_uses_post_processed_value(tmp_path):
    """save_output should store the post-processed result."""
    out_file = tmp_path / "out.txt"
    llm = SequencedLLM(["  trimmed  "])
    agent = TemplateAgent(
        _basic_cfg(post_process="strip", save_output=str(out_file), output_key="out"),
        llm,
    )
    agent.run({"request": "task"})
    assert out_file.read_text(encoding="utf-8") == "trimmed"


def test_post_process_in_custom_team(tmp_path):
    """post_process works end-to-end through CustomTeam."""
    from antcrew.teams.custom_team import CustomTeam
    llm = SequencedLLM(["```python\ndef hello(): pass\n```", "done"])
    steps = [
        {"name": "coder", "system_prompt": "Write code.", "output_key": "code",
         "post_process": "strip_fences"},
        {"name": "reviewer", "system_prompt": "Review.", "output_key": "review"},
    ]
    team = CustomTeam(steps, llm)
    result = team.run("task")
    assert result["code"] == "def hello(): pass"


# ---------------------------------------------------------------------------
# validate CLI: post_process checks
# ---------------------------------------------------------------------------

def test_validate_shows_post_process_in_flags(tmp_path):
    import yaml
    from typer.testing import CliRunner
    from antcrew.cli import app
    cfg = {
        "team": "custom",
        "steps": [{"name": "a", "system_prompt": "Do.",
                   "output_key": "out", "post_process": "strip_fences"}],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    r = CliRunner().invoke(app, ["validate", str(p)])
    assert r.exit_code == 0
    assert "strip_fences" in r.output or "pp:" in r.output


def test_validate_errors_unknown_post_process(tmp_path):
    import yaml
    from typer.testing import CliRunner
    from antcrew.cli import app
    cfg = {
        "team": "custom",
        "steps": [{"name": "a", "system_prompt": "Do.",
                   "output_key": "out", "post_process": "nonexistent"}],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    r = CliRunner().invoke(app, ["validate", str(p)])
    assert r.exit_code == 1
    assert "nonexistent" in r.output


# ===========================================================================
# register_transform
# ===========================================================================

def test_register_transform_makes_it_available():
    from antcrew.agents.template_agent import register_transform, POST_PROCESS_TRANSFORMS
    register_transform("reverse_test", lambda s: s[::-1])
    assert "reverse_test" in POST_PROCESS_TRANSFORMS
    assert POST_PROCESS_TRANSFORMS["reverse_test"]("abc") == "cba"


def test_register_transform_used_in_post_process():
    from antcrew.agents.template_agent import register_transform
    register_transform("shout", lambda s: s.upper() + "!")

    agent = TemplateAgent(
        {"name": "a", "system_prompt": "Go.", "output_key": "out",
         "post_process": "shout"},
        SequencedLLM(["hello world"]),
    )
    result = agent.run({"request": "task"})
    assert result["out"] == "HELLO WORLD!"


def test_register_transform_overrides_existing():
    from antcrew.agents.template_agent import register_transform, POST_PROCESS_TRANSFORMS
    original = POST_PROCESS_TRANSFORMS.get("strip")
    register_transform("strip", lambda s: "overridden")
    assert POST_PROCESS_TRANSFORMS["strip"]("anything") == "overridden"
    # Restore to avoid polluting other tests
    POST_PROCESS_TRANSFORMS["strip"] = original


def test_register_transform_exported_from_antcrew():
    import antcrew
    assert hasattr(antcrew, "register_transform")
    assert callable(antcrew.register_transform)


# ===========================================================================
# antcrew agents CLI command
# ===========================================================================

def test_agents_cmd_exits_zero():
    from typer.testing import CliRunner as _CR
    from antcrew.cli import app
    r = _CR().invoke(app, ["agents"])
    assert r.exit_code == 0


def test_agents_cmd_lists_built_in_agents():
    from typer.testing import CliRunner as _CR
    from antcrew.cli import app
    r = _CR().invoke(app, ["agents"])
    assert "backend_dev" in r.output
    assert "researcher" in r.output
    assert "reviewer" in r.output


def test_agents_cmd_lists_transforms():
    from typer.testing import CliRunner as _CR
    from antcrew.cli import app
    r = _CR().invoke(app, ["agents"])
    assert "strip" in r.output
    assert "strip_fences" in r.output


def test_agents_cmd_shows_registered_transform():
    from typer.testing import CliRunner as _CR
    from antcrew.cli import app
    from antcrew.agents.template_agent import register_transform, POST_PROCESS_TRANSFORMS

    register_transform("my_custom_xform", str.title)
    r = _CR().invoke(app, ["agents"])
    assert "my_custom_xform" in r.output
    # Cleanup
    del POST_PROCESS_TRANSFORMS["my_custom_xform"]
