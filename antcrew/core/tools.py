"""
Agent tools — callable capabilities injected into agents via a ReAct loop.

Usage::

    from antcrew.core.tools import WebSearchTool, CodeExecutorTool, ReadFileTool
    from antcrew.agents.researcher import ResearcherAgent

    agent = ResearcherAgent(llm, tools=[WebSearchTool(), ReadFileTool()])
    # agent.system_with_tools(...) automatically runs a search-act-observe loop.

Built-in tools
--------------
- WebSearchTool   — DuckDuckGo Instant Answer API (no API key needed)
- CodeExecutorTool — sandboxed Python subprocess, 15 s timeout
- ReadFileTool    — local filesystem read, configurable root and size limit
"""
from __future__ import annotations

import abc
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------

class ToolResult:
    """Structured output from a single tool invocation."""

    def __init__(self, output: str, *, error: Optional[str] = None) -> None:
        self.output = output
        self.error = error
        self.ok = error is None

    def __str__(self) -> str:
        if self.error:
            return f"ERROR: {self.error}"
        return self.output


# ---------------------------------------------------------------------------
# BaseTool
# ---------------------------------------------------------------------------

class BaseTool(abc.ABC):
    """Abstract base class for all agent tools.

    Subclasses must set ``name`` and ``description`` as class attributes and
    implement ``run(input: str) -> ToolResult``.
    """

    name: str
    description: str

    @abc.abstractmethod
    def run(self, input: str) -> ToolResult:
        """Execute the tool and return a :class:`ToolResult`."""

    def schema(self) -> str:
        """One-line description included in the agent system prompt."""
        return f"- {self.name}: {self.description}"


# ---------------------------------------------------------------------------
# Built-in tools
# ---------------------------------------------------------------------------

class WebSearchTool(BaseTool):
    """Search the web using the DuckDuckGo Instant Answer API.

    No API key is required.  Results are limited to the abstract and the
    top *max_results* related topics.
    """

    name = "web_search"
    description = (
        "Search the web for current information. "
        "Input: a plain-text search query. Returns a short summary."
    )

    def __init__(self, *, max_results: int = 3, timeout: float = 10.0) -> None:
        self.max_results = max_results
        self.timeout = timeout

    def run(self, input: str) -> ToolResult:
        try:
            import json as _json
            import urllib.parse
            import urllib.request

            query = urllib.parse.quote_plus(input.strip())
            url = (
                f"https://api.duckduckgo.com/?q={query}"
                "&format=json&no_html=1&skip_disambig=1"
            )
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                data = _json.loads(resp.read().decode())

            parts: list[str] = []
            if data.get("AbstractText"):
                parts.append(data["AbstractText"])
            for topic in data.get("RelatedTopics", [])[: self.max_results]:
                if isinstance(topic, dict) and topic.get("Text"):
                    parts.append(topic["Text"])

            if not parts:
                return ToolResult(f"No results found for: {input!r}")
            return ToolResult("\n\n".join(parts[: self.max_results]))
        except Exception as exc:
            return ToolResult("", error=str(exc))


