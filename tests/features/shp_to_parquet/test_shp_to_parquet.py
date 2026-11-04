"""Tests for shp_to_parquet primitives, helpers, and live conversion."""

import json
import warnings
import zipfile
from pathlib import Path

import pytest
import pyarrow as pa
from pydantic import ValidationError

from ept.features.shp_to_parquet.models import ShpToParquetRequest
from ept.features.shp_to_parquet.service import _output_name, shp_to_parquet
from ept.infrastructure.formats.shp import (
    open_shp_layer,
    shp_files_from_zip,
    stage_shp_sources,
)
from tests.conftest import integration, unit

warnings.filterwarnings("ignore", category=RuntimeWarning)


def _request(**overrides):
    base = {"source_uri": "/data/pkg.zip", "local_output": "/tmp/out"}
    base.update(overrides)
    return ShpToParquetRequest.model_validate(base)


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
def test_stage_shp_sources_accepts_local_shp(tmp_path):
    pytest.importorskip("geopandas")
    shp = tmp_path / "single.shp"
    _make_shp(shp)
    assert stage_shp_sources(shp, tmp_path / "work") == [shp]


@unit
def test_stage_shp_sources_rejects_bare_remote_shp(tmp_path):
    with pytest.raises(ValueError, match="must be a .zip archive"):
        stage_shp_sources("https://files.example.org/data/layer.shp", tmp_path)


@unit
def test_stage_shp_sources_rejects_non_shp(tmp_path):
    other = tmp_path / "not-a-shape.zip"
    with zipfile.ZipFile(other, "w") as archive:
        archive.writestr("readme.txt", "nothing here")
    with pytest.raises(ValueError, match="no .shp shapefiles"):
        stage_shp_sources(other, tmp_path / "work")
    with pytest.raises(FileNotFoundError, match="source_uri not found"):
        stage_shp_sources(tmp_path / "missing.zip", tmp_path / "work")


@unit
def test_shp_files_from_zip_rejects_path_traversal(tmp_path):
    archive_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../evil.shp", "payload")
    with pytest.raises(ValueError, match="escapes extraction directory"):
        shp_files_from_zip(archive_path, tmp_path / "extracted")


@unit
def test_output_name_is_filesystem_safe():
    assert _output_name("mangrove_eov_poly_2025") == "mangrove_eov_poly_2025"
    assert _output_name("layer with/space") == "layer_with_space"


def _make_shp(path: Path) -> None:
    """Build a fixture shapefile (polygons, EPSG:4326)."""

    geopandas = pytest.importorskip("geopandas")
    from shapely.geometry import Polygon

    geopandas.GeoDataFrame(
        {"id": [10], "label": ["p1"]},
        geometry=[
            Polygon([(2.5, 51.0), (3.5, 51.0), (3.5, 52.0), (2.5, 52.0)])
        ],
        crs="EPSG:4326",
    ).to_file(path, driver="ESRI Shapefile")


@unit
def test_open_shp_layer_streams_renamed_wkb_batches(tmp_path):
    pytest.importorskip("geopandas")

    shp = tmp_path / "fixture.shp"
    _make_shp(shp)
    with open_shp_layer(shp, "fixture", batch_size=1) as stream:
        assert stream.meta.crs == "EPSG:4326"
        assert stream.meta.geometry_type == "Polygon"
        assert stream.geometry_types == ["Polygon"]
        assert stream.geometry_column == "geometry"
        assert stream.schema.field("geometry").type == pa.binary()
        batches = list(stream.batches)
    assert [batch.num_rows for batch in batches] == [1]
    assert batches[0].schema.names == ["id", "label", "geometry"]


@unit
def test_shp_to_parquet_writes_one_file_per_shapefile(tmp_path):
    """Offline conversion check: one file per .shp with its own `geo` metadata."""

    pytest.importorskip("geopandas")
    import pyarrow.parquet as pq

    shp = tmp_path / "mangrove_eov_poly.shp"
    _make_shp(shp)

    response = shp_to_parquet(
        ShpToParquetRequest.model_validate(
            {"source_uri": str(shp), "local_output": str(tmp_path / "out")}
        )
    )
    assert response.status == "completed"
    assert response.layers == ["mangrove_eov_poly"]
    assert response.feature_counts == {"mangrove_eov_poly": 1}
    assert response.crs == {"mangrove_eov_poly": "EPSG:4326"}
    assert len(response.outputs) == 1
    for output in response.outputs:
        assert Path(output).is_file()

    parquet = pq.ParquetFile(response.outputs[0])
    assert parquet.metadata.num_rows == 1
    geo = json.loads(parquet.metadata.metadata[b"geo"])
    assert geo["version"] == "1.1.0"
    assert geo["primary_column"] == "geometry"
    column = geo["columns"]["geometry"]
    assert column["encoding"] == "WKB"
    assert column["geometry_types"] == ["Polygon"]
    assert column["crs"]["id"] == {"authority": "EPSG", "code": 4326}
    table = parquet.read()
    assert table.column("geometry").type == pa.binary()
    assert table.num_rows == 1


