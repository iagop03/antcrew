"""antcrew template — save and run reusable pipeline configurations from the platform."""
from __future__ import annotations

import os
from typing import Optional

import typer

from antcrew.cli._app import app, console

_template_app = typer.Typer(
    name="template",
    help="Manage reusable run templates saved on the platform.",
    add_completion=False,
)
app.add_typer(_template_app)

# Platform stores team names as class names; OSS _build_team expects lowercase slugs.
_PLATFORM_TO_OSS: dict[str, str] = {
    "DevTeam":       "dev",
    "FullStackTeam": "fullstack",
    "ResearchTeam":  "research",
    "ContentTeam":   "content",
    "FeatureTeam":   "feature",
}


def _get_client(base_url: str, api_key: str):
    try:
        import httpx
    except ImportError:
        console.print("[red]Error:[/] httpx required.  pip install httpx")
        raise typer.Exit(1)
    headers = {"X-Api-Key": api_key} if api_key else {}
    return httpx.Client(base_url=base_url, headers=headers, timeout=10.0)


def _resolve_conn(url_override: str = "", key_override: str = "") -> tuple[str, str]:
    from antcrew.cli.configure_cmd import get_platform_api_key
    url = (url_override or os.environ.get("ANTCREW_PLATFORM_URL", "http://localhost:8000")).rstrip("/")
    key = key_override or get_platform_api_key()
    return url, key


@_template_app.command("list")
def template_list(
    url: str = typer.Option("", "--url", envvar="ANTCREW_PLATFORM_URL",
                             help="Platform URL (default: http://localhost:8000)."),
    api_key: str = typer.Option("", "--api-key", envvar="ANTCREW_PLATFORM_API_KEY",
                                 help="Platform API key.", show_default=False),
) -> None:
    """List templates saved on the platform."""
    _url, _key = _resolve_conn(url, api_key)

    with _get_client(_url, _key) as client:
        try:
            r = client.get("/templates/")
            r.raise_for_status()
        except Exception as exc:
            console.print(f"[red]Error:[/] {exc}")
            raise typer.Exit(1)

    templates = r.json()
    if not templates:
        console.print("[dim]No templates saved.[/dim]")
        return

    console.print(f"\n[bold]{len(templates)} template(s)[/bold]  [dim]{_url}/templates[/dim]\n")
    for t in templates:
        hitl_flag = "  [yellow]hitl[/yellow]" if t.get("hitl") else ""
        console.print(
            f"  [bold cyan]{t['id']}[/bold cyan]  [green]{t['name']}[/green]"
            f"  [dim]{t['team']}[/dim]{hitl_flag}"
        )
        if t.get("request"):
            excerpt = t["request"][:80].replace("\n", " ")
            suffix = "…" if len(t["request"]) > 80 else ""
            console.print(f"     [dim]{excerpt}{suffix}[/dim]")
    console.print()


@_template_app.command("save")
def template_save(
    name: str = typer.Option(..., "--name", "-n", help="Template name (unique label)."),
    team: str = typer.Option(..., "--team", "-t",
                              help="Team name (e.g. DevTeam, FullStackTeam)."),
    request: str = typer.Option(..., "--request", "-q",
                                 help="The pipeline request / task description."),
    max_cost: Optional[float] = typer.Option(None, "--max-cost", help="Max cost in USD."),
    hitl: bool = typer.Option(False, "--hitl/--no-hitl",
                               help="Enable HITL by default for this template."),
    repo_url: Optional[str] = typer.Option(None, "--repo-url", help="Default repo URL."),
    url: str = typer.Option("", "--url", envvar="ANTCREW_PLATFORM_URL"),
    api_key: str = typer.Option("", "--api-key", envvar="ANTCREW_PLATFORM_API_KEY",
                                 show_default=False),
) -> None:
    """Save a reusable run configuration to the platform."""
    _url, _key = _resolve_conn(url, api_key)

    payload: dict = {"name": name, "team": team, "request": request, "hitl": hitl}
    if max_cost is not None:
        payload["max_cost_usd"] = max_cost
    if repo_url:
        payload["repo_url"] = repo_url

    with _get_client(_url, _key) as client:
        try:
            r = client.post("/templates/", json=payload)
            r.raise_for_status()
        except Exception as exc:
            console.print(f"[red]Error:[/] {exc}")
            raise typer.Exit(1)

    t = r.json()
    console.print(
        f"\n[bold green]Saved[/bold green]  [cyan]{t['name']}[/cyan]  "
        f"[dim]id={t['id']}  team={t['team']}[/dim]\n"
        f"Run it with: [bold]antcrew template run {t['id']}[/bold]"
    )


