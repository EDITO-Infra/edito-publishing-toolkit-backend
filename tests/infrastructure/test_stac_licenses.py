"""Unit tests for free-text SPDX license matching."""

from ept.infrastructure.formats.stac.licenses import match_spdx_ids
from tests.conftest import unit


@unit
def test_matches_spacing_casing_and_punctuation_variants():
    assert match_spdx_ids("Creative Commons CC-BY 4.0 https://creativecommons.org/licenses/by/4.0/") == ["CC-BY-4.0"]
    assert match_spdx_ids("This product is licensed under cc-by-sa 4.0") == ["CC-BY-SA-4.0"]
    assert match_spdx_ids("Released into the public domain (CC0 1.0).") == ["CC0-1.0"]
    assert match_spdx_ids("ODbL 1.0 applies") == ["ODbL-1.0"]
    assert match_spdx_ids("Apache License 2.0" ) == ["Apache-2.0"]
    assert match_spdx_ids("MIT license") == ["MIT"]


@unit
def test_returns_all_distinct_matches_in_known_order():
    text = "Data under CC-BY 4.0, metadata under CC0-1.0."
    assert match_spdx_ids(text) == ["CC-BY-4.0", "CC0-1.0"]


@unit
def test_no_match_for_unrelated_text():
    assert match_spdx_ids("This data was downloaded from the EMODnet Portal.") == []
    assert match_spdx_ids("") == []
