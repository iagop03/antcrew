# Configuration reference

All configuration is via environment variables. In development, create a `.env` file at the project root and set them there. In production (Fly.io, Docker, Hetzner), set them as platform secrets.

Only `ANTHROPIC_API_KEY` is required for a local dev run with SQLite.

---

## Core

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///platform.db` | DB connection string. Use `postgresql+asyncpg://user:pass@host/db` in production. |
| `ANTHROPIC_API_KEY` | — | **Required.** Default LLM key for managed mode. |
| `REDIS_URL` | — | Redis connection string (e.g. `redis://localhost:6379/0`). Required in production for the sliding-window rate limiter. When unset, rate limiting is skipped (dev/SQLite only). |
| `BASE_URL` | `https://app.antcrew.ai` | Public base URL embedded in invite and join-request emails. |
| `PLATFORM_BASE_URL` | — | Public base URL embedded in HITL review links sent via webhooks/Slack. |
| `ANTCREW_WORKERS` | `4` | Background thread pool size for engine runs. |

## Auth

| Variable | Default | Description |
|---|---|---|
| `PLATFORM_API_KEY` | — | Static master key. When set, any request with this key bypasses the database key check. Useful for initial bootstrap or infrastructure scripts. |
| `BYOK_ENCRYPTION_KEY` | — | Fernet key for encrypting per-workspace LLM API keys and proxy tokens at rest. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |
| `PLATFORM_ADMIN_TOKEN` | — | Bootstrap token for granting platform admin access. Send `POST /admin/make-admin` with `{"email": "you@example.com"}` and `Authorization: Bearer <token>`. Required only once per environment to create the first admin. |
| *(no var)* | — | **MFA required for admin access.** Platform admin accounts must have MFA enabled (`Settings → Security → Enable MFA`) before accessing `/admin/*` endpoints via browser session. API-key-based access to admin routes is not permitted — admin endpoints only accept browser sessions. |

## Email

Required to send verification codes, workspace invites, and HITL notifications.

| Variable | Default | Description |
|---|---|---|
| `SMTP_HOST` | — | SMTP server hostname. Leave unset to disable all email sending. |
| `SMTP_PORT` | `587` | SMTP port. Use `465` with `SMTP_TLS=ssl`. |
| `SMTP_USER` | — | SMTP login username (usually the sender address). |
| `SMTP_PASSWORD` | — | SMTP login password or app password. |
| `SMTP_FROM` | `SMTP_USER` | From address on outgoing emails. Defaults to `SMTP_USER` if unset. |
| `SMTP_TLS` | `true` | TLS mode: `true` = STARTTLS (port 587), `ssl` = SMTPS (port 465), `none` = plain (not recommended). |

## LLM providers

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | OpenAI models and eval judge scoring. |
| `GROQ_API_KEY` | — | Groq inference. |
| `GEMINI_API_KEY` | — | Google Gemini. |

Other providers (Ollama, Moonshot, DeepSeek, etc.) are configured by their model string prefix — see the [engine providers reference](../engine/providers.md).

## HITL integrations

### Slack

| Variable | Default | Description |
|---|---|---|
| `SLACK_BOT_TOKEN` | — | `xoxb-…` bot token. Enables Slack interactive HITL (Socket Mode). |
| `SLACK_APP_TOKEN` | — | `xapp-…` app-level token for Socket Mode. |
| `SLACK_CHANNEL_ID` | — | Default Slack channel for HITL review messages. |
| `SLACK_TOKEN_ENCRYPTION_KEY` | — | Fernet key for encrypting per-workspace Slack tokens at rest. |

### Telegram

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Bot token from BotFather. |
| `TELEGRAM_CHAT_ID` | — | Default chat/group ID for notifications. |

## Integrations

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | — | Personal access token for GitHub PR automation (`pipeline.end` → branch → PR). |
| `WEBHOOK_URL` | — | Global fallback webhook fired on every `pipeline.end` event across all workspaces. |

## HITL tuning

| Variable | Default | Description |
|---|---|---|
| `HITL_TIMEOUT_S` | `3600` | Seconds before a pending HITL review auto-times-out and the run resumes/fails. |

## Database connection pool (PostgreSQL)

Only applies when `DATABASE_URL` starts with `postgresql`.

| Variable | Default | Description |
|---|---|---|
| `DB_POOL_SIZE` | `5` | Connections kept open per worker. |
| `DB_MAX_OVERFLOW` | `5` | Burst connections allowed per worker above pool_size. |
| `DB_POOL_TIMEOUT` | `30` | Seconds to wait for a connection before raising an error. |

