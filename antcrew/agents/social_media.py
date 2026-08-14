"""SocialMediaAgent — adapts content for Twitter, LinkedIn, and Instagram."""
from __future__ import annotations

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import ContentPiece, SocialMediaPlan, coerce_list
from antcrew.core.state import TeamState

_SYSTEM = """\
You are a social media strategist. Adapt the provided content for major social platforms.

Respond ONLY with a valid JSON object:
{
  "topic": "<topic summary in one sentence>",
  "twitter": "<tweet ≤280 chars with hook>",
  "linkedin": "<LinkedIn post 150-300 words, professional tone>",
  "instagram": "<Instagram caption with emojis, 100-150 words>",
  "hashtags": ["#tag1", "#tag2", "#tag3"],
  "best_posting_times": ["Twitter: Tue-Thu 9am-3pm EST", "LinkedIn: Tue-Wed 10am-12pm EST"],
  "rationale": "<brief note on tone choices>"
}
"""


class SocialMediaAgent(BaseAgent):
    name = "social_media"
    role_description = "Adapts content for Twitter, LinkedIn, and Instagram with platform-specific formatting."
    consumes: list[str] = ["content_artifacts", "request"]
    produces: list[str] = ["social_media_plan"]

    def run(self, state: TeamState) -> dict:
        content_artifacts = coerce_list(state, "content_artifacts", ContentPiece)
        request = state.get("request", "")

        if content_artifacts:
            content_text = "\n\n".join(
                f"Title: {c.title}\n{c.body[:600]}" for c in content_artifacts[:2]
            )
        else:
            content_text = request

        data: dict = self.system_parsed(_SYSTEM, content_text, dict)

        try:
            plan = SocialMediaPlan(**data)
        except Exception:
            plan = SocialMediaPlan(
                topic=data.get("topic", ""),
                twitter=data.get("twitter", ""),
                linkedin=data.get("linkedin", ""),
                instagram=data.get("instagram", ""),
                hashtags=data.get("hashtags", []),
            )

        return {
            "social_media_plan": plan,
            "current_agent": self.name,
            "messages": [{
                "role": "assistant",
                "content": (
                    f"[SocialMedia] Created posts for Twitter/LinkedIn/Instagram. "
                    f"{len(plan.hashtags)} hashtag(s). "
                    f"Tweet preview: {plan.twitter[:60]}…"
                ),
            }],
        }
