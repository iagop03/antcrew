"""CloudArchitectAgent — designs cloud infrastructure specifications."""
from __future__ import annotations

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import CloudArchSpec, CloudService, Ticket, coerce_list
from antcrew.core.state import TeamState

_SYSTEM = """\
You are a cloud solutions architect. Design a cloud infrastructure specification.

Respond ONLY with a valid JSON object:
{
  "title": "<spec title>",
  "provider": "<aws|gcp|azure>",
  "services": [
    {
      "name": "<service name>",
      "service_type": "<compute|storage|database|messaging|networking|security|monitoring>",
      "purpose": "<what it does in this architecture>",
      "estimated_monthly_cost_usd": <float>
    }
  ],
  "architecture_diagram": "<ASCII or Mermaid diagram showing service relationships>",
  "estimated_monthly_cost_usd": <total float>,
  "security_notes": ["<security best practice>"],
  "rationale": "<architectural decisions explanation>"
}

Include 3-8 services. Include IAM, monitoring, and backup services. Be realistic with costs.
"""


class CloudArchitectAgent(BaseAgent):
    name = "cloud_architect"
    role_description = "Designs cloud architecture: service selection, cost estimation, and security considerations."
    consumes: list[str] = ["request", "prd", "tickets"]
    produces: list[str] = ["cloud_arch_spec"]

    def run(self, state: TeamState) -> dict:
        request = state.get("request", "")
        prd = state.get("prd")
        tickets = coerce_list(state, "tickets", Ticket)

        parts = [request]
        if prd:
            raw = prd.model_dump() if hasattr(prd, "model_dump") else prd
            parts.append(
                f"Product: {raw.get('title', '')}\n"
                f"Non-functional: {raw.get('non_functional_requirements', [])}"
            )
        if tickets:
            parts.append(f"Features: {', '.join(t.title for t in tickets[:8])}")

        data: dict = self.system_parsed(_SYSTEM, "\n\n".join(parts), dict)

        try:
            services = [CloudService(**s) for s in (data.get("services") or [])]
            spec = CloudArchSpec(
                title=data.get("title", "Cloud Architecture"),
                provider=data.get("provider", "aws"),
                services=services,
                architecture_diagram=data.get("architecture_diagram", ""),
                estimated_monthly_cost_usd=float(data.get("estimated_monthly_cost_usd", 0)),
                security_notes=data.get("security_notes") or [],
                rationale=data.get("rationale"),
            )
        except Exception:
            spec = CloudArchSpec(title="Cloud Architecture")

        return {
            "cloud_arch_spec": spec,
            "current_agent": self.name,
            "messages": [{
                "role": "assistant",
                "content": (
                    f"[CloudArchitect] {spec.title} ({spec.provider.upper()}): "
                    f"{len(spec.services)} service(s), "
                    f"~${spec.estimated_monthly_cost_usd:.0f}/mo."
                ),
            }],
        }
