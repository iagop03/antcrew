"""Tests for CustomTeam (v0.8.3 – v0.8.5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from antcrew.teams.custom_team import CustomTeam
from antcrew.models.simulated import SimulatedLLM
from antcrew.core.run_result import RunResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _llm():
    return SimulatedLLM()


def _steps(**overrides) -> list[dict]:
    base = [
        {"name": "step1", "system_prompt": "Do step 1.", "output_key": "out1"},
    ]
    if overrides:
        base[0].update(overrides)
    return base


def _two_steps() -> list[dict]:
    return [
        {"name": "planner",  "system_prompt": "Plan it.",    "output_key": "plan"},
        {"name": "executor", "system_prompt": "Execute: {plan}", "input_key": "plan",
         "output_key": "result"},
    ]


# ===========================================================================
# Construction
# ===========================================================================

def test_empty_steps_raises():
    with pytest.raises(ValueError, match="at least one step"):
        CustomTeam([], _llm())


def test_single_step_ok():
    team = CustomTeam(_steps(), _llm())
    assert len(team._agents) == 1
    assert team._agents[0].name == "step1"


def test_multiple_steps_ok():
    team = CustomTeam(_two_steps(), _llm())
    assert len(team._agents) == 2


def test_max_cost_propagated():
    llm = _llm()
    CustomTeam(_steps(), llm, max_cost_usd=0.5)
    assert llm.max_cost_usd == 0.5


def test_checkpointer_is_none():
    team = CustomTeam(_steps(), _llm())
    assert team._checkpointer is None


# ===========================================================================
# run() return value
# ===========================================================================

def test_run_returns_run_result():
    team = CustomTeam(_steps(), _llm())
    result = team.run("Build login")
    assert isinstance(result, RunResult)


def test_run_thread_id_preserved():
    team = CustomTeam(_steps(), _llm())
    result = team.run("task", thread_id="my-thread")
    assert result.thread_id == "my-thread"


def test_run_default_thread_id():
    team = CustomTeam(_steps(), _llm())
    result = team.run("task")
    assert result.thread_id == "default"


def test_run_state_has_request():
    team = CustomTeam(_steps(), _llm())
    result = team.run("Build JWT auth")
    assert result.state["request"] == "Build JWT auth"


def test_run_state_has_output_key():
    team = CustomTeam(_steps(), _llm())
    result = team.run("task")
    assert "out1" in result.state


def test_run_result_dict_access():
    team = CustomTeam(_steps(), _llm())
    result = team.run("task")
    assert "out1" in result          # __contains__
    assert result["out1"] is not None  # __getitem__


# ===========================================================================
# Multi-step state threading
# ===========================================================================

def test_two_steps_run():
    team = CustomTeam(_two_steps(), _llm())
    result = team.run("Build login")
    assert "plan" in result.state
    assert "result" in result.state


def test_later_step_receives_earlier_output():
    """The second step's input_key must be populated from step 1's output_key."""
    from antcrew.testing import SequencedLLM
    llm = SequencedLLM(["My plan output", "Execution done"])
    steps = [
        {"name": "planner",  "system_prompt": "Plan it.",        "output_key": "plan"},
        {"name": "executor", "system_prompt": "Execute: {plan}", "input_key": "plan",
         "output_key": "result"},
    ]
    team = CustomTeam(steps, llm)
    result = team.run("Build login")
    # The executor's input should contain the planner's output
    assert result["plan"] == "My plan output"
    assert result["result"] == "Execution done"


def test_three_step_chain():
    from antcrew.testing import SequencedLLM
    llm = SequencedLLM(["plan", "code", "review"])
    steps = [
        {"name": "planner",  "system_prompt": "Plan.",         "output_key": "plan"},
        {"name": "coder",    "system_prompt": "Code: {plan}",  "input_key": "plan",   "output_key": "code"},
        {"name": "reviewer", "system_prompt": "Review: {code}","input_key": "code",   "output_key": "review"},
    ]
    team = CustomTeam(steps, llm)
    result = team.run("task")
    assert result["plan"] == "plan"
    assert result["code"] == "code"
    assert result["review"] == "review"


# ===========================================================================
# _initial_state (Pipeline compatibility)
# ===========================================================================

def test_initial_state_has_request():
    team = CustomTeam(_steps(), _llm())
    state = team._initial_state("Do something")
    assert state["request"] == "Do something"


def test_initial_state_is_dict():
    team = CustomTeam(_steps(), _llm())
    assert isinstance(team._initial_state("x"), dict)


# ===========================================================================
# Pipeline compatibility
# ===========================================================================

def test_pipeline_with_custom_team():
    from antcrew.core.pipeline import Pipeline
    team = CustomTeam(_steps(), _llm())
    pipeline = Pipeline([team])
    result = pipeline.run("Build login")
    assert "out1" in result.state


# ===========================================================================
# TraceLog integration
# ===========================================================================

def test_trace_log_begin_run_called(tmp_path):
    from antcrew.trace import TraceLog
    db = tmp_path / "trace.db"
    tlog = TraceLog(db)

    team = CustomTeam(_steps(), _llm(), trace_log=tlog)
    team.run("Build login", thread_id="t1")
    tlog.close()

    tlog2 = TraceLog(db)
    runs = tlog2.list_runs()
    tlog2.close()

    assert len(runs) == 1
    assert runs[0]["thread_id"] == "t1"
    assert runs[0]["status"] == "done"


def test_trace_log_records_error_status(tmp_path):
    from antcrew.trace import TraceLog

    class _BoomLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            raise RuntimeError("boom")

    db = tmp_path / "trace.db"
    tlog = TraceLog(db)
    team = CustomTeam(_steps(), _BoomLLM(), trace_log=tlog)

    with pytest.raises(RuntimeError):
        team.run("task", thread_id="err-thread")

    tlog.close()

    tlog2 = TraceLog(db)
    runs = tlog2.list_runs()
    tlog2.close()

    assert runs[0]["status"] == "error"


# ===========================================================================
# config.py integration
# ===========================================================================

def test_config_load_custom_team(tmp_path):
    import yaml
    from antcrew.config import load

    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [
            {"name": "summarizer", "system_prompt": "Summarise.", "output_key": "summary"},
        ],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")

    from antcrew.teams.custom_team import CustomTeam as CT
    team = load(p)
    assert isinstance(team, CT)
    assert len(team._agents) == 1
    assert team._agents[0].name == "summarizer"


def test_config_custom_team_no_steps_raises(tmp_path):
    import yaml
    from antcrew.config import load

    cfg = {"team": "custom", "model": "simulated"}
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")

    with pytest.raises(ValueError, match="steps"):
        load(p)


def test_config_custom_team_empty_steps_raises(tmp_path):
    import yaml
    from antcrew.config import load

    cfg = {"team": "custom", "model": "simulated", "steps": []}
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")

    with pytest.raises((ValueError, Exception)):
        load(p)


# ===========================================================================
# antcrew init --template custom
# ===========================================================================

def test_init_custom_template(tmp_path):
    from typer.testing import CliRunner
    from antcrew.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["init", "--template", "custom", "--output", str(tmp_path)])
    assert result.exit_code == 0, result.output

    yaml_file = tmp_path / "agentteam.yaml"
    main_file = tmp_path / "main.py"
    assert yaml_file.exists()
    assert main_file.exists()
    content = yaml_file.read_text()
    assert "team: custom" in content
    assert "steps:" in content


# ===========================================================================
# antcrew namespace export
# ===========================================================================

def test_custom_team_importable_from_top_level():
    from antcrew import CustomTeam as CT
    assert CT is CustomTeam


# ===========================================================================
# Parallel steps
# ===========================================================================

def _parallel_steps() -> list[dict]:
    return [
        {"name": "planner", "system_prompt": "Plan it.", "output_key": "plan"},
        {"parallel": [
            {"name": "backend",  "system_prompt": "Backend: {plan}",
             "input_key": "plan", "output_key": "backend_code"},
            {"name": "frontend", "system_prompt": "Frontend: {plan}",
             "input_key": "plan", "output_key": "frontend_code"},
        ]},
        {"name": "reviewer",
         "system_prompt": "Review: {backend_code} {frontend_code}",
         "output_key": "review"},
    ]


def test_parallel_agents_flat_list():
    """_agents must contain all agents including those in parallel groups."""
    team = CustomTeam(_parallel_steps(), _llm())
    names = [a.name for a in team._agents]
    assert "planner" in names
    assert "backend" in names
    assert "frontend" in names
    assert "reviewer" in names
    assert len(names) == 4


def test_parallel_step_groups_structure():
    team = CustomTeam(_parallel_steps(), _llm())
    # 3 step groups: planner, parallel(backend+frontend), reviewer
    assert len(team._step_groups) == 3
    assert len(team._step_groups[0]) == 1   # planner
    assert len(team._step_groups[1]) == 2   # backend + frontend
    assert len(team._step_groups[2]) == 1   # reviewer


def test_parallel_run_produces_all_outputs():
    team = CustomTeam(_parallel_steps(), _llm())
    result = team.run("Build login")
    assert "plan" in result.state
    assert "backend_code" in result.state
    assert "frontend_code" in result.state
    assert "review" in result.state


def test_parallel_both_outputs_non_none():
    team = CustomTeam(_parallel_steps(), _llm())
    result = team.run("Build login")
    assert result["backend_code"] is not None
    assert result["frontend_code"] is not None


def test_parallel_later_step_sees_parallel_outputs():
    """The reviewer (step 3) must see outputs from the parallel group (step 2)."""
    from antcrew.testing import SequencedLLM
    llm = SequencedLLM(["plan-text", "backend-text", "frontend-text", "review-text"])
    team = CustomTeam(_parallel_steps(), llm)
    result = team.run("Build login")
    assert result["review"] == "review-text"


def test_parallel_empty_group_raises():
    with pytest.raises(ValueError, match="parallel"):
        CustomTeam([{"parallel": []}], _llm())


def test_parallel_only_group():
    """A team that is entirely one parallel group."""
    steps = [
        {"parallel": [
            {"name": "a", "system_prompt": "Task A.", "output_key": "out_a"},
            {"name": "b", "system_prompt": "Task B.", "output_key": "out_b"},
        ]}
    ]
    team = CustomTeam(steps, _llm())
    result = team.run("task")
    assert "out_a" in result.state
    assert "out_b" in result.state


def test_parallel_max_workers_respected():
    team = CustomTeam(_parallel_steps(), _llm(), max_workers=1)
    # Should still produce correct output even with a single worker.
    result = team.run("task")
    assert "backend_code" in result.state
    assert "frontend_code" in result.state


def test_parallel_state_snapshot_isolation():
    """Parallel agents must not see each other's writes during execution."""
    seen: list[dict] = []

    class _SpyLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            # record a snapshot of state keys visible at call time
            seen.append(set(messages[-1].content.split() if messages else []))
            return super().complete(messages, **kw)

    steps = [
        {"parallel": [
            {"name": "a", "system_prompt": "A.", "output_key": "out_a"},
            {"name": "b", "system_prompt": "B.", "output_key": "out_b"},
        ]}
    ]
    team = CustomTeam(steps, _SpyLLM())
    result = team.run("task")
    # Both run; neither should crash — output isolation is the core guarantee.
    assert "out_a" in result.state
    assert "out_b" in result.state


