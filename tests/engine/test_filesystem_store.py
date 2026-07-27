"""Tests for FilesystemStore — the disk-backed ArtifactStore implementation."""
from __future__ import annotations

import json

import pytest

from antcrew.engine import (
    Artifact,
    ArtifactDelta,
    ArtifactId,
    ArtifactKind,
    FilesystemStore,
)


@pytest.fixture
def store(tmp_path):
    return FilesystemStore(tmp_path)


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------

class TestFilesystemStoreBasic:
    def test_write_and_read_text_artifact(self, store):
        art = Artifact(id=ArtifactId("requirements"), kind=ArtifactKind.REQUIREMENTS,
                       content="# Requirements\n- Feature A")
        store.write(art)
        result = store.read(ArtifactId("requirements"))
        assert result is not None
        assert "Feature A" in result.content

    def test_read_nonexistent_returns_none(self, store):
        assert store.read(ArtifactId("missing")) is None

    def test_has_after_write(self, store):
        art = Artifact(id=ArtifactId("arch"), kind=ArtifactKind.ARCHITECTURE, content="# Arch")
        store.write(art)
        assert store.has(ArtifactId("arch"))

    def test_has_false_when_missing(self, store):
        assert not store.has(ArtifactId("ghost"))

    def test_delete_removes_artifact(self, store):
        art = Artifact(id=ArtifactId("tmp"), kind=ArtifactKind.GENERIC, content="x")
        store.write(art)
        store.delete(ArtifactId("tmp"))
        assert not store.has(ArtifactId("tmp"))
        assert store.read(ArtifactId("tmp")) is None

    def test_delete_nonexistent_is_noop(self, store):
        store.delete(ArtifactId("nothing"))  # must not raise

    def test_overwrite_updates_content(self, store):
        a1 = Artifact(id=ArtifactId("doc"), kind=ArtifactKind.DOCUMENTATION, content="v1")
        a2 = Artifact(id=ArtifactId("doc"), kind=ArtifactKind.DOCUMENTATION, content="v2")
        store.write(a1)
        store.write(a2)
        assert store.read(ArtifactId("doc")).content == "v2"


# ---------------------------------------------------------------------------
# Content serialisation
# ---------------------------------------------------------------------------

class TestContentSerialisation:
    def test_dict_content_roundtrips(self, store):
        content = {"tasks": [{"id": "t1", "status": "done"}]}
        art = Artifact(id=ArtifactId("task_graph"), kind=ArtifactKind.TASK_GRAPH, content=content)
        store.write(art)
        result = store.read(ArtifactId("task_graph"))
        assert result.content == content

    def test_report_content_roundtrips(self, store):
        content = {"passed": True, "returncode": 0, "output": "1 passed"}
        art = Artifact(id=ArtifactId("test_report"), kind=ArtifactKind.REPORT, content=content)
        store.write(art)
        result = store.read(ArtifactId("test_report"))
        assert result.content["passed"] is True

    def test_source_content_is_plain_text(self, store, tmp_path):
        code = "def add(a, b):\n    return a + b\n"
        art = Artifact(
            id=ArtifactId("src/add.py"), kind=ArtifactKind.SOURCE,
            content=code, metadata={"file_path": "src/add.py"},
        )
        store.write(art)
        on_disk = (tmp_path / "src" / "add.py").read_text()
        assert on_disk == code

    def test_metadata_preserved(self, store):
        art = Artifact(
            id=ArtifactId("src/app.py"), kind=ArtifactKind.SOURCE,
            content="x=1", metadata={"file_path": "src/app.py", "lang": "python"},
        )
        store.write(art)
        result = store.read(ArtifactId("src/app.py"))
        assert result.metadata["lang"] == "python"


# ---------------------------------------------------------------------------
# list()
# ---------------------------------------------------------------------------

