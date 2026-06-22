from __future__ import annotations

import json
from pathlib import Path

from antcrew.core.agent import BaseAgent, _json_loads, _strip_fences
from antcrew.core.artifacts import CodebaseAnalysis
from antcrew.core.state import TeamState

_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "coverage", ".cache",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
}
_KEY_FILES = [
    "README.md", "readme.md", "README.rst",
    "package.json", "requirements.txt", "pyproject.toml",
    "setup.py", "setup.cfg", "tsconfig.json",
    "vite.config.ts", "vite.config.js",
    "docker-compose.yml", "docker-compose.yaml",
    ".env.example", "Makefile",
]
_MAX_FILE_CHARS = 3_000
_MAX_TREE_LINES = 200

_SYSTEM = """\
You are a Senior Software Architect analyzing an existing codebase before continuing development.
Given the directory tree and key file contents, produce a structured JSON analysis.

Respond ONLY with a valid JSON object (no markdown fences, no prose):
{
  "tech_stack": ["Python 3.12", "FastAPI", "React 18", "TypeScript 5"],
  "existing_modules": ["src/auth", "src/models/client.py", "frontend/components/InvoiceList.tsx"],
  "entry_points": ["src/main.py", "frontend/src/main.tsx"],
  "test_coverage_summary": "Tests exist for auth and models only",
  "what_exists": "Full JWT auth system. Client CRUD endpoints. React frontend with invoice listing.",
  "what_is_missing": "Billing module, PDF generation, email notifications, recurring scheduler.",
  "continuation_context": "A garden services management system at MVP stage — auth and CRUD done, billing not started."
}
"""


def _build_tree(root: Path, max_depth: int = 4) -> str:
    lines: list[str] = []

    def _walk(path: Path, depth: int, prefix: str) -> None:
        if len(lines) >= _MAX_TREE_LINES:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            return
        for entry in entries:
            if entry.name in _IGNORE_DIRS or entry.name.startswith("."):
                continue
            lines.append(f"{prefix}{entry.name}{'/' if entry.is_dir() else ''}")
            if entry.is_dir() and depth > 1:
                _walk(entry, depth - 1, prefix + "  ")

    _walk(root, max_depth, "")
    return "\n".join(lines)


def _read_key_files(root: Path) -> str:
    parts: list[str] = []
    for name in _KEY_FILES:
        p = root / name
        if p.exists():
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")[:_MAX_FILE_CHARS]
                parts.append(f"=== {name} ===\n{content}\n")
            except OSError:
                pass
    return "\n".join(parts)


class CodebaseScannerAgent(BaseAgent):
    """Scans an existing project directory and produces a CodebaseAnalysis for the BA."""

    name = "codebase_scanner"
    role_description = "Analyzes an existing codebase to provide continuation context."

    def run(self, state: TeamState) -> dict:
        project_dir = state.get("project_dir") or ""
        if not project_dir:
            return {
                "current_agent": self.name,
                "codebase_analysis": None,
                "messages": [{"role": "assistant", "content": "[Scanner] No project_dir — skipping."}],
            }

        root = Path(project_dir).expanduser().resolve()
        if not root.is_dir():
            return {
                "current_agent": self.name,
                "codebase_analysis": None,
                "messages": [{
                    "role": "assistant",
                    "content": f"[Scanner] Directory not found: {root} — skipping.",
                }],
            }

        tree = _build_tree(root)
        key_files = _read_key_files(root)
        context = f"Project root: {root}\n\nFile tree:\n{tree}\n\n{key_files}".strip()

        raw = self.system(_SYSTEM, context)
        try:
            data: dict = _json_loads(_strip_fences(raw))
        except Exception:
            data = {}

        analysis = CodebaseAnalysis(
            **{k: v for k, v in data.items() if k in CodebaseAnalysis.model_fields}
        )
        return {
            "codebase_analysis": analysis,
            "current_agent": self.name,
            "messages": [{
                "role": "assistant",
                "content": (
                    f"[Scanner] {root.name}: "
                    f"stack=[{', '.join(analysis.tech_stack[:3])}]  "
                    f"{analysis.continuation_context}"
                ),
            }],
        }
