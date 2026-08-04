# The ring migration

Between commits `94b9ae1` and the head of `rearchitect/graph-vector-ports`,
kg-builder was rebuilt from a service-and-ORM application into a library with
pluggable storage. 164 commits, **32579 insertions and 61699 deletions across
354 files**, `src/` going from 123 Python files to 81.

This document exists because the reasoning behind that has no other home. The
working notes it was extracted from — eleven briefs, eleven implementer
reports, ten reviews, 4.2 MB — were never tracked and are gone.

Everything below is either verifiable from the tree today or names the git ref
that holds the evidence.

> **The recovery refs in this document and in `BACKLOG.md` only survive if this
> branch's history does.** They are commits on `rearchitect/graph-vector-ports`,
> not on any tag. A squash-merge, or a rebase that orphans them past `gc`,
> destroys every deleted capability recorded here. Merge with history, or tag
> the pre-deletion refs before you do anything else.

## What the library is now

Five things, and nothing else:

- **Two storage ports** — `GraphStore` and `VectorStore` — with a compliance
  suite each (`tests/compliance/`) that is the actual definition of the
  contract. Adapters: in-memory (default gate), Neo4j, pgvector.
- **A write model** of aggregates and events (`aggregates/`, `events/`) and a
  **read model** of projections (`projections/`). Extraction emits; projections
  write. Nothing else writes to a store.
- **A domain** (`domain/`) of pure types: `Entity`, `Relationship`, `Alias`,
  similarity, blocking, interval arithmetic, temporal parsing.
- **An `LlmProvider` port** with a LangChain adapter and a fake, plus transport
  concerns (retry, rate limiting, circuit breaking, caching) behind a `Cache`
  port with an in-memory default, so the library runs with no infrastructure.
- **One composition module** (`composition.py`) holding `build_graph`, the only
  place both halves meet.

There is no ORM, no session, no SQLAlchemy, no settings object, no schema this
library expects a caller to have migrated, and no code that fetches a document.

## What was deleted, and where to get it back

Each ref below is the last commit at which the path exists. `git show <ref>:<path>`
works today; all were verified resolvable when this document was written.

| Capability | Path | Ref | Replaced by |
|---|---|---|---|
| Document sourcing, scraping | `src/kg_builder/scraping/` | `94b9ae1` | Nothing — out of scope, by decision |
| Vendor extractors, inference providers | `src/kg_builder/inference/` | `a75015a` | `LlmProvider` port + `llm/adapters/` |
| Preprocessing, chunkers, mergers | `src/kg_builder/preprocessing/` | `bd40882` | `extraction/chunking.py`, `extraction/merging.py` |
| Fuzzy merging (`SimpleMerger`, `LLMMerger`) | `src/kg_builder/services/consolidation/` | `ff36ec7` | `consolidation/` on the `ConsolidationLog` aggregate |
| Temporal parser service | `src/kg_builder/services/temporal_parser.py` | `d49f56b` | `domain/temporal_parsing.py::parse_temporal` |
| Timeline query / export / cache | `src/kg_builder/services/` | `d49f56b` | `temporal/` (query); export is a genuine loss, see `BACKLOG` B47 |
| Strategy router | `src/kg_builder/extraction/strategy_router.py` | `66f589d` | Nothing — 826-line test file supplied every input as a `MagicMock` |
| Neo4j client (443 lines, zero callers) | `src/kg_builder/graph/client.py` | `3502900` | `graph/adapters/neo4j.py` |
| The whole service layer | `src/kg_builder/services/` | `c3c88ad` | `aggregates` + `events` + `projections` |
| ORM models, schemas, `db.py` | `src/kg_builder/models/`, `schemas/`, `db.py` | `1b9f9f3` | The two ports. The library owns no schema |
| Settings object, Redis singleton | `src/kg_builder/config.py`, `cache.py` | `6a473ff` | Explicit constructor arguments |
| Prompt library, JSON-schema generator | `src/kg_builder/extraction/prompts.py` | `e063faa` | `extraction/domains/` + `prompt_generator.domain_system_prompt` |
| Encryption at rest | `src/kg_builder/encryption.py` | `e063faa` | Nothing — see `BACKLOG` B58 |

The deletions were not uniformly costly. Three of the four modules that looked
like live dependents of the relational layer were dead code that merely still
parsed. The two capabilities genuinely lost are the iCalendar/CSV exporters
(B47) and encryption (B58), and both entries carry the ref and the argument.

## Decisions with their own record

These are the ones expensive enough to revisit that they got an ADR:

