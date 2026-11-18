# Delivery Roadmap

## Milestone 1: Foundations (`v0.1.0` – `v0.12.0`)
- Event-sourced knowledge graph extraction, projections (`GraphStore`, `VectorStore`, `ChunkStore`), hybrid retrieval, entity consolidation, temporal queries, and LLM transport resilience (`PRD-0001`, `PRD-0002`, `PRD-0004`–`PRD-0006`).

## Milestone 2: Unified Client, High-Performance Indexing & `eventsource-py 0.15.0` (`v0.13.0` — Shipped)
- Unified `Redstring` client facade, input coercion, concurrent batch ingestion, and `eventsource-py 0.15.0` adoption (`PRD-0003`, `US-0011`, `US-0012`, `TASK-0042`–`TASK-0052`).

## Milestone 3: Portable Graph Interoperability, Explainable Diagnostics, Lineage & Governance (`v0.14.0` — Shipped)
- **Wave 0 (`PRD-0007`, `US-0018`, `US-0019`)**: Portable knowledge graph export (`export_graph` in Mermaid, JSON-LD, GraphML), explainable shortest paths (`explain_path`), and streaming batch ingestion (`build_graph_stream`) (`TASK-0053`–`TASK-0055`).
- **Wave 1 (`PRD-0008`, `US-0020`, `US-0021`)**: Symmetric portable graph import (`import_graph`, `parse_json_ld`, `parse_graphml`) and filtered/ego-network subgraph visualization (`TASK-0056`).
- **Wave 2 (`PRD-0009`, `US-0022`, `US-0023`)**: Structural topology diagnostics (`analyze_topology`, `EntityHub`) and snapshot diffing (`diff_graphs`) (`TASK-0057`).
- **Wave 3 (`PRD-0010`, `US-0024`, `US-0025`)**: Explainable hybrid entity and passage retrieval diagnostics (`explain_entity_retrieval`, `explain_chunk_retrieval`) (`TASK-0058`).
- **Wave 4 (`PRD-0011`, `US-0026`, `US-0027`)**: Reverse provenance lineage (`trace_source_lineage`, `trace_entity_lineage`) and corpus health reporting (`analyze_corpus`) (`TASK-0059`).
- **Wave 5 (`PRD-0012`, `US-0028`, `US-0029`)**: Non-destructive consolidation preview (`preview_consolidation`), tenant-wide consolidation sweep (`consolidate_tenant`), post-extraction schema conformance auditing (`audit_schema_conformance`), and ontology Mermaid visualization (`format_schema_mermaid`) (`TASK-0060`).

## Milestone 4: Security Hardening & `eventsource-py 0.16.0` (Unreleased)
- **`PRD-0013`, `US-0030`, `ADR-0150`**: Every dependency is current, including `eventsource-py 0.16.0`, and `uv.lock` is committed. The work and the text that untrusted documents and LLM output can cause are bounded: linear sanitisation, call timeouts, inert LLM output, safe GraphML parsing, capped traversal and work parameters, and validated SQL identifiers. The CI supply chain is pinned, and secret scanning runs in CI (`TASK-0061`–`TASK-0067`).
