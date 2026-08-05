# The ring migration

Between commit `94b9ae1` and `341be8d`, the head of
`rearchitect/graph-vector-ports` as it was merged, redstring was rebuilt from
a service-and-ORM application into a library with pluggable storage. 171
commits, **33912 insertions and 62029 deletions across 362 files**, `src/`
going from 123 Python files to 81.

Those figures are the one part of this document that decays, so they name the
range that produces them rather than standing on their own:

```
git diff --shortstat 94b9ae1 341be8d
git rev-list --count 94b9ae1..341be8d
```

`341be8d` is the second parent of the merge commit `15a948c`; work landing on
`main` afterwards is not part of the migration and is deliberately outside the
range.

This document exists because the reasoning behind that has no other home. The
working notes it was extracted from — eleven briefs, eleven implementer
reports, ten reviews, 4.2 MB — were never tracked and are gone. What survives
is the tree, the commit messages, the ADRs in `docs/adr/`, and twelve
`recovery/*` tags.

Everything below is either verifiable from the tree today or names the git ref
that holds the evidence. Where a claim is about something that no longer
exists, the ref is the evidence, and the next section explains how to read
one.

> **Every recovery ref below is also an annotated tag**, so it survives a
> squash-merge, a rebase, or `gc`. `git tag -l 'recovery/*'` lists all twelve —
> the same set the two tables below account for, no more and no fewer — and
> each tag message says what it holds and which `BACKLOG` entry discusses it.
>
> Recover a deleted file with `git show recovery/<name>:<path>`, e.g.
> `git show recovery/strategy-router:src/redstring/extraction/strategy_router.py`.
>
> **Eleven of the twelve mark a deletion. One does not.**
> `recovery/schema-org-preport` marks a *port*: `extraction/schema_org.py` is
> still in the tree today, and the tag exists to hold the state it was in
> before it moved off the `EntityType` enum. The `git show` recipe applies to
> it unchanged — it is an ordinary commit-ish — with one adjustment, because
> the tag names the port commit rather than the state before it:
>
> ```
> git show recovery/schema-org-preport~1:src/redstring/extraction/schema_org.py
> ```
>
> The tag itself (`1b915f8`) already has the ported file; `~1` is the pre-port
> one, importing `EntityType` and `ExtractionMethod` from
> `models/extracted_entity.py`. Reading both is the diff, and the diff is the
> point of the tag.
>
> **Push the tags along with the branch** — `git push origin --tags` — or they
> exist only on the machine that made them.

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

The contract this section keeps is **every `recovery/*` tag is accounted for
below** — the twelve `git tag -l 'recovery/*'` prints appear here, and nothing
appears here that is not one of them. That is the property worth checking when
a tag is added or this document is edited.

The tags are not all the same kind of thing, so they are split into two tables.

**Deletion refs.** Each ref in the table below is the last commit at which the
path exists; the path is gone from the tree today, and `git show <ref>:<path>`
is how you read it back. All were verified resolvable when this document was
written.

| Capability | Path | Ref | Replaced by |
|---|---|---|---|
| Document sourcing, scraping | `src/redstring/scraping/` | `94b9ae1`<br>`recovery/sourcing` | Nothing — out of scope, by decision |
| Vendor extractors, inference providers | `src/redstring/inference/` | `a75015a`<br>`recovery/vendor-extractors` | `LlmProvider` port + `llm/adapters/` |
| Preprocessing, chunkers, mergers | `src/redstring/preprocessing/` | `bd40882`<br>`recovery/preprocessing` | `extraction/chunking.py`, `extraction/merging.py` |
| Fuzzy merging (`SimpleMerger`, `LLMMerger`) | `src/redstring/services/consolidation/` | `ff36ec7`<br>`recovery/fuzzy-merging` | `consolidation/` on the `ConsolidationLog` aggregate |
| Temporal parser service | `src/redstring/services/temporal_parser.py` | `d49f56b`<br>`recovery/temporal-services` | `domain/temporal_parsing.py::parse_temporal` |
| Timeline query / export / cache | `src/redstring/services/` | `d49f56b`<br>`recovery/temporal-services` | `temporal/` (query); export is a genuine loss, see `BACKLOG` B47 |
| Strategy router | `src/redstring/extraction/strategy_router.py` | `66f589d`<br>`recovery/strategy-router` | Nothing — 826-line test file supplied every input as a `MagicMock` |
| Neo4j client (443 lines, zero callers) | `src/redstring/graph/client.py` | `3502900`<br>`recovery/neo4j-client` | `graph/adapters/neo4j.py` |
| The whole service layer | `src/redstring/services/` | `c3c88ad`<br>`recovery/service-layer` | `aggregates` + `events` + `projections` |
| ORM models, schemas, `db.py` | `src/redstring/models/`, `schemas/`, `db.py` | `1b9f9f3`<br>`recovery/orm-layer` | The two ports. The library owns no schema |
| Settings object, Redis singleton | `src/redstring/config.py`, `cache.py` | `6a473ff`<br>`recovery/settings` | Explicit constructor arguments |
| Prompt library, JSON-schema generator | `src/redstring/extraction/prompts.py` | `e063faa`<br>`recovery/prompts-encryption` | `extraction/domains/` + `prompt_generator.domain_system_prompt` |
| Encryption at rest | `src/redstring/encryption.py` | `e063faa`<br>`recovery/prompts-encryption` | Nothing — see `BACKLOG` B58 |

