"""EDITO Publishing Toolkit."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def _read_version() -> str:
    """Global project version.

    Source and editable checkouts read the repo-root ``VERSION`` file (the
    single source of truth, consumed by pyproject and the docs build too);
    installed wheels fall back to the distribution metadata.
    """

    if _VERSION_FILE.is_file():
        return _VERSION_FILE.read_text(encoding="utf-8").strip()
    try:
        return version("edito-publishing-toolkit")
    except PackageNotFoundError:
        return "0.0.0.dev0"


__version__ = _read_version()
