"""Basic primitives for S3-compatible object storage.

Connect once, then use plain functions against the returned filesystem::

    from ept.infrastructure.storage.s3 import (
        get_from_s3, ls_buckets, ls_folder, put_to_s3, rm_from_s3, s3_connect,
    )

    fs = s3_connect(params)  # S3ConnectionParameters from ept auth, a dict of
                             # {endpoint_url, access_key_id, secret_access_key,
                             #  session_token?}, or None to read S3_*/AWS_* env vars
    ls_buckets(fs)                              # ["my-bucket", ...]
    ls_folder(fs, "my-bucket/some/prefix")      # direct children; recursive=True for all keys
    put_to_s3(fs, "local.nc", "my-bucket/some/x.nc")
    get_from_s3(fs, "my-bucket/some/x.nc", "local.nc")
    rm_from_s3(fs, "my-bucket/some/prefix")     # one key or a whole prefix

All remote paths are plain ``bucket/key`` strings (no ``s3://`` scheme).

Upload orchestration (target resolution, transfer tuning, HTTP-source and
prefix handling) lives in the features that compose these primitives, e.g.
``ept.features.upload_s3``.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from s3fs import S3FileSystem

from ept.infrastructure.services.edito_auth import S3ConnectionParameters

logger = logging.getLogger(__name__)

S3_ACCESS_KEY_ID_ENV = "S3_ACCESS_KEY_ID"
S3_SECRET_KEY_ENV = "S3_SECRET_ACCESS_KEY"
S3_SESSION_TOKEN_ENV = "S3_SESSION_TOKEN"
S3_ENDPOINT_URL_ENV = "S3_ENDPOINT_URL"
S3_REGION_ENV = "S3_REGION"

# Canonical names are S3_*.
# AWS_* names are accepted as compatibility aliases for common S3 tooling.
S3_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    S3_ACCESS_KEY_ID_ENV: ("AWS_ACCESS_KEY_ID",),
    S3_SECRET_KEY_ENV: ("AWS_SECRET_ACCESS_KEY",),
    S3_SESSION_TOKEN_ENV: ("AWS_SESSION_TOKEN",),
    S3_ENDPOINT_URL_ENV: (
        "AWS_S3_ENDPOINT",
        "AWS_ENDPOINT_URL_S3",
        "AWS_ENDPOINT_URL",
        "S3_ENDPOINT",
    ),
    S3_REGION_ENV: (
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
    ),
}

# Default multipart tuning for put_to_s3. s3fs defaults (50 MiB parts x 10
# concurrent) keep single HTTP requests in flight for minutes; proxies in
# front of MinIO kill requests after ~60s with HTTP 499. Small parts + low
# concurrency keep each request short.
S3_PUT_CHUNK_BYTES = 20 * 2**20  # 20 MiB; S3 requires >=5 MiB per part
S3_PUT_MAX_CONCURRENCY = 8


def s3_connect(
    params: S3ConnectionParameters | dict[str, str] | None = None,
) -> S3FileSystem:
    """Connect to S3-compatible storage and return the filesystem handle.

    ``params`` may be an ``S3ConnectionParameters`` (as issued by the auth
    service), a plain dict of the same fields (e.g. built from user form
    input; it is validated the same way), or ``None`` to fall back to the
    S3_*/AWS_* environment variables. Credentials are never written to the
    environment. Connecting is lazy: no network traffic happens until the
    first operation.
    """

    if params is not None and not isinstance(params, S3ConnectionParameters):
        params = S3ConnectionParameters(**params)
    options = resolve_s3_storage_options(params)
    logger.info("Connecting to S3 endpoint=%s", options.get("endpoint_url"))
    return S3FileSystem(**options)


def is_s3_uri(uri: str) -> bool:
    return uri.lower().startswith("s3://")


def s3_endpoint_url(params: S3ConnectionParameters | None = None) -> str | None:
    """Endpoint URL from explicit params or the S3_*/AWS_* environment; None when unset.

    Unlike :func:`resolve_s3_storage_options`, this never requires credentials —
    use it when only the endpoint is needed (e.g. to render full https:// URLs).
    """

    return (params.endpoint_url if params else None) or _env_value(S3_ENDPOINT_URL_ENV)


def with_file_name(target: str, file_name: str) -> str:
    """Append the feature-decided file name to a target prefix.

    The caller's target names a folder/prefix; the feature owns the file name
    (e.g. ``<item_id>.json``, ``<nc-stem>.zarr``). A target whose last segment
    contains a ``.`` is treated as the caller's explicit file path/key and is
    returned unchanged.
    """

    target = target.rstrip("/")
    return target if "." in target.rsplit("/", 1)[-1] else f"{target}/{file_name}"


def is_http_url(uri: str) -> bool:
    parsed = urlsplit(uri)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def resolve_s3_storage_options(
    params: S3ConnectionParameters | None = None,
) -> dict[str, object]:
    """Build s3fs storage options from connection parameters with environment fallbacks.

    ``params`` (e.g. an ``S3ConnectionParameters``) wins per field; any field it
    leaves unset falls back to the S3_* environment variables and their AWS_*
    aliases. Credentials are only read from the environment, never written to it.
    """

    logger.info("Resolving S3 storage options params=%s", params is not None)
    # Params may carry explicit credentials per field (request models redeclare
    # them optional); an unset field falls back to the S3_* environment.
    options: dict[str, object] = {
        "key": (params.access_key_id if params else None) or _env_value(S3_ACCESS_KEY_ID_ENV),
        "secret": (params.secret_access_key if params else None) or _env_value(S3_SECRET_KEY_ENV),
        "endpoint_url": (params.endpoint_url if params else None) or _env_value(S3_ENDPOINT_URL_ENV),
        "client_kwargs": {"region_name": _env_value(S3_REGION_ENV)},
        # Without explicit timeouts aiobotocore waits indefinitely and s3fs's
        # own retry logic does not cover botocore-level connection failures
        # (e.g. proxies dropping multipart uploads with HTTP 499).
        "config_kwargs": {
            "connect_timeout": 30,
            "read_timeout": 120,
            "retries": {"max_attempts": 5, "mode": "standard"},
        },
    }
    token = (params.session_token if params else None) or _env_value(S3_SESSION_TOKEN_ENV)
    if token:
        options["token"] = token
    if params:
        # Explicit request credentials must not inherit an unrelated
        # AWS_PROFILE from the EPT process. s3fs forwards this public
        # botocore session override to aiobotocore.AioSession.
        options["session_vars"] = {"profile": (None, None, None, None)}

    required_options = {
        S3_ENDPOINT_URL_ENV: "endpoint_url",
        S3_ACCESS_KEY_ID_ENV: "key",
        S3_SECRET_KEY_ENV: "secret",
    }
    missing = [
        env_name
        for env_name, option_name in required_options.items()
        if not options.get(option_name)
    ]
    if missing:
        logger.warning("Missing required S3 connection settings: %s", ", ".join(missing))
        raise RuntimeError(f"Missing required S3 connection settings: {', '.join(missing)}")
    logger.info(
        "Resolved S3 storage options endpoint=%s region=%s has_session_token=%s from_params=%s",
        options.get("endpoint_url"),
        (options.get("client_kwargs") or {}).get("region_name"),
        bool(options.get("token")),
        params is not None,
    )
    return options


def s3_url_to_fs(
    uri: str,
    *,
    storage_options: dict[str, object],
) -> tuple[S3FileSystem, str]:
    """Open the s3fs filesystem for an S3 URI using already-resolved storage options."""

    if not is_s3_uri(uri):
        raise ValueError("uri must use the s3:// scheme.")
    try:
        from fsspec.core import url_to_fs
    except ImportError as exc:
        raise ImportError("S3 URI support requires the 'storage' extra.") from exc
    logger.info("Resolving S3 filesystem uri=%s endpoint=%s", uri, storage_options.get("endpoint_url"))
    fs, path = url_to_fs(uri, **storage_options)
    logger.info("Resolved S3 filesystem uri=%s path=%s", uri, path)
    return fs, path


def ls_buckets(fs: S3FileSystem) -> list[str]:
    """List bucket names."""

    buckets = sorted(str(bucket).removeprefix("s3://").strip("/") for bucket in fs.ls("/"))
    logger.info("Listed S3 buckets count=%d", len(buckets))
    return buckets


def ls_folder(fs: S3FileSystem, remote_prefix: str, *, recursive: bool = False) -> list[str]:
    """List paths under "bucket" or "bucket/key prefix".

    Returns direct children (objects and common prefixes) by default; all
    object keys when ``recursive`` is true. An empty prefix or "/" lists
    buckets.
    """

    prefix = remote_prefix.strip("/")
    if not prefix:
        return ls_buckets(fs)
    if recursive:
        paths = sorted(str(path) for path in fs.find(prefix, withdirs=False))
    else:
        paths = sorted(str(path) for path in fs.ls(prefix))
    logger.info("Listed S3 folder prefix=s3://%s recursive=%s count=%d", prefix, recursive, len(paths))
    return paths


def put_to_s3(
    fs: S3FileSystem,
    source: str | Path,
    remote_path: str,
    *,
    overwrite: bool = False,
    chunksize: int = S3_PUT_CHUNK_BYTES,
    max_concurrency: int = S3_PUT_MAX_CONCURRENCY,
) -> str:
    """Write one local file or HTTP(S) resource into one S3 object key ("bucket/key")."""

    s3_uri = f"s3://{remote_path}"
    if is_http_url(str(source)):
        return _put_s3_url(fs, str(source), remote_path, overwrite=overwrite)
    logger.info(
        "Uploading S3 object source=%s target=%s full=%s overwrite=%s",
        source,
        s3_uri,
        full_remote_url(fs, remote_path),
        overwrite,
    )
    if not overwrite and _target_exists(fs, remote_path):
        logger.warning("S3 upload target already exists target=%s", s3_uri)
        raise FileExistsError(f"Target already exists: {s3_uri}. Set overwrite=true to replace it.")
    fs.put_file(str(source), remote_path, chunksize=chunksize, max_concurrency=max_concurrency)
    logger.info(
        "Uploaded S3 object source=%s target=%s full=%s bytes=%d",
        source,
        s3_uri,
        full_remote_url(fs, remote_path),
        Path(source).stat().st_size,
    )
    return s3_uri


def get_from_s3(
    fs: S3FileSystem,
    remote_path: str,
    local_path: str | Path,
) -> Path:
    """Download one object, or a whole prefix ("bucket/key") when it is a folder.

    A single object is written to ``local_path`` as a file; a prefix is
    written as a directory tree underneath it. Returns the local path.
    """

    remote_path = remote_path.strip("/")
    local_path = Path(local_path)
    if fs.isdir(remote_path):
        fs.get(remote_path, str(local_path), recursive=True)
        logger.info("Downloaded S3 prefix source=s3://%s target=%s", remote_path, local_path)
    else:
        fs.get_file(remote_path, str(local_path))
        logger.info("Downloaded S3 object source=s3://%s target=%s", remote_path, local_path)
    return local_path


def rm_from_s3(fs: S3FileSystem, remote_paths: str | list[str]) -> list[str]:
    """Delete S3 objects by key path, or every object under a prefix, and return the targets."""

    targets = [remote_paths] if isinstance(remote_paths, str) else list(remote_paths)
    deleted: list[str] = []
    for target in targets:
        path = str(target).strip("/")
        if not path:
            raise ValueError("S3 delete targets must include a bucket and object key or prefix.")
        if fs.isdir(path):
            fs.rm(path, recursive=True)
        else:
            fs.rm(path)
        deleted.append(path)
    logger.info("Deleted S3 objects count=%d", len(deleted))
    return deleted


def full_s3_url(endpoint_url: str | None, bucket_key: str) -> str:
    """Public https URL for a "bucket/key" path; falls back to the s3:// URI."""

    base = str(endpoint_url or "").rstrip("/")
    bucket, _, object_key = bucket_key.partition("/")
    if not base or not object_key:
        return f"s3://{bucket_key}"
    return f"{base}/{quote(bucket)}/{quote(object_key, safe='/')}"


def full_remote_url(fs: S3FileSystem, remote_path: str) -> str:
    """Public https URL for a "bucket/key" path on this filesystem."""

    return full_s3_url(getattr(fs, "endpoint_url", None), remote_path)


def _put_s3_url(fs: S3FileSystem, source_url: str, remote_path: str, *, overwrite: bool) -> str:
    """Stream one HTTP(S) resource into an S3 object."""

    s3_uri = f"s3://{remote_path}"
    logger.info(
        "Uploading URL to S3 source=%s target=%s full=%s overwrite=%s",
        source_url,
        s3_uri,
        full_remote_url(fs, remote_path),
        overwrite,
    )
    if not overwrite and _target_exists(fs, remote_path):
        logger.warning("S3 upload target already exists target=%s", s3_uri)
        raise FileExistsError(f"Target already exists: {s3_uri}. Set overwrite=true to replace it.")

    started = time.perf_counter()
    with _open_source_url(source_url) as source:
        logger.info("Opened HTTP source source=%s elapsed_ms=%.0f", source_url, (time.perf_counter() - started) * 1000)
        with fs.open(remote_path, "wb") as target:
            logger.info("Opened S3 target target=%s elapsed_ms=%.0f", s3_uri, (time.perf_counter() - started) * 1000)
            shutil.copyfileobj(source, target)
            bytes_copied = source.tell()
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "Uploaded URL to S3 source=%s target=%s full=%s bytes=%d elapsed_ms=%.0f",
        source_url,
        s3_uri,
        full_remote_url(fs, remote_path),
        bytes_copied,
        elapsed_ms,
    )
    return s3_uri


