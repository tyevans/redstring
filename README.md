# redstring

[![CI](https://github.com/tyevans/redstring/actions/workflows/ci.yml/badge.svg)](https://github.com/tyevans/redstring/actions/workflows/ci.yml)
[![Docs](https://github.com/tyevans/redstring/actions/workflows/docs.yml/badge.svg)](https://tyevans.github.io/redstring)
[![PyPI](https://img.shields.io/pypi/v/redstring)](https://pypi.org/project/redstring/)
[![Python](https://img.shields.io/pypi/pyversions/redstring)](https://pypi.org/project/redstring/)
[![License](https://img.shields.io/pypi/l/redstring)](LICENSE)

**Build a knowledge graph from documents you already have.** Extract entities
and relationships with a language model, decide which of them are the same
thing, and keep a graph store in step with the result.

The name is the picture: facts pinned up from what you have read, and string
drawn between the ones that connect.

📖 **[Documentation](https://tyevans.github.io/redstring)** ·
[Getting started](https://tyevans.github.io/redstring/getting-started/) ·
[How-to guides](https://tyevans.github.io/redstring/how-to/) ·
[Decisions](https://tyevans.github.io/redstring/adr/)

---

## Why

You have a corpus, and the questions you want to ask of it are about
*connections*: who else worked on this, what changed between these two
contracts, which incidents share a cause. Full-text search cannot answer
those, because the answer is not in any one document.

Getting from documents to a graph that can answer them is three problems, and
they fail differently:

- **Extraction** — a model names the entities and relationships in a
  document. Nearly a solved problem; one careful prompt gets most of the way.
- **Consolidation** — "Ada Lovelace", "Lovelace, A." and "Ada King" are one
  person, and nothing in extraction knows that, because each document was read
  alone. Skip this and you get one node per *mention*: a structure that looks
  like a knowledge graph and answers every question wrong, because each
  entity's edges are split across its aliases.
- **Storage you can rebuild** — extraction is non-deterministic and models
  change. A store written to directly cannot be regenerated when a better
  prompt lands, or audited when an edge turns out to be wrong.

redstring treats all three as first-class, and the third shapes the
architecture: **extraction writes to no store.** It emits an event describing
what was found, and a projection folds that into the graph. The store is a
derived value, the log is the truth, and "re-extract everything with the new
prompt" is a replay rather than a migration.

**It never fetches content.** No crawling, no HTML cleanup, no PDF parsing —
you supply a `SourceDocument`. Getting one is a different problem with
different failure modes.

## Install

```bash
uv add redstring                 # in-memory adapters, the fake provider
uv add "redstring[llm]"          # any OpenAI-compatible server, via langchain-openai
uv add "redstring[neo4j]"        # the Neo4j GraphStore adapter
```

Python 3.13+. Ships `py.typed`.

The base install carries no compiled dependency it does not use — `numpy` and
`httpx` were both declared and imported by nothing, and are gone.
`eventsource-py` is core deliberately (the exported types need it, and a
public API that fails to import without an extra is not a public API);
`asyncpg` and `redis` are core for now and tracked in `BACKLOG.md` B61. See
[Installation](https://tyevans.github.io/redstring/installation/).

## Use

```python
from redstring import InMemoryGraphStore, SourceDocument, build_graph
from redstring.llm.adapters.langchain import LangChainLlmProvider
from langchain_openai import ChatOpenAI

# Any OpenAI-compatible server: llama.cpp, vLLM, Ollama's shim, OpenAI itself.
chat_model = ChatOpenAI(model="qwen3-30b", base_url="http://localhost:8080/v1", api_key="-")
store = InMemoryGraphStore()

report = await build_graph(
    SourceDocument(id="notes", text="Ada Lovelace worked with Charles Babbage."),
    provider=LangChainLlmProvider(chat_model),
    store=store,
    tenant_id=tenant_id,
)

print(report.domain, report.domain_confidence)  # which prompt ran, and how sure

people = await store.find_entities(tenant_id, entity_type="Person")
neighbours = await store.neighbors(people[0].id, tenant_id)
```

`docs/examples/build_a_graph.py` is the same composition, complete and
runnable against `FakeLlmProvider` — no server, no extra. The test suite
executes it on every commit, including an assertion that it imports nothing
but `redstring`, so it cannot go stale.

Pass `domain="literature_fiction"` for one of the six bundled schemas, or
`domain=AUTO` to have a classifier choose. **`AUTO` never raises** — it falls
back to `encyclopedia_wiki` on three paths, and a fallback is
indistinguishable from a confident choice by `report.domain` alone.
`report.domain_confidence` is the field that tells them apart. See
[Getting started](https://tyevans.github.io/redstring/getting-started/#specialising-the-prompt).

## How it fits together

```
SourceDocument            -> ExtractionPipeline    -> DocumentExtracted     -\
                                                                             >- GraphProjection -> GraphStore
ConsolidationService.merge/resolve/undo -> EntitiesMerged | MergeUndone     -/
```

Two producers, one projection. Extraction **emits an event and writes
nothing**; consolidation is the same shape — it reads the graph to work out
what a merge would do, records that as an `EntitiesMerged` (or a
`MergeUndone`), and writes to no store of its own. Both fold through
`GraphProjection`, so the store stays a projection of the log rather than a
second source of truth.

`build_graph` does both in one call for a caller who has no event store; a
caller who has one appends `report.event` and drives
`redstring.projections.project` over the feed instead. That separation is why
a store can be rebuilt from the log.

| Package | What it is |
|---|---|
| `redstring.composition` | `build_graph` — the only module holding both halves |
| `redstring.domain` | `Entity`, `Relationship`, `Alias`, similarity, temporal parsing |
| `redstring.ports` | `GraphStore`, `VectorStore`, `LlmProvider`, `Cache` |
| `redstring.graph` / `.vector` | Adapters: in-memory, Neo4j, pgvector |
| `redstring.llm` | Provider adapters, retry, rate limiting, circuit breaking, caching |
| `redstring.extraction` | Chunking, the pipeline, mapping, merging, domain prompting |
| `redstring.consolidation` | Deciding two entities are one, and undoing it |
| `redstring.temporal` | Interval inference and time-sliced queries |
| `redstring.aggregates` / `.events` / `.projections` | The write model and the read model |

Implementing a port of your own? `tests/compliance/` is a suite you can point
at it; it is what says whether you got the contract right.

## The public API

`from redstring import ...` — everything in `__all__` is supported. Anything
reached through a dotted path is internal and may change in a patch release.
The module docstring is the reference, including what is deliberately left
out.

The surface is **closed**, which is stronger than "documented": every type
named in an exported signature is either exported too or recorded with the
package it comes from, and every `RedstringError` is either exported or
recorded against the capability whose export would bring it. That is a test
(`tests/unit/test_public_surface_is_self_contained.py`), not an intention —
[ADR 0006](docs/adr/0006-the-public-surface-is-gated.md) records why it is
gated rather than curated.

**Consolidation, temporal inference and the resilience stack are not
exported.** All are real and tested; none has a composed entry point yet, so
exporting them would publish an API whose shape is still being decided by
callers it does not have. Reach them by dotted path
(`redstring.consolidation.service`, `redstring.llm.retry`) and expect
movement — a rename there is not a breaking change, because nothing promised
it.

[Consolidate duplicate entities](https://tyevans.github.io/redstring/how-to/consolidate-duplicate-entities/)
is the end-to-end recipe, from a populated graph through blocking, scoring and
adjudication to a merge you can audit and reverse — including the four
constants that decide how aggressive it is.

## Documentation

| | |
|---|---|
| [Getting started](https://tyevans.github.io/redstring/getting-started/) | One document to a queryable graph, no server needed |
| [How-to guides](https://tyevans.github.io/redstring/how-to/) | Authoring a domain schema, consolidating duplicates, driving and rebuilding projections, querying a timeline, writing a store adapter |
| [Reference](https://tyevans.github.io/redstring/reference/) | The events, aggregates, domain value types, schema YAML, Neo4j store, quality gates |
| [Decisions](https://tyevans.github.io/redstring/adr/) | Fourteen ADRs, each with the alternative that was rejected |
| [Contributing](https://tyevans.github.io/redstring/contributing/) | Setup, the commit gate, and how tests are organised |

## Development

```bash
uv sync --all-extras && uv run pre-commit install
```

**`--all-extras`, not `--extra dev`** — a venv without `neo4j` and `llm` fails
*collection* rather than skipping those tests, and that mistake presents as
something else entirely (a mutation run reporting "0 survivors out of 426";
47 phantom mypy errors in untouched files).

Every quality gate — ruff, `mypy --strict`, bandit, the layered import
contract, and pytest under a coverage ratchet — runs on `git commit`. **There
is no separate step to run**, and running one by hand duplicates work the hook
already does.

The integration and mutation suites are deliberately outside the gate:

```bash
docker compose -f docker-compose.test.yml up -d      # Neo4j on 7688, pgvector on 5434
KG_COMPLIANCE_MAX_EXAMPLES=10 uv run pytest -m integration    # serial; no -n auto
```

[Run the integration and mutation suites](https://tyevans.github.io/redstring/how-to/run-integration-and-mutation-suites/)
is the full runbook: which test needs which backend, the four environment
variables for pointing at your own, and the two invocation constraints that
otherwise produce dozens of failures reading as flakiness.

**There is no accuracy suite.** `tests/accuracy/` is an empty package and
`-m accuracy` collects zero tests, so no claim about extraction *quality* is
backed by anything here — correct and accurate are different properties, and a
pipeline can satisfy every invariant while finding the wrong entities. Tracked
as `BACKLOG.md` B12.

## Further reading

`BACKLOG.md` is the index of everything known and not fixed, grouped by what a
reader would search for. Its `B` numbers are stable handles cited from `src/`
and `tests/`, so a comment naming `B10f` resolves to a real entry. Anything
deferred lands there in the same commit that passes it by — this project's
hardest rule, and the one with no exceptions.

`docs/plans/ring-migration.md` is the history: what this library was before it
became a library, which commit range rebuilt it, and where the deleted parts
can be recovered from. It also indexes the closed backlog entries that shipped
code still cites, so those pointers keep resolving.

`CHANGELOG.md`, `RELEASING.md` and `SECURITY.md` cover releases — including
why there is no PyPI API token anywhere in this repository.

## Licence

MIT. See [LICENSE](LICENSE).
