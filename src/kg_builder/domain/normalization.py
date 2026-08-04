"""Name normalization for entity identity.

This is an identity concern, not a blocking-key concern: it must not
collapse distinct-looking names (e.g. "foo bar" vs "foo-bar") into the same
value. See `extraction/domains/models.py:216` for the slug-producing
normalizer used elsewhere, which this deliberately does not replicate.
"""

from __future__ import annotations

import re

_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Casefold, strip, and collapse internal whitespace runs to one space.

    Hyphens and underscores are left untouched. Never raises.
    """
    return _WHITESPACE_RUN.sub(" ", name.casefold().strip())
