"""ServiceNow adapter protocol — neutral Change Record interface.

The adapter wraps ServiceNow's Table API v2. Field names in ServiceNow
vary per instance (custom fields, renamed OOB fields) so all mapping is
configured via ``field_map`` in the adapter config rather than hard-coded.

Default field map (can be overridden in YAML)::

    field_map:
      short_description: short_description
      description:       description
      risk:              risk
      impact:            impact
      state:             state
      assignment_group:  assignment_group
      requested_by:      requested_by
      start_date:        start_date
      end_date:          end_date
      change_ref:        u_change_ref          # custom field — adjust to your instance
      risk_level:        u_risk_level          # custom field — adjust to your instance
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChangeRecord:
    """Neutral representation of a ServiceNow Change Request."""

    sys_id: str = ""
    number: str = ""
    short_description: str = ""
    description: str = ""
    state: str = ""
    risk: str = ""
    impact: str = ""
    assignment_group: str = ""
    requested_by: str = ""
    start_date: str = ""
    end_date: str = ""
    change_ref: str = ""
    risk_level: str = ""
    url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChangeRecordInput:
    """Fields to set when creating or updating a Change Request."""

    short_description: str = ""
    description: str = ""
    risk: str = ""
    impact: str = ""
    state: str = ""
    assignment_group: str = ""
    requested_by: str = ""
    start_date: str = ""
    end_date: str = ""
    change_ref: str = ""
    risk_level: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
