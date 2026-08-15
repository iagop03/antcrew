from __future__ import annotations

import hashlib as _hashlib
import json as _json
import logging
import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

from pydantic import ValidationError

from antcrew.core.state import TeamState
from antcrew.core.validation import _validate_schema
from antcrew.models.base import BaseLLM

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from antcrew.core.channel import BaseChannel
    from antcrew.core.symbol_index import SymbolIndex
    from antcrew.core.tools import BaseTool
    from antcrew.memory.repo_index import RepoIndex
    from antcrew.memory.store import BaseMemory


_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*<name>(.*?)</name>\s*<input>(.*?)</input>\s*</tool_call>",
    re.DOTALL,
)

_TOOL_SYSTEM_SUFFIX = """
You have access to the following tools:
{tool_schemas}

To call a tool, respond with this exact XML format (one call at a time):
<tool_call>
<name>tool_name</name>
<input>your input here</input>
</tool_call>

After gathering the information you need, give your final answer without any <tool_call> tags.
"""


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
    stage: str = ""                      # pipeline stage label, e.g. "planning", "implementation"
    conversational: bool = False  # True in agents that implement refine()
    review_type: str = "approval"       # "approval" or "structured_list" (for gate agents)
    item_schema: Optional[str] = None   # "requirements"|"tickets"|"screens"|"endpoints"
    hitl_channel: str = "default"       # routing hint for the platform's HITL dispatcher
    feedback_schema: Optional[Any] = None  # optional Pydantic model for structured feedback
    memory: Optional["BaseMemory"] = None           # set by team after construction
    repo_index: Optional["RepoIndex"] = None        # set by team after construction
    symbol_index: Optional["SymbolIndex"] = None    # set by team after construction
    kv_memory: Optional[Any] = None                 # BaseKVMemory — injected by platform runner

    def __init__(
        self,
        llm: BaseLLM,
        *,
        channel: Optional["BaseChannel"] = None,
        approval_required: bool = False,
        response_options: Optional[list[str]] = None,
        max_tokens: Optional[int] = None,
        system_prompt_suffix: Optional[str] = None,
        tools: Optional[list["BaseTool"]] = None,
        max_tool_steps: int = 5,
        preset=None,
        memory_n: int = 3,
        memory_score_threshold: float = 0.0,
        output_schema: Optional[Any] = None,
        max_cost_usd: Optional[float] = None,
    ) -> None:
        from antcrew.presets import resolve_preset
        self.llm = llm
        self.channel = channel
        self.approval_required = approval_required
        self.response_options = response_options or ["approve", "edit", "reject"]
        self.max_tokens = max_tokens
        self.system_prompt_suffix = system_prompt_suffix
        self.tools: list["BaseTool"] = tools or []
        self.max_tool_steps = max_tool_steps
        self.preset = resolve_preset(preset)
        self.memory_n = memory_n
        self.memory_score_threshold = memory_score_threshold
        # Structured output: when set, system_structured() validates against this schema
        if output_schema is not None:
            self.output_schema = output_schema
        # Per-agent cost cap: raises CostLimitExceeded when exceeded
        self.max_cost_usd: Optional[float] = max_cost_usd
        # Run context — set by _make_evented_run before each run so _tracelog can correlate
        self._run_id: Optional[str] = None
        self._thread_id: Optional[str] = None

    @abstractmethod
    def run(self, state: TeamState) -> dict:
        """
        Execute the agent's task.
        Returns a partial TeamState dict (only updated keys).
        """

    def system(self, system_prompt: str, user: str, **kwargs) -> str:
        """Call LLM with system + user messages, tagging the LLM with this agent's name.

        Applies per-agent overrides (in order):
        1. preset:              instruction prepended to the system prompt.
        2. memory:              relevant past context prepended to the user message.
        3. system_prompt_suffix: text appended to the system prompt.
        4. max_tokens:          overrides the LLM default when set.
        """
        if self.preset is not None:
            system_prompt = self.preset.apply(system_prompt)
        user = self._inject_memory(user)
        if self.system_prompt_suffix:
            system_prompt = system_prompt + "\n\n" + self.system_prompt_suffix
        if self.max_tokens and "max_tokens" not in kwargs:
            kwargs["max_tokens"] = self.max_tokens
        self.llm.current_agent = self.name
        self._tracelog("llm.call", prompt_chars=len(system_prompt) + len(user))
        result = self.llm.system(system_prompt, user, **kwargs)
        self._check_agent_cost()
        return result

    def _inject_memory(self, user: str) -> str:
        """Prepend relevant memory context to the user message, if memory is attached."""
        if self.memory is None or self.memory.count() == 0:
            return user
        try:
            results = self.memory.search(
                user,
                n=self.memory_n,
            )
        except Exception:
            return user
        relevant = [r for r in results if r.score >= self.memory_score_threshold]
        if not relevant:
            return user
        context_lines = ["[Relevant context from memory]"]
        for r in relevant:
            context_lines.append(r.text.strip())
        context_block = "\n".join(context_lines)
        return f"{context_block}\n\n{user}"

    def system_with_images(
        self,
        system_prompt: str,
        user: str,
        images: "list[str]",
        *,
        schema: "Optional[type]" = None,
        **kwargs,
    ) -> "str | dict":
        """Call LLM with a system prompt, user message, and one or more images.

        Requires a multi-modal-capable model (e.g. :class:`~antcrew.models.gemini_model.GeminiModel`
        or GPT-4V via :class:`~antcrew.models.openai_model.OpenAIModel`).  Models that do not
        implement ``system_multimodal()`` raise :class:`NotImplementedError`.

        Args:
            system_prompt: The agent's system prompt (same as in :meth:`system`).
            user:          User message text.
            images:        List of local file paths or public URLs to include in the
                           request.  Paths are read and base64-encoded; URLs are passed
                           through to the model as-is when the model supports URL references.
            schema:        Optional Pydantic ``BaseModel`` class.  When set the LLM response
                           is parsed and validated against it (same as :meth:`system_structured`).
            **kwargs:      Extra kwargs forwarded to the underlying LLM call.

        Returns:
            Raw string response, or a validated *schema* instance when *schema* is given.

        Example::

            class UIAnalysis(BaseModel):
                issues: list[str]
                suggestions: list[str]

            class UIDesignAgent(BaseAgent):
                def run(self, state):
                    screenshots = state.get("screenshot_paths", [])
                    analysis = self.system_with_images(
                        "You are a UX expert.",
                        "Analyse these UI screenshots for accessibility issues.",
                        images=screenshots,
                        schema=UIAnalysis,
                    )
                    return {"ui_analysis": analysis.model_dump()}
        """
        if not hasattr(self.llm, "system_multimodal"):
            raise NotImplementedError(
                f"{type(self.llm).__name__} does not support multi-modal input. "
                "Use GeminiModel or a vision-capable OpenAIModel."
            )

        if self.preset is not None:
            system_prompt = self.preset.apply(system_prompt)
        user = self._inject_memory(user)
        if self.system_prompt_suffix:
            system_prompt = system_prompt + "\n\n" + self.system_prompt_suffix

        self.llm.current_agent = self.name
        response = self.llm.system_multimodal(system_prompt, user, images, **kwargs)
        self._check_agent_cost()

        if schema is None:
            return response
        return self._extract_schema(response, schema)

    def _extract_schema(self, response: str, schema: type):
        """Parse *response* as JSON and coerce to *schema* (Pydantic BaseModel)."""
        try:
            data = _json_loads(response)
        except Exception:
            stripped = _strip_fences(response)
            data = _json_loads(stripped)
        return schema(**data) if isinstance(data, dict) else data

    def system_with_tools(
        self,
        system_prompt: str,
        user: str,
        *,
        tools: Optional[list["BaseTool"]] = None,
        max_tool_steps: Optional[int] = None,
        **kwargs,
    ) -> str:
        """Call LLM with a ReAct tool-use loop.

        The agent iterates up to *max_tool_steps* times.  On each step it
        checks the LLM response for a ``<tool_call>`` block; if found, it
        executes the tool and feeds the result back as context before the next
        LLM call.  The loop exits when the model produces a response with no
        ``<tool_call>`` tags, which is returned as the final answer.

        *tools* defaults to ``self.tools``.  *max_tool_steps* defaults to
        ``self.max_tool_steps``.
        """
        active_tools = tools if tools is not None else self.tools
        steps = max_tool_steps if max_tool_steps is not None else self.max_tool_steps

        if not active_tools:
            return self.system(system_prompt, user, **kwargs)

        tool_map = {t.name: t for t in active_tools}
        schemas = "\n".join(t.schema() for t in active_tools)
        full_system = system_prompt + _TOOL_SYSTEM_SUFFIX.format(tool_schemas=schemas)

        history: list[str] = []
        current_user = user

        for step in range(steps):
            if history:
                current_user = user + "\n\n" + "\n".join(history)

            response = self.system(full_system, current_user, **kwargs)

            match = _TOOL_CALL_RE.search(response)
            if not match:
                return response

            tool_name = match.group(1).strip()
            tool_input = match.group(2).strip()

            log.debug(
                "tool_call agent=%s tool=%s step=%d/%d",
                self.name, tool_name, step + 1, steps,
            )

            tool = tool_map.get(tool_name)
            if tool is None:
                result_text = f"ERROR: unknown tool '{tool_name}'. Available: {list(tool_map)}"
            else:
                result = tool.run(tool_input)
                result_text = str(result)

            history.append(
                f"[Tool call: {tool_name}]\n{tool_input}\n\n"
                f"[Tool result: {tool_name}]\n{result_text}"
            )

        log.warning(
            "tool_loop_exhausted agent=%s steps=%d", self.name, steps
        )
        return self.system(full_system, current_user, **kwargs)

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

        On parse or validation failure, retries up to *max_retries* times.
        Each retry appends a hint to the user message containing the previous
        error and the invalid response so the model can self-correct.
        **kwargs are forwarded to self.system() (e.g. max_tokens).
        """
        schema_name = getattr(schema, "__name__", str(schema))
        raw = self.system(system_prompt, user, json_mode=True, **kwargs)
        last_exc: Exception = ValueError("no attempts made")

        for attempt in range(1 + max_retries):
            if attempt > 0:
                hint = (
                    f"\n\nYour previous response was invalid.\n"
                    f"Error: {last_exc}\n"
                    f"Previous response:\n{raw[:500]}\n"
                    "Respond ONLY with valid JSON — no prose, no code fences."
                )
                log.debug(
                    "parse_retry agent=%s schema=%s attempt=%d/%d",
                    self.name, schema_name, attempt, max_retries,
                )
                raw = self.system(system_prompt, user + hint, json_mode=True, **kwargs)

            try:
                return _validate_schema(schema, _json_loads(_extract_json(raw)))
            except (ValidationError, ValueError) as exc:
                last_exc = exc

        log.warning(
            "parse_failure agent=%s schema=%s attempts=%d error=%s raw=%.300s",
            self.name, schema_name, 1 + max_retries, last_exc, raw,
        )
        self._tracelog(
            "parse_failure",
            schema=schema_name,
            error=str(last_exc),
            raw_preview=raw[:200],
        )
        raise last_exc

    def system_structured(
        self,
        system_prompt: str,
        user: str,
        schema: Any = None,
        *,
        max_retries: int = 1,
        **kwargs,
    ) -> Any:
        """Call LLM and return a validated Pydantic model.

        Uses *schema* if provided, otherwise falls back to ``self.output_schema``.
        Raises ``ValueError`` if neither is set.  Retries up to *max_retries* times
        on parse / validation failure (delegates to ``system_parsed``).

        Example::

            class MyOutput(BaseModel):
                summary: str
                action_items: list[str]

            agent = MyAgent(llm=llm, output_schema=MyOutput)
            result: MyOutput = agent.system_structured("You are…", "Summarise…")
            print(result.summary)
        """
        effective_schema = schema if schema is not None else getattr(self, "output_schema", None)
        if effective_schema is None:
            raise ValueError(
                "system_structured() requires a schema. "
                "Pass one explicitly or set output_schema on the agent."
            )
        return self.system_parsed(
            system_prompt, user, effective_schema, max_retries=max_retries, **kwargs
        )

    def focused_state(self, state: TeamState) -> dict:
        """Return a trimmed copy of *state* containing only keys this agent needs.

        Includes the agent's declared ``consumes`` keys plus a fixed set of
        pass-through keys (``request``, ``messages``, ``thread_id``, ``metadata``,
        ``_kb_context``) that most agents implicitly rely on.

        If the agent has no ``consumes`` attribute (older agents), the full state
        is returned unchanged so behaviour is never regressed.

        Using this in ``run()`` reduces prompt size for agents that only need a
        slice of a large TeamState::

            def run(self, state):
                local = self.focused_state(state)
                raw = self.system(SYSTEM_PROMPT, json.dumps(local))
                ...
        """
        consumes: list[str] = getattr(self, "consumes", [])
        if not consumes:
            return dict(state)
        _ALWAYS_INCLUDE = {"request", "messages", "thread_id", "metadata", "_kb_context"}
        keep = set(consumes) | _ALWAYS_INCLUDE
        return {k: v for k, v in state.items() if k in keep and v is not None}

    def _check_agent_cost(self) -> None:
        """Raise CostLimitExceeded if this agent's accumulated cost exceeds max_cost_usd."""
        if self.max_cost_usd is None:
            return
        entries = [e for e in self.llm._usage_log if e.get("agent") == self.name]
        cost = sum(e.get("cost_usd", 0.0) for e in entries)
        if cost > self.max_cost_usd:
            from antcrew.core.exceptions import CostLimitExceeded
            raise CostLimitExceeded(cost_usd=cost, limit_usd=self.max_cost_usd)

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

    def _mem_get(self, key: str) -> Optional[Any]:
        """Read a persistent key from KV memory (None when not set or memory not wired)."""
        if self.kv_memory is None:
            return None
        return self.kv_memory.get(key)

    def _mem_set(self, key: str, value: Any) -> None:
        """Write a persistent key to KV memory (no-op when memory not wired)."""
        if self.kv_memory is not None:
            self.kv_memory.set(key, value)

    def _tracelog(self, event: str, **kwargs) -> None:
        """Emit a structured trace event to the global EventBus.

        Events are correlated with the current run via ``_run_id`` and
        ``_thread_id`` (set automatically by the team runner before each agent
        invocation). Subscribers on the platform side persist them as
        ``agent.tracelog`` rows in the event log.

        Built-in events emitted by BaseAgent:

        - ``parse_failure`` — JSON parse error after all retries exhausted
        - ``llm.call`` — every LLM invocation via :meth:`system`

        Custom agents can call ``self._tracelog("my.event", key=value)`` to
        record domain-specific trace points.
        """
        from antcrew.core.events import Event, bus
        bus.emit(Event(
            "agent.tracelog",
            {"agent": self.name, "stage": self.stage, "event": event, **kwargs},
            run_id=self._run_id,
            thread_id=self._thread_id,
        ))
        log.debug("tracelog agent=%s event=%s %s", self.name, event, kwargs)

    @property
    def governance_hash(self) -> str:
        """16-char SHA-256 prefix over this agent's immutable configuration.

        Covers name, role_description, stage, system_prompt_suffix, and tool
        names (sorted).  Two agents with identical configs produce the same
        hash, enabling run-level provenance checks::

            if agent_a.governance_hash == agent_b.governance_hash:
                # configs are identical; comparison is apples-to-apples
        """
        components = [
            f"name:{self.name}",
            f"role:{self.role_description}",
            f"stage:{self.stage}",
            f"suffix:{self.system_prompt_suffix or ''}",
            f"tools:{','.join(sorted(t.name for t in self.tools))}",
        ]
        return _hashlib.sha256("|".join(components).encode()).hexdigest()[:16]

    def refine(self, state: TeamState, artifact, feedback: str) -> dict:
        """
        Refine the last artifact based on human feedback (conversational mode).

        Override this and set ``conversational = True`` to enable multi-turn
        dialogue. Must return a **partial** state dict with only the updated
        artifact key — do NOT update ``current_agent`` or ``messages`` here.

        Default: no-op (empty dict → artifact unchanged).
        """
        return {}


def compute_team_hash(agents: "list[BaseAgent]") -> str:
    """Stable 16-char hash for a team's full agent configuration.

    Combines each agent's :attr:`~BaseAgent.governance_hash` (sorted by name)
    so two runs with the same team hash used exactly the same prompts, tools,
    and stage layout::

        hash_a = compute_team_hash(team.agents)
        # ... deploy new prompt ...
        hash_b = compute_team_hash(team.agents)
        if hash_a != hash_b:
            print("Config changed — baseline comparison invalid")
    """
    components = sorted(f"{a.name}:{a.governance_hash}" for a in agents)
    return _hashlib.sha256("|".join(components).encode()).hexdigest()[:16]
