# Stage 1 — build Tailwind CSS from HTML sources
FROM node:20-slim AS css-builder
WORKDIR /build
COPY app/static/ ./static/
RUN printf '@tailwind base;\n@tailwind components;\n@tailwind utilities;\n' > input.css && \
    npx --yes tailwindcss@3 \
        --content './static/*.html' \
        -i input.css \
        -o tailwind.css \
        --minify


# Stage 2 — install Python dependencies
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml .
# Install into an isolated prefix so only these files are copied to the runtime stage
RUN pip install --no-cache-dir --prefix=/deps ".[billing]"


# Stage 2 — lean runtime image
FROM python:3.11-slim AS runtime

# asyncpg needs libpq at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN adduser --disabled-password --gecos "" --uid 1000 appuser

COPY --from=builder /deps /usr/local

WORKDIR /app
COPY . .
COPY --from=css-builder /build/tailwind.css ./app/static/tailwind.css

# Data volume for SQLite database and generated credentials; fix ownership
RUN mkdir -p /data && chown appuser:appuser /data \
    && chmod +x /app/docker-entrypoint.sh \
    && chown -R appuser:appuser /app

USER appuser

# APP_ENV is baked at build time and can be overridden at container runtime.
# Default is 'dev' for single-container deployments (SQLite, relaxed startup guards).
# For multi-replica production with PostgreSQL: --build-arg APP_ENV=prod
# Build: docker build --build-arg APP_ENV=prod .
# Run:   docker run -e APP_ENV=prod -e DATABASE_URL=postgresql://... ...
ARG APP_ENV=dev
ENV APP_ENV=${APP_ENV} \
    DATA_DIR=/data \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Mount this volume to persist the SQLite database and generated credentials
# across container restarts: docker run -v ./data:/data ...
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["/app/docker-entrypoint.sh"]
