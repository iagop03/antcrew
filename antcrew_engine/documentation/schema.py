"""DocumentationSchemaRegistry: load, validate, and query documentation schemas."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DocumentTypeConfig:
    id: str
    name: str
    category: str  # functional | technical | operational | procedural | …
    purpose: str = ""
    parser: str = "markdown"
    related_to: list[str] = field(default_factory=list)


@dataclass
class QueryHint:
    """Maps query keywords/patterns to preferred doc types.

    When a user query matches ``pattern`` (case-insensitive regex or plain
    substring), the listed ``doc_types`` are searched first and their results
    are ranked above generic matches.
    """
    pattern: str          # regex or plain substring (matched case-insensitively)
    doc_types: list[str]  # doc_type ids to prioritise
    _compiled: "re.Pattern | None" = field(default=None, init=False, repr=False)

    def matches(self, query: str) -> bool:
        if self._compiled is None:
            try:
                object.__setattr__(self, "_compiled", re.compile(self.pattern, re.IGNORECASE))
            except re.error:
                object.__setattr__(self, "_compiled", re.compile(re.escape(self.pattern), re.IGNORECASE))
        return bool(self._compiled.search(query))


@dataclass
class PathRule:
    """Maps an S3 / storage path prefix or suffix to a doc type.

    Used by ``index_from_storage()`` to classify pre-existing files that have
    no metadata sidecar.  Rules are evaluated in order; first match wins.

    Examples::

        PathRule(prefix="procedures/", doc_type="procedure")
        PathRule(prefix="specs/",      doc_type="functional_spec")
        PathRule(suffix=".jira.json",  doc_type="jira_ticket")
    """
    doc_type: str
    prefix: str = ""   # match if doc_id starts with this string (case-insensitive)
    suffix: str = ""   # match if doc_id ends with this string (case-insensitive)

    def matches(self, doc_id: str) -> bool:
        lower = doc_id.lower()
        if self.prefix and not lower.startswith(self.prefix.lower()):
            return False
        if self.suffix and not lower.endswith(self.suffix.lower()):
            return False
        return bool(self.prefix or self.suffix)


@dataclass
class CobolSupport:
    """COBOL / legacy system options when org_type is 'legacy'."""
    enabled: bool = True
    as400_connection: bool = False    # expose AS400Connector in CLI / agents
    copybook_parsing: bool = True     # parse .cpy/.copy files as COBOL
    batch_job_docs: bool = True       # treat COBOL programs as batch-job docs


class DocumentationSchemaRegistry:
    """Manages the documentation schema (structure) for a project.

    Each project defines its own document types, per-agent search hints,
    query-intent routing hints, and path-based classification rules via a
    YAML file (or a plain dict).

    Schema YAML example::

        documentation_schema:
          org_name: Acme Corp
          org_type: legacy          # legacy | microservices | mixed

          if_legacy:
            cobol_support:
              enable: true
              as400_connection: false
              copybook_parsing: true
              batch_job_docs: true

          document_types:
            - id: functional_spec
              name: Functional Specification
              category: functional
              parser: markdown

            - id: procedure
              name: Procedure
              category: procedural
              parser: markdown

          agent_hints:
            CodeGenerator:
              - "For requirements: search Functional Specification (functional_spec)"

          query_hints:
            - pattern: "crear tabla|table creation|nueva tabla"
              doc_types: [procedure]
            - pattern: "componente|component|implementar"
              doc_types: [functional_spec, technical_design]

          path_rules:
            - prefix: "procedures/"
              doc_type: procedure
            - prefix: "specs/"
              doc_type: functional_spec
    """

    def __init__(self, schema_path: str | None = None) -> None:
        self._types: dict[str, DocumentTypeConfig] = {}
        self._agent_hints: dict[str, list[str]] = {}
        self._query_hints: list[QueryHint] = []
        self._path_rules: list[PathRule] = []
        self._org_name: str = ""
        self._org_type: str = "microservices"   # legacy | microservices | mixed
        self._cobol_support: CobolSupport | None = None
        if schema_path:
            self.load_from_file(schema_path)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_from_file(self, schema_path: str) -> None:
        """Load schema from a YAML file."""
        try:
            import yaml
        except ImportError:
            raise ImportError("pyyaml is required for schema YAML files: pip install pyyaml")
        data = yaml.safe_load(Path(schema_path).read_text(encoding="utf-8"))
        self.load_from_dict(data)

    def load_from_dict(self, schema_dict: dict[str, Any]) -> None:
        """Load schema from a plain dictionary."""
        root = schema_dict.get("documentation_schema", schema_dict)
        self._org_name = root.get("org_name", "")
        self._org_type = root.get("org_type", "microservices")

        if_legacy = root.get("if_legacy", {})
        cs_raw = if_legacy.get("cobol_support", {})
        if self._org_type == "legacy" or cs_raw.get("enable", cs_raw.get("enabled", False)):
            self._cobol_support = CobolSupport(
                enabled=True,
                as400_connection=cs_raw.get("as400_connection", False),
                copybook_parsing=cs_raw.get("copybook_parsing", True),
                batch_job_docs=cs_raw.get("batch_job_docs", True),
            )

        for dt in root.get("document_types", []):
            cfg = DocumentTypeConfig(
                id=dt["id"],
                name=dt.get("name", dt["id"]),
                category=dt.get("category", "technical"),
                purpose=dt.get("purpose", ""),
                parser=dt.get("parser", "markdown"),
                related_to=list(dt.get("related_to", [])),
            )
            self._types[cfg.id] = cfg

        for agent, hints in root.get("agent_hints", {}).items():
            self._agent_hints[agent] = list(hints)

        self._query_hints = [
            QueryHint(
                pattern=qh["pattern"],
                doc_types=list(qh.get("doc_types", [])),
            )
            for qh in root.get("query_hints", [])
        ]

        self._path_rules = [
            PathRule(
                doc_type=pr["doc_type"],
                prefix=pr.get("prefix", ""),
                suffix=pr.get("suffix", ""),
            )
            for pr in root.get("path_rules", [])
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_schema(self) -> bool:
        """Return True if the schema has at least one valid document type."""
        if not self._types:
            return False
        return all(dt.id and dt.name for dt in self._types.values())

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_document_type(self, doc_type_id: str) -> DocumentTypeConfig | None:
        return self._types.get(doc_type_id)

    def get_parser_for_type(self, doc_type_id: str) -> str:
        dt = self._types.get(doc_type_id)
        return dt.parser if dt else "markdown"

    def get_related_types(self, doc_type_id: str) -> list[str]:
        dt = self._types.get(doc_type_id)
        return list(dt.related_to) if dt else []

    def get_agent_hints(self, agent_name: str) -> list[str]:
        return list(self._agent_hints.get(agent_name, []))

    def get_category_for_type(self, doc_type_id: str) -> str:
        dt = self._types.get(doc_type_id)
        return dt.category if dt else ""

    def all_types(self) -> list[DocumentTypeConfig]:
        return list(self._types.values())

    @property
    def org_name(self) -> str:
        return self._org_name

    @property
    def org_type(self) -> str:
        return self._org_type

    @property
    def is_legacy(self) -> bool:
        return self._org_type == "legacy"

    @property
    def cobol_support(self) -> "CobolSupport | None":
        return self._cobol_support

    # ------------------------------------------------------------------
    # Query-intent routing
    # ------------------------------------------------------------------

    def get_doc_types_for_query(self, query: str) -> list[str]:
        """Return doc_type ids that the query_hints say are relevant for this query.

        Returns an empty list when no hint matches (→ generic search across all types).
        Multiple matching hints are merged in order; duplicates are preserved for ranking.
        """
        matched: list[str] = []
        for hint in self._query_hints:
            if hint.matches(query):
                for dt in hint.doc_types:
                    if dt not in matched:
                        matched.append(dt)
        return matched

    # ------------------------------------------------------------------
    # Path-based classification (for existing storage files)
    # ------------------------------------------------------------------

    def classify_by_path(self, doc_id: str) -> str | None:
        """Return a doc_type id for a file path, or None if no rule matches.

        Evaluated in schema order; first match wins.
        """
        for rule in self._path_rules:
            if rule.matches(doc_id):
                return rule.doc_type
        return None

    # ------------------------------------------------------------------
    # Runtime registration
    # ------------------------------------------------------------------

    def register_document_type(self, doc_type_config: dict[str, Any]) -> None:
        """Dynamically register a new document type at runtime."""
        cfg = DocumentTypeConfig(
            id=doc_type_config["id"],
            name=doc_type_config.get("name", doc_type_config["id"]),
            category=doc_type_config.get("category", "technical"),
            purpose=doc_type_config.get("purpose", ""),
            parser=doc_type_config.get("parser", "markdown"),
            related_to=list(doc_type_config.get("related_to", [])),
        )
        self._types[cfg.id] = cfg
