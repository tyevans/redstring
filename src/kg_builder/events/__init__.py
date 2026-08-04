"""The event log's schema: what kg-builder writes, and where it writes it.

`KG_EVENT_TYPES` is the whole schema, and it is a tuple rather than prose so
that the properties every event must have -- an explicitly declared
`event_version`, a required tenant, one of the two stream categories, no
hand-declared `event_type` -- can be asserted by introspection over it, and a
new event class inherits those checks by joining it. `tests/unit/events/
test_schema.py` and `tests/unit/projections/test_replay_coverage.py` both key
off this tuple.

**Adding an event means adding it here.** Both suites derive their cases from
this tuple, so an event class that exists but is not listed is an event
nothing checks.

The modules `consolidation` and `scraping` are **not** part of this schema.
They are what is left of the ORM-shaped classes this package used to hold;
none has ever been emitted, and they are reachable only by their own module
path, kept alive by the legacy services that die in slices 7 and 9. See
BACKLOG B33.
"""

from __future__ import annotations

from eventsource.domain.tenant_events import TenantDomainEvent

from kg_builder.events.document import DocumentExtracted, EntitiesEmbedded
from kg_builder.events.merge import EntitiesMerged, MergeUndone
from kg_builder.events.streams import (
    CONSOLIDATION_CATEGORY,
    DOCUMENT_CATEGORY,
    consolidation_stream,
    document_stream,
)

#: Every event type kg-builder writes to its log. See the module docstring.
KG_EVENT_TYPES: tuple[type[TenantDomainEvent], ...] = (
    DocumentExtracted,
    EntitiesEmbedded,
    EntitiesMerged,
    MergeUndone,
)

__all__ = [
    "CONSOLIDATION_CATEGORY",
    "DOCUMENT_CATEGORY",
    "KG_EVENT_TYPES",
    "DocumentExtracted",
    "EntitiesEmbedded",
    "EntitiesMerged",
    "MergeUndone",
    "consolidation_stream",
    "document_stream",
]
