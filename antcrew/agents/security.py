"""SecurityAgent — LLM-based OWASP security review for Layer 1 team pipelines.

Use this agent inside a Supervisor / DevTeam workflow where code artifacts live
in TeamState. It applies OWASP Top 10 analysis using the LLM and (optionally)
static tools like semgrep, producing a SecurityReport artifact.

For standalone goal-directed security analysis over a full source tree, prefer
SecurityAuditor from antcrew-engine, which performs cross-file consistency
reasoning and runs as a Capability inside an EngineLoop.

Layer 1 (antcrew teams) → SecurityAgent
Layer 2 (antcrew-engine loops) → SecurityAuditor + SecurityScanner
"""
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

_AUDITOR_SEVERITY: dict[str, str] = {
    "critical": "critical",
    "high":     "error",
    "medium":   "warning",
    "low":      "info",
    "info":     "info",
}


def _map_auditor_severity(s: str) -> str:
    return _AUDITOR_SEVERITY.get(s.lower(), "warning")


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


def _run_bandit(code_artifacts: list[CodeArtifact]) -> "SecurityReport | None":
    """Run bandit on Python files. Returns None if bandit is unavailable or no Python files."""
    python_files = [a for a in code_artifacts if a.file_path.endswith(".py")]
    if not python_files:
        return None
    try:
        result = subprocess.run(["bandit", "--version"], capture_output=True, timeout=10)
        if result.returncode != 0:
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        written: list[str] = []
        for artifact in python_files:
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
                ["bandit", "-r", tmpdir, "-f", "json", "-q"],
                capture_output=True,
                timeout=60,
            )
            raw = proc.stdout.decode("utf-8", errors="replace")
            data = json.loads(raw)
        except Exception:
            return None

        findings: list[SecurityFinding] = []
        for r in data.get("results", []):
            sev = r.get("issue_severity", "MEDIUM").upper()
            severity = {"HIGH": "error", "MEDIUM": "warning", "LOW": "info"}.get(sev, "warning")
            findings.append(
                SecurityFinding(
                    rule_id=r.get("test_id", ""),
                    severity=severity,
                    file_path=_strip_tmp(r.get("filename", ""), tmpdir),
                    line=r.get("line_number"),
                    message=r.get("issue_text", ""),
                    fix_suggestion=r.get("more_info"),
                )
            )
        return SecurityReport(
            findings=findings,
            scanned_files=len(written),
            tool="bandit",
            summary=f"bandit: {len(findings)} finding(s) across {len(written)} Python file(s).",
        )


def _run_trufflehog(code_artifacts: list[CodeArtifact]) -> list[SecurityFinding]:
    """Scan for hardcoded secrets with TruffleHog. Returns [] if unavailable."""
    try:
        result = subprocess.run(["trufflehog", "--version"], capture_output=True, timeout=10)
        if result.returncode != 0:
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    with tempfile.TemporaryDirectory() as tmpdir:
        for artifact in code_artifacts:
            dest = Path(tmpdir) / artifact.file_path.lstrip("/").replace("/", os.sep)
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                dest.write_text(artifact.content, encoding="utf-8")
            except Exception:
                continue

        try:
            proc = subprocess.run(
                ["trufflehog", "filesystem", tmpdir, "--json", "--no-update"],
                capture_output=True,
                timeout=60,
            )
            findings: list[SecurityFinding] = []
            for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                source = item.get("SourceMetadata", {}).get("Data", {}).get("Filesystem", {})
                findings.append(
                    SecurityFinding(
                        rule_id=item.get("DetectorName", "secret"),
                        severity="critical",
                        file_path=_strip_tmp(source.get("file", ""), tmpdir),
                        line=source.get("line"),
                        message=f"Potential secret detected: {item.get('DetectorName', 'unknown')}",
                        fix_suggestion="Remove hardcoded secret and use environment variables.",
                    )
                )
            return findings
        except Exception:
            return []


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

        # Run all available static scanners; fall back to LLM if none are installed.
        semgrep_report  = _run_semgrep(code_artifacts)
        bandit_report   = _run_bandit(code_artifacts)
        secret_findings = _run_trufflehog(code_artifacts)

        # Merge findings from all static tools that ran
        static_findings: list[SecurityFinding] = list(secret_findings)
        static_tools: list[str] = []
        if semgrep_report:
            static_findings.extend(semgrep_report.findings)
            static_tools.append("semgrep")
        if bandit_report:
            static_findings.extend(bandit_report.findings)
            static_tools.append("bandit")
        if secret_findings:
            static_tools.append("trufflehog")

        if static_findings or static_tools:
            tool_str = "+".join(static_tools) if static_tools else "static"
            report = SecurityReport(
                findings=static_findings,
                scanned_files=len(code_artifacts),
                tool=tool_str,
                summary=(
                    f"{tool_str}: {len(static_findings)} finding(s) across "
                    f"{len(code_artifacts)} file(s)."
                ),
            )
        else:
            report = None

        if report is None:
            from antcrew_engine.capabilities.security_auditor import analyze_sources

            sources = {a.file_path: a.content for a in code_artifacts}

            def _call_json(system: str, user: str) -> str:
                return self.system(system, user, json_mode=True)

            data = analyze_sources(sources, _call_json)
            try:
                findings = [
                    SecurityFinding(
                        rule_id=f.get("pattern_class", ""),
                        severity=_map_auditor_severity(f.get("severity", "medium")),
                        file_path=f.get("file_path", ""),
                        line=f.get("line_number"),
                        message=(
                            f"{f.get('title', '')} — {f.get('evidence', '')}".strip(" —")
                        ),
                        fix_suggestion=f.get("reference_fix"),
                    )
                    for f in (data.get("findings") or [])
                ]
                report = SecurityReport(
                    findings=findings,
                    scanned_files=len(code_artifacts),
                    tool="llm-two-phase",
                    summary=data.get("summary", ""),
                )
            except Exception:
                report = SecurityReport(
                    scanned_files=len(code_artifacts),
                    tool="llm-two-phase",
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
