---
id: 0058
title: Implement explainable hybrid retrieval diagnostics (explain_entity_retrieval,
  explain_chunk_retrieval)
status: Complete
governing_adrs:
- ADR-0142
- ADR-0149
- ADR-0002
- ADR-0003
- ADR-0006
- ADR-0009
governing_prds:
- PRD-0010
governing_stories:
- US-0024
- US-0025
target_bc: composition
---

# TASK-0058: Implement explainable hybrid retrieval diagnostics (explain_entity_retrieval, explain_chunk_retrieval)

## Summary
Implement `ExplainedEntityMatch`, `EntityRetrievalExplanation`, `ExplainedChunkMatch`, `ChunkRetrievalExplanation`, `explain_entity_retrieval`, and `explain_chunk_retrieval` in `src/redstring/composition/retrieval_diagnostics.py` and expose `Redstring.explain_entity_retrieval` and `Redstring.explain_passage_retrieval`.

## Definition of Done (Blackbox Frontdoor TDD)
1. Public interfaces implemented in `src/redstring/composition/retrieval_diagnostics.py` and exported in `redstring.__all__`.
2. Verified via executable BDD scenarios (`US-0024`, `US-0025`) and Hypothesis RRF/BM25 conservation property tests.
3. All modified and created source files strictly under 400 lines.
