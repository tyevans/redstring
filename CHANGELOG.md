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
- Documentation at <https://tyevans.github.io/redstring>, including fourteen
  ADRs.

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

[Unreleased]: https://github.com/tyevans/redstring/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tyevans/redstring/releases/tag/v0.1.0
