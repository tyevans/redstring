# ADR 0010: One total order decides which mapping of a thing survives

**Status:** accepted, slice 8 of the ring migration (the `temporal` slot),
extended in slice 10 by consolidation composing over it.

**Why this is an ADR:** the decision is load-bearing and expensive to reverse.
Three call sites now defer to one order, and the moment a second definition
appears the library has two tie-breaks that can disagree about which mention of
an entity survives — a difference nobody would go looking for, in a durable,
replayable event log. Until now the whole argument lived in
`redstring/domain/preference.py`'s docstrings, which is the right place for
it to be *maintained* and the wrong place for it to be *found*. This records
it where a reader looking for decisions will look, together with the rule for
extending the order and the defects that shaped it.

## Context: three places have to pick a winner

One logical entity or edge gets described twice, routinely, and something has
to choose:

1. **Within one model answer.** `extraction/mapping.py` builds entities and
   relationships from a single `Extraction` and collapses mentions that map to
   the same id (`if existing is None or preference(built) > preference(existing)`).
2. **Across a document's chunks.** `extraction/merging.py` folds per-chunk
   results together. Overlapping windows report the same entity more than once
   on purpose, so this is the common path, not an edge case.
3. **Across parallel edges a merge would create.** `consolidation/planning.py`
   redirects edges onto a canonical entity, and two redirected edges can land
   on the same `(source, target, type)` signature — see
   [ADR 0004](0004-consolidation-emits-events.md) for why that resolution is
   emitted as events.

## Decision: the order lives in `domain`, below all three callers

`redstring/domain/preference.py` defines `preference` (entities) and
`relationship_preference` (relationships). Higher wins. All three call sites
import from there; none defines its own comparison.

`domain` is the bottom layer, so this placement is what makes sharing possible
at all — `consolidation` is a *sibling* of `extraction`, not above it, and the
tie-break moved down to `domain` precisely when consolidation became its third
caller.

## Two definitions are two tie-breaks

This is not a style preference. "Dedup within one model answer" disagreeing
with "dedup across chunks" about which mention wins produces a defect with no
symptom at the point of failure: the graph is merely *slightly wrong*, in a way
that depends on which stage saw the duplicate. It happened, and it cost a fix
round. One definition means the two stages cannot disagree, by construction.

## Both orders must be total, or arrival order decides

Each order ends in components that carry no meaning:

```python
return (
    entity.confidence,
    entity.temporal is not None,
    len(entity.description or ""),
    entity.description is not None,
    entity.description or "",
    entity.name,
    _temporally(entity.temporal),
    *_stably(entity.properties),
)
```

Only the first two slots are designed. The rest exist so that **no two distinct
objects compare equal** — because the moment two do, `>` is false, the incumbent
is kept, and the winner is decided by arrival order.

An earlier version compared confidence alone. Ties are the *common* case: every
mention the model declines to score carries the same default, so the same
document could map differently depending on the order the model happened to
list things in. `relationship_preference`'s predecessor was worse in the same
way — `(confidence, relationship_type)`, where the type is constant inside every
bucket, so the tuple degenerated to `(confidence,)`.

Totality is fragile in ways that read as harmless. `description or ""` maps
`None` and `""` onto the same value, so two mentions differing only in which
they carried tied on every field and fell through to arrival order. Hence the
`description is not None` slot, which looks like padding and is not. The
strengthened order-independence property in `tests/unit/extraction/test_merging.py`
found it on its first run with the minimal example `[("a", None)], [("a", "")]`.

`_stably` and `_temporally` do the same job for the free-form parts:
`json.dumps(..., sort_keys=True, default=repr)`, so two equal dicts built in
different key orders render identically, and a model-supplied value that is not
JSON-serialisable cannot raise a `TypeError` from deep inside a sort.

## Why totality is what makes a `>` -> `>=` mutant equivalent rather than live

cosmic-ray is what finds a partial order, and it finds it as a `>` mutated to
`>=` that survives. Both call sites keep the incumbent on a tie
(`if existing is None or preference(built) > preference(existing)`); `>=` takes
the challenger instead. **The two spellings can only differ on a tie** — on two
distinct objects that compare equal, which is precisely what a total order
forbids. So with a genuinely total order the mutant is equivalent, and with a
partial one it is a live defect that disagrees only on inputs no test happened
to use. Nothing about the mutant's *diff* tells you which case you are in.

