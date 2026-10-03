"""Jira issue-tracker adapter — implements IssueTrackerAdapter over Jira REST API v3.

Configuration (YAML)::

    tracker:
      type: jira
      url: https://yourorg.atlassian.net
      email: svc_antcrew@yourorg.com
      api_token: ${JIRA_API_TOKEN}
      project_key: MYPROJ
      # State mapping: neutral_name -> Jira status name
      state_map:
        uat: "UAT"
        ready_for_prod: "Request to Production"
        in_progress: "In Progress"
        done: "Done"
      # CR pattern to extract from issue summary/description
      cr_pattern: 'CR-\\d+'

Usage::

    from antcrew.adapters.tracker.jira import JiraAdapter
    from antcrew.adapters.tracker.protocol import IssueFilter

    adapter = JiraAdapter(url="https://org.atlassian.net", email="e@org.com",
                          api_token="...", project_key="PROJ")
    issues = adapter.fetch_issues(IssueFilter(state="uat"))
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from antcrew.adapters.tracker.protocol import Issue, IssueFilter

log = logging.getLogger(__name__)

_DEFAULT_STATE_MAP: dict[str, str] = {
    "open":             "To Do",
    "in_progress":      "In Progress",
    "uat":              "UAT",
    "ready_for_prod":   "Request to Production",
    "done":             "Done",
    "closed":           "Done",
}


class JiraAdapter:
    """IssueTrackerAdapter implementation for Jira Cloud / Server REST API v3."""

    def __init__(
        self,
        url: str,
        email: str,
        api_token: str,
        project_key: str,
        *,
        state_map: Optional[dict[str, str]] = None,
        cr_pattern: str = r"\b(CR-\d+)\b",
        field_map: Optional[dict[str, str]] = None,
    ) -> None:
        self._base = url.rstrip("/")
        self._auth = (email, api_token)
        self._project_key = project_key
        self._state_map: dict[str, str] = {**_DEFAULT_STATE_MAP, **(state_map or {})}
        self._cr_re = re.compile(cr_pattern)
        self._field_map = field_map or {}

    # ------------------------------------------------------------------
    # IssueTrackerAdapter protocol
    # ------------------------------------------------------------------

    def fetch_issues(self, filter: IssueFilter) -> list[Issue]:
        """Return Jira issues matching *filter*."""
        jql_parts: list[str] = [f'project = "{self._project_key}"']

        if filter.state:
            jira_state = self._state_map.get(filter.state, filter.state)
            jql_parts.append(f'status = "{jira_state}"')

        if filter.change_refs:
            cr_clauses = " OR ".join(
                f'(summary ~ "{cr}" OR description ~ "{cr}")'
                for cr in filter.change_refs
            )
            jql_parts.append(f"({cr_clauses})")

        if filter.labels:
            for label in filter.labels:
                jql_parts.append(f'labels = "{label}"')

        if filter.assignee:
            jql_parts.append(f'assignee = "{filter.assignee}"')

        jql = " AND ".join(jql_parts) + " ORDER BY created DESC"
        log.debug("jira fetch_issues JQL: %s", jql)

        data = self._get("search", params={
            "jql": jql,
            "maxResults": filter.max_results,
            "fields": "summary,status,assignee,reporter,description,labels",
        })
        return [self._to_issue(raw) for raw in data.get("issues", [])]

    def get_issue(self, key: str) -> Optional[Issue]:
        """Return a single Jira issue by key, or None."""
        try:
            raw = self._get(f"issue/{key}")
            return self._to_issue(raw)
        except Exception as exc:
            log.warning("jira get_issue %s failed: %s", key, exc)
            return None

    def update_issue(self, key: str, fields: dict[str, Any]) -> None:
        """Update issue fields using the neutral field names in *fields*.

        Neutral names are mapped to Jira fields via ``field_map`` in config.
        Unmapped names are passed through as-is.
        """
        jira_fields: dict[str, Any] = {}
        for neutral, value in fields.items():
            jira_key = self._field_map.get(neutral, neutral)
            jira_fields[jira_key] = value
        self._put(f"issue/{key}", {"fields": jira_fields})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _to_issue(self, raw: dict) -> Issue:
        key = raw.get("key", "")
        f = raw.get("fields") or {}
        summary = f.get("summary") or ""
        status = (f.get("status") or {}).get("name") or ""
        assignee_data = f.get("assignee") or {}
        reporter_data = f.get("reporter") or {}
        description_data = f.get("description")
        description = self._extract_description(description_data)
        labels = f.get("labels") or []
        # extract CR reference from summary + description
        cr_text = summary + " " + description
        cr_matches = self._cr_re.findall(cr_text)
        return Issue(
            key=key,
            summary=summary,
            status=self._reverse_state(status),
            assignee=(assignee_data.get("displayName") or assignee_data.get("emailAddress") or ""),
            reporter=(reporter_data.get("displayName") or reporter_data.get("emailAddress") or ""),
            description=description,
            change_ref=cr_matches[0] if cr_matches else "",
            labels=labels,
            extra=f,
        )

    def _reverse_state(self, jira_status: str) -> str:
        """Map Jira status name → neutral state name (best-effort)."""
        for neutral, jira in self._state_map.items():
            if jira.lower() == jira_status.lower():
                return neutral
        return jira_status.lower().replace(" ", "_")

    @staticmethod
    def _extract_description(desc: Any) -> str:
        """Extract plain text from ADF or plain string description."""
        if not desc:
            return ""
        if isinstance(desc, str):
            return desc
        if isinstance(desc, dict):
            # Atlassian Document Format — walk nodes and collect text
            texts: list[str] = []
            def _walk(node: dict) -> None:
                if node.get("type") == "text":
                    texts.append(node.get("text") or "")
                for child in node.get("content") or []:
                    _walk(child)
            _walk(desc)
            return " ".join(t for t in texts if t)
        return str(desc)

    def _get(self, endpoint: str, params: Optional[dict] = None) -> dict:
        import httpx
        r = httpx.get(
            f"{self._base}/rest/api/3/{endpoint}",
            params=params,
            auth=self._auth,
            headers={"Accept": "application/json"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def _put(self, endpoint: str, payload: dict) -> None:
        import httpx
        r = httpx.put(
            f"{self._base}/rest/api/3/{endpoint}",
            json=payload,
            auth=self._auth,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30,
        )
        r.raise_for_status()


def from_config(cfg: dict) -> "JiraAdapter":
    """Create a JiraAdapter from a ``tracker:`` config dict."""
    return JiraAdapter(
        url=str(cfg["url"]),
        email=str(cfg.get("email") or os.environ.get("JIRA_EMAIL", "")),
        api_token=str(cfg.get("api_token") or os.environ.get("JIRA_API_TOKEN", "")),
        project_key=str(cfg["project_key"]),
        state_map=cfg.get("state_map"),
        cr_pattern=str(cfg.get("cr_pattern", r"\b(CR-\d+)\b")),
        field_map=cfg.get("field_map"),
    )
