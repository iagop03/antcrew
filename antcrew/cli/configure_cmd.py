"""antcrew configure — save platform connection settings to .antcrew/config.yaml."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import app, console

_CONFIG_DIR = ".antcrew"
_CONFIG_FILE = "config.yaml"


def _global_config_path() -> Path:
    return Path.home() / _CONFIG_DIR / _CONFIG_FILE


def _project_config_path(cwd: Optional[Path] = None) -> Path:
    return (Path(cwd or ".").resolve()) / _CONFIG_DIR / _CONFIG_FILE


def _read_yaml(path: Path) -> dict:
    """Read a YAML file; return {} on any error or absence."""
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore[import]
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_config(cwd: Optional[Path] = None) -> dict:
    """Return merged config from global + project locations, project taking precedence.

    Search order (last wins):
      1. ~/.antcrew/config.yaml       — global / user-level defaults
      2. <cwd>/.antcrew/config.yaml   — project-level overrides
    """
    global_cfg = _read_yaml(_global_config_path())
    project_cfg = _read_yaml(_project_config_path(cwd))
    return {**global_cfg, **project_cfg}


def get_platform_api_key() -> str:
    """Return the platform API key; emit a deprecation warning for the old env var name.

    Canonical name: ANTCREW_PLATFORM_API_KEY
    Deprecated alias: ANTCREW_PLATFORM_KEY (will be removed in a future release)
    """
    key = os.environ.get("ANTCREW_PLATFORM_API_KEY", "")
    if not key:
        old = os.environ.get("ANTCREW_PLATFORM_KEY", "")
        if old:
            import warnings
            warnings.warn(
                "ANTCREW_PLATFORM_KEY is deprecated. "
                "Rename it to ANTCREW_PLATFORM_API_KEY to silence this warning.",
                DeprecationWarning,
                stacklevel=2,
            )
            return old
    return key


def apply_config_to_env(cwd: Optional[Path] = None) -> None:
    """Load cascaded config and inject values into os.environ (only if not already set).

    Called at CLI startup so all commands automatically inherit the configured
    platform URL and API key without repeating flags or env vars.
    """
    cfg = load_config(cwd)
    mapping = {
        "platform_url": "ANTCREW_PLATFORM_URL",
        "api_key": "ANTCREW_PLATFORM_API_KEY",
    }
    for cfg_key, env_key in mapping.items():
        if cfg_key in cfg and not os.environ.get(env_key):
            os.environ[env_key] = str(cfg[cfg_key])


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml  # type: ignore[import]
        path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    except ImportError:
        lines = "\n".join(f"{k}: {v}" for k, v in data.items())
        path.write_text(lines + "\n", encoding="utf-8")


@app.command()
def configure(
    platform_url: Optional[str] = typer.Option(
        None, "--platform-url", "-u",
        help="Platform base URL (e.g. https://antcrew.example.com).",
    ),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", "-k",
        help="Platform API key (X-Api-Key header).",
    ),
    global_: bool = typer.Option(
        False, "--global", "-g",
        help="Write to ~/.antcrew/config.yaml instead of the current directory.",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Directory where .antcrew/config.yaml is written (default: current dir). "
             "Ignored when --global is set.",
    ),
    show: bool = typer.Option(False, "--show", help="Print the current config and exit."),
) -> None:
    """Save platform connection settings to .antcrew/config.yaml.

    All antcrew CLI commands automatically pick up the saved URL and key
    so you don't need to pass flags or set env vars every time.

    Config is loaded in order: ~/.antcrew/config.yaml (global) then
    .antcrew/config.yaml in the current directory (project). Project values
    take precedence over global ones.

    Examples:
        antcrew configure --global --platform-url https://antcrew.example.com --api-key sk-abc
        antcrew configure --api-key sk-proj-override   # project-level override
        antcrew configure --show
    """
    if show:
        global_path = _global_config_path()
        project_path = _project_config_path(output)
        printed_any = False
        for label, path in [("global", global_path), ("project", project_path)]:
            if path.exists():
                console.print(f"[dim]{label}: {path}[/dim]")
                raw = _read_yaml(path)
                for k, v in raw.items():
                    display = str(v)
                    if k == "api_key" and len(display) > 8:
                        display = display[:4] + "…" + display[-4:]
                    console.print(f"  {k}: [cyan]{display}[/]")
                console.print()
                printed_any = True
        if not printed_any:
            console.print("[yellow]No config found.[/] Run [bold]antcrew configure --help[/] to get started.")
        return

    if platform_url is None and api_key is None:
        console.print(
            "[yellow]Nothing to save.[/] Pass [bold]--platform-url[/] and/or [bold]--api-key[/].\n"
            "Use [bold]--show[/] to print the current config."
        )
        raise typer.Exit(1)

    if global_:
        cfg_path = _global_config_path()
    else:
        cfg_path = _project_config_path(output)

    existing = _read_yaml(cfg_path)

    if platform_url is not None:
        existing["platform_url"] = platform_url.rstrip("/")
    if api_key is not None:
        existing["api_key"] = api_key

    _write_yaml(cfg_path, existing)

    scope = "global" if global_ else "project"
    console.print(f"\n[bold green]Saved[/] ({scope}) → [cyan]{cfg_path}[/]\n")
    for k, v in existing.items():
        display = str(v)
        if k == "api_key" and len(display) > 8:
            display = display[:4] + "…" + display[-4:]
        console.print(f"  {k}: [cyan]{display}[/]")
    console.print(
        "\n[dim]All antcrew commands will now use these settings automatically.[/]"
    )
