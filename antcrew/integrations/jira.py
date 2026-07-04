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
        print(f"{ticket.id} -> {key}")

sync_tickets() is idempotent: re-running the same pipeline updates existing
Jira issues rather than creating duplicates. It uses the deterministic
TICKET-<sha8> IDs (antcrew >= 0.14.0) stored as Jira labels to look up
previously created issues.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from antcrew.core.artifacts import Ticket

log = logging.getLogger(__name__)

_PRIORITY_MAP = {
    "low":      "Low",
    "medium":   "Medium",
    "high":     "High",
    "critical": "Highest",
}

_ISSUE_TYPE = "Story"

_ANTCREW_LABEL_PREFIX = "antcrew:"


def _antcrew_label(ticket_id: str) -> str:
    return f"{_ANTCREW_LABEL_PREFIX}{ticket_id}"


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

    sync_tickets() is idempotent when tickets carry stable TICKET-<sha8> IDs
    (antcrew >= 0.14.0): existing Jira issues are updated, not duplicated.
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
            import httpx  # noqa: F401
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

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _issue_fields(self, ticket: "Ticket") -> dict:
        return {
            "project":     {"key": self._project_key},
            "summary":     ticket.title,
            "description": _adf_doc(ticket.description, ticket.acceptance_criteria),
            "issuetype":   {"name": self._issue_type},
            "priority":    {"name": _PRIORITY_MAP.get(ticket.priority.value, "Medium")},
            "labels":      [_antcrew_label(ticket.id)],
        }

    def _find_by_antcrew_id(self, ticket_id: str) -> Optional[str]:
        """Return the Jira issue key for a ticket, or None if not found."""
        label = _antcrew_label(ticket_id)
        jql = (
            f'project = "{self._project_key}" '
            f'AND labels = "{label}" '
            f'ORDER BY created DESC'
        )
        try:
            data = self._get("search", params={"jql": jql, "maxResults": 1, "fields": "summary"})
            issues = data.get("issues", [])
            return issues[0]["key"] if issues else None
        except Exception as exc:
            log.warning("jira: label search failed for %s: %s", ticket_id, exc)
            return None

    def _update_issue(self, key: str, ticket: "Ticket") -> None:
        """Update summary, description, priority, and labels on an existing issue."""
        self._put(f"issue/{key}", {"fields": self._issue_fields(ticket)})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_issue(self, ticket: "Ticket") -> str:
        """Create a Jira issue for a ticket. Returns the issue key, e.g. ``DEV-42``.

        Stamps the antcrew ticket ID as a label (``antcrew:TICKET-<sha8>``) so
        future calls to sync_tickets() can detect and update this issue instead
        of creating a duplicate.
        """
        result = self._post("issue", {"fields": self._issue_fields(ticket)})
        return result["key"]

    def upsert_issue(self, ticket: "Ticket") -> tuple[str, bool]:
        """Create or update a Jira issue for a ticket.

        Returns ``(jira_key, created)`` where ``created`` is True if a new
        issue was created, False if an existing one was updated.

        Requires a stable ticket ID (TICKET-<sha8>) to detect existing issues.
        Falls back to create if the lookup fails.
        """
        existing_key = self._find_by_antcrew_id(ticket.id)
        if existing_key:
            self._update_issue(existing_key, ticket)
            return existing_key, False
        key = self.create_issue(ticket)
        return key, True

    def sync_tickets(self, tickets: list["Ticket"]) -> list[tuple["Ticket", str]]:
        """Upsert a Jira issue for every ticket in the list.

        Re-running the same pipeline updates existing issues instead of
        creating duplicates, provided tickets carry stable TICKET-<sha8> IDs
        (antcrew >= 0.14.0).

        Returns a list of ``(ticket, jira_key)`` pairs in the same order.
        """
        pairs = []
        for ticket in tickets:
            existing = self._find_by_antcrew_id(ticket.id)
            if existing:
                self._update_issue(existing, ticket)
                log.debug("jira: updated %s -> %s", ticket.id, existing)
                pairs.append((ticket, existing))
            else:
                key = self.create_issue(ticket)
                log.debug("jira: created %s -> %s", ticket.id, key)
                pairs.append((ticket, key))
        return pairs

    def get_project_issues(self) -> list[dict]:
        """Return open issues for the configured project (raw Jira dicts)."""
        jql = f"project = {self._project_key} AND statusCategory != Done ORDER BY created DESC"
        data = self._get("search", params={"jql": jql, "maxResults": 50})
        return data.get("issues", [])
