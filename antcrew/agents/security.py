from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import (
    CodeArtifact,
    SecurityFinding,
    SecurityReport,
    coerce_list,
)
from antcrew.core.state import TeamState

_LLM_SYSTEM = """\
You are a security code reviewer specializing in OWASP Top 10 and common vulnerability patterns.
Analyze the provided code for security issues.

Respond ONLY with a valid JSON object (no markdown fences, no prose):
{
  "findings": [
    {
      "rule_id": "<short identifier, e.g. 'sql-injection', 'hardcoded-secret'>",
      "severity": "<info|warning|error|critical>",
      "file_path": "<file path>",
      "line": <line number or null>,
      "message": "<description of the vulnerability>",
      "fix_suggestion": "<concrete fix in 1-2 sentences>"
    }
  ],
  "summary": "<overall security posture in 1-2 sentences>",
  "rationale": "<brief explanation of your analysis approach>"
}

Focus on:
- Injection flaws (SQL, command, LDAP)
- Authentication / session management issues
- Hardcoded secrets, tokens, passwords
- Insecure deserialization
- Missing input validation
- Exposed sensitive data in logs or responses
- Insecure direct object references
- Cross-site scripting (XSS) in frontend code
"""


def _run_semgrep(code_artifacts: list[CodeArtifact]) -> SecurityReport | None:
    """Try to run semgrep on the code artifacts. Returns None if semgrep is unavailable."""
    try:
        result = subprocess.run(
            ["semgrep", "--version"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        written: list[str] = []
        for artifact in code_artifacts:
            dest = Path(tmpdir) / artifact.file_path.lstrip("/").replace("/", os.sep)
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                dest.write_text(artifact.content, encoding="utf-8")
                written.append(str(dest))
            except Exception:
                continue

        if not written:
            return None

        try:
            proc = subprocess.run(
                [
                    "semgrep",
                    "--config=auto",
                    "--json",
                    "--quiet",
                    tmpdir,
                ],
                capture_output=True,
                timeout=120,
            )
            raw_output = proc.stdout.decode("utf-8", errors="replace")
            semgrep_data = json.loads(raw_output)
        except Exception:
            return None

        findings: list[SecurityFinding] = []
        for r in semgrep_data.get("results", []):
            findings.append(
                SecurityFinding(
                    rule_id=r.get("check_id", ""),
                    severity=_map_semgrep_severity(r.get("extra", {}).get("severity", "warning")),
                    file_path=_strip_tmp(r.get("path", ""), tmpdir),
                    line=r.get("start", {}).get("line"),
                    message=r.get("extra", {}).get("message", ""),
                    fix_suggestion=r.get("extra", {}).get("fix"),
                )
            )

        return SecurityReport(
            findings=findings,
            scanned_files=len(written),
            tool="semgrep",
            summary=f"semgrep: {len(findings)} finding(s) across {len(written)} file(s).",
        )


def _map_semgrep_severity(s: str) -> str:
    mapping = {"ERROR": "error", "WARNING": "warning", "INFO": "info"}
    return mapping.get(s.upper(), "warning")


def _strip_tmp(path: str, tmpdir: str) -> str:
    rel = path.replace(tmpdir, "").lstrip("/\\").replace("\\", "/")
    return rel or path


class SecurityAgent(BaseAgent):
    name = "security_auditor"
    role_description = (
        "Audits code artifacts for security vulnerabilities. "
        "Uses semgrep when available, falls back to LLM analysis."
    )
    consumes: list[str] = ["code_artifacts"]
    produces: list[str] = ["security_report"]

    def run(self, state: TeamState) -> dict:
        code_artifacts = coerce_list(state, "code_artifacts", CodeArtifact)

        if not code_artifacts:
            report = SecurityReport(summary="No code artifacts to audit.")
            return {
                "security_report": report,
                "current_agent": self.name,
                "messages": [{"role": "assistant", "content": "[SecurityAuditor] No code to audit."}],
            }

        # Try semgrep first
        report = _run_semgrep(code_artifacts)

        if report is None:
            # Fallback: LLM-based analysis
            code_summary = json.dumps(
                [
                    {
                        "file_path": a.file_path,
                        "language": a.language,
                        "content": a.content[:3000],  # truncate large files
                    }
                    for a in code_artifacts
                ],
                indent=2,
            )
            data: dict = self.system_parsed(
                _LLM_SYSTEM,
                f"Code to analyze ({len(code_artifacts)} file(s)):\n{code_summary}",
                dict,
            )
            try:
                findings = [
                    SecurityFinding(
                        rule_id=f.get("rule_id", ""),
                        severity=f.get("severity", "warning"),
                        file_path=f.get("file_path", ""),
                        line=f.get("line"),
                        message=f.get("message", ""),
                        fix_suggestion=f.get("fix_suggestion"),
                    )
                    for f in (data.get("findings") or [])
                ]
                report = SecurityReport(
                    findings=findings,
                    scanned_files=len(code_artifacts),
                    tool="llm",
                    summary=data.get("summary", ""),
                    rationale=data.get("rationale"),
                )
            except Exception:
                report = SecurityReport(
                    scanned_files=len(code_artifacts),
                    tool="llm",
                    summary="Analysis incomplete.",
                )

        critical = sum(1 for f in report.findings if f.severity == "critical")
        errors = sum(1 for f in report.findings if f.severity == "error")

        return {
            "security_report": report,
            "current_agent": self.name,
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[SecurityAuditor/{report.tool}] "
                        f"{len(report.findings)} finding(s) — "
                        f"critical={critical}, error={errors}. "
                        f"{report.summary}"
                    ),
                }
            ],
        }
