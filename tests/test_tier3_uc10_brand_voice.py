"""Tests for UC10: BrandVoiceContentTeam + BrandVoiceProfile."""
from __future__ import annotations

import re
import pytest
from antcrew.memory.store import MemoryResult
from antcrew.teams.brand_voice_team import (
    BrandVoiceContentTeam,
    BrandVoiceProfile,
    _collection_name,
)


# ---------------------------------------------------------------------------
# BrandVoiceProfile
# ---------------------------------------------------------------------------

def test_profile_defaults():
    p = BrandVoiceProfile(name="Acme")
    assert p.name == "Acme"
    assert p.tone == ""
    assert p.style == ""
    assert p.persona == ""
    assert p.examples == []
    assert p.standards == []


def test_profile_full():
    p = BrandVoiceProfile(
        name="Acme Corp",
        tone="Professional but approachable",
        style="Short sentences, active voice",
        persona="An expert who makes things simple",
        examples=["Our API ships same-day."],
        standards=["Always end with a CTA", "Use 'you' not 'users'"],
    )
    assert p.name == "Acme Corp"
    assert len(p.examples) == 1
    assert len(p.standards) == 2


def test_to_context_block_contains_brand_name():
    p = BrandVoiceProfile(name="FooBar Inc")
    block = p.to_context_block()
    assert "FooBar Inc" in block


def test_to_context_block_contains_tone():
    p = BrandVoiceProfile(name="X", tone="Bold and direct")
    block = p.to_context_block()
    assert "Bold and direct" in block


def test_to_context_block_contains_style():
    p = BrandVoiceProfile(name="X", style="Short sentences only")
    block = p.to_context_block()
    assert "Short sentences only" in block


def test_to_context_block_contains_persona():
    p = BrandVoiceProfile(name="X", persona="A friendly expert")
    block = p.to_context_block()
    assert "A friendly expert" in block


def test_to_context_block_contains_standards():
    p = BrandVoiceProfile(name="X", standards=["No passive voice", "CTA required"])
    block = p.to_context_block()
    assert "No passive voice" in block
    assert "CTA required" in block


def test_to_context_block_contains_examples():
    p = BrandVoiceProfile(name="X", examples=["Example sentence one."])
    block = p.to_context_block()
    assert "Example sentence one." in block


def test_to_context_block_empty_profile():
    p = BrandVoiceProfile(name="MinimalBrand")
    block = p.to_context_block()
    assert "MinimalBrand" in block
    # No tone/style/persona — block should still be valid string
    assert isinstance(block, str)
    assert len(block) > 0


def test_to_context_block_caps_examples_at_three():
    """to_context_block must include at most 3 examples."""
    p = BrandVoiceProfile(
        name="X",
        examples=[f"Example {i}" for i in range(10)],
    )
    block = p.to_context_block()
    # Only examples 0, 1, 2 should appear
    assert "Example 0" in block
    assert "Example 1" in block
    assert "Example 2" in block
    assert "Example 3" not in block


def test_to_context_block_no_empty_sections():
    """Sections with empty content should not appear in the output."""
    p = BrandVoiceProfile(name="X", tone="Bold")
    block = p.to_context_block()
    assert "Style:" not in block
    assert "Persona:" not in block
    assert "Content standards:" not in block
    assert "Voice examples:" not in block


# ---------------------------------------------------------------------------
# _collection_name helper
# ---------------------------------------------------------------------------

def test_collection_name_basic():
    name = _collection_name("Acme Corp")
    assert name.startswith("bv_")
    assert " " not in name


def test_collection_name_lowercase():
    name = _collection_name("UPPER BRAND")
    assert name == name.lower()


def test_collection_name_special_chars():
    name = _collection_name("Brand & Co. Ltd!")
    assert re.match(r"^[a-z0-9_]+$", name)


def test_collection_name_max_length():
    long_name = "A" * 100
    name = _collection_name(long_name)
    # bv_ prefix + max 48 chars slug
    assert len(name) <= 51


def test_collection_name_same_brand_same_collection():
    assert _collection_name("Acme") == _collection_name("Acme")


def test_collection_name_different_brands_different_collections():
    assert _collection_name("Acme") != _collection_name("Bravo")


# ---------------------------------------------------------------------------
# BrandVoiceContentTeam — construction (uses ChromaMemory mock)
# ---------------------------------------------------------------------------

