"""Git VCS adapter — wraps ``git log`` and ``git diff`` via subprocess or GitPython.

Configuration (YAML)::

    vcs:
      type: git
      repo_path: /path/to/repo     # local clone; defaults to cwd
      remote: origin               # optional; for future remote operations
      cr_pattern: 'CR-\\d+'       # regex to extract CR number from commit message

Usage::

    from antcrew.adapters.vcs.git import GitAdapter
    adapter = GitAdapter(repo_path="/srv/myrepo")
    changesets = adapter.changes_for_ref("CR-1234")
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from antcrew.adapters.vcs.protocol import ChangeSet, FileDiff

_DEFAULT_CR_PATTERN = r"\b(CR-\d+)\b"
_LOG_SEP = "\x00"           # NUL-separated fields in git log format
_RECORD_SEP = "\x1e"        # record separator between commits


class GitAdapter:
    """VCS adapter for Git repositories."""

    def __init__(
        self,
        repo_path: Optional[str] = None,
        *,
        cr_pattern: str = _DEFAULT_CR_PATTERN,
    ) -> None:
        self._repo = Path(repo_path).resolve() if repo_path else Path.cwd()
        self._cr_re = re.compile(cr_pattern)

    # ------------------------------------------------------------------
    # VCSAdapter protocol
    # ------------------------------------------------------------------

    def changes_for_ref(self, change_ref: str) -> list[ChangeSet]:
        """Return commits whose message contains *change_ref*."""
        raw = self._git(
            "log", f"--grep={re.escape(change_ref)}",
            f"--format={_RECORD_SEP}%H{_LOG_SEP}%an{_LOG_SEP}%aI{_LOG_SEP}%s%n%b",
            "--name-only",
        )
        return self._parse_log(raw)

    def diff(self, changeset_id: str) -> list[FileDiff]:
        """Return per-file diffs for commit *changeset_id*."""
        raw = self._git("diff", f"{changeset_id}^!", "--unified=3")
        return _parse_git_diff(raw)

    def changed_files(self, changeset_id: str) -> list[str]:
        """Return paths changed in commit *changeset_id*."""
        raw = self._git("diff-tree", "--no-commit-id", "-r", "--name-only", changeset_id)
        return [ln.strip() for ln in raw.splitlines() if ln.strip()]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_log(self, raw: str) -> list[ChangeSet]:
        result: list[ChangeSet] = []
        for block in raw.split(_RECORD_SEP):
            block = block.strip()
            if not block:
                continue
            lines = block.splitlines()
            if not lines:
                continue
            fields = lines[0].split(_LOG_SEP, 3)
            if len(fields) < 4:
                continue
            sha, author, ts, subject = fields
            body_lines = lines[1:]
            # changed files appear after an empty line following the message
            file_start = next(
                (i for i, ln in enumerate(body_lines) if not ln.strip()), len(body_lines)
            )
            files = [ln.strip() for ln in body_lines[file_start:] if ln.strip()]
            message = subject + ("\n" + "\n".join(body_lines[:file_start]) if body_lines[:file_start] else "")
            cr_matches = self._cr_re.findall(message)
            result.append(ChangeSet(
                changeset_id=sha.strip(),
                author=author.strip(),
                message=message.strip(),
                timestamp=ts.strip(),
                change_ref=cr_matches[0] if cr_matches else "",
                changed_files=files,
            ))
        return result

    def _git(self, *args: str) -> str:
        cmd = ["git", "-C", str(self._repo), *args]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"git command failed: {result.stderr.strip()}")
        return result.stdout


def _parse_git_diff(raw: str) -> list[FileDiff]:
    """Parse ``git diff`` output into FileDiff objects (best-effort)."""
    diffs: list[FileDiff] = []
    current_path = ""
    current_lines: list[str] = []
    change_type = "modified"

    for line in raw.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current_path:
                diffs.append(FileDiff(path=current_path, diff_text="".join(current_lines),
                                      change_type=change_type))
            current_path = ""
            current_lines = [line]
            change_type = "modified"
        elif line.startswith("--- /dev/null"):
            change_type = "added"
            current_lines.append(line)
        elif line.startswith("+++ /dev/null"):
            change_type = "deleted"
            current_lines.append(line)
        elif line.startswith("+++ b/") and not current_path:
            current_path = line[6:].strip()
            current_lines.append(line)
        else:
            current_lines.append(line)

    if current_path:
        diffs.append(FileDiff(path=current_path, diff_text="".join(current_lines),
                              change_type=change_type))
    return diffs
