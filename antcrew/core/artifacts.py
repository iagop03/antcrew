from __future__ import annotations

from enum import Enum
from typing import Literal, Optional
from pydantic import BaseModel, Field


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TicketStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"


# ── Dev pipeline artifacts ───────────────────────────────────────────────────

class PRD(BaseModel):
    title: str
    summary: str
    goals: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    functional_requirements: list[str] = Field(default_factory=list)
    non_functional_requirements: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class Ticket(BaseModel):
    id: str
    title: str
    description: str
    priority: Priority = Priority.MEDIUM
    status: TicketStatus = TicketStatus.OPEN
    acceptance_criteria: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)


class CodeArtifact(BaseModel):
    ticket_id: str
    file_path: str
    description: str
    content: str
    language: Optional[str] = None


# ── QA artifacts ─────────────────────────────────────────────────────────────

class TestArtifact(BaseModel):
    ticket_id: str
    file_path: str
    description: str
    content: str
    language: Optional[str] = None
    coverage_areas: list[str] = Field(default_factory=list)


# ── Code-review artifacts ─────────────────────────────────────────────────────

Severity = Literal["info", "warning", "error", "critical"]


class ReviewFinding(BaseModel):
    severity: Severity = "warning"
    file_path: str
    message: str
    suggestion: Optional[str] = None
    line: Optional[int] = None


class CodeReview(BaseModel):
    verdict: Literal["approve", "request_changes", "reject"] = "approve"
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)


# ── Research artifacts ────────────────────────────────────────────────────────

class ResearchSection(BaseModel):
    heading: str
    content: str


class ResearchDocument(BaseModel):
    title: str
    topic: str
    key_findings: list[str] = Field(default_factory=list)
    sections: list[ResearchSection] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


# ── DevOps artifacts ─────────────────────────────────────────────────────────

class DevOpsArtifact(BaseModel):
    """A single infrastructure / deployment file (Dockerfile, CI YAML, Terraform, etc.)."""
    file_path: str
    description: str
    language: str   # "dockerfile" | "yaml" | "hcl" | "shell" | etc.
    content: str


# ── Documentation artifacts ──────────────────────────────────────────────────

class DocumentationArtifact(BaseModel):
    """A generated documentation file (README, architecture doc, API reference, etc.)."""
    file_path: str
    title: str
    doc_type: str   # "readme" | "architecture" | "api" | "user_guide" | "adr"
    format: str = "markdown"
    content: str


# ── Content artifacts ─────────────────────────────────────────────────────────

class ContentPiece(BaseModel):
    title: str
    target_audience: str
    tone: str = "professional"
    outline: list[str] = Field(default_factory=list)
    body: str = ""
    word_count: Optional[int] = None
