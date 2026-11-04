"""Safe output-name helpers shared by storage and the STAC format modules.

Pure string functions — no I/O, no storage knowledge. Output names are
derived from arbitrary caller input (STAC ids, file stems) but must be safe
on POSIX and as S3 keys: alphanumerics plus ``. _ -``, starting with an
alphanumeric, no ``..`` — so no path separators, traversal, or shell-hostile
characters.
"""

from __future__ import annotations

import re

__all__ = ["safe_export_name", "slugify"]

_SAFE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)
_SLUG_RUN_PATTERN = re.compile(r"[^a-z0-9]+")


def slugify(value: str, fallback: str = "output") -> str:
    """Lowercase kebab-slug of ``value`` (non-alphanumeric runs → ``-``)."""

    return _SLUG_RUN_PATTERN.sub("-", value.lower()).strip("-") or fallback


def safe_export_name(value: str, fallback: str = "output") -> str:
    """``value`` itself when safe as a file name / object key, else its slug."""

    value = value.strip()
    if _SAFE_NAME_PATTERN.match(value) and ".." not in value:
        return value
    return slugify(value, fallback)
