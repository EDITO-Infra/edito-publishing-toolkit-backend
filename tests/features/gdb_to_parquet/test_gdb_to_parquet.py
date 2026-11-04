"""Tests for gdb_to_parquet primitives, helpers, and live conversion."""

import json
import warnings
import zipfile
from pathlib import Path

import pytest
import pyarrow as pa
from pydantic import ValidationError

from ept.features.gdb_to_parquet.models import GdbToParquetRequest
from ept.features.gdb_to_parquet.service import _output_name, gdb_to_parquet
from ept.infrastructure.formats.gdb import (
    GDB_ITEMS_TABLE,
    gdb_from_zip,
    open_gdb,
    open_gdb_layer,
)
from tests.conftest import integration, unit

warnings.filterwarnings("ignore", category=RuntimeWarning)


def _request(**overrides):
    base = {"source_uri": "/data/pkg.gdb", "local_output": "/tmp/out"}
    base.update(overrides)
    return GdbToParquetRequest.model_validate(base)


def _credentials(**overrides):
    credentials = {
        "endpoint_url": "https://s3.example.test",
        "access_key_id": "temporary-key",
        "secret_access_key": "temporary-secret",
    }
    credentials.update(overrides)
    return credentials


@unit
def test_request_validates_credentials_and_output_choice():
    # S3 output is the default: target required, creds optional (env fallback).
    request = _request(target="geo/out", local_output=None, **_credentials())
    assert request.target == "geo/out"
    # local_output is exclusive with target/bucket and forbids credentials.
    with pytest.raises(ValidationError, match="not both"):
        _request(local_output="/tmp/out", target="geo/out")
    with pytest.raises(ValidationError, match="only valid with S3 output"):
        _request(**_credentials())


@unit
def test_request_strips_and_validates_layers():
    with pytest.raises(ValidationError, match="non-empty list"):
        _request(layers=[])
    with pytest.raises(ValidationError, match="non-empty list"):
        _request(layers=["  "])
    assert _request(layers=[" points "]).layers == ["points"]
    assert _request().layers is None


@unit
def test_open_gdb_rejects_directory_without_gdbtable(tmp_path):
    with pytest.raises(ValueError, match="not a valid ESRI file geodatabase"):
        open_gdb(tmp_path, tmp_path)


@unit
def test_open_gdb_stages_http_zip_without_path_mangling(tmp_path, monkeypatch):
    """HTTP(S) source URLs must never pass through Path() — it collapses
    'https://' to 'https:/' (the registry of the 404 with a one-slash URL)."""

    url = "https://s3.example.test/bucket/EMODnet_HA_20230125.zip"

    def fake_download(received_url, dest, **kwargs):
        assert received_url == url
        assert str(dest) == str(tmp_path / "source.zip")
        with zipfile.ZipFile(dest, "w") as archive:
            archive.writestr("pkg/d.gdb/" + GDB_ITEMS_TABLE, b"table")
        return dest

    monkeypatch.setattr("ept.infrastructure.formats.gdb.download_url", fake_download)
    gdb_dir = open_gdb(url, tmp_path)
    assert (gdb_dir / GDB_ITEMS_TABLE).is_file()


@unit
def test_gdb_from_zip_requires_exactly_one_gdb(tmp_path):
    archive_path = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("readme.txt", "no gdb here")
    with pytest.raises(ValueError, match="exactly one file geodatabase"):
        gdb_from_zip(archive_path, tmp_path / "extracted")


@unit
def test_gdb_from_zip_extracts_nested_gdb(tmp_path):
    archive_path = tmp_path / "pkg.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("pkg/data.gdb/" + GDB_ITEMS_TABLE, b"table")
        archive.writestr("pkg/data.gdb/a00000001.gdbtable", b"table")
    gdb_dir = gdb_from_zip(archive_path, tmp_path / "extracted")
    assert (gdb_dir / GDB_ITEMS_TABLE).is_file()


@unit
def test_gdb_from_zip_rejects_path_traversal(tmp_path):
    archive_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../evil.txt", "payload")
    with pytest.raises(ValueError, match="escapes extraction directory"):
        gdb_from_zip(archive_path, tmp_path / "extracted")


