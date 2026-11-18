---
id: '0150'
title: Attacker-influenced input is bounded and inert
status: Accepted
target_bc: core
governing_prds:
- PRD-0013
governing_stories:
- US-0030
amends:
- ADR-0116
- ADR-0129
- ADR-0139
---

# ADR-0150: Attacker-influenced input is bounded and inert

## Status

Accepted. Amends [ADR-0116](adr-0116-graph-store-is-five-capabilities.md),
[ADR-0129](adr-0129-a-chunk-is-not-extracted-alone.md) and
[ADR-0139](adr-0139-bounded-concurrency-over-chunks.md). Each amended record
keeps its Decision. This record adds limits on top of it.

## Context

redstring reads documents that the caller does not control, and the LLM output
that comes from those documents. Both are attacker-influenced. A document can
make the model emit any string, and a prompt injection makes that likely, not
only possible.

Before this record, the library treated that input as trusted in three ways:

1. Caller parameters that multiply work (traversal depth, concurrency, gleaning
   rounds, page size, chunk size) had a lower limit and no upper limit. A
   dense graph or one large value caused unbounded work.
2. Entity names from one chunk went into the system prompt of later chunks
   exactly as the model wrote them, with their newlines.
3. The GraphML importer used an HTML parser, which is not an XML parser, and
   the exporter wrote characters that XML forbids.

A library cannot do authorisation for its caller. It can make sure that the
work and the text an input causes have a known limit.

## Decision

1. **Every parameter that multiplies work has a named maximum.** The maximum
   is a module constant beside the code that checks it. A value above the
   maximum raises before any work starts, so a refused call has no side
   effect. Each maximum is a backstop, not a tuning target: a caller who needs
   more splits the work.

2. **Traversal depth is part of the `GraphStore` contract.**
   `MAX_TRAVERSAL_DEPTH` is declared in `ports/graph_store.py`. Every adapter
   refuses a deeper `neighbors` call before it reads the store, and the
   compliance suite tests both sides of the limit, so a third-party adapter
   inherits the rule. `explain_path` uses the same limit. This amends
   ADR-0116: the capability protocols are unchanged, and `neighbors` has a
   stated upper bound.

3. **LLM output is data.** Schema fields that the model fills have length and
   item limits. A name that goes into a later prompt (carryover, gleaning) has
   its control characters replaced and is clipped first. This amends ADR-0129:
   the prompt still names what earlier chunks found, in a form that cannot
   add lines to the prompt. Log records of a model failure contain the error
   type and a bounded, escaped excerpt, not the raw model text.

4. **Batch concurrency is a documented product.** A batch runs up to
   `max_concurrency` documents, and each document has up to `concurrency`
   calls in flight. The endpoint therefore sees up to
   `max_concurrency * concurrency` calls, and both factors are capped. This
   amends ADR-0139, whose `CallLimiter` stays per document. A batch-wide
   limiter of size `concurrency` was tried and rejected: with the default
   values it cut a default batch from four parallel calls to one.

5. **Import files are parsed by safe parsers and trusted for identity.**
   GraphML is parsed with `defusedxml`, which refuses DTDs and entity
   declarations. JSON-LD and GraphML payloads have a size limit, and malformed
   input raises `ValueError`. An import file is trusted for provenance and for
   entity identity: an imported id that already exists in the target tenant
   replaces that entity. PRD-0008 requires a lossless round trip, which is
   not possible if import rewrites provenance. The caller decides which files
   to trust.

6. **SQL identifier limits are derived.** The longest table name an adapter
   accepts is computed from the suffixes it appends, so every derived index
   and table name fits the Postgres identifier limit. A longer suffix lowers
   the limit with no other edit.

## Consequences

- A caller who passed a value above a new maximum now gets `ValueError` (or
  `ChunkSizeError` from a chunker) where before the call ran. This is a
  behaviour change, and the changelog states it.
- Table names that were legal and too long for their derived names are now
  refused when the store is constructed. A deployment that used one renames
  the table.
- Each new limit is one more constant to keep in step with the docs. The
  constants are the single declaration site, and docstrings name the constant,
  not its value.
- The consolidation candidate cap limits scoring. It does not limit the block
  read before the cut, which BACKLOG B167 tracks.

## Related records

- [ADR-0111](adr-0111-domain-schemas-prompt-but-do-not-constrain.md) stands.
  Length limits are not type constraints, and an off-schema entity is still
  not an error.
- [ADR-0123](adr-0123-the-chunk-corpus.md) stands.
- [ADR-0141](adr-0141-the-consolidation-pass-is-decide-then-emit.md) stands.
  The candidate cap applies inside the scoring phase and keeps its order.