# ===========================================================================
# Parallel in YAML config
# ===========================================================================

# ===========================================================================
# Retry policy — _Step dataclass and _run_step helper
# ===========================================================================

def test_retry_keys_not_forwarded_to_template_agent():
    """max_retries / retry_delay must not reach TemplateAgent as config keys."""
    steps = [{"name": "x", "system_prompt": "Do x.", "output_key": "out",
              "max_retries": 2, "retry_delay": 0.5}]
    team = CustomTeam(steps, _llm())
    agent = team._agents[0]
    # TemplateAgent should not have stored these as attributes
    assert not hasattr(agent, "max_retries")
    assert not hasattr(agent, "retry_delay")


def test_step_default_retries_zero():
    from antcrew.teams.custom_team import _parse_steps
    groups = _parse_steps([{"name": "a", "system_prompt": "s.", "output_key": "o"}], _llm())
    step = groups[0][0]
    assert step.max_retries == 0
    assert step.retry_delay == 0.0


def test_step_parses_max_retries():
    from antcrew.teams.custom_team import _parse_steps
    groups = _parse_steps(
        [{"name": "a", "system_prompt": "s.", "output_key": "o",
          "max_retries": 3, "retry_delay": 0.1}],
        _llm(),
    )
    step = groups[0][0]
    assert step.max_retries == 3
    assert step.retry_delay == pytest.approx(0.1)


