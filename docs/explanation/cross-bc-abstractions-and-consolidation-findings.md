# Cross-Bounded Context Abstractions and Consolidation Opportunities

This document records the empirical findings of the cross-bounded context architectural spike (**TASK-0019**), evaluating structural redundancies, candidate shared abstractions, and the architectural boundaries governing them in `redstring`.

---

## Executive Summary

An audit across all bounded contexts (`extraction`, `consolidation`, `projections`, `aggregates`, `events`, `ports`, `domain`, `composition`, `storage/adapters`) evaluated four primary candidate areas for consolidation:

1. **Batching and Slicing Primitives**: Ad-hoc `_batches` generator helpers.
2. **Concurrency Retry & Re-planning Logic**: Domain state collision retries vs transport-level transient retries.
3. **Storage Connection & Transaction Boilerplate**: Asyncpg and driver patterns across sibling adapters (`chunks`, `vector`, `graph`).
4. **Rate Limiting & Concurrency Control**: Domain concurrency semaphores vs temporal token/sliding-window limiters.

The findings establish that while utility batching is ripe for standardization via Python stdlib primitives, several apparent duplications across adapters and retry policies are deliberate architectural boundaries enforcing Layered Port & Adapter Isolation (ADR-0007).

---

## 1. Batching & Slicing Primitives

### Analysis
Prior to this audit, multiple bounded contexts implemented local, ad-hoc batching generators:
- `redstring.extraction.pipeline._batches`: Slicing chunks into concurrency wavefronts.
- `redstring.consolidation.resolve_many._batches`: Slicing subjects into parallel score-and-band batches.
- `redstring.consolidation.policy.adjudicate` / `adjudicate_many`: Manual `range(0, len(...), batch_size)` slicing loops.

### Finding & Resolution
`redstring` requires Python `>=3.13`. Python 3.12 introduced `itertools.batched(iterable, n)`, which natively provides lazy, robust, type-safe batch tuple slicing with zero memory overhead and no custom maintenance burden.

In **TASK-0019**, all ad-hoc `_batches` helper functions were eliminated in favor of stdlib `itertools.batched`, simultaneously reducing line count in `src/redstring/extraction/pipeline.py` (a grandfathered file with strict line ceiling constraints).

---

## 2. Concurrency Retry & Re-planning Logic

### Analysis
Two major retry patterns exist in the codebase:
- **Domain Optimistic Lock Retry** (`src/redstring/consolidation/service.py:164-210`): Retrying `OptimisticLockError` during aggregate merge commits.
- **LLM Transport Retry** (`src/redstring/llm/retry.py`): Exponential backoff with jitter for network/transport faults (`ConnectionError`, `TimeoutError`, HTTP 429/503).

### Finding
These two retry mechanisms serve fundamentally divergent concerns in Domain-Driven Design (ADR-0007):
- **Domain Re-planning**: When `OptimisticLockError` occurs, an entity stream was modified concurrently. Retrying cannot simply re-post the same command; it must re-read the updated event streams, re-evaluate invariant rules, and determine if candidates remain mergeable. This logic belongs strictly within the consolidation aggregate write model.
- **Transport Backoff**: Transient network and HTTP errors require blind exponential backoff without domain state re-reading.

**Recommendation**: Retain the strict separation between domain state re-planning and transport backoff. However, the class `ExtractionRetryPolicy` in `src/redstring/llm/retry.py` is misnamed for historic reasons (as it is used across both extraction and consolidation LLM calls) and should be renamed to `LlmRetryPolicy` in a future cleanup task.

---

## 3. Storage Connection & Transaction Boilerplate

### Analysis
Both `src/redstring/chunks/adapters/postgres.py` (`PostgresChunkStore`) and `src/redstring/vector/adapters/pgvector.py` (`PgVectorStore`) interact with PostgreSQL using `asyncpg`. Both implement connection acquisition, schema initialization, and transactional queries. Similarly, vector formatting routines (e.g. converting Python floats to pgvector string literals `"[0.1,0.2,...]"`) appear in both vector and chunk adapters.

### Finding
In `pyproject.toml`, the architectural layering rules (`[tool.importlinter]`) define:
```toml
extraction : consolidation : temporal : chunks : graph : vector : llm
```
Sibling packages on this tier are explicitly forbidden from importing one another.
- Creating a shared `redstring.storage.common` or `redstring.adapters.postgres` package to deduplicate ~15 lines of connection pooling would introduce an unnecessary horizontal shared dependency, coupling chunk storage (document ingestion) directly to vector index implementation details.
- Parity tests (`tests/unit/chunks/test_postgres_schema.py`) already verify structural alignment across these stores via blackbox frontdoors.

**Recommendation**: Maintain the sibling import isolation. The minor duplication of connection setup and vector formatting is healthy decoupling under ADR-0007.

---

## 4. Rate Limiting & Concurrency Control

### Analysis
The codebase contains two limiting primitives:
- `CallLimiter` (`src/redstring/domain/limiter.py`): Pure concurrency throttle using an `asyncio.Semaphore`.
- `RateLimiter` (`src/redstring/llm/rate_limiter.py`): Temporal sliding-window request/token quota tracker backed by cache timestamps (`HitWindow`).

### Finding
- `CallLimiter` enforces a hard ceiling on simultaneously active asynchronous tasks (e.g. max 4 in-flight model calls or graph queries). Placed in `redstring.domain.limiter`, it allows sibling domains (`extraction` and `consolidation`) to share a global concurrency governor without importing each other or reaching into infrastructure.
- `RateLimiter` enforces rate quotas (e.g. 500 requests per minute) across a sliding temporal window.

These two controls are orthogonal and complementary: a workload can be constrained to at most 5 concurrent requests while simultaneously ensuring it does not exceed 100 requests per minute.

**Recommendation**: Keep both primitives distinct and in their respective layers.

---

## Conclusion & Proposed Actions

1. ✅ **Standardize Batching**: Completed in `TASK-0019` via stdlib `itertools.batched`.
2. 📋 **Rename ExtractionRetryPolicy**: Propose a task to rename `ExtractionRetryPolicy` to `LlmRetryPolicy` in `src/redstring/llm/retry.py`.
3. 🛡️ **Preserve Sibling Boundaries**: Continue enforcing the import-linter prohibition on sibling cross-adapter dependencies.
