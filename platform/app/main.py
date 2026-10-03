"""antcrew-platform FastAPI application."""
from __future__ import annotations

import asyncio
import logging
import os

try:
    from pathlib import Path as _Path

    from dotenv import load_dotenv as _load_dotenv
    _env_file = _Path(__file__).parent.parent / f".env.{os.environ.get('APP_ENV', 'dev')}"
    if _env_file.exists():
        _load_dotenv(_env_file, override=False)
except ImportError:
    pass
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import select
from starlette.middleware.base import BaseHTTPMiddleware

from app.api import a2a as a2a_api
from app.api import accounting as accounting_api
from app.api import admin as admin_api
from app.api import admin_analytics as admin_analytics_api
from app.api import admin_billing as admin_billing_api
from app.api import admin_campaigns as admin_campaigns_api
from app.api import admin_users as admin_users_api
from app.api import (
    api_keys,
    billing,
    client_review,
    engine,
    eval_schedules,
    evals,
    pipeline,
    reviews,
    runs,
    sprints,
    stream,
    templates,
    tickets,
    webhook_mor,
    workspaces,
    workspaces_byok,
    workspaces_members,
)
from app.api import auth_session as auth_session_api
from app.api import bootstrap as bootstrap_api
from app.api import compare as compare_api
from app.api import compliance as compliance_api
from app.api import contract_schemas as contract_schemas_api
from app.api import discovery as discovery_api
from app.api import feedback as feedback_api
from app.api import github_app as github_app_api
from app.api import integrations as integrations_api
from app.api import invites as invites_api
from app.api import memory as memory_api
from app.api import pages as pages_api
from app.api import pipelines as pipelines_api
from app.api import run_schedules as run_schedules_api
from app.api import security_audit as security_audit_api
from app.api import teams as teams_api
from app.api import waitlist as waitlist_api
from app.api import workspaces_analytics as workspaces_analytics_api
from app.api import workspaces_billing as workspaces_billing_api
from app.api import workspaces_config as workspaces_config_api
from app.api import workspaces_docs as workspaces_docs_api
from app.api import workspaces_proxy as workspaces_proxy_api
from app.api import workspaces_slack as workspaces_slack_api
from app.api import workspaces_webhooks as workspaces_webhooks_api
from app.core.background import (
    _budget_alert_loop,
    _compliance_digest_loop,
    _data_retention_loop,
    _discovery_session_cleanup_loop,
    _do_retention,  # noqa: F401
    _eval_scheduler_loop,
    _hitl_cleanup_loop,
    _run_scheduler_loop,
    _velocity_check_loop,
)
from app.core.config import APP_ENV, VERSION
from app.core.csrf import require_csrf as _require_csrf
from app.core.database import get_session, init_db
from app.core.listener import start_listening, stop_listening
from app.core.logging import _setup_logging

# Re-export for backward compatibility — tests import these names from app.main
from app.core.startup import (  # noqa: F401
    _check_auth_mode,
    _check_slack_config,
    run_startup_checks,
)
from app.core.telemetry import setup_tracing

_STATIC = Path(__file__).parent / "static"
_TESTING = os.environ.get("ANTCREW_TESTING") == "1"