That makes the survivor and the defect indistinguishable at the point of
triage, and this is not hypothetical: the partial order that compared
confidence alone had already survived once by looking like an equivalent
mutant. Reading a survivor here as "equivalent" is a claim about the order,
not an observation about the diff.

The resolution is to stop labelling and start asserting. Totality is stated as
a property rather than a belief, in
`tests/unit/extraction/test_merging.py` —
`test_two_mentions_of_one_entity_with_equal_preference_are_equal` and
`test_two_statements_of_one_edge_with_equal_preference_are_equal`. Each groups
generated mentions by `(id, preference(...))` and asserts every object sharing
a key is equal to the others. When it holds, the mutants are equivalent *by
argument*; when the order goes partial, the property fails rather than a
mutation run quietly reporting a survivor someone will label away.

Two things about how that property has to be built, both learned by getting it
wrong:

- **Each mention is mapped alone, then grouped.** Mapping them together is
  vacuous — `map_extraction` already deduplicates, so its output holds one
  entity per id and every group is a singleton, and no assertion about ties can
  fail. The first version of the property passed that way while the order was
  still partial. It is the CLAUDE.md shape where the input is built by the
  function under test.
- **It is scoped to what `map_extraction` actually produces**, which is the
  claim being made and no more. That scoping is what the three-group rule
  below makes checkable when a field is added.

So the survivor label is only honest if totality has been *argued* and,
better, asserted. That is why both docstrings carry the argument explicitly,
and why the rule below exists.

## The three-group rule for deciding whether a new `Entity` field joins the order

A field can be left out of the order only if two mentions in one id bucket
*cannot* disagree about it. There are exactly three reasons that can be true,
and a field that has none of them belongs in the order. Check a newly added
field against the reasons, not against the lists below — the lists are a
snapshot, and the worked example is what happens when someone checks against a
snapshot instead.

The bucket is what `entity_id_for` fixes: `tenant_id`, `source_id`,
`entity_type`, and the *normalized* `name`. Everything in the argument below
follows from that.

### Fixed by the caller

`tenant_id`, `source_id`, `extraction_method`, `model`. `_build_entity`
(`extraction/mapping.py`) takes all four as keyword-only arguments — `tenant_id`,
`source_id`, `method`, `model` — and `map_extraction` passes the same values to
every mention it builds. They vary per *call*, never within one, so two mentions
in a bucket cannot disagree. Safe to omit.

Two fields sit adjacent to this group and are fixed for a different reason,
which is worth separating because the reason is what generalises:

- `entity_type` is *not* a caller argument — it comes off the candidate, per
  mention — but it is an input to `entity_id_for`, so mentions that disagree
  about it land in different buckets. Fixed by the id, not by the caller.
- `id` is the bucket. Nothing to compare.

The distinction matters when a field moves. A caller-fixed field stops being
safe the moment some caller varies it within one document; an id-fixed field
stops being safe only if it leaves the id, which is a change to `entity_id_for`
and visible as one.

### Derived from fields the id already fixes

`normalized_name` and `blocking_keys`. Both are pure functions of what the
bucket already fixes, so two mentions in one bucket compute the same value and
cannot disagree. Safe to omit — **and this is the group to check first when
adding a field.** A derived field is safe; an independently-supplied one is
not, however strongly it correlates with something the id fixes.

`normalized_name` is the easy case: `_build_entity` sets it to
`normalize_name(candidate.name)`, which is literally the fourth argument
`entity_id_for` hashes. Same input, same function, same bucket.

`blocking_keys` needs the argument spelled out, and the way it goes through is
the part worth remembering. The field is `blocking_keys_for(built)`, a
`frozenset` of at most three keys from `prefix_key`, `entity_type_key` and
`soundex_key` (`domain/blocking.py`). None of the three reads `entity.name` —
each one calls `normalize_name` itself, deliberately, so that a key function
cannot produce a different answer for one name depending on which extractor
filled `normalized_name` in. That self-normalizing habit exists for blocking's
own reasons, and it is also what puts the field in this group: the bucket fixes
the *normalized* name, not the name, and a key function reading the raw string
would read something two mentions in one bucket can genuinely differ about.
`entity_type_key` normalizes too, over an `entity_type` the id fixes unmodified.

