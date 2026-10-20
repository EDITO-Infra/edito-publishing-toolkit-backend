import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx

from ept.infrastructure.storage.s3 import (
    S3_PUT_CHUNK_BYTES,
    full_remote_url,
    full_s3_url,
    is_http_url,
    put_to_s3,
    resolve_s3_storage_options,
    s3_url_to_fs,
)

from .models import UploadS3Request, UploadS3Response

logger = logging.getLogger(__name__)

# Transfer-size heuristics: many small files favor more parallel requests of
# the minimum part size; large files favor more parts in flight at the size
# cap. Part size stays <=20 MiB everywhere — larger parts risk the ~60s
# proxy timeout (HTTP 499) on slow links.
S3_UPLOAD_SMALL_AVG_BYTES = 1 * 2**20
S3_UPLOAD_MANY_FILES = 1000
S3_UPLOAD_SMALL_CHUNK_BYTES = 5 * 2**20
S3_UPLOAD_SMALL_MAX_CONCURRENCY = 24
S3_UPLOAD_LARGE_MAX_CONCURRENCY = 16

# Some HTTP sources (e.g. GeoNetwork behind a sticky load balancer) answer the
# first GET with a transient 404 while a plain browser fetch succeeds. Retry
# the single-object fetch before concluding the source is a store prefix —
# the retry sleeps run in the upload threadpool, so keep the budget short.
S3_UPLOAD_HTTP_ATTEMPTS = 3
S3_UPLOAD_HTTP_RETRY_BACKOFF_S = 1.0


def upload_s3(request: UploadS3Request, default_bucket: str | None = None) -> UploadS3Response:
    """Upload an HTTP(S) resource or local path to S3-compatible storage."""

    bucket = _resolve_bucket(request, default_bucket)
    source_name = None if request.output_name else _source_name(request.source)
    output_name = request.output_name or source_name
    storage_options: dict[str, Any] = resolve_s3_storage_options(request)
    target_uri = full_s3_url(
        storage_options.get("endpoint_url"), f"{bucket}/{request.target}/{output_name}"
    )
    logger.info(
        "Starting upload_s3 feature source=%s target=%s recursive=%s overwrite=%s",
        request.source,
        target_uri,
        request.recursive,
        request.overwrite,
    )
    uploaded = upload_to_s3(
        request.source,
        f"s3://{bucket}/{request.target}/{output_name}",
        recursive=request.recursive,
        overwrite=request.overwrite,
        storage_options=storage_options,
    )
    logger.info(
        "Completed upload_s3 feature source=%s target=%s uploaded=%d",
        request.source,
        target_uri,
        len(uploaded),
    )
    return UploadS3Response(
        bucket=bucket,
        source=request.source,
        target_uri=target_uri,
        uploaded=uploaded,
        status="completed",
    )


def upload_to_s3(
    source_path: str,
    target_uri: str,
    *,
    recursive: bool = True,
    overwrite: bool = False,
    storage_options: dict[str, Any] | None = None,
) -> list[str]:
    """Upload a local file or directory (or HTTP(S) URL) and return full https URLs.

    ``target_uri`` must be a full ``s3://bucket/key`` URI and ``storage_options``
    must already be resolved, e.g. via
    :func:`ept.infrastructure.storage.s3.resolve_s3_storage_options`. Returned
    URLs are ``<endpoint>/bucket/key`` when the endpoint is known, else ``s3://``.
    """

    logger.info(
        "Starting S3 upload source=%s target=%s recursive=%s overwrite=%s",
        source_path,
        target_uri,
        recursive,
        overwrite,
    )

    fs, target_path = s3_url_to_fs(target_uri, storage_options=storage_options or {})

    def public_urls(uris: list[str]) -> list[str]:
        return [full_remote_url(fs, uri.removeprefix("s3://")) for uri in uris]

    if is_http_url(source_path):
        uploaded = _upload_http_source(fs, source_path, target_path, overwrite=overwrite)
        log_s3_location("Uploaded", source_path, target_path, storage_options=storage_options)
        logger.info("Finished S3 upload source=%s uploaded=%d", source_path, len(uploaded))
        return public_urls(uploaded)

    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"Source does not exist: {source_path}")
    if source.is_dir() and not recursive:
        raise ValueError("recursive must be true when source is a local directory.")

    targets = iter_upload_targets(source, target_path)
    profile = upload_transfer_profile(targets)
    chunksize, max_concurrency = optimized_upload_params(profile)
    logger.info(
        "Prepared %d S3 upload target(s) source=%s target=%s total_bytes=%d avg_size=%d "
        "chunksize=%d max_concurrency=%d",
        len(targets),
        source_path,
        target_uri,
        profile["total_bytes"],
        profile["avg_size"],
        chunksize,
        max_concurrency,
    )
    uploaded: list[str] = []
    for local_path, remote_path in targets:
        s3_uri = put_to_s3(
            fs,
            local_path,
            remote_path,
            overwrite=overwrite,
            chunksize=chunksize,
            max_concurrency=max_concurrency,
        )
        log_s3_location("Uploaded", local_path.name, remote_path, storage_options=storage_options)
        uploaded.append(s3_uri)
    logger.info("Finished S3 upload source=%s uploaded=%d", source_path, len(uploaded))
    return public_urls(uploaded)


