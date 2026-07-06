"""antcrew doctor — verify environment, API keys, and optional dependencies."""
from __future__ import annotations

import os
import sys
from typing import Optional

import typer
from rich.table import Table

from antcrew.cli._app import app, console


def _check(label: str, ok: bool, detail: str = "") -> tuple[str, str, str]:
    """Return (label, status_markup, detail)."""
    if ok:
        return label, "[green]OK[/green]", detail
    return label, "[red]FAIL[/red]", detail


def _warn(label: str, detail: str = "") -> tuple[str, str, str]:
    return label, "[yellow]WARN[/yellow]", detail


def _try_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


@app.command()
def doctor(
    ping: bool = typer.Option(
        False, "--ping",
        help="Actually call each configured API to verify connectivity (uses tokens).",
    ),
    platform: Optional[str] = typer.Option(
        None, "--platform",
        help="Check if antcrew-platform is reachable at this URL (e.g. http://localhost:8000).",
    ),
) -> None:
    """Check API keys, optional dependencies, and (optionally) live API access.

    \b
    Quick environment check (no API calls):
        antcrew doctor

    Full connectivity check (makes a tiny live API call per key found):
        antcrew doctor --ping
    """
    from antcrew import __version__ as _ver

    rows: list[tuple[str, str, str]] = []

    # ── Runtime ──────────────────────────────────────────────────────────────
    py = sys.version_info
    rows.append(_check(
        "Python version",
        py >= (3, 11),
        f"{py.major}.{py.minor}.{py.micro} (need ≥3.11)",
    ))
    rows.append(("antcrew version", "[cyan]INFO[/cyan]", _ver))

    # ── Core API keys ────────────────────────────────────────────────────────
    rows.append(("", "", ""))   # spacer

    ant_key = os.environ.get("ANTHROPIC_API_KEY", "")
    rows.append(_check(
        "ANTHROPIC_API_KEY",
        bool(ant_key),
        f"sk-ant-…{ant_key[-6:]}" if ant_key else "not set — Anthropic models unavailable",
    ))

    oai_key = os.environ.get("OPENAI_API_KEY", "")
    rows.append(_check(
        "OPENAI_API_KEY",
        bool(oai_key),
        f"sk-…{oai_key[-6:]}" if oai_key else "not set — OpenAI models unavailable",
    ) if oai_key else _warn(
        "OPENAI_API_KEY",
        "not set (optional — only needed for OpenAI models)",
    ))

    groq_key = os.environ.get("GROQ_API_KEY", "")
    rows.append(_check(
        "GROQ_API_KEY",
        bool(groq_key),
        f"gsk_…{groq_key[-6:]}" if groq_key else "not set",
    ) if groq_key else _warn(
        "GROQ_API_KEY",
        "not set (optional — only needed for Groq models)",
    ))

    gg_key = os.environ.get("GOOGLE_API_KEY", "")
    rows.append(_check(
        "GOOGLE_API_KEY",
        bool(gg_key),
        f"AIza…{gg_key[-6:]}" if gg_key else "not set",
    ) if gg_key else _warn(
        "GOOGLE_API_KEY",
        "not set (optional — only needed for Gemini models)",
    ))

    api_key_env = os.environ.get("ANTCREW_API_KEY", "")
    rows.append(_warn(
        "ANTCREW_API_KEY",
        "not set — REST server runs without auth (OK for local dev)",
    ) if not api_key_env else _check(
        "ANTCREW_API_KEY",
        True,
        "set — REST server will require Bearer token",
    ))

    # ── Optional dependencies ─────────────────────────────────────────────────
    rows.append(("", "", ""))   # spacer

    opt_deps: list[tuple[str, str, str]] = [
        ("fastapi",   "antcrew serve / REST API"),
        ("uvicorn",   "antcrew serve / REST API"),
        ("openai",    "OpenAI / Azure models"),
        ("chromadb",  "ChromaMemory (long-term agent memory)"),
        ("watchdog",  "antcrew watch (file-change trigger)"),
        ("aiosqlite", "SqliteSaver checkpointer"),
        ("aiofiles",  "static file serving (dashboard)"),
    ]
    for pkg, purpose in opt_deps:
        ok = _try_import(pkg)
        if ok:
            rows.append(_check(pkg, True, purpose))
        else:
            rows.append(_warn(pkg, f"not installed — {purpose} unavailable"))

    # ── Platform health ──────────────────────────────────────────────────────
    if platform:
        rows.append(("", "", ""))
        rows += _platform_check(platform.rstrip("/"))

    # ── Live ping (optional) ─────────────────────────────────────────────────
    if ping:
        rows.append(("", "", ""))   # spacer
        rows += _live_ping(ant_key, oai_key, groq_key, gg_key)

    # ── Render ───────────────────────────────────────────────────────────────
    tbl = Table(title="antcrew doctor", show_header=True, header_style="bold dim")
    tbl.add_column("Check",  style="bold", no_wrap=True)
    tbl.add_column("Status", no_wrap=True)
    tbl.add_column("Detail")

    for label, status, detail in rows:
        if label == "":
            tbl.add_section()
            continue
        tbl.add_row(label, status, detail)

    console.print(tbl)

    # Summary line
    fails = sum(1 for _, s, _ in rows if "FAIL" in s)
    warns = sum(1 for _, s, _ in rows if "WARN" in s)
    if fails:
        console.print(f"\n[red bold]{fails} check(s) failed.[/]  Fix the issues above before running antcrew.")
    elif warns:
        console.print(f"\n[yellow]{warns} warning(s).[/]  Everything should work; install optional deps as needed.")
    else:
        console.print("\n[green bold]All checks passed.[/]")