class CodeExecutorTool(BaseTool):
    """Execute Python code in an isolated subprocess.

    The code runs in a temporary file with a configurable timeout (default
    15 s).  stdout + stderr are captured and returned.
    """

    name = "execute_code"
    description = (
        "Execute Python code and capture its output. "
        "Input: a Python code snippet as a plain string. Timeout: 15 seconds."
    )

    def __init__(self, *, timeout: float = 15.0, max_output: int = 4000) -> None:
        self.timeout = timeout
        self.max_output = max_output

    # Env var prefixes that are safe to propagate into the subprocess.
    # Everything else (API keys, tokens, DB URLs, etc.) is withheld.
    _SAFE_ENV_PREFIXES = (
        "PATH", "HOME", "TMPDIR", "TEMP", "TMP",
        "LANG", "LC_", "PYTHON", "VIRTUAL_ENV",
        # Windows-specific
        "SYSTEMROOT", "SystemRoot", "USERPROFILE",
        "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
    )

    def run(self, input: str) -> ToolResult:
        import os as _os
        if _os.environ.get("ANTCREW_SANDBOX", "").lower() == "required":
            return ToolResult("", error=(
                "Code execution is disabled (ANTCREW_SANDBOX=required). "
                "Use antcrew-engine capabilities for sandboxed execution."
            ))

        # Propagate only safe env vars — omit API keys, tokens, DB URLs, etc.
        safe_env = {
            k: v for k, v in _os.environ.items()
            if any(k == p or k.startswith(p) for p in self._SAFE_ENV_PREFIXES)
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(textwrap.dedent(input))
            tmp = fh.name
        try:
            proc = subprocess.run(
                [sys.executable, tmp],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=safe_env,
            )
            combined = proc.stdout + proc.stderr
            if len(combined) > self.max_output:
                combined = combined[: self.max_output] + "\n[output truncated]"
            if proc.returncode != 0:
                return ToolResult("", error=combined or f"Exit code {proc.returncode}")
            return ToolResult(combined or "(no output)")
        except subprocess.TimeoutExpired:
            return ToolResult("", error=f"Timed out after {self.timeout}s")
        except Exception as exc:
            return ToolResult("", error=str(exc))
        finally:
            Path(tmp).unlink(missing_ok=True)


class ReadFileTool(BaseTool):
    """Read a file from the local filesystem.

    When *root* is set, relative paths are resolved relative to it.
    Output is capped at *max_chars* characters.
    """

    name = "read_file"
    description = (
        "Read a file from the filesystem. "
        "Input: an absolute or relative file path. "
        "Returns the file contents (up to 8 000 characters)."
    )

    def __init__(
        self,
        *,
        root: Optional[str] = None,
        max_chars: int = 8_000,
    ) -> None:
        self.root = Path(root) if root else None
        self.max_chars = max_chars

    def run(self, input: str) -> ToolResult:
        try:
            p = Path(input.strip())
            if self.root and not p.is_absolute():
                p = self.root / p
            if not p.exists():
                return ToolResult("", error=f"File not found: {p}")
            content = p.read_text(encoding="utf-8", errors="replace")
            if len(content) > self.max_chars:
                content = content[: self.max_chars] + "\n[truncated]"
            return ToolResult(content)
        except Exception as exc:
            return ToolResult("", error=str(exc))


class WriteFileTool(BaseTool):
    """Write content to a file on the local filesystem.

    Input format (two parts separated by a ``---`` line)::

        path/to/file.py
        ---
        <file content here>

    When *root* is set, relative paths are resolved relative to it and
    absolute paths that escape *root* are rejected.  Parent directories are
    created automatically when *allow_create_dirs* is True (default).
    """

    name = "write_file"
    description = (
        "Write content to a file. "
        "Input must be two parts separated by a line containing only '---': "
        "first the file path, then the file content. "
        "Example input: 'src/app.py\\n---\\nprint(\"hello\")'."
    )

    def __init__(
        self,
        *,
        root: Optional[str] = None,
        allow_create_dirs: bool = True,
    ) -> None:
        self.root = Path(root).resolve() if root else None
        self.allow_create_dirs = allow_create_dirs

    def _safe_path(self, raw: str) -> Path:
        p = Path(raw.strip())
        if self.root:
            if not p.is_absolute():
                p = self.root / p
            resolved = p.resolve()
            # is_relative_to() is exact; the old startswith() allowed
            # /x/project-evil/ to bypass a root of /x/project.
            if not resolved.is_relative_to(self.root):
                raise ValueError(
                    f"Path '{raw}' escapes the allowed root '{self.root}'"
                )
            return resolved
        # No root configured: reject absolute paths to prevent accidental writes
        # to arbitrary filesystem locations when the tool is used without a scope.
        if p.is_absolute():
            raise ValueError(
                "WriteFileTool has no root configured — absolute paths are not "
                "allowed. Instantiate with root='/your/project' or use a relative path."
            )
        return p.resolve()

    def run(self, input: str) -> ToolResult:
        sep = "\n---\n"
        if sep not in input:
            return ToolResult("", error="Input must be 'path\\n---\\ncontent'.")
        path_part, _, content = input.partition(sep)
        try:
            p = self._safe_path(path_part)
            if self.allow_create_dirs:
                p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return ToolResult(f"Written: {p}")
        except Exception as exc:
            return ToolResult("", error=str(exc))


class ListDirTool(BaseTool):
    """List files in a directory tree.

    Returns a text tree of files, ignoring common noise directories
    (``__pycache__``, ``.git``, ``node_modules``, ``venv``, ``.venv``).
    """

    name = "list_dir"
    description = (
        "List all files in a directory tree. "
        "Input: a directory path (or '.' for the project root). "
        "Returns a newline-delimited list of relative file paths."
    )

    _IGNORE = frozenset({
        "__pycache__", ".git", "node_modules", "venv", ".venv",
        ".mypy_cache", ".ruff_cache", ".pytest_cache", "dist", "build",
        ".eggs", "*.egg-info",
    })

    def __init__(
        self,
        *,
        root: Optional[str] = None,
        max_depth: int = 4,
        max_files: int = 200,
    ) -> None:
        self.root = Path(root).resolve() if root else None
        self.max_depth = max_depth
        self.max_files = max_files

    def run(self, input: str) -> ToolResult:
        try:
            target = Path(input.strip())
            if self.root:
                if not target.is_absolute():
                    target = self.root / target
                target = target.resolve()
            if not target.exists():
                return ToolResult("", error=f"Directory not found: {target}")
            if not target.is_dir():
                return ToolResult("", error=f"Not a directory: {target}")

            lines: list[str] = []
            base = self.root or target

            def _walk(d: Path, depth: int) -> None:
                if depth > self.max_depth or len(lines) >= self.max_files:
                    return
                try:
                    entries = sorted(d.iterdir(), key=lambda e: (e.is_file(), e.name))
                except PermissionError:
                    return
                for entry in entries:
                    if entry.name in self._IGNORE or entry.name.endswith(".egg-info"):
                        continue
                    rel = entry.relative_to(base)
                    if entry.is_dir():
                        lines.append(f"{rel}/")
                        _walk(entry, depth + 1)
                    else:
                        lines.append(str(rel))
                    if len(lines) >= self.max_files:
                        lines.append("... (truncated)")
                        return

            _walk(target, 0)
            return ToolResult("\n".join(lines) if lines else "(empty directory)")
        except Exception as exc:
            return ToolResult("", error=str(exc))
