# One image for every VEYRA Python service; compose picks the service with `command`.
# Non-root, slim, uv-installed deps, no build toolchain in the final layer.
FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependency layer: only the manifests, so a code change does not re-resolve.
COPY pyproject.toml uv.lock ./
COPY packages/veyra_common/pyproject.toml packages/veyra_common/
COPY packages/veyra_engine/pyproject.toml packages/veyra_engine/
COPY packages/veyra_evidence/pyproject.toml packages/veyra_evidence/
COPY packages/veyra_lineage/pyproject.toml packages/veyra_lineage/
COPY packages/veyra_contracts/pyproject.toml packages/veyra_contracts/
COPY services/ services/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-packages --no-install-workspace

# Source layer.
COPY packages/ packages/
COPY demo/ demo/
COPY tools/ tools/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-packages

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin veyra \
    && mkdir -p /app/data && chown -R veyra:veyra /app /opt/venv
USER veyra

# Each service overrides this; the default keeps `docker run` honest.
CMD ["python", "-c", "print('set a command: python -m <service>')"]
