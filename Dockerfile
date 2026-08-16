FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

WORKDIR /srv/app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY swgoh_reviewer swgoh_reviewer
COPY server server
COPY templates templates
COPY squads.json squads.schema.json squads.md ./

# Fetch the pinned htmx build into the served static dir.
RUN mkdir -p server/static && \
    curl -fsSL "https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js" -o server/static/htmx.min.js

ENV PYTHONUNBUFFERED=1 \
    SWGOH_DATA_ROOT=/var/lib/swgoh-reviewer/data \
    SWGOH_COMLINK=http://comlink:3000
EXPOSE 8000

CMD [".venv/bin/uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
