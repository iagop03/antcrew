"""Tests for UC6: LegalReviewTeam + LegalFindingArtifact."""
from __future__ import annotations

import pytest
from antcrew.core.artifacts import LegalClause, LegalFindingArtifact, RiskLevel


# ---------------------------------------------------------------------------
# LegalFindingArtifact
# ---------------------------------------------------------------------------

def test_legal_finding_defaults():
    f = LegalFindingArtifact()
    assert f.document_name == ""
    assert f.document_type == ""
    assert f.clauses == []
    assert f.high_risk_count == 0
    assert f.approved is False


def test_legal_finding_high_risk_count_auto():
    f = LegalFindingArtifact(clauses=[
        LegalClause(clause_id="C1", text="...", risk_level=RiskLevel.LOW),
        LegalClause(clause_id="C2", text="...", risk_level=RiskLevel.HIGH),
        LegalClause(clause_id="C3", text="...", risk_level=RiskLevel.CRITICAL),
        LegalClause(clause_id="C4", text="...", risk_level=RiskLevel.MEDIUM),
    ])
    assert f.high_risk_count == 2


def test_legal_finding_no_high_risk():
    f = LegalFindingArtifact(clauses=[
        LegalClause(clause_id="C1", text="...", risk_level=RiskLevel.LOW),
        LegalClause(clause_id="C2", text="...", risk_level=RiskLevel.MEDIUM),
    ])
    assert f.high_risk_count == 0


def test_legal_finding_all_critical():
    f = LegalFindingArtifact(clauses=[
        LegalClause(clause_id=f"C{i}", text="...", risk_level=RiskLevel.CRITICAL)
        for i in range(5)
    ])
    assert f.high_risk_count == 5


def test_legal_finding_empty_clauses_high_risk_zero():
    f = LegalFindingArtifact(clauses=[])
    assert f.high_risk_count == 0


def test_legal_finding_approved_flag():
    f = LegalFindingArtifact(approved=True)
    assert f.approved is True


def test_legal_finding_serializes_to_dict():
    f = LegalFindingArtifact(
        document_name="NDA.pdf",
        document_type="NDA",
        summary="Low risk overall.",
        approved=True,
    )
    d = f.model_dump()
    assert d["document_name"] == "NDA.pdf"
    assert d["approved"] is True
    assert "clauses" in d


# ---------------------------------------------------------------------------
# LegalClause
# ---------------------------------------------------------------------------

def test_legal_clause_defaults():
    c = LegalClause(text="Some clause text.")
    assert c.clause_id == ""
    assert c.section == ""
    assert c.risk_level == RiskLevel.LOW
    assert c.issue == ""
    assert c.regulations == []


def test_legal_clause_with_regulations():
    c = LegalClause(
        clause_id="C3",
        text="Data shall be processed under GDPR.",
        risk_level=RiskLevel.MEDIUM,
        issue="Data processing scope unclear",
        regulations=["GDPR", "CCPA"],
    )
    assert len(c.regulations) == 2
    assert "GDPR" in c.regulations


def test_legal_clause_critical_risk():
    c = LegalClause(text="All IP assigned to client.", risk_level=RiskLevel.CRITICAL)
    assert c.risk_level == RiskLevel.CRITICAL


# ---------------------------------------------------------------------------
# RiskLevel
# ---------------------------------------------------------------------------

def test_risk_level_ordering():
    levels = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
    assert len(set(levels)) == 4


def test_risk_level_string_values():
    assert RiskLevel.LOW.value == "low"
    assert RiskLevel.MEDIUM.value == "medium"
    assert RiskLevel.HIGH.value == "high"
    assert RiskLevel.CRITICAL.value == "critical"


def test_risk_level_from_string():
    assert RiskLevel("high") == RiskLevel.HIGH
    assert RiskLevel("critical") == RiskLevel.CRITICAL


def test_risk_level_invalid():
    with pytest.raises(ValueError):
        RiskLevel("extreme")


# ---------------------------------------------------------------------------
# LegalReviewTeam — instantiation (no LLM calls)
# ---------------------------------------------------------------------------

def test_legal_review_team_instantiates():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.legal_team import LegalReviewTeam
    team = LegalReviewTeam(llm=SimulatedLLM(), document_name="Contract.pdf")
    assert team._document_name == "Contract.pdf"


def test_legal_review_team_has_three_agents():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.legal_team import LegalReviewTeam
    team = LegalReviewTeam(llm=SimulatedLLM())
    assert len(team._agents) == 3


def test_legal_review_team_agent_names():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.legal_team import LegalReviewTeam
    team = LegalReviewTeam(llm=SimulatedLLM())
    names = [a.name for a in team._agents]
    assert "clause_extractor" in names
    assert "risk_flagging" in names
    assert "legal_reviewer" in names


def test_legal_review_team_importable_from_antcrew():
    from antcrew import LegalReviewTeam, LegalFindingArtifact, LegalClause, RiskLevel  # noqa: F401
    assert LegalReviewTeam is not None


def test_legal_review_team_default_llm_not_called_at_init():
    """LegalReviewTeam should not call the LLM at instantiation time."""
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.legal_team import LegalReviewTeam
    llm = SimulatedLLM()
    team = LegalReviewTeam(llm=llm, document_type="Employment")
    assert team._document_type == "Employment"


def test_legal_review_team_simulated_run():
    """SimulatedLLM returns deterministic fake output — pipeline should not crash."""
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.legal_team import LegalReviewTeam
    team = LegalReviewTeam(llm=SimulatedLLM())
    result = team.run("This is a test contract.")
    assert "legal_finding" in result
    finding = result["legal_finding"]
    assert isinstance(finding, dict)
    assert "approved" in finding
    assert "clauses" in finding
    assert "high_risk_count" in finding


def test_legal_review_team_parse_finding_approves():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.legal_team import LegalReviewTeam
    team = LegalReviewTeam(llm=SimulatedLLM())
    raw = "OVERALL: APPROVE\nSUMMARY: Low risk contract, safe to sign."
    finding = team._parse_finding(raw, risk_raw="")
    assert finding.approved is True
    assert "Low risk" in finding.summary


def test_legal_review_team_parse_finding_rejects():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.legal_team import LegalReviewTeam
    team = LegalReviewTeam(llm=SimulatedLLM())
    raw = "OVERALL: REJECT\nSUMMARY: Critical IP clause must be removed."
    finding = team._parse_finding(raw, risk_raw="")
    assert finding.approved is False
