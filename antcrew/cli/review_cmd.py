"""antcrew review — interactive terminal UI for pending HITL reviews."""
from __future__ import annotations

import json
import os
from typing import Optional

import typer

from antcrew.cli._app import app, console


def _artifact_excerpt(artifact: dict) -> str:
    if not artifact or not isinstance(artifact, dict):
        return ""
    title = artifact.get("title") or artifact.get("name") or ""
    tickets = artifact.get("tickets") or artifact.get("items") or []
    if tickets and isinstance(tickets, list):
        first = tickets[0]
        if isinstance(first, dict):
            ticket_title = first.get("title") or first.get("name") or ""
            if ticket_title:
                count = len(tickets)
                suffix = f" (+{count - 1} more)" if count > 1 else ""
                return f"{ticket_title}{suffix}"
    if title:
        return title[:100]
    for v in artifact.values():
        if isinstance(v, str) and v.strip():
            return v.strip()[:100]
    return ""


@app.command()
def review(
    url: str = typer.Option(
        "",
        "--url",
        help="Platform URL. Defaults to ANTCREW_PLATFORM_URL env var or http://localhost:8000.",
        envvar="ANTCREW_PLATFORM_URL",
    ),
    api_key: str = typer.Option(
        "",
        "--api-key",
        help="Platform API key. Defaults to ANTCREW_PLATFORM_API_KEY env var.",
        envvar="ANTCREW_PLATFORM_API_KEY",
    ),
    open_browser: bool = typer.Option(
        False, "--open",
        help="Open the platform reviews page in the browser instead of the terminal UI.",
    ),
    run_filter: Optional[str] = typer.Option(
        None, "--run",
        help="Filter pending reviews to a specific run_id.",
    ),
) -> None:
    """Interactively resolve pending HITL reviews from the platform.

    \b
    Without --open, lists pending reviews in the terminal and lets you
    approve, reject, or add feedback directly without opening a browser.

    \b
    With --open, opens the platform /reviews page in the default browser.

    \b
    Examples:
        antcrew review
        antcrew review --url http://localhost:8000 --open
        antcrew review --run <run_id>
    """
    platform_url = (
        url or os.environ.get("ANTCREW_PLATFORM_URL", "http://localhost:8000")
    ).rstrip("/")
    from antcrew.cli.configure_cmd import get_platform_api_key as _get_key
    _api_key = api_key or _get_key()

    if open_browser:
        import webbrowser
        reviews_url = f"{platform_url}/reviews"
        console.print(f"[dim]Opening {reviews_url}…[/dim]")
        webbrowser.open(reviews_url)
        return

    try:
        import httpx
    except ImportError:
        console.print(
            "[red]Error:[/] httpx is required for [bold]antcrew review[/bold]. "
            "Install with: [cyan]pip install httpx[/cyan]"
        )
        raise typer.Exit(1)

    headers = {"X-Api-Key": _api_key} if _api_key else {}
    params: dict = {"status": "pending"}
    if run_filter:
        params["run_id"] = run_filter

    try:
        r = httpx.get(
            f"{platform_url}/reviews/",
            params=params,
            headers=headers,
            timeout=8.0,
        )
        r.raise_for_status()
        reviews = r.json()
    except httpx.ConnectError:
        console.print(
            f"[red]Error:[/] Cannot connect to platform at [cyan]{platform_url}[/]\n"
            "[dim]Start the platform with: antcrew serve[/dim]"
        )
        raise typer.Exit(1)
    except httpx.HTTPStatusError as exc:
        console.print(f"[red]Error:[/] HTTP {exc.response.status_code} from platform")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(1)

    if not reviews:
        console.print("[green]✓[/] No pending reviews.")
        return

    console.print(
        f"\n[bold]{len(reviews)} pending review(s)[/bold]  "
        f"[dim]({platform_url}/reviews)[/dim]\n"
    )
    for i, rev in enumerate(reviews, 1):
        agent = rev.get("agent_name", "?")
        run_id_short = rev.get("run_id", "?")[:16]
        artifact_raw = rev.get("artifact_json") or "{}"
        try:
            artifact = json.loads(artifact_raw)
        except Exception:
            artifact = {}
        excerpt = _artifact_excerpt(artifact)
        options = json.loads(rev.get("options_json") or '["approve","reject"]')
        opts_str = " / ".join(options)
        console.print(
            f"  [bold cyan]{i}.[/]  agent=[yellow]{agent}[/]  "
            f"run=[dim]{run_id_short}…[/dim]"
        )
        if excerpt:
            console.print(f"     [dim]{excerpt[:120]}[/dim]")
        console.print(f"     options: [dim]{opts_str}[/dim]\n")

    if len(reviews) == 1:
        choice = 1
        console.print("[dim]Single review — selecting automatically.[/dim]\n")
    else:
        choice_str = typer.prompt(
            f"Select review (1-{len(reviews)}, 0 to exit)", default="1"
        )
        try:
            choice = int(choice_str)
        except ValueError:
            choice = 0
        if choice == 0:
            return
        if choice < 1 or choice > len(reviews):
            console.print("[yellow]Invalid selection.[/yellow]")
            raise typer.Exit(1)

    rev = reviews[choice - 1]
    review_id: str = rev["review_id"]
    agent = rev.get("agent_name", "?")
    options = json.loads(rev.get("options_json") or '["approve","reject"]')

    artifact_raw = rev.get("artifact_json") or "{}"
    try:
        artifact = json.loads(artifact_raw)
        artifact_str = json.dumps(artifact, indent=2, ensure_ascii=False)
    except Exception:
        artifact_str = artifact_raw

    console.print(
        f"[bold]Review[/]  [cyan]{review_id[:16]}…[/]  "
        f"agent=[yellow]{agent}[/]\n"
    )
    if len(artifact_str) > 1200:
        console.print(artifact_str[:1200])
        console.print("[dim]… (truncated — open browser for full artifact)[/dim]")
    else:
        console.print(artifact_str)
    console.print()

    decision = typer.prompt(
        f"Decision ({'/'.join(options)})",
        default=options[0],
    )
    if decision not in options:
        console.print(
            f"[yellow]Invalid decision. Choose from: {', '.join(options)}[/yellow]"
        )
        raise typer.Exit(1)

    payload: dict = {"decision": decision}

    if typer.confirm("Add feedback?", default=False):
        feedback = typer.prompt("Feedback")
        if feedback.strip():
            payload["feedback"] = feedback.strip()

    if decision == "edit":
        console.print("[dim]Paste the edited JSON (single line) and press Enter:[/dim]")
        edited = input("edited JSON > ").strip()
        if edited:
            payload["edited"] = edited

    try:
        r = httpx.post(
            f"{platform_url}/reviews/{review_id}",
            json=payload,
            headers=headers,
            timeout=8.0,
        )
        r.raise_for_status()
        data = r.json()
        console.print(
            f"\n[green]✓[/] Review [cyan]{review_id[:16]}…[/] resolved as "
            f"[bold]{data.get('decision', decision)}[/bold]"
        )
    except httpx.HTTPStatusError as exc:
        console.print(
            f"[red]Error:[/] HTTP {exc.response.status_code}: "
            f"{exc.response.text[:200]}"
        )
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(1)
