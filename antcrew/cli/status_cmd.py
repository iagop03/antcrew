"""antcrew status — show the state of the local platform server."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from antcrew.cli._app import app, console

_PID_FILE = Path.home() / ".antcrew" / "platform.pid"


def _pid_alive(pid: int) -> bool:
    """Return True if the process with *pid* is running."""
    if sys.platform == "win32":
        import subprocess
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True,
        )
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False


@app.command()
def status(
    url: str = typer.Option(
        "",
        "--url",
        help="Platform URL to check. Defaults to ANTCREW_PLATFORM_URL env var or http://localhost:8000.",
        envvar="ANTCREW_PLATFORM_URL",
    ),
) -> None:
    """Show the status of the local antcrew platform server.

    \b
    Checks:
      • Background server PID (via ~/.antcrew/platform.pid)
      • Platform health endpoint (/health)
      • Active API key hint

    \b
    Example:
        antcrew status
        antcrew status --url http://localhost:9000
    """
    platform_url = (url or os.environ.get("ANTCREW_PLATFORM_URL", "http://localhost:8000")).rstrip("/")

    # --- PID check ---
    pid_status = "[dim]no PID file[/dim]"
    if _PID_FILE.exists():
        try:
            pid = int(_PID_FILE.read_text().strip())
            if _pid_alive(pid):
                pid_status = f"[green]running[/green]  [dim](PID {pid})[/dim]"
            else:
                pid_status = f"[yellow]stale PID {pid}[/yellow]  [dim](process not found)[/dim]"
        except ValueError:
            pid_status = "[yellow]invalid PID file[/yellow]"

    console.print("\n[bold]antcrew platform status[/bold]")
    console.print(f"  URL    : [cyan]{platform_url}[/]")
    console.print(f"  Server : {pid_status}")

    # --- Health check ---
    from antcrew.cli.configure_cmd import get_platform_api_key as _get_key
    _api_key = _get_key()
    try:
        import httpx
        headers = {"X-Api-Key": _api_key} if _api_key else {}
        r = httpx.get(f"{platform_url}/health", headers=headers, timeout=4.0)
        if r.status_code == 200:
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            version = data.get("version", "")
            console.print(f"  Health : [green]ok[/green]  [dim]{version}[/dim]")
        else:
            console.print(f"  Health : [yellow]HTTP {r.status_code}[/yellow]")
    except ImportError:
        console.print("  Health : [dim]httpx not installed (pip install httpx)[/dim]")
    except Exception as exc:
        console.print(f"  Health : [red]unreachable[/red]  [dim]({exc})[/dim]")

    # --- API key hint ---
    if _api_key:
        console.print(f"  Auth   : [green]API key set[/green]  [dim]({_api_key[:4]}…)[/dim]")
    else:
        console.print("  Auth   : [dim]no API key (open access)[/dim]")

    console.print()
