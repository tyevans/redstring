---
id: 0059
title: Implement reverse provenance lineage (trace_source_lineage, trace_entity_lineage)
  and corpus health reporting (analyze_corpus)
status: Complete
governing_adrs:
- ADR-0135
- ADR-0149
- ADR-0002
- ADR-0003
- ADR-0006
- ADR-0009
governing_prds:
- PRD-0011
governing_stories:
- US-0026
- US-0027
target_bc: composition
---

# TASK-0059: Implement reverse provenance lineage (trace_source_lineage, trace_entity_lineage) and corpus health reporting (analyze_corpus)

## Summary
Implement `SourceLineageReport`, `EntityLineageReport`, `CorpusHealthReport`, `trace_source_lineage`, `trace_entity_lineage`, and `analyze_corpus` in `src/redstring/composition/lineage.py` and expose `Redstring.trace_source_lineage`, `Redstring.trace_entity_lineage`, and `Redstring.analyze_corpus`.

## Definition of Done (Blackbox Frontdoor TDD)
1. Public interfaces implemented in `src/redstring/composition/lineage.py` and exported in `redstring.__all__`.
2. Verified via executable BDD scenarios (`US-0026`, `US-0027`) and Hypothesis partition conservation property tests.
3. All modified and created source files strictly under 400 lines.
