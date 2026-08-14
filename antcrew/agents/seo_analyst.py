"""SEOAnalystAgent — analyzes content for SEO quality and generates recommendations."""
from __future__ import annotations

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import ContentPiece, SEOAnalysis, coerce_list
from antcrew.core.state import TeamState

_SYSTEM = """\
You are an SEO expert. Analyze the provided content and return an SEO analysis.

Respond ONLY with a valid JSON object:
{
  "title": "<SEO-optimized title suggestion>",
  "meta_description": "<max 160-char meta description>",
  "primary_keyword": "<main keyword>",
  "secondary_keywords": ["<kw1>", "<kw2>"],
  "score": <integer 0-100>,
  "readability_score": <integer 0-100>,
  "recommendations": ["<actionable improvement>"],
  "issues": ["<specific SEO issue>"],
  "rationale": "<brief explanation>"
}

Score criteria: 80-100 excellent, 60-79 good, 40-59 fair, 0-39 poor.
"""


class SEOAnalystAgent(BaseAgent):
    name = "seo_analyst"
    role_description = "Analyzes content for SEO: keyword density, readability, meta tags, and structure."
    consumes: list[str] = ["content_artifacts", "request"]
    produces: list[str] = ["seo_analysis"]

    def run(self, state: TeamState) -> dict:
        content_artifacts = coerce_list(state, "content_artifacts", ContentPiece)
        request = state.get("request", "")

        if content_artifacts:
            content_text = "\n\n".join(
                f"Title: {c.title}\n{c.body}" for c in content_artifacts[:3]
            )
        else:
            content_text = request

        data: dict = self.system_parsed(_SYSTEM, content_text, dict)

        try:
            analysis = SEOAnalysis(**data)
        except Exception:
            analysis = SEOAnalysis(
                title=data.get("title", ""),
                score=data.get("score", 0),
                recommendations=data.get("recommendations", []),
                issues=data.get("issues", []),
            )

        return {
            "seo_analysis": analysis,
            "current_agent": self.name,
            "messages": [{
                "role": "assistant",
                "content": (
                    f"[SEOAnalyst] Score: {analysis.score}/100. "
                    f"Primary keyword: '{analysis.primary_keyword}'. "
                    f"{len(analysis.recommendations)} recommendation(s), "
                    f"{len(analysis.issues)} issue(s)."
                ),
            }],
        }
