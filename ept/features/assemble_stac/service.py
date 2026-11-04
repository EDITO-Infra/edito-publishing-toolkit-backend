"""STAC assemble use case: loose JSON files into a valid, linked STAC tree."""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from ept.features.assemble_stac.assemble import (
    StacObject,
    assemble_tree,
    export_tree,
)
from ept.features.assemble_stac.models import (
    AssembleStacObject,
    AssembleStacRequest,
    AssembleStacResponse,
)
from ept.features.assemble_stac.sources import fetch_json_dir
from ept.infrastructure.formats.stac import (
    StacObjectType,
    get_stac_object_type,
)
from ept.infrastructure.formats.stac.catalog import validate_catalog
from ept.infrastructure.formats.stac.collection import validate_collection
from ept.infrastructure.formats.stac.item import validate_item
from ept.infrastructure.storage.s3 import is_s3_uri, put_to_s3, s3_connect

logger = logging.getLogger(__name__)

__all__ = ["assemble_stac"]


def assemble_stac(request: AssembleStacRequest, default_bucket: str | None = None) -> AssembleStacResponse:
    """Assemble the STAC JSON files under ``source_uri`` and export the tree.

    An S3 or zip source is materialized into a staging directory; an S3
    output (``s3://<bucket>/<target>``, the bucket defaulting to the caller's
    personal one) is staged locally, uploaded via the shared S3 storage
    layer, and the staging copy removed. ``local_output`` exports to that
    directory on the EPT filesystem instead.
    """
    if request.local_output:
        target_uri = request.local_output
        local_dir = Path(request.local_output)
        s3_output = False
    else:
        bucket = request.bucket or default_bucket
        if not bucket:
            raise ValueError(
                "bucket is required: the bearer token carries no username from which to "
                "derive the default oidc-<username> bucket."
            )
        target_uri = f"s3://{bucket}/{request.target}"
        local_dir = None
        s3_output = True

    with tempfile.TemporaryDirectory(prefix="ept-assemble-stac-") as workdir_name:
        workdir = Path(workdir_name)
        source_dir = fetch_json_dir(request.source_uri, workdir, request)
        objects, parse_warnings = _load_json_objects(source_dir, request.source_uri)
        root, assemble_warnings = assemble_tree(objects)

        staged_dir = workdir / "stac" if s3_output else local_dir
        export_warnings: list[str] = []
        report = export_tree(root, str(staged_dir), overwrite=request.overwrite, warnings=export_warnings)

        uploaded: list[str] = []
        if s3_output:
            uploaded = _upload_export(staged_dir, target_uri, request)

    return AssembleStacResponse(
        source_uri=request.source_uri,
        target_uri=target_uri,
        root_path=report.root_path,
        objects=[
            AssembleStacObject(
                path=e.path,
                object_type=e.object_type,
                object_id=e.object_id,
                validation_issues=_validate_object(e),
            )
            for e in report.exported
        ],
        uploaded=uploaded,
        warnings=parse_warnings + assemble_warnings + export_warnings,
    )


def _upload_export(export_dir: Path, target_uri: str, request: AssembleStacRequest) -> list[str]:
    """Upload every exported file under the ``s3://bucket/prefix`` target."""
    bucket, _, prefix = target_uri[len("s3://"):].partition("/")
    prefix = prefix.strip("/")
    # The request model guarantees complete credentials whenever an s3://
    # source is involved; s3_connect falls back to the S3_*/AWS_*
    # environment for anything left unset.
    fs = s3_connect(request)
    uploaded: list[str] = []
    for path in sorted(export_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(export_dir).as_posix()
        remote = f"{bucket}/{prefix}/{rel}" if prefix else f"{bucket}/{rel}"
        put_to_s3(fs, path, remote, overwrite=request.overwrite)
        uploaded.append(f"s3://{remote}")
    return uploaded


def _load_json_objects(source_dir: Path, source_uri: str) -> tuple[list[StacObject], list[str]]:
    """Parse every ``*.json`` file under ``source_dir`` into StacObjects."""
    objects: list[StacObject] = []
    warnings: list[str] = []
    for path in sorted(source_dir.rglob("*.json")):
        rel_path = path.relative_to(source_dir).as_posix()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"{rel_path}: skipped unreadable JSON ({exc}).")
            continue
        if not isinstance(data, dict):
            warnings.append(f"{rel_path}: skipped, top-level JSON value is not an object.")
            continue
        objects.append(StacObject(path=rel_path, data=data))
    if not objects:
        raise ValueError(f"No JSON files found under source: {source_uri}")
    return objects, warnings


def _validate_object(exported) -> list[str]:
    """Schema-validate one exported object; issues are reported, never fatal."""
    data = exported.data
    try:
        match get_stac_object_type(data):
            case StacObjectType.CATALOG:
                validate_catalog(data)
            case StacObjectType.COLLECTION:
                validate_collection(data)
            case StacObjectType.ITEM:
                validate_item(data)
            case _:
                return ["not a STAC object (no usable 'type')."]
    except ValueError as exc:
        return [str(exc)]
    except Exception as exc:  # stac-validator crashes on malformed objects
        return [f"STAC schema validation crashed: {exc}"]
    return []
