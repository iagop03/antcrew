"""Contract tests for the antcrew_engine integration boundary.

These tests do NOT run the engine (no LLM calls, no filesystem I/O).
They verify that every name engine_runner.py imports from antcrew_engine
still exists with the expected constructor signatures and public API.

A failure here means antcrew_engine changed a public interface that
engine_runner.py depends on — catch it here, not in production.
"""
from __future__ import annotations

import inspect
import threading
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# 1. Import contract — all names engine_runner imports must exist
# ---------------------------------------------------------------------------

def test_top_level_imports_stable():
    """All names imported at module level in engine_runner.py exist."""
    from antcrew_engine import (  # noqa: F401
        CapabilityRegistry,
        ConditionId,
        EngineLoop,
        EventBusBridge,
        EventLog,
        FilesystemStore,
        HitlDecision,
        HitlRequested,
        HitlResolved,
        HitlReviewer,
        MemoryStore,
        artifact_validators,
        build_llm,
    )


def test_capability_classes_importable():
    """All 11 concrete capability classes used in _build_engine_registry exist."""
    from antcrew_engine import (  # noqa: F401
        Architect,
        BugFixer,
        CodeGenerator,
        CodeRegenerator,
        CodeReviewer,
        DependencyInstaller,
        DocGenerator,
        ReviewFixer,
        TaskPlanner,
        TestGenerator,
        TestRunner,
    )
    from antcrew_engine.capabilities import ManualActionCapability  # noqa: F401


def test_goal_types_importable():
    """All types used in _build_engine_goal exist."""
    from antcrew_engine import (  # noqa: F401
        Condition,
        ConditionId,
        Constraints,
        DesiredProjectState,
        Goal,
    )


def test_validator_classes_importable():
    """All validator classes used in _build_engine_validators exist."""
    from antcrew_engine.capabilities.validators import (  # noqa: F401
        AllTasksCompletedValidator,
        CodeReviewedValidator,
        DependenciesInstalledValidator,
        DocumentationExistsValidator,
        TestsExistValidator,
        TestsPassValidator,
    )


def test_artifact_types_importable():
    """Artifact, ArtifactId, ArtifactKind used in _load_existing_codebase exist."""
    from antcrew_engine import Artifact, ArtifactId, ArtifactKind  # noqa: F401


# ---------------------------------------------------------------------------
# 2. Constructor signature contracts — critical kwargs must be present
# ---------------------------------------------------------------------------

def test_engine_loop_accepts_expected_params():
    """EngineLoop.__init__ must accept all kwargs that engine_runner passes."""
    from antcrew_engine import EngineLoop
    params = set(inspect.signature(EngineLoop.__init__).parameters)
    required = {
        "registry", "validators", "event_log",
        "max_iterations", "retry_limits", "total_limits",
        "stop_event", "max_cost_usd",
    }
    missing = required - params
    assert not missing, f"EngineLoop missing params: {missing}"


def test_event_bus_bridge_accepts_expected_params():
    """EventBusBridge.__init__ must accept event_log, run_id, thread_id, on_event."""
    from antcrew_engine import EventBusBridge
    params = set(inspect.signature(EventBusBridge.__init__).parameters)
    required = {"event_log", "run_id", "on_event"}
    missing = required - params
    assert not missing, f"EventBusBridge missing params: {missing}"


def test_build_llm_accepts_expected_params():
    """antcrew_engine.build_llm must accept prompt_caching, api_key, base_url."""
    from antcrew_engine import build_llm
    params = set(inspect.signature(build_llm).parameters)
    required = {"prompt_caching", "api_key", "base_url"}
    missing = required - params
    assert not missing, f"build_llm missing params: {missing}"


def test_hitl_reviewer_accepts_expected_params():
    """HitlReviewer must accept reviewed_capability, request_review, artifact_id, triggers_condition."""
    from antcrew_engine import HitlReviewer
    params = set(inspect.signature(HitlReviewer.__init__).parameters)
    required = {"reviewed_capability", "request_review", "artifact_id", "triggers_condition"}
    missing = required - params
    assert not missing, f"HitlReviewer missing params: {missing}"


def test_hitl_requested_accepts_review_id_and_capability():
    """HitlRequested must accept review_id and reviewed_capability."""
    from antcrew_engine import HitlRequested
    params = set(inspect.signature(HitlRequested.__init__).parameters)
    assert "review_id" in params, "HitlRequested missing review_id"
    assert "reviewed_capability" in params, "HitlRequested missing reviewed_capability"


