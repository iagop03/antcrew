"""Publish command (GitHub PR / Confluence)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import app, console


@app.command(name="publish")
def publish_cmd(
    state_file: Path = typer.Argument(
        ..., help="JSON state file produced by 'antcrew run --output …' or save_state()."
    ),
    github: bool = typer.Option(False, "--github", help="Open a GitHub PR with code artifacts."),
    github_token: Optional[str] = typer.Option(
        None, "--token", envvar="GITHUB_TOKEN", help="GitHub personal access token.",
    ),
    github_repo: Optional[str] = typer.Option(
        None, "--repo", help="GitHub repo in 'owner/repo' format.",
    ),
    github_base: str = typer.Option("main", "--base", help="Base branch for the PR."),
    confluence: bool = typer.Option(
        False, "--confluence", help="Publish PRD and docs to Confluence."
    ),
    confluence_url: Optional[str] = typer.Option(
        None, "--confluence-url", envvar="CONFLUENCE_URL",
        help="Atlassian base URL (e.g. https://yourorg.atlassian.net).",
    ),
    confluence_email: Optional[str] = typer.Option(
        None, "--confluence-email", envvar="CONFLUENCE_EMAIL",
    ),
    confluence_token: Optional[str] = typer.Option(
        None, "--confluence-token", envvar="CONFLUENCE_API_TOKEN",
    ),
    confluence_space: Optional[str] = typer.Option(
        None, "--space", help="Confluence space key (e.g. DEV).",
    ),
    confluence_parent: Optional[str] = typer.Option(
        None, "--parent", help="Optional parent page title in Confluence.",
    ),
) -> None:
    """Publish pipeline output to GitHub (PR) and/or Confluence.

    \b
    Open a GitHub PR with all generated code:
        antcrew publish run_output.json \\
          --github --repo my-org/my-repo

    \b
    Publish PRD + docs to Confluence:
        antcrew publish run_output.json \\
          --confluence --confluence-url https://myorg.atlassian.net \\
          --space DEV

    \b
    Both at once:
        antcrew publish run_output.json --github --repo org/repo --confluence ...
    """
    if not github and not confluence:
        console.print("[yellow]Nothing to publish.[/] Specify --github and/or --confluence.")
        raise typer.Exit(0)

    if not state_file.exists():
        console.print(f"[red]State file not found:[/] {state_file}")
        raise typer.Exit(1)

    # Load and re-hydrate the state dict into Pydantic objects
    from antcrew.core.artifacts import (
        PRD,
        CodeArtifact,
        DevOpsArtifact,
        DocumentationArtifact,
        ResearchDocument,
        Ticket,
    )
    from antcrew.utils.persistence import load_state as _load_state

    raw = _load_state(state_file)

    def _hydrate(raw_state: dict) -> dict:
        state: dict = dict(raw_state)
        if state.get("prd"):
            try:
                state["prd"] = PRD.model_validate(state["prd"])
            except Exception:
                pass
        for key, cls in [
            ("code_artifacts", CodeArtifact),
            ("devops_artifacts", DevOpsArtifact),
            ("doc_artifacts", DocumentationArtifact),
            ("tickets", Ticket),
        ]:
            if state.get(key):
                try:
                    state[key] = [cls.model_validate(a) for a in state[key]]
                except Exception:
                    pass
        if state.get("research_document"):
            try:
                state["research_document"] = ResearchDocument.model_validate(
                    state["research_document"]
                )
            except Exception:
                pass
        return state

    state = _hydrate(raw)

    # ------------------------------------------------------------------
    # GitHub
    # ------------------------------------------------------------------
    if github:
        if not github_token:
            console.print(
                "[red]Error:[/] --token / $GITHUB_TOKEN is required for --github."
            )
            raise typer.Exit(1)
        if not github_repo:
            console.print("[red]Error:[/] --repo is required for --github.")
            raise typer.Exit(1)

        from antcrew.integrations.github import GitHubIntegration
        gh = GitHubIntegration(
            token=github_token, repo=github_repo, base_branch=github_base
        )
        with console.status("Opening GitHub PR…"):
            try:
                pr_url = gh.create_pr(state)
            except Exception as exc:
                console.print(f"[red]GitHub error:[/] {exc}")
                raise typer.Exit(1)
        console.print(f"[green]PR opened:[/] {pr_url}")

    # ------------------------------------------------------------------
    # Confluence
    # ------------------------------------------------------------------
    if confluence:
        missing = [
            f for f, v in [
                ("--confluence-url", confluence_url),
                ("--confluence-email", confluence_email),
                ("--confluence-token", confluence_token),
                ("--space", confluence_space),
            ]
            if not v
        ]
        if missing:
            console.print(
                "[red]Error:[/] Missing required options for --confluence: "
                + ", ".join(missing)
            )
            raise typer.Exit(1)

        from antcrew.integrations.confluence import ConfluenceIntegration
        cf = ConfluenceIntegration(
            url=confluence_url,  # type: ignore[arg-type]
            email=confluence_email,  # type: ignore[arg-type]
            api_token=confluence_token,  # type: ignore[arg-type]
        )
        published: list[str] = []
        with console.status("Publishing to Confluence…"):
            try:
                if state.get("prd"):
                    page = cf.publish_prd(
                        state,
                        confluence_space,  # type: ignore[arg-type]
                        parent_title=confluence_parent,
                    )
                    if page:
                        published.append(f"PRD — {state['prd'].title}")

                if state.get("research_document"):
                    page = cf.publish_research(
                        state,
                        confluence_space,  # type: ignore[arg-type]
                        parent_title=confluence_parent,
                    )
                    if page:
                        doc = state["research_document"]
                        published.append(f"Research — {doc.title}")

                doc_pages = cf.publish_docs(
                    state,
                    confluence_space,  # type: ignore[arg-type]
                    parent_title=confluence_parent,
                )
                published.extend(f"Doc — {p.get('title', '?')}" for p in doc_pages)
            except Exception as exc:
                console.print(f"[red]Confluence error:[/] {exc}")
                raise typer.Exit(1)

        if published:
            console.print("[green]Published to Confluence:[/]")
            for item in published:
                console.print(f"  • {item}")
        else:
            console.print("[yellow]Nothing to publish to Confluence[/] — no PRD, research, or doc artifacts found.")