@unit
def test_output_name_is_filesystem_safe():
    assert _output_name("MSFD_GES_MRU_points") == "MSFD_GES_MRU_points"
    assert _output_name("layer with/space") == "layer_with_space"


def _make_gdb(path: Path) -> None:
    """Build a two-layer fixture geodatabase (points + polygons, EPSG:31370)."""

    geopandas = pytest.importorskip("geopandas")
    from shapely.geometry import Point, Polygon

    geopandas.GeoDataFrame(
        {"id": [1, 2], "name": ["a", "b"]},
        geometry=[Point(150000, 150000), Point(160000, 155000)],
        crs="EPSG:31370",
    ).to_file(path, layer="points", driver="OpenFileGDB")
    geopandas.GeoDataFrame(
        {"id": [10], "label": ["p1"]},
        geometry=[
            Polygon([(150000, 150000), (160000, 150000), (160000, 155000), (150000, 155000)])
        ],
        crs="EPSG:31370",
    ).to_file(path, layer="areas", driver="OpenFileGDB")


@unit
def test_open_gdb_layer_streams_renamed_wkb_batches(tmp_path):
    pytest.importorskip("geopandas")

    gdb = tmp_path / "fixture.gdb"
    _make_gdb(gdb)
    with open_gdb_layer(gdb, "points", batch_size=1) as stream:
        assert stream.meta.crs == "EPSG:31370"
        assert stream.meta.geometry_type == "Point"
        assert stream.geometry_types == ["Point"]
        assert stream.geometry_column == "geometry"
        assert stream.schema.field("geometry").type == pa.binary()
        batches = list(stream.batches)
    assert [batch.num_rows for batch in batches] == [1, 1]
    assert batches[0].schema.names == ["id", "name", "geometry"]


@unit
def test_gdb_to_parquet_writes_one_file_per_layer(tmp_path):
    """Offline conversion check: one file per layer with its own `geo` metadata."""

    pytest.importorskip("geopandas")
    import pyarrow.parquet as pq

    gdb = tmp_path / "fixture.gdb"
    _make_gdb(gdb)

    response = gdb_to_parquet(
        GdbToParquetRequest.model_validate(
            {"source_uri": str(gdb), "local_output": str(tmp_path / "out")}
        )
    )
    assert response.status == "completed"
    assert sorted(response.layers) == ["areas", "points"]
    assert response.feature_counts == {"points": 2, "areas": 1}
    assert response.crs == {"points": "EPSG:31370", "areas": "EPSG:31370"}
    assert len(response.outputs) == 2
    for output in response.outputs:
        assert Path(output).is_file()

    points = pq.ParquetFile(response.outputs[1])
    assert points.metadata.num_rows == 2
    geo = json.loads(points.metadata.metadata[b"geo"])
    assert geo["version"] == "1.1.0"
    assert geo["primary_column"] == "geometry"
    column = geo["columns"]["geometry"]
    assert column["encoding"] == "WKB"
    assert column["geometry_types"] == ["Point"]
    assert column["crs"]["id"] == {"authority": "EPSG", "code": 31370}
    # Full PROJJSON with the datum embedded — degraded id-only CRS breaks
    # DuckDB-based viewers ("datum not found" on bbox reprojection).
    assert "datum" in json.dumps(column["crs"])
    table = points.read()
    assert table.column("geometry").type == pa.binary()
    assert table.num_rows == 2


@unit
def test_gdb_to_parquet_layer_selection_and_missing_layer(tmp_path):
    pytest.importorskip("geopandas")
    gdb = tmp_path / "fixture.gdb"
    _make_gdb(gdb)
    out = tmp_path / "out"

    response = gdb_to_parquet(
        GdbToParquetRequest.model_validate(
            {"source_uri": str(gdb), "local_output": str(out), "layers": ["points"]}
        )
    )
    assert response.layers == ["points"]
    assert len(response.outputs) == 1

    with pytest.raises(ValueError, match="Layers not found"):
        gdb_to_parquet(
            GdbToParquetRequest.model_validate(
                {"source_uri": str(gdb), "local_output": str(out / "x"), "layers": ["nope"]}
            )
        )