class TestFilesystemStoreList:
    def test_list_all(self, store):
        store.write(Artifact(id=ArtifactId("r"), kind=ArtifactKind.REQUIREMENTS, content="r"))
        store.write(Artifact(id=ArtifactId("a"), kind=ArtifactKind.ARCHITECTURE, content="a"))
        assert len(store.list()) == 2

    def test_list_by_kind(self, store):
        store.write(Artifact(id=ArtifactId("r"), kind=ArtifactKind.REQUIREMENTS, content="r"))
        store.write(Artifact(
            id=ArtifactId("src/x.py"), kind=ArtifactKind.SOURCE,
            content="x=1", metadata={"file_path": "src/x.py"},
        ))
        sources = store.list(ArtifactKind.SOURCE)
        assert len(sources) == 1
        assert sources[0].id == ArtifactId("src/x.py")

    def test_list_empty_when_no_match(self, store):
        store.write(Artifact(id=ArtifactId("r"), kind=ArtifactKind.REQUIREMENTS, content="r"))
        assert store.list(ArtifactKind.TEST) == []

    def test_len(self, store):
        store.write(Artifact(id=ArtifactId("a"), kind=ArtifactKind.GENERIC, content="1"))
        store.write(Artifact(id=ArtifactId("b"), kind=ArtifactKind.GENERIC, content="2"))
        assert len(store) == 2


# ---------------------------------------------------------------------------
# apply(delta)
# ---------------------------------------------------------------------------

class TestApplyDelta:
    def test_apply_creates(self, store):
        art = Artifact(id=ArtifactId("r"), kind=ArtifactKind.REQUIREMENTS, content="x")
        store.apply(ArtifactDelta(created=(art,)))
        assert store.has(ArtifactId("r"))

    def test_apply_modifies(self, store):
        a1 = Artifact(id=ArtifactId("r"), kind=ArtifactKind.REQUIREMENTS, content="v1")
        a2 = Artifact(id=ArtifactId("r"), kind=ArtifactKind.REQUIREMENTS, content="v2")
        store.apply(ArtifactDelta(created=(a1,)))
        store.apply(ArtifactDelta(modified=(a2,)))
        assert store.read(ArtifactId("r")).content == "v2"

    def test_apply_deletes(self, store):
        art = Artifact(id=ArtifactId("r"), kind=ArtifactKind.REQUIREMENTS, content="x")
        store.apply(ArtifactDelta(created=(art,)))
        store.apply(ArtifactDelta(deleted=(ArtifactId("r"),)))
        assert not store.has(ArtifactId("r"))

    def test_apply_renames(self, store):
        art = Artifact(id=ArtifactId("old"), kind=ArtifactKind.GENERIC, content="hello")
        store.apply(ArtifactDelta(created=(art,)))
        store.apply(ArtifactDelta(renamed=((ArtifactId("old"), ArtifactId("new")),)))
        assert not store.has(ArtifactId("old"))
        assert store.has(ArtifactId("new"))
        assert store.read(ArtifactId("new")).content == "hello"


# ---------------------------------------------------------------------------
# Persistence across instances
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_survives_new_store_instance(self, tmp_path):
        s1 = FilesystemStore(tmp_path)
        s1.write(Artifact(id=ArtifactId("r"), kind=ArtifactKind.REQUIREMENTS, content="hello"))

        s2 = FilesystemStore(tmp_path)
        result = s2.read(ArtifactId("r"))
        assert result is not None
        assert result.content == "hello"

    def test_manifest_is_valid_json(self, tmp_path):
        store = FilesystemStore(tmp_path)
        store.write(Artifact(id=ArtifactId("x"), kind=ArtifactKind.GENERIC, content="y"))
        manifest_text = (tmp_path / ".antcrew" / "manifest.json").read_text()
        manifest = json.loads(manifest_text)
        assert "x" in manifest

    def test_delete_reflected_in_new_instance(self, tmp_path):
        s1 = FilesystemStore(tmp_path)
        s1.write(Artifact(id=ArtifactId("tmp"), kind=ArtifactKind.GENERIC, content="bye"))
        s1.delete(ArtifactId("tmp"))

        s2 = FilesystemStore(tmp_path)
        assert not s2.has(ArtifactId("tmp"))

    def test_file_path_metadata_used_for_subdir(self, tmp_path):
        store = FilesystemStore(tmp_path)
        store.write(Artifact(
            id=ArtifactId("src/models.py"), kind=ArtifactKind.SOURCE,
            content="class M: pass", metadata={"file_path": "src/models.py"},
        ))
        assert (tmp_path / "src" / "models.py").exists()

    def test_location_set_on_read(self, tmp_path):
        store = FilesystemStore(tmp_path)
        store.write(Artifact(id=ArtifactId("r"), kind=ArtifactKind.REQUIREMENTS, content="req"))
        result = store.read(ArtifactId("r"))
        assert result.location is not None
        assert result.location.exists()