That is thirteen rows against eleven tags, because `recovery/prompts-encryption`
covers two of them: `prompts.py` and `encryption.py` went in the same commit.

### Refs that preserve a prior state rather than a deleted path

One tag is not a deletion, and reading it as one sends you to the wrong commit.

| What it preserves | Path | Ref | Superseded by |
|---|---|---|---|
| `schema_org.py` before it moved off the ORM enum | `src/redstring/extraction/schema_org.py` | `1b915f8`<br>`recovery/schema-org-preport`<br>(pre-port state is at `~1`) | Free-string `entity_type`; `ExtractionMethod` from `domain/entity.py` |

The path still exists in the tree today. What the tag holds is the *before*
side of a port: at `recovery/schema-org-preport~1` the module opens with

```python
from redstring.models.extracted_entity import EntityType, ExtractionMethod
```

and `SCHEMA_TYPE_MAP` maps `"Person"` onto `EntityType.PERSON` rather than onto
the string `"person"`. The tagged commit itself already has the ported file, so
the diff across the tag is the whole record — thirteen lines each way, which is
exactly why it needed a tag: a change that small is invisible in a 171-commit
range and unrecoverable once the enum's module is deleted.

That deleted module is the second thing the tag preserves. `EntityType`'s
members survive in only two places now: this ref (which still contains
`models/extracted_entity.py` on both sides of `~1`) and
`tests/unit/extraction/test_schema_org.py`, which continues to assert the
mappings by their string values. `recovery/orm-layer` (`1b9f9f3`) is the
*last* commit at which `models/extracted_entity.py` exists and is the ref to
use for the enum in full — including the docstring quoted under the
module-docstring decisions below.

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

Those are the numbers that have been allocated. `docs/adr/` also holds a set of
drafts still carrying `0007`, and that is the numbering rule working rather
than failing: numbers are allocated at merge, against the highest on `main` at
that moment, so parallel slices all draft the same next one and whichever
merges second renumbers (`.claude/rules/definition-of-done.md`;
`.claude/rules/recurring-defects.md` §6). Cite a draft by its **filename**,
never by `0007` — the number is not yet a fact about it:

| Draft | Decision |
|---|---|
| [`no-ann-index-in-a-multi-tenant-vector-store`](adr/0012-no-ann-index-in-a-multi-tenant-vector-store.md) | pgvector carries no ANN index, and the reason is not performance |
| [`resilience-behind-the-cache-port`](adr/0013-resilience-behind-the-cache-port.md) | Retry, rate limiting and circuit breaking live in `llm/`, over the `Cache` port |
| [`the-extraction-fold-resolves-through-aliases`](adr/0009-the-extraction-fold-resolves-through-aliases.md) | The extraction fold resolves endpoints through the alias table |
| [`one-total-order-for-preference`](adr/0010-one-total-order-for-preference.md) | One total order decides which mapping of a thing survives |
| [`the-two-non-store-ports`](adr/0008-the-two-non-store-ports.md) | Why `Cache` and `LlmProvider` are ports, which 0002 does not cover |
| [`composition-is-the-only-top-layer`](adr/0007-composition-is-the-only-top-layer.md) | `composition` is the only top layer, and `build_graph` writes without a log |
| [`domain-schemas-prompt-but-do-not-constrain`](adr/0011-domain-schemas-prompt-but-do-not-constrain.md) | Domain schemas prompt the model; they do not constrain it |
| [`exemption-lists-are-empty-and-must-stay-falsifiable`](adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md) | Both exemption lists are empty, and an emptied one is deleted rather than kept |

