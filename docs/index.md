# redstring

**Build a knowledge graph from documents you already have.** Extract entities
and relationships with a language model, decide which of them are the same
thing, and keep a graph store in step with the result.

The name is the picture: facts pinned up from what you have read, and string
drawn between the ones that connect.

---

## The problem this solves

You have a corpus — support tickets, filings, papers, an internal wiki — and
the questions you want to ask of it are about *connections*. Who else worked
on this. What changed between these two contracts. Which incidents share a
root cause. Full-text search cannot answer those, because the answer is not in
any one document; it is in the relationships between them.

Getting from documents to a graph that can answer such questions is three
problems wearing one coat, and they fail in different ways:

1. **Extraction.** A model reads a document and names the entities and
   relationships in it. This is the part everyone starts with, and the part
   that is nearly a solved problem — one careful prompt gets you most of the
   way.
2. **Consolidation.** "Ada Lovelace", "Lovelace, A." and "Ada King" are one
   person, and nothing in step 1 knows that, because each document was read on
   its own. Skip this and your graph accumulates one node per *mention* — a
   structure that looks like a knowledge graph and answers no question
   correctly, because every entity's edges are split across its aliases.
3. **Storage that can be rebuilt.** Extraction is non-deterministic and models
   change. A store written to directly is a store you cannot regenerate when
   a better prompt lands, and cannot audit when an edge turns out to be wrong.

redstring treats all three as first-class, and the third is what shapes the
architecture: **extraction writes to no store.** It emits an event describing
what was found, and a projection folds that event into the graph. So the store
is a *derived* value, the log is the source of truth, and "re-extract
everything with the new prompt" is a replay rather than a migration.

!!! note "What this library does not do"

    **It never fetches content.** No crawling, no HTML cleanup, no PDF
    parsing. You supply a `SourceDocument`; getting one is a different problem
    with different failure modes, and libraries that do both tend to do
    neither well.

## What it looks like

```python
from redstring import FakeLlmProvider, InMemoryGraphStore, SourceDocument, build_graph

store = InMemoryGraphStore()

report = await build_graph(
    SourceDocument(id="notes", text="Ada Lovelace worked with Charles Babbage."),
    provider=FakeLlmProvider(by_substring={"Ada": ANSWER}),
    store=store,
    tenant_id=tenant_id,
)

people = await store.find_entities(tenant_id, entity_type="Person")
neighbours = await store.neighbors(people[0].id, tenant_id)
```

`FakeLlmProvider` and `InMemoryGraphStore` are real implementations rather
than mocks. Swapping them for `LangChainLlmProvider` and `Neo4jGraphStore`
changes the two constructor calls and nothing else in the composition — which
is the point of the ports, and the reason the test suite can run the whole
pipeline with no infrastructure.

[Your first graph](getting-started.md) walks through a complete runnable
version.

## Three commitments

Almost every API here follows from one of three decisions.

**Async-first.** Both store ports, the LLM port and the cache port are `async`
protocols. There is no parallel blocking API; a synchronous caller wraps the
one coroutine it needs.

**The write model is events.** `DocumentExtracted`, `EntitiesMerged` and
`MergeUndone` are the whole schema, and both producers — extraction and
consolidation — emit rather than write.
[ADR 0004](adr/0004-consolidation-emits-events.md) records what collapsing
that back into direct writes would cost, and
[ADR 0001](adr/0001-event-log-schema-and-granularity.md) records the schema
itself, which is the one decision in the library that cannot be taken back: a
log already written cannot be refactored.

**Backends are ports.** `GraphStore` and `VectorStore` are Protocols with an
in-memory implementation and a real one each (Neo4j, pgvector), and
`src/redstring/testing/` is a shared contract suite that *both* run unchanged. Code
written against the interface in a test is the code that runs against the
database in production — and a contract two implementations satisfy by
accident is not a contract, which is why the in-memory adapter is never the
only one.

## Multi-tenancy is not optional

Every store call takes a `tenant_id`, and every compliance suite asserts that
reads never cross tenants. This is not a feature to enable; it is a parameter
you cannot omit, because a knowledge graph that leaks one customer's entities
into another's answers is a data breach rather than a bug.

## The public API is gated, not curated

`from redstring import ...` — everything in `__all__` is supported, and
anything reached by a dotted path is internal and may move in a patch release.

The surface is **closed**, which is a stronger claim than "documented": every
type named in an exported signature is either exported too or recorded with
the package it comes from, and every `RedstringError` is either exported or
recorded against the capability whose export would bring it. Three tests hold
that, each blind to what the other two catch —
[ADR 0006](adr/0006-the-public-surface-is-gated.md) explains why all three are
needed.

Two capabilities are deliberately **not** exported yet. `redstring.consolidation`
and `redstring.temporal` are both real and both tested, but neither has a
composed entry point, and exporting the classes would publish an API whose
shape is still being decided by callers it does not have. Reach them by dotted
path and expect movement.

## Where to go next

<div class="grid cards" markdown>

-   **Get it running**

    [Installation](installation.md) — the base install, the two extras, and
    which one you actually need.

    [Your first graph](getting-started.md) — one document to a queryable
    graph, with no server.

-   **Do a specific thing**

    [How-to guides](how-to/index.md) — authoring a domain schema,
    consolidating duplicates, driving projections from an event store,
    rebuilding one, querying a timeline, writing a store adapter.

-   **Look something up**

    [Reference](reference/index.md) — the events, the aggregates, the domain
    value types, the schema YAML, the Neo4j store, the quality gates.

-   **Understand a decision**

    [Decisions](adr/index.md) — the choices that are expensive to revisit,
    each with the alternative that was rejected and why.

</div>

redstring targets Python 3.13+, ships a `py.typed` marker, is MIT licensed,
and is distributed on PyPI as
[`redstring`](https://pypi.org/project/redstring/). Source lives at
[github.com/tyevans/redstring](https://github.com/tyevans/redstring).