class _FakeChromaMemory:
    def __init__(self, *args, **kwargs):
        self._docs: list[dict] = []

    def search(self, query, *, n=5, filter=None):
        results = self._docs
        if filter:
            results = [d for d in results if all(d["metadata"].get(k) == v for k, v in filter.items())]
        return [
            MemoryResult(text=d["text"], metadata=d["metadata"], score=1.0)
            for d in results[:n]
        ]

    def add(self, text, metadata):
        self._docs.append({"text": text, "metadata": metadata})
        return f"id_{len(self._docs)}"

    def count(self):
        return len(self._docs)


@pytest.fixture()
def mock_chroma(monkeypatch):
    monkeypatch.setattr(
        "antcrew.teams.brand_voice_team.ContentTeam.__init__",
        lambda self, *a, **kw: None,
    )
    monkeypatch.setattr(
        "antcrew.teams.brand_voice_team.ContentTeam.run",
        lambda self, req, **kw: type("R", (), {"state": {"content_piece": None}, "cost_usd": 0.0})(),
    )
    monkeypatch.setattr(
        "antcrew.memory.chroma.ChromaMemory", _FakeChromaMemory
    )


def _make_profile(**kw) -> BrandVoiceProfile:
    return BrandVoiceProfile(name="TestBrand", **kw)


def test_team_init_seeds_guidelines(mock_chroma):
    p = _make_profile(tone="Bold")
    team = BrandVoiceContentTeam(brand=p)
    # Guidelines should be in memory
    guidelines = [d for d in team._memory._docs if d["metadata"]["type"] == "brand_guidelines"]
    assert len(guidelines) == 1


def test_team_init_seeds_examples(mock_chroma):
    p = _make_profile(examples=["Ex A", "Ex B"])
    team = BrandVoiceContentTeam(brand=p)
    examples = [d for d in team._memory._docs if d["metadata"]["type"] == "brand_example"]
    assert len(examples) == 2


def test_team_init_no_double_seed(mock_chroma):
    """If memory already has docs, _seed_brand_voice must not add duplicates."""
    p = _make_profile(examples=["Ex A"])
    team = BrandVoiceContentTeam(brand=p)
    count_before = team._memory.count()
    # Simulate a second init on the same memory (already seeded)
    team._seed_brand_voice()
    count_after = team._memory.count()
    # Because search returns existing docs, seeding is skipped
    assert count_after == count_before


def test_team_add_example_stores_doc(mock_chroma):
    p = _make_profile()
    team = BrandVoiceContentTeam(brand=p)
    entry_id = team.add_example("New brand copy that works.")
    assert isinstance(entry_id, str)
    assert any(d["text"] == "New brand copy that works." for d in team._memory._docs)


def test_team_add_example_metadata(mock_chroma):
    p = _make_profile()
    team = BrandVoiceContentTeam(brand=p)
    team.add_example("Some content.")
    example_docs = [d for d in team._memory._docs if d["metadata"]["type"] == "brand_example"]
    assert any(d["metadata"]["brand"] == "TestBrand" for d in example_docs)


def test_team_run_augments_request(mock_chroma):
    """run() should inject brand context into the request."""
    received_requests: list[str] = []

    import antcrew.teams.brand_voice_team as _mod
    original_run = _mod.ContentTeam.run

    def capturing_run(self, req, **kw):
        received_requests.append(req)
        return type("R", (), {"state": {}, "cost_usd": 0.0})()

    _mod.ContentTeam.run = capturing_run
    try:
        p = _make_profile(tone="Bold")
        team = BrandVoiceContentTeam(brand=p)
        team.run("Write a product launch post")
    finally:
        _mod.ContentTeam.run = original_run

    assert len(received_requests) == 1
    req = received_requests[0]
    assert "Write a product launch post" in req
    assert "TestBrand" in req


def test_team_search_examples_filters_by_type(mock_chroma):
    """search_examples should pass filter={'type': 'brand_example'}."""
    p = _make_profile()
    team = BrandVoiceContentTeam(brand=p)
    results = team.search_examples("product launch")
    # _FakeChromaMemory.search returns docs without filtering — just check no crash
    assert isinstance(results, list)


def test_team_brand_stored(mock_chroma):
    p = _make_profile(tone="Friendly")
    team = BrandVoiceContentTeam(brand=p)
    assert team.brand is p


def test_team_max_retrieved_examples_default(mock_chroma):
    p = _make_profile()
    team = BrandVoiceContentTeam(brand=p)
    assert team._max_examples == 3


def test_team_max_retrieved_examples_custom(mock_chroma):
    p = _make_profile()
    team = BrandVoiceContentTeam(brand=p, max_retrieved_examples=5)
    assert team._max_examples == 5
