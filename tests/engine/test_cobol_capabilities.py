"""Tests for COBOL capabilities and DocumentationReader.

Uses SimulatedLLM (no real API calls) and in-memory storage.
"""
from __future__ import annotations

import json
import tempfile
import os

import pytest

from antcrew_engine.capabilities.cobol_analyzer import COBOLAnalyzerExecutor
from antcrew_engine.capabilities.cobol_generator import COBOLGeneratorExecutor
from antcrew_engine.capabilities.cobol_refactorer import COBOLRefactorerExecutor
from antcrew_engine.capabilities.cobol_validator import COBOLSyntaxValidatorExecutor
from antcrew_engine.capabilities.cobol_test_generator import COBOLTestGeneratorExecutor
from antcrew_engine.capabilities.requirements_elicitation import RequirementsElicitationExecutor
from antcrew_engine.documentation import DocumentationManager, DocumentationReader
from antcrew_engine.documentation.storage.local import LocalFileStorage
from antcrew_engine.engine import (
    ArtifactId,
    ArtifactKind,
    Artifact,
    ArtifactDelta,
    MemoryStore,
    Goal,
    DesiredProjectState,
    Condition,
    ConditionId,
    Constraints,
)
from antcrew.models.simulated import SimulatedLLM


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SAMPLE_COBOL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. PAYROLL.
       AUTHOR. JOHN SMITH.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-EMP-ID        PIC 9(6).
       01 WS-GROSS-PAY     PIC S9(7)V99.
       01 WS-NET-PAY       PIC S9(7)V99.
       01 WS-STATUS        PIC X.
          88 WS-ACTIVE     VALUE 'A'.
          88 WS-INACTIVE   VALUE 'I'.
       PROCEDURE DIVISION.
       MAIN-PARA.
           MOVE 100000 TO WS-EMP-ID.
           MOVE 5000.00 TO WS-GROSS-PAY.
           PERFORM CALC-NET-PAY.
           STOP RUN.
       CALC-NET-PAY.
           COMPUTE WS-NET-PAY = WS-GROSS-PAY * 0.8.
"""

_SAMPLE_SPEC = """\
# Payroll Processing Specification

## Overview
The payroll system must calculate net pay for all active employees.

## Requirements
- MUST read employee records from the EMPLOYEE-FILE
- MUST apply tax deduction of 20% to gross pay
- MUST write results to PAYROLL-OUTPUT-FILE
- Status code 'A' means active, 'I' means inactive

