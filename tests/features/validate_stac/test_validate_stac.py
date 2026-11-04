"""Unit tests for the validate-STAC feature."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from stac_validator.fast_validator import FastValidator

from ept.api.dependencies import require_edito_bearer_auth
from ept.api.main import app
from ept.features.validate_stac import service as service_mod
from ept.features.validate_stac.models import (
    StacObjectType,
    ValidateStacRequest,
)
from ept.features.validate_stac.service import (
    StacVersionError,
    validate_stac,
)
from ept.features.validate_stac.adapter import (
    validate_edito_constraints,
    run_schema_validation,
    ITEM_NOT_IN_COLLECTION_ERROR,
    COLLECTION_IN_COLLECTION_ERROR,
    ASSET_HREF_ERROR,
)
from ept.features.validate_stac.validation import StacSchemaValidator
from ept.infrastructure.formats.stac import CollectedObject
from ept.infrastructure.services.edito_auth import EditoBearerAuth
from tests.conftest import integration, unit


# ── Request model: SSRF boundary ─────────────────────────────────────────


@unit
def test_request_model_rejects_unsafe_fetch_targets():
    """Bad URLs die at the boundary (422) — non-http(s), credentials, private IPs."""
    for url in (
        "file:///etc/passwd",
        "ftp://example.com/catalog.json",
        "https://user:secret@example.com/catalog.json",
        "http://127.0.0.1:8000/catalog.json",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/catalog.json",
    ):
        with pytest.raises(ValueError):
            ValidateStacRequest(stac_url=url)


@unit
def test_request_model_accepts_public_https_url():
    request = ValidateStacRequest(stac_url="https://example.com/catalog.json")
    assert str(request.stac_url) == "https://example.com/catalog.json"


@unit
def test_traversal_never_leaves_the_catalog_host():
    """child/item/next links pointing at other hosts are skipped, not fetched.

    SSRF containment: once the caller names a host, the whole traversal stays
    on it — a malicious catalog cannot bounce the collector at internal or
    third-party services via its own links.
    """
    catalog = {
        **CATALOG,
        "links": [
            {"rel": "child", "href": "https://evil.example.com/steal.json"},
            {"rel": "child", "href": "/collection/collection.json"},
        ],
    }
    fixture = {
        "catalog.json": catalog,
        "collection/collection.json": COLLECTION,
        "collection/item-1.json": VALID_ITEM,
    }
    hosts_requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts_requested.append(request.url.host)
        key = request.url.path.lstrip("/")
        if key not in fixture:
            return httpx.Response(404)
        return httpx.Response(200, json=fixture[key])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await validate_stac(
                ValidateStacRequest(
                    stac_url="https://stac.example/catalog.json",
                    validate_children=True,
                    max_depth=3,
                ),
                http_client=client,
            )

    resp = asyncio.run(run())

    # The evil host is never contacted; traversal covers root + collection +
    # item only (api.github.com also appears: the version gate's own lookup).
    assert "evil.example.com" not in hosts_requested
    assert hosts_requested.count("stac.example") == 3
    assert resp.total_objects_checked == 3


# ── Fixtures ────────────────────────────────────────────────────────

CATALOG = {
    "id": "root-catalog",
    "type": "Catalog",
    "stac_version": "1.1.0",
    "description": "Test catalog",
    "links": [
        {"rel": "child", "href": "collection/collection.json"},
        {"rel": "self", "href": "catalog.json"},
    ],
}

COLLECTION = {
    "id": "test-collection",
    "type": "Collection",
    "stac_version": "1.1.0",
    "description": "Test collection",
    "license": "CC-BY-4.0",
    "extent": {
        "spatial": {"bbox": [[0.0, 0.0, 1.0, 1.0]]},
        "temporal": {"interval": [["2024-01-01T00:00:00Z", None]]},
    },
    "links": [
        {"rel": "item", "href": "item-1.json"},
        {"rel": "parent", "href": "../catalog.json"},
        {"rel": "self", "href": "collection.json"},
    ],
}

VALID_ITEM = {
    "id": "item-1",
    "type": "Feature",
    "stac_version": "1.1.0",
    "collection": "test-collection",
    "geometry": {"type": "Point", "coordinates": [0.5, 0.5]},
    "bbox": [0.5, 0.5, 0.5, 0.5],
    "properties": {"datetime": "2024-06-01T00:00:00Z"},
    "assets": {
        "data": {"href": "https://data.example.com/file.nc", "roles": ["data"]}
    },
    "links": [{"rel": "collection", "href": "collection.json"}],
}


def _write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def fake_schema_fetching(monkeypatch: pytest.MonkeyPatch):
    """Replace stac-validator's schema fetch/compile with a pass-through.

    Real schema validation runs in the integration tests, which have network
    access to schemas.stacspec.org. Unit tests stay offline.
    """
    monkeypatch.setattr(
        FastValidator,
        "run_dict",
        lambda self, stac_dict, source_name="in-memory": setattr(self, "valid", True),
    )


@pytest.fixture(autouse=True)
def fake_latest_stac_version(monkeypatch: pytest.MonkeyPatch):
    """Pin the latest-version lookup offline; tests never hit the GitHub API."""

    async def _latest(client: object) -> str:
        return "1.1.0"

    monkeypatch.setattr(service_mod, "get_latest_stac_version", _latest)


@pytest.fixture
def valid_catalog(tmp_path: Path) -> Path:
    collection_dir = tmp_path / "collection"
    collection_dir.mkdir()
    _write(tmp_path / "catalog.json", CATALOG)
    _write(collection_dir / "collection.json", COLLECTION)
    _write(collection_dir / "item-1.json", VALID_ITEM)
    return tmp_path / "catalog.json"


# ── EDITO profile checks (Validators) ───────────────────────────────


@unit
def test_edito_rejects_item_not_in_collection() -> None:
    """Items directly under a Catalog should fail EDITO rules."""
    issues = validate_edito_constraints(VALID_ITEM, parent_type=StacObjectType.CATALOG)
    codes = [issue.rule for issue in issues]
    assert "edito_item_not_in_collection" in codes
    assert ITEM_NOT_IN_COLLECTION_ERROR in [i.message for i in issues]


@unit
def test_edito_accepts_item_in_collection() -> None:
    """Items under a Collection should pass."""
    issues = validate_edito_constraints(VALID_ITEM, parent_type=StacObjectType.COLLECTION)
    assert len(issues) == 0


@unit
def test_edito_rejects_collection_in_collection() -> None:
    """Nested collections are forbidden."""
    issues = validate_edito_constraints(COLLECTION, parent_type=StacObjectType.COLLECTION)
    rules = [issue.rule for issue in issues]
    assert "edito_collection_in_collection" in rules
    assert COLLECTION_IN_COLLECTION_ERROR in [i.message for i in issues]


@unit
def test_edito_accepts_collection_in_catalog() -> None:
    """A collection under a catalog is the normal EDITO layout."""
    issues = validate_edito_constraints(COLLECTION, parent_type=StacObjectType.CATALOG)
    assert len(issues) == 0


@unit
def test_edito_requires_public_http_asset_hrefs() -> None:
    """Assets must be public http(s)://; relative/empty hrefs are rejected."""
    item = {
        **VALID_ITEM,
        "assets": {
            "data": {"href": "https://data.example.com/file.nc"},
            "http": {"href": "http://data.example.com/file.nc"},
            "relative": {"href": "data/file.nc"},
            "empty": {"href": ""},
        },
    }

    issues = validate_edito_constraints(item, parent_type=StacObjectType.COLLECTION)
    asset_issues = [i for i in issues if i.rule == "edito_asset_href_not_public_https"]

    assert len(asset_issues) == 2, f"Expected 2 errors, got {len(asset_issues)}"
    assert all(i.message.startswith(ASSET_HREF_ERROR) for i in asset_issues)


