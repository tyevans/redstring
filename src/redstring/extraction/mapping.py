"""From what a model said to what the domain requires.

`ExtractedEntity` has a name and a type. `Entity` needs an id, a tenant, a
source, a normalized name and a provenance string. This module is the one
place that gap is closed, which makes it the one place a cross-tenant write or
a hallucinated identity could be introduced.

## Identity is derived, never invented

`entity_id_for` is a pure function of `(tenant, source, entity type,
normalized name)`. Everything downstream rests on that:

- **Chunks agree.** A document split into ten overlapping windows mentions the
  same person in several of them. Random ids would make those ten different
  people, and any merge would have to re-derive exactly this key to see they
  are one -- at which point the key may as well be the id.
- **Re-extraction upserts.** `Document.record_extraction` permits a second run
  under a new model version, and the projection upserts. With derived ids the
  second run lands on the first run's entities instead of doubling them.
- **Relationships resolve.** The model names its endpoints; a name maps to an
  id by the same function that gave the entity its id, so the two cannot drift.

### Why the `uuid5` calls are nested rather than the parts joined

ADR 0001 records the hazard for stream ids: a scheme that concatenates before
hashing maps `("ab", "c")` and `("a", "bc")` onto one value. Two of the four
parts here are free-form model output, so no separator character can be ruled
out of them. Nesting means every hashed name is a single whole string and the
ambiguity cannot arise -- it is not a delimiter chosen carefully, it is a
delimiter that does not exist.

## Nothing here raises on the model's behalf

A model that emits one blank name, or one edge to something it forgot to list,
has not failed -- it has produced a long answer with a bad row in it. Raising
would discard the other two hundred rows. So bad rows are dropped and
**counted** on `MappedExtraction`, where a caller can log them, alert on a
ratio, or ignore them. Silently dropping would be the actual sin; the counters
are what make this a decision rather than a bug.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple
from uuid import NAMESPACE_URL, uuid5

from redstring.domain.blocking import blocking_keys_for
from redstring.domain.entity import Entity, ExtractionMethod
from redstring.domain.ids import EntityId, RelationshipId
from redstring.domain.json_safety import has_unstorable_text
from redstring.domain.normalization import normalize_name

# Re-exported rather than defined here. They moved to `domain/` when
# consolidation became the third caller: `consolidation` and `extraction` are
# sibling layers and may not import each other, so a shared tie-break has to
# live below both. Kept importable from this module because `merging.py` and
# every test already name it here, and because the alternative -- two
# definitions -- is what `domain/preference.py` exists to prevent.
from redstring.domain.preference import preference, relationship_preference
from redstring.domain.relationship import Relationship
from redstring.domain.temporal import TemporalExtent
from redstring.domain.temporal_parsing import AmbiguousReferenceDateError, parse_temporal

if TYPE_CHECKING:
    from datetime import datetime

    from redstring.domain.ids import SourceId, TenantId
    from redstring.extraction.schema import ExtractedEntity, Extraction

#: Roots the relationship id space. Fixed and arbitrary; it exists only so
#: that edge ids and entity ids cannot collide, and changing it would re-key
#: every relationship ever written.
_RELATIONSHIP_NAMESPACE = uuid5(NAMESPACE_URL, "urn:redstring:relationship")

#: The methods that may carry `Entity.model`. Mirrors `Entity`'s own rule so
#: that a missing provenance string is caught here, where the fix is obvious,
#: rather than passing `Entity` validation and reaching the log unattributed.
_MODEL_BEARING = frozenset({ExtractionMethod.LLM, ExtractionMethod.HYBRID})


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
    method: ExtractionMethod = ExtractionMethod.LLM,
) -> MappedExtraction:
    """Map one model answer onto domain types.

    Args:
        extraction: What the model returned.
        tenant_id: Applied to every entity and relationship produced. This is
            the only place it is set, so `DocumentExtracted`'s foreign-tenant
            validator cannot catch a mistake made here -- it would be a
            consistent, valid, wrong tenant.
        source_id: The document these came from. Recorded on every entity;
            `DocumentExtracted` rejects a payload that disagrees with it.
        model: Provenance, from `LlmProvider.model`. Required for `LLM` and
            `HYBRID`, forbidden otherwise.
        reference_date: The vantage point relative temporal expressions are
            read against -- `SourceDocument.published_at`. Required rather
            than defaulted, and explicitly `None`-able, because the parser
            reads no clock: see `redstring.domain.temporal_parsing`. `None`
            means "this document is undated", and expressions that need a
            vantage point are then dropped and counted rather than resolved
            against today.
        method: How these were derived. Defaults to `LLM` because that is what
            this module exists for; `PATTERN` and the rest are for callers
            mapping non-model extractions through the same code.

    Returns:
        A `MappedExtraction`. Never raises for anything the *model* did wrong.

    Raises:
        ValueError: `model` disagrees with `method` -- either a model-bearing
            method with no model string, which would put unattributable
            entities in a permanent log, or a model string on a method that
            invokes no model, which `Entity` refuses anyway.
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
    )


def _build_entity(
    candidate: ExtractedEntity,
    *,
    tenant_id: TenantId,
    source_id: SourceId,
    model: str | None,
    method: ExtractionMethod,
    reference_date: datetime | None,
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
        source_id=source_id,
        properties=dict(candidate.properties),
        extraction_method=method,
        model=model,
        confidence=candidate.confidence,
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
