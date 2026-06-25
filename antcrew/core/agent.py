from __future__ import annotations

import json as _json
import logging
import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

from pydantic import ValidationError

log = logging.getLogger(__name__)

from antcrew.core.state import TeamState
from antcrew.core.validation import _validate_schema
from antcrew.models.base import BaseLLM

if TYPE_CHECKING:
    from antcrew.core.channel import BaseChannel
    from antcrew.memory.store import BaseMemory
    from antcrew.memory.repo_index import RepoIndex


_FENCE_RE = re.compile(r'^```[a-zA-Z]*[ \t]*\n?([\s\S]*)```[ \t]*$')
_INLINE_FENCE_RE = re.compile(r'```[a-zA-Z]*[ \t]*\n?([\s\S]*?)```', re.MULTILINE)


def _json_loads(text: str):
    """json.loads with fallback to strict=False for LLM output containing raw newlines."""
    try:
        return _json.loads(text)
    except _json.JSONDecodeError as exc:
        if "Invalid control character" in str(exc) or "control character" in str(exc).lower():
            return _json.loads(text, strict=False)
        raise


def _strip_fences(text: str) -> str:
    """Remove outermost markdown code fences from LLM JSON output.

    Handles three cases:
    - No fence → return as-is
    - Full fence (```lang ... ```) → strip both fences, return content
    - Opening fence only (truncated response) → strip first line, return rest
      so that json.loads at least sees the JSON content (even if truncated)
      rather than failing immediately on the backtick character.
    """
    text = text.strip()
    if not text.startswith("```"):
        return text
    m = _FENCE_RE.match(text)
    if m:
        return m.group(1).strip()
    _, _, rest = text.partition('\n')
    return rest.strip()


def _extract_json(text: str) -> str:
    """Extract JSON from a response that may contain surrounding prose.

    Tries in order:
    1. Plain strip_fences (response is already JSON or starts with a fence).
    2. Any ```[lang] ... ``` block found anywhere in the text.
    3. First '{' or '[' to end of string (prose prefix, raw JSON suffix).

    Returns the best candidate; the caller is responsible for parsing.
    """
    # Fast path: already parseable after stripping fences.
    candidate = _strip_fences(text)
    try:
        if candidate:
            _json_loads(candidate)
            return candidate
    except Exception:
        pass

    # Look for a code fence anywhere in the text.
    for m in _INLINE_FENCE_RE.finditer(text):
        inner = m.group(1).strip()
        if not inner:
            continue
        try:
            _json_loads(inner)
            return inner
        except Exception:
            continue

    # Last resort: slice from the first JSON-like character.
    for start in ('{', '['):
        idx = text.find(start)
        if idx != -1:
            tail = text[idx:].strip()
            try:
                _json_loads(tail)
                return tail
            except Exception:
                pass

    return candidate  # return whatever we have; caller handles the error


class BaseAgent(ABC):
    """
    Minimum contract that all AntCrew agents must implement.

    Each agent is a stateless callable: receives TeamState, does its work
    (usually one or more LLM calls), and returns a partial state dict with
    only the keys it produced. LangGraph merges those updates back.

    Conversational mode:
        Set ``conversational = True`` and implement ``refine()`` to enable
        multi-turn dialogue with the human reviewer. The reviewer types
        feedback in natural language; the agent revises its artifact and
        re-presents it before the pipeline continues.

    Per-agent configuration (all optional):
        agent = BackendDevAgent(
            llm=OllamaModel("llama3"),
            channel=ConsoleChannel(),
            approval_required=True,
            response_options=["approve", "request_changes", "reject"],
        )
    """

    name: str = "base"
    role_description: str = ""
    conversational: bool = False  # True in agents that implement refine()
    memory: Optional["BaseMemory"] = None      # set by team after construction
    repo_index: Optional["RepoIndex"] = None   # set by team after construction

    def __init__(
        self,
        llm: BaseLLM,
        *,
        channel: Optional["BaseChannel"] = None,
        approval_required: bool = False,
        response_options: Optional[list[str]] = None,
        max_tokens: Optional[int] = None,
        system_prompt_suffix: Optional[str] = None,
    ) -> None:
        self.llm = llm
        self.channel = channel
        self.approval_required = approval_required
        self.response_options = response_options or ["approve", "edit", "reject"]
        self.max_tokens = max_tokens                        # per-agent token cap
        self.system_prompt_suffix = system_prompt_suffix   # appended to every system call

    @abstractmethod
    def run(self, state: TeamState) -> dict:
        """
        Execute the agent's task.
        Returns a partial TeamState dict (only updated keys).
        """

    def system(self, system_prompt: str, user: str, **kwargs) -> str:
        """Call LLM with system + user messages, tagging the LLM with this agent's name.

        Applies per-agent overrides:
        - system_prompt_suffix: appended to the system prompt on every call.
        - max_tokens: overrides the LLM default when set.
        """
        if self.system_prompt_suffix:
            system_prompt = system_prompt + "\n\n" + self.system_prompt_suffix
        if self.max_tokens and "max_tokens" not in kwargs:
            kwargs["max_tokens"] = self.max_tokens
        self.llm.current_agent = self.name
        return self.llm.system(system_prompt, user, **kwargs)

    def system_parsed(
        self,
        system_prompt: str,
        user: str,
        schema: Any,
        *,
        max_retries: int = 0,
        **kwargs,
    ) -> Any:
        """Call LLM, parse JSON, validate against schema.

        On parse or validation failure: logs a warning with raw output and re-raises.
        max_retries is reserved for Commit 2b (retry-with-hint) — currently ignored.
        **kwargs are forwarded to self.system() (e.g. max_tokens).
        """
        raw = self.system(system_prompt, user, **kwargs)
        try:
            return _validate_schema(schema, _json_loads(_extract_json(raw)))
        except (ValidationError, ValueError) as exc:
            schema_name = getattr(schema, "__name__", str(schema))
            log.warning(
                "parse_failure agent=%s schema=%s error=%s raw=%.300s",
                self.name,
                schema_name,
                exc,
                raw,
            )
            self._tracelog(
                "parse_failure",
                schema=schema_name,
                error=str(exc),
                raw_preview=raw[:200],
            )
            raise

    def _search_repo(self, query: str, *, n: int = 5) -> str:
        """Search the repository index for relevant existing code.

        Returns a formatted string block ready to append to a system prompt,
        or an empty string if no repo index is configured.
        """
        if not self.repo_index:
            return ""
        return self.repo_index.search(query, n=n)

    def _recall(self, query: str, *, n: int = 3) -> str:
        """Search semantic memory for context from past runs.

        Returns a formatted string block ready to append to a system prompt,
        or an empty string if memory is not configured or no results found.
        """
        if not self.memory:
            return ""
        results = self.memory.search(query, n=n)
        if not results:
            return ""
        lines = [
            f"- [{r.metadata.get('artifact_type', '?')}] {r.text[:200]}"
            for r in results
        ]
        return "\n\nRelevant context from previous runs:\n" + "\n".join(lines) + "\n"

    def _tracelog(self, event: str, **kwargs) -> None:
        """No-op stub. Replaced by TraceLog PR (roadmap #5)."""
        log.debug("tracelog:%s %s", event, kwargs)

    def refine(self, state: TeamState, artifact, feedback: str) -> dict:
        """
        Refine the last artifact based on human feedback (conversational mode).

        Override this and set ``conversational = True`` to enable multi-turn
        dialogue. Must return a **partial** state dict with only the updated
        artifact key — do NOT update ``current_agent`` or ``messages`` here.

        Default: no-op (empty dict → artifact unchanged).
        """
        return {}
