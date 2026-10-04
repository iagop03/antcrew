"""Pluggable adapter layer for external tools (VCS, issue trackers, ServiceNow).

Config-driven routing — set ``vcs: svn``, ``tracker: jira``, or
``servicenow: {instance_url: ...}`` in your YAML. Adapters expose neutral
Protocol interfaces so business logic never references SVN/Jira/ServiceNow
concepts directly.
"""
from antcrew.adapters.servicenow import ServiceNowClient, get_servicenow_adapter
from antcrew.adapters.tracker import IssueTrackerAdapter, get_tracker_adapter
from antcrew.adapters.vcs import VCSAdapter, get_vcs_adapter

__all__ = [
    "VCSAdapter",
    "get_vcs_adapter",
    "IssueTrackerAdapter",
    "get_tracker_adapter",
    "ServiceNowClient",
    "get_servicenow_adapter",
]
