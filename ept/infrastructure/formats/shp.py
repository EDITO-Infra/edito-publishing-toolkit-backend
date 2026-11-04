"""ESRI shapefile primitives: stage the source, locate .shp files, stream one as Arrow batches.

Unlike file geodatabases, shapefiles have no single marker table: every
``*.shp`` found in an archive is a layer source (with its sidecar files).
Remote sources must be ``.zip`` — a bare ``.shp`` URL is unusable because
its sidecar files (``.shx``/``.dbf``/``.prj``) would not be fetched.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyogrio

from ept.infrastructure.formats.geoparquet import crs_projjson_from_field
from ept.infrastructure.storage.http import download_url
from ept.infrastructure.storage.s3 import is_http_url

# Keeps the feature isolated from the gdb module: an identical (driver-generic,
# pyogrio-based) copy of the streaming wrapper lives in formats/gdb.py; the two
# features evolve independently on top of pyogrio.
GEOMETRY_COLUMN = "geometry"

# Rows per Arrow batch when streaming a shapefile.
BATCH_SIZE = 65_536


@dataclass(frozen=True)
class ShpLayerMeta:
    """Static description of one shapefile layer."""

    layer: str
    fields: list[str]
    geometry_name: str | None
    geometry_type: str | None  # GDAL declaration, e.g. 'Point', 'Point Z', 'MultiPolygon'.
    crs: str | None  # Authority label, e.g. 'EPSG:4326'; None without a usable .prj.


@dataclass(frozen=True)
class ShpLayerStream:
    """Chunked read of one shapefile as Arrow record batches.

    Batches must be consumed while the context manager is open. The geometry
    column is renamed to ``geometry`` and reduced to plain WKB binary
    (extension metadata dropped). ``schema`` always reflects that shape, even
    for files with zero features.
    """

    meta: ShpLayerMeta
    schema: pa.Schema
    batches: Iterator[pa.RecordBatch]
    crs_projjson: dict | None  # CRS as PROJJSON, embedded in the source field metadata.

    @property
    def geometry_column(self) -> str | None:
        """Name of the geometry column, or None for non-spatial shapefiles."""
        return GEOMETRY_COLUMN if GEOMETRY_COLUMN in self.schema.names else None

    @property
    def geometry_types(self) -> list[str] | None:
        """GeoParquet geometry_types entry, or None when undeclared/mixed."""
        geometry_type = self.meta.geometry_type
        return [geometry_type] if geometry_type and geometry_type != "Unknown" else None


def stage_shp_sources(source: str | Path, workdir: Path) -> list[Path]:
    """Stage a shapefile source and return every ``.shp`` file it contains.

    Accepts a local ``.shp`` file, a local ``.zip`` archive containing one or
    more, or an HTTP(S) URL of such a ``.zip`` (downloaded into ``workdir``).
    Raises FileNotFoundError/ValueError for anything else.
    """

    if isinstance(source, str) and is_http_url(source):
        if not source.split("?", 1)[0].lower().endswith(".zip"):
            raise ValueError(
                "A remote shapefile source must be a .zip archive: a bare .shp "
                "URL is unusable because its sidecar files (.shx/.dbf/.prj) "
                "would not be available."
            )
        source = download_url(source, workdir / "source.zip")
    source = Path(source)
    if source.suffix.lower() == ".zip":
        if not source.is_file():
            raise FileNotFoundError(f"source_uri not found: {source}")
        return shp_files_from_zip(source, workdir / "extracted")
    if source.is_file() and source.suffix.lower() == ".shp":
        return [source]
    raise ValueError(
        f"'{source}' is not a shapefile source: provide a local .shp file, "
        "a .zip archive containing .shp files, or an HTTP(S) URL of such a .zip."
    )


def shp_files_from_zip(archive: Path, dest: Path) -> list[Path]:
    """Extract a zip and return every ``.shp`` file inside (at least one required)."""

    dest_resolved = dest.resolve()
    try:
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                target = (dest / member.filename).resolve()
                if not target.is_relative_to(dest_resolved):
                    raise ValueError(
                        f"Zip entry escapes extraction directory: {member.filename}"
                    )
            zf.extractall(dest)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Source is not a readable zip archive: {archive}") from exc
    shp_files = sorted(dest.rglob("*.shp"))
    if not shp_files:
        raise ValueError(f"Zip archive contains no .shp shapefiles: {archive}")
    return shp_files


def list_shp_layers(shp_file: Path) -> list[str]:
    """Layer names of one shapefile via OGR auto-detection (a single-element list)."""

    return [name for name, _geometry_type in pyogrio.list_layers(shp_file)]


@contextmanager
def open_shp_layer(
    shp_file: Path, layer: str, batch_size: int = BATCH_SIZE
) -> Iterator[ShpLayerStream]:
    """Stream one shapefile as Arrow record batches (see ShpLayerStream)."""

    with pyogrio.open_arrow(shp_file, layer=layer, batch_size=batch_size) as opened:
        meta, stream = opened
        geometry_type = meta["geometry_type"] or None
        fields = list(meta["fields"])

        reader = pa.RecordBatchReader.from_stream(stream)

        first = next(reader, None)
        if first is None:
            # Zero-feature shapefile: open_arrow yields no batch, so take the
            # schema from a full (empty) read instead.
            _meta, table = pyogrio.read_arrow(shp_file, layer=layer)
            source_schema = table.schema
            batches: Iterator[pa.RecordBatch] = iter(())
        else:
            source_schema = first.schema

        # The ESRI Shapefile driver reports no geometry field name; pyogrio
        # emits the WKB as a trailing "wkb_geometry" column instead. Resolve
        # the actual column from the schema before renaming.
        geometry_name = meta["geometry_name"] or None
        if geometry_name and geometry_name not in source_schema.names:
            geometry_name = None
        if geometry_name is None and "wkb_geometry" in source_schema.names:
            geometry_name = "wkb_geometry"
        if geometry_name and GEOMETRY_COLUMN in fields:
            raise ValueError(
                f"Shapefile '{layer}' has a field clashing with the output column '{GEOMETRY_COLUMN}'."
            )

        def renamed_schema(schema: pa.Schema) -> pa.Schema:
            if geometry_name is None:
                return schema
            index = schema.get_field_index(geometry_name)
            return schema.set(index, pa.field(GEOMETRY_COLUMN, pa.binary()))

        def renamed(batch: pa.RecordBatch) -> pa.RecordBatch:
            if geometry_name is None:
                return batch
            index = batch.schema.get_field_index(geometry_name)
            return batch.set_column(
                index, pa.field(GEOMETRY_COLUMN, pa.binary()), batch.column(index)
            )

        if first is not None:
            batches = (renamed(batch) for batch in _chain(first, reader))

        yield ShpLayerStream(
            meta=ShpLayerMeta(
                layer=layer,
                fields=fields,
                geometry_name=geometry_name,
                geometry_type=geometry_type,
                crs=meta["crs"],
            ),
            schema=renamed_schema(source_schema),
            batches=batches,
            crs_projjson=(
                crs_projjson_from_field(source_schema.field(geometry_name))
                if geometry_name
                else None
            ),
        )


def _chain(first: pa.RecordBatch, rest: Iterator[pa.RecordBatch]) -> Iterator[pa.RecordBatch]:
    yield first
    yield from rest
