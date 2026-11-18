---
id: '0063'
title: Harden portable graph import and export against untrusted payloads
status: Complete
governing_adrs:
- ADR-0107
governing_prds:
- PRD-0013
governing_stories:
- US-0030
target_bc: composition
---

# TASK-0063: Harden portable graph import and export against untrusted payloads

## Summary
Parse GraphML as safe XML, bound JSON-LD import, and make the GraphML and Mermaid exporters produce output that cannot carry markup.

## Problem Statement & Context
Found by the 2026-11-17 security sweep (parsing review).

- `src/redstring/composition/import_graph.py:200-267` parses GraphML with `html.parser.HTMLParser`. It is not an XML parser: `<data>A<b>B</b>C</data>` imports as `ABC`, CDATA text is dropped, and an unclosed `<node>` is replaced by the next one with no error.
- `import_graph.py:112-113` calls `json.loads` with no size limit and catches only `JSONDecodeError`. Input nested 100 000 levels raises `RecursionError`, which is not the documented `ValueError`.
- `composition/export.py:201-219` GraphML export escapes `& < > " '` only. A name with `\x01` gives a file that XML parsers reject.
- `export.py:55-57` Mermaid label escaping does not handle `<`, `>`, `&` or `#`. A viewer with a permissive `securityLevel` renders an entity name as HTML.
- Provenance on import is kept as supplied (`import_graph.py:60-87`), and an import with an existing id replaces that entity in the target tenant. Both are required by the PRD-0008 lossless round trip; the docs must state that an import file is trusted for provenance and identity.

## Definition of Done (Blackbox Frontdoor TDD)
1. GraphML import uses `defusedxml`. A payload with `<!DOCTYPE` or `<!ENTITY` raises `ValueError`; CDATA text and nested markup are kept as text; an unclosed element raises `ValueError`.
2. JSON-LD and GraphML payloads above a documented size raise `ValueError`; JSON nested 100 000 levels raises `ValueError`.
3. GraphML export of a name with an XML 1.0 forbidden character raises `ValueError` that names the entity; every exported file parses with `xml.etree`.
4. Mermaid labels encode `<`, `>`, `&`, `#` and `"` as Mermaid entity codes (test with `<img src=x onerror=1>`).
5. The import docstring and how-to state the provenance and overwrite trust rule.
6. Existing round-trip tests stay green.
