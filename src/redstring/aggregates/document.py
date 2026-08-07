"""One document's extraction history, and the one rule it enforces.

Short stream, one per document, parallel across documents -- so extraction
keeps the concurrency it has today, with ordering only where a document's own
runs need it. No snapshot policy: a document accumulates one event per model
version, which is a handful over the aggregate's whole life.

## Extraction is idempotent per model version

`record_extraction` returns `None` and emits nothing when this document has
already been extracted under that `model_version`. That makes a retry after a
crash a no-op rather than a second write of the same ten thousand entities,
and it is the reason `model_version` is on the event: without it, two runs are
indistinguishable and the aggregate has nothing to be idempotent *on*.

The key is the model version and **not** the payload. A re-run of the same
model can legitimately produce slightly different output -- decoding is not
deterministic -- so comparing payloads would classify the retry as a new
extraction and write it, which is exactly the double write being prevented.
The cost is that a genuine re-run under an unchanged model cannot be
recorded; bump the version, which is what a re-run worth recording implies.

Extraction and embedding keep separate key spaces. Sharing one would let an
extraction under a model suppress an embedding run under a model of the same
name, and the two namespaces do overlap in practice.

## Chunking is idempotent per chunking signature, in a third key space

`record_chunking` is keyed on `chunking_signature`, a string the *emitter*
composes rather than one the aggregate derives, because the two write paths
compose it differently on purpose:

- `index_documents` emits `f"{method}:{params_digest}"`.
- The extraction pipeline emits `f"{method}:{params_digest}:{model_version}"`.

Indexing a document and later extracting it therefore produces two different
signatures, so both are recorded and the extraction -- whose chunks carry
`entity_ids` -- lands last and wins. A retry of either is a no-op, and
re-chunking under new settings changes `params_digest` and is recorded.

The signature is a third key space, not a share of either existing one:
`"v1"` is a plausible chunking signature *and* a plausible model version, and
one list would let either suppress the other.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from eventsource.domain.aggregate import AggregateRoot
from pydantic import BaseModel, Field

from redstring.events.document import DocumentChunked, DocumentExtracted, EntitiesEmbedded
from redstring.events.streams import DOCUMENT_CATEGORY

if TYPE_CHECKING:
    from collections.abc import Sequence

    from eventsource.domain.event import DomainEvent

    from redstring.domain.chunk import StoredChunk
    from redstring.domain.entity import Entity
    from redstring.domain.ids import SourceId, TenantId
    from redstring.domain.relationship import Relationship
    from redstring.domain.vector import VectorRecord


class DocumentState(BaseModel):
    """Which runs this document has already recorded.

    `list` rather than `set` because state is snapshotted through
    `model_dump(mode="json")`, which has no representation for a set.
    Membership is what the field is for; order is incidental.
    """

    extraction_model_versions: list[str] = Field(default_factory=list)
    embedding_models: list[str] = Field(default_factory=list)
    chunking_signatures: list[str] = Field(default_factory=list)


class Document(AggregateRoot[DocumentState]):
    """The write model for one document's extraction and embedding runs."""

    aggregate_type = DOCUMENT_CATEGORY

    def _get_initial_state(self) -> DocumentState:
        return DocumentState()

    @property
    def _current(self) -> DocumentState:
        if self._state is None:
            self._state = self._get_initial_state()
        return self._state

    def record_extraction(
        self,
        *,
        tenant_id: TenantId,
        source_id: SourceId,
        model_version: str,
        entities: Sequence[Entity] = (),
        relationships: Sequence[Relationship] = (),
    ) -> DocumentExtracted | None:
        """Record what one extraction run found, or `None` if it is a repeat.

        `None` rather than an exception: a repeat is the *expected* outcome of
        a retry, and making the caller catch an error to handle the normal
        path would push every caller into a try/except that swallows real
        failures alongside it.
        """
        if model_version in self._current.extraction_model_versions:
            return None
        return self.create_event(
            DocumentExtracted,
            tenant_id=tenant_id,
            source_id=source_id,
            model_version=model_version,
            entities=list(entities),
            relationships=list(relationships),
        )

    def record_embeddings(
        self,
        *,
        tenant_id: TenantId,
        source_id: SourceId,
        embedding_model: str,
        embeddings: Sequence[VectorRecord] = (),
    ) -> EntitiesEmbedded | None:
        """Record embeddings for this document, or `None` if it is a repeat."""
        if embedding_model in self._current.embedding_models:
            return None
        return self.create_event(
            EntitiesEmbedded,
            tenant_id=tenant_id,
            source_id=source_id,
            embedding_model=embedding_model,
            embeddings=list(embeddings),
        )

    def record_chunking(
        self,
        *,
        tenant_id: TenantId,
        source_id: SourceId,
        chunking_signature: str,
        chunks: Sequence[StoredChunk] = (),
    ) -> DocumentChunked | None:
        """Record how this document was split, or `None` if it is a repeat.

        An empty `chunks` is a legitimate chunking: it says this document now
        has no passages, and the projection needs it to empty a source.
        """
        if chunking_signature in self._current.chunking_signatures:
            return None
        return self.create_event(
            DocumentChunked,
            tenant_id=tenant_id,
            source_id=source_id,
            chunking_signature=chunking_signature,
            chunks=list(chunks),
        )

    def _apply(self, event: DomainEvent) -> None:
        if isinstance(event, DocumentExtracted):
            self._current.extraction_model_versions.append(event.model_version)
        elif isinstance(event, EntitiesEmbedded):
            self._current.embedding_models.append(event.embedding_model)
        elif isinstance(event, DocumentChunked):
            self._current.chunking_signatures.append(event.chunking_signature)
