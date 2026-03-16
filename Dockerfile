# Stage 1: Build
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY researchloop/ researchloop/

# Install dependencies into a virtual environment
RUN uv venv /app/.venv && \
    uv pip install --python /app/.venv/bin/python .

# Stage 2: Runtime
FROM python:3.12-slim

WORKDIR /app

# Copy virtual environment and application from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/researchloop /app/researchloop

# Put venv on PATH
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8080

CMD ["researchloop", "serve"]
