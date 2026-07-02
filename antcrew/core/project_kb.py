"""Structured project knowledge base — persists across pipeline runs.

Unlike ChromaMemory (semantic search over free text), ProjectKB stores
structured records that agents can query directly: API endpoints, data models,
services, and resolved dependencies.  It is updated after each pipeline run
and survives restarts via a JSON file.

Usage::

    from antcrew.core.project_kb import ProjectKB

    kb = ProjectKB(path="./project_kb.json")
    kb.update_from_state(state)           # after pipeline run
    kb.save()

    # In the next run:
    kb = ProjectKB.load("./project_kb.json")
    print(kb.context_for_agent("backend_dev"))
"""
from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Record types
# ---------------------------------------------------------------------------

@dataclass
class EndpointRecord:
    path: str
    method: str
    module: str
    handler: str
    description: str = ""


@dataclass
class ModelRecord:
    name: str
    module: str
    fields: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class ServiceRecord:
    name: str
    module: str
    methods: list[str] = field(default_factory=list)
    description: str = ""


# ---------------------------------------------------------------------------
# ProjectKB
# ---------------------------------------------------------------------------

class ProjectKB:
    """Structured, persistable project knowledge base."""

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self._path: Optional[Path] = Path(path) if path else None
        self.endpoints: list[EndpointRecord] = []
        self.models: list[ModelRecord] = []
        self.services: list[ServiceRecord] = []
        self.dependencies: dict[str, str] = {}
        self.tech_stack: list[str] = []

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Optional[str | Path] = None) -> None:
        """Serialise to JSON.  Uses *path* or the path given at construction."""
        target = Path(path) if path else self._path
        if target is None:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "endpoints": [asdict(e) for e in self.endpoints],
            "models": [asdict(m) for m in self.models],
            "services": [asdict(s) for s in self.services],
            "dependencies": self.dependencies,
            "tech_stack": self.tech_stack,
        }
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log.debug("project_kb saved to %s", target)

    @classmethod
    def load(cls, path: str | Path) -> "ProjectKB":
        """Load from JSON.  Returns an empty KB if the file doesn't exist."""
        p = Path(path)
        kb = cls(path=p)
        if not p.exists():
            return kb
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("project_kb: failed to load %s — %s", p, exc)
            return kb
        kb.endpoints = [EndpointRecord(**e) for e in data.get("endpoints") or []]
        kb.models = [ModelRecord(**m) for m in data.get("models") or []]
        kb.services = [ServiceRecord(**s) for s in data.get("services") or []]
        kb.dependencies = data.get("dependencies") or {}
        kb.tech_stack = data.get("tech_stack") or []
        return kb

    # ------------------------------------------------------------------
    # Updating
    # ------------------------------------------------------------------

    def update_from_state(self, state: dict) -> None:
        """Extract structured knowledge from a completed pipeline state."""
        code_artifacts = state.get("code_artifacts") or []
        for art in code_artifacts:
            content = getattr(art, "content", None) or (art.get("content") if isinstance(art, dict) else "")
            file_path = getattr(art, "file_path", None) or (art.get("file_path") if isinstance(art, dict) else "")
            if not content or not file_path:
                continue
            suffix = Path(file_path).suffix.lower()
            if suffix == ".py":
                self._extract_python(content, file_path)
            elif suffix in (".toml", ".txt") and ("requirements" in file_path or "pyproject" in file_path):
                self._extract_dependencies(content, file_path)

        self._deduplicate()
        log.debug(
            "project_kb updated: %d endpoints, %d models, %d services",
            len(self.endpoints), len(self.models), len(self.services),
        )

    def update_dependencies(self, deps: dict[str, str]) -> None:
        """Merge *deps* into the dependency registry."""
        self.dependencies.update(deps)

    def set_tech_stack(self, stack: list[str]) -> None:
        self.tech_stack = list(dict.fromkeys(stack))  # deduplicate, preserve order

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def context_for_agent(self, agent_name: str, *, max_chars: int = 3_000) -> str:
        """Return a compact KB summary relevant to *agent_name* for LLM injection."""
        sections: list[str] = []

        if self.tech_stack:
            sections.append("Tech stack: " + ", ".join(self.tech_stack))

        if self.dependencies and agent_name in (
            "backend_dev", "devops", "qa", "reviewer",
        ):
            top = dict(list(self.dependencies.items())[:10])
            sections.append("Key dependencies: " + ", ".join(f"{k}=={v}" for k, v in top.items()))

        if self.endpoints and agent_name in (
            "backend_dev", "frontend_dev", "qa", "reviewer", "pm", "doc_writer",
        ):
            ep_lines = [f"  {e.method} {e.path}  [{e.handler}]" for e in self.endpoints[:15]]
            sections.append("Existing API endpoints:\n" + "\n".join(ep_lines))

        if self.models and agent_name in (
            "backend_dev", "qa", "reviewer", "pm", "doc_writer",
        ):
            model_lines = [f"  {m.name}({', '.join(m.fields[:6])})" for m in self.models[:10]]
            sections.append("Data models:\n" + "\n".join(model_lines))

        if self.services and agent_name in (
            "backend_dev", "frontend_dev", "reviewer", "pm", "doc_writer",
        ):
            svc_lines = [f"  {s.name}: {', '.join(s.methods[:4])}" for s in self.services[:8]]
            sections.append("Services:\n" + "\n".join(svc_lines))

        if not sections:
            return ""

        block = "## Project Knowledge Base\n" + "\n\n".join(sections) + "\n\n"
        if len(block) > max_chars:
            block = block[:max_chars] + "\n...[truncated]\n\n"
        return block

    def summary(self) -> str:
        return (
            f"{len(self.endpoints)} endpoints, {len(self.models)} models, "
            f"{len(self.services)} services, {len(self.dependencies)} dependencies"
        )

    # ------------------------------------------------------------------
    # Private extraction helpers
    # ------------------------------------------------------------------

    def _extract_python(self, source: str, file_path: str) -> None:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return

        module = _file_to_module(file_path)

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                self._process_class(node, module, file_path)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                # Top-level route decorators (FastAPI, Flask, Django)
                for dec in node.decorator_list:
                    ep = _try_route_decorator(dec, node.name, module)
                    if ep:
                        self.endpoints.append(ep)
                        break

    def _process_class(self, node: ast.ClassDef, module: str, file_path: str) -> None:
        # Pydantic / SQLAlchemy models
        base_names = {_base_name(b) for b in node.bases}
        if base_names & {"BaseModel", "Base", "Model", "SQLModel", "Document"}:
            fields = [
                n.targets[0].id if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                else n.target.id if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
                else None
                for n in node.body
                if isinstance(n, (ast.Assign, ast.AnnAssign))
            ]
            self.models.append(ModelRecord(
                name=node.name,
                module=module,
                fields=[f for f in fields if f],
                description=ast.get_docstring(node) or "",
            ))
            return

        # Service / Repository classes
        methods = [
            n.name for n in ast.walk(node)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and not n.name.startswith("_")
        ]
        if methods:
            self.services.append(ServiceRecord(
                name=node.name,
                module=module,
                methods=methods,
                description=ast.get_docstring(node) or "",
            ))

    def _extract_dependencies(self, content: str, file_path: str) -> None:
        if file_path.endswith(".toml"):
            _parse_toml_deps(content, self.dependencies)
        else:
            _parse_requirements_txt(content, self.dependencies)

    def _deduplicate(self) -> None:
        seen_ep: set[tuple[str, str]] = set()
        unique_ep: list[EndpointRecord] = []
        for e in self.endpoints:
            key = (e.method.upper(), e.path)
            if key not in seen_ep:
                seen_ep.add(key)
                unique_ep.append(e)
        self.endpoints = unique_ep

        seen_m: set[str] = set()
        unique_m: list[ModelRecord] = []
        for m in self.models:
            if m.name not in seen_m:
                seen_m.add(m.name)
                unique_m.append(m)
        self.models = unique_m

        seen_s: set[str] = set()
        unique_s: list[ServiceRecord] = []
        for s in self.services:
            if s.name not in seen_s:
                seen_s.add(s.name)
                unique_s.append(s)
        self.services = unique_s


