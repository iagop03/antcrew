"""Tests for operator/transform layer and validate_agent_dag (v0.12.0)."""
from __future__ import annotations

import pytest

from antcrew.core.operators import (
    BaseOperator, CopyOp, DropOp, MapOp, MergeOp, RenameOp, SetOp,
    _DELETE, build_operator,
)
from antcrew.core.validation import validate_agent_dag
from antcrew.teams.custom_team import CustomTeam, _apply_patch
from antcrew.models.simulated import SimulatedLLM


# ---------------------------------------------------------------------------
# _apply_patch
# ---------------------------------------------------------------------------

class TestApplyPatch:
    def test_sets_value(self):
        s = {"a": 1}
        _apply_patch(s, {"b": 2})
        assert s == {"a": 1, "b": 2}

    def test_deletes_key(self):
        s = {"a": 1, "b": 2}
        _apply_patch(s, {"a": _DELETE})
        assert s == {"b": 2}

    def test_delete_missing_key_noop(self):
        s = {"a": 1}
        _apply_patch(s, {"nonexistent": _DELETE})
        assert s == {"a": 1}

    def test_mixed_set_and_delete(self):
        s = {"a": 1, "b": 2}
        _apply_patch(s, {"a": _DELETE, "c": 3})
        assert s == {"b": 2, "c": 3}


# ---------------------------------------------------------------------------
# RenameOp
# ---------------------------------------------------------------------------

class TestRenameOp:
    def test_renames_key(self):
        op = RenameOp("old", "new")
        patch = op.run({"old": "value", "other": "x"})
        s = {"old": "value", "other": "x"}
        _apply_patch(s, patch)
        assert "new" in s and s["new"] == "value"
        assert "old" not in s

    def test_noop_when_key_absent(self):
        op = RenameOp("missing", "new")
        assert op.run({"x": 1}) == {}

    def test_name_set(self):
        op = RenameOp("a", "b")
        assert "a" in op.name and "b" in op.name


# ---------------------------------------------------------------------------
# CopyOp
# ---------------------------------------------------------------------------

class TestCopyOp:
    def test_copies_key(self):
        op = CopyOp("src", "dst")
        patch = op.run({"src": "hello"})
        assert patch == {"dst": "hello"}

    def test_original_kept(self):
        op = CopyOp("src", "dst")
        s = {"src": "hello"}
        _apply_patch(s, op.run(s))
        assert "src" in s and "dst" in s

    def test_noop_when_absent(self):
        assert CopyOp("x", "y").run({}) == {}


# ---------------------------------------------------------------------------
# DropOp
# ---------------------------------------------------------------------------

class TestDropOp:
    def test_drops_single_key(self):
        op = DropOp("a")
        s = {"a": 1, "b": 2}
        _apply_patch(s, op.run(s))
        assert "a" not in s and "b" in s

    def test_drops_multiple_keys(self):
        op = DropOp("a", "b")
        s = {"a": 1, "b": 2, "c": 3}
        _apply_patch(s, op.run(s))
        assert s == {"c": 3}

    def test_ignores_missing_keys(self):
        op = DropOp("x", "y")
        s = {"z": 1}
        _apply_patch(s, op.run(s))
        assert s == {"z": 1}


# ---------------------------------------------------------------------------
# SetOp
# ---------------------------------------------------------------------------

