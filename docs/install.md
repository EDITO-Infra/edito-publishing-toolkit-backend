# Installing EPT

Three ways to run EPT — same API either way.

1. EDITO service — zero install, an EDITO account is all you need.
2. Docker — prebuilt images, no clone.
3. Clone — `uv sync` and run.

## EDITO service

Launch the
[service playground](https://datalab.dive.edito.eu/launcher/service-playground/edito-publishing-toolkit-service)
from your personal space. Sign in in the UI, or point scripts at your
service URL. Nothing below applies.

## Docker

Docker is the only prerequisite. Prebuilt API and frontend images are
on GHCR — no clone needed:

```bash
docker network create ept-net
docker run -d --name ept-api --network ept-net -p 8000:8000 \
  -v ept_workspace:/app/workspace -v ept_data:/app/data \
  ghcr.io/edito-infra/edito-publishing-toolkit-backend:latest
```

API on `:8000`, docs at `/docs`. No environment variables needed —
defaults are baked in. More (frontend, compose, logs, state):
[Docker usage](docker-usage.md).

## Local source code

Prerequisite: [`uv`](https://docs.astral.sh/uv/) — installs the Python
required by `pyproject.toml` itself.

```bash
git clone https://github.com/EDITO-Infra/edito-publishing-toolkit.git
cd edito-publishing-toolkit
uv sync
uv run pytest # verify
uv run uvicorn ept.api.main:app --host 0.0.0.0 --port 8000 --reload
```

API on `:8000`, docs at `/docs`. Next: [usage](usage.md). The Makefile
wraps these (`make install`, `make test`, `make api-start`);
`api-stop`/`api-restart` are Unix-only.
