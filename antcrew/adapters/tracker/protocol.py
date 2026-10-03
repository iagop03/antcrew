"""Issue tracker adapter protocol — neutral interface over Jira / ADO / Linear / ServiceNow.

Business logic (ChangePackager, T6 release creation) programs to this interface
and never references Jira-specific fields (``issuetype``, ``customfield_…``) directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass
class IssueFilter:
    """Neutral filter for fetching issues.

    State names use the project's canonical names; the adapter maps them to
    provider-specific values via ``state_map`` in the config.

    Example::

        IssueFilter(state="uat", project="MYPROJ")
        IssueFilter(change_refs=["CR-1234", "CR-1235"])
    """

    state: Optional[str] = None            # logical state name (e.g. "uat", "ready_for_prod")
    project: Optional[str] = None         # project key / board
    change_refs: list[str] = field(default_factory=list)   # filter by CR references in summary/description
    labels: list[str] = field(default_factory=list)
    assignee: Optional[str] = None
    max_results: int = 200


@dataclass
class Issue:
    """Neutral representation of one issue / work item."""

    key: str                           # e.g. PROJ-123
    summary: str
    status: str                        # logical state
    assignee: str = ""
    reporter: str = ""
    description: str = ""
    change_ref: str = ""               # CR reference extracted from summary/description
    labels: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)   # provider-specific raw fields


@runtime_checkable
class IssueTrackerAdapter(Protocol):
    """Pluggable issue-tracker adapter."""

    def fetch_issues(self, filter: IssueFilter) -> list[Issue]:
        """Return issues matching *filter*."""
        ...

    def get_issue(self, key: str) -> Optional[Issue]:
        """Return the issue identified by *key*, or ``None`` if not found."""
        ...

    def update_issue(self, key: str, fields: dict[str, Any]) -> None:
        """Update arbitrary fields on an issue (neutral field names).

        The adapter is responsible for mapping neutral field names to
        provider-specific ones using the configured ``field_map``.
        """
        ...
