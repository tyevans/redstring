# ADR 0002: Two store ports, and the absence of `delete_entity`

**Status:** accepted, slices 3-5 of the ring migration; scope extended through
slices 6-7 to cover the alias surface on `GraphStore` and the closure of
`BACKLOG` B34.

The extension is deliberate rather than a later amendment. The no-`delete_entity`
argument below was always conditional on something keeping the merge fact
durable, and slices 6-7 supplied it: `upsert_alias`, `remove_alias`,
`find_aliases` and `resolve_entity_ids` on the port
(`src/redstring/ports/graph_store.py`), resolve-before-write in the extraction
fold (`src/redstring/projections/graph.py`), and an alias tier in
`tests/compliance/graph_store.py`. Read the alias methods as part of this
decision, not as news arriving after it — the ADR is not settled without them,
and B34 is closed by them. B32 remains open and is unaffected.

See also [ADR 0009 *The extraction fold resolves through aliases*](0009-the-extraction-fold-resolves-through-aliases.md) for the fold's
side of the obligation, ADR 0006 *The public surface is gated* for what
exporting `AliasCycleError` commits us to, and
`docs/how-to/implement-a-store-adapter.md` for what an adapter must now
implement.

**Why this is an ADR:** a port is the one interface that is expensive to
change, because every adapter and the compliance suite move with it. Three
adapters implement `GraphStore` today. The absent method in particular keeps
being proposed for symmetry, and the argument against it is not obvious from
reading the Protocol.

## Context

Before the migration, persistence was SQLAlchemy models plus a service layer
that reached into them, and "the graph" was a 443-line Neo4j client with no
callers. Storage decisions and domain decisions were the same code.

## Decision: two ports, `GraphStore` and `VectorStore`, not one

They are separated because their contracts differ in kind, not in degree.
`GraphStore` is exact: a write is visible to the next read, filters are
predicates, and results are sets. `VectorStore` is approximate by nature —
`search` returns the `k` nearest, and "nearest" is a property of an index that
a real backend is entitled to approximate.

Merging them would force one compliance suite to state both contracts, and the
weaker one wins: an exactness assertion that some adapters may fail is not a
contract. Keeping them apart let `tests/compliance/vector_store.py` grow a
separate *recall* tier, which states the weaker property honestly.

**A capability flag was explicitly rejected.** An `is_approximate` on the port
would let each adapter opt out of whichever assertions it fails, which is how
two adapters quietly stop being interchangeable while both "pass the suite".

## Decision: there is no `delete_entity`, and there will not be one

This is the part that gets re-proposed. The reasoning, in the order it was
found:

1. **Nothing needs it.** The obvious caller is a merge — but a merge does not
   delete the absorbed entity. It records an `Alias`, and the store keeps that
   alias.

   The write is `upsert_alias`. `_apply_merge` in
   `src/redstring/projections/graph.py:155` calls it once per absorbed id,
   before it touches any relationship redirection, carrying the absorbed
   entity's name and normalized name across so the merge fact survives even
   though the entity is no longer reachable as itself. Keyed on
   `(tenant_id, alias_entity_id)` and idempotent, so redelivery of
   `EntitiesMerged` rewrites the same row rather than accumulating rows —
   which is the same at-least-once argument as reason 3 below, applied to the
   thing a delete would otherwise have destroyed.

   Keeping that row is only worth doing because two reads make it useful.
   `resolve_entity_ids` is the load-bearing one: it maps a batch of ids to
   their canonical ids, totally (an id that was never merged, or is not known
   at all, maps to itself) and transitively. It has two production callers,
   and they are the two places a stale id would do damage —
   `projections/graph.py:140`, resolving both endpoints of every relationship
   before an extraction fold writes it, and `consolidation/candidates.py:170`,
   dropping candidates that have already been merged away so consolidation
   does not propose a merge `ConsolidationLog` would refuse. `find_aliases` is
   the other direction — the aliases absorbed *directly* into one canonical
   id, ordered by alias id — and it is what makes a merge inspectable after
   the fact: which ids a canonical entity swallowed, under what reason, at
   what time. Today it is exercised by the compliance tier and by the
   consolidation oracle rather than by library code, and that is fine; it is
   the read that would otherwise require reconstructing history from the
   event log.

   So "nothing needs `delete_entity`" is not an absence of demand. The demand
   exists, and it is served by a write that records the merge and two reads
   that consult it. A delete would satisfy the same surface request by
   destroying the input both reads run on.

2. **Deleting would break replay.** `DocumentExtracted` folded after
   `EntitiesMerged` writes the pre-merge endpoints back and silently reverts
   the merge — in strict log order, with every event delivered exactly once, no
   race required. The fix is that the extraction fold resolves each endpoint
   through the alias table before writing, which requires the alias to still be
   there. That was `BACKLOG` B34, and it is **closed** — by
   `upsert_alias`/`remove_alias` keeping the merge fact durable on the port
   (`src/redstring/ports/graph_store.py:146,160`) and by
   `_resolve_endpoints` in `src/redstring/projections/graph.py:139` putting
   every edge's endpoints through `resolve_entity_ids` before the extraction
   fold writes them. The mechanism is what closed it, so removing any of the
   three re-opens it.

### 2a. Where the argument is pinned now

Reason 2 used to cite `tests/unit/projections/test_known_gaps.py`. **That file
was renamed, not removed**, and the argument it carried is now split across
three live files.

