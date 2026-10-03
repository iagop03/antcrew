"""Pluggable adapter layer for external tools (VCS, issue trackers).

Config-driven routing — set ``vcs: svn`` or ``tracker: jira`` in your YAML.
Adapters expose neutral Protocol interfaces so business logic never references
SVN-specific or Jira-specific concepts directly.
"""
from antcrew.adapters.vcs import VCSAdapter, get_vcs_adapter
from antcrew.adapters.tracker import IssueTrackerAdapter, get_tracker_adapter

__all__ = [
    "VCSAdapter",
    "get_vcs_adapter",
    "IssueTrackerAdapter",
    "get_tracker_adapter",
]
