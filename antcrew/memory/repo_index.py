"""RAG over a local repository — index source files and search by semantic similarity.

Default backend: InMemoryMemory (Jaccard, no deps).
For embedding-based search pass memory=ChromaMemory(...).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterator, Optional

from antcrew.memory.store import BaseMemory, InMemoryMemory

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_LANG_MAP: dict[str, str] = {
    ".py":   "python",
    ".ts":   "typescript",  ".tsx": "typescript",
    ".js":   "javascript",  ".jsx": "javascript",  ".mjs": "javascript",
    ".go":   "go",
    ".java": "java",
    ".rs":   "rust",
    ".rb":   "ruby",
    ".cs":   "csharp",
    ".cpp":  "cpp",  ".cc": "cpp",  ".cxx": "cpp",
    ".c":    "c",    ".h":  "c",
    ".php":  "php",
    ".kt":   "kotlin",
    ".swift":"swift",
    ".yaml": "yaml",  ".yml": "yaml",
    ".toml": "toml",
    ".sql":  "sql",
    ".sh":   "bash",
    ".md":   "markdown",
}

_INCLUDE_EXTENSIONS = frozenset(_LANG_MAP)

_EXCLUDE_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", ".pytest_cache", ".mypy_cache",
    "venv", ".venv", "env", ".env",
    "dist", "build", ".next", ".nuxt", ".svelte-kit",
    "coverage", ".coverage", "htmlcov",
    "target",           # Rust / Maven
    "vendor",           # Go / PHP
    "site-packages", "site_packages",
    ".cargo", ".gradle",
})

_MAX_FILE_BYTES = 200_000  # skip files larger than 200 KB

# ---------------------------------------------------------------------------
# Chunkers
# ---------------------------------------------------------------------------

# Top-level Python definitions
_PY_SPLIT = re.compile(r"^(?:async\s+def\s|def\s|class\s)", re.MULTILINE)

# Top-level TypeScript / JavaScript definitions
_TS_SPLIT = re.compile(
    r"^(?:export\s+(?:default\s+)?(?:async\s+)?(?:function|class|interface|type|const|let|enum)\b"
    r"|(?:async\s+)?function\b|class\b|interface\b)",
    re.MULTILINE,
)


def _detect_language(path: Path) -> str:
    return _LANG_MAP.get(path.suffix.lower(), "")


def _chunk_fixed(text: str, max_size: int, overlap: int = 80) -> list[str]:
    """Hard-split text into overlapping chunks of max_size characters."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + max_size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def _chunk_by_pattern(content: str, pattern: re.Pattern, max_size: int) -> list[str]:
    """Split at positions where pattern matches (top-level definitions)."""
    splits = [m.start() for m in pattern.finditer(content)]

    if not splits:
        return _chunk_paragraphs(content, max_size)

    boundaries = splits + [len(content)]
    chunks: list[str] = []

    # Preamble: imports, module docstring, etc.
    if splits[0] > 0:
        pre = content[: splits[0]].strip()
        if pre:
            chunks.extend(_chunk_fixed(pre, max_size) if len(pre) > max_size else [pre])

    # Each top-level definition
    for i, start in enumerate(splits):
        end = boundaries[i + 1]
        chunk = content[start:end].strip()
        if chunk:
            chunks.extend(_chunk_fixed(chunk, max_size) if len(chunk) > max_size else [chunk])

    return [c for c in chunks if c.strip()]


