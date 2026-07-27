"""Tests for ArtifactContract, resolve_artifact_schema, and output_schema in TemplateAgent (v0.11.10)."""
from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from antcrew.core.artifacts import (
    ARTIFACT_REGISTRY,
    PRD,
    ArtifactContract,
    CodeArtifact,
    ContractError,
    resolve_artifact_schema,
)
from antcrew.models.simulated import SimulatedLLM

# ---------------------------------------------------------------------------
# resolve_artifact_schema
# ---------------------------------------------------------------------------

class TestResolveArtifactSchema:
    def test_built_in_prd(self):
        cls = resolve_artifact_schema("PRD")
        assert cls is PRD

    def test_built_in_code_artifact(self):
        cls = resolve_artifact_schema("CodeArtifact")
        assert cls is CodeArtifact

    def test_all_built_ins_resolve(self):
        for name in ARTIFACT_REGISTRY:
            cls = resolve_artifact_schema(name)
            assert issubclass(cls, BaseModel)

    def test_dotted_path(self):
        cls = resolve_artifact_schema("antcrew.core.artifacts.PRD")
        assert cls is PRD

    def test_unknown_name_raises(self):
        with pytest.raises(ValueError, match="Unknown artifact schema"):
            resolve_artifact_schema("NonExistentModel")

    def test_bad_dotted_path_module_raises(self):
        with pytest.raises(ValueError, match="Cannot import"):
            resolve_artifact_schema("no.such.module.MyClass")

    def test_bad_dotted_path_class_raises(self):
        with pytest.raises(ValueError, match="no class"):
            resolve_artifact_schema("antcrew.core.artifacts.NoSuchClass")

    def test_dotted_path_non_basemodel_raises(self):
        with pytest.raises(ValueError, match="Pydantic BaseModel"):
            resolve_artifact_schema("antcrew.core.artifacts.Priority")

    def test_registry_has_expected_schemas(self):
        for name in ("PRD", "CodeArtifact", "TestArtifact", "CodeReview",
                     "ResearchDocument", "DevOpsArtifact", "DocumentationArtifact",
                     "ContentPiece", "CodebaseAnalysis", "Ticket"):
            assert name in ARTIFACT_REGISTRY


# ---------------------------------------------------------------------------
# ArtifactContract
# ---------------------------------------------------------------------------

class TestArtifactContract:
    def _make_prd(self) -> PRD:
        return PRD(title="Auth", summary="JWT auth system", goals=["secure login"])

    def test_extract_from_model_instance(self):
        contract = ArtifactContract("prd", PRD)
        prd = self._make_prd()
        state = {"prd": prd}
        result = contract.extract(state)
        assert isinstance(result, PRD)
        assert result.title == "Auth"

    def test_extract_from_dict(self):
        contract = ArtifactContract("prd", PRD)
        prd = self._make_prd()
        state = {"prd": prd.model_dump()}
        result = contract.extract(state)
        assert isinstance(result, PRD)
        assert result.title == "Auth"

    def test_extract_from_json_string(self):
        contract = ArtifactContract("prd", PRD)
        prd = self._make_prd()
        state = {"prd": prd.model_dump_json()}
        result = contract.extract(state)
        assert isinstance(result, PRD)
        assert result.title == "Auth"

    def test_extract_missing_key_raises(self):
        contract = ArtifactContract("prd", PRD)
        with pytest.raises(ContractError, match="Missing required artifact"):
            contract.extract({})

    def test_extract_invalid_dict_raises(self):
        contract = ArtifactContract("prd", PRD)
        # Missing required fields (title, summary)
        with pytest.raises(ContractError, match="Cannot parse"):
            contract.extract({"prd": {"title": "only title"}})

    def test_extract_unsupported_type_raises(self):
        contract = ArtifactContract("prd", PRD)
        with pytest.raises(ContractError, match="Unsupported type"):
            contract.extract({"prd": 12345})

    def test_inject_returns_dict(self):
        contract = ArtifactContract("prd", PRD)
        prd = self._make_prd()
        result = contract.inject(prd)
        assert isinstance(result, dict)
        assert "prd" in result
        assert isinstance(result["prd"], dict)  # model_dump() → JSON-serializable
        assert result["prd"]["title"] == "Auth"

    def test_inject_wrong_type_raises(self):
        contract = ArtifactContract("prd", PRD)
        with pytest.raises(ContractError, match="inject\\(\\)"):
            contract.inject("not a PRD")  # type: ignore

    def test_inject_then_extract_roundtrip(self):
        contract = ArtifactContract("prd", PRD)
        prd = self._make_prd()
        state = contract.inject(prd)
        recovered = contract.extract(state)
        assert recovered.title == prd.title
        assert recovered.summary == prd.summary

    def test_repr(self):
        contract = ArtifactContract("prd", PRD)
        assert "prd" in repr(contract)
        assert "PRD" in repr(contract)

    def test_key_stored_correctly(self):
        contract = ArtifactContract("code_artifacts", CodeArtifact)
        assert contract.key == "code_artifacts"
        assert contract.model is CodeArtifact


# ---------------------------------------------------------------------------
# TemplateAgent — output_schema
# ---------------------------------------------------------------------------

