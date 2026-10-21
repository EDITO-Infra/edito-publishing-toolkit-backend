# Installing EPT

EPT works on Linux, macOS, and Windows.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) — the simplest way to run EPT; it installs the Python version required by [`pyproject.toml`](https://docs.astral.sh/uv/concepts/python-versions/) automatically
- [Docker](https://docs.docker.com/get-docker/) — only if you want to run EPT in a container

Other tooling (a Python ≥3.13 interpreter plus pip, for example) works too; the commands below just assume `uv`.

## Install

From the repository root, sync the project dependencies into a local virtual environment:

```bash
uv sync
```

## Verify

Run the test suite:

```bash
uv run pytest
```

Start the API:

```bash
uv run uvicorn ept.api.main:app --host 0.0.0.0 --port 8000 --reload
```

The API is available at `http://localhost:8000`, with interactive documentation at `http://localhost:8000/docs`. See the [usage guide](usage.md) for next steps.

## Notes

- The `Makefile` provides convenience targets (`make api-start`, `make test`, `make docs-serve`, ...) for Unix-users.
- `make api-stop` and `make api-restart` additionally use `pkill`/`lsof` and are Unix-only.
- For containers instead of a local install, see [Docker usage](docker-usage.md) — prebuilt API and frontend images are on GHCR, no clone needed.
