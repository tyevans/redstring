# Installation

Python **3.13 or newer**. `requires-python` enforces it, so an older
interpreter gets a clear resolution error rather than a runtime one.

## The base install

```bash
uv add redstring
```

```bash
pip install redstring
```

That gives you the whole library **except two adapters**: the in-memory
`GraphStore` and `VectorStore`, `FakeLlmProvider`, the extraction pipeline,
consolidation, temporal inference, the events, the aggregates and both
projections. It is enough to run the entire test suite and every example in
these docs.

## The extras

| Extra | Adds | Install when |
|---|---|---|
| `llm` | `LangChainLlmProvider`, via `langchain-core` and `langchain-openai` | you want a real model rather than `FakeLlmProvider` |
| `neo4j` | `Neo4jGraphStore`, via the `neo4j` driver | you want the graph in Neo4j rather than in memory |
| `pgvector` | `PgVectorStore`, via `asyncpg` | you want vectors in Postgres rather than in memory |
| `redis` | `RedisCache`, via `redis[hiredis]` | you want the model cache shared between processes |
| `all` | all four | — |

```bash
uv add "redstring[llm]"
uv add "redstring[neo4j]"
uv add "redstring[pgvector]"
uv add "redstring[redis]"
uv add "redstring[all]"
```

### One extra covers every model deployment

`langchain-openai` speaks the OpenAI wire protocol, and so does nearly
everything else: llama.cpp, llama-swap, vLLM, Ollama's compatibility shim, and
OpenAI itself. Pointing at a local server is a `base_url`, not a different
adapter:

```python
from langchain_openai import ChatOpenAI
from redstring.llm.adapters.langchain import LangChainLlmProvider

chat_model = ChatOpenAI(
    model="qwen3-30b",
    base_url="http://localhost:8080/v1",
    api_key="-",  # most local servers ignore it, but the client requires one
)
provider = LangChainLlmProvider(chat_model)
```

The dependency ranges are deliberately wide (`langchain-core>=0.3,<2`) rather
than pinned. That is what the `LlmProvider` port is for: a breaking change
upstream touches `llm/adapters/langchain.py` and nothing else.

### Every adapter module imports without its package

The four adapter modules are importable on a base install; only the
constructor that builds its own connection needs the package. So
`redstring/__init__.py` can export the store types without dragging a
Postgres driver in, and a caller who never touches `PgVectorStore.connect`
never needs `asyncpg`.

Reaching one without its extra is an `ImportError` naming the extra, not a
`ModuleNotFoundError` naming a package you did not ask for:

```
PgVectorStore.connect needs asyncpg: install `redstring[pgvector]`
RedisCache.from_url needs redis: install `redstring[redis]`
```

`tests/unit/test_optional_dependency_guards.py` asserts both, by putting
`None` into `sys.modules` — which is the only way to test it, since everyone
working on this repository has every extra installed and would never see the
failure.

### One dependency is core and will stay that way

`eventsource-py`. `redstring.__init__` exports `Document`,
`DocumentExtracted` and both projections, and every one of those needs it —
a public API that fails to import without an extra is not a public API. The
events are the write model, not an optional backend.

The `<0.11` cap is deliberate: that library is pre-1.0 and its entire API
changed between 0.5 and 0.9.

## Verifying the install

```python
import redstring

print(redstring.__version__)
```

If you are type-checking your own code against this package, `py.typed` ships
in the wheel, so mypy and pyright see the annotations with nothing configured.

## For contributors

A working tree needs the dev tooling *and* both optional extras — a venv
without them fails **collection** on the modules that import them rather than
skipping those tests:

```bash
uv sync --all-extras
uv run pre-commit install
```

`--all-extras`, not `--extra dev`. This is worth knowing because it has cost
this project two debugging sessions that presented as something else entirely
— a mutation run reporting "0 survivors out of 426" (every mutant killed by an
import error) and 47 phantom mypy errors in untouched files. Note also that
`uv add` and `uv remove` re-resolve and can silently narrow the venv back to
`dev`, so re-sync with `--all-extras` after any dependency change.

See [Contributing](contributing.md).
