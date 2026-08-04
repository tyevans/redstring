"""Chunk, extract, merge, emit. Extraction's whole job, on domain types.

```
SourceDocument -> Chunker -> LlmProvider per chunk -> map -> merge
               -> Document.record_extraction -> DocumentExtracted
```

## No store is reachable from here, and that is the point of the slice

This module writes nothing. It produces an event on the `Document` aggregate
and stops; `kg_builder.projections` is what puts entities into a `GraphStore`.
Wanting a store reference here is the signal that the thing this
re-architecture exists to remove is growing back, and it would look entirely
reasonable in review -- so
`tests/unit/extraction/test_pipeline.py::TestNoStoreReachesExtraction`
asserts the constructor has no store-shaped parameter at all.

The layered contract backs it from the other side: `extraction` and `llm` are
siblings, so this module can reach `kg_builder.ports.llm_provider` and never
`kg_builder.llm.adapters`.

## A partial extraction must not be recorded as a whole one

This is the sharpest edge in the module, and it comes from an interaction
rather than from either half alone.

`skip_failed_chunks` makes a partial result *available*, which is a
legitimate thing to want: nine chunks of ten is better than nothing for a
caller who knows that is what they have. But `Document.record_extraction` is
idempotent **per model version**, so writing that partial result marks the
version as extracted -- and the retry that would have repaired it returns
`None`, forever, silently. The gap is then permanent and invisible.

So `record` refuses a result with `failed_chunks > 0` unless the caller passes
`allow_partial=True`. Both halves stay available; what is removed is the
chance of getting the combination by accident.

The default is to raise on the first failed chunk, for the reason the
`LlmProvider` port already gives: an empty extraction and a failed one are
indistinguishable downstream, and only one of them is an answer.

## Chunks are extracted one at a time

Deliberate, and cheap to change if measurement asks for it. The merge is
order-independent and idempotent, both proved by property tests, so nothing
about correctness depends on the sequence -- but the reference deployment is a
single-GPU llama.cpp server that processes one request at a time, and firing
ten concurrent requests at it converts a queue into ten timeouts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, NamedTuple

from kg_builder.domain.exceptions import KgBuilderError, LlmProviderError
from kg_builder.extraction.chunkers import SlidingWindowChunker
from kg_builder.extraction.mapping import MappedExtraction, map_extraction
from kg_builder.extraction.merging import merge_extractions
from kg_builder.extraction.schema import Extraction

if TYPE_CHECKING:
    from kg_builder.aggregates.document import Document
    from kg_builder.domain.entity import Entity
    from kg_builder.domain.ids import TenantId
    from kg_builder.domain.relationship import Relationship
    from kg_builder.domain.source import SourceDocument
    from kg_builder.events.document import DocumentExtracted
    from kg_builder.extraction.protocols import Chunker
    from kg_builder.ports.llm_provider import LlmProvider

#: What the model is told before it is shown a chunk.
#:
#: Short on purpose. The *shape* of the answer is already pinned by the JSON
#: schema `Extraction` generates, and repeating it in prose only creates a
#: second specification that can disagree with the first. What prose is good
#: for is the part a schema cannot express: that a relationship's endpoints
#: must be entities the model also listed, which is the single largest source
#: of unresolved edges.
DEFAULT_SYSTEM_PROMPT: Final = (
    "You extract a knowledge graph from text.\n"
    "\n"
    "List every entity the text names: people, organisations, places, works, "
    "concepts, events. Use the name exactly as the text spells it.\n"
    "\n"
    "Then list the relationships the text states between them. Every "
    "relationship's source_name and target_name MUST be an entity you listed "
    "above, spelled the same way. Do not relate an entity to itself.\n"
    "\n"
    "Extract only what the text says. Do not add knowledge of your own, and "
    "do not guess at facts the text leaves out."
)


class PartialExtractionError(KgBuilderError):
    """A run with failed chunks was about to be recorded as a complete one.

    Carries the counts so a caller deciding whether to pass `allow_partial`
    can see the size of the hole rather than only that there is one.
    """

    def __init__(self, *, source_id: str, failed_chunks: int, total_chunks: int) -> None:
        self.source_id = source_id
        self.failed_chunks = failed_chunks
        self.total_chunks = total_chunks
        super().__init__(
            f"refusing to record {source_id!r} as extracted: {failed_chunks} of "
            f"{total_chunks} chunks failed. Recording it would mark this model "
            f"version done and make the retry a silent no-op. Pass "
            f"allow_partial=True if an incomplete extraction is what you want."
        )


class PipelineResult(NamedTuple):
    """Everything the run found, plus how much of the document it actually saw.

    Restates `MappedExtraction`'s fields rather than extending it, because
    subclassing a `NamedTuple` does not add fields -- it silently produces a
    type whose extra annotations are class attributes the constructor refuses.
    A frozen dataclass would compose; the tuple shape is kept for consistency
    with `MappedExtraction`, which callers already unpack.
    """

    entities: list[Entity]
    relationships: list[Relationship]
    dropped_entities: int = 0
    unresolved_relationships: int = 0
    self_loops: int = 0
    #: Chunks whose model call failed and were skipped. Always 0 unless
    #: `skip_failed_chunks` is on, because otherwise the failure propagates.
    failed_chunks: int = 0
    #: Chunks the document was split into. `failed_chunks == total_chunks`
    #: means nothing at all was extracted, which reads very differently from
    #: one bad chunk in fifty.
    total_chunks: int = 0


class ExtractionPipeline:
    """Turns one `SourceDocument` into one `DocumentExtracted`."""

    def __init__(
        self,
        provider: LlmProvider,
        *,
        chunker: Chunker | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        skip_failed_chunks: bool = False,
    ) -> None:
        """Assemble a pipeline.

        Args:
            provider: What to ask. Its `model` becomes both the entities'
                provenance and the `model_version` `Document` keys
                idempotency on.
            chunker: How to split. A `SlidingWindowChunker` with its own
                defaults when None -- a default rather than a required
                argument because every caller wants a chunker and almost none
                has an opinion about which.
            system_prompt: What the model is told. A constructor argument
                because domain schemas supply their own, and because a
                provider that substituted a default of its own would make two
                callers passing the same text get different answers.
            skip_failed_chunks: Continue past a chunk whose model call
                failed, counting it. Off by default; see the module
                docstring, and note that `record` still refuses the result
                unless asked twice.
        """
        self._provider = provider
        self._chunker = chunker if chunker is not None else SlidingWindowChunker()
        self._system_prompt = system_prompt
        self._skip_failed = skip_failed_chunks

    @property
    def system_prompt(self) -> str:
        """What this pipeline tells the model. Readable so a caller can log it."""
        return self._system_prompt

    async def extract(self, document: SourceDocument, tenant_id: TenantId) -> PipelineResult:
        """Extract `document` without recording anything.

        Args:
            document: The content. `SourceDocument` already refuses blank
                text, so there is no empty-document case to handle here.
            tenant_id: Applied to every entity and relationship produced.

        Returns:
            A `PipelineResult`. Entities are deduplicated across chunks;
            `failed_chunks` and the three `MappedExtraction` counters say what
            did not survive.

        Raises:
            LlmProviderError: A chunk's model call failed and
                `skip_failed_chunks` is off.
        """
        chunks = self._chunker.chunk(document.text).chunks
        parts: list[MappedExtraction] = []
        failed = 0

        for chunk in chunks:
            try:
                answer = await self._provider.extract(
                    chunk.text, Extraction, system_prompt=self._system_prompt
                )
            except LlmProviderError:
                if not self._skip_failed:
                    raise
                failed += 1
                continue
            parts.append(
                map_extraction(
                    answer,
                    tenant_id=tenant_id,
                    source_id=document.id,
                    model=self._provider.model,
                )
            )

        merged = merge_extractions(parts)
        return PipelineResult(
            entities=merged.entities,
            relationships=merged.relationships,
            dropped_entities=merged.dropped_entities,
            unresolved_relationships=merged.unresolved_relationships,
            self_loops=merged.self_loops,
            failed_chunks=failed,
            total_chunks=len(chunks),
        )

    async def record(
        self,
        aggregate: Document,
        document: SourceDocument,
        tenant_id: TenantId,
        *,
        allow_partial: bool = False,
    ) -> DocumentExtracted | None:
        """Extract `document` and record the run on `aggregate`.

        Args:
            aggregate: The `Document` whose stream this run belongs to.
                Loaded and saved by the caller -- this method neither reads
                nor writes an event store, it only asks the aggregate for an
                event.
            document: The content.
            tenant_id: The tenant the run belongs to.
            allow_partial: Record even though chunks failed. See below.

        Returns:
            The `DocumentExtracted`, or `None` when this document has already
            been extracted under this provider's model. `None` is the
            *expected* outcome of a retry, not an error.

            An extraction that found nothing still returns an event. "This
            document held nothing" is a finding, and omitting it would leave
            the document looking un-extracted so that every backfill retried
            it forever.

        Raises:
            PartialExtractionError: Chunks failed and `allow_partial` is
                False. Nothing is recorded -- the aggregate is untouched, so
                the refusal cannot itself cause the damage it prevents.
        """
        result = await self.extract(document, tenant_id)
        if result.failed_chunks and not allow_partial:
            raise PartialExtractionError(
                source_id=document.id,
                failed_chunks=result.failed_chunks,
                total_chunks=result.total_chunks,
            )
        return aggregate.record_extraction(
            tenant_id=tenant_id,
            source_id=document.id,
            model_version=self._provider.model,
            entities=result.entities,
            relationships=result.relationships,
        )
