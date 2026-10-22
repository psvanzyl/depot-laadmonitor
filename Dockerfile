# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN python -m venv /app/.venv \
    && /app/.venv/bin/pip install --upgrade pip \
    && /app/.venv/bin/pip install httpx "psycopg[binary]" fastapi "uvicorn[standard]" jinja2

COPY src ./src
COPY migrations ./migrations
COPY tests ./tests
COPY validation ./validation
RUN /app/.venv/bin/pip install --no-deps -e .

RUN mkdir -p /data/raw /data/reports
EXPOSE 8080
CMD ["depotmon", "--help"]
