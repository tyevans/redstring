"""What happened to one document: the `Document` aggregate's events.

## One event per document, not one per entity

`DocumentExtracted` carries **every** entity and relationship the extraction
found. The alternative -- an `EntityExtracted` per entity -- was rejected on
what the projection has to do with them, not on volume alone:

- `GraphStore.upsert_relationship` raises `MissingEntityError` when an
  endpoint is absent. A coarse event lets one handler write the entities and
  then the edges that connect them, so an extraction is never *partly*
  applied. Split into per-entity events, the ordering between an edge and its
  endpoints becomes load-bearing across events, and any reordering or partial
  delivery produces a poison event out of data that was perfectly good.
- The fold is then genuinely order-independent *between* events, which is what
  the projection contract asks for. Within the event, entities before edges is
  one handler's business rather than the bus's.
- Volume is the secondary argument and still holds: a document yielding ten
  thousand entities yields one event, and the stream stays short enough that
  the `Document` aggregate rehydrates without snapshots.

The cost, stated plainly: a consumer wanting per-entity granularity has to
iterate the payload, and a re-extraction that finds *fewer* entities than the
last run cannot express the removal -- the projection upserts, so the earlier
entities survive. See BACKLOG B32.

## Extraction is idempotent per model version

`model_version` is what makes a repeat extraction distinguishable from a new
one. The `Document` aggregate refuses a second `DocumentExtracted` for a
model version it has already recorded, so a retry after a crash is a no-op
rather than a second write of the same entities.
"""

from __future__ import annotations

from eventsource import register_event
from eventsource.domain.tenant_events import TenantDomainEvent
from pydantic import Field, model_validator

from kg_builder.domain.entity import Entity
from kg_builder.domain.ids import SourceId
from kg_builder.domain.relationship import Relationship
from kg_builder.domain.vector import VectorRecord
from kg_builder.events.streams import DOCUMENT_CATEGORY


def _reject_foreign_tenants(event: TenantDomainEvent, payloads: object, field: str) -> None:
    """Raise unless every payload in `payloads` carries the event's tenant.

    The projection writes each payload under **its own** `tenant_id`, not the
    event's -- `GraphStore.upsert_entities` says so explicitly. So an event
    that passed validation while carrying a foreign-tenant entity would not
    fail anywhere: it would quietly write into a tenant that never emitted it.
    This is the one place the two can still be compared.
    """
    assert isinstance(payloads, list)
    foreign = {p.tenant_id for p in payloads if p.tenant_id != event.tenant_id}
    if foreign:
        raise ValueError(
            f"{field} carries tenants the event does not belong to: "
            f"{sorted(str(t) for t in foreign)} != {event.tenant_id}"
        )


@register_event
class DocumentExtracted(TenantDomainEvent):
    """Everything one extraction run found in one document."""

    event_version: int = 1
    aggregate_type: str = DOCUMENT_CATEGORY

    source_id: SourceId
    model_version: str
    entities: list[Entity] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)

    @model_validator(mode="after")
    def _payloads_belong_to_this_document_and_tenant(self) -> DocumentExtracted:
        _reject_foreign_tenants(self, self.entities, "entities")
        _reject_foreign_tenants(self, self.relationships, "relationships")
        strays = {e.source_id for e in self.entities if e.source_id != self.source_id}
        if strays:
            raise ValueError(
                f"entities must be attributed to the document they were extracted "
                f"from; found source_id {sorted(map(str, strays))} in an event for "
                f"{self.source_id!r}"
            )
        return self


@register_event
class EntitiesEmbedded(TenantDomainEvent):
    """Embeddings computed for entities of one document.

    Separate from `DocumentExtracted` because embedding is a separate step
    against a separate model, and re-embedding under a new model must not
    re-emit the entities. `embedding_model` is on the event rather than
    implied, because a `VectorStore` holds vectors from exactly one model and
    two models' vectors are not comparable even at equal dimension.
    """

    event_version: int = 1
    aggregate_type: str = DOCUMENT_CATEGORY

    source_id: SourceId
    embedding_model: str
    embeddings: list[VectorRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _embeddings_belong_to_this_tenant(self) -> EntitiesEmbedded:
        _reject_foreign_tenants(self, self.embeddings, "embeddings")
        return self
