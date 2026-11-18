---
id: '0056'
title: Implement portable graph import parsers (parse_json_ld, parse_graphml, import_graph)
  and filtered subgraph export
status: Complete
governing_adrs:
- ADR-0149
- ADR-0002
- ADR-0003
- ADR-0006
- ADR-0009
governing_prds:
- PRD-0008
governing_stories:
- US-0020
- US-0021
target_bc: composition
---

# TASK-0056: Implement portable graph import parsers (parse_json_ld, parse_graphml, import_graph) and filtered subgraph export

## Summary
Implement `GraphImportReport`, `parse_json_ld`, `parse_graphml`, and `import_graph` in `src/redstring/composition/import_graph.py`, enhance `export_graph` in `src/redstring/composition/export.py` with `entity_types`, `relationship_types`, `center_entity_id`, and `max_depth` subgraph filtering, and wire `Redstring.import_graph` and `Redstring.export_graph`.

## Definition of Done (Blackbox Frontdoor TDD)
1. Public interfaces `GraphImportReport`, `parse_json_ld`, `parse_graphml`, `import_graph`, and filtered `export_graph` implemented and exported in `redstring.__all__`.
2. Verified via executable BDD scenarios (`US-0020`, `US-0021`) and Hypothesis round-trip property tests.
3. All modified and created source files strictly under 400 lines.
