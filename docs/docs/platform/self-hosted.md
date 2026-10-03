# Self-hosted deployment

antcrew-platform ships as a single Docker image. For teams of 1–15 running on one machine, no external services are required — it uses SQLite and an in-process task queue out of the box.

---

## Single-container quickstart

```bash
docker run -d \
  --name antcrew \
  -p 8000:8000 \
  -v ./data:/data \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  ghcr.io/antcrew/platform:latest
```

On first start, the platform:

1. Creates a SQLite database at `/data/platform.db`
2. Generates a `PLATFORM_API_KEY` and saves it to `/data/.credentials`
3. Logs the key prominently in the startup output

Retrieve it with:

```bash
docker logs antcrew 2>&1 | grep "PLATFORM_API_KEY="
# or read the file directly:
cat ./data/.credentials
```

Pass the key in the `X-Api-Key` header for all API calls.

---

## What runs inside one container

| Component | Mode |
|---|---|
| **API + dashboard** | FastAPI / uvicorn, single worker |
| **Database** | SQLite at `/data/platform.db` |
| **Pipeline execution** | In-process `ThreadPoolExecutor` (4 workers by default) |
| **Scheduler** | asyncio background loop — eval and run schedules fire every 60 s |
| **SSE fan-out** | In-process; all clients served by the same worker |
| **Rate limiting** | Per-process in-memory sliding window |

Everything that would otherwise require Redis or Celery degrades gracefully to an in-process equivalent. No data is lost; the only trade-off is that a worker restart interrupts in-flight runs (they are marked `interrupted` on the next start).

---

## Environment variables

Required:

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Default provider key used when no workspace BYOK key is set |

Optional but recommended for any persistent deployment:

| Variable | Default | Description |
|---|---|---|
| `DATA_DIR` | `/data` | Directory for SQLite database and generated credentials |
| `DATABASE_URL` | `sqlite+aiosqlite:////$DATA_DIR/platform.db` | Override to use PostgreSQL |
| `ANTCREW_ENCRYPTION_KEY` | _(unset)_ | AES-GCM-256 key for encrypting run state at rest. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `BYOK_ENCRYPTION_KEY` | _(unset)_ | Encrypts BYOK provider keys at rest. Same generation command. |
| `CORS_ORIGINS` | `http://localhost:8000` | Comma-separated list of allowed browser origins |
| `ANTCREW_WORKERS` | `4` | Thread-pool workers for in-process pipeline execution |

---

## Persisting data

Mount `/data` as a Docker volume to survive container restarts:

```bash
# Named volume (Docker manages it)
docker run -v antcrew-data:/data ...

# Bind mount (you control the path)
docker run -v ./data:/data ...
```

The SQLite database and the auto-generated `PLATFORM_API_KEY` both live in this directory.

---

## Upgrading

```bash
docker pull ghcr.io/antcrew/platform:latest
docker stop antcrew && docker rm antcrew
docker run -d --name antcrew -p 8000:8000 -v ./data:/data ... ghcr.io/antcrew/platform:latest
```

SQLite migrations run automatically on startup — no manual steps required.

---

## Scaling up

When you need multi-replica deployments, add PostgreSQL, Redis, and Celery. The same image supports all modes — they activate via environment variables:

```bash
docker run \
  --build-arg APP_ENV=prod \
  -e DATABASE_URL=postgresql+asyncpg://user:pass@db/antcrew \
  -e REDIS_URL=redis://redis:6379/0 \
  -e REDIS_PUBSUB_URL=redis://redis:6379/0 \
  -e CELERY_BROKER_URL=redis://redis:6379/1 \
  -e ANTCREW_ENCRYPTION_KEY=... \
  -e BYOK_ENCRYPTION_KEY=... \
  ...
```

See [deployment.md](deployment.md) for the full compose setup and Hetzner Terraform config.

---

## Limitations of the single-container mode

- **Single-writer**: SQLite locks on writes. Under high concurrency (>10 simultaneous pipeline runs), you may see contention. Switch to PostgreSQL when that happens.
- **No worker recovery**: If the container restarts mid-run, the run is marked `interrupted`. Celery workers retry on failure; the in-process executor does not.
- **Rate limiting is per-process**: If you run multiple uvicorn workers (`--workers N`), effective rate limit is `RATE_LIMIT_RPM × N`. Set `REDIS_URL` to share a single limiter.
- **SSE is in-process**: Live dashboard works fine with one container. With multiple replicas behind a load balancer, a client on worker A may miss events from a run executing on worker B unless `REDIS_PUBSUB_URL` is set.
