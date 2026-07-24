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
# HITL (Human-in-the-Loop) via ConsoleChannel
# ---------------------------------------------------------------------------

def _run_with_hitl(
    team, request: str, thread: str,
    *, hitl_timeout: Optional[float] = None, channel=None,
):
    """Run in HITL mode: blocks at each approval_required agent for terminal review.

    Uses ConsoleChannel (or a custom *channel*) so the agent pauses and prompts the
    user directly in the terminal.  Returns a RunResult wrapping the final state so
    --save, --write-back, and --output-dir all work identically to non-HITL runs.

    hitl_timeout: maximum seconds for the entire HITL run (all reviews combined).
    When exceeded the pipeline is aborted with exit code 1.

    channel: optional BaseChannel override (e.g. SlackNotifyChannel). Defaults to
    ConsoleChannel when not provided.

    No spinner is shown — the channel display IS the UX during HITL.
    """
    import concurrent.futures

    from antcrew.core.run_result import RunResult

    if channel is None:
        try:
            from antcrew.integrations.console import ConsoleChannel
        except ImportError as exc:
            console.print(f"[red]Error:[/] ConsoleChannel not available: {exc}")
            raise SystemExit(1)
        ch = ConsoleChannel()
    else:
        ch = channel
    all_agents = list(getattr(team, "_agents", {}).values())
    hitl_agents = [a for a in all_agents if getattr(a, "approval_required", False)]

    if not hitl_agents:
        for agent in all_agents:
            agent.channel = ch
            agent.approval_required = True
        console.print("[dim]HITL (force): all agents will pause for review.[/dim]\n")
    else:
        for agent in hitl_agents:
            if not getattr(agent, "channel", None):
                agent.channel = ch
        console.print(f"[dim]HITL: {len(hitl_agents)} agent(s) will pause for review.[/dim]\n")

    if hitl_timeout is not None:
        console.print(f"[dim]HITL timeout: {hitl_timeout:.0f}s — pipeline auto-aborts if no response.[/dim]\n")
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="antcrew-hitl")
        future = ex.submit(team.run_interactive, request, thread_id=thread)
        try:
            state_dict: dict = future.result(timeout=hitl_timeout)
        except concurrent.futures.TimeoutError:
            console.print(
                f"\n[yellow bold]HITL timeout ({hitl_timeout:.0f}s)[/] — "
                "no response received. Pipeline aborted."
            )
            ex.shutdown(wait=False, cancel_futures=True)
            raise SystemExit(1)
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
    else:
        state_dict = team.run_interactive(request, thread_id=thread)

    return RunResult(
        state=state_dict,
        thread_id=thread,
        cost_usd=state_dict.get("_cost_usd") or 0.0,
    )


# ---------------------------------------------------------------------------
# SlackNotifyChannel — posts HITL notification to Slack, resolves via terminal
# ---------------------------------------------------------------------------


class SlackNotifyChannel:
    """HITL channel that pings a Slack webhook then falls through to ConsoleChannel.

    On ``send_for_review``:
      1. POSTs a Slack message with the agent name and artifact summary.
      2. Falls through to ConsoleChannel so the reviewer resolves in the terminal.

    This is intentionally simple: Slack is used for notification, not interactive
    resolution. Use PlatformChannel (antcrew-platform) when you want Slack-button
    approval workflows.
    """

    def __init__(self, webhook_url: str, console_ch=None) -> None:
        self._webhook_url = webhook_url
        try:
            from antcrew.integrations.console import ConsoleChannel as _CC
            self._console = console_ch or _CC()
        except ImportError:
            self._console = console_ch

    async def notify(self, message: str, **kwargs) -> None:
        self._post({"text": message})
        if self._console is not None:
            await self._console.notify(message, **kwargs)

    async def send_for_review(
        self,
        artifact,
        agent_name: str,
        session_id: str,
        response_options=None,
    ) -> dict:
        text = f":eyes: *HITL review required* — agent: `{agent_name}`\nResolve in your terminal."
        self._post({"text": text})
        if self._console is None:
            return {"decision": "approve"}
        return await self._console.send_for_review(artifact, agent_name, session_id, response_options)

    def _post(self, payload: dict) -> None:
        import json as _json
        import urllib.request as _req
        try:
            data = _json.dumps(payload).encode()
            request = _req.Request(
                self._webhook_url, data=data,
                headers={"Content-Type": "application/json"},
            )
            _req.urlopen(request, timeout=5)  # nosec B310
        except Exception:
            pass  # Slack notification failure is never fatal


# ---------------------------------------------------------------------------
# Platform push helper
# ---------------------------------------------------------------------------


def _push_run_to_platform(
    platform_url: str,
    api_key: Optional[str],
    state: Any,
    *,
    team: str,
    request: str,
    thread: str,
    llm: Any,
    duration_s: Optional[float] = None,
) -> None:
    """POST the completed run to a running antcrew platform instance.

    Called when `antcrew run --push-to <url>` is set. Failure is non-fatal:
    a warning is printed but the CLI exits 0.
    """
    try:
        import httpx
    except ImportError:
        console.print("[yellow]Warning:[/] --push-to requires httpx. Install with: pip install httpx")
        return

    state_dict = state.state if hasattr(state, "state") else (state if isinstance(state, dict) else {})
    cost = float(getattr(state, "cost_usd", None) or state_dict.get("_cost_usd") or 0.0)

    payload: dict = {
        "team": team,
        "request": request,
        "thread_id": thread,
        "cost_usd": cost,
        "state": state_dict,
    }
    if duration_s is not None:
        payload["duration_s"] = round(duration_s, 3)

    headers: dict = {"Content-Type": "application/json"}
    if api_key:
        headers["X-Api-Key"] = api_key

    def _json_default(obj: object) -> object:
        # LangChain messages and other non-serializable objects → string fallback
        try:
            return obj.__dict__  # type: ignore[union-attr]
        except AttributeError:
            return str(obj)

    url = f"{platform_url.rstrip('/')}/runs/upload"
    try:
        r = httpx.post(
            url,
            content=json.dumps(payload, default=_json_default).encode(),
            headers=headers,
            timeout=20.0,
        )
        r.raise_for_status()
        run_id = r.json().get("run_id", "?")
        console.print(f"[dim]Run pushed to platform → run_id=[cyan]{run_id}[/] ({platform_url})[/dim]")
    except httpx.HTTPStatusError as exc:
        console.print(f"[yellow]Warning:[/] --push-to HTTP {exc.response.status_code}: {exc.response.text[:200]}")
    except Exception as exc:
        console.print(f"[yellow]Warning:[/] --push-to failed: {exc}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

