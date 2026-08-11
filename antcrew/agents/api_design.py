"""APIDesignAgent — designs a REST API specification from PRD and tickets."""
from __future__ import annotations

import logging
from typing import Any

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import APISpec
from antcrew.core.state import TeamState

log = logging.getLogger(__name__)

_SYSTEM = """\
You are a senior API architect. Given a Product Requirements Document and development tickets, \
design a complete REST API specification for the backend.

Respond with a JSON object matching this schema exactly:
{
  "base_path": "/api/v1",
  "endpoints": [
    {
      "method": "GET|POST|PUT|DELETE|PATCH",
      "path": "/resource/{id}",
      "description": "What this endpoint does",
      "request_schema": {"field": "type", ...} or null,
      "response_schema": {"field": "type", ...} or null
    }
  ],
  "rationale": "Brief explanation of the overall API design choices"
}

Guidelines:
- Include auth endpoints (POST /auth/login, POST /auth/register) if user management is implied.
- Group resources logically (users, items, etc.).
- Use consistent naming: plural nouns for collections, singular with {id} for items.
- For request/response schemas use simple field→type dicts (e.g. {"email": "string", "age": "integer"}).
- Limit to the endpoints the tickets actually require — don't over-engineer.
- Respond ONLY with the JSON object, no prose, no code fences.\
"""


class APIDesignAgent(BaseAgent):
    """Produces an APISpec from the PRD and ticket list."""

    name = "api_design"
    role_description = "Designs the REST API specification from PRD and tickets"

    def run(self, state: TeamState) -> dict:
        prd = state.get("prd")
        tickets = state.get("tickets") or []

        prd_lines: list[str] = []
        if prd:
            prd_data: dict[str, Any] = (
                prd.model_dump() if hasattr(prd, "model_dump") else dict(prd)
            )
            prd_lines.append(f"Title: {prd_data.get('title', '')}")
            prd_lines.append(f"Summary: {prd_data.get('summary', '')}")
            reqs = prd_data.get("functional_requirements") or []
            if reqs:
                prd_lines.append("Functional requirements:")
                for r in reqs:
                    prd_lines.append(f"  - {r}")

        ticket_lines: list[str] = []
        for t in tickets[:25]:
            td: dict[str, Any] = (
                t.model_dump() if hasattr(t, "model_dump") else dict(t)
            )
            ticket_lines.append(f"- {td.get('id', '')}: {td.get('title', '')} — {td.get('description', '')[:120]}")

        user = "PRD:\n" + "\n".join(prd_lines or ["(none)"])
        user += "\n\nTickets:\n" + "\n".join(ticket_lines or ["(none)"])

        try:
            spec = self.system_parsed(_SYSTEM, user, APISpec, max_retries=1)
        except Exception as exc:
            log.warning("APIDesignAgent: parse failed (%s) — returning empty spec", exc)
            spec = APISpec(endpoints=[], base_path="/api/v1")

        return {"api_spec": spec, "current_agent": self.name}
