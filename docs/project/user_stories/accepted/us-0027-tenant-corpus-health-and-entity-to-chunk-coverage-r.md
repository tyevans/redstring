---
id: '0027'
title: "Tenant Corpus Health and Entity-to-Chunk Coverage Reporting"
status: Accepted
created: 2026-10-16
persona: "Liam"
target_bc: "composition"
feature: "FEAT-COMP-27"
governing_prd: "PRD-0011"
release: "v0.14.0"
scenarios:
  - "Compute corpus health report across mixed extracted and indexed documents"
  - "Analyze an empty tenant corpus returns zeroed metrics without division-by-zero errors"
---

# US-0027 — Tenant Corpus Health and Entity-to-Chunk Coverage Reporting

## Governing PRD
- [`PRD-0011: Reverse Provenance Lineage Tracing and Corpus Coverage Reporting`](../../product/accepted/prd-0011-reverse-provenance-lineage-tracing-and-corpus-cover.md)

## Governing ADRs
- [`ADR-0149: Unified Redstring Client Facade and In-Memory Indexing Architecture`](../../adrs/accepted/adr-0149-unified-redstring-client-facade-and-in-memory-inde.md)
- [`ADR-0006: Executable BDD User Stories`](../../adrs/accepted/adr-0006-executable-bdd-user-stories.md)

## User Story

**As a** Liam (The Provenance Lineage & Corpus Quality Auditor),
**I want** `analyze_corpus` and `Redstring.analyze_corpus` to return a `CorpusHealthReport` summarizing total entities, relationships, chunks, linked vs. unlinked passages, embedding coverage, character length statistics, and entity-to-chunk coverage ratio,
**So that** I can detect unextracted documents, unembedded passages, and entity citation coverage gaps across a tenant corpus.

## Acceptance Criteria

```gherkin
Scenario: Compute corpus health report across mixed extracted and indexed documents
  Given a tenant with 1 extracted document (producing entities, relationships, and embedded linked chunks)
  And 1 indexed document via index_documents without embeddings (producing unlinked, unembedded chunks)
  When I call client.analyze_corpus including the indexed SourceId
  Then a CorpusHealthReport is returned with unique_source_count 2
  And report.linked_chunks + report.unlinked_chunks == report.total_chunks
  And report.embedded_chunks + report.unembedded_chunks == report.total_chunks
  And report.min_chunk_chars <= report.mean_chunk_chars <= report.max_chunk_chars
  And report.entity_chunk_coverage_ratio is between 0.0 and 1.0.
```

```gherkin
Scenario: Analyze an empty tenant corpus returns zeroed metrics without division-by-zero errors
  Given an empty tenant with no entities, relationships, or chunks
  When I call analyze_corpus for that tenant
  Then a CorpusHealthReport is returned with total_entities 0, total_chunks 0, mean_chunk_chars 0.0, and entity_chunk_coverage_ratio 0.0.
```
