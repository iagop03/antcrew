"""Tests for agent presets (antcrew.presets and BaseAgent preset param)."""
from __future__ import annotations

import pytest

from antcrew.presets import (
    AgentPreset,
    CONCISE,
    STRICT,
    VERBOSE,
    CAREFUL,
    get_preset,
    resolve_preset,
)
from antcrew.models.simulated import SimulatedLLM


# ===========================================================================
# AgentPreset dataclass
# ===========================================================================

def test_agent_preset_apply_prepends_instruction():
    p = AgentPreset(name="test", instruction="Be brief.")
    result = p.apply("You are a developer.")
    assert result.startswith("Be brief.")
    assert "You are a developer." in result


def test_agent_preset_apply_has_blank_line_separator():
    p = AgentPreset(name="test", instruction="X")
    result = p.apply("Y")
    assert "\n\n" in result


def test_agent_preset_is_frozen():
    p = AgentPreset(name="test", instruction="X")
    with pytest.raises((TypeError, AttributeError)):
        p.name = "changed"


# ===========================================================================
# Built-in presets
# ===========================================================================

def test_builtin_presets_exist():
    for p in (CONCISE, STRICT, VERBOSE, CAREFUL):
        assert isinstance(p, AgentPreset)
        assert p.name
        assert p.instruction


def test_get_preset_concise():
    p = get_preset("concise")
    assert p is CONCISE


def test_get_preset_strict():
    p = get_preset("strict")
    assert p is STRICT


def test_get_preset_verbose():
    p = get_preset("verbose")
    assert p is VERBOSE


def test_get_preset_careful():
    p = get_preset("careful")
    assert p is CAREFUL


def test_get_preset_case_insensitive():
    assert get_preset("CONCISE") is CONCISE
    assert get_preset("Strict") is STRICT


def test_get_preset_strips_whitespace():
    assert get_preset("  verbose  ") is VERBOSE


def test_get_preset_unknown_raises():
    with pytest.raises(ValueError, match="Unknown preset"):
        get_preset("nonexistent")


def test_get_preset_error_lists_available():
    with pytest.raises(ValueError, match="concise"):
        get_preset("nope")


# ===========================================================================
# resolve_preset
# ===========================================================================

def test_resolve_preset_none():
    assert resolve_preset(None) is None


def test_resolve_preset_string():
    assert resolve_preset("concise") is CONCISE


def test_resolve_preset_instance():
    custom = AgentPreset("x", "X instruction.")
    assert resolve_preset(custom) is custom


def test_resolve_preset_unknown_string_raises():
    with pytest.raises(ValueError):
        resolve_preset("invented")


# ===========================================================================
# BaseAgent integration
# ===========================================================================

def _make_agent(preset=None, suffix=None):
    from antcrew.agents.pm import PMAgent
    llm = SimulatedLLM()
    return PMAgent(llm, preset=preset, system_prompt_suffix=suffix)


def test_agent_accepts_string_preset():
    agent = _make_agent(preset="concise")
    assert agent.preset is CONCISE


def test_agent_accepts_preset_instance():
    custom = AgentPreset("x", "Custom instruction.")
    agent = _make_agent(preset=custom)
    assert agent.preset is custom


def test_agent_none_preset_is_none():
    agent = _make_agent()
    assert agent.preset is None


def test_agent_invalid_preset_raises():
    with pytest.raises(ValueError):
        _make_agent(preset="nonexistent")


def test_agent_preset_applied_to_system_prompt():
    """Preset instruction appears at the top of the captured system prompt."""
    from antcrew.agents.pm import PMAgent

    captured: list[str] = []

    class SpyLLM(SimulatedLLM):
        def complete(self, messages, *, max_tokens=4096):
            captured.append(messages[0].content)  # system message
            return super().complete(messages, max_tokens=max_tokens)

    llm = SpyLLM()
    agent = PMAgent(llm, preset="strict")
    agent.system("You are a PM.", "Build X")

    assert len(captured) == 1
    assert captured[0].startswith(STRICT.instruction)
    assert "You are a PM." in captured[0]


def test_agent_preset_before_suffix():
    """Preset instruction comes before system_prompt_suffix."""
    from antcrew.agents.pm import PMAgent

    captured: list[str] = []

    class SpyLLM(SimulatedLLM):
        def complete(self, messages, *, max_tokens=4096):
            captured.append(messages[0].content)
            return super().complete(messages, max_tokens=max_tokens)

    llm = SpyLLM()
    agent = PMAgent(llm, preset="concise", system_prompt_suffix="Extra suffix.")
    agent.system("Base prompt.", "user input")

    sys = captured[0]
    assert sys.index(CONCISE.instruction) < sys.index("Base prompt.")
    assert sys.index("Base prompt.") < sys.index("Extra suffix.")


def test_agent_no_preset_prompt_unchanged():
    """Without a preset the system prompt is passed through as-is (plus suffix if any)."""
    from antcrew.agents.pm import PMAgent

    captured: list[str] = []

    class SpyLLM(SimulatedLLM):
        def complete(self, messages, *, max_tokens=4096):
            captured.append(messages[0].content)
            return super().complete(messages, max_tokens=max_tokens)

    llm = SpyLLM()
    agent = PMAgent(llm)
    agent.system("You are a PM.", "Build X")

    assert captured[0] == "You are a PM."


# ===========================================================================
# Public API
# ===========================================================================

def test_presets_importable_from_antcrew():
    import antcrew
    assert hasattr(antcrew, "AgentPreset")
    assert hasattr(antcrew, "CONCISE")
    assert hasattr(antcrew, "STRICT")
    assert hasattr(antcrew, "VERBOSE")
    assert hasattr(antcrew, "CAREFUL")
    assert hasattr(antcrew, "get_preset")


def test_concise_preset_content():
    assert "concise" in CONCISE.name


def test_strict_preset_content():
    assert "strict" in STRICT.name


def test_verbose_preset_content():
    assert "verbose" in VERBOSE.name


def test_careful_preset_content():
    assert "careful" in CAREFUL.name
