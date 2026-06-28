from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import CodebaseAnalysis
from antcrew.core.state import TeamState

_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "coverage", ".cache",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "target",
    ".gradle", ".idea", ".mvn",
}
_KEY_FILES = [
    "README.md", "readme.md", "README.rst",
    # JS / TS
    "package.json", "tsconfig.json", "vite.config.ts", "vite.config.js",
    "angular.json", "next.config.js", "nuxt.config.ts",
    # Python
    "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg",
    # Java / JVM
    "pom.xml", "build.gradle", "build.gradle.kts",
    # Infra
    "docker-compose.yml", "docker-compose.yaml", "Dockerfile",
    ".env.example", "Makefile",
]
_MAX_FILE_CHARS = 3_000
_MAX_TREE_LINES = 200


class _ScannerPayload(BaseModel):
    """LLM output schema for the codebase scanner — excludes 'label' (injected from path)."""
    tech_stack: list[str] = []
    existing_modules: list[str] = []
    entry_points: list[str] = []
    test_coverage_summary: str = ""
    what_exists: str = ""
    what_is_missing: str = ""
    continuation_context: str = ""

_SYSTEM = """\
You are a Senior Software Architect analyzing one component of an existing project.
Given the directory tree and key file contents, produce a structured JSON analysis.

Respond ONLY with a valid JSON object (no markdown fences, no prose):
{
  "tech_stack": ["Angular 17", "TypeScript 5", "RxJS 7"],
  "existing_modules": ["src/auth", "src/clients", "src/invoices"],
  "entry_points": ["src/main.ts", "src/app/app.module.ts"],
  "test_coverage_summary": "Unit tests for auth service only",
  "what_exists": "Full Keycloak-backed auth, client listing, basic invoice form",
  "what_is_missing": "Billing summary page, PDF download, recurring invoice UI",
  "continuation_context": "Angular frontend for a garden services SaaS — auth and CRUD done, billing UI not started"
}
"""


def _build_tree(root: Path, ignore: set[str], max_depth: int = 4) -> str:
    lines: list[str] = []

    def _walk(path: Path, depth: int, prefix: str) -> None:
        if len(lines) >= _MAX_TREE_LINES:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            return
        for entry in entries:
            if len(lines) >= _MAX_TREE_LINES:
                break
            if entry.name in ignore or entry.name.startswith("."):
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


def _scan_one(agent: "CodebaseScannerAgent", label: str, path: str) -> CodebaseAnalysis | None:
    """Scan a single directory and return a CodebaseAnalysis (or None on error)."""
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        return None

    ignore = getattr(agent, "_ignore", _IGNORE_DIRS)
    tree = _build_tree(root, ignore)
    key_files = _read_key_files(root)
    context = f"Component: {label}\nPath: {root}\n\nFile tree:\n{tree}\n\n{key_files}".strip()

    try:
        payload = agent.system_parsed(_SYSTEM, context, _ScannerPayload)
    except Exception:
        return CodebaseAnalysis(label=label)
    return CodebaseAnalysis(label=label, **payload.model_dump())


class CodebaseScannerAgent(BaseAgent):
    """Scans one or more project directories and produces CodebaseAnalysis artifacts."""

    name = "codebase_scanner"
    role_description = "Analyzes existing codebase components to provide continuation context."
    consumes: list[str] = ["project_dir", "project_dirs"]
    produces: list[str] = ["codebase_analysis", "codebase_analyses"]

    def __init__(self, llm, *, extra_ignore_dirs: list[str] | None = None, **kwargs):
        super().__init__(llm, **kwargs)
        self._ignore = _IGNORE_DIRS | set(extra_ignore_dirs or [])

    def run(self, state: TeamState) -> dict:
        # Short-circuit: if pre-computed context was injected, skip the scan.
        if state.get("codebase_analysis") is not None or state.get("codebase_analyses"):
            return {
                "current_agent": self.name,
                "codebase_analysis": state.get("codebase_analysis"),
                "codebase_analyses": state.get("codebase_analyses"),
                "messages": [{"role": "assistant", "content": "[Scanner] Using pre-computed context."}],
            }

        # Build the label → path mapping from whichever source is set.
        dirs: dict[str, str] = {}

        project_dirs = state.get("project_dirs") or {}
        if project_dirs:
            dirs = dict(project_dirs)
        elif state.get("project_dir"):
            pd = state["project_dir"]
            dirs = {Path(pd).name: pd}

        if not dirs:
            return {
                "current_agent": self.name,
                "codebase_analysis": None,
                "codebase_analyses": None,
                "messages": [{"role": "assistant", "content": "[Scanner] No project dirs — skipping."}],
            }

        analyses: list[CodebaseAnalysis] = []
        skipped: list[str] = []

        for label, path in dirs.items():
            result = _scan_one(self, label, path)
            if result is not None:
                analyses.append(result)
            else:
                skipped.append(f"{label}:{path}")

        if not analyses:
            return {
                "current_agent": self.name,
                "codebase_analysis": None,
                "codebase_analyses": None,
                "messages": [{
                    "role": "assistant",
                    "content": f"[Scanner] No valid directories found: {skipped}",
                }],
            }

        summary_parts = [
            f"[{a.label}] {', '.join(a.tech_stack[:2])} — {a.continuation_context}"
            for a in analyses
        ]
        return {
            "codebase_analyses": analyses,
            "codebase_analysis": analyses[0],       # backward-compat single alias
            "current_agent": self.name,
            "messages": [{
                "role": "assistant",
                "content": "[Scanner] " + " | ".join(summary_parts),
            }],
        }
