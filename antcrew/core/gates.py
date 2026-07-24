"""Verification gates — lightweight checks that run after an agent completes.

Gates are registered on a Supervisor and run after the named agent's output
is merged into the TeamState. A failing gate raises GateError and stops the
pipeline with a clear message instead of silently propagating bad output to
the next agent.

Usage::

    from antcrew.core.gates import NonEmptyGate, PythonSyntaxGate
    from antcrew import Supervisor, DevTeam

    supervisor = Supervisor(
        flow=[("pm", "backend_dev"), ("backend_dev", "qa")],
        gates={
            "pm":          NonEmptyGate("prd"),
            "backend_dev": PythonSyntaxGate("code_artifacts"),
        },
    )
    team = DevTeam(supervisor=supervisor)
    team.run("Build authentication module")

Built-in gates
--------------
NonEmptyGate(field, min_length=1)
    Passes when *field* is not None and not empty.

PythonSyntaxGate(field)
    Passes when every Python snippet in *field* is syntactically valid.
    Handles plain strings, markdown code-fenced blocks, and lists of objects
    with a ``.content`` attribute (e.g. CodeArtifact lists).

JsonGate(field)
    Passes when *field* contains valid JSON.

SchemaGate(field, model)
    Passes when *field* validates against a Pydantic v2 model class.

AllGate(*gates)
    Passes when every sub-gate passes.

AnyGate(*gates)
    Passes when at least one sub-gate passes.
"""
from __future__ import annotations

import ast
import json as _json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional, Type

# ---------------------------------------------------------------------------
# Result and exception
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GateResult:
    """Outcome returned by BaseGate.check()."""
    passed: bool
    message: str
    field: Optional[str] = None


class GateError(Exception):
    """Raised when a gate check fails and the pipeline must stop.

    Attributes:
        gate_name: Class name of the gate that failed.
        field:     The state field that was checked (if applicable).
    """
    def __init__(
        self,
        gate_name: str,
        message: str,
        field: Optional[str] = None,
    ) -> None:
        self.gate_name = gate_name
        self.field = field
        field_info = f" (field: {field!r})" if field else ""
        super().__init__(f"[{gate_name}] {message}{field_info}")


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class BaseGate(ABC):
    """Abstract gate.  Subclass and implement :meth:`check`."""

    @abstractmethod
    def check(self, state: dict) -> GateResult:
        """Inspect *state* and return whether the check passed."""

    @property
    def name(self) -> str:
        return type(self).__name__


# ---------------------------------------------------------------------------
# Built-in gates
# ---------------------------------------------------------------------------

class NonEmptyGate(BaseGate):
    """Passes when a state field is non-None and non-empty.

    Args:
        field:      Key to inspect in the TeamState dict.
        min_length: Minimum length for string/list values (default 1).
    """

    def __init__(self, field: str, *, min_length: int = 1) -> None:
        self.field = field
        self.min_length = min_length

    def check(self, state: dict) -> GateResult:
        value = state.get(self.field)
        if value is None:
            return GateResult(False, f"Field '{self.field}' is None", self.field)
        if isinstance(value, str):
            if not value.strip():
                return GateResult(False, f"Field '{self.field}' is blank", self.field)
            if len(value) < self.min_length:
                return GateResult(
                    False,
                    f"Field '{self.field}' too short: {len(value)} < {self.min_length}",
                    self.field,
                )
        elif isinstance(value, (list, tuple)) and len(value) < self.min_length:
            return GateResult(
                False,
                f"Field '{self.field}' has {len(value)} item(s), need ≥ {self.min_length}",
                self.field,
            )
        return GateResult(True, f"Field '{self.field}' is non-empty")


class PythonSyntaxGate(BaseGate):
    """Passes when every Python snippet in a state field is syntactically valid.

    Handles:
    - Plain Python strings
    - Markdown code-fenced blocks (```python ... ```)
    - Lists of strings or objects with a ``.content`` attribute (CodeArtifact)
    """

    _FENCE_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)

    def __init__(self, field: str) -> None:
        self.field = field

    def check(self, state: dict) -> GateResult:
        value = state.get(self.field)
        if value is None:
            return GateResult(False, f"Field '{self.field}' is None", self.field)

        sources = self._extract_sources(value)
        if not sources:
            return GateResult(True, f"Field '{self.field}' has no Python code to check")

        for src in sources:
            code = self._extract_code(src)
            try:
                ast.parse(code)
            except SyntaxError as exc:
                return GateResult(
                    False,
                    f"Syntax error in field '{self.field}': {exc}",
                    self.field,
                )
        return GateResult(True, f"Field '{self.field}' has valid Python syntax")

    # ------------------------------------------------------------------

    def _extract_sources(self, value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)):
            out = []
            for item in value:
                if isinstance(item, str):
                    out.append(item)
                elif hasattr(item, "content"):
                    out.append(str(item.content))
                elif isinstance(item, dict) and "content" in item:
                    out.append(str(item["content"]))
                else:
                    out.append(str(item))
            return out
        if hasattr(value, "content"):
            return [str(value.content)]
        return [str(value)]

    def _extract_code(self, text: str) -> str:
        blocks = self._FENCE_RE.findall(text)
        return "\n".join(blocks) if blocks else text


