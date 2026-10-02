"""From what a model said to what the domain requires.

ExtractedEntity has a name and type; Entity requires an id, tenant, source,
normalized name, and provenance. This module closes that gap cleanly.

Identity is derived, never invented: entity_id_for is a pure function of
(tenant, source, entity type, normalized name). Nested uuid5 hashing prevents
concatenation collisions (ADR 0001). Bad rows from models are dropped and
counted on MappedExtraction rather than failing the extraction run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple
from uuid import NAMESPACE_URL, uuid5

from redstring.domain.blocking import blocking_keys_for
from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, RelationshipId
from redstring.domain.json_safety import has_unstorable_text
from redstring.domain.normalization import normalize_name
from redstring.domain.preference import preference, relationship_preference
from redstring.domain.provenance import MODEL_BEARING_METHODS, ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.domain.temporal import TemporalExtent
from redstring.domain.temporal_parsing import AmbiguousReferenceDateError, parse_temporal
from redstring.extraction.date_nodes import lift_date_nodes

__all__ = [
    "MappedExtraction",
    "entity_id_for",
    "map_extraction",
]

if TYPE_CHECKING:
    from datetime import datetime

    from redstring.domain.ids import SourceId, TenantId
    from redstring.extraction.schema import ExtractedEntity, Extraction

#: Roots the relationship id space. Fixed and arbitrary; it exists only so
#: that edge ids and entity ids cannot collide, and changing it would re-key
#: every relationship ever written.
_RELATIONSHIP_NAMESPACE = uuid5(NAMESPACE_URL, "urn:redstring:relationship")

#: The methods that may carry `Provenance.model`. Imported rather than
#: restated: this module checks the rule *early*, so that a missing provenance
#: string is caught here where the fix is obvious rather than reaching the log
#: unattributed -- but checking it early against a second copy of the
#: membership set is how the two quietly stop agreeing.
_MODEL_BEARING = MODEL_BEARING_METHODS


class MappedExtraction(NamedTuple):
    """Domain objects, plus what could not be turned into one.

    The three counters are not diagnostics for their own sake. A run where
    `unresolved_relationships` dwarfs `relationships` means the prompt is
    asking for edges between things it never asks to be listed, which is
    invisible in the output -- the output simply has fewer edges.
    """

    entities: list[Entity]
    relationships: list[Relationship]
    #: Rows the domain refused: blank names overwhelmingly, and anything
    #: carrying text no JSON-backed store can hold (a NUL, or an unpaired
    #: surrogate, which has no UTF-8 encoding at all).
    dropped_entities: int = 0
    #: Edges naming an endpoint that was never listed as an entity.
    unresolved_relationships: int = 0
    #: Edges whose endpoints resolved to one entity. See `Relationship`.
    self_loops: int = 0
    #: Entities whose temporal expression was relative ("last year") with no
    #: `reference_date` to read it against, so the date was dropped and the
    #: entity kept. Counted rather than raised for the reason above, and
    #: counted rather than ignored because a document with no publication date
    #: loses every relative date in it -- which is invisible in the output,
    #: since the output simply has fewer dated entities.
    undatable_relative: int = 0
    #: Entities that gained a `temporal_expression` lifted off a date-node
    #: the model had related them to. See `extraction/date_nodes.py`: these
    #: are dates the model found and filed as entities, and this counter is
    #: how a caller sees the recovery happening rather than inferring it from
    #: a timeline that got fuller.
    lifted_dates: int = 0
    #: Entities that were a bare date and were removed before mapping. A
    #: large number here is not a fault in the document -- it is the model
    #: reading `ExtractedEntity.temporal_expression` as a type name, which is
    #: what `date_nodes` exists to absorb.
    date_nodes: int = 0


def entity_id_for(
    *,
    tenant_id: TenantId,
    source_id: SourceId,
    name: str,
    entity_type: str,
) -> EntityId:
    """The id an entity with this name and type has in this document.

    Deterministic across processes and across deploys: seeded only from its
    arguments and a `uuid5` namespace constant, never from anything
    process-local. A `uuid4` namespace built at import time would satisfy
    every "same call, same answer" test and still re-key the whole corpus on
    the next restart.

    Scoped to the document on purpose. Deciding that `doc-1`'s "Ada" and
    `doc-2`'s "Ada" are one person is consolidation's judgement, recorded as
    an `EntitiesMerged` that can be audited and undone -- not something
    extraction does by choosing an id.
    """
    within_tenant = uuid5(tenant_id, source_id)
    within_document = uuid5(within_tenant, entity_type)
    return EntityId(uuid5(within_document, normalize_name(name)))


def _relationship_id_for(
    *, source_entity_id: EntityId, target_entity_id: EntityId, relationship_type: str
) -> RelationshipId:
    """The id of one directed, typed edge. Nested for the reason above."""
    from_source = uuid5(_RELATIONSHIP_NAMESPACE, str(source_entity_id))
    to_target = uuid5(from_source, str(target_entity_id))
    return RelationshipId(uuid5(to_target, relationship_type))


def map_extraction(
    extraction: Extraction,
    *,
    tenant_id: TenantId,
    source_id: SourceId,
    model: str | None,
    reference_date: datetime | None,
    observed_at: datetime,
    method: ExtractionMethod = ExtractionMethod.LLM,
) -> MappedExtraction:
    """Map one model answer onto domain types.

    Args:
        extraction: What the model returned.
        tenant_id: Applied to every entity and relationship produced.
        source_id: Document these entities and edges originated from.
        model: Provenance model name, required for model-bearing methods.
        reference_date: Vantage point for relative temporal expressions.
        observed_at: Record time stamped onto every entity's Provenance.
        method: Extraction method used (defaults to LLM).

    Returns:
        MappedExtraction containing valid domain entities and relationships.

    Raises:
        ValueError: If model presence disagrees with extraction method.
    """
    if method in _MODEL_BEARING and model is None:
        raise ValueError(
            f"extraction_method {method.value!r} must record which model produced it; "
            f"pass LlmProvider.model as `model`"
        )
    if method not in _MODEL_BEARING and model is not None:
        raise ValueError(
            f"extraction_method {method.value!r} invokes no model, so `model` must be None"
        )

    # **Before anything is built, and deliberately not after.** A date-node
    # that reached `_build_entity` would be a real `Entity` with a derived id
    # and edges pointing at it, and unpicking that is a graph surgery rather
    # than a filter. Here it is still a row in a list.
    extraction, lifted, date_nodes = lift_date_nodes(extraction)

    by_id: dict[EntityId, Entity] = {}
    dropped = 0
    undatable = 0
    for candidate in extraction.entities:
        built, was_undatable = _build_entity(
            candidate,
            tenant_id=tenant_id,
            source_id=source_id,
            model=model,
            method=method,
            reference_date=reference_date,
            observed_at=observed_at,
        )
        undatable += was_undatable
        if built is None:
            dropped += 1
            continue
        existing = by_id.get(built.id)
        if existing is None or preference(built) > preference(existing):
            by_id[built.id] = built

    relationships, unresolved, self_loops = _map_relationships(
        extraction, tenant_id=tenant_id, source_id=source_id, known=set(by_id)
    )
    return MappedExtraction(
        entities=list(by_id.values()),
        relationships=relationships,
        dropped_entities=dropped,
        unresolved_relationships=unresolved,
        self_loops=self_loops,
        undatable_relative=undatable,
        lifted_dates=lifted,
        date_nodes=date_nodes,
    )


def _build_entity(
    candidate: ExtractedEntity,
    *,
    tenant_id: TenantId,
    source_id: SourceId,
    model: str | None,
    method: ExtractionMethod,
    reference_date: datetime | None,
    observed_at: datetime,
) -> tuple[Entity | None, bool]:
    """One `ExtractedEntity` as an `Entity`, or None if the domain refuses it.

    Returns the entity and whether its temporal expression had to be dropped
    for want of a reference date.

    The guards are explicit -- `name.strip()`, and a NUL anywhere in the
    candidate -- rather than a `try`/`except ValidationError`, so that a
    *different* validation failure, one that is our bug rather than the
    model's, still raises instead of being counted as a dropped row.

    Both guards name a way the *model* can hand back something unusable.
    Text that no JSON-backed event store can hold -- a NUL, or an unpaired
    surrogate -- is refused by `Entity` (`domain/json_safety.py`); without
    this guard that refusal would surface as a `ValidationError` out of
    `map_extraction` and fail the whole chunk over one bad row, which is not
    how any other bad row is treated. The surrogate case does not even get
    that far: `entity_id_for` hashes with `uuid5`, which encodes, so it raised
    `UnicodeEncodeError` from this function.
    `model_dump()` rather than the fields read below, because the dropping
    decision should not have to be revisited each time this function starts
    reading one more field of the candidate.
    """
    if not candidate.name.strip() or has_unstorable_text(candidate.model_dump()):
        return None, False
    temporal, undatable = _build_extent(candidate, reference_date=reference_date)
    built = Entity(
        id=entity_id_for(
            tenant_id=tenant_id,
            source_id=source_id,
            name=candidate.name,
            entity_type=candidate.entity_type,
        ),
        tenant_id=tenant_id,
        name=candidate.name,
        normalized_name=normalize_name(candidate.name),
        entity_type=candidate.entity_type,
        description=candidate.description,
        properties=dict(candidate.properties),
        provenance=Provenance(
            observed_at=observed_at,
            extraction_method=method,
            confidence=candidate.confidence,
            source_id=source_id,
            model=model,
        ),
        temporal=temporal,
    )
    # Blocking keys are computed **here**, at extraction time, and stored on
    # the entity -- `GraphStore.find_by_blocking_key` only looks them up and
    # computes nothing. Two rounds rather than one because `blocking_keys_for`
    # takes an `Entity`, and building one to derive a field of itself is
    # cheaper to read than threading the four inputs through separately.
    #
    # An entity extracted without them is not findable by consolidation at all,
    # which is a silent failure: blocking returns an empty candidate list, and
    # an empty candidate list is what "no duplicates" also looks like.
    return built.model_copy(update={"blocking_keys": blocking_keys_for(built)}), undatable


def _build_extent(
    candidate: ExtractedEntity, *, reference_date: datetime | None
) -> tuple[TemporalExtent | None, bool]:
    """The entity's `TemporalExtent`, and whether a date was lost building it.

    Enrichment is **part of building the entity**, not a pass over a store
    afterwards. `Entity` already carries `temporal` and entities already reach
    the log inside `DocumentExtracted`, so a second pass would need either a
    store write outside the event log or a second event -- and ADR 0001's
    granularity decision is permanent and coarse. Re-extraction under a new
    model version is how an entity's dates improve.

    Nothing here raises on the model's behalf, for the reason the module
    docstring gives. An unparseable phrase yields no extent and the entity
    survives; a relative phrase with no vantage point does the same and is
    counted, because losing every relative date in an undated document is
    otherwise invisible -- the output simply has fewer dated entities.
    """
    parsed: TemporalExtent | None = None
    undatable = False
    if candidate.temporal_expression:
        try:
            parsed = parse_temporal(candidate.temporal_expression, reference_date=reference_date)
        except AmbiguousReferenceDateError:
            undatable = True

    if candidate.sequence_position is None:
        return parsed, undatable
    if parsed is None:
        return TemporalExtent(sequence_position=candidate.sequence_position), undatable
    return parsed.model_copy(update={"sequence_position": candidate.sequence_position}), undatable


def _map_relationships(
    extraction: Extraction,
    *,
    tenant_id: TenantId,
    source_id: SourceId,
    known: set[EntityId],
) -> tuple[list[Relationship], int, int]:
    """Resolve endpoint names to ids, dropping what cannot become an edge.

    Endpoints are resolved by computing the id the *entity* would have had,
    not by matching text: the model spells an endpoint differently from the
    entity constantly ("ada lovelace" for "Ada Lovelace"), and byte equality
    would drop most real edges into the unresolved count, where it reads as
    the model having failed to list an entity.

    That resolution needs the entity's *type*, which a relationship does not
    carry, so the lookup is over the ids that exist rather than a direct
    computation -- one candidate id per known type.
    """
    types_by_name: dict[str, list[str]] = {}
    for candidate in extraction.entities:
        if candidate.name.strip():
            types_by_name.setdefault(normalize_name(candidate.name), []).append(
                candidate.entity_type
            )

    def resolve(name: str) -> EntityId | None:
        for entity_type in types_by_name.get(normalize_name(name), ()):
            candidate_id = entity_id_for(
                tenant_id=tenant_id, source_id=source_id, name=name, entity_type=entity_type
            )
            if candidate_id in known:
                return candidate_id
        return None

    by_id: dict[RelationshipId, Relationship] = {}
    unresolved = 0
    self_loops = 0
    for stated in extraction.relationships:
        start, end = resolve(stated.source_name), resolve(stated.target_name)
        if start is None or end is None:
            unresolved += 1
            continue
        # Checked on the resolved ids, not the names: two spellings of one
        # name resolve to one entity, and `Relationship` refuses that edge.
        if start == end:
            self_loops += 1
            continue
        edge = Relationship(
            id=_relationship_id_for(
                source_entity_id=start,
                target_entity_id=end,
                relationship_type=stated.relationship_type,
            ),
            tenant_id=tenant_id,
            source_entity_id=start,
            target_entity_id=end,
            relationship_type=stated.relationship_type,
            # Not part of the id. The endpoints already carry `source_id`
            # through `entity_id_for`, so two documents stating the same edge
            # produce different ids anyway -- putting it in the hash as well
            # would change every existing id to express something already
            # expressed.
            source_id=source_id,
            properties=dict(stated.properties),
            confidence=stated.confidence,
        )
        # Not `setdefault`. That kept the first mention and ignored
        # confidence entirely, while `merge_extractions` kept the most
        # confident -- so one model answer stating an edge twice, hedged then
        # certain, recorded the hedge, and the same two statements arriving in
        # separate chunks recorded the certainty.
        seen = by_id.get(edge.id)
        if seen is None or relationship_preference(edge) > relationship_preference(seen):
            by_id[edge.id] = edge
    return list(by_id.values()), unresolved, self_loops
