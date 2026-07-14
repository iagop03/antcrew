"""Setup wizard, cache management, and serve commands."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import app, console

@app.command()
def setup(
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Project name (skips the prompt)"),
    output: Path = typer.Option(Path("."), "--output", "-o", help="Output directory"),
    filename: str = typer.Option("agentteam.yaml", "--filename", "-f", help="YAML filename"),
) -> None:
    """Conversational wizard that generates an agentteam.yaml for your project."""
    from antcrew.agents.setup import SetupAgent
    agent = SetupAgent()
    agent.run_wizard(name=name, output_dir=output, filename=filename)


cache_app = typer.Typer(help="Manage the LLM response cache.")
app.add_typer(cache_app, name="cache")


@cache_app.command("clear")
def cache_clear(
    db: Path = typer.Argument(
        ...,
        help="Path to the SQLite cache file (e.g. ~/.antcrew/cache.db)",
    ),
    agent: Optional[str] = typer.Option(
        None, "--agent", "-a",
        help="Clear only entries for this agent (e.g. frontend_dev). Omit to clear all.",
    ),
) -> None:
    """Delete entries from a persistent cache file (all, or just one agent)."""
    from antcrew.models.cache import FileLLMCache
    db = Path(db).expanduser()
    if not db.exists():
        console.print(f"[yellow]Cache file not found:[/] {db}")
        raise typer.Exit(1)
    c = FileLLMCache(db)
    if agent:
        n = c.clear_agent(agent)
        console.print(f"[green]Cleared[/] {n} entr{'y' if n == 1 else 'ies'} for agent [cyan]{agent}[/] from [cyan]{db}[/]")
    else:
        size = c.size
        c.clear()
        console.print(f"[green]Cleared[/] {size} entr{'y' if size == 1 else 'ies'} from [cyan]{db}[/]")


@cache_app.command("stats")
def cache_stats(
    db: Path = typer.Argument(
        ...,
        help="Path to the SQLite cache file (e.g. ~/.antcrew/cache.db)",
    ),
) -> None:
    """Show the number of entries stored in a cache file."""
    from antcrew.models.cache import FileLLMCache
    db = Path(db).expanduser()
    if not db.exists():
        console.print(f"[yellow]Cache file not found:[/] {db}")
        raise typer.Exit(1)
    c = FileLLMCache(db)
    console.print(f"[cyan]{db}[/] — [bold]{c.size}[/] entries")


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Bind host"),  # nosec B104
    port: int = typer.Option(8000, "--port", "-p", help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev only)"),
    api_key: Optional[str] = typer.Option(
        None, "--api-key",
        help="Require this Bearer token on every request. "
             "Alternatively set ANTCREW_API_KEY env var. "
             "Omit to run without authentication (local dev only).",
        envvar="ANTCREW_API_KEY",
    ),
) -> None:
    """Start the AntCrew REST API server (requires pip install antcrew[server])."""
    import os as _os

    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]uvicorn is required.[/] Install it: [bold]pip install antcrew[server][/]"
        )
        raise typer.Exit(1)

    # Propagate key to server module (env var is the canonical channel)
    if api_key:
        _os.environ["ANTCREW_API_KEY"] = api_key
    elif not _os.environ.get("ANTCREW_API_KEY") and host not in ("127.0.0.1", "localhost"):
        console.print(
            "[yellow]Warning:[/] No API key set — the server is publicly accessible without "
            "authentication. Use [bold]--api-key SECRET[/] or set [bold]ANTCREW_API_KEY[/]."
        )

    from pathlib import Path as _Path
    _static_index = _Path(__file__).parent / "static" / "index.html"
    _has_dashboard = _static_index.exists()

    from antcrew import __version__ as _ver
    display_host = "localhost" if host in ("0.0.0.0", "::") else host  # nosec B104
    auth_status = "[green]auth enabled[/]" if (_os.environ.get("ANTCREW_API_KEY")) else "[yellow]no auth[/]"
    console.print(f"\n[bold green]AntCrew[/] v{_ver}  ({auth_status})")
    console.print(f"  API       → [cyan]http://{display_host}:{port}[/]")
    console.print(f"  Docs      → [dim]http://{display_host}:{port}/docs[/dim]")
    if _has_dashboard:
        console.print(f"  Dashboard → [bold cyan]http://{display_host}:{port}/ui/[/bold cyan]")
    console.print()
    uvicorn.run("antcrew.server:app", host=host, port=port, reload=reload)