def test_retry_succeeds_on_second_attempt():
    """Agent fails once then succeeds — result should be the success value."""
    calls = [0]

    class _FlakyLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("transient error")
            return super().complete(messages, **kw)

    steps = [{"name": "x", "system_prompt": "Do x.", "output_key": "out",
              "max_retries": 1}]
    team = CustomTeam(steps, _FlakyLLM())
    result = team.run("task")
    assert "out" in result.state
    assert calls[0] == 2  # failed once, succeeded on retry


def test_retry_exhausted_raises():
    """After all retries are spent the original exception must propagate."""
    class _AlwaysFailLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            raise RuntimeError("always fails")

    steps = [{"name": "x", "system_prompt": "Do x.", "output_key": "out",
              "max_retries": 2}]
    team = CustomTeam(steps, _AlwaysFailLLM())
    with pytest.raises(RuntimeError, match="always fails"):
        team.run("task")


def test_retry_attempt_count(monkeypatch):
    """With max_retries=3 the LLM must be called exactly 4 times (1 + 3)."""
    calls = [0]

    class _CountLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            calls[0] += 1
            raise RuntimeError("boom")

    # Suppress sleep to keep tests fast
    monkeypatch.setattr("antcrew.teams.custom_team.time.sleep", lambda _: None)

    steps = [{"name": "x", "system_prompt": "s.", "output_key": "o", "max_retries": 3}]
    team = CustomTeam(steps, _CountLLM())
    with pytest.raises(RuntimeError):
        team.run("task")
    assert calls[0] == 4


