"""TemplateAgent — YAML-defined agents without writing Python.

Allows teams to be extended with custom agents whose system prompt and
I/O wiring are declared in a YAML (or JSON) config file instead of code.

YAML format::

    name: security_reviewer          # required
    role_description: "Reviews code" # optional
    system_prompt: |                 # required
        You are a senior security engineer. Review the provided code for
        SQL injection, XSS, and authentication weaknesses. Return your
        findings as structured JSON.
    input_key: request               # optional (default: "request")
    output_key: security_review      # optional (default: "{name}_output")
    max_tokens: 4096                 # optional

Usage::

    from antcrew.agents.template_agent import TemplateAgent, load_template_agent
    from antcrew.models.simulated import SimulatedLLM

    # From a dict
    agent = TemplateAgent(
        {
            "name": "summarizer",
            "system_prompt": "Summarize the request in one sentence.",
            "output_key": "summary",
        },
        llm=SimulatedLLM(),
    )

    # From a YAML file
    agent = load_template_agent("agents/security_reviewer.yaml", SimulatedLLM())

    # Inline in a pipeline (TemplateAgent is a full BaseAgent)
    result = agent.run({"request": "Build JWT auth"})
    print(result["summary"])
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from antcrew.core.agent import BaseAgent
from antcrew.core.state import TeamState

if TYPE_CHECKING:
    from antcrew.models.base import BaseLLM


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _parse_cfg_text(text: str, suffix: str = "") -> dict:
    """Parse YAML or JSON text into a dict."""
    if suffix.lower() == ".json":
        return json.loads(text)
    try:
        import yaml  # type: ignore[import]
        result = yaml.safe_load(text)
        if not isinstance(result, dict):
            raise ValueError(f"Expected a YAML mapping, got {type(result).__name__}")
        return result
    except ImportError:
        # Fall back to JSON if PyYAML isn't available (shouldn't happen — it's
        # a core dependency, but keep the code defensive).
        return json.loads(text)


def _load_cfg(config: "dict | str | Path") -> dict:
    """Resolve config to a plain dict regardless of input type."""
    if isinstance(config, dict):
        return config

    if not isinstance(config, (str, Path)):
        raise TypeError(
            f"config must be a dict, a file path, or raw YAML/JSON text; got {type(config).__name__}"
        )

    p = Path(config) if isinstance(config, str) else config

    if p.exists():
        text = p.read_text(encoding="utf-8")
        return _parse_cfg_text(text, suffix=p.suffix)

    # Treat a non-path str as raw YAML/JSON content
    if isinstance(config, str):
        return _parse_cfg_text(config)

    raise TypeError(
        f"config must be a dict, a file path, or raw YAML/JSON text; got {type(config).__name__}"
    )


def _validate_cfg(cfg: dict) -> None:
    """Raise ValueError for any missing required keys."""
    missing = [k for k in ("name", "system_prompt") if not cfg.get(k)]
    if missing:
        raise ValueError(
            f"TemplateAgent config is missing required key(s): {missing}\n"
            "Both 'name' and 'system_prompt' must be non-empty strings."
        )


# ---------------------------------------------------------------------------
# TemplateAgent
# ---------------------------------------------------------------------------

class TemplateAgent(BaseAgent):
    """An agent whose entire behaviour is declared in a YAML / dict config.

    The agent reads a *user message* from ``state[input_key]``, calls the LLM
    with the configured ``system_prompt``, and writes the response string to
    ``state[output_key]``.

    Args:
        config:    Agent configuration — a dict, a file Path, or raw YAML text.
        llm:       The LLM to use for this agent.
        **kwargs:  Forwarded to :class:`~antcrew.core.agent.BaseAgent` (e.g.
                   ``preset``, ``channel``, ``max_tokens``).

    Raises:
        ValueError: If *config* is missing the required ``name`` or
            ``system_prompt`` keys.
        TypeError:  If *config* is not a recognised type.
    """

    # Defaults overridden at __init__ time from config
    name: str = "template"
    role_description: str = ""

    def __init__(
        self,
        config: "dict | str | Path",
        llm: "BaseLLM",
        **kwargs: Any,
    ) -> None:
        cfg = _load_cfg(config)
        _validate_cfg(cfg)

        self.name = cfg["name"]
        self.role_description = cfg.get("role_description", "")
        self._system_prompt: str = cfg["system_prompt"]
        self._input_key: str = cfg.get("input_key", "request")
        self._output_key: str = cfg.get("output_key", f"{self.name}_output")

        # max_tokens in config acts as a default; explicit kwarg takes precedence
        if "max_tokens" in cfg and "max_tokens" not in kwargs:
            kwargs["max_tokens"] = int(cfg["max_tokens"])

        super().__init__(llm, **kwargs)

    # ------------------------------------------------------------------
    # BaseAgent contract
    # ------------------------------------------------------------------

    def run(self, state: TeamState) -> dict:  # type: ignore[override]
        """Call the LLM with this agent's system prompt and return the result.

        Reads ``state[input_key]`` (default ``"request"``) as the user message.
        Writes the LLM response to ``{output_key}`` in the returned dict.
        """
        raw = state.get(self._input_key)

        if raw is None:
            user_msg = ""
        elif isinstance(raw, str):
            user_msg = raw
        elif isinstance(raw, list):
            user_msg = str(raw[-1]) if raw else ""
        else:
            user_msg = str(raw)

        response = self.system(self._system_prompt, user_msg)
        return {self._output_key: response}


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def load_template_agent(
    path: "str | Path",
    llm: "BaseLLM",
    **kwargs: Any,
) -> TemplateAgent:
    """Load a :class:`TemplateAgent` from a YAML or JSON file.

    Equivalent to ``TemplateAgent(Path(path), llm, **kwargs)`` but communicates
    intent clearly at the call site.

    Example::

        agent = load_template_agent("agents/summarizer.yaml", AnthropicModel())
    """
    return TemplateAgent(Path(path), llm, **kwargs)
