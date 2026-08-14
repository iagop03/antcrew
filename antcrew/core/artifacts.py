from __future__ import annotations

import importlib
import json as _json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Generic, Literal, Optional, TypeVar

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
    label: str = ""
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
    # Phase 1 extension point — validated against WorkspaceContractSchema when
    # one is registered.  Built-in operators never read this field.
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    rationale: Optional[str] = None


class Ticket(BaseModel):
    id: str
    title: str
    description: str
    priority: Priority = Priority.MEDIUM
    status: TicketStatus = TicketStatus.OPEN
    acceptance_criteria: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    rationale: Optional[str] = None


class CodeArtifact(BaseModel):
    ticket_id: str
    file_path: str
    description: str = ""
    content: str
    language: Optional[str] = None
    created_at_run: int = 0
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    rationale: Optional[str] = None


# ── QA artifacts ─────────────────────────────────────────────────────────────

class TestArtifact(BaseModel):
    ticket_id: str
    file_path: str
    description: str = ""
    content: str
    language: Optional[str] = None
    coverage_areas: list[str] = Field(default_factory=list)
    created_at_run: int = 0
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    rationale: Optional[str] = None


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
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    rationale: Optional[str] = None

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
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    rationale: Optional[str] = None


# ── DevOps artifacts ─────────────────────────────────────────────────────────

class DevOpsArtifact(BaseModel):
    """A single infrastructure / deployment file (Dockerfile, CI YAML, Terraform, etc.)."""
    file_path: str
    description: str = ""
    language: str   # "dockerfile" | "yaml" | "hcl" | "shell" | etc.
    content: str
    created_at_run: int = 0
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    rationale: Optional[str] = None


# ── Documentation artifacts ──────────────────────────────────────────────────

class DocumentationArtifact(BaseModel):
    """A generated documentation file (README, architecture doc, API reference, etc.)."""
    file_path: str
    title: str
    doc_type: str   # "readme" | "architecture" | "api" | "user_guide" | "adr"
    format: str = "markdown"
    content: str
    created_at_run: int = 0
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    rationale: Optional[str] = None


# ── Content artifacts ─────────────────────────────────────────────────────────

class ContentPiece(BaseModel):
    title: str
    target_audience: str
    tone: str = "professional"
    outline: list[str] = Field(default_factory=list)
    body: str = ""
    word_count: Optional[int] = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    rationale: Optional[str] = None


# ── UI design artifacts ───────────────────────────────────────────────────────

class DesignTokens(BaseModel):
    """Color and typography tokens for a product's design system."""
    color_primary: str
    color_secondary: str
    color_background: str
    color_text: str
    font_family: str
    font_scale: dict[str, str] = Field(default_factory=dict)


class Screen(BaseModel):
    """A single UI screen / page in the application."""
    id: str
    name: str
    description: str
    route: str = ""
    components: list[str] = Field(default_factory=list)
    linked_tickets: list[str] = Field(default_factory=list)


class UIDesignSpec(BaseModel):
    """Full UI design specification produced by UIDesignAgent."""
    screens: list[Screen]
    navigation_flow: list[str] = Field(default_factory=list)
    tokens: DesignTokens
    design_system: str = ""
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    rationale: Optional[str] = None


# ── API design artifacts ─────────────────────────────────────────────────────

class Endpoint(BaseModel):
    """A single REST API endpoint in the design spec."""
    method: str  # GET | POST | PUT | DELETE | PATCH
    path: str
    description: str
    request_schema: Optional[dict[str, Any]] = None
    response_schema: Optional[dict[str, Any]] = None


class APISpec(BaseModel):
    """Full API specification produced by APIDesignAgent."""
    endpoints: list[Endpoint] = Field(default_factory=list)
    base_path: str = "/api/v1"
    rationale: Optional[str] = None


# ── Discovery artifacts (produced by DiscoveryAgent) ────────────────────────

class DiscoveryQA(BaseModel):
    question: str
    answer: str = ""


