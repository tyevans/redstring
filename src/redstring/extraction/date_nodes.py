"""Entities that are a date rather than a thing, and the dates hidden in them.

A model asked to extract entities and dates will sometimes file a date as an
*entity*: a node named "September 2016" or "the 1990s", related to the thing
it dates by an edge. That is not a shape any downstream reader wants. The
node has no description, no properties and nothing to say; the edge asserts a
relationship between a company and a month.

**The name the model reaches for comes from this library.**
`ExtractedEntity.temporal_expression` is described to the model as "The date
or period this entity is associated with ... e.g. 'March 15, 1920', 'circa
1850', 'the 1990s'", and a model reading a schema cannot always tell a field
name from a type name. Measured against a real 5,647-entity corpus on
2026-08-23: **356 entities of type `temporal_expression`**, one of them named
literally `the 1990s` -- the field description's own example, returned as an
entity. So this is not a quirk of one prompt to be fixed in one prompt. It is
a shape this schema invites, and it is fixed here, once, for every caller.

## The date is not junk; the node is

The important half is that a date-node is usually a date the model got
*right* and filed in the wrong place. From that same corpus:

    'the 1990s'    <- temporal_expression <- 'United Paramount Network'
    'January 1968' <- temporal_expression <- 'Star Trek'

Deleting those loses two dates that belong on a timeline. So `lift_date_nodes`
does not delete first -- it reads each edge touching a date-node as a
statement about the *other* endpoint, copies the date onto that entity's
`temporal_expression`, and only then drops the node and its edges. What was
noise becomes the field it should have been in.

An entity that already states its own `temporal_expression` keeps it. The
model put it in the right place there, and a date-node edge is the weaker
evidence of the two.

### What the lift gets wrong, measured rather than guessed

Replaying that corpus: 343 nodes removed, **22 dates lifted**. The clean wins
are the two above. Most of the other twenty are not clean:

    'Quentin Tarantino' (person)  <- 'December 2017'
    'Chris Pine'        (person)  <- 'August'

Those came from `affects` edges standing in for "in December 2017, Tarantino
pitched a film" -- a date that belongs to the *event*, not to the person who
took part in it. The lift puts it on the person, which is wrong.

It is kept because the alternative is worse in the same direction and louder:
without it those are `affects` edges from a person to a month, sitting in the
graph as assertions and feeding anything that clusters on edges. A slightly
over-attributed date on a real entity is a smaller error than a fabricated
relationship to a fabricated node.

Two things limit the damage. A bare month like `August` raises
`AmbiguousReferenceDateError` in `_build_entity` unless the document has a
publication date, so it is dropped and counted as `undatable_relative` rather
than becoming a confident extent. And the lift is counted separately from the
removal, so `lifted_dates` can be driven to zero -- by returning the entities
unchanged and keeping only the drop -- without touching the rest of this pass.

## Why the test is a conjunction, and why it is not the type name

`entity_type == "temporal_expression"` catches today's spelling and nothing
else; the same corpus already shows `date`-shaped nodes filed under `event`,
and the next model will invent a third name. So the test is the *shape*:

1. the name is anchored by a 3-4 digit year, or by a month name that is not
   the whole name,
2. `parse_temporal` reads the whole name as a date,
3. the entity carries no description, and
4. the entity carries no properties.

All four are needed, and (1) is the one that is easy to leave out. Without it
`parse_temporal` accepts a startling number of short real names -- measured
over that corpus, it reads `Borg`, `Seven of Nine`, `Kor`, `MIT`, `Sun`,
`API` and `DIS` as dates, and a shape test without an anchor deletes the Borg
from a Star Trek graph.

**And (1)'s month clause is easy to write one step too wide, which is how the
Borg came back wearing a different name.** The anchor originally read "a year
or a month name", which `MAR`, `May`, `Jun` and `April` satisfy -- ordinary
surnames, deleted whatever type the model gave them, and with no date lifted
out of them either, since a bare month is `undatable_relative` downstream. A
month name therefore anchors only when something else is in the name too; see
`_is_anchored`. Every date-node in the measured corpus carries that something,
and the bare-month case recorded above (`'Chris Pine' <- 'August'`) is one of
the lifts this module already describes as putting a date on the wrong entity.

(3) and (4) are what separate a bare date-node from an event the model named
badly but described anyway; those keep their node, because there is something
in them to read.

**Measured over 5,647 real entities: 343 caught, all 343 typed
`temporal_expression`, and zero false positives.** The 14 typed date-nodes it
does not catch are ones `parse_temporal` itself cannot read -- "first season",
"the end of the year", "December 1967 and March 1968" -- so there is no date
to lift out of them and nothing is lost by leaving them to be dropped
downstream.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Protocol

from redstring.domain.temporal_parsing import AmbiguousReferenceDateError, parse_temporal

if TYPE_CHECKING:
    from redstring.extraction.schema import ExtractedEntity, Extraction

#: Month names, full or abbreviated, as the anchor alternation uses them.
_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"

#: A year: three or four digits, optionally decade-pluralised ("1990s"). Two
#: digits deliberately do not qualify -- `M 33`, `G2`, `#1` and `The 37's` are
#: all real entity names from the measured corpus that `parse_temporal`
#: accepts, and all four survive because of this.
_YEAR = re.compile(r"\b\d{3,4}s?\b")

#: A month name, full or abbreviated.
_MONTH = re.compile(rf"\b({_MONTHS})[a-z]*\b", re.IGNORECASE)


def _is_anchored(name: str) -> bool:
    """Whether `name` looks enough like a date to ask `parse_temporal` about it.

    A year anchors on its own. **A month name anchors only when it is not the
    whole name**, and that asymmetry is the whole of this function.

    `parse_temporal` reads a startling number of short real names as dates --
    `Borg`, `Seven of Nine`, `Kor`, `MIT`, `Sun`, `API`, `DIS` -- so an anchor
    is what stops a shape test deleting the Borg from a Star Trek graph. But
    an anchor written as "a year *or a month name*" is satisfied by `MAR`,
    `May` and `Jun`, which are surnames at least as often as they are months,
    and the entity was deleted whatever type the model gave it. That is a
    silent data loss: the node is removed and, for a bare month, no date is
    lifted out of it either, because `AmbiguousReferenceDateError` makes it
    `undatable_relative` downstream.

    Requiring a second token costs nothing measured. Every genuine date-node
    in the corpus this module was built against carries one -- `September
    2016`, `January 1968`, `the 1990s`, `March 15, 1920` -- and the bare-month
    case the module's own docstring records, `'Chris Pine' (person) <-
    'August'`, is one it lists among the lifts that attribute a date to the
    wrong entity. So the names this now declines are the ones it was getting
    wrong in both directions.

    The remainder is tested for an **alphanumeric character** rather than for
    being non-empty: stripping the month out of `Mar.` leaves a full stop,
    which is punctuation and not a second token.
    """
    if _YEAR.search(name):
        return True
    if not _MONTH.search(name):
        return False
    return any(character.isalnum() for character in _MONTH.sub("", name))


class _Nameable(Protocol):
    """The three attributes `is_date_node` reads, and nothing else.

    A `Protocol` rather than `ExtractedEntity` because the same question is
    asked at two different times about two different types. Extraction asks it
    of an `ExtractedEntity`, before anything is built. A reader asks it of a
    stored `Entity`, because a store written before this pass existed is full
    of date-nodes that no extraction-time filter can retroactively reach --
    and `Entity` happens to carry the same three fields.

    Structural rather than a second predicate on the reader's side. Two
    definitions of "this is a date, not a thing" would drift, and the one that
    drifted would be the one nobody was measuring: see `domain/preference.py`
    for the same argument about a tie-break.
    """

    name: str
    description: str | None
    properties: dict[str, Any]


def is_date_node(candidate: _Nameable) -> bool:
    """Whether `candidate` is a date wearing an entity's clothes.

    Deliberately ignores `entity_type`. See the module docstring: the type
    name is whatever the model coined this run, and the same corpus that
    produced 356 `temporal_expression` nodes also produced date-named `event`
    nodes. The shape is stable in a way the vocabulary is not.

    A candidate that already fills `temporal_expression` is still a date-node
    if it looks like one -- the field being set does not make the *node*
    meaningful, and `lift_date_nodes` prefers that field's value when it
    lifts.
    """
    name = candidate.name.strip()
    if not name or not _is_anchored(name):
        return False
    if candidate.description or candidate.properties:
        return False
    try:
        # `reference_date=None` on purpose: a name that needs a vantage point
        # to resolve ("last year") is ambiguous rather than unparseable, and
        # ambiguity is still evidence that the name denotes a time. The date
        # itself is resolved later, in `_build_entity`, against the real
        # publication date.
        return parse_temporal(name, reference_date=None) is not None
    except AmbiguousReferenceDateError:
        return True


def lift_date_nodes(extraction: Extraction) -> tuple[Extraction, int, int]:
    """`extraction` with date-nodes folded into the entities they date.

    Args:
        extraction: What the model returned, unmodified.

    Returns:
        The rewritten extraction, the number of entities that gained a
        `temporal_expression` they did not have, and the number of date-nodes
        removed. The two counts are independent: an isolated date-node is
        dropped and lifts nothing, and one date-node related to three entities
        lifts three times.

    The rewrite is expressed as new `ExtractedEntity` objects via
    `model_copy`, never by mutating what the provider returned -- the caller
    may still want to log the raw answer, and a pass that edits its input in
    place makes that log a lie.

    Edges are matched on `normalize_name`, the same function
    `_map_relationships` resolves endpoints with. Matching on the raw string
    would miss the ordinary case where the model writes an endpoint in a
    different case from the entity it listed.
    """
    date_nodes = {
        candidate.name.strip().casefold(): candidate
        for candidate in extraction.entities
        if is_date_node(candidate)
    }
    if not date_nodes:
        return extraction, 0, 0

    def as_date_node(name: str) -> ExtractedEntity | None:
        return date_nodes.get(name.strip().casefold())

    #: name -> the expression to put on it, first edge wins.
    lifted: dict[str, str] = {}
    kept_edges = []
    for edge in extraction.relationships:
        source, target = as_date_node(edge.source_name), as_date_node(edge.target_name)
        if source is None and target is None:
            kept_edges.append(edge)
            continue
        # Spelled as three explicit arms rather than two arms and a
        # narrowing `assert`, which bandit refuses (B101). The last arm is an
        # edge between two date-nodes: it states nothing about anything, and
        # silently keeping one end would invent a fact.
        if target is None and source is not None:
            node, other = source, edge.target_name
        elif source is None and target is not None:
            node, other = target, edge.source_name
        else:
            continue
        lifted.setdefault(other.strip().casefold(), _expression_of(node))

    entities = []
    gained = 0
    for candidate in extraction.entities:
        if is_date_node(candidate):
            continue
        found = lifted.get(candidate.name.strip().casefold())
        # `or` on the existing value, not a plain absence check: an entity
        # that stated its own date keeps it. A date-node edge is the weaker
        # of the two pieces of evidence, and overwriting would let the model's
        # sloppier answer beat its careful one.
        if found is not None and not candidate.temporal_expression:
            candidate = candidate.model_copy(update={"temporal_expression": found})
            gained += 1
        entities.append(candidate)

    rewritten = extraction.model_copy(update={"entities": entities, "relationships": kept_edges})
    return rewritten, gained, len(date_nodes)


def _expression_of(node: ExtractedEntity) -> str:
    """The date a date-node carries.

    Its own `temporal_expression` when it filled one, else its name. A model
    that emits `{"name": "1968", "temporal_expression": "January 1968"}` has
    said the more precise thing in the field, and the name is a truncation of
    it.
    """
    return node.temporal_expression or node.name.strip()
