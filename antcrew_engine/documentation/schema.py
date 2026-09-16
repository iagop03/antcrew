"""DocumentationSchemaRegistry: load, validate, and query documentation schemas."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DocumentTypeConfig:
    id: str
    name: str
    category: str  # functional | technical | operational
    purpose: str = ""
    parser: str = "markdown"
    related_to: list[str] = field(default_factory=list)


class DocumentationSchemaRegistry:
    """Manages the documentation schema (structure) for a project.

    Each project defines its own document types and per-agent search hints
    via a YAML file (or a plain dict).
    """

    def __init__(self, schema_path: str | None = None) -> None:
        self._types: dict[str, DocumentTypeConfig] = {}
        self._agent_hints: dict[str, list[str]] = {}
        self._org_name: str = ""
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
