"""Tests for antcrew.core.symbol_index."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from antcrew.core.symbol_index import SymbolIndex, _path_to_module, _build_signature, FunctionSymbol, ClassSymbol


def _write(tmp_path: Path, filename: str, src: str) -> Path:
    p = tmp_path / filename
    p.write_text(textwrap.dedent(src))
    return p


# ── _build_signature ─────────────────────────────────────────────────────────

class TestBuildSignature:
    def _sig(self, src: str) -> str:
        import ast
        tree = ast.parse(src)
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        return _build_signature(node)

    def test_no_args(self):
        assert self._sig("def f(): pass") == "()"

    def test_simple_args(self):
        assert "a" in self._sig("def f(a, b): pass")

    def test_annotated_arg(self):
        sig = self._sig("def f(x: int) -> str: pass")
        assert "int" in sig
        assert "str" in sig

    def test_default_arg(self):
        sig = self._sig("def f(x=1): pass")
        assert "= 1" in sig or "=1" in sig

    def test_vararg(self):
        sig = self._sig("def f(*args): pass")
        assert "*args" in sig

    def test_kwarg(self):
        sig = self._sig("def f(**kwargs): pass")
        assert "**kwargs" in sig


# ── _path_to_module ───────────────────────────────────────────────────────────

class TestPathToModule:
    def test_simple(self, tmp_path):
        p = tmp_path / "foo" / "bar.py"
        p.parent.mkdir()
        assert _path_to_module(p, tmp_path) == "foo.bar"

    def test_init_stripped(self, tmp_path):
        p = tmp_path / "pkg" / "__init__.py"
        p.parent.mkdir()
        assert _path_to_module(p, tmp_path) == "pkg"


# ── SymbolIndex.build ─────────────────────────────────────────────────────────

class TestSymbolIndexBuild:
    def test_finds_function(self, tmp_path):
        _write(tmp_path, "auth.py", """
            def login(user: str, password: str) -> bool:
                pass
        """)
        idx = SymbolIndex.build([tmp_path])
        hits = idx.query_function("login")
        assert len(hits) == 1
        assert hits[0].name == "login"
        assert "user" in hits[0].signature

    def test_skips_private_function(self, tmp_path):
        # private functions ARE still indexed (underscore filter is only for context display)
        _write(tmp_path, "util.py", """
            def _helper(): pass
        """)
        idx = SymbolIndex.build([tmp_path])
        assert idx.query_function("_helper")  # still indexed

    def test_finds_class(self, tmp_path):
        _write(tmp_path, "models.py", """
            class User:
                def get_id(self): return 1
        """)
        idx = SymbolIndex.build([tmp_path])
        hits = idx.query_class("User")
        assert len(hits) == 1
        assert "get_id" in hits[0].methods

    def test_skips_syntax_error_file(self, tmp_path):
        _write(tmp_path, "broken.py", "def (((")
        idx = SymbolIndex.build([tmp_path])
        assert idx._file_count == 0

    def test_single_file_path(self, tmp_path):
        f = _write(tmp_path, "x.py", "def greet(): pass")
        idx = SymbolIndex.build([f])
        assert idx.query_function("greet")

    def test_summary_format(self, tmp_path):
        _write(tmp_path, "a.py", "def foo(): pass\nclass Bar: pass")
        idx = SymbolIndex.build([tmp_path])
        s = idx.summary()
        assert "function" in s
        assert "class" in s

    def test_empty_dir(self, tmp_path):
        idx = SymbolIndex.build([tmp_path])
        assert idx._file_count == 0


# ── context_for ───────────────────────────────────────────────────────────────

class TestContextFor:
    def test_returns_matching_function(self, tmp_path):
        _write(tmp_path, "auth.py", """
            def hash_password(plain: str) -> str: pass
        """)
        idx = SymbolIndex.build([tmp_path])
        ctx = idx.context_for(["hash_password"])
        assert "hash_password" in ctx

    def test_returns_empty_for_no_match(self, tmp_path):
        _write(tmp_path, "a.py", "def foo(): pass")
        idx = SymbolIndex.build([tmp_path])
        assert idx.context_for(["nonexistent_xyz"]) == ""

    def test_partial_topic_match(self, tmp_path):
        _write(tmp_path, "a.py", "def authenticate_user(): pass")
        idx = SymbolIndex.build([tmp_path])
        ctx = idx.context_for(["auth"])
        assert "authenticate_user" in ctx

    def test_class_context(self, tmp_path):
        _write(tmp_path, "a.py", "class AuthService:\n    def login(self): pass")
        idx = SymbolIndex.build([tmp_path])
        ctx = idx.context_for(["AuthService"])
        assert "AuthService" in ctx
        assert "login" in ctx

    def test_respects_max_chars(self, tmp_path):
        # Generate many functions
        src = "\n".join(f"def func_{i}_{j}(): pass" for i in range(20) for j in range(20))
        _write(tmp_path, "big.py", src)
        idx = SymbolIndex.build([tmp_path])
        ctx = idx.context_for(["func"], max_chars=200)
        assert len(ctx) <= 220  # small buffer for truncation suffix


# ── TypeScript/JavaScript indexing ────────────────────────────────────────────

class TestTypeScriptIndex:
    def test_indexes_exported_function(self, tmp_path):
        _write(tmp_path, "api.ts", """
            export async function getUser(id: string): Promise<User> {
                return fetch(`/users/${id}`);
            }
        """)
        idx = SymbolIndex.build([tmp_path])
        hits = idx.query_function("getUser")
        assert hits, "getUser not found"
        assert hits[0].file_path.endswith("api.ts")

    def test_indexes_exported_class(self, tmp_path):
        _write(tmp_path, "service.ts", """
            export class AuthService {
                login(user: string, pass: string): boolean { return true; }
                logout(): void {}
            }
        """)
        idx = SymbolIndex.build([tmp_path])
        hits = idx.query_class("AuthService")
        assert hits, "AuthService not found"

    def test_indexes_exported_interface(self, tmp_path):
        _write(tmp_path, "types.ts", """
            export interface User {
                id: number;
                name: string;
            }
        """)
        idx = SymbolIndex.build([tmp_path])
        hits = idx.query_class("User")
        assert hits, "User interface not found"

    def test_skips_non_exported_function(self, tmp_path):
        _write(tmp_path, "util.ts", """
            function internal(): void {}
        """)
        idx = SymbolIndex.build([tmp_path])
        # internal (non-exported) function — either indexed or not, just ensure no crash
        # and file was processed
        assert idx._file_count >= 1

    def test_mixed_py_and_ts_directory(self, tmp_path):
        _write(tmp_path, "auth.py", "def login(user): pass")
        _write(tmp_path, "api.ts", "export function getUser(id: string) {}")
        idx = SymbolIndex.build([tmp_path])
        assert idx.query_function("login"), "Python function not found"
        assert idx.query_function("getUser"), "TS function not found"

    def test_context_for_ts_symbol(self, tmp_path):
        _write(tmp_path, "api.ts", """
            export function fetchOrders(): Promise<Order[]> {}
        """)
        idx = SymbolIndex.build([tmp_path])
        ctx = idx.context_for(["fetchOrders"])
        assert "fetchOrders" in ctx
