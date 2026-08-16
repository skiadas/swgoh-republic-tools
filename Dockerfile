FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

WORKDIR /srv/app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY swgoh_reviewer swgoh_reviewer
COPY server server
COPY templates templates
COPY scripts scripts
COPY squads.json squads.schema.json squads.md ./

# Fetch the pinned htmx build into the served static dir. The version pin lives
# in scripts/fetch-htmx.sh (used for local dev and the image alike).
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/* \
    && ./scripts/fetch-htmx.sh

ENV PYTHONUNBUFFERED=1 \
    SWGOH_DATA_ROOT=/var/lib/swgoh-reviewer/data \
    SWGOH_COMLINK=http://comlink:3000
EXPOSE 8000

CMD [".venv/bin/uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