@unit
def test_edito_accepts_https_asset_with_credentials() -> None:
    """https:// URLs carry user info in userinfo; the validator must reject it."""
    item = {
        **VALID_ITEM,
        "assets": {"data": {"href": "https://user:secret@data.example.com/file.nc"}},
    }

    issues = validate_edito_constraints(item, parent_type=StacObjectType.COLLECTION)
    assert "edito_asset_href_not_public_https" in [i.rule for i in issues]


# ── Standard Core Checks ────────────────────────────────────────────


@unit
def test_core_check_reports_schema_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STAC schema validation failures must surface as issues."""

    def mock_run_dict(
        self: FastValidator, stac_dict: dict, source_name: str = "in-memory"
    ) -> None:
        self.valid = False
        self.message = [
            {
                "path": source_name,
                "valid_stac": False,
                "errors": [
                    {
                        "error_message": "Mocked schema error",
                        "affected_items": [],
                        "count": 0,
                    }
                ],
            }
        ]

    monkeypatch.setattr(FastValidator, "run_dict", mock_run_dict)

    issues = run_schema_validation(StacSchemaValidator(), VALID_ITEM)

    assert len(issues) == 1
    assert issues[0].rule == "stac-core-schema"
    assert "Mocked schema error" in issues[0].message
    assert issues[0].level is not None and issues[0].level.value == "error"


@unit
def test_core_check_valid_object_has_no_errors() -> None:
    issues = run_schema_validation(StacSchemaValidator(), VALID_ITEM)
    assert len(issues) == 0


# ── Throttling-aware fetch (infrastructure) ─────────────────────────


@unit
def test_read_stac_honors_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 with Retry-After waits the requested time, then succeeds."""
    import ept.infrastructure.formats.stac.read as stac_infra

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(stac_infra.asyncio, "sleep", fake_sleep)

    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] <= 2:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    async def run() -> dict:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await stac_infra.read_stac(
                "https://example.com/x", client=client, timeout=30
            )

    assert asyncio.run(run()) == {"ok": True}
    assert calls["count"] == 3
    assert sleeps == [0.0, 0.0]


