# ADR-0148: Entity Retraction on Re-extraction without delete_entity

## Status
Proposed

## Context
Exploratory architectural spike SPIKE-0020 investigated the hypothesis:
"How to define entity identity across extraction runs and retract stale entities without violating GraphStore port invariants?".

Empirical benchmark findings from SPIKE-0020:
- 100 iterations completed in 27.92ms (p95 latency 0.28ms). Alias resolution and replay equivalence preserved.
- Entity identity is deterministically scoped to document, entity_type, and normalized name. Document aggregate computes diff retractions across model versions. GraphStore invariants (ADR-0102) are preserved by tombstoning entity properties and deleting incident relationships via delete_relationship without hard deletion.

## Decision
We adopt Entity Retraction on Re-extraction without delete_entity based on empirical benchmark findings from SPIKE-0020:
- Findings: 100 iterations completed in 27.92ms (p95 latency 0.28ms). Alias resolution and replay equivalence preserved..

## Consequences
- **Positive**: Validated by empirical benchmark findings from SPIKE-0020 (100 iterations completed in 27.92ms (p95 latency 0.28ms). Alias resolution and replay equivalence preserved.).
- **Negative**: Incurs implementation and ongoing maintenance responsibilities.