- `tests/unit/projections/test_aliases_survive_re_extraction.py` is the
  renamed file. It builds the same three-event sequence — `DocumentExtracted`
  for `doc-1` under one model, `EntitiesMerged` folding `e1` into `e0`, then
  `DocumentExtracted` for the same document under a newer model, writing the
  pre-merge endpoints again — in strict order, each event delivered once.
  What changed is the assertions, which were **inverted rather than deleted**:
  where the file once asserted the merge was reverted, `TestALaterExtractionCannotRevertAMerge`
  now asserts it survives (`test_a_re_extraction_preserves_the_merge`), that
  the absorbed entity is still present and still resolves
  (`test_the_absorbed_entity_still_exists_and_resolves`), and that replay
  reproduces the corrected state exactly. `TestAnEdgeThatCollapsesOntoOneEntity`
  covers the self-loop case in the same shape, and
  `TestTheEventStoreWasNeverTheProblem` keeps the original diagnosis on the
  record: the log always held both extractions and the merge, so the defect
  was in the fold, never in the log. Keeping the sequence and flipping the
  assertions is what makes the fix's *scope* checkable; a deletion would have
  left the sequence untested and B34 closed on assertion alone.
- `tests/unit/projections/test_replay_equivalence.py` generalises it. Its
  `PINNED` scenarios include `merge`, `dropping-merge`, `undo`,
  `undo-of-dropping-merge` and `two-tenants` — the merge-adjacent shapes an
  alias delete would break — and each is checked against an independently
  recorded oracle, wiped and replayed, and delivered twice.
- `tests/unit/projections/test_replay_coverage.py` enumerates them, deriving
  the event list from `KG_EVENT_TYPES` by introspection and failing if any
  event type no `PINNED` scenario emits. So the pinning above cannot be
  quietly narrowed: dropping the merge scenarios makes that gate red rather
  than shrinking the evidence unnoticed.

Citing a path is a commitment to keep the citation live — see the closing note
on citation hygiene.

### 2b. Two more reasons, found after the fix

Reason 2 is enough on its own, but implementing it surfaced two behaviours
that a delete would break independently. Both are consequences of the same
thing: the absorbed entity's alias row is not a record of history, it is an
input two later writes read.

**Alias chains form, and resolution is transitive.** `ConsolidationLog`
refuses to merge *into* an alias — which is what stops cycles — but it does
not refuse to merge a canonical entity away afterwards. So `B -> A` followed
by `A -> C` is a legal pair of merges, and `B` must resolve to `C`, through
`A`'s row (`src/redstring/ports/graph_store.py:181`). Delete `A` when the
second merge absorbs it and the walk loses its middle hop: `B` resolves to
`A`, an entity that no longer stands for anything. Note the asymmetry with
`find_aliases`, which is deliberately *direct* — `find_aliases(C)` gives `A`
alone — because an undo asks what *this* merge absorbed. The two reads want
opposite things from the same rows, and both want the rows.

The compliance tier pins the transitive half three deep rather than two
(`test_resolution_follows_a_chain_to_the_end`, `tests/compliance/graph_store.py:1441`),
for the reason `CLAUDE.md` names: at depth two, "follow one hop" and "follow
to a fixed point" agree, so `d -> c -> b -> a` is what separates them.

**An edge whose endpoints resolve together is deleted, not upserted.** When
one merge absorbs both ends of a relationship, resolution collapses them onto
the same id, and `Relationship` rejects a self-loop outright — so there is no
value the resolver could return that means "and this one must go". `_resolved`
in `src/redstring/projections/graph.py:139` deletes the edge in place and
drops it from the batch; it is the only path through the resolver that does
not end in an upsert. That decision is reachable only because the aliases are
still there to collapse the endpoints. Without them a re-extraction writes the
pre-merge edge back between two entities the merge already dissolved.

`TestAnEdgeThatCollapsesOntoOneEntity` in
`tests/unit/projections/test_aliases_survive_re_extraction.py:214` pins it,
and it is worth reading for its second assertion: it checks `report.failed == 0`
alongside the absent edge, because a fold that *tried* to write the self-loop
would raise and the edge would be equally absent. Only the failure count tells
"deleted" from "poisoned". It also carries a control (`test_the_merge_drops_the_edge`)
so the re-extraction test cannot pass because the edge was never written.

3. **A delete-then-insert projection is not idempotent.** Making the fold
   delete a document's entities before reinserting them would make redelivery
   destructive: the delete half of a redelivered event removes entities a later
   event added. At-least-once is the normal bus guarantee.

`delete_by_tenant` covers bulk removal, which is what replay and test teardown
actually want. The consequence is recorded honestly rather than hidden: a
re-extraction that finds *fewer* entities than the previous run leaves the
dropped ones in the graph forever, so the graph converges on the union of every
run rather than on the latest one. That is `BACKLOG` B32, still open.

## Decision: aliases are part of the port, not projection-side hygiene

The four alias methods could have lived above the port — a merge map held by
the projection, or a side table an adapter knew nothing about. They are on
`GraphStore` because **a later write has to consult the merge fact**, and only
the store is present at the moment that write happens.

That is the whole argument, and it is what turns the previous decision from a
preference into a constraint. "There is no `delete_entity`" is aspirational as
long as nothing depends on the absorbed row: a projection that quietly dropped
it would pass every test about entities and relationships, because the damage
only shows up in the *next* extraction fold. Put resolution on the port and the
dependency becomes structural — `_resolve_endpoints` cannot write an edge
without calling `resolve_entity_ids`, `resolve_entity_ids` cannot answer
without the alias rows, and an adapter that discards them fails the compliance
tier rather than a code review. The port docstring
(`src/redstring/ports/graph_store.py:20-32`) states this in the same terms,
deliberately: the no-delete rule and the alias surface are one decision written
in two places.

