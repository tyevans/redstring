"""The event log's schema: what redstring writes, and where it writes it.

`KG_EVENT_TYPES` is the whole schema, and it is a tuple rather than prose so
that the properties every event must have -- an explicitly declared
`event_version`, a required tenant, one of the two stream categories, no
hand-declared `event_type` -- can be asserted by introspection over it, and a
new event class inherits those checks by joining it. `tests/unit/events/
test_schema.py` and `tests/unit/projections/test_replay_coverage.py` both key
off this tuple.

**Adding an event means adding it here** -- and forgetting is a red test
rather than a rule you had to remember. Both suites derive their cases from
this tuple, so an event class that exists but is not listed would otherwise be
an event nothing checks. `test_the_tuple_lists_exactly_the_registered_events`
closes that by walking every module in this package and comparing the library
registry against this tuple, in both directions; the filesystem is the source
of truth, because it is the one thing that cannot be forgotten.

Every module in this package is now part of that schema. The ORM-shaped
classes this package used to hold -- 40 of them across `consolidation` and
`scraping`, none ever emitted -- are gone: `consolidation` in slice 7 with
`services/consolidation/`, `scraping` in slice 9 with `services/
neo4j_errors.py`, its last consumer. `events/base.py` existed only to serve
`scraping` and went with it. So the walk below no longer needs an exclusion
list, which is why `tests/unit/events/test_schema.py` no longer has one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from redstring.events.document import DocumentChunked, DocumentExtracted, EntitiesEmbedded
from redstring.events.merge import EntitiesMerged, MergeUndone
from redstring.events.streams import (
    CONSOLIDATION_CATEGORY,
    DOCUMENT_CATEGORY,
    consolidation_stream,
    document_stream,
)

if TYPE_CHECKING:
    from eventsource.domain.tenant_events import TenantDomainEvent

#: Every event type redstring writes to its log. See the module docstring.
KG_EVENT_TYPES: tuple[type[TenantDomainEvent], ...] = (
    DocumentChunked,
    DocumentExtracted,
    EntitiesEmbedded,
    EntitiesMerged,
    MergeUndone,
)

__all__ = [
    "CONSOLIDATION_CATEGORY",
    "DOCUMENT_CATEGORY",
    "KG_EVENT_TYPES",
    "DocumentChunked",
    "DocumentExtracted",
    "EntitiesEmbedded",
    "EntitiesMerged",
    "MergeUndone",
    "consolidation_stream",
    "document_stream",
]
