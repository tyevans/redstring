# kg-builder

Build a knowledge graph out of documents you already have: extract entities and
relationships with a language model, consolidate duplicates, and keep a graph store
in step with the result.

You supply the documents. This library does not fetch anything.

## Install

```
uv add kg-builder                 # in-memory adapters, the fake provider
uv add "kg-builder[llm]"          # any OpenAI-compatible server, via langchain-openai
uv add "kg-builder[neo4j]"        # the Neo4j GraphStore adapter
```

Python 3.13+.

## Use

```python
from kg_builder import InMemoryGraphStore, SourceDocument, build_graph
from kg_builder.llm.adapters.langchain import LangChainLlmProvider

store = InMemoryGraphStore()

report = await build_graph(
    SourceDocument(id="notes", text="Ada Lovelace worked with Charles Babbage."),
    provider=LangChainLlmProvider(chat_model),
    store=store,
    tenant_id=tenant_id,
)

people = await store.find_entities(tenant_id, entity_type="Person")
neighbours = await store.neighbors(people[0].id, tenant_id)
```

`docs/examples/build_a_graph.py` is the same thing, complete and runnable against the
fake provider. It is executed by the test suite on every commit, so it cannot go stale.

Pass `domain="literature_fiction"` to specialise the prompt to one of the bundled domain
schemas, or `domain=AUTO` to have the content classifier choose (one extra model call).

## The public API

`from kg_builder import ...` — everything in that module's `__all__` is supported.
Anything reached through a dotted path is internal and may change. The module docstring
is the reference, and it says what is deliberately left out.

## How it fits together

```
SourceDocument -> ExtractionPipeline -> Document.record_extraction
               -> DocumentExtracted -> GraphProjection -> GraphStore
```

Extraction **emits an event and writes nothing**. A projection folds that event into a
store. `build_graph` does both in one call for a caller who has no event store; a caller
who has one appends `report.event` and drives `kg_builder.projections.project` over the
feed instead. That separation is why a store can be rebuilt from the log.

| Package | What it is |
|---|---|
| `kg_builder.composition` | `build_graph` — the only module that holds both halves |
| `kg_builder.domain` | `Entity`, `Relationship`, `Alias`, similarity, temporal parsing |
| `kg_builder.ports` | `GraphStore`, `VectorStore`, `LlmProvider` |
| `kg_builder.graph` / `.vector` | Adapters: in-memory, Neo4j, pgvector |
| `kg_builder.llm` | Provider adapters, retry, rate limiting, circuit breaking, caching |
| `kg_builder.extraction` | Chunking, the pipeline, mapping, merging, domain prompting |
| `kg_builder.consolidation` | Deciding that two entities are one, and undoing it |
| `kg_builder.temporal` | Interval inference and time-sliced queries |
| `kg_builder.aggregates` / `.events` / `.projections` | The write model and the read model |

Implementing a port of your own? `tests/compliance/` is a suite you can point at it;
it is what says whether you got the contract right.

## Development

```
uv sync --all-extras && uv run pre-commit install
```

Every quality gate — ruff, mypy `--strict`, bandit, the layered import contract, and
pytest under a coverage ratchet — runs on `git commit`. Do not run them separately.

```
uv run pytest -m integration              # needs docker-compose.test.yml
uv run pytest -m accuracy tests/accuracy/ # needs a live LLM
```

`BACKLOG.md` carries every known gap, each with enough context to pick up cold.
