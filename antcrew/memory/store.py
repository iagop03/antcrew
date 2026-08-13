"""Semantic memory store — abstract base + in-memory backend."""
from __future__ import annotations

import logging
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel

_log = logging.getLogger(__name__)


class MemoryResult(BaseModel):
    text: str
    metadata: dict
    score: float = 0.0  # higher = more relevant


@dataclass
class RunSnapshot:
    """Structured view of all artifacts stored for one past run.

    Obtain via ``memory.retrieve_run(run_id)``.
    """

    run_id: str
    project: str
    entries: list[MemoryResult] = field(default_factory=list)

    def _of_type(self, artifact_type: str) -> list[MemoryResult]:
        return [e for e in self.entries if e.metadata.get("artifact_type") == artifact_type]

    @property
    def request(self) -> str:
        results = self._of_type("request")
        return results[0].text if results else ""

    @property
    def prd_text(self) -> str:
        results = self._of_type("prd")
        return results[0].text if results else ""

    @property
    def tickets(self) -> list[MemoryResult]:
        return self._of_type("ticket")

    @property
    def code_artifacts(self) -> list[MemoryResult]:
        return self._of_type("code")

    def as_context(self, max_chars: int = 2000) -> str:
        """Format as an LLM-ready context block for injection into a system prompt."""
        if not self.entries:
            return ""
        lines = [f"\n\nContext from previous run (run_id={self.run_id}):"]
        chars = 0
        for entry in self.entries:
            at = entry.metadata.get("artifact_type", "?")
            snippet = entry.text[:300].replace("\n", " ")
            line = f"[{at}] {snippet}"
            if chars + len(line) > max_chars:
                break
            lines.append(line)
            chars += len(line)
        return "\n".join(lines) + "\n"


