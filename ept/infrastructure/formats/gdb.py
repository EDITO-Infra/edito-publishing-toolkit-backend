"""ESRI file geodatabase primitives: open, list layers, stream one layer in chunks."""

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

# ESRI GDB_Items system table present in every file geodatabase; used to
# validate that a directory really is one.
GDB_ITEMS_TABLE = "a00000004.gdbtable"

# Output geometry column name; clashes with a same-named source field are rejected.
GEOMETRY_COLUMN = "geometry"

# Rows per Arrow batch when streaming a layer.
BATCH_SIZE = 65_536


@dataclass(frozen=True)
class GdbLayerMeta:
    """Static description of one geodatabase layer."""

    layer: str
    fields: list[str]
    geometry_name: str | None
    geometry_type: str | None  # GDAL declaration, e.g. 'Point', 'Point Z', 'MultiPolygon'.
    crs: str | None  # Authority label, e.g. 'EPSG:31370'.


@dataclass(frozen=True)
class GdbLayerStream:
    """Chunked read of one geodatabase layer as Arrow record batches.

    Batches must be consumed while the context manager is open. The geometry
    column (``SHAPE`` in OpenFileGDB) is renamed to ``geometry`` and reduced to
    plain WKB binary (extension metadata dropped). ``schema`` always reflects
    that shape, even for layers with zero features.
    """

    meta: GdbLayerMeta
    schema: pa.Schema
    batches: Iterator[pa.RecordBatch]
    crs_projjson: dict | None  # CRS as PROJJSON, embedded in the source field metadata.

    @property
    def geometry_column(self) -> str | None:
        """Name of the geometry column, or None for non-spatial tables."""
        return GEOMETRY_COLUMN if GEOMETRY_COLUMN in self.schema.names else None

    @property
    def geometry_types(self) -> list[str] | None:
        """GeoParquet geometry_types entry, or None when undeclared/mixed."""
        geometry_type = self.meta.geometry_type
        return [geometry_type] if geometry_type and geometry_type != "Unknown" else None


def open_gdb(source: str | Path, workdir: Path) -> Path:
    """Validate a local ``.gdb`` directory or extract one from a local or remote ``.zip``.

    An HTTP(S) URL of a ``.zip`` is staged into ``workdir`` first. Returns the
    geodatabase directory; raises FileNotFoundError/ValueError for anything else.
    """

    if isinstance(source, str) and is_http_url(source):
        source = download_url(source, workdir / "source.zip")
    source = Path(source)
    if source.suffix.lower() == ".zip":
        if not source.is_file():
            raise FileNotFoundError(f"source_uri not found: {source}")
        return gdb_from_zip(source, workdir / "extracted")
    return require_gdb(source, source)


def gdb_from_zip(archive: Path, dest: Path) -> Path:
    """Extract a zip that must contain exactly one file geodatabase; return its directory."""

    dest.resolve()
    with zipfile.ZipFile(archive) as zf:
        dest_resolved = dest.resolve()
        for member in zf.infolist():
            target = (dest / member.filename).resolve()
            if not target.is_relative_to(dest_resolved):
                raise ValueError(f"Zip entry escapes extraction directory: {member.filename}")
        zf.extractall(dest)
    gdb_dirs = [
        path for path in dest.rglob("*.gdb") if path.is_dir() and (path / GDB_ITEMS_TABLE).is_file()
    ]
    if len(gdb_dirs) != 1:
        raise ValueError(
            f"Zip must contain exactly one file geodatabase (*.gdb), found {len(gdb_dirs)}."
        )
    return gdb_dirs[0]


def list_gdb_layers(gdb_dir: Path) -> list[str]:
    """Layer names (feature classes *and* standalone tables) via the OpenFileGDB driver."""

    return [name for name, _geometry_type in pyogrio.list_layers(gdb_dir)]


@contextmanager
def open_gdb_layer(
    gdb_dir: Path, layer: str, batch_size: int = BATCH_SIZE
) -> Iterator[GdbLayerStream]:
    """Stream one geodatabase layer as Arrow record batches (see GdbLayerStream)."""

    with pyogrio.open_arrow(gdb_dir, layer=layer, batch_size=batch_size) as opened:
        meta, stream = opened
        geometry_name = meta["geometry_name"] or None
        geometry_type = meta["geometry_type"] or None
        if geometry_type is None:
            geometry_name = None
        fields = list(meta["fields"])
        if geometry_name and GEOMETRY_COLUMN in fields:
            raise ValueError(
                f"Layer '{layer}' has a field clashing with the output column '{GEOMETRY_COLUMN}'."
            )

        reader = pa.RecordBatchReader.from_stream(stream)

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

        first = next(reader, None)
        if first is None:
            # Zero-feature layer: open_arrow yields no batch, so take the
            # schema from a full (empty) read instead.
            _meta, table = pyogrio.read_arrow(gdb_dir, layer=layer)
            source_schema = table.schema
            batches: Iterator[pa.RecordBatch] = iter(())
        else:
            source_schema = first.schema
            batches = (renamed(batch) for batch in _chain(first, reader))

        yield GdbLayerStream(
            meta=GdbLayerMeta(
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


def require_gdb(gdb_dir: Path, origin: Path | str) -> Path:
    """Return gdb_dir if it is a valid file geodatabase, else raise."""

    if not (gdb_dir / GDB_ITEMS_TABLE).is_file():
        raise ValueError(
            f"'{origin}' is not a valid ESRI file geodatabase (missing {GDB_ITEMS_TABLE})."
        )
    return gdb_dir


def _chain(first: pa.RecordBatch, rest: Iterator[pa.RecordBatch]) -> Iterator[pa.RecordBatch]:
    yield first
    yield from rest
