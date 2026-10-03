"""VCS adapter protocol — neutral interface over SVN / Git / TFS / Perforce.

Business logic (ImpactAnalyzer, ChangePackager) programs to this interface
and never imports SVN or Git concepts directly.  Implementations live in
``svn.py``, ``git.py``, etc., and are selected via config ``vcs: <type>``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class ChangeSet:
    """Neutral representation of one VCS changeset.

    Deliberately avoids SVN's "revision" or Git's "commit hash" terminology —
    consumers receive ``changeset_id`` which maps to whichever underlying
    concept the adapter wraps.
    """

    changeset_id: str
    author: str = ""
    message: str = ""
    timestamp: str = ""          # ISO-8601 string
    change_ref: str = ""         # CR number extracted from commit message
    changed_files: list[str] = field(default_factory=list)


@dataclass
class FileDiff:
    """Raw diff for one file in a changeset."""

    path: str
    diff_text: str
    change_type: str = "modified"  # added | modified | deleted | renamed


@runtime_checkable
class VCSAdapter(Protocol):
    """Pluggable VCS adapter — implement this Protocol for any VCS."""

    def changes_for_ref(self, change_ref: str) -> list[ChangeSet]:
        """Return all changesets whose commit message references *change_ref*.

        Implementations should extract the CR number using the configured
        ``cr_pattern`` regex.  The default pattern is ``r'\\b(CR-\\d+)\\b'``.
        """
        ...

    def diff(self, changeset_id: str) -> list[FileDiff]:
        """Return per-file diffs for a single changeset."""
        ...

    def changed_files(self, changeset_id: str) -> list[str]:
        """Return the list of file paths changed by *changeset_id*."""
        ...
