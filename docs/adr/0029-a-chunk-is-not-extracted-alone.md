# ADR 0029: A chunk is not extracted alone

## Status

Accepted. [`0011` domain schemas prompt but do not
constrain](0011-domain-schemas-prompt-but-do-not-constrain.md) **stands, and
is extended**: both mechanisms here are prompt content and neither validates
anything, which is the same position that ADR takes about a domain's
vocabulary. [`0005` temporal inference on
read](0005-temporal-inference-on-read.md) stands untouched -- nothing here
emits an edge. [`0009` the extraction fold resolves through
aliases](0009-the-extraction-fold-resolves-through-aliases.md) stands: the
carryover reduces how much work that fold and `consolidation` are handed, and
changes neither.

[`0008` the two non-store ports](0008-the-two-non-store-ports.md) stands
**unamended, and that is the load-bearing part of this decision**:
`LlmProvider.extract(text, schema, *, system_prompt)` was sufficient for both
mechanisms without widening. A design that had needed a `messages` list, a
conversation handle, or a `previous_response_id` would have put a chat API's
shape back into the port that exists to keep it out.

## Context

Extraction sent each chunk to the model alone, once, with a fixed system
prompt. Two consequences followed, and neither was visible in the output --
the output simply had fewer, or more numerous, entities than it should.

**A chunk after the first begins mid-argument.** It says "Lovelace" where an
earlier chunk said "Ada Lovelace". In a library whose entity ids are derived
from `(tenant, source, entity_type, normalized_name)`, that is not a stylistic
wobble: it manufactures a second entity. `merge_extractions` cannot combine
the two -- it combines only what is the same entity *by construction* --
so the pair reaches `consolidation`, which pays a model call to decide they
are one person and writes an `EntitiesMerged` to record it. Naming drift at a
chunk boundary is therefore billed twice.

**A model asked once for the entities in a dense paragraph stops early.** Not
because the paragraph is exhausted, but because the answer feels complete.
This is well attested: Microsoft GraphRAG runs a "gleaning" loop that feeds
the extraction back and asks what was missed, and Graphiti runs a reflexion
step after its first pass for the same reason.

Both problems are addressed everywhere else in this field by giving the model
more context, and the two obvious ways to do that are the two this decision
rejects.

## Decision

### Each chunk's prompt names what earlier chunks found

`extraction/carryover.py` accumulates `(name, entity_type)` pairs from every
chunk's *mapped* result and appends a bounded, oldest-first list to the system
prompt for the next chunk, with an instruction to reuse those spellings and to
list nothing the current text does not mention. On by default
(`carryover_entities=32`), because it costs no model call and the defect it
prevents costs one.

**In the system prompt, not prepended to the chunk.** The list is an
instruction about naming, not content to extract from. Inside the chunk it is
indistinguishable from the document, and the failure is exact: the model
reports carried names as entities of the current chunk, and
`PipelineResult.chunks` then attributes them to a passage that never mentioned
them -- so a chunk retrieved for an entity does not contain it. That would
corrupt the corpus [`0023` built](0023-the-chunk-corpus.md).

**Built from mapped entities, not from the raw answer.** The mapper normalizes
names and drops rows the domain refuses. A carryover built from the wire shape
would offer later chunks a spelling no entity in the document has.

**Bounded by recency.** A prompt cannot carry a long document's entities. An
unresolved short form refers to something named nearby, so recency is the
right side to keep; it is admittedly the wrong side for a protagonist named
once at the start.

**Rejected: carrying names across documents.** It is the same operation as
cross-document entity resolution, and doing it in a prompt would do it with no
event, nothing to audit and nothing to undo -- which is precisely what
[`0004`](0004-consolidation-emits-events.md) and `merging.py` refuse. A
`Carryover` belongs to one `extract` call and is discarded with it.

**Rejected: giving the model the previous chunk's text**, as Graphiti gives an
episode its previous four messages. It carries the same information at many
times the tokens, and it re-presents prose the model will extract from again
-- the attribution problem above, in a form no instruction can suppress.

### A chunk may be shown its own answer and asked what it missed

`extraction/gleaning.py`, reached by `gleanings=N`. **Off by default**: it is
one extra model call per chunk per pass, and this pipeline is sequential over
chunks because the reference deployment serves one request at a time, so
`gleanings=1` roughly doubles a run. Recall is worth paying for and is not
worth paying for by accident.

**The two answers are combined as `Extraction`s, before mapping.** Not a
choice about where to put a fold: `_map_relationships` resolves an endpoint
name against the entities in the same answer, so an edge stated by the second
pass between one of its entities and one of the first pass's is resolvable
only if the mapper sees a single `Extraction`. Merged after mapping, exactly
the edges a second pass exists to find are counted unresolved and dropped.

**Repeating is explicitly permitted in the prompt**, which is the opposite of
the carryover's instruction and for a reason that generalises: a repeat here
is deduplicated by derived id and costs only tokens, while a model told
sternly not to repeat itself also withholds the entity it has just realised it
mis-typed.

**A failed gleaning never propagates, whatever `skip_failed_chunks` says.** A
failed chunk is a hole in the document; a failed gleaning is a chunk that got
one pass instead of two, with a complete first answer in hand. Discarding that
answer would trade a smaller extraction for none. It is counted on
`failed_gleanings` rather than logged, because fewer entities is what a
successful run also looks like.

**Rejected: gleaning on by default with a small N.** GraphRAG defaults to one
pass. Here the cost is not amortised across a fleet -- it is a doubling of
wall-clock on a single-GPU server -- and a library that silently doubles what
a caller pays for a model has made a decision that was the caller's.

## Consequences

Both mechanisms are prompt content. Neither validates, neither constrains, and
neither can fail a run: a model that ignores the carryover produces exactly
what it produced before, and a gleaning pass that fails leaves the first
answer standing. That is the same "shapes but does not enforce" position
[`0011`](0011-domain-schemas-prompt-but-do-not-constrain.md) takes, and it is
what makes both safe to have on a path with no ability to reject an answer.

`ExtractionPipeline.system_prompt` now reports the configured base rather than
what any chunk was sent. A caller logging it sees configuration; the carryover
block is per-chunk and is not configuration.

**The graded corpus cannot see either change, and that is a gap this decision
knowingly accepts.** All five documents in `tests/accuracy/` are a single
chunk, so the carryover never acts on them and a gleaning pass has no second
chunk to be compared across. The commit gate proves the mechanisms are wired
and correct; it does not prove they improve extraction, and no committed suite
currently can. `BACKLOG.md` records what a multi-chunk graded document would
take.

**Gleaning stays off, now measured rather than only argued.** Once ADR 0031
made the graded corpus cheap to run, `gleanings=1` was compared against
`gleanings=0` on it: **identical results**, 12 entity true positives and 4
false positives either way. The second pass reported nothing missed, so the
combined answer equalled the first answer exactly and the extra call per chunk
bought nothing.

That is a null result on a corpus that **cannot show gleaning's benefit** --
recall is already 1.000 with no headroom, and every document is one short
chunk, which is the opposite of the long dense chunk a second pass is for. So
this neither vindicates nor condemns the mechanism; it says the default is
correct on the evidence available, and that B115's third property (entities a
good model plausibly misses) is what would change the answer.

Two counters are added to `PipelineResult` (`gleaning_passes`,
`failed_gleanings`) and one argument to each of `ExtractionPipeline` and
`build_graph`. `build_graph` exposes both because it constructs the pipeline,
so a caller would otherwise have no reach to them -- and because turning the
carryover off is what makes a before/after comparison possible at all.