So the derivation is over `(normalize_name(name), entity_type)` — exactly the
two free inputs to `entity_id_for` — and the omission holds. It would stop
holding the moment a fourth key function read a per-mention field. Nothing
structural prevents that: `blocking_keys_for` takes the whole `Entity`, so a
key over `description` or `properties` would type-check, pass blocking's own
tests, and quietly make this field a thing two mentions can disagree about
while it still sits in the "derived" list.

Two things this group does *not* rest on, both of which look like they might:

- **Not where it is computed.** `blocking_keys` is filled by a second pass
  (`built.model_copy(update={"blocking_keys": blocking_keys_for(built)})`)
  rather than at construction, because `blocking_keys_for` takes an `Entity`.
  A field derived after the fact is still derived.
- **Not that the function is lossy.** `prefix_key` keeps five characters and
  `soundex_key` returns `None` for a name with no ASCII letters. Losing
  information is fine; the property being used is only that the function is
  *pure* and its inputs are fixed. Determinism is the whole claim.

### Never populated

`original_entity_type`, `external_ids`, `source_text`. `_build_entity`
(`extraction/mapping.py`) names none of the three, so every mention it produces
carries the field default — `None`, `{}`, `None` — and two mentions in one
bucket cannot disagree about a constant. Safe to omit.

This is the weakest of the three reasons, and the difference is worth being
explicit about. The first two groups are safe because of something *structural*:
a caller-fixed field cannot vary without a caller varying it within one call,
and a derived field cannot vary without its inputs varying, which the id
forbids. This group is safe only because of what the code happens not to do
today. Nothing enforces it, nothing fails when it stops being true, and the
field's own type already permits a value — `external_ids` is a `dict[str, str]`
with a mutable-looking default, not a field the domain has closed off. **A
field in this group is a promise about a code path, and code paths get wired
up.**

`external_ids` is the one to watch, because the code that populates it exists
already. `extraction/schema_org.py` reads `@id` and `sameAs` off a JSON-LD item
and builds an `external_ids` mapping from them. It emits **plain dicts**, not
`Entity` objects, and nothing routes those dicts through `map_extraction` — so
no entity the order has ever compared carried one. The field is empty by an
accident of plumbing, one adapter away from not being.

That matters because of what the day-it-is-wired-up failure looks like: two
JSON-LD mentions of one organisation, same name and same type so the same
bucket, differing only in `@id`. Same confidence, same description, same
properties, no temporal extent. They tie on every slot in `preference`,
including the tail that exists to make ties impossible, and the winner is
decided by which chunk the parser reached first. That is the
`description`/`None` tie again — the one the order-independence property caught
with `[("a", None)], [("a", "")]` — and it is exactly as invisible: the graph
is merely slightly wrong, differently on different runs.

`source_text` is quieter. `schema_org.py` mentions it and sets it to `None`
unconditionally, so even wiring that path up would not populate it; it stays in
this group until some extractor records the span it came from, which is the
obvious thing for one to want to do. `original_entity_type` is set by nothing
that builds an entity — the Neo4j adapter round-trips it, which is persistence
of a field, not population of one.

So the rule for this group is narrower than for the other two: **when you wire
up a path that fills one of these fields, the same change adds a slot to the
order** — or argues, in the docstring, why two mentions in one bucket still
cannot disagree about it. The order goes partial in the commit that populates
the field, not in some later one, and by then the mutation survivor it produces
reads like an equivalent mutant.

### What is left is the order

`confidence`, `name`, `description`, `properties`, `temporal` — the five things
two mappings of one entity can genuinely disagree about — and all five appear
in `preference`. `name` is in it because the id fixes only the *normalized*
name: "Ada Lovelace" and "ada  lovelace" share a bucket and are distinct
objects.

### Worked example: `temporal` moving out of "never populated" in slice 8

`temporal` was in the third group until extraction began parsing the model's
temporal expression into a `TemporalExtent`. It is the second field to leave
that group (after `blocking_keys`) and, unlike `blocking_keys`, it is **not**
derived from anything the id fixes: overlapping windows report one entity twice
and only the window containing the date phrase can date it, so two mentions in
one bucket disagree about it routinely.

