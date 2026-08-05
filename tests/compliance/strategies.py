"""Hypothesis strategies for domain types used by the port-compliance suites.

Two deliberate choices:

- `properties` generates **nested** containers. A shallow-copying adapter
  passes a flat-dict mutation test and corrupts its store on a nested one, so
  the nesting is what gives the mutation-isolation property its teeth.
- Identifiers are generated, not fixed, because tenant isolation must hold for
  *any* pair of distinct tenants rather than two hand-picked UUIDs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from hypothesis import assume
from hypothesis import strategies as st

from redstring.domain.entity import _MODEL_BEARING_METHODS as MODEL_BEARING_METHODS
from redstring.domain.entity import Entity, ExtractionMethod
from redstring.domain.json_safety import reject_nul
from redstring.domain.relationship import Relationship
from redstring.domain.temporal import DatePrecision, TemporalExtent, UncertaintyMarker

if TYPE_CHECKING:
    from uuid import UUID


#: Text that a domain type will actually accept.
#:
#: `Entity` and `Relationship` refuse a NUL in every free-form field, because
#: a JSON column cannot hold one (`domain/json_safety.py`), so a bare
#: `st.text()` here draws values these strategies cannot construct -- rarely,
#: and therefore as an intermittent failure in whatever property happened to
#: draw it rather than as a finding about the guard. Excluded in the alphabet
#: so the constraint is stated once, where the text is generated.
def text(**kwargs: Any) -> st.SearchStrategy[str]:
    return st.text(alphabet=st.characters(exclude_characters="\x00"), **kwargs)


# Non-blank: `Entity.name` rejects whitespace-only values.
names = text(min_size=1, max_size=40).filter(lambda s: bool(s.strip()))
entity_types = st.sampled_from(["person", "organization", "place", "concept", "plot_point"])
relationship_types = st.sampled_from(["knows", "works_at", "located_in", "mentions", "part_of"])
blocking_key_values = text(min_size=1, max_size=12)
# Provider-qualified and versioned, per the convention on `Entity.model`.
model_names = st.sampled_from(
    [
        "ollama/qwen3.6-27b-mtp",
        "anthropic/claude-opus-4-20250514",
        "openai/gpt-5-2025-08-07",
    ]
)
confidences = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


def _property_values(children: st.SearchStrategy[Any]) -> st.SearchStrategy[Any]:
    return st.lists(children, max_size=3) | st.dictionaries(text(max_size=6), children, max_size=3)


property_dicts = st.dictionaries(
    text(max_size=6),
    st.recursive(
        st.none() | st.booleans() | st.integers() | text(max_size=10),
        _property_values,
        max_leaves=6,
    ),
    max_size=4,
)

aware_datetimes = st.datetimes(
    min_value=datetime(1800, 1, 1),
    max_value=datetime(2200, 1, 1),
).map(lambda d: d.replace(tzinfo=UTC))

# `TemporalExtent` requires end_date >= start_date, so the pair is drawn
# together and sorted rather than filtered -- filtering here discards roughly
# half of all draws and hypothesis rightly complains about the waste.
_date_ranges = st.lists(aware_datetimes, min_size=2, max_size=2).map(sorted)


@st.composite
def temporal_extents(draw: st.DrawFn) -> TemporalExtent:
    """Generate a valid `TemporalExtent`, honouring its ordering invariant."""
    start, end = draw(_date_ranges)
    return TemporalExtent(
        start_date=draw(st.none() | st.just(start)),
        end_date=draw(st.none() | st.just(end)),
        precision=draw(st.none() | st.sampled_from(list(DatePrecision))),
        uncertainty=draw(st.none() | st.sampled_from(list(UncertaintyMarker))),
        original_text=draw(st.none() | text(max_size=20)),
        sequence_position=draw(st.none() | st.integers(min_value=0, max_value=1000)),
        publication_date=draw(st.none() | aware_datetimes),
    )


@st.composite
def entities(
    draw: st.DrawFn,
    *,
    tenant_id: UUID | None = None,
    entity_id: UUID | None = None,
) -> Entity:
    """Generate a valid `Entity`, optionally pinning its tenant or its id."""
    method = draw(st.sampled_from(list(ExtractionMethod)))
    return Entity(
        id=entity_id if entity_id is not None else draw(st.uuids()),
        tenant_id=tenant_id if tenant_id is not None else draw(st.uuids()),
        name=draw(names),
        normalized_name=draw(names),
        entity_type=draw(entity_types),
        original_entity_type=draw(st.none() | names),
        description=draw(st.none() | text(max_size=40)),
        source_id=draw(st.none() | text(min_size=1, max_size=12)),
        source_text=draw(st.none() | text(max_size=40)),
        external_ids=draw(st.dictionaries(text(max_size=6), text(max_size=12), max_size=3)),
        properties=draw(property_dicts),
        extraction_method=method,
        # `Entity` rejects `model` for methods that invoke none; the strategy
        # mirrors the validator rather than restating it, so widening the rule
        # in one place cannot silently stop being generated in the other.
        model=draw(st.none() | model_names) if method in MODEL_BEARING_METHODS else None,
        confidence=draw(confidences),
        temporal=draw(st.none() | temporal_extents()),
        blocking_keys=draw(st.none() | st.frozensets(blocking_key_values, max_size=4)),
    )


@st.composite
def relationships(
    draw: st.DrawFn,
    *,
    tenant_id: UUID,
    source_entity_id: UUID,
    target_entity_id: UUID,
    relationship_id: UUID | None = None,
) -> Relationship:
    """Generate a valid `Relationship` between two given endpoints.

    Endpoints are required rather than generated: a relationship whose
    endpoints do not exist is rejected by the port, so the caller must supply
    entities it has already written.
    """
    return Relationship(
        id=relationship_id if relationship_id is not None else draw(st.uuids()),
        tenant_id=tenant_id,
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        relationship_type=draw(relationship_types),
        properties=draw(property_dicts),
        confidence=draw(confidences),
    )


distinct_tenant_pairs = st.tuples(st.uuids(), st.uuids()).filter(lambda pair: pair[0] != pair[1])


# ----------------------------------------------------------------------
# Vectors
#
# `width=32` is not cosmetic. pgvector's `vector` type is float4, so a value
# that is not exactly representable in single precision cannot round-trip
# through it -- and the round-trip property would then fail on the real
# adapter while passing in-memory, which is the exact divergence the shared
# suite exists to prevent. `width=32` restricts generation to values float32
# holds exactly, so "the stored vector equals the written one" is a contract
# every adapter can meet. See the port on precision.
#
# The magnitude bounds keep the sum of squares far from float32 overflow, so
# a norm is never `inf` and cosine is never NaN for a reason unrelated to the
# property under test.
# ----------------------------------------------------------------------

vector_components = st.floats(
    min_value=-1e3,
    max_value=1e3,
    allow_nan=False,
    allow_infinity=False,
    width=32,
    # Subnormals square to zero, so a vector of them has a zero norm and no
    # direction. The port now *rejects* those (`domain.vector.has_zero_norm`,
    # which closed the divergence), which is why this exclusion is still here
    # and no longer papering over a gap: a drawn subnormal would make an
    # unrelated property fail on a legitimate `ValueError` from the guard.
    # The guard's own band is pinned by examples rather than by sampling --
    # `test_a_vector_whose_norm_underflows_is_rejected_too` in
    # `vector_store.py` and `TestHasZeroNorm` in
    # `tests/unit/domain/test_vector.py`.
    allow_subnormal=False,
)


def vectors(dimension: int) -> st.SearchStrategy[list[float]]:
    """Vectors of exactly `dimension` components, with a usable direction.

    Zero vectors are excluded because cosine is undefined at the origin and
    the port rejects them; generating one would test the guard, not the
    property, in every property that draws a vector.

    The bound is on the **norm**, not on "some component is non-zero" -- see
    the comment on `vector_components` for why those are not the same
    question, and `domain.vector.has_zero_norm` for the guard that asks the
    norm's version of it.
    """
    return st.lists(vector_components, min_size=dimension, max_size=dimension).filter(
        lambda values: sum(value * value for value in values) > 1e-12
    )


def _has_no_nul(mapping: dict[str, Any]) -> bool:
    """Whether `VectorRecord` would accept this metadata.

    Written against the domain rule rather than restating it, so widening or
    narrowing the rule in one place cannot leave the strategy generating
    values the model rejects. `json.dumps` is *not* usable for this: it
    escapes a NUL to the six characters `\\u0000`, so a substring search over
    its output never finds one.
    """
    try:
        reject_nul(mapping)
    except ValueError:
        return False
    return True


#: The one key `VectorStore.search` reads. Its values are drawn deliberately
#: rather than left to chance, and the reason is worth keeping.
#:
#: `property_dicts` alone **can never generate this key**: it draws keys from
#: `st.text(max_size=6)` and `entity_type` is eleven characters. So every
#: property test that claimed to exercise arbitrary metadata was silently
#: exercising metadata with no `entity_type` in it, and a real divergence hid
#: there -- the in-memory adapter raised `TypeError: unhashable type: 'list'`
#: for a stored `{"entity_type": ["person"]}` where pgvector returned `[]`.
#: A property test is only as good as the values that reach it, and a
#: *reserved* key is exactly the value a general-purpose generator misses.
#:
#: The values mix plausible type names with the shapes a caller can
#: legitimately put in JSON and a store cannot treat as a type name.
_entity_type_values = (
    entity_types
    | st.none()
    | st.booleans()
    | st.integers()
    | st.lists(entity_types, max_size=2)
    | st.dictionaries(st.text(max_size=4), entity_types, max_size=2)
)


@st.composite
def metadata_dicts(draw: st.DrawFn) -> dict[str, Any]:
    """Metadata a `VectorStore` must accept, including the reserved key.

    `property_dicts` plus, about half the time, an `entity_type` entry (see
    `_entity_type_values`), with the whole result filtered to what
    `VectorRecord` accepts -- Postgres `jsonb` cannot store a NUL in text
    while a Python dict can, and an adapter accepting what another refuses is
    the divergence the shared suite exists to prevent.

    Two details, both of which this got wrong first time round and
    `TestTheMetadataStrategyReachesTheReservedKey` caught:

    - the NUL filter runs on the **finished** mapping. Filtering the base and
      then adding the reserved key lets a NUL in through the added value:
      `_entity_type_values` can draw a nested dict whose keys come from
      `st.text`, and `st.text` generates NUL.
    - the base draw is **copied, not mutated**. Hypothesis reuses drawn
      objects while shrinking, so writing into one can leak a key into an
      unrelated example and make a failure irreproducible.
    """
    metadata = dict(draw(property_dicts))
    if draw(st.booleans()):
        metadata["entity_type"] = draw(_entity_type_values)
    assume(_has_no_nul(metadata))
    return metadata
