from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from antcrew_engine.engine import (
    Artifact,
    ArtifactDelta,
    ArtifactId,
    ArtifactKind,
    CapabilityDescriptor,
    CapabilityResult,
    ConditionId,
)

from .base import BaseExecutor

_MAX_OUTPUT = 16_000  # chars of pip-audit output kept in artifact


class SecurityScanner(BaseExecutor):
    """Audits installed dependencies for known CVEs using pip-audit.

    Reads the venv_config artifact (written by DependencyInstaller) to locate
    the venv.  In Docker mode, scans the pip_requirements artifact via stdin.
    No LLM is required.
    """

    descriptor = CapabilityDescriptor(
        name        = "security_scanner",
        description = "Audits installed dependencies for known CVEs using pip-audit.",
        needs       = frozenset([ConditionId("dependencies_installed")]),
        produces    = frozenset([ConditionId("security_audit_done")]),
        emits       = frozenset(["report"]),
        cost        = 0.2,
    )

    def __init__(self, llm=None) -> None:
        super().__init__(llm=None)

    def _run(self, store, goal) -> CapabilityResult:
        venv_cfg_art = store.read(ArtifactId("venv_config"))
        if not venv_cfg_art:
            return CapabilityResult(errors=["venv_config artifact not found — run DependencyInstaller first"])

        cfg = venv_cfg_art.content if isinstance(venv_cfg_art.content, dict) else {}
        docker_mode = cfg.get("docker_mode", False)

        if docker_mode:
            audit_content = _scan_docker_mode(store)
        else:
            venv_path_str = cfg.get("venv_path")
            if not venv_path_str:
                return CapabilityResult(errors=["venv_path not found in venv_config"])
            audit_content = _scan_host_mode(Path(venv_path_str))

        report_artifact = Artifact(
            id       = ArtifactId("security_audit"),
            kind     = ArtifactKind.REPORT,
            content  = audit_content,
            metadata = {"file_path": ".antcrew/security_audit.json"},
        )
        return CapabilityResult(delta=ArtifactDelta(created=(report_artifact,)))


# ---------------------------------------------------------------------------
# Scan helpers
# ---------------------------------------------------------------------------

def _pip_audit_exe_in_venv(venv_path: Path) -> Path | None:
    """Return the path to pip-audit inside a venv's Scripts/bin, if it exists."""
    if sys.platform == "win32":
        candidate = venv_path / "Scripts" / "pip-audit.exe"
    else:
        candidate = venv_path / "bin" / "pip-audit"
    return candidate if candidate.exists() else None


def _run_pip_audit(cmd: list[str], input_data: str | None = None) -> tuple[bool, dict]:
    """Run pip-audit and return (success, parsed_result_dict).

    On FileNotFoundError (pip-audit not installed) returns a warning dict.
    On JSON parse failure returns a human-readable summary dict.
    """
    try:
        result = subprocess.run(
            cmd,
            input=input_data,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False, {
            "warning": "pip-audit is not installed or not found on PATH. "
                       "Install it with: pip install pip-audit",
            "vulnerabilities_found": 0,
            "dependencies": [],
        }

    raw_out = result.stdout.strip()
    raw_err = result.stderr.strip()

    # pip-audit exits non-zero when vulnerabilities are found (exit code 1).
    # We treat that as a successful scan with findings, not an execution error.
    # Only truly unexpected exit codes (e.g. 2) indicate a tool failure.
    if result.returncode not in (0, 1):
        return False, {
            "error": f"pip-audit exited with code {result.returncode}",
            "stderr": raw_err[-_MAX_OUTPUT:],
            "stdout": raw_out[-_MAX_OUTPUT:],
        }

    try:
        parsed = json.loads(raw_out)
    except json.JSONDecodeError:
        # Fall back to a plain text summary if JSON parsing fails.
        return True, {
            "summary": raw_out[-_MAX_OUTPUT:] or raw_err[-_MAX_OUTPUT:],
            "vulnerabilities_found": None,
            "dependencies": [],
        }

    return True, _enrich(parsed)


def _enrich(parsed: dict) -> dict:
    """Add a human-readable summary and vuln count to the pip-audit JSON output."""
    deps = parsed.get("dependencies", [])
    total_vulns = sum(len(dep.get("vulns", [])) for dep in deps)
    vulnerable_packages = [
        {
            "name": dep["name"],
            "version": dep["version"],
            "vulns": dep.get("vulns", []),
        }
        for dep in deps
        if dep.get("vulns")
    ]
    if total_vulns == 0:
        summary = f"Clean — no known vulnerabilities found in {len(deps)} package(s)."
    else:
        pkg_list = ", ".join(p["name"] for p in vulnerable_packages)
        summary = (
            f"{total_vulns} vulnerability/vulnerabilities found across "
            f"{len(vulnerable_packages)} package(s): {pkg_list}."
        )

    return {
        "summary": summary,
        "vulnerabilities_found": total_vulns,
        "vulnerable_packages": vulnerable_packages,
        "dependencies": deps,
    }


def _scan_host_mode(venv_path: Path) -> dict:
    """Locate pip-audit in the venv (or fall back to sys.executable) and scan."""
    pip_audit = _pip_audit_exe_in_venv(venv_path)
    if pip_audit:
        cmd = [str(pip_audit), "--format=json", "--output", "-"]
    else:
        # Fallback: try running pip_audit as a module via the venv python
        if sys.platform == "win32":
            python_bin = venv_path / "Scripts" / "python.exe"
        else:
            python_bin = venv_path / "bin" / "python"
        cmd = [str(python_bin), "-m", "pip_audit", "--format=json", "--output", "-"]

    ok, result = _run_pip_audit(cmd)
    return result


def _scan_docker_mode(store) -> dict:
    """Scan by piping pip_requirements content into pip-audit via stdin."""
    req_art = store.read(ArtifactId("pip_requirements"))
    if not req_art or not isinstance(req_art.content, str):
        return {
            "warning": "pip_requirements artifact not found; skipping Docker-mode scan.",
            "vulnerabilities_found": 0,
            "dependencies": [],
        }

    requirements_txt = req_art.content

    # On POSIX systems, /dev/stdin works. On Windows or when unavailable, we
    # pipe via stdin using -r /dev/stdin; if that fails, pip-audit is absent.
    cmd = ["pip-audit", "-r", "/dev/stdin", "--format=json", "--output", "-"]
    ok, result = _run_pip_audit(cmd, input_data=requirements_txt)
    if not ok and "not installed" in result.get("warning", ""):
        # Try the module fallback
        cmd_fallback = [sys.executable, "-m", "pip_audit", "-r", "/dev/stdin",
                        "--format=json", "--output", "-"]
        _, result = _run_pip_audit(cmd_fallback, input_data=requirements_txt)
    return result
