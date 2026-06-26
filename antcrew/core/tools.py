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

    def run(self, input: str) -> ToolResult:
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
