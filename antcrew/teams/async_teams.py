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


# ---------------------------------------------------------------------------
# Async wrappers for newer team types
# ---------------------------------------------------------------------------

class AsyncCustomTeam(AsyncTeamMixin):
    """Async version of :class:`~antcrew.teams.custom_team.CustomTeam`.

    Because CustomTeam does not inherit DevTeam, this class cannot use the
    standard mixin pattern (which relies on ``super().run()``).  Instead it
    wraps CustomTeam directly.

    Args:
        *args, **kwargs: Forwarded to :class:`~antcrew.teams.custom_team.CustomTeam`.

    Usage::

        from antcrew import AsyncCustomTeam
        from antcrew.models.anthropic_model import AnthropicModel

        team = AsyncCustomTeam(
            steps=[...],
            llm=AnthropicModel(),
        )
        result = await team.run("build it")
    """

    def __init__(self, *args, **kwargs):
        from antcrew.teams.custom_team import CustomTeam
        self._inner = CustomTeam(*args, **kwargs)

    async def run(self, request: str, *, thread_id: str = "default"):  # type: ignore[override]
        import asyncio
        import functools
        return await asyncio.to_thread(
            functools.partial(self._inner.run, request, thread_id=thread_id)
        )

    def run_sync(self, request: str, *, thread_id: str = "default"):
        return self._inner.run(request, thread_id=thread_id)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class AsyncFeatureTeam(AsyncTeamMixin):
    """Async version of :class:`~antcrew.agents.feature_agent.FeatureTeam`.

    Args:
        *args, **kwargs: Forwarded to :class:`~antcrew.agents.feature_agent.FeatureTeam`.

    Usage::

        team = AsyncFeatureTeam(llm=AnthropicModel(), project_dir="/app")
        result = await team.run("add auth endpoint")
    """

    def __init__(self, *args, **kwargs):
        from antcrew.agents.feature_agent import FeatureTeam
        self._inner = FeatureTeam(*args, **kwargs)

    async def run(self, request: str):  # type: ignore[override]
        import asyncio
        import functools
        return await asyncio.to_thread(functools.partial(self._inner.run, request))

    def run_sync(self, request: str):
        return self._inner.run(request)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class AsyncRouter(AsyncTeamMixin):
    """Async version of :class:`~antcrew.core.router.Router`.

    The classifier and each route handler may block on HTTP; this wrapper
    offloads the entire dispatch to a thread so the event loop stays free.

    Args:
        *args, **kwargs: Forwarded to :class:`~antcrew.core.router.Router`.
    """

    def __init__(self, *args, **kwargs):
        from antcrew.core.router import Router
        self._inner = Router(*args, **kwargs)

    async def run(self, request: str):  # type: ignore[override]
        import asyncio
        import functools
        return await asyncio.to_thread(functools.partial(self._inner.run, request))

    def run_sync(self, request: str):
        return self._inner.run(request)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)