class TestSetOp:
    def test_sets_constant(self):
        op = SetOp("status", "done")
        assert op.run({}) == {"status": "done"}

    def test_overwrites_existing(self):
        op = SetOp("k", 42)
        patch = op.run({"k": 0})
        assert patch == {"k": 42}

    def test_any_value_type(self):
        op = SetOp("data", [1, 2, 3])
        assert op.run({})["data"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# MapOp
# ---------------------------------------------------------------------------

class TestMapOp:
    def test_transforms_value(self):
        op = MapOp("text", str.upper)
        s = {"text": "hello"}
        _apply_patch(s, op.run(s))
        assert s["text"] == "HELLO"

    def test_noop_when_absent(self):
        op = MapOp("missing", str.upper)
        assert op.run({"other": "x"}) == {}

    def test_lambda(self):
        op = MapOp("n", lambda x: x * 2)
        patch = op.run({"n": 5})
        assert patch == {"n": 10}


# ---------------------------------------------------------------------------
# MergeOp
# ---------------------------------------------------------------------------

class TestMergeOp:
    def test_merges_two_keys(self):
        op = MergeOp(["a", "b"], "merged")
        patch = op.run({"a": "hello", "b": "world"})
        assert patch == {"merged": "hello\n\nworld"}

    def test_custom_sep(self):
        op = MergeOp(["x", "y"], "out", sep=" | ")
        patch = op.run({"x": "A", "y": "B"})
        assert patch["out"] == "A | B"

    def test_skips_missing_keys(self):
        op = MergeOp(["a", "b", "c"], "out")
        patch = op.run({"a": "only"})
        assert patch == {"out": "only"}

    def test_all_missing_empty_string(self):
        op = MergeOp(["x", "y"], "out")
        patch = op.run({})
        assert patch == {"out": ""}


# ---------------------------------------------------------------------------
# build_operator (YAML/dict)
# ---------------------------------------------------------------------------

class TestBuildOperator:
    def test_rename(self):
        op = build_operator({"operator": "rename", "from": "a", "to": "b"})
        assert isinstance(op, RenameOp)

    def test_copy(self):
        op = build_operator({"operator": "copy", "from": "a", "to": "b"})
        assert isinstance(op, CopyOp)

    def test_drop(self):
        op = build_operator({"operator": "drop", "keys": ["x", "y"]})
        assert isinstance(op, DropOp)

    def test_drop_single_string_key(self):
        op = build_operator({"operator": "drop", "keys": "x"})
        assert isinstance(op, DropOp)

    def test_set(self):
        op = build_operator({"operator": "set", "key": "k", "value": 42})
        assert isinstance(op, SetOp)
        assert op.run({})["k"] == 42

    def test_merge(self):
        op = build_operator({"operator": "merge", "keys": ["a", "b"], "to": "c"})
        assert isinstance(op, MergeOp)

    def test_merge_custom_sep(self):
        op = build_operator({"operator": "merge", "keys": ["a", "b"], "to": "c", "sep": "---"})
        patch = op.run({"a": "X", "b": "Y"})
        assert patch["c"] == "X---Y"

    def test_map_lambda(self):
        op = build_operator({"operator": "map", "key": "n", "fn": "lambda x: x + 1"})
        assert isinstance(op, MapOp)
        assert op.run({"n": 5})["n"] == 6

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown operator"):
            build_operator({"operator": "nonexistent"})

    def test_map_non_callable_raises(self):
        with pytest.raises(ValueError, match="not callable"):
            build_operator({"operator": "map", "key": "k", "fn": "42"})


# ---------------------------------------------------------------------------
# Operators in CustomTeam (Python API)
# ---------------------------------------------------------------------------

class TestOperatorsInCustomTeam:
    def test_rename_op_in_step_list(self):
        team = CustomTeam([
            {"name": "gen", "system_prompt": "Say it.", "output_key": "raw"},
            RenameOp("raw", "output"),
        ], llm=SimulatedLLM())
        result = team.run("task")
        assert "output" in result.state

    def test_drop_op_removes_key(self):
        team = CustomTeam([
            {"name": "gen", "system_prompt": "Say it.", "output_key": "temp"},
            DropOp("temp"),
            SetOp("done", True),
        ], llm=SimulatedLLM())
        result = team.run("task")
        assert "temp" not in result.state
        assert result.state.get("done") is True

    def test_set_op_injects_constant(self):
        team = CustomTeam([
            SetOp("phase", "init"),
            {"name": "gen", "system_prompt": "Phase {phase}", "output_key": "out"},
        ], llm=SimulatedLLM())
        result = team.run("task")
        assert result.state.get("phase") == "init"

    def test_operator_after_agent(self):
        team = CustomTeam([
            {"name": "a", "system_prompt": "Generate.", "output_key": "a_out"},
            CopyOp("a_out", "b_in"),
            {"name": "b", "system_prompt": "Use {b_in}.", "output_key": "b_out"},
        ], llm=SimulatedLLM())
        result = team.run("task")
        assert "b_out" in result.state

    def test_map_op_transforms_value(self):
        team = CustomTeam([
            SetOp("n", 5),
            MapOp("n", lambda x: x * 2),
        ], llm=SimulatedLLM())
        result = team.run("task")
        assert result.state["n"] == 10


# ---------------------------------------------------------------------------
# Operators in CustomTeam (YAML)
# ---------------------------------------------------------------------------

class TestOperatorsYAML:
    def test_rename_from_yaml(self, tmp_path):
        yaml = """
team: custom
model: simulated
steps:
  - name: gen
    system_prompt: "Generate something."
    output_key: raw_output
  - operator: rename
    from: raw_output
    to: final_output
"""
        f = tmp_path / "t.yaml"
        f.write_text(yaml, encoding="utf-8")
        from antcrew.config import load
        team = load(f)
        result = team.run("task")
        assert "final_output" in result.state

    def test_set_then_drop_from_yaml(self, tmp_path):
        yaml = """
team: custom
model: simulated
steps:
  - operator: set
    key: debug
    value: true
  - name: step
    system_prompt: "Do it."
    output_key: result
  - operator: drop
    keys: [debug]
"""
        f = tmp_path / "t.yaml"
        f.write_text(yaml, encoding="utf-8")
        from antcrew.config import load
        team = load(f)
        result = team.run("task")
        assert "debug" not in result.state
        assert "result" in result.state


# ---------------------------------------------------------------------------
# validate_agent_dag
# ---------------------------------------------------------------------------

class _MockAgent:
    def __init__(self, name, consumes=None, produces=None):
        self.name = name
        self.consumes = consumes or []
        self.produces = produces or []


class TestValidateAgentDag:
    def test_valid_linear_dag(self):
        agents = [
            _MockAgent("a", produces=["x"]),
            _MockAgent("b", consumes=["x"], produces=["y"]),
            _MockAgent("c", consumes=["y"]),
        ]
        violations = validate_agent_dag(agents, strict=False)
        assert violations == []

    def test_missing_consumed_key(self):
        agents = [
            _MockAgent("b", consumes=["prd"]),
        ]
        violations = validate_agent_dag(agents, strict=False)
        assert len(violations) == 1
        assert "prd" in violations[0]

    def test_raises_in_strict_mode(self):
        agents = [_MockAgent("x", consumes=["missing"])]
        with pytest.raises(ValueError, match="missing"):
            validate_agent_dag(agents, strict=True)

    def test_initial_keys_available(self):
        agents = [_MockAgent("a", consumes=["request", "config"])]
        violations = validate_agent_dag(agents, initial_keys={"request", "config"}, strict=False)
        assert violations == []

    def test_partial_violation(self):
        agents = [
            _MockAgent("a", produces=["x"]),
            _MockAgent("b", consumes=["x", "missing"]),
        ]
        violations = validate_agent_dag(agents, strict=False)
        assert len(violations) == 1
        assert "missing" in violations[0]

    def test_no_agents_valid(self):
        assert validate_agent_dag([]) == []

    def test_with_real_agents(self):
        from antcrew.agents.pm import PMAgent
        llm = SimulatedLLM()
        # pm consumes "prd" — when prd is in initial_keys, no violation
        agents = [PMAgent(llm)]
        violations = validate_agent_dag(
            agents,
            initial_keys={"request", "prd"},
            strict=False,
        )
        assert violations == []

    def test_request_always_available(self):
        agents = [_MockAgent("a", consumes=["request"])]
        violations = validate_agent_dag(agents, strict=False)
        assert violations == []


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

class TestExports:
    def test_all_operators_exported(self):
        from antcrew import (
            BaseOperator, RenameOp, CopyOp, DropOp, SetOp, MapOp, MergeOp, build_operator,
        )
        assert all(x is not None for x in [
            BaseOperator, RenameOp, CopyOp, DropOp, SetOp, MapOp, MergeOp, build_operator,
        ])

    def test_validate_agent_dag_exported(self):
        from antcrew import validate_agent_dag
        assert callable(validate_agent_dag)

    def test_sequenced_llm_exported(self):
        from antcrew import SequencedLLM
        llm = SequencedLLM(["hello", "world"])
        assert llm.system("s", "u") == "hello"
        assert llm.call_count == 1

    def test_async_wrappers_exported(self):
        from antcrew import AsyncCustomTeam, AsyncFeatureTeam, AsyncRouter
        assert AsyncCustomTeam is not None
        assert AsyncFeatureTeam is not None
        assert AsyncRouter is not None


# ---------------------------------------------------------------------------
# Mixing built-in agents with operators in CustomTeam
# ---------------------------------------------------------------------------

class TestMixedPipeline:
    """Built-in agent instances can be used as CustomTeam steps."""

    def _make_dummy_agent(self, name: str, output_key: str, output_value):
        """Return a minimal agent-like object (not TemplateAgent)."""
        class _Dummy:
            def __init__(self):
                self.name = name
                self._output_key = output_key
            def run(self, state: dict) -> dict:
                return {output_key: output_value}
        return _Dummy()

    def test_agent_instance_accepted_as_step(self):
        agent = self._make_dummy_agent("gen", "data", "hello")
        team = CustomTeam(steps=[agent], llm=SimulatedLLM())
        result = team.run("task")
        assert result.state["data"] == "hello"

    def test_agent_then_operator(self):
        agent = self._make_dummy_agent("gen", "draft", "content")
        team = CustomTeam(
            steps=[agent, RenameOp("draft", "final")],
            llm=SimulatedLLM(),
        )
        result = team.run("task")
        assert result.state["final"] == "content"
        assert "draft" not in result.state

    def test_operator_then_agent(self):
        agent = self._make_dummy_agent("consumer", "out", "done")
        team = CustomTeam(
            steps=[SetOp("injected", True), agent],
            llm=SimulatedLLM(),
        )
        result = team.run("task")
        assert result.state["injected"] is True
        assert result.state["out"] == "done"

    def test_mixed_pipeline_order(self):
        a1 = self._make_dummy_agent("a1", "step1", "A")
        a2 = self._make_dummy_agent("a2", "step2", "B")
        team = CustomTeam(
            steps=[
                a1,
                CopyOp("step1", "step1_copy"),
                a2,
                DropOp("step1_copy"),
            ],
            llm=SimulatedLLM(),
        )
        result = team.run("task")
        assert result.state["step1"] == "A"
        assert result.state["step2"] == "B"
        assert "step1_copy" not in result.state

    def test_template_agent_and_builtin_agent_together(self):
        from antcrew.agents.pm import PMAgent
        llm = SimulatedLLM()
        # PMAgent is a built-in typed agent — should be accepted as a step
        team = CustomTeam(
            steps=[
                {"name": "intro", "system_prompt": "Say hello.", "output_key": "greeting"},
                PMAgent(llm),
            ],
            llm=llm,
        )
        # Construction must not raise — that's the contract
        assert team is not None
        assert len(team._step_groups) == 2

    def test_three_agent_types_together(self):
        """TemplateAgent + built-in agent + operator all in one pipeline."""
        from antcrew.agents.business import BusinessAnalystAgent
        llm = SimulatedLLM()
        team = CustomTeam(
            steps=[
                {"name": "intro", "system_prompt": "Start.", "output_key": "note"},
                SetOp("ready", True),
                BusinessAnalystAgent(llm),
            ],
            llm=llm,
        )
        assert len(team._step_groups) == 3
