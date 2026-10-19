FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.5.23 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	PIP_NO_CACHE_DIR=1 \
	UV_SYSTEM_PYTHON=1

WORKDIR /app

RUN apt-get update \
	&& apt-get install -y --no-install-recommends build-essential curl \
	&& rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md mkdocs.yml VERSION ./
COPY ept ./ept
COPY docs ./docs

RUN uv pip install --system --no-deps . \
	&& uv pip install --system . \
	# Bake the handbook into the image so the running API serves docs that
	# match its own version (mounted at /handbook by ept.api.app).
	&& mkdocs build --strict

# Mount target for local_output exports (see docker-compose.yml); the bind
# mount supplies ownership at runtime, so nothing is chowned here.
RUN mkdir -p /app/workspace

EXPOSE 8000

CMD ["uvicorn", "ept.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