def _target_exists(fs: S3FileSystem, remote_path: str) -> bool:
    """Check object existence, mapping HeadObject failures to clear errors.

    With STS credentials lacking s3:ListBucket, HeadObject on a missing key
    returns 403 (not 404). Existence then cannot be verified; report it as
    "exists" so the caller gets the standard overwrite hint instead of a 500.
    """

    try:
        return bool(fs.exists(remote_path))
    except Exception as exc:  # re-raised unless it is a known S3 error
        client_error = _botocore_client_error(exc)
        if client_error is not None:
            code = str(client_error.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
        # s3fs translates HeadObject 403 into PermissionError before we see
        # the ClientError, so both shapes must be handled.
        if client_error is not None or isinstance(exc, PermissionError):
            logger.warning(
                "Cannot verify whether S3 target exists (HeadObject forbidden). "
                "Target: s3://%s",
                remote_path,
            )
            raise FileExistsError(
                "Cannot verify whether the target already exists: your S3 credentials "
                "are not allowed to read object metadata (403 Forbidden). The upload "
                "was blocked to avoid overwriting an existing object. Target: "
                f"s3://{remote_path}. Set overwrite=true to upload anyway."
            ) from exc
        raise


def _botocore_client_error(exc: BaseException) -> Any | None:
    """Return the botocore ClientError for exc, or None (botocore optional)."""

    try:
        from botocore.exceptions import ClientError
    except ImportError:
        return None
    return exc if isinstance(exc, ClientError) else None


def _open_source_url(source_url: str) -> Any:
    """Open an HTTP(S) source through fsspec for streaming reads.

    fsspec sets no socket timeout by default, so a stalled server hangs the
    upload forever; sock_read bounds time between received bytes only.
    """

    try:
        from fsspec import open as open_file
    except ImportError as exc:
        raise ImportError("HTTP source support requires fsspec.") from exc
    client_kwargs: dict[str, Any] = {}
    try:
        import aiohttp

        client_kwargs["timeout"] = aiohttp.ClientTimeout(total=None, sock_read=60)
    except ImportError:
        pass
    return open_file(source_url, mode="rb", client_kwargs=client_kwargs)


def _env_value(name: str) -> str | None:
    for env_name in (name, *S3_ENV_ALIASES.get(name, ())):
        value = os.getenv(env_name, "").strip().strip("\"'")
        if value:
            return value
    return None
