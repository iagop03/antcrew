"""UC10 — Brand Voice Content Team.

Combines ContentTeam with ChromaMemory to embed brand voice guidelines into
every agent call, producing content that adheres to the brand's tone, style,
persona, and content standards.

The BrandVoiceProfile is stored in a per-brand ChromaDB collection so retrieved
examples are semantically ranked against each content request.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from antcrew.core.run_result import RunResult
from antcrew.teams.content_team import ContentTeam

if TYPE_CHECKING:
    from antcrew.memory.store import MemoryResult
    from antcrew.models.base import BaseLLM
    from antcrew.trace import TraceLog

log = logging.getLogger(__name__)

_COLLECTION_RE = re.compile(r"[^a-z0-9_]")


def _collection_name(brand_name: str) -> str:
    slug = _COLLECTION_RE.sub("_", brand_name.lower())
    return f"bv_{slug[:48]}"


@dataclass
class BrandVoiceProfile:
    """Brand voice guidelines stored in and retrieved from semantic memory.

    Build the profile once, pass it to :class:`BrandVoiceContentTeam`, and every
    run will automatically inject the brand context into the content pipeline.

    Attributes:
        name:       Brand name (e.g. ``"Acme Corp"``).
        tone:       Tone description (e.g. ``"Professional yet approachable"``).
        style:      Writing style notes (e.g. ``"Short sentences, active voice"``).
        persona:    Narrator persona (e.g. ``"A knowledgeable industry insider"``).
        examples:   Representative content pieces in the desired voice.
        standards:  Mandatory content rules (e.g. ``"Always end with a CTA"``).
    """

    name: str
    tone: str = ""
    style: str = ""
    persona: str = ""
    examples: list[str] = field(default_factory=list)
    standards: list[str] = field(default_factory=list)

    def to_context_block(self) -> str:
        """Render guidelines as a structured context block for agent injection."""
        parts = [f"[Brand: {self.name}]"]
        if self.tone:
            parts.append(f"Tone: {self.tone}")
        if self.style:
            parts.append(f"Style: {self.style}")
        if self.persona:
            parts.append(f"Persona: {self.persona}")
        if self.standards:
            parts.append("Content standards:")
            parts.extend(f"  • {s}" for s in self.standards)
        if self.examples:
            parts.append("Voice examples:")
            for ex in self.examples[:3]:
                parts.append(f'  "{ex[:300]}"')
        return "\n".join(parts)


class BrandVoiceContentTeam:
    """UC10: ContentTeam with brand voice memory via ChromaDB.

    Seeds a ChromaDB collection with the brand profile on first run.  Subsequent
    runs retrieve semantically similar brand examples to include in the prompt,
    keeping content on-brand as the example library grows.

    Usage::

        from antcrew.teams.brand_voice_team import BrandVoiceContentTeam, BrandVoiceProfile

        profile = BrandVoiceProfile(
            name="Acme Corp",
            tone="Professional but approachable",
            style="Short paragraphs, active voice, no jargon",
            persona="An industry expert who makes complex things simple",
            examples=[
                "Our API ships same-day — because waiting is so 2019.",
                "Built for developers, loved by teams.",
            ],
            standards=[
                "Always end with a clear call-to-action.",
                "Use 'you' not 'users' or 'customers'.",
            ],
        )

        team = BrandVoiceContentTeam(brand=profile)
        result = team.run("Write a product launch announcement for our new API")
        print(result.state["content_piece"].body)

    Grow the example library over time with successful content::

        team.add_example("Our new dashboard is live. One click to your metrics.")

    chromadb must be installed::

        pip install antcrew[memory]
    """

    def __init__(
        self,
        brand: BrandVoiceProfile,
        *,
        model: Optional["BaseLLM"] = None,
        memory_path: str = ".antcrew_brand_voice",
        trace_log: Optional["TraceLog"] = None,
        max_retrieved_examples: int = 3,
    ) -> None:
        """
        Args:
            brand:                   Brand voice profile with guidelines and examples.
            model:                   LLM to use; defaults to AnthropicModel().
            memory_path:             Path for the ChromaDB persistent store.
            trace_log:               Optional TraceLog for observability.
            max_retrieved_examples:  Max brand examples retrieved per run.
        """
        from antcrew.memory.chroma import ChromaMemory

        self.brand = brand
        self._max_examples = max_retrieved_examples
        self._memory = ChromaMemory(
            path=memory_path,
            collection=_collection_name(brand.name),
        )
        self._seed_brand_voice()
        self._team = ContentTeam(model=model, memory=self._memory, trace_log=trace_log)

    def _seed_brand_voice(self) -> None:
        """Populate ChromaMemory with brand guidelines if the collection is empty."""
        existing = self._memory.search(f"brand guidelines {self.brand.name}", n=1)
        if existing:
            log.debug("Brand voice for %r already seeded (%d docs)", self.brand.name, self._memory.count())
            return

        self._memory.add(
            text=self.brand.to_context_block(),
            metadata={"type": "brand_guidelines", "brand": self.brand.name},
        )
        for i, example in enumerate(self.brand.examples):
            self._memory.add(
                text=example,
                metadata={"type": "brand_example", "brand": self.brand.name, "idx": str(i)},
            )
        log.debug(
            "Seeded brand voice for %r with %d example(s)", self.brand.name, len(self.brand.examples)
        )

    def run(self, request: str, *, thread_id: str = "default") -> RunResult:
        """Run the content pipeline with brand voice context injected.

        Retrieves semantically relevant brand examples from ChromaMemory and
        prepends them to the request so all agents see the brand context.

        Args:
            request:   Content request (topic, type, audience, etc.).
            thread_id: LangGraph thread_id for HITL checkpointing.

        Returns:
            :class:`RunResult` with ``state["content_piece"]`` populated.
        """
        brand_ctx = self.brand.to_context_block()

        # Retrieve semantically similar examples (beyond the seeded guidelines)
        examples = self._memory.search(
            request,
            n=self._max_examples,
            filter={"type": "brand_example"},
        )
        if examples:
            ex_block = "\n".join(f'  • "{r.text[:200]}"' for r in examples)
            brand_ctx += f"\nRelevant brand examples:\n{ex_block}"

        augmented = f"{request}\n\n{brand_ctx}"
        return self._team.run(augmented, thread_id=thread_id)

    def add_example(self, content: str) -> str:
        """Store a new successful content piece in the brand voice library.

        Args:
            content: Content piece that exemplifies the brand voice.

        Returns:
            ChromaDB entry_id for the stored document.
        """
        idx = self._memory.count()
        entry_id = self._memory.add(
            text=content,
            metadata={"type": "brand_example", "brand": self.brand.name, "idx": str(idx)},
        )
        log.debug("Added brand example #%d for %r", idx, self.brand.name)
        return entry_id

    def search_examples(self, query: str, *, n: int = 3) -> "list[MemoryResult]":
        """Search the brand example library by semantic similarity.

        Args:
            query: Semantic search query.
            n:     Maximum number of results.

        Returns:
            List of :class:`~antcrew.memory.store.MemoryResult` ranked by similarity.
        """
        return self._memory.search(
            query,
            n=n,
            filter={"type": "brand_example"},
        )