| ADR | Decision |
|---|---|
| [0001](adr/0001-event-log-schema-and-granularity.md) | Event log schema and granularity |
| [0002](adr/0002-two-store-ports.md) | Two store ports, and why there is no `delete_entity` |
| [0003](adr/0003-blocking-keys-as-nodes.md) | Blocking keys are Neo4j nodes, not a list property |
| [0004](adr/0004-consolidation-emits-events.md) | Consolidation emits events rather than writing |
| [0005](adr/0005-temporal-inference-on-read.md) | Temporal inference is computed on read |
| [0006](adr/0006-the-public-surface-is-gated.md) | The public surface is gated by three tests, not curated |

Several other decisions live in module docstrings rather than here, and that is
deliberate — a reason belongs next to the code it constrains when the code is
the only thing that could contradict it. The substantial ones:

- `ports/graph_store.py` — why the store holds aliases at all.
- `graph/adapters/neo4j.py` — JSON for nested fields; why alias nodes; why no
  index on the `blocking_keys` property.
- `vector/adapters/pgvector.py` — no ANN index, deliberately (`BACKLOG` B10k).
- `temporal/inference.py` — the whole on-read argument, and the sort-order
  defect that produced it.
- `consolidation/service.py`, `consolidation/policy.py` — read/plan/emit, and
  the concurrency window (`BACKLOG` B43).
- `projections/graph.py` — why the extraction fold resolves through aliases.
- `extraction/schema_org.py` — why `entity_type` is a free string. The deleted
  enum had conceded the point in its own docstring: `String(100)` "to support
  dynamic domain-specific types", with `is_valid`/`get_or_none` helpers whose
  only job was to say "legitimately not one of mine" without raising. An enum
  that needs those is a vocabulary, not a type.

## Backlog entries that were closed

`BACKLOG.md` carries only open work, and closing an entry deletes it. Tracked
code still cites eight closed entries by number; without this index those
pointers resolve to nothing.

| Id | What it was | Closed by | Where the reasoning lives now |
|---|---|---|---|
| B10b | Blocking-key lookup scanned the tenant | Slice 7 | [ADR 0003](adr/0003-blocking-keys-as-nodes.md) |
| B10d | `retry.py` read a process-wide settings object, so its tests inserted a `MagicMock` at `sys.modules["kg_builder.config"]` and poisoned every test that ran after them | Slice 6, by replacing the read with a plain default | `llm/retry.py`, `tests/unit/llm/test_retry.py` — both docstrings state it |
| B26 | `DatePrecision`/`UncertaintyMarker` duplicated between `domain/` and the ORM models | Slice 9, by deleting the ORM | Nothing left to say; `domain/temporal.py` is the only definition |
| B33 | A `LEGACY_EVENT_MODULES` exclusion list in the event-schema gate | Slice 9, with the last legacy module (`events/scraping.py`) | `tests/unit/events/test_schema.py` — an exclusion over an empty set excludes nothing, so it was deleted rather than emptied |
| B34 | A `DocumentExtracted` folded after an `EntitiesMerged` silently reverted the merge | Slice 7 | `ports/graph_store.py`, `projections/graph.py`; [ADR 0001](adr/0001-event-log-schema-and-granularity.md) |
| B40 | Fuzzy merging deleted rather than ported | Slice 7 | `consolidation/policy.py`; [ADR 0004](adr/0004-consolidation-emits-events.md) |
| B55 | Domain schemas had no caller | Slice 10 | `extraction/prompt_generator.py`. The residue is open as B57 |
| B56 | `kg_builder.config` read the environment | Slice 10 | `tests/unit/test_library_reads_no_environment.py` |

Entries B2, B3, B5, B6, B7, B11, B13, B19, B24, B25 are cited only by the
archived plan and were resolved by the deletions above. B24 — "no migration
path" — is the one worth naming: it asked who owns the relational schema, and
the answer turned out to be that the library owns no schema.

## What the campaign learned about testing

`CLAUDE.md` carries this, and it is the most reused output of the whole
migration. The short version: a sixteen-row table of test inputs that made two
candidate implementations agree, every one of which passed review while proving
nothing, and every one of which was found by mutation testing rather than by
reading. If you read one thing before writing a test here, read that table.

Two campaign-level facts that belong with it:

- **Mutation testing found defects nothing else did, repeatedly.** Slice 3: 9.
  Slice 5b: 11, "none findable by reading". Slice 6: three tie-break defects in
  sequence, each found by the fix for the last. Slice 8's only Critical came
  from asking a reviewer to hunt for a fourth defect after the run found three.
- **A zero-survivor run means the harness is broken.** It happened twice, in
  both directions, from a venv missing an extra. `uv sync --all-extras`.

## The archived plan

`docs/history/2026-08-ring-migration-plan.md` is the plan this executed against.
It is history — written in the future tense about work that is done, including
slices that were re-scoped mid-campaign. Its Global Constraints section is the
part still worth reading; it is what the eleven slices were held to.
