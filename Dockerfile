FROM python:3.13-slim AS runtime

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# Invoke the venv's console scripts directly rather than `uv run` — `uv run` re-checks
# pyproject.toml/uv.lock against the environment on every invocation and, given the image was
# built with `--no-install-project`, "fixes" that by re-syncing (including the dev group) at
# container start, which is both slow and an unwanted network call at runtime.
# Cloud Run sets $PORT; default 8080 for local `docker run`.
CMD ["sh", "-c", "alembic upgrade head && uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
