"""antcrew serve — start the antcrew platform locally (SQLite, no auth by default)."""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import app, console

_DEFAULT_DB = Path.home() / ".antcrew" / "platform.db"
_PID_FILE = Path.home() / ".antcrew" / "platform.pid"


@app.command()
def serve(
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on."),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind."),
    db: Optional[Path] = typer.Option(
        None, "--db",
        help=(
            "SQLite database file (default: ~/.antcrew/platform.db). "
            "Override with DATABASE_URL env var for PostgreSQL."
        ),
    ),
    api_key: Optional[str] = typer.Option(
        None, "--api-key",
        help="Require this key on all API requests (sets PLATFORM_API_KEY). "
             "Also readable from ANTCREW_API_KEY env var.",
        envvar="ANTCREW_API_KEY",
        show_default=False,
    ),
    reload: bool = typer.Option(
        False, "--reload",
        help="Auto-reload on code changes (development only).",
    ),
    workers: int = typer.Option(
        1, "--workers", "-w",
        help="Number of uvicorn worker processes. >1 requires HITL_DB_POLLING=1.",
    ),
    open_browser: bool = typer.Option(
        False, "--open", "-o",
        help="Open the dashboard in the default browser after startup.",
    ),
    local: bool = typer.Option(
        False, "--local",
        help="Local dev mode: SQLite, no auth, open browser automatically. "
             "Equivalent to --open with localhost defaults.",
    ),
    background: bool = typer.Option(
        False, "--background", "-b",
        help="Run as a background daemon. PID is written to ~/.antcrew/platform.pid. "
             "Stop with: antcrew serve --stop",
    ),
    stop: bool = typer.Option(
        False, "--stop",
        help="Stop a background antcrew platform server started with --background.",
    ),
) -> None:
    """Start antcrew platform locally (SQLite, no auth by default).

    \b
    Quick start:
        antcrew serve

    \b
    Custom port and DB:
        antcrew serve --port 9000 --db ./myproject.db

    \b
    With API key authentication:
        antcrew serve --api-key mysecret
        ANTCREW_API_KEY=mysecret antcrew serve

    \b
    PostgreSQL + multi-worker:
        DATABASE_URL=postgresql+asyncpg://user:pass@localhost/antcrew \\
        HITL_DB_POLLING=1 antcrew serve --workers 4

    \b
    antcrew-platform must be importable. Run from the antcrew-platform directory
    or install it: pip install antcrew-platform
    """
    if local:
        open_browser = True
        if host not in ("127.0.0.1", "localhost"):
            host = "127.0.0.1"
        console.print("[dim]local mode — SQLite, no auth, browser will open[/dim]")

    # --stop: terminate a background server by PID
    if stop:
        if not _PID_FILE.exists():
            console.print("[yellow]No background server found[/] (no PID file at ~/.antcrew/platform.pid)")
            raise typer.Exit(1)
        try:
            pid = int(_PID_FILE.read_text().strip())
            if sys.platform == "win32":
                import subprocess
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], check=True, capture_output=True)
            else:
                os.kill(pid, signal.SIGTERM)
            _PID_FILE.unlink(missing_ok=True)
            console.print(f"[green]Stopped[/] antcrew platform server (PID {pid})")
        except (ValueError, ProcessLookupError):
            console.print("[yellow]Server process not found[/] — removing stale PID file")
            _PID_FILE.unlink(missing_ok=True)
            raise typer.Exit(1)
        except Exception as exc:
            console.print(f"[red]Failed to stop server:[/] {exc}")
            raise typer.Exit(1)
        raise typer.Exit(0)

    # Set DATABASE_URL before importing the platform (it reads at import time).
    if "DATABASE_URL" not in os.environ:
        db_path = Path(db or _DEFAULT_DB).expanduser().resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
        console.print(f"[dim]DB: {db_path}[/dim]")

    # Apply API key if provided.
    if api_key:
        os.environ["PLATFORM_API_KEY"] = api_key
        console.print("auth enabled")
    elif host not in ("127.0.0.1", "localhost") and "PLATFORM_API_KEY" not in os.environ:
        console.print(
            "[yellow]Warning:[/yellow] running on a public host without auth. "
            "Set --api-key or ANTCREW_API_KEY to protect the API."
        )

    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]uvicorn not installed.[/red]\n"
            "Install with: [bold]pip install uvicorn[standard][/bold]"
        )
        raise typer.Exit(1)

    if workers > 1 and os.environ.get("HITL_DB_POLLING", "0") != "1":
        console.print(
            "[yellow]Warning:[/yellow] --workers > 1 requires "
            "[bold]HITL_DB_POLLING=1[/bold] for HITL reviews to work across workers."
        )

    url = f"http://{host}:{port}"

    # --background: spawn a detached uvicorn subprocess and exit
    if background:
        # Anti-duplicate: refuse if an existing background server is still alive.
        if _PID_FILE.exists():
            try:
                existing_pid = int(_PID_FILE.read_text().strip())
                alive = False
                if sys.platform == "win32":
                    import subprocess as _sp
                    r = _sp.run(
                        ["tasklist", "/FI", f"PID eq {existing_pid}", "/NH"],
                        capture_output=True, text=True,
                    )
                    alive = str(existing_pid) in r.stdout
                else:
                    os.kill(existing_pid, 0)
                    alive = True
            except (ValueError, ProcessLookupError, OSError):
                alive = False

            if alive:
                console.print(
                    f"[yellow]Already running[/yellow] — antcrew platform is alive at PID {existing_pid}.\n"
                    f"Stop it first with: [bold]antcrew serve --stop[/bold]"
                )
                raise typer.Exit(1)
            else:
                _PID_FILE.unlink(missing_ok=True)

        import subprocess
        env = dict(os.environ)
        cmd = [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", host, "--port", str(port),
        ]
        if reload:
            cmd.append("--reload")
        if workers > 1 and not reload:
            cmd += ["--workers", str(workers)]

        if sys.platform == "win32":
            proc = subprocess.Popen(
                cmd, env=env,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        else:
            proc = subprocess.Popen(
                cmd, env=env,
                start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True,
            )

        _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PID_FILE.write_text(str(proc.pid))
        console.print(
            f"\n[bold green]antcrew platform[/bold green]  {url}  "
            f"[dim](PID {proc.pid}, background)[/dim]\n"
            f"Stop with: [bold]antcrew serve --stop[/bold]"
        )
        raise typer.Exit(0)

    console.print(f"\n[bold green]antcrew platform[/bold green]  {url}\n")

    if open_browser:
        import threading
        import webbrowser

        def _open() -> None:
            import time
            import urllib.request
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                try:
                    urllib.request.urlopen(f"{url}/health", timeout=1)  # nosec B310
                    break
                except Exception:
                    time.sleep(0.4)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()

    # Use string form — uvicorn handles the import, no early platform check needed.
    # If app.main is not importable, uvicorn will report a clear import error.
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        workers=workers if not reload else 1,
    )
