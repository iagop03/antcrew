"""Project — persistent development project that accumulates state across runs.

Without Project every team.run() starts from scratch:

    team.run("Build JWT auth")     # → PRD, 3 tickets, 2 code files
    team.run("Add OAuth")          # → PRD, 3 tickets, 2 code files  (overwrites!)

With Project each run builds on the previous ones:

    from antcrew import Project, DevTeam

    project = Project(DevTeam(model=llm), name="auth-service", path="auth.json")

    project.run("Build JWT auth")       # run 1
    project.run("Add OAuth with Google") # run 2 — agents receive context from run 1
    project.run("Fix refresh token bug") # run 3 — continues from run 2

    print(len(project.state["tickets"]))        # total across all runs
    print(len(project.state["code_artifacts"])) # accumulated code files
    print(project.summary())

State is auto-saved to disk after each run when ``path`` is set.
Load a saved project with ``Project.load(path, team=...)``.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from antcrew.core.artifacts import (
    PRD,
    CodeArtifact,
    CodeReview,
    ContentPiece,
    DevOpsArtifact,
    DocumentationArtifact,
    ResearchDocument,
    TestArtifact,
    Ticket,
)

log = logging.getLogger(__name__)

# Keys whose values are lists of Pydantic models
_LIST_KEYS: dict[str, Any] = {
    "tickets":          Ticket,
    "code_artifacts":   CodeArtifact,
    "test_artifacts":   TestArtifact,
    "devops_artifacts": DevOpsArtifact,
    "doc_artifacts":    DocumentationArtifact,
}

# Keys whose value is a single Pydantic model (latest run wins)
_SINGLE_KEYS: dict[str, Any] = {
    "prd":               PRD,
    "review":            CodeReview,
    "research_document": ResearchDocument,
    "content_piece":     ContentPiece,
}


class Project:
    """Persistent dev project that accumulates state across multiple team runs.

    Args:
        team:  Any team instance (DevTeam, FullStackTeam, ResearchTeam, ContentTeam).
        name:  Human-readable project name (informational).
        path:  If set, the project is auto-saved to this JSON file after every run.
               Also used as the default destination for ``save()``.
    """

    def __init__(
        self,
        team,
        *,
        name: str = "",
        path: Optional[str | Path] = None,
        use_memory: bool = True,
    ) -> None:
        self.team = team
        self.name = name
        self._path = Path(path) if path else None
        self._memory = None
        if use_memory:
            try:
                from antcrew.memory.chroma import ChromaMemory
                mem_path = str(Path(path).parent / ".antcrew_memory") if path else ".antcrew_memory"
                self._memory = ChromaMemory(path=mem_path, collection=name or "project")
                log.debug("Project: ChromaMemory active at %s", mem_path)
            except Exception:
                log.debug("Project: chromadb not available — memory disabled")
        self._state: dict = {}
        self._history: list[dict] = []
        self._created_at: float = time.time()
        # Set by CLI to allow auto-restore of team without passing team= to load()
        self._team_spec: Optional[dict] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> dict:
        """Snapshot of the accumulated project state (copy)."""
        return dict(self._state)

    @property
    def history(self) -> list[dict]:
        """Log of every run: request, timestamp, new_tickets, new_code_files."""
        return list(self._history)

    def run(self, request: str) -> dict:
        """Run the team pipeline and merge the result into the project state.

        The request is transparently enriched with a brief summary of the
        existing state so agents can build on previous work rather than
        starting over.

        Returns the raw state dict from the latest run.
        """
        enriched = self._enrich(request)
        if self._memory and hasattr(self.team, 'memory') and self.team.memory is None:
            self.team.memory = self._memory
            for agent in getattr(self.team, '_agents', {}).values():
                agent.memory = self._memory
        run_result = self.team.run(enriched)
        if self._memory:
            result_state = run_result.state if hasattr(run_result, "state") else run_result
            self._memory.store_run(result_state)
        self._merge(run_result, request)
        if self._path:
            self.save()
        return run_result.state if hasattr(run_result, "state") else run_result

    async def run_async(self, request: str) -> dict:
        """Async variant of :meth:`run`."""
        enriched = self._enrich(request)
        state = await self.team.run_async(enriched)
        self._merge(state, request)
        if self._path:
            self.save()
        return state

    def save(self, path: Optional[str | Path] = None) -> None:
        """Serialize the project to a JSON file.

        Args:
            path: Destination path. Falls back to the path set in the constructor.

        Raises:
            ValueError: if no path is available.
        """
        dest = Path(path) if path else self._path
        if dest is None:
            raise ValueError(
                "No path specified. Pass a path argument or set path= in the constructor."
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            json.dumps(self._to_dict(), indent=2, default=_json_default),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path, *, team=None) -> "Project":
        """Load a previously saved project from disk.

        Args:
            path: Path to the JSON file written by :meth:`save`.
            team: Team instance to attach.  If omitted, the project must have
                  a ``team_spec`` stored (written by the CLI or set via
                  ``project._team_spec = {...}`` before the first save).

        Returns:
            Project instance with history and state restored.

        Raises:
            ValueError: if no team is provided and no team_spec is stored.
        """
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))

        if team is None:
            stored = data.get("team_spec")
            if stored:
                team = cls._team_from_spec(stored)
            else:
                raise ValueError(
                    "No 'team' argument provided and no 'team_spec' stored in "
                    f"{path}.  Pass team= or use the CLI with --team / --config."
                )

        p = cls(team, name=data.get("name", ""), path=path)
        p._created_at = data.get("created_at", time.time())
        p._history = data.get("history", [])
        p._state = _deserialize_state(data.get("state", {}))
        p._team_spec = data.get("team_spec")
        return p

    @staticmethod
    def _team_from_spec(spec: dict):
        """Reconstruct a team from a stored team_spec dict."""
        spec_type = spec.get("type", "inline")
        if spec_type == "config":
            from antcrew.config import load as _load_config
            return _load_config(spec["path"])
        # inline
        from antcrew.config import build_llm
        llm = build_llm(spec.get("model", "claude"))
        team_type = spec.get("team", "dev")
        if team_type == "dev":
            from antcrew.teams.dev_team import DevTeam
            return DevTeam(model=llm)
        if team_type == "fullstack":
            from antcrew.teams.fullstack_team import FullStackTeam
            return FullStackTeam(model=llm)
        if team_type == "research":
            from antcrew.teams.research_team import ResearchTeam
            return ResearchTeam(model=llm)
        if team_type == "content":
            from antcrew.teams.content_team import ContentTeam
            return ContentTeam(model=llm)
        raise ValueError(f"Unknown team type '{team_type}' in stored team_spec.")

    def summary(self) -> str:
        """Return a human-readable summary of project state."""
        lines: list[str] = [
            f"Project: {self.name or '(unnamed)'}",
            f"Runs:    {len(self._history)}",
        ]

        if self._state.get("prd"):
            prd = self._state["prd"]
            lines.append(f"PRD:     {getattr(prd, 'title', str(prd))}")

        for key, label in (
            ("tickets",          "Tickets"),
            ("code_artifacts",   "Code files"),
            ("test_artifacts",   "Test files"),
            ("devops_artifacts", "DevOps files"),
            ("doc_artifacts",    "Docs"),
        ):
            items = self._state.get(key) or []
            if items:
                lines.append(f"{label}:{'':>{11 - len(label)}} {len(items)}")

        if self._history:
            lines.append("History:")
            for i, h in enumerate(self._history, 1):
                req = h.get("request", "")[:60]
                t = h.get("new_tickets", 0)
                c = h.get("new_code_files", 0)
                ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(h.get("timestamp", 0)))
                lines.append(f"  {i}. [{ts}] {req}  (+{t} tickets, +{c} files)")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _enrich(self, request: str) -> str:
        """Prepend a brief project-context header when there is existing state."""
        if not self._state:
            return request

        parts = ["[Project context — extend existing work, do not recreate from scratch]"]

        if self._state.get("prd"):
            prd = self._state["prd"]
            title   = getattr(prd, "title", "")
            summary = getattr(prd, "summary", "")
            parts.append(f"PRD: {title} — {summary[:200]}")

        if self._state.get("tickets"):
            items = self._state["tickets"]
            titles = [getattr(t, "title", str(t)) for t in items[:3]]
            suffix = f" (+{len(items) - 3} more)" if len(items) > 3 else ""
            parts.append(f"Existing tickets ({len(items)}): {', '.join(titles)}{suffix}")

        if self._state.get("code_artifacts"):
            items = self._state["code_artifacts"]
            files = [getattr(a, "file_path", str(a)) for a in items[:4]]
            suffix = f" (+{len(items) - 4} more)" if len(items) > 4 else ""
            parts.append(f"Existing code ({len(items)} files): {', '.join(files)}{suffix}")

        parts.append(f"\nNew request: {request}")
        return "\n".join(parts)

    def _merge(self, new_state: dict, original_request: str) -> None:
        """Merge a run's output into the accumulated project state."""
        run_number = len(self._history) + 1
        entry: dict = {
            "run_number":    run_number,
            "request":       original_request,
            "timestamp":     time.time(),
            "new_tickets":   0,
            "new_code_files": 0,
        }

        # Lists: accumulate — each run adds new items
        for key in _LIST_KEYS:
            existing  = self._state.get(key) or []
            new_items = new_state.get(key)   or []
            if new_items:
                # tag artifacts that support run tracking
                tagged = []
                for item in new_items:
                    if hasattr(item, "created_at_run") and item.created_at_run == 0:
                        item = item.model_copy(update={"created_at_run": run_number})
                    tagged.append(item)
                self._state[key] = existing + tagged
                if key == "tickets":
                    entry["new_tickets"] = len(new_items)
                elif key == "code_artifacts":
                    entry["new_code_files"] = len(new_items)

        # Singles: latest run wins
        for key in _SINGLE_KEYS:
            if new_state.get(key) is not None:
                self._state[key] = new_state[key]

        # test_results: latest run wins
        if new_state.get("test_results") is not None:
            self._state["test_results"] = new_state["test_results"]

        # Errors: accumulate
        self._state.setdefault("errors", [])
        self._state["errors"] = list(self._state["errors"]) + list(new_state.get("errors") or [])

        # Metadata: always from latest run
        self._state["request"]       = original_request
        self._state["current_agent"] = new_state.get("current_agent", "")

        self._history.append(entry)

    def _to_dict(self) -> dict:
        d: dict = {
            "name":       self.name,
            "created_at": self._created_at,
            "state":      _serialize_state(self._state),
            "history":    self._history,
        }
        if self._team_spec:
            d["team_spec"] = self._team_spec
        return d


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _json_default(obj: Any) -> Any:
    """Fallback JSON encoder for Pydantic models and other objects."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


def _serialize_state(state: dict) -> dict:
    """Convert Pydantic model instances to plain dicts for JSON storage."""
    result: dict = {}
    for key, value in state.items():
        if isinstance(value, list):
            result[key] = [
                item.model_dump() if hasattr(item, "model_dump") else item
                for item in value
            ]
        elif hasattr(value, "model_dump"):
            result[key] = value.model_dump()
        else:
            result[key] = value
    return result


def _deserialize_state(data: dict) -> dict:
    """Reconstruct Pydantic models from the JSON dict produced by _serialize_state."""
    result: dict = {}
    for key, value in data.items():
        if key in _LIST_KEYS and isinstance(value, list):
            cls = _LIST_KEYS[key]
            result[key] = [
                cls.model_validate(item) if isinstance(item, dict) else item
                for item in value
            ]
        elif key in _SINGLE_KEYS and isinstance(value, dict):
            result[key] = _SINGLE_KEYS[key].model_validate(value)
        else:
            result[key] = value
    return result
