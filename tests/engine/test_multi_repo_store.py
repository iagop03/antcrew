"""Tests for MultiRepoStore, _load_existing_codebase, and _ensure_conftest."""
from __future__ import annotations

from pathlib import Path

import pytest

from antcrew_engine.engine import (
    Artifact, ArtifactDelta, ArtifactId, ArtifactKind,
    FilesystemStore, MemoryStore, MultiRepoStore,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def repos(tmp_path):
    return {
        "backend":  tmp_path / "backend",
        "frontend": tmp_path / "frontend",
        "shared":   tmp_path / "shared",
    }


@pytest.fixture
def store(repos):
    return MultiRepoStore(
        repos=repos,
        routes={"src/api/": "backend", "src/ui/": "frontend"},
        default="shared",
    )


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

class TestMultiRepoStoreRouting:
    def test_api_prefix_routes_to_backend(self, store, repos):
        art = Artifact(
            id=ArtifactId("main.py"), kind=ArtifactKind.SOURCE,
            content="# api", metadata={"file_path": "src/api/main.py"},
        )
        store.write(art)
        assert (repos["backend"] / "src" / "api" / "main.py").exists()
        assert not (repos["frontend"] / "src" / "api" / "main.py").exists()

    def test_ui_prefix_routes_to_frontend(self, store, repos):
        art = Artifact(
            id=ArtifactId("app.js"), kind=ArtifactKind.SOURCE,
            content="// ui", metadata={"file_path": "src/ui/app.js"},
        )
        store.write(art)
        assert (repos["frontend"] / "src" / "ui" / "app.js").exists()

    def test_unmatched_prefix_routes_to_default(self, store, repos):
        art = Artifact(
            id=ArtifactId("README.md"), kind=ArtifactKind.DOCUMENTATION,
            content="# Docs", metadata={"file_path": "README.md"},
        )
        store.write(art)
        assert (repos["shared"] / "README.md").exists()

    def test_longer_prefix_wins_over_shorter(self, tmp_path):
        store = MultiRepoStore(
            repos={"specific": tmp_path / "specific", "broad": tmp_path / "broad"},
            routes={"src/": "broad", "src/api/": "specific"},
            default="broad",
        )
        art = Artifact(
            id=ArtifactId("v.py"), kind=ArtifactKind.SOURCE,
            content="x", metadata={"file_path": "src/api/v.py"},
        )
        store.write(art)
        assert (tmp_path / "specific" / "src" / "api" / "v.py").exists()
        assert not (tmp_path / "broad" / "src" / "api" / "v.py").exists()

    def test_invalid_default_raises(self, repos):
        with pytest.raises(ValueError, match="default repo"):
            MultiRepoStore(repos=repos, routes={}, default="nonexistent")


# ---------------------------------------------------------------------------
# Read / has / delete
# ---------------------------------------------------------------------------

class TestMultiRepoStoreReadOps:
    def test_read_from_correct_repo(self, store):
        art = Artifact(
            id=ArtifactId("models.py"), kind=ArtifactKind.SOURCE,
            content="class M: pass", metadata={"file_path": "src/api/models.py"},
        )
        store.write(art)
        result = store.read(ArtifactId("models.py"))
        assert result is not None
        assert "class M" in result.content

    def test_read_nonexistent_returns_none(self, store):
        assert store.read(ArtifactId("ghost.py")) is None

    def test_has_after_write(self, store):
        art = Artifact(
            id=ArtifactId("x.py"), kind=ArtifactKind.SOURCE,
            content="x", metadata={"file_path": "src/api/x.py"},
        )
        store.write(art)
        assert store.has(ArtifactId("x.py"))

    def test_has_returns_false_for_missing(self, store):
        assert not store.has(ArtifactId("missing"))

    def test_delete_removes_from_all_repos(self, store):
        # Write same id to one store, delete should clean up
        art = Artifact(
            id=ArtifactId("del.py"), kind=ArtifactKind.SOURCE,
            content="x", metadata={"file_path": "src/api/del.py"},
        )
        store.write(art)
        assert store.has(ArtifactId("del.py"))
        store.delete(ArtifactId("del.py"))
        assert not store.has(ArtifactId("del.py"))


# ---------------------------------------------------------------------------
# list()
# ---------------------------------------------------------------------------

class TestMultiRepoStoreList:
    def test_list_aggregates_across_repos(self, store):
        store.write(Artifact(
            id=ArtifactId("a.py"), kind=ArtifactKind.SOURCE,
            content="a", metadata={"file_path": "src/api/a.py"},
        ))
        store.write(Artifact(
            id=ArtifactId("b.js"), kind=ArtifactKind.SOURCE,
            content="b", metadata={"file_path": "src/ui/b.js"},
        ))
        store.write(Artifact(
            id=ArtifactId("README.md"), kind=ArtifactKind.DOCUMENTATION,
            content="r", metadata={"file_path": "README.md"},
        ))
        assert len(store.list()) == 3

    def test_list_by_kind(self, store):
        store.write(Artifact(
            id=ArtifactId("a.py"), kind=ArtifactKind.SOURCE,
            content="x", metadata={"file_path": "src/api/a.py"},
        ))
        store.write(Artifact(
            id=ArtifactId("r.md"), kind=ArtifactKind.DOCUMENTATION,
            content="doc", metadata={"file_path": "README.md"},
        ))
        sources = store.list(ArtifactKind.SOURCE)
        assert len(sources) == 1
        assert sources[0].id == ArtifactId("a.py")

    def test_list_deduplicates_by_id(self, tmp_path):
        # If two stores somehow have the same artifact id, list() returns it once
        store = MultiRepoStore(
            repos={"a": tmp_path / "a", "b": tmp_path / "b"},
            routes={},
            default="a",
        )
        art = Artifact(id=ArtifactId("x"), kind=ArtifactKind.GENERIC, content="v")
        FilesystemStore(tmp_path / "a").write(art)
        FilesystemStore(tmp_path / "b").write(art)
        assert len(store.list()) == 1


# ---------------------------------------------------------------------------
# apply(delta)
# ---------------------------------------------------------------------------

class TestMultiRepoStoreApply:
    def test_apply_created(self, store):
        art = Artifact(
            id=ArtifactId("c.py"), kind=ArtifactKind.SOURCE,
            content="x", metadata={"file_path": "src/api/c.py"},
        )
        store.apply(ArtifactDelta(created=(art,)))
        assert store.has(ArtifactId("c.py"))

    def test_apply_modified(self, store):
        art1 = Artifact(
            id=ArtifactId("m.py"), kind=ArtifactKind.SOURCE,
            content="v1", metadata={"file_path": "src/api/m.py"},
        )
        art2 = Artifact(
            id=ArtifactId("m.py"), kind=ArtifactKind.SOURCE,
            content="v2", metadata={"file_path": "src/api/m.py"},
        )
        store.apply(ArtifactDelta(created=(art1,)))
        store.apply(ArtifactDelta(modified=(art2,)))
        assert store.read(ArtifactId("m.py")).content == "v2"


# ---------------------------------------------------------------------------
# stores() accessor
# ---------------------------------------------------------------------------

class TestMultiRepoStoreStores:
    def test_stores_returns_dict(self, store):
        s = store.stores()
        assert set(s.keys()) == {"backend", "frontend", "shared"}
        assert all(isinstance(v, FilesystemStore) for v in s.values())

    def test_repr_contains_repo_names(self, store):
        r = repr(store)
        assert "backend" in r
        assert "frontend" in r


# ---------------------------------------------------------------------------
# _load_existing_codebase
# ---------------------------------------------------------------------------

class TestLoadExistingCodebase:
    def test_loads_py_files_as_source(self, tmp_path):
        from antcrew_engine.cli.app import _load_existing_codebase

        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "main.py").write_text("print('hello')")
        (proj / "utils.py").write_text("def fn(): pass")

        store = MemoryStore()
        n = _load_existing_codebase(store, proj, "Fix something")
        assert n >= 2
        sources = store.list(ArtifactKind.SOURCE)
        assert len(sources) == 2

    def test_skips_venv_and_pycache(self, tmp_path):
        from antcrew_engine.cli.app import _load_existing_codebase

        proj = tmp_path / "proj"
        (proj / "src").mkdir(parents=True)
        (proj / "src" / "app.py").write_text("x=1")
        (proj / "venv" / "lib").mkdir(parents=True)
        (proj / "venv" / "lib" / "skip.py").write_text("# venv")
        (proj / "__pycache__").mkdir()
        (proj / "__pycache__" / "app.cpython-311.pyc").write_bytes(b"\x00")

        store = MemoryStore()
        n = _load_existing_codebase(store, proj, "Test")
        assert n == 1

    def test_writes_stub_planning_artifacts(self, tmp_path):
        from antcrew_engine.cli.app import _load_existing_codebase

        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "main.py").write_text("x=1")

        store = MemoryStore()
        _load_existing_codebase(store, proj, "Fix auth")

        assert store.has(ArtifactId("requirements"))
        assert store.has(ArtifactId("architecture"))
        assert store.has(ArtifactId("task_graph"))

    def test_loads_markdown_as_documentation(self, tmp_path):
        from antcrew_engine.cli.app import _load_existing_codebase

        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "README.md").write_text("# Docs")

        store = MemoryStore()
        _load_existing_codebase(store, proj, "Goal")
        docs = store.list(ArtifactKind.DOCUMENTATION)
        assert any("README" in str(a.id) for a in docs)


# ---------------------------------------------------------------------------
# _ensure_conftest
# ---------------------------------------------------------------------------

class TestEnsureConftest:
    def test_creates_conftest_when_missing(self, tmp_path):
        from antcrew_engine.capabilities.test_runner import _ensure_conftest

        _ensure_conftest(tmp_path)
        conftest = tmp_path / "conftest.py"
        assert conftest.exists()
        assert "sys.path" in conftest.read_text()

    def test_does_not_overwrite_existing_conftest(self, tmp_path):
        from antcrew_engine.capabilities.test_runner import _ensure_conftest

        existing = tmp_path / "conftest.py"
        existing.write_text("# custom conftest\nimport custom_plugin\n")
        _ensure_conftest(tmp_path)
        assert "custom_plugin" in existing.read_text()

    def test_idempotent_on_repeated_calls(self, tmp_path):
        from antcrew_engine.capabilities.test_runner import _ensure_conftest

        _ensure_conftest(tmp_path)
        mtime1 = (tmp_path / "conftest.py").stat().st_mtime
        _ensure_conftest(tmp_path)
        mtime2 = (tmp_path / "conftest.py").stat().st_mtime
        assert mtime1 == mtime2  # file not touched twice
