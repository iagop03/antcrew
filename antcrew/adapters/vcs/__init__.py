"""VCS adapters — SVN, Git (TFS / Perforce can be added here).

Select via config::

    vcs:
      type: svn          # or git
      url: https://...   # SVN
      repo_path: /path   # Git
      cr_pattern: 'CR-\\d+'
"""
from __future__ import annotations

from antcrew.adapters.vcs.protocol import ChangeSet, FileDiff, VCSAdapter


def get_vcs_adapter(cfg: dict) -> VCSAdapter:
    """Instantiate the right VCSAdapter from a ``vcs:`` config block.

    Supported types: ``svn``, ``git``.

    Example config dict::

        {"type": "svn", "url": "https://svn.example.com/repos/myproject"}
        {"type": "git", "repo_path": "/srv/myrepo", "cr_pattern": "CR-\\\\d+"}
    """
    vcs_type = str(cfg.get("type", "git")).lower()
    cr_pattern = str(cfg.get("cr_pattern", r"\b(CR-\d+)\b"))

    if vcs_type == "svn":
        from antcrew.adapters.vcs.svn import SVNAdapter
        return SVNAdapter(
            url=str(cfg["url"]),
            username=cfg.get("username"),
            password=cfg.get("password"),
            cr_pattern=cr_pattern,
        )

    if vcs_type == "git":
        from antcrew.adapters.vcs.git import GitAdapter
        return GitAdapter(
            repo_path=cfg.get("repo_path"),
            cr_pattern=cr_pattern,
        )

    raise ValueError(
        f"Unknown VCS adapter type: {vcs_type!r}. Supported: svn, git."
    )


__all__ = ["VCSAdapter", "ChangeSet", "FileDiff", "get_vcs_adapter"]