@_template_app.command("delete")
def template_delete(
    template_id: int = typer.Argument(..., help="Template ID to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
    url: str = typer.Option("", "--url", envvar="ANTCREW_PLATFORM_URL"),
    api_key: str = typer.Option("", "--api-key", envvar="ANTCREW_PLATFORM_API_KEY",
                                 show_default=False),
) -> None:
    """Delete a saved template from the platform."""
    _url, _key = _resolve_conn(url, api_key)

    if not yes:
        typer.confirm(f"Delete template {template_id}?", abort=True)

    with _get_client(_url, _key) as client:
        try:
            r = client.delete(f"/templates/{template_id}")
            r.raise_for_status()
        except Exception as exc:
            console.print(f"[red]Error:[/] {exc}")
            raise typer.Exit(1)

    console.print(f"[green]Deleted[/green] template {template_id}")


@_template_app.command("run")
def template_run(
    template_id: int = typer.Argument(...,
                                       help="Template ID (from `antcrew template list`)."),
    request_override: Optional[str] = typer.Option(
        None, "--request", "-q", help="Override the template's request text.",
    ),
    model: str = typer.Option("claude", "--model", "-m"),
    dry_run: bool = typer.Option(False, "--dry-run",
                                  help="Print what would run without executing."),
    url: str = typer.Option("", "--url", envvar="ANTCREW_PLATFORM_URL"),
    api_key: str = typer.Option("", "--api-key", envvar="ANTCREW_PLATFORM_API_KEY",
                                 show_default=False),
) -> None:
    """Fetch a saved template and run it as a local pipeline."""
    _url, _key = _resolve_conn(url, api_key)

    with _get_client(_url, _key) as client:
        try:
            r = client.get("/templates/")
            r.raise_for_status()
            templates = r.json()
        except Exception as exc:
            console.print(f"[red]Error fetching templates:[/] {exc}")
            raise typer.Exit(1)

    tmpl = next((t for t in templates if t["id"] == template_id), None)
    if tmpl is None:
        console.print(f"[red]Error:[/] Template {template_id} not found.")
        raise typer.Exit(1)

    request = request_override or tmpl["request"]
    platform_team = tmpl["team"]
    oss_team = _PLATFORM_TO_OSS.get(platform_team, platform_team.lower().replace("team", ""))

    console.print(
        f"\n[bold]Template:[/bold] [cyan]{tmpl['name']}[/cyan]  "
        f"[dim]team={platform_team}[/dim]\n"
        f"[bold]Request:[/bold] {request[:120]}\n"
    )

    if dry_run:
        console.print("[dim]--dry-run: skipping execution.[/dim]")
        return

    integrations: list = []
    if tmpl.get("hitl") and os.environ.get("ANTCREW_PLATFORM_URL"):
        try:
            from antcrew.integrations.platform import PlatformChannel
            integrations.append(PlatformChannel())
        except Exception:
            pass

    from antcrew.cli._shared import _build_team, _print_state

    active_team = _build_team(oss_team, model, integrations)
    result = active_team.run(request)
    state = result.state if hasattr(result, "state") else result
    _print_state(state, oss_team)
