"""Tests for QAAgent symbol extraction and code-first test generation."""
from __future__ import annotations

import pytest
from antcrew.agents.qa import _extract_symbols_context, _python_symbols


class TestPythonSymbols:
    def test_extracts_public_functions(self):
        src = "def foo(a, b): pass\ndef _private(): pass\n"
        out = _python_symbols(src)
        assert "def foo" in out
        assert "_private" not in out

    def test_extracts_public_classes(self):
        src = "class Service:\n    pass\nclass _Hidden:\n    pass\n"
        out = _python_symbols(src)
        assert "class Service" in out
        assert "_Hidden" not in out

    def test_extracts_method_names(self):
        src = "class A:\n    def run(self, x): pass\n"
        out = _python_symbols(src)
        assert "def run" in out

    def test_empty_file_returns_empty(self):
        assert _python_symbols("") == ""

    def test_syntax_error_returns_empty(self):
        assert _python_symbols("def (((broken:") == ""

    def test_no_public_symbols_returns_empty(self):
        src = "_a = 1\n_b = 2\n"
        assert _python_symbols(src) == ""

    def test_args_listed_in_signature(self):
        src = "def greet(name: str, times: int = 1): pass\n"
        out = _python_symbols(src)
        assert "name" in out
        assert "times" in out


class TestExtractSymbolsContext:
    def test_python_file_returns_symbols_block(self):
        src = "def add(a, b): return a + b\n"
        out = _extract_symbols_context(src, "math_utils.py")
        assert "Public symbols" in out
        assert "def add" in out

    def test_non_python_file_returns_empty(self):
        src = "export function greet() {}"
        assert _extract_symbols_context(src, "utils.ts") == ""

    def test_context_prepended_with_import_hint(self):
        src = "def authenticate(token): pass\n"
        out = _extract_symbols_context(src, "auth.py")
        assert "import exactly" in out