class DiscoveryContext(BaseModel):
    """Accumulated requirements gathered during a conversational discovery session."""
    project_name: str = ""
    problem_statement: str = ""
    target_users: str = ""
    tech_stack: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    key_features: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    qa_pairs: list[DiscoveryQA] = Field(default_factory=list)
    is_complete: bool = False
    max_rounds: int = Field(default=7, ge=2, le=20)
    rationale: Optional[str] = None


# ── Analysis / meta artifacts ────────────────────────────────────────────────

class ConflictItem(BaseModel):
    artifact_a: str
    artifact_b: str
    description: str
    severity: Literal["low", "medium", "high"] = "medium"


class ConflictReport(BaseModel):
    conflicts: list[ConflictItem] = Field(default_factory=list)
    summary: str = ""
    rationale: Optional[str] = None


class RetroReport(BaseModel):
    sprint_number: int = 1
    what_went_well: list[str] = Field(default_factory=list)
    what_could_improve: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    velocity_note: str = ""
    rationale: Optional[str] = None


class SecurityFinding(BaseModel):
    rule_id: str = ""
    severity: Severity = "warning"
    file_path: str = ""
    line: Optional[int] = None
    message: str
    fix_suggestion: Optional[str] = None


class SecurityReport(BaseModel):
    findings: list[SecurityFinding] = Field(default_factory=list)
    scanned_files: int = 0
    tool: str = "llm"  # "semgrep" | "llm"
    summary: str = ""
    rationale: Optional[str] = None


# ── SEO artifacts ─────────────────────────────────────────────────────────────

class SEOAnalysis(BaseModel):
    title: str = ""
    meta_description: str = ""
    primary_keyword: str = ""
    secondary_keywords: list[str] = Field(default_factory=list)
    score: int = 0
    readability_score: int = 0
    recommendations: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    rationale: Optional[str] = None


# ── Social media artifacts ────────────────────────────────────────────────────

class SocialMediaPlan(BaseModel):
    topic: str = ""
    twitter: str = ""
    linkedin: str = ""
    instagram: str = ""
    hashtags: list[str] = Field(default_factory=list)
    best_posting_times: list[str] = Field(default_factory=list)
    rationale: Optional[str] = None


# ── Email campaign artifacts ──────────────────────────────────────────────────

class EmailCampaign(BaseModel):
    subject: str = ""
    preview_text: str = ""
    body_html: str = ""
    body_text: str = ""
    cta_text: str = ""
    cta_url: str = ""
    target_segments: list[str] = Field(default_factory=list)
    rationale: Optional[str] = None


# ── Data analysis artifacts ───────────────────────────────────────────────────

class DataInsight(BaseModel):
    title: str
    finding: str
    metric: str = ""


class DataAnalysisReport(BaseModel):
    title: str = "Data Analysis"
    summary: str = ""
    insights: list[DataInsight] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    data_quality_notes: str = ""
    rationale: Optional[str] = None


# ── Cloud architecture artifacts ──────────────────────────────────────────────

class CloudService(BaseModel):
    name: str
    service_type: str = "compute"
    purpose: str = ""
    estimated_monthly_cost_usd: float = 0.0


class CloudArchSpec(BaseModel):
    title: str = "Cloud Architecture"
    provider: str = "aws"
    services: list[CloudService] = Field(default_factory=list)
    architecture_diagram: str = ""
    estimated_monthly_cost_usd: float = 0.0
    security_notes: list[str] = Field(default_factory=list)
    rationale: Optional[str] = None


# ── Database schema artifacts ─────────────────────────────────────────────────

class DBColumn(BaseModel):
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False
    foreign_key: Optional[str] = None
    unique: bool = False
    description: str = ""


class DBTable(BaseModel):
    name: str
    description: str = ""
    columns: list[DBColumn] = Field(default_factory=list)
    indexes: list[str] = Field(default_factory=list)


class DatabaseSchema(BaseModel):
    title: str = "Database Schema"
    db_type: str = "postgresql"
    tables: list[DBTable] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)
    migration_notes: str = ""
    rationale: Optional[str] = None


# ── Design system artifacts ───────────────────────────────────────────────────

class DesignSystemComponent(BaseModel):
    name: str
    description: str = ""
    variants: list[str] = Field(default_factory=list)
    props: list[str] = Field(default_factory=list)


