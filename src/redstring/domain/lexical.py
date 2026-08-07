"""Scoring an entity against a free-text query, with no text index.

This is **not a corpus-statistics ranker**, and nothing here should be renamed
towards one. Okapi-style term weighting depends on document frequency and
average document length, and neither quantity means anything over a corpus of
entity names, where every "document" is a handful of words. A field-weighted
string similarity does the job people actually want from a lexical channel
here: catching `ACME Corporation` against `Acme Corp`, which is exactly where
cosine is weakest. A real term-weighted ranker needs stored text, which this
library does not keep.

The score is a **maximum over fields, not a sum.** Summing would let an entity
with many mediocre fields outrank an exact name match, and would leave the
result unbounded above -- so it could not be compared against the semantic
channel's `0..1` even informally, and could not be reported on `ScoredEntity`
under a stated scale.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from redstring.domain.similarity import string_similarity

if TYPE_CHECKING:
    from redstring.domain.entity import Entity

#: What a match on a property value is worth relative to a match on the name.
#:
#: A name is what an entity *is*; a property is something recorded about it,
#: and a query matching one is weaker evidence. The exact figure is a
#: judgement rather than a measurement -- there is no graded retrieval corpus
#: in this repo to fit it against, and inventing one from the accuracy suite's
#: five documents would dress a guess as a result. See BACKLOG B80, which says
#: what evidence would settle it.
PROPERTY_WEIGHT = 0.6


def lexical_score(query: str, entity: Entity) -> float:
    """How well `entity` matches `query` lexically, on `0..1`.

    The best of: the name, the extractor's `normalized_name`, and each string
    value in `properties` at `PROPERTY_WEIGHT`. Casing and whitespace are not
    differences -- `string_similarity` normalizes both sides, and this reuses
    it rather than growing a second normalization, because two normalizations
    that agree today are how two subsystems disagree in six months.

    Non-string property values are skipped rather than coerced. `properties`
    is free-form JSON, and `str(7)` would invent a match against the query
    `"7"` that no caller asked for -- the same reading `ports/vector_store.py`
    gives a non-string `entity_type`.
    """
    best = max(
        string_similarity(query, entity.name),
        string_similarity(query, entity.normalized_name),
    )
    for value in entity.properties.values():
        if isinstance(value, str):
            best = max(best, PROPERTY_WEIGHT * string_similarity(query, value))
    return best
