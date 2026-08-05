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

from redstring.aggregates.document import Document
from redstring.events.streams import document_stream
from redstring.extraction.classifier import ContentClassifier
from redstring.extraction.pipeline import DEFAULT_SYSTEM_PROMPT, ExtractionPipeline
from redstring.extraction.prompt_generator import domain_system_prompt
from redstring.projections.graph import GraphProjection

if TYPE_CHECKING:
    from redstring.domain.ids import TenantId
    from redstring.domain.source import SourceDocument
    from redstring.events.document import DocumentExtracted
    from redstring.extraction.domains.models import DomainSchema
    from redstring.extraction.protocols import Chunker
    from redstring.ports.graph_store import GraphStore
    from redstring.ports.llm_provider import LlmProvider


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
