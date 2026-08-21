"""LegalReviewTeam — contract review and due diligence pipeline.

Three-agent pipeline:
  1. ClauseExtractor   — identifies and extracts contract clauses
  2. RiskFlagging      — assesses risk level per clause
  3. LegalReviewer     — writes recommendations and final summary

Usage::

    from antcrew import LegalReviewTeam
    from antcrew.models.anthropic_model import AnthropicModel

    team = LegalReviewTeam(llm=AnthropicModel())
    result = team.run("Review this NDA: <contract text here>")
    finding = result.get("legal_finding")  # LegalFindingArtifact dict

YAML equivalent (agentteam.yaml)::

    team: legal
    model: claude
    steps:
      - name: clause_extractor
        system_prompt: |
          You are a legal document analyst. Extract all key clauses from the
          contract provided, identifying each clause's section and full text.
          Output a numbered list: [CLAUSE-N] Section: ... | Text: ...
        output_key: clauses_raw
      - name: risk_flagging
        system_prompt: |
          You are a legal risk analyst. For each clause in {clauses_raw},
          assess the risk level (low/medium/high/critical) and identify any
          issues (liability, IP assignment, non-compete, termination, etc.).
          Format: [CLAUSE-N] Risk: <level> | Issue: ... | Regulation: ...
        input_key: clauses_raw
        output_key: risk_assessment
      - name: legal_reviewer
        system_prompt: |
          You are a senior legal reviewer. Based on the risk assessment
          {risk_assessment}, write concrete recommendations for each
          high/critical risk clause and a final approval recommendation.
        input_key: risk_assessment
        output_key: legal_finding
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from antcrew.core.supervisor import Supervisor
from antcrew.models.base import BaseLLM

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_CLAUSE_EXTRACTOR_PROMPT = """\
You are a legal document analyst specialising in contract review.
Extract all key clauses from the contract provided.
For each clause identify:
- Section / article reference
- The clause verbatim text (or a close paraphrase if very long)
- The clause type (indemnity, IP assignment, non-compete, termination,
  liability cap, governing law, confidentiality, payment, SLA, etc.)

Output format (one per line):
[CLAUSE-{n}] Section: <ref> | Type: <type> | Text: <text>
"""

_RISK_FLAGGING_PROMPT = """\
You are a legal risk analyst with expertise in corporate and regulatory law.
Given the extracted clauses below, assess each one:

{clauses_raw}

For every clause assign:
- Risk level: low | medium | high | critical
- Issue: what is the legal or business risk
- Applicable regulations (GDPR, HIPAA, SOC 2, NDA, IP law, etc.) if relevant

Output format:
[CLAUSE-{n}] Risk: <level> | Issue: <issue> | Regulations: <regs>
"""

_LEGAL_REVIEWER_PROMPT = """\
You are a senior legal reviewer. Review the risk assessment below and produce:
1. For each HIGH or CRITICAL clause: a concrete, actionable recommendation
2. A final overall recommendation: APPROVE | APPROVE WITH CHANGES | REJECT

Risk assessment:
{risk_assessment}

Format your response as:
CLAUSE RECOMMENDATIONS:
[CLAUSE-{n}] Recommendation: ...

