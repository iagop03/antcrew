from __future__ import annotations

import operator
from typing import Annotated, Any, Optional
from typing_extensions import TypedDict

from langgraph.graph.message import add_messages

from antcrew.core.artifacts import (
    PRD, Ticket, CodeArtifact,
    TestArtifact, CodeReview,
    DevOpsArtifact, DocumentationArtifact,
    ResearchDocument, ContentPiece,
)


class TeamState(TypedDict):
    """Central shared state that flows through the LangGraph pipeline."""

    # Task
    request: str
    messages: Annotated[list, add_messages]

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
