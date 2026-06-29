"""Lightweight state transforms for CustomTeam pipelines.

Operators run between agent steps and reshape the shared state dict without
making an LLM call.  They implement the same ``run(state) -> dict`` interface
as agents, so they slot directly into :class:`~antcrew.teams.custom_team.CustomTeam`
step lists.

Usage — Python::

    from antcrew import CustomTeam, RenameOp, DropOp, SetOp
    from antcrew.models.simulated import SimulatedLLM

    team = CustomTeam([
        {"name": "planner", "system_prompt": "Plan it.", "output_key": "raw_plan"},
        RenameOp("raw_plan", "plan"),          # rename key
        DropOp("debug_info"),                  # remove key
        SetOp("status", "ready"),              # constant key
        {"name": "executor", "system_prompt": "Execute: {plan}", "output_key": "result"},
    ], llm=SimulatedLLM())

Usage — YAML::

    steps:
      - name: planner
        system_prompt: "Plan it."
        output_key: raw_plan

      - operator: rename
        from: raw_plan
        to: plan

      - operator: drop
        keys: [debug_info, temp]

      - operator: set
        key: status
        value: "ready"

      - operator: copy
        from: plan
        to: plan_backup

      - operator: merge
        keys: [backend_code, frontend_code]
        to: all_code
        sep: "\\n\\n---\\n\\n"

      - operator: map
        key: plan
        fn: "lambda x: x.strip()"   # evaluated with eval(); use with care

      - name: executor
        system_prompt: "Execute: {plan}"
        output_key: result
"""
from __future__ import annotations

from typing import Any, Callable

# ---------------------------------------------------------------------------
# Sentinel: return this value mapped to a key to remove that key from state
# ---------------------------------------------------------------------------

_DELETE = object()


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class BaseOperator:
    """Lightweight state transform with the same interface as a team agent.

    Subclass and override :meth:`apply`.  The ``run(state) -> dict`` wrapper
    is inherited and makes the operator compatible with the CustomTeam step
    system.
    """

    name: str = "operator"

    def apply(self, state: dict) -> dict:
        """Return a patch dict.

        Values equal to :data:`_DELETE` instruct the caller to remove the key
        from state rather than set it.
        """
        raise NotImplementedError

    def run(self, state: dict) -> dict:
        """Agent-compatible entry point — delegates to :meth:`apply`."""
        return self.apply(state)


# ---------------------------------------------------------------------------
# Built-in operators
# ---------------------------------------------------------------------------

class RenameOp(BaseOperator):
    """Move ``state[from_key]`` to ``state[to_key]``, deleting the original.

    No-op when *from_key* is absent from state.
    """

    def __init__(self, from_key: str, to_key: str) -> None:
        self._from = from_key
        self._to = to_key
        self.name = f"rename:{from_key}→{to_key}"

    def apply(self, state: dict) -> dict:
        if self._from not in state:
            return {}
        return {self._to: state[self._from], self._from: _DELETE}


class CopyOp(BaseOperator):
    """Copy ``state[from_key]`` to ``state[to_key]``, keeping the original.

    No-op when *from_key* is absent from state.
    """

    def __init__(self, from_key: str, to_key: str) -> None:
        self._from = from_key
        self._to = to_key
        self.name = f"copy:{from_key}→{to_key}"

    def apply(self, state: dict) -> dict:
        if self._from not in state:
            return {}
        return {self._to: state[self._from]}


class DropOp(BaseOperator):
    """Remove one or more keys from state.

    Keys absent from state are silently ignored.
    """

    def __init__(self, *keys: str) -> None:
        self._keys = keys
        self.name = f"drop:{','.join(keys)}"

    def apply(self, state: dict) -> dict:
        return {k: _DELETE for k in self._keys if k in state}


class SetOp(BaseOperator):
    """Set ``state[key]`` to a constant *value*.

    Useful for injecting configuration or status flags between steps.
    """

    def __init__(self, key: str, value: Any) -> None:
        self._key = key
        self._value = value
        self.name = f"set:{key}"

    def apply(self, state: dict) -> dict:
        return {self._key: self._value}


class MapOp(BaseOperator):
    """Apply a Python callable to transform ``state[key]`` in-place.

    No-op when *key* is absent from state.

    Args:
        key: The state key to transform.
        fn:  A callable ``(value) -> new_value``.
    """

    def __init__(self, key: str, fn: Callable[[Any], Any]) -> None:
        self._key = key
        self._fn = fn
        self.name = f"map:{key}"

    def apply(self, state: dict) -> dict:
        if self._key not in state:
            return {}
        return {self._key: self._fn(state[self._key])}


class MergeOp(BaseOperator):
    """Concatenate multiple string state values into a single key.

    All source keys are coerced to ``str`` and joined with *sep*.
    Missing source keys are skipped (not treated as errors).

    Args:
        keys:   Source state keys to merge, in order.
        to_key: Destination key for the merged string.
        sep:    Separator between values (default: two newlines).
    """

    def __init__(self, keys: list[str], to_key: str, sep: str = "\n\n") -> None:
        self._keys = keys
        self._to = to_key
        self._sep = sep
        self.name = f"merge:{'+'.join(keys)}→{to_key}"

    def apply(self, state: dict) -> dict:
        parts = [str(state[k]) for k in self._keys if k in state]
        return {self._to: self._sep.join(parts)}


# ---------------------------------------------------------------------------
# YAML / dict builder
# ---------------------------------------------------------------------------

def build_operator(cfg: dict) -> BaseOperator:
    """Build a :class:`BaseOperator` from a YAML/dict step config.

    The ``operator`` key selects the type; remaining keys are type-specific.

    Supported types:
    - ``rename``: ``from``, ``to``
    - ``copy``:   ``from``, ``to``
    - ``drop``:   ``keys`` (list)
    - ``set``:    ``key``, ``value``
    - ``merge``:  ``keys`` (list), ``to``, optional ``sep``
    - ``map``:    ``key``, ``fn`` (Python lambda source string)

    Raises:
        ValueError: If the operator type is unknown or required keys are missing.
    """
    op_type = str(cfg.get("operator", "")).lower()

    if op_type == "rename":
        return RenameOp(str(cfg["from"]), str(cfg["to"]))

    if op_type == "copy":
        return CopyOp(str(cfg["from"]), str(cfg["to"]))

    if op_type == "drop":
        keys = cfg.get("keys") or []
        if isinstance(keys, str):
            keys = [keys]
        return DropOp(*[str(k) for k in keys])

    if op_type == "set":
        return SetOp(str(cfg["key"]), cfg["value"])

    if op_type == "merge":
        keys = list(cfg["keys"])
        return MergeOp([str(k) for k in keys], str(cfg["to"]), str(cfg.get("sep", "\n\n")))

    if op_type == "map":
        raw_fn = str(cfg["fn"])
        try:
            fn = eval(raw_fn)  # noqa: S307 — intentional, documented
        except Exception as exc:
            raise ValueError(f"operator 'map': cannot eval fn={raw_fn!r}: {exc}") from exc
        if not callable(fn):
            raise ValueError(f"operator 'map': fn={raw_fn!r} is not callable")
        return MapOp(str(cfg["key"]), fn)

    raise ValueError(
        f"Unknown operator type {op_type!r}. "
        "Expected: rename, copy, drop, set, merge, map"
    )
