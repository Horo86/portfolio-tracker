# syntax=docker/dockerfile:1
# Portfolio Tracker — one container: FastAPI backend serving the built React frontend.
# Build:  docker build --platform linux/amd64 -t portfolio-tracker:dev .

# --- 1. Frontend build (Node is used only here) ---------------------------------
FROM node:24.21.0-alpine AS frontend-build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci --no-audit
COPY frontend/ ./
RUN npm run build

# --- 2. Backend virtual environment from the lockfile ---------------------------
FROM python:3.12.15-slim-trixie AS backend-build
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --locked --no-dev --no-editable

# --- 3. Runtime image ------------------------------------------------------------
FROM python:3.12.15-slim-trixie
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --no-create-home --shell /usr/sbin/nologin app \
    && mkdir /data && chown app:app /data
COPY --from=backend-build /app/.venv /app/.venv
COPY --from=frontend-build /src/frontend/dist /app/static
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PT_DATA_DIR=/data \
    PT_HOST=0.0.0.0 \
    PT_PORT=8000 \
    PT_STATIC_DIR=/app/static
# Default non-root user; any UID/GID can be chosen at run time (docker run --user / compose user:).
USER app
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PT_PORT', '8000') + '/api/health', timeout=4)"]
CMD ["python", "-m", "portfolio_tracker"]
