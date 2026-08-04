from __future__ import annotations

import json

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import PRD, Ticket, coerce_list
from antcrew.core.state import TeamState

# Static monthly price table (USD). Updated: 2024-Q4 estimates.
# Components are identified by the LLM; costs are looked up here.
_PRICE_TABLE: dict[str, float] = {
    "compute_small":             15.0,   # t3.small or equivalent (1 vCPU, 2 GB)
    "compute_medium":            40.0,   # t3.medium (2 vCPU, 4 GB)
    "compute_large":            100.0,   # t3.large (2 vCPU, 8 GB)
    "container_orchestration":   75.0,   # ECS Fargate / small cluster
    "database_small":            25.0,   # RDS db.t3.micro, 20 GB storage
    "database_medium":           80.0,   # RDS db.t3.small, 50 GB storage
    "storage_gb":                 0.023, # S3 per GB/month
    "cdn":                       10.0,   # CloudFront or equivalent base cost
    "load_balancer":             18.0,   # ALB or Nginx/Traefik equivalent
    "cache_small":               15.0,   # ElastiCache t3.micro / Redis
    "queue":                      5.0,   # SQS, RabbitMQ, or similar
    "email":                      2.0,   # SES per 10k transactional emails
    "monitoring":                10.0,   # CloudWatch / Datadog starter
    "auth_service":              25.0,   # Cognito / Auth0 developer tier
    "search":                    30.0,   # OpenSearch / Typesense small
    "vector_db":                 40.0,   # Pinecone / Weaviate starter
}

_SYSTEM = f"""\
You are a cloud infrastructure cost estimator.
Given a product spec and development tickets, identify the infrastructure components needed
and estimate their monthly cost.

Available components and their USD/month costs:
{json.dumps(_PRICE_TABLE, indent=2)}

Respond ONLY with a valid JSON object (no markdown fences, no prose):
{{
  "components": [
    {{
      "name": "<component key from the table above>",
      "count": <integer — how many instances needed>,
      "justification": "<one sentence why this is needed>"
    }}
  ],
  "storage_estimate_gb": <integer — estimated S3/blob storage in GB, 0 if none>,
  "notes": "<any important caveats, e.g. 'excludes LLM API costs', 'assumes AWS us-east-1'>",
  "rationale": "<overall reasoning in 1-2 sentences>"
}}

Rules:
- Only pick components from the table. If something is not in the table, skip it.
- Estimate count conservatively for MVP scope (1 instance unless clearly needed otherwise).
- Do NOT include LLM/AI API costs — only infrastructure.
"""


def _compute_total(components: list[dict], storage_gb: int) -> float:
    total = 0.0
    for comp in components:
        name = comp.get("name", "")
        count = int(comp.get("count", 1))
        unit_cost = _PRICE_TABLE.get(name, 0.0)
        total += unit_cost * count
    total += _PRICE_TABLE["storage_gb"] * max(storage_gb, 0)
    return round(total, 2)


class CostAgent(BaseAgent):
    name = "cost_estimator"
    role_description = "Estimates monthly infrastructure cost from PRD and tickets using a static price table."
    consumes: list[str] = ["prd", "tickets"]
    produces: list[str] = ["metadata"]

    def run(self, state: TeamState) -> dict:
        prd = state.get("prd")
        tickets = coerce_list(state, "tickets", Ticket)

        parts: list[str] = []
        if prd:
            raw = prd.model_dump() if hasattr(prd, "model_dump") else prd
            parts.append(f"PRD:\n{json.dumps(raw, indent=2)}")
        if tickets:
            parts.append(
                f"Tickets ({len(tickets)}):\n"
                + json.dumps(
                    [{"id": t.id, "title": t.title, "description": t.description} for t in tickets],
                    indent=2,
                )
            )

        if not parts:
            return {
                "metadata": {"cost_estimate": {"error": "No PRD or tickets to analyze."}},
                "current_agent": self.name,
                "messages": [{"role": "assistant", "content": "[CostEstimator] No input artifacts."}],
            }

        context = "\n\n".join(parts)
        data: dict = self.system_parsed(_SYSTEM, context, dict)

        components = data.get("components") or []
        storage_gb = int(data.get("storage_estimate_gb") or 0)
        total_usd = _compute_total(components, storage_gb)

        estimate = {
            "total_monthly_usd": total_usd,
            "components": components,
            "storage_estimate_gb": storage_gb,
            "notes": data.get("notes", ""),
            "rationale": data.get("rationale", ""),
        }

        return {
            "metadata": {"cost_estimate": estimate},
            "current_agent": self.name,
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[CostEstimator] Estimated infra cost: "
                        f"${total_usd:.2f}/month "
                        f"({len(components)} component(s)). "
                        f"{data.get('notes', '')}"
                    ),
                }
            ],
        }
