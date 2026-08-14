"""
QuickStart — one-liner teams for the most common use cases.

The goal is that a developer can see results in three lines before they need
to understand Supervisor, flow edges, or TeamState.

Usage::

    from antcrew import QuickStart

    # Option A — string model name (resolved via build_llm)
    team = QuickStart.dev()
    result = team.run("Build a FastAPI auth service")

    # Option B — pass your own LLM instance
    from antcrew.models import AnthropicModel
    team = QuickStart.dev(AnthropicModel("claude-opus-5"))

    # Option C — different team types
    research = QuickStart.research()
    blog     = QuickStart.content()
    full     = QuickStart.fullstack()
"""
from __future__ import annotations

from typing import Union


def _resolve(model_or_llm) -> object:
    """Accept either a string model key or a BaseLLM instance."""
    if isinstance(model_or_llm, str):
        from antcrew.config import build_llm
        return build_llm(model_or_llm)
    return model_or_llm  # already a BaseLLM


class QuickStart:
    """Pre-configured teams for the most common use cases.

    Each factory method accepts an optional ``model`` argument that can be:
    - A model key string: ``"claude"`` (default), ``"gpt-4o"``, ``"simulated"``,
      ``"ollama:llama3"``, ``"groq:llama-3.1-8b-instant"``
    - A :class:`~antcrew.models.base.BaseLLM` instance for full control.

    All methods return a team whose ``.run(request)`` method can be called
    immediately — no additional configuration required.
    """

    @staticmethod
    def dev(model: Union[str, object] = "claude") -> object:
        """BA → PM → Backend Dev → QA → Reviewer pipeline.

        The standard software-development team.  Good for:
        feature requests, bug descriptions, refactoring tasks.
        """
        from antcrew.teams.dev_team import DevTeam
        return DevTeam(llm=_resolve(model))

    @staticmethod
    def fullstack(model: Union[str, object] = "claude") -> object:
        """BA → PM → Backend Dev + Frontend Dev (parallel) → QA → Reviewer.

        Adds a frontend developer running concurrently with backend.
        Good for: full-stack features that touch UI and API together.
        """
        from antcrew.teams.fullstack_team import FullStackTeam
        return FullStackTeam(llm=_resolve(model))

    @staticmethod
    def research(model: Union[str, object] = "claude") -> object:
        """Researcher → Analyst → Writer pipeline.

        Good for: technical research docs, literature reviews, competitive
        analyses, architecture evaluations.
        """
        from antcrew.teams.research_team import ResearchTeam
        return ResearchTeam(llm=_resolve(model))

    @staticmethod
    def content(model: Union[str, object] = "claude") -> object:
        """Researcher → Copywriter → Editor pipeline.

        Good for: blog posts, product copy, email campaigns, social content.
        """
        from antcrew.teams.content_team import ContentTeam
        return ContentTeam(llm=_resolve(model))

    @staticmethod
    def custom(agents: list, model: Union[str, object] = "claude") -> object:
        """Build a team from a list of agent instances.

        Args:
            agents: Ordered list of :class:`~antcrew.core.agent.BaseAgent` instances.
                    They will be wired sequentially: agents[0] → agents[1] → … → agents[-1].
            model:  Model for agents that don't have their own LLM set.

        Example::

            from antcrew import QuickStart
            from antcrew.agents import PMAgent, QAAgent

            llm = QuickStart._llm()
            team = QuickStart.custom([PMAgent(llm), QAAgent(llm)])
            team.run("Define requirements for user authentication")
        """
        from antcrew.teams.custom_team import CustomTeam
        llm = _resolve(model)
        return CustomTeam(llm=llm, agents=agents)

    @staticmethod
    def _llm(model: Union[str, object] = "claude") -> object:
        """Return a :class:`~antcrew.models.base.BaseLLM` instance for the given model key.

        Convenience helper when constructing agents manually for :meth:`custom`.
        """
        return _resolve(model)