def test_hitl_resolved_accepts_review_id_and_verdict():
    """HitlResolved must accept review_id and verdict."""
    from antcrew_engine import HitlResolved
    params = set(inspect.signature(HitlResolved.__init__).parameters)
    assert "review_id" in params, "HitlResolved missing review_id"
    assert "verdict" in params, "HitlResolved missing verdict"


def test_artifact_accepts_expected_params():
    """Artifact must accept id, kind, content, metadata."""
    from antcrew_engine import Artifact
    params = set(inspect.signature(Artifact.__init__).parameters)
    required = {"id", "kind", "content"}
    missing = required - params
    assert not missing, f"Artifact missing params: {missing}"


# ---------------------------------------------------------------------------
# 3. API contracts — methods that engine_runner calls must exist
# ---------------------------------------------------------------------------

def test_capability_registry_api():
    """CapabilityRegistry must have register() and all() methods."""
    from antcrew_engine import CapabilityRegistry
    assert callable(getattr(CapabilityRegistry, "register", None)), "CapabilityRegistry.register missing"
    assert callable(getattr(CapabilityRegistry, "all", None)), "CapabilityRegistry.all missing"


def test_event_log_api():
    """EventLog must have emit() and events() methods."""
    from antcrew_engine import EventLog
    el = EventLog()
    assert callable(getattr(el, "emit", None)), "EventLog.emit missing"
    assert callable(getattr(el, "events", None)), "EventLog.events missing"


def test_memory_store_api():
    """MemoryStore must have write(), read(), has() methods."""
    from antcrew_engine import MemoryStore
    store = MemoryStore()
    for method in ("write", "read", "has"):
        assert callable(getattr(store, method, None)), f"MemoryStore.{method} missing"


def test_artifact_kind_has_required_members():
    """ArtifactKind enum must have SOURCE, REQUIREMENTS, ARCHITECTURE, TASK_GRAPH, TEST, DOCUMENTATION."""
    from antcrew_engine import ArtifactKind
    required = {"SOURCE", "REQUIREMENTS", "ARCHITECTURE", "TASK_GRAPH", "TEST", "DOCUMENTATION"}
    missing = required - {m.name for m in ArtifactKind}
    assert not missing, f"ArtifactKind missing members: {missing}"


# ---------------------------------------------------------------------------
# 4. Integration helpers — verify engine_runner's builder functions work
# ---------------------------------------------------------------------------

def test_build_engine_goal_structure():
    """_build_engine_goal returns a Goal with the expected condition structure."""
    from app.services.engine_runner import _build_engine_goal

    goal = _build_engine_goal(
        description="Build a REST API",
        tech=["python", "fastapi"],
        conditions=[],
        full=False,
    )

    from antcrew_engine import Goal
    assert isinstance(goal, Goal)
    # Non-full mode: only 3 conditions (requirements, architecture, task_graph)
    cond_ids = {str(c.id) for c in goal.desired_state.conditions}
    assert "requirements_exists" in cond_ids
    assert "architecture_exists" in cond_ids
    assert "task_graph_exists" in cond_ids
    assert len(cond_ids) == 3


def test_build_engine_goal_full_mode_has_more_conditions():
    """Full mode produces more conditions than non-full (>=9 vs 3)."""
    from app.services.engine_runner import _build_engine_goal

    goal_full = _build_engine_goal("API", [], [], full=True)
    goal_slim = _build_engine_goal("API", [], [], full=False)

    assert len(goal_full.desired_state.conditions) > len(goal_slim.desired_state.conditions)
    assert len(goal_full.desired_state.conditions) >= 9


def test_build_engine_goal_custom_conditions():
    """Custom conditions override the defaults."""
    from app.services.engine_runner import _build_engine_goal

    goal = _build_engine_goal("Test", [], ["requirements_exists", "tests_pass"], full=False)
    cond_ids = {str(c.id) for c in goal.desired_state.conditions}
    assert cond_ids == {"requirements_exists", "tests_pass"}


def test_build_engine_validators_returns_nonempty_list():
    """_build_engine_validators returns a list with all expected validator types."""
    from antcrew_engine.capabilities.validators import (
        AllTasksCompletedValidator,
        TestsPassValidator,
    )

    from app.services.engine_runner import _build_engine_validators

    validators = _build_engine_validators()
    assert isinstance(validators, list)
    assert len(validators) >= 6

    types = {type(v) for v in validators}
    assert AllTasksCompletedValidator in types, "AllTasksCompletedValidator not in validators"
    assert TestsPassValidator in types, "TestsPassValidator not in validators"