Rule of thumb: `DB_POOL_SIZE × workers × replicas < max_connections − 10`.

## Artifact storage

| Variable | Default | Description |
|---|---|---|
| `ARTIFACT_STORAGE_URL` | _(unset)_ | Where to store engine-run artifacts. Unset = inline in DB (default). `file:///abs/path` = local filesystem. `s3://bucket/prefix` = Amazon S3 (requires `pip install boto3`). |

## Observability

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `LOG_FORMAT` | `json` | Log format: `json` (structured, production) or any other value for plain text. |
| `REDIS_PUBSUB_URL` | _(unset)_ | Redis connection string for SSE relay (e.g. `redis://localhost:6379/0`). When set, workers elect a **leader per run** via Redis SETNX. The leader polls the DB and publishes events to `antcrew:sse:{run_id}`; follower workers subscribe to Redis and fan out to their local SSE queues — eliminating duplicate DB polls across replicas. External consumers can also subscribe directly. Requires `pip install "redis[asyncio]"`. |

## Job queue (Celery)

Optional persistent job queue. When unset, runs execute in the in-process thread pool and are lost if the process crashes mid-run.

| Variable | Default | Description |
|---|---|---|
| `CELERY_BROKER_URL` | _(unset)_ | Celery broker URL (e.g. `redis://localhost:6379/1`, `amqp://guest:guest@localhost/`). When set, `POST /run/` enqueues work via Celery instead of the in-process thread pool. |
| `CELERY_RESULT_BACKEND` | `CELERY_BROKER_URL` | Celery result backend. Defaults to the broker URL. |

**Starting the worker:**

```bash
celery -A app.celery_app worker --loglevel=info --concurrency=4
```

Requires `pip install celery`. The worker must have access to the same `DATABASE_URL`, `ANTHROPIC_API_KEY`, and all other env vars as the API process.

**How it works:**

1. `POST /run` resolves workspace settings (BYOK keys, model overrides, budget) and repo context in the API process, then enqueues all serialised params to Celery.
2. The worker recreates `PlatformChannel` (DB-backed by default — works cross-process) and calls `_run_sync()` in a thread pool.
3. When `pipeline.start` fires, the worker publishes the `run_id` via Celery task state. The API polls this for up to `_DISPATCH_TIMEOUT` (10 s) and returns `run_id` to the client as soon as it arrives.
4. After the run finishes, the worker calls `_store_result()` and sets run attribution in the DB.

**Limitations in Celery mode:**
- `write_back=True` (Git push) is not supported — the cloned repo directory exists only in the API process. Use the standard in-process mode for write-back runs.
- KV team memory is not persisted after Celery runs (planned for a future release).

---

## Example `.env` for local development

```dotenv
# Minimum required
ANTHROPIC_API_KEY=sk-ant-...

# Optional: use PostgreSQL instead of the default SQLite
# DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/antcrew

# Auth bootstrap (run once to create the first platform admin)
# PLATFORM_ADMIN_TOKEN=your-secret-token

# Email (verification codes, invites — leave unset to skip)
# SMTP_HOST=smtp.example.com
# SMTP_PORT=587
# SMTP_USER=hello@example.com
# SMTP_PASSWORD=
# SMTP_FROM=hello@example.com

# BYOK encryption (required to store per-workspace LLM keys)
# BYOK_ENCRYPTION_KEY=...

# Public URLs (required for invite/review links to work)
# BASE_URL=http://localhost:8000
# PLATFORM_BASE_URL=http://localhost:8000

# Artifact storage (default: inline in DB; file or S3 for large pipelines)
# ARTIFACT_STORAGE_URL=file:///var/antcrew/artifacts
# ARTIFACT_STORAGE_URL=s3://my-bucket/antcrew-artifacts

# Redis relay (optional; enables leader-election SSE relay and external consumers)
# REDIS_PUBSUB_URL=redis://localhost:6379/0

# Celery job queue (optional, for durable runs that survive process restarts)
# CELERY_BROKER_URL=redis://localhost:6379/1

# Logging
# LOG_LEVEL=DEBUG
# LOG_FORMAT=json
```

---

## Generating Fernet keys

Both `BYOK_ENCRYPTION_KEY` and `SLACK_TOKEN_ENCRYPTION_KEY` must be valid Fernet keys:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Generate a separate key for each variable. Rotating a key requires re-encrypting all stored secrets — there is no automatic migration.