def _chunk_paragraphs(content: str, max_size: int) -> list[str]:
    """Language-agnostic split: blank-line boundaries, merge small blocks."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", content) if b.strip()]
    chunks: list[str] = []
    current = ""

    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= max_size:
            current = candidate
        else:
            if current:
                chunks.append(current)
            if len(block) > max_size:
                chunks.extend(_chunk_fixed(block, max_size))
                current = ""
            else:
                current = block

    if current.strip():
        chunks.append(current)

    return [c for c in chunks if c.strip()]


def _chunk_file(content: str, language: str, max_size: int) -> list[str]:
    if language == "python":
        return _chunk_by_pattern(content, _PY_SPLIT, max_size)
    if language in ("typescript", "javascript"):
        return _chunk_by_pattern(content, _TS_SPLIT, max_size)
    return _chunk_paragraphs(content, max_size)


# ---------------------------------------------------------------------------
# RepoIndex
# ---------------------------------------------------------------------------

class RepoIndex:
    """Index a local repository for RAG — semantic search over source files.

    By default uses InMemoryMemory with Jaccard word-overlap similarity.
    For embedding-based search (better recall), pass memory=ChromaMemory(...).

    Usage:
        idx = RepoIndex("/path/to/project")
        idx.build()                              # walk + chunk + index
        context = idx.search("auth JWT tokens")  # formatted string for LLM

    With ChromaDB:
        from antcrew import ChromaMemory
        idx = RepoIndex("/path/to/project", memory=ChromaMemory(collection="myproject"))
        idx.build()

    Via teams (auto-builds on construction):
        team = DevTeam(model=llm, repo_path="/path/to/project")
    """

    def __init__(
        self,
        repo_path: str,
        *,
        memory: Optional[BaseMemory] = None,
        extensions: Optional[frozenset] = None,
        exclude_dirs: Optional[frozenset] = None,
        max_chunk_size: int = 1_500,
        max_file_bytes: int = _MAX_FILE_BYTES,
    ) -> None:
        self._path = Path(repo_path).resolve()
        self._memory = memory or InMemoryMemory()
        self._extensions = extensions or _INCLUDE_EXTENSIONS
        self._exclude_dirs = exclude_dirs or _EXCLUDE_DIRS
        self._max_chunk = max_chunk_size
        self._max_file = max_file_bytes

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self) -> int:
        """Walk the repository, chunk source files, and index them.

        Clears any previous index before building.
        Returns the total number of chunks stored.
        """
        self._memory.clear()
        count = 0

        for file_path in self._walk():
            try:
                raw = file_path.read_bytes()
            except OSError:
                continue

            if len(raw) > self._max_file:
                continue

            content = raw.decode("utf-8", errors="ignore")
            if not content.strip():
                continue

            lang = _detect_language(file_path)
            rel  = file_path.relative_to(self._path).as_posix()

            for i, chunk in enumerate(_chunk_file(content, lang, self._max_chunk)):
                if not chunk.strip():
                    continue
                self._memory.add(
                    chunk,
                    {"file_path": rel, "language": lang, "chunk_index": i},
                )
                count += 1

        return count

    def search(self, query: str, *, n: int = 5) -> str:
        """Return formatted code context ready to append to an LLM system prompt.

        Returns an empty string if the query is empty or nothing was indexed.
        """
        if not query.strip() or self._memory.count() == 0:
            return ""

        results = self._memory.search(query, n=n)
        if not results:
            return ""

        lines = ["\n\nRelevant existing code in this repository:"]
        seen: set[str] = set()

        for r in results:
            fp   = r.metadata.get("file_path", "unknown")
            lang = r.metadata.get("language", "")
            # Deduplicate identical chunks (same file + same leading text)
            key  = f"{fp}::{r.text[:60]}"
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"\n### {fp}")
            lines.append(f"```{lang}\n{r.text}\n```")

        return "\n".join(lines) + "\n"

    def count(self) -> int:
        """Total number of indexed chunks."""
        return self._memory.count()

    def clear(self) -> None:
        """Remove all indexed chunks."""
        self._memory.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _walk(self) -> Iterator[Path]:
        for root, dirs, files in os.walk(self._path):
            # Prune excluded dirs in-place — prevents os.walk from descending
            dirs[:] = sorted(
                d for d in dirs
                if d not in self._exclude_dirs and not d.startswith(".")
            )
            root_path = Path(root)
            for fname in sorted(files):
                fp = root_path / fname
                if fp.suffix.lower() in self._extensions:
                    yield fp
