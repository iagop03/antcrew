"""Agent presets — named prompt-style modifiers.

A preset injects a short instruction block into every system prompt, nudging
the LLM's output style without touching the agent's main logic.

Built-in presets
----------------
concise  — brief, minimal prose; no padding or pleasantries
strict   — precise, literal; follow instructions exactly; flag ambiguities
verbose  — detailed, step-by-step explanations with examples and rationale
careful  — safety-first; double-check assumptions; prefer doing less over more

Usage
-----
    from antcrew.presets import AgentPreset
    agent = BackendDevAgent(llm, preset="strict")

    # Or with a custom instruction:
    agent = BackendDevAgent(llm, preset=AgentPreset("ultra-brief", "Respond in one sentence."))
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentPreset:
    """A named prompt-style modifier applied to every system call.

    Attributes:
        name:        Short identifier used in logs and YAML config.
        instruction: Text prepended to the system prompt on every call.
    """
    name: str
    instruction: str

    def apply(self, system_prompt: str) -> str:
        """Return the system prompt with the preset instruction prepended."""
        return f"{self.instruction}\n\n{system_prompt}"


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

CONCISE = AgentPreset(
    name="concise",
    instruction=(
        "Be concise. Omit pleasantries, preamble, and padding. "
        "Deliver the result directly with no unnecessary explanation."
    ),
)

STRICT = AgentPreset(
    name="strict",
    instruction=(
        "Follow the instructions precisely and literally. "
        "Do not add unrequested content. "
        "If the instructions are ambiguous, state the ambiguity explicitly "
        "before proceeding. Prefer under-delivering to over-delivering."
    ),
)

VERBOSE = AgentPreset(
    name="verbose",
    instruction=(
        "Be thorough and detailed. Explain your reasoning step by step. "
        "Include examples, edge cases, and rationale where relevant. "
        "Prefer clarity over brevity."
    ),
)

CAREFUL = AgentPreset(
    name="careful",
    instruction=(
        "Prioritise safety and correctness above all else. "
        "Double-check your assumptions before acting. "
        "When uncertain, do less rather than more and explain why. "
        "Never make irreversible changes without explicitly flagging them."
    ),
)


_REGISTRY: dict[str, AgentPreset] = {
    p.name: p for p in (CONCISE, STRICT, VERBOSE, CAREFUL)
}


def get_preset(name: str) -> AgentPreset:
    """Return a built-in preset by name, or raise ValueError."""
    key = name.lower().strip()
    if key not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY))
        raise ValueError(
            f"Unknown preset {name!r}. Available presets: {available}. "
            "Pass an AgentPreset instance to use a custom preset."
        )
    return _REGISTRY[key]


def resolve_preset(preset: "str | AgentPreset | None") -> "AgentPreset | None":
    """Accept a string name, an AgentPreset instance, or None."""
    if preset is None:
        return None
    if isinstance(preset, AgentPreset):
        return preset
    return get_preset(preset)
