# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**What "breaking" means here is narrower than it looks.** The public API is
`redstring.__all__` and nothing else — anything reached through a dotted path
(`redstring.consolidation.service`, `redstring.llm.retry`) is internal, and a
rename or signature change there is not a breaking change and will not appear
under **Removed** or **Changed**. See
[ADR 0006](https://github.com/tyevans/redstring/blob/main/docs/adr/0006-the-public-surface-is-gated.md).

## [Unreleased]

## [0.2.0] - 2026-08-05

### Added

- **`EmbeddingProvider`, and a `VectorStore` you can actually fill.**
  `VectorStore` shipped in `0.1.0` with two adapters and no way for the library
  to put a vector in it — every write path took vectors you had computed
  elsewhere. `build_graph` now takes an `embedding_provider` and a
  `vector_store` together and populates both stores:

  ```python
  await build_graph(
      document,
      provider=llm,
      store=graph,
      tenant_id=tenant_id,
      embedding_provider=embedder,
      vector_store=vectors,
  )
  ```

  Exported: `EmbeddingProvider` (the port), `FakeEmbeddingProvider`
  (deterministic, no model needed — for your tests as much as ours), and
  `EmbeddingProviderError`.
  `redstring.llm.adapters.langchain_embedding.LangChainEmbeddingProvider`
  speaks to any OpenAI-compatible embeddings endpoint and is reached by path,
  so `import redstring` still does not pull LangChain in.

  Vectors reach the store through an `EntitiesEmbedded` event and
  `VectorProjection`, so a vector store stays rebuildable by replay. See
  [ADR 0017](https://github.com/tyevans/redstring/blob/main/docs/adr/0017-the-embedding-provider-port.md).

- **Dimensions are checked where the mistake is.** A provider declares the
  width it produces and a store declares what it holds; `build_graph` refuses a
  mismatched pair before anything is embedded, and refuses one of the two
  without the other. Previously the first sign of either was a database
  complaining about a column type after you had paid for the embedding calls.

- **An accuracy suite.** `-m accuracy` measures precision, recall and F1 over a
  graded corpus. Five hand-graded documents — enough to catch a regression, not
  a benchmark, and it says so.

### Fixed

- **`eventsource-py` floor raised to `>=0.10.0`.** `0.1.0` declared `>=0.9.1`
  while `redstring.projections` forwards `retry_policy` and `tracer`, which
  `DeclarativeProjection.__init__` gained in 0.10.0. A resolver picking the low
  end raised `TypeError: unexpected keyword argument 'retry_policy'` when
  constructing a projection — **not at import**, so `import redstring`
  succeeded and the error surfaced in your code with no obvious link to a
  dependency bound. If you pinned `eventsource-py==0.9.1` alongside
  `redstring==0.1.0`, upgrading resolves it.

### Notes

- **Embedding vectors are reproducible in direction, not bit-for-bit.** The
  same text embeds to the same vector in the sense that matters — cosine above
  0.99 — and not an identical one, because floating-point accumulation depends
  on how a batch was packed. Do not compare vectors with `==`, and do not hash
  one as an identity. Measured at up to `4e-3` per component against llama.cpp;
  the compliance suite states the contract this way because an earlier version
  asserted equality and no real backend could satisfy it.
- Entity **names** are embedded, whole. There is no chunk-level or
  document-level embedding yet, and a merged entity keeps its pre-merge vector
  until something re-embeds it.
- No breaking changes. Everything above is additive.

## [0.1.0a1] - 2026-08-04

**A rehearsal of the release pipeline, published to TestPyPI as
`redstring-test`. Not a release, and not on PyPI.**

The library is identical to `0.1.0` below; this version exists so that the
tagging, building, publishing and post-publish verification steps run once
against a real index on a version nobody minds burning. PyPI never permits
reusing a filename, so the first execution of that path is also irreversible —
which is a poor combination with never having executed it.

Install it, if you want to look at it, with both indexes — TestPyPI does not
mirror PyPI, so resolving `pydantic` needs the real one:

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ \
            redstring-test==0.1.0a1
```

The import name is `redstring` on both indexes; only the distribution is
renamed, because `redstring` on TestPyPI belongs to an unrelated project.

## [0.1.0] - 2026-08-04

First release.

### Added

- **Extraction.** `ExtractionPipeline` and `build_graph`: chunk a
  `SourceDocument`, extract entities and relationships through the
  `LlmProvider` port, merge across chunks, and emit a `DocumentExtracted`
  event. Six bundled domain schemas, selectable by name or by classifier
  (`AUTO`).
- **Two store ports with two implementations each.** `GraphStore`
  (`InMemoryGraphStore`, `Neo4jGraphStore`) and `VectorStore`
  (`InMemoryVectorStore`, `PgVectorStore`), both held to a shared compliance
  suite that every adapter runs unchanged.
- **An event-sourced write model.** `DocumentExtracted`, `EntitiesMerged` and
  `MergeUndone`, with `GraphProjection` and `VectorProjection` folding them
  into the stores. Extraction and consolidation both emit and write to no
  store, so a store can be rebuilt by replay.
- **Consolidation.** `Consolidator` — blocking, scoring, banding and model
  adjudication behind `resolve()`, an explicit `merge()`, and an `undo()` that
  takes only the merge's event id and reads what to restore from the log.
  Every change reaches the graph through a projection, never a direct write.
  With no `event_store` argument the merge history is in-memory, so undo is
  session-only; `remembers_merges_across_restarts` reports which arrangement
  is in use. See ADR 0015.
- **Temporal inference.** Interval relations computed on read from
  `TemporalExtent`, never persisted into the event log. Not exported yet.
- **Resilience over the `Cache` port.** Retry with jitter, rate limiting and
  circuit breaking, with in-memory and Redis cache adapters. Not exported yet.
- **Multi-tenancy throughout.** Every store call takes a `tenant_id`, and
  every compliance suite asserts reads never cross tenants.
- **A gated public surface.** `__all__` is the whole promise, held by three
  tests: exported signatures name only exported types, every `RedstringError`
  is exported or recorded, and the end-to-end example imports nothing but
  `redstring`.
- `py.typed`, so downstream type checkers see the annotations.
- Documentation at <https://tyevans.github.io/redstring>, including the
  architecture decision records.

### Notes

- Requires Python 3.13+.
- **Every backend is an extra.** The base install is `pydantic`,
  `eventsource-py` and four small pure-Python libraries — no database driver,
  no Redis client, no compiled numerical package. `neo4j`, `pgvector`,
  `redis` and `llm` each pull exactly what their adapter needs, and reaching
  an adapter without its extra raises an `ImportError` naming the extra.
- `eventsource-py` is the one **core** dependency that is not pure
  configuration, and that is deliberate: `redstring.__init__` exports types
  that need it, and a public API that fails to import without an extra is not
  a public API.
- The library **never fetches content**, and extraction **writes to no store**.
  Both are architectural commitments rather than gaps.
- There is no accuracy suite. `tests/accuracy/` is empty, so no claim about
  extraction *quality* is backed by anything in this repository — correct and
  accurate are different properties (`BACKLOG.md` B12).

[Unreleased]: https://github.com/tyevans/redstring/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/tyevans/redstring/releases/tag/v0.2.0
[0.1.0]: https://github.com/tyevans/redstring/releases/tag/v0.1.0
[0.1.0a1]: https://github.com/tyevans/redstring/releases/tag/v0.1.0a1
