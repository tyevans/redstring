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

from hypothesis import strategies as st

from kg_builder.domain.entity import _MODEL_BEARING_METHODS as MODEL_BEARING_METHODS
from kg_builder.domain.entity import Entity, ExtractionMethod
from kg_builder.domain.relationship import Relationship
from kg_builder.domain.temporal import DatePrecision, TemporalExtent, UncertaintyMarker

if TYPE_CHECKING:
    from uuid import UUID

# Non-blank: `Entity.name` rejects whitespace-only values.
names = st.text(min_size=1, max_size=40).filter(lambda s: bool(s.strip()))
entity_types = st.sampled_from(["person", "organization", "place", "concept", "plot_point"])
relationship_types = st.sampled_from(["knows", "works_at", "located_in", "mentions", "part_of"])
blocking_key_values = st.text(min_size=1, max_size=12)
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
    return st.lists(children, max_size=3) | st.dictionaries(
        st.text(max_size=6), children, max_size=3
    )


property_dicts = st.dictionaries(
    st.text(max_size=6),
    st.recursive(
        st.none() | st.booleans() | st.integers() | st.text(max_size=10),
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
        original_text=draw(st.none() | st.text(max_size=20)),
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
        description=draw(st.none() | st.text(max_size=40)),
        source_id=draw(st.none() | st.text(min_size=1, max_size=12)),
        source_text=draw(st.none() | st.text(max_size=40)),
        external_ids=draw(st.dictionaries(st.text(max_size=6), st.text(max_size=12), max_size=3)),
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