@unit
def test_shp_to_parquet_zip_selection_and_missing_layer(tmp_path):
    pytest.importorskip("geopandas")
    first = tmp_path / "a_one.shp"
    second = tmp_path / "b_two.shp"
    _make_shp(first)
    _make_shp(second)
    archive = tmp_path / "pkg.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for shp in (first, second):
            for member in shp.parent.glob(shp.stem + ".*"):
                zf.write(member, member.name)
    out = tmp_path / "out"

    response = shp_to_parquet(
        ShpToParquetRequest.model_validate(
            {"source_uri": str(archive), "local_output": str(out), "layers": ["b_two"]}
        )
    )
    assert response.layers == ["b_two"]
    assert len(response.outputs) == 1

    with pytest.raises(ValueError, match="Layers not found"):
        shp_to_parquet(
            ShpToParquetRequest.model_validate(
                {"source_uri": str(archive), "local_output": str(out / "x"), "layers": ["nope"]}
            )
        )


@unit
def test_shp_to_parquet_overwrite_and_conflict(tmp_path):
    pytest.importorskip("geopandas")
    shp = tmp_path / "fixture.shp"
    _make_shp(shp)
    target = tmp_path / "out"
    request = ShpToParquetRequest.model_validate(
        {"source_uri": str(shp), "local_output": str(target)}
    )
    shp_to_parquet(request)
    with pytest.raises(FileExistsError, match="overwrite=false"):
        shp_to_parquet(request)
    response = shp_to_parquet(
        ShpToParquetRequest.model_validate(
            {"source_uri": str(shp), "local_output": str(target), "overwrite": True}
        )
    )
    assert response.status == "completed"


@unit
def test_shp_to_parquet_uploads_outputs_to_s3_target(tmp_path, monkeypatch):
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
        "ept.features.shp_to_parquet.service.s3_url_to_fs",
        lambda uri, storage_options: (fake, uri.removeprefix("s3://")),
    )
    monkeypatch.setattr("ept.features.shp_to_parquet.service.put_to_s3", fake.put)

    shp = tmp_path / "fixture.shp"
    _make_shp(shp)
    response = shp_to_parquet(
        ShpToParquetRequest.model_validate(
            {
                "source_uri": str(shp),
                "bucket": "bucket",
                "target": "geo/",
                **_credentials(),
            }
        )
    )

    assert response.target_uri == "s3://bucket/geo"
    assert response.outputs == ["s3://bucket/geo/fixture.parquet"]
    assert [(Path(source).name, remote, overwrite) for source, remote, overwrite in fake.calls] == [
        ("fixture.parquet", "bucket/geo/fixture.parquet", False),
    ]


@unit
def test_shp_to_parquet_empty_shapefile(tmp_path):
    """A zero-feature shapefile still produces a valid, correctly-typed parquet file."""

    pytest.importorskip("geopandas")
    import pyarrow.parquet as pq
    import geopandas as gpd

    shp = tmp_path / "empty.shp"
    gpd.GeoDataFrame(
        {"id": []}, geometry=gpd.GeoSeries([], crs="EPSG:4326")
    ).to_file(shp, driver="ESRI Shapefile", geometry_type="Point")
    response = shp_to_parquet(
        ShpToParquetRequest.model_validate(
            {"source_uri": str(shp), "local_output": str(tmp_path / "out")}
        )
    )
    assert response.feature_counts == {"empty": 0}
    parquet = pq.ParquetFile(response.outputs[0])
    assert parquet.metadata.num_rows == 0
    geo = json.loads(parquet.metadata.metadata[b"geo"])
    assert geo["columns"]["geometry"]["geometry_types"] == ["Point"]


@integration
def test_shp_to_parquet_api_route_converts(tmp_path):
    """Full request path: bearer-auth-gated route through run_in_threadpool."""

    pytest.importorskip("geopandas")
    from fastapi.testclient import TestClient

    from ept.api.app import create_app
    from ept.api.dependencies import require_edito_bearer_auth
    from ept.infrastructure.services.edito_auth import EditoBearerAuth

    shp = tmp_path / "fixture.shp"
    _make_shp(shp)

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
            "/v1/transform/shp-to-parquet",
            json={"source_uri": str(shp), "local_output": str(tmp_path / "out")},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["layers"] == ["fixture"]
    assert all(Path(output).is_file() for output in body["outputs"])
