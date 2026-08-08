# Your first graph

One document in, a queryable graph out — with no model server, no database,
and nothing installed beyond the base package.

The program below is `docs/examples/build_a_graph.py` in the repository, and
`tests/unit/test_end_to_end_example.py` executes it on every commit. That test
also asserts every import in it comes from `redstring` itself, so this page
cannot quietly start depending on an internal path.

## The whole program

```python
import asyncio
from uuid import uuid4

from redstring import (
    FakeLlmProvider,
    InMemoryGraphStore,
    SourceDocument,
    build_graph,
)

# What the model "finds" in the text below. A real provider reads the text.
ANSWER = {
    "entities": [
        {"name": "Ada Lovelace", "entity_type": "Person"},
        {"name": "Charles Babbage", "entity_type": "Person"},
        {"name": "Analytical Engine", "entity_type": "Machine"},
    ],
    "relationships": [
        {
            "source_name": "Ada Lovelace",
            "target_name": "Charles Babbage",
            "relationship_type": "WORKED_WITH",
        },
        {
            "source_name": "Charles Babbage",
            "target_name": "Analytical Engine",
            "relationship_type": "DESIGNED",
        },
    ],
}


async def main() -> tuple[list[str], list[str]]:
    tenant_id = uuid4()
    store = InMemoryGraphStore()

    report = await build_graph(
        SourceDocument(
            id="lovelace-notes",
            text="Ada Lovelace worked with Charles Babbage on the Analytical Engine.",
        ),
        provider=FakeLlmProvider(by_substring={"Ada": ANSWER}),
        store=store,
        tenant_id=tenant_id,
    )

    people = await store.find_entities(tenant_id, entity_type="Person")
    babbage = next(entity for entity in people if entity.name == "Charles Babbage")
    neighbours = await store.neighbors(babbage.id, tenant_id)

    print(f"{report.entities} entities, {report.relationships} relationships")
    return (
        sorted(entity.name for entity in people),
        sorted(entity.name for entity in neighbours),
    )


if __name__ == "__main__":
    asyncio.run(main())
```

```
3 entities, 2 relationships
```

## What each piece is doing

**`SourceDocument`** is the input contract, and it is the whole of it: an id
and some text. There is no fetching step to configure because the library does
not fetch — see [the front page](index.md#the-problem-this-solves).

**`FakeLlmProvider`** is a real `LlmProvider`, not a mock. `by_substring` maps
a needle to the answer returned when the chunk contains it, which makes an
extraction test deterministic without patching anything. The other constructor
form, `script=[...]`, returns answers in order. Both are what let the suite run
the real pipeline with no server.

**`build_graph`** is the composition — extract, then project — and it is the
only function in the library that holds both halves. `extraction` may not
import `projections`, which is what keeps a store reference out of the
pipeline; `build_graph` exists because something has to hold both or the
library ships two halves and a diagram
([ADR 0007](adr/0007-composition-is-the-only-top-layer.md)).

**`tenant_id`** is not optional anywhere. Every read is scoped to it and every
compliance test asserts reads never cross tenants.

**`report`** is a `GraphBuildReport`: counts, the `DocumentExtracted` event
that was folded, and — when the classifier ran — which domain prompt was used
and how confident it was.

## Using a real model

Two lines change. Everything else on this page is identical:

```python
from langchain_openai import ChatOpenAI
from redstring.llm.adapters.langchain import LangChainLlmProvider

chat_model = ChatOpenAI(model="qwen3-30b", base_url="http://localhost:8080/v1", api_key="-")
provider = LangChainLlmProvider(chat_model, model="openai-compatible/qwen3-30b")
```

Constructing the chat model is the one step the example does not show, because
it is langchain's step rather than this library's. Needs the `llm` extra — see
[Installation](installation.md#the-extras).

## Specialising the prompt

`build_graph` takes a `domain`, which selects one of the six bundled schemas
and shapes the prompt around it:

```python
report = await build_graph(
    document, provider=provider, store=store, tenant_id=tenant_id, domain="literature_fiction"
)
```

Or let a classifier choose, at the cost of one extra model call:

```python
from redstring import AUTO

report = await build_graph(document, ..., domain=AUTO)
print(report.domain, report.domain_confidence)
```

`AUTO` is the **sentinel exported from `redstring`**, not the string
`"auto"` — that would be read as a domain id like any other.

!!! warning "`AUTO` never raises, and a fallback looks like a choice"

    Three paths fall back to `encyclopedia_wiki`: a document under 100
    characters is not classified at all, an answer below the confidence
    threshold is replaced, and an `LlmProviderError` from the classifier is
    caught. All three report `domain == "encyclopedia_wiki"`, which is exactly
    what a *confident* classification of an encyclopedia article reports.

    `report.domain_confidence` is the only field that tells them apart: `0.0`
    means the classifier gave up, and `None` means no classifier ran — which
    includes every call that named its own `domain`, so filtering on `== 0.0`
    does not sweep those up.

A schema **prompts the model; it does not constrain it.** An entity type the
schema never mentions is not an error, and nothing validates the model's
answer against the schema —
[ADR 0011](adr/0011-domain-schemas-prompt-but-do-not-constrain.md) records
why. To write your own, see
[Author a domain schema](how-to/author-a-domain-schema.md).

## Where this example stops

It builds a graph from **one** document. Three things become real the moment
there is a second one, and each has its own guide:

- **The same entity appears in both, under different names.** That is
  [Consolidate duplicate entities](how-to/consolidate-duplicate-entities.md).
  Skipping it gives you one node per mention.
- **You want the graph rebuildable.** `build_graph` writes directly, which is
  the right shape for a caller with no event store. A caller who has one
  appends `report.event` and drives the projection over the feed instead:
  [Drive projections from an event store](how-to/drive-projections-from-an-event-store.md).
- **The model is flaky, slow or rate-limited.** Retry, rate limiting, circuit
  breaking and caching sit between the pipeline and the provider:
  [Harden model calls](how-to/harden-model-calls.md).
