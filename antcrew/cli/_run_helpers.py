"""Streaming, progress, output-dir and REPL helpers for the run command."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from antcrew.cli._app import console
from antcrew.cli._shared import _print_state

def _print_dry_run(team) -> None:
    """Print the step structure of a CustomTeam without running it."""
    from rich.table import Table
    from antcrew.teams.custom_team import _NestedTeamAgent

    groups = team._step_groups
    n_agents = sum(len(g) for g in groups)
    console.print(
        f"\n[bold]Dry run[/bold] — [cyan]{len(groups)}[/cyan] step group(s), "
        f"[cyan]{n_agents}[/cyan] agent(s)"
    )
    if team._vars:
        console.print(f"  [dim]vars:[/dim] {list(team._vars.keys())}")

    tbl = Table(show_header=True, header_style="bold", box=None, show_edge=False)
    tbl.add_column("Step", style="dim", width=8)
    tbl.add_column("Agent", style="cyan")
    tbl.add_column("Type", style="dim", width=7)
    tbl.add_column("output_key", style="green")
    tbl.add_column("Flags", style="yellow")

    for i, group in enumerate(groups, 1):
        for j, step in enumerate(group):
            idx = f"[{i}/{len(groups)}]" if j == 0 else ""
            gtype = "par" if len(group) > 1 else "seq"
            is_nested = isinstance(step.agent, _NestedTeamAgent)
            out_key = (
                f"{step.agent.name}/* (merged)"
                if is_nested
                else step.agent._output_key
            )
            flags = []
            if step.condition:
                flags.append(f"if:{','.join(step.condition)}")
            if step.max_retries:
                flags.append(f"retry×{step.max_retries}")
            if step.timeout is not None:
                flags.append(f"timeout:{step.timeout:.0f}s")
            if step.on_error == "skip":
                dv = f"={step.default!r}" if step.default is not None else ""
                flags.append(f"skip{dv}")
            if not is_nested and hasattr(step.agent, "_post_process") and step.agent._post_process:
                flags.append(f"pp:({','.join(step.agent._post_process)})")
            tbl.add_row(idx, step.agent.name, gtype, out_key, " ".join(flags))

    console.print(tbl)
    console.print("\n[dim]No LLM calls will be made.[/dim]\n")


def _run_custom_with_progress(team, request: str, thread: str, *, stream: bool, llm=None):
    """Run a CustomTeam with per-step Rich progress output."""
    import time as _time
    from rich.live import Live
    from rich.panel import Panel as _Panel
    from rich.text import Text

    total = len(team._step_groups)
    _state: dict = {"n": 0, "name": "", "t0": 0.0}

    def _on_step(name: str, event: str) -> None:
        if event == "start":
            _state["n"] += 1
            _state["name"] = name
            _state["t0"] = _time.monotonic()
        elif event == "done":
            elapsed = _time.monotonic() - _state["t0"]
            console.print(
                f"  [dim][{_state['n']}/{total}][/dim] "
                f"[green]✓[/] [bold]{name}[/bold] "
                f"[dim]({elapsed:.1f}s)[/dim]"
            )
        elif event == "skip":
            _state["n"] += 1
            console.print(
                f"  [dim][{_state['n']}/{total}] ○ {name} (skipped)[/dim]"
            )

    if stream and llm is not None:
        # Streaming mode: show step progress lines + live token panel.
        display: dict = {"agent": "", "text": "", "panel": None}

        def _on_token(token: str) -> None:
            if llm.current_agent != display["agent"]:
                display["agent"] = llm.current_agent
                display["text"] = ""
            display["text"] += token
            live.update(_Panel(
                Text(display["text"][-800:], overflow="fold"),
                title=f"[bold cyan]{display['agent'] or _state['name'] or '…'}[/bold cyan]",
                border_style="cyan",
            ))

        llm.on_token = _on_token
        team._on_step = _on_step
        try:
            with Live(
                _Panel("Starting…", border_style="dim"),
                console=console,
                refresh_per_second=15,
                transient=True,
            ) as live:
                state = team.run(request, thread_id=thread)
        finally:
            llm.on_token = None
            team._on_step = None
    else:
        # Non-streaming: just print each step event.
        team._on_step = _on_step
        try:
            with console.status(
                f"[bold]Running pipeline ({total} step{'s' if total != 1 else ''})…[/bold]",
                spinner="dots",
            ):
                state = team.run(request, thread_id=thread)
        finally:
            team._on_step = None

    return state


def _run_with_stream(team, request: str, thread: str, stream: bool, llm=None):
    """Run the pipeline, optionally showing tokens live with Rich."""
    from rich.live import Live
    from rich.panel import Panel as _Panel
    from rich.text import Text

    # CustomTeam: show per-step progress instead of a generic spinner.
    try:
        from antcrew.teams.custom_team import CustomTeam as _CT
        _is_custom = isinstance(team, _CT)
    except ImportError:
        _is_custom = False

    if _is_custom:
        return _run_custom_with_progress(team, request, thread, stream=stream, llm=llm)

    if not stream or llm is None:
        with console.status("[bold]Running…[/]"):
            return team.run(request, thread_id=thread)

    display: dict = {"agent": "", "text": ""}

    def _on_token(token: str) -> None:
        if llm.current_agent != display["agent"]:
            display["agent"] = llm.current_agent
            display["text"] = ""
        display["text"] += token
        live.update(_Panel(
            Text(display["text"][-800:], overflow="fold"),
            title=f"[bold cyan]{display['agent'] or '…'}[/bold cyan]",
            border_style="cyan",
        ))

    llm.on_token = _on_token

    with Live(
        _Panel("Starting…", border_style="dim"),
        console=console,
        refresh_per_second=15,
        transient=True,
    ) as live:
        state = team.run(request, thread_id=thread)

    llm.on_token = None
    return state


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

_OUTPUT_DIR_SKIP = frozenset({"request", "messages", "errors", "metadata"})


def _save_outputs_to_dir(state: Any, output_dir: Path) -> None:
    """Write each non-None step output in *state* to a file in *output_dir*."""
    raw: dict = state.state if hasattr(state, "state") else (state if isinstance(state, dict) else {})
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for key, value in raw.items():
        if key in _OUTPUT_DIR_SKIP or value is None:
            continue
        if isinstance(value, dict):
            p = output_dir / f"{key}.json"
            p.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            p = output_dir / f"{key}.txt"
            p.write_text(str(value), encoding="utf-8")
        count += 1
    if count:
        console.print(f"[dim]Outputs saved to [cyan]{output_dir}[/] ({count} file(s))[/dim]")


_REPL_CARRY_SKIP = frozenset({"request", "messages", "errors", "metadata"})


def _run_repl(
    team: Any, *, stream: bool, llm: Any, output_dir: Optional[Path],
    team_name: str, stateful: bool = False,
) -> None:
    """Interactive REPL loop — runs the pipeline until the user quits."""
    mode = " [dim](stateful)[/dim]" if stateful else ""
    console.print(
        f"\n[bold green]AntCrew REPL[/] — team=[cyan]{team_name}[/]{mode}  "
        "(type [bold]quit[/] or press Ctrl-C to exit)\n"
    )
    carry: dict = {}
    while True:
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Bye.[/dim]")
            break
        if not raw:
            continue
        if raw.lower() in ("quit", "exit", "q"):
            console.print("[dim]Bye.[/dim]")
            break
        try:
            if stateful and carry and hasattr(team, "_vars"):
                saved_vars = dict(team._vars)
                team._vars = {**team._vars, **carry}
                try:
                    state = _run_with_stream(team, raw, "repl", stream, llm=llm)
                finally:
                    team._vars = saved_vars
            else:
                state = _run_with_stream(team, raw, "repl", stream, llm=llm)
        except Exception as exc:
            console.print(f"[red bold]Error:[/] {exc}")
            continue
        _print_state(state, team_name)
        if output_dir:
            _save_outputs_to_dir(state, output_dir)
        if stateful:
            raw_state = state.state if hasattr(state, "state") else {}
            carry = {k: v for k, v in raw_state.items()
                     if k not in _REPL_CARRY_SKIP and v is not None}
        console.print()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