class JsonGate(BaseGate):
    """Passes when a state field contains valid JSON.

    Args:
        field: Key to inspect. The value must be a string or stringify-able.
    """

    def __init__(self, field: str) -> None:
        self.field = field

    def check(self, state: dict) -> GateResult:
        value = state.get(self.field)
        if value is None:
            return GateResult(False, f"Field '{self.field}' is None", self.field)
        text = value if isinstance(value, str) else str(value)
        try:
            _json.loads(text)
        except _json.JSONDecodeError as exc:
            return GateResult(
                False,
                f"Field '{self.field}' is not valid JSON: {exc}",
                self.field,
            )
        return GateResult(True, f"Field '{self.field}' is valid JSON")


class SchemaGate(BaseGate):
    """Passes when a state field validates against a Pydantic v2 model.

    The field value may be a dict (validated directly), a JSON string
    (parsed first), or an already-instantiated model (re-validated via
    ``model_validate``).

    Args:
        field:  Key to inspect in the TeamState dict.
        schema: Pydantic model class to validate against.
    """

    def __init__(self, field: str, schema: Type) -> None:
        self.field = field
        self.schema = schema

    def check(self, state: dict) -> GateResult:
        value = state.get(self.field)
        if value is None:
            return GateResult(False, f"Field '{self.field}' is None", self.field)
        try:
            if isinstance(value, str):
                value = _json.loads(value)
            self.schema.model_validate(value)
        except Exception as exc:
            return GateResult(
                False,
                f"Field '{self.field}' failed schema validation ({self.schema.__name__}): {exc}",
                self.field,
            )
        return GateResult(True, f"Field '{self.field}' matches schema {self.schema.__name__}")


# ---------------------------------------------------------------------------
# Composable gates
# ---------------------------------------------------------------------------

class AllGate(BaseGate):
    """Passes when ALL sub-gates pass (short-circuits on first failure)."""

    def __init__(self, *gates: BaseGate) -> None:
        if not gates:
            raise ValueError("AllGate requires at least one sub-gate.")
        self._gates = gates

    def check(self, state: dict) -> GateResult:
        for g in self._gates:
            result = g.check(state)
            if not result.passed:
                return result
        return GateResult(True, f"All {len(self._gates)} gates passed")


class AnyGate(BaseGate):
    """Passes when AT LEAST ONE sub-gate passes."""

    def __init__(self, *gates: BaseGate) -> None:
        if not gates:
            raise ValueError("AnyGate requires at least one sub-gate.")
        self._gates = gates

    def check(self, state: dict) -> GateResult:
        messages: list[str] = []
        for g in self._gates:
            result = g.check(state)
            if result.passed:
                return result
            messages.append(result.message)
        return GateResult(False, " | ".join(messages))


# ---------------------------------------------------------------------------
# YAML / dict spec parser
# ---------------------------------------------------------------------------

def parse_gate(raw) -> "BaseGate":
    """Parse a gate from a YAML value — string shorthand or full dict.

    String shorthand ``"type:field"``::

        "non_empty:prd"
        "python_syntax:code_artifacts"
        "json:payload"

    Full dict (all options available)::

        type: non_empty
        field: prd
        min_length: 100

    Composable::

        type: all
        gates:
          - type: non_empty
            field: prd
          - type: python_syntax
            field: code_artifacts

    Raises:
        ValueError: if the gate type is unknown or the spec is malformed.
    """
    if isinstance(raw, str):
        parts = raw.strip().split(":", 1)
        gate_type = parts[0].lower()
        field = parts[1] if len(parts) > 1 else ""
        return _build_gate(gate_type, field, {})

    if isinstance(raw, dict):
        gate_type = str(raw.get("type", "")).lower()
        field = str(raw.get("field", ""))
        if gate_type in ("all", "any"):
            sub_specs = raw.get("gates") or []
            if not sub_specs:
                raise ValueError(f"'{gate_type}' gate requires a non-empty 'gates' list.")
            sub = [parse_gate(s) for s in sub_specs]
            return AllGate(*sub) if gate_type == "all" else AnyGate(*sub)
        return _build_gate(gate_type, field, raw)

    raise ValueError(
        f"Cannot parse gate spec: {raw!r}. "
        "Expected a string ('non_empty:field') or a dict ({'type': 'non_empty', 'field': 'prd'})."
    )


def _build_gate(gate_type: str, field: str, cfg: dict) -> "BaseGate":
    if gate_type == "non_empty":
        return NonEmptyGate(field, min_length=int(cfg.get("min_length", 1)))
    if gate_type in ("python_syntax", "python"):
        return PythonSyntaxGate(field)
    if gate_type == "json":
        return JsonGate(field)
    raise ValueError(
        f"Unknown gate type '{gate_type}'. "
        "Known types: non_empty, python_syntax, json, all, any."
    )