class DesignSystemDoc(BaseModel):
    title: str = "Design System"
    description: str = ""
    principles: list[str] = Field(default_factory=list)
    components: list[DesignSystemComponent] = Field(default_factory=list)
    tokens: dict[str, str] = Field(default_factory=dict)
    usage_guidelines: list[str] = Field(default_factory=list)
    rationale: Optional[str] = None


# ---------------------------------------------------------------------------
# Workspace contract schema registry
# ---------------------------------------------------------------------------

class ContractError(Exception):
    """Raised when an artifact cannot be extracted, validated, or produced."""


class WorkspaceContractSchema:
    """Per-workspace registry of custom_fields constraints for artifact types.

    Each artifact type that carries ``custom_fields`` can have an optional
    schema registered here.  When a schema is registered for a key, any call
    to :meth:`ArtifactContract.inject` that produces a non-empty
    ``custom_fields`` dict is validated against it.  Built-in operators never
    populate ``custom_fields``, so they are unaffected by any registry.

    Two schema formats are accepted:

    **Pydantic model class** (no extra dependencies)::

        class PRDExtras(BaseModel):
            target_audience: str
            brand_guidelines_url: str = ""

        registry = WorkspaceContractSchema()
        registry.register("prd", PRDExtras)

    **JSON Schema dict** (requires ``pip install jsonschema``)::

        registry.register("prd", {
            "type": "object",
            "properties": {
                "target_audience": {"type": "string"},
                "brand_guidelines_url": {"type": "string"},
            },
            "required": ["target_audience"],
            "additionalProperties": False,
        })

    Apply globally so all contracts pick it up automatically::

        from antcrew.core.artifacts import set_workspace_schema
        set_workspace_schema(registry)
    """

    def __init__(self) -> None:
        self._schemas: dict[str, type[BaseModel] | dict] = {}

    def register(
        self,
        artifact_key: str,
        schema: type[BaseModel] | dict,
    ) -> "WorkspaceContractSchema":
        """Register a schema for *artifact_key*.  Returns self for chaining."""
        if isinstance(schema, dict):
            self._schemas[artifact_key] = schema
        elif isinstance(schema, type) and issubclass(schema, BaseModel):
            self._schemas[artifact_key] = schema
        else:
            raise ValueError(
                "schema must be a Pydantic BaseModel subclass or a JSON Schema dict, "
                f"got {type(schema).__name__}"
            )
        return self

    def validate(self, artifact_key: str, custom_fields: dict) -> None:
        """Validate *custom_fields* against the registered schema.

        No-op when no schema is registered for *artifact_key*.
        Raises :class:`ContractError` on validation failure.
        """
        schema = self._schemas.get(artifact_key)
        if schema is None:
            return

        if isinstance(schema, type) and issubclass(schema, BaseModel):
            try:
                schema.model_validate(custom_fields)
            except Exception as exc:
                raise ContractError(
                    f"custom_fields for '{artifact_key}' failed Pydantic validation: {exc}"
                ) from exc
        else:
            # JSON Schema dict — requires jsonschema package
            try:
                import jsonschema  # type: ignore[import]
            except ImportError as exc:
                raise ContractError(
                    "JSON Schema dict validation requires 'jsonschema'. "
                    "Install it: pip install jsonschema  "
                    "(or pip install 'antcrew[contracts]')"
                ) from exc
            try:
                jsonschema.validate(custom_fields, schema)
            except jsonschema.ValidationError as exc:
                raise ContractError(
                    f"custom_fields for '{artifact_key}' failed JSON Schema validation: "
                    f"{exc.message}"
                ) from exc

    def has(self, artifact_key: str) -> bool:
        """Return True when a schema is registered for *artifact_key*."""
        return artifact_key in self._schemas

    def schema_for(self, artifact_key: str) -> type[BaseModel] | dict | None:
        """Return the registered schema for *artifact_key*, or None."""
        return self._schemas.get(artifact_key)

    def __repr__(self) -> str:
        return f"WorkspaceContractSchema(keys={sorted(self._schemas)})"


