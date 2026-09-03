"""Entrypoint for starting the EPT API with uvicorn. use ``python -m ept.api.main`` to run the API with default settings."""

from __future__ import annotations

import os

import uvicorn

from .app import app


def main() -> None:
    """Run the EPT API with uvicorn using simple environment settings."""
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))

    uvicorn.run(
        "ept.api.app:app",
        host=host,
        port=port,
    )


if __name__ == "__main__":
    main()

