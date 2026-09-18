"""Tests for COBOL parser, analyzer, augment, and AS400Connector helpers."""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_FORMAT_COBOL = textwrap.dedent("""\
    000100 IDENTIFICATION DIVISION.
    000200 PROGRAM-ID. ORDPRC.
    000300 AUTHOR. IAGO PUEYO.
    000400 DATE-WRITTEN. 2024-01-15.
    000500 ENVIRONMENT DIVISION.
    000600 DATA DIVISION.
    000700 WORKING-STORAGE SECTION.
    000800 01 WS-ORDER-ID         PIC 9(10).
    000900 01 WS-AMOUNT           PIC 9(7)V99.
    001000 01 WS-STATUS           PIC X(2).
    001100 LINKAGE SECTION.
    001200 01 LK-INPUT-AMOUNT     PIC 9(7)V99.
    001300 01 LK-OUTPUT-FLAG      PIC X(1).
    001400 PROCEDURE DIVISION USING LK-INPUT-AMOUNT LK-OUTPUT-FLAG.
    001500 MAIN-PARA.
    001600     MOVE 0 TO WS-ORDER-ID.
    001700     CALL 'VALDATE' USING WS-ORDER-ID.
    001800     COPY COMMONLIB.
    001900     STOP RUN.
""")

FREE_FORMAT_COBOL = textwrap.dedent("""\
    IDENTIFICATION DIVISION.
    PROGRAM-ID. FREEFORM.
    AUTHOR. TEST.
    DATA DIVISION.
    WORKING-STORAGE SECTION.
    01 WS-NAME PIC X(50).
    PROCEDURE DIVISION.
    START-PARA.
        MOVE "HELLO" TO WS-NAME
        STOP RUN.
""")

COPYBOOK = textwrap.dedent("""\
    01 CUSTOMER-RECORD.
       05 CUST-ID     PIC 9(8).
       05 CUST-NAME   PIC X(40).
       05 CUST-CITY   PIC X(20).
""")


@pytest.fixture
def fixed_cbl(tmp_path: Path) -> Path:
    f = tmp_path / "ORDPRC.cbl"
    f.write_text(FIXED_FORMAT_COBOL, encoding="utf-8")
    return f


@pytest.fixture
def free_cbl(tmp_path: Path) -> Path:
    f = tmp_path / "FREEFORM.cbl"
    f.write_text(FREE_FORMAT_COBOL, encoding="utf-8")
    return f