A draft constrains a new spec exactly as much as an accepted ADR does, so run
against the content and ignore the number. The two tables together are the
whole of `docs/adr/`; if a file there appears in neither, one of them is stale.

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
- `extraction/schema_org.py` — why `entity_type` is a free string. The
  docstring argues the decision; the port that carried it out is at
  `recovery/schema-org-preport`, whose `~1` side still maps `"Person"` onto
  `EntityType.PERSON`, so the two readings of `SCHEMA_TYPE_MAP` sit either
  side of one tag. The deleted
  enum had conceded the point in its own docstring: `String(100)` "to support
  dynamic domain-specific types", with `is_valid`/`get_or_none` helpers whose
  only job was to say "legitimately not one of mine" without raising. An enum
  that needs those is a vocabulary, not a type.

## Backlog entries that were closed

`BACKLOG.md` carries only open work, and closing an entry deletes it. Docstrings
and tests still cite closed entries by number, and without this index those
pointers resolve to nothing. Seven of the eight below are cited from `src/` or
`tests/`; B26 is cited only by the archived plan and is indexed here because it
is the one whose closure is invisible in the tree — the duplicate it names was
removed, so nothing is left to point at it.

| Id | What it was | Closed by | Where the reasoning lives now |
|---|---|---|---|
| B10b | Blocking-key lookup scanned the tenant | Slice 7 | [ADR 0003](adr/0003-blocking-keys-as-nodes.md) |
| B10d | `retry.py` read a process-wide settings object, so its tests inserted a `MagicMock` at `sys.modules["redstring.config"]` and poisoned every test that ran after them | Slice 6, by replacing the read with a plain default | `llm/retry.py`, `tests/unit/llm/test_retry.py` — both docstrings state it |
| B26 | `DatePrecision`/`UncertaintyMarker` duplicated between `domain/` and the ORM models | Slice 9, by deleting the ORM | Nothing left to say; `domain/temporal.py` is the only definition |
| B33 | A `LEGACY_EVENT_MODULES` exclusion list in the event-schema gate | Slice 9, with the last legacy module (`events/scraping.py`) | `tests/unit/events/test_schema.py` — an exclusion over an empty set excludes nothing, so it was deleted rather than emptied |
| B34 | A `DocumentExtracted` folded after an `EntitiesMerged` silently reverted the merge | Slice 7 | `ports/graph_store.py`, `projections/graph.py`; [ADR 0001](adr/0001-event-log-schema-and-granularity.md) |
| B40 | Fuzzy merging deleted rather than ported | Slice 7 | `consolidation/policy.py`; [ADR 0004](adr/0004-consolidation-emits-events.md) |
| B55 | Domain schemas had no caller | Slice 10 | `extraction/prompt_generator.py`. The residue is open as B57 |
| B56 | `redstring.config` read the environment | Slice 10 | `tests/unit/test_library_reads_no_environment.py` |

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

## Where this file lives

This document is `docs/plans/ring-migration.md`. It was `docs/ring-migration.md`
for most of the campaign, and it was moved with `git mv`, so its history is
intact and `git log --follow docs/plans/ring-migration.md` reads across the
rename.

The old path is dead, and every reference to it has been corrected: `README.md`,
three lines in `BACKLOG.md`, and the reciprocal link at the top of
`docs/history/2026-08-ring-migration-plan.md` — that last one both in its link
text and in its relative target, which is now `../plans/ring-migration.md`.
`git grep 'docs/ring-migration.md'` should return exactly one hit,
`.claude/rules/recurring-defects.md`, where the wrong path is quoted on purpose
as the example of a stale reference surviving several slices.

That one deliberate hit is why the check is a grep and not a link checker: a
relative link that resolves is not evidence the path in the prose beside it is
right, and a path quoted as an example of a mistake must not be swept. When
this file moves again, grep for the symbol across `docs/`, `README.md`,
`CLAUDE.md`, `.claude/` and docstrings rather than a remembered list of files —
the sweep that fails is always the one that fixed the pages it thought of.
