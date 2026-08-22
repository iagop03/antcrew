"""Docker-based sandbox for executing untrusted generated code.

Wraps a subprocess command in ``docker run --rm`` with hard resource limits.
Falls back to direct execution when Docker is unavailable (with a warning).

Control via environment variable ANTCREW_SANDBOX:
  auto     (default) — use Docker if available, fall back to direct subprocess
  required           — error if Docker is not available; never run unsandboxed
  none               — skip Docker entirely, run direct subprocess always

Resource defaults (all overridable per-call):
  --memory=256m         process + stack cap
  --memory-swap=256m    disable swap
  --cpus=0.5            half a core
  --pids-limit=256      prevent fork bombs
  --network=none        no network during test execution
  --security-opt=no-new-privileges
"""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_AVAILABLE: Optional[bool] = None  # cached after first probe


def is_available() -> bool:
    """Return True if ``docker info`` exits 0.  Result is cached per process."""
    global _AVAILABLE
    if _AVAILABLE is None:
        if shutil.which("docker") is None:
            _AVAILABLE = False
        else:
            try:
                r = subprocess.run(
                    ["docker", "info"],
                    capture_output=True,
                    timeout=5,
                )
                _AVAILABLE = r.returncode == 0
            except Exception:
                _AVAILABLE = False
        if not _AVAILABLE:
            log.debug("sandbox: Docker not available — will run unsandboxed")
    return _AVAILABLE


def _mode() -> str:
    return os.environ.get("ANTCREW_SANDBOX", "auto").lower()


def use_docker() -> bool:
    """True when the current configuration would route execution through Docker.

    Returns False on Windows (bind-mount path translation not supported),
    when ANTCREW_SANDBOX=none, or when mode is 'auto' but Docker is absent.
    Returns True when mode is 'required' even if Docker is not yet confirmed
    available — the error surfaces at run time via run() / run_with_install().
    """
    if sys.platform == "win32":
        return False
    mode = _mode()
    if mode == "none":
        return False
    if mode == "required":
        return True
    return is_available()  # auto: only True when Docker is actually reachable


def _docker_image() -> str:
    """Return the Docker image to use for sandbox execution.

    Defaults to ``python:{major}.{minor}-slim`` (mutable tag, matches host Python).
    For production, override with a pinned digest to guarantee reproducibility:

      ANTCREW_SANDBOX_IMAGE=python:3.12-slim@sha256:<digest>

    Pin a new digest by running:
      docker pull python:3.12-slim && docker inspect python:3.12-slim --format '{{index .RepoDigests 0}}'
    """
    override = os.environ.get("ANTCREW_SANDBOX_IMAGE")
    if override:
        return override
    vi = sys.version_info
    return f"python:{vi.major}.{vi.minor}-slim"


def run(
    args: list[str],
    *,
    cwd: Path,
    env: Optional[dict[str, str]] = None,
    timeout: int = 120,
    memory: str = "256m",
    cpus: str = "0.5",
    pids_limit: int = 256,
    network: str = "none",
    extra_mounts: Optional[list[tuple[str, str]]] = None,
) -> subprocess.CompletedProcess:
    """Run *args* inside a Docker container mounted at *cwd*.

    Falls back to a direct subprocess when Docker is unavailable and mode is
    'auto'.  Raises RuntimeError when mode is 'required' and Docker is absent.

    Parameters
    ----------
    args:
        Command to run inside the container (e.g. ``["python", "-m", "pytest", ...]``).
    cwd:
        Host directory to mount into the container (read-write so pytest can
        write ``__pycache__`` and ``.pytest_cache``).  Mounted at the same
        absolute path inside the container so paths in tracebacks match.
    env:
        Environment variables forwarded into the container.  Defaults to the
        current process environment.
    timeout:
        Seconds before the container is killed with SIGKILL.
    extra_mounts:
        Additional ``(host_path, container_path)`` pairs to mount read-only
        (e.g. a shared venv directory).
    """
    mode = _mode()
    if mode == "none":
        return _direct(args, cwd=cwd, env=env, timeout=timeout)

    if not is_available():
        if mode == "required":
            raise RuntimeError(
                "ANTCREW_SANDBOX=required but Docker is not available. "
                "Install Docker or set ANTCREW_SANDBOX=auto to allow unsandboxed fallback."
            )
        log.warning(
            "sandbox: Docker unavailable — running %s unsandboxed (set "
            "ANTCREW_SANDBOX=required to block this)",
            args[0] if args else "command",
        )
        return _direct(args, cwd=cwd, env=env, timeout=timeout)

    return _docker(
        args, cwd=cwd, env=env, timeout=timeout,
        memory=memory, cpus=cpus, pids_limit=pids_limit,
        network=network, extra_mounts=extra_mounts or [],
    )


