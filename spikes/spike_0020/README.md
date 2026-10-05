# Architectural Spike Harness: SPIKE-0020

## Hypothesis
How to define entity identity across extraction runs and retract stale entities without violating GraphStore port invariants?

## Timebox
2h

## Rules & Guidelines
1. Implement prototype logic strictly within `spikes/spike_0020/`.
2. Modifying files under `src/` violates write isolation and will trigger preflight rejection.
3. Run benchmark tests via `pytest spikes/spike_0020/test_spike.py`.
4. Conclude experiment via `spec-ops spike graduate SPIKE-0020 --result proven|disproven`.
