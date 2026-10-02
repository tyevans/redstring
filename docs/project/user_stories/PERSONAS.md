# User Personas

Archetypes representing the core users, operators, and developers interacting with redstring.

---

## 1. Alex — The Knowledge Graph Architect
- **Role**: Software engineer and GraphRAG application architect.
- **Pain Points**:
  - Context blindness when trying to answer cross-document questions using standard vector search.
  - Fragile graph stores that require destructive database migrations when prompts change.
  - Fractured entity nodes split across lexical aliases ("Ada Lovelace" vs "Lovelace, A.").
- **Goals with redstring**:
  - Clean LLM-driven entity and relationship extraction emitting immutable domain events.
  - Automatic alias consolidation and deterministic property merge resolution.
  - Multi-channel hybrid retrieval combining vector similarity, lexical search, and graph traversal.

---

## 2. Jordan — The Data Platform Lead
- **Role**: Data platform engineer managing document pipelines and persistent store infrastructure.
- **Pain Points**:
  - High operational burden syncing multiple databases (Neo4j, pgvector, PostgreSQL) during pipeline failures.
  - Inability to safely wipe or rebuild tenant data without risking cross-tenant data leaks.
- **Goals with redstring**:
  - Event-sourced write model where read projections can be safely cleared and replayed from sequence zero.
  - Pluggable storage adapters conforming to strict port contracts with zero leaky abstractions.
  - Multi-tenant isolation enforced at the port boundary with zero cross-tenant reads or writes.

---

## 3. Morgan — The Autonomous Coding Agent
- **Role**: LLM-powered coding worker contributing features, bug fixes, and refactors to the redstring repository.
- **Pain Points**:
  - Ambiguous task requirements lacking explicit domain invariants and Definition of Done.
  - Breaking public API contracts or violating file length limits (<500 lines).
- **Goals with redstring**:
  - Explicit task specifications citing governing ADRs, PRDs, and executable BDD user stories.
  - Strict worktree isolation preventing merge conflicts across concurrent workers.
  - Blackbox frontdoor test suites verifying contracts without private internal mocks.

---

## 4. Riley — The Open-Source Library Maintainer
- **Role**: Core maintainer responsible for releases, code quality, and architectural integrity.
- **Pain Points**:
  - Silent version declaration drift between `pyproject.toml` and `__init__.py`.
  - Stale Diataxis documentation and out-of-date code examples in published guides.
  - Untracked architectural decisions and creeping file length sprawl.
- **Goals with redstring**:
  - Strict preflight verification gates asserting 100% test passes and zero file length violations.
  - Automated documentation drift audits ensuring all CLI and code snippets remain verified.
  - Bidirectional traceability connecting user personas, stories, tasks, and commits.