def _platform_check(base_url: str) -> list[tuple[str, str, str]]:
    """Check reachability and health of a running antcrew-platform instance."""
    rows: list[tuple[str, str, str]] = []
    try:
        import urllib.request
        import urllib.error
        from antcrew.cli.configure_cmd import get_platform_api_key as _get_key
        req = urllib.request.Request(f"{base_url}/health", method="GET")
        platform_key = _get_key()
        if platform_key:
            req.add_header("X-Api-Key", platform_key)
        with urllib.request.urlopen(req, timeout=5) as resp:
            import json as _json
            data = _json.loads(resp.read())
            db_ok = data.get("db", False)
            version = data.get("version", "?")
            rows.append(_check(
                "Platform reachable",
                True,
                f"{base_url}  (v{version})",
            ))
            rows.append(_check(
                "Platform DB",
                db_ok,
                "connected" if db_ok else "unreachable — check DATABASE_URL on the server",
            ))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            rows.append(_check(
                "Platform reachable",
                False,
                f"{base_url} — 401 Unauthorized (set ANTCREW_PLATFORM_API_KEY)",
            ))
        else:
            rows.append(_check("Platform reachable", False, f"HTTP {exc.code}"))
    except Exception as exc:
        rows.append(_check(
            "Platform reachable",
            False,
            f"{base_url} — {exc} (is antcrew serve running?)",
        ))
    return rows


def _live_ping(
    ant_key: str,
    oai_key: str,
    groq_key: str,
    gg_key: str,
) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []

    if ant_key:
        try:
            import anthropic as _ant
            _ant.Anthropic(api_key=ant_key).models.list()
            rows.append(_check("Anthropic API ping", True, "reachable"))
        except Exception as exc:
            rows.append(_check("Anthropic API ping", False, str(exc)[:80]))

    if oai_key and _try_import("openai"):
        try:
            from openai import OpenAI as _OAI
            _OAI(api_key=oai_key).models.list()
            rows.append(_check("OpenAI API ping", True, "reachable"))
        except Exception as exc:
            rows.append(_check("OpenAI API ping", False, str(exc)[:80]))

    if groq_key:
        try:
            from groq import Groq as _Groq
            _Groq(api_key=groq_key).models.list()
            rows.append(_check("Groq API ping", True, "reachable"))
        except Exception as exc:
            rows.append(_check("Groq API ping", False, str(exc)[:80]))

    if gg_key:
        try:
            import httpx as _httpx
            r = _httpx.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": gg_key},
                timeout=5,
            )
            r.raise_for_status()
            rows.append(_check("Google API ping", True, "reachable"))
        except Exception as exc:
            rows.append(_check("Google API ping", False, str(exc)[:80]))

    return rows
