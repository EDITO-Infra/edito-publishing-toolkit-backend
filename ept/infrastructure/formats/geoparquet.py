"""Primitive GeoParquet writer: stream Arrow record batches to one Parquet file."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

GEOPARQUET_VERSION = "1.1.0"


def write_geoparquet(
    batches: Iterable[pa.RecordBatch],
    schema: pa.Schema,
    path: Path,
    *,
    geometry_column: str | None,
    crs_projjson: dict | None,
    geometry_types: list[str] | None,
) -> int:
    """Write record batches to one Parquet file with a GeoParquet `geo` footer.

    Batches stream straight to disk (ZSTD); only one batch is ever in memory.
    The `geo` metadata is built up front from what is known before reading:
    WKB encoding, geometry types, and the CRS as PROJJSON. The optional bbox
    key is omitted — a streaming writer cannot amend the footer after the data
    is known.
    honey: no bbox; add a second pass over the written file if spatial-filter
    pushdown ever matters.

    Returns the number of rows written.
    """

    geo = {
        "version": GEOPARQUET_VERSION,
        "primary_column": geometry_column,
        "columns": {
            geometry_column: {
                "encoding": "WKB",
                "geometry_types": geometry_types,
                "crs": crs_projjson,
            }
        },
    }
    metadata = dict(schema.metadata or {})
    metadata[b"geo"] = json.dumps(geo).encode("utf-8")
    writer = pq.ParquetWriter(path, schema.with_metadata(metadata), compression="zstd")
    rows = 0
    try:
        for batch in batches:
            writer.write_batch(batch)
            rows += batch.num_rows
    finally:
        writer.close()
    return rows


def crs_projjson_from_field(field: pa.Field) -> dict | None:
    """PROJJSON CRS embedded in pyogrio's geoarrow.wkb extension metadata.

    Returns None (the spec's OGC:CRS84 default) when the field carries no
    embedded CRS.
    """

    metadata = field.metadata or {}
    if metadata.get(b"ARROW:extension:name") != b"geoarrow.wkb":
        return None
    extension = json.loads(metadata.get(b"ARROW:extension:metadata", b"{}"))
    crs = extension.get("crs")
    return crs if isinstance(crs, dict) else None
