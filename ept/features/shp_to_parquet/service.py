"""Shapefile → GeoParquet use case, over the OGR primitives in ept.infrastructure.formats."""

import logging
import re
import shutil
from pathlib import Path
from uuid import uuid4

from ept.infrastructure.formats.geoparquet import write_geoparquet
from ept.infrastructure.formats.shp import list_shp_layers, open_shp_layer, stage_shp_sources
from ept.infrastructure.storage.s3 import (
    full_remote_url,
    put_to_s3,
    resolve_s3_storage_options,
    s3_url_to_fs,
)

from .models import ShpToParquetRequest, ShpToParquetResponse

logger = logging.getLogger(__name__)

WORKSPACE_DIR = Path("workspace")


def shp_to_parquet(request: ShpToParquetRequest, default_bucket: str | None = None) -> ShpToParquetResponse:
    """Convert one or more shapefiles to GeoParquet — one file per ``.shp``.

    Stages under ``./workspace/<uuid>`` (removed afterwards). Pipeline:
    ``stage_shp_sources`` (downloads/stages, extracts a .zip) → per ``.shp``
    file ``open_shp_layer`` streaming Arrow batches in chunks →
    ``write_geoparquet`` streaming to disk (GeoParquet 1.1 ``geo`` footer,
    WKB, ZSTD) → deliver the staged outputs to the S3 target
    (``s3://<bucket>/<target>/<stem>.parquet``, the bucket defaulting to the
    caller's personal one) or, when ``local_output`` is set, copy them into
    that directory on the EPT filesystem. Only one file's batch is ever held
    in memory, so multi-GB shapefiles do not accumulate. Each output file
    carries its own schema and CRS; no reprojection happens (that belongs
    upstream of EPT).
    """

    if request.local_output:
        local_target = Path(request.local_output)
        s3_prefix = None
        target_display = str(local_target)
    else:
        bucket = request.bucket or default_bucket
        if not bucket:
            raise ValueError(
                "bucket is required: the bearer token carries no username from which to "
                "derive the default oidc-<username> bucket."
            )
        local_target = None
        s3_prefix = f"s3://{bucket}/{request.target}"
        target_display = s3_prefix

    logger.info(
        "Starting shp_to_parquet source=%s target=%s overwrite=%s",
        request.source_uri,
        target_display,
        request.overwrite,
    )
    workdir = WORKSPACE_DIR / uuid4().hex
    workdir.mkdir(parents=True)
    try:
        shp_files = stage_shp_sources(request.source_uri, workdir)
        selected = _select_layers(shp_files, request.layers)

        output_dir = workdir / "output"
        output_dir.mkdir(parents=True)
        counts: dict[str, int] = {}
        crs: dict[str, str | None] = {}
        outputs: list[Path] | list[str] = []
        written: set[str] = set()
        for shp_file, layer in selected:
            with open_shp_layer(shp_file, layer) as stream:
                output_path = output_dir / f"{_output_name(layer)}.parquet"
                if output_path.name in written:
                    raise ValueError(
                        f"Shapefile stems collide after sanitization: '{layer}' → {output_path.name}"
                    )
                written.add(output_path.name)
                geometry_column = stream.geometry_column
                counts[layer] = write_geoparquet(
                    stream.batches,
                    stream.schema,
                    output_path,
                    geometry_column=geometry_column,
                    crs_projjson=stream.crs_projjson,
                    geometry_types=stream.geometry_types,
                )
                crs[layer] = stream.meta.crs if geometry_column else None
                outputs.append(output_path)

        if s3_prefix is not None:
            target_uri, outputs = _upload_outputs_to_s3(output_dir, s3_prefix, request)
        else:
            target = _write_local_output(output_dir, local_target, request.overwrite)
            target_uri = str(target)
            outputs = [target / output_path.name for output_path in outputs]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    logger.info(
        "Completed shp_to_parquet source=%s target=%s layers=%d files=%d",
        request.source_uri,
        target_uri,
        len(counts),
        len(outputs),
    )
    return ShpToParquetResponse(
        source_uri=request.source_uri,
        target_uri=target_uri,
        layers=[layer for _shp, layer in selected],
        feature_counts=counts,
        crs=crs,
        outputs=[str(output) for output in outputs],
        status="completed",
    )


def _select_layers(shp_files: list[Path], layers: list[str] | None) -> list[tuple[Path, str]]:
    """Pair every (or only the requested) shapefile with its OGR layer name (the stem)."""

    available = [(shp_file, stem) for shp_file in shp_files for stem in list_shp_layers(shp_file)]
    if not available:
        raise ValueError("No readable layers found among the shapefile sources.")
    if layers is None:
        return available
    stems = {stem for _shp, stem in available}
    missing = sorted(set(layers) - stems)
    if missing:
        raise ValueError(f"Layers not found among the shapefile sources: {', '.join(missing)}")
    return [(shp_file, layer) for shp_file, layer in available if layer in set(layers)]


def _upload_outputs_to_s3(output_dir: Path, target_prefix: str, request: ShpToParquetRequest) -> tuple[str, list[str]]:
    """Upload the staged per-layer files to one s3://bucket/prefix.

    Each file becomes one object at ``prefix/<name>.parquet`` via the
    infrastructure S3 primitives with request-scoped credentials.
    """

    fs, remote_prefix = s3_url_to_fs(
        target_prefix, storage_options=resolve_s3_storage_options(request)
    )
    uploaded: list[str] = []
    for output_path in sorted(output_dir.iterdir()):
        put_to_s3(
            fs,
            output_path,
            f"{remote_prefix}/{output_path.name}",
            overwrite=request.overwrite,
        )
        uploaded.append(full_remote_url(fs, f"{remote_prefix}/{output_path.name}"))
    return full_remote_url(fs, remote_prefix), uploaded


def _write_local_output(output_dir: Path, target: Path, overwrite: bool) -> Path:
    if target.exists():
        if not overwrite:
            raise FileExistsError(f"Target exists and overwrite=false: {target}")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(output_dir, target)
    return target


def _output_name(layer: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", layer)
