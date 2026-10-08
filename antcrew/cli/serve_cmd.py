"""antcrew serve — start the antcrew platform locally (SQLite, no auth by default)."""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import app, console

_DEFAULT_DB = Path.home() / ".antcrew" / "platform.db"
_DEFAULT_TRACE_DB = Path.home() / ".antcrew" / "trace.db"
_PID_FILE = Path.home() / ".antcrew" / "platform.pid"


def _serve_evidence(*, host: str, port: int, trace_path: Path, open_browser: bool) -> None:
    """Start a lightweight local HTTP evidence browser without needing antcrew-platform."""
    import http.server
    import json as _json
    import threading
    import urllib.parse
    import webbrowser

    from antcrew.evidence import EvidencePackage
    from antcrew.trace import TraceLog

    trace_path = Path(str(trace_path).replace("~", str(Path.home())))

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # silence default request logging

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path   = parsed.path.rstrip("/")

            if path in ("", "/"):
                self._serve_index()
            elif path.startswith("/evidence/"):
                run_id = path[len("/evidence/"):]
                self._serve_evidence_page(run_id)
            elif path == "/api/runs":
                self._api_runs()
            elif path.startswith("/api/evidence/"):
                run_id = path[len("/api/evidence/"):]
                self._api_evidence(run_id)
            else:
                self.send_error(404)

        def _send_html(self, html: str):
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, data):
            body = _json.dumps(data, default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_index(self):
            if not trace_path.exists():
                self._send_html("<h1>No trace DB found</h1><p>Run antcrew issue or antcrew run first.</p>")
                return
            tlog = TraceLog(str(trace_path))
            runs = tlog.list_runs(limit=50)
            tlog.close()
            def _row(r):
                sc = "#34D399" if r["status"] == "done" else ("#f87171" if r["status"] == "error" else "#FBBF24")
                cost = f"${r.get('cost_usd') or 0:.4f}"
                date = (r.get("started_at") or "")[:16].replace("T", " ")
                rid  = r["id"]
                return (
                    f"<tr onclick=\"location='/evidence/{rid}'\" style='cursor:pointer'>"
                    f"<td style='font-family:monospace;color:#2DD4BF'>{rid[:14]}…</td>"
                    f"<td>{r['team']}</td>"
                    f"<td style='color:{sc}'>{r['status']}</td>"
                    f"<td style='text-align:right'>{cost}</td>"
                    f"<td style='color:#7A9AB5;font-size:12px'>{date}</td>"
                    f"<td style='color:#7A9AB5'>{r['request'][:60]}</td>"
                    f"</tr>"
                )
            rows = "".join(_row(r) for r in runs)
            self._send_html(f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>antcrew evidence browser</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{background:#080F1C;color:#E8EDF5;font-family:system-ui,sans-serif;padding:32px 24px}}
h1{{font-family:Georgia,serif;font-size:20px;margin-bottom:4px}}
p{{color:#7A9AB5;font-size:13px;margin-bottom:24px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{padding:8px 12px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:#4E6A85;border-bottom:1px solid #1E2D42}}
td{{padding:10px 12px;border-bottom:1px solid #0F1929}}tr:hover td{{background:#0F1929}}</style></head>
<body><h1>antcrew evidence browser</h1>
<p>{len(runs)} recent run(s) · {trace_path}</p>
<table><thead><tr><th>Run ID</th><th>Team</th><th>Status</th><th>Cost</th><th>Date</th><th>Request</th></tr></thead>
<tbody>{rows}</tbody></table>
<p style="margin-top:16px;font-size:11px">Click a row to view its evidence package.</p>
</body></html>""")

        def _serve_evidence_page(self, run_id: str):
            if not trace_path.exists():
                self.send_error(404); return
            tlog = TraceLog(str(trace_path))
            run = tlog.get_run(run_id)
            if run is None:
                all_runs = tlog.list_runs(limit=200)
                candidates = [r for r in all_runs if r["id"].startswith(run_id)]
                if not candidates:
                    tlog.close(); self.send_error(404); return
                run_id = candidates[0]["id"]
            pkg = EvidencePackage.from_trace(tlog, run_id)
            tlog.close()
            self._send_html(pkg.to_html())

        def _api_runs(self):
            if not trace_path.exists():
                self._send_json([]); return
            tlog = TraceLog(str(trace_path))
            runs = tlog.list_runs(limit=50)
            tlog.close()
            self._send_json(runs)

        def _api_evidence(self, run_id: str):
            if not trace_path.exists():
                self.send_error(404); return
            tlog = TraceLog(str(trace_path))
            pkg = EvidencePackage.from_trace(tlog, run_id)
            tlog.close()
            self._send_json(pkg.to_dict())

    url = f"http://{host}:{port}"
    server = http.server.HTTPServer((host, port), _Handler)
    console.print(f"\n[bold green]antcrew evidence browser[/bold green]  {url}\n")
    console.print(f"[dim]Trace DB: {trace_path}[/dim]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

    if open_browser:
        def _open():
            import time
            import urllib.request as _ur
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                try:
                    _ur.urlopen(f"{url}/", timeout=0.5)  # nosec B310
                    break
                except Exception:
                    time.sleep(0.2)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]Evidence browser stopped.[/dim]")


@app.command()
def serve(
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on."),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind."),
    db: Optional[Path] = typer.Option(
        None, "--db",
        help=(
            "SQLite database file (default: ~/.antcrew/platform.db). "
            "Override with DATABASE_URL env var for PostgreSQL."
        ),
    ),
    api_key: Optional[str] = typer.Option(
        None, "--api-key",
        help="Require this key on all API requests (sets PLATFORM_API_KEY). "
             "Also readable from ANTCREW_API_KEY env var.",
        envvar="ANTCREW_API_KEY",
        show_default=False,
    ),
    reload: bool = typer.Option(
        False, "--reload",
        help="Auto-reload on code changes (development only).",
    ),
    workers: int = typer.Option(
        1, "--workers", "-w",
        help="Number of uvicorn worker processes. >1 requires HITL_DB_POLLING=1.",
    ),
    open_browser: bool = typer.Option(
        False, "--open", "-o",
        help="Open the dashboard in the default browser after startup.",
    ),
    local: bool = typer.Option(
        False, "--local",
        help="Local dev mode: SQLite, no auth, open browser automatically. "
             "Equivalent to --open with localhost defaults.",
    ),
    background: bool = typer.Option(
        False, "--background", "-b",
        help="Run as a background daemon. PID is written to ~/.antcrew/platform.pid. "
             "Stop with: antcrew serve --stop",
    ),
    stop: bool = typer.Option(
        False, "--stop",
        help="Stop a background antcrew platform server started with --background.",
    ),
    evidence: bool = typer.Option(
        False, "--evidence",
        help="Start a lightweight local evidence browser (reads ~/.antcrew/trace.db). "
             "Does not require antcrew-platform to be installed.",
    ),
    trace: Optional[Path] = typer.Option(
        None, "--trace",
        help="TraceLog DB for --evidence mode (default: ~/.antcrew/trace.db).",
    ),
) -> None:
    """Start antcrew platform locally (SQLite, no auth by default).

    \b
    Quick start:
        antcrew serve

    \b
    Custom port and DB:
        antcrew serve --port 9000 --db ./myproject.db

    \b
    With API key authentication:
        antcrew serve --api-key mysecret
        ANTCREW_API_KEY=mysecret antcrew serve

    \b
    PostgreSQL + multi-worker:
        DATABASE_URL=postgresql+asyncpg://user:pass@localhost/antcrew \\
        HITL_DB_POLLING=1 antcrew serve --workers 4

    \b
    antcrew-platform must be importable. Run from the antcrew-platform directory
    or install it: pip install antcrew-platform
    """
    # --evidence: lightweight local evidence browser (no platform required)
    if evidence:
        _serve_evidence(
            host=host,
            port=port,
            trace_path=trace or _DEFAULT_TRACE_DB,
            open_browser=open_browser or local,
        )
        return

    if local:
        open_browser = True
        if host not in ("127.0.0.1", "localhost"):
            host = "127.0.0.1"
        console.print("[dim]local mode — SQLite, no auth, browser will open[/dim]")

    # --stop: terminate a background server by PID
    if stop:
        if not _PID_FILE.exists():
            console.print("[yellow]No background server found[/] (no PID file at ~/.antcrew/platform.pid)")
            raise typer.Exit(1)
        try:
            pid = int(_PID_FILE.read_text().strip())
            if sys.platform == "win32":
                import subprocess
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], check=True, capture_output=True)
            else:
                os.kill(pid, signal.SIGTERM)
            _PID_FILE.unlink(missing_ok=True)
            console.print(f"[green]Stopped[/] antcrew platform server (PID {pid})")
        except (ValueError, ProcessLookupError):
            console.print("[yellow]Server process not found[/] — removing stale PID file")
            _PID_FILE.unlink(missing_ok=True)
            raise typer.Exit(1)
        except Exception as exc:
            console.print(f"[red]Failed to stop server:[/] {exc}")
            raise typer.Exit(1)
        raise typer.Exit(0)

    # Set DATABASE_URL before importing the platform (it reads at import time).
    if "DATABASE_URL" not in os.environ:
        db_path = Path(db or _DEFAULT_DB).expanduser().resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
        console.print(f"[dim]DB: {db_path}[/dim]")

    # Apply API key if provided.
    if api_key:
        os.environ["PLATFORM_API_KEY"] = api_key
        console.print("auth enabled")
    elif host not in ("127.0.0.1", "localhost") and "PLATFORM_API_KEY" not in os.environ:
        console.print(
            "[yellow]Warning:[/yellow] running on a public host without auth. "
            "Set --api-key or ANTCREW_API_KEY to protect the API."
        )

    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]uvicorn not installed.[/red]\n"
            "Install with: [bold]pip install uvicorn[standard][/bold]"
        )
        raise typer.Exit(1)

    if workers > 1 and os.environ.get("HITL_DB_POLLING", "0") != "1":
        console.print(
            "[yellow]Warning:[/yellow] --workers > 1 requires "
            "[bold]HITL_DB_POLLING=1[/bold] for HITL reviews to work across workers."
        )

    url = f"http://{host}:{port}"

    # --background: spawn a detached uvicorn subprocess and exit
    if background:
        # Anti-duplicate: refuse if an existing background server is still alive.
        if _PID_FILE.exists():
            try:
                existing_pid = int(_PID_FILE.read_text().strip())
                alive = False
                if sys.platform == "win32":
                    import subprocess as _sp
                    r = _sp.run(
                        ["tasklist", "/FI", f"PID eq {existing_pid}", "/NH"],
                        capture_output=True, text=True,
                    )
                    alive = str(existing_pid) in r.stdout
                else:
                    os.kill(existing_pid, 0)
                    alive = True
            except (ValueError, ProcessLookupError, OSError):
                alive = False

            if alive:
                console.print(
                    f"[yellow]Already running[/yellow] — antcrew platform is alive at PID {existing_pid}.\n"
                    f"Stop it first with: [bold]antcrew serve --stop[/bold]"
                )
                raise typer.Exit(1)
            else:
                _PID_FILE.unlink(missing_ok=True)

        import subprocess
        env = dict(os.environ)
        cmd = [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", host, "--port", str(port),
        ]
        if reload:
            cmd.append("--reload")
        if workers > 1 and not reload:
            cmd += ["--workers", str(workers)]

        if sys.platform == "win32":
            proc = subprocess.Popen(
                cmd, env=env,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        else:
            proc = subprocess.Popen(
                cmd, env=env,
                start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True,
            )

        _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PID_FILE.write_text(str(proc.pid))
        console.print(
            f"\n[bold green]antcrew platform[/bold green]  {url}  "
            f"[dim](PID {proc.pid}, background)[/dim]\n"
            f"Stop with: [bold]antcrew serve --stop[/bold]"
        )
        raise typer.Exit(0)

    console.print(f"\n[bold green]antcrew platform[/bold green]  {url}\n")

    if open_browser:
        import threading
        import webbrowser

        def _open() -> None:
            import time
            import urllib.request
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                try:
                    urllib.request.urlopen(f"{url}/health", timeout=1)  # nosec B310
                    break
                except Exception:
                    time.sleep(0.4)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()

    # Use string form — uvicorn handles the import, no early platform check needed.
    # If app.main is not importable, uvicorn will report a clear import error.
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        workers=workers if not reload else 1,
    )
