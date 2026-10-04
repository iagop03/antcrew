"""SVN VCS adapter — wraps ``svn log`` and ``svn diff`` over subprocess.

Configuration (YAML)::

    vcs:
      type: svn
      url: https://svn.example.com/repos/myproject
      username: svc_antcrew          # optional; falls back to SVN_USERNAME
      password: secret               # optional; falls back to SVN_PASSWORD
      cr_pattern: 'CR-\\d+'         # regex to extract CR number from log message

Usage::

    from antcrew.adapters.vcs.svn import SVNAdapter
    adapter = SVNAdapter(url="https://svn.example.com/repos/myproject")
    changesets = adapter.changes_for_ref("CR-1234")
"""
from __future__ import annotations

import os
import re
import subprocess
import xml.etree.ElementTree as ET
from typing import Optional

from antcrew.adapters.vcs.protocol import ChangeSet, FileDiff

_DEFAULT_CR_PATTERN = r"\b(CR-\d+)\b"


class SVNAdapter:
    """VCS adapter for Subversion repositories."""

    def __init__(
        self,
        url: str,
        *,
        username: Optional[str] = None,
        password: Optional[str] = None,
        cr_pattern: str = _DEFAULT_CR_PATTERN,
    ) -> None:
        self._url = url.rstrip("/")
        self._username = username or os.environ.get("SVN_USERNAME", "")
        self._password = password or os.environ.get("SVN_PASSWORD", "")
        self._cr_re = re.compile(cr_pattern)

    # ------------------------------------------------------------------
    # VCSAdapter protocol
    # ------------------------------------------------------------------

    def changes_for_ref(self, change_ref: str) -> list[ChangeSet]:
        """Return changesets whose log message contains *change_ref*."""
        all_sets = self._fetch_log()
        return [cs for cs in all_sets if change_ref in cs.change_ref or change_ref in cs.message]

    def diff(self, changeset_id: str) -> list[FileDiff]:
        """Return per-file diffs for SVN revision *changeset_id*."""
        rev = changeset_id.lstrip("r")
        prev = str(int(rev) - 1)
        raw = self._svn("diff", f"-r{prev}:{rev}", self._url)
        return _parse_unified_diff(raw, default_changeset=changeset_id)

    def changed_files(self, changeset_id: str) -> list[str]:
        """Return paths changed in SVN revision *changeset_id*."""
        rev = changeset_id.lstrip("r")
        raw = self._svn("diff", "--summarize", "--xml", f"-r{str(int(rev) - 1)}:{rev}", self._url)
        try:
            root = ET.fromstring(raw)  # nosec B314 — XML from local svn subprocess, not user input
            return [p.text or "" for p in root.findall(".//path") if p.text]
        except ET.ParseError:
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            return [ln.split()[-1] for ln in lines if len(ln.split()) >= 2]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_log(self, limit: int = 1000) -> list[ChangeSet]:
        raw = self._svn("log", "--xml", f"--limit={limit}", self._url)
        try:
            root = ET.fromstring(raw)  # nosec B314 — XML from local svn subprocess, not user input
        except ET.ParseError:
            return []
        result: list[ChangeSet] = []
        for entry in root.findall("logentry"):
            rev = entry.get("revision", "0")
            author = (entry.findtext("author") or "").strip()
            date = (entry.findtext("date") or "").strip()
            msg = (entry.findtext("msg") or "").strip()
            cr_matches = self._cr_re.findall(msg)
            cr_ref = cr_matches[0] if cr_matches else ""
            result.append(ChangeSet(
                changeset_id=rev,
                author=author,
                message=msg,
                timestamp=date,
                change_ref=cr_ref,
            ))
        return result

    def _svn(self, *args: str) -> str:
        cmd = ["svn", *args]
        if self._username:
            cmd += ["--username", self._username, "--password", self._password,
                    "--non-interactive", "--no-auth-cache"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"svn command failed: {result.stderr.strip()}")
        return result.stdout


def _parse_unified_diff(raw: str, *, default_changeset: str = "") -> list[FileDiff]:
    """Parse unified diff output into FileDiff objects (best-effort)."""
    diffs: list[FileDiff] = []
    current_path = ""
    current_lines: list[str] = []
    for line in raw.splitlines(keepends=True):
        if line.startswith("Index: "):
            if current_path:
                diffs.append(FileDiff(path=current_path, diff_text="".join(current_lines)))
            current_path = line[len("Index: "):].strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_path:
        diffs.append(FileDiff(path=current_path, diff_text="".join(current_lines)))
    return diffs
