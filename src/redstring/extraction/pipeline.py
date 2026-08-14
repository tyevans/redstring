"""Chunk, extract, merge, emit. Extraction's whole job, on domain types.

```
SourceDocument -> Chunker -> LlmProvider per chunk -> map -> merge
               -> Document.record_extraction -> DocumentExtracted
```

## No store is reachable from here, and that is the point of the slice

This module writes nothing. It produces an event on the `Document` aggregate
and stops; `redstring.projections` is what puts entities into a `GraphStore`.
Wanting a store reference here is the signal that the thing this
re-architecture exists to remove is growing back, and it would look entirely
reasonable in review -- so
`tests/unit/extraction/test_pipeline.py::TestNoStoreReachesExtraction`
asserts the constructor has no store-shaped parameter at all.

The layered contract backs it from the other side: `extraction` and `llm` are
siblings, so this module can reach `redstring.ports.llm_provider` and never
`redstring.llm.adapters`.

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

## Chunks are extracted in bounded wavefront batches

Measurement asked: a 33k-character document is 14 serial calls at roughly 24s
each, and the merge is order-independent and idempotent -- both proved by
property tests -- so nothing about correctness depends on the sequence, only
the caller's inference backend decides how many requests it can absorb at
once. `concurrency` (default `1`) is that bound. Chunks are grouped into
consecutive batches of that size; every chunk in a batch is sent with the same
prompt, computed once before the batch runs, and `asyncio.gather` fires the
batch with `return_exceptions=True` so one chunk's failure does not cancel
siblings whose answers are already paid for -- `skip_failed_chunks` still
decides whether a failure propagates or is counted, and anything that is not
an `LlmProviderError` is re-raised unchanged. Carryover is folded in from the
batch's results afterwards, in chunk order, never as each call returns:
updating on completion would make a chunk's prompt depend on which sibling
happened to finish first, so two runs of the same document could disagree with
each other.

`concurrency=1` is not a special case of this -- it is the same code with a
batch size of one, and is byte-identical to the pipeline before this
parameter existed: the same calls, the same prompts, in the same order. That
is what makes the knob a measurement rather than a rewrite, and it is why the
default stays `1`: a single-GPU llama.cpp server that processes one request at
a time turns ten concurrent requests into ten timeouts, and this module has no
way to know what is on the other end of `LlmProvider` unless the caller says
so.

## Each chunk is told what the ones before it found

Not the whole prompt, and not the document: a bounded list of
`(name, entity_type)` pairs appended to the system prompt, so a chunk that
says "Lovelace" where an earlier one said "Ada Lovelace" is asked to spell it
the earlier way. Identity here is derived from the name -- see
`redstring.extraction.mapping.entity_id_for` -- so naming drift at a chunk
boundary is not cosmetic: it manufactures a second entity that this module's
fold cannot combine and that `consolidation` then pays a model call to
resolve.

`redstring.extraction.carryover` holds the mechanism and the reasoning,
including why the list goes in the system prompt rather than into the chunk
and why the bound keeps the most recent rather than the most frequent.

**`system_prompt` reports the base and not what any particular chunk was
sent.** A caller logging it sees the prompt the pipeline was configured with;
the carryover block is per-chunk and is not configuration.

## A chunk can be asked twice, and is not by default

`gleanings` shows a chunk's own answer back to the model and asks what it
missed -- GraphRAG's gleaning loop, Graphiti's reflexion step. It is off by
default because it is one extra model call per chunk per pass and this
pipeline is sequential over chunks: `gleanings=1` roughly doubles a run.
`redstring.extraction.gleaning` holds the prompt and the fold, including why
the two answers are combined as `Extraction`s rather than after mapping.

## The chunking is carried out, not stored

`PipelineResult` gains the passages themselves and the signature they were
produced under. This module still writes to nothing: the passages are payload
on the way to `Document.record_chunking`, exactly as the entities are payload
on the way to `record_extraction`, and `redstring.projections.chunk` is what
puts them in a `ChunkStore`.

**Which chunk produced which entity is captured before the merge, and there
is no other place it could be.** `merge_extractions` folds every chunk's
`MappedExtraction` into one and the chunk boundary is gone after it -- by
design, since deduplicating what overlapping windows both reported is the
whole point. But each chunk's ids are in hand *at the moment its answer is
mapped*, one loop iteration earlier, so nothing about `merging.py` or
`mapping.py` had to change: the links are read off `map_extraction`'s return
value in the loop that already exists.

A chunk whose model call failed under `skip_failed_chunks` is still recorded
as a passage, with no entity links. The corpus is meant to be a faithful
split of the document -- a hole in it is a passage that can never be
retrieved -- and the alternative reading of an empty `entity_ids` is the one
`StoredChunk` explicitly refuses to support.

The signature is `f"{chunker_type}:{digest}:{model_version}"`. The trailing
model version is what keeps this path's key space distinct from
`index_documents`'s; see `redstring.aggregates.document`.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final, NamedTuple

from redstring.domain.exceptions import LlmProviderError, RedstringError
from redstring.extraction.carryover import DEFAULT_CARRYOVER_ENTITIES, Carryover
from redstring.extraction.chunkers import SlidingWindowChunker
from redstring.extraction.corpus import chunking_digest, stored_chunks
from redstring.extraction.gleaning import combine, found_nothing, gleaning_prompt
from redstring.extraction.limiter import CallLimiter
from redstring.extraction.mapping import MappedExtraction, map_extraction
from redstring.extraction.merging import merge_extractions
from redstring.extraction.schema import Extraction

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from datetime import datetime

    from redstring.aggregates.document import Document
    from redstring.domain.chunk import StoredChunk
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, TenantId
    from redstring.domain.relationship import Relationship
    from redstring.domain.source import SourceDocument
    from redstring.events.document import DocumentExtracted
    from redstring.extraction.chunking import Chunk
    from redstring.extraction.protocols import Chunker
    from redstring.ports.llm_provider import LlmProvider

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


class PartialExtractionError(RedstringError):
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
    #: Entities whose relative temporal expression had no `published_at` to be
    #: read against. See `MappedExtraction`.
    undatable_relative: int = 0
    #: Chunks whose model call failed and were skipped. Always 0 unless
    #: `skip_failed_chunks` is on, because otherwise the failure propagates.
    failed_chunks: int = 0
    #: Chunks the document was split into. `failed_chunks == total_chunks`
    #: means nothing at all was extracted, which reads very differently from
    #: one bad chunk in fifty.
    total_chunks: int = 0
    #: The passages themselves, each carrying the ids of the entities *that*
    #: passage produced. Payload for `Document.record_chunking`; nothing here
    #: writes them anywhere.
    #:
    #: A tuple rather than a list, because a `NamedTuple` field's default is
    #: shared by every instance that takes it and a mutable one would be a
    #: default every caller could append to.
    #:
    #: Shorter than `total_chunks` when the document repeats a passage
    #: verbatim: two identical chunks are one content-addressed id. See
    #: `redstring.extraction.corpus.stored_chunks`.
    chunks: tuple[StoredChunk, ...] = ()
    #: What `Document.record_chunking` keys idempotency on for this run.
    #: Carries the model version, so an extraction and a bare indexing of one
    #: document never suppress each other.
    chunking_signature: str = ""
    #: Second-pass model calls that returned an answer. Always 0 unless
    #: `gleanings` is on. Less than `gleanings * total_chunks` whenever a pass
    #: found nothing and the loop stopped early, which is the ordinary case.
    gleaning_passes: int = 0
    #: Second-pass model calls that failed and were abandoned. Non-zero means
    #: some chunks got less scrutiny than asked for -- which is invisible in
    #: the output, since the output simply has fewer entities. Never raises;
    #: see `ExtractionPipeline.extract`.
    failed_gleanings: int = 0


def _batches(chunks: Sequence[Chunk], size: int) -> Iterator[Sequence[Chunk]]:
    """Consecutive groups of `size`, in order. `size=1` yields one chunk each."""
    for start in range(0, len(chunks), size):
        yield chunks[start : start + size]


class ExtractionPipeline:
    """Turns one `SourceDocument` into one `DocumentExtracted`."""

    def __init__(
        self,
        provider: LlmProvider,
        *,
        chunker: Chunker | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        skip_failed_chunks: bool = False,
        concurrency: int = 1,
        carryover_entities: int = DEFAULT_CARRYOVER_ENTITIES,
        gleanings: int = 0,
        schema: type[Extraction] = Extraction,
        limiter: CallLimiter | None = None,
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
            concurrency: How many chunks may be in flight at once. `1` --
                the default -- reproduces the serial pipeline byte for byte:
                the same calls, the same prompts, in the same order. Above
                `1`, chunks are extracted in consecutive batches of this
                size; every chunk in a batch shares one prompt, computed
                before the batch runs, and carryover is updated from the
                whole batch's results afterwards, in chunk order -- never as
                each call returns, which would make a chunk's prompt depend
                on which sibling happened to finish first. Refused below `1`
                at construction, for the same reason `carryover_entities`
                is: a bad limit belongs to the caller, not to the first
                document it is used on. This bounds the *batch size*, not
                calls in flight -- see `limiter`, which bounds that
                separately and can be narrower. `concurrency=8` with an
                explicit `limiter=CallLimiter(2)` batches eight chunks and
                admits two calls at a time; passing a limiter never changes
                the batch size this parameter sets.
            carryover_entities: How many previously-seen entities are named in
                the next chunk's prompt, so the model spells a recurring
                entity the way the earlier chunk spelled it. `0` disables it,
                which restores the byte-for-byte prompt of every run before
                this parameter existed. See `redstring.extraction.carryover`
                for why the default is on and why the bound is by recency.
            gleanings: How many times each chunk is shown its own answer and
                asked what it missed. `0` -- the default -- is one call per
                chunk, as before. Each pass is one more model call per chunk,
                so `1` doubles the run at worst; a pass that finds nothing
                stops the loop for that chunk. Off by default because recall
                is worth paying for and is not worth paying for by accident.
                See `redstring.extraction.gleaning`.
            schema: What the model is asked to fill in. `Extraction` -- the
                default -- lets it name any entity type it likes. A subclass
                from `redstring.extraction.constrained` admits only one
                domain's declared ids, and admits them at the *server's*
                decoder rather than in prose. A schema argument rather than a
                flag because the pipeline has no domain to consult: it is
                given a prompt, and this is the other half of the same
                decision, made by whoever chose the prompt.
            limiter: The ceiling every call against `provider` passes
                through -- the extraction call and the gleaning call alike.
                `CallLimiter(concurrency)` when omitted, so a caller who
                constructs this pipeline directly with `concurrency=4` and no
                limiter still gets a working ceiling of four. Pass one
                explicitly to bound this pipeline's calls jointly with a call
                this pipeline does not own -- `build_graph` does, so its
                embedding call shares the same limiter rather than getting a
                second one that would bound extraction and embedding
                separately at `concurrency` each. Bounds calls *in flight*,
                not batch size -- see `concurrency`, which sets that
                separately. A limiter narrower than `concurrency` is a
                legitimate, useful combination: it batches at `concurrency`
                and admits fewer than a batch's worth of calls at once.
        """
        self._provider = provider
        self._chunker = chunker if chunker is not None else SlidingWindowChunker()
        self._system_prompt = system_prompt
        self._skip_failed = skip_failed_chunks
        if concurrency < 1:
            raise ValueError(f"concurrency must be >= 1, got {concurrency}")
        self._concurrency = concurrency
        # Constructed and discarded, purely so a bad limit is refused here
        # rather than on the first `extract` -- a constructor argument that
        # validates a document later is a constructor argument that validates
        # in production. The rule itself has one declaration site, in
        # `Carryover`; this only makes it fire early.
        Carryover(carryover_entities)
        self._carryover_entities = carryover_entities
        if gleanings < 0:
            raise ValueError(f"gleanings must be >= 0, got {gleanings}")
        self._gleanings = gleanings
        self._schema = schema
        self._limiter = limiter if limiter is not None else CallLimiter(concurrency)

    @property
    def system_prompt(self) -> str:
        """What this pipeline tells the model. Readable so a caller can log it."""
        return self._system_prompt

    async def extract(
        self, document: SourceDocument, tenant_id: TenantId, *, observed_at: datetime
    ) -> PipelineResult:
        """Extract `document` without recording anything.

        `document.published_at` becomes the reference date every relative
        temporal expression is read against. That is the only place a vantage
        point enters the pipeline, and it comes from the caller's document
        rather than from a clock -- so a re-extraction of the same document
        dates its entities identically, however much later it runs. A document
        with no `published_at` simply loses its relative dates, counted on
        `undatable_relative`; see `redstring.domain.temporal_parsing`.

        Args:
            document: The content. `SourceDocument` already refuses blank
                text, so there is no empty-document case to handle here.
            tenant_id: Applied to every entity and relationship produced.
            observed_at: When this library was told, stamped onto every
                entity's `Provenance`. One instant for the whole document,
                captured by the caller -- one per chunk would make two
                entities from one document differ for no reason a reader could
                act on, and a clock here would break the same determinism
                `document.published_at` is read from the caller to preserve.

        Returns:
            A `PipelineResult`. Entities are deduplicated across chunks;
            `failed_chunks` and the four `MappedExtraction` counters say what
            did not survive. `chunks` carries the passages, each with the ids
            of the entities it produced, and `chunking_signature` is the key
            `Document.record_chunking` refuses a repeat under.

        Raises:
            LlmProviderError: A chunk's model call failed and
                `skip_failed_chunks` is off.
        """
        chunking = self._chunker.chunk(document.text)
        chunks = chunking.chunks
        parts: list[MappedExtraction] = []
        # Keyed on `chunk_index` rather than on position in `parts`, because a
        # failed chunk is skipped and every later chunk's position would then
        # be off by the number that failed before it -- attributing chunk
        # four's entities to chunk three.
        found_by_index: dict[int, list[EntityId]] = {}
        failed = 0
        # One per document and discarded with the run. See
        # `redstring.extraction.carryover` -- an instance that outlived this
        # call would be doing cross-document entity resolution silently.
        carryover = Carryover(self._carryover_entities)

        gleaned = 0
        failed_gleanings = 0

        for batch in _batches(chunks, self._concurrency):
            # One prompt for the whole batch, computed before any call in it
            # runs -- every chunk in the batch sees the same carryover. At
            # concurrency=1 a batch is one chunk, so this is exactly the
            # prompt the serial loop computed.
            prompt = self._system_prompt + carryover.block()
            results = await asyncio.gather(
                *(
                    self._extract_one(chunk, prompt, document, tenant_id, observed_at)
                    for chunk in batch
                ),
                return_exceptions=True,
            )
            for chunk, result in zip(batch, results, strict=True):
                if isinstance(result, LlmProviderError):
                    if not self._skip_failed:
                        raise result
                    failed += 1
                    continue
                if isinstance(result, BaseException):
                    raise result
                mapped, passes, glean_failures = result
                gleaned += passes
                failed_gleanings += glean_failures
                parts.append(mapped)
                # Here and not after the fold: `merge_extractions` deduplicates
                # across chunks and has no reason to remember which one
                # reported what. This is the last moment the answer and the
                # chunk that produced it are both in hand.
                found_by_index[chunk.chunk_index] = [entity.id for entity in mapped.entities]
            # A second pass, in chunk order, after every call in the batch has
            # completed -- not as each one returns. Updating on completion
            # would make a chunk's prompt depend on which sibling finished
            # first, so two runs of one document could disagree with each
            # other. At concurrency=1 this pass has one entry and matches the
            # serial loop exactly.
            # `batch` is not read here -- the pass exists for its order, which
            # `results` already carries, since `gather` returns in argument
            # order rather than completion order.
            for result in results:
                if not isinstance(result, BaseException):
                    # After mapping, not from the raw answer: the mapper is
                    # what normalizes a name and drops a row the domain
                    # refuses, and a carryover built from the raw answer would
                    # offer later chunks a spelling that no entity in this
                    # document actually has.
                    carryover.remember(result[0].entities)

        merged = merge_extractions(parts)
        passages = stored_chunks(
            chunking,
            tenant_id=tenant_id,
            source_id=document.id,
            entity_ids_by_index=found_by_index,
        )
        return PipelineResult(
            entities=merged.entities,
            relationships=merged.relationships,
            dropped_entities=merged.dropped_entities,
            unresolved_relationships=merged.unresolved_relationships,
            self_loops=merged.self_loops,
            undatable_relative=merged.undatable_relative,
            failed_chunks=failed,
            total_chunks=len(chunks),
            chunks=tuple(passages),
            chunking_signature=(
                f"{self._chunker.chunker_type}:{chunking_digest(chunking)}:{self._provider.model}"
            ),
            gleaning_passes=gleaned,
            failed_gleanings=failed_gleanings,
        )

    async def _extract_one(
        self,
        chunk: Chunk,
        prompt: str,
        document: SourceDocument,
        tenant_id: TenantId,
        observed_at: datetime,
    ) -> tuple[MappedExtraction, int, int]:
        """One chunk's whole answer: model call, gleaning, mapping.

        Raises `LlmProviderError` on a failed model call, same as the serial
        loop did inline -- the caller decides whether that propagates or is
        counted, via `skip_failed_chunks`. Everything after the call is
        unchanged from before batching existed; this is that code moved, not
        rewritten, so a batch of one behaves exactly as the loop body used to.
        """
        async with self._limiter:
            answer = await self._provider.extract(chunk.text, self._schema, system_prompt=prompt)
        answer, passes, glean_failures = await self._glean(chunk.text, prompt, answer)
        mapped = map_extraction(
            answer,
            tenant_id=tenant_id,
            source_id=document.id,
            model=self._provider.model,
            reference_date=document.published_at,
            observed_at=observed_at,
        )
        return mapped, passes, glean_failures

    async def _glean(
        self, text: str, prompt: str, answer: Extraction
    ) -> tuple[Extraction, int, int]:
        """Ask again, up to `gleanings` times, and fold each answer in.

        Returns the accumulated answer, how many passes returned something,
        and how many failed.

        **A failed gleaning never propagates, whatever `skip_failed_chunks`
        says.** The two are not the same failure: a failed *chunk* is a hole
        in the document, while a failed gleaning is a chunk that got one pass
        instead of two -- there is a complete first answer in hand, and
        discarding it because an optional second look failed would trade a
        smaller extraction for none at all. It is counted rather than logged
        so the degradation has a number, since fewer entities is exactly what
        a successful run also looks like.

        The stop condition is on the *pass*, not on the accumulated answer:
        "this pass added nothing" is the signal that more passes are wasted,
        and the accumulated answer is non-empty from the first pass onwards.
        """
        passes = 0
        failures = 0
        for _ in range(self._gleanings):
            try:
                async with self._limiter:
                    extra = await self._provider.extract(
                        text, self._schema, system_prompt=gleaning_prompt(prompt, answer)
                    )
            except LlmProviderError:
                failures += 1
                break
            passes += 1
            if found_nothing(extra):
                break
            answer = combine(answer, extra)
        return answer, passes, failures

    async def record(
        self,
        aggregate: Document,
        document: SourceDocument,
        tenant_id: TenantId,
        *,
        allow_partial: bool = False,
        observed_at: datetime | None = None,
        result: PipelineResult | None = None,
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
            observed_at: The observation instant, when this method has to run
                the extraction itself. Required in that case and ignored
                otherwise -- a supplied `result` already carries the instant
                its own caller chose, and taking a second one here would be
                two declaration sites for one fact. `None` with no `result` is
                refused rather than defaulted to a clock: this layer is below
                `composition`, and defaulting is how the determinism
                `observed_at` exists to provide would leak away one caller at
                a time.
            result: An extraction of this document already in hand. Supplied
                by a caller that needed the `PipelineResult`'s counters as
                well as the event -- `redstring.composition.build_graph` is
                the one in the library. Without it that caller would call
                `extract` and then this method, and pay the model twice for
                one document; the second run produces the same entities, so
                nothing about the resulting graph would show it and only the
                bill would.

                Passing a result for a *different* document is the one way to
                misuse this, and it does not need a guard here:
                `DocumentExtracted` validates that every entity's `source_id`
                matches the event's, so the aggregate refuses the event with a
                `ValueError` naming both documents. Checked, not assumed --
                `TestRecordRefusesAResultFromAnotherDocument`. The parameter
                is still keyword-only, because a caller should have to mean
                it.

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
            ValueError: Neither `result` nor `observed_at` was given, so there
                is an extraction to run and no instant to stamp it with.
        """
        if result is None:
            if observed_at is None:
                raise ValueError(
                    "record() must be given either a `result` or an `observed_at`: "
                    "extracting here needs an observation instant, and this layer "
                    "reads no clock"
                )
            result = await self.extract(document, tenant_id, observed_at=observed_at)
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
