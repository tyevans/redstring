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
precisely what the event-sourced write model exists to prevent.

Slice 7 rebuilt that resolution in `redstring.consolidation.policy`, with the
same two thresholds and the same batched model call for the band between them,
emitting an event each time. Note that within-document resolution is not a
special case there: ids are namespaced per document, so two mentions in one
document reach it by the same path as two in different documents.

## Order-independence is a property of the tie-break, not of the dict

Merging by id into a mapping is commutative only if the choice between two
mappings of one id is total. It is not enough to say "the more confident one
wins": two chunks routinely report the same entity with the *same*
confidence -- above all `DEFAULT_CONFIDENCE`, which is what every entity gets
when the model declines to say -- and "keep the one already there" would then
make the answer depend on which chunk the splitter happened to emit first.

`redstring.extraction.mapping.preference` is therefore a total order over the
whole object, so the winner is a `max` and the result cannot depend on
iteration order. That is what the hypothesis property in
`tests/unit/extraction/test_merging.py` checks, by permuting chunks whose
entities are deliberately tied.

Both orders are **imported** rather than defined here, and shared with the
within-answer deduplication in `mapping.py`. Two definitions are two
tie-breaks, and "dedup within one model answer" disagreeing with "dedup across
chunks" about which mention wins is a difference nobody would go looking for.
That is not hypothetical: this module and `mapping.py` did disagree about
relationships until fix round 1, because one used `setdefault` and the other a
partial order over a field the id had already fixed.

## The fold can also say how many chunks reported each entity

`mention_counts` answers that, over the same `parts`. It is a separate
function rather than a field on `MappedExtraction`, because on a *single*
chunk's result the number is `1` for everything and would mean nothing; the
quantity only exists across the fold.

It is deliberately not a field on `Entity`. See that function's docstring for
what a mention is and what it is not, and BACKLOG B143 for the corpus-level
statistic that shares the name and is a projection rather than a counter.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from redstring.domain.preference import preference, relationship_preference
from redstring.extraction.mapping import MappedExtraction

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from uuid import UUID

    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId
    from redstring.domain.relationship import Relationship


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
    undatable = 0
    lifted = 0
    date_nodes = 0

    for part in parts:
        for entity in part.entities:
            seen = entities.get(entity.id)
            if seen is None or preference(entity) > preference(seen):
                entities[entity.id] = entity
        for relationship in part.relationships:
            seen_edge = relationships.get(relationship.id)
            if seen_edge is None or relationship_preference(relationship) > (
                relationship_preference(seen_edge)
            ):
                relationships[relationship.id] = relationship
        dropped += part.dropped_entities
        unresolved += part.unresolved_relationships
        self_loops += part.self_loops
        undatable += part.undatable_relative
        lifted += part.lifted_dates
        date_nodes += part.date_nodes

    return MappedExtraction(
        entities=list(entities.values()),
        relationships=list(relationships.values()),
        dropped_entities=dropped,
        unresolved_relationships=unresolved,
        self_loops=self_loops,
        undatable_relative=undatable,
        lifted_dates=lifted,
        date_nodes=date_nodes,
    )


def mention_counts(parts: Iterable[MappedExtraction]) -> Mapping[EntityId, int]:
    """How many of `parts` reported each entity, keyed by id.

    **A mention is one chunk's report of the entity, not one occurrence of its
    name.** `map_extraction` has already deduplicated within a single chunk's
    answer -- gleaning passes included, since those are folded into one
    `Extraction` before mapping -- so a chunk contributes at most `1` however
    many times the model listed the entity or the text spelled it. The number
    is therefore "in how many chunks did this entity appear", which is the
    same quantity GraphRAG's node `frequency` carries in its NLP path, and it
    is **not** a term frequency: an entity named forty times in one chunk and
    once in another counts `2`.

    It follows that a count is bounded by the number of chunks whose model
    call succeeded, and that it moves when the chunker's window size moves.
    The number is per-run and per-document: two documents' counts are not
    comparable, because the denominators are different documents split
    different numbers of ways. A corpus-level statistic -- the one a
    local-mutual-information edge weight would need -- is a different
    quantity that would have to be summed by a projection over many runs; see
    BACKLOG B143.

    Entities the domain *refused* are absent rather than zero. A dropped row
    never becomes an `Entity` and so never has an `EntityId` to key on;
    `MappedExtraction.dropped_entities` is where it is counted.

    Args:
        parts: The same `MappedExtraction`s given to `merge_extractions`, in
            any order -- addition is commutative, so the counts are too.

    Returns:
        A read-only mapping. Its key set is exactly the id set of
        `merge_extractions(parts).entities`, and every value is `>= 1`: both
        functions read `part.entities`, so an id reaches one iff it reaches
        the other.
    """
    counts: dict[EntityId, int] = {}
    for part in parts:
        for entity in part.entities:
            counts[entity.id] = counts.get(entity.id, 0) + 1
    return MappingProxyType(counts)
