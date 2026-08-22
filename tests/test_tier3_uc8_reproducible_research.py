"""Tests for UC8: ReproducibleResearchPipeline + ExperimentRecord."""
from __future__ import annotations

import pytest
from antcrew.models.simulated import SimulatedLLM
from antcrew.teams.reproducible_research import ExperimentRecord, ReproducibleResearchPipeline
from antcrew.trace import TraceLog


def _sim() -> SimulatedLLM:
    return SimulatedLLM()


# ---------------------------------------------------------------------------
# ExperimentRecord
# ---------------------------------------------------------------------------

def test_experiment_record_fields():
    rec = ExperimentRecord(
        experiment_id="abc123:run-uuid",
        run_id="run-uuid",
        team_hash="abc123",
        request="What is AI safety?",
        cost_usd=0.042,
        state={"content_piece": None},
    )
    assert rec.experiment_id == "abc123:run-uuid"
    assert rec.run_id == "run-uuid"
    assert rec.team_hash == "abc123"
    assert rec.request == "What is AI safety?"
    assert rec.cost_usd == 0.042


def test_experiment_record_is_frozen():
    rec = ExperimentRecord(
        experiment_id="h:r",
        run_id="r",
        team_hash="h",
        request="q",
        cost_usd=0.0,
        state={},
    )
    with pytest.raises((AttributeError, TypeError)):
        rec.experiment_id = "new_id"  # type: ignore[misc]


def test_experiment_id_format():
    """experiment_id must be <team_hash>:<run_id> — colon-separated."""
    rec = ExperimentRecord(
        experiment_id="deadbeef:550e8400-e29b-41d4-a716-446655440000",
        run_id="550e8400-e29b-41d4-a716-446655440000",
        team_hash="deadbeef",
        request="test",
        cost_usd=0.0,
        state={},
    )
    parts = rec.experiment_id.split(":", 1)
    assert len(parts) == 2
    assert parts[0] == rec.team_hash
    assert parts[1] == rec.run_id


def test_experiment_record_state_dict():
    state = {"_run_id": "r1", "research_document": None, "errors": []}
    rec = ExperimentRecord(
        experiment_id="h:r1",
        run_id="r1",
        team_hash="h",
        request="query",
        cost_usd=0.01,
        state=state,
    )
    assert rec.state["_run_id"] == "r1"
    assert rec.state["errors"] == []


def test_experiment_record_zero_cost():
    rec = ExperimentRecord(
        experiment_id="h:r",
        run_id="r",
        team_hash="h",
        request="q",
        cost_usd=0.0,
        state={},
    )
    assert rec.cost_usd == 0.0


# ---------------------------------------------------------------------------
# ReproducibleResearchPipeline initialisation
# ---------------------------------------------------------------------------

def test_pipeline_creates_tracelog_automatically(tmp_path):
    db = str(tmp_path / "exp.db")
    pipeline = ReproducibleResearchPipeline(model=_sim(), db_path=db)
    assert pipeline._trace_log.full_trace is True


def test_pipeline_accepts_full_trace_tracelog(tmp_path):
    tlog = TraceLog(str(tmp_path / "t.db"), full_trace=True)
    pipeline = ReproducibleResearchPipeline(model=_sim(), trace_log=tlog)
    assert pipeline._trace_log is tlog


def test_pipeline_rejects_partial_trace_tracelog(tmp_path):
    tlog = TraceLog(str(tmp_path / "t.db"), full_trace=False)
    with pytest.raises(ValueError, match="full_trace=True"):
        ReproducibleResearchPipeline(model=_sim(), trace_log=tlog)


def test_pipeline_team_hash_is_deterministic(tmp_path):
    """Two pipelines with default agents must produce the same team_hash."""
    p1 = ReproducibleResearchPipeline(model=_sim(), db_path=str(tmp_path / "a.db"))
    p2 = ReproducibleResearchPipeline(model=_sim(), db_path=str(tmp_path / "b.db"))
    assert p1.team_hash == p2.team_hash


def test_pipeline_team_hash_is_hex_string(tmp_path):
    pipeline = ReproducibleResearchPipeline(model=_sim(), db_path=str(tmp_path / "exp.db"))
    th = pipeline.team_hash
    assert isinstance(th, str)
    assert len(th) > 0


def test_pipeline_replay_splits_experiment_id(tmp_path, monkeypatch):
    """replay_experiment must pass the run_id half to TraceLog.replay_all."""
    called_with = {}

    def fake_replay_all(run_id, llm):
        called_with["run_id"] = run_id
        return []

    tlog = TraceLog(str(tmp_path / "t.db"), full_trace=True)
    pipeline = ReproducibleResearchPipeline(model=_sim(), trace_log=tlog)
    monkeypatch.setattr(pipeline._trace_log, "replay_all", fake_replay_all)

    pipeline.replay_experiment("deadbeef:my-run-id-123")
    assert called_with["run_id"] == "my-run-id-123"


def test_pipeline_replay_experiment_id_with_uuid(tmp_path, monkeypatch):
    """Colon in UUID should not confuse split(sep, 1) maxsplit."""
    seen = {}

    def fake_replay_all(run_id, llm):
        seen["run_id"] = run_id
        return []

    tlog = TraceLog(str(tmp_path / "t.db"), full_trace=True)
    pipeline = ReproducibleResearchPipeline(model=_sim(), trace_log=tlog)
    monkeypatch.setattr(pipeline._trace_log, "replay_all", fake_replay_all)

    exp_id = "sha256hash:550e8400-e29b-41d4"
    pipeline.replay_experiment(exp_id)
    assert seen["run_id"] == "550e8400-e29b-41d4"


def test_pipeline_has_research_team(tmp_path):
    from antcrew.teams.research_team import ResearchTeam
    pipeline = ReproducibleResearchPipeline(model=_sim(), db_path=str(tmp_path / "t.db"))
    assert isinstance(pipeline._team, ResearchTeam)


def test_pipeline_team_uses_same_tracelog(tmp_path):
    tlog = TraceLog(str(tmp_path / "t.db"), full_trace=True)
    pipeline = ReproducibleResearchPipeline(model=_sim(), trace_log=tlog)
    assert pipeline._team._trace_log is tlog
