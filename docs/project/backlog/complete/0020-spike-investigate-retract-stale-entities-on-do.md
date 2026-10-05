---
id: SPIKE-0020
title: 'Architectural Spike: Investigate Retract stale entities on document re-extraction'
status: Graduated
governing_prds:
- PRD-0001
governing_stories:
- US-0001
target_bc: extraction
hypothesis: How to define entity identity across extraction runs and retract stale
  entities without violating GraphStore port invariants?
timebox: 2h
allows_dependencies: true
---

# SPIKE-0020: Architectural Spike: Investigate Retract stale entities on document re-extraction

## Summary & Unanswered Question
How to define entity identity across extraction runs and retract stale entities without violating GraphStore port invariants?

## Timebox & Scope
- **Timebox**: 2h
- **Target Harness**: `spikes/spike_0020/`
- **Governing Task**: TASK-0003

## Graduation Criteria
1. Execute exploratory benchmark or prototype tests in `spikes/spike_0020/`.
2. Run `spec-ops spike check` to verify write isolation and timebox compliance.
3. Run `spec-ops spike graduate SPIKE-0020 --result proven|disproven` to synthesize the ADR.
