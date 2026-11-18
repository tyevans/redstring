---
id: '0016'
title: "LLM Transport Resilience, Rate Limiting, and Circuit Breaking"
status: Accepted
created: 2026-10-16
persona: "Marcus"
target_bc: "llm"
feature: "FEAT-LLM-16"
governing_prd: "PRD-0006"
scenarios:
  - "Enforce per-tenant sliding-window rate limits without cross-tenant interference"
  - "Trip circuit breaker on consecutive failures and recover through half-open probe"
  - "Retry transient transport failures with exponential backoff and chain cause on exhaustion"
  - "Preserve caller-owned cache lifecycle when shared across limiter and breaker"
---

# US-0016 — LLM Transport Resilience, Rate Limiting, and Circuit Breaking

## Governing PRD
- [`PRD-0006: Production LLM Resilience, Rate Limiting, Circuit Breaking, and Governance`](../../product/accepted/prd-0006-production-llm-resilience-rate-limiting-circuit-b.md)

## Governing ADRs
- [`ADR-0126: Chunk Store and Cache Are Capabilities Too`](../../adrs/accepted/adr-0126-chunk-store-and-cache-are-capabilities-too.md)
- [`ADR-0128: A Capability Declares Its Own Release`](../../adrs/accepted/adr-0128-a-capability-declares-its-own-release.md)

## User Story

**As a** Marcus (The Production AI & LLM Reliability Engineer),
**I want** sliding-window per-tenant rate limiting (`RateLimiter`), three-state circuit breaking (`CircuitBreaker`), exponential backoff retries (`with_retry`), and caller-owned `Cache` lifecycle guarantees,
**So that** production LLM pipelines degrade gracefully during upstream outages and never overload model servers or corrupt shared cache connections.

## Acceptance Criteria

```gherkin
Scenario: Enforce per-tenant sliding-window rate limits without cross-tenant interference
  Given a RateLimiter configured with rpm 2 and window_seconds 60
  When tenant A acquires 2 slots and attempts a 3rd slot
  Then RateLimitExceeded is raised for tenant A with a positive retry_after duration
  And tenant B can still acquire slots freely.
```

```gherkin
Scenario: Trip circuit breaker on consecutive failures and recover through half-open probe
  Given a CircuitBreaker configured with failure_threshold 2 and half_open_max_calls 1
  When 2 failures are recorded
  Then the breaker transitions to CircuitState.OPEN and refuses calls with CircuitOpen
  And when recovery_timeout elapses it transitions to HALF_OPEN and closes upon a successful probe.
```

```gherkin
Scenario: Retry transient transport failures with exponential backoff and chain cause on exhaustion
  Given an async function decorated with with_retry for ConnectionError and max_retries 2
  When the function fails twice with ConnectionError and succeeds on the 3rd attempt
  Then the successful result is returned
  And if all attempts fail then RetryExhausted is raised with the original ConnectionError as __cause__.
```

```gherkin
Scenario: Preserve caller-owned cache lifecycle when shared across limiter and breaker
  Given a single MemoryCache passed to both a RateLimiter and a CircuitBreaker
  When the RateLimiter exits its async context manager block
  Then the shared MemoryCache remains open and usable by the CircuitBreaker.
```