An earlier version of the docstring listed `blocking_keys` as never populated.
That had already stopped being true, and the conclusion survived only because
the field happened to be derived — the reasoning was wrong and the answer was
right, which is the failure mode a list produces and a rule does not. The
three-group form is stated so the next field is checked against a reason.

The fields the order compares are described in
[Domain value types](../reference/domain-value-types.md); the operational
consequence of a bucket resolving one way rather than another is in
[Consolidate duplicate entities](../how-to/consolidate-duplicate-entities.md).

## Deliberate slots vs. tail slots, and why placement matters (`temporal` above description length)

Only the first two slots of `preference` are *designed*. Everything after them
is tail: it exists so that no two distinct objects compare equal, and which way
it happens to resolve a comparison carries no meaning. The distinction is not
cosmetic, because the two kinds of slot answer different questions. A deliberate
slot answers "which mention should win"; a tail slot answers only "answer the
same way every time".

That makes **position** the entire content of a deliberate slot.
`entity.temporal is not None` sits **above** `len(entity.description or "")`,
and the ordering is the whole decision:

```python
(entity.confidence,)
(entity.temporal is not None,)
(len(entity.description or ""),)
```

A date appears in one window. A description appears in every window that
mentions the entity at all, usually longer in the one with more surrounding
text. Below description length the flag would be unreachable whenever two
mentions describe the entity differently — the common case — so the fuller
description would win and the date would be discarded. The asymmetry is what
settles it: a lost description costs a sentence the next chunk mostly repeats,
and a lost date cannot be recovered from anything else in the payload. The
placement is paid for in exactly that currency, visibly, in
`test_a_dated_mention_outranks_a_better_described_undated_one`
(`tests/unit/extraction/test_temporal_enrichment.py`), which asserts the winner
keeps the *shorter* description `"A battle."` over
`"A battle fought in the south of England, at some length."`.

### A deliberate slot needs an input where it is reachable

The first version had the flag below description length, and the test covering
it could not tell the difference: both mentions were undescribed, so the
comparison fell through to the tail, where `_temporally`'s `""` for "no extent"
sorts below any real rendering and the dated mention won anyway. **Deleting the
flag entirely left that test green.** The sibling test
`test_a_dated_mention_beats_an_undated_one_regardless_of_order` is that test,
kept deliberately and docstringed as proving nothing about placement — it states
the order-independence claim, not the precedence one.

This is the CLAUDE.md failure shape in its positional form, and it is worth
naming separately because the usual remedies do not reach it. The input did not
have to be exotic; it had to make the *earlier* slots tie. A tail slot happening
to agree with a deliberate slot is the normal case rather than a coincidence —
absent sorts below present in both — so a test that never reaches the deliberate
slot passes for a reason that has nothing to do with the design. **To test where
a slot sits, force every slot above it to tie and every slot below it to
disagree.** Here that means two mentions with equal confidence, one dated and
tersely described, one undated and described at length, run in both arrival
orders.

### Tail slots buy determinism and nothing else

Which of two *dated* mentions wins is arbitrary: both read the same document and
neither is more authoritative, so `_temporally`'s stable rendering is not a
judgement about extents. It only ensures the same run twice gives the same
graph. Reading a tail slot as a preference is a mistake in the other direction —
it invites someone to "improve" the rendering, and the only property it has to
keep is that distinct extents render distinctly.

The practical rule when adding a slot: decide first which kind it is. A
deliberate slot goes at a position you can argue for and needs an input where
that position is reachable; a tail slot goes at the end and needs only to be
injective. The three-group rule says whether a field belongs in the order at
all — this says where.

## Scope: total *within an id bucket*, and how consolidation composes rather than redefines

Both orders are total **within an id bucket**, where the fields feeding the id
are fixed by construction. `relationship_preference` in particular does not
distinguish two edges with different ids; inside extraction it never has to,
because the id is what defines the bucket.

Consolidation is the case where that is not enough, because two edges competing
for one signature are genuinely distinct rows with distinct ids. It **composes**:

```python
def duplicate_preference(relationship: Relationship) -> tuple[float, int, str, str]:
    return (*relationship_preference(relationship), str(relationship.id))
```

