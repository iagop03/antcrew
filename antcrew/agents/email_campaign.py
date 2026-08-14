"""EmailCampaignAgent — designs email campaigns with subject lines, body, and CTAs."""
from __future__ import annotations

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import ContentPiece, EmailCampaign, coerce_list
from antcrew.core.state import TeamState

_SYSTEM = """\
You are an email marketing expert. Design a complete email campaign.

Respond ONLY with a valid JSON object:
{
  "subject": "<compelling subject line ≤60 chars>",
  "preview_text": "<preheader text ≤90 chars>",
  "body_html": "<full HTML email body with inline styles>",
  "body_text": "<plain-text fallback, ≤400 words>",
  "cta_text": "<call-to-action button label>",
  "cta_url": "<placeholder URL>",
  "target_segments": ["<segment e.g. 'active users'>", "<segment>"],
  "rationale": "<brief explanation of strategy>"
}
"""


class EmailCampaignAgent(BaseAgent):
    name = "email_campaign"
    role_description = "Designs email campaigns: subject lines, HTML body copy, CTAs, and audience segments."
    consumes: list[str] = ["request", "content_artifacts"]
    produces: list[str] = ["email_campaign"]

    def run(self, state: TeamState) -> dict:
        content_artifacts = coerce_list(state, "content_artifacts", ContentPiece)
        request = state.get("request", "")

        context = request
        if content_artifacts:
            context += "\n\n" + "\n".join(
                f"{c.title}: {c.body[:300]}" for c in content_artifacts[:2]
            )

        data: dict = self.system_parsed(_SYSTEM, context, dict)

        try:
            campaign = EmailCampaign(**data)
        except Exception:
            campaign = EmailCampaign(
                subject=data.get("subject", ""),
                body_text=data.get("body_text", ""),
                cta_text=data.get("cta_text", ""),
                target_segments=data.get("target_segments", []),
            )

        return {
            "email_campaign": campaign,
            "current_agent": self.name,
            "messages": [{
                "role": "assistant",
                "content": (
                    f"[EmailCampaign] Subject: '{campaign.subject}'. "
                    f"CTA: '{campaign.cta_text}'. "
                    f"Segments: {campaign.target_segments}."
                ),
            }],
        }