@unit
def test_gdb_to_parquet_overwrite_and_conflict(tmp_path):
    pytest.importorskip("geopandas")
    gdb = tmp_path / "fixture.gdb"
    _make_gdb(gdb)
    target = tmp_path / "out"
    request = GdbToParquetRequest.model_validate(
        {"source_uri": str(gdb), "local_output": str(target)}
    )
    gdb_to_parquet(request)
    with pytest.raises(FileExistsError, match="overwrite=false"):
        gdb_to_parquet(request)
    response = gdb_to_parquet(
        GdbToParquetRequest.model_validate(
            {"source_uri": str(gdb), "local_output": str(target), "overwrite": True}
        )
    )
    assert response.status == "completed"


@unit
def test_gdb_to_parquet_uploads_outputs_to_s3_target(tmp_path, monkeypatch):
    """S3 target: staged local files upload object-by-object with request-scoped options."""

    pytest.importorskip("geopandas")

    class FakeS3:
        def __init__(self):
            self.calls = []

        def put(self, fs, source, remote_path, *, overwrite):
            self.calls.append((str(source), remote_path, overwrite))
            return f"s3://{remote_path}"

    fake = FakeS3()
    monkeypatch.setattr(
        "ept.features.gdb_to_parquet.service.s3_url_to_fs",
        lambda uri, storage_options: (fake, uri.removeprefix("s3://")),
    )
    monkeypatch.setattr("ept.features.gdb_to_parquet.service.put_to_s3", fake.put)

    gdb = tmp_path / "fixture.gdb"
    _make_gdb(gdb)
    response = gdb_to_parquet(
        GdbToParquetRequest.model_validate(
            {
                "source_uri": str(gdb),
                "bucket": "bucket",
                "target": "geo/",
                **_credentials(),
            }
        )
    )

    assert response.target_uri == "s3://bucket/geo"
    assert response.outputs == [
        "s3://bucket/geo/areas.parquet",
        "s3://bucket/geo/points.parquet",
    ]
    assert [(Path(source).name, remote, overwrite) for source, remote, overwrite in fake.calls] == [
        ("areas.parquet", "bucket/geo/areas.parquet", False),
        ("points.parquet", "bucket/geo/points.parquet", False),
    ]


@unit
def test_gdb_to_parquet_empty_layer(tmp_path):
    """A zero-feature layer still produces a valid, correctly-typed parquet file."""

    pytest.importorskip("geopandas")
    import pyarrow.parquet as pq
    import geopandas as gpd

    gdb = tmp_path / "fixture.gdb"
    gpd.GeoDataFrame(
        {"id": []}, geometry=gpd.GeoSeries([], crs="EPSG:31370")
    ).to_file(gdb, layer="empty", driver="OpenFileGDB", geometry_type="Point")
    response = gdb_to_parquet(
        GdbToParquetRequest.model_validate(
            {"source_uri": str(gdb), "local_output": str(tmp_path / "out")}
        )
    )
    assert response.feature_counts == {"empty": 0}
    parquet = pq.ParquetFile(response.outputs[0])
    assert parquet.metadata.num_rows == 0
    geo = json.loads(parquet.metadata.metadata[b"geo"])
    assert geo["columns"]["geometry"]["geometry_types"] == ["Point"]


@integration
def test_gdb_to_parquet_api_route_converts(tmp_path):
    """Full request path: bearer-auth-gated route through run_in_threadpool."""

    pytest.importorskip("geopandas")
    from fastapi.testclient import TestClient

    from ept.api.app import create_app
    from ept.api.dependencies import require_edito_bearer_auth
    from ept.infrastructure.services.edito_auth import EditoBearerAuth

    gdb = tmp_path / "fixture.gdb"
    _make_gdb(gdb)

    app = create_app()

    async def authenticated_principal() -> EditoBearerAuth:
        return EditoBearerAuth(
            subject="test-user",
            username="alice",
            claims={"sub": "test-user"},
            access_token="access-secret",
        )

    app.dependency_overrides[require_edito_bearer_auth] = authenticated_principal
    with TestClient(app) as client:
        response = client.post(
            "/v1/transform/gdb-to-parquet",
            json={"source_uri": str(gdb), "local_output": str(tmp_path / "out")},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert sorted(body["layers"]) == ["areas", "points"]
    assert all(Path(output).is_file() for output in body["outputs"])
