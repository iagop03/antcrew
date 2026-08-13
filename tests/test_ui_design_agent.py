"""Tests for UIDesignAgent and FrontendDevAgent's ui_design_spec consumption."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from antcrew.agents.frontend_dev import FrontendDevAgent
from antcrew.agents.ui_design import UIDesignAgent
from antcrew.core.artifacts import (
    DesignTokens,
    PRD,
    Priority,
    Screen,
    Ticket,
    TicketStatus,
    UIDesignSpec,
)
from antcrew.core.state import TeamState


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _prd() -> PRD:
    return PRD(
        title="Task Manager",
        summary="A simple task management app for small teams",
        functional_requirements=["Users can create, update, and delete tasks"],
    )


def _tickets() -> list[Ticket]:
    return [
        Ticket(
            id="TICKET-001",
            title="Task list view",
            description="Show all tasks in a list",
            priority=Priority.HIGH,
            status=TicketStatus.OPEN,
        ),
        Ticket(
            id="TICKET-002",
            title="Create task form",
            description="Form to create a new task",
            priority=Priority.HIGH,
            status=TicketStatus.OPEN,
        ),
    ]


def _state(**overrides) -> TeamState:
    base: TeamState = {
        "request": "Build a task manager",
        "messages": [{"role": "user", "content": "Build a task manager"}],
        "prd": _prd(),
        "tickets": _tickets(),
        "code_artifacts": None,
        "test_artifacts": None,
        "review": None,
        "research_document": None,
        "content_piece": None,
        "ui_design_spec": None,
        "current_agent": "",
        "errors": [],
        "metadata": {},
        "project_dir": None,
        "project_dirs": None,
        "codebase_analysis": None,
        "codebase_analyses": None,
        "devops_artifacts": None,
        "doc_artifacts": None,
        "test_results": None,
        "sprint_backlog": None,
        "sprint_number": 1,
    }
    base.update(overrides)
    return base


_VALID_SPEC: dict = {
    "screens": [
        {
            "id": "task-list",
            "name": "Task List",
            "description": "Displays all tasks",
            "route": "/tasks",
            "components": ["TaskList", "TaskItem", "TaskFilter"],
            "linked_tickets": ["TICKET-001"],
        },
        {
            "id": "create-task",
            "name": "Create Task",
            "description": "Form to create a new task",
            "route": "/tasks/new",
            "components": ["TaskForm", "TextInput", "PrioritySelect"],
            "linked_tickets": ["TICKET-002"],
        },
    ],
    "navigation_flow": ["Task List", "Create Task"],
    "tokens": {
        "color_primary": "#6366f1",
        "color_secondary": "#8b5cf6",
        "color_background": "#f9fafb",
        "color_text": "#111827",
        "font_family": "Inter, system-ui, sans-serif",
        "font_scale": {"h1": "2rem", "body": "1rem"},
    },
    "design_system": "Tailwind+shadcn",
}

_FRONTEND_FILE: list[dict] = [
    {
        "file_path": "src/pages/TaskList.tsx",
        "description": "Task list page",
        "language": "tsx",
        "content": "export default function TaskList() { return <ul /> }",
    }
]


def _mock_llm(return_value: str) -> MagicMock:
    m = MagicMock()
    m.system.return_value = return_value
    return m


# ── UIDesignAgent tests ───────────────────────────────────────────────────────

def test_ui_designer_returns_spec():
    agent = UIDesignAgent(_mock_llm(json.dumps(_VALID_SPEC)))
    result = agent.run(_state())

    assert "ui_design_spec" in result
    spec = result["ui_design_spec"]
    assert isinstance(spec, UIDesignSpec)
    assert len(spec.screens) == 2
    assert spec.screens[0].id == "task-list"
    assert spec.screens[1].route == "/tasks/new"
    assert spec.tokens.color_primary == "#6366f1"
    assert spec.design_system == "Tailwind+shadcn"
    assert result["current_agent"] == "ui_designer"


def test_ui_designer_navigation_flow():
    agent = UIDesignAgent(_mock_llm(json.dumps(_VALID_SPEC)))
    result = agent.run(_state())
    assert result["ui_design_spec"].navigation_flow == ["Task List", "Create Task"]


def test_ui_designer_linked_tickets():
    agent = UIDesignAgent(_mock_llm(json.dumps(_VALID_SPEC)))
    result = agent.run(_state())
    spec = result["ui_design_spec"]
    assert "TICKET-001" in spec.screens[0].linked_tickets
    assert "TICKET-002" in spec.screens[1].linked_tickets


def test_ui_designer_errors_without_prd():
    state = _state(prd=None)
    agent = UIDesignAgent(_mock_llm(json.dumps(_VALID_SPEC)))
    result = agent.run(state)
    assert result.get("errors")


def test_ui_designer_refine():
    updated = {**_VALID_SPEC}
    updated["design_system"] = "Material UI"
    agent = UIDesignAgent(_mock_llm(json.dumps(updated)))
    original_spec = UIDesignSpec.model_validate(_VALID_SPEC)
    result = agent.refine(_state(), original_spec, "Switch to Material UI")
    assert result["ui_design_spec"].design_system == "Material UI"


# ── FrontendDevAgent — ui_design_spec consumption ────────────────────────────

def _ui_spec() -> UIDesignSpec:
    return UIDesignSpec.model_validate(_VALID_SPEC)


def test_frontend_dev_uses_screen_context_in_prompt():
    """FrontendDev includes screen name + components in the LLM prompt when spec is present."""
    llm = MagicMock()
    llm.system.return_value = json.dumps(_FRONTEND_FILE)

    agent = FrontendDevAgent(llm)
    state = _state(ui_design_spec=_ui_spec())
    agent.run(state)

    # Verify the prompt sent to LLM mentions the screen
    calls = llm.system.call_args_list
    # The first call is the plan phase for TICKET-001
    plan_call_kwargs = calls[0][0] if calls[0][0] else {}
    plan_call_user = calls[0][0][1] if len(calls[0][0]) > 1 else str(calls[0])
    assert "Task List" in plan_call_user or any("Task List" in str(c) for c in calls)


def test_frontend_dev_no_spec_still_works():
    """FrontendDev works normally when no ui_design_spec is in state."""
    llm = MagicMock()
    llm.system.return_value = json.dumps(_FRONTEND_FILE)

    agent = FrontendDevAgent(llm)
    state = _state()  # no ui_design_spec
    result = agent.run(state)

    assert len(result["code_artifacts"]) >= 1


def test_frontend_dev_spec_as_dict():
    """ui_design_spec can arrive as raw dict (post-serialization) and is coerced."""
    llm = MagicMock()
    llm.system.return_value = json.dumps(_FRONTEND_FILE)

    agent = FrontendDevAgent(llm)
    state = _state(ui_design_spec=_VALID_SPEC)  # raw dict, not UIDesignSpec instance
    result = agent.run(state)

    assert len(result["code_artifacts"]) >= 1


def test_frontend_dev_design_system_in_prompt():
    """Design system token appears in the plan/file system prompt."""
    llm = MagicMock()
    llm.system.return_value = json.dumps(_FRONTEND_FILE)

    agent = FrontendDevAgent(llm)
    state = _state(ui_design_spec=_ui_spec())
    agent.run(state)

    all_prompt_text = " ".join(str(c) for c in llm.system.call_args_list)
    assert "Tailwind+shadcn" in all_prompt_text
    assert "#6366f1" in all_prompt_text


# ── UIDesignSpec model tests ──────────────────────────────────────────────────

def test_screen_model():
    s = Screen(
        id="dashboard",
        name="Dashboard",
        description="Main dashboard",
        route="/dashboard",
        components=["StatsCard", "RecentTasks"],
        linked_tickets=["TICKET-001"],
    )
    assert s.id == "dashboard"
    assert "StatsCard" in s.components


def test_design_tokens_defaults():
    tok = DesignTokens(
        color_primary="#000",
        color_secondary="#111",
        color_background="#fff",
        color_text="#222",
        font_family="Inter",
    )
    assert tok.font_scale == {}


def test_ui_design_spec_validates_from_dict():
    spec = UIDesignSpec.model_validate(_VALID_SPEC)
    assert len(spec.screens) == 2
    assert spec.tokens.font_family == "Inter, system-ui, sans-serif"


# ---------------------------------------------------------------------------
# AN-P04 — UIDesignAgent: zero-screen spec is accepted without error
# ---------------------------------------------------------------------------

_ZERO_SCREEN_SPEC: dict = {
    "screens": [],
    "navigation_flow": [],
    "tokens": {
        "color_primary": "#000000",
        "color_secondary": "#ffffff",
        "color_background": "#f5f5f5",
        "color_text": "#111111",
        "font_family": "Inter",
    },
    "design_system": "Bare",
}


def test_ui_designer_zero_screens_returns_empty_spec():
    """UIDesignAgent must not crash when LLM returns a spec with zero screens."""
    agent = UIDesignAgent(_mock_llm(json.dumps(_ZERO_SCREEN_SPEC)))
    result = agent.run(_state())

    # Either a valid spec or an error — no uncaught exception
    assert isinstance(result, dict)
    if result.get("ui_design_spec"):
        spec = result["ui_design_spec"]
        if isinstance(spec, UIDesignSpec):
            assert spec.screens == []
        else:
            assert spec.get("screens") == []


def test_frontend_dev_zero_screen_spec_does_not_crash():
    """FrontendDev receiving an empty screens spec must not raise."""
    llm = _mock_llm(json.dumps(_FRONTEND_FILE))
    agent = FrontendDevAgent(llm)
    empty_spec = UIDesignSpec.model_validate(_ZERO_SCREEN_SPEC)
    state = _state(ui_design_spec=empty_spec)

    result = agent.run(state)
    assert isinstance(result, dict)
    # code_artifacts may be empty list or None, but not an unhandled exception
    arts = result.get("code_artifacts") or []
    assert isinstance(arts, list)