@unit
def test_read_stac_backs_off_then_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without Retry-After, back off exponentially; surface 429 when exhausted."""
    import ept.infrastructure.formats.stac.read as stac_infra

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(stac_infra.asyncio, "sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    async def run() -> dict:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await stac_infra.read_stac(
                "https://example.com/x", client=client, timeout=30
            )

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(run())
    assert sleeps == [1.0, 2.0, 4.0]


@unit
def test_collect_fetches_each_object_once() -> None:
    """Traversal issues exactly one GET per object — no duplicate catalog fetches.

    Regression guard: the old sync traversal made pystac re-resolve relative
    links on every ``to_dict()``, flooding logs with repeated GETs of the root
    catalog. The collector must fetch each URL once. ``preserve_dict=True``
    keeps ``to_dict()`` fully offline.
    """
    from collections import Counter

    fixture = {
        "catalog.json": CATALOG,
        "collection/collection.json": COLLECTION,
        "collection/item-1.json": VALID_ITEM,
    }
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.lstrip("/")
        fetched.append(key)
        if key not in fixture:
            return httpx.Response(404)
        return httpx.Response(200, json=fixture[key])

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            resp = await validate_stac(
                ValidateStacRequest(
                    stac_url="https://stac.example/catalog.json",
                    validate_children=True,
                    max_depth=3,
                ),
                http_client=client,
            )
            assert resp.total_objects_checked == 3

    asyncio.run(run())

    counts = Counter(fetched)
    for path in fixture:
        assert counts[path] == 1, f"{path} fetched {counts[path]}x"


@unit
def test_schema_validation_runs_on_raw_dicts() -> None:
    """Validation consumes the raw fetched dict — no parsing, no re-fetch.

    Regression guard for the pystac era: to_dict() used to resolve the
    object's 'root' link over HTTP (one GET of the root catalog per
    validated object). Raw dicts make that class of bug impossible.
    """
    item_dict = {
        **VALID_ITEM,
        "links": [
            {"rel": "root", "href": "https://stac.example/catalog.json"},
            {"rel": "parent", "href": "https://stac.example/collection.json"},
        ],
    }

    assert run_schema_validation(
        StacSchemaValidator(), item_dict, location="https://stac.example/item-1.json"
    ) == []


# ── Version gate ────────────────────────────────────────────────────


@unit
def test_version_gate_stops_on_older_version() -> None:
    """Any object with stac_version != latest aborts the run, asking for upgrade."""
    old_catalog = {**CATALOG, "stac_version": "1.0.0"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=old_catalog)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await validate_stac(
                ValidateStacRequest(
                    stac_url="https://stac.example/catalog.json",
                    validate_children=True,
                ),
                http_client=client,
            )

    with pytest.raises(StacVersionError) as excinfo:
        asyncio.run(run())

    assert excinfo.value.found == "1.0.0"
    assert excinfo.value.latest == "1.1.0"
    assert "Upgrade" in excinfo.value.message


@unit
def test_version_gate_children_stop_traversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An outdated child stops the run mid-traversal with the upgrade request."""
    old_collection = {**COLLECTION, "stac_version": "1.0.0"}
    fixture = {
        "catalog.json": CATALOG,
        "collection/collection.json": old_collection,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.lstrip("/")
        if key not in fixture:
            return httpx.Response(404)
        return httpx.Response(200, json=fixture[key])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await validate_stac(
                ValidateStacRequest(
                    stac_url="https://stac.example/catalog.json",
                    validate_children=True,
                    max_depth=3,
                ),
                http_client=client,
            )

    with pytest.raises(StacVersionError) as excinfo:
        asyncio.run(run())

    assert excinfo.value.found == "1.0.0"
    assert excinfo.value.url is not None and "collection.json" in excinfo.value.url


@unit
def test_route_returns_422_on_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An outdated object surfaces as HTTP 422 with the upgrade message."""
    old_catalog = {**CATALOG, "stac_version": "1.0.0"}
    collected = CollectedObject(
        data=old_catalog,
        url="https://example.com/catalog.json",
        obj_type=StacObjectType.CATALOG,
        parent_type=None,
        parent_url=None,
        depth=0,
    )

    async def fake_collect(**kwargs: object):
        yield [collected]

    monkeypatch.setattr(
        "ept.features.validate_stac.service.collect_stac_tree", fake_collect
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/stac/validate",
            json={"stac_url": "https://example.com/catalog.json"},
        )

    assert response.status_code == 422
    assert "Upgrade" in response.json()["detail"]


# ── Traversal behaviour (integration, over real HTTP) ───────────────


def _run_validate(request: ValidateStacRequest, **kwargs: object):
    """Run validate_stac against a caller-supplied client (e.g. local server)."""

    async def run():
        async with httpx.AsyncClient(timeout=30) as client:
            return await validate_stac(request, http_client=client, **kwargs)

    return asyncio.run(run())


@integration
def test_scan_respects_max_depth(valid_catalog: Path) -> None:
    """max_depth=1 reaches the collection but not its items."""
    server = _serve(valid_catalog.parent)
    try:
        url = f"http://127.0.0.1:{server.server_port}/catalog.json"
        response = _run_validate(ValidateStacRequest(stac_url=url, max_depth=1))

        assert response.stac_is_valid is True
        assert response.total_objects_checked == 2
        collection = response.validation_tree.children[0]
        assert collection.object_type is StacObjectType.COLLECTION
        assert collection.children == []
    finally:
        server.shutdown()
        server.server_close()


@integration
def test_scan_detects_cycles(valid_catalog: Path) -> None:
    """A catalog linking to itself must not loop forever."""
    cycle_catalog = {
        **CATALOG,
        "links": [
            {"rel": "child", "href": "catalog.json"},
            {"rel": "self", "href": "catalog.json"},
        ],
    }
    _write(valid_catalog, cycle_catalog)

    server = _serve(valid_catalog.parent)
    try:
        url = f"http://127.0.0.1:{server.server_port}/catalog.json"
        response = _run_validate(ValidateStacRequest(stac_url=url, max_depth=10))

        assert response.stac_is_valid is True
        assert response.total_objects_checked == 1
    finally:
        server.shutdown()
        server.server_close()


@integration
def test_traversal_limit_emits_warning(
    valid_catalog: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hitting the fan-out cap yields a warning, not a crash."""
    from ept.features.validate_stac import service

    monkeypatch.setattr(service, "MAX_OBJECTS", 2)

    server = _serve(valid_catalog.parent)
    try:
        url = f"http://127.0.0.1:{server.server_port}/catalog.json"
        response = _run_validate(ValidateStacRequest(stac_url=url))

        assert response.total_objects_checked == 2
        assert response.stac_is_valid is True  # warning, not error
        assert any(
            i.rule == "traversal_limit"
            for i in response.validation_tree.issues
        )
        assert any(i.rule == "traversal_limit" for i in response.warnings)
    finally:
        server.shutdown()
        server.server_close()


# ── API Route ───────────────────────────────────────────────────────


@unit
def test_route_rejects_invalid_token() -> None:
    """The route wires the EDITO bearer auth dependency: its 401 propagates."""

    async def rejected_principal() -> EditoBearerAuth:
        raise HTTPException(status_code=401, detail="rejected by shared auth dependency")

    app.dependency_overrides[require_edito_bearer_auth] = rejected_principal
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/stac/validate",
                json={"stac_url": "https://example.com/catalog.json"},
            )
    finally:
        app.dependency_overrides.pop(require_edito_bearer_auth, None)

    assert response.status_code == 401


