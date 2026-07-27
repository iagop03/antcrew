"""Tests for OpenAIModel, SimulatedLLM, and config loader."""
import json
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# SimulatedLLM
# ---------------------------------------------------------------------------

def test_simulated_llm_returns_prd_fixture():
    from antcrew.models.simulated import SimulatedLLM

    llm = SimulatedLLM()
    result = llm.system(
        "You are a Business Analyst. Produce a PRD with functional_requirements.",
        "Build a login module",
    )
    data = json.loads(result)
    assert "title" in data
    assert "functional_requirements" in data


def test_simulated_llm_returns_tickets_fixture():
    from antcrew.models.simulated import SimulatedLLM

    llm = SimulatedLLM()
    result = llm.system(
        "You are a PM. Produce a JSON array of ticket objects.",
        "PRD: ...",
    )
    data = json.loads(result)
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "title" in data[0]


def test_simulated_llm_returns_code_fixture():
    from antcrew.models.simulated import SimulatedLLM

    llm = SimulatedLLM()
    result = llm.system(
        "You are a Backend Developer. Produce code artifact objects to implement the ticket.",
        "Ticket: ...",
    )
    data = json.loads(result)
    assert isinstance(data, list)
    assert "file_path" in data[0]
    assert "content" in data[0]


def test_simulated_llm_full_dev_pipeline():
    """End-to-end pipeline with SimulatedLLM — no API calls, must complete."""
    from antcrew import DevTeam, SimulatedLLM

    team = DevTeam(model=SimulatedLLM())
    state = team.run("Build a login module", thread_id="sim-test")

    assert state.get("prd") is not None
    assert state.get("tickets") is not None
    assert len(state["tickets"]) >= 1
    assert state.get("code_artifacts") is not None


def test_simulated_llm_content_pipeline():
    """ContentTeam with SimulatedLLM must produce a content_piece with body."""
    from antcrew import ContentTeam, SimulatedLLM

    team = ContentTeam(model=SimulatedLLM())
    state = team.run("Blog post about LangGraph", thread_id="sim-content")

    piece = state.get("content_piece")
    assert piece is not None
    assert piece.title
    assert piece.body


# ---------------------------------------------------------------------------
# OpenAIModel
# ---------------------------------------------------------------------------

def test_openai_model_instantiation():
    with patch("antcrew_engine.models.openai_model.OpenAI") as mock_openai:
        from antcrew.models.openai_model import OpenAIModel

        llm = OpenAIModel(api_key="sk-fake")
        mock_openai.assert_called_once_with(api_key="sk-fake")
        assert llm._model == "gpt-4o"


def test_openai_model_custom_model():
    with patch("antcrew_engine.models.openai_model.OpenAI"):
        from antcrew.models.openai_model import OpenAIModel

        llm = OpenAIModel("gpt-4o-mini", api_key="sk-fake")
        assert llm._model == "gpt-4o-mini"


def test_openai_model_complete():
    with patch("antcrew_engine.models.openai_model.OpenAI") as mock_openai:
        from antcrew.models.base import Message
        from antcrew.models.openai_model import OpenAIModel

        mock_response = MagicMock()
        mock_response.choices[0].message.content = "Hello from GPT"
        mock_openai.return_value.chat.completions.create.return_value = mock_response

        llm = OpenAIModel(api_key="sk-fake")
        result = llm.complete([Message(role="user", content="Hi")])

        assert result == "Hello from GPT"
        mock_openai.return_value.chat.completions.create.assert_called_once()


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def test_config_load_dev_team(tmp_path):
    yaml_file = tmp_path / "agentteam.yaml"
    yaml_file.write_text(
        "team: dev\nmodel: simulated\n",
        encoding="utf-8",
    )
    from antcrew import config
    from antcrew.teams.dev_team import DevTeam

    team = config.load(yaml_file)
    assert isinstance(team, DevTeam)


def test_config_load_research_team(tmp_path):
    yaml_file = tmp_path / "agentteam.yaml"
    yaml_file.write_text("team: research\nmodel: simulated\n", encoding="utf-8")

    from antcrew import config
    from antcrew.teams.research_team import ResearchTeam

    team = config.load(yaml_file)
    assert isinstance(team, ResearchTeam)


def test_config_load_content_team(tmp_path):
    yaml_file = tmp_path / "agentteam.yaml"
    yaml_file.write_text("team: content\nmodel: simulated\n", encoding="utf-8")

    from antcrew import config
    from antcrew.teams.content_team import ContentTeam

    team = config.load(yaml_file)
    assert isinstance(team, ContentTeam)


def test_config_expands_env_vars(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_MODEL", "simulated")
    yaml_file = tmp_path / "agentteam.yaml"
    yaml_file.write_text("team: dev\nmodel: ${TEST_MODEL}\n", encoding="utf-8")

    from antcrew import config
    from antcrew.models.simulated import SimulatedLLM

    team = config.load(yaml_file)
    assert isinstance(team.llm, SimulatedLLM)


def test_config_per_agent_override(tmp_path):
    yaml_file = tmp_path / "agentteam.yaml"
    yaml_file.write_text(
        "team: dev\nmodel: simulated\nagents:\n  backend_dev:\n    model: simulated\n    approval_required: true\n",
        encoding="utf-8",
    )
    from antcrew import config

    team = config.load(yaml_file)
    assert team._agents["backend_dev"].approval_required is True
