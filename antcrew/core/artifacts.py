from __future__ import annotations

import importlib
from enum import Enum
from typing import Generic, Literal, Optional, TypeVar
from pydantic import BaseModel, Field, field_validator

T = TypeVar("T", bound=BaseModel)


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TicketStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"


# ── Codebase analysis (produced by CodebaseScannerAgent) ─────────────────────

class CodebaseAnalysis(BaseModel):
    label: str = ""                                      # e.g. "frontend", "backend", "keycloak"
    tech_stack: list[str] = Field(default_factory=list)
    existing_modules: list[str] = Field(default_factory=list)
    entry_points: list[str] = Field(default_factory=list)
    test_coverage_summary: str = ""
    what_exists: str = ""
    what_is_missing: str = ""
    continuation_context: str = ""


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
    description: str = ""
    content: str
    language: Optional[str] = None
    created_at_run: int = 0


# ── QA artifacts ─────────────────────────────────────────────────────────────

class TestArtifact(BaseModel):
    ticket_id: str
    file_path: str
    description: str = ""
    content: str
    language: Optional[str] = None
    coverage_areas: list[str] = Field(default_factory=list)
    created_at_run: int = 0


# ── Code-review artifacts ─────────────────────────────────────────────────────

Severity = Literal["info", "warning", "error", "critical"]


class ReviewFinding(BaseModel):
    severity: Severity = "warning"
    file_path: str = ""
    message: str
    suggestion: Optional[str] = None
    line: Optional[int] = None


class CodeReview(BaseModel):
    verdict: Literal["approve", "request_changes", "reject"] = "approve"
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)

    @field_validator("verdict", mode="before")
    @classmethod
    def _normalise_verdict(cls, v: object) -> object:
        _MAP = {"approved": "approve", "rejected": "reject", "reject": "reject",
                "needs_changes": "request_changes", "request_change": "request_changes"}
        if isinstance(v, str):
            return _MAP.get(v.lower().strip(), v)
        return v


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
    description: str = ""
    language: str   # "dockerfile" | "yaml" | "hcl" | "shell" | etc.
    content: str
    created_at_run: int = 0


# ── Documentation artifacts ──────────────────────────────────────────────────

class DocumentationArtifact(BaseModel):
    """A generated documentation file (README, architecture doc, API reference, etc.)."""
    file_path: str
    title: str
    doc_type: str   # "readme" | "architecture" | "api" | "user_guide" | "adr"
    format: str = "markdown"
    content: str
    created_at_run: int = 0


# ── Content artifacts ─────────────────────────────────────────────────────────

class ContentPiece(BaseModel):
    title: str
    target_audience: str
    tone: str = "professional"
    outline: list[str] = Field(default_factory=list)
    body: str = ""
    word_count: Optional[int] = None


# ---------------------------------------------------------------------------
# Contract system
# ---------------------------------------------------------------------------

class ContractError(Exception):
    """Raised when an artifact cannot be extracted or validated from state."""


class ArtifactContract(Generic[T]):
    """Typed state accessor for a Pydantic artifact model.

    Encapsulates the key under which an artifact lives in the shared state
    and the Pydantic model used to validate it.  Agents that produce or
    consume a typed artifact use this to exchange objects instead of raw
    strings.

    Usage::

        from antcrew.core.artifacts import ArtifactContract, PRD

        prd_contract = ArtifactContract("prd", PRD)

        # In a producing agent:
        def run(self, state):
            prd = PRD(title="...", summary="...", ...)
            return prd_contract.inject(prd)   # → {"prd": {...}}

        # In a consuming agent:
        def run(self, state):
            prd = prd_contract.extract(state)  # → PRD instance, validated
            ...
    """

    def __init__(self, key: str, model: type[T]) -> None:
        self.key = key
        self.model = model

    def extract(self, state: dict) -> T:
        """Return the artifact from *state* as a validated model instance.

        Accepts three stored forms:
        - Already a model instance (pass-through)
        - A ``dict`` (``model.model_validate``)
        - A JSON string (``model.model_validate_json``)

        Raises :class:`ContractError` on missing key or validation failure.
        """
        import json as _json
        value = state.get(self.key)
        if value is None:
            raise ContractError(
                f"Missing required artifact '{self.key}' in state. "
                f"Expected {self.model.__name__}."
            )
        try:
            if isinstance(value, self.model):
                return value
            if isinstance(value, dict):
                return self.model.model_validate(value)
            if isinstance(value, str):
                return self.model.model_validate(_json.loads(value))
        except Exception as exc:
            raise ContractError(
                f"Cannot parse state['{self.key}'] as {self.model.__name__}: {exc}"
            ) from exc
        raise ContractError(
            f"Unsupported type for '{self.key}': {type(value).__name__}. "
            f"Expected dict, str, or {self.model.__name__}."
        )

    def inject(self, instance: T) -> dict:
        """Return ``{key: model_dump()}`` ready to be returned from ``agent.run()``.

        Stores as a plain dict (JSON-serializable) so checkpointers, the REPL,
        and downstream agents all work without special handling.
        """
        if not isinstance(instance, self.model):
            raise ContractError(
                f"inject() received {type(instance).__name__}, "
                f"expected {self.model.__name__}."
            )
        return {self.key: instance.model_dump()}

    def __repr__(self) -> str:
        return f"ArtifactContract(key={self.key!r}, model={self.model.__name__})"