@unit
def test_route_returns_validation_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock the tree collector to serve one fetched catalog object."""

    collected = CollectedObject(
        data=CATALOG,
        url="https://example.com/catalog.json",
        obj_type=StacObjectType.CATALOG,
        parent_type=None,
        parent_url=None,
        depth=0,
    )

    async def fake_collect(**kwargs: object):
        yield [collected]

    monkeypatch.setattr(
        "ept.features.validate_stac.service.collect_stac_tree", fake_collect
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/stac/validate",
            json={"stac_url": "https://example.com/catalog.json", "validate_children": True},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stac_is_valid"] is True
    assert body["root_type"] == "catalog"
    assert body["total_objects_checked"] == 1
    assert body["errors"] == []
    assert body["warnings"] == []


@unit
def test_route_rejects_non_http_urls() -> None:
    """Pydantic HttpUrl validation should catch non-HTTP schemes."""
    with TestClient(app) as client:
        response = client.post(
            "/v1/stac/validate",
            json={"stac_url": "/etc/passwd", "validate_children": True},
        )
    assert response.status_code == 422


@unit
def test_route_handles_fetch_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Network errors on the root object should surface as 502."""

    async def fake_collect(**kwargs: object):
        yield [
            CollectedObject(
                data=None,
                url="https://example.com/broken.json",
                obj_type=StacObjectType.UNKNOWN,
                parent_type=None,
                parent_url=None,
                depth=0,
                fetch_error=Exception("Connection refused"),
            )
        ]

    monkeypatch.setattr(
        "ept.features.validate_stac.service.collect_stac_tree", fake_collect
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/stac/validate",
            json={"stac_url": "https://example.com/broken.json", "validate_children": True},
        )

    assert response.status_code == 502