_sentry_dsn = os.environ.get("SENTRY_DSN", "")
if _sentry_dsn and not _TESTING:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    sentry_sdk.init(
        dsn=_sentry_dsn,
        environment=APP_ENV,
        integrations=[FastApiIntegration(), SqlalchemyIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,
    )

log = logging.getLogger(__name__)

_webhook_task: Optional[asyncio.Task] = None
_scheduler_task: Optional[asyncio.Task] = None
_hitl_cleanup_task: Optional[asyncio.Task] = None
_retention_task: Optional[asyncio.Task] = None

import re as _re
import secrets as _secrets

# Template for the CSP — {nonce} is replaced per-request.
# script-src uses 'nonce-{nonce}' instead of 'unsafe-inline': only scripts with the
# matching nonce attribute will execute, eliminating the XSS bypass from unsafe-inline.
# style-src keeps 'unsafe-inline' (CSS injection is far less dangerous than script injection
# and Alpine.js relies on inline styles internally).
_CSP_TEMPLATE = (
    "default-src 'self'; "
    "script-src 'self' 'nonce-{nonce}' 'unsafe-eval' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "font-src 'self' https://cdn.jsdelivr.net data:; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "base-uri 'self'"
)

# Matches <script ...> tags that do NOT have a src= attribute (inline scripts).
# External scripts (<script src="...">) are already controlled by the origin allowlist.
_INLINE_SCRIPT_RE = _re.compile(rb'<script((?!\s[^>]*\bsrc\b)[^>]*)>')


def _inject_nonce(html: bytes, nonce: bytes) -> bytes:
    """Add nonce="..." to every inline <script> tag in an HTML document."""
    return _INLINE_SCRIPT_RE.sub(
        lambda m: b'<script nonce="' + nonce + b'"' + m.group(1) + b'>',
        html,
    )


class _CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Propagate X-Request-ID header to structured logging and echo it in the response."""
    async def dispatch(self, request: Request, call_next):
        import uuid as _uuid

        from app.core.logging import request_id_var
        rid = request.headers.get("X-Request-ID") or str(_uuid.uuid4())
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject security headers and per-request CSP nonce on every response.

    For HTML responses, reads the body and injects the nonce into every inline
    <script> tag so 'unsafe-inline' can be removed from script-src.
    """

    async def dispatch(self, request: Request, call_next):
        from starlette.responses import Response as _Response

        nonce = _secrets.token_urlsafe(16)
        nonce_b = nonce.encode()
        csp = _CSP_TEMPLATE.format(nonce=nonce)

        response = await call_next(request)

        # Inject nonce into HTML responses (static files + server-rendered pages).
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            body = b""
            async for chunk in response.body_iterator:
                body += chunk
            body = _inject_nonce(body, nonce_b)
            # Rebuild response — preserves status, headers (minus content-length
            # which Starlette recomputes).
            headers = dict(response.headers)
            headers.pop("content-length", None)
            response = _Response(
                content=body,
                status_code=response.status_code,
                headers=headers,
                media_type="text/html",
            )

        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Content-Security-Policy"] = csp
        if APP_ENV not in ("dev", "int"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


async def _bootstrap_first_run() -> None:
    """Auto-generate PLATFORM_API_KEY on first run in single-container mode.

    Activated when DATA_DIR env var is set (Docker entrypoint sets it to /data).
    On first start, generates a secure key, persists it to $DATA_DIR/.credentials,
    and sets PLATFORM_API_KEY so the startup auth check passes without pre-configuration.
    On subsequent starts, loads the saved key from disk.

    Does nothing when PLATFORM_API_KEY is already set in the environment, or when
    DATA_DIR is not set (multi-replica deployments configure keys externally).
    """
    if os.environ.get("PLATFORM_API_KEY"):
        return
    data_dir = os.environ.get("DATA_DIR", "")
    if not data_dir:
        return
    if os.environ.get("APP_ENV", "dev") == "prod":
        # prod must use per-workspace API keys (POST /api-keys/), not PLATFORM_API_KEY
        return

    import secrets as _sec

    creds_file = Path(data_dir) / ".credentials"

    if creds_file.exists():
        for line in creds_file.read_text().splitlines():
            if line.startswith("PLATFORM_API_KEY="):
                key = line[len("PLATFORM_API_KEY="):]
                if len(key) >= 32:
                    os.environ["PLATFORM_API_KEY"] = key
                    log.info("auth: loaded PLATFORM_API_KEY from %s", creds_file)  # nosemgrep: python-logger-credential-disclosure
                    return

    key = _sec.token_urlsafe(32)
    os.environ["PLATFORM_API_KEY"] = key
    try:
        creds_file.write_text(
            "# antcrew-platform credentials — keep this file safe\n"
            "# Generated on first startup. To rotate: delete this file and restart.\n"
            f"PLATFORM_API_KEY={key}\n"
        )
        creds_file.chmod(0o600)
    except OSError as exc:
        log.warning("auth: could not write credentials to %s: %s", creds_file, exc)  # nosemgrep: python-logger-credential-disclosure

    border = "=" * 60
    log.warning(
        "\n%s\n"
        "  First run: generated PLATFORM_API_KEY\n"
        "  Key:   %s\n"
        "  Saved: %s\n"
        "  Pass this key as the X-Api-Key header to authenticate.\n"
        "%s",
        border, key, creds_file, border,
    )


async def _mark_interrupted_runs() -> None:
    """Mark runs stuck in 'running' at startup as 'interrupted' (process died mid-run)."""
    from datetime import datetime
    from datetime import timezone as _tz

    from sqlalchemy import update as _sa_update
    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession

    from app.core.database import engine as _db_engine
    from app.models.run import Run

    async with _AsyncSession(_db_engine) as _session:
        result = await _session.execute(
            _sa_update(Run)
            .where(Run.status == "running")
            .values(status="interrupted", finished_at=datetime.now(_tz.utc).replace(tzinfo=None))
        )
        await _session.commit()
    if result.rowcount:
        log.warning("startup: marked %d zombie run(s) as interrupted", result.rowcount)
    else:
        log.debug("startup: no zombie runs found")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _webhook_task, _scheduler_task, _hitl_cleanup_task, _retention_task
    _setup_logging()
    if not _TESTING:
        await init_db()
        await _bootstrap_first_run()
    await run_startup_checks(_TESTING)
    if not _TESTING:
        await _mark_interrupted_runs()
    if not _TESTING:
        start_listening()
    _security_scheduler_task: Optional[asyncio.Task] = None
    if not _TESTING:
        from app.core.slack_hitl import maybe_start_from_env as _slack_start
        _slack_start()
        from app.core.slack_hitl import set_main_loop as _set_loop
        _set_loop(asyncio.get_event_loop())
        from app.services.webhook import start_webhook_retry_loop
        _webhook_task = asyncio.create_task(start_webhook_retry_loop(), name="webhook-retry")
        _hitl_cleanup_task = asyncio.create_task(_hitl_cleanup_loop(), name="hitl-cleanup")
        _retention_task = asyncio.create_task(_data_retention_loop(), name="data-retention")
        _security_scheduler_task = asyncio.create_task(
            security_audit_api.run_schedule_loop(), name="security-audit-scheduler"
        )
        # Scheduler loops run as Celery beat tasks when a broker is configured.
        # Fall back to asyncio loops only in broker-less environments (dev, test).
        _celery_active = bool(os.environ.get("CELERY_BROKER_URL"))
        if not _celery_active:
            _scheduler_task = asyncio.create_task(_eval_scheduler_loop(), name="eval-scheduler")
            asyncio.create_task(_run_scheduler_loop(), name="run-scheduler")
        asyncio.create_task(_discovery_session_cleanup_loop(), name="discovery-cleanup")
        asyncio.create_task(_velocity_check_loop(), name="velocity-check")
        asyncio.create_task(_budget_alert_loop(), name="budget-alert")
        asyncio.create_task(_compliance_digest_loop(), name="compliance-digest")
    yield
    if not _TESTING:
        stop_listening()
    for task in (_webhook_task, _scheduler_task, _hitl_cleanup_task, _retention_task,
                 _security_scheduler_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    from app.services.runner import shutdown as _runner_shutdown
    _runner_shutdown()
    from app.services.engine_runner import shutdown as _engine_shutdown
    _engine_shutdown()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="antcrew-platform",
    version=VERSION,
    description="Dashboard and API layer for antcrew pipelines",
    lifespan=lifespan,
    docs_url="/docs" if APP_ENV == "dev" else None,
    redoc_url="/redoc" if APP_ENV == "dev" else None,
    openapi_url="/openapi.json" if APP_ENV == "dev" else None,
)
setup_tracing(app)

_cors_origins_raw = os.environ.get("CORS_ORIGINS", "").strip()
_cors_origins = (
    _cors_origins_raw.split(",")
    if _cors_origins_raw
    else ["http://localhost:3000", "http://localhost:8000",
          "http://127.0.0.1:3000", "http://127.0.0.1:8000"]
)

app.add_middleware(_CorrelationIdMiddleware)
app.add_middleware(_SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Api-Key", "X-CSRF-Token", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)

_csrf = [Depends(_require_csrf)]

app.include_router(auth_session_api.router)
app.include_router(pipeline.router,             dependencies=_csrf)
app.include_router(runs.router,                 dependencies=_csrf)
app.include_router(tickets.router,              dependencies=_csrf)
app.include_router(sprints.router,              dependencies=_csrf)
app.include_router(stream.router)                               # SSE/WebSocket — GET only, no mutations
app.include_router(api_keys.router,             dependencies=_csrf)
app.include_router(reviews.router,              dependencies=_csrf)
app.include_router(templates.router,            dependencies=_csrf)
app.include_router(workspaces.router,           dependencies=_csrf)
app.include_router(workspaces_byok.router,      dependencies=_csrf)
app.include_router(workspaces_proxy_api.router, dependencies=_csrf)
app.include_router(workspaces_members.router,   dependencies=_csrf)
app.include_router(evals.router,                dependencies=_csrf)
app.include_router(eval_schedules.router,       dependencies=_csrf)
app.include_router(engine.router,               dependencies=_csrf)
app.include_router(billing.router,              dependencies=_csrf)
app.include_router(webhook_mor.router)                          # server-to-server, HMAC-signed body
app.include_router(pipelines_api.router,        dependencies=_csrf)
app.include_router(client_review.router,        dependencies=_csrf)
app.include_router(compare_api.router,          dependencies=_csrf)
app.include_router(contract_schemas_api.router, dependencies=_csrf)
app.include_router(security_audit_api.router,          dependencies=_csrf)
app.include_router(security_audit_api.webhook_router)           # HMAC-signed, no CSRF
app.include_router(invites_api.router,                 dependencies=_csrf)
app.include_router(run_schedules_api.router,           dependencies=_csrf)
app.include_router(pages_api.router)
app.include_router(bootstrap_api.router)
app.include_router(admin_api.router,              dependencies=_csrf)
app.include_router(admin_campaigns_api.router,    dependencies=_csrf)
app.include_router(admin_billing_api.router,      dependencies=_csrf)
app.include_router(admin_analytics_api.router,    dependencies=_csrf)
app.include_router(admin_users_api.router,        dependencies=_csrf)
app.include_router(workspaces_docs_api.router,        dependencies=_csrf)
app.include_router(workspaces_slack_api.router,       dependencies=_csrf)
app.include_router(workspaces_webhooks_api.router,    dependencies=_csrf)
app.include_router(workspaces_billing_api.router,     dependencies=_csrf)
app.include_router(workspaces_analytics_api.router,   dependencies=_csrf)
app.include_router(workspaces_config_api.router,      dependencies=_csrf)
app.include_router(feedback_api.router, dependencies=_csrf)
app.include_router(discovery_api.router,  dependencies=_csrf)
app.include_router(github_app_api.webhook_router)               # OAuth callback + HMAC-signed webhook — no CSRF
app.include_router(github_app_api.router,    dependencies=_csrf)
app.include_router(accounting_api.router,    dependencies=_csrf)
app.include_router(integrations_api.router,  dependencies=_csrf)
app.include_router(teams_api.router,         dependencies=_csrf)
app.include_router(memory_api.router,        dependencies=_csrf)
app.include_router(a2a_api.router)            # A2A: no CSRF — called by external agents
app.include_router(waitlist_api.router)       # Public — no auth, no CSRF
app.include_router(compliance_api.router,    dependencies=_csrf)

app.mount("/static", StaticFiles(directory=_STATIC), name="static")


# ---------------------------------------------------------------------------
# Utility routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health(session=Depends(get_session)):
    """Liveness + readiness check. Returns 503 if the DB is unreachable."""
    from sqlalchemy import text as sa_text
    try:
        await session.exec(sa_text("SELECT 1"))
        return {"status": "ok", "db": True, "version": VERSION}
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": False, "version": VERSION, "error": str(exc)},
        )
