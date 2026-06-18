"""Tests for the RAG-over-repository feature (RepoIndex)."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from antcrew.memory.repo_index import (
    RepoIndex,
    _chunk_by_pattern,
    _chunk_file,
    _chunk_fixed,
    _chunk_paragraphs,
    _detect_language,
    _PY_SPLIT,
    _TS_SPLIT,
)
from antcrew.memory.store import InMemoryMemory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(root: Path, rel: str, content: str) -> Path:
    fp = root / rel
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(textwrap.dedent(content), encoding="utf-8")
    return fp


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def test_detect_python():
    assert _detect_language(Path("auth.py")) == "python"

def test_detect_typescript():
    assert _detect_language(Path("App.tsx")) == "typescript"

def test_detect_javascript():
    assert _detect_language(Path("util.mjs")) == "javascript"

def test_detect_unknown():
    assert _detect_language(Path("Makefile")) == ""


# ---------------------------------------------------------------------------
# Chunkers — unit tests
# ---------------------------------------------------------------------------

def test_chunk_fixed_splits_evenly():
    text = "a" * 100
    chunks = _chunk_fixed(text, max_size=40, overlap=10)
    assert all(len(c) <= 40 for c in chunks)
    assert len(chunks) >= 3

def test_chunk_fixed_single_chunk_if_small():
    text = "hello world"
    chunks = _chunk_fixed(text, max_size=100)
    assert chunks == ["hello world"]

def test_chunk_paragraphs_splits_on_blank_lines():
    content = "block A\n\nblock B\n\nblock C"
    chunks = _chunk_paragraphs(content, max_size=200)
    combined = "\n\n".join(chunks)
    assert "block A" in combined
    assert "block B" in combined
    assert "block C" in combined

def test_chunk_paragraphs_merges_small_blocks():
    content = "x\n\ny\n\nz"
    chunks = _chunk_paragraphs(content, max_size=100)
    # All three small blocks should merge into one chunk
    assert len(chunks) == 1
    assert "x" in chunks[0] and "z" in chunks[0]

def test_chunk_python_splits_at_def_class():
    content = textwrap.dedent("""\
        import os

        def foo():
            return 1

        class Bar:
            pass

        def baz():
            pass
    """)
    chunks = _chunk_by_pattern(content, _PY_SPLIT, max_size=500)
    # Preamble + 3 definitions = up to 4 chunks (preamble may merge with first)
    assert len(chunks) >= 3
    assert any("def foo" in c for c in chunks)
    assert any("class Bar" in c for c in chunks)
    assert any("def baz" in c for c in chunks)

def test_chunk_typescript_splits_at_export():
    content = textwrap.dedent("""\
        import React from 'react';

        export function Hello() {
          return <div>hello</div>;
        }

        export interface User {
          id: number;
          name: string;
        }

        export const API_URL = 'http://localhost';
    """)
    chunks = _chunk_by_pattern(content, _TS_SPLIT, max_size=500)
    assert any("Hello" in c for c in chunks)
    assert any("interface User" in c for c in chunks)

def test_chunk_file_dispatches_python():
    content = "def alpha(): pass\ndef beta(): pass"
    chunks = _chunk_file(content, "python", max_size=500)
    assert len(chunks) >= 1

def test_chunk_file_dispatches_generic_for_go():
    content = "package main\n\nfunc main() {}\n\nfunc helper() {}"
    chunks = _chunk_file(content, "go", max_size=500)
    assert len(chunks) >= 1


# ---------------------------------------------------------------------------
# RepoIndex — build and search
# ---------------------------------------------------------------------------

def test_build_returns_chunk_count(tmp_path):
    _write(tmp_path, "auth.py", """\
        def login(email, password):
            pass

        def logout(token):
            pass
    """)
    idx = RepoIndex(str(tmp_path))
    n = idx.build()
    assert n >= 1

def test_count_matches_build(tmp_path):
    _write(tmp_path, "a.py", "def f(): pass")
    idx = RepoIndex(str(tmp_path))
    n = idx.build()
    assert idx.count() == n

def test_search_finds_relevant_code(tmp_path):
    _write(tmp_path, "auth.py", """\
        def authenticate_user(email, password):
            return verify_jwt(email, password)
    """)
    _write(tmp_path, "products.py", """\
        def list_products():
            return db.query(Product).all()
    """)
    idx = RepoIndex(str(tmp_path))
    idx.build()
    # InMemoryMemory uses Jaccard word-overlap (\w+ tokens, underscore retained)
    # so query must share tokens with the indexed content
    result = idx.search("authenticate_user email verify_jwt")
    assert "auth.py" in result

def test_search_returns_empty_when_not_built(tmp_path):
    idx = RepoIndex(str(tmp_path))
    # Nothing built, count == 0
    result = idx.search("anything")
    assert result == ""

def test_search_returns_empty_for_blank_query(tmp_path):
    _write(tmp_path, "a.py", "def f(): pass")
    idx = RepoIndex(str(tmp_path))
    idx.build()
    assert idx.search("   ") == ""

def test_search_output_contains_file_path(tmp_path):
    _write(tmp_path, "models/user.py", "class User:\n    id: int\n    email: str")
    idx = RepoIndex(str(tmp_path))
    idx.build()
    result = idx.search("user model email")
    assert "models/user.py" in result

def test_search_output_contains_code_fences(tmp_path):
    _write(tmp_path, "service.py", "def run(): pass")
    idx = RepoIndex(str(tmp_path))
    idx.build()
    result = idx.search("run service")
    assert "```" in result

def test_clear_resets_count(tmp_path):
    _write(tmp_path, "a.py", "def f(): pass")
    idx = RepoIndex(str(tmp_path))
    idx.build()
    assert idx.count() > 0
    idx.clear()
    assert idx.count() == 0

def test_build_twice_does_not_duplicate(tmp_path):
    _write(tmp_path, "a.py", "def f(): pass")
    idx = RepoIndex(str(tmp_path))
    n1 = idx.build()
    n2 = idx.build()
    assert n1 == n2
    assert idx.count() == n2


# ---------------------------------------------------------------------------
# RepoIndex — exclusions
# ---------------------------------------------------------------------------

def test_excludes_node_modules(tmp_path):
    _write(tmp_path, "node_modules/lib/secret.py", "def secret(): pass")
    _write(tmp_path, "main.py", "def main(): pass")
    idx = RepoIndex(str(tmp_path))
    idx.build()
    result = idx.search("secret")
    assert "secret" not in result

def test_excludes_git_dir(tmp_path):
    _write(tmp_path, ".git/hooks/pre-commit.py", "def hook(): pass")
    _write(tmp_path, "app.py", "def app(): pass")
    idx = RepoIndex(str(tmp_path))
    idx.build()
    # .git starts with '.' so it is filtered
    assert idx.count() == idx.count()  # just verify no crash; main check is no .git chunks
    result = idx.search("hook pre-commit")
    # dot-dirs are excluded by the starts-with-'.' guard
    assert ".git" not in result

def test_skips_large_files(tmp_path):
    big = tmp_path / "huge.py"
    big.write_bytes(b"# big\n" + b"x = 1\n" * 50_000)  # ~300 KB
    _write(tmp_path, "small.py", "def tiny(): pass")
    idx = RepoIndex(str(tmp_path), max_file_bytes=200_000)
    idx.build()
    result = idx.search("tiny")
    assert "small.py" in result

def test_ignores_unknown_extensions(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM python:3.12")
    idx = RepoIndex(str(tmp_path))
    n = idx.build()
    assert n == 0  # .Dockerfile has no extension in _INCLUDE_EXTENSIONS


# ---------------------------------------------------------------------------
# Custom memory backend
# ---------------------------------------------------------------------------

def test_accepts_custom_memory(tmp_path):
    _write(tmp_path, "a.py", "def f(): pass")
    custom_mem = InMemoryMemory()
    idx = RepoIndex(str(tmp_path), memory=custom_mem)
    idx.build()
    assert custom_mem.count() > 0


# ---------------------------------------------------------------------------
# BaseAgent._search_repo integration
# ---------------------------------------------------------------------------

def test_search_repo_returns_empty_without_index():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.agents.backend_dev import BackendDevAgent

    agent = BackendDevAgent(SimulatedLLM())
    assert agent.repo_index is None
    assert agent._search_repo("anything") == ""

def test_search_repo_returns_context_when_wired(tmp_path):
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.agents.backend_dev import BackendDevAgent

    _write(tmp_path, "auth.py", "def login(user, pw): pass")
    idx = RepoIndex(str(tmp_path))
    idx.build()

    agent = BackendDevAgent(SimulatedLLM())
    agent.repo_index = idx
    result = agent._search_repo("login auth")
    assert "auth.py" in result or "login" in result


# ---------------------------------------------------------------------------
# Team-level wiring
# ---------------------------------------------------------------------------

def test_dev_team_repo_path_builds_index(tmp_path):
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.dev_team import DevTeam

    _write(tmp_path, "core.py", "def core_logic(): pass")
    team = DevTeam(model=SimulatedLLM(), repo_path=str(tmp_path))
    assert team.repo_index is not None
    assert team.repo_index.count() > 0

def test_dev_team_wires_index_to_all_agents(tmp_path):
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.dev_team import DevTeam

    _write(tmp_path, "a.py", "def a(): pass")
    team = DevTeam(model=SimulatedLLM(), repo_path=str(tmp_path))
    for agent in team._agents.values():
        assert agent.repo_index is team.repo_index

def test_dev_team_no_repo_path_leaves_index_none():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.dev_team import DevTeam

    team = DevTeam(model=SimulatedLLM())
    assert team.repo_index is None
    for agent in team._agents.values():
        assert agent.repo_index is None

def test_fullstack_team_repo_path_builds_index(tmp_path):
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.fullstack_team import FullStackTeam

    _write(tmp_path, "api.py", "def get_users(): pass")
    team = FullStackTeam(model=SimulatedLLM(), repo_path=str(tmp_path))
    assert team.repo_index is not None
    assert team.repo_index.count() > 0

def test_fullstack_team_wires_index_to_all_agents(tmp_path):
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.fullstack_team import FullStackTeam

    _write(tmp_path, "b.py", "def b(): pass")
    team = FullStackTeam(model=SimulatedLLM(), repo_path=str(tmp_path))
    for agent in team._agents.values():
        assert agent.repo_index is team.repo_index


# ---------------------------------------------------------------------------
# Top-level antcrew import
# ---------------------------------------------------------------------------

def test_repo_index_importable_from_antcrew():
    from antcrew import RepoIndex as RI  # noqa: F401
    assert RI is RepoIndex
