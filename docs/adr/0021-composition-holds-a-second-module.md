# ADR 0021: `composition` holds a second module, and retrieval is what it composes

## Status

Accepted. Amends
[`0007` `composition` is the only top layer](0007-composition-is-the-only-top-layer.md)
in fact. 0007's *reasoning* stands unchanged and is what this decision was
tested against — its Decision 1 said the layer holds exactly one module, and
its own "admission test" section said what a second one would have to
demonstrate. This records that a candidate met it.

[`0006` the public surface is gated](0006-the-public-surface-is-gated.md)
stands. [`0002` two store ports](0002-two-store-ports.md) stands — retrieval
reads through both ports and adds no method to either.
[`0017` the embedding provider port](0017-the-embedding-provider-port.md)
stands, and this is its second consumer.

## Context

0007 put one module on the top layer and left the constraint keeping it there
to review rather than to a gate, saying so plainly: `lint-imports` checks
direction and not population, so a second file under `redstring/composition/`
would inherit the top layer's permissions and pass. The section that closes
the gap is an *admission test*, and it asks one question about the candidate —
**which two layers, forbidden from importing each other, does this module
join?**

Entity retrieval needs three collaborators at once: an `EmbeddingProvider` to
turn the query into a vector, a `VectorStore` to search, and a `GraphStore` to
generate lexical candidates and to resolve matches back into entities.

## Decision

**`composition` is a package holding two modules**, `build_graph` and
`retrieval`, and membership continues to require naming the pair of layers a
module joins.

`retrieval` joins `vector`, `graph` and `llm`. Those three are siblings in the
import contract: `vector` and `graph` may not import each other, and neither
may import `llm`. So the set of layers that may hold all three collaborators
is exactly one, the top, and a `Retriever` placed anywhere else would require
a cross-sibling import — which is to say, it would require the contract to be
weakened rather than the module to be placed.

That is the same argument that admitted `build_graph`, applied to a different
pair. `build_graph` joins `extraction` and `projections`, whose separation is
what keeps a store reference out of the pipeline.

`redstring.composition` re-exports everything the module of that name
exported, so no import path changed.

## Consequences

**The layer is no longer visibly disproportionate, and that costs something.**
0007 observed that one file above six siblings invites a tidying instinct, and
placed three copies of the reasoning where an author would meet it mid-edit.
Two files invite it less — which means the *shape* of the package now argues
less loudly than the prose does, and the prose has to carry more of the weight.
The admission question is therefore repeated verbatim in
`src/redstring/composition/__init__.py`, in `pyproject.toml`'s layer comment,
and in `CLAUDE.md`.

**The population constraint is still not a gate.** Nothing fails when a third
module appears; `exhaustive = true` catches a new top-level *package*, which
is the opposite mistake. This ADR does not change that, and pretending
otherwise would be worse than admitting it. What it does change is the
precedent: the layer has now admitted a module on a stated argument, so the
next candidate is compared against two worked examples rather than one rule.

**A module here can never be reused by the code it supports.** Nothing may
import the top layer, so `Retriever` is unavailable to `extraction`,
`consolidation` and every sibling. That is the constraint working as intended —
a component the composed path needs to call belongs below, not beside.

**The submodule and its principal function share a name.**
`redstring.composition.build_graph` resolves to the function, not the module,
because the package `__init__` rebinds it after the submodule loads. That is
deliberate and load-bearing: it is what preserves the pre-existing import
path. Renaming the submodule to un-shadow it would break that path.
