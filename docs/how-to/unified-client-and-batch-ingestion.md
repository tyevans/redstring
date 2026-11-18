# Use the unified client and batch ingestion

The `Redstring` client facade provides a high-level, unified interface for
building, querying, and consolidating knowledge graphs. It coordinates
graph stores, vector stores, chunk stores, and model providers under a single
convenient API, while supporting flexible input coercion and concurrent
batch processing.

## Initializing the client

For local development, testing, or scratchpad workflows, initialize an
in-memory client with default or custom stores:

```python
from redstring import Redstring

# In-memory graph, vector, and chunk stores configured automatically
client = Redstring.in_memory(
    provider=llm_provider,
    embeddings=embedding_provider,
)
```

For production deployments backed by persistent databases (such as Neo4j,
pgvector, or PostgreSQL chunk stores), use `Redstring.connect()`:

```python
from redstring import Redstring
from redstring.graph.adapters.neo4j import Neo4jGraphStore
from redstring.vector.adapters.pgvector import PgVectorStore
from redstring.chunks.adapters.postgres import PostgresChunkStore

client = Redstring.connect(
    graph_store=Neo4jGraphStore(uri="neo4j://localhost:7687", auth=("neo4j", "password")),
    vector_store=PgVectorStore(connection_string="postgresql://..."),
    chunk_store=PostgresChunkStore(connection_string="postgresql://..."),
    provider=llm_provider,
    embeddings=embedding_provider,
)
```

## Ingesting content with flexible input coercion

The client accepts multiple input representations without requiring manual
wrapping into `SourceDocument`:

1. **Plain string**: Automatically generates a deterministic content-addressed `SourceId`.
2. **Dictionary**: Uses `{"text": ..., "id": ...}` with optional metadata.
3. **`SourceDocument`**: Used directly when explicit metadata or source IDs are needed.

To ingest a single document:

```python
# Ingest from plain string
report = await client.build_graph(
    "Alice Vance is the Lead Architect at OctoCorp, based in Seattle."
)

# Ingest from dictionary with explicit ID and metadata
report = await client.build_graph(
    {
        "id": "octocorp-memo-42",
        "text": "Bob Martinez joined the Security Guild under Alice Vance.",
        "metadata": {"department": "Security", "priority": "high"},
    }
)
```

`report` is a `GraphBuildReport` detailing the entities, relationships, vectors, and chunks extracted.

## Concurrent and streaming batch ingestion

When ingesting multiple documents, `client.build_graph_batch` processes them
concurrently with bounded semaphore concurrency (`max_concurrency`) and per-document error isolation:

```python
documents = [
    "Quantum computing leverages superposition and entanglement.",
    {"id": "doc-2", "text": "Qubits are the fundamental unit of quantum information."},
    "Cryogenic dilution refrigerators cool superconducting qubits to millikelvin temperatures.",
]

# Run concurrent ingestion with bounded concurrency
batch_report = await client.build_graph_batch(
    documents,
    max_concurrency=4,
    continue_on_error=True,
)

print(batch_report.total_entities_written, batch_report.total_relationships_written)
```

To stream progress incrementally as each document finishes extraction, use `client.build_graph_stream` (or standalone `build_graph_stream`):

```python
from redstring import BatchDocumentError, GraphBuildReport

async for item in client.build_graph_stream(documents, max_concurrency=4):
    if isinstance(item, GraphBuildReport):
        print(f"Completed {item.source_id}: {item.entities_written} entities")
    elif isinstance(item, BatchDocumentError):
        print(f"Failed {item.source_id}: {item.error}")
```

## Retrieving entities and passages

The client exposes hybrid search methods that fuse vector embeddings with
lexical matching via Reciprocal Rank Fusion (RRF):

```python
# Semantic & lexical retrieval over entities
entity_results = await client.retrieve_entities("lead systems architect", k=5)
for scored in entity_results.entities:
    print(scored.entity.name, scored.score)

# Dual-channel BM25 + dense cosine retrieval over document passages
passage_results = await client.retrieve_passages("cryogenic cooling requirements", k=3)
for scored_chunk in passage_results.chunks:
    print(scored_chunk.chunk.text, scored_chunk.score)
```

## Exploring graph topology and explainable paths

Traverse graph neighborhoods or discover the shortest multi-hop connection path between two entities with supporting `StoredChunk` passage citations:

```python
# Retrieve reachable neighbor entities within `depth` hops
neighbors = await client.neighbors("Alice Vance", depth=2)
for entity in neighbors:
    print(f"Neighbor: {entity.name} ({entity.entity_type})")

# Discover the shortest connection path between two entities with chunk citations
explanation = await client.explain_path("Alice Vance", "Security Guild", max_depth=3)
if explanation is not None:
    print("Path:", " -> ".join(e.name for e in explanation.entities))
    for chunk in explanation.chunks:
        print("Citation:", chunk.text)
```

