"""DocGenerator — generates a README.md from source files and architecture."""
from __future__ import annotations

from antcrew_engine.engine import (
    Artifact,
    ArtifactDelta,
    ArtifactId,
    ArtifactKind,
    CapabilityDescriptor,
    CapabilityResult,
    ConditionId,
)

from ._utils import head as _head
from .base import BaseExecutor

_SYSTEM = """\
You are a technical writer producing clear, accurate documentation.
Given a project's source files and architecture document, write a comprehensive README.md.

Output ONLY the raw Markdown content — no additional commentary.

Include these sections:
1. **Overview** — what the project does (2-3 sentences)
2. **Installation** — how to install dependencies and set up the environment
3. **Usage** — how to run or import the project (with code examples)
4. **API Reference** — public classes/functions/endpoints with brief descriptions
5. **Architecture** — high-level structure (derived from the architecture document)

Rules:
- Be concise and precise — no filler text
- Use code blocks with language tags
- Reference actual file paths and module names from the source files
- Do not make up features not present in the source
"""


class DocGenerator(BaseExecutor):
    descriptor = CapabilityDescriptor(
        name        = "doc_generator",
        description = "Generates README.md documentation from reviewed source files.",
        needs       = frozenset([
            ConditionId("implementation_exists"),
            ConditionId("code_reviewed"),
        ]),
        produces    = frozenset([ConditionId("documentation_exists")]),
        emits       = frozenset(["documentation"]),
        cost        = 1.5,
    )

    def _run(self, store, goal) -> CapabilityResult:
        sources = store.list(ArtifactKind.SOURCE)
        if not sources:
            return CapabilityResult(errors=["no source artifacts found — run CodeGenerator first"])

        arch = store.read(ArtifactId("architecture"))
        arch_text = arch.content if arch else "No architecture document."

        files_block = "\n\n".join(
            f"--- {art.metadata.get('file_path', str(art.id))} ---\n{_head(art.content, 80)}"
            for art in sources
            if isinstance(art.content, str) and art.content.strip()
        )
        system = f"{_SYSTEM}\n\n## Project Architecture\n{_head(arch_text, 100)}"
        user = (
            f"Goal: {goal.description}\n\n"
            f"Source files:\n{files_block}"
        )

        readme_content = self._call(system, user)

        artifact = Artifact(
            id       = ArtifactId("documentation"),
            kind     = ArtifactKind.DOCUMENTATION,
            content  = readme_content,
            metadata = {"file_path": "README.md"},
        )
        return CapabilityResult(delta=ArtifactDelta(created=(artifact,)))
