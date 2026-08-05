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

A caller who wants either appends `report.event` to an `EventStore` and drives
`redstring.projections.project` over the feed. `report.event` is returned for
precisely that, and it is the same object the projection just consumed.

## `domain=AUTO` costs an extra model call, and says so

Classifying a document is a model call before the extraction calls. With
`domain=None` there is no classifier and no call; with an explicit domain
there is no call either. Only `AUTO` pays, and it pays once per document
rather than once per chunk -- the classifier sees the head of the text, not
every window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from eventsource.adapters.memory import InMemoryEventStore, InMemorySnapshotStore

from redstring.aggregates.document import Document
from redstring.consolidation.candidates import CandidateFinder
from redstring.consolidation.policy import HIGH_SIMILARITY, LOW_SIMILARITY
from redstring.consolidation.service import ConsolidationService
from redstring.events.streams import document_stream
from redstring.extraction.classifier import ContentClassifier
from redstring.extraction.pipeline import DEFAULT_SYSTEM_PROMPT, ExtractionPipeline
from redstring.extraction.prompt_generator import domain_system_prompt
from redstring.projections.graph import GraphProjection

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from eventsource.ports.snapshots import SnapshotStore
    from eventsource.ports.store import AggregateStore

    from redstring.consolidation.policy import Adjudicator
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, TenantId
    from redstring.domain.source import SourceDocument
    from redstring.events.document import DocumentExtracted
    from redstring.events.merge import EntitiesMerged, MergeUndone
    from redstring.extraction.domains.models import DomainSchema
    from redstring.extraction.protocols import Chunker
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
        skip_failed_chunks: Continue past a chunk whose model call failed.
        allow_partial: Record a result that has failed chunks in it. Off by
            default, because recording a partial extraction marks this model
            version done and makes the retry that would repair it a silent
            no-op.

    Returns:
        A `GraphBuildReport`. `report.event is None` means this document was
        already extracted under this model on *this aggregate*, which given
        the fresh-aggregate-per-call shape above only happens if you pass the
        same one twice.

    Raises:
        UnknownDomainError: `domain` named an id no schema has.
        PartialExtractionError: Chunks failed and `allow_partial` is False.
            Nothing is written -- the refusal happens before the projection
            runs, so it cannot itself cause the gap it prevents.
        LlmProviderError: A model call failed and `skip_failed_chunks` is off.
    """
    domain_id, confidence, system_prompt = await _resolve_prompt(domain, document, provider)

    pipeline = ExtractionPipeline(
        provider,
        chunker=chunker,
        system_prompt=system_prompt,
        skip_failed_chunks=skip_failed_chunks,
    )
    aggregate = Document(document_stream(tenant_id=tenant_id, source_id=document.id).aggregate_id)

    # Extracted once and handed to `record`, which would otherwise extract
    # again -- see the `result` parameter there. `record` asks the aggregate
    # for an event and writes nothing itself, which is the property the whole
    # re-architecture rests on.
    result = await pipeline.extract(document, tenant_id)
    event = await pipeline.record(
        aggregate, document, tenant_id, allow_partial=allow_partial, result=result
    )

    if event is not None:
        await GraphProjection(store).handle(event)

    return GraphBuildReport(
        event=event,
        domain=domain_id,
        domain_confidence=confidence,
        entities=len(result.entities),
        relationships=len(result.relationships),
        failed_chunks=result.failed_chunks,
        total_chunks=result.total_chunks,
        unresolved_relationships=result.unresolved_relationships,
    )


async def _resolve_prompt(
    domain: str | DomainSchema | AutoDomain | None,
    document: SourceDocument,
    provider: LlmProvider,
) -> tuple[str | None, float | None, str]:
    """The domain chosen, how sure the classifier was, and the prompt to ask with."""
    if domain is None:
        return None, None, DEFAULT_SYSTEM_PROMPT
    if isinstance(domain, AutoDomain):
        classification = await ContentClassifier(provider).classify(document.text)
        return (
            classification.domain,
            classification.confidence,
            domain_system_prompt(classification.domain),
        )
    if isinstance(domain, str):
        return domain, None, domain_system_prompt(domain)
    return domain.domain_id, None, domain_system_prompt(domain)


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
        """
        self._store = store
        self._service = ConsolidationService(
            event_store=event_store if event_store is not None else InMemoryEventStore(),
            snapshot_store=(
                snapshot_store if snapshot_store is not None else InMemorySnapshotStore()
            ),
            graph_store=store,
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
        finder: CandidateFinder | None = None,
        adjudicator: Adjudicator | None = None,
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
            subject: The entity that survives. Its duplicates are absorbed
                into it.
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