OVERALL: <APPROVE|APPROVE WITH CHANGES|REJECT>
SUMMARY: <2-3 sentence executive summary>
"""


class LegalReviewTeam:
    """Contract review pipeline with structured LegalFindingArtifact output.

    Args:
        llm: LLM instance to use for all agents.
        document_name: Optional display name for the contract being reviewed.
        document_type: Optional contract type label (NDA, SaaS, Employment, etc.).
        hitl_on_high_risk: When True, the pipeline pauses for human approval
            before the final reviewer step when high-risk clauses are found.
    """

    def __init__(
        self,
        llm: Optional[BaseLLM] = None,
        *,
        document_name: str = "",
        document_type: str = "",
        hitl_on_high_risk: bool = False,
    ) -> None:
        if llm is None:
            from antcrew.models.anthropic_model import AnthropicModel
            llm = AnthropicModel()

        self._llm = llm
        self._document_name = document_name
        self._document_type = document_type
        self._hitl_on_high_risk = hitl_on_high_risk

        from antcrew.agents.template_agent import TemplateAgent
        self._clause_extractor = TemplateAgent(
            {"name": "clause_extractor", "system_prompt": _CLAUSE_EXTRACTOR_PROMPT,
             "output_key": "clauses_raw"},
            llm,
        )
        self._risk_flagging = TemplateAgent(
            {"name": "risk_flagging", "system_prompt": _RISK_FLAGGING_PROMPT,
             "input_key": "clauses_raw", "output_key": "risk_assessment"},
            llm,
        )
        self._legal_reviewer = TemplateAgent(
            {"name": "legal_reviewer", "system_prompt": _LEGAL_REVIEWER_PROMPT,
             "input_key": "risk_assessment", "output_key": "legal_finding_raw"},
            llm,
        )

    @property
    def _agents(self) -> list:
        return [self._clause_extractor, self._risk_flagging, self._legal_reviewer]

    def run(self, contract_text: str) -> dict:
        """Run the contract review pipeline.

        Args:
            contract_text: Full text of the contract to review.

        Returns:
            State dict with keys:
            - ``clauses_raw``: raw clause extraction output
            - ``risk_assessment``: raw risk assessment output
            - ``legal_finding_raw``: raw reviewer output
            - ``legal_finding``: parsed :class:`~antcrew.core.artifacts.LegalFindingArtifact` dict
        """
        from antcrew.core.events import new_run_id
        supervisor = Supervisor(flow=[
            ("clause_extractor", "risk_flagging"),
            ("risk_flagging", "legal_reviewer"),
        ])
        graph = supervisor.build({
            "clause_extractor": self._clause_extractor,
            "risk_flagging": self._risk_flagging,
            "legal_reviewer": self._legal_reviewer,
        })
        _run_id = new_run_id()
        thread_id = _run_id
        state = {"request": contract_text, "_run_id": _run_id, "_thread_id": thread_id}
        config = {"configurable": {"thread_id": thread_id}}
        result = graph.invoke(state, config=config)
        finding = self._parse_finding(
            result.get("legal_finding_raw", ""),
            risk_raw=result.get("risk_assessment", ""),
        )
        result["legal_finding"] = finding.model_dump()
        log.info(
            "legal_review: %d clauses, %d high/critical, approved=%s",
            len(finding.clauses),
            finding.high_risk_count,
            finding.approved,
        )
        return result

    def _parse_finding(self, raw: str, risk_raw: str = "") -> "LegalFindingArtifact":
        from antcrew.core.artifacts import LegalClause, LegalFindingArtifact, RiskLevel

        risk_text = risk_raw
        clauses: list[LegalClause] = []

        for line in risk_text.splitlines():
            line = line.strip()
            if not line.startswith("[CLAUSE-"):
                continue
            try:
                parts = {p.split(":", 1)[0].strip(): p.split(":", 1)[1].strip()
                         for p in line.split("|") if ":" in p}
                clause_id = line.split("]")[0].lstrip("[")
                risk_str = parts.get("Risk", "low").lower()
                try:
                    risk = RiskLevel(risk_str)
                except ValueError:
                    risk = RiskLevel.LOW
                clauses.append(LegalClause(
                    clause_id=clause_id,
                    text=parts.get("Text", ""),
                    risk_level=risk,
                    issue=parts.get("Issue", ""),
                    regulations=[r.strip() for r in parts.get("Regulations", "").split(",") if r.strip()],
                ))
            except Exception:
                continue

        approved = "APPROVE" in raw.upper() and "REJECT" not in raw.upper()
        summary = ""
        for line in raw.splitlines():
            if line.strip().startswith("SUMMARY:"):
                summary = line.split(":", 1)[-1].strip()
                break

        return LegalFindingArtifact(
            document_name=self._document_name,
            document_type=self._document_type,
            clauses=clauses,
            summary=summary or raw[:300],
            approved=approved,
        )
