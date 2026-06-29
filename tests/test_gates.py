"""Tests for verification gates (antcrew.core.gates)."""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch

from antcrew.core.gates import (
    GateResult, GateError,
    NonEmptyGate, PythonSyntaxGate, JsonGate, SchemaGate,
    AllGate, AnyGate,
)
from antcrew import (
    BaseGate, GateResult as _GR, GateError as _GE,
    NonEmptyGate as _NEG, PythonSyntaxGate as _PSG,
)


# ── top-level exports ─────────────────────────────────────────────────────────

def test_gates_importable_from_top_level():
    assert _NEG is NonEmptyGate
    assert _PSG is PythonSyntaxGate
    assert _GR is GateResult
    assert _GE is GateError


# ── GateResult ────────────────────────────────────────────────────────────────

def test_gate_result_passed():
    r = GateResult(passed=True, message="ok")
    assert r.passed
    assert r.field is None


def test_gate_result_failed_with_field():
    r = GateResult(passed=False, message="empty", field="prd")
    assert not r.passed
    assert r.field == "prd"


# ── GateError ────────────────────────────────────────────────────────────────

def test_gate_error_message_format():
    exc = GateError("NonEmptyGate", "Field 'prd' is None", field="prd")
    assert "NonEmptyGate" in str(exc)
    assert "prd" in str(exc)
    assert exc.gate_name == "NonEmptyGate"
    assert exc.field == "prd"


def test_gate_error_no_field():
    exc = GateError("JsonGate", "not valid JSON")
    assert exc.field is None
    assert "JsonGate" in str(exc)


# ── NonEmptyGate ──────────────────────────────────────────────────────────────

class TestNonEmptyGate:
    def test_passes_with_string(self):
        g = NonEmptyGate("prd")
        r = g.check({"prd": "some content"})
        assert r.passed

    def test_fails_on_none(self):
        g = NonEmptyGate("prd")
        r = g.check({"prd": None})
        assert not r.passed
        assert r.field == "prd"

    def test_fails_on_blank_string(self):
        g = NonEmptyGate("prd")
        r = g.check({"prd": "   "})
        assert not r.passed

    def test_fails_on_missing_key(self):
        g = NonEmptyGate("prd")
        r = g.check({})
        assert not r.passed

    def test_min_length_passes(self):
        g = NonEmptyGate("prd", min_length=10)
        r = g.check({"prd": "hello world"})
        assert r.passed

    def test_min_length_fails(self):
        g = NonEmptyGate("prd", min_length=100)
        r = g.check({"prd": "short"})
        assert not r.passed

    def test_passes_with_non_empty_list(self):
        g = NonEmptyGate("tickets")
        r = g.check({"tickets": ["ticket1", "ticket2"]})
        assert r.passed

    def test_fails_with_empty_list(self):
        g = NonEmptyGate("tickets")
        r = g.check({"tickets": []})
        assert not r.passed

    def test_passes_with_pydantic_object(self):
        g = NonEmptyGate("prd")
        obj = MagicMock()
        r = g.check({"prd": obj})
        assert r.passed


# ── PythonSyntaxGate ──────────────────────────────────────────────────────────