Composition is what keeps this a fourth caller rather than a second
definition. `duplicate_preference` adds a slot; it does not restate confidence
or properties, so it cannot come to a different view of them than extraction's
two deduplications do. A consolidation-local order over the same fields would
have been a handful of lines and would have re-created exactly the divergence
this ADR exists to prevent — the difference being invisible, because both
orders would agree on almost every input.

The id is appended as its canonical lowercase hyphenated string — the same
rendering `GraphStore`'s cursor order uses (`str(entity.id)` in the memory
adapter's keyset sort) — so "which duplicate survived" and "which page an
entity fell on" cannot disagree about how two UUIDs compare. UUID ordering and
lexicographic ordering of the hyphenated form are not the same relation, and
one module comparing objects while another compares strings is the sort of
mismatch nothing surfaces until a page boundary lands between two duplicates.

The direction of what the id buys is worth stating plainly: it makes the order
total, and nothing else. Which of two competing edges has the lower id is
arbitrary — it is a tail slot in the sense of the section above, bought for
determinism. See
[Consolidate duplicate entities](../how-to/consolidate-duplicate-entities.md)
for the operational side.

## `relationship_preference`: the shorter totality argument

```python
return (relationship.confidence, *_stably(relationship.properties))
```

Two designed-looking slots and no tail, next to `preference`'s eight. The
brevity is not a lesser standard — it is the same three-group argument reaching
its conclusion sooner, because `Relationship` has exactly seven fields
(`id`, `tenant_id`, `source_entity_id`, `target_entity_id`, `relationship_type`,
`properties`, `confidence`) and five of them are fixed inside a bucket.

Enumerating them is the whole proof, and it fits in a sentence per group:

- **The id is the bucket.** Nothing to compare.
- **Fixed by the id.** `source_entity_id`, `target_entity_id` and
  `relationship_type` are the three inputs `_relationship_id_for` hashes
  (`uuid5` nested source → target → type, in `extraction/mapping.py`), so two
  edges disagreeing about any of them land in different buckets.
- **Fixed by the caller.** `tenant_id`, set once per `map_extraction` call.
- There is no "never populated" group at all — `Relationship` has no field
  extraction leaves at its default — which is why this order has no group that
  decays and `preference` does.

What is left is `confidence` and `properties`, and both are in the order. That
is what makes it total, and the enumeration is the argument: a `>` → `>=`
mutant over `relationship_preference` is equivalent because the count comes out
even, not because no test happened to notice.

`_stably` is doing the same job here as in `preference`: size first, then a
canonical `json.dumps(..., sort_keys=True, default=repr)`, so two property bags
of equal size that are not equal still compare unequal. Confidence alone would
not be total, and the order this replaced was worse than that.
`(confidence, relationship_type)` looks like two slots and is one — the type is
constant inside every bucket by the argument above, so the tuple degenerated to
`(confidence,)` while reading as though it had a tie-break. Ties are then the
common case, because every edge the model declines to score carries
`DEFAULT_CONFIDENCE` and overlapping windows manufacture duplicate edges on
purpose. Which `properties` survived was decided by arrival order, in a
durable, replayable log.

**A slot that cannot vary is worse than no slot**, and this is the shape to
watch for when extending either order: it satisfies review, it makes the tuple
longer, and it leaves the order exactly as partial as it was. Check a proposed
slot against the groups — if it is fixed by the id or by the caller, adding it
buys nothing.

Totality is asserted rather than argued in
`test_two_statements_of_one_edge_with_equal_preference_are_equal`
(`tests/unit/extraction/test_merging.py`), which groups generated statements by
`(edge.id, relationship_preference(edge))` and requires everything sharing a key
to be equal. Like its entity counterpart it maps each statement alone before
grouping: `map_extraction` already deduplicates, so grouping its output makes
every group a singleton and the property vacuous.

For consolidation the same enumeration runs one step further, and lands one
slot short. The competitors there are distinct rows with distinct ids, so `id`
stops being the bucket and becomes a seventh field that can vary — which is
exactly the gap `duplicate_preference` closes by appending it. The other five
still hold: the signature *is*
`(source_entity_id, target_entity_id, relationship_type)`, and `tenant_id` is
fixed for two independent reasons — `get_relationships_for` is tenant-scoped,
and `RelationshipRedirection`'s validator refuses a cross-tenant move
(`domain/consolidation.py`). Either alone would do; both being true is why that
step of the argument does not depend on a caller keeping a habit.

