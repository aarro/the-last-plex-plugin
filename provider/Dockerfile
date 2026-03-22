# ── Stage 1: Build React UI ───────────────────────────────────────────────────
FROM oven/bun:1 AS ui-builder

WORKDIR /app/ui
COPY ui/package.json ui/bun.lock* ./
RUN bun install --frozen-lockfile

COPY ui/ .
RUN bun run build

# ── Stage 2: Python server ────────────────────────────────────────────────────
FROM python:3.12-slim AS server

ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install Python dependencies (cached layer before copying source)
COPY pyproject.toml .
RUN uv sync --no-dev --no-install-project

# Copy source
COPY *.py .

# Copy built UI from stage 1
COPY --from=ui-builder /app/ui/dist ./ui/dist

EXPOSE 8765

CMD ["uv", "run", "--no-dev", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8765"]
