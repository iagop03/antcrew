"""Tests for context_keys — per-step state filtering (v0.11.11)."""
from __future__ import annotations

import pytest

from antcrew.models.simulated import SimulatedLLM
from antcrew.teams.custom_team import CustomTeam, _filter_state, _Step
from antcrew.agents.template_agent import TemplateAgent


# ---------------------------------------------------------------------------
# _filter_state helper
# ---------------------------------------------------------------------------

class TestFilterState:
    def test_none_returns_full_state(self):
        state = {"request": "x", "prd": "doc", "code": "py"}
        assert _filter_state(state, None) is state

    def test_filters_to_listed_keys(self):
        state = {"request": "x", "prd": "doc", "code": "py", "other": "z"}
        result = _filter_state(state, ["prd"])
        assert set(result.keys()) == {"request", "prd"}
        assert result["prd"] == "doc"

    def test_request_always_included(self):
        state = {"request": "x", "prd": "doc"}
        result = _filter_state(state, ["prd"])
        assert "request" in result

    def test_missing_keys_silently_omitted(self):
        state = {"request": "x", "prd": "doc"}
        result = _filter_state(state, ["prd", "nonexistent"])
        assert "nonexistent" not in result
        assert "prd" in result

    def test_empty_list_returns_only_request(self):
        state = {"request": "x", "prd": "doc", "code": "py"}
        result = _filter_state(state, [])
        assert set(result.keys()) == {"request"}

    def test_does_not_mutate_original_state(self):
        state = {"request": "x", "prd": "doc"}
        _filter_state(state, ["prd"])
        assert "prd" in state  # original untouched


# ---------------------------------------------------------------------------
# CustomTeam — context_keys in steps
# ---------------------------------------------------------------------------

class TestCustomTeamContextKeys:
    def test_step_without_context_keys_sees_full_state(self):
        seen_state = {}

        class _SpyAgent:
            name = "spy"
            _output_key = "spy_out"
            def run(self, state):
                seen_state.update(state)
                return {"spy_out": "done"}

        from antcrew.teams.custom_team import _Step, _run_step
        step = _Step(agent=_SpyAgent())
        full_state = {"request": "r", "prd": "doc", "code": "py"}
        _run_step(step, full_state)
        assert "prd" in seen_state
        assert "code" in seen_state

    def test_step_with_context_keys_sees_filtered_state(self):
        seen_state = {}

        class _SpyAgent:
            name = "spy"
            _output_key = "spy_out"
            def run(self, state):
                seen_state.update(state)
                return {"spy_out": "done"}

        from antcrew.teams.custom_team import _Step, _run_step
        step = _Step(agent=_SpyAgent(), context_keys=["prd"])
        _run_step(step, {"request": "r", "prd": "doc", "code": "py"})
        assert "prd" in seen_state
        assert "code" not in seen_state
        assert "request" in seen_state

    def test_context_keys_from_yaml_step(self):
        team = CustomTeam(
            steps=[
                {"name": "planner", "system_prompt": "Plan: {request}", "output_key": "plan"},
                {
                    "name": "executor",
                    "system_prompt": "Execute: {plan}",
                    "output_key": "result",
                    "context_keys": ["plan"],
                },
            ],
            llm=SimulatedLLM(),
        )
        result = team.run("task")
        assert result.state.get("result") is not None

    def test_context_keys_string_shorthand(self):
        """context_keys: plan (str) works the same as context_keys: [plan]."""
        team = CustomTeam(
            steps=[
                {"name": "p", "system_prompt": "Plan: {request}", "output_key": "plan"},
                {
                    "name": "e",
                    "system_prompt": "Execute: {plan}",
                    "output_key": "result",
                    "context_keys": "plan",
                },
            ],
            llm=SimulatedLLM(),
        )
        result = team.run("task")
        assert result.state.get("result") is not None

    def test_context_keys_multiple_values(self):
        team = CustomTeam(
            steps=[
                {"name": "a", "system_prompt": "A: {request}", "output_key": "a_out"},
                {"name": "b", "system_prompt": "B: {request}", "output_key": "b_out"},
                {
                    "name": "c",
                    "system_prompt": "C: {a_out} + {b_out}",
                    "output_key": "c_out",
                    "context_keys": ["a_out", "b_out"],
                },
            ],
            llm=SimulatedLLM(),
        )
        result = team.run("task")
        assert result.state.get("c_out") is not None

    def test_gate_still_sees_full_merged_state(self):
        """Gate runs on full merged state even when context_keys filters the agent's view."""
        from antcrew.core.gates import NonEmptyGate
        team = CustomTeam(
            steps=[
                {"name": "p", "system_prompt": "Plan.", "output_key": "plan"},
                {
                    "name": "e",
                    "system_prompt": "Execute: {plan}",
                    "output_key": "result",
                    "context_keys": ["plan"],
                    "gate": "non_empty:result",
                },
            ],
            llm=SimulatedLLM(),
        )
        result = team.run("task")
        assert result.state.get("result") is not None

    def test_context_keys_not_in_agent_config(self):
        """'context_keys' is a team key — not passed to TemplateAgent config."""
        from antcrew.teams.custom_team import _agent_cfg, _TEAM_KEYS
        assert "context_keys" in _TEAM_KEYS
        raw = {
            "name": "a",
            "system_prompt": "Do X.",
            "output_key": "x",
            "context_keys": ["prd"],
        }
        agent_cfg = _agent_cfg(raw)
        assert "context_keys" not in agent_cfg


