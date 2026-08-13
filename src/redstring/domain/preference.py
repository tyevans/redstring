"""The total orders that decide which of two versions of a thing survives.

Both are used wherever one logical entity or edge has been described twice and
one description has to win. That happens in three places now -- deduplicating
within one model answer, folding across a document's chunks, and choosing
between parallel edges a merge would create -- and the reason they live here,
below all three, is that **two definitions are two tie-breaks**. "Dedup within
one answer" disagreeing with "dedup across chunks" about which mention wins is
a difference nobody would go looking for; that is not hypothetical, it happened
and cost a fix round.

## Total is the whole point

Each order ends in components that carry no meaning. They are there so that no
two distinct objects compare equal, because the moment two do, the answer falls
through to "keep the one already there" and depends on arrival order -- in a
durable, replayable event log.

cosmic-ray is what finds this, and it finds it as a `>` mutated to `>=` that
survives: with a total order the two spellings genuinely agree, and with a
partial one they disagree only on inputs no test happened to use.

## Scope, and what each order does *not* cover

Both are total **within an id bucket**, where the fields feeding the id are
fixed by construction. `relationship_preference` in particular does not
distinguish two edges with different ids -- inside extraction it never has to,
because the id is what defines the bucket.

Consolidation's duplicate detection is the case where that is not enough, and
it composes rather than redefines: see
`redstring.consolidation.planning.duplicate_preference`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from redstring.domain.entity import Entity
    from redstring.domain.relationship import Relationship
    from redstring.domain.temporal import TemporalExtent


def preference(entity: Entity) -> tuple[float, bool, int, bool, str, str, str, int, str]:
    """A **total** order on two mappings of one entity. Higher wins.

    Total, and that is the whole point. An earlier version compared
    confidence alone and fell through to "keep the one already there", which
    is order-dependent exactly where it matters: two mentions of one entity
    carry the *same* confidence whenever the model declined to score them,
    which is the common case rather than the edge case. The same document
    would then map differently depending on the order the model happened to
    list things in.

    cosmic-ray found it. Mutating `>` to `>=` flipped which of two tied
    mentions survived and every test passed, because they all used distinct
    confidences -- the CLAUDE.md failure shape, where the input makes two
    candidate implementations agree.

    Confidence first, which is the only part anyone would design
    deliberately. Then whether the mention carries a date, which is the second
    deliberate part -- see below. The rest exist to make the order total:

    - description length, preferring the mention that said more, which is
      usually the chunk holding the whole sentence rather than its tail;
    - then whether a description was given *at all*, the description text,
      the name, and a stable rendering of `properties` -- all of which carry
      no meaning and are there purely so that no two distinct objects compare
      equal.

    The "at all" slot is not padding. `description or ""` maps `None` and
    `""` onto the same value, so two mentions differing only in which of
    those they carry tied on every field of the order and arrival order
    decided between them. The strengthened order-independence property in
    `test_merging.py` found it on its first run, with the minimal example
    `[("a", None)], [("a", "")]`.

    That last one is what makes the order genuinely total rather than merely
    long, and the argument for totality is worth stating precisely because it
    is what makes a `>` -> `>=` mutant *equivalent* here rather than live.

    Within one id bucket, every field of an `Entity` is in one of three
    groups:

    - **Fixed by the caller.** `_build_entity` sets `tenant_id`,
      `entity_type` and the whole of `provenance` except `confidence` --
      `source_id`, `extraction_method`, `model` and `observed_at` --
      identically for every mention in a bucket. `observed_at` is worth
      naming: it is one instant per document by construction, taken from
      `map_extraction`'s argument rather than a clock, so two mentions of one
      entity cannot disagree about it and it does not belong in the order.
      Were it read per chunk it would be an independently-supplied field and
      would have to be.
    - **Derived from fields the id already fixes.** `normalized_name` and
      `blocking_keys` are pure functions of `name` and `entity_type`, both of
      which are inputs to `entity_id_for` -- so two mentions in one bucket
      cannot disagree about them. This group is the one to check when adding
      a field: a derived field is safe, an independently-supplied one is not.
    - **Never populated.** `external_ids` and `provenance.source_text`.

    What is left -- `provenance.confidence`, `name`, `description`,
    `properties` and `temporal` -- is exactly what two mappings of one entity
    can disagree about, and all five are here.

    `temporal` joined that list in slice 8, when extraction began parsing the
    model's temporal expression into an extent. It is the second field to move
    out of "never populated", after `blocking_keys`, and unlike `blocking_keys`
    it is *not* derived from anything the id fixes: overlapping windows report
    one entity twice and only the window holding the date phrase can date it,
    so two mentions in one bucket disagree about it routinely. Preferring the
    dated mention is the deliberate part; the rendering in the tail exists,
    like the rest of it, only so that two distinct extents cannot tie.

    It sits **above** description length, not below, and that placement is the
    whole content of the flag. A date appears in one window; a description
    appears in every window that mentions the entity at all, usually longer in
    the one with more surrounding text. Below description length the flag is
    unreachable whenever the two mentions describe the entity differently --
    which is the common case -- and the fuller description wins while the date
    is discarded. A lost description costs a sentence; a lost date cannot be
    recovered from anything else in the payload.

    The first version of this had the flag below description length and a test
    that could not tell the difference: both mentions were undescribed, so the
    comparison fell through to the tail, where the empty-string rendering of
    "no extent" sorts below any real one and the dated mention won anyway.
    Deleting the flag entirely left that test green. `test_a_dated_mention_
    outranks_a_better_described_undated_one` is the input where the two
    implementations disagree.

    (An earlier version of this paragraph listed `blocking_keys` as never
    populated. That stopped being true when extraction began computing keys,
    and the conclusion survived only because the field is derived. The
    three-group form above is stated so the next added field is checked
    against a rule rather than against a list.)

    Property-level *merging* is still not attempted: the winner keeps its own
    `properties` and the loser's are discarded. That is BACKLOG B28.

    Shared with `redstring.extraction.merging`, which folds across chunks
    using this same order. Two definitions would be two tie-breaks, and
    "dedup within one model answer" disagreeing with "dedup across chunks"
    about which mention wins is a difference nobody would go looking for.
    """
    return (
        entity.provenance.confidence,
        entity.temporal is not None,
        len(entity.description or ""),
        entity.description is not None,
        entity.description or "",
        entity.name,
        _temporally(entity.temporal),
        *_stably(entity.properties),
    )


def _temporally(extent: TemporalExtent | None) -> str:
    """A deterministic, orderable rendering of an extent. `""` for none.

    Only the tail of the order reaches this: which of two *dated* mentions of
    one entity wins is arbitrary, because both read the same document and
    neither is more authoritative. What is not arbitrary is that the choice be
    the same on every run, which is what a canonical rendering buys.
    """
    if extent is None:
        return ""
    return json.dumps(extent.model_dump(mode="json"), sort_keys=True, default=repr)


def _stably(properties: Mapping[str, Any]) -> tuple[int, str]:
    """A deterministic, orderable rendering of a free-form property bag.

    Size first, so "the mention that said more" wins by the same instinct
    description length encodes for entities. Then a canonical JSON rendering,
    which exists purely to make the order **total**: two bags of equal size
    that are not equal must still compare unequal, or the comparison falls
    through to "keep the one already there" and the answer depends on arrival
    order.

    `sort_keys=True` so two equal dicts built in different key orders render
    identically. `default=repr` because these values come from a model and the
    port only guarantees they parsed as JSON *once* -- a caller mapping a
    hand-built `Extraction` can put anything in here, and a `TypeError` from
    deep inside a sort would be an appalling way to find out.
    """
    return len(properties), json.dumps(properties, sort_keys=True, default=repr)


def relationship_preference(relationship: Relationship) -> tuple[float, int, str]:
    """A **total** order on two mappings of one edge. Higher wins.

    Total over everything that can vary, and the argument is short: within one
    id bucket the endpoints and the relationship type are fixed, because all
    three are inputs to `_relationship_id_for`. `tenant_id` is fixed by the
    caller. So `confidence` and `properties` are the only fields two mappings
    of one edge can disagree about, and both are here.

    The order this replaces was `(confidence, relationship_type)` -- and the
    type is constant inside every bucket, so the tuple degenerated to
    `(confidence,)`. Ties are the common case, because every relationship the
    model declines to score carries `DEFAULT_CONFIDENCE` and overlapping
    windows manufacture duplicate edges on purpose. Which `properties`
    survived was therefore decided by arrival order, which means the same
    document extracted twice could produce different `DocumentExtracted`
    payloads in a durable, replayable log.

    Shared with `redstring.extraction.merging` for the reason `preference`
    is: the within-answer and across-chunk deduplications must not disagree
    about which mention wins, and two definitions are two chances to.
    """
    return (relationship.confidence, *_stably(relationship.properties))
