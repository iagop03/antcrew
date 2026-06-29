"""Tests for async team wrappers (AsyncDevTeam, AsyncResearchTeam, etc.)."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from antcrew.models.simulated import SimulatedLLM
from antcrew.teams.async_teams import (
    AsyncContentTeam,
    AsyncDevTeam,
    AsyncFullStackTeam,
    AsyncResearchTeam,
    AsyncTeamMixin,
)
from antcrew.teams.dev_team import DevTeam
from antcrew.teams.research_team import ResearchTeam
from antcrew.teams.content_team import ContentTeam
from antcrew.teams.fullstack_team import FullStackTeam
from antcrew.core.run_result import RunResult


# ===========================================================================
# Inheritance checks
# ===========================================================================

def test_async_dev_team_is_dev_team():
    assert issubclass(AsyncDevTeam, DevTeam)


def test_async_research_team_is_research_team():
    assert issubclass(AsyncResearchTeam, ResearchTeam)


def test_async_content_team_is_content_team():
    assert issubclass(AsyncContentTeam, ContentTeam)


def test_async_fullstack_team_is_fullstack_team():
    assert issubclass(AsyncFullStackTeam, FullStackTeam)


def test_all_async_teams_have_mixin():
    for cls in (AsyncDevTeam, AsyncFullStackTeam, AsyncResearchTeam, AsyncContentTeam):
        assert issubclass(cls, AsyncTeamMixin)


# ===========================================================================
# run() is a coroutine
# ===========================================================================

def test_async_dev_team_run_returns_coroutine():
    llm = SimulatedLLM()
    team = AsyncDevTeam(model=llm)
    coro = team.run("test")
    assert asyncio.iscoroutine(coro)
    coro.close()  # clean up without executing


def test_async_research_team_run_returns_coroutine():
    llm = SimulatedLLM()
    team = AsyncResearchTeam(model=llm)
    coro = team.run("research AI")
    assert asyncio.iscoroutine(coro)
    coro.close()


def test_async_content_team_run_returns_coroutine():
    llm = SimulatedLLM()
    team = AsyncContentTeam(model=llm)
    coro = team.run("write a blog post")
    assert asyncio.iscoroutine(coro)
    coro.close()


# ===========================================================================
# run_sync() still works
# ===========================================================================

def test_async_dev_team_run_sync():
    llm = SimulatedLLM()
    team = AsyncDevTeam(model=llm)
    result = team.run_sync("Build auth")
    assert isinstance(result, RunResult)


def test_async_research_team_run_sync():
    llm = SimulatedLLM()
    team = AsyncResearchTeam(model=llm)
    result = team.run_sync("Research LLMs")
    assert isinstance(result, RunResult)


def test_async_content_team_run_sync():
    llm = SimulatedLLM()
    team = AsyncContentTeam(model=llm)
    result = team.run_sync("Write a post about AI")
    assert isinstance(result, RunResult)


# ===========================================================================
# async run() actually executes the pipeline
# ===========================================================================

@pytest.mark.asyncio
async def test_async_dev_team_run_returns_result():
    llm = SimulatedLLM()
    team = AsyncDevTeam(model=llm)
    result = await team.run("Build a login module")
    assert isinstance(result, RunResult)


@pytest.mark.asyncio
async def test_async_research_team_run_returns_result():
    llm = SimulatedLLM()
    team = AsyncResearchTeam(model=llm)
    result = await team.run("Research AI safety")
    assert isinstance(result, RunResult)


@pytest.mark.asyncio
async def test_async_content_team_run_returns_result():
    llm = SimulatedLLM()
    team = AsyncContentTeam(model=llm)
    result = await team.run("Blog post about Python")
    assert isinstance(result, RunResult)


@pytest.mark.asyncio
async def test_async_dev_team_result_has_state():
    llm = SimulatedLLM()
    team = AsyncDevTeam(model=llm)
    result = await team.run("Build auth")
    assert hasattr(result, "state")
    assert isinstance(result.state, dict)


@pytest.mark.asyncio
async def test_async_dev_team_thread_id_forwarded():
    """thread_id is passed through to the underlying synchronous run()."""
    llm = SimulatedLLM()
    team = AsyncDevTeam(model=llm)
    result = await team.run("Build X", thread_id="my-thread")
    assert result.thread_id == "my-thread"


@pytest.mark.asyncio
async def test_async_teams_run_concurrently():
    """Two async runs on separate teams complete without interference."""
    llm_a = SimulatedLLM()
    llm_b = SimulatedLLM()
    team_a = AsyncDevTeam(model=llm_a)
    team_b = AsyncResearchTeam(model=llm_b)

    result_a, result_b = await asyncio.gather(
        team_a.run("Build API"),
        team_b.run("Research ML"),
    )
    assert isinstance(result_a, RunResult)
    assert isinstance(result_b, RunResult)


# ===========================================================================
# asyncio.to_thread is used (not blocking the loop directly)
# ===========================================================================

@pytest.mark.asyncio
async def test_run_uses_to_thread():
    """run() delegates to asyncio.to_thread, not a direct call."""
    llm = SimulatedLLM()
    team = AsyncDevTeam(model=llm)

    call_thread_ids: list[int] = []

    original_run = DevTeam.run

    def spy_run(self, request, *, thread_id="default"):
        import threading
        call_thread_ids.append(threading.get_ident())
        return original_run(self, request, thread_id=thread_id)

    main_thread_id = __import__("threading").get_ident()

    with patch.object(DevTeam, "run", spy_run):
        await team.run("test")

    assert len(call_thread_ids) == 1
    # The pipeline ran in a worker thread, not the main thread.
    assert call_thread_ids[0] != main_thread_id


# ===========================================================================
# Public API
# ===========================================================================

def test_exported_from_antcrew():
    import antcrew
    assert hasattr(antcrew, "AsyncDevTeam")
    assert hasattr(antcrew, "AsyncFullStackTeam")
    assert hasattr(antcrew, "AsyncResearchTeam")
    assert hasattr(antcrew, "AsyncContentTeam")


# ===========================================================================
# AsyncCustomTeam
# ===========================================================================

from antcrew.teams.async_teams import AsyncCustomTeam, AsyncFeatureTeam, AsyncRouter


def test_async_custom_team_init():
    team = AsyncCustomTeam(
        steps=[{"name": "gen", "system_prompt": "Do it.", "output_key": "out"}],
        llm=SimulatedLLM(),
    )
    assert team is not None


def test_async_custom_team_run_sync():
    team = AsyncCustomTeam(
        steps=[{"name": "gen", "system_prompt": "Do it.", "output_key": "out"}],
        llm=SimulatedLLM(),
    )
    result = team.run_sync("task")
    assert isinstance(result, RunResult)
    assert "out" in result.state


@pytest.mark.asyncio
async def test_async_custom_team_run_async():
    team = AsyncCustomTeam(
        steps=[{"name": "gen", "system_prompt": "Do it.", "output_key": "out"}],
        llm=SimulatedLLM(),
    )
    result = await team.run("task")
    assert isinstance(result, RunResult)
    assert "out" in result.state


def test_async_custom_team_request_preserved():
    team = AsyncCustomTeam(
        steps=[{"name": "a", "system_prompt": "Say hi.", "output_key": "reply"}],
        llm=SimulatedLLM(),
    )
    result = team.run_sync("my request")
    assert result.state["request"] == "my request"


def test_async_custom_team_exported():
    import antcrew
    assert hasattr(antcrew, "AsyncCustomTeam")


# ===========================================================================
# AsyncFeatureTeam
# ===========================================================================

def test_async_feature_team_init():
    team = AsyncFeatureTeam(llm=SimulatedLLM())
    assert team is not None


def test_async_feature_team_run_sync():
    team = AsyncFeatureTeam(llm=SimulatedLLM())
    result = team.run_sync("Add a function")
    assert isinstance(result, RunResult)
    assert "feature_output" in result.state


@pytest.mark.asyncio
async def test_async_feature_team_run_async():
    team = AsyncFeatureTeam(llm=SimulatedLLM())
    result = await team.run("Add a function")
    assert isinstance(result, RunResult)


def test_async_feature_team_exported():
    import antcrew
    assert hasattr(antcrew, "AsyncFeatureTeam")


# ===========================================================================
# AsyncRouter
# ===========================================================================

from unittest.mock import MagicMock
from antcrew.core.router import RuleClassifier


def _make_async_router():
    handler = MagicMock()
    handler.run.return_value = RunResult(state={"request": "r", "out": "x"})
    return AsyncRouter(
        classifier=RuleClassifier([], default="h"),
        routes={"h": handler},
        default="h",
    ), handler


def test_async_router_init():
    team, _ = _make_async_router()
    assert team is not None


def test_async_router_run_sync():
    team, _ = _make_async_router()
    result = team.run_sync("task")
    assert isinstance(result, RunResult)
    assert result.state["_route"] == "h"


@pytest.mark.asyncio
async def test_async_router_run_async():
    team, _ = _make_async_router()
    result = await team.run("task")
    assert isinstance(result, RunResult)
    assert "_route" in result.state


def test_async_router_exported():
    import antcrew
    assert hasattr(antcrew, "AsyncRouter")


# ===========================================================================
# SequencedLLM
# ===========================================================================

from antcrew.testing import SequencedLLM


def test_sequenced_llm_returns_in_order():
    llm = SequencedLLM(["first", "second", "third"])
    assert llm.system("s", "u") == "first"
    assert llm.system("s", "u") == "second"
    assert llm.system("s", "u") == "third"


def test_sequenced_llm_call_count():
    llm = SequencedLLM(["a", "b"])
    llm.system("s", "u")
    llm.system("s", "u")
    assert llm.call_count == 2


def test_sequenced_llm_exhaustion():
    llm = SequencedLLM(["only"])
    llm.system("s", "u")
    with pytest.raises(StopIteration):
        llm.system("s", "u")


def test_sequenced_llm_exported():
    from antcrew import SequencedLLM as SL
    assert SL is SequencedLLM