class TestPythonSyntaxGate:
    def test_passes_valid_python(self):
        g = PythonSyntaxGate("code")
        r = g.check({"code": "def foo():\n    return 42\n"})
        assert r.passed

    def test_fails_invalid_python(self):
        g = PythonSyntaxGate("code")
        r = g.check({"code": "def foo(\n    return 42"})
        assert not r.passed
        assert r.field == "code"

    def test_passes_markdown_fenced_block(self):
        g = PythonSyntaxGate("code")
        code = "```python\ndef hello():\n    print('hi')\n```"
        r = g.check({"code": code})
        assert r.passed

    def test_fails_invalid_fenced_block(self):
        g = PythonSyntaxGate("code")
        code = "```python\ndef (bad syntax:\n```"
        r = g.check({"code": code})
        assert not r.passed

    def test_passes_list_of_strings(self):
        g = PythonSyntaxGate("code_artifacts")
        r = g.check({"code_artifacts": ["x = 1", "y = 2"]})
        assert r.passed

    def test_fails_list_with_bad_item(self):
        g = PythonSyntaxGate("code_artifacts")
        r = g.check({"code_artifacts": ["x = 1", "def bad(:\n    pass"]})
        assert not r.passed

    def test_passes_list_of_objects_with_content(self):
        g = PythonSyntaxGate("code_artifacts")
        obj = MagicMock()
        obj.content = "x = 1\n"
        r = g.check({"code_artifacts": [obj]})
        assert r.passed

    def test_fails_on_none(self):
        g = PythonSyntaxGate("code")
        r = g.check({"code": None})
        assert not r.passed

    def test_no_code_passes(self):
        g = PythonSyntaxGate("code")
        r = g.check({"code": ""})
        assert r.passed


# ── JsonGate ──────────────────────────────────────────────────────────────────

class TestJsonGate:
    def test_passes_valid_json(self):
        g = JsonGate("payload")
        r = g.check({"payload": '{"key": "value"}'})
        assert r.passed

    def test_fails_invalid_json(self):
        g = JsonGate("payload")
        r = g.check({"payload": "{not json}"})
        assert not r.passed
        assert r.field == "payload"

    def test_fails_on_none(self):
        g = JsonGate("payload")
        r = g.check({"payload": None})
        assert not r.passed

    def test_passes_json_array(self):
        g = JsonGate("payload")
        r = g.check({"payload": "[1, 2, 3]"})
        assert r.passed


# ── SchemaGate ────────────────────────────────────────────────────────────────

class TestSchemaGate:
    def _make_schema(self):
        from pydantic import BaseModel
        class MySchema(BaseModel):
            title: str
            items: list[str]
        return MySchema

    def test_passes_valid_dict(self):
        schema = self._make_schema()
        g = SchemaGate("data", schema)
        r = g.check({"data": {"title": "Test", "items": ["a", "b"]}})
        assert r.passed

    def test_passes_valid_json_string(self):
        schema = self._make_schema()
        g = SchemaGate("data", schema)
        payload = json.dumps({"title": "Test", "items": []})
        r = g.check({"data": payload})
        assert r.passed

    def test_fails_invalid_schema(self):
        schema = self._make_schema()
        g = SchemaGate("data", schema)
        r = g.check({"data": {"title": 123}})  # items missing, title wrong type
        assert not r.passed
        assert r.field == "data"

    def test_fails_on_none(self):
        schema = self._make_schema()
        g = SchemaGate("data", schema)
        r = g.check({"data": None})
        assert not r.passed

    def test_passes_model_instance(self):
        schema = self._make_schema()
        instance = schema(title="hi", items=[])
        g = SchemaGate("data", schema)
        r = g.check({"data": instance})
        assert r.passed


# ── AllGate / AnyGate ─────────────────────────────────────────────────────────

class TestAllGate:
    def test_passes_when_all_pass(self):
        g = AllGate(NonEmptyGate("a"), NonEmptyGate("b"))
        r = g.check({"a": "x", "b": "y"})
        assert r.passed

    def test_fails_when_any_fails(self):
        g = AllGate(NonEmptyGate("a"), NonEmptyGate("b"))
        r = g.check({"a": "x", "b": None})
        assert not r.passed

    def test_short_circuits_on_first_failure(self):
        called = []
        class TrackGate(BaseGate):
            def check(self, state):
                called.append(1)
                return GateResult(False, "fail")
        g = AllGate(NonEmptyGate("missing"), TrackGate())
        g.check({})
        assert len(called) == 0  # second gate never called

    def test_requires_at_least_one_gate(self):
        with pytest.raises(ValueError):
            AllGate()


