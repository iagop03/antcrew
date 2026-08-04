from __future__ import annotations

import json

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import (
    ArtifactContract,
    ContractError,
    DesignTokens,
    PRD,
    Screen,
    Ticket,
    UIDesignSpec,
    coerce_list,
)
from antcrew.core.state import TeamState

_PRD_CONTRACT: ArtifactContract[PRD] = ArtifactContract("prd", PRD)

_SYSTEM = """\
You are a UI/UX Designer on a software development team.
Given a PRD and a list of development tickets, propose a complete UI design specification.

Respond ONLY with a valid JSON object (no markdown fences, no prose):
{
  "screens": [
    {
      "id": "<kebab-case id, e.g. login-screen>",
      "name": "<human-readable name, e.g. Login Screen>",
      "description": "<what this screen does and who uses it>",
      "route": "<URL route, e.g. /login>",
      "components": ["<UI component names, e.g. LoginForm, EmailInput, PasswordInput>"],
      "linked_tickets": ["<ticket IDs from input that this screen implements>"]
    }
  ],
  "navigation_flow": ["<ordered screen names showing primary user journey, e.g. Landing → Login → Dashboard>"],
  "tokens": {
    "color_primary": "#hex",
    "color_secondary": "#hex",
    "color_background": "#hex",
    "color_text": "#hex",
    "font_family": "<e.g. Inter, system-ui, sans-serif>",
    "font_scale": {"h1": "2rem", "h2": "1.5rem", "body": "1rem", "small": "0.875rem"}
  },
  "design_system": "<one of: Tailwind+shadcn, Material UI, Ant Design, Bootstrap, custom>"
}

Rules:
- Every ticket ID must appear in at least one screen's linked_tickets.
- Use exact ticket IDs from the input (no invented IDs).
- Keep screens focused: one per major user flow, not one per ticket.
- Choose design tokens that reflect the product's tone and audience from the PRD.
- navigation_flow shows the primary user journey in order.
"""

_REFINE_SYSTEM = """\
You are a UI/UX Designer. A reviewer provided feedback on the UI design spec.
Update the spec to address the feedback while keeping all screens coherent.

Current design spec:
{artifact_json}

Reviewer feedback:
{feedback}

Respond ONLY with the complete updated JSON object (no markdown fences, no prose).
Every ticket must still be linked to at least one screen.
"""


def _build_spec(data: dict) -> UIDesignSpec:
    screens = [
        Screen(**{k: v for k, v in s.items() if k in Screen.model_fields})
        for s in data.get("screens", [])
    ]
    tok_data = data.get("tokens") or {}
    tokens = DesignTokens(
        color_primary=tok_data.get("color_primary", "#6366f1"),
        color_secondary=tok_data.get("color_secondary", "#8b5cf6"),
        color_background=tok_data.get("color_background", "#ffffff"),
        color_text=tok_data.get("color_text", "#111827"),
        font_family=tok_data.get("font_family", "Inter, system-ui, sans-serif"),
        font_scale=tok_data.get("font_scale", {}),
    )
    return UIDesignSpec(
        screens=screens,
        navigation_flow=data.get("navigation_flow", []),
        tokens=tokens,
        design_system=data.get("design_system", ""),
    )


class UIDesignAgent(BaseAgent):
    name = "ui_designer"
    role_description = "Proposes UI screens, navigation flow, and design tokens from a PRD and tickets."
    conversational = True
    consumes: list[str] = ["prd", "tickets"]
    produces: list[str] = ["ui_design_spec"]

    def run(self, state: TeamState) -> dict:
        try:
            prd = _PRD_CONTRACT.extract(state)
        except ContractError as exc:
            return {"errors": [f"UIDesignAgent: {exc}"]}

        tickets = coerce_list(state, "tickets", Ticket)

        context = (
            f"PRD:\n{prd.model_dump_json(indent=2)}\n\n"
            f"Tickets:\n{json.dumps([t.model_dump() for t in tickets], indent=2)}"
        )
        data: dict = self.system_parsed(_SYSTEM, context, dict, max_tokens=4096)
        spec = _build_spec(data)

        return {
            "ui_design_spec": spec,
            "current_agent": self.name,
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[UIDesigner] {len(spec.screens)} screens designed "
                        f"({spec.design_system or 'custom'} design system)."
                    ),
                }
            ],
        }

    def refine(self, state: TeamState, artifact: UIDesignSpec, feedback: str) -> dict:
        data: dict = self.system_parsed(
            _REFINE_SYSTEM.format(
                artifact_json=artifact.model_dump_json(indent=2),
                feedback=feedback,
            ),
            "Update the UI design spec based on the feedback.",
            dict,
        )
        spec = _build_spec(data)
        return {"ui_design_spec": spec}