# ---------------------------------------------------------------------------
# TemplateAgent — context_keys in standalone agent
# ---------------------------------------------------------------------------

class TestTemplateAgentContextKeys:
    def test_no_context_keys_by_default(self):
        agent = TemplateAgent(
            {"name": "a", "system_prompt": "Do X."},
            llm=SimulatedLLM(),
        )
        assert agent._context_keys is None

    def test_context_keys_list(self):
        agent = TemplateAgent(
            {"name": "a", "system_prompt": "Do X.", "context_keys": ["prd", "code"]},
            llm=SimulatedLLM(),
        )
        assert agent._context_keys == ["prd", "code"]

    def test_context_keys_string(self):
        agent = TemplateAgent(
            {"name": "a", "system_prompt": "Do X.", "context_keys": "prd"},
            llm=SimulatedLLM(),
        )
        assert agent._context_keys == ["prd"]

    def test_system_prompt_only_interpolates_allowed_keys(self):
        """Placeholders for excluded keys are left as {key} in the prompt."""
        from unittest.mock import patch

        calls = []

        agent = TemplateAgent(
            {
                "name": "a",
                "system_prompt": "Plan: {plan}. Secret: {secret}",
                "context_keys": ["plan"],
            },
            llm=SimulatedLLM(),
        )

        def _capture_system(sys_prompt, user, **kw):
            calls.append(sys_prompt)
            return "response"

        with patch.object(agent, "system", side_effect=_capture_system):
            agent.run({"request": "r", "plan": "do X", "secret": "hidden"})

        assert "do X" in calls[0]
        assert "hidden" not in calls[0]
        assert "{secret}" in calls[0]

    def test_user_template_only_interpolates_allowed_keys(self):
        """user_template placeholders for excluded keys are left literal."""
        from unittest.mock import patch

        calls = []

        agent = TemplateAgent(
            {
                "name": "a",
                "system_prompt": "Act.",
                "user_template": "Plan={plan} | Noise={noise}",
                "context_keys": ["plan"],
            },
            llm=SimulatedLLM(),
        )

        def _capture(sys_p, user, **kw):
            calls.append(user)
            return "response"

        with patch.object(agent, "system", side_effect=_capture):
            agent.run({"request": "r", "plan": "step1", "noise": "irrelevant"})

        assert "step1" in calls[0]
        assert "irrelevant" not in calls[0]

    def test_request_always_available_in_system_prompt(self):
        from unittest.mock import patch

        calls = []

        agent = TemplateAgent(
            {
                "name": "a",
                "system_prompt": "Task: {request}",
                "context_keys": [],  # empty — only request allowed
            },
            llm=SimulatedLLM(),
        )

        with patch.object(agent, "system", side_effect=lambda s, u, **k: calls.append(s) or "r"):
            agent.run({"request": "build something", "secret": "hidden"})

        assert "build something" in calls[0]

    def test_context_keys_in_yaml_file(self, tmp_path):
        """context_keys: works when loaded from a YAML agent file."""
        yaml_content = """
name: pm
system_prompt: "Write PRD for: {request}. (plan={plan})"
output_key: prd
context_keys:
  - plan
"""
        yaml_file = tmp_path / "pm.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        from antcrew.agents.template_agent import load_template_agent
        agent = load_template_agent(str(yaml_file), SimulatedLLM())
        assert agent._context_keys == ["plan"]


# ---------------------------------------------------------------------------
# Integration: context_keys in YAML pipeline
# ---------------------------------------------------------------------------

class TestContextKeysYAMLIntegration:
    def test_full_pipeline_with_context_filtering(self, tmp_path):
        yaml_content = """
team: custom
model: simulated
steps:
  - name: analyst
    system_prompt: "Analyse the request."
    output_key: analysis
  - name: planner
    system_prompt: "Plan based on: {analysis}"
    output_key: plan
    context_keys: [analysis]
  - name: builder
    system_prompt: "Build based on: {plan}"
    output_key: result
    context_keys: [plan]
"""
        cfg_file = tmp_path / "team.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")

        from antcrew.config import load
        team = load(cfg_file)
        result = team.run("make something")
        assert result.state.get("result") is not None
