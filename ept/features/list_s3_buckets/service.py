import logging
from typing import Any

from ept.infrastructure.services.edito_auth import S3ConnectionParameters
from ept.infrastructure.storage.s3 import resolve_s3_storage_options, s3_url_to_fs

logger = logging.getLogger(__name__)


def list_s3_buckets(params: S3ConnectionParameters) -> list[str]:
    """Return the names of the S3 buckets visible to the given credentials.

    Uses the account-level ListBuckets operation (not a prefix listing), so the
    result is every bucket the credentials may see — the caller's personal
    bucket plus any project buckets granted to the user. Invalid or
    unauthorized credentials surface as a botocore ClientError, which the API
    layer maps to a 502 problem.
    """
    fs, _ = s3_url_to_fs("s3:///", storage_options=resolve_s3_storage_options(params))
    result: dict[str, Any] = fs.call_s3("list_buckets")
    buckets = sorted(str(bucket["Name"]) for bucket in result.get("Buckets", []))
    logger.info("Listed S3 buckets endpoint=%s count=%d", params.endpoint_url, len(buckets))
    return buckets
