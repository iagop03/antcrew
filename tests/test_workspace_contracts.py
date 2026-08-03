"""Tests for WorkspaceContractSchema — Phase 1 custom_fields extension."""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from antcrew.core.artifacts import (
    ArtifactContract,
    CodeArtifact,
    CodeReview,
    ContractError,
    PRD,
    TestArtifact,
    Ticket,
    WorkspaceContractSchema,
    get_workspace_schema,
    set_workspace_schema,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

class PRDExtras(BaseModel):
    target_audience: str
    brand_guidelines_url: str = ""


def _prd(**kw) -> PRD:
    return PRD(title="T", summary="S", **kw)


def _ticket(**kw) -> Ticket:
    return Ticket(id="T-1", title="T", description="D", **kw)


def _code(**kw) -> CodeArtifact:
    return CodeArtifact(ticket_id="T-1", file_path="app.py", content="x=1", **kw)


def _review(**kw) -> CodeReview:
    return CodeReview(summary="looks good", **kw)


def _test(**kw) -> TestArtifact:
    return TestArtifact(ticket_id="T-1", file_path="test_app.py", content="assert True", **kw)


# ── custom_fields field present on all main artifacts ────────────────────────

def test_prd_has_custom_fields():
    assert _prd().custom_fields == {}


def test_ticket_has_custom_fields():
    assert _ticket().custom_fields == {}


def test_code_artifact_has_custom_fields():
    assert _code().custom_fields == {}


def test_code_review_has_custom_fields():
    assert _review().custom_fields == {}


def test_test_artifact_has_custom_fields():
    assert _test().custom_fields == {}


# ── WorkspaceContractSchema — Pydantic model validation ──────────────────────

def test_pydantic_schema_valid():
    reg = WorkspaceContractSchema()
    reg.register("prd", PRDExtras)

    contract = ArtifactContract("prd", PRD, schema_registry=reg)
    prd = _prd(custom_fields={"target_audience": "developers"})
    result = contract.inject(prd)
    assert result["prd"]["custom_fields"]["target_audience"] == "developers"


def test_pydantic_schema_invalid_raises():
    reg = WorkspaceContractSchema()
    reg.register("prd", PRDExtras)

    contract = ArtifactContract("prd", PRD, schema_registry=reg)
    prd = _prd(custom_fields={"brand_guidelines_url": "http://x.com"})  # missing required field
    with pytest.raises(ContractError, match="custom_fields"):
        contract.inject(prd)


def test_pydantic_schema_empty_custom_fields_skips_validation():
    reg = WorkspaceContractSchema()
    reg.register("prd", PRDExtras)

    contract = ArtifactContract("prd", PRD, schema_registry=reg)
    prd = _prd()  # empty custom_fields — validation skipped
    result = contract.inject(prd)
    assert result["prd"]["custom_fields"] == {}


# ── WorkspaceContractSchema — JSON Schema dict validation ─────────────────────

jsonschema = pytest.importorskip("jsonschema", reason="jsonschema not installed")

_TICKET_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "story_points": {"type": "integer", "minimum": 1, "maximum": 21},
        "business_value": {"type": "string"},
    },
    "required": ["story_points"],
    "additionalProperties": False,
}


def test_json_schema_valid():
    reg = WorkspaceContractSchema()
    reg.register("ticket", _TICKET_JSON_SCHEMA)

    contract = ArtifactContract("ticket", Ticket, schema_registry=reg)
    t = _ticket(custom_fields={"story_points": 5})
    result = contract.inject(t)
    assert result["ticket"]["custom_fields"]["story_points"] == 5


def test_json_schema_invalid_type_raises():
    reg = WorkspaceContractSchema()
    reg.register("ticket", _TICKET_JSON_SCHEMA)

    contract = ArtifactContract("ticket", Ticket, schema_registry=reg)
    t = _ticket(custom_fields={"story_points": "not-an-int"})
    with pytest.raises(ContractError, match="JSON Schema"):
        contract.inject(t)


def test_json_schema_missing_required_raises():
    reg = WorkspaceContractSchema()
    reg.register("ticket", _TICKET_JSON_SCHEMA)

    contract = ArtifactContract("ticket", Ticket, schema_registry=reg)
    t = _ticket(custom_fields={"business_value": "high"})  # story_points missing
    with pytest.raises(ContractError, match="JSON Schema"):
        contract.inject(t)


def test_json_schema_additional_properties_rejected():
    reg = WorkspaceContractSchema()
    reg.register("ticket", _TICKET_JSON_SCHEMA)

    contract = ArtifactContract("ticket", Ticket, schema_registry=reg)
    t = _ticket(custom_fields={"story_points": 3, "unknown_key": "x"})
    with pytest.raises(ContractError, match="JSON Schema"):
        contract.inject(t)


# ── No schema registered = permissive ────────────────────────────────────────

def test_no_schema_allows_any_custom_fields():
    reg = WorkspaceContractSchema()  # nothing registered
    contract = ArtifactContract("prd", PRD, schema_registry=reg)
    prd = _prd(custom_fields={"anything": 42, "goes": True})
    result = contract.inject(prd)
    assert result["prd"]["custom_fields"]["anything"] == 42


def test_no_registry_allows_any_custom_fields():
    contract = ArtifactContract("prd", PRD)  # no registry at all
    prd = _prd(custom_fields={"x": 1})
    result = contract.inject(prd)
    assert result["prd"]["custom_fields"]["x"] == 1


# ── Module-level default registry ────────────────────────────────────────────

def test_set_workspace_schema_used_as_default(monkeypatch):
    reg = WorkspaceContractSchema()
    reg.register("prd", PRDExtras)

    monkeypatch.setattr("antcrew.core.artifacts._default_schema_registry", reg)

    contract = ArtifactContract("prd", PRD)  # no per-contract registry
    prd = _prd(custom_fields={"brand_guidelines_url": "x"})  # missing required field
    with pytest.raises(ContractError):
        contract.inject(prd)


def test_set_workspace_schema_roundtrip(monkeypatch):
    monkeypatch.setattr("antcrew.core.artifacts._default_schema_registry", None)
    assert get_workspace_schema() is None

    reg = WorkspaceContractSchema()
    set_workspace_schema(reg)
    assert get_workspace_schema() is reg


# ── Registry helpers ──────────────────────────────────────────────────────────

def test_registry_has():
    reg = WorkspaceContractSchema()
    assert not reg.has("prd")
    reg.register("prd", PRDExtras)
    assert reg.has("prd")


def test_registry_schema_for():
    reg = WorkspaceContractSchema()
    reg.register("prd", PRDExtras)
    assert reg.schema_for("prd") is PRDExtras
    assert reg.schema_for("ticket") is None


def test_registry_chaining():
    reg = (
        WorkspaceContractSchema()
        .register("prd", PRDExtras)
        .register("ticket", _TICKET_JSON_SCHEMA)
    )
    assert reg.has("prd")
    assert reg.has("ticket")


def test_registry_invalid_schema_type_raises():
    reg = WorkspaceContractSchema()
    with pytest.raises(ValueError, match="Pydantic BaseModel"):
        reg.register("prd", "not-a-schema")  # type: ignore[arg-type]


# ── ArtifactContract.inject type guard ───────────────────────────────────────

def test_inject_wrong_type_raises():
    contract = ArtifactContract("prd", PRD)
    with pytest.raises(ContractError, match="inject()"):
        contract.inject(_ticket())  # type: ignore[arg-type]
