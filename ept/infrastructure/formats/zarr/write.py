"""Public Zarr format primitives."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

from ept.infrastructure.storage.s3 import is_s3_uri

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import xarray as xr


@dataclass(frozen=True)
class ZarrWriteOptions:
    """Options for writing a Zarr store."""

    consolidated: bool | None = True
    overwrite: bool = False
    zarr_format: Literal[2, 3] | None = None


@dataclass(frozen=True)
class ZarrWriteResult:
    """Result of writing a Zarr store."""

    target_uri: str


def create_zarr_store(
    uri: str,
    *,
    storage_options: dict[str, Any] | None = None,
) -> str:
    """Create or prepare a local or remote Zarr store target."""

    if not is_s3_uri(uri):
        logger.info("Preparing local Zarr target parent uri=%s", uri)
        Path(uri).parent.mkdir(parents=True, exist_ok=True)
    else:
        logger.info("Preparing remote Zarr target uri=%s", uri)
    return uri


def write_zarr(
    dataset: xr.Dataset,
    target: Any,
    *,
    target_uri: str,
    options: ZarrWriteOptions | None = None,
    storage_options: dict[str, Any] | None = None,
) -> ZarrWriteResult:
    """Write an xarray dataset to a local or remote Zarr store.

    ``storage_options`` must already be resolved (e.g. via
    :func:`ept.infrastructure.storage.s3.resolve_s3_storage_options`) when the
    target is an S3 URI; pass None for local targets.
    """

    options = options or ZarrWriteOptions()
    logger.info(
        "Starting Zarr write target=%s target_uri=%s overwrite=%s consolidated=%s requested_zarr_format=%s%s",
        target,
        target_uri,
        options.overwrite,
        options.consolidated,
        options.zarr_format,
        _format_dataset_size(dataset),
    )
    if _can_write_local_zarr_v2(dataset, target, options, storage_options):
        try:
            _write_local_zarr_v2(dataset, Path(target), options=options)
        except Exception as exc:
            raise RuntimeError(f"Failed to write Zarr store to {target_uri}") from exc
        return ZarrWriteResult(target_uri=target_uri)

    target_is_s3 = is_s3_uri(str(target))
    write_storage_options = storage_options if target_is_s3 else None
    write_mode = "w" if options.overwrite else "w-"
    zarr_format = 2 if target_is_s3 and options.zarr_format is None else options.zarr_format
    if target_is_s3 and options.overwrite:
        _delete_s3_prefix_objects(str(target), write_storage_options or {})
        write_mode = "w-"
    to_zarr_kwargs: dict[str, Any] = {}
    if target_is_s3:
        to_zarr_kwargs["write_empty_chunks"] = True
    logger.info(
        "Calling xarray.to_zarr target=%s mode=%s zarr_format=%s consolidated=%s target_is_s3=%s extra_kwargs=%s",
        target,
        write_mode,
        zarr_format,
        options.consolidated,
        target_is_s3,
        sorted(to_zarr_kwargs),
    )
    try:
        dataset.to_zarr(
            target,
            mode=write_mode,
            consolidated=options.consolidated,
            storage_options=write_storage_options,
            zarr_format=zarr_format,
            **to_zarr_kwargs,
        )
    except Exception as exc:
        logger.exception("Failed during xarray.to_zarr target=%s target_uri=%s", target, target_uri)
        raise RuntimeError(f"Failed to write Zarr store to {target_uri}") from exc

    logger.info("Finished Zarr write target_uri=%s", target_uri)
    return ZarrWriteResult(target_uri=target_uri)


def _can_write_local_zarr_v2(
    dataset: xr.Dataset,
    target: Any,
    options: ZarrWriteOptions,
    storage_options: dict[str, Any] | None,
) -> bool:
    if storage_options or is_s3_uri(str(target)):
        return False
    if options.zarr_format not in (None, 2):
        return False
    try:
        import xarray as xr
    except ImportError:
        return False
    return isinstance(dataset, xr.Dataset) and isinstance(target, str)


def _write_local_zarr_v2(
    dataset: xr.Dataset,
    target: Path,
    *,
    options: ZarrWriteOptions,
) -> None:
    if target.exists() or target.is_symlink():
        if not options.overwrite:
            raise FileExistsError(f"Target already exists: {target}")
        logger.info("Removing existing local Zarr target path=%s", target)
        _remove_existing_local_target(target)
    target.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Writing local Zarr v2 store to %s with %d variables%s",
        target,
        len(dataset.variables),
        _format_dataset_size(dataset),
    )
    metadata: dict[str, Any] = {
        ".zgroup": {"zarr_format": 2},
        ".zattrs": _json_attrs(dataset.attrs),
    }
    _write_json(target / ".zgroup", metadata[".zgroup"])
    _write_json(target / ".zattrs", metadata[".zattrs"])

    for name, variable in dataset.variables.items():
        array_path = target / str(name)
        array_path.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Writing Zarr variable %s dims=%s shape=%s dtype=%s%s",
            name,
            variable.dims,
            variable.shape,
            variable.dtype,
            _format_variable_size(variable),
        )
        data = variable.values
        chunks = tuple(max(1, size) for size in data.shape) or ()
        fill_value = _json_scalar(data.dtype.type()) if data.size else None
        zarray = {
            "zarr_format": 2,
            "shape": list(data.shape),
            "chunks": list(chunks),
            "dtype": data.dtype.str,
            "compressor": None,
            "fill_value": fill_value,
            "order": "C",
            "filters": None,
        }
        zattrs = {
            **_json_attrs(variable.attrs),
            "_ARRAY_DIMENSIONS": list(variable.dims),
        }
        metadata[f"{name}/.zarray"] = zarray
        metadata[f"{name}/.zattrs"] = zattrs
        _write_json(array_path / ".zarray", zarray)
        _write_json(array_path / ".zattrs", zattrs)
        _write_chunk(array_path, data)

    if options.consolidated:
        _write_json(
            target / ".zmetadata",
            {
                "zarr_consolidated_format": 1,
                "metadata": metadata,
            },
        )
    logger.info("Finished local Zarr v2 store at %s", target)


def _format_dataset_size(dataset: xr.Dataset) -> str:
    try:
        nbytes = dataset.nbytes
    except Exception:
        return ""
    return f" (estimated {nbytes / 1024 / 1024:.2f} MiB)"


def _format_variable_size(variable: Any) -> str:
    try:
        nbytes = variable.nbytes
    except Exception:
        return ""
    return f" ({nbytes / 1024 / 1024:.2f} MiB)"


def _delete_s3_prefix_objects(uri: str, storage_options: dict[str, Any]) -> None:
    parsed = urlparse(uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket or not key:
        raise ValueError("S3 Zarr overwrite target must include a bucket and object prefix.")

    prefix = key.rstrip("/") + "/"
    client = _s3_client(storage_options)
    logger.info("Removing existing S3 Zarr objects under s3://%s/%s", bucket, prefix)

    continuation_token: str | None = None
    deleted_count = 0
    page_count = 0
    while True:
        list_kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if continuation_token:
            list_kwargs["ContinuationToken"] = continuation_token
        response = client.list_objects_v2(**list_kwargs)
        page_count += 1
        contents = response.get("Contents", [])
        logger.info(
            "Listed S3 Zarr overwrite page bucket=%s prefix=%s page=%d objects=%d truncated=%s",
            bucket,
            prefix,
            page_count,
            len(contents),
            bool(response.get("IsTruncated")),
        )
        deleted_count += _delete_s3_objects_batch(
            client, bucket, [item["Key"] for item in contents]
        )
        if not response.get("IsTruncated"):
            break
        continuation_token = response.get("NextContinuationToken")

    # The store-path marker object (the prefix without its trailing slash)
    # is not matched by the listing above.
    client.delete_object(Bucket=bucket, Key=key.rstrip("/"))

    # Overwrite hands off to xarray with zarr mode 'w-' (create, fail if the
    # store holds data). An object written by a concurrent request after the
    # listing above would survive the cleanup and surface as a cryptic
    # FileExistsError deep inside zarr — verify emptiness here and name the
    # likely cause instead.
    leftover = client.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
    if leftover:
        raise RuntimeError(
            f"S3 Zarr overwrite target s3://{bucket}/{prefix} is still not empty "
            f"after deleting {deleted_count} objects ({len(leftover)} remain). "
            "Another process is likely writing to the same target right now — "
            "wait for it to finish and retry; do not run overlapping conversions "
            "to one target."
        )

    logger.info(
        "Finished S3 Zarr overwrite cleanup bucket=%s prefix=%s deleted_objects=%d pages=%d",
        bucket,
        prefix,
        deleted_count,
        page_count,
    )


def _delete_s3_objects_batch(client: Any, bucket: str, keys: list[str]) -> int:
    """Delete up to 1000 objects per request; returns the number deleted.

    One delete_object call per key makes large overwrites take minutes of
    round trips — a long window for a concurrent writer to re-create objects
    in. The batched API deletes 1000 keys per call instead.
    """
    deleted = 0
    for start in range(0, len(keys), 1000):
        batch = keys[start : start + 1000]
        response = client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
        )
        deleted += len(batch) - len(response.get("Errors", []))
        for error in response.get("Errors", []):
            logger.warning(
                "S3 batch delete failed bucket=%s key=%s code=%s message=%s",
                bucket,
                error["Key"],
                error["Code"],
                error["Message"],
            )
    return deleted


def _s3_client(storage_options: dict[str, Any]) -> Any:
    import boto3
    from botocore.session import Session as BotocoreSession

    logger.info(
        "Creating boto3 S3 client endpoint=%s region=%s has_session_token=%s",
        storage_options.get("endpoint_url"),
        (storage_options.get("client_kwargs") or {}).get("region_name"),
        bool(storage_options.get("token")),
    )
    client_kwargs: dict[str, Any] = {
        "aws_access_key_id": storage_options.get("key"),
        "aws_secret_access_key": storage_options.get("secret"),
        "aws_session_token": storage_options.get("token"),
        "endpoint_url": storage_options.get("endpoint_url"),
    }
    client_kwargs.update(storage_options.get("client_kwargs") or {})
    # Request-scoped clients must not consult the host's ambient AWS profile
    # chain (AWS_PROFILE / AWS_DEFAULT_PROFILE): a profile missing from
    # ~/.aws/config aborts session construction with ProfileNotFound even
    # when explicit credentials are given. Overriding the profile variable's
    # (config_file, env_var, default, conversion) chain removes the env
    # lookup entirely, without mutating the process environment.
    botocore_session = BotocoreSession(
        session_vars={"profile": (None, None, None, None)}
    )
    session = boto3.Session(botocore_session=botocore_session)
    client = session.client(
        "s3",
        **{key: value for key, value in client_kwargs.items() if value is not None},
    )
    # botocore >= 1.36 sends DeleteObjects with an x-amz-checksum-crc32 header
    # instead of the legacy Content-MD5; MinIO rejects that with
    # MissingContentMD5. Compute the Content-MD5 ourselves before signing —
    # verified against a request-capturing stub that the header matches the
    # serialized body and survives SigV4 signing.
    client.meta.events.register("before-sign.s3.DeleteObjects", _add_content_md5)
    return client


def _add_content_md5(request: Any, **kwargs: Any) -> None:
    """Add the Content-MD5 header MinIO requires on batch deletes."""
    body = request.body
    if isinstance(body, (bytes, bytearray)):
        digest = hashlib.md5(bytes(body), usedforsecurity=False).digest()
        request.headers["Content-MD5"] = base64.b64encode(digest).decode("ascii")


def _remove_existing_local_target(target: Path) -> None:
    try:
        if target.is_symlink() or target.is_file():
            logger.info("Removing existing local Zarr file/symlink path=%s", target)
            target.unlink()
            return
        logger.info("Removing existing local Zarr directory path=%s", target)
        shutil.rmtree(target, onexc=_make_writable_and_retry)
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot remove existing Zarr target {target}. "
            "Check that the directory and its contents are owned by the current user and are writable."
        ) from exc


def _make_writable_and_retry(function: Any, path: str, excinfo: BaseException) -> None:
    if not isinstance(excinfo, PermissionError):
        raise excinfo
    logger.warning("Retrying removal after making path writable path=%s", path)
    _make_path_writable(Path(path).parent)
    _make_path_writable(Path(path))
    function(path)


def _make_path_writable(path: Path) -> None:
    os.chmod(path, path.stat().st_mode | 0o700)


def _write_chunk(array_path: Path, data: Any) -> None:
    import numpy as np

    array = np.ascontiguousarray(data)
    chunk_key = ".".join("0" for _ in array.shape) or "0"
    logger.debug("Writing local Zarr chunk path=%s chunk_key=%s bytes=%d", array_path, chunk_key, array.nbytes)
    (array_path / chunk_key).write_bytes(array.tobytes(order="C"))


def _write_json(path: Path, value: dict[str, Any]) -> None:
    logger.debug("Writing local Zarr JSON metadata path=%s", path)
    path.write_text(json.dumps(_json_scalar(value), indent=2) + "\n", encoding="utf-8")


def _json_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    return _json_scalar(attrs)


def _json_scalar(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_scalar(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_scalar(item) for item in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    if hasattr(value, "tolist"):
        return _json_scalar(value.tolist())
    if hasattr(value, "item"):
        return _json_scalar(value.item())
    return value
