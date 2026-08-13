# How-to guides

Task-shaped recipes. Each one assumes you have a working install and starts
from a concrete goal rather than from a concept — for the concepts, see
[Decisions](../adr/index.md); for the shape of a type, see
[Reference](../reference/index.md).

## Build and extract

- [**Author a domain schema**](author-a-domain-schema.md) — the six bundled
  schemas are a starting point, not a limit. Covers the YAML, what each field
  does to the prompt, and the thing that surprises everyone: a schema shapes
  the prompt and validates nothing, so an off-schema entity comes back
  unflagged.
- [**Harden model calls**](harden-model-calls.md) — retry with jitter, rate
  limiting, circuit breaking, and caching, all composed over the `Cache` port
  so a single process and a fleet behave the same way.

## Search and read

- [**Retrieve entities**](retrieve-entities.md) — a query string to ranked
  entities, fusing a semantic channel over the vector store with a lexical
  one over blocking keys. Read it for what the two score scales mean and for
  the recall blocking costs you.

## Keep the graph honest

- [**Consolidate duplicate entities**](consolidate-duplicate-entities.md) —
  the guide to read second. A populated graph through blocking, scoring,
  banding and adjudication, to a merge you can audit and reverse. This is the
  step whose absence makes a knowledge graph quietly wrong.
- [**Query a timeline**](query-a-timeline.md) — temporal extents, the interval
  relations inferred between them, and time-sliced reads.

## Storage and projections

- [**Use the write model**](use-the-write-model.md) — the aggregates, the
  three events, and how to emit rather than write.
- [**Drive projections from an event store**](drive-projections-from-an-event-store.md) —
  what to do instead of `build_graph` when you have a real event store.
- [**Rebuild a projection**](rebuild-a-projection.md) — wipe and replay, which
  is the payoff for extraction writing to no store.
- [**Index documents without extracting them**](index-documents.md) — build a
  corpus of passages with no model call, what an empty `entity_ids` means, and
  the extract-then-index case that is lossy.
- [**Use the pgvector store**](use-the-pgvector-store.md) — schema, the score
  expression, and why there is deliberately no ANN index.
- [**Implement a store adapter**](implement-a-store-adapter.md) — writing a
  third `GraphStore` or `VectorStore`, and pointing the compliance suite at it
  so you find out whether you got the contract right.

## Development

- [**Run the integration and mutation suites**](run-integration-and-mutation-suites.md) —
  everything the default suite deliberately leaves out, including the two
  invocation constraints that produce dozens of failures reading as flakiness.
- [**Run the ingestion benchmark**](run-the-benchmark.md) — wall-clock and
  accuracy against a live endpoint, what it refuses and why, and the exit
  code table.
