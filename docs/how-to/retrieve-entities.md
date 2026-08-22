# Retrieve entities

Once entities are in a `GraphStore` and their embeddings in a `VectorStore`,
`Retriever` turns a query string into ranked entities. It runs two channels and
fuses them: a **semantic** one that embeds the query and searches the vector
store, and a **lexical** one that finds candidates by blocking key and scores
them by string similarity.

Everything here is in `redstring.__all__`, so nothing in this guide reaches
past the public API.

You will need:

- a `GraphStore` holding entities,
- a `VectorStore` holding their embeddings, and an `EmbeddingProvider` of the
  **same dimension** — `Retriever` refuses a mismatched pair at construction,
- entities carrying `blocking_keys`. Extraction writes them; an entity without
  them can still be found semantically but is invisible to the lexical channel.

Read [ADR 0022](../adr/0022-the-lexical-channel-is-not-bm25.md) before tuning
anything. It records why the lexical channel is not a term-weighted ranker and
why the channels fuse by rank rather than by a weighted score.

## Retrieve

```python
import asyncio
from uuid import uuid4

from redstring import (
    FakeEmbeddingProvider,
    FakeLlmProvider,
    InMemoryGraphStore,
    InMemoryVectorStore,
    RetrievalMode,
    Retriever,
    SourceDocument,
    build_graph,
)

ANSWER = {
    "entities": [
        {"name": "Ada Lovelace", "entity_type": "Person"},
        {"name": "Charles Babbage", "entity_type": "Person"},
        {"name": "Analytical Engine", "entity_type": "Machine"},
    ],
    "relationships": [],
}


async def main() -> None:
    tenant_id = uuid4()
    graph = InMemoryGraphStore()
    embeddings = FakeEmbeddingProvider()
    vectors = InMemoryVectorStore(dimension=embeddings.dimension)

    await build_graph(
        SourceDocument(id="notes", text="Ada Lovelace worked with Charles Babbage."),
        provider=FakeLlmProvider(by_substring={"Ada": ANSWER}),
        store=graph,
        tenant_id=tenant_id,
        embedding_provider=embeddings,
        vector_store=vectors,
    )

    retriever = Retriever(embeddings=embeddings, vectors=vectors, graph=graph)

    # Misspelled on purpose: it shares a blocking key with the stored name.
    result = await retriever.retrieve("Charles Babage", tenant_id, k=5)

    for match in result.matches:
        print(
            f"{match.entity.name}: {match.score:.4f} "
            f"semantic={match.semantic} lexical={match.lexical}"
        )


asyncio.run(main())
```

`retrieve` takes the query and a tenant, and four keyword arguments:

| Argument | Default | Means |
|---|---|---|
| `k` | `10` | Maximum results. `k=0` returns nothing; a negative `k` raises `ValueError`. |
| `entity_types` | `None` | `None` is no filter; `[]` matches **nothing**. Applied to both channels. |
| `mode` | `RetrievalMode.HYBRID` | Which channels run. |

A blank query — empty, or only whitespace — raises `ValueError` rather than
returning everything or nothing. Both of those answers would hide the caller
bug that produced it.

## The three modes

```python
await retriever.retrieve(q, tenant_id, mode=RetrievalMode.HYBRID)  # both
await retriever.retrieve(q, tenant_id, mode=RetrievalMode.SEMANTIC)  # vectors only
await retriever.retrieve(q, tenant_id, mode=RetrievalMode.LEXICAL)  # names only
```

`LEXICAL` makes **no embedding call at all**, which matters when the provider
is a paid API: it is the mode for a type-ahead box or a name lookup, where a
round trip per keystroke is the whole cost of the feature.

`SEMANTIC` is the mode for a question rather than a name — "who worked on
early computing" retrieves nothing lexically, because no blocking key of that
phrase matches any stored name.

## Retrieve lexically with no embedding provider at all

`LEXICAL` skips the embedding *call*, but a `Retriever` built the usual way
still requires a provider and a vector store to construct. If you want only
the name channel, build the retriever that says so:

