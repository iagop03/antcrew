"""DesignSystemAgent — produces design system specifications with components and tokens."""
from __future__ import annotations

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import DesignSystemComponent, DesignSystemDoc
from antcrew.core.state import TeamState

_SYSTEM = """\
You are a design systems engineer. Create a design system specification.

Respond ONLY with a valid JSON object:
{
  "title": "<Design System name>",
  "description": "<2-sentence description>",
  "principles": ["<design principle>"],
  "components": [
    {
      "name": "<ComponentName>",
      "description": "<what it does>",
      "variants": ["<variant>"],
      "props": ["<prop: type — description>"]
    }
  ],
  "tokens": {
    "color-primary": "#value",
    "color-secondary": "#value",
    "color-background": "#value",
    "color-text": "#value",
    "font-family-heading": "value",
    "font-family-body": "value",
    "spacing-unit": "8px",
    "border-radius-sm": "4px",
    "border-radius-md": "8px"
  },
  "usage_guidelines": ["<guideline>"],
  "rationale": "<design philosophy>"
}

Include 5-10 core components: Button, Input, Card, Modal, Badge, Tooltip, etc.
"""


class DesignSystemAgent(BaseAgent):
    name = "design_system"
    role_description = "Creates design system specs: components, tokens, principles, and usage guidelines."
    consumes: list[str] = ["request", "prd", "ui_design_spec"]
    produces: list[str] = ["design_system_doc"]

    def run(self, state: TeamState) -> dict:
        request = state.get("request", "")
        prd = state.get("prd")
        ui_spec = state.get("ui_design_spec")

        parts = [request]
        if prd:
            raw = prd.model_dump() if hasattr(prd, "model_dump") else prd
            parts.append(f"Product: {raw.get('title', '')} — {raw.get('summary', '')}")
        if ui_spec:
            raw = ui_spec.model_dump() if hasattr(ui_spec, "model_dump") else ui_spec
            screens = raw.get("screens") or []
            if screens:
                parts.append(f"Screens: {', '.join(s.get('name', '') for s in screens[:5])}")

        data: dict = self.system_parsed(_SYSTEM, "\n\n".join(parts), dict)

        try:
            components = [DesignSystemComponent(**c) for c in (data.get("components") or [])]
            doc = DesignSystemDoc(
                title=data.get("title", "Design System"),
                description=data.get("description", ""),
                principles=data.get("principles") or [],
                components=components,
                tokens=data.get("tokens") or {},
                usage_guidelines=data.get("usage_guidelines") or [],
                rationale=data.get("rationale"),
            )
        except Exception:
            doc = DesignSystemDoc(title="Design System")

        return {
            "design_system_doc": doc,
            "current_agent": self.name,
            "messages": [{
                "role": "assistant",
                "content": (
                    f"[DesignSystem] {doc.title}: "
                    f"{len(doc.components)} component(s), "
                    f"{len(doc.tokens)} token(s), "
                    f"{len(doc.principles)} principle(s)."
                ),
            }],
        }
