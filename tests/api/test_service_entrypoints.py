"""Tests for public service discovery routes.

These prove that a client can discover the EPT API, docs UI, and OpenAPI schema
without credentials.
"""

import asyncio

from .helpers import create_api_shell_test_app, get_many
from tests.conftest import contract, unit


@unit
def test_base_url_redirects_to_v1():
    """The base URL should send browser users to the versioned API index."""
    app = create_api_shell_test_app()
    root = asyncio.run(get_many("/", app=app, follow_redirects=False))[0]

    assert root.status_code == 307
    assert root.headers["location"] == "/v1"


@unit
def test_v1_returns_service_index():
    """The v1 route should return a service index document."""
    app = create_api_shell_test_app()
    root = asyncio.run(get_many("/v1", app=app))[0]

    assert root.status_code == 200
    assert root.json() == {
        "service": "EDITO Publishing Toolkit API",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@contract
def test_healthz_returns_ok_without_credentials():
    """The health endpoint should be a small public infrastructure probe."""
    app = create_api_shell_test_app()
    healthz = asyncio.run(get_many("/healthz", app=app))[0]

    assert healthz.status_code == 200
    assert healthz.json() == {"status": "ok"}


@contract
def test_docs_and_openapi_are_publicly_available():
    """The docs UI and OpenAPI schema should be available without credentials."""
    app = create_api_shell_test_app()
    root, docs, openapi = asyncio.run(get_many("/v1", "/docs", "/openapi.json", app=app))

    assert root.status_code == 200
    assert docs.status_code == 200
    assert openapi.status_code == 200
    assert "/openapi.json" in docs.text
