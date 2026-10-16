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
    llm=llm_provider,
    embedding=embedding_provider,
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
    llm=llm_provider,
    embedding=embedding_provider,
)
```

## Ingesting content with flexible input coercion

The client accepts multiple input representations without requiring manual
wrapping into `SourceDocument`:

1. **Plain string**: Automatically generates a deterministic UUID source ID.
2. **Dictionary**: Uses `{"text": ..., "id": ...}` with optional metadata.
3. **`SourceDocument`**: Used directly when explicit chunking configurations are needed.

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

`report` is a `BuildGraphReport` detailing entities and relationships extracted.

## Concurrent batch ingestion

When ingesting multiple documents, `client.build_graph_batch` processes them
concurrently with bounded semaphore concurrency to saturate downstream models
without exceeding rate limits:

```python
documents = [
    "Quantum computing leverages superposition and entanglement.",
    {"id": "doc-2", "text": "Qubits are the fundamental unit of quantum information."},
    "Cryogenic dilution refrigerators cool superconducting qubits to millikelvin temperatures.",
]

# Run concurrent ingestion with bounded concurrency
reports = await client.build_graph_batch(
    documents,
    max_concurrency=4,
)

total_entities = sum(len(r.entities) for r in reports)
total_relationships = sum(len(r.relationships) for r in reports)
```

You can also call the standalone `build_graph_batch` function directly from
`redstring` if managing stores manually:

```python
from redstring import TenantId, build_graph_batch

reports = await build_graph_batch(
    documents,
    provider=llm_provider,
    store=graph_store,
    tenant_id=tenant_id,
    vector_store=vector_store,
    embedding_provider=embedding_provider,
    max_concurrency=8,
)
```

## Retrieving entities and passages

The client exposes hybrid search methods that fuse vector embeddings with
lexical matching:

```python
# Semantic & lexical retrieval over entities
entities = await client.retrieve_entities("lead systems architect", limit=5)
for result in entities:
    print(result.entity.name, result.score)

# Semantic retrieval over raw document chunks
passages = await client.retrieve_passages("cryogenic cooling requirements", limit=3)
for passage in passages:
    print(passage.chunk.text, passage.score)
```

## Exploring graph topology

Inspect entity connections and traverse graph neighborhoods directly through
the client:

```python
# Retrieve adjacent relationships using the target entity or its name
neighbors = await client.neighbors("Alice Vance")
for rel in neighbors:
    print(f"Relationship: {rel.relationship_type} -> {rel.target_entity_id}")
```

## Consolidating duplicates

To identify and merge duplicate entities across the ingested corpus:

```python
summary = await client.consolidate()
print(f"Consolidation complete: evaluated pairs across graph.")
```
