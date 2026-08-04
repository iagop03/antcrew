from __future__ import annotations

import operator
from typing import Annotated, Any, Optional

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from antcrew.core.artifacts import (
    PRD,
    CodeArtifact,
    CodebaseAnalysis,
    CodeReview,
    ConflictReport,
    ContentPiece,
    DevOpsArtifact,
    DocumentationArtifact,
    ResearchDocument,
    RetroReport,
    SecurityReport,
    TestArtifact,
    Ticket,
    UIDesignSpec,
)


class TeamState(TypedDict):
    """Central shared state that flows through the LangGraph pipeline."""

    # Task
    request: str
    messages: Annotated[list, add_messages]

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

    # Analysis / meta outputs
    conflict_report: Optional[ConflictReport]
    retro_report: Optional[RetroReport]
    security_report: Optional[SecurityReport]

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