def _docker(
    args: list[str],
    *,
    cwd: Path,
    env: Optional[dict[str, str]],
    timeout: int,
    memory: str,
    cpus: str,
    pids_limit: int,
    network: str,
    extra_mounts: list[tuple[str, str]],
) -> subprocess.CompletedProcess:
    cwd_str = str(cwd.resolve())
    # Windows paths cannot be mounted into Linux containers without WSL translation.
    # Fall back to direct execution to avoid silent failures.
    if sys.platform == "win32":
        log.debug("sandbox: Windows host — falling back to direct subprocess")
        return _direct(args, cwd=cwd, env=env, timeout=timeout)
    image = _docker_image()

    docker_cmd: list[str] = [
        "docker", "run", "--rm",
        "--user=65534:65534",  # nobody:nogroup — prevents container-escape via kernel vuln as root
        f"--memory={memory}",
        f"--memory-swap={memory}",  # disable swap
        f"--cpus={cpus}",
        f"--pids-limit={pids_limit}",
        f"--network={network}",
        "--security-opt=no-new-privileges",
        f"--stop-timeout={min(timeout, 60)}",
        # project dir — rw so pytest can write __pycache__ / .pytest_cache
        "-v", f"{cwd_str}:{cwd_str}",
        "-w", cwd_str,
    ]

    for host_p, cont_p in extra_mounts:
        docker_cmd += ["-v", f"{host_p}:{cont_p}:ro"]

    # Forward only the env vars the command actually needs
    for key, val in (env or os.environ).items():
        if key in ("PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "HOME", "TMPDIR", "TMP", "TEMP"):
            docker_cmd += ["-e", f"{key}={val}"]

    docker_cmd += [image] + args

    log.debug("sandbox: %s", " ".join(docker_cmd[:10]) + " ...")
    try:
        return subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 10,  # outer timeout > inner stop-timeout
        )
    except subprocess.TimeoutExpired:
        # Build a fake result matching what the caller expects
        return subprocess.CompletedProcess(
            args=docker_cmd,
            returncode=124,
            stdout="",
            stderr=f"[sandbox] Docker container killed after {timeout}s timeout",
        )


def _direct(
    args: list[str],
    *,
    cwd: Path,
    env: Optional[dict[str, str]],
    timeout: int,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env or os.environ,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Install-then-test: single container for full isolation
# ---------------------------------------------------------------------------

def run_with_install(
    args: list[str],
    *,
    cwd: Path,
    requirements: Path,
    env: Optional[dict[str, str]] = None,
    timeout: int = 300,
    memory: str = "512m",
    cpus: str = "1.0",
    pids_limit: int = 512,
) -> subprocess.CompletedProcess:
    """Run ``pip install -r requirements`` then *args* in a single Docker container.

    Both steps share one ephemeral container so packages installed by pip are
    available to the test process without any host-side venv.  Network access
    is kept open for the duration (pip needs it); this is acceptable because
    the entire execution — including pip's post-install hooks — is isolated
    inside Docker and cannot touch the host filesystem beyond *cwd*.

    Falls back to ``_direct(args)`` when Docker is unavailable or mode is
    'none'; in that case the host must already have the packages installed.
    """
    mode = _mode()
    if mode == "none" or sys.platform == "win32":
        return _direct(args, cwd=cwd, env=env, timeout=timeout)

    if not is_available():
        if mode == "required":
            raise RuntimeError(
                "ANTCREW_SANDBOX=required but Docker is not available. "
                "Install Docker or set ANTCREW_SANDBOX=auto to allow unsandboxed fallback."
            )
        log.warning(
            "sandbox: Docker unavailable — running %s unsandboxed",
            args[0] if args else "command",
        )
        return _direct(args, cwd=cwd, env=env, timeout=timeout)

    return _docker_with_install(
        args, cwd=cwd, requirements=requirements, env=env,
        timeout=timeout, memory=memory, cpus=cpus, pids_limit=pids_limit,
    )


def _docker_with_install(
    args: list[str],
    *,
    cwd: Path,
    requirements: Path,
    env: Optional[dict[str, str]],
    timeout: int,
    memory: str,
    cpus: str,
    pids_limit: int,
) -> subprocess.CompletedProcess:
    cwd_str = str(cwd.resolve())
    # requirements.txt is inside cwd, so it's already mounted at the same path
    req_inside = shlex.quote(f"{cwd_str}/requirements.txt")
    test_cmd   = shlex.join(args)
    # pip install --user writes to $HOME/.local (not system site-packages), so
    # nobody (65534) can install when HOME=/tmp.  Python's site module picks up
    # ~/.local automatically, so the test command finds all installed packages.
    shell_script = f"pip install --user -q -r {req_inside} && {test_cmd}"

    image = _docker_image()
    docker_cmd: list[str] = [
        "docker", "run", "--rm",
        "--user=65534:65534",  # nobody:nogroup — same isolation as run_in_sandbox()
        f"--memory={memory}",
        f"--memory-swap={memory}",
        f"--cpus={cpus}",
        f"--pids-limit={pids_limit}",
        # Network intentionally open: pip install requires internet.
        # Post-install hooks and the test process run inside this container,
        # not on the host.
        "--security-opt=no-new-privileges",
        f"--stop-timeout={min(timeout, 120)}",
        "-v", f"{cwd_str}:{cwd_str}",
        "-w", cwd_str,
        "-e", "HOME=/tmp",  # writable home for pip --user installs under nobody
    ]

    for key, val in (env or os.environ).items():
        if key in ("PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "TMPDIR", "TMP", "TEMP"):
            docker_cmd += ["-e", f"{key}={val}"]

    docker_cmd += [image, "/bin/sh", "-c", shell_script]

    log.debug("sandbox: install+test %s", " ".join(docker_cmd[:10]) + " ...")
    try:
        return subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 10,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=docker_cmd,
            returncode=124,
            stdout="",
            stderr=f"[sandbox] Docker container killed after {timeout}s timeout",
        )