```python
retriever = Retriever.lexical_only(graph=graph)
await retriever.retrieve("Ada Lovelase", tenant_id)  # LEXICAL by default
```

There is no `EmbeddingProvider` and no `VectorReader` anywhere in that path,
so an endpoint that is absent, paid for, or misconfigured cannot take
misspelling-tolerant entity search down with it. The retriever's default mode
is `LEXICAL`, and asking it for `SEMANTIC` or `HYBRID` raises `ValueError`
rather than quietly answering with the lexical half.

`ChunkRetriever.lexical_only(chunks=chunks)` is the same thing over the chunk
corpus. Note one difference it does not change: a `HYBRID` query over a corpus
whose rows carry no embeddings still answers lexically and does not raise,
because "unembedded" is a per-row fact rather than a configuration.

## Reading the scores

`ScoredEntity` carries three numbers, on two different scales, and confusing
them is the easiest mistake to make here.

- **`score`** is the fused score, from reciprocal rank fusion. It is
  **ordinal**: comparable within one result set, meaningless across queries,
  and never interpretable as a similarity. It is not bounded to `0..1` — two
  channels agreeing at rank 0 give `2/61`, and nothing caps the sum.
- **`semantic`** is on `VectorMatch`'s scale: cosine mapped onto `0..1`.
- **`lexical`** is Jaro-Winkler on `0..1`.

**`None` and `0.0` are different facts.** A component is `None` when that
channel did not rank the entity at all, and a float when it did. So
`match.semantic is None` in `LEXICAL` mode says the channel was off, while
`match.semantic == 0.0` would say the channel ran and scored it zero. Do not
collapse them with `or 0.0` — that turns "not measured" into "measured as
nothing", which is the same error `SimilarityFeatures` avoids for merge
decisions.

Both components are retained after fusion precisely so a caller can see what
fusion discarded: RRF uses only position, so a semantic match at `0.99` and
one at `0.51` contribute equally if both rank first.

## The limitation to plan around: lexical recall is bounded by blocking

**A query that shares no blocking key with an entity cannot be retrieved
lexically, however similar the strings are.**

There is no text index in this library. Lexical candidates come from the same
prefix and soundex keys consolidation uses, so a query is blocked on the first
five characters of its normalized form and on the soundex of the whole string.
The consequence in practice:

| Query | Against `Ada Lovelace` | Why |
|---|---|---|
| `Ada Lovelace` | found | prefix and soundex both match |
| `Ada Lovelace, Countess` | found | shares the `ada l` prefix |
| `ada  LOVELACE` | found | normalization folds case and whitespace |
| `Lovelace` | **not found** | different prefix, different soundex — nothing blocks it |

That last row is the one to design around. A caller who searches by surname
alone and gets nothing has hit a stated limit, not a bug. The semantic channel
is what covers it, and against a real embedding model it usually does — which
is an argument for leaving `mode` at `HYBRID` unless you have a reason not to.

Note that entities are found by their **names and properties**, not by the
text they were extracted from: the source document is not retained. Retrieval
here answers "which entities match this string", not "which passages discuss
this topic".

## What a result does not tell you

Two honest gaps, both filed:

- **Aliases are not consulted.** `GraphStore.find_aliases` and
  `resolve_entity_ids` exist, and a query matching an alias name retrieves
  nothing today (B83).
- **Nothing measures whether hybrid beats semantic alone** on your corpus. The
  claim that fusion helps is an argument from how the two channels fail, not a
  measured result (B81), and `PROPERTY_WEIGHT` is a judgement rather than a
  fitted value (B80).

## Related

- [ADR 0022](../adr/0022-the-lexical-channel-is-not-bm25.md) — why not BM25,
  why rank fusion, and what blocking costs in recall.
- [ADR 0021](../adr/0021-composition-holds-a-second-module.md) — why
  `Retriever` sits on the composition layer.
- [ADR 0017](../adr/0017-the-embedding-provider-port.md) — the
  `EmbeddingProvider` port and its dimension check.
- [Use the pgvector store](use-the-pgvector-store.md) — swapping the in-memory
  vector store for a real one.
