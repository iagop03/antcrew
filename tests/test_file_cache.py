"""Tests for FileLLMCache — SQLite-backed persistent LLM cache."""
from __future__ import annotations

from antcrew.models.base import Message
from antcrew.models.cache import FileLLMCache, LLMCache
from antcrew.models.simulated import SimulatedLLM

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msgs(text: str = "hello") -> list[Message]:
    return [
        Message(role="system",  content="You are helpful."),
        Message(role="user",    content=text),
    ]


# ---------------------------------------------------------------------------
# Basic get / set
# ---------------------------------------------------------------------------

def test_file_cache_miss(tmp_path):
    cache = FileLLMCache(tmp_path / "cache.db")
    result = cache.get(_msgs(), "ModelA")
    assert result is None
    assert cache.stats()["misses"] == 1

def test_file_cache_set_and_hit(tmp_path):
    cache = FileLLMCache(tmp_path / "cache.db")
    cache.set(_msgs(), "ModelA", "the answer")
    assert cache.get(_msgs(), "ModelA") == "the answer"
    assert cache.stats()["hits"] == 1

def test_file_cache_different_models_different_keys(tmp_path):
    cache = FileLLMCache(tmp_path / "cache.db")
    cache.set(_msgs(), "ModelA", "answer-A")
    cache.set(_msgs(), "ModelB", "answer-B")
    assert cache.get(_msgs(), "ModelA") == "answer-A"
    assert cache.get(_msgs(), "ModelB") == "answer-B"

def test_file_cache_different_messages_different_keys(tmp_path):
    cache = FileLLMCache(tmp_path / "cache.db")
    cache.set(_msgs("hello"), "M", "hi")
    cache.set(_msgs("goodbye"), "M", "bye")
    assert cache.get(_msgs("hello"), "M") == "hi"
    assert cache.get(_msgs("goodbye"), "M") == "bye"


# ---------------------------------------------------------------------------
# Persistence across restarts
# ---------------------------------------------------------------------------

def test_file_cache_persists_after_close(tmp_path):
    db = tmp_path / "cache.db"
    # Write in one instance
    c1 = FileLLMCache(db)
    c1.set(_msgs(), "M", "persisted response")
    c1.close()

    # Read in a fresh instance pointing at the same file
    c2 = FileLLMCache(db)
    assert c2.get(_msgs(), "M") == "persisted response"
    assert c2.stats()["hits"] == 1

def test_file_cache_accumulates_entries(tmp_path):
    db = tmp_path / "cache.db"
    c1 = FileLLMCache(db)
    c1.set(_msgs("q1"), "M", "a1")
    c1.set(_msgs("q2"), "M", "a2")
    c1.close()

    c2 = FileLLMCache(db)
    assert c2.get(_msgs("q1"), "M") == "a1"
    assert c2.get(_msgs("q2"), "M") == "a2"
    assert c2.size == 2

def test_file_cache_file_created(tmp_path):
    db = tmp_path / "subdir" / "cache.db"
    c = FileLLMCache(db)
    c.set(_msgs(), "M", "x")
    assert db.exists()

def test_file_cache_loads_after_multiple_writes(tmp_path):
    db = tmp_path / "cache.db"
    # Write across 3 separate instances
    for i in range(3):
        c = FileLLMCache(db)
        c.set(_msgs(f"msg{i}"), "M", f"ans{i}")
        c.close()

    c_final = FileLLMCache(db)
    for i in range(3):
        assert c_final.get(_msgs(f"msg{i}"), "M") == f"ans{i}"


# ---------------------------------------------------------------------------
# clear()
# ---------------------------------------------------------------------------

def test_file_cache_clear_removes_in_memory(tmp_path):
    cache = FileLLMCache(tmp_path / "cache.db")
    cache.set(_msgs(), "M", "hi")
    cache.clear()
    assert cache.get(_msgs(), "M") is None
    assert cache.size == 0

