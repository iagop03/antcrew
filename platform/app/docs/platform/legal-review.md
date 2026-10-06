# Contract Review Pipeline

`LegalReviewTeam` is a three-agent pipeline that reviews contracts and produces a structured `LegalFindingArtifact` — typed output with per-clause risk levels, regulatory references, and a final approval recommendation.

## Agents

| Agent | Role |
|---|---|
| `clause_extractor` | Extracts and categorises every clause |
| `risk_flagging` | Assigns risk level (low/medium/high/critical) + regulatory mapping |
| `legal_reviewer` | Writes recommendations for high-risk clauses + APPROVE / REJECT verdict |

## Quickstart

```python
from antcrew import LegalReviewTeam
from antcrew.models.anthropic_model import AnthropicModel

team = LegalReviewTeam(
    llm=AnthropicModel(),
    document_name="SaaS Agreement v2.pdf",
    document_type="SaaS",
)

with open("contract.txt") as f:
    contract_text = f.read()

result = team.run(contract_text)
finding = result["legal_finding"]   # dict (LegalFindingArtifact)

print(f"Approved: {finding['approved']}")
print(f"High-risk clauses: {finding['high_risk_count']}")
print(finding["summary"])
```

## LegalFindingArtifact

```python
from antcrew import LegalFindingArtifact, LegalClause, RiskLevel

finding = LegalFindingArtifact(
    document_name="NDA.pdf",
    document_type="NDA",
    clauses=[
        LegalClause(
            clause_id="CLAUSE-3",
            section="§ 3.2",
            text="All IP created during the engagement...",
            risk_level=RiskLevel.CRITICAL,
            issue="Broad IP assignment — may include pre-existing tools",
            regulations=["IP law", "Work-for-hire doctrine"],
        ),
    ],
    summary="Critical IP assignment clause requires negotiation before signing.",
    approved=False,
)
```

### Fields

| Field | Type | Description |
|---|---|---|
| `document_name` | `str` | Contract file name or identifier |
| `document_type` | `str` | NDA, SaaS, Employment, etc. |
| `clauses` | `list[LegalClause]` | All extracted clauses |
| `high_risk_count` | `int` | Auto-computed: high + critical clauses |
| `summary` | `str` | Executive summary from the reviewer |
| `approved` | `bool` | APPROVE verdict from the legal reviewer |

### RiskLevel

```python
class RiskLevel(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"
```

## Human-in-the-loop (HITL)

For regulated industries, pause the pipeline before the final reviewer step when high-risk clauses are found:

```python
team = LegalReviewTeam(
    llm=AnthropicModel(),
    hitl_on_high_risk=True,   # pauses for human approval if high/critical clauses found
)
```

## YAML (CustomTeam)

```yaml
team: custom
model: claude
steps:
  - name: clause_extractor
    system_prompt: |
      You are a legal document analyst. Extract all key clauses, their section
      references, and clause types (indemnity, IP, non-compete, termination…).
      Format: [CLAUSE-N] Section: <ref> | Type: <type> | Text: <text>
    output_key: clauses_raw

  - name: risk_flagging
    system_prompt: |
      For each clause in {clauses_raw}, assign a risk level (low/medium/high/critical),
      identify the legal issue, and list applicable regulations (GDPR, HIPAA, etc.).
      Format: [CLAUSE-N] Risk: <level> | Issue: <issue> | Regulations: <regs>
    input_key: clauses_raw
    output_key: risk_assessment

  - name: legal_reviewer
    system_prompt: |
      Based on {risk_assessment}, write recommendations for HIGH/CRITICAL clauses
      and give a final verdict: APPROVE | APPROVE WITH CHANGES | REJECT.
      Include SUMMARY: <executive summary>.
    input_key: risk_assessment
    output_key: legal_finding
```

## WorkspaceContractSchema for legal

Register a custom schema to enforce structured output across your legal workspace:

```python
from pydantic import BaseModel
from antcrew import WorkspaceContractSchema

class LegalExtras(BaseModel):
    jurisdiction: str = "ES"
    review_sla_days: int = 5
    requires_counsel_sign_off: bool = False

registry = WorkspaceContractSchema()
registry.register("legal_finding", LegalExtras)
```

## Compliance note

`LegalFindingArtifact` pairs with the [Compliance Pack](./compliance-pack.md):
each finding gets a `governance_hash` and can be attested via `GET /runs/{id}/attestation`.
This creates a cryptographically signed record of which model reviewed which contract.
