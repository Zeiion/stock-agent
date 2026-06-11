# syntax=docker/dockerfile:1
#
# Stock Agent — multi-stage image
# =================================
# Final layout (must match app.config / app.main so the SPA is served):
#   /app/backend            <- Python package root (importable as `app`); WORKDIR
#   /app/frontend/dist      <- built Vite SPA  (app.main mounts /assets + serves index.html)
#   /app/data               <- SQLite DB lives here (mounted as a volume in compose)
#
# Why this layout:
#   config.py:  PKG_ROOT     = .../app's parent  -> /app/backend
#               PROJECT_ROOT = PKG_ROOT.parent   -> /app
#   main.py:    _FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist" -> /app/frontend/dist
#   So with WORKDIR=/app/backend, _FRONTEND_DIST.exists() is True at runtime and the
#   SPA (+ /assets static mount) is served.  DB default would be /app/data/stockagent.db;
#   we also set DB_PATH explicitly via compose.
#
# AI provider caveat (IMPORTANT):
#   The `claude` / `codex` AI tiers are HOST CLIs spawned as subprocesses — they are NOT
#   available inside this container. For Docker you MUST use the Anthropic API tier:
#       AI_PROVIDER=anthropic
#       ANTHROPIC_API_KEY=sk-ant-...      (supply via .env / env_file)
#   Data sources (akshare / yfinance) only need outbound network, which works fine in Docker.

# --------------------------------------------------------------------------- #
# Stage 1: build the React/Vite frontend
# --------------------------------------------------------------------------- #
FROM node:20-alpine AS frontend
WORKDIR /build/frontend

# Install deps first (better layer caching). pnpm comes from corepack (bundled with node 20).
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN corepack enable \
    && (pnpm install --frozen-lockfile || pnpm install)

# Build the SPA -> /build/frontend/dist  (package.json "build" = tsc && vite build)
COPY frontend/ ./
RUN pnpm build

# --------------------------------------------------------------------------- #
# Stage 2: Python runtime that serves API + built SPA
# --------------------------------------------------------------------------- #
FROM python:3.10-slim AS runtime

# Faster, quieter, no .pyc clutter
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Bind to all interfaces inside the container (config.py default is 127.0.0.1)
    HOST=0.0.0.0 \
    PORT=8848 \
    # In Docker the CLI tiers can't run; default to the Anthropic API tier.
    AI_PROVIDER=anthropic

# Python deps (pandas/numpy ship wheels for slim; build-essential not required for these pins)
COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# Backend package -> /app/backend  (importable as `app`)
COPY backend/ /app/backend/

# Built frontend -> /app/frontend/dist  (so _FRONTEND_DIST.exists() == True)
COPY --from=frontend /build/frontend/dist /app/frontend/dist

# SQLite DB directory (mounted as a volume in compose; DB_PATH points here)
RUN mkdir -p /app/data

WORKDIR /app/backend
EXPOSE 8848

# Run the ASGI app; honours HOST/PORT envs set above.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8848"]
