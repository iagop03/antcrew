"""Typer app instances and CLI-level constants shared across all CLI modules."""
from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(
    name="antcrew",
    help="Multi-agent framework for software dev teams, built on LangGraph.",
    add_completion=False,
)
console = Console()

# Sub-app for flow commands (antcrew flow show / antcrew flow validate)
_flow_app = typer.Typer(
    name="flow",
    help="Inspect and validate flow definitions (YAML/JSON).",
    add_completion=False,
)
app.add_typer(_flow_app)

# Sub-app for project commands (antcrew project run / show / history)
_project_app = typer.Typer(
    name="project",
    help="Manage persistent multi-run projects (state survives across runs).",
    add_completion=False,
)
app.add_typer(_project_app)

_TEAM_CHOICES = ("dev", "fullstack", "research", "content", "custom", "feature", "auto", "routed", "direct")
_MODEL_HELP = "Model to use: claude (default), gpt-4o, ollama:<name>, groq:<name>, simulated"