# ---------------------------------------------------------------------------
# Schema registry + resolver
# ---------------------------------------------------------------------------

#: Short-name → class mapping for all built-in artifact types.
ARTIFACT_REGISTRY: dict[str, type[BaseModel]] = {
    "CodebaseAnalysis":     CodebaseAnalysis,
    "PRD":                  PRD,
    "Ticket":               Ticket,
    "CodeArtifact":         CodeArtifact,
    "TestArtifact":         TestArtifact,
    "ReviewFinding":        ReviewFinding,
    "CodeReview":           CodeReview,
    "ResearchSection":      ResearchSection,
    "ResearchDocument":     ResearchDocument,
    "DevOpsArtifact":       DevOpsArtifact,
    "DocumentationArtifact": DocumentationArtifact,
    "ContentPiece":         ContentPiece,
}


def coerce_model(raw, cls: type[T]) -> T:
    """Return *raw* as an instance of *cls*, coercing from dict if necessary.

    Handles three forms:
    - Already an instance of *cls* → returned as-is.
    - A ``dict`` → ``cls(**raw)``.
    - Anything else → ``ValueError``.
    """
    if isinstance(raw, cls):
        return raw
    if isinstance(raw, dict):
        return cls(**raw)
    raise ValueError(f"Cannot coerce {type(raw).__name__} to {cls.__name__}")


def coerce_list(state: dict, key: str, cls: type[T]) -> list[T]:
    """Return ``state[key]`` as a list of *cls* instances.

    Each element is coerced via :func:`coerce_model`; elements that are
    already *cls* instances pass through unchanged. Missing or ``None`` key
    returns an empty list.
    """
    raw_list = state.get(key) or []
    out: list[T] = []
    for item in raw_list:
        try:
            out.append(coerce_model(item, cls))
        except (ValueError, TypeError):
            out.append(item)  # leave broken items in place; don't silently drop
    return out


def resolve_artifact_schema(name: str) -> type[BaseModel]:
    """Resolve a schema name to a Pydantic model class.

    Accepts:
    - Short built-in name: ``"PRD"``, ``"CodeArtifact"``
    - Fully-qualified dotted path: ``"mypackage.models.MyModel"``

    Raises :class:`ValueError` when the name cannot be resolved.
    """
    if name in ARTIFACT_REGISTRY:
        return ARTIFACT_REGISTRY[name]
    if "." in name:
        module_path, class_name = name.rsplit(".", 1)
        try:
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
                raise ValueError(
                    f"'{name}' is not a Pydantic BaseModel subclass."
                )
            return cls
        except ImportError as exc:
            raise ValueError(
                f"Cannot import '{module_path}' to resolve schema '{name}': {exc}"
            ) from exc
        except AttributeError:
            raise ValueError(
                f"Module '{module_path}' has no class '{class_name}'."
            )
    raise ValueError(
        f"Unknown artifact schema '{name}'. "
        f"Built-in schemas: {sorted(ARTIFACT_REGISTRY)}. "
        "For custom schemas use the fully-qualified path: 'mypackage.models.MyModel'."
    )
