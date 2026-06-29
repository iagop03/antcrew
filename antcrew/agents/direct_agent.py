"""DirectAgent — single LLM call, no pipeline, no tools.

The lightest possible "team": one system prompt, one user message, one response.
Used as the fast path in a :class:`~antcrew.core.router.Router` for requests
that don't need multi-agent processing.

Usage::

    from antcrew import DirectAgent, AnthropicModel

    agent = DirectAgent(
        AnthropicModel(),
        system_prompt="You are a helpful assistant. Answer concisely.",
        output_key="response",
    )
    result = agent.run("What is JWT?")
    print(result.state["response"])
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from antcrew.core.run_result import RunResult

if TYPE_CHECKING:
    from antcrew.models.base import BaseLLM

_DEFAULT_SYSTEM = "You are a helpful assistant. Answer the user's question clearly and concisely."


class DirectAgent:
    """Single-call agent: one prompt → one response → done.

    Compatible with the ``run(request) -> RunResult`` team interface so it
    plugs into :class:`~antcrew.core.router.Router` and anywhere else a team
    is expected.

    Args:
        llm:           LLM to use for the response.
        system_prompt: System message.  Defaults to a generic helpful assistant.
        output_key:    State key where the response is stored (default: ``"response"``).
        max_tokens:    Optional token cap forwarded to the LLM.
    """

    def __init__(
        self,
        llm: "BaseLLM",
        *,
        system_prompt: Optional[str] = None,
        output_key: str = "response",
        max_tokens: Optional[int] = None,
    ) -> None:
        self.llm = llm
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM
        self._output_key = output_key
        self._max_tokens = max_tokens

    def run(self, request: str) -> RunResult:
        kwargs: dict = {}
        if self._max_tokens:
            kwargs["max_tokens"] = self._max_tokens
        response = self.llm.system(self._system_prompt, request, **kwargs)
        return RunResult(state={"request": request, self._output_key: response})