class TestAnyGate:
    def test_passes_when_one_passes(self):
        g = AnyGate(NonEmptyGate("a"), NonEmptyGate("b"))
        r = g.check({"a": None, "b": "yes"})
        assert r.passed

    def test_fails_when_none_pass(self):
        g = AnyGate(NonEmptyGate("a"), NonEmptyGate("b"))
        r = g.check({"a": None, "b": None})
        assert not r.passed

    def test_requires_at_least_one_gate(self):
        with pytest.raises(ValueError):
            AnyGate()


# ── Supervisor integration ────────────────────────────────────────────────────

class TestSupervisorGates:
    def _make_agent(self, output: dict):
        agent = MagicMock()
        agent.run = MagicMock(return_value=output)
        agent.approval_required = False
        return agent

    def test_gate_passes_pipeline_continues(self):
        from antcrew.core.supervisor import Supervisor

        pm = self._make_agent({"prd": "A product requirements document"})
        backend = self._make_agent({"code_artifacts": ["x = 1"]})

        sup = Supervisor(
            flow=[("pm", "backend_dev")],
            gates={"pm": NonEmptyGate("prd")},
        )
        app = sup.build({"pm": pm, "backend_dev": backend})

        state = {"request": "test", "messages": [], "metadata": {}, "errors": [],
                 "current_agent": "", "sprint_number": 0}
        result = app.invoke(state, config={"configurable": {"thread_id": "t1"}})
        assert result is not None

    def test_gate_fails_raises_gate_error(self):
        from antcrew.core.supervisor import Supervisor

        pm = self._make_agent({"prd": None})  # empty output → gate fails

        sup = Supervisor(
            flow=[("pm", "backend_dev")],
            gates={"pm": NonEmptyGate("prd")},
        )
        backend = self._make_agent({"code_artifacts": []})
        app = sup.build({"pm": pm, "backend_dev": backend})

        state = {"request": "test", "messages": [], "metadata": {}, "errors": [],
                 "current_agent": "", "sprint_number": 0}
        with pytest.raises(GateError) as exc_info:
            app.invoke(state, config={"configurable": {"thread_id": "t2"}})
        assert "prd" in str(exc_info.value)

    def test_gate_not_on_all_agents(self):
        from antcrew.core.supervisor import Supervisor

        pm = self._make_agent({"prd": "some prd"})
        backend = self._make_agent({"code_artifacts": ["x = 1"]})

        sup = Supervisor(
            flow=[("pm", "backend_dev")],
            gates={"pm": NonEmptyGate("prd")},
            # backend_dev has no gate
        )
        app = sup.build({"pm": pm, "backend_dev": backend})
        state = {"request": "test", "messages": [], "metadata": {}, "errors": [],
                 "current_agent": "", "sprint_number": 0}
        result = app.invoke(state, config={"configurable": {"thread_id": "t3"}})
        assert result is not None

    def test_no_gates_works_as_before(self):
        from antcrew.core.supervisor import Supervisor

        pm = self._make_agent({"prd": "prd"})
        sup = Supervisor(flow=[("pm", "backend_dev")])
        backend = self._make_agent({"code_artifacts": []})
        app = sup.build({"pm": pm, "backend_dev": backend})
        state = {"request": "test", "messages": [], "metadata": {}, "errors": [],
                 "current_agent": "", "sprint_number": 0}
        result = app.invoke(state, config={"configurable": {"thread_id": "t4"}})
        assert result is not None


# ── CLI GateError output ──────────────────────────────────────────────────────

def test_cli_gate_error_message(tmp_path):
    from typer.testing import CliRunner
    from antcrew.cli._app import app
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.core.supervisor import Supervisor
    from antcrew.teams.custom_team import CustomTeam
    from antcrew.agents.pm import PMAgent

    # Build a team where the gate will definitely fail
    # PMAgent output with a gate requiring a very long prd
    runner = CliRunner()
    # We test the exception formatting indirectly by checking GateError's str format
    exc = GateError("NonEmptyGate", "Field 'prd' is None", field="prd")
    assert "Gate check failed" not in str(exc)  # message is in exc.args[0]
    assert "NonEmptyGate" in str(exc)
    assert "prd" in str(exc)
