"""Tests for Router, LLMClassifier, RuleClassifier, DirectAgent (v0.11.12)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from antcrew.agents.direct_agent import DirectAgent
from antcrew.core.router import LLMClassifier, RouteClassifier, Router, RuleClassifier
from antcrew.core.run_result import RunResult
from antcrew.models.simulated import SimulatedLLM

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _team(label: str):
    """Minimal team stub that returns RunResult with route_label in state."""
    m = MagicMock()
    m.run.return_value = RunResult(state={"request": "r", "team_label": label})
    return m


# ---------------------------------------------------------------------------
# RuleClassifier
# ---------------------------------------------------------------------------

class TestRuleClassifier:
    def test_matches_first_rule(self):
        clf = RuleClassifier([
            (r"\bwhat\b", "simple"),
            (r"\bbuild\b", "complex"),
        ], default="complex")
        assert clf.classify("What is JWT?") == "simple"

    def test_matches_second_rule(self):
        clf = RuleClassifier([
            (r"\bwhat\b", "simple"),
            (r"\bbuild\b", "complex"),
        ], default="complex")
        assert clf.classify("Build an auth system") == "complex"

    def test_returns_default_when_no_match(self):
        clf = RuleClassifier([(r"\bwhat\b", "simple")], default="complex")
        assert clf.classify("Deploy the server") == "complex"

    def test_case_insensitive(self):
        clf = RuleClassifier([(r"\bWhat\b", "simple")], default="complex")
        assert clf.classify("WHAT is REST?") == "simple"

    def test_first_rule_wins(self):
        clf = RuleClassifier([
            (r"\bwhat\b", "simple"),
            (r"\bwhat.*build\b", "complex"),
        ], default="complex")
        assert clf.classify("What should I build?") == "simple"

    def test_empty_rules_returns_default(self):
        clf = RuleClassifier([], default="complex")
        assert clf.classify("anything") == "complex"

    def test_custom_default(self):
        clf = RuleClassifier([], default="direct")
        assert clf.classify("anything") == "direct"


# ---------------------------------------------------------------------------
# LLMClassifier
# ---------------------------------------------------------------------------

class TestLLMClassifier:
    def _llm_returning(self, text: str):
        llm = MagicMock()
        llm.system.return_value = text
        return llm

    def test_classifies_simple(self):
        llm = self._llm_returning("simple")
        clf = LLMClassifier(llm, {"simple": "Quick", "complex": "Dev task"})
        assert clf.classify("What is JWT?") == "simple"

    def test_classifies_complex(self):
        llm = self._llm_returning("complex")
        clf = LLMClassifier(llm, {"simple": "Quick", "complex": "Dev task"})
        assert clf.classify("Build JWT auth") == "complex"

    def test_fallback_on_llm_error(self):
        llm = MagicMock()
        llm.system.side_effect = RuntimeError("API error")
        clf = LLMClassifier(llm, {"simple": "Quick", "complex": "Dev task"}, default="complex")
        assert clf.classify("anything") == "complex"

    def test_fallback_on_unknown_label(self):
        llm = self._llm_returning("unknown_label_xyz")
        clf = LLMClassifier(llm, {"simple": "Quick", "complex": "Dev task"}, default="complex")
        assert clf.classify("anything") == "complex"

    def test_case_insensitive_match(self):
        llm = self._llm_returning("  SIMPLE  ")
        clf = LLMClassifier(llm, {"simple": "Quick", "complex": "Dev task"})
        assert clf.classify("anything") == "simple"

    def test_empty_routes_raises(self):
        with pytest.raises(ValueError, match="at least one route"):
            LLMClassifier(MagicMock(), {})

    def test_default_is_last_label(self):
        clf = LLMClassifier(MagicMock(), {"a": "A", "b": "B", "c": "C"})
        assert clf._default == "c"

    def test_calls_llm_with_request(self):
        llm = MagicMock()
        llm.system.return_value = "simple"
        clf = LLMClassifier(llm, {"simple": "Q", "complex": "D"})
        clf.classify("my request")
        call_args = llm.system.call_args
        assert "my request" in str(call_args)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class TestRouter:
    def _make_router(self, *, classifier_label="simple"):
        clf = MagicMock(spec=RouteClassifier)
        clf.classify.return_value = classifier_label
        simple = _team("simple")
        complex_ = _team("complex")
        router = Router(
            classifier=clf,
            routes={"simple": simple, "complex": complex_},
            default="complex",
        )
        return router, simple, complex_

    def test_dispatches_to_simple(self):
        router, simple, _ = self._make_router(classifier_label="simple")
        result = router.run("What is REST?")
        simple.run.assert_called_once_with("What is REST?")
        assert result.state["_route"] == "simple"

    def test_dispatches_to_complex(self):
        router, _, complex_ = self._make_router(classifier_label="complex")
        result = router.run("Build a system")
        complex_.run.assert_called_once_with("Build a system")
        assert result.state["_route"] == "complex"

    def test_falls_back_to_default_on_unknown_label(self):
        clf = MagicMock(spec=RouteClassifier)
        clf.classify.return_value = "nonexistent"
        complex_ = _team("complex")
        router = Router(
            classifier=clf,
            routes={"simple": _team("simple"), "complex": complex_},
            default="complex",
        )
        result = router.run("anything")
        complex_.run.assert_called_once()
        assert result.state["_route"] == "complex"

    def test_route_key_injected_in_state(self):
        router, _, _ = self._make_router(classifier_label="simple")
        result = router.run("r")
        assert "_route" in result.state

    def test_empty_routes_raises(self):
        with pytest.raises(ValueError, match="at least one route"):
            Router(
                classifier=MagicMock(),
                routes={},
                default="x",
            )

    def test_default_not_in_routes_raises(self):
        with pytest.raises(ValueError, match="not in routes"):
            Router(
                classifier=MagicMock(),
                routes={"a": MagicMock()},
                default="missing",
            )

    def test_result_is_run_result(self):
        router, _, _ = self._make_router()
        result = router.run("r")
        assert isinstance(result, RunResult)


# ---------------------------------------------------------------------------
# DirectAgent
# ---------------------------------------------------------------------------

class TestDirectAgent:
    def test_run_returns_run_result(self):
        agent = DirectAgent(SimulatedLLM())
        result = agent.run("What is REST?")
        assert isinstance(result, RunResult)

    def test_response_in_state(self):
        agent = DirectAgent(SimulatedLLM())
        result = agent.run("Hello")
        assert "response" in result.state
        assert result.state["response"]

    def test_custom_output_key(self):
        agent = DirectAgent(SimulatedLLM(), output_key="answer")
        result = agent.run("question")
        assert "answer" in result.state

    def test_request_preserved_in_state(self):
        agent = DirectAgent(SimulatedLLM())
        result = agent.run("my question")
        assert result.state["request"] == "my question"

    def test_custom_system_prompt_used(self):
        llm = MagicMock()
        llm.system.return_value = "answer"
        agent = DirectAgent(llm, system_prompt="Custom system.")
        agent.run("question")
        called_system = llm.system.call_args[0][0]
        assert "Custom system." in called_system

    def test_default_system_prompt(self):
        agent = DirectAgent(SimulatedLLM())
        assert agent._system_prompt  # not empty

    def test_max_tokens_forwarded(self):
        llm = MagicMock()
        llm.system.return_value = "ok"
        agent = DirectAgent(llm, max_tokens=100)
        agent.run("q")
        kwargs = llm.system.call_args[1]
        assert kwargs.get("max_tokens") == 100


# ---------------------------------------------------------------------------
# YAML config: team: auto
# ---------------------------------------------------------------------------

class TestConfigAutoTeam:
    def test_load_auto_team(self, tmp_path):
        yaml_content = """
