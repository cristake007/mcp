# Build ContextForge from the imported Python and UI sources; Rust disabled.
FROM node:24-bookworm-slim AS assets
WORKDIR /build
COPY package.json package-lock.json postcss.config.js vite.config.js ./
COPY mcpgateway/templates/ mcpgateway/templates/
COPY mcpgateway/static/ mcpgateway/static/
COPY mcpgateway/admin_ui/ mcpgateway/admin_ui/
RUN npm ci && npm run build:css && npm run vite:build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 RUST_MCP_MODE=off \
    MCPGATEWAY_SKIP_MIGRATIONS=true
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl ca-certificates libmagic1 gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY mcpgateway/ mcpgateway/
COPY plugins/ plugins/
COPY scripts/ scripts/
COPY gunicorn.config.py mcp-catalog.yml docker-entrypoint.sh run-gunicorn.sh run.sh ./
COPY --from=assets /build/mcpgateway/static/ mcpgateway/static/
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && BUILD_UI_ASSETS=false python -m pip install --no-cache-dir ".[redis,postgres,plugins]" \
    && chmod +x docker-entrypoint.sh run-gunicorn.sh run.sh \
    && python -c "import mcpgateway.bootstrap_db; import mcpgateway.main"
EXPOSE 4444
CMD ["./docker-entrypoint.sh"]