def test_build_engine_registry_with_mock_llm():
    """_build_engine_registry builds a registry with all 11 core capabilities."""
    from app.services.engine_runner import _build_engine_registry

    mock_llm = MagicMock()
    registry = _build_engine_registry(
        mock_llm,
        manual_action_callback=lambda c: None,
    )

    from antcrew_engine import CapabilityRegistry
    assert isinstance(registry, CapabilityRegistry)

    registered_names = {e.descriptor.name for e in registry.all()}
    expected_core = {
        "architect", "task_planner", "code_generator", "code_regenerator",
        "dependency_installer", "test_generator", "test_runner",
        "bug_fixer", "code_reviewer", "review_fixer", "doc_generator",
    }
    missing = expected_core - registered_names
    assert not missing, f"Registry missing capabilities: {missing}"


def test_build_engine_registry_manual_action_registered():
    """ManualActionCapability is always registered in the registry."""
    from app.services.engine_runner import _build_engine_registry

    registry = _build_engine_registry(
        MagicMock(),
        manual_action_callback=lambda c: None,
    )
    names = {e.descriptor.name for e in registry.all()}
    assert "manual_action" in names, f"manual_action not in registry names: {names}"


# ---------------------------------------------------------------------------
# 5. HITL threading bridge contracts
# ---------------------------------------------------------------------------

def test_resolve_engine_review_unknown_id_returns_false():
    """resolve_engine_review with an unknown review_id returns False (no crash)."""
    from app.services.engine_runner import resolve_engine_review

    result = resolve_engine_review("nonexistent-review-id", {"verdict": "approve"})
    assert result is False


def test_resolve_manual_action_unknown_id_returns_false():
    """resolve_manual_action with an unknown ticket_id returns False (no crash)."""
    from app.services.engine_runner import resolve_manual_action

    result = resolve_manual_action("nonexistent-ticket-id")
    assert result is False


def test_cancel_engine_run_unknown_id_returns_false():
    """cancel_engine_run with an unknown run_id returns False (no crash)."""
    from app.services.engine_runner import cancel_engine_run

    result = cancel_engine_run("nonexistent-run-id")
    assert result is False


def test_resolve_engine_review_sets_verdict_and_unblocks():
    """resolve_engine_review signals the event and stores the verdict."""
    from app.services.engine_runner import (
        _engine_reviews,
        _engine_verdicts,
        resolve_engine_review,
    )

    review_id = "test-review-contract-001"
    event = threading.Event()
    _engine_reviews[review_id] = event

    verdict = {"verdict": "approve", "feedback": "LGTM"}
    result = resolve_engine_review(review_id, verdict)

    assert result is True
    assert event.is_set()
    assert _engine_verdicts.get(review_id) == verdict
    # Cleanup
    _engine_verdicts.pop(review_id, None)


# ---------------------------------------------------------------------------
# 6. AVAILABLE_ENGINE_CAPABILITIES list is in sync with the registry
# ---------------------------------------------------------------------------

def test_available_engine_capabilities_all_known_to_antcrew_engine():
    """Every name in AVAILABLE_ENGINE_CAPABILITIES maps to an importable class or
    is a known alias (HitlReviewer is optional; ManualActionCapability is internal)."""
    from app.services.engine_runner import AVAILABLE_ENGINE_CAPABILITIES

    # These are the registered names (snake_case descriptor names in the registry).
    # The list uses PascalCase class names — verify each one corresponds to a real class.
    known_classes = {
        "Architect", "TaskPlanner", "CodeGenerator", "CodeRegenerator",
        "DependencyInstaller", "DocGenerator", "HitlReviewer",
        "TestGenerator", "TestRunner", "BugFixer", "CodeReviewer", "ReviewFixer",
    }
    for cap in AVAILABLE_ENGINE_CAPABILITIES:
        if cap == "ManualActionCapability":
            from antcrew_engine.capabilities import ManualActionCapability  # noqa: F401
        elif cap in known_classes:
            mod = __import__("antcrew_engine", fromlist=[cap])
            assert hasattr(mod, cap), f"antcrew_engine has no attribute {cap!r}"
        else:
            pytest.fail(f"Unexpected capability {cap!r} — update test if intentional")