@pytest.fixture
def copybook(tmp_path: Path) -> Path:
    f = tmp_path / "CUSTRECORD.cpy"
    f.write_text(COPYBOOK, encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# CobolParser tests
# ---------------------------------------------------------------------------

class TestCobolParser:
    def test_fixed_format_program_id(self, fixed_cbl: Path) -> None:
        from antcrew_engine.documentation.parsers.cobol import CobolParser
        doc = CobolParser().parse(str(fixed_cbl))
        assert doc.metadata["program_id"] == "ORDPRC"

    def test_fixed_format_author(self, fixed_cbl: Path) -> None:
        from antcrew_engine.documentation.parsers.cobol import CobolParser
        doc = CobolParser().parse(str(fixed_cbl))
        assert "IAGO" in doc.metadata.get("author", "")

    def test_fixed_format_call_detected(self, fixed_cbl: Path) -> None:
        from antcrew_engine.documentation.parsers.cobol import CobolParser
        doc = CobolParser().parse(str(fixed_cbl))
        assert "VALDATE" in doc.metadata.get("called_programs", [])

    def test_fixed_format_copy_detected(self, fixed_cbl: Path) -> None:
        from antcrew_engine.documentation.parsers.cobol import CobolParser
        doc = CobolParser().parse(str(fixed_cbl))
        assert "COMMONLIB" in doc.metadata.get("copybooks", [])

    def test_free_format_parses(self, free_cbl: Path) -> None:
        from antcrew_engine.documentation.parsers.cobol import CobolParser
        doc = CobolParser().parse(str(free_cbl))
        assert doc.metadata["program_id"] == "FREEFORM"

    def test_copybook_flag(self, copybook: Path) -> None:
        from antcrew_engine.documentation.parsers.cobol import CobolParser
        doc = CobolParser().parse(str(copybook))
        assert doc.metadata.get("is_copybook") is True

    def test_content_is_human_readable(self, fixed_cbl: Path) -> None:
        from antcrew_engine.documentation.parsers.cobol import CobolParser
        doc = CobolParser().parse(str(fixed_cbl))
        assert "ORDPRC" in doc.content
        assert "Working-Storage" in doc.content or "WORKING-STORAGE" in doc.content.upper()

    def test_unreadable_file_returns_error_doc(self, tmp_path: Path) -> None:
        from antcrew_engine.documentation.parsers.cobol import CobolParser
        missing = tmp_path / "missing.cbl"
        doc = CobolParser().parse(str(missing))
        assert "unreadable" in doc.content.lower()


# ---------------------------------------------------------------------------
# COBOLAnalyzer tests
# ---------------------------------------------------------------------------

class TestCOBOLAnalyzer:
    def test_analyze_returns_analysis(self, fixed_cbl: Path) -> None:
        from antcrew.augment.cobol.analyzer import COBOLAnalyzer
        analysis = COBOLAnalyzer().analyze(str(fixed_cbl))
        assert analysis.program_id == "ORDPRC"

    def test_external_calls(self, fixed_cbl: Path) -> None:
        from antcrew.augment.cobol.analyzer import COBOLAnalyzer
        analysis = COBOLAnalyzer().analyze(str(fixed_cbl))
        calls = {c.target for c in analysis.external_calls}
        assert "VALDATE" in calls

    def test_copy_statements(self, fixed_cbl: Path) -> None:
        from antcrew.augment.cobol.analyzer import COBOLAnalyzer
        analysis = COBOLAnalyzer().analyze(str(fixed_cbl))
        copies = {c.target for c in analysis.external_calls if c.kind == "COPY"}
        assert "COMMONLIB" in copies

    def test_summary_contains_program_id(self, fixed_cbl: Path) -> None:
        from antcrew.augment.cobol.analyzer import COBOLAnalyzer
        analysis = COBOLAnalyzer().analyze(str(fixed_cbl))
        assert "ORDPRC" in analysis.summary()


# ---------------------------------------------------------------------------
# AIGenerator tests
# ---------------------------------------------------------------------------

class TestAIGenerator:
    def test_static_generate_produces_class(self, fixed_cbl: Path) -> None:
        from antcrew.augment.cobol.ai_generator import AIGenerator
        from antcrew.augment.cobol.analyzer import COBOLAnalyzer
        analysis = COBOLAnalyzer().analyze(str(fixed_cbl))
        code = AIGenerator().generate(analysis, "Add ML fraud scoring")
        assert "class Ordprc" in code or "class ORDPRC" in code or "OrdprcAI" in code

    def test_static_generate_contains_requirement(self, fixed_cbl: Path) -> None:
        from antcrew.augment.cobol.ai_generator import AIGenerator
        from antcrew.augment.cobol.analyzer import COBOLAnalyzer
        analysis = COBOLAnalyzer().analyze(str(fixed_cbl))
        code = AIGenerator().generate(analysis, "Add ML fraud scoring")
        assert "fraud" in code.lower()

    def test_llm_generate_fallback_on_exception(self, fixed_cbl: Path) -> None:
        from antcrew.augment.cobol.ai_generator import AIGenerator
        from antcrew.augment.cobol.analyzer import COBOLAnalyzer
        bad_llm = MagicMock()
        bad_llm.invoke.side_effect = RuntimeError("LLM error")
        analysis = COBOLAnalyzer().analyze(str(fixed_cbl))
        code = AIGenerator(llm=bad_llm).generate(analysis, "requirement")
        assert "class" in code.lower()


# ---------------------------------------------------------------------------
# Integrator tests
# ---------------------------------------------------------------------------

class TestIntegrator:
    def test_generate_caller_contains_bridge(self, fixed_cbl: Path) -> None:
        from antcrew.augment.cobol.analyzer import COBOLAnalyzer
        from antcrew.augment.cobol.integrator import Integrator
        analysis = COBOLAnalyzer().analyze(str(fixed_cbl))
        caller = Integrator().generate_caller(analysis)
        assert "PROGRAM-ID" in caller
        assert "ANTCREW-SEND" in caller

    def test_generate_guide_is_markdown(self, fixed_cbl: Path) -> None:
        from antcrew.augment.cobol.analyzer import COBOLAnalyzer
        from antcrew.augment.cobol.integrator import Integrator
        analysis = COBOLAnalyzer().analyze(str(fixed_cbl))
        guide = Integrator().generate_guide(analysis, "")
        assert "## Architecture" in guide
        assert "## Integration Options" in guide


# ---------------------------------------------------------------------------
# COBOLAugment (facade) tests
# ---------------------------------------------------------------------------

class TestCOBOLAugment:
    def test_augment_returns_result(self, fixed_cbl: Path) -> None:
        from antcrew.augment.cobol import COBOLAugment
        result = COBOLAugment().augment(str(fixed_cbl), "Add fraud scoring")
        assert result.python_wrapper
        assert result.cobol_caller
        assert result.deployment_guide

    def test_augment_result_repr(self, fixed_cbl: Path) -> None:
        from antcrew.augment.cobol import COBOLAugment
        result = COBOLAugment().augment(str(fixed_cbl), "Test")
        assert "ORDPRC" in repr(result)


# ---------------------------------------------------------------------------
# AS400Connector unit tests (no real DB)
# ---------------------------------------------------------------------------

class TestAS400ConnectorInit:
    def test_requires_dsn_or_connection_string(self) -> None:
        from antcrew.integrations.as400 import AS400Connector
        with pytest.raises(ValueError, match="dsn.*connection_string"):
            AS400Connector()

    def test_valid_with_dsn(self) -> None:
        from antcrew.integrations.as400 import AS400Connector
        conn = AS400Connector(dsn="MY_DSN", username="user", password="pass")
        assert conn._dsn == "MY_DSN"

    def test_valid_with_connection_string(self) -> None:
        from antcrew.integrations.as400 import AS400Connector
        conn = AS400Connector(connection_string="Driver={IBM i Access};System=localhost;")
        assert "IBM" in conn._conn_str

    def test_split_table_with_library(self) -> None:
        from antcrew.integrations.as400 import AS400Connector
        conn = AS400Connector(dsn="X")
        lib, tbl = conn._split_table("ORDLIB.ORDHDRF")
        assert lib == "ORDLIB"
        assert tbl == "ORDHDRF"

    def test_split_table_without_library(self) -> None:
        from antcrew.integrations.as400 import AS400Connector
        conn = AS400Connector(dsn="X", library="MYLIB")
        lib, tbl = conn._split_table("ORDHDRF")
        assert lib == "MYLIB"
        assert tbl == "ORDHDRF"

    def test_pyodbc_import_error_on_connect(self) -> None:
        from antcrew.integrations.as400 import AS400Connector
        conn = AS400Connector(dsn="X")
        with patch.dict("sys.modules", {"pyodbc": None}):
            with pytest.raises(ImportError, match="pyodbc"):
                conn._connect()


class TestDB2ToPic:
    def test_char_maps_to_x(self) -> None:
        from antcrew.integrations.as400 import _db2_to_pic, ColumnInfo
        col = ColumnInfo(name="C", data_type="CHAR", length=10)
        assert _db2_to_pic(col) == "X(10)"

    def test_decimal_maps_to_9v(self) -> None:
        from antcrew.integrations.as400 import _db2_to_pic, ColumnInfo
        col = ColumnInfo(name="D", data_type="DECIMAL", length=9, scale=2)
        assert "V" in _db2_to_pic(col)

    def test_integer_maps_to_comp(self) -> None:
        from antcrew.integrations.as400 import _db2_to_pic, ColumnInfo
        col = ColumnInfo(name="I", data_type="INTEGER", length=4)
        assert "COMP" in _db2_to_pic(col)


# ---------------------------------------------------------------------------
# Schema — org_type / CobolSupport
# ---------------------------------------------------------------------------

class TestSchemaLegacySupport:
    def test_org_type_legacy_enables_cobol_support(self) -> None:
        from antcrew_engine.documentation.schema import DocumentationSchemaRegistry
        reg = DocumentationSchemaRegistry()
        reg.load_from_dict({
            "documentation_schema": {
                "org_type": "legacy",
                "if_legacy": {
                    "cobol_support": {
                        "as400_connection": True,
                        "copybook_parsing": True,
                        "batch_job_docs": True,
                    }
                }
            }
        })
        assert reg.is_legacy
        assert reg.cobol_support is not None
        assert reg.cobol_support.as400_connection is True

    def test_org_type_microservices_no_cobol_support(self) -> None:
        from antcrew_engine.documentation.schema import DocumentationSchemaRegistry
        reg = DocumentationSchemaRegistry()
        reg.load_from_dict({"documentation_schema": {"org_type": "microservices"}})
        assert not reg.is_legacy
        assert reg.cobol_support is None

    def test_cobol_support_explicit_enable(self) -> None:
        from antcrew_engine.documentation.schema import DocumentationSchemaRegistry
        reg = DocumentationSchemaRegistry()
        reg.load_from_dict({
            "documentation_schema": {
                "org_type": "mixed",
                "if_legacy": {"cobol_support": {"enable": True}},
            }
        })
        assert reg.cobol_support is not None
        assert reg.cobol_support.enabled
