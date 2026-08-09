# ADR 0030: A domain schema may constrain, when asked

## Status

Accepted. **Amends
[`0011` domain schemas prompt but do not constrain](0011-domain-schemas-prompt-but-do-not-constrain.md)**
and supersedes none of it: 0011's decision remains the default, its reasoning
is unchanged and is restated below as the argument for keeping the default
where it is. What changes is that "a domain schema does not constrain"
describes the default rather than the library.

[`0008` the two non-store ports](0008-the-two-non-store-ports.md) stands
**unamended**, and as with
[`0029`](0029-a-chunk-is-not-extracted-alone.md) that is the load-bearing
part: `LlmProvider.extract` has always taken `type[S]`, so constraining the
vocabulary needed a different argument, not a different port.
[`0029`](0029-a-chunk-is-not-extracted-alone.md) stands -- the schema and the
prompt are independent dials on the same call.

## Context

`domain_system_prompt` renders a `DomainSchema`'s entity and relationship
types into prose. The JSON Schema the server actually decodes against comes
from `extraction.schema.Extraction`, whose `entity_type` is a bare `str`. So
the vocabulary is a description the model may follow, and "chief executive"
is as decodable an answer as "person".

Whether that is right depends on what the caller is doing, which is why
LlamaIndex ships `SchemaLLMPathExtractor` and `DynamicLLMPathExtractor` side
by side rather than choosing. 0011 chose, for a library that had one
behaviour.

**The measurable cost is specific.** `tests/accuracy/scoring.py` keys an
entity on `(normalized name, lowercased type)`, and `corpus.yaml` grades types
as domain schema ids. A model answering "executive" where the corpus says
"person" scores a false positive *and* a false negative from one entity. That
is a scoring artefact in a five-document corpus and a real cost in a graph:
two type spellings for one kind of thing are two node labels.

**What looked like this feature and was not.** `prompt_generator.generate_json_schema`
built a JSON Schema `dict` with the domain's ids as an `enum`. There was no
parameter to pass a dict to -- `extract` takes a pydantic class -- and the
dict named its fields `type`/`source`/`target` where `Extraction` uses
`entity_type`/`source_name`/`target_name`, so a model obeying it produced
output `map_extraction` cannot read. It was deleted in slice 10. Its existence
is why this ADR is explicit about the mechanism rather than only the decision.

## Decision

### The constraint is a pydantic subclass, built per domain, passed as `schema`

`extraction/constrained.py::constrained_extraction` returns an `Extraction`
subclass whose `entity_type` and `relationship_type` are `Literal`s over the
domain's declared ids. `ExtractionPipeline` takes it as `schema`;
`build_graph` builds it when `constrain_to_domain=True`.

**Subclassing, not rebuilding.** `Extraction`'s field *names* are what
`map_extraction` reads. A freshly constructed model can rename a field and
nothing downstream will say so -- that is precisely how the deleted function
was broken, undetectably, because its output was never passed anywhere.
Inheriting the fields makes the drift unrepresentable rather than merely
discouraged, and carries the field descriptions (which are prompt, not
documentation) along unchanged.

**Off by default, and 0011's reasoning is why.** A domain schema's type list
is what its author thought of. A hard enum turns everything they did not think
of into the nearest wrong answer rather than into a new type: a news schema
with no `legislation` does not stop documents mentioning acts of parliament,
and unconstrained the model says "legislation" while constrained it says
"document". The unconstrained graph has a type nobody declared; the
constrained one is quietly wrong. Which is worse is the caller's judgement,
and it is not the same judgement for a curated newsroom feed and an open
crawl.

**`constrain_to_domain=True` with no `domain` is refused, before the model is
called.** Falling back to the unconstrained schema would be the worst
available behaviour: the two runs are then indistinguishable except in the
numbers the caller was trying to move. Checked ahead of extraction for the
reason the embedding pair is -- discovering it afterwards costs a document.

**Rejected: a validation pass that drops or re-labels out-of-vocabulary
types.** It needs a decision this library has already made the other way --
`map_extraction` raises for nothing the model did wrong, it counts. And it
constrains *after* paying for the tokens, where an enum in the decoded schema
constrains instead of them.

**Rejected: constraining entity types and leaving relationship types free.**
Defensible -- a domain's relationship list is usually the less complete of the
two -- but it makes one flag mean two things and leaves the caller unable to
ask for the other combination. One vocabulary, one dial.

### There is no empty-vocabulary case

`Literal[()]` is not a type, so this began with a guard and an exception for a
domain declaring no types. Both were deleted: `DomainSchema` declares both
lists `min_length=1`, so the branch was unreachable -- inert code arriving as
defensiveness. A test over `DomainSchema` pins the invariant instead, so
relaxing either constraint fails there rather than as a `TypeError` out of
`typing`.

## Consequences

The vocabulary now has two possible meanings and which one is in force is a
caller's argument, so a graph's type set is no longer inferable from the
domain alone. `permitted_entity_types` exists so a run can log what it was
constrained to.

`_resolve_prompt` returns the resolved `DomainSchema` alongside the prompt
rather than only the id. That matters on the `AUTO` path: the classifier runs
once, and a second registry lookup at the call site could disagree with it
about the fallback -- `ContentClassifier` returns `encyclopedia_wiki` on three
different give-up paths, and only the resolution that ran knows.

**The measurement was run, and it argues for the default rather than against
it.** Against the graded corpus at `temperature=0.0`, constrained decoding
left recall identical (perfect in both arms) and made precision *worse*:
entity false positives rose from 8 to 13, relationship false positives from 6
to 7. Counts rather than F1, and `BACKLOG.md` B57 carries them with the limits
of the instrument.

The reason is a mechanism this decision did not anticipate and which belongs
in the record. **An enum does not only forbid the types outside it; it
advertises the types inside it**, and a model reads the list as a checklist.
On an 81-character sentence the unconstrained run emitted four entity types
and the constrained run emitted all nine the schema declares, inventing a
`claim`, a `date`, a `quote`, a `source` and a `statistic` that the text does
not contain.

So the trade stated above -- coverage for consistency -- is incomplete. It is
that, plus a hallucination pressure proportional to how many types the schema
declares and how few of them the document actually contains. That does not
retract the decision: the dial exists, it is off, and a caller who wants one
label per kind of thing can still have it. It does mean the dial should be
described as a specialised tool rather than as a quality improvement anyone
should reach for by default.

This is five documents against one model, so it settles that constrained
decoding is not free here -- not that it loses everywhere.

### Re-measured after ADR 0031: the finding above was confounded

Both arms above ran with the model *thinking*.
[`0031`](0031-extraction-does-not-think.md) turned that off by default, and
the same comparison re-run against the new baseline is **identical in both
arms** -- 12 entity true positives, 3 false positives, 0 false negatives
either way, down to which types each document produced.

So the "enum as a checklist" mechanism explains nothing. The false positives
it was invented to account for were the reasoning trace inventing entities,
and they went away with the thinking rather than with the constraint. The
decision recorded above stands unchanged; only its rationale moves, from "the
dial costs something" to "the dial buys nothing measurable here".

**The lesson is about the reasoning rather than the flag.** A mechanism
inferred from a single measurement is a hypothesis, and this one was
persuasive enough to be written into this ADR, a BACKLOG entry and a
documentation warning before the confounder surfaced a day later. When a
result arrives with a satisfying story attached, the story is the part to
distrust: it is what stops you looking for the variable you did not control.
