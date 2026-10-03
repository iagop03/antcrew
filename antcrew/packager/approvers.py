"""Approvers YAML config — risk-proportional approval routing.

Config format (YAML)::

    approvers:
      - role: qa_lead
        name: "María García"
        email: mgarcia@company.com
        required_if:
          risk: [medium, high]     # required when CR risk is medium or high

      - role: risk_officer
        name: "John Smith"
        email: jsmith@company.com
        required: always           # always required regardless of risk

      - role: business_owner
        name: "Ana Pérez"
        email: aperez@company.com
        required_if:
          risk: [high]

    # Optional: customize Excel columns for your ServiceNow template
    excel_columns:
      - change_ref
      - summary
      - risk_level
      - modified_components
      - db2_tables
      - test_runs
      - approvers
      - target_date

Usage::

    config = ApproversConfig.from_yaml("approvers.yaml")
    required = config.required_for("high")   # → [Approver(role="qa_lead"), Approver(role="risk_officer"), ...]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

RiskLevel = Literal["low", "medium", "high"]


@dataclass
class Approver:
    """One configured approver."""

    role: str
    name: str = ""
    email: str = ""
    required_always: bool = False
    required_for_risks: list[str] = field(default_factory=list)   # e.g. ["medium", "high"]

    def is_required_for(self, risk: str) -> bool:
        if self.required_always:
            return True
        return risk.lower() in [r.lower() for r in self.required_for_risks]


@dataclass
class ApproversConfig:
    """Parsed approvers configuration."""

    approvers: list[Approver] = field(default_factory=list)
    excel_columns: Optional[list[str]] = None

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ApproversConfig":
        """Load from a YAML file."""
        try:
            import yaml as _yaml
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required to load approvers config. pip install pyyaml"
            ) from exc
        raw = Path(path).read_text(encoding="utf-8")
        data = _yaml.safe_load(raw) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "ApproversConfig":
        """Parse from a plain dict (already loaded from YAML/JSON)."""
        approvers: list[Approver] = []
        for a in data.get("approvers") or []:
            required_always = str(a.get("required", "")).lower() == "always"
            risk_list = (a.get("required_if") or {}).get("risk") or []
            if isinstance(risk_list, str):
                risk_list = [risk_list]
            approvers.append(Approver(
                role=str(a.get("role", "")),
                name=str(a.get("name", "")),
                email=str(a.get("email", "")),
                required_always=required_always,
                required_for_risks=[str(r).lower() for r in risk_list],
            ))
        return cls(
            approvers=approvers,
            excel_columns=data.get("excel_columns"),
        )

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def required_for(self, risk: str) -> list[Approver]:
        """Return approvers required for the given risk level."""
        return [a for a in self.approvers if a.is_required_for(risk)]

    def all_approvers(self) -> list[Approver]:
        return list(self.approvers)

    def is_empty(self) -> bool:
        return len(self.approvers) == 0
