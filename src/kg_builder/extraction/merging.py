"""Combining what each chunk found back into one document's worth of entities.

## What this does, and the much larger thing it deliberately does not

Chunking manufactures duplicates: windows overlap so that a sentence spanning
a boundary survives intact somewhere, which means every entity near a boundary
is reported twice. Those two reports are **the same entity by construction** --
`entity_id_for` gives them one id, because the name, type, tenant and document
are identical. Combining them is not a judgement about the world. It is
recognising that there was only ever one entity, and it is what this module
does.

**Fuzzy resolution -- deciding that "Ada Lovelace" and "Ada" denote one person
-- is not done here, and that is a decision rather than an omission.** It is
the same operation as cross-document consolidation, which slice 7 implements
against `ConsolidationLog`, and doing it here would do it *invisibly*: no
`EntitiesMerged` event, nothing to audit, nothing to undo, and no record that a
judgement was made at all. An unaudited merge buried inside extraction is
precisely what the event-sourced write model exists to prevent. See BACKLOG
B40, which records what was deleted rather than ported and why.

## Order-independence is a property of the tie-break, not of the dict

Merging by id into a mapping is commutative only if the choice between two
mappings of one id is total. It is not enough to say "the more confident one
wins": two chunks routinely report the same entity with the *same*
confidence -- above all `DEFAULT_CONFIDENCE`, which is what every entity gets
when the model declines to say -- and "keep the one already there" would then
make the answer depend on which chunk the splitter happened to emit first.

`_preference` is therefore a total order over the whole object, so the winner
is a `max` and the result cannot depend on iteration order. That is what the
hypothesis property in `tests/unit/extraction/test_merging.py` checks, by
permuting chunks whose entities are deliberately tied.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kg_builder.extraction.mapping import MappedExtraction

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID

    from kg_builder.domain.entity import Entity
    from kg_builder.domain.relationship import Relationship


def _preference(entity: Entity) -> tuple[float, int, str, str]:
    """A total order on two mappings of one entity. Higher wins.

    Confidence first, which is the only part anyone would design
    deliberately. The rest exist to make the order *total*, so that equal
    confidence -- the common case, not the edge case -- resolves the same way
    whatever order the chunks arrive in:

    - description length, preferring the mention that said more, which is
      usually the chunk that held the whole sentence rather than its tail;
    - then the description text and the name, which carry no meaning at all
      and are there purely so no two distinct objects compare equal.

    Property-level merging is explicitly not attempted: the winner keeps its
    own `properties` and the loser's are discarded. Combining them is BACKLOG
    B28, which is about strategies for exactly this and is unresolved.
    """
    return (
        entity.confidence,
        len(entity.description or ""),
        entity.description or "",
        entity.name,
    )


def _relationship_preference(relationship: Relationship) -> tuple[float, str]:
    """The same idea for an edge, over the only two fields that can differ.

    Id already fixes the endpoints and the type, so two mappings of one edge
    can disagree about confidence and `properties` and nothing else.
    """
    return (relationship.confidence, relationship.relationship_type)


def merge_extractions(parts: Iterable[MappedExtraction]) -> MappedExtraction:
    """Fold each chunk's mapped output into one document's worth.

    Args:
        parts: One `MappedExtraction` per chunk, in any order.

    Returns:
        A single `MappedExtraction`. Entities and relationships are
        deduplicated by id; the counters are summed, because a row dropped in
        chunk three is still a row that was dropped.

    Note:
        No relationship can be orphaned by this fold. Each part's edges
        already reference only that part's entities, and every part's
        entities survive into the result -- deduplication removes copies, not
        entities. So the "entities before edges" guarantee `DocumentExtracted`
        needs holds without a second pass.
    """
    entities: dict[UUID, Entity] = {}
    relationships: dict[UUID, Relationship] = {}
    dropped = 0
    unresolved = 0
    self_loops = 0

    for part in parts:
        for entity in part.entities:
            seen = entities.get(entity.id)
            if seen is None or _preference(entity) > _preference(seen):
                entities[entity.id] = entity
        for relationship in part.relationships:
            seen_edge = relationships.get(relationship.id)
            if seen_edge is None or _relationship_preference(
                relationship
            ) > _relationship_preference(seen_edge):
                relationships[relationship.id] = relationship
        dropped += part.dropped_entities
        unresolved += part.unresolved_relationships
        self_loops += part.self_loops

    return MappedExtraction(
        entities=list(entities.values()),
        relationships=list(relationships.values()),
        dropped_entities=dropped,
        unresolved_relationships=unresolved,
        self_loops=self_loops,
    )