def iter_upload_targets(source: Path, target_path: str) -> list[tuple[Path, str]]:
    if source.is_file():
        targets = [(source, target_path)]
    else:
        targets = [
            (
                local_path,
                str(PurePosixPath(target_path) / local_path.relative_to(source).as_posix()),
            )
            for local_path in sorted(path for path in source.rglob("*") if path.is_file())
        ]
    logger.debug("Iterated upload targets source=%s count=%d", source, len(targets))
    return targets


def upload_transfer_profile(targets: list[tuple[Path, str]]) -> dict[str, int]:
    """Exact byte counts from already-resolved upload targets (stat is metadata-only)."""

    file_count = 0
    total_bytes = 0
    for local_path, _ in targets:
        try:
            total_bytes += local_path.stat().st_size
            file_count += 1
        except OSError:
            continue
    avg_size = total_bytes // file_count if file_count else 0
    return {"file_count": file_count, "total_bytes": total_bytes, "avg_size": avg_size}


def optimized_upload_params(profile: dict[str, int]) -> tuple[int, int]:
    """(chunksize, max_concurrency) for put_to_s3, tuned to the transfer profile."""

    if (
        profile.get("avg_size", 0) < S3_UPLOAD_SMALL_AVG_BYTES
        or profile.get("file_count", 0) > S3_UPLOAD_MANY_FILES
    ):
        return S3_UPLOAD_SMALL_CHUNK_BYTES, S3_UPLOAD_SMALL_MAX_CONCURRENCY
    return S3_PUT_CHUNK_BYTES, S3_UPLOAD_LARGE_MAX_CONCURRENCY


def _upload_http_source(fs: Any, source_url: str, remote_path: str, *, overwrite: bool) -> list[str]:
    """Upload an HTTP(S) source: a single object, or a directory-like remote store.

    Zarr stores and parquet datasets on S3-compatible hosts are key prefixes,
    not objects — GET on the store URL returns 404 NoSuchKey. When the URL
    itself does not resolve, list the prefix via S3 ListObjectsV2 and upload
    each member under the target path (same mapping as a local directory).
    """

    try:
        return [_put_s3_url_with_retries(fs, source_url, remote_path, overwrite=overwrite)]
    except FileNotFoundError:
        logger.info("HTTP source is not a single object, probing store prefix source=%s", source_url)

    bucket, prefix, base = _http_prefix_parts(source_url)
    keys = _list_s3_prefix_keys(base, bucket, prefix)
    if not keys:
        detail = _probe_http_source(source_url)
        raise FileNotFoundError(
            f"Source does not exist or could not be fetched after "
            f"{S3_UPLOAD_HTTP_ATTEMPTS} attempts ({detail}): {source_url}"
        )

    uploaded: list[str] = []
    for key in keys:
        member_url = f"{base}/{bucket}/{quote(key, safe='/')}"
        member_target = str(PurePosixPath(remote_path) / key.removeprefix(prefix).lstrip("/"))
        uploaded.append(put_to_s3(fs, member_url, member_target, overwrite=overwrite))
    logger.info("Uploaded HTTP store prefix source=%s members=%d", source_url, len(uploaded))
    return uploaded