# Module-level default registry — set once at workspace initialisation.
_default_schema_registry: WorkspaceContractSchema | None = None


def set_workspace_schema(registry: WorkspaceContractSchema) -> None:
    """Set the module-level default schema registry.

    All :class:`ArtifactContract` instances that do not carry their own
    registry will fall back to this one.  Call once during workspace setup::

        from antcrew.core.artifacts import WorkspaceContractSchema, set_workspace_schema

        registry = WorkspaceContractSchema()
        registry.register("prd", PRDExtras)
        set_workspace_schema(registry)
    """
    global _default_schema_registry
    _default_schema_registry = registry


def get_workspace_schema() -> WorkspaceContractSchema | None:
    """Return the current module-level default schema registry, or None."""
    return _default_schema_registry


# ---------------------------------------------------------------------------
# Contract system
# ---------------------------------------------------------------------------

class ArtifactContract(Generic[T]):
    """Typed state accessor for a Pydantic artifact model.

    Encapsulates the key under which an artifact lives in the shared state
    and the Pydantic model used to validate it.  Agents that produce or
    consume a typed artifact use this to exchange objects instead of raw
    strings.

    An optional *schema_registry* (or the module-level default set via
    :func:`set_workspace_schema`) is used to validate ``custom_fields``
    whenever :meth:`inject` is called with a non-empty dict.

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

    def __init__(
        self,
        key: str,
        model: type[T],
        schema_registry: WorkspaceContractSchema | None = None,
    ) -> None:
        self.key = key
        self.model = model
        self._schema_registry = schema_registry

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

        If the instance has a non-empty ``custom_fields`` dict and a schema
        registry is active (per-contract or module-level default), the dict is
        validated before the artifact is stored.

        Raises :class:`ContractError` on type mismatch or custom_fields
        validation failure.
        """
        if not isinstance(instance, self.model):
            raise ContractError(
                f"inject() received {type(instance).__name__}, "
                f"expected {self.model.__name__}."
            )
        dumped = instance.model_dump()

        registry = self._schema_registry or _default_schema_registry
        if registry is not None:
            cf = dumped.get("custom_fields")
            if cf:
                registry.validate(self.key, cf)

        return {self.key: dumped}

    def __repr__(self) -> str:
        return f"ArtifactContract(key={self.key!r}, model={self.model.__name__})"


# ---------------------------------------------------------------------------
# Schema registry + resolver
# ---------------------------------------------------------------------------

