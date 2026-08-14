"""DataAnalystAgent — analyzes structured data and produces insights + recommendations."""
from __future__ import annotations

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import DataAnalysisReport, DataInsight
from antcrew.core.state import TeamState

_SYSTEM = """\
You are a senior data analyst. Analyze the provided data description and produce actionable insights.

Respond ONLY with a valid JSON object:
{
  "title": "<analysis title>",
  "summary": "<2-3 sentence executive summary>",
  "insights": [
    {"title": "<insight title>", "finding": "<what you found>", "metric": "<key number or %>"},
    ...
  ],
  "recommendations": ["<actionable recommendation>", ...],
  "data_quality_notes": "<notes on data completeness or caveats>",
  "rationale": "<methodology note>"
}

Provide 3-7 insights. Be specific with numbers when available. Prioritize business impact.
"""


class DataAnalystAgent(BaseAgent):
    name = "data_analyst"
    role_description = "Analyzes structured data or descriptions to extract insights and actionable recommendations."
    consumes: list[str] = ["request", "research_document"]
    produces: list[str] = ["data_analysis_report"]

    def run(self, state: TeamState) -> dict:
        request = state.get("request", "")
        research = state.get("research_document")

        context = request
        if research:
            raw = research.model_dump() if hasattr(research, "model_dump") else research
            findings = raw.get("key_findings") or []
            if findings:
                context += "\n\nFindings:\n" + "\n".join(f"- {f}" for f in findings[:10])

        data: dict = self.system_parsed(_SYSTEM, context, dict)

        try:
            insights = [DataInsight(**i) for i in (data.get("insights") or [])]
            report = DataAnalysisReport(
                title=data.get("title", "Data Analysis"),
                summary=data.get("summary", ""),
                insights=insights,
                recommendations=data.get("recommendations") or [],
                data_quality_notes=data.get("data_quality_notes", ""),
                rationale=data.get("rationale"),
            )
        except Exception:
            report = DataAnalysisReport(
                title="Data Analysis",
                summary=data.get("summary", ""),
                recommendations=data.get("recommendations") or [],
            )

        return {
            "data_analysis_report": report,
            "current_agent": self.name,
            "messages": [{
                "role": "assistant",
                "content": (
                    f"[DataAnalyst] {report.title}: "
                    f"{len(report.insights)} insight(s), "
                    f"{len(report.recommendations)} recommendation(s). "
                    f"{report.summary[:120]}"
                ),
            }],
        }
