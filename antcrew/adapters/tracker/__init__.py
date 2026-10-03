"""Issue-tracker adapters — Jira (Azure DevOps, Linear, ServiceNow can be added here).

Select via config::

    tracker:
      type: jira          # or ado, linear, servicenow (future)
      url: https://...
      project_key: MYPROJ
      state_map:
        uat: "UAT"
"""
from __future__ import annotations

from antcrew.adapters.tracker.protocol import Issue, IssueFilter, IssueTrackerAdapter


def get_tracker_adapter(cfg: dict) -> IssueTrackerAdapter:
    """Instantiate the right IssueTrackerAdapter from a ``tracker:`` config block.

    Currently supported: ``jira``.  Raises ``ValueError`` for unknown types.

    Example config dict::

        {
            "type": "jira",
            "url": "https://org.atlassian.net",
            "email": "svc@org.com",
            "api_token": "...",
            "project_key": "MYPROJ",
            "state_map": {"uat": "UAT", "ready_for_prod": "Request to Production"},
        }
    """
    tracker_type = str(cfg.get("type", "jira")).lower()

    if tracker_type == "jira":
        from antcrew.adapters.tracker.jira import from_config
        return from_config(cfg)

    raise ValueError(
        f"Unknown issue-tracker adapter type: {tracker_type!r}. Supported: jira."
    )


__all__ = ["IssueTrackerAdapter", "Issue", "IssueFilter", "get_tracker_adapter"]
