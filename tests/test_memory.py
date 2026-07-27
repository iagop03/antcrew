"""Tests for semantic memory — InMemoryMemory, BaseMemory.store_run(), agent integration."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from antcrew.memory.store import InMemoryMemory, MemoryResult

# ===========================================================================
# InMemoryMemory — basic CRUD
# ===========================================================================

def test_add_and_count():
    mem = InMemoryMemory()
    assert mem.count() == 0
    mem.add("hello world", {"type": "test"})
    assert mem.count() == 1


def test_add_returns_unique_ids():
    mem = InMemoryMemory()
    id_a = mem.add("text a", {})
    id_b = mem.add("text b", {})
    assert id_a != id_b


def test_clear_resets_count():
    mem = InMemoryMemory()
    mem.add("x", {})
    mem.add("y", {})
    mem.clear()
    assert mem.count() == 0


def test_search_empty_store_returns_empty():
    mem = InMemoryMemory()
    assert mem.search("anything") == []


def test_search_empty_query_returns_empty():
    mem = InMemoryMemory()
    mem.add("some text", {})
    assert mem.search("") == []


def test_search_finds_relevant_entry():
    mem = InMemoryMemory()
    mem.add("user authentication login password", {"artifact_type": "prd"})
    mem.add("database schema tables migrations", {"artifact_type": "code"})

    results = mem.search("authentication login system")
    assert len(results) >= 1
    assert "authentication" in results[0].text


def test_search_returns_memory_result_objects():
    mem = InMemoryMemory()
    mem.add("test entry", {"run_id": "abc"})
    results = mem.search("test entry")
    assert isinstance(results[0], MemoryResult)
    assert results[0].text == "test entry"
    assert results[0].metadata["run_id"] == "abc"
    assert 0 < results[0].score <= 1.0


def test_search_ranks_by_relevance():
    mem = InMemoryMemory()
    mem.add("user auth login password", {})
    mem.add("unrelated database schema", {})

    results = mem.search("login user")
    # The auth entry should score higher than the unrelated one
    assert "auth" in results[0].text or "login" in results[0].text


def test_search_respects_n_limit():
    mem = InMemoryMemory()
    for i in range(10):
        mem.add(f"test document {i}", {})

    results = mem.search("test", n=3)
    assert len(results) <= 3


def test_search_filter_by_metadata():
    mem = InMemoryMemory()
    mem.add("PRD: auth module", {"artifact_type": "prd"})
    mem.add("Code: auth.py", {"artifact_type": "code"})

    results = mem.search("auth", filter={"artifact_type": "prd"})
    assert all(r.metadata["artifact_type"] == "prd" for r in results)


# ===========================================================================
# BaseMemory.store_run()
# ===========================================================================

def _make_prd():
    prd = MagicMock()
    prd.title = "Auth Module"
    prd.summary = "Implement user authentication"
    prd.functional_requirements = ["Login", "Logout", "Password reset"]
    return prd


def _make_ticket(title="Implement login"):
    t = MagicMock()
    t.title = title
    t.description = "Build the login endpoint"
    t.priority = MagicMock()
    t.priority.value = "high"
    return t


def _make_code_artifact():
    a = MagicMock()
    a.file_path = "src/auth.py"
    a.description = "Auth service module"
    a.language = "python"
    return a


def _make_review():
    r = MagicMock()
    r.verdict = "approve"
    r.summary = "Code looks good"
    r.findings = []
    return r


def test_store_run_stores_prd():
    mem = InMemoryMemory()
    state = {"prd": _make_prd(), "tickets": None, "code_artifacts": None,
             "devops_artifacts": None, "review": None,
             "research_document": None, "doc_artifacts": None}
    n = mem.store_run(state)
    assert n >= 1
    results = mem.search("auth module")
    assert any("Auth Module" in r.text for r in results)


def test_store_run_stores_tickets():
    mem = InMemoryMemory()
    state = {"prd": None, "tickets": [_make_ticket(), _make_ticket("Setup DB")],
             "code_artifacts": None, "devops_artifacts": None,
             "review": None, "research_document": None, "doc_artifacts": None}
    n = mem.store_run(state)
    assert n == 2
    results = mem.search("login")
    assert any("Implement login" in r.text for r in results)


def test_store_run_stores_code_artifacts():
    mem = InMemoryMemory()
    state = {"prd": None, "tickets": None,
             "code_artifacts": [_make_code_artifact()],
             "devops_artifacts": None, "review": None,
             "research_document": None, "doc_artifacts": None}
    n = mem.store_run(state)
    assert n == 1
    results = mem.search("auth service python")
    assert any("auth.py" in r.text for r in results)


def test_store_run_stores_review():
    mem = InMemoryMemory()
    state = {"prd": None, "tickets": None, "code_artifacts": None,
             "devops_artifacts": None, "review": _make_review(),
             "research_document": None, "doc_artifacts": None}
    n = mem.store_run(state)
    assert n == 1
    results = mem.search("approve code")
    assert any("approve" in r.text.lower() for r in results)


def test_store_run_accepts_plain_dict_state():
    """store_run works with dicts (load_state output), not just Pydantic models."""
    mem = InMemoryMemory()
    state = {
        "prd": {
            "title": "Dict PRD",
            "summary": "Testing plain dict",
            "functional_requirements": ["feature A"],
        },
        "tickets": [
            {"title": "Dict Ticket", "description": "desc", "priority": "medium"}
        ],
        "code_artifacts": None, "devops_artifacts": None, "review": None,
        "research_document": None, "doc_artifacts": None,
    }
    n = mem.store_run(state)
    assert n == 2  # 1 PRD + 1 ticket
    assert any("Dict PRD" in r.text for r in mem.search("dict PRD testing"))


def test_store_run_returns_zero_for_empty_state():
    mem = InMemoryMemory()
    state = {"prd": None, "tickets": None, "code_artifacts": None,
             "devops_artifacts": None, "review": None,
             "research_document": None, "doc_artifacts": None}
    assert mem.store_run(state) == 0


def test_store_run_tags_run_id():
    mem = InMemoryMemory()
    state = {"prd": _make_prd(), "tickets": None, "code_artifacts": None,
             "devops_artifacts": None, "review": None,
             "research_document": None, "doc_artifacts": None}
    mem.store_run(state, run_id="run-001")
    results = mem.search("auth", filter={"run_id": "run-001"})
    assert len(results) >= 1


# ===========================================================================
# Agent integration
# ===========================================================================

def test_recall_returns_empty_when_no_memory():
    from antcrew.agents.business import BusinessAnalystAgent
    from antcrew.models.simulated import SimulatedLLM

    llm = SimulatedLLM()
    agent = BusinessAnalystAgent(llm)
    assert agent.memory is None
    assert agent._recall("anything") == ""


def test_recall_returns_formatted_results():
    from antcrew.agents.business import BusinessAnalystAgent
    from antcrew.models.simulated import SimulatedLLM

    llm = SimulatedLLM()
    agent = BusinessAnalystAgent(llm)
    mem = InMemoryMemory()
    mem.add("PRD: previous auth system", {"artifact_type": "prd"})
    agent.memory = mem

    context = agent._recall("auth login")
    assert "previous auth system" in context
    assert "prd" in context


def test_business_analyst_uses_memory_in_prompt():
    """BA agent injects memory context into its LLM call when memory is set."""
    from antcrew.agents.business import BusinessAnalystAgent
    from antcrew.models.simulated import SimulatedLLM

    llm = SimulatedLLM()
    agent = BusinessAnalystAgent(llm)
    mem = InMemoryMemory()
    mem.add("PRD: Shopping cart feature\nAdds an e-commerce cart", {"artifact_type": "prd"})
    agent.memory = mem

    captured_prompts = []
    original_system = agent.llm.system

    def capturing_system(prompt, user, **kwargs):
        captured_prompts.append(prompt)
        return original_system(prompt, user, **kwargs)

    agent.llm.system = capturing_system

    state = {"request": "Build a shopping cart feature", "messages": []}
    agent.run(state)

    assert captured_prompts, "system() was never called"
    assert "Shopping cart" in captured_prompts[0] or "previous" in captured_prompts[0].lower()


# ===========================================================================
# Team integration
# ===========================================================================

def test_dev_team_accepts_memory_parameter():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.dev_team import DevTeam

    mem = InMemoryMemory()
    team = DevTeam(model=SimulatedLLM(), memory=mem)
    assert team.memory is mem


def test_dev_team_wires_memory_to_agents():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.dev_team import DevTeam

    mem = InMemoryMemory()
    team = DevTeam(model=SimulatedLLM(), memory=mem)
    for agent in team._agents.values():
        assert agent.memory is mem


def test_dev_team_stores_run_in_memory():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.dev_team import DevTeam

    mem = InMemoryMemory()
    team = DevTeam(model=SimulatedLLM(), memory=mem)
    assert mem.count() == 0

    team.run("Build a login module")
    assert mem.count() > 0


def test_team_without_memory_works_unchanged():
    """Teams without memory= still work exactly as before."""
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.dev_team import DevTeam

    team = DevTeam(model=SimulatedLLM())
    assert team.memory is None
    state = team.run("Build a simple feature")
    assert state["prd"] is not None


# ===========================================================================
# ChromaMemory — import guard (chromadb not installed in CI)
# ===========================================================================

def test_chroma_memory_raises_import_error_when_not_installed():
    from antcrew.memory.chroma import ChromaMemory

    with patch.dict("sys.modules", {"chromadb": None}):
        with pytest.raises(ImportError, match="chromadb"):
            ChromaMemory()
