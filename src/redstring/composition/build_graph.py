"""The one module allowed to know both the write model and the read model.

Nine slices built parts. This joins them:

```
SourceDocument -> ExtractionPipeline -> Document.record_extraction
               -> DocumentExtracted -> GraphProjection -> GraphStore
```

Every arrow already existed and was tested; nothing called them in sequence,
which is why `redstring.__init__` exported a version string and nothing else.

## Why this is a layer of its own

`extraction` may not import `projections`, and neither may import the other's
adapters -- that separation is what stops a store reference growing back into
the pipeline, and `tests/unit/extraction/test_pipeline.py::TestNoStoreReachesExtraction`
enforces it directly. But *somebody* has to hold both, or the library ships
two halves and a diagram. `composition` is the top layer of the import
contract for exactly that reason, and it is the only module in it: if a second
one appears, ask what it composes before adding it.

## What `build_graph` skips, and when that matters

It folds the event straight into the store instead of appending it to an event
store and replaying. That is the honest shape for a caller who has no event
store -- most callers, most of the time -- and it costs two things worth
stating plainly rather than discovering:

- **Idempotency is per call, not per document.** `Document.record_extraction`
  refuses a second extraction under the same `model_version`, but that refusal
  lives in the aggregate's *state*, and this function builds a fresh aggregate
  each time. Two `build_graph` calls for one document under one model will
  extract twice. The store still ends up right -- every projection write is an
  upsert -- but the model was paid for twice.
- **There is no log to rebuild from.** A store rebuilt from nothing is a store
  restored from backup.

**Both are removed by passing `event_store`**, which loads the aggregate from
that log and appends to it. That is not merely a convenience: the chunking
signature's whole design -- two write paths, two key spaces, so that indexing
a document and later extracting it does not suppress the entity links -- is
only *observable* across calls that share aggregate state. Without a log there
is no state and no refusal, so nothing behavioural can distinguish the design
from its opposite. See `index_documents`.

A caller who wants a log without this parameter appends `report.event` to an
`EventStore` themselves and drives `eventsource.replay` over the feed.
`report.event` is returned for precisely that, and it is the same object the
projection just consumed.

## `domain=AUTO` costs an extra model call, and says so

Classifying a document is a model call before the extraction calls. With
`domain=None` there is no classifier and no call; with an explicit domain
there is no call either. Only `AUTO` pays, and it pays once per document
rather than once per chunk -- the classifier sees the head of the text, not
every window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, NamedTuple

from eventsource.adapters.memory import InMemoryEventStore, InMemorySnapshotStore
from eventsource.domain.tenant_context import tenant_scope

from redstring.aggregates.document import Document
from redstring.aggregates.repositories import document_repository
from redstring.consolidation.candidates import CandidateFinder
from redstring.consolidation.policy import HIGH_SIMILARITY, LOW_SIMILARITY
from redstring.consolidation.service import ConsolidationService
from redstring.domain.exceptions import DimensionMismatchError, EmbeddingProviderError
from redstring.domain.limiter import CallLimiter
from redstring.domain.vector import VectorRecord
from redstring.events.streams import document_stream
from redstring.extraction.carryover import DEFAULT_CARRYOVER_ENTITIES
from redstring.extraction.classifier import ContentClassifier
from redstring.extraction.constrained import constrained_extraction_for
from redstring.extraction.domains.registry import get_domain_schema
from redstring.extraction.pipeline import DEFAULT_SYSTEM_PROMPT, ExtractionPipeline
from redstring.extraction.prompt_generator import domain_system_prompt
from redstring.extraction.schema import Extraction
from redstring.projections.chunk import ChunkProjection
from redstring.projections.graph import GraphProjection
from redstring.projections.vector import VectorProjection

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from eventsource.application.aggregates.tenant_repository import TenantAwareRepository
    from eventsource.ports.snapshots import SnapshotStore
    from eventsource.ports.store import AggregateStore

    from redstring.consolidation.protocols import CandidateSource, MergeAdjudicator
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, TenantId
    from redstring.domain.merge_strategy import PropertyMergePolicy
    from redstring.domain.source import SourceDocument
    from redstring.events.document import DocumentExtracted
    from redstring.events.merge import EntitiesMerged, MergeUndone
    from redstring.extraction.domains.models import DomainSchema
    from redstring.extraction.protocols import Chunker
    from redstring.ports.chunk_store import ChunkStore
    from redstring.ports.embedding_provider import EmbeddingProvider
    from redstring.ports.graph_store import GraphStore
    from redstring.ports.llm_provider import LlmProvider
    from redstring.ports.vector_store import VectorStore


class AutoDomain:
    """The type of `AUTO`. Public only because `build_graph`'s signature names it.

    A private `_Auto` would leave `domain: str | DomainSchema | AutoDomain | None`
    on the public surface, telling a caller who reads the signature that there
    is a type they cannot have -- see
    `tests/unit/test_public_surface_is_self_contained.py`. Nobody should
    construct one; use `AUTO`.
    """

    def __repr__(self) -> str:
        return "AUTO"


#: Pass as `domain=` to classify the document before extracting it.
#:
#: A sentinel object rather than the string `"auto"`, because `domain` also
#: takes domain ids and a real schema called "auto" would then be
#: unreachable -- the same trap `ScrapingJob.extraction_strategy` fell into,
#: where a magic string in a free-form field decided control flow.
AUTO: Final = AutoDomain()


@dataclass(frozen=True, slots=True)
class GraphBuildReport:
    """What one `build_graph` call extracted, and what it wrote."""

    #: The event the aggregate emitted, already applied to the store. `None`
    #: means nothing was recorded -- see `Document.record_extraction`. Append
    #: it to an event store if you have one.
    event: DocumentExtracted | None
    #: The domain whose prompt was used, or `None` for the default prompt.
    #: Worth having when `AUTO` chose it, because the choice is otherwise
    #: invisible and it is the first thing to look at when extraction for one
    #: document comes back oddly shaped.
    domain: str | None
    #: How sure the classifier was, and `None` when no classifier ran.
    #:
    #: **`0.0` means it gave up**, and that is the field's whole reason for
    #: existing. `ContentClassifier` falls back to `encyclopedia_wiki` on
    #: three paths -- a document under 100 characters is never sent at all, a
    #: below-threshold answer is replaced, and an `LlmProviderError` is
    #: swallowed -- and all three produce a `domain` that reads exactly like a
    #: confident classification. The confidence was computed and discarded;
    #: now it is not.
    #:
    #: `None` rather than `0.0` when `domain` was given or omitted, so a
    #: caller filtering for give-ups on `== 0.0` does not catch every run that
    #: named its own domain.
    domain_confidence: float | None
    entities: int
    relationships: int
    #: Chunks whose model call failed and were skipped. Non-zero only with
    #: `skip_failed_chunks`; see `ExtractionPipeline`.
    failed_chunks: int
    total_chunks: int
    #: Relationships the model stated between entities it did not list, and so
    #: could not be resolved to ids. A normal outcome, not an error, but a
    #: large number means the prompt is not landing.
    unresolved_relationships: int
    #: Entities written to a `VectorStore`, and zero when no embedding
    #: provider was given.
    #:
    #: **Not a repeat-suppression signal.** `Document.record_embeddings`
    #: refuses a second `EntitiesEmbedded` for the same model, but
    #: `build_graph` builds a fresh aggregate on every call -- the same shape
    #: that makes `event is None` unreachable here -- so re-running it
    #: re-embeds and reports the full count again. The store absorbs that:
    #: `upsert_many` is idempotent, so the second run rewrites identical
    #: vectors. It costs an embedding call, not correctness.
    embedded: int = 0
    #: Passages written to a `ChunkStore`, and **zero when no chunk store was
    #: given** -- which is the default, so a caller who wants a corpus has to
    #: ask for one. Distinct from `total_chunks`, which counts what the
    #: document was split into whether or not anything stored it, and which
    #: is larger when a document repeats a passage verbatim (two identical
    #: chunks share one content-addressed id).
    chunks_written: int = 0


async def build_graph(
    document: SourceDocument,
    *,
    provider: LlmProvider,
    store: GraphStore,
    tenant_id: TenantId,
    domain: str | DomainSchema | AutoDomain | None = None,
    chunker: Chunker | None = None,
    skip_failed_chunks: bool = False,
    allow_partial: bool = False,
    carryover_entities: int = DEFAULT_CARRYOVER_ENTITIES,
    gleanings: int = 0,
    constrain_to_domain: bool = False,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
    chunks: ChunkStore | None = None,
    event_store: AggregateStore | None = None,
    observed_at: datetime | None = None,
    concurrency: int = 1,
) -> GraphBuildReport:
    """Extract `document` and write the result into `store`.

    Args:
        document: The content. Supplied by the caller -- this library never
            fetches anything.
        provider: What to ask. Its `model` becomes the entities' provenance.
        store: Where the result lands. Any `GraphStore`.
        tenant_id: Applied to every entity, relationship and store call.
        domain: `None` for the general-purpose prompt; a domain id or a
            `DomainSchema` to specialise it; `AUTO` to have
            `ContentClassifier` choose, at the cost of one extra model call.
            `AUTO` never fails: a document under 100 characters is not sent
            to the classifier at all, and a low-confidence or failed
            classification falls back to `encyclopedia_wiki`. Read
            `report.domain_confidence` to tell those apart from a real
            choice -- `0.0` is a give-up.
        chunker: How to split the document. A `SlidingWindowChunker` with its
            own defaults when None.
        observed_at: When this library was told, stamped onto every entity's
            `Provenance`. `None` reads the clock here, which is the only place
            in the library permitted to: everything below `composition` takes
            the instant as a required argument, so a re-extraction of one
            document produces identical entities whenever it runs. Pass a
            value when you need that determinism -- a test, a backfill
            re-stating when the observation actually happened, or a batch that
            should share one instant across documents.
        embedding_provider: Embeds each entity's name so the extraction lands
            in a `VectorStore` as well as the graph. Must be given together
            with `vector_store`.
        vector_store: Where the vectors land. Must be given together with
            `embedding_provider`.
        chunks: A corpus to keep in step, so each passage of the document is
            stored alongside the ids of the entities it produced. Omitting it
            means "do not maintain a corpus" -- unlike the embedding pair,
            there is no second argument it has to arrive with and no
            half-configured state to refuse, so `None` is not an error and
            neither is a document that chunks to something while this is
            `None`.
        event_store: Where this run's events are appended, so the aggregate's
            idempotence survives the call. Without one -- the default, and the
            shape this function has always had -- a fresh aggregate is built
            each time and every refusal it could make is unreachable. With
            one, a second `build_graph` for the same document under the same
            model returns `event is None` and a repeat chunking returns
            `chunks_written == 0`.

            **Chunking is recorded whenever this is given, whether or not
            `chunks` is** -- `record_chunking` runs unconditionally on the
            aggregate, and only the write into `chunks` is gated on it being
            given. A caller passing `event_store` for extraction idempotence
            alone, with no `chunks`, still gets a `DocumentChunked` carrying
            the document's full text into the log. This is correct and not
            new: the log is the authority a corpus rebuild replays from, so
            the event has to exist independently of whether a projection
            writes it anywhere today.

            **This is what makes the two write paths' key spaces observable.**
            Indexing a document with `index_documents` and then extracting it
            over the same log is the case the signatures are composed
            differently for: they differ, so the extraction's chunking is
            recorded and its entity links survive. Were the signatures equal,
            the extraction would read as a repeat and the links would be
            silently dropped -- which is exactly what
            `tests/unit/composition/test_index_documents.py::TestTheTwoOrderings`
            now fails on, rather than the string-shape assertion that used to
            be the only thing holding it.
        skip_failed_chunks: Continue past a chunk whose model call failed.
        allow_partial: Record a result that has failed chunks in it. Off by
            default, because recording a partial extraction marks this model
            version done and makes the retry that would repair it a silent
            no-op.
        carryover_entities: How many entities found by earlier chunks are
            named in the next chunk's prompt, so a recurring entity keeps one
            spelling and therefore one id. `0` turns it off. Exposed here and
            not left to `ExtractionPipeline` alone because this function
            builds the pipeline, so a caller of `build_graph` would otherwise
            have no way to reach it -- and because turning it off is what
            makes a before/after quality comparison possible at all. See
            `redstring.extraction.carryover`.
        gleanings: How many times each chunk is shown its own answer and asked
            what it missed. `0` -- the default -- is one model call per chunk.
            Each pass is one more call per chunk, so this is the one argument
            here that changes what the run costs rather than only what it
            does. See `redstring.extraction.gleaning`.
        constrain_to_domain: Admit only the `domain`'s declared entity and
            relationship type ids, enforced by the server's structured
            decoding rather than asked for in prose. Off by default, and
            requires a `domain`: it trades coverage for consistency, since a
            type the schema's author did not think of becomes the nearest
            wrong answer instead of a new type.
            `docs/adr/0011-domain-schemas-prompt-but-do-not-constrain.md`
            remains the default's reasoning; ADR 0030 is this dial's.
        concurrency: How many calls against `provider` may be in flight at
            once -- classification, extraction, gleaning and embedding alike.
            One `CallLimiter` is built here, before any of those calls runs,
            and shared by all four, so a stated ceiling of four stays four
            instead of becoming five when `domain=AUTO` adds a classification
            call ahead of the batch, or six when gleaning or embedding
            overlaps the next one. `1` -- the default -- reproduces the
            serial pipeline byte for byte, the same as passing no value at
            all. See `redstring.domain.limiter` and
            `redstring.extraction.pipeline.ExtractionPipeline`'s `concurrency`
            parameter, which this both configures and bounds jointly with.

            **Raising this past the document's chunk count does nothing.**
            What runs at once is `min(concurrency, chunks in the batch)`, so a
            document that splits into four chunks issues four calls whether
            this is `4` or `40`. The two knobs therefore cannot be tuned
            independently: getting more concurrency out of a fixed document
            means smaller chunks, not a larger ceiling.

            Get that chunk count from `chunker.chunk(text).total_chunks`
            rather than estimating it as `len(text) / chunk_size` -- the
            chunker advances by `chunk_size - overlap` and backs each break up
            to a paragraph, sentence or word boundary, so the estimate ran
            28-47% low at every size the benchmark measured, which caps
            `concurrency` below what the document could have used.

            There is a floor on shrinking chunks -- smaller ones manufacture
            more naming variants, since identity is derived from the name and
            every boundary is a chance to drift -- and
            `docs/how-to/tune-ingestion-throughput.md` carries the
            measurements and how to take your own.

    Returns:
        A `GraphBuildReport`. `report.event is None` means this document was
        already extracted under this model on *this aggregate* -- which
        without an `event_store` cannot happen, since each call builds a fresh
        one, and with an `event_store` is the ordinary outcome of a re-run.

    Raises:
        ValueError: `constrain_to_domain` was set with no `domain` to draw a
            vocabulary from. Or one of `embedding_provider` and `vector_store`
            was given without the other, or their dimensions disagree. Both are checked
            **before** anything is extracted: "I configured embeddings and got
            no vectors" is the failure this argument pair exists to prevent,
            and discovering it after a document has been through a model is
            discovering it too late.
        UnknownDomainError: `domain` named an id no schema has.
        PartialExtractionError: Chunks failed and `allow_partial` is False.
            Nothing is written -- the refusal happens before the projection
            runs, so it cannot itself cause the gap it prevents.
        LlmProviderError: A model call failed and `skip_failed_chunks` is off.
    """
    _check_embedding_wiring(embedding_provider, vector_store)

    _check_vocabulary_wiring(domain, constrain_to_domain)

    # One limiter, shared by every call against `provider` this function
    # makes or hands to a collaborator -- classification, extraction,
    # gleaning and embedding alike. Constructed before the first of those
    # calls (`_resolve_prompt`'s classifier call on the `domain=AUTO` path)
    # runs, so nothing can slip out ahead of it the way the classifier call
    # used to. A second limiter built later for the embedding call alone
    # would bound extraction and embedding separately at `concurrency` each
    # rather than jointly, which is exactly the gap this parameter exists to
    # close.
    limiter = CallLimiter(concurrency)

    resolved = await _resolve_prompt(domain, document, provider, limiter)
    domain_id, confidence = resolved.domain_id, resolved.confidence

    pipeline = ExtractionPipeline(
        provider,
        chunker=chunker,
        system_prompt=resolved.system_prompt,
        schema=(constrained_extraction_for(resolved.schema) if constrain_to_domain else Extraction),
        skip_failed_chunks=skip_failed_chunks,
        concurrency=concurrency,
        carryover_entities=carryover_entities,
        gleanings=gleanings,
        limiter=limiter,
    )
    stream = document_stream(tenant_id=tenant_id, source_id=document.id).aggregate_id
    repository = document_repository(event_store) if event_store is not None else None
    if repository is None:
        aggregate = Document(stream)
    else:
        async with tenant_scope(tenant_id):
            aggregate = await repository.load_or_create(stream)

    # Extracted once and handed to `record`, which would otherwise extract
    # again -- see the `result` parameter there. `record` asks the aggregate
    # for an event and writes nothing itself, which is the property the whole
    # re-architecture rests on.
    result = await pipeline.extract(
        document, tenant_id, observed_at=observed_at or datetime.now(UTC)
    )
    event = await pipeline.record(
        aggregate, document, tenant_id, allow_partial=allow_partial, result=result
    )

    # Recorded whether or not the extraction was: the two are separate key
    # spaces on the aggregate, and a document already extracted under this
    # model has still been split into the passages a corpus wants.
    chunk_event = aggregate.record_chunking(
        tenant_id=tenant_id,
        source_id=document.id,
        chunking_signature=result.chunking_signature,
        chunks=result.chunks,
    )
    # The log before the read models, when there is a log: a crash between the
    # two leaves an event a replay will apply, whereas projecting first and
    # crashing leaves read models holding what nothing accounts for.
    await _persist(repository, aggregate, tenant_id)

    chunks_written = 0
    if chunk_event is not None and chunks is not None:
        await ChunkProjection(chunks).handle(chunk_event)
        chunks_written = len(chunk_event.chunks)

    embedded = 0
    if event is not None:
        await GraphProjection(store).handle(event)
        if embedding_provider is not None and vector_store is not None:
            embedded = await _embed_entities(
                aggregate,
                document,
                tenant_id,
                entities=result.entities,
                provider=embedding_provider,
                vector_store=vector_store,
                limiter=limiter,
            )
            await _persist(repository, aggregate, tenant_id)

    return GraphBuildReport(
        event=event,
        domain=domain_id,
        domain_confidence=confidence,
        entities=len(result.entities),
        relationships=len(result.relationships),
        failed_chunks=result.failed_chunks,
        total_chunks=result.total_chunks,
        unresolved_relationships=result.unresolved_relationships,
        embedded=embedded,
        chunks_written=chunks_written,
    )


async def _persist(
    repository: TenantAwareRepository[Document] | None,
    aggregate: Document,
    tenant_id: TenantId,
) -> None:
    """Append whatever the aggregate has emitted, when there is a log to append to.

    A no-op without an `event_store`, which is the default and the shape
    `build_graph` has always had. `TenantAwareRepository.save` raises outside a
    `tenant_scope`, so the scope is entered here rather than being a
    precondition on the caller.
    """
    if repository is None:
        return
    async with tenant_scope(tenant_id):
        await repository.save(aggregate)


def _check_vocabulary_wiring(
    domain: str | DomainSchema | AutoDomain | None, constrain_to_domain: bool
) -> None:
    """Refuse `constrain_to_domain` with nothing to constrain to.

    Checked **before** the document reaches a model, for the reason
    `_check_embedding_wiring` is: "I asked for constrained extraction and got
    free-form types" is the failure this argument pair exists to prevent, and
    discovering it after paying for a document is discovering it too late.

    Silently falling back to the unconstrained schema would be worse than
    either -- the two runs are then indistinguishable except in the numbers
    the caller was trying to move.
    """
    if constrain_to_domain and domain is None:
        raise ValueError(
            "constrain_to_domain=True needs a domain to take a vocabulary from; "
            "pass domain=<id>, a DomainSchema, or AUTO"
        )


def _check_embedding_wiring(provider: EmbeddingProvider | None, store: VectorStore | None) -> None:
    """Refuse a half-configured or mismatched embedding pair, before any work.

    Two failures, both of which are otherwise discovered late and read as
    something else.

    **Half-configured** is a silent no-op: a caller who passes an embedding
    provider and forgets the store gets a perfectly successful run with an
    empty vector store, and every symptom of that appears later in whatever
    was going to search it.

    **Mismatched dimensions** would otherwise surface from pgvector, after the
    embedding API call has been paid for, as an error about a column type.
    `VectorStore` does raise `DimensionMismatchError` per write -- that check
    stays and is the backstop -- but it fires once per vector at the end of a
    pipeline rather than once at the seam, which is where the configuration
    mistake actually is. This raises the same `DimensionMismatchError`,
    because it is the same condition `Retriever.__init__` refuses; the two
    entry points diverged on exception type until B82 closed. Two models'
    vectors are not comparable even at equal dimension, so point this run at
    a store built for this model rather than widening either.

    The half-configured case below stays a `ValueError`: arity and
    disagreement are different mistakes, and there is no `DimensionMismatchError`
    to have when one collaborator is entirely absent.

    The comparison is `!=` and not `is not`. CLAUDE.md records that exact
    defect: CPython caches small integers, so an identity check passes at a
    test dimension of 8 and rejects every legitimate vector at 768.
    """
    if (provider is None) != (store is None):
        given, missing = (
            ("embedding_provider", "vector_store")
            if provider is not None
            else ("vector_store", "embedding_provider")
        )
        raise ValueError(
            f"{given} was given without {missing}; embedding needs both, and "
            f"one alone writes no vectors while reporting success"
        )

    if provider is not None and store is not None and provider.dimension != store.dimension:
        raise DimensionMismatchError(expected=store.dimension, actual=provider.dimension)


async def _embed_entities(
    aggregate: Document,
    document: SourceDocument,
    tenant_id: TenantId,
    *,
    entities: Sequence[Entity],
    provider: EmbeddingProvider,
    vector_store: VectorStore,
    limiter: CallLimiter,
) -> int:
    """Embed `entities` and fold the result into `vector_store`.

    Goes through the aggregate and the projection rather than calling
    `upsert_many` directly, which is ADR 0004's rule and not ceremony here:
    `EntitiesEmbedded` is what lets a vector store be rebuilt by replay, and a
    direct write would make the vector half the one part of this library that
    cannot be.

    **`Document.record_embeddings` already existed and nothing called it.**
    So did `VectorProjection`. Both were written when the event was designed
    and neither had a caller -- the inert-code shape from
    `recurring-defects.md` §3, sitting in the tree for six slices.

    `limiter` bounds this call jointly with `build_graph`'s pipeline, since
    both hit the same endpoint the operator sized `concurrency` for.

    Returns:
        How many vectors were written.

    The `event is None` branch handles `record_embeddings` refusing a repeat
    for a model already recorded on the aggregate. **It is unreachable from
    `build_graph`**, which constructs a fresh `Document` per call, and is kept
    because this helper takes the aggregate as an argument: a caller that loads
    one from an event store reaches it immediately. Left rather than asserted
    away, and named here so it is a known dead branch rather than a mystery --
    `recurring-defects.md` ยง3 is about the ones nobody wrote down.
    """
    if not entities:
        return 0

    async with limiter:
        vectors = await provider.embed([entity.name for entity in entities])
    if len(vectors) != len(entities):
        raise EmbeddingProviderError(
            f"asked for {len(entities)} embeddings and got {len(vectors)}; "
            f"results are positional, so a short list cannot be matched to "
            f"the entities it came from",
            model=provider.model,
        )

    event = aggregate.record_embeddings(
        tenant_id=tenant_id,
        source_id=document.id,
        embedding_model=provider.model,
        embeddings=[
            VectorRecord(entity_id=entity.id, tenant_id=tenant_id, vector=vector)
            for entity, vector in zip(entities, vectors, strict=True)
        ],
    )
    if event is None:  # pragma: no cover -- unreachable from `build_graph`
        return 0

    await VectorProjection(vector_store).handle(event)
    return len(vectors)


class _ResolvedDomain(NamedTuple):
    """What choosing a domain settled: what to say, and what to permit.

    The schema comes back alongside the prompt because both are derived from
    one choice and one of them is made by a classifier. Resolving the domain
    id and then looking the schema up again at the call site would run
    `AUTO`'s classifier once and its registry lookup twice, and would leave
    two places that have to agree about what a missing domain means.
    """

    domain_id: str | None
    confidence: float | None
    system_prompt: str
    #: `None` for the default prompt, which has no vocabulary to constrain to.
    schema: DomainSchema | None


async def _resolve_prompt(
    domain: str | DomainSchema | AutoDomain | None,
    document: SourceDocument,
    provider: LlmProvider,
    limiter: CallLimiter,
) -> _ResolvedDomain:
    """The domain chosen, how sure the classifier was, and what to ask with.

    `limiter` bounds the classifier's call jointly with `build_graph`'s
    pipeline and embedding call, on the `AUTO` branch -- the only branch that
    calls `provider` at all. `None` and an explicit domain both return
    without touching `limiter` or the network.
    """
    if domain is None:
        return _ResolvedDomain(None, None, DEFAULT_SYSTEM_PROMPT, None)
    if isinstance(domain, AutoDomain):
        classification = await ContentClassifier(provider, limiter=limiter).classify(document.text)
        return _ResolvedDomain(
            classification.domain,
            classification.confidence,
            domain_system_prompt(classification.domain),
            get_domain_schema(classification.domain),
        )
    if isinstance(domain, str):
        return _ResolvedDomain(
            domain, None, domain_system_prompt(domain), get_domain_schema(domain)
        )
    return _ResolvedDomain(domain.domain_id, None, domain_system_prompt(domain), domain)


# ---------------------------------------------------------------------------
# Consolidation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConsolidationReport:
    """What one merge or undo decided, and what it did to the graph."""

    #: The event that was emitted and then folded into the store. Append it to
    #: an event store if you have one; it is the same object the projection
    #: consumed.
    event: EntitiesMerged | MergeUndone
    #: The entity that survived (`merge`) or that gave the others back
    #: (`undo`).
    canonical_entity_id: EntityId
    #: Absorbed by this merge, or handed back by this undo. Ordered as the
    #: event records them.
    affected_entity_ids: tuple[EntityId, ...]
    #: Edges the merge moved or dropped, or that the undo restored. Non-zero
    #: is the usual case for entities with any structure around them, and a
    #: zero here on a well-connected graph is worth a second look.
    relationships_changed: int
    #: Why. Carried through from `merge_reason`, or composed by `resolve` from
    #: the score band and any adjudicator verdicts. `None` for an undo.
    reason: str | None


class Consolidator:
    """Merge duplicate entities, and undo merges, keeping a `GraphStore` in step.

    This is the composed entry point for the second of the three problems this
    library exists to solve. Extraction reads each document alone, so "Ada
    Lovelace", "Lovelace, A." and "Ada King" arrive as three entities; without
    this step a graph accumulates one node per *mention*, which looks like a
    knowledge graph and answers every question wrong, because each entity's
    edges are split across its aliases.

    It stands to `ConsolidationService` exactly as `build_graph` stands to
    `ExtractionPipeline`: the service decides and emits, a projection writes,
    and this holds both so a caller does not have to. Assembling it by hand
    means six objects from three packages -- two of them eventsource
    internals -- which is why consolidation went four slices without a public
    surface.

    ```python
    consolidator = Consolidator(store)
    report = await consolidator.resolve(subject)
    if report is not None:
        print(report.canonical_entity_id, report.affected_entity_ids)
    ```

    ## The log, and what `undo` needs from it

    Every method emits an event and folds it into `store`. `undo` is different
    from the others in a way worth knowing before you rely on it: it takes a
    merge's `event_id` and **nothing describing what to restore**, because the
    aggregate rehydrates its own history and writes the restoration into
    `MergeUndone` itself. A caller supplying what to restore would be a caller
    able to restore something that never happened.

    That history lives in the event store. With no `event_store` argument this
    class creates an in-memory one, which means:

    - **merge, resolve and undo all work**, and the graph is correct;
    - **the history dies with this object.** A new `Consolidator` cannot undo
      a merge an earlier one made, and `undo` will raise `UnknownMergeError`
      -- which is also what it raises for a merge that never happened. The two
      are indistinguishable from the error alone.

    Pass an `event_store` and `snapshot_store` to keep it. That is the same
    trade `build_graph` makes and states, and it is stated here rather than
    discovered, because "undo stopped working after a restart" is an expensive
    thing to debug from the outside.

    ## What it never does

    It does not write to the store directly. Every change to the graph arrives
    through `GraphProjection` applying an event, so a store rebuilt by replay
    and a store maintained by this class end up the same -- which is the whole
    reason consolidation emits rather than writes
    (`docs/adr/0004-consolidation-emits-events.md`).
    """

    def __init__(
        self,
        store: GraphStore,
        *,
        event_store: AggregateStore | None = None,
        snapshot_store: SnapshotStore | None = None,
        vector_store: VectorStore | None = None,
        use_graph_signal: bool = True,
        merge_policy: PropertyMergePolicy | None = None,
    ) -> None:
        """Wire a consolidator over `store`.

        Args:
            store: The graph. Read to plan a merge, and written by the
                projection that applies it.
            event_store: Where merges are recorded. An `InMemoryEventStore`
                when omitted -- see the class docstring on what that costs
                `undo`.
            snapshot_store: Companion to `event_store`. In-memory when
                omitted. Passing one without the other is accepted; they are
                independent.
            vector_store: Gives `resolve`'s default candidate finder an
                embedding signal. Its absence is not an error -- scoring
                renormalises over the features it has, so a deployment
                without embeddings still consolidates on names and structure.
            use_graph_signal: Whether that finder scores shared neighbours.
                This is the expensive feature (one `get_relationships_for`
                per subject and per candidate); turning it off is a stated
                trade rather than a silent degradation.
            merge_policy: How a merge reconciles the canonical entity's
                fields -- `description`, `properties` and `external_ids`. The
                default keeps every canonical value.
        """
        self._store = store
        self._service = ConsolidationService(
            event_store=event_store if event_store is not None else InMemoryEventStore(),
            snapshot_store=(
                snapshot_store if snapshot_store is not None else InMemorySnapshotStore()
            ),
            graph_store=store,
            merge_policy=merge_policy,
        )
        self._default_finder = CandidateFinder(
            store, vector_store=vector_store, use_graph_signal=use_graph_signal
        )
        self._durable = event_store is not None

    @property
    def remembers_merges_across_restarts(self) -> bool:
        """False when the log is the in-memory default, so `undo` is session-only.

        Named for what a caller wants to know rather than for the mechanism.
        `durable` would invite the reading "are my *merges* durable" -- they
        are: the graph is written through the projection either way. What is
        not kept is the ability to reverse them.
        """
        return self._durable

    async def merge(
        self,
        *,
        tenant_id: TenantId,
        canonical_entity_id: EntityId,
        merged_entity_ids: Sequence[EntityId],
        merge_reason: str | None = None,
    ) -> ConsolidationReport:
        """Absorb `merged_entity_ids` into `canonical_entity_id`.

        The explicit path, for when the decision is already made -- by a human,
        by a rule of your own, by an import that knows two ids are one thing.
        No blocking, no scoring, no model call.

        Choosing which duplicate to pass as canonical is choosing which one
        survives.

        Raises:
            MergeIntoAliasError: `canonical_entity_id` is itself already
                merged into something. Refused before anything is written.
            DoubleMergeError: one of `merged_entity_ids` is already merged.
                Also refused before writing, so there is nothing
                half-applied.
        """
        event = await self._service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical_entity_id,
            merged_entity_ids=merged_entity_ids,
            merge_reason=merge_reason,
        )
        return await self._project_merge(event)

    async def resolve(
        self,
        subject: Entity,
        *,
        finder: CandidateSource | None = None,
        adjudicator: MergeAdjudicator | None = None,
        high: float = HIGH_SIMILARITY,
        low: float = LOW_SIMILARITY,
    ) -> ConsolidationReport | None:
        """Find `subject`'s duplicates, decide, merge what survives the decision.

        Block, score, band against `low` and `high`, put the middle band to
        `adjudicator` if there is one, and emit a single merge covering
        everything that came out a yes.

        `None` is the ordinary outcome, not a failure: it means nothing was
        found worth merging.

        **Without an `adjudicator` the middle band is rejected, not merged.**
        The band exists precisely because the score does not settle those
        pairs, so treating "nobody asked" as a yes would merge exactly the
        pairs a model was there to protect. Narrowing the band is therefore
        not symmetric when no adjudicator is configured.

        Args:
            subject: The entity to consolidate around. **If `subject` has
                itself already been merged away, its terminal canonical
                survives instead** -- so `report.canonical_entity_id` can
                differ from `subject.id`. See
                `ConsolidationService.resolve`'s docstring for why that is
                the right reading of a merge (A merged into B means new
                duplicates of A belong with B too) and why it is safe
                (transitive, terminating by construction). A caller sweeping
                a whole tenant no longer needs to pre-resolve its own ids for
                this reason alone, though `find_entities` returning absorbed
                entities is still the way to discover them at all.
            finder: Overrides the default built from this consolidator's
                stores. Supply one to change the weights or the blocking.
            adjudicator: Asked about the middle band, in batches. Omit it and
                that band is rejected.
            high: At or above this score, merge without asking.
            low: Below this score, never merge and never ask.
        """
        event = await self._service.resolve(
            subject,
            finder=finder if finder is not None else self._default_finder,
            adjudicator=adjudicator,
            high=high,
            low=low,
        )
        if event is None:
            return None
        return await self._project_merge(event)

    async def resolve_many(
        self,
        subjects: Sequence[Entity],
        *,
        finder: CandidateSource | None = None,
        adjudicator: MergeAdjudicator | None = None,
        concurrency: int = 1,
        limiter: CallLimiter | None = None,
        high: float = HIGH_SIMILARITY,
        low: float = LOW_SIMILARITY,
    ) -> list[ConsolidationReport]:
        """`resolve` over a whole corpus, in one decide-then-emit pass.

        One report per merge actually emitted, in emit order -- shorter than
        `subjects` whenever a subject decided nothing, which is the common
        case.

        Each report's graph effects are already applied: this folds every
        event through the projection as it goes, exactly as `resolve` does for
        one.

        See `ConsolidationService.resolve_many` for the phase structure and
        for why a subject merged away mid-pass is skipped rather than retried.
        Two knobs are worth knowing before raising them: `concurrency` bounds
        phase 1's wavefronts of subjects scored at once; phase 2 makes a
        single `adjudicate_many` call over the whole batch, held under the
        limiter for that call's entire duration, so `concurrency` does not
        multiply model calls in flight the way it does in `build_graph`.
        `limiter` is the endpoint ceiling, and it is only load-bearing when
        shared across callers -- pass a shared one to bound a backend serving
        more than this pass.
        """
        events = await self._service.resolve_many(
            subjects,
            finder=finder if finder is not None else self._default_finder,
            adjudicator=adjudicator,
            concurrency=concurrency,
            limiter=limiter,
            high=high,
            low=low,
        )
        return [await self._project_merge(event) for event in events]

    async def undo(self, *, tenant_id: TenantId, merge_event_id: UUID) -> ConsolidationReport:
        """Reverse the merge that `merge_event_id` recorded.

        Takes the merge's event id and nothing else; what to restore is read
        from the log, not from the caller. `report.event.event_id` on a merge
        is the id to keep.

        Raises:
            UnknownMergeError: no merge in effect has that id. This covers
                "never happened" and "already undone" as one case -- and, when
                this consolidator holds the in-memory default log, also "made
                by a different `Consolidator`". See
                `remembers_merges_across_restarts`.
        """
        event = await self._service.undo(tenant_id=tenant_id, merge_event_id=merge_event_id)
        await GraphProjection(self._store).handle(event)
        return ConsolidationReport(
            event=event,
            canonical_entity_id=event.canonical_entity_id,
            affected_entity_ids=tuple(event.unmerged_entity_ids),
            relationships_changed=len(event.restored_relationships),
            reason=None,
        )

    async def _project_merge(self, event: EntitiesMerged) -> ConsolidationReport:
        await GraphProjection(self._store).handle(event)
        return ConsolidationReport(
            event=event,
            canonical_entity_id=event.canonical_entity_id,
            affected_entity_ids=tuple(event.merged_entity_ids),
            relationships_changed=len(event.redirections),
            reason=event.merge_reason,
        )