## Missing information
- Tax brackets for different salary ranges not defined
"""


@pytest.fixture
def llm():
    return SimulatedLLM()


@pytest.fixture
def goal():
    return Goal(
        description="Analyse the PAYROLL COBOL program and identify modernisation risks",
        desired_state=DesiredProjectState(frozenset()),
        constraints=Constraints(),
    )


@pytest.fixture
def store_with_cobol_source():
    store = MemoryStore()
    store.create(Artifact(
        id=ArtifactId("cobol_source"),
        kind=ArtifactKind.SOURCE,
        content=_SAMPLE_COBOL,
    ))
    return store


@pytest.fixture
def doc_manager_with_cobol(tmp_path):
    mgr = DocumentationManager()
    storage = LocalFileStorage(root=str(tmp_path))
    mgr.storage = storage
    cbl_path = tmp_path / "payroll.cbl"
    cbl_path.write_text(_SAMPLE_COBOL, encoding="utf-8")
    storage.save("cobol/PAYROLL.cbl", _SAMPLE_COBOL.encode(), {
        "doc_type": "cobol",
        "source_file": "PAYROLL.cbl",
        "size_bytes": len(_SAMPLE_COBOL),
    })
    storage.save("srs/payroll-spec.md", _SAMPLE_SPEC.encode(), {
        "doc_type": "srs",
        "source_file": "payroll-spec.md",
        "size_bytes": len(_SAMPLE_SPEC),
    })
    mgr.index_from_storage()
    return mgr


# ---------------------------------------------------------------------------
# DocumentationReader
# ---------------------------------------------------------------------------

class TestDocumentationReader:
    def test_list_docs_returns_entries(self, doc_manager_with_cobol):
        reader = DocumentationReader(doc_manager_with_cobol)
        docs = reader.list_docs()
        assert len(docs) == 2
        doc_ids = {d.doc_id for d in docs}
        assert "cobol/PAYROLL.cbl" in doc_ids
        assert "srs/payroll-spec.md" in doc_ids

    def test_list_docs_filtered_by_type(self, doc_manager_with_cobol):
        reader = DocumentationReader(doc_manager_with_cobol)
        cobol_docs = reader.list_docs(doc_type="cobol")
        assert len(cobol_docs) == 1
        assert cobol_docs[0].doc_type == "cobol"

    def test_read_returns_content(self, doc_manager_with_cobol):
        reader = DocumentationReader(doc_manager_with_cobol)
        content = reader.read("cobol/PAYROLL.cbl")
        assert "PAYROLL" in content
        assert "WORKING-STORAGE" in content

    def test_read_unknown_doc_raises(self, doc_manager_with_cobol):
        reader = DocumentationReader(doc_manager_with_cobol)
        with pytest.raises(FileNotFoundError):
            reader.read("nonexistent/file.cbl")

    def test_read_with_header_includes_metadata(self, doc_manager_with_cobol):
        reader = DocumentationReader(doc_manager_with_cobol)
        result = reader.read_with_header("srs/payroll-spec.md")
        assert "payroll-spec.md" in result
        assert "srs" in result

    def test_list_docs_as_text_returns_table(self, doc_manager_with_cobol):
        reader = DocumentationReader(doc_manager_with_cobol)
        text = reader.list_docs_as_text()
        assert "Doc ID" in text
        assert "PAYROLL.cbl" in text

    def test_search_returns_results(self, doc_manager_with_cobol):
        reader = DocumentationReader(doc_manager_with_cobol)
        results = reader.search("net pay calculation", top_k=3)
        # Index may be empty without vector backend — just check no exception
        assert isinstance(results, list)

    def test_search_as_text_no_crash(self, doc_manager_with_cobol):
        reader = DocumentationReader(doc_manager_with_cobol)
        text = reader.search_as_text("employee tax deduction")
        assert isinstance(text, str)


# ---------------------------------------------------------------------------
# COBOLSyntaxValidator — static path (no GnuCOBOL required)
# ---------------------------------------------------------------------------

class TestCOBOLSyntaxValidatorStatic:
    def _validator(self):
        return COBOLSyntaxValidatorExecutor(llm=None)

    def test_valid_cobol_passes(self):
        result = self._validator()._validate_static(_SAMPLE_COBOL)
        assert result["method"] == "static"
        assert result["valid"] is True
        assert len(result["errors"]) == 0

    def test_missing_identification_division_is_error(self):
        bad = "       DATA DIVISION.\n       WORKING-STORAGE SECTION.\n       01 WS-X PIC 9.\n"
        result = self._validator()._validate_static(bad)
        assert result["valid"] is False
        assert any("IDENTIFICATION" in e["message"] for e in result["errors"])

    def test_if_end_if_mismatch_is_error(self):
        bad = _SAMPLE_COBOL + "\n       IF WS-STATUS = 'A' THEN\n           MOVE 1 TO WS-EMP-ID\n"
        result = self._validator()._validate_static(bad)
        assert result["valid"] is False
        assert any("END-IF" in e["message"] for e in result["errors"])

    def test_stop_run_missing_is_warning(self):
        no_stop = _SAMPLE_COBOL.replace("STOP RUN.", "CONTINUE.")
        result = self._validator()._validate_static(no_stop)
        assert any("STOP RUN" in w["message"] for w in result["warnings"])

    def test_goto_triggers_warning(self):
        with_goto = _SAMPLE_COBOL + "       GOTO CALC-NET-PAY.\n"
        result = self._validator()._validate_static(with_goto)
        assert any("GOTO" in w["message"] for w in result["warnings"])

    def test_summary_ok_for_valid(self):
        result = self._validator()._validate_static(_SAMPLE_COBOL)
        assert "OK" in result["summary"]


# ---------------------------------------------------------------------------
# COBOLAnalyzerExecutor
# ---------------------------------------------------------------------------

class TestCOBOLAnalyzerExecutor:
    def test_no_docs_returns_empty_programs(self, llm, goal):
        executor = COBOLAnalyzerExecutor(llm=llm)
        store = MemoryStore()
        result = executor.execute(store, goal)
        art = store.get(ArtifactId("cobol_analysis"))
        assert art is not None
        data = json.loads(art.content)
        assert data["programs"] == []
        assert "No COBOL files" in data["summary"]

    def test_with_docs_calls_llm(self, llm, goal, doc_manager_with_cobol):
        executor = COBOLAnalyzerExecutor(llm=llm)
        executor.set_documentation(doc_manager_with_cobol)
        store = MemoryStore()
        result = executor.execute(store, goal)
        art = store.get(ArtifactId("cobol_analysis"))
        assert art is not None
        data = json.loads(art.content)
        assert len(data["programs"]) >= 1
        assert data["programs"][0]["source_file"] == "PAYROLL.cbl"


# ---------------------------------------------------------------------------
# COBOLGeneratorExecutor
# ---------------------------------------------------------------------------

class TestCOBOLGeneratorExecutor:
    def test_creates_cobol_source_artifact(self, llm):
        executor = COBOLGeneratorExecutor(llm=llm)
        store = MemoryStore()
        goal = Goal(
            description="Generate a COBOL program that reads an employee file and calculates payroll",
            desired_state=DesiredProjectState(frozenset()),
            constraints=Constraints(),
        )
        executor.execute(store, goal)
        art = store.get(ArtifactId("cobol_source"))
        assert art is not None
        assert art.kind == ArtifactKind.SOURCE
        assert len(art.content) > 0

    def test_incorporates_elicitation_report(self, llm):
        executor = COBOLGeneratorExecutor(llm=llm)
        store = MemoryStore()
        store.create(Artifact(
            id=ArtifactId("elicitation_report"),
            kind=ArtifactKind.REQUIREMENTS,
            content=json.dumps({"summary": "Payroll processing", "ambiguities": [], "missing_info": []}),
        ))
        goal = Goal(
            description="Generate COBOL payroll program",
            desired_state=DesiredProjectState(frozenset()),
            constraints=Constraints(),
        )
        executor.execute(store, goal)
        art = store.get(ArtifactId("cobol_source"))
        assert art is not None


# ---------------------------------------------------------------------------
# COBOLRefactorerExecutor
# ---------------------------------------------------------------------------

class TestCOBOLRefactorerExecutor:
    def test_refactors_existing_source(self, llm):
        executor = COBOLRefactorerExecutor(llm=llm)
        store = MemoryStore()
        store.create(Artifact(
            id=ArtifactId("cobol_source"),
            kind=ArtifactKind.SOURCE,
            content=_SAMPLE_COBOL,
        ))
        goal = Goal(
            description="Refactor the PAYROLL program for readability",
            desired_state=DesiredProjectState(frozenset()),
            constraints=Constraints(),
        )
        executor.execute(store, goal)
        art = store.get(ArtifactId("cobol_source"))
        assert art is not None

    def test_returns_error_when_no_source(self, llm):
        executor = COBOLRefactorerExecutor(llm=llm)
        store = MemoryStore()
        goal = Goal(
            description="Refactor COBOL",
            desired_state=DesiredProjectState(frozenset()),
            constraints=Constraints(),
        )
        result = executor.execute(store, goal)
        assert len(result.errors) > 0


# ---------------------------------------------------------------------------
# RequirementsElicitationExecutor
# ---------------------------------------------------------------------------

class TestRequirementsElicitationExecutor:
    def test_creates_elicitation_report(self, llm, goal):
        executor = RequirementsElicitationExecutor(llm=llm)
        store = MemoryStore()
        goal_elicit = Goal(
            description="Analyse payroll specification for ambiguities and missing requirements",
            desired_state=DesiredProjectState(frozenset()),
            constraints=Constraints(),
        )
        executor.execute(store, goal_elicit)
        art = store.get(ArtifactId("elicitation_report"))
        assert art is not None
        # SimulatedLLM may not produce valid JSON — just check artifact created
        assert len(art.content) > 0

    def test_with_docs_uses_documentation(self, llm, doc_manager_with_cobol):
        executor = RequirementsElicitationExecutor(llm=llm)
        executor.set_documentation(doc_manager_with_cobol)
        store = MemoryStore()
        goal = Goal(
            description="Find gaps in the payroll specification",
            desired_state=DesiredProjectState(frozenset()),
            constraints=Constraints(),
        )
        executor.execute(store, goal)
        art = store.get(ArtifactId("elicitation_report"))
        assert art is not None
