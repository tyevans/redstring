---
id: '0004'
title: Domain Ontology Schemas, Auto-Classification, and Multi-Pass Gleaning
status: Accepted
created: 2026-10-16
target_persona: Priya
component: extraction
---

# PRD-0004 — Domain Ontology Schemas, Auto-Classification, and Multi-Pass Gleaning

## Who this is for

- **Priya (The Domain Ontologist & Subject-Matter Expert)**: Needs declarative YAML domain schemas, Schema.org vocabulary mapping, automatic domain classification (`domain=AUTO`), and strict vocabulary enforcement (`constrain_to_domain=True`) without editing core library code.
- **Alex (The Knowledge Graph Architect)**: Needs iterative multi-pass gleaning (`gleanings`) and cross-chunk entity carryover (`carryover_entities`) so dense or long documents do not drop entities across sliding windows.
- **Morgan (The Autonomous Coding Agent)**: Needs deterministic schema validation errors (`SchemaLoadError`, `UnknownDomainError`) and transparent classification confidence metrics (`GraphBuildReport.domain_confidence`).

## What the person cannot do today

- **Unconstrained Vocabulary Drift**: Without domain-specific prompts and schema constraints, LLMs invent arbitrary entity types and relationship predicates that fragment downstream graph queries.
- **Manual Domain Triage on Heterogeneous Corpora**: When ingesting mixed document collections, callers cannot always pre-label every document's domain before calling `build_graph`.
- **Single-Pass Recall Ceiling & Window Amnesia**: A single LLM extraction pass over a dense passage often misses secondary entities, and chunk boundaries cause pronouns or abbreviated references in chunk $N+1$ to disconnect from entities introduced in chunk $N$.

## What good looks like

1. **Declarative Domain Schemas & Prompt Generation**:
   - `load_schema_from_string` and `load_schema_from_file` parse and validate YAML `DomainSchema` definitions (entity types, relationship types, properties, confidence thresholds, and Schema.org mappings).
   - `domain_system_prompt` synthesizes tailored extraction instructions from any registered or custom `DomainSchema`.
   - `constrain_to_domain=True` filters or coerces extracted entities and relationships strictly to the declared domain vocabulary (governed by ADR-0130).

2. **Zero-Config Automatic Domain Classification (`domain=AUTO`)**:
   - Passing `domain=AUTO` to `build_graph` invokes `ContentClassifier` once per document head, recording the selected domain ID in `GraphBuildReport.domain` and classifier certainty in `GraphBuildReport.domain_confidence`.
   - Short documents (<100 chars), low-confidence predictions, or classifier errors fall back gracefully to `encyclopedia_wiki` with `domain_confidence == 0.0`.

3. **Iterative Gleaning & Cross-Chunk Carryover**:
   - `ExtractionPipeline` and `build_graph` support `gleanings >= 1` to execute follow-up extraction passes that capture missed entities without duplicating prior extractions.
   - `carryover_entities` forwards high-salience entities from chunk $N$ into the context of chunk $N+1$ (governed by ADR-0129).

## What this does not do

- **Arbitrary Code Execution in Schemas**: All YAML schemas are parsed strictly via `yaml.safe_load()` and validated through Pydantic models.
- **Per-Chunk Domain Switching**: `domain=AUTO` classifies at the document level rather than paying for a classification call on every chunk window.

## Checkable Outcomes

1. Loading a valid YAML schema string via `load_schema_from_string` returns a validated `DomainSchema` instance and `domain_system_prompt(schema)` produces a domain-specific prompt containing its entity and relationship types.
2. Calling `build_graph(..., domain=AUTO)` classifies the document, populates `report.domain` and `report.domain_confidence`, and falls back safely with `domain_confidence == 0.0` when text is under 100 characters.
3. Calling `build_graph(..., domain=schema, constrain_to_domain=True)` restricts extracted entity and relationship types to those permitted by the `DomainSchema`.
4. Running extraction with `gleanings=1` and `carryover_entities` merges supplementary gleaned entities and resolves cross-chunk references into the unified `DocumentExtracted` event.

## Linked User Stories

- [`US-0013`](../../user_stories/accepted/us-0013-domain-ontology-schemas-auto-classification-and-gl.md): Domain Ontology Schemas, Auto-Classification, and Multi-Pass Gleaning

## Implementing Backlog Tasks

- [`TASK-0025`](../../backlog/complete/0025-decompose-schema-org-module.md): Decompose schema_org module
- [`TASK-0026`](../../backlog/complete/0026-decompose-domains-registry-module.md): Decompose domains registry module
- [`TASK-0033`](../../backlog/complete/0033-port-legacy-domain-schema-and-extraction-adrs.md): Port legacy domain schema and extraction ADRs
