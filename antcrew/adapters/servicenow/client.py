"""ServiceNow Table API v2 client.

Wraps change_request table CRUD. All field names are mapped through a
configurable ``field_map`` so the adapter works on any ServiceNow instance
regardless of local field customisation.

Configuration (agentteam.yaml)::

    servicenow:
      instance_url: https://mycompany.service-now.com
      # SERVICENOW_USER / SERVICENOW_PASSWORD env vars for Basic auth
      # or SERVICENOW_TOKEN for OAuth bearer token
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
        change_ref:        u_change_ref      # adjust to your instance
        risk_level:        u_risk_level      # adjust to your instance
      # Optional: map neutral risk levels to ServiceNow risk values
      risk_map:
        low:    low
        medium: moderate
        high:   high
      # Optional: map neutral states to ServiceNow state values
      state_map:
        new:          new
        in_progress:  in progress
        approved:     approved
        deployed:     closed

Usage::

    from antcrew.adapters.servicenow import get_servicenow_adapter

    adapter = get_servicenow_adapter(cfg["servicenow"])
    record = adapter.create_change(ChangeRecordInput(
        short_description="Deploy Sprint-42",
        change_ref="CR-4201",
        risk_level="medium",
    ))
    print(record.number, record.url)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from .protocol import ChangeRecord, ChangeRecordInput

log = logging.getLogger(__name__)

_DEFAULT_FIELD_MAP: dict[str, str] = {
    "short_description": "short_description",
    "description":       "description",
    "risk":              "risk",
    "impact":            "impact",
    "state":             "state",
    "assignment_group":  "assignment_group",
    "requested_by":      "requested_by",
    "start_date":        "start_date",
    "end_date":          "end_date",
    "change_ref":        "u_change_ref",
    "risk_level":        "u_risk_level",
}

_DEFAULT_RISK_MAP: dict[str, str] = {
    "low":    "low",
    "medium": "moderate",
    "high":   "high",
}

_DEFAULT_STATE_MAP: dict[str, str] = {
    "new":         "new",
    "in_progress": "in progress",
    "approved":    "approved",
    "deployed":    "closed",
}

_TABLE = "change_request"


class ServiceNowClient:
    """Thin client over ServiceNow Table API v2 for change_request records.

    Auth priority (first non-empty wins):
    1. ``SERVICENOW_TOKEN`` env var — OAuth bearer token
    2. ``SERVICENOW_USER`` + ``SERVICENOW_PASSWORD`` env vars — Basic auth
    3. ``username`` / ``password`` kwargs passed to ``from_config()``
    """

    def __init__(
        self,
        instance_url: str,
        username: str = "",
        password: str = "",
        token: str = "",
        field_map: Optional[dict[str, str]] = None,
        risk_map: Optional[dict[str, str]] = None,
        state_map: Optional[dict[str, str]] = None,
    ) -> None:
        self._base = instance_url.rstrip("/")
        self._api = f"{self._base}/api/now/v2/table/{_TABLE}"

        # Auth — env vars override constructor args
        _token = os.environ.get("SERVICENOW_TOKEN", token).strip()
        _user = os.environ.get("SERVICENOW_USER", username).strip()
        _pass = os.environ.get("SERVICENOW_PASSWORD", password).strip()

        if _token:
            self._auth_headers: dict[str, str] = {"Authorization": f"Bearer {_token}"}
            self._auth_tuple: tuple[str, str] | None = None
        elif _user:
            self._auth_headers = {}
            self._auth_tuple = (_user, _pass)
        else:
            raise ValueError(
                "ServiceNow auth not configured. "
                "Set SERVICENOW_TOKEN or SERVICENOW_USER + SERVICENOW_PASSWORD."
            )

        self._field_map  = {**_DEFAULT_FIELD_MAP,  **(field_map  or {})}
        self._risk_map   = {**_DEFAULT_RISK_MAP,   **(risk_map   or {})}
        self._state_map  = {**_DEFAULT_STATE_MAP,  **(state_map  or {})}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_change(self, inp: ChangeRecordInput) -> ChangeRecord:
        """Create a new Change Request. Returns the created record."""
        payload = self._to_snow(inp)
        resp = self._post(payload)
        return self._from_snow(resp)

    def update_change(self, sys_id: str, inp: ChangeRecordInput) -> ChangeRecord:
        """Update an existing Change Request by sys_id."""
        payload = self._to_snow(inp)
        resp = self._patch(sys_id, payload)
        return self._from_snow(resp)

    def get_change(self, sys_id: str) -> ChangeRecord:
        """Fetch a Change Request by sys_id."""
        resp = self._get(sys_id)
        return self._from_snow(resp)

    def find_by_change_ref(self, change_ref: str) -> list[ChangeRecord]:
        """Return all Change Requests whose change_ref field matches."""
        snow_field = self._field_map.get("change_ref", "u_change_ref")
        import httpx
        r = httpx.get(
            self._api,
            params={"sysparm_query": f"{snow_field}={change_ref}", "sysparm_limit": "50"},
            headers={**self._auth_headers, "Accept": "application/json"},
            auth=self._auth_tuple,
            timeout=30,
        )
        r.raise_for_status()
        return [self._from_snow(row) for row in r.json().get("result", [])]

    # ------------------------------------------------------------------
    # Field mapping
    # ------------------------------------------------------------------

    def _to_snow(self, inp: ChangeRecordInput) -> dict[str, Any]:
        """Map neutral ChangeRecordInput to ServiceNow field names."""
        fm = self._field_map
        result: dict[str, Any] = {}

        def _set(neutral: str, value: str) -> None:
            if value:
                result[fm.get(neutral, neutral)] = value

        _set("short_description", inp.short_description)
        _set("description",       inp.description)
        _set("risk",              self._risk_map.get(inp.risk, inp.risk))
        _set("impact",            inp.impact)
        _set("state",             self._state_map.get(inp.state, inp.state))
        _set("assignment_group",  inp.assignment_group)
        _set("requested_by",      inp.requested_by)
        _set("start_date",        inp.start_date)
        _set("end_date",          inp.end_date)
        _set("change_ref",        inp.change_ref)
        _set("risk_level",        self._risk_map.get(inp.risk_level, inp.risk_level))

        result.update(inp.extra)
        return result

    def _from_snow(self, row: dict[str, Any]) -> ChangeRecord:
        """Map ServiceNow field names back to neutral ChangeRecord."""
        rev = {v: k for k, v in self._field_map.items()}

        def _get(neutral: str) -> str:
            snow_field = self._field_map.get(neutral, neutral)
            raw = row.get(snow_field, "")
            if isinstance(raw, dict):
                return str(raw.get("value", raw.get("display_value", "")))
            return str(raw) if raw else ""

        sys_id = _get_raw(row, "sys_id")
        number = _get_raw(row, "number")
        return ChangeRecord(
            sys_id=sys_id,
            number=number,
            short_description=_get("short_description"),
            description=_get("description"),
            state=_get("state"),
            risk=_get("risk"),
            impact=_get("impact"),
            assignment_group=_get("assignment_group"),
            requested_by=_get("requested_by"),
            start_date=_get("start_date"),
            end_date=_get("end_date"),
            change_ref=_get("change_ref"),
            risk_level=_get("risk_level"),
            url=f"{self._base}/nav_to.do?uri=change_request.do?sys_id={sys_id}" if sys_id else "",
            extra=row,
        )

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            **self._auth_headers,
            "Accept":       "application/json",
            "Content-Type": "application/json",
        }

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx
        r = httpx.post(
            self._api,
            json=payload,
            headers=self._headers(),
            auth=self._auth_tuple,
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("result", {})

    def _patch(self, sys_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx
        r = httpx.patch(
            f"{self._api}/{sys_id}",
            json=payload,
            headers=self._headers(),
            auth=self._auth_tuple,
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("result", {})

    def _get(self, sys_id: str) -> dict[str, Any]:
        import httpx
        r = httpx.get(
            f"{self._api}/{sys_id}",
            headers=self._headers(),
            auth=self._auth_tuple,
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("result", {})

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "ServiceNowClient":
        return cls(
            instance_url=cfg.get("instance_url", ""),
            username=cfg.get("username", ""),
            password=cfg.get("password", ""),
            token=cfg.get("token", ""),
            field_map=cfg.get("field_map"),
            risk_map=cfg.get("risk_map"),
            state_map=cfg.get("state_map"),
        )


def _get_raw(row: dict[str, Any], key: str) -> str:
    raw = row.get(key, "")
    if isinstance(raw, dict):
        return str(raw.get("value", raw.get("display_value", "")))
    return str(raw) if raw else ""
