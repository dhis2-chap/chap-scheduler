# syntax=docker/dockerfile:1.7

# --- builder ---------------------------------------------------------------
# Builds the project venv at /opt/venv. Anything installed here that isn't
# copied into the runtime stage gets discarded -- so this is where uv, build
# tooling, and any *-dev packages live.
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# git is needed by uv to fetch the dhis2-client dependency from GitHub.
# Lives in the builder stage only; the runtime image stays slim.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.4 /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first (without the project itself) so that source-only
# changes don't bust the slow dep-install layer. The lockfile MUST be present
# and frozen -- if it isn't, we want a loud failure, not a silent regenerate.
# chap_client/ is copied here too because it's a path-dep and uv needs the
# source tree present at resolution time.
COPY pyproject.toml uv.lock README.md ./
COPY chap_client ./chap_client
RUN uv sync --frozen --no-dev --no-install-project

# Now install just the project on top of the dep layer.
COPY src ./src
RUN uv sync --frozen --no-dev

# --- runtime ---------------------------------------------------------------
# Slim image: no uv, no apt cache, no build tooling. Just python + the venv +
# the bare minimum apt deps (ca-certificates for outbound HTTPS, curl for the
# compose healthcheck).
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# Non-root user. UID 10001 stays clear of the host-typical 1000-range so
# bind-mounted host volumes don't accidentally collide with a real user.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /opt/venv /opt/venv
COPY --from=builder --chown=app:app /app /app

USER app

EXPOSE 9090

CMD ["chap-scheduler", "serve", "--host", "0.0.0.0", "--port", "9090"]