team: auto
model: simulated
complex_team: custom
steps:
  - name: builder
    system_prompt: "Build: {request}"
    output_key: result
"""
        cfg_file = tmp_path / "team.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")
        from antcrew.config import load
        team = load(cfg_file)
        assert isinstance(team, Router)

    def test_auto_team_has_simple_and_complex_routes(self, tmp_path):
        yaml_content = "team: auto\nmodel: simulated\n"
        cfg_file = tmp_path / "t.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")
        from antcrew.config import load
        team = load(cfg_file)
        assert "simple" in team._routes
        assert "complex" in team._routes

    def test_auto_team_simple_route_is_direct_agent(self, tmp_path):
        yaml_content = "team: auto\nmodel: simulated\n"
        cfg_file = tmp_path / "t.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")
        from antcrew.config import load
        team = load(cfg_file)
        assert isinstance(team._routes["simple"], DirectAgent)

    def test_auto_team_run_produces_route_key(self, tmp_path):
        yaml_content = "team: auto\nmodel: simulated\n"
        cfg_file = tmp_path / "t.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")
        from antcrew.config import load
        team = load(cfg_file)
        result = team.run("What is JWT?")
        assert "_route" in result.state
        assert result.state["_route"] in ("simple", "complex")


# ---------------------------------------------------------------------------
# YAML config: team: routed
# ---------------------------------------------------------------------------

class TestConfigRoutedTeam:
    def test_load_routed_with_rule_classifier(self, tmp_path):
        yaml_content = """
team: routed
model: simulated
classifier: rule
default_route: fallback
rules:
  - pattern: "\\\\bwhat\\\\b"
    label: quick
  - pattern: "\\\\bbuild\\\\b"
    label: dev
routes:
  quick:
    team: direct
    system_prompt: "Answer quickly."
  dev:
    team: custom
    steps:
      - name: builder
        system_prompt: "Build: {request}"
        output_key: result
  fallback:
    team: direct
    system_prompt: "Help."
"""
        cfg_file = tmp_path / "t.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")
        from antcrew.config import load
        team = load(cfg_file)
        assert isinstance(team, Router)
        assert isinstance(team._classifier, RuleClassifier)

    def test_routed_without_routes_raises(self, tmp_path):
        yaml_content = "team: routed\nmodel: simulated\n"
        cfg_file = tmp_path / "t.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")
        from antcrew.config import load
        with pytest.raises(ValueError, match="routes"):
            load(cfg_file)


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

class TestPublicExports:
    def test_router_exported(self):
        from antcrew import Router
        assert Router is not None

    def test_llm_classifier_exported(self):
        from antcrew import LLMClassifier
        assert LLMClassifier is not None

    def test_rule_classifier_exported(self):
        from antcrew import RuleClassifier
        assert RuleClassifier is not None

    def test_direct_agent_exported(self):
        from antcrew import DirectAgent
        assert DirectAgent is not None
