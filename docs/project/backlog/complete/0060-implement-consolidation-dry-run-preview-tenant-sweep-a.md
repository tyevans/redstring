---
id: '0060'
title: Implement consolidation dry-run preview, tenant sweep, schema conformance auditing,
  and ontology Mermaid visualization
status: Complete
governing_adrs:
- ADR-0111
- ADR-0115
- ADR-0149
- ADR-0002
- ADR-0003
- ADR-0006
- ADR-0009
governing_prds:
- PRD-0012
governing_stories:
- US-0028
- US-0029
target_bc: composition
---

# TASK-0060: Implement consolidation dry-run preview, tenant sweep, schema conformance auditing, and ontology Mermaid visualization

## Summary
Implement `PreviewCandidate`, `ConsolidationPreview`, `TenantConsolidationReport`, `preview_consolidation`, and `consolidate_tenant` in `src/redstring/composition/consolidation_sweep.py`, and `SchemaViolation`, `SchemaConformanceReport`, `audit_schema_conformance`, and `format_schema_mermaid` in `src/redstring/composition/schema_audit.py`. Wire facade methods on `Redstring` and update Diataxis documentation.

## Definition of Done (Blackbox Frontdoor TDD)
1. Public interfaces implemented and exported in `redstring.__all__`.
2. Verified via executable BDD scenarios (`US-0028`, `US-0029`) and Hypothesis property tests.
3. All modified and created source files strictly under 400 lines.