class TestTemplateAgentOutputSchema:
    """Tests for output_schema: in TemplateAgent config."""

    def _agent_with_schema(self, schema_name: str):
        from antcrew.agents.template_agent import TemplateAgent
        return TemplateAgent(
            {
                "name": "pm",
                "system_prompt": "Produce a PRD for the feature: {request}",
                "output_key": "prd",
                "output_schema": schema_name,
                "output_parse_retries": 1,
            },
            llm=SimulatedLLM(),
        )

    def test_output_schema_resolves_on_init(self):
        agent = self._agent_with_schema("PRD")
        assert agent._output_schema is PRD

    def test_output_schema_raises_on_invalid_json(self):
        """When the LLM fails to produce valid schema JSON, the contract raises."""
        agent = self._agent_with_schema("PRD")
        # SimulatedLLM does not return valid PRD JSON → should raise after retries
        with pytest.raises(Exception):
            agent.run({"request": "Build login"})

    def test_output_schema_dotted_path(self):
        from antcrew.agents.template_agent import TemplateAgent
        agent = TemplateAgent(
            {
                "name": "pm",
                "system_prompt": "Produce a PRD: {request}",
                "output_key": "prd",
                "output_schema": "antcrew.core.artifacts.PRD",
            },
            llm=SimulatedLLM(),
        )
        assert agent._output_schema is PRD

    def test_output_schema_unknown_raises_on_init(self):
        from antcrew.agents.template_agent import TemplateAgent
        with pytest.raises(ValueError, match="Unknown artifact schema"):
            TemplateAgent(
                {
                    "name": "agent",
                    "system_prompt": "Do stuff.",
                    "output_schema": "NonExistentModel",
                },
                llm=SimulatedLLM(),
            )

    def test_no_output_schema_by_default(self):
        from antcrew.agents.template_agent import TemplateAgent
        agent = TemplateAgent(
            {"name": "a", "system_prompt": "Do X."},
            llm=SimulatedLLM(),
        )
        assert agent._output_schema is None

    def test_output_schema_with_json_roundtrip(self):
        """When LLM returns valid PRD JSON, result is stored as dict."""
        from unittest.mock import patch

        from antcrew.agents.template_agent import TemplateAgent

        prd_data = {
            "title": "JWT Auth",
            "summary": "Secure JWT authentication",
            "goals": ["Stateless sessions"],
            "out_of_scope": [],
            "functional_requirements": ["POST /login returns JWT"],
            "non_functional_requirements": [],
            "open_questions": [],
        }
        prd_json = json.dumps(prd_data)

        agent = TemplateAgent(
            {
                "name": "pm",
                "system_prompt": "Write a PRD.",
                "output_key": "prd",
                "output_schema": "PRD",
            },
            llm=SimulatedLLM(),
        )

        with patch.object(agent, "system", return_value=prd_json):
            result = agent.run({"request": "Auth"})

        assert isinstance(result["prd"], dict)
        assert result["prd"]["title"] == "JWT Auth"
        # Should be extractable via ArtifactContract
        contract = ArtifactContract("prd", PRD)
        prd = contract.extract(result)
        assert prd.title == "JWT Auth"

    def test_output_schema_with_contract_in_pipeline(self):
        """output_schema result flows into ArtifactContract.extract() downstream."""
        from unittest.mock import patch

        from antcrew.agents.template_agent import TemplateAgent

        prd_data = {
            "title": "Feature X",
            "summary": "A new feature",
            "goals": [],
            "out_of_scope": [],
            "functional_requirements": [],
            "non_functional_requirements": [],
            "open_questions": [],
        }

        agent = TemplateAgent(
            {"name": "pm", "system_prompt": "Write PRD.", "output_key": "prd",
             "output_schema": "PRD"},
            llm=SimulatedLLM(),
        )

        with patch.object(agent, "system", return_value=json.dumps(prd_data)):
            state = agent.run({"request": "Feature X"})

        prd_contract = ArtifactContract("prd", PRD)
        prd = prd_contract.extract(state)
        assert prd.title == "Feature X"

    def test_output_schema_with_yaml_config(self, tmp_path):
        """output_schema works end-to-end from YAML file."""

        from antcrew.agents.template_agent import load_template_agent

        yaml_content = """
name: pm
system_prompt: "Write a product requirements document for: {request}"
output_key: prd
output_schema: PRD
output_parse_retries: 0
"""
        yaml_file = tmp_path / "pm.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        llm = SimulatedLLM()
        agent = load_template_agent(str(yaml_file), llm)
        assert agent._output_schema is PRD


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

class TestPublicExports:
    def test_artifact_contract_exported(self):
        from antcrew import ArtifactContract
        assert ArtifactContract is not None

    def test_contract_error_exported(self):
        from antcrew import ContractError
        assert ContractError is not None

    def test_artifact_registry_exported(self):
        from antcrew import ARTIFACT_REGISTRY
        assert "PRD" in ARTIFACT_REGISTRY

    def test_resolve_artifact_schema_exported(self):
        from antcrew import resolve_artifact_schema
        assert resolve_artifact_schema("PRD") is PRD

    def test_prd_exported(self):
        from antcrew import PRD
        assert PRD is not None

    def test_code_artifact_exported(self):
        from antcrew import CodeArtifact
        assert CodeArtifact is not None

    def test_research_document_exported(self):
        from antcrew import ResearchDocument
        assert ResearchDocument is not None
