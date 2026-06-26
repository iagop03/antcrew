"""Async-compatible team wrappers for use in FastAPI, Jupyter, and other
async contexts.

Each async team is a thin subclass of its synchronous counterpart.  The only
difference is ``async def run()``, which delegates to the synchronous
pipeline via :func:`asyncio.to_thread` so LLM HTTP calls never block the
event loop.

Usage::

    from antcrew import AsyncDevTeam, AsyncResearchTeam
    from antcrew.models.anthropic_model import AnthropicModel

    llm = AnthropicModel()
    team = AsyncDevTeam(model=llm)

    # In an async context (FastAPI route, Jupyter cell, etc.)
    result = await team.run("Build a login module")

    # Also works synchronously — sync .run() is still available via super()
    result = team.run_sync("Build a login module")

All constructor arguments, integrations, checkpointers, and pipeline
configuration are identical to the synchronous counterparts.
"""
from __future__ import annotations

import asyncio
import functools

from antcrew.core.run_result import RunResult
from antcrew.teams.dev_team import DevTeam
from antcrew.teams.fullstack_team import FullStackTeam
from antcrew.teams.research_team import ResearchTeam
from antcrew.teams.content_team import ContentTeam


class AsyncTeamMixin:
    """Adds ``async def run()`` to any synchronous team class.

    Uses :func:`asyncio.to_thread` so blocking LLM HTTP calls run in a
    worker thread and never stall the event loop.  The synchronous path is
    still accessible via :meth:`run_sync`.
    """

    async def run(self, request: str, *, thread_id: str = "default") -> RunResult:
        """Async wrapper around the synchronous pipeline.

        Delegates to the parent ``run()`` in a thread-pool thread via
        ``asyncio.to_thread``, making it safe to ``await`` inside FastAPI
        request handlers, Jupyter notebooks, or any other async context.
        """
        return await asyncio.to_thread(
            functools.partial(super().run, request, thread_id=thread_id)  # type: ignore[misc]
        )

    def run_sync(self, request: str, *, thread_id: str = "default") -> RunResult:
        """Convenience alias — calls the synchronous parent ``run()`` directly."""
        return super().run(request, thread_id=thread_id)  # type: ignore[misc]


class AsyncDevTeam(AsyncTeamMixin, DevTeam):
    """Async version of :class:`~antcrew.teams.dev_team.DevTeam`.

    Identical constructor and configuration; adds ``async def run()``.
    """


class AsyncFullStackTeam(AsyncTeamMixin, FullStackTeam):
    """Async version of :class:`~antcrew.teams.fullstack_team.FullStackTeam`.

    Identical constructor and configuration; adds ``async def run()``.
    """


class AsyncResearchTeam(AsyncTeamMixin, ResearchTeam):
    """Async version of :class:`~antcrew.teams.research_team.ResearchTeam`.

    Identical constructor and configuration; adds ``async def run()``.
    """


class AsyncContentTeam(AsyncTeamMixin, ContentTeam):
    """Async version of :class:`~antcrew.teams.content_team.ContentTeam`.

    Identical constructor and configuration; adds ``async def run()``.
    """
