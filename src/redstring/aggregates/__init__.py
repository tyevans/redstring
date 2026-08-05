"""The write model: the two aggregates and how they are loaded and saved.

`Document` owns "extraction is idempotent per model version";
`ConsolidationLog` owns the three merge invariants. `Entity` is deliberately
not an aggregate -- ten thousand entities from one document are one
transactional unit, not ten thousand. See
`docs/adr/0001-event-log-schema-and-granularity.md`.
"""

from redstring.aggregates.consolidation_log import ConsolidationLog
from redstring.aggregates.document import Document
from redstring.aggregates.repositories import (
    consolidation_repository,
    document_repository,
)

__all__ = [
    "ConsolidationLog",
    "Document",
    "consolidation_repository",
    "document_repository",
]
