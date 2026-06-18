"""
AntCrew REST API server.

Start with:
    antcrew serve
    antcrew serve --host 0.0.0.0 --port 8080
    ANTCREW_DATA_DIR=./data antcrew serve   # persist runs across restarts

Endpoints:
    POST /run                      Start a pipeline run (async background task)
    GET  /run/{run_id}             Get run status + serialised state when done
    GET  /run/{run_id}/stream      Server-Sent Events: tokens + done/error events
    GET  /run/{run_id}/artifacts   Get code/devops/doc artifacts for a finished run
    GET  /runs                     List all runs (summary)
    DELETE /run/{run_id}           Remove a run from memory

    POST /eval                     Run an eval case (async background task)
    GET  /eval/{eval_id}           Get eval result when done
    GET  /evals                    List all eval reports

Requires: pip install antcrew[server]
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

try:
    from fastapi import BackgroundTasks, FastAPI, HTTPException
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel as _PB
except ImportError as exc:
    raise ImportError(
        "FastAPI is required for the API server.\n"
        "Install it: pip install antcrew[server]"
    ) from exc

from antcrew.utils.persistence import _encode

# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

_DATA_DIR: Optional[Path] = (
    Path(os.environ["ANTCREW_DATA_DIR"]) if os.environ.get("ANTCREW_DATA_DIR") else None
)

# In-memory run registry
_runs: dict[str, dict[str, Any]] = {}
# In-memory eval registry
_evals: dict[str, dict[str, Any]] = {}


def _save_run(run_id: str) -> None:
    """Persist a completed run record to ANTCREW_DATA_DIR."""
    if _DATA_DIR is None:
        return
    rec = _runs.get(run_id)
    if rec is None or rec["status"] == "running":
        return
    runs_dir = _DATA_DIR / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    payload = {k: v for k, v in rec.items() if k != "events"}
    try:
        (runs_dir / f"{run_id}.json").write_text(
            json.dumps(payload, default=str), encoding="utf-8"
        )
    except OSError:
        pass


def _save_eval(eval_id: str) -> None:
    if _DATA_DIR is None:
        return
    rec = _evals.get(eval_id)
    if rec is None or rec["status"] == "running":
        return
    evals_dir = _DATA_DIR / "evals"
    evals_dir.mkdir(parents=True, exist_ok=True)
    try:
        (evals_dir / f"{eval_id}.json").write_text(
            json.dumps(rec, default=str), encoding="utf-8"
        )
    except OSError:
        pass


def _load_persisted() -> None:
    """Load completed runs and evals from disk on server startup."""
    if _DATA_DIR is None:
        return
    for fp in sorted((_DATA_DIR / "runs").glob("*.json")) if (_DATA_DIR / "runs").exists() else []:
        try:
            rec = json.loads(fp.read_text(encoding="utf-8"))
            rec.setdefault("events", [])
            _runs[fp.stem] = rec
        except (json.JSONDecodeError, OSError):
            pass
    for fp in sorted((_DATA_DIR / "evals").glob("*.json")) if (_DATA_DIR / "evals").exists() else []:
        try:
            _evals[fp.stem] = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app: FastAPI):
    _load_persisted()
    yield


app = FastAPI(
    title="AntCrew API",
    version="0.4.0",
    description="Run AntCrew multi-agent pipelines via REST.",
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class RunRequest(_PB):
    request: str
    team: str = "dev"
    model: str = "simulated"
    thread_id: Optional[str] = None


class RunSummary(_PB):
    run_id: str
    status: str
    request: str
    team: str
    error: Optional[str] = None


class EvalRequest(_PB):
    request: str
    name: str = ""
    team: str = "dev"
    model: str = "simulated"
    judge_model: Optional[str] = None
    expect_min_tickets: int = 0
    expect_min_code_files: int = 0
    expect_review_verdict: str = ""


class EvalSummary(_PB):
    eval_id: str
    status: str
    name: str
    request: str


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

def _execute_run(run_id: str, body: RunRequest) -> None:
    rec = _runs[run_id]
    try:
        from antcrew.config import build_llm

        llm = build_llm(body.model)
        thread_id = body.thread_id or run_id

        def _on_token(token: str) -> None:
            rec["events"].append(("token", llm.current_agent, token))

        llm.on_token = _on_token

        if body.team == "dev":
            from antcrew.teams.dev_team import DevTeam
            team = DevTeam(model=llm)
        elif body.team == "fullstack":
            from antcrew.teams.fullstack_team import FullStackTeam
            team = FullStackTeam(model=llm)
        elif body.team == "research":
            from antcrew.teams.research_team import ResearchTeam
            team = ResearchTeam(model=llm)
        elif body.team == "content":
            from antcrew.teams.content_team import ContentTeam
            team = ContentTeam(model=llm)
        else:
            raise ValueError(f"Unknown team '{body.team}'.")

        state = team.run(body.request, thread_id=thread_id)
        encoded = _encode(state)
        usage = llm.get_usage_summary()

        rec["state"] = encoded
        rec["usage"] = usage
        rec["status"] = "done"
        rec["events"].append(("done", None, json.dumps({"state": encoded, "usage": usage})))

    except Exception as exc:
        rec["status"] = "error"
        rec["error"] = str(exc)
        rec["events"].append(("error", None, str(exc)))
    finally:
        _save_run(run_id)


def _execute_eval(eval_id: str, body: EvalRequest) -> None:
    rec = _evals[eval_id]
    try:
        from antcrew.config import build_llm
        from antcrew.eval import EvalCase, EvalRunner

        llm = build_llm(body.model)
        judge_llm = build_llm(body.judge_model) if body.judge_model else None

        if body.team == "dev":
            from antcrew.teams.dev_team import DevTeam
            team = DevTeam(model=llm)
        elif body.team == "fullstack":
            from antcrew.teams.fullstack_team import FullStackTeam
            team = FullStackTeam(model=llm)
        elif body.team == "research":
            from antcrew.teams.research_team import ResearchTeam
            team = ResearchTeam(model=llm)
        elif body.team == "content":
            from antcrew.teams.content_team import ContentTeam
            team = ContentTeam(model=llm)
        else:
            raise ValueError(f"Unknown team '{body.team}'.")

        case = EvalCase(
            request=body.request,
            name=body.name or body.request[:60],
            expect_min_tickets=body.expect_min_tickets,
            expect_min_code_files=body.expect_min_code_files,
            expect_review_verdict=body.expect_review_verdict,
        )
        runner = EvalRunner(team, judge_llm=judge_llm)
        report = runner.run_one(case)

        rec["status"] = "done"
        rec["report"] = report.to_dict()

    except Exception as exc:
        rec["status"] = "error"
        rec["error"] = str(exc)
    finally:
        _save_eval(eval_id)


# ---------------------------------------------------------------------------
# Run endpoints
# ---------------------------------------------------------------------------

@app.post("/run", response_model=RunSummary, status_code=202)
async def create_run(body: RunRequest, background_tasks: BackgroundTasks) -> RunSummary:
    """Start a pipeline run. Returns immediately with a run_id to poll or stream."""
    run_id = str(uuid.uuid4())
    _runs[run_id] = {
        "status":  "running",
        "request": body.request,
        "team":    body.team,
        "state":   None,
        "error":   None,
        "events":  [],
        "usage":   None,
    }
    background_tasks.add_task(_execute_run, run_id, body)
    return RunSummary(run_id=run_id, status="running", request=body.request, team=body.team)


@app.get("/run/{run_id}")
def get_run(run_id: str) -> dict:
    """Get the status and (when done) the serialised state of a run."""
    rec = _runs.get(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    result: dict[str, Any] = {
        "run_id":  run_id,
        "status":  rec["status"],
        "request": rec["request"],
        "team":    rec["team"],
    }
    if rec["state"] is not None:
        result["state"] = rec["state"]
    if rec["error"]:
        result["error"] = rec["error"]
    if rec.get("usage"):
        result["usage"] = rec["usage"]
    return result


@app.get("/run/{run_id}/stream")
async def stream_run(run_id: str) -> StreamingResponse:
    """Server-Sent Events stream for a run (replays all past events safely)."""
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    async def generate():
        idx = 0
        stall = 0.0

        while True:
            events: list = _runs.get(run_id, {}).get("events", [])
            while idx < len(events):
                stall = 0.0
                kind, agent, data = events[idx]
                idx += 1
                if kind == "token":
                    payload = json.dumps({"agent": agent or "", "token": data})
                    yield f"data: {payload}\n\n"
                elif kind == "done":
                    yield f"event: done\ndata: {data}\n\n"
                    return
                elif kind == "error":
                    yield f"event: error\ndata: {json.dumps(data)}\n\n"
                    return

            status = _runs.get(run_id, {}).get("status", "running")
            if status in ("done", "error") and idx >= len(_runs.get(run_id, {}).get("events", [])):
                break

            await asyncio.sleep(0.05)
            stall += 0.05
            if stall >= 1.0:
                yield ": ping\n\n"
                stall = 0.0

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )


@app.get("/run/{run_id}/artifacts")
def get_artifacts(run_id: str) -> dict:
    """Return code, devops, and doc artifacts for a finished run."""
    rec = _runs.get(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    if rec["status"] != "done":
        raise HTTPException(status_code=409, detail=f"Run is '{rec['status']}' — not done yet.")
    state = rec["state"] or {}
    result: dict = {
        "run_id":           run_id,
        "code_artifacts":   state.get("code_artifacts")   or [],
        "devops_artifacts": state.get("devops_artifacts") or [],
        "doc_artifacts":    state.get("doc_artifacts")    or [],
        "test_artifacts":   state.get("test_artifacts")   or [],
    }
    if state.get("test_results") is not None:
        tr = state["test_results"]
        # tr may be a RunResult dataclass (live) or a plain dict (loaded from disk)
        if hasattr(tr, "to_dict"):
            result["test_results"] = tr.to_dict()
        else:
            result["test_results"] = tr
    return result


@app.get("/runs")
def list_runs() -> list[dict]:
    """List all runs with their status."""
    return [
        {
            "run_id":  run_id,
            "status":  rec["status"],
            "request": rec["request"],
            "team":    rec["team"],
            "error":   rec.get("error"),
        }
        for run_id, rec in _runs.items()
    ]


@app.delete("/run/{run_id}", status_code=204)
def delete_run(run_id: str) -> None:
    """Remove a run record from memory (and disk if persisted)."""
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    del _runs[run_id]
    if _DATA_DIR:
        fp = _DATA_DIR / "runs" / f"{run_id}.json"
        if fp.exists():
            fp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Eval endpoints
# ---------------------------------------------------------------------------

@app.post("/eval", response_model=EvalSummary, status_code=202)
async def create_eval(body: EvalRequest, background_tasks: BackgroundTasks) -> EvalSummary:
    """Start an evaluation run. Returns immediately with an eval_id to poll."""
    eval_id = str(uuid.uuid4())
    name = body.name or body.request[:60]
    _evals[eval_id] = {"status": "running", "name": name, "request": body.request, "report": None, "error": None}
    background_tasks.add_task(_execute_eval, eval_id, body)
    return EvalSummary(eval_id=eval_id, status="running", name=name, request=body.request)


@app.get("/eval/{eval_id}")
def get_eval(eval_id: str) -> dict:
    """Get the status and (when done) the full eval report."""
    rec = _evals.get(eval_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Eval '{eval_id}' not found.")
    result: dict[str, Any] = {
        "eval_id": eval_id,
        "status":  rec["status"],
        "name":    rec["name"],
        "request": rec["request"],
    }
    if rec.get("report"):
        result["report"] = rec["report"]
    if rec.get("error"):
        result["error"] = rec["error"]
    return result


@app.get("/evals")
def list_evals() -> list[dict]:
    """List all eval reports with their status and top-level scores."""
    out = []
    for eval_id, rec in _evals.items():
        entry: dict[str, Any] = {
            "eval_id": eval_id,
            "status":  rec["status"],
            "name":    rec["name"],
            "request": rec["request"],
        }
        if rec.get("report"):
            rpt = rec["report"]
            entry["overall_score"] = rpt.get("overall_score", 0)
            entry["judge_score"]   = rpt.get("judge_score", 0)
            entry["passed"]        = rpt.get("passed", False)
            entry["token_count"]   = rpt.get("token_count", 0)
        if rec.get("error"):
            entry["error"] = rec["error"]
        out.append(entry)
    return out


@app.delete("/eval/{eval_id}", status_code=204)
def delete_eval(eval_id: str) -> None:
    if eval_id not in _evals:
        raise HTTPException(status_code=404, detail=f"Eval '{eval_id}' not found.")
    del _evals[eval_id]
    if _DATA_DIR:
        fp = _DATA_DIR / "evals" / f"{eval_id}.json"
        fp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Dashboard SPA — mounted LAST so API routes take precedence
# ---------------------------------------------------------------------------

def _mount_dashboard() -> None:
    static_dir = Path(__file__).parent / "static"
    if not static_dir.exists():
        return
    try:
        from fastapi.staticfiles import StaticFiles
    except ImportError:
        return
    app.mount("/ui", StaticFiles(directory=str(static_dir), html=True), name="ui")


_mount_dashboard()