## Exporting, filtering subgraphs, and importing portable graphs

Serialize a tenant's full graph or a filtered subgraph into Mermaid, JSON-LD, or GraphML, and re-import JSON-LD or GraphML snapshots via `client.import_graph` (or standalone `import_graph`, `parse_json_ld`, `parse_graphml`):

```python
from redstring import ExportFormat

# Export a focused 2-hop ego-subgraph filtered by entity and relationship types
subgraph_json_ld = await client.export_graph(
    format=ExportFormat.JSON_LD,
    center_entity_id=alice_id,
    max_depth=2,
    entity_types=["Person", "Organization"],
    relationship_types=["WORKS_AT", "REPORTS_TO"],
)

# Round-trip import JSON-LD or GraphML into a target tenant
import_report = await client.import_graph(
    subgraph_json_ld,
    format="json-ld",
    tenant_id=target_tenant_id,
)
print(import_report.entities_imported, import_report.relationships_imported)
```

## Analyzing graph topology and diffing tenant snapshots

Compute structural metrics—connected components, isolated singletons, degree distribution, and top-k hubs—or compare two tenant snapshots structurally:

```python
# Structural health and hub centrality diagnostics
topology = await client.analyze_topology(top_k_hubs=10)
print(topology.entity_count, topology.connected_components, topology.isolated_entities)
for hub in topology.top_hubs:
    print(hub.name, hub.degree, hub.in_degree, hub.out_degree)

# Structural diff between a baseline tenant and a candidate tenant
diff = await client.diff_graphs(other_tenant_id)
print(
    len(diff.added_entities),
    len(diff.removed_entities),
    len(diff.modified_entities),
    diff.is_identical,
)
```

## Explaining hybrid entity and passage retrieval

Inspect per-channel ranks, raw scores, Reciprocal Rank Fusion (RRF) contributions, matched blocking keys, BM25 term contributions, and dangling vector IDs:

```python
# Explain why entities ranked where they did
entity_expl = await client.explain_entity_retrieval("lead systems architect", k=5)
for match in entity_expl.matches:
    print(
        match.entity.name,
        match.dominant_channel,
        match.semantic_rrf_contribution,
        match.lexical_rrf_contribution,
        match.best_lexical_field,
    )

# Explain passage retrieval with per-term BM25 contributions
chunk_expl = await client.explain_passage_retrieval("cryogenic cooling", k=3)
for match in chunk_expl.matches:
    print(match.chunk.id, match.dominant_channel, dict(match.term_bm25_contributions))
```

## Tracing provenance lineage and auditing corpus health

Trace forward from a `SourceId` to every entity, relationship, and chunk it produced, trace backward from an `EntityId` (or entity name) across merged aliases, or audit overall corpus coverage:

```python
# Forward lineage from a source document
src_lineage = await client.trace_source_lineage(SourceId("octocorp-memo-42"))
print(len(src_lineage.entities), len(src_lineage.relationships), len(src_lineage.chunks))

# Backward evidence lineage for an entity (resolving merged aliases automatically)
ent_lineage = await client.trace_entity_lineage("Alice Vance")
if ent_lineage is not None:
    print(ent_lineage.source_ids, len(ent_lineage.chunks), len(ent_lineage.aliases))

# Tenant-wide corpus coverage and chunk health report
corpus_health = await client.analyze_corpus()
print(
    corpus_health.entity_chunk_coverage_ratio,
    corpus_health.linked_chunks,
    corpus_health.unlinked_chunks,
)
```

## Consolidation dry-run preview and tenant-wide sweeps

Preview candidate duplicate pairs across `MERGE`, `ADJUDICATE`, and `REJECT` decision bands without mutating the store, or execute a full tenant-wide consolidation sweep:

```python
# Non-destructive dry-run preview across all canonical entities (or a single subject)
preview = await client.preview_consolidation(high=0.85, low=0.40)
print(len(preview.merge_candidates), len(preview.adjudicate_candidates))

# Live tenant-wide consolidation sweep (skips already-aliased entities)
sweep_report = await client.consolidate_tenant(high=0.85, low=0.40)
print(
    sweep_report.entities_scanned,
    sweep_report.aliases_skipped,
    sweep_report.merges_executed,
    sweep_report.relationships_redirected,
)
```

## Auditing ontology schema conformance and visualizing schemas

Validate a tenant's stored entities and relationships against a `DomainSchema` (or registered domain ID) and render the ontology itself as a Mermaid diagram:

```python
from redstring import format_schema_mermaid

conformance = await client.audit_schema_conformance("medical")
print(conformance.conformance_rate, len(conformance.violations))

# Render the DomainSchema ontology as a Mermaid flowchart
ontology_mermaid = format_schema_mermaid("medical")
```
