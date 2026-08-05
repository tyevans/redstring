"""Which stream an event belongs to, and how its id is derived.

Two categories, matching the two aggregates. The stream is the unit of
ordering and concurrency, so the choice is not cosmetic:

**`Document`**, id `uuid5(tenant_id, source_id)`. Extraction is per-document
and parallel across documents, so one short stream each gives real
concurrency with ordering where it matters.

**`Consolidation`**, id the `tenant_id` itself. Merges span documents, and two
concurrent merges touching the same entities must not interleave -- so they
are deliberately serialised per tenant.

`StreamId.aggregate_id` is a `UUID`, but `SourceId` is a caller-supplied
`str`. `uuid5` is the bridge: deterministic, so re-extracting a document
appends to the stream it already has rather than starting a new one, and
namespaced by tenant, so the same URL ingested by two tenants is two streams.
Deriving the id rather than storing a mapping means there is no table to keep
consistent and no lookup on the write path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid5

from eventsource.domain import StreamId

if TYPE_CHECKING:
    from redstring.domain.ids import SourceId, TenantId

#: Stream category (and `aggregate_type`) for the `Document` aggregate.
DOCUMENT_CATEGORY = "Document"

#: Stream category (and `aggregate_type`) for the `ConsolidationLog` aggregate.
CONSOLIDATION_CATEGORY = "Consolidation"


def document_stream(*, tenant_id: TenantId, source_id: SourceId) -> StreamId:
    """The stream carrying one document's extraction history.

    The tenant is the `uuid5` namespace rather than part of the hashed name.
    That keeps the two halves of the key structurally separate: a scheme that
    concatenated them before hashing would map `("t", "ab")` and `("ta", "b")`
    onto one stream, and `SourceId` is free-form text, so nothing else would
    stop it.

    Raises `ValueError` for a blank `source_id`. `SourceDocument.id` carries no
    validation of its own, so this is the last point at which a blank one can
    be caught; hashed instead, it would yield a valid-looking stream shared by
    every document that had one.
    """
    if not source_id.strip():
        raise ValueError("source_id must not be blank; it identifies the document's stream")
    return StreamId(aggregate_id=uuid5(tenant_id, source_id), category=DOCUMENT_CATEGORY)


def consolidation_stream(*, tenant_id: TenantId) -> StreamId:
    """The stream carrying one tenant's merge history.

    The id **is** the tenant id, not a derivation of it: there is exactly one
    consolidation log per tenant, so any further mapping would be a fiction
    with no second value to distinguish.
    """
    return StreamId(aggregate_id=tenant_id, category=CONSOLIDATION_CATEGORY)
