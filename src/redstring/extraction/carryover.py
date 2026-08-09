"""What one chunk tells the next one, and the several things it must not.

A chunker splits a document and the pipeline extracts each piece alone, so
every chunk after the first begins mid-argument. The text says "she" where
chunk one said "Ada Lovelace", or "the company" where chunk one said "the
Analytical Engine Company". A model reading that piece in isolation does the
best available thing and names the entity as the text names it.

**For this library that is worse than a stylistic wobble, because identity is
derived from the name.** `mapping.entity_id_for` hashes
`(tenant, source, entity_type, normalized_name)`, so "Lovelace" and "Ada
Lovelace" are two ids, `merge_extractions` has no basis to combine them --
they are not the same entity by construction, which is the only kind of
sameness that fold claims -- and the pair travels all the way to
`consolidation`, where deciding they are one person costs a model call and an
`EntitiesMerged`. Naming drift at a chunk boundary is therefore paid for
twice: once as a duplicate entity, once as the judgement to remove it.

Graphiti reaches for the same fix from a different direction (it extracts each
episode with the previous four messages in view) and so does the overlap in
`SlidingWindowChunker` -- but overlap only helps a sentence *spanning* the
boundary, and the antecedent is usually many paragraphs back.

## Why this goes in the system prompt and not into the chunk

Two reasons, and the second is the one that would bite.

The list is an instruction about how to name things, not content to extract
from. Prepending it to the chunk makes it indistinguishable from the document,
and the failure mode is exact: the model reports every carried name as an
entity of the current chunk, `PipelineResult.chunks` then attributes those
entities to a passage that never mentioned them, and a chunk retrieved for one
of them does not contain it.

The second reason is that `FakeLlmProvider(by_substring=...)` answers on the
*text*. A carryover written into the text would change which canned answer
each chunk receives, so every chunking and merging test in the suite would
start exercising a different scenario than it was written for -- silently,
since they would still pass.

## The instruction is half the mechanism

A bare list of names invites a model to list them. The block therefore states
the negative explicitly, and that sentence is load-bearing rather than
polite: without it this feature converts a naming problem into a hallucination
problem, which is a strictly worse trade because a duplicate is at least
visible.

## Bounded, and bounded by recency

A 200-chunk document mentions thousands of entities and a prompt cannot carry
them. The bound keeps the **most recently seen**, which is the right side to
keep for what this is fixing: an unresolved pronoun refers to something named
nearby, and an entity last mentioned eighty chunks ago is not what "she" means
here. It is admittedly the wrong side for a protagonist named once in chapter
one -- `BACKLOG.md` B70 records what a frequency-weighted bound would take.

Order is insertion order, oldest first, so the same document produces the same
prompt on every run. Nothing here reads a clock, a set, or a hash iteration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from redstring.domain.normalization import normalize_name

if TYPE_CHECKING:
    from collections.abc import Iterable

    from redstring.domain.entity import Entity

#: How many previously-seen entities reach the next chunk's prompt.
#:
#: Sized against the prompt rather than against the document: thirty-two names
#: with their types is a few hundred tokens beside a 3000-character chunk,
#: which is a cost worth paying for consistent naming and would not be at ten
#: times the size. A model asked to keep track of four hundred names is also
#: not obviously better at keeping track of any of them.
DEFAULT_CARRYOVER_ENTITIES = 32

_HEADING = "Entities already identified in earlier parts of this document:"

#: Stated after the list rather than before it, because the list is what the
#: model has just read and the instruction has to survive it.
_INSTRUCTION = (
    "If the text below refers to any of these, spell it exactly as it is "
    "spelled above and give it the same type. Do NOT list an entity unless "
    "the text below actually mentions it -- this list is about spelling, not "
    "about what is present."
)


class Carryover:
    """The entities seen so far, bounded, in the order they were first seen.

    Mutable and single-document: one instance belongs to one run of
    `ExtractionPipeline.extract` and is discarded with it. It is deliberately
    not a store, not shared between documents, and not reachable from
    anything that persists -- carrying names across documents would be
    cross-document entity resolution done invisibly, which is exactly what
    `merging.py` declines to do and `consolidation` exists to do out loud.
    """

    def __init__(self, limit: int = DEFAULT_CARRYOVER_ENTITIES) -> None:
        """Build an empty carryover.

        Args:
            limit: How many mentions reach the prompt. Zero disables the
                block entirely, which is what `ExtractionPipeline` passes when
                a caller turns the feature off -- so "off" is one code path
                with the feature rather than a second one without it.

        Raises:
            ValueError: `limit` is negative. Not clamped: a negative limit is
                a caller bug that would otherwise read as "off", and silently
                disabling a quality feature is the failure this whole module
                is about.
        """
        if limit < 0:
            raise ValueError(f"carryover limit must be >= 0, got {limit}")
        self._limit = limit
        # Keyed on the *normalized* name and type, so "Ada Lovelace" and "ada
        # lovelace" occupy one slot rather than two -- the key `entity_id_for`
        # derives identity from, so two entries here would be one entity
        # listed twice. The value keeps the first spelling seen, which is the
        # spelling later chunks are being asked to converge on.
        self._seen: dict[tuple[str, str], tuple[str, str]] = {}

    def remember(self, entities: Iterable[Entity]) -> None:
        """Record what a chunk found, for the chunks after it.

        A name already present keeps its original position and its original
        spelling. Re-inserting on every mention would make a name mentioned in
        every chunk permanently "most recent" and evict the ones that are
        genuinely new, which inverts what the bound is for.
        """
        for entity in entities:
            key = (normalize_name(entity.name), entity.entity_type)
            if key not in self._seen:
                self._seen[key] = (entity.name, entity.entity_type)

    def mentions(self) -> tuple[tuple[str, str], ...]:
        """The `(name, type)` pairs that would reach a prompt, oldest first."""
        if self._limit == 0:
            return ()
        return tuple(self._seen.values())[-self._limit :]

    def block(self) -> str:
        """The text to append to a system prompt, or `""` when there is none.

        Empty for an empty carryover *and* for a zero limit, and the caller
        appends it unconditionally -- so the prompt for the first chunk of a
        document is byte-identical to the prompt this pipeline would send with
        the feature off. That is what makes a carryover run comparable to a
        baseline one on the first chunk rather than only nearly so.
        """
        mentions = self.mentions()
        if not mentions:
            return ""
        listed = "\n".join(f"- {name} ({entity_type})" for name, entity_type in mentions)
        return f"\n\n{_HEADING}\n{listed}\n\n{_INSTRUCTION}"