#: Short-name → class mapping for all built-in artifact types.
ARTIFACT_REGISTRY: dict[str, type[BaseModel]] = {
    "CodebaseAnalysis":      CodebaseAnalysis,
    "PRD":                   PRD,
    "Ticket":                Ticket,
    "CodeArtifact":          CodeArtifact,
    "TestArtifact":          TestArtifact,
    "ReviewFinding":         ReviewFinding,
    "CodeReview":            CodeReview,
    "ResearchSection":       ResearchSection,
    "ResearchDocument":      ResearchDocument,
    "DevOpsArtifact":        DevOpsArtifact,
    "DocumentationArtifact": DocumentationArtifact,
    "ContentPiece":          ContentPiece,
    "DesignTokens":          DesignTokens,
    "Screen":                Screen,
    "UIDesignSpec":          UIDesignSpec,
    "Endpoint":              Endpoint,
    "APISpec":               APISpec,
    "ConflictItem":          ConflictItem,
    "ConflictReport":        ConflictReport,
    "RetroReport":           RetroReport,
    "SecurityFinding":       SecurityFinding,
    "SecurityReport":        SecurityReport,
    "DiscoveryQA":           DiscoveryQA,
    "DiscoveryContext":      DiscoveryContext,
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


# ---------------------------------------------------------------------------
# Artifact version history
# ---------------------------------------------------------------------------

@dataclass
class ArtifactVersion:
    """A single snapshot in an artifact's version history.

    Attributes:
        run_id:         Run that produced this version.
        version:        Monotonically increasing version number (1-based).
        content:        Artifact content (dict or string).
        summary:        Human-readable narrative of what changed, e.g.
                        ``"Removed JWT auth requirement"`` (set by the caller or
                        left empty for the initial version).
        diff_lines:     Unified diff lines vs the previous version.
        ts:             UTC timestamp when this version was recorded.
    """
    run_id:     str
    version:    int
    content:    "dict | str"
    summary:    str = ""
    diff_lines: list[str] = field(default_factory=list)
    ts:         datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ArtifactHistory:
    """Persist artifact versions to a JSON file across runs.

    Stores a version history for each artifact ID so you can see how an
    artifact evolved across multiple pipeline runs.

    Usage::

        from antcrew.core.artifacts import ArtifactHistory

        history = ArtifactHistory(".antcrew/artifact_history.json")

        # After a run produces a PRD:
        history.record("prd", run_id=run_id, content=prd.model_dump(),
                       summary="Initial PRD for auth service")

        # After a second run revises it:
        history.record("prd", run_id=run_id2, content=revised_prd.model_dump(),
                       summary="Removed OAuth, simplified to API keys")

        # Inspect history:
        for v in history.timeline("prd"):
            print(v.version, v.summary, len(v.diff_lines), "diff lines")

    Args:
        path: File path for the JSON history store.  Created on first write.
    """

    def __init__(self, path: "str | Path") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._store: dict[str, list[dict]] = (
            _json.loads(self._path.read_text(encoding="utf-8"))
            if self._path.exists()
            else {}
        )

    def record(
        self,
        artifact_id: str,
        *,
        run_id: str,
        content: "dict | str",
        summary: str = "",
    ) -> ArtifactVersion:
        """Record a new version of *artifact_id*.

        Computes a unified diff vs the previous version automatically.
        Persists the updated history to disk immediately.

        Args:
            artifact_id: Logical identifier for the artifact (e.g. ``"prd"``).
            run_id:      Identifier of the run that produced this version.
            content:     New artifact content — a dict or string.
            summary:     Human-readable narrative of what changed.

        Returns:
            The newly created :class:`ArtifactVersion`.
        """
        import difflib as _dl

        prev_versions = self._store.get(artifact_id, [])
        prev_text = (
            _json.dumps(prev_versions[-1]["content"], indent=2, ensure_ascii=False)
            if prev_versions
            else ""
        )
        curr_text = (
            _json.dumps(content, indent=2, ensure_ascii=False)
            if isinstance(content, dict)
            else str(content)
        )

        diff_lines = list(
            _dl.unified_diff(
                prev_text.splitlines(),
                curr_text.splitlines(),
                fromfile=f"v{len(prev_versions)}",
                tofile=f"v{len(prev_versions) + 1}",
                lineterm="",
            )
        )

        ts = datetime.now(timezone.utc)
        version = ArtifactVersion(
            run_id=run_id,
            version=len(prev_versions) + 1,
            content=content,
            summary=summary,
            diff_lines=diff_lines,
            ts=ts,
        )

        row = {
            "run_id":     run_id,
            "version":    version.version,
            "content":    content,
            "summary":    summary,
            "diff_lines": diff_lines,
            "ts":         ts.isoformat(),
        }
        self._store.setdefault(artifact_id, []).append(row)
        self._path.write_text(
            _json.dumps(self._store, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return version

    def timeline(self, artifact_id: str) -> list[ArtifactVersion]:
        """Return all versions of *artifact_id*, oldest first.

        Returns an empty list if no versions have been recorded.
        """
        return [
            ArtifactVersion(
                run_id=row["run_id"],
                version=row["version"],
                content=row["content"],
                summary=row.get("summary", ""),
                diff_lines=row.get("diff_lines", []),
                ts=datetime.fromisoformat(row["ts"]) if row.get("ts") else datetime.now(timezone.utc),
            )
            for row in self._store.get(artifact_id, [])
        ]

    def latest(self, artifact_id: str) -> "ArtifactVersion | None":
        """Return the most recent version of *artifact_id*, or ``None``."""
        versions = self.timeline(artifact_id)
        return versions[-1] if versions else None

    def artifact_ids(self) -> list[str]:
        """Return all artifact IDs that have recorded history."""
        return list(self._store.keys())
