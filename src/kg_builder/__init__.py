"""kg-builder: knowledge graph construction.

Entity extraction, entity consolidation, embeddings, and graph storage.

Two things this deliberately does **not** do, both removed rather than
unfinished. It never fetches content -- a caller supplies a `SourceDocument`,
so there is no scraping and no HTML preprocessing (slice 1). And extraction
writes to no store: it emits events on the `Document` aggregate, and
`kg_builder.projections` is what puts entities into a `GraphStore` or a
`VectorStore` (slice 6).

Extracted from the knowledge-mapper application. See README.md for status.
"""

__version__ = "0.1.0"