def _put_s3_url_with_retries(fs: Any, source_url: str, remote_path: str, *, overwrite: bool) -> str:
    """Stream one HTTP(S) resource into S3, retrying transient source failures."""

    for attempt in range(1, S3_UPLOAD_HTTP_ATTEMPTS + 1):
        try:
            return put_to_s3(fs, source_url, remote_path, overwrite=overwrite)
        except FileNotFoundError as exc:
            logger.warning(
                "HTTP source fetch failed attempt=%d/%d source=%s error=%s",
                attempt,
                S3_UPLOAD_HTTP_ATTEMPTS,
                source_url,
                exc,
            )
            if attempt < S3_UPLOAD_HTTP_ATTEMPTS:
                time.sleep(S3_UPLOAD_HTTP_RETRY_BACKOFF_S * attempt)
    raise FileNotFoundError(f"Source could not be fetched after {S3_UPLOAD_HTTP_ATTEMPTS} attempts: {source_url}")


def _probe_http_source(source_url: str) -> str:
    """Plain redirected GET to report the upstream's real status in error messages.

    fsspec's probe may 404 where a browser succeeds (sticky load balancers,
    header-sensitive servers); this distinguishes "server really says 404"
    from "our fetch path is broken" in the raised error.
    """

    try:
        response = httpx.get(source_url, follow_redirects=True, timeout=30)
    except httpx.HTTPError as exc:
        return f"unreachable: {exc}"
    return f"plain GET status={response.status_code}"


def _http_prefix_parts(source_url: str) -> tuple[str, str, str]:
    """Split a store URL into (bucket, prefix, scheme://host)."""

    parts = urlsplit(source_url)
    segments = unquote(parts.path).strip("/").split("/", 1)
    bucket = segments[0]
    prefix = segments[1] if len(segments) > 1 else ""
    return bucket, prefix, f"{parts.scheme}://{parts.netloc}"


def _list_s3_prefix_keys(base: str, bucket: str, prefix: str) -> list[str]:
    """List object keys under a prefix via unsigned S3 ListObjectsV2, paginated."""

    keys: list[str] = []
    token: str | None = None
    while True:
        params: dict[str, str] = {"list-type": "2", "prefix": f"{prefix}/"}
        if token:
            params["continuation-token"] = token
        try:
            response = httpx.get(f"{base}/{bucket}", params=params, timeout=60)
        except httpx.HTTPError as exc:
            logger.info("HTTP store prefix listing failed base=%s bucket=%s error=%s", base, bucket, exc)
            return []
        if response.status_code != 200:
            logger.info(
                "HTTP store prefix listing unavailable status=%d base=%s bucket=%s",
                response.status_code,
                base,
                bucket,
            )
            return []
        truncated = False
        for element in ET.fromstring(response.content).iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "Key":
                keys.append(element.text or "")
            elif tag == "NextContinuationToken":
                token = element.text
            elif tag == "IsTruncated":
                truncated = (element.text or "").lower() == "true"
        if not truncated:
            return keys


def log_s3_location(
    action: str,
    label: str,
    remote_path: str,
    *,
    storage_options: dict[str, Any] | None = None,
) -> None:
    s3_uri = f"s3://{remote_path}"
    http_url = full_s3_url((storage_options or {}).get("endpoint_url"), remote_path)
    if http_url == s3_uri:
        logger.info("%s %s | %s", action, label, s3_uri)
    else:
        logger.info("%s %s | %s | %s", action, label, s3_uri, http_url)


def _resolve_bucket(request: UploadS3Request, default_bucket: str | None) -> str:
    """Use the explicit bucket, else the caller's personal oidc-<username> bucket."""

    if request.bucket:
        return request.bucket
    if default_bucket:
        logger.info("Using default S3 bucket from authenticated user bucket=%s", default_bucket)
        return default_bucket
    raise ValueError(
        "bucket is required: the bearer token carries no username from which to "
        "derive the default oidc-<username> bucket."
    )


def _source_name(source: str) -> str:
    """Derive the destination name from a URL path or local filesystem path."""

    if is_http_url(source):
        name = PurePosixPath(unquote(urlsplit(source).path.rstrip("/"))).name
    else:
        name = Path(source.rstrip("/\\")).name
    if not name:
        raise ValueError("source must include a file or directory name.")
    return name
