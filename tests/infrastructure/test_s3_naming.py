"""Tests for output-name safety (`slugify`/`safe_export_name`/`with_file_name` in
`ept.infrastructure.storage.s3`)."""

from __future__ import annotations

import pytest

from ept.infrastructure.storage.s3 import safe_export_name, slugify, with_file_name
from tests.conftest import unit


@unit
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("it-1", "it-1"),
        ("My-Item", "My-Item"),  # case kept when already safe
        ("data.v2_final", "data.v2_final"),
        ("My Item!", "my-item"),
        ("a/b", "a-b"),
        ("../../escape", "escape"),
        ("  spaced  ", "spaced"),
        ("///", "output"),  # fallback
        ("", "output"),
    ],
)
def test_safe_export_name(value: str, expected: str):
    assert safe_export_name(value) == expected


@unit
def test_safe_export_name_fallback():
    assert safe_export_name("///", fallback="item") == "item"


@unit
def test_slugify():
    assert slugify("North Sea Bathymetry!") == "north-sea-bathymetry"
    assert slugify("---") == "output"
    assert slugify("---", fallback="collection") == "collection"


@unit
def test_with_file_name_checks_explicit_override():
    # Derived file names are appended as-is.
    assert with_file_name("out", "it-1.json") == "out/it-1.json"
    # Explicit override file names go through the same safety check; the
    # extension survives, folder segments are the target validators' concern.
    assert with_file_name("out/my data.json", "x.json") == "out/my-data.json"
    assert with_file_name("out/My-Item.json", "x.json") == "out/My-Item.json"
    assert with_file_name("out/archive.tar.gz", "x.json") == "out/archive.tar.gz"
