# ADR 0042: Themes are recomputed, never stored

**Status:** accepted.

**Why this is an ADR:** it adds a thematic read surface above the entity —
the capability BACKLOG **B142** filed and explicitly refused to let anyone
start in code. Four questions had to be answered before a line was worth
writing: which layer holds it, whether communities are persisted, what a
community is in a multi-tenant store, and whether clustering is a
`GraphStore` capability or a caller-side algorithm.

## Context

`Retriever.retrieve` fuses two channels and returns `k` scored *entities*.
That is the entire read surface. "What are the main themes in this corpus" has
no answer and no composition of the existing ports produces one: `neighbors`
walks out from an entity you already know, and both retrieval channels rank
individual entities against a query string.

Microsoft GraphRAG's answer is two steps — cluster the graph, then write one
report per cluster with a model call. The cost shape is why it is worth
having at all: model calls scale with the number of *communities*, that is
with the corpus's structure, not with its length. It is the one place in that
design where a model call does not scale with tokens ingested.

[`0023`](0023-the-chunk-corpus.md) is what makes
the second step tractable here. GraphRAG's fast pipeline summarises a
community from its **source text units** rather than from entity descriptions,
because in that pipeline the descriptions are empty. We store passages now, so
that variant is available and **B117**'s description-quality problem does not
block this.

## Decision

### Communities are recomputed on every call and never persisted

A community is a function of the whole graph at one instant. Every
`DocumentExtracted` and every `EntitiesMerged` can move a node between
clusters, and can do so for nodes the new document never mentioned — a single
edge joining two previously separate regions re-partitions both. So a stored
community is stale the moment the next document lands, and there is no
invalidation smaller than "recompute everything".

That is the difference from every other judgement in this library.
[`0004`](0004-consolidation-emits-events.md) says a judgement emits an event,
and a community report *is* a judgement — but the rule's premise is that the
judgement is about something durable enough for the record to remain true.
A merge is about two entity ids and stays true. A community is about a
partition of a graph that no longer exists.

So there is no `CommunityId`, no community event, no community store, and
nothing new for a projection to write. `summarize_themes` returns its reports
to the caller, who keeps them for exactly as long as they are useful. This is
the same shape as `PipelineResult`: computed, returned, written nowhere.

**The route back**, when a caller needs community identity stable across
calls — cross-call diffing, a UI that pins a theme, incremental reporting —
is not "persist what we compute now". It is a decision about what makes two
partitions' communities *the same* community, which is a harder question than
storage and has to be answered first. Filed as B147.

### Clustering is a caller-side algorithm over the existing port

ADR [`0016`](0016-graph-store-is-five-capabilities.md) says the port is
capabilities, and "read the whole tenant's topology" is not one of the five.
It does not have to become one: `find_entities` already pages over a total
order with a resumable cursor, and `get_relationships_for` takes a batch of
ids. Page the entities, batch their edges, and the whole topology arrives
through capabilities that already exist, with no port change and no adapter
obliged to grow a method.

A bulk-topology read would be faster and is deliberately not being added.
Nothing has measured the paged read as a cost centre, and widening a port that
two adapters and a published compliance suite implement is not a speculative
change. Filed as B148 with what would justify it.

### The clustering itself is a pure function in `domain`

`domain/community.py` takes node ids and weighted edges and returns a
partition. No store, no I/O, no model — so it is testable against
hand-built graphs where the right answer is known, which is the property
that matters most for an algorithm whose output is otherwise hard to
falsify.

**Modularity optimisation in pure Python, not Leiden via `leidenalg`.**
`igraph`/`leidenalg` are a C dependency with wheels that a consumer of a
library like this one should not be made to acquire for a capability they may
not use, and per CLAUDE.md a new third-party client owes a confinement row in
the same commit. The greedy modularity pass here is the first half of Louvain
and gives the same *kind* of answer; where they differ is on quality at scale,
which is a measurement nobody here has taken. Swapping the implementation
behind this function later changes no signature — filed as B149.

**Determinism is part of the contract.** Modularity optimisation is
order-sensitive, and a clustering that varies run to run makes every
downstream report unreproducible and every test a coin flip. Nodes are visited
in ascending id order and every tie is broken by the same order, so two runs
over the same graph return the identical partition — asserted, not assumed.

### The composition is a new module in `composition`

`themes.py` joins `graph`, `llm` and `chunks`. Those are three siblings in the
band, forbidden from importing each other, so no lower layer can hold all
three — which is the test CLAUDE.md sets for admission to this layer, and
`retrieval.py` passes it the same way for a different triple.

It holds the *narrowest* ports it uses: `EntityReader` and
`RelationshipStore`, not the composed `GraphStore`. A theme summariser that
could wipe a tenant is a fact worth keeping out of a signature.

### A community is per-tenant by construction

Every read the summariser makes is tenant-scoped, so no cross-tenant edge can
enter the partition and the question does not arise at the algorithm's level
at all. `domain/community.py` never sees a `TenantId`, which is the point:
the isolation is enforced where every other read enforces it, not re-derived
by a second mechanism that could disagree.

## Consequences

- There is a thematic read surface, and it is one function.
- Nothing new is stored, no event is added, and replay is unaffected.
- The clustering can be replaced without touching a signature.
- A caller wanting stable themes across calls is not served, deliberately.
