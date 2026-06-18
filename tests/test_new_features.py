"""
Tests for 4 new features:
  1. Artifact tracking (created_at_run)
  2. TelegramChannel notify parameter
  3. TelegramChannel HITL — Sugerir cambios / feedback flow
  4. SetupAgent / antcrew setup wizard
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from antcrew.core.artifacts import (
    CodeArtifact,
    DevOpsArtifact,
    DocumentationArtifact,
    TestArtifact,
    Ticket,
)
from antcrew.models.simulated import SimulatedLLM
from antcrew.project import Project
from antcrew.teams.dev_team import DevTeam


# ===========================================================================
# 1. Artifact tracking -- created_at_run
# ===========================================================================

class TestArtifactTracking:
    def test_code_artifact_has_created_at_run(self):
        a = CodeArtifact(ticket_id="T1", file_path="x.py", description="d", content="c")
        assert a.created_at_run == 0

    def test_test_artifact_has_created_at_run(self):
        a = TestArtifact(ticket_id="T1", file_path="x.py", description="d", content="c")
        assert a.created_at_run == 0

    def test_devops_artifact_has_created_at_run(self):
        a = DevOpsArtifact(file_path="Dockerfile", description="d", language="dockerfile", content="c")
        assert a.created_at_run == 0

    def test_doc_artifact_has_created_at_run(self):
        a = DocumentationArtifact(file_path="README.md", title="T", doc_type="readme", content="c")
        assert a.created_at_run == 0

    def test_created_at_run_can_be_set(self):
        a = CodeArtifact(ticket_id="T1", file_path="x.py", description="d", content="c", created_at_run=3)
        assert a.created_at_run == 3

    def test_model_copy_update(self):
        a = CodeArtifact(ticket_id="T1", file_path="x.py", description="d", content="c")
        b = a.model_copy(update={"created_at_run": 2})
        assert b.created_at_run == 2
        assert a.created_at_run == 0

    def test_serialization_roundtrip(self):
        a = CodeArtifact(ticket_id="T1", file_path="x.py", description="d", content="c", created_at_run=7)
        data = a.model_dump()
        b = CodeArtifact.model_validate(data)
        assert b.created_at_run == 7


class TestProjectArtifactTracking:
    def _make_project(self):
        team = DevTeam(model=SimulatedLLM())
        return Project(team, name="test")

    def _state(self, code_files: int = 1) -> dict:
        from antcrew.core.artifacts import PRD
        return {
            "prd": PRD(title="T", summary="S"),
            "tickets": [Ticket(id="T1", title="t1", description="d1")],
            "code_artifacts": [
                CodeArtifact(ticket_id="T1", file_path=f"f{i}.py", description="d", content="c")
                for i in range(code_files)
            ],
            "test_artifacts": [],
            "devops_artifacts": [],
            "doc_artifacts": [],
            "errors": [],
            "current_agent": "backend_dev",
            "request": "build",
        }

    def test_first_run_tagged_run_1(self):
        p = self._make_project()
        p._merge(self._state(2), "build")
        for a in p.state["code_artifacts"]:
            assert a.created_at_run == 1

    def test_second_run_tagged_run_2(self):
        p = self._make_project()
        p._merge(self._state(1), "build 1")
        p._merge(self._state(1), "build 2")
        arts = p.state["code_artifacts"]
        assert arts[0].created_at_run == 1
        assert arts[1].created_at_run == 2

    def test_history_contains_run_number(self):
        p = self._make_project()
        p._merge(self._state(), "r1")
        p._merge(self._state(), "r2")
        assert p.history[0]["run_number"] == 1
        assert p.history[1]["run_number"] == 2

    def test_pre_tagged_artifact_not_overwritten(self):
        p = self._make_project()
        state = self._state()
        state["code_artifacts"][0] = state["code_artifacts"][0].model_copy(update={"created_at_run": 99})
        p._merge(state, "build")
        assert p.state["code_artifacts"][0].created_at_run == 99

    def test_tickets_have_no_created_at_run(self):
        p = self._make_project()
        p._merge(self._state(), "build")
        ticket = p.state["tickets"][0]
        assert not hasattr(ticket, "created_at_run")


# ===========================================================================
# 2. TelegramChannel -- notify parameter
# ===========================================================================

class TestTelegramChannelNotify:
    def _ch(self, **kwargs):
        from antcrew.integrations.telegram.integration import TelegramChannel
        return TelegramChannel(token="T", **kwargs)

    def test_notify_sets_primary_chat_id(self):
        ch = self._ch(notify=[111, 222, 333])
        assert ch._chat_id == 111

    def test_notify_stores_all_ids(self):
        ch = self._ch(notify=[111, 222])
        assert ch._notify_all == [111, 222]

    def test_notify_single_id(self):
        ch = self._ch(notify=[555])
        assert ch._chat_id == 555
        assert ch._notify_all == [555]

    def test_get_notify_list_returns_all(self):
        ch = self._ch(notify=[111, 222, 333])
        assert ch._get_notify_list() == [111, 222, 333]

    def test_get_notify_list_plain_chat_id(self):
        ch = self._ch(chat_id=555)
        assert ch._get_notify_list() == [555]

    def test_get_chat_id_with_notify(self):
        ch = self._ch(notify=[101, 202])
        assert ch._get_chat_id() == 101

    def test_no_token_raises(self):
        from antcrew.integrations.telegram.integration import TelegramChannel
        with pytest.raises(ValueError, match="token is required"):
            TelegramChannel()

    def test_set_chat_id_updates_notify_all_when_empty(self):
        from antcrew.integrations.telegram.integration import TelegramChannel
        ch = TelegramChannel(token="T", chat_id=None)
        ch._notify_all = []
        ch.set_chat_id(777)
        assert ch._chat_id == 777
        assert 777 in ch._notify_all

    @pytest.mark.asyncio
    async def test_send_status_broadcasts_to_all(self):
        from antcrew.integrations.telegram.integration import TelegramChannel
        ch = TelegramChannel(token="T", notify=[111, 222])
        mock_bot = AsyncMock()
        with patch("antcrew.integrations.telegram.integration.Bot", return_value=mock_bot):
            await ch.send_status("hello")
        calls = [c.kwargs["chat_id"] for c in mock_bot.send_message.await_args_list]
        assert 111 in calls
        assert 222 in calls

    @pytest.mark.asyncio
    async def test_notify_delegates_to_send_status(self):
        from antcrew.integrations.telegram.integration import TelegramChannel
        ch = TelegramChannel(token="T", notify=[333])
        mock_bot = AsyncMock()
        with patch("antcrew.integrations.telegram.integration.Bot", return_value=mock_bot):
            await ch.notify("status update")
        assert mock_bot.send_message.await_count == 1

    def test_agent_bot_config_has_notify(self):
        from antcrew.integrations.telegram.integration import AgentBotConfig
        cfg = AgentBotConfig(token="T", chat_id=123, notify=[456, 789])
        assert cfg.notify == [456, 789]

    def test_agent_bot_config_notify_defaults_empty(self):
        from antcrew.integrations.telegram.integration import AgentBotConfig
        cfg = AgentBotConfig(token="T", chat_id=123)
        assert cfg.notify == []


# ===========================================================================
# 3. TelegramChannel -- HITL buttons + feedback
# ===========================================================================

class TestTelegramHITLFeedback:
    def _ch(self):
        from antcrew.integrations.telegram.integration import TelegramChannel
        return TelegramChannel(token="T", chat_id=111)

    @pytest.mark.asyncio
    async def test_feedback_button_returns_feedback_decision(self):
        ch = self._ch()
        mock_bot = AsyncMock()

        call_count = 0

        async def mock_wait(session_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"decision": "feedback", "edited": None}
            return {"decision": "edit", "edited": "Make it simpler"}

        ch.hitl.wait = mock_wait
        ch.hitl.create = MagicMock()
        ch.hitl.set_edit_waiting = MagicMock()

        with patch("antcrew.integrations.telegram.integration.Bot", return_value=mock_bot):
            from antcrew.core.artifacts import PRD
            result = await ch.send_for_review(PRD(title="T", summary="S"), "pm", "sess1")

        assert result["decision"] == "feedback"
        assert result["feedback"] == "Make it simpler"
        assert result["edited"] is None

    @pytest.mark.asyncio
    async def test_feedback_sends_text_prompt(self):
        """After feedback button, bot asks for free text (>= 2 send_message calls)."""
        ch = self._ch()
        mock_bot = AsyncMock()

        call_count = 0

        async def mock_wait(session_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"decision": "feedback", "edited": None}
            return {"decision": "edit", "edited": "my suggestion"}

        ch.hitl.wait = mock_wait
        ch.hitl.create = MagicMock()
        ch.hitl.set_edit_waiting = MagicMock()

        with patch("antcrew.integrations.telegram.integration.Bot", return_value=mock_bot):
            from antcrew.core.artifacts import PRD
            await ch.send_for_review(PRD(title="T", summary="S"), "pm", "sess1")

        assert mock_bot.send_message.await_count >= 2

    @pytest.mark.asyncio
    async def test_reject_returns_immediately(self):
        ch = self._ch()
        mock_bot = AsyncMock()

        async def mock_wait(session_id):
            return {"decision": "reject", "edited": None}

        ch.hitl.wait = mock_wait
        ch.hitl.create = MagicMock()

        with patch("antcrew.integrations.telegram.integration.Bot", return_value=mock_bot):
            from antcrew.core.artifacts import PRD
            result = await ch.send_for_review(PRD(title="T", summary="S"), "pm", "sess1")

        assert result["decision"] == "reject"
        assert result["feedback"] is None
        assert result["edited"] is None

    @pytest.mark.asyncio
    async def test_approve_returns_immediately(self):
        ch = self._ch()
        mock_bot = AsyncMock()

        async def mock_wait(session_id):
            return {"decision": "approve", "edited": None}

        ch.hitl.wait = mock_wait
        ch.hitl.create = MagicMock()

        with patch("antcrew.integrations.telegram.integration.Bot", return_value=mock_bot):
            from antcrew.core.artifacts import PRD
            result = await ch.send_for_review(PRD(title="T", summary="S"), "pm", "sess1")

        assert result["decision"] == "approve"
        assert result["feedback"] is None

    @pytest.mark.asyncio
    async def test_edit_json_flow(self):
        ch = self._ch()
        mock_bot = AsyncMock()

        call_count = 0

        async def mock_wait(session_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"decision": "edit", "edited": None}
            return {"decision": "edit", "edited": '{"title": "New"}'}

        ch.hitl.wait = mock_wait
        ch.hitl.create = MagicMock()
        ch.hitl.set_edit_waiting = MagicMock()

        with patch("antcrew.integrations.telegram.integration.Bot", return_value=mock_bot):
            from antcrew.core.artifacts import PRD
            result = await ch.send_for_review(
                PRD(title="T", summary="S"), "pm", "sess1",
                options=["approve", "edit", "reject"],
            )

        assert result["decision"] == "edit"
        assert result["edited"] == '{"title": "New"}'
        assert result["feedback"] is None

    def test_build_keyboard_default_options(self):
        from antcrew.integrations.telegram.integration import _build_keyboard
        kb = _build_keyboard("s1", ["approve", "feedback", "reject"])
        labels = [b.text for b in kb.inline_keyboard[0]]
        assert any("Aprobar" in l for l in labels)
        assert any("Sugerir" in l for l in labels)
        assert any("Rechazar" in l for l in labels)

    def test_build_keyboard_edit_option(self):
        from antcrew.integrations.telegram.integration import _build_keyboard
        kb = _build_keyboard("s1", ["approve", "edit", "reject"])
        labels = [b.text for b in kb.inline_keyboard[0]]
        assert any("Editar JSON" in l for l in labels)
        assert not any("Sugerir" in l for l in labels)

    def test_build_keyboard_callback_data(self):
        from antcrew.integrations.telegram.integration import _build_keyboard
        kb = _build_keyboard("mysess", ["approve", "feedback"])
        callbacks = [b.callback_data for b in kb.inline_keyboard[0]]
        assert "approve:mysess" in callbacks
        assert "feedback:mysess" in callbacks


# ===========================================================================
# 4. SetupAgent / antcrew setup
# ===========================================================================

class TestSetupAgent:
    """
    When name= is provided, Prompt.ask is called 4 times:
    [description, type_choice, model_choice, runner_choice]
    Confirm.ask is called 2 times: [use_project, use_cache]
    """

    _P = ["", "1", "1", "1"]   # defaults: desc="", software, claude, no runner
    _C = [False, False]          # defaults: no project, no cache

    def _w(self, tmp_path, *, name="test", prompts=None, confirms=None, **kw):
        from antcrew.agents.setup import SetupAgent
        agent = SetupAgent()
        with patch("rich.prompt.Prompt.ask", side_effect=prompts or self._P), \
             patch("rich.prompt.Confirm.ask", side_effect=confirms or self._C):
            return agent.run_wizard(name=name, output_dir=tmp_path, **kw)

    def test_generates_yaml_file(self, tmp_path):
        dest = self._w(tmp_path)
        assert dest.exists()
        assert dest.suffix == ".yaml"

    def test_default_filename(self, tmp_path):
        dest = self._w(tmp_path)
        assert dest.name == "agentteam.yaml"

    def test_custom_filename(self, tmp_path):
        dest = self._w(tmp_path, filename="team.yaml")
        assert dest.name == "team.yaml"

    def test_name_is_slugified(self, tmp_path):
        dest = self._w(tmp_path, name="Mi App de Reservas!")
        assert "mi-app-de-reservas" in dest.read_text()

    def test_team_dev(self, tmp_path):
        dest = self._w(tmp_path, prompts=["", "1", "1", "1"])
        assert "team: dev" in dest.read_text()

    def test_team_fullstack(self, tmp_path):
        dest = self._w(tmp_path, prompts=["", "2", "1", "1"])
        assert "team: fullstack" in dest.read_text()

    def test_team_research(self, tmp_path):
        dest = self._w(tmp_path, prompts=["", "3", "1", "1"])
        assert "team: research" in dest.read_text()

    def test_team_content(self, tmp_path):
        dest = self._w(tmp_path, prompts=["", "4", "1", "1"])
        assert "team: content" in dest.read_text()

    def test_claude_provider(self, tmp_path):
        dest = self._w(tmp_path, prompts=["", "1", "1", "1"])
        content = dest.read_text()
        assert "provider: claude" in content
        assert "claude-sonnet" in content

    def test_gpt4_provider(self, tmp_path):
        dest = self._w(tmp_path, prompts=["", "1", "2", "1"])
        assert "provider: gpt4" in dest.read_text()

    def test_gemini_provider(self, tmp_path):
        dest = self._w(tmp_path, prompts=["", "1", "3", "1"])
        assert "provider: gemini" in dest.read_text()

    def test_ollama_provider(self, tmp_path):
        dest = self._w(tmp_path, prompts=["", "1", "4", "1"])
        content = dest.read_text()
        assert "provider: ollama" in content
        assert "base_url" in content

    def test_simulated_provider(self, tmp_path):
        dest = self._w(tmp_path, prompts=["", "1", "5", "1"])
        assert "provider: simulated" in dest.read_text()

    def test_project_block_enabled(self, tmp_path):
        dest = self._w(tmp_path, name="myapp",
                       prompts=["", "1", "1", "1"], confirms=[True, False])
        assert "project: myapp.json" in dest.read_text()

    def test_project_block_disabled(self, tmp_path):
        dest = self._w(tmp_path, confirms=[False, False])
        assert "project:" not in dest.read_text()

    def test_cache_block_enabled(self, tmp_path):
        dest = self._w(tmp_path, confirms=[False, True])
        content = dest.read_text()
        assert "cache:" in content
        assert ".antcrew/cache.db" in content

    def test_cache_block_disabled(self, tmp_path):
        dest = self._w(tmp_path, confirms=[False, False])
        assert "cache:" not in dest.read_text()

    def test_local_runner(self, tmp_path):
        dest = self._w(tmp_path, prompts=["", "1", "1", "2"])
        content = dest.read_text()
        assert "type: local" in content
        assert "test_cmd: pytest" in content

    def test_docker_runner(self, tmp_path):
        dest = self._w(tmp_path, prompts=["", "1", "1", "3"])
        assert "type: docker" in dest.read_text()

    def test_no_runner(self, tmp_path):
        dest = self._w(tmp_path, prompts=["", "1", "1", "1"])
        assert "runner:" not in dest.read_text()

    def test_creates_nested_output_dir(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        dest = self._w(nested)
        assert dest.exists()

    def test_empty_choice_falls_back_to_default(self, tmp_path):
        dest = self._w(tmp_path, prompts=["", "", "", ""])
        content = dest.read_text()
        assert "team: dev" in content
        assert "provider: claude" in content

    def test_yaml_contains_run_example(self, tmp_path):
        dest = self._w(tmp_path, prompts=["Build a REST API", "1", "1", "1"])
        content = dest.read_text()
        assert "antcrew run" in content

    def test_slugify_exclamation(self):
        from antcrew.agents.setup import _slugify
        assert _slugify("My App!") == "my-app"

    def test_slugify_spaces(self):
        from antcrew.agents.setup import _slugify
        assert _slugify("  spaces  ") == "spaces"

    def test_slugify_underscores(self):
        from antcrew.agents.setup import _slugify
        assert _slugify("a_b_c") == "a-b-c"

    def test_slugify_lowercase(self):
        from antcrew.agents.setup import _slugify
        assert _slugify("MyApp") == "myapp"


class TestSetupCLI:
    def test_setup_command_exists(self):
        from typer.testing import CliRunner
        from antcrew.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["setup", "--help"])
        assert result.exit_code == 0
        assert "wizard" in result.output.lower() or "setup" in result.output.lower()

    def test_setup_command_with_name_flag(self, tmp_path):
        from typer.testing import CliRunner
        from antcrew.cli import app
        runner = CliRunner()
        with patch("rich.prompt.Prompt.ask", side_effect=["", "1", "1", "1"]), \
             patch("rich.prompt.Confirm.ask", side_effect=[False, False]):
            result = runner.invoke(app, ["setup", "--name", "myproj", "--output", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / "agentteam.yaml").exists()
