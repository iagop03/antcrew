"""Tests for run_async() and LLMCache."""
from __future__ import annotations

import asyncio
import pytest

from antcrew.models.base import BaseLLM, Message
from antcrew.models.cache import LLMCache
from antcrew.models.fallback import FallbackLLM
from antcrew.models.simulated import SimulatedLLM


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msgs(system="You are helpful.", user="Hello"):
    return [
        Message(role="system", content=system),
        Message(role="user",   content=user),
    ]


class CountCallsLLM(BaseLLM):
    """Delegates to SimulatedLLM but counts how many times complete() is called."""
    def __init__(self):
        self._inner = SimulatedLLM()
        self.call_count = 0

    def complete(self, messages, *, max_tokens=4096):
        self.call_count += 1
        return self._inner.complete(messages, max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# LLMCache — unit tests
# ---------------------------------------------------------------------------

def test_cache_miss_first_call():
    cache = LLMCache()
    result = cache.get(_msgs(), "TestModel")
    assert result is None
    assert cache.misses == 1
    assert cache.hits == 0

def test_cache_hit_after_set():
    cache = LLMCache()
    msgs = _msgs()
    cache.set(msgs, "TestModel", "hello world")
    result = cache.get(msgs, "TestModel")
    assert result == "hello world"
    assert cache.hits == 1

def test_cache_separate_by_model():
    cache = LLMCache()
    msgs = _msgs()
    cache.set(msgs, "ModelA", "response A")
    assert cache.get(msgs, "ModelB") is None  # different model → miss

def test_cache_separate_by_content():
    cache = LLMCache()
    cache.set(_msgs(user="foo"), "M", "resp-foo")
    assert cache.get(_msgs(user="bar"), "M") is None

def test_cache_stats():
    cache = LLMCache()
    cache.get(_msgs(), "M")       # miss
    cache.set(_msgs(), "M", "x")
    cache.get(_msgs(), "M")       # hit
    s = cache.stats()
    assert s["hits"]   == 1
    assert s["misses"] == 1
    assert s["hit_rate"] == 0.5
    assert s["size"] == 1

def test_cache_clear():
    cache = LLMCache()
    cache.set(_msgs(), "M", "x")
    assert cache.size == 1
    cache.clear()
    assert cache.size == 0
    # Counters preserved
    assert cache.misses == 0 and cache.hits == 0

def test_cache_reset_stats():
    cache = LLMCache()
    cache.get(_msgs(), "M")      # miss
    cache.reset_stats()
    assert cache.hits == 0 and cache.misses == 0

def test_cache_max_size_evicts_oldest():
    cache = LLMCache(max_size=2)
    cache.set(_msgs(user="a"), "M", "A")
    cache.set(_msgs(user="b"), "M", "B")
    cache.set(_msgs(user="c"), "M", "C")  # evicts "a"
    assert cache.size == 2
    assert cache.get(_msgs(user="a"), "M") is None  # evicted
    assert cache.get(_msgs(user="b"), "M") == "B"
    assert cache.get(_msgs(user="c"), "M") == "C"

def test_cache_size_property():
    cache = LLMCache()
    assert cache.size == 0
    cache.set(_msgs(), "M", "x")
    assert cache.size == 1


# ---------------------------------------------------------------------------
# LLMCache — integration with BaseLLM.system()
# ---------------------------------------------------------------------------

def test_llm_cache_miss_calls_backend():
    llm = CountCallsLLM()
    llm.cache = LLMCache()
    llm.system("You are a PM. Output JSON tickets array.", "Build x")
    assert llm.call_count == 1

def test_llm_cache_hit_skips_backend():
    llm = CountCallsLLM()
    llm.cache = LLMCache()
    llm.system("You are a PM. Output JSON tickets array.", "Build x")
    llm.system("You are a PM. Output JSON tickets array.", "Build x")
    assert llm.call_count == 1  # second call served from cache

def test_llm_cache_different_prompts_both_call_backend():
    llm = CountCallsLLM()
    llm.cache = LLMCache()
    llm.system("You are a PM. Output JSON tickets array.", "Build x")
    llm.system("You are a PM. Output JSON tickets array.", "Build y")
    assert llm.call_count == 2  # different content → two misses

def test_llm_cache_returns_same_response():
    llm = SimulatedLLM()
    llm.cache = LLMCache()
    r1 = llm.system("You are a PM. Output JSON tickets array.", "Build login")
    r2 = llm.system("You are a PM. Output JSON tickets array.", "Build login")
    assert r1 == r2

def test_llm_streaming_cached_on_second_call():
    """First streaming call hits backend and caches; second call is a cache hit."""
    tokens: list[str] = []
    llm = CountCallsLLM()
    llm.cache = LLMCache()
    llm.on_token = tokens.append
    llm.system("You are a PM. Output JSON tickets array.", "Build x")
    llm.system("You are a PM. Output JSON tickets array.", "Build x")
    assert llm.call_count == 1  # second call served from cache

def test_llm_no_cache_when_none():
    llm = CountCallsLLM()
    # cache is None by default
    llm.system("You are a PM. Output JSON tickets array.", "Build x")
    llm.system("You are a PM. Output JSON tickets array.", "Build x")
    assert llm.call_count == 2  # no cache → every call hits backend


# ---------------------------------------------------------------------------
# with_cache() fluent API
# ---------------------------------------------------------------------------

def test_with_cache_returns_self():
    llm = SimulatedLLM()
    result = llm.with_cache()
    assert result is llm  # returns self

def test_with_cache_creates_new_cache():
    llm = SimulatedLLM()
    llm.with_cache()
    assert isinstance(llm.cache, LLMCache)

def test_with_cache_accepts_existing_cache():
    shared = LLMCache(max_size=256)
    llm = SimulatedLLM()
    llm.with_cache(shared)
    assert llm.cache is shared

def test_with_cache_shared_between_instances():
    shared = LLMCache()
    llm1 = CountCallsLLM()
    llm2 = CountCallsLLM()
    llm1.cache = shared
    llm2.cache = shared
    llm1.system("You are a PM. Output JSON tickets array.", "Build x")
    llm2.system("You are a PM. Output JSON tickets array.", "Build x")
    # llm2 should get a hit (same model name "CountCallsLLM")
    assert llm2.call_count == 0
    assert shared.hits == 1


# ---------------------------------------------------------------------------
# FallbackLLM cache propagation
# ---------------------------------------------------------------------------

def test_fallback_cache_propagates():
    m1, m2 = SimulatedLLM(), SimulatedLLM()
    llm = FallbackLLM([m1, m2])
    cache = LLMCache()
    llm.cache = cache
    assert m1.cache is cache
    assert m2.cache is cache

def test_fallback_with_cache_fluent():
    m1, m2 = SimulatedLLM(), SimulatedLLM()
    llm = FallbackLLM([m1, m2])
    llm.with_cache()
    assert isinstance(m1.cache, LLMCache)
    assert isinstance(m2.cache, LLMCache)


# ---------------------------------------------------------------------------
# Top-level import
# ---------------------------------------------------------------------------

def test_llmcache_importable_from_antcrew():
    from antcrew import LLMCache as LC
    assert LC is LLMCache

def test_llmcache_importable_from_models():
    from antcrew.models import LLMCache as LC
    assert LC is LLMCache


# ---------------------------------------------------------------------------
# run_async()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_async_returns_dict():
    from antcrew.teams.dev_team import DevTeam
    team = DevTeam(model=SimulatedLLM())
    state = await team.run_async("Build a login module")
    assert isinstance(state, dict)

@pytest.mark.asyncio
async def test_run_async_same_result_as_run():
    from antcrew.teams.dev_team import DevTeam
    team_sync  = DevTeam(model=SimulatedLLM())
    team_async = DevTeam(model=SimulatedLLM())
    sync_state  = team_sync.run("Build login")
    async_state = await team_async.run_async("Build login")
    # Both return a prd and tickets
    assert (sync_state.get("prd") is not None) == (async_state.get("prd") is not None)
    assert (sync_state.get("tickets") is not None) == (async_state.get("tickets") is not None)

@pytest.mark.asyncio
async def test_run_async_accepts_thread_id():
    from antcrew.teams.dev_team import DevTeam
    team = DevTeam(model=SimulatedLLM())
    state = await team.run_async("Build x", thread_id="custom-thread-42")
    assert state  # did not raise

@pytest.mark.asyncio
async def test_run_async_research_team():
    from antcrew.teams.research_team import ResearchTeam
    team = ResearchTeam(model=SimulatedLLM())
    state = await team.run_async("Risks of agentic AI")
    assert state

@pytest.mark.asyncio
async def test_run_async_content_team():
    from antcrew.teams.content_team import ContentTeam
    team = ContentTeam(model=SimulatedLLM())
    state = await team.run_async("Write a blog about AI")
    assert state

@pytest.mark.asyncio
async def test_run_async_concurrent():
    """Two concurrent run_async() calls must not interfere."""
    from antcrew.teams.dev_team import DevTeam
    team1 = DevTeam(model=SimulatedLLM())
    team2 = DevTeam(model=SimulatedLLM())
    state1, state2 = await asyncio.gather(
        team1.run_async("Build auth module"),
        team2.run_async("Build payment module"),
    )
    assert state1.get("request") == "Build auth module"
    assert state2.get("request") == "Build payment module"

@pytest.mark.asyncio
async def test_run_async_fullstack_team():
    from antcrew.teams.fullstack_team import FullStackTeam
    team = FullStackTeam(model=SimulatedLLM())
    state = await team.run_async("Build a todo app")
    assert state
