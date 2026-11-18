---
id: '0065'
title: Bound caller-supplied work parameters in batch, chunking, gleaning, paging
  and consolidation
status: Complete
governing_adrs:
- ADR-0115
governing_prds:
- PRD-0013
governing_stories:
- US-0030
target_bc: composition
---

# TASK-0065: Bound caller-supplied work parameters in batch, chunking, gleaning, paging and consolidation

## Summary
Put documented upper limits on caller parameters that multiply work, and make batch ingestion share one LLM call limiter and cancel siblings on fail-fast.

## Problem Statement & Context
Found by the 2026-11-17 security sweep (resource-exhaustion and tenant-isolation reviews).

- `src/redstring/composition/batch.py:73,154,202`: `max_concurrency` has no upper limit; each `build_graph` call makes its own `CallLimiter`, so in-flight LLM calls are `max_concurrency * concurrency`; with `continue_on_error=False` the first exception propagates but sibling tasks keep running.
- `src/redstring/extraction/chunkers/sliding_window_chunker.py:74,127-132`: `min_chunk_size` is enforced only in `__init__`; `chunk()` accepts any `max_chunk_size` and `overlap_size`. Measured: 100 000 characters at size 200 and overlap 199 give 99 801 chunks.
- `src/redstring/extraction/pipeline.py:391-392`: `gleanings` checks only `>= 0`.
- `page_size` in `themes/topology.py`, `consolidation_sweep.py`, `lineage.py` and `export.py` checks only `>= 1`.
- Consolidation: `domain/blocking.py:134-138` includes an `ENTITY_TYPE` key, so one block holds every entity of a type; `consolidation/candidates.py:150-170` scores the whole block with two store reads per candidate.
- `src/redstring/composition/client.py:195-210`: the per-call `tenant_id` override is a trusted-caller parameter, but the docstring does not say so.

## Definition of Done (Blackbox Frontdoor TDD)
1. Each listed parameter has a named maximum; the maximum is accepted and maximum + 1 is refused (one test per parameter). The refusal is `ValueError`, except in the chunkers, which keep their existing `ChunkSizeError` contract.
2. `chunk()` applies the same validation as `__init__`, and `overlap_size` must be less than `max_chunk_size`.
3. Batch in-flight provider calls are at most `max_concurrency * concurrency`, both factors are capped at `MAX_CONCURRENCY`, and the docstrings state the product (test counts peak concurrent calls and reaches the bound exactly). On fail-fast the remaining documents are cancelled (test counts calls after the first failure).

   A shared batch limiter of size `concurrency` was implemented first and then removed. With the defaults (`max_concurrency=4`, `concurrency=1`) it cut a default batch from 4 parallel calls to 1. The defect was an undocumented, uncapped product, not the product itself.
4. Consolidation scores at most a documented number of candidates per subject, chosen deterministically; the cap is a parameter. The block read before the cut stays unbounded: BACKLOG B167.
5. The `Redstring` client docstring states that `tenant_id=` is a trusted-caller parameter and that redstring does no authorisation.
