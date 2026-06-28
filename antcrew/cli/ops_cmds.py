"""Operations commands: benchmark, watch, export, diff, test."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.panel import Panel
from rich.table import Table

from antcrew.cli._app import app, console
from antcrew.cli._shared import _print_test_results

@app.command(name="benchmark")
def benchmark_cmd(
    cases_file: Path = typer.Argument(
        ...,
        help=(
            "JSON file with a list of benchmark cases.  Each entry: "
            '{"request": "...", "team": "dev", "label": "optional"}.'
        ),
    ),
    model_name: str = typer.Option("claude-haiku-4-5-20251001", "--model", "-m", help="Model ID."),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write results JSON to this file."
    ),
    parallel: int = typer.Option(1, "--parallel", "-p", help="Number of cases to run in parallel."),
    timeout: float = typer.Option(300.0, "--timeout", help="Per-case timeout in seconds (0 = no limit)."),
    context: Optional[Path] = typer.Option(
        None, "--context", "-c",
        help="Pre-computed scan JSON (antcrew scan --output) injected for fullstack cases.",
    ),
) -> None:
    """Run a batch of pipeline requests and compare metrics.

    \b
    benchmark.json:
    [
      {"request": "Build a login module",   "team": "dev",     "label": "Login"},
      {"request": "Write about AI safety",  "team": "research","label": "Research"}
    ]

    \b
    antcrew benchmark benchmark.json
    antcrew benchmark benchmark.json --parallel 3 --output results.json
    """
    import concurrent.futures
    import time as _time
    from antcrew.config import build_llm

    if not cases_file.exists():
        console.print(f"[red]File not found:[/] {cases_file}")
        raise typer.Exit(1)

    try:
        raw = json.loads(cases_file.read_text(encoding="utf-8"))
    except Exception as exc:
        console.print(f"[red]Failed to parse cases file:[/] {exc}")
        raise typer.Exit(1)

    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        console.print("[red]Cases file must contain a JSON array of case objects.[/]")
        raise typer.Exit(1)

    _scan_ctx: "dict | None" = None
    if context is not None:
        if not context.exists():
            console.print(f"[red]--context file not found:[/] {context}")
            raise typer.Exit(1)
        try:
            _scan_ctx = json.loads(context.read_text(encoding="utf-8"))
        except Exception as exc:
            console.print(f"[red]Failed to parse --context file:[/] {exc}")
            raise typer.Exit(1)

    def _run_case(idx: int, case: dict) -> dict:
        label = case.get("label") or f"case-{idx + 1}"
        req   = case.get("request", "")
        t     = (case.get("team") or "dev").lower()

        if not req:
            return {"label": label, "status": "skipped", "reason": "empty request",
                    "cost_usd": 0.0, "elapsed_s": 0.0, "artifacts": {}}

        llm = build_llm(case.get("model") or model_name)

        def _mk_team():
            if t == "dev":
                from antcrew.teams.dev_team import DevTeam
                return DevTeam(model=llm)
            if t in ("fullstack", "full"):
                from antcrew.teams.fullstack_team import FullStackTeam
                return FullStackTeam(model=llm, scan_context=_scan_ctx)
            if t == "research":
                from antcrew.teams.research_team import ResearchTeam
                return ResearchTeam(model=llm)
            if t == "content":
                from antcrew.teams.content_team import ContentTeam
                return ContentTeam(model=llm)
            raise ValueError(f"Unknown team: {t!r}")

        start = _time.monotonic()
        try:
            team_inst = _mk_team()
            result = team_inst.run(req, thread_id=f"bench-{idx}")
            elapsed = _time.monotonic() - start
            state = result.state
            artifacts: dict[str, int] = {}
            for key in ("tickets", "code_artifacts", "test_artifacts",
                        "devops_artifacts", "doc_artifacts"):
                v = state.get(key)
                if isinstance(v, list):
                    artifacts[key] = len(v)
            if state.get("prd"):
                artifacts["prd"] = 1
            if state.get("research_document"):
                artifacts["research_document"] = 1
            if state.get("content_piece"):
                artifacts["content_piece"] = 1
            return {
                "label": label,
                "request": req,
                "team": t,
                "status": "ok",
                "cost_usd": result.cost_usd,
                "elapsed_s": round(elapsed, 2),
                "artifacts": artifacts,
            }
        except Exception as exc:
            elapsed = _time.monotonic() - start
            return {
                "label": label,
                "request": req,
                "team": t,
                "status": "error",
                "reason": str(exc),
                "cost_usd": 0.0,
                "elapsed_s": round(elapsed, 2),
                "artifacts": {},
            }

    console.print(
        f"\n[bold green]antcrew benchmark[/]  "
        f"{len(raw)} case(s)  parallel={parallel}  model={model_name}\n"
    )

    results: list[dict] = [{}] * len(raw)
    with console.status("Running benchmark…") as status:
        if parallel <= 1:
            for i, case in enumerate(raw):
                status.update(f"Running {i + 1}/{len(raw)}…")
                results[i] = _run_case(i, case)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
                futs = {pool.submit(_run_case, i, c): i for i, c in enumerate(raw)}
                for fut in concurrent.futures.as_completed(futs):
                    idx = futs[fut]
                    results[idx] = fut.result()

    # ── Results table ─────────────────────────────────────────────────────────

    tbl = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 1))
    tbl.add_column("#",         style="dim",   width=3)
    tbl.add_column("Label",     min_width=14)
    tbl.add_column("Team",      width=9)
    tbl.add_column("Status",    width=7)
    tbl.add_column("Elapsed",   width=8, justify="right")
    tbl.add_column("Cost USD",  width=9, justify="right")
    tbl.add_column("Artifacts", min_width=18)

    total_cost = 0.0
    total_time = 0.0
    ok_count   = 0

    for i, r in enumerate(results, 1):
        status_str = r.get("status", "?")
        status_col = (
            "[green]ok[/green]"    if status_str == "ok"      else
            "[yellow]skip[/yellow]" if status_str == "skipped" else
            "[red]error[/red]"
        )
        arts = r.get("artifacts") or {}
        arts_str = "  ".join(f"{k}:{v}" for k, v in arts.items()) or "[dim]—[/dim]"
        cost = r.get("cost_usd") or 0.0
        elapsed = r.get("elapsed_s") or 0.0
        total_cost += cost
        total_time += elapsed
        if status_str == "ok":
            ok_count += 1

        tbl.add_row(
            str(i),
            r.get("label", ""),
            r.get("team", ""),
            status_col,
            f"{elapsed:.1f}s",
            f"${cost:.4f}",
            arts_str,
        )

    console.print(tbl)
    console.print(
        f"\n[dim]Total: {ok_count}/{len(raw)} ok  "
        f"time={total_time:.1f}s  cost=${total_cost:.4f}[/dim]\n"
    )

    if output:
        output.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        console.print(f"[dim]Results written → [cyan]{output}[/][/dim]\n")


@app.command(name="watch")
def watch_cmd(
    watch_path: Path = typer.Argument(..., help="File or directory to watch for changes."),
    request: Optional[str] = typer.Option(
        None, "--request", "-r",
        help="Request to run on each change. Defaults to the file's content.",
    ),
    team: str = typer.Option("dev", "--team", "-t", help="Team to run: dev, fullstack, research, content."),
    model_name: str = typer.Option("claude-haiku-4-5-20251001", "--model", "-m", help="Model ID."),
    output: Path = typer.Option(
        Path("antcrew-watch-latest.json"), "--output", "-o",
        help="Where to write each run's state (overwritten on each change).",
    ),
    project_dir: Optional[str] = typer.Option(
        None, "--project-dir",
        help="Existing project directory for CodebaseScannerAgent context (fullstack team).",
    ),
    context: Optional[Path] = typer.Option(
        None, "--context",
        help="Pre-computed scan JSON (antcrew scan --output) injected for fullstack team.",
    ),
    repo_index: Optional[Path] = typer.Option(
        None, "--repo-index",
        help="Build a RepoIndex from this directory and attach it to the fullstack team agents.",
    ),
    write_back: Optional[Path] = typer.Option(
        None, "--write-back", "-W",
        help="After each run, write artifacts to this directory (respects file_path).",
    ),
    diff: bool = typer.Option(True, "--diff/--no-diff", help="Show artifact diff after each re-run."),
    debounce: float = typer.Option(2.0, "--debounce", help="Seconds to wait after a change before re-running."),
) -> None:
    """Re-run the pipeline whenever a watched file changes.

    \b
    Watch a spec file and re-run DevTeam on every save:
        antcrew watch spec.md --request "implement this spec"

    \b
    Watch a directory (any file change triggers a re-run):
        antcrew watch src/ --team fullstack

    \b
    Requires:  pip install antcrew[watch]
    """
    watch_path = watch_path.resolve()
    if not watch_path.exists():
        console.print(f"[red]Path not found:[/] {watch_path}")
        raise typer.Exit(1)

    if project_dir and team not in ("fullstack", "full"):
        console.print(
            f"[yellow]Warning:[/] --project-dir is only used by the "
            f"[bold]fullstack[/] team (CodebaseScannerAgent). "
            f"Current team [bold]{team}[/] will ignore it."
        )

    _watch_ctx: "dict | None" = None
    if context is not None:
        if not context.exists():
            console.print(f"[red]--context file not found:[/] {context}")
            raise typer.Exit(1)
        if team not in ("fullstack", "full"):
            console.print(
                f"[yellow]Warning:[/] --context is only used by the "
                f"[bold]fullstack[/] team. Current team [bold]{team}[/] will ignore it."
            )
        else:
            try:
                _watch_ctx = json.loads(context.read_text(encoding="utf-8"))
            except Exception as exc:
                console.print(f"[red]Failed to parse --context file:[/] {exc}")
                raise typer.Exit(1)

    _watch_repo_path: "str | None" = None
    if repo_index is not None:
        if not repo_index.is_dir():
            console.print(f"[red]--repo-index is not a directory:[/] {repo_index}")
            raise typer.Exit(1)
        if team not in ("fullstack", "full"):
            console.print(
                f"[yellow]Warning:[/] --repo-index is only used by the "
                f"[bold]fullstack[/] team. Current team [bold]{team}[/] will ignore it."
            )
        else:
            _watch_repo_path = str(repo_index.resolve())

    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        console.print(
            "[red]watchdog is not installed.[/] "
            "Run: [cyan]pip install antcrew\\[watch][/]"
        )
        raise typer.Exit(1)

    import threading
    import time as _time
    import difflib
    from antcrew.utils.persistence import save_state as _save
    from antcrew.config import build_llm

    llm = build_llm(model_name)

    def _make_team():
        t = team.lower()
        if t == "dev":
            from antcrew.teams.dev_team import DevTeam
            return DevTeam(model=llm)
        if t in ("fullstack", "full"):
            from antcrew.teams.fullstack_team import FullStackTeam
            return FullStackTeam(
                model=llm, project_dir=project_dir,
                scan_context=_watch_ctx, repo_path=_watch_repo_path,
            )
        if t == "research":
            from antcrew.teams.research_team import ResearchTeam
            return ResearchTeam(model=llm)
        if t == "content":
            from antcrew.teams.content_team import ContentTeam
            return ContentTeam(model=llm)
        console.print(f"[red]Unknown team:[/] {team}")
        raise typer.Exit(1)

    def _read_request() -> str:
        if request:
            return request
        if watch_path.is_file():
            try:
                return watch_path.read_text(encoding="utf-8", errors="replace")[:4000].strip()
            except Exception:
                pass
        return f"Process changes in {watch_path.name}"

    def _run_once(run_n: int) -> Optional[dict]:
        req = _read_request()
        console.print(
            f"\n[bold green]antcrew watch[/]  run #{run_n}  "
            f"[dim]{watch_path.name}[/dim]\n"
        )
        try:
            team_inst = _make_team()
            with console.status(f"Running {team}…"):
                result = team_inst.run(req, thread_id=f"watch-{run_n}")
            state = dict(result.state)
            _save(state, output)
            console.print(f"[green]Done.[/]  cost={result.cost_usd:.4f}  → [cyan]{output}[/]\n")
            if write_back is not None:
                from antcrew.core.writeback import write_back as _wb
                _wb(result, write_back.resolve(), dry_run=False, yes=True,
                    print_fn=console.print)
            return state
        except Exception as exc:
            console.print(f"[red]Run failed:[/] {exc}\n")
            return None

    def _show_diff(prev: dict, curr: dict) -> None:
        # Code artifact diff
        def _fmap(state):
            arts = state.get("code_artifacts") or []
            return {
                a["file_path"]: a.get("content", "")
                for a in arts if isinstance(a, dict) and a.get("file_path")
            }
        fa, fb = _fmap(prev), _fmap(curr)
        if fa == fb:
            console.print("[dim]Code artifacts unchanged.[/dim]\n")
            return
        all_files = sorted(set(fa) | set(fb))
        for f in all_files:
            if f not in fa:
                console.print(f"  [green]+[/] {f}  [dim]\\[new][/dim]")
            elif f not in fb:
                console.print(f"  [red]−[/] {f}  [dim]\\[removed][/dim]")
            elif fa[f] != fb[f]:
                lines = list(difflib.unified_diff(
                    fa[f].splitlines(keepends=True),
                    fb[f].splitlines(keepends=True),
                    fromfile=f"prev/{f}", tofile=f"curr/{f}", n=2,
                ))
                console.print(f"  [yellow]~[/] {f}")
                for line in lines[2:]:  # skip --- +++
                    line = line.rstrip("\n")
                    if line.startswith("+"):
                        console.print(f"    [green]{line}[/green]")
                    elif line.startswith("-"):
                        console.print(f"    [red]{line}[/red]")
        console.print()

    # ── debounce + run state ─────────────────────────────────────────────────
    _lock = threading.Lock()
    _pending: list[bool] = [False]
    _run_n: list[int] = [0]
    _prev_state: list[Optional[dict]] = [None]

    def _schedule_run():
        with _lock:
            _pending[0] = True

    class _Handler(FileSystemEventHandler):
        def on_modified(self, event):
            if not event.is_directory:
                _schedule_run()

        def on_created(self, event):
            if not event.is_directory:
                _schedule_run()

    watch_dir = str(watch_path if watch_path.is_dir() else watch_path.parent)
    observer = Observer()
    observer.schedule(_Handler(), path=watch_dir, recursive=watch_path.is_dir())
    observer.start()

    console.print(
        f"[bold green]antcrew watch[/]  watching [cyan]{watch_path}[/]  "
        f"team=[cyan]{team}[/]  debounce={debounce}s\n"
        "Press [bold]Ctrl+C[/bold] to stop.\n"
    )

    # Immediate first run
    _run_n[0] += 1
    _prev_state[0] = _run_once(_run_n[0])

    try:
        while True:
            _time.sleep(0.5)
            with _lock:
                if not _pending[0]:
                    continue
                _pending[0] = False

            _time.sleep(debounce)
            _run_n[0] += 1
            curr = _run_once(_run_n[0])
            if curr is not None and diff and _prev_state[0] is not None:
                _show_diff(_prev_state[0], curr)
            _prev_state[0] = curr
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]\n")
    finally:
        observer.stop()
        observer.join()


@app.command(name="export")
def export_cmd(
    path: Path = typer.Argument(..., help="JSON state file to export."),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Destination zip path (default: <stem>.zip next to the source file).",
    ),
    include_tests: bool = typer.Option(True, "--tests/--no-tests", help="Include test artifacts."),
    include_devops: bool = typer.Option(True, "--devops/--no-devops", help="Include devops artifacts."),
    include_docs: bool = typer.Option(True, "--docs/--no-docs", help="Include doc artifacts."),
    include_state: bool = typer.Option(True, "--state/--no-state", help="Include the raw state JSON."),
) -> None:
    """Export a saved run to a zip archive containing all generated files.

    \b
    antcrew export run_output.json
    antcrew export run_output.json --output my_project.zip --no-tests
    """
    import zipfile
    from antcrew.utils.persistence import load_state as _load_state

    if not path.exists():
        console.print(f"[red]File not found:[/] {path}")
        raise typer.Exit(1)

    raw = _load_state(path)
    # Support project files that nest state under "state"
    state = raw.get("state", raw) if isinstance(raw.get("state"), dict) else raw

    zip_path = output or path.with_suffix(".zip")

    def _artifacts(key: str) -> list[dict]:
        items = state.get(key) or []
        return [a for a in items if isinstance(a, dict) and a.get("file_path")]

    entries: list[tuple[str, str]] = []  # (arcname, content)

    for art in _artifacts("code_artifacts"):
        entries.append((f"src/{art['file_path']}", art.get("content") or ""))

    if include_tests:
        for art in _artifacts("test_artifacts"):
            entries.append((f"tests/{art['file_path']}", art.get("content") or ""))

    if include_devops:
        for art in _artifacts("devops_artifacts"):
            entries.append((f"devops/{art['file_path']}", art.get("content") or ""))

    if include_docs:
        for art in _artifacts("doc_artifacts"):
            entries.append((f"docs/{art['file_path']}", art.get("content") or ""))

    # Research document → docs/research.md
    if include_docs and state.get("research_document"):
        rd = state["research_document"]
        if isinstance(rd, dict):
            body = rd.get("body") or rd.get("summary") or ""
            title = rd.get("title") or "research"
            entries.append((f"docs/{title}.md", body))

    if include_state:
        entries.append(("state.json", json.dumps(raw, indent=2, default=str)))

    if not entries:
        console.print("[yellow]Nothing to export[/] — no artifacts found in this run.")
        raise typer.Exit(0)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arcname, content in entries:
            zf.writestr(arcname, content)

    size_kb = zip_path.stat().st_size / 1024
    console.print(
        f"[green]Exported[/] {len(entries)} files → [cyan]{zip_path}[/]  "
        f"[dim]({size_kb:.1f} KB)[/dim]"
    )
    for arcname, _ in entries:
        console.print(f"  [dim]{arcname}[/dim]")


@app.command(name="diff")
def diff_cmd(
    run_a: Path = typer.Argument(..., help="First state JSON file (baseline)."),
    run_b: Path = typer.Argument(..., help="Second state JSON file (comparison)."),
    files: bool = typer.Option(True, "--files/--no-files", help="Show per-file content diffs."),
    context: int = typer.Option(3, "--context", "-c", help="Unified diff context lines."),
) -> None:
    """Compare two saved pipeline runs side-by-side.

    \b
    antcrew diff run_v1.json run_v2.json
    antcrew diff run_v1.json run_v2.json --no-files   # metadata only
    """
    import difflib
    from antcrew.utils.persistence import load_state as _load_state
    from rich.rule import Rule

    for p in (run_a, run_b):
        if not p.exists():
            console.print(f"[red]File not found:[/] {p}")
            raise typer.Exit(1)

    a = _load_state(run_a)
    b = _load_state(run_b)

    def _val(state: dict, key: str):
        v = state.get(key)
        if isinstance(v, dict) and v:
            return v
        return v

    console.print(f"\n[bold green]antcrew diff[/]  [cyan]{run_a.name}[/]  →  [cyan]{run_b.name}[/]\n")

    # ── request ──────────────────────────────────────────────────────────────
    req_a = a.get("request") or ""
    req_b = b.get("request") or ""
    if req_a != req_b:
        console.print(Rule("[bold]request[/]"))
        console.print(f"  [red]A:[/] {req_a}")
        console.print(f"  [green]B:[/] {req_b}")
        console.print()

    # ── PRD ──────────────────────────────────────────────────────────────────
    prd_a = a.get("prd") or {}
    prd_b = b.get("prd") or {}
    if isinstance(prd_a, dict) and isinstance(prd_b, dict) and (prd_a or prd_b):
        prd_changed = any(prd_a.get(k) != prd_b.get(k) for k in ("title", "summary"))
        if prd_changed:
            console.print(Rule("[bold]PRD[/]"))
            for field in ("title", "summary"):
                va, vb = prd_a.get(field, ""), prd_b.get(field, "")
                if va != vb:
                    console.print(f"  [dim]{field}:[/]")
                    console.print(f"    [red]A:[/] {va}")
                    console.print(f"    [green]B:[/] {vb}")
            console.print()

    # ── Tickets ───────────────────────────────────────────────────────────────
    tix_a: list[dict] = a.get("tickets") or []
    tix_b: list[dict] = b.get("tickets") or []
    if isinstance(tix_a, list) and isinstance(tix_b, list):
        ids_a = {t.get("id") if isinstance(t, dict) else None for t in tix_a}
        ids_b = {t.get("id") if isinstance(t, dict) else None for t in tix_b}
        added   = ids_b - ids_a
        removed = ids_a - ids_b
        if added or removed or len(tix_a) != len(tix_b):
            delta_str = ""
            if added:
                delta_str += f"  [green]+{len(added)} added[/]"
            if removed:
                delta_str += f"  [red]-{len(removed)} removed[/]"
            console.print(Rule(f"[bold]Tickets[/]  A:{len(tix_a)}  B:{len(tix_b)}{delta_str}"))
            for t in tix_b:
                if isinstance(t, dict) and t.get("id") in added:
                    console.print(f"  [green]+[/] {t.get('id','?')}  {t.get('title','')}")
            for t in tix_a:
                if isinstance(t, dict) and t.get("id") in removed:
                    console.print(f"  [red]−[/] {t.get('id','?')}  {t.get('title','')}")
            console.print()

    # ── Code artifacts ────────────────────────────────────────────────────────
    def _artifact_map(lst) -> dict[str, str]:
        if not lst:
            return {}
        out = {}
        for a in lst:
            fp = a.get("file_path") if isinstance(a, dict) else None
            ct = a.get("content") if isinstance(a, dict) else None
            if fp:
                out[fp] = ct or ""
        return out

    ca_a = _artifact_map(a.get("code_artifacts"))
    ca_b = _artifact_map(b.get("code_artifacts"))
    if ca_a or ca_b:
        all_files = sorted(set(ca_a) | set(ca_b))
        added_f   = [f for f in all_files if f not in ca_a]
        removed_f = [f for f in all_files if f not in ca_b]
        changed_f = [f for f in all_files if f in ca_a and f in ca_b and ca_a[f] != ca_b[f]]
        same_f    = [f for f in all_files if f in ca_a and f in ca_b and ca_a[f] == ca_b[f]]

        parts = []
        if added_f:
            parts.append(f"[green]+{len(added_f)} added[/]")
        if removed_f:
            parts.append(f"[red]-{len(removed_f)} removed[/]")
        if changed_f:
            parts.append(f"[yellow]~{len(changed_f)} changed[/]")

        summary = "  " + "  ".join(parts) if parts else ""
        console.print(Rule(f"[bold]Code files[/]  A:{len(ca_a)}  B:{len(ca_b)}{summary}"))

        for f in sorted(added_f):
            console.print(f"  [green]+[/] {f}  [dim]\\[new][/dim]")
        for f in sorted(removed_f):
            console.print(f"  [red]−[/] {f}  [dim]\\[removed][/dim]")
        for f in sorted(changed_f):
            console.print(f"  [yellow]~[/] {f}  [dim]\\[modified][/dim]")
        for f in same_f:
            console.print(f"  [dim]=[/dim] [dim]{f}  \\[unchanged][/dim]")

        if files and changed_f:
            console.print()
            for f in sorted(changed_f):
                lines_a = (ca_a[f] or "").splitlines(keepends=True)
                lines_b = (ca_b[f] or "").splitlines(keepends=True)
                diff_lines = list(difflib.unified_diff(
                    lines_a, lines_b,
                    fromfile=f"A/{f}", tofile=f"B/{f}",
                    n=context,
                ))
                if diff_lines:
                    console.print(f"\n  [bold]{f}[/]")
                    for line in diff_lines:
                        line = line.rstrip("\n")
                        if line.startswith("+++") or line.startswith("---"):
                            console.print(f"  [dim]{line}[/dim]")
                        elif line.startswith("+"):
                            console.print(f"  [green]{line}[/green]")
                        elif line.startswith("-"):
                            console.print(f"  [red]{line}[/red]")
                        elif line.startswith("@@"):
                            console.print(f"  [cyan]{line}[/cyan]")
                        else:
                            console.print(f"  {line}")

        console.print()

    # ── Summary ───────────────────────────────────────────────────────────────
    any_diff = (
        req_a != req_b
        or prd_a != prd_b
        or tix_a != tix_b
        or ca_a != ca_b
    )
    if not any_diff:
        console.print("[dim]No differences found.[/dim]\n")
    else:
        console.print("[dim]─── end of diff ───[/dim]\n")


# ---------------------------------------------------------------------------
# antcrew test — run test artifacts from a saved pipeline state
# ---------------------------------------------------------------------------

@app.command(name="test")
def test_cmd(
    state_file: Path = typer.Argument(
        ...,
        help="JSON state file produced by 'antcrew run --save' or 'antcrew interactive'.",
    ),
    runner: str = typer.Option(
        "local", "--runner", "-r",
        help="Execution backend: local (same Python, temp dir) | docker (isolated container).",
    ),
    timeout: int = typer.Option(
        60, "--timeout", help="Seconds before killing pytest (default: 60)."
    ),
    keep: Optional[Path] = typer.Option(
        None, "--keep", "-k",
        help="Also write test + code files to this directory and keep them after the run.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Always print full pytest output (default: only on failure).",
    ),
    no_code: bool = typer.Option(
        False, "--no-code",
        help="Skip code artifacts — run test files only (useful when the project already exists).",
    ),
) -> None:
    """Run test artifacts from a saved pipeline state.

    \b
    Basic usage:
        antcrew test antcrew-output.json

    \b
    Isolated Docker run with longer timeout:
        antcrew test antcrew-output.json --runner docker --timeout 120

    \b
    Keep generated files on disk for inspection:
        antcrew test antcrew-output.json --keep ./test-output

    \b
    Run only the test files (project already checked out separately):
        antcrew test antcrew-output.json --no-code

    Exit code is 0 when all tests pass, 1 otherwise.
    """
    from antcrew.core.artifacts import CodeArtifact, TestArtifact
    from antcrew.sandbox import LocalRunner, DockerRunner
    from antcrew.sandbox.runner import _write_artifacts
    from antcrew.utils.persistence import load_state

    if not state_file.exists():
        console.print(f"[red]State file not found:[/] {state_file}")
        raise typer.Exit(1)

    try:
        raw = load_state(state_file)
    except Exception as exc:
        console.print(f"[red]Failed to load state file:[/] {exc}")
        raise typer.Exit(1)

    # Project files nest state under "state"
    if "state" in raw and isinstance(raw.get("state"), dict):
        raw = raw["state"]

    test_arts_raw = raw.get("test_artifacts") or []
    code_arts_raw = [] if no_code else (raw.get("code_artifacts") or [])

    if not test_arts_raw:
        console.print(
            f"[yellow]No test artifacts found in[/] [cyan]{state_file}[/]\n"
            "[dim]Run a pipeline first (DevTeam or FullStackTeam with QA enabled).[/dim]"
        )
        raise typer.Exit(0)

    try:
        test_artifacts = [
            TestArtifact.model_validate(a) if isinstance(a, dict) else a
            for a in test_arts_raw
        ]
        code_artifacts = [
            CodeArtifact.model_validate(a) if isinstance(a, dict) else a
            for a in code_arts_raw
        ]
    except Exception as exc:
        console.print(f"[red]Failed to parse artifacts:[/] {exc}")
        raise typer.Exit(1)

    runner_label = runner.strip().lower()
    console.print(
        f"\n[bold green]antcrew test[/]  [cyan]{state_file}[/]  "
        f"[dim]{len(test_artifacts)} test file(s)"
        + (f"  +{len(code_artifacts)} code file(s)" if code_artifacts else "")
        + f"  runner={runner_label}[/dim]\n"
    )

    # Optionally persist files so the user can inspect them.
    if keep is not None:
        keep.mkdir(parents=True, exist_ok=True)
        _write_artifacts(keep, test_artifacts, code_artifacts)
        console.print(f"[dim]Files written → [cyan]{keep}/[/][/dim]\n")

    # Build runner
    if runner_label == "docker":
        active_runner = DockerRunner(timeout=timeout)
    elif runner_label == "local":
        active_runner = LocalRunner(timeout=timeout)
    else:
        console.print(
            f"[red]Unknown runner:[/] {runner_label!r}  (choose 'local' or 'docker')"
        )
        raise typer.Exit(1)

    with console.status("[bold]Running tests…[/]"):
        result = active_runner.run(test_artifacts, code_artifacts=code_artifacts)

    _print_test_results(result)

    if verbose or not result.success:
        output = result.output or ""
        tail = output[-3000:] if len(output) > 3000 else output
        if tail.strip():
            console.print(Panel(
                tail,
                title="pytest output",
                border_style="dim",
            ))

    if not result.success:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# antcrew graph — visualise Supervisor flow
# ---------------------------------------------------------------------------

