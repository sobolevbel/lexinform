FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    LEXINFORM_DB_PATH=/data/lexinform.db
RUN useradd --create-home --uid 10001 lexinform && mkdir -p /data && chown lexinform /data
COPY --from=base /opt/venv /opt/venv
USER lexinform
VOLUME ["/data"]
ENTRYPOINT ["lexinform"]
CMD ["run"]