It is worth being explicit about what this is *not*, because the objection is
reasonable and will recur. It is not consolidation logic leaking downward. The
store decides nothing about *whether* two entities are the same — that is
`ConsolidationLog` on the write side, and the alias arrives as a fact that has
already happened, emitted by `EntitiesMerged`. What the store gains is
somewhere to put that fact and a way to read it back. The asymmetry is the
tell: there is no `merge_entities` on the port, only `upsert_alias`, which
records a decision taken elsewhere.

The alternative — keeping the map in the projection — fails on rebuild. A
projection is derived and disposable; wiping the store and replaying is the
supported operation (`delete_by_tenant`, above). A merge map living beside the
store would have to be wiped and rebuilt in step with it, by hand, and any
adapter or deployment that forgot would silently reinstate B34. Holding the
aliases *in* the store makes "the projection" one thing that replay restores
atomically.

Two consequences follow, and they are the subsections below: the port must say
what each method promises precisely enough that two adapters agree
(`docs/how-to/implement-a-store-adapter.md` is the implementer's side of it),
and resolve-before-write has to be stated as an obligation of the port rather
than a habit of the current callers.
[ADR 0009 *The extraction fold resolves through aliases*](0009-the-extraction-fold-resolves-through-aliases.md)
carries the fold's half of that contract.

### The four methods

Each of the four carries one decision that an adapter could plausibly get
wrong, and the port states it rather than leaving it to be inferred
(`src/redstring/ports/graph_store.py:146-200`).

**`upsert_alias`** is idempotent and last-write-wins, keyed on
`(tenant_id, alias_entity_id)` — the absorbed id, not the canonical one. That
key is the store-side shape of `ConsolidationLog`'s double-merge rule: an
entity has at most one canonical parent, and re-delivering `EntitiesMerged`
rewrites the same row rather than accumulating rows. The compliance tier pins
it from the other side (`test_an_alias_is_keyed_by_the_absorbed_entity`,
`tests/compliance/graph_store.py:1465`), asserting the *earlier* canonical no
longer lists the alias — keyed on the canonical instead, both rows would
survive and the assertion would fail.

The clause worth arguing for is that **neither endpoint needs to exist**. An
alias is a statement about ids, and requiring the entities would make the merge
fold depend on the extraction fold having already run — the exact ordering
assumption aliases exist to remove. This is a deliberate asymmetry with
`upsert_relationship`, which does raise `MissingEntityError` on a missing
endpoint: an edge is a claim about two things in the graph, an alias is a claim
about identity, and only the former is meaningless without its endpoints.

**`remove_alias`** returns `bool` rather than raising, and the return type is
the decision. `_apply_unmerge` in `src/redstring/projections/graph.py:203`
calls it once per unmerged id and ignores the result; under at-least-once
delivery the second copy of `EntitiesUnmerged` finds nothing to remove, and a
method that raised there would turn ordinary redelivery into a poisoned event.
Same reasoning as reason 3 above, applied to a removal rather than a write.
`test_removing_an_alias_restores_the_identity`
(`tests/compliance/graph_store.py:1476`) asserts both returns — `True` then
`False` — because an adapter returning a constant would otherwise satisfy the
half of the contract anyone thinks to check.

**`find_aliases`** is **direct, not transitive**, and ordered ascending by
`alias_entity_id` as its canonical lowercase hyphenated string. After
`B -> A -> C`, `find_aliases(C)` is `[A]` alone. That looks like a weaker
answer than resolution gives, and it is the answer the caller wants: an undo
asks what *this* merge absorbed, and a transitive result would make that
unanswerable. The ordering is contract rather than convenience for the same
reason `find_entities`'s is — two adapters must agree — and
`test_find_aliases_is_direct_and_ordered` (`tests/compliance/graph_store.py:1487`)
inserts the higher id first so an adapter returning insertion order fails.

**`resolve_entity_ids`** is the load-bearing one, and it is batched, total and
transitive.

- *Batched* because the caller is a fold resolving both endpoints of every
  edge in a document; as a loop it is two round trips per edge.
- *Total* because every requested id appears in the result: an id that was
  never merged maps to itself, and so does an id this tenant has never seen.
  Resolution answers "has this been merged away", not "does this exist" — the
  existence question is `get_entity`'s, and conflating them would put a
  `None` in the map that every caller would have to branch on.
- *Transitive* because chains form. `ConsolidationLog` refuses to merge *into*
  an alias, which is what stops cycles, but nothing stops a canonical entity
  being merged away afterwards, so `B -> A` then `A -> C` is legal and `B`
  must resolve to `C`. The compliance test walks three hops
  (`test_resolution_follows_a_chain_to_the_end`,
  `tests/compliance/graph_store.py:1441`): at two hops, "follow one hop" and
  "follow to a fixed point" are the same function.

The two reads pull in opposite directions over the same rows — one deliberately
stops at the first hop, the other deliberately does not — which is why both are
on the port and neither is expressible as a filter over the other.

Both reads are also subject to the suite's standing obligations: mutation
isolation (`test_find_aliases_returns_copies`) and tenant isolation
(`test_find_aliases_never_crosses_tenants`,
`test_resolve_entity_ids_never_crosses_tenants`), enforced by introspection rather than
by anyone remembering — see the coverage-gate decision below.
`test_deleting_a_tenant_takes_its_aliases` closes the loop with
`delete_by_tenant`: aliases surviving a wipe would have a rebuild replay merges
over rows the replay did not create.

### Resolve-before-write is a contract obligation

The port does not merely *offer* `resolve_entity_ids`. **A write that carries
entity ids which may predate a merge must resolve them first**, and that is
stated by the port rather than left as a habit of the current callers
(`src/redstring/ports/graph_store.py:20-31`: "`resolve_entity_ids` is the read
a fold makes before writing an edge"). A fold that writes such an edge without
resolving is a defect against this ADR, not a style preference.

Stating it at the port is the whole point. The alternative is that each caller
remembers — and the reason B34 existed is that the fold could not have
remembered: before the alias surface there was nothing to consult. Now there
is, so "did you resolve?" is a question with a definite answer at every call
site, and the two production call sites are the two places a stale id does
damage:

- `src/redstring/projections/graph.py:140` — `_resolved` collects both
  endpoints of every relationship in a `DocumentExtracted`, makes one
  `resolve_entity_ids` call for the whole document, and rewrites the edges from
  the returned map before `_apply_extraction` upserts them
  (`graph.py:112-117`). This is the obligation's original case: extraction data
  can predate a merge and does not know it.
- `src/redstring/consolidation/candidates.py:170` — the same call, used to
  *exclude* rather than to rewrite. A candidate that has already been merged
  away cannot be merged again — `ConsolidationLog` refuses it — so proposing it
  produces a candidate nobody can act on. One resolution call for the whole
  block, and the comparison is `==` rather than `is`, pinned by
  `test_resolution_by_value_not_by_identity` after a cosmic-ray mutant survived
  the `is` spelling: both adapters happen to hand back the same `UUID` object,
  so `is` passes everything here and returns an empty candidate list against
  any adapter that rebuilds ids.

That second caller is what makes this an obligation of the *port* rather than a
detail of the extraction fold. Two layers that never import each other reached
the same conclusion from opposite directions — one rewrites stale ids, one
drops them — and the only thing they share is the port. A rule living in
`projections/` would not have reached `consolidation/`.

**Not every write resolves, and the exceptions are the argument, not
counter-examples.** `_apply_merge` upserts `redirection.after`
(`graph.py:186`) and `_apply_unmerge` upserts `restored_relationships`
(`graph.py:204`) without resolving, deliberately: both carry endpoints computed
by the write model *for this merge*, so they are post-merge by construction and
resolving them would be a no-op at best. The obligation is scoped by whether the
ids could predate a merge the store already knows about, which is a property of
where the ids came from — and `_apply_unmerge` carries an in-code note saying
so, because the surrounding code makes it look as though ordering should matter
there. Widening the rule to "resolve everything, always" would be easier to
state and would hide that distinction; the cost of the narrower rule is that it
has to be written down, which is what this section is.

What the obligation buys is a *structural* dependency in place of an
aspirational one. `_resolved` cannot write an edge without calling
`resolve_entity_ids`; `resolve_entity_ids` cannot answer without the alias rows;
an adapter that drops those rows fails the compliance tier. That chain is what
turns "there is no `delete_entity`" from a preference into a constraint — see
the decision above, and
[ADR 0009 *The extraction fold resolves endpoints through the alias table*](0009-the-extraction-fold-resolves-through-aliases.md)
for the fold's half of the contract, including why a resolver
that collapses an edge's endpoints deletes it rather than upserting.

`docs/how-to/implement-a-store-adapter.md` is the implementer's side: an adapter
owes a correct, total, transitive `resolve_entity_ids` because callers above it
are contractually required to depend on one.

### Termination is the adapter's job, not the write model's

A cycle in the alias table is unreachable through legal history. A cycle needs
some merge to name an entity that is *already* an alias as its canonical, and
`ConsolidationLog` refuses precisely that. So the walk in `resolve_entity_ids`
terminates — as an argument about the write model.

**The port requires adapters to bound it anyway**
(`src/redstring/ports/graph_store.py:195-201`). That is the
"bound any loop whose exit depends on adapter-supplied data" rule from
`CLAUDE.md`, and resolution is exactly its shape: the loop's exit condition
comes from rows the adapter read, not from anything the fold computed. A store
whose rows are corrupt — a bad migration, a hand-edited row, a partially
applied write — turns a walk that trusts them into a hang. **A hang is worse
than an error**: in CI it reads as infrastructure trouble and gets retried
rather than investigated, so the one signal that would name the corrupt tenant
is the one that never appears. `AliasCycleError`
(`src/redstring/domain/exceptions.py:51`) carries `entity_id` and `tenant_id`
for that reason; it is the cheap half of the trade, and it is public, so
ADR 0006's gates apply to it.

The two adapters bound the walk differently, and the difference is instructive
about what the port is actually asking for.

- **In-memory** (`src/redstring/graph/adapters/memory.py:162`) walks the dict
  hop by hop with `limit = len(aliases) + 1` and raises from the `for`/`else`.
  The bound is the tenant's alias count rather than a visited set: a walk longer
  than every alias in the tenant has necessarily revisited a node. The `+ 1` is
  load-bearing and is pinned as its own test
  (`test_the_longest_legal_chain_still_resolves`,
  `tests/unit/graph/test_memory_store.py`) — a chain using *every* alias is the
  worst legal history, and `len(aliases)` alone would reject it, converting the
  longest correct case into a spurious error.
- **Neo4j** (`src/redstring/graph/adapters/neo4j.py:459`) does not loop in
  Python at all. The chain is a variable-length match, and Cypher's
  relationship-uniqueness rule is what terminates it — a cycle simply yields no
  chain end. The bound is therefore free, but the *detection* is not: an empty
  result is indistinguishable from "not an alias" unless you ask separately, so
  the query returns `is_alias` alongside `canonical`, and an id with an outgoing
  alias edge and no chain end raises. Without that second column the adapter
  would answer "resolves to itself" for a cycle — terminating, and silently
  wrong.

Both are bounded; neither borrows the write model's guarantee. That is the
point of putting the requirement on the port rather than in a comment next to
`ConsolidationLog`: an adapter author reading the Protocol has no view of the
write model, and "this cannot happen" is not a property an adapter can verify
about data it was handed.

Two cycle lengths are tested, not one
(`test_a_two_cycle_raises_rather_than_hanging` and
`test_a_three_cycle_raises_too`, `tests/unit/graph/test_memory_store.py:101,113`).
The three-cycle is not redundant: the obvious cheap guard — comparing each hop
against the id you started from — catches `A -> B -> A` and misses
`A -> B -> C -> A`, so a suite with only the two-cycle admits an implementation
that hangs on the first real corruption. This is the `CLAUDE.md` table's shape
again, one row shorter: the shorter input makes a correct bound and a
self-referential check agree.

## Decision: the compliance suite is the contract

`tests/compliance/` is not a convenience. It is the only artifact that says
what a port means, and the migration produced repeated evidence that the
Protocol alone does not:

- Slice 5 found two adapter divergences in validation paths — pgvector
  deduplicating before validating where in-memory validated everything, and
  in-memory raising `TypeError` on an unhashable stored `entity_type` where
  pgvector returned `[]`. Both were invisible to the suite as written, for
  input-shape reasons; both are now covered.
- Slice 3 injected 28 deliberate defects into the in-memory adapter one at a
  time. Two escaped the suite, and both escapes were port gaps rather than test
  gaps: `neighbors` deduplicates by entity, so relationship state — type,
  confidence, properties — was unobservable through the port entirely. That is
  why `get_relationships` exists.

The alias surface was added under the same rule, and its tier in
`tests/compliance/graph_store.py:1412-1557` is where the four methods' promises
are actually stated. Four of those tests are load-bearing in the sense this
section means — an adapter can pass everything else while failing them, and the
Protocol says nothing that would catch it:

- **The chain is three deep, not two**
  (`test_resolution_follows_a_chain_to_the_end`, line 1441). `d -> c -> b -> a`,
  asserting all four ids resolve to `a`. At two hops "follow one hop" and
  "follow to a fixed point" agree, which is the `CLAUDE.md` failure shape
  exactly: the shorter input makes the correct implementation and the wrong one
  the same function.
- **Last write wins on the alias key**
  (`test_an_alias_is_keyed_by_the_absorbed_entity`, line 1465). Two merges
  absorb the same entity into different canonicals, and the test asserts both
  the new resolution *and* `find_aliases(first) == []`. The second assertion is
  the one that bites: an adapter keyed on the canonical id keeps both rows and
  still resolves correctly, so checking resolution alone cannot see the defect.
- **Mutation isolation on `find_aliases`** (`test_find_aliases_returns_copies`,
  line 1511), tampering with `alias_name` and `merge_reason` on every returned
  row and re-reading. This is the habit four read methods shipped without in
  slice 3; the alias reads did not repeat it.
- **Tenant isolation on both reads**
  (`test_find_aliases_never_crosses_tenants`, line 1525, and
  `test_resolve_entity_ids_never_crosses_tenants`, line 1533). The second is
  the dangerous direction and its docstring says so: a leak there does not
  merely show one tenant another's data, it silently rewrites one tenant's edge
  endpoints onto an entity that tenant has never heard of. It asserts the
  negative *and* the positive in the same test, so an adapter that resolves
  nothing at all cannot pass.

`test_deleting_a_tenant_takes_its_aliases` (line 1545) closes the tier against
`delete_by_tenant`: aliases surviving a wipe make a rebuild replay merges over
rows the replay never created, which is `delete_by_tenant` quietly ceasing to
be a reset.

### Two structural properties that are easy to undo by accident

Two structural properties of the suite are load-bearing and easy to undo by
accident:

- **Adapters supply `new_store()` rather than overriding a `store` fixture.** A
  pytest fixture is per-test-function, not per-hypothesis-example, so a shared
  store leaks state across examples and silently weakens every property test.
- **Every read method needs a mutation-isolation test.** Four read methods
  shipped without one, and each time a mutation run — not review, not the
  property tests — found that a shallow copy passed everything. This is now
  enforced by introspection over the Protocol in
  `tests/unit/graph/test_compliance_coverage.py`. `CLAUDE.md` explains why a
  written rule was not enough: it had already failed four times.

## Decision: both ports carry the same coverage gate, and the vector one carries no legacy registry

The compliance suite states what a port means; a *coverage gate* states that
the suite still covers the port. `GraphStore` grew one in slice 3
(`tests/unit/graph/test_compliance_coverage.py`), and `VectorStore` now has the
same one (`tests/unit/vector/test_compliance_coverage.py`). Two ports, one
mechanism, deliberately — `CLAUDE.md` says to give every store port this gate,
and the reason it is a decision rather than a chore is in the next-to-last
bullet below.

**Both derive the read-method list from the Protocol by introspection, never
from a hand-kept list.** `read_methods()` walks the Protocol with
`inspect.getmembers` and counts a method as a read if its *return annotation*
mentions a domain type at any nesting depth — `{VectorRecord, VectorMatch}` in
`tests/unit/vector/test_compliance_coverage.py::read_methods`, and
`{Entity, Relationship, Alias}` in its `GraphStore` counterpart. The nesting is
what makes it usable rather than merely clever: the recursive `_mentions`
helper descends through `typing.get_args`, so `list[Entity]` and
`dict[str, list[Entity]]` both count while
`delete_relationship() -> bool`, `delete` and every `upsert_*` drop out
without anyone saying so, and a read method added tomorrow is in scope the day
it lands.

Deriving it is the decision, not an implementation detail. A hand-kept list
would need updating by the same person who forgot the test — the same failure
one level up, which is exactly how the four slice-3 read methods shipped
uncovered. Because the criterion is "hands a domain object back", it is also
the criterion that matters: those are precisely the methods that can leak a
mutable view of stored state or another tenant's rows, and a `bool` cannot.

Both ports annotate under `if TYPE_CHECKING`, so both modules pass a
`_PORT_NAMESPACE` into `typing.get_type_hints` to resolve the strings. And the
one thing introspection cannot infer is a *new domain type* on the port, so
that set alone is written down — `Alias` was added to the graph gate's set the
moment the port gained aliases, rather than after a mutation run found the
leak.

**Both require the same two proofs per read method**, under the same
conventional names: `test_{method}_returns_copies` and
`test_{method}_never_crosses_tenants`. Two proofs, because they fail in
different directions and neither implies the other. Mutation isolation is the
habit four read methods shipped without in slice 3 — behavioural tests cannot
see it, since handing back the live internal object is *correct* on every read
and wrong only afterwards. Tenant isolation is the one a behavioural test also
cannot see, because a leaking read returns data that is well-formed and
plausible; it is simply someone else's.

The convention is the mechanism, not a naming preference. Both gates check
coverage with `hasattr(<Compliance class>, convention.format(method=method))`,
so a method whose two tests exist under those names is covered with **no entry
in either module**. Write them and neither gate needs editing at all; write
them under some other name and the gate fails and tells you what to add. That
is deliberately the cheapest path, because the alternative path — registering a
differently-named test — is the one this ADR is trying to keep closed (see the
asymmetry below).

**The tenant half admits no exemptions.** `ISOLATION_EXEMPT` exists on both
gates as a dict of method to reason: empty today, guarded by
`test_exemptions_carry_a_reason` so a blank string cannot stand in for an
argument, and by a staleness test so an entry cannot outlive the method it
excuses. There is no corresponding `TENANT_EXEMPT` on either port. Both
modules call the shared `_uncovered` helper with a literal `{}` for its exempt
argument on the tenant check —

```python
    def test_every_read_method_declares_tenant_coverage(self):
        missing = _uncovered(TENANT_CONVENTION, {})
```

— which is the same helper, deliberately denied the escape hatch its isolation
sibling has. The reasoning is that the two failures are not comparable in kind.
A read that hands back stored state without copying might genuinely be unable
to leak — a method returning a frozen value, or rebuilding its result from
scratch — so "exempt, and here is why" is a sentence someone could honestly
write. A read that crosses tenants is a confidentiality bug in every case, so
there is no read for which "cannot leak" is arguable in advance rather than
proved by a test. An exemption slot exists to be filled; the way to make sure
one is never filled wrongly is not to build it.

That asymmetry is worth stating in the ADR rather than leaving in the code,
because the symmetric-looking fix is obvious and wrong. Adding `TENANT_EXEMPT`
to match `ISOLATION_EXEMPT` would read in a diff as tidying up an
inconsistency, and its first entry would be indistinguishable from its
existence.

**Both are guarded against the two ways a gate goes quiet.** A coverage gate is
itself code that can stop working without failing, and it fails silently in two
distinct ways: by having nothing to check, and by checking against a list that
has outlived what it described. Each port's gate carries a test for each.

**Vacuity.** `test_the_port_has_read_methods_to_check` asserts the
introspection actually found something. Everything else in both modules is a
statement of the form "no read method is uncovered", which is true of the empty
set — so a `read_methods()` that returned nothing would leave every other test
in the file green while checking nothing at all. That is not hypothetical
paranoia about a function that obviously works: `read_methods()` resolves
annotations through `typing.get_type_hints` with an explicitly supplied
`_PORT_NAMESPACE`, because both ports annotate under `if TYPE_CHECKING`. Rename
a domain type, or add a new one to a signature without adding it to that
namespace, and resolution is where it breaks — plausibly for the whole port at
once. The guard is the same instruction as `exhaustive = true` on the import
contract, which had to be broken on purpose before it counted as evidence: **a
check you have never seen fail is not yet a check.**

**Staleness.** The other way is an entry that outlives its subject. This is the
`CLAUDE.md` rule about exemption lists applied to a test module rather than to
`pyproject.toml` — an exemption naming a path or a method that no longer exists
matches nothing and passes, so a shrinking list stops shrinking and nobody is
told. The vector gate's `test_the_exemption_list_does_not_outlive_the_port`
subtracts `read_methods()` from `ISOLATION_EXEMPT` and fails on any remainder.
The graph gate's equivalent is named
`test_the_registries_do_not_outlive_the_port` because it has three lists to
cover rather than one — `ISOLATION_COVERAGE`, `TENANT_COVERAGE` and
`ISOLATION_EXEMPT` — and it loops over all three with the label in the failure
message. The names differ; the property is identical, and both are
[ADR 0014 *Exemption lists are empty and must stay falsifiable*](0014-exemption-lists-are-empty-and-must-stay-falsifiable.md)
applied per port.

Two details are worth keeping when this is copied to a third port. First, the
staleness check runs **in both directions** on the graph side: the test above
catches an entry for a method the port dropped, and
`test_registered_tests_exist_on_the_compliance_class` catches the opposite — a
registry entry naming a compliance test that has since been renamed away. One
direction alone leaves a registry that can be silently emptied by a rename,
which looks exactly like a registry that was never needed. Second,
`test_exemptions_carry_a_reason` rejects a blank or whitespace reason on both
sides. Both `ISOLATION_EXEMPT` dicts are empty today, so all three of these
tests currently iterate over nothing — which is precisely why the vacuity guard
above has to exist as a separate assertion about the *port*, not about the
exemptions. A test that loops over an empty dict proves nothing; a test that
asserts the port has reads to check proves the module is looking at something.

**The asymmetry between the two gates is the live invariant, and it is the
reason this decision is written down at all.** Everything above is symmetric:
same introspection, same two conventional names, same vacuity and staleness
guards. The one place the two modules differ is that the graph gate has
hand-written registries and the vector gate has none — and that difference is
maintained deliberately, in the direction of the vector gate.

`GraphStore`'s gate carries two of them, `ISOLATION_COVERAGE` and
`TENANT_COVERAGE`, mapping the same eight read methods to differently-named
tests that predate the convention (`get_entity` to
`test_mutating_a_read_result_does_not_change_the_store`, `neighbors` to
`test_relationships_do_not_cross_tenants`, and so on; several tenant entries
repeat, because one broad property test exercises every read under the wrong
tenant). They exist for exactly one reason, and the module's comment states it:
so that eight working, well-named tests did not have to be renamed to satisfy a
checker. They are closed to additions — `_uncovered` consults the convention
*first* and the registry only as a fallback, so a new read method is covered
without touching either dict.

They are tolerable only because they are checked in both directions.
`test_registered_tests_exist_on_the_compliance_class` asserts every registered
name still exists on `GraphStoreCompliance`, catching a rename that would
otherwise silently empty a registry into something that looks like a registry
nobody needed; `test_the_registries_do_not_outlive_the_port` asserts no entry
names a method the port has dropped. Both directions are required: a registry
checked in one direction only is an exemption list that can go stale in the
other, which is the `CLAUDE.md` rule this whole module is an application of.

**The vector gate has neither registry, because every test in
`tests/compliance/vector_store.py` was named to the convention from the start** —
`test_get_returns_copies`, `test_search_returns_copies`,
`test_get_never_crosses_tenants`, `test_search_never_crosses_tenants` (lines
647, 665, 325 and 348). Its `_uncovered` takes no registry argument at all;
there is no fallback path to consult, because there is nothing to fall back to.
The whole gate is derived: the method list from the Protocol, the test names
from the convention, and the only hand-written thing in the module is an empty
`ISOLATION_EXEMPT`.

Keeping it that way is the point of this note. **Adding a registry to the
vector gate is the erosion this ADR exists to make visible in review.** It
would arrive as the cheap fix the first time someone writes a
differently-named vector test — three lines, plainly modelled on the graph gate
that already sits beside it, and green. What it would actually do is convert a
mechanism with nothing hand-kept into one with a hand-kept part, and hand-kept
parts are exactly what failed four times in slice 3: a list that has to be
updated by the same person who forgot the test. The graph registries are a debt
carried for a specific, expired reason; copying them to a port that never
incurred it is copying the debt without the reason.

The failure mode is that this is invisible in a diff unless the reader knows
the absence was deliberate — a *new* registry reads as consistency with the
sibling module, not as a regression. So the absence is stated twice: the vector
module's docstring says "unlike its `GraphStore` counterpart there is no legacy
registry ... keep it that way", and this ADR is the second place, so a reviewer
who does not open the test file still has grounds to object. The correct
response to a differently-named vector test is to rename the test.

**The two vacuity guards are not equally strong, and the difference is a
deliberate concession on the graph side.** Both are named
`test_the_port_has_read_methods_to_check` and both carry the same docstring
("Guard the guard: a detector that finds nothing passes vacuously"), but the
assertions differ:

```python
    assert len(read_methods()) >= 8              # graph
    assert read_methods() == {"get", "search"}   # vector
```

The exact form is strictly stronger, in a way that matters beyond vacuity. An
inequality only fails when the introspection finds *too little*; the exact set
fails on a wrong answer in either direction. That second direction is the one
worth having, because the ways `read_methods()` goes wrong are not all
subtractive. It resolves annotations through `typing.get_type_hints` with an
explicitly supplied `_PORT_NAMESPACE` (both ports annotate under
`if TYPE_CHECKING`), and it counts a method as a read if its return annotation
mentions a domain type at any nesting depth. Widen the domain-type set by one
entry too many, or change a method's annotation so it now mentions
`VectorRecord`, and the set grows. Under `>= 8` that is silent; under `== {"get",
"search"}` it is a failing test that names the method.

The vector port can afford the exact form because it is **small and closed**.
`VectorStore` has two reads, and it is not expected to grow a third —
[ADR 0012 *No ANN index in a multi-tenant vector store*](0012-no-ann-index-in-a-multi-tenant-vector-store.md)
is part of why: the port
deliberately does not acquire index-shaped surface, so the read set is stable
by design rather than by luck. Writing the set out costs nothing and it will
not need editing.

`GraphStore` is in the opposite position. Its read surface has grown across the
migration — `get_relationships` came out of the slice 3 defect injection,
`find_by_blocking_keys` and `get_relationships_for` from batching work, and
`find_aliases` and `resolve_entity_ids` are the most recent additions in this
ADR — so an exact set would have to be edited on every legitimate addition.
**A check that must be edited to stay green is one people edit reflexively**,
and reflexive edits are how the check stops being read. Worse, the edit would
be indistinguishable in a diff from an edit made to accommodate an accidental
over-count: both look like "the port grew, update the number". The inequality
takes the weaker guarantee in exchange for never asking the author of a new
read method to touch this file at all, which is the same principle as the
naming convention — the cheapest path has to be the correct one.

So the asymmetry is not an inconsistency to tidy. **Neither direction of
"fixing" it is an improvement**: loosening the vector assertion to
`len(...) >= 2` discards a real check for symmetry with a compromise, and
tightening the graph one to an exact set installs the maintenance burden the
inequality was chosen to avoid. Read `>= 8` as the strongest form a growing
port can sustain, and `== {"get", "search"}` as the form a closed port should
keep. If `VectorStore` ever does gain a third read, the choice comes up again
on its merits — and the answer is still to edit the set, once, in the commit
that adds the method.

**The vector gate also guards the *strategy*, and that is the part
introspection cannot reach.** Everything above answers "is every read method
covered?". `TestTheMetadataStrategyReachesTheReservedKey` answers a question no
amount of introspection can even ask: *is the covered method being fed input
its contract bites on?*

The port reserves exactly one metadata key. `search` filters on
`entity_type`, and nothing else in a record's metadata is interpreted. So the
compliance suite's properties over stored metadata are only worth anything if
the generator behind them draws that key — and for a while it could not.
`metadata_dicts` was built on `property_dicts`, whose keys come from
`st.text(max_size=6)` (`tests/compliance/strategies.py:51-59`), and
`entity_type` is eleven characters. **The only key the port reads was
undrawable.** Every property over stored metadata passed, at full example
count, while saying nothing at all about the filter.

That is where the slice 5 divergence lived: the in-memory adapter raised
`TypeError: unhashable type: 'list'` on a stored `{"entity_type": ["person"]}`
where pgvector returned `[]`. Two adapters disagreeing about a legal record,
under a suite whose whole purpose is that they cannot.

The class pins the strategy from the outside, drawing 300 examples and
asserting over what came out (`tests/unit/vector/test_compliance_coverage.py:122-176`):

- `test_the_reserved_key_is_generated` — some drawn mapping contains
  `entity_type` at all. This is the assertion that would have failed for the
  whole period the blind spot existed.
- `test_both_string_and_non_string_values_are_generated` — values on *both*
  sides of the filter, since a filter is only exercised by input it accepts
  and input it rejects, and then the unhashable shapes specifically: some
  drawn value must be a `list` or `dict`. That last assertion is the slice 5
  bug written as a requirement on the generator. `_entity_type_values`
  (`strategies.py:226-233`) is what satisfies it — a type name, or `None`, a
  `bool`, an `int`, a small `list`, a small `dict` — and it is drawn about half
  the time, so records with and without the key both occur.
- `test_generated_metadata_is_always_storable` — whatever it draws still
  constructs a `VectorRecord`. Widening the strategy in future cannot silently
  start generating metadata no adapter is obliged to accept, which would turn
  a strategy fix into spurious adapter failures.

The general rule is worth stating plainly, because it applies to every
property test in this repo and not just this one: **a generator that cannot
draw the interesting value does not fail — the properties over it simply go
quiet.** That is the same failure shape `CLAUDE.md` records for property
samplers and for `exhaustive = true` on the import contract: a check you have
never seen fail is not yet a check. Coverage tooling cannot see it either; the
lines execute, the assertions run, and the branch that matters is never
reached. Introspection can prove a method is uncovered. Nothing mechanical can
prove a covered method is being asked the question its contract is about, so
that has to be asserted by hand, once, next to the gate.

The strategy's own docstring records two further traps this class caught while
the fix was being written, both worth knowing before touching it: the NUL
filter must run on the **finished** mapping (filtering the base and then adding
the reserved key lets a NUL through the added value, since a nested dict's keys
come from `st.text`), and the base draw must be **copied, not mutated**
(hypothesis reuses drawn objects while shrinking, so writing into one leaks a
key into an unrelated example and makes a failure irreproducible). Both are the
kind of defect that makes a suite quietly weaker rather than red.

See `docs/how-to/implement-a-store-adapter.md` for what these gates mean when
you are adding an adapter rather than a method, and
[ADR 0012 *No ANN index in a multi-tenant vector store*](0012-no-ann-index-in-a-multi-tenant-vector-store.md)
for why the vector port's read surface is small
enough to pin exactly.

## Consequences

- Adding a port method means the compliance suite, its mutation-isolation test,
  its tenant-isolation test, and every adapter — deliberately expensive.
- `VectorStore`'s recall tier currently passes trivially, because both adapters
  are exact. Whoever adds an approximate adapter must strengthen it first
  (`BACKLOG` B10k).
- Hop distance was left off `neighbors` knowingly (`BACKLOG` B10c1), with the
  retrofit costed in both adapters at the time the decision was taken.
