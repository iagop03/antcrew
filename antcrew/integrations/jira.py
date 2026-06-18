"""
Jira integration — syncs Ticket objects from PMAgent to a Jira project.

Requires: httpx (core dependency, already installed).

Usage:
    from antcrew.integrations.jira import JiraIntegration

    jira = JiraIntegration(
        url="https://yourorg.atlassian.net",
        email="you@yourorg.com",
        api_token=os.environ["JIRA_API_TOKEN"],
        project_key="DEV",
    )
    state = team.run("Build auth module")
    pairs = jira.sync_tickets(state["tickets"])
    for ticket, key in pairs:
        print(f"{ticket.id} → {key}")
"""
from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from antcrew.core.artifacts import Ticket

_PRIORITY_MAP = {
    "low":      "Low",
    "medium":   "Medium",
    "high":     "High",
    "critical": "Highest",
}

_ISSUE_TYPE = "Story"


def _adf_doc(description: str, acceptance_criteria: list[str]) -> dict:
    """Build Atlassian Document Format body from plain text + ACs."""
    content: list[dict] = [
        {
            "type": "paragraph",
            "content": [{"type": "text", "text": description}],
        }
    ]
    if acceptance_criteria:
        content.append({
            "type": "heading",
            "attrs": {"level": 3},
            "content": [{"type": "text", "text": "Acceptance Criteria"}],
        })
        content.append({
            "type": "bulletList",
            "content": [
                {
                    "type": "listItem",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": ac}],
                        }
                    ],
                }
                for ac in acceptance_criteria
            ],
        })
    return {"type": "doc", "version": 1, "content": content}


class JiraIntegration:
    """
    Pushes Ticket objects (from PMAgent) to a Jira project via the Jira REST API v3.

    Authentication: Basic Auth using an Atlassian API token.
    Generate one at: https://id.atlassian.com/manage-profile/security/api-tokens
    """

    def __init__(
        self,
        url: str,
        email: str,
        api_token: str,
        project_key: str,
        issue_type: str = _ISSUE_TYPE,
    ) -> None:
        try:
            import httpx  # noqa: F401  (verify it's available)
        except ImportError as exc:
            raise ImportError("httpx is required. pip install httpx") from exc

        self._base = url.rstrip("/")
        self._auth = (email, api_token)
        self._project_key = project_key
        self._issue_type = issue_type

    # ------------------------------------------------------------------
    # Low-level HTTP
    # ------------------------------------------------------------------

    def _post(self, endpoint: str, payload: dict) -> dict:
        import httpx
        r = httpx.post(
            f"{self._base}/rest/api/3/{endpoint}",
            json=payload,
            auth=self._auth,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def _get(self, endpoint: str) -> dict:
        import httpx
        r = httpx.get(
            f"{self._base}/rest/api/3/{endpoint}",
            auth=self._auth,
            headers={"Accept": "application/json"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_issue(self, ticket: "Ticket") -> str:
        """Create a single Jira issue. Returns the issue key, e.g. ``DEV-42``."""
        payload: dict = {
            "fields": {
                "project":     {"key": self._project_key},
                "summary":     ticket.title,
                "description": _adf_doc(ticket.description, ticket.acceptance_criteria),
                "issuetype":   {"name": self._issue_type},
                "priority":    {"name": _PRIORITY_MAP.get(ticket.priority.value, "Medium")},
            }
        }
        result = self._post("issue", payload)
        return result["key"]

    def sync_tickets(self, tickets: list["Ticket"]) -> list[tuple["Ticket", str]]:
        """
        Create a Jira issue for every ticket in the list.

        Returns a list of ``(ticket, jira_key)`` pairs in the same order.
        """
        return [(ticket, self.create_issue(ticket)) for ticket in tickets]

    def get_project_issues(self) -> list[dict]:
        """Return open issues for the configured project (raw Jira dicts)."""
        jql = f"project = {self._project_key} AND statusCategory != Done ORDER BY created DESC"
        data = self._get(f"search?jql={jql}&maxResults=50")
        return data.get("issues", [])