# ---------------------------------------------------------------------------
# Pure-function helpers
# ---------------------------------------------------------------------------

def _file_to_module(file_path: str) -> str:
    return Path(file_path).with_suffix("").as_posix().replace("/", ".")


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _try_route_decorator(dec: ast.expr, handler: str, module: str) -> Optional[EndpointRecord]:
    """Return an EndpointRecord if *dec* looks like a route decorator, else None."""
    # FastAPI / Flask: @router.get("/path") or @app.post("/path")
    if isinstance(dec, ast.Call):
        func = dec.func
        method = ""
        if isinstance(func, ast.Attribute):
            method = func.attr.upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            return None
        path_arg = ""
        if dec.args and isinstance(dec.args[0], ast.Constant):
            path_arg = str(dec.args[0].value)
        elif dec.keywords:
            for kw in dec.keywords:
                if kw.arg == "path" and isinstance(kw.value, ast.Constant):
                    path_arg = str(kw.value.value)
        if path_arg:
            return EndpointRecord(path=path_arg, method=method, module=module, handler=handler)
    return None


def _parse_requirements_txt(content: str, deps: dict[str, str]) -> None:
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_\-]+)[=><!\s]*([\d.]*)", line)
        if m:
            name, version = m.group(1), m.group(2)
            deps.setdefault(name.lower(), version or "*")


def _parse_toml_deps(content: str, deps: dict[str, str]) -> None:
    # Simple regex scan: no full TOML parser needed for our use case
    in_deps = False
    for line in content.splitlines():
        if re.match(r"\[.*dependencies.*\]", line, re.I):
            in_deps = True
            continue
        if line.strip().startswith("[") and "dependencies" not in line:
            in_deps = False
        if in_deps:
            m = re.match(r'\s*([A-Za-z0-9_\-]+)\s*=\s*["\'^>=<~!]*([0-9.]+)', line)
            if m:
                deps.setdefault(m.group(1).lower(), m.group(2))
