"""Property-based tests for antcrew gates using Hypothesis.

These tests complement the example-based tests in test_gates.py. Hypothesis
generates hundreds of random inputs per test, catching edge cases (empty strings,
Unicode, deeply nested JSON, boundary numbers) that hand-picked examples miss.

Gates are correctness-critical: a JsonGate that accepts invalid JSON or a
NonEmptyGate that accepts whitespace-only strings breaks downstream pipeline
steps that expect well-formed data.
"""
from __future__ import annotations

import json

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st
from pydantic import BaseModel

from antcrew.core.gates import (
    AllGate,
    AnyGate,
    GateResult,
    JsonGate,
    NonEmptyGate,
    SchemaGate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state(field: str, value) -> dict:
    return {field: value}


# ---------------------------------------------------------------------------
# NonEmptyGate property tests
# ---------------------------------------------------------------------------

class TestNonEmptyGateProperty:
    @given(st.text(min_size=1).filter(lambda s: s.strip()))
    def test_non_whitespace_string_passes(self, s: str):
        gate = NonEmptyGate(field="x")
        result = gate.check(_state("x", s))
        assert result.passed

    @given(st.text(alphabet=" \t\n\r"))
    def test_whitespace_only_fails(self, s: str):
        gate = NonEmptyGate(field="x")
        result = gate.check(_state("x", s))
        assert not result.passed

    def test_none_fails(self):
        assert not NonEmptyGate(field="x").check({"x": None}).passed

    def test_missing_field_fails(self):
        assert not NonEmptyGate(field="x").check({}).passed

    @given(st.lists(st.integers(), min_size=1))
    def test_non_empty_list_passes(self, lst):
        gate = NonEmptyGate(field="items")
        assert gate.check(_state("items", lst)).passed

    def test_empty_list_fails(self):
        assert not NonEmptyGate(field="items").check({"items": []}).passed

    @given(st.integers())
    def test_integer_value_passes(self, n: int):
        gate = NonEmptyGate(field="n")
        result = gate.check(_state("n", n))
        assert result.passed

    @given(st.floats(allow_nan=False, allow_infinity=False))
    def test_float_value_passes(self, f: float):
        gate = NonEmptyGate(field="f")
        assert gate.check(_state("f", f)).passed


# ---------------------------------------------------------------------------
# JsonGate property tests
# ---------------------------------------------------------------------------

class TestJsonGateProperty:
    @given(st.recursive(
        st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False) | st.text(),
        lambda children: st.lists(children) | st.dictionaries(st.text(), children),
        max_leaves=6,
    ))
    def test_valid_json_object_passes(self, obj):
        """Any JSON-serialisable Python object encodes to a string that JsonGate must accept."""
        json_str = json.dumps(obj)
        gate = JsonGate(field="data")
        result = gate.check(_state("data", json_str))
        assert result.passed, f"JsonGate rejected valid JSON string: {json_str[:80]!r}"

    @given(st.text().filter(lambda s: _is_invalid_json(s)))
    def test_invalid_json_string_fails(self, s: str):
        gate = JsonGate(field="data")
        result = gate.check(_state("data", s))
        assert not result.passed

    @given(st.dictionaries(st.text(min_size=1, max_size=10), st.text(max_size=10), min_size=1, max_size=3))
    def test_non_empty_dict_value_stringified_fails(self, d: dict):
        """JsonGate stringifies non-str values with str(). A non-empty Python dict
        str repr uses single quotes — NOT valid JSON."""
        gate = JsonGate(field="data")
        result = gate.check(_state("data", d))
        # str({"k": "v"}) → "{'k': 'v'}" — single-quoted keys, invalid JSON
        assert not result.passed, f"Expected JsonGate to reject str({d!r}) = {str(d)!r}"

    def test_none_field_fails(self):
        assert not JsonGate(field="data").check({"data": None}).passed

    def test_missing_field_fails(self):
        assert not JsonGate(field="data").check({}).passed

    @given(st.integers())
    def test_integer_value_passes(self, n: int):
        gate = JsonGate(field="n")
        assert gate.check(_state("n", n)).passed

    @given(st.text(min_size=1, alphabet=st.characters(blacklist_categories=("Cs",))))
    def test_unicode_text_not_valid_json_fails(self, s: str):
        assume(not _is_valid_json(s))
        gate = JsonGate(field="s")
        assert not gate.check(_state("s", s)).passed


# ---------------------------------------------------------------------------
# SchemaGate property tests
# ---------------------------------------------------------------------------

class _Item(BaseModel):
    name: str
    count: int