@unit
def test_route_maps_upstream_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The feature maps StacFetchError onto its HTTP surface: upstream 404 passthrough."""
    request = httpx.Request("GET", "https://example.com/missing.json")
    response = httpx.Response(404, request=request)
    fetch_error = httpx.HTTPStatusError(
        "Client error '404 Not Found'", request=request, response=response
    )

    async def fake_collect(**kwargs: object):
        yield [
            CollectedObject(
                data=None,
                url="https://example.com/missing.json",
                obj_type=StacObjectType.UNKNOWN,
                parent_type=None,
                parent_url=None,
                depth=0,
                fetch_error=fetch_error,
            )
        ]

    monkeypatch.setattr(
        "ept.features.validate_stac.service.collect_stac_tree", fake_collect
    )

    with TestClient(app) as client:
        result = client.post(
            "/v1/stac/validate",
            json={"stac_url": "https://example.com/missing.json"},
        )

    assert result.status_code == 404


# ── Integration ─────────────────────────────────────────────────────


def _serve(local_dir: Path):
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    import threading

    handler = partial(SimpleHTTPRequestHandler, directory=str(local_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@integration
def test_scan_validates_catalog_over_http(valid_catalog: Path) -> None:
    """End-to-end scan through real HTTP against a local catalog server."""
    server = _serve(valid_catalog.parent)
    try:
        url = f"http://127.0.0.1:{server.server_port}/catalog.json"
        request = ValidateStacRequest(stac_url=url, validate_children=True, max_depth=3)

        async def run():
            async with httpx.AsyncClient(timeout=30) as client:
                return await validate_stac(request, access_token=None, http_client=client)

        response = asyncio.run(run())

        assert response.stac_is_valid is True
        assert response.total_objects_checked == 3
        assert response.root_type is StacObjectType.CATALOG

    finally:
        server.shutdown()
        server.server_close()


@integration
def test_edito_constraints_fail_on_remote_invalid_data(valid_catalog: Path) -> None:
    """Verify EDITO rules trigger on remote data that violates them."""
    bad_item = {**VALID_ITEM, "assets": {"data": {"href": "data/file.nc"}}}
    _write(valid_catalog.parent / "collection" / "item-1.json", bad_item)

    server = _serve(valid_catalog.parent)
    try:
        url = f"http://127.0.0.1:{server.server_port}/catalog.json"
        request = ValidateStacRequest(stac_url=url, validate_children=True)

        async def run():
            async with httpx.AsyncClient(timeout=30) as client:
                return await validate_stac(request, access_token=None, http_client=client)

        response = asyncio.run(run())

        assert response.stac_is_valid is False
        assert any(
            issue.rule == "edito_asset_href_not_public_https" for issue in response.errors
        )

    finally:
        server.shutdown()
        server.server_close()


@integration
def test_child_traversal_sends_bearer_token(valid_catalog: Path) -> None:
    """Children must be fetched with the caller's Authorization header."""
    seen: list[str | None] = []

    async def record_auth(request: httpx.Request) -> None:
        seen.append(request.headers.get("Authorization"))

    server = _serve(valid_catalog.parent)
    try:
        url = f"http://127.0.0.1:{server.server_port}/catalog.json"
        request = ValidateStacRequest(stac_url=url, validate_children=True)

        async def run():
            async with httpx.AsyncClient(
                timeout=30, event_hooks={"request": [record_auth]}
            ) as client:
                return await validate_stac(
                    request, access_token="secret-token", http_client=client
                )

        response = asyncio.run(run())
        assert response.stac_is_valid is True
        assert len(seen) == 3, "expected root, collection, and item fetches"
        assert all(auth == "Bearer secret-token" for auth in seen)

    finally:
        server.shutdown()
        server.server_close()
