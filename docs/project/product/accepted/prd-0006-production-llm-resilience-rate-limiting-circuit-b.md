---
id: '0006'
title: Production LLM Resilience, Rate Limiting, Circuit Breaking, and Governance
status: Accepted
created: 2026-10-16
target_persona: Marcus
component: llm
---

# PRD-0006 — Production LLM Resilience, Rate Limiting, Circuit Breaking, and Governance

## Who this is for

- **Marcus (The Production AI & LLM Reliability Engineer)**: Needs sliding-window per-tenant rate limiting, three-state circuit breaking, jittered exponential retry policies, and shared concurrency ceilings (`CallLimiter`) to keep production LLM pipelines stable under load and upstream outages.
- **Nadia (The Compliance & Multi-Tenant Governance Officer)**: Needs non-destructive event-sourced merge undo (`Consolidator.undo`), tenant-scoped projection rebuilds (`rebuild_tenant`), right-to-be-forgotten tenant purges (`delete_by_tenant`), and strict vector/entity provenance validation.

## What the person cannot do today

- **Burst Overload & Cascading Outages**: Fixed-window rate counters allow double-limit bursts across minute boundaries, and unhandled provider outages cause worker fleets to hammer failing endpoints instead of failing fast with `CircuitOpen`.
- **Shared Cache Lifecycle Corruption**: When a `RateLimiter` and `CircuitBreaker` share a single `RedisCache` or `MemoryCache`, closing one component must not close the caller-owned cache out from under the other.
- **Irreversible Entity Merges & Tenant Residue**: Automated consolidation mistakes cannot be safely reversed unless merge history is event-sourced, and compliance officers need verifiable single-tenant erasure (`delete_by_tenant`) and isolated projection replay (`rebuild_tenant`) without affecting co-hosted tenants.

## What good looks like

1. **Sliding-Window Rate Limiting & Three-State Circuit Breaking**:
   - `RateLimiter(rpm=..., window_seconds=..., cache=...)` enforces a per-tenant sliding window over the `HitWindow` cache port, raising `RateLimitExceeded` with an accurate `retry_after` duration when a tenant exhausts its quota.
   - `CircuitBreaker(failure_threshold=..., recovery_timeout=..., half_open_max_calls=...)` transitions deterministically across `CLOSED` -> `OPEN` -> `HALF_OPEN` -> `CLOSED`, rejecting calls with `CircuitOpen` while open and limiting concurrent recovery probes while half-open.
   - `with_retry` and `LlmRetryPolicy` execute exponential backoff with jitter for caller-specified retryable exceptions and raise `RetryExhausted` chaining the root cause when attempts run out.

2. **Event-Sourced Merge Reversal (`Consolidator.undo`)**:
   - `Consolidator.merge` and `Consolidator.resolve` emit `EntitiesMerged` events recorded in the consolidation log.
   - Calling `Consolidator.undo(tenant_id=..., merge_event_id=...)` emits `MergeUndone`, restores absorbed entities, removes aliases, and reverts redirected relationships back to their pre-merge endpoints.

3. **Multi-Tenant Purge, Scoped Rebuild, and Vector Provenance**:
   - `delete_by_tenant(tenant_id)` across `GraphStore`, `VectorStore`, and `ChunkStore` completely erases all records for the target tenant while leaving other tenants untouched.
   - `rebuild_tenant` wipes a single tenant's read projections and replays only that tenant's events from the feed.
   - `VectorProjection` and `Retriever` validate `VectorProvenance` and task prefixes, raising `VectorProvenanceMismatchError` or `TaskPrefixMismatchError` on model drift.

## What this does not do

- **Close Caller-Owned Caches or Drivers**: Components handed an external `Cache` or database driver never close it on exit (ADR-0128).
- **Cross-Tenant Rate Limit Bleeding or Erasure**: Rate limit windows, circuit keys, purges, and merge undos are strictly partitioned by `TenantId` or `key_prefix`.

## Checkable Outcomes

1. Acquiring slots via `RateLimiter.acquire(tenant_id)` allows `rpm` calls within `window_seconds` and raises `RateLimitExceeded` with `retry_after > 0` on the `(rpm + 1)`-th call without affecting other tenants.
2. Recording `failure_threshold` failures on `CircuitBreaker` transitions state to `CircuitState.OPEN`, refuses subsequent calls with `CircuitOpen`, probes via `CircuitState.HALF_OPEN` after `recovery_timeout`, and resets to `CircuitState.CLOSED` on probe success.
3. Executing `Consolidator.undo(tenant_id=..., merge_event_id=...)` reverses a prior `Consolidator.merge`, restoring the absorbed entity and original relationship endpoints in `GraphStore`.
4. Executing `delete_by_tenant` and `rebuild_tenant` on a multi-tenant store cleanly wipes and reconstructs the target tenant's state with zero cross-tenant mutation or leakage.

## Linked User Stories

- [`US-0016`](../../user_stories/accepted/us-0016-llm-transport-resilience-rate-limiting-and-circuit.md): LLM Transport Resilience, Rate Limiting, and Circuit Breaking
- [`US-0017`](../../user_stories/accepted/us-0017-reversible-entity-consolidation-and-multi-tenant-g.md): Reversible Entity Consolidation and Multi-Tenant Governance

## Implementing Backlog Tasks

- [`TASK-0004`](../../backlog/complete/0004-support-tenant-scoped-projection-rebuild.md): Support tenant-scoped projection rebuild
- [`TASK-0007`](../../backlog/complete/0007-detect-corpus-embedded-under-mismatched-task-pref.md): Detect corpus embedded under mismatched task prefix
- [`TASK-0030`](../../backlog/complete/0030-standardize-extraction-retry-policy-naming.md): Standardize extraction retry policy naming