class TestSchemaGateProperty:
    @given(st.text(min_size=1), st.integers())
    def test_valid_dict_passes(self, name: str, count: int):
        gate = SchemaGate(field="item", schema=_Item)
        result = gate.check(_state("item", {"name": name, "count": count}))
        assert result.passed

    @given(st.text(min_size=1), st.integers())
    def test_valid_json_string_passes(self, name: str, count: int):
        gate = SchemaGate(field="item", schema=_Item)
        payload = json.dumps({"name": name, "count": count})
        result = gate.check(_state("item", payload))
        assert result.passed

    @given(st.text(min_size=1))
    def test_missing_required_field_fails(self, name: str):
        gate = SchemaGate(field="item", schema=_Item)
        result = gate.check(_state("item", {"name": name}))  # count missing
        assert not result.passed

    @given(st.text(min_size=1), st.text(min_size=1))
    def test_wrong_type_for_int_field_fails(self, name: str, not_int: str):
        # Pydantic v2 coerces numeric strings (e.g. "+0", "42") to int.
        # Exclude any string that Python's int() can parse (same set Pydantic accepts).
        try:
            int(not_int)
            assume(False)  # skip numeric strings
        except (ValueError, OverflowError):
            pass
        gate = SchemaGate(field="item", schema=_Item)
        result = gate.check(_state("item", {"name": name, "count": not_int}))
        assert not result.passed

    def test_none_fails(self):
        assert not SchemaGate(field="item", schema=_Item).check({"item": None}).passed

    def test_missing_field_fails(self):
        assert not SchemaGate(field="item", schema=_Item).check({}).passed


# ---------------------------------------------------------------------------
# AllGate property tests
# ---------------------------------------------------------------------------

class TestAllGateProperty:
    @given(st.lists(st.text(min_size=1).filter(lambda s: s.strip()), min_size=1, max_size=4))
    def test_all_pass_when_all_fields_non_empty(self, fields: list[str]):
        combo = AllGate(*[NonEmptyGate(field=f) for f in fields])
        state = {f: "value" for f in fields}
        assert combo.check(state).passed

    @given(
        st.lists(st.text(min_size=1).filter(lambda s: s.strip()), min_size=2, max_size=4),
        st.integers(min_value=0),
    )
    def test_fails_when_any_field_missing(self, fields: list[str], missing_idx: int):
        missing = fields[missing_idx % len(fields)]
        combo = AllGate(*[NonEmptyGate(field=f) for f in fields])
        state = {f: "value" for f in fields if f != missing}
        assert not combo.check(state).passed

    def test_empty_all_gate_raises(self):
        """AllGate() with no sub-gates raises ValueError — enforces contract."""
        import pytest
        with pytest.raises(ValueError):
            AllGate()


# ---------------------------------------------------------------------------
# AnyGate property tests
# ---------------------------------------------------------------------------

class TestAnyGateProperty:
    @given(
        st.lists(st.text(min_size=1).filter(lambda s: s.strip()), min_size=2, max_size=4),
        st.integers(min_value=0),
    )
    def test_passes_when_at_least_one_field_present(self, fields: list[str], present_idx: int):
        present = fields[present_idx % len(fields)]
        combo = AnyGate(*[NonEmptyGate(field=f) for f in fields])
        state = {present: "value"}  # only one field populated
        assert combo.check(state).passed

    @given(st.lists(st.text(min_size=1).filter(lambda s: s.strip()), min_size=1, max_size=4))
    def test_fails_when_all_fields_missing(self, fields: list[str]):
        combo = AnyGate(*[NonEmptyGate(field=f) for f in fields])
        assert not combo.check({}).passed  # no fields in state at all

    def test_empty_any_gate_raises(self):
        """AnyGate() with no sub-gates raises ValueError — enforces contract."""
        import pytest
        with pytest.raises(ValueError):
            AnyGate()


# ---------------------------------------------------------------------------
# GateResult invariants
# ---------------------------------------------------------------------------

class TestGateResultInvariants:
    @given(st.booleans(), st.text(), st.text() | st.none())
    def test_passed_field_matches_constructor(self, passed: bool, message: str, field):
        r = GateResult(passed, message, field)
        assert r.passed == passed
        assert r.message == message

    @given(st.text(min_size=1))
    def test_message_preserved_on_failure(self, msg: str):
        r = GateResult(False, msg)
        assert r.message == msg

    def test_passed_attribute_is_the_truth_check(self):
        """GateResult is a frozen dataclass without __bool__; callers use .passed."""
        r_ok = GateResult(True, "ok")
        r_fail = GateResult(False, "fail")
        assert r_ok.passed is True
        assert r_fail.passed is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_valid_json(s: str) -> bool:
    try:
        json.loads(s)
        return True
    except (json.JSONDecodeError, ValueError):
        return False


def _is_invalid_json(s: str) -> bool:
    return not _is_valid_json(s)
