---
id: '0068'
title: Mark missing import provenance as imported instead of inventing it
status: Complete
governing_adrs:
- ADR-0151
governing_prds:
- PRD-0013
- PRD-0008
governing_stories:
- US-0030
target_bc: composition
---

# TASK-0068: Mark missing import provenance as imported instead of inventing it

## Summary
Record an import's omitted provenance as `ExtractionMethod.IMPORTED`, `IMPORTED_CONFIDENCE` and the import time, and keep stated provenance as stated.

## Problem Statement & Context
Was BACKLOG B168, found twice by the 2026-11-17 security sweep (the parsing review and the coverage pass).

- `composition/import_graph.py`, `_parse_provenance`: a node with no provenance imported as `MANUAL`, confidence `1.0`, `observed_at=2026-01-01`. Every GraphML entity got it, because GraphML carries no provenance.
- Consolidation's preference order (ADR-0110) reads `confidence`, so such an entity's fields won merges against scored extractions.
- `ExtractionMethod` values are a wire format, so the new value needed ADR-0151.

## Definition of Done (Blackbox Frontdoor TDD)
1. An omitted method, confidence or `observedAt` is each filled on its own with `IMPORTED`, `IMPORTED_CONFIDENCE` (0.5) or the import time (tests use stated confidences 0.25 and 0.75 so stated and default values cannot coincide).
2. A relationship without a confidence gets `IMPORTED_CONFIDENCE`, in JSON-LD and GraphML.
3. `imported_at` is passed through `import_graph`, both parsers and the client facade; a naive value raises `ValueError`.
4. `"imported"` is pinned in the wire-format test, and `import_graph.py` is a reasoned entry on the clock exemption list.
5. Delivered in PR #202 (`a5b7e8a`).