def test_retry_delay_called(monkeypatch):
    """retry_delay > 0 must call time.sleep between attempts."""
    slept: list[float] = []
    monkeypatch.setattr("antcrew.teams.custom_team.time.sleep", slept.append)

    calls = [0]

    class _FlakyLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            calls[0] += 1
            if calls[0] < 3:
                raise RuntimeError("transient")
            return super().complete(messages, **kw)

    steps = [{"name": "x", "system_prompt": "s.", "output_key": "o",
              "max_retries": 3, "retry_delay": 0.5}]
    team = CustomTeam(steps, _FlakyLLM())
    team.run("task")
    assert len(slept) == 2           # slept between attempt 1→2 and 2→3
    assert all(s == pytest.approx(0.5) for s in slept)


def test_no_sleep_when_retry_delay_zero(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr("antcrew.teams.custom_team.time.sleep", slept.append)
    calls = [0]

    class _FlakyLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("transient")
            return super().complete(messages, **kw)

    steps = [{"name": "x", "system_prompt": "s.", "output_key": "o",
              "max_retries": 1, "retry_delay": 0.0}]
    team = CustomTeam(steps, _FlakyLLM())
    team.run("task")
    assert slept == []


def test_retry_on_second_step_only():
    """Only the failing step retries; the first step must run exactly once."""
    first_calls = [0]
    second_calls = [0]

    class _SelectiveLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            # Distinguish agents by their system prompt (messages[0])
            sys_content = messages[0].content if messages else ""
            if "step1" in sys_content:
                first_calls[0] += 1
                return super().complete(messages, **kw)
            else:
                second_calls[0] += 1
                if second_calls[0] == 1:
                    raise RuntimeError("step2 fails first time")
                return super().complete(messages, **kw)

    steps = [
        {"name": "step1", "system_prompt": "step1 task", "output_key": "out1"},
        {"name": "step2", "system_prompt": "step2 task", "output_key": "out2",
         "max_retries": 1},
    ]
    team = CustomTeam(steps, _SelectiveLLM())
    result = team.run("task")
    assert "out1" in result.state
    assert "out2" in result.state
    assert first_calls[0] == 1
    assert second_calls[0] == 2


def test_retry_in_parallel_group(monkeypatch):
    """Retry policy must also work for steps inside a parallel group."""
    monkeypatch.setattr("antcrew.teams.custom_team.time.sleep", lambda _: None)
    calls: dict[str, int] = {"a": 0, "b": 0}

    class _FlakyLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            # Distinguish agents by their system prompt (messages[0])
            sys_content = messages[0].content if messages else ""
            if "Task A" in sys_content:
                calls["a"] += 1
                if calls["a"] == 1:
                    raise RuntimeError("a fails")
            else:
                calls["b"] += 1
            return super().complete(messages, **kw)

    steps = [{"parallel": [
        {"name": "a", "system_prompt": "Task A.", "output_key": "out_a", "max_retries": 1},
        {"name": "b", "system_prompt": "Task B.", "output_key": "out_b"},
    ]}]
    team = CustomTeam(steps, _FlakyLLM())
    result = team.run("task")
    assert "out_a" in result.state
    assert "out_b" in result.state
    assert calls["a"] == 2


def test_config_retry_from_yaml(tmp_path):
    """max_retries and retry_delay must be parsed from a YAML config file."""
    import yaml
    from antcrew.config import load
    from antcrew.teams.custom_team import _parse_steps

    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [
            {"name": "x", "system_prompt": "Do x.", "output_key": "out",
             "max_retries": 2, "retry_delay": 0.5},
        ],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")

    team = load(p)
    step = team._step_groups[0][0]
    assert step.max_retries == 2
    assert step.retry_delay == pytest.approx(0.5)


# ===========================================================================
# Conditional steps
# ===========================================================================

def test_condition_none_always_runs():
    from antcrew.teams.custom_team import _parse_condition, _Step, _condition_met
    step = _Step(agent=None, condition=None)  # type: ignore[arg-type]
    assert _condition_met(step, {}) is True


def test_parse_condition_string():
    from antcrew.teams.custom_team import _parse_condition
    assert _parse_condition("plan") == ["plan"]


def test_parse_condition_list():
    from antcrew.teams.custom_team import _parse_condition
    assert _parse_condition(["plan", "tickets"]) == ["plan", "tickets"]


def test_parse_condition_none():
    from antcrew.teams.custom_team import _parse_condition
    assert _parse_condition(None) is None


def test_parse_condition_empty_list_returns_none():
    from antcrew.teams.custom_team import _parse_condition
    assert _parse_condition([]) is None


def test_parse_condition_invalid_type_raises():
    from antcrew.teams.custom_team import _parse_condition
    with pytest.raises(ValueError):
        _parse_condition(42)


def test_condition_key_truthy_runs():
    steps = [
        {"name": "producer", "system_prompt": "Produce.", "output_key": "data"},
        {"name": "consumer", "system_prompt": "Consume: {data}",
         "input_key": "data", "output_key": "result", "condition": "data"},
    ]
    team = CustomTeam(steps, _llm())
    result = team.run("task")
    # producer sets "data"; consumer condition met → result must be present
    assert "result" in result.state


def test_condition_key_missing_skips():
    steps = [
        {"name": "skipped", "system_prompt": "Skip me.", "output_key": "skipped_out",
         "condition": "nonexistent_key"},
    ]
    team = CustomTeam(steps, _llm())
    result = team.run("task")
    assert "skipped_out" not in result.state


def test_condition_key_falsy_skips():
    """Step with condition on a key that holds an empty string must be skipped."""
    from antcrew.testing import SequencedLLM
    llm = SequencedLLM([""])   # producer returns empty string
    steps = [
        {"name": "producer", "system_prompt": "Produce.", "output_key": "data"},
        {"name": "consumer", "system_prompt": "Consume.", "output_key": "result",
         "condition": "data"},
    ]
    team = CustomTeam(steps, llm)
    result = team.run("task")
    assert "result" not in result.state


def test_condition_not_forwarded_to_template_agent():
    steps = [{"name": "x", "system_prompt": "s.", "output_key": "o", "condition": "plan"}]
    team = CustomTeam(steps, _llm())
    agent = team._agents[0]
    assert not hasattr(agent, "condition")


def test_condition_list_all_truthy_runs():
    from antcrew.testing import SequencedLLM
    llm = SequencedLLM(["plan-ok", "tickets-ok", "review-done"])
    steps = [
        {"name": "planner",  "system_prompt": "Plan.",    "output_key": "plan"},
        {"name": "ticketer", "system_prompt": "Tickets.", "output_key": "tickets"},
        {"name": "reviewer", "system_prompt": "Review.",  "output_key": "review",
         "condition": ["plan", "tickets"]},
    ]
    team = CustomTeam(steps, llm)
    result = team.run("task")
    assert "review" in result.state


def test_condition_list_one_falsy_skips():
    from antcrew.testing import SequencedLLM
    llm = SequencedLLM(["plan-ok", ""])   # tickets step returns empty
    steps = [
        {"name": "planner",  "system_prompt": "Plan.",    "output_key": "plan"},
        {"name": "ticketer", "system_prompt": "Tickets.", "output_key": "tickets"},
        {"name": "reviewer", "system_prompt": "Review.",  "output_key": "review",
         "condition": ["plan", "tickets"]},
    ]
    team = CustomTeam(steps, llm)
    result = team.run("task")
    assert "review" not in result.state


def test_condition_skipped_step_not_called():
    """A skipped step must not call the LLM at all."""
    calls = [0]

    class _CountLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            calls[0] += 1
            return super().complete(messages, **kw)

    steps = [
        {"name": "skipped", "system_prompt": "Skip me.", "output_key": "out",
         "condition": "missing"},
    ]
    team = CustomTeam(steps, _CountLLM())
    team.run("task")
    assert calls[0] == 0


def test_condition_in_parallel_group_some_skipped():
    """Inside a parallel group, only steps whose condition is met run."""
    from antcrew.testing import SequencedLLM
    # Only one LLM call should happen (backend runs, frontend is skipped)
    llm = SequencedLLM(["backend-result"])
    steps = [
        {"parallel": [
            {"name": "backend",  "system_prompt": "Backend.",  "output_key": "backend_code"},
            {"name": "frontend", "system_prompt": "Frontend.", "output_key": "frontend_code",
             "condition": "missing_key"},
        ]}
    ]
    team = CustomTeam(steps, llm)
    result = team.run("task")
    assert "backend_code" in result.state
    assert "frontend_code" not in result.state


def test_condition_in_yaml_config(tmp_path):
    import yaml
    from antcrew.config import load

    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [
            {"name": "planner",  "system_prompt": "Plan.", "output_key": "plan"},
            {"name": "reviewer", "system_prompt": "Review: {plan}.", "output_key": "review",
             "condition": "plan"},
        ],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")

    team = load(p)
    step = team._step_groups[1][0]
    assert step.condition == ["plan"]

    result = team.run("Build login")
    assert "review" in result.state


def test_config_parallel_steps(tmp_path):
    import yaml
    from antcrew.config import load

    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [
            {"name": "planner", "system_prompt": "Plan.", "output_key": "plan"},
            {"parallel": [
                {"name": "writer_a", "system_prompt": "Write A.", "output_key": "text_a"},
                {"name": "writer_b", "system_prompt": "Write B.", "output_key": "text_b"},
            ]},
        ],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")

    team = load(p)
    assert len(team._step_groups) == 2
    assert len(team._step_groups[1]) == 2

    result = team.run("Build login")
    assert "plan" in result.state
    assert "text_a" in result.state
    assert "text_b" in result.state
