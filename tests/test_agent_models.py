"""Tests for per-agent LLM (agent_models= parameter on all 4 teams)."""
from __future__ import annotations

from antcrew.models.simulated import SimulatedLLM
from antcrew.teams.content_team import ContentTeam
from antcrew.teams.dev_team import DevTeam
from antcrew.teams.fullstack_team import FullStackTeam
from antcrew.teams.research_team import ResearchTeam

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _two_llms():
    return SimulatedLLM(), SimulatedLLM()


# ---------------------------------------------------------------------------
# _unique_llms()
# ---------------------------------------------------------------------------

def test_unique_llms_single_model():
    """All agents share one LLM → _unique_llms returns a list of 1."""
    team = DevTeam(model=SimulatedLLM())
    assert len(team._unique_llms()) == 1


def test_unique_llms_with_per_agent_model():
    """Two distinct LLMs → _unique_llms returns both."""
    primary, secondary = _two_llms()
    team = DevTeam(
        model=primary,
        agent_models={"backend_dev": secondary},
    )
    llms = team._unique_llms()
    assert len(llms) == 2
    assert primary in llms
    assert secondary in llms


def test_unique_llms_deduplicates_same_instance():
    """Same instance used for two agents counts only once."""
    shared = SimulatedLLM()
    team = DevTeam(model=shared, agent_models={"pm": shared})
    assert len(team._unique_llms()) == 1


# ---------------------------------------------------------------------------
# DevTeam — agent_models
# ---------------------------------------------------------------------------

def test_dev_team_agent_models_assigns_llm():
    primary, secondary = _two_llms()
    team = DevTeam(model=primary, agent_models={"backend_dev": secondary})
    assert team._agents["backend_dev"].llm is secondary
    assert team._agents["pm"].llm is primary


def test_dev_team_agent_models_run_produces_output():
    primary, secondary = _two_llms()
    team = DevTeam(model=primary, agent_models={"backend_dev": secondary})
    result = team.run("Build login module")
    assert result.get("code_artifacts") is not None


def test_dev_team_cost_aggregates_both_llms():
    """RunResult.cost_usd sums cost from both LLMs, not just the primary."""
    primary, secondary = _two_llms()
    team = DevTeam(model=primary, agent_models={"backend_dev": secondary})
    result = team.run("Build login module")
    primary_cost = primary.get_usage_summary()["total_cost_usd"]
    secondary_cost = secondary.get_usage_summary()["total_cost_usd"]
    assert abs(result.cost_usd - (primary_cost + secondary_cost)) < 1e-9


def test_dev_team_all_agents_use_default_when_no_agent_models():
    llm = SimulatedLLM()
    team = DevTeam(model=llm)
    for agent in team._agents.values():
        assert agent.llm is llm


# ---------------------------------------------------------------------------
# ContentTeam — agent_models
# ---------------------------------------------------------------------------

def test_content_team_agent_models_assigns_llm():
    primary, secondary = _two_llms()
    team = ContentTeam(model=primary, agent_models={"editor": secondary})
    assert team._agents["editor"].llm is secondary
    assert team._agents["idea"].llm is primary


def test_content_team_run_with_agent_models():
    primary, secondary = _two_llms()
    team = ContentTeam(model=primary, agent_models={"editor": secondary})
    result = team.run("Blog post about AI")
    assert result.get("content_piece") is not None


def test_content_team_cost_aggregates():
    primary, secondary = _two_llms()
    team = ContentTeam(model=primary, agent_models={"editor": secondary})
    result = team.run("Blog post about AI")
    total = (
        primary.get_usage_summary()["total_cost_usd"]
        + secondary.get_usage_summary()["total_cost_usd"]
    )
    assert abs(result.cost_usd - total) < 1e-9


# ---------------------------------------------------------------------------
# ResearchTeam — agent_models
# ---------------------------------------------------------------------------

def test_research_team_agent_models_assigns_llm():
    primary, secondary = _two_llms()
    team = ResearchTeam(model=primary, agent_models={"writer": secondary})
    assert team._agents["writer"].llm is secondary
    assert team._agents["researcher"].llm is primary


def test_research_team_run_with_agent_models():
    primary, secondary = _two_llms()
    team = ResearchTeam(model=primary, agent_models={"writer": secondary})
    result = team.run("AI safety research")
    assert result.get("content_piece") is not None


# ---------------------------------------------------------------------------
# FullStackTeam — agent_models
# ---------------------------------------------------------------------------

def test_fullstack_team_agent_models_assigns_llm():
    primary, secondary = _two_llms()
    team = FullStackTeam(model=primary, agent_models={"qa": secondary, "frontend_dev": secondary})
    assert team._agents["qa"].llm is secondary
    assert team._agents["frontend_dev"].llm is secondary
    assert team._agents["pm"].llm is primary


def test_fullstack_team_unique_llms_count():
    primary, secondary = _two_llms()
    team = FullStackTeam(model=primary, agent_models={"qa": secondary})
    assert len(team._unique_llms()) == 2


# ---------------------------------------------------------------------------
# Overriding via agents= still wins over agent_models=
# ---------------------------------------------------------------------------

def test_explicit_agents_override_takes_priority():
    """Explicit agents= dict wins over agent_models= for the same key."""
    from antcrew.agents.backend_dev import BackendDevAgent
    primary, secondary, override_llm = SimulatedLLM(), SimulatedLLM(), SimulatedLLM()
    explicit_agent = BackendDevAgent(override_llm)
    team = DevTeam(
        model=primary,
        agent_models={"backend_dev": secondary},
        agents={"backend_dev": explicit_agent},
    )
    assert team._agents["backend_dev"] is explicit_agent
    assert team._agents["backend_dev"].llm is override_llm