def _get(obj: Any, key: str, default=None):
    """Attribute access that works for both Pydantic models and plain dicts."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


class BaseMemory(ABC):
    """Abstract semantic memory backend.

    Implementations: InMemoryMemory (no deps), ChromaMemory (pip install chromadb).
    """

    @abstractmethod
    def add(self, text: str, metadata: dict) -> str:
        """Store text with metadata. Returns the entry ID."""

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        n: int = 5,
        filter: Optional[dict] = None,
    ) -> list[MemoryResult]:
        """Return the n most relevant entries for query."""

    @abstractmethod
    def count(self) -> int:
        """Total number of stored entries."""

    def clear(self) -> None:
        """Remove all entries (override in subclasses)."""

    def get_by_filter(self, filter: dict) -> list[MemoryResult]:
        """Return all entries matching the metadata filter (no semantic ranking).

        The default implementation logs a warning and returns an empty list.
        Subclasses should override this for efficient filtering.
        """
        _log.warning(
            "%s does not implement get_by_filter(); returning []",
            type(self).__name__,
        )
        return []

    def retrieve_run(self, run_id: str) -> RunSnapshot:
        """Return a structured snapshot of all artifacts stored for *run_id*."""
        entries = self.get_by_filter({"run_id": run_id})
        project = entries[0].metadata.get("project", "") if entries else ""
        return RunSnapshot(run_id=run_id, project=project, entries=entries)

    # ------------------------------------------------------------------
    # High-level helpers
    # ------------------------------------------------------------------

    def store_run(
        self,
        state: Any,
        *,
        run_id: str = "",
        project: str = "",
    ) -> int:
        """Extract and store key artifacts from a completed pipeline state.

        Works with both Pydantic TeamState values and plain dicts (load_state).
        Returns the number of entries stored.
        """
        count = 0
        run_id = run_id or uuid.uuid4().hex[:8]
        base = {"run_id": run_id, "project": project}

        # Original request
        req = _get(state, "request")
        if req:
            self.add(str(req), {**base, "artifact_type": "request"})
            count += 1

        # PRD
        prd = _get(state, "prd")
        if prd is not None:
            title   = _get(prd, "title", "")
            summary = _get(prd, "summary", "")
            reqs    = _get(prd, "functional_requirements") or []
            text    = f"PRD: {title}\n{summary}"
            if reqs:
                text += "\nRequirements: " + "; ".join(str(r) for r in reqs[:5])
            self.add(text, {**base, "artifact_type": "prd", "title": title})
            count += 1

        # Tickets
        for ticket in (_get(state, "tickets") or []):
            t_title = _get(ticket, "title", "")
            t_desc  = _get(ticket, "description", "")
            t_pri   = _get(ticket, "priority", "")
            if hasattr(t_pri, "value"):
                t_pri = t_pri.value
            self.add(
                f"Ticket: {t_title}\n{t_desc}",
                {**base, "artifact_type": "ticket",
                 "title": t_title, "priority": str(t_pri)},
            )
            count += 1

        # Code artifacts — path + description only (content can be huge)
        for art in (_get(state, "code_artifacts") or []):
            fp   = _get(art, "file_path", "")
            desc = _get(art, "description", "")
            lang = _get(art, "language", "")
            self.add(
                f"Code: {fp} ({lang})\n{desc}",
                {**base, "artifact_type": "code", "file_path": fp},
            )
            count += 1

        # DevOps artifacts
        for art in (_get(state, "devops_artifacts") or []):
            fp   = _get(art, "file_path", "")
            desc = _get(art, "description", "")
            lang = _get(art, "language", "")
            self.add(
                f"DevOps: {fp} ({lang})\n{desc}",
                {**base, "artifact_type": "devops", "file_path": fp},
            )
            count += 1

        # Code review
        review = _get(state, "review")
        if review is not None:
            verdict  = _get(review, "verdict", "")
            summary  = _get(review, "summary", "")
            findings = _get(review, "findings") or []
            text = f"Review: {verdict}\n{summary}"
            if findings:
                text += "\n" + "\n".join(
                    str(_get(f, "message", "")) for f in findings[:3]
                )
            self.add(text, {**base, "artifact_type": "review", "verdict": str(verdict)})
            count += 1

        # Research document
        research = _get(state, "research_document")
        if research is not None:
            title    = _get(research, "title", "")
            findings = _get(research, "key_findings") or []
            text = f"Research: {title}\n" + "\n".join(str(f) for f in findings[:5])
            self.add(text, {**base, "artifact_type": "research", "title": title})
            count += 1

        # Doc artifacts
        for doc in (_get(state, "doc_artifacts") or []):
            title    = _get(doc, "title", "")
            doc_type = _get(doc, "doc_type", "")
            content  = _get(doc, "content", "")
            self.add(
                f"Doc ({doc_type}): {title}\n{content[:400]}",
                {**base, "artifact_type": "doc",
                 "title": title, "doc_type": doc_type},
            )
            count += 1

        return count


# ---------------------------------------------------------------------------
# In-memory backend (no dependencies)
# ---------------------------------------------------------------------------

class InMemoryMemory(BaseMemory):
    """In-memory backend using Jaccard word-overlap similarity.

    No external dependencies. Suitable for demos, testing, and small projects.
    For large collections or persistent storage use ChromaMemory.
    """

    def __init__(self) -> None:
        self._entries: list[dict] = []

    def add(self, text: str, metadata: dict) -> str:
        entry_id = uuid.uuid4().hex
        self._entries.append({
            "id":       entry_id,
            "text":     text,
            "words":    _words(text),
            "metadata": dict(metadata),
        })
        return entry_id

    def search(
        self,
        query: str,
        *,
        n: int = 5,
        filter: Optional[dict] = None,
    ) -> list[MemoryResult]:
        q_words = _words(query)
        if not q_words:
            return []

        scored: list[tuple[float, dict]] = []
        for entry in self._entries:
            if filter:
                if not all(entry["metadata"].get(k) == v for k, v in filter.items()):
                    continue
            union = q_words | entry["words"]
            if not union:
                continue
            score = len(q_words & entry["words"]) / len(union)
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            MemoryResult(text=e["text"], metadata=e["metadata"], score=s)
            for s, e in scored[:n]
        ]

    def count(self) -> int:
        return len(self._entries)

    def get_by_filter(self, filter: dict) -> list[MemoryResult]:
        results = []
        for entry in self._entries:
            if all(entry["metadata"].get(k) == v for k, v in filter.items()):
                results.append(MemoryResult(text=entry["text"], metadata=entry["metadata"]))
        return results

    def clear(self) -> None:
        self._entries.clear()
