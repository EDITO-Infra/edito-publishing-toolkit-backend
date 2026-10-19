"""Minimal SPDX license matching against free-text constraint language.

ISO19139 legal constraints and STAC item fields carry license references as
prose ("Creative Commons CC-BY 4.0", "This product is licensed under MIT");
this module recovers SPDX identifiers from such text. Only the small set of
licenses actually occurring in EMODnet/EDITO metadata is recognized — extend
``KNOWN_SPDX_IDS`` when new ones appear.
"""

from __future__ import annotations

import re

KNOWN_SPDX_IDS = (
    "CC-BY-4.0",
    "CC-BY-SA-4.0",
    "CC0-1.0",
    "MIT",
    "Apache-2.0",
    "ODbL-1.0",
)

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")


def match_spdx_ids(
    text: str, known: tuple[str, ...] = KNOWN_SPDX_IDS
) -> list[str]:
    """SPDX ids whose compacted form occurs in the compacted text.

    Both sides are uppercased and stripped of all non-alphanumerics before
    matching, so spacing, casing, and punctuation variants ("CC-BY 4.0",
    "cc-by-4.0", "Creative Commons CC-BY 4.0") all resolve to ``CC-BY-4.0``.
    """

    normalized = _NON_ALNUM.sub("", text.upper())
    # Prose like "Apache License 2.0" embeds the filler word "License" inside
    # the id; no known id contains it, so it can be dropped safely.
    normalized = normalized.replace("LICENSE", "")
    return [
        license_id
        for license_id in known
        if _NON_ALNUM.sub("", license_id.upper()) in normalized
    ]
