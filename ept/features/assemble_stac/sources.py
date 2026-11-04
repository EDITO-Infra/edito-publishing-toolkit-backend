"""Source materialization for the assemble feature: local folder, zip, or s3:// to disk."""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from ept.infrastructure.services.edito_auth import S3ConnectionParameters
from ept.infrastructure.storage.http import download_url
from ept.infrastructure.storage.s3 import get_from_s3, is_http_url, is_s3_uri, s3_connect

logger = logging.getLogger(__name__)

__all__ = ["fetch_json_dir"]


def fetch_json_dir(
    source_uri: str,
    workdir: Path,
    s3_params: S3ConnectionParameters | None = None,
) -> Path:
    """Materialize a directory of JSON files from a local path, zip, or s3:// source.

    Sources: a local directory or ``.zip`` archive, an HTTP(S) URL of a
    ``.zip`` archive, or an ``s3://`` URI of a ``.zip`` object or a prefix
    (authenticated via the request's S3 connection parameters). Returns the
    local directory (or single file) containing the files.
    """
    if is_s3_uri(source_uri):
        fs = s3_connect(s3_params)
        local = get_from_s3(fs, source_uri[len("s3://"):].strip("/"), workdir / "source")
        if local.is_file() and local.suffix.lower() == ".zip":
            return extract_zip_archive(local, workdir / "extracted")
        return local if local.is_dir() else local.parent

    if is_http_url(source_uri):
        if source_uri.split("?", 1)[0].lower().rstrip("/").endswith(".zip"):
            archive = download_url(source_uri, workdir / "source.zip")
            return extract_zip_archive(archive, workdir / "extracted")
        raise ValueError("Remote JSON sources must be a .zip download URL or an s3:// URI.")

    source = Path(source_uri)
    if source.is_dir():
        return source
    if source.is_file() and source.suffix.lower() == ".zip":
        return extract_zip_archive(source, workdir / "extracted")
    raise FileNotFoundError(f"source_uri not found or unsupported: {source_uri}")


def extract_zip_archive(archive: Path, dest: Path) -> Path:
    """Extract a zip archive into dest, rejecting entries that escape it."""
    dest.mkdir(parents=True, exist_ok=True)
    resolved_dest = dest.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            member_target = (dest / member.filename).resolve()
            if not member_target.is_relative_to(resolved_dest):
                raise ValueError(f"Zip entry escapes extraction directory: {member.filename}")
        zf.extractall(dest)
    return dest