def test_file_cache_clear_removes_from_disk(tmp_path):
    db = tmp_path / "cache.db"
    c1 = FileLLMCache(db)
    c1.set(_msgs(), "M", "hi")
    c1.clear()
    c1.close()

    c2 = FileLLMCache(db)
    assert c2.get(_msgs(), "M") is None
    assert c2.size == 0


# ---------------------------------------------------------------------------
# max_size eviction
# ---------------------------------------------------------------------------

def test_file_cache_max_size_in_memory(tmp_path):
    cache = FileLLMCache(tmp_path / "cache.db", max_size=3)
    for i in range(4):
        cache.set(_msgs(f"q{i}"), "M", f"a{i}")
    assert cache.size == 3  # oldest evicted from in-memory store

def test_file_cache_load_respects_max_size(tmp_path):
    db = tmp_path / "cache.db"
    # Write 10 entries to disk
    for i in range(10):
        c = FileLLMCache(db, max_size=100)
        c.set(_msgs(f"q{i}"), "M", f"a{i}")
        c.close()

    # Reload with max_size=5 — only 5 most recent loaded into memory
    c2 = FileLLMCache(db, max_size=5)
    assert c2.size == 5


# ---------------------------------------------------------------------------
# stats()
# ---------------------------------------------------------------------------

def test_file_cache_stats(tmp_path):
    cache = FileLLMCache(tmp_path / "cache.db")
    cache.set(_msgs("x"), "M", "r")
    cache.get(_msgs("x"), "M")   # hit
    cache.get(_msgs("y"), "M")   # miss
    s = cache.stats()
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert s["hit_rate"] == 0.5
    assert s["size"] == 1

def test_file_cache_reset_stats(tmp_path):
    cache = FileLLMCache(tmp_path / "cache.db")
    cache.set(_msgs(), "M", "r")
    cache.get(_msgs(), "M")
    cache.reset_stats()
    assert cache.hits == 0
    assert cache.misses == 0


# ---------------------------------------------------------------------------
# with_cache(path) integration
# ---------------------------------------------------------------------------

def test_with_cache_path_string_creates_file_cache(tmp_path):
    db = str(tmp_path / "cache.db")
    llm = SimulatedLLM()
    llm.with_cache(db)
    assert isinstance(llm.cache, FileLLMCache)

def test_with_cache_path_object_creates_file_cache(tmp_path):
    llm = SimulatedLLM()
    llm.with_cache(tmp_path / "cache.db")
    assert isinstance(llm.cache, FileLLMCache)

def test_with_cache_none_creates_in_memory_cache():
    llm = SimulatedLLM()
    llm.with_cache()
    assert type(llm.cache) is LLMCache

def test_with_cache_instance_uses_provided_cache(tmp_path):
    fc = FileLLMCache(tmp_path / "cache.db")
    llm = SimulatedLLM()
    llm.with_cache(fc)
    assert llm.cache is fc

def test_file_cache_works_via_system(tmp_path):
    llm = SimulatedLLM().with_cache(str(tmp_path / "c.db"))
    # First call — miss, fills cache
    r1 = llm.system("sys", "Build a login page")
    # Second call — hit, returns same response without calling complete again
    r2 = llm.system("sys", "Build a login page")
    assert r1 == r2
    assert llm.cache.stats()["hits"] == 1

def test_file_cache_persists_via_system(tmp_path):
    db = tmp_path / "c.db"
    llm1 = SimulatedLLM().with_cache(str(db))
    r1 = llm1.system("sys", "Build a login page")
    llm1.cache.close()

    llm2 = SimulatedLLM().with_cache(str(db))
    r2 = llm2.system("sys", "Build a login page")
    assert r1 == r2
    assert llm2.cache.stats()["hits"] == 1


# ---------------------------------------------------------------------------
# Top-level imports
# ---------------------------------------------------------------------------

def test_importable_from_antcrew():
    from antcrew import FileLLMCache as FC
    assert FC is FileLLMCache

def test_importable_from_models():
    from antcrew.models import FileLLMCache as FC
    assert FC is FileLLMCache
