# syntax=docker/dockerfile:1.7

FROM python:3.14-slim

# set environment variables
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/hylde/.venv/bin:$PATH"

WORKDIR /hylde

# install uv
COPY --from=ghcr.io/astral-sh/uv:0.11.17 /uv /uvx /usr/local/bin/

# install only dependencies
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# install project
COPY README.md config.toml ./
COPY hylde ./hylde
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# expose flask port
EXPOSE 5000

CMD ["python", "hylde/server.py"]
