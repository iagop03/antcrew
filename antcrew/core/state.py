"""TeamState — the central shared state TypedDict for AntCrew LangGraph pipelines.

Three state stores exist in an AntCrew run, each with a distinct lifetime and purpose:

  TeamState (this file)
    LangGraph per-run state. Lives in memory for the duration of one team.run()
    call. Every agent node reads and partially updates it. Contains: the user
    request, typed artifact slots (prd, tickets, code_artifacts, …), messages
    (full LLM conversation history), and runtime metadata. Ephemeral — gone when
    the run ends (unless checkpointed via PostgresSaver).

  MemoryStore (antcrew_engine/engine/)
    antcrew-engine's internal dict[ArtifactId, Artifact]. Used only by Layer-2
    capability executors (EngineLoop) to accumulate code artifacts across loop
    iterations. Not shared with Layer-1 teams. Ephemeral — per-EngineLoop
    instance.

  KVMemory / RunMemory (platform DB)
    dict[str, Any] persisted to the RunMemory table in the platform database.
    Cross-run: survives between separate team.run() invocations for the same
    team in the same workspace. Read at run start, written at run end. Use for
    long-lived agent memory (e.g. "what did we decide in the last sprint?").

Invariant: data flows ONE direction per call:
  KVMemory → (loaded into) TeamState at run start
  TeamState → (extracted from) KVMemory at run end
  MemoryStore ↔ EngineLoop only (never touches TeamState or KVMemory directly)
"""
from __future__ import annotations

import contextvars
import operator
from typing import Annotated, Any, Optional

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from antcrew.core.artifacts import (
    PRD,
    APISpec,
    CodeArtifact,
    CodebaseAnalysis,
    CodeReview,
    ConflictReport,
    ContentPiece,
    DevOpsArtifact,
    DiscoveryContext,
    DocumentationArtifact,
    ResearchDocument,
    RetroReport,
    SecurityReport,
    TestArtifact,
    Ticket,
    UIDesignSpec,
)

# Per-supervisor limit on the number of messages kept in TeamState.
# Supervisor.build() calls _max_messages_var.set(N) before compiling the graph.
# When None (the default), no trimming is applied.
_max_messages_var: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "_max_messages_var", default=None
)


def _capped_add_messages(left: list, right: list) -> list:
    """LangGraph reducer: accumulate messages, then trim to the configured limit.

    Reads _max_messages_var at reduction time (not build time) so the limit can
    differ between concurrent supervisors running in the same process.
    """
    merged = add_messages(left, right)
    limit = _max_messages_var.get()
    if limit is not None and len(merged) > limit:
        return merged[-limit:]
    return merged


class TeamState(TypedDict):
    """Central shared state that flows through the LangGraph pipeline."""

    # Task
    request: str
    messages: Annotated[list, _capped_add_messages]

    # Codebase continuation context (set when --project-dir / project_dirs is provided)
    project_dir: Optional[str]                          # single-dir shorthand
    project_dirs: Optional[dict[str, str]]              # label → path (multi-component)
    codebase_analysis: Optional[CodebaseAnalysis]       # single-dir result (backward compat)
    codebase_analyses: Optional[list[CodebaseAnalysis]] # one per component

    # Dev-team artifacts
    prd: Optional[PRD]
    tickets: Optional[list[Ticket]]
    code_artifacts: Optional[list[CodeArtifact]]
    test_artifacts: Optional[list[TestArtifact]]
    review: Optional[CodeReview]
    devops_artifacts: Optional[list[DevOpsArtifact]]
    doc_artifacts: Optional[list[DocumentationArtifact]]

    # Research / content artifacts
    research_document: Optional[ResearchDocument]
    content_piece: Optional[ContentPiece]

    # UI design artifacts (produced by UIDesignAgent, consumed by FrontendDevAgent)
    ui_design_spec: Optional[UIDesignSpec]

    # API design artifacts (produced by APIDesignAgent, consumed by BackendDevAgent)
    api_spec: Optional[APISpec]

    # Analysis / meta outputs
    conflict_report: Optional[ConflictReport]
    retro_report: Optional[RetroReport]
    security_report: Optional[SecurityReport]

    # Conversational discovery (produced by DiscoveryAgent, consumed as PRD seed)
    discovery_context: Optional[DiscoveryContext]

    # Populated post-pipeline by SandboxRunner when injected into a team
    test_results: Optional[Any]

    # Sprint-based iteration (FullStackTeam)
    sprint_backlog: Optional[list[Ticket]]   # tickets not yet started
    sprint_number: int                        # 1-based sprint counter

    # Workflow metadata
    current_agent: str
    errors: list[str]

    # Arbitrary key-value store for conditional routing.
    # Agents write flags here (e.g. {"has_critical_bugs": True}) and the
    # Supervisor's router reads them when evaluating conditional edges.
    # Merged with | so partial updates never wipe existing keys.
    metadata: Annotated[dict[str, Any], operator.or_]
