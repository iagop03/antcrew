"""Tests for CoherenceAgent cross-file consistency pass."""
from __future__ import annotations

import json
from unittest.mock import patch

from antcrew.agents.coherence import (
    CoherenceAgent,
    _build_context,
    _detect_changes,
    _parse_artifacts,
)
from antcrew.core.artifacts import CodeArtifact
from antcrew.models.simulated import SimulatedLLM


def _artifact(file_path: str, content: str, ticket_id: str = "T-1") -> CodeArtifact:
    return CodeArtifact(ticket_id=ticket_id, file_path=file_path, content=content,
                        description="", language="python")


def _state(*artifacts: CodeArtifact) -> dict:
    return {"code_artifacts": list(artifacts)}


class TestBuildContext:
    def test_small_payload_unchanged(self):
        arts = [_artifact("a.py", "x=1"), _artifact("b.py", "y=2")]
        result, lang_summary = _build_context(arts)
        data = json.loads(result)
        assert len(data) == 2

    def test_truncates_large_content(self):
        big = "x" * 100_000
        arts = [_artifact("big.py", big)]
        result, _ = _build_context(arts)
        assert len(result) <= 42_000  # slightly above cap for metadata

    def test_lang_summary_single_language(self):
        arts = [_artifact("a.py", "x=1"), _artifact("b.py", "y=2")]
        _, lang_summary = _build_context(arts)
        assert "Python" in lang_summary

    def test_lang_summary_mixed_languages_warns(self):
        arts = [_artifact("a.py", "x=1"), _artifact("b.ts", "export const x = 1")]
        _, lang_summary = _build_context(arts)
        assert "Python" in lang_summary
        assert "TypeScript" in lang_summary
        assert "import conventions" in lang_summary


class TestDetectChanges:
    def test_no_change(self):
        arts = [_artifact("a.py", "x=1")]
        assert _detect_changes(arts, arts) == []

    def test_detects_changed_content(self):
        orig = [_artifact("a.py", "x=1")]
        updated = [_artifact("a.py", "x=2")]
        assert _detect_changes(orig, updated) == ["a.py"]


class TestParseArtifacts:
    def test_parses_valid_dicts(self):
        originals = [_artifact("a.py", "old")]
        raw = [{"ticket_id": "T-1", "file_path": "a.py", "content": "new",
                "description": "", "language": "python"}]
        result = _parse_artifacts(raw, originals)
        assert result[0].content == "new"

    def test_falls_back_on_empty_response(self):
        originals = [_artifact("a.py", "original")]
        result = _parse_artifacts([], originals)
        assert result == originals

    def test_restores_omitted_files(self):
        orig = [_artifact("a.py", "a"), _artifact("b.py", "b")]
        # agent only returns a.py
        raw = [{"ticket_id": "T-1", "file_path": "a.py", "content": "a",
                "description": "", "language": "python"}]
        result = _parse_artifacts(raw, orig)
        paths = {a.file_path for a in result}
        assert "b.py" in paths


class TestCoherenceAgent:
    def test_skips_single_file(self):
        agent = CoherenceAgent(SimulatedLLM())
        state = _state(_artifact("a.py", "x=1"))
        result = agent.run(state)
        assert "skipped" in result["messages"][0]["content"].lower()
        assert result["coherence_issues"] == []

    def test_returns_updated_artifacts_on_issues(self):
        fixed_json = json.dumps([
            {"ticket_id": "T-1", "file_path": "a.py", "content": "fixed_a",
             "description": "", "language": "python"},
            {"ticket_id": "T-1", "file_path": "b.py", "content": "original_b",
             "description": "", "language": "python"},
        ])
        agent = CoherenceAgent(SimulatedLLM())
        with patch.object(agent, "system", return_value=fixed_json):
            state = _state(_artifact("a.py", "broken_a"), _artifact("b.py", "original_b"))
            result = agent.run(state)

        assert result["coherence_issues"] == ["a.py"]
        contents = {a.file_path: a.content for a in result["code_artifacts"]}
        assert contents["a.py"] == "fixed_a"
        assert contents["b.py"] == "original_b"

    def test_no_issues_message(self):
        orig_json = json.dumps([
            {"ticket_id": "T-1", "file_path": "a.py", "content": "x=1",
             "description": "", "language": "python"},
            {"ticket_id": "T-1", "file_path": "b.py", "content": "y=2",
             "description": "", "language": "python"},
        ])
        agent = CoherenceAgent(SimulatedLLM())
        with patch.object(agent, "system", return_value=orig_json):
            state = _state(_artifact("a.py", "x=1"), _artifact("b.py", "y=2"))
            result = agent.run(state)

        assert result["coherence_issues"] == []
        assert "No issues" in result["messages"][0]["content"]


# ---------------------------------------------------------------------------
# AN-P03 — CoherenceAgent: graceful handling of None / empty code_artifacts
# ---------------------------------------------------------------------------

class TestCoherenceAgentEdgeCases:
    def test_none_code_artifacts_does_not_raise(self):
        """State with code_artifacts=None must be handled without raising."""
        agent = CoherenceAgent(SimulatedLLM())
        result = agent.run({"code_artifacts": None})
        # No exception — result must be a dict
        assert isinstance(result, dict)
        assert result.get("coherence_issues", []) == []

    def test_empty_list_code_artifacts_skips_review(self):
        """Empty list is treated as a single/skip case — no LLM call, no issues reported."""
        agent = CoherenceAgent(SimulatedLLM())
        result = agent.run({"code_artifacts": []})
        assert isinstance(result, dict)
        assert result.get("coherence_issues", []) == []

    def test_single_artifact_skips_cross_file_review(self):
        """One artifact → coherence pass is skipped (no cross-file issues possible)."""
        agent = CoherenceAgent(SimulatedLLM())
        result = agent.run({"code_artifacts": [_artifact("only.py", "x=1")]})
        assert result.get("coherence_issues") == []
        assert "skipped" in result["messages"][0]["content"].lower()