## Consequences

**Adding a field to `Entity` or `Relationship` is now a decision about the
order.** Run it through the three reasons a field can be omitted — fixed by the
caller, derived from what the id fixes, never populated. A field with none of
them belongs in the order, and the same commit that adds it adds the slot.
This is a real cost paid deliberately: it is a step in a review that would
otherwise be "add a field", and it is the step that stops the order going
partial one field at a time.

**Wiring up a code path can make the order partial without touching
`preference`.** That is the "never populated" group decaying, and it is the
only one of the three that can fail from a distance. `extraction/schema_org.py`
already builds `external_ids`; the day something routes its output through
`map_extraction`, two mentions differing only in `@id` tie on every slot. The
consequence to internalise is that **the diff which breaks totality need not be
in `domain/`**, so the three-group check belongs to whoever wires the path, not
to whoever last edited the order.

**The tail is unreadable on purpose, and every slot in it was bought.**
`description is not None` next to `description or ""` reads as redundancy and
is the fix for a real tie. Shortening the tail on grounds of tidiness
re-introduces arrival-order dependence, in a durable, replayable log, with no
symptom at the point of failure.

**Placement of a designed slot is a claim, and needs an input where it is
reachable.** `temporal is not None` above description length is the only such
claim currently made, and testing it required two mentions tying on
confidence and disagreeing on description — a tail slot agreeing with a
designed one is the normal case, not a coincidence, so the obvious test passes
whatever the placement.

**Totality is asserted, not argued.** Two properties in
`tests/unit/extraction/test_merging.py` group mapped mentions and statements by
`(id, preference(...))` and require everything sharing a key to be equal. That
converts a `>` → `>=` survivor from a judgement call into a decided question:
with the properties green the mutants are equivalent, and a partial order fails
a test rather than producing a survivor someone labels away. The properties
have to map each mention *alone* before grouping — `map_extraction` already
deduplicates, so grouping its output makes every group a singleton and the
assertion vacuous.

**`domain` is where this has to live.** Moving it up to `extraction` would put
it out of reach of `consolidation`, which is a sibling and not a dependant —
the shape that produced two tie-breaks the first time. The cost is that
`domain/preference.py` cannot see any of its callers, so its docstrings carry
the whole argument and this ADR is what makes it findable.

**Extending is composing.** `consolidation/planning.py` appends `str(id)` to
`relationship_preference` rather than restating confidence and properties, so a
fourth caller cannot come to a different view of the fields the first three
share. Any fifth caller should do the same.

**Property-level merging is a separate mechanism, deliberately.** The order
picks a whole object: the winner keeps its own `properties` and the loser's are
discarded. Reconciling values across the objects a *merge* combines is
`domain/merge_strategy.py`, which raises rather than falling back on anything
it cannot answer (BACKLOG B28). Extraction has no equivalent, and giving it one
would be a new decision, not an extension of this one.

**The claim order composes with this one rather than competing with it** — see
[`0035` provenance is a value object](0035-provenance-is-a-value-object.md).
`merge_strategy` now ranks a single property's *claims* on a tuple of its own,
appending the origin id for totality exactly as `duplicate_preference` appends
`str(id)` above. That is the composition rule of the previous paragraph applied
to a narrower subject, not a second answer to this ADR's question: the fields
it ranks on belong to the observation and the fields ranked here belong to the
entity, and neither order can be expressed in the other's terms. **A future
edit that makes one call the other is the defect this ADR exists to prevent,
arriving from the side.**

The fields this order reads have moved without changing: `confidence`,
`source_id`, `extraction_method` and `model` are reached through
`entity.provenance` rather than off `Entity` directly, so the tuples quoted in
the Decision above are spelled differently in the source and rank identically.
There is no forwarding property, deliberately — a second way to spell the same
read is a second declaration site.

The value types the order compares are described in
[Domain value types](../reference/domain-value-types.md); what a resolved
bucket means operationally is in
[Consolidate duplicate entities](../how-to/consolidate-duplicate-entities.md);
and [ADR 0004](0004-consolidation-emits-events.md) records why consolidation's
use of the order is emitted as events rather than written directly.
