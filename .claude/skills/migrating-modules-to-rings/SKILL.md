---
name: migrating-modules-to-rings
description: Use when moving a top-level module or package under src/redstring/ onto the layered architecture (composition; the extraction/consolidation/temporal/graph/vector/llm sibling band; projections; aggregates; events; ports; domain), retiring a legacy import path, or planning such a migration. Also use when a sweep, an import-linter contract change, or a `redstring.__all__` public-API question comes up mid-migration.
---

# Migrating Modules to Rings

## Overview

This skill covers one job: dissolving a top-level `src/redstring/<pkg>/` (or
`<mod>.py`) onto the layered architecture enforced by `lint-imports`, and
retiring the old import path.

The layers, highest to lowest, are declared in the
`[tool.importlinter.contracts]` `layers` list in `pyproject.toml` and repeated
in `CLAUDE.md`:

```
composition
extraction : consolidation : temporal : graph : vector : llm   (siblings)
projections
aggregates
events
ports
domain
```

A migration is done when the package no longer exists at its old path, every
module that used to live there has an argued home on one of those layers,
`lint-imports` passes with `exhaustive = true`, and nothing in the tree still
imports the retired path.

Four properties hold for every migration here:

- **Clean break, no shims.** The old path stops existing; it does not
  re-export. There is no deprecation window and no compatibility module. The
  supported surface is `redstring.__all__` (ADR
  `docs/adr/0006-the-public-surface-is-gated.md`), so any importer of
  `redstring.<pkg>.thing` was reaching into an internal dotted path that the
  package docstring already says may change in a patch release. A shim would
  preserve a promise that was never made, and it would keep the old path alive
  in exactly the greps the sweep depends on.
- **`git mv` preserves history.** Move whole files with `git mv` and confirm
  `git status` reports `R` (rename) rather than a delete plus an add. Rewriting
  a file's contents in the same commit as the move defeats rename detection and
  costs `git log --follow`; split content changes into a following commit.
- **`redstring.__all__` is the gated public surface**, and moving a module can
  change it. Three tests keep the surface honest — exported signatures may name
  only exported types (walking the MRO), every `RedstringError` subclass is
  exported or listed against a capability, and `docs/examples/build_a_graph.py`
  imports nothing but `redstring`. A move that changes what a public signature
  names, or that reparents an exception, is a public-API change and is gated as
  one.
- **Placement is an argument, not a preference.** The sibling band's memberships
  are load-bearing (`llm` beside `extraction` so extraction reaches only
  `ports.llm_provider`; `consolidation` and `temporal` beside it so neither can
  reach `extraction/mapping.py`). Landing a module on a layer that forces a new
  cross-layer import means either the module is in the wrong place or the
  contract needs a deliberate, argued change — recorded in an ADR under
  `docs/adr/` and mirrored into the inline reasoning in `pyproject.toml` and
  the architecture block in `CLAUDE.md`.

Deferred work found along the way goes in `BACKLOG.md` in the same commit that
passes it by — a migration is exactly the kind of work that turns up defects
adjacent to the change, and `.claude/rules/definition-of-done.md` admits no
substitute.

Prior art: `docs/plans/ring-migration.md` is the account of the campaign that
produced this layout, including which slices went wrong and why. Read it before
planning a new one.

## Scope check before starting

Do this before writing a plan. Two of the three checks take a `ls` and a
`grep`, and each of them has sent someone down a wrong path.

**1. There is no `application` layer and no `adapters` ring.** The layer names
are exactly the twelve in the `layers` list above; `ls src/redstring/` returns
`aggregates composition.py consolidation domain events extraction graph llm
ports projections temporal vector` and nothing else. "Adapters" here is a
*package inside a layer* — `graph/adapters/`, not a ring — which is why the
contract can say an adapter may import its port and never the reverse. If the
work you are about to do is phrased as "move this into the application layer"
or "put the adapters in the adapter ring", the phrasing is from a different
codebase and the classification it implies will not survive `lint-imports`.

**2. `services`, `models`, `db` and `schemas` are deleted, and re-adding one
needs an argument.** They went in slice 9 (`docs/plans/ring-migration.md`,
recovery table rows for `recovery/service-layer` and `recovery/orm-layer`):
the write model is `aggregates` + `events`, the read model is `projections`,
and persistence is the two ports. There is no ORM and no session for a layer
to be built around. The reasoning is inline in `pyproject.toml` beside the
`layers` list, in the words that will be quoted back at you — *"A new name here
should be argued for, not added because something needs somewhere to sit."*

So this skill does **not** cover "create a new top-level package for X".
`exhaustive = true` on the contract means a new top-level package is a gate
failure until it is placed on a layer deliberately, and that placement is an
ADR under `docs/adr/` (`0007-composition-is-the-only-top-layer.md` is the
worked example) plus a matching edit to the architecture block in `CLAUDE.md`.
Reach for a new package only after you have failed to justify the module's
home on an existing layer — and note that "it doesn't fit anywhere" was, both
times it came up, a sign the module had no caller rather than a sign the
contract was short a layer.

**3. The work is a move, not a rewrite.** This skill assumes whole files
relocating with `git mv`. Splitting a module, changing what it does, or
deleting it outright are different jobs with different gates — a deletion
moves the coverage ratchet (`docs/reference/quality-gates.md`) and usually
needs its justification in the commit message rather than a plan.

If all three checks pass, continue. If check 1 or 2 fails, stop and settle the
architecture question first; a migration executed against the wrong layer map
is the most expensive kind to unwind, because `git mv` history and a swept
tree both have to be redone.

## Classification rules

Classify each module by **what it imports and who imports it**, not by what it
is named. Work bottom-up: find the lowest layer that can hold the module
without forcing a new import, and stop there. A module that lands too high
still passes `lint-imports` — the contract only forbids *upward* and *sideways*
imports — so the gate cannot catch this mistake for you, and a module parked
above its true layer is what makes the next module's placement wrong.

Take the layers in order, lowest first.

**`domain/` — pure values and the exception root.** Every module here imports
only stdlib, pydantic, and other `domain` modules; `entity.py` imports
`domain.ids` and `domain.temporal`, and that is the whole shape. Entities,
value objects, ids, precision enums, normalisation and similarity functions,
merge-strategy and preference rules, and `exceptions.py` live here. If the
module needs no I/O and no event schema, it belongs here even when only one
sibling-band package uses it today — `domain/preference.py` moved down
precisely when a second and third caller appeared, and pushing it down was
what let `consolidation` stay a sibling.

**`ports/` — Protocol only, no implementation.** All four modules
(`graph_store.py`, `vector_store.py`, `llm_provider.py`, `cache.py`) declare a
single `@runtime_checkable` `Protocol` over `domain` types and nothing else.
A class with a method body is not a port. The one thing a port may carry
beyond the Protocol is a helper that defines the *contract's* own convention
rather than any adapter's — `ports/vector_store.py::entity_type_of` reads the
metadata key the port specifies, so every adapter agrees on it. Anything that
touches a driver, a wire format, or a storage format is an **adapter**, and
adapters are packages *inside a sibling-band layer* (`graph/adapters/neo4j.py`,
`vector/adapters/pgvector.py`, `llm/adapters/langchain.py`), never a layer of
their own. The direction is fixed: an adapter imports its port; the port never
imports the adapter.

**`events/` — the event schema.** Immutable `TenantDomainEvent` subclasses
(`DocumentExtracted`, `EntitiesEmbedded`) and the stream-id functions in
`streams.py`. These import `domain` and the `eventsource` base classes. A
module belongs here only if it is part of the recorded schema; a type that
merely *appears in* a payload is a `domain` type.

**`aggregates/` — the write model.** Aggregates decide and emit; they import
`events` and `domain`. `repositories.py` sits here too, because the repository
factories are typed on the aggregates they build. Nothing below an aggregate
may import one.

**`projections/` — the read model.** Projections fold events through a port and
write nothing back. They may import `ports` and `events`; they may not import
an adapter, and no lower layer may import a projection. `StoreProjection` is
generic over its store for that reason — the concrete adapter arrives as a
constructor argument from above.

**The sibling band — `extraction : consolidation : temporal : graph : vector :
llm`.** This is where the work happens, and its defining constraint is that
**siblings may not import each other**. A module lands here when it needs a
port or a capability, not merely values: extraction chunks and prompts and maps
LLM output to entities; consolidation decides merges; temporal infers intervals;
`graph`, `vector` and `llm` each hold a port's adapters. If a candidate module
would need to import a *second* sibling, it is misclassified — the shared piece
belongs in `domain` (or, if it is behavioural, behind a narrow Protocol in
`ports/`). See the next section for why each of these memberships is
load-bearing rather than alphabetical.

**`composition.py` — the top layer, one module.** It exists so that something
can hold both `extraction` and `projections` when neither may import the other:
`build_graph` imports `Document`, `document_stream`, the extraction pipeline
and `GraphProjection` and wires them together. A module belongs here only if
its job *is* the wiring. "It needs two siblings" is not a ticket to the top
layer — it is usually the misclassification described above.

Two rules cut across the band:

- **Exceptions merge into `domain/exceptions.py`,** rooted on
  `RedstringError`. Prefer an existing intermediate root over a new direct
  subclass when one fits — `LlmProviderError` and `ConsolidationInvariantError`
  each already carry a family, and callers catch the family. Verify every
  `except` site before rebasing a root: widening or narrowing what a handler
  catches is a behaviour change that no import gate can see. A new exception
  also has a public-API gate to satisfy; see the public-API section below.
- **When a layer needs one or two methods of a wider interface, cut a narrow
  Protocol in `ports/`** rather than importing across the band or arguing the
  contract should relax. That is the move `temporal` makes — it reads entities
  through `ports.graph_store` and computes over `domain.interval`, and so needs
  nothing from `extraction` at all.

## Why the sibling band placements are load-bearing

The sibling band reads like a list of six coequal packages, and the temptation
during a migration is to treat the line as a bag: anything that is "a
capability" goes there, anything that "uses extraction" goes above it. Both
halves of that instinct are wrong, and the three memberships below are the
reasons. Each one is a *constraint someone chose*, and the constraint is
enforced by exactly one property of the contract — **siblings may not import
each other.** Move a sibling up or down and that property stops holding for it.

**`llm` sits beside `extraction`, not beneath it.** The obvious layering puts
`llm` lower, since extraction calls a model and nothing calls extraction. Do
that and extraction may import `llm.adapters.langchain` directly, because a
higher layer may import a lower one — and the `LlmProvider` port becomes
decorative. As a sibling, the only thing extraction can reach is
`ports.llm_provider`, which is the whole point of having the port. Two
consequences to carry into any migration that touches this:

- A module that wants to be "beneath" extraction so extraction can use it is
  describing the failure mode, not a requirement. Give it a Protocol in
  `ports/` instead.
- **`lint-imports` cannot see this one all the way.** The contract is over
  first-party packages, so a stray `from langchain_core...` in a non-adapter
  module is not a violation, not a lint finding, and not a test failure.
  `tests/unit/llm/test_port_does_not_leak.py` parses every file under `src/`
  and fails on the leak. Any dependency the architecture confines to one
  directory needs that second, source-text check; the layer contract alone will
  not do it.

**`consolidation` is a sibling, not a layer above `extraction`.** It plainly
consumes what extraction produces, so "above" looks natural. It is not, for two
reasons. First, it needs nothing from extraction: the tie-break the two shared
moved down to `domain/preference.py` when consolidation became its third
caller, which is the general remedy — a piece two siblings need belongs
*below* both, in `domain`. Second, above extraction it could import
`extraction/mapping.py`, and that module is the single place entity identity is
derived (`entity_id_for` over `(tenant, source, entity type, normalized
name)`). A second caller deriving ids is how a second id scheme gets born, and
the failure is silent: chunks stop agreeing, re-extraction doubles entities
instead of upserting, and relationship endpoints drift from the entities they
name.

**`temporal` is a sibling for the same reason, with a sharper edge.** It reads
entities through `ports.graph_store` and computes over `domain.interval` — no
extraction dependency at all. Placed above extraction it too could reach
`mapping.py`, and the specific temptation there is persistence: inferred edges
would acquire a path into `DocumentExtracted`, which is exactly the decision
`temporal/inference.py` spends its module docstring arguing against (a derived
fact in the durable log goes stale silently, is quadratic in the tenant rather
than the document, and makes a replay disagree with the same arithmetic run
today). The placement is what makes that argument structural instead of
advisory. Note the module's belt-and-braces companion: `InferredRelation` has
no `id`, so it cannot be handed to `GraphStore.upsert_relationship` even by a
caller who wanted to.

The shape common to all three: **a placement is load-bearing when moving it
would not break anything today, but would make a specific future mistake
possible.** That is also why none of these is discoverable from the code — no
import exists to read — and why the reasoning must live somewhere a person will
find it before editing the `layers` list.

### Keep three files in step

The reasoning is written down in three places, and a migration that changes a
placement must update all three in the same commit:

| Where | What it holds |
|---|---|
| `pyproject.toml`, `[tool.importlinter.contracts]` | The `layers` list itself, with the full reasoning inline as comments beside each line. This is the source of truth — it is the only copy the gate reads. |
| `CLAUDE.md`, "Architecture contract" | The same layer diagram plus a condensed version of the sibling-band reasoning, because it is binding instruction for every future session. |
| `docs/adr/` | The decision record for a *change* to the contract (`0007-composition-is-the-only-top-layer.md` is the worked example). ADR bodies are immutable; supersede rather than edit. |

A stale layer diagram in `CLAUDE.md` is worse than none: it sends the next
author to a package that does not exist and gets believed, because binding
instructions are read as current by construction. The `docs/plans/ring-migration.md`
index and `docs/reference/quality-gates.md` reference the contract but do not
restate the band; check them for incidental mentions during the docs slice
rather than treating them as copies to sync.

## Cross-layer imports

A move is finished when `lint-imports` passes. When it does not, the failure
names an import that goes upward or sideways, and there are exactly three
honest responses. Take them in this order; the first that fits is the answer.

**1. Move the module.** The commonest cause of a cross-layer import is a module
parked one layer too high, and the import it needs is the evidence. A module on
`projections` reaching for `domain.preference` is fine; a module on `domain`
reaching for `ports` is a module that is not a value type. Work the
classification rules bottom-up again on the offending module *and* on the one
it imports — often the fix is to push the **imported** module down rather than
to move the importer. `domain/preference.py` is the worked example: it was
shared by two sibling-band packages, and pushing it to `domain` removed the
import without touching either caller's layer.

**2. Cut a narrow Protocol in `ports/`.** When one layer needs *behaviour* from
another — not a value, not a pure function, but something with an
implementation — the answer is a Protocol over `domain` types, placed in
`ports/`, which every layer may import. This is the move that keeps the sibling
band a band instead of a stack:

- `ports/llm_provider.py` is why `extraction` can call a model without
  importing `llm`.
- `ports/graph_store.py` is why `consolidation` and `temporal` can read
  entities without importing `graph`.

Two constraints on doing it well, both visible in the existing ports:

- **Narrow means narrow.** `Cache` documents this at length: it exposes
  `record_hit`/`count_hits`/`oldest_hit` — "events in a time window" — rather
  than `zadd`/`zcard`/`zremrangebyscore`, because a port spelled in a driver's
  vocabulary is that driver wearing a different name, and no second adapter
  can satisfy it without reimplementing the first. Write the methods the
  *caller* needs, in the caller's terms.
- **Add methods for a caller that exists, not one you anticipate.**
  `GraphStore.neighbors` deliberately does not return hop distance, and says
  so in the docstring: the temporal work might have wanted it, and widening
  the contract on speculation was rejected because the addition is
  backwards-compatible whenever the caller actually arrives. The mirror case is
  also there — `find_by_blocking_keys` was added because consolidation's real
  shape was one query per key otherwise. Both decisions are argued in the
  docstring at the point of the method, which is where the next author will
  read them.

A port method is not free: `tests/unit/graph/test_compliance_coverage.py`
derives the read-method list from the Protocol by introspection and fails
until every read method has a registered **mutation-isolation** test and
**tenant-isolation** test. Adding a method to a port during a migration adds
those tests to the same slice. Treat that as a feature — it is the gate that
makes "cut a narrow Protocol" cheap to review and expensive to do sloppily.

**3. Argue the contract change.** Only when neither of the above fits: the
module is on the right layer, the dependency is genuinely structural, and the
`layers` list is wrong. This is rare and it is deliberate work, not an edit.
It requires, in one commit:

- an ADR under `docs/adr/` stating what the current contract prevents, why that
  prevention is no longer wanted, and what mistake becomes possible once the
  edge exists (`0007-composition-is-the-only-top-layer.md` is the shape to
  follow);
- the `layers` edit in `pyproject.toml` **with the reasoning inline**, in the
  same style as the comments already there;
- the matching update to the architecture block in `CLAUDE.md`.

What does **not** count as an argument: "it only needs one function from
there", "the import is only under `TYPE_CHECKING`", "it is temporary", or "the
alternative is more code". The first is response 1 or 2; the second still binds
the layers at review time even where it does not at runtime; the third has no
mechanism to end; the fourth is the price of the property being bought.

Two failure modes specific to this step:

- **Widening a layer to admit a module is the same mistake as moving the
  module up.** Adding a package to the sibling line so it can be imported by
  another sibling does not work at all — siblings may not import each other —
  and moving it *below* the band to make it importable is exactly how the
  `llm`-beneath-`extraction` failure described above happens. If the reflex is
  "make this reachable", the answer is a port.
- **`lint-imports` sees only first-party imports.** A cross-layer dependency
  smuggled in as a third-party import — a `langchain*` import outside
  `llm/adapters/` — passes the contract silently.
  `tests/unit/llm/test_port_does_not_leak.py` parses every module under `src/`
  and is what catches it. Any confinement the contract cannot express needs
  that second, source-text check written in the same slice.

## Exceptions

Exceptions move differently from everything else, because they have two
independent gates on them: the layer contract, and the public-surface tests.
A migration that reparents or relocates an exception has to satisfy both, in
the same slice.

### Where an exception lives

**The root and the shared families live in `domain/exceptions.py`.**
`RedstringError` is defined there, along with every error a caller of the
public surface can reach: `MissingEntityError`, `DimensionMismatchError`,
`AliasCycleError`, `UnknownDomainError`, the `LlmProviderError` family
(`EmptyCompletionError`, `RefusedCompletionError`,
`MalformedCompletionError`), and the `ConsolidationInvariantError` family
(`MergeIntoAliasError`, `DoubleMergeError`, `UnknownMergeError`). Putting
them on the lowest layer is what lets any layer raise one without an upward
import — an exception module that sits on a sibling-band layer is unraisable
from `domain` and unraisable from another sibling, and that constraint is the
usual reason a migration is holding an exception in the wrong place.

**Four errors live outside it, and each has a reason to.**
`extraction/errors.py` holds `ChunkingError`/`ChunkerError`/`ChunkSizeError`,
`extraction/pipeline.py` holds `PartialExtractionError`, and
`llm/rate_limiter.py` and `llm/circuit_breaker.py` hold `RateLimitExceeded`
and `CircuitOpen`. All four subclass `RedstringError` from `domain`, which is
a *downward* import and therefore legal. The rule that follows: an exception
raised by exactly one layer, and meaningful only in that layer's vocabulary,
may stay beside its raiser. An exception two layers raise, or that a caller
catches without knowing which layer produced it, moves down to
`domain/exceptions.py`. Do not move an exception down merely because it is an
exception.

### Merging onto `RedstringError`

When a migration brings in an exception hierarchy of its own, merge it rather
than parking it:

1. **Reparent the root onto `RedstringError`** — or, better, onto an existing
   intermediate root when one already carries the family. `LlmProviderError`
   and `ConsolidationInvariantError` exist for exactly this: callers catch the
   family, and adding a fifth direct subclass of `RedstringError` where a
   family fits makes every caller's `except` clause longer for nothing.
2. **Check every `except` site before rebasing a root.** Widening or narrowing
   what a handler catches is a behaviour change, and no import gate, lint rule
   or type check can see it. `grep -rn "except .*Error" src/ tests/` and read
   the hits that name anything in the family you are moving.
3. **Do not collapse two exceptions because they look alike.** The worked
   argument is in `RefusedCompletionError`'s docstring: it is a *sibling* of
   `EmptyCompletionError` rather than a subclass, because a truncation is a
   configuration problem worth retrying and a refusal is a permanent property
   of the content, and collapsing them removes the distinction at the moment a
   caller extracting clinical or legal text needs it most. If a migration is
   tempted to unify two errors, the question is whether any caller reacts
   differently — not whether the messages are similar.
4. **Keep the constructor keyword-only and carrying identifiers.** Every
   exception here takes `*` keyword arguments and stores what it names
   (`entity_id`, `tenant_id`, `expected`/`actual`, `model`) as attributes, so
   a caller acts on the exception without parsing its message. An exception
   arriving from a migration with a single formatted string is unfinished.

### The `__all__` export/list gate a new exception must satisfy

`tests/unit/test_public_surface_is_self_contained.py` enforces that
`RedstringError` is the base of every deliberate error *and that the promise
is actionable*. It discovers subclasses by importing every module under
`redstring` with `pkgutil.walk_packages` and then walking `__subclasses__`
recursively — so a new exception is picked up the moment it exists, whether or
not `redstring/__init__.py` reaches its module. There is no way to add one
quietly.

Every discovered subclass must be **either** in `redstring.__all__` **or** in
`UNEXPORTED_BECAUSE_THEIR_RAISER_IS`, a dict in that test file mapping the
exception name to the capability whose export would bring it (today: the four
consolidation errors, plus `CircuitOpen` and `RateLimitExceeded`, which are
middleware). So when a move introduces or relocates an exception, do one of
two things in the same commit:

- **Export it.** Add the import to `redstring/__init__.py`, add the name to
  `__all__`, and add it to the "Errors" bullet in the module docstring — the
  docstring is the reference documentation for the surface, and a name in
  `__all__` that it does not mention is a hole a test will not catch.
- **List it,** with the *capability* reason rather than a description of the
  error. The entry is a pair: "`redstring.consolidation` is not exported yet"
  is what tells the next person that exporting consolidation obliges exporting
  its errors too. "Internal" or "not needed" throws that away.

Three sibling tests close the loop, and knowing them prevents the usual
false starts:

| Test | What it stops |
|---|---|
| `test_every_error_is_catchable_from_the_public_surface` | A new `RedstringError` subclass that is neither exported nor listed. |
| `test_no_unexported_error_reason_is_stale` | A listed name that no longer exists — delete the entry when you delete or rename the exception. |
| `test_an_exported_error_is_not_also_listed_as_unexported` | Exporting an error while leaving its entry in place, which would leave the only record of *why* it was unexported standing as a false statement. |

The third is the one a migration trips: exporting a capability is the moment
its listed errors must be exported **and** their entries deleted, and doing
only the first half passes two of the three tests.

Two things this gate deliberately does not do, so do not expect them:

- **It does not see exceptions in signatures**, because exceptions do not
  appear in signatures — they appear in a `raise`. The signature gate
  (`test_exported_name_mentions_only_reachable_types`) is structurally blind
  here, which is why the exception check exists separately. Removing
  `MissingEntityError` from `__all__` passes the signature gate cleanly.
- **It does not tell you whether the exception is raised by exported code.**
  That judgement is yours, and getting it wrong is what produced the gate:
  `RefusedCompletionError` was raised by exported code, was not exported, and
  its own docstring argued a caller must be able to distinguish it — so
  `except RefusedCompletionError` needed a dotted import into an internal
  module. Four such names failed the first run of this test, which is a
  missing gate rather than four mistakes.

Errors that are not `RedstringError` subclasses at all are outside this
gate and outside the promise. If a migration brings in a bare
`ValueError`/`KeyError` raised deliberately, rebase it — `UnknownDomainError`
is the precedent: it exists because `domain_system_prompt` is public and the
registry's `KeyError` was not something `RedstringError` covered.

## Public API consequences of a move

A move is not supposed to change the public API, and that is exactly why the
public-API gates fire during migrations: the surface changes as a *side
effect* of relocating a type, and nobody set out to change it. Three tests
guard it, none of which can see the other two's failures. Run them before you
believe a move is done, and expect at least one of them to have an opinion
whenever the moved module contributed a type to an exported signature or a
subclass of `RedstringError`.

### Gate 1 — an exported name's signature may name only exported types

`tests/unit/test_public_surface_is_self_contained.py::test_exported_name_mentions_only_reachable_types`
parametrises over every name in `redstring.__all__` that has a signature at
all, and fails if any annotation refers to an identifier that is neither
exported nor recorded in `DOCUMENTED_FOREIGN_TYPES`. That dict is *not* an
exemption list — it names types belonging to other packages (`GlobalEventFeed`
and `Position` from `eventsource.ports`, `RetryPolicy`, `Tracer`, `StreamId`)
together with the import path a caller needs, and a companion test
(`test_no_documented_foreign_type_is_stale`) deletes it out from under you when
no signature mentions an entry any more.

Two properties of this gate decide how a move interacts with it:

- **It walks the MRO, and that is load-bearing.** `_surface_of` iterates
  `inspect.getmro(obj)` and takes the annotations of every base defined under
  `redstring`. `GraphProjection` declares no `__init__`; the constructor a
  caller actually calls is `StoreProjection.__init__`, which is not exported
  and takes five `eventsource` types. A body-only check reported it clean.
  So when a move changes a **base class** — reparenting, splitting a mixin
  out, or relocating a base to another layer — it changes the gated surface of
  every exported subclass, even though no exported file was edited.
- **It reads source with `ast`, not `typing.get_type_hints`.** Every module
  has `from __future__ import annotations` and most annotation imports sit
  under `if TYPE_CHECKING:`, so `get_type_hints` raises `NameError` on exactly
  the modules that matter most (`composition.py` imports all eight of its
  annotation types that way). The practical consequence for a migration:
  moving a type changes the *string* in the annotation whenever the import
  spelling changes, and a `TYPE_CHECKING`-only import is gated exactly as a
  runtime one is. There is no way to dodge the gate by deferring an import.

The gate is also structurally blind in one place worth knowing before you
trust it: a constructor inherited from a **foreign** base contributes no
annotation of ours. `Document(...)` is `AggregateRoot.__init__` from
`eventsource`, so nothing in our source mentions the `StreamId` it takes —
which is why `document_stream` is exported on an argument rather than on a
measurement. If a move puts an exported type under a foreign base, reachability
there has to be reasoned about by hand.

### Gate 2 — every `RedstringError` subclass is exported or listed

Covered in full under "Exceptions" above; the migration-relevant summary is
that the check discovers subclasses by `pkgutil.walk_packages` over the whole
package and then recursing `__subclasses__`, so relocating an exception cannot
hide it, and every subclass must be in `__all__` or in
`UNEXPORTED_BECAUSE_THEIR_RAISER_IS` keyed to the capability whose export
would bring it. This gate exists because gate 1 is blind to exceptions: they
appear in a `raise`, never in a signature, so removing `MissingEntityError`
from `__all__` passes gate 1 cleanly.

### Gate 3 — the end-to-end example imports nothing but `redstring`

`tests/unit/test_end_to_end_example.py` loads `docs/examples/build_a_graph.py`
from its path, **runs it**, and asserts the graph answers the questions it
asks. It also parses the example's imports and fails on any root outside
`redstring` and `ALLOWED_NON_KG_ROOTS` (`asyncio`, `uuid`, `__future__`).

That second assertion is the one a migration must not lose. Without it the
example could reach into `redstring.graph.adapters.memory` and pass while the
top-level surface was empty — the gate is what makes the example evidence
about the *public API* rather than about the internals. So a move that
relocates anything the example touches is finished only when the example still
imports `redstring` alone; "fix the example's import" is the wrong repair if
the fix is a deeper dotted path. There is a third assertion with teeth too
(`test_it_fits_in_a_screen`, under 80 lines from the first import): a
migration that makes the composition longer to express is telling you
something about the placement.

### Exporting one name pulls its closure

The gates compose into a rule that governs how much a migration can bite off:
**exporting a name obliges exporting everything its signature names,
transitively.** `Entity` obliges `TemporalExtent`, which obliges
`DatePrecision`. Exporting `DomainSchema` alone would have satisfied the
letter of the original finding and left the type unconstructible.

Two consequences when planning slices:

- **A capability's export is one slice, not a name at a time.** Pick the
  entry point, close the annotation graph over it, and export the closure in a
  single commit — with the module docstring's reference list updated in the
  same edit, since gate 1 checks `__all__` and nothing checks the docstring.
- **The closure includes the errors.** Exporting a capability is the moment
  its entries in `UNEXPORTED_BECAUSE_THEIR_RAISER_IS` must be deleted, because
  `test_an_exported_error_is_not_also_listed_as_unexported` fails on the
  overlap. Consolidation is the live case: four errors are listed against
  "`redstring.consolidation` is not exported yet", and whoever exports it
  inherits all four.

The gate makes the closure visible at the moment it happens, which is the
point of it — an under-exported capability is a surface that type-checks and
cannot be used, and it is not detectable by reading `__all__`.

## Sibling campaigns — coordinate before writing anything

Two migrations running at once do not collide over the modules they move —
those are disjoint by construction, or the plans are wrong. They collide over
the handful of files that *every* migration edits. Those collisions are the
most expensive kind, because they surface at merge, after both branches are
green, and the resolution is a judgement about architecture rather than a
textual one.

So the ordering rule is: **agree the shared-seam edits before either branch
writes them.** Not at review, not at merge.

### The four shared seams

| Seam | Why every migration touches it | Cheapest coordination |
|---|---|---|
| `src/redstring/__init__.py` | The gated public surface. A move that exports, un-exports, or re-spells a name edits the import block, `__all__`, and the module docstring's reference list — three places in one file, all near each other. | Agree who owns `__all__` for the duration. The other branch stages its export as a diff and applies it after the merge. |
| `src/redstring/ports/` | The remedy for a cross-layer import is a narrow Protocol, so two migrations independently reaching for one land in the same four files. | Agree the *port* and its method names up front. Two ports over the same store is the failure to avoid; a second method on an existing Protocol is cheap. |
| `src/redstring/domain/exceptions.py` | Exceptions merge onto `RedstringError`, and both branches add to or reparent within the same family blocks. | Agree the family (`LlmProviderError`, `ConsolidationInvariantError`, or a direct subclass) before either writes it. Reparenting decided twice is a behaviour change decided twice. |
| `pyproject.toml` | The `layers` list, its inline reasoning, `[tool.mutmut] only_mutate`, `cosmic-ray.toml`'s module list, and the pytest selection args. | Only one branch may edit the `layers` list. If both must, one lands first and the other rebases onto it — a conflicted `layers` list resolved by hand is how a layer silently loses its comment. |

Two of these have a second-order property worth naming. `__init__.py` and
`exceptions.py` are both *gated*: the three public-surface tests
(`tests/unit/test_public_surface_is_self_contained.py`,
`tests/unit/test_end_to_end_example.py`) read the merged state, so two branches
can each be green and the merge fail — an exception exported on one branch and
listed in `UNEXPORTED_BECAUSE_THEIR_RAISER_IS` on the other satisfies
`test_every_error_is_catchable_from_the_public_surface` separately and fails
`test_an_exported_error_is_not_also_listed_as_unexported` together. Neither
branch's CI can see it.

### Before writing the plan

- **Find the sibling branches.** `git branch -a` and, if the campaign uses
  them, open PRs. For each, read the *plan artifact* under `docs/plans/` rather
  than the diff — the plan says which modules it intends to move and which
  seams it intends to touch, which is the information you need and the diff
  does not yet contain.
- **Diff the layer assignments.** If a module appears in two plans' assignment
  tables, settle its home now. The classification rules above are the argument;
  the wrong outcome is two branches each landing it somewhere defensible.
- **Do not allocate an ADR number.** Numbers are allocated **at merge**,
  against the highest on `main` at that moment, so every parallel branch
  drafting one drafts the same next number and whichever merges second
  renumbers (`.claude/rules/definition-of-done.md` item 5;
  `.claude/rules/recurring-defects.md` §6). Draft under a descriptive filename
  and cite it by filename — `docs/adr/` currently holds eight drafts sharing
  `0007`, which is the rule working rather than failing. Citing a draft by its
  number writes down a fact that is not yet true.

### While both branches are live

- **Merge `main` into your branch the moment a sibling lands.** Drift
  compounds, and a seam file that has diverged over three sibling merges is
  resolved by rewriting rather than by merging.
- **Resolve conflicted seam files hunk by hunk.** Never
  `git checkout --ours` a partially conflicted file: it discards `main`'s
  auto-merged hunks in the same file, which for `__init__.py` means silently
  dropping a sibling's export while the tests still pass on your side.
- **Re-read a shared file immediately before each edit to it**, and keep the
  edit to the lines you need. Formatting a seam file, or a tree-wide
  `ruff format`, turns a one-line conflict into a whole-file one. Format only
  the files your migration actually moves.
- **Sweep after merging, not only before.** A sibling merge can reintroduce a
  reference to the path you retired — `sweep.sh` is cheap, and the leftover-dir
  check is the half that catches a resurrected namespace package.


## Workflow

### Step 1 — plan first, as an artifact

Write the plan down before touching a file, and write it *in the repo* rather
than in a chat window or a task description. The campaign that produced this
layout is the argument: its working notes — eleven briefs, eleven implementer
reports, ten reviews, 4.2 MB — were never tracked and are gone, and
`docs/plans/ring-migration.md` exists because the reasoning behind 171 commits
had no other home. Anything not committed is not a plan; it is a message.

**Where it goes.** A new file under `docs/plans/`, alongside
`ring-migration.md`, named for the migration rather than dated. `docs/plans/`
is live planning; `docs/history/` is where a plan is archived *unchanged* once
its work is done (`docs/history/2026-08-ring-migration-plan.md` was moved there
in slice 11, and its header says so). Do not start a plan in `docs/history/`,
and do not rewrite a plan after its campaign ends — archive it and let the
account in `docs/plans/ring-migration.md` carry what actually happened. The
sweep treats `docs/plans/` as a by-design location for mentions of the retired
path, so the plan may name old paths freely.

The plan has four required parts.

**1. The layer-assignment table.** One row per module being moved, and no
module may be missing from it — this table is what a sibling campaign diffs
against (see "Sibling campaigns"), so an omission is invisible to the one
process that would have caught a double assignment.

| Module | Layer | Why that layer, not the one above | New imports it needs |
|---|---|---|---|

The third column is the load-bearing one. "Classification rules" says to work
bottom-up and stop at the lowest layer that holds the module without forcing a
new import, and the gate cannot check that for you: a module parked too high
still passes `lint-imports`, because the contract forbids only upward and
sideways imports. Writing the *rejected* lower layer down is what makes the
placement reviewable. The fourth column is where cross-layer trouble surfaces
before it is written — an entry there naming a sibling means the row is wrong,
or a port is owed.

**2. The move list.** Every path, old → new, including tests. Say for each
whether it is a `git mv` of a whole file (the default), a split, or a
deletion — the three have different gates, and a deletion moves the coverage
ratchet and needs its justification in the commit message rather than here
(`docs/reference/quality-gates.md`). Name the retired import path explicitly,
because that string is the argument to `sweep.sh` and the subject of the
`ModuleNotFoundError` guard test in step 3. If anything is being deleted rather
than moved, plan a `recovery/<name>` annotated tag for it now: eleven of the
twelve tags in `ring-migration.md` mark a deletion, and the reason they are
annotated tags rather than commit shas is that a sha does not survive a
squash-merge, a rebase, or `gc`.

**3. The slice list, foundation slice first.** A slice is a commit-sized unit
with its own gate, dispatched one at a time. Order them so that the slice whose
outputs other slices import lands first — the ports-and-domain extraction of
step 2 — and put any pure deletion before everything else, because every later
slice is cheaper against a smaller tree. Give each slice a stated gate in the
table, not a generic "tests pass":

| # | Slice | Gate |
|---|---|---|
| 0 | Foundation: extract to `domain`/`ports`, old package re-exports | Identity tests green; old imports still work |
| 1 | Move `<pkg>` onto `<layer>` | `git status` shows `R`; `lint-imports`; `ModuleNotFoundError` guard |
| 2 | Docs/meta | Sweep clean |

Two constraints on slicing that come from the public-API gates rather than from
convenience: **a capability's export is one slice, not a name at a time**
(exporting a name obliges its whole annotation closure, plus deleting its
entries from `UNEXPORTED_BECAUSE_THEIR_RAISER_IS`), and **a port method added
mid-migration brings its mutation-isolation and tenant-isolation tests into the
same slice**, because `tests/unit/graph/test_compliance_coverage.py` derives
the required list from the Protocol by introspection.

**4. The constraints every slice is held to.** Do not restate the project
rules; cite them, and record only what is specific to this migration.
`docs/history/2026-08-ring-migration-plan.md`'s "Global Constraints" section is
the reusable text — it is explicitly kept for the next campaign of this kind —
and `.claude/rules/definition-of-done.md`, `.claude/rules/recurring-defects.md`
and `docs/reference/quality-gates.md` carry the rest. What *is* migration
specific and belongs in the plan: which layer placements are being changed and
therefore which of the three files in "Keep three files in step" this campaign
must edit; which shared seams it will touch; and any coverage movement expected
from a deletion.

Also plan the risks with the same specificity the archived plan used —
"projection lag is real", "the event schema is forever" — rather than a generic
list. A risk worth writing is one that names the thing that would go wrong and
the slice at which it would be discovered.

**What not to put in the plan.** No ADR *number* — numbers are allocated at
merge against the highest on `main`, so a plan that names `0007` is writing
down something that is not yet true; cite the draft by filename. No counts or
survivor lists that will decay; those belong in commit messages, which are
immutable and correctly scoped to a moment
(`.claude/rules/recurring-defects.md` §5). And no schedule — slices are
dispatched one at a time, and a plan that predicts when slice 6 lands is
predicting the outcome of slice 5's review.

Finally, **the plan is a commit like any other**: it lands before the first
move, and any work it defers goes into `BACKLOG.md` in that same commit.

### Step 2 — foundation slice

The foundation slice extracts the pieces every later slice will import —
the `domain` values and the `ports` Protocols — **while the old package stays
where it is and re-exports them**. Nothing moves yet. At the end of this slice
every existing import still works, `lint-imports` passes, and the tree contains
one copy of each extracted definition rather than two.

Do it first, and separately, for a reason worth stating: the extraction is the
part that changes code, and the move is the part that changes paths. Done
together, `git` sees a rewritten file rather than a rename and `git log
--follow` is lost, so the two halves are separated by the same constraint that
makes `git mv` worth insisting on (step 3).

**What "re-exports" means here, and why it is not a shim.** The Overview says
clean break, no shims, and this does not contradict it. A shim is a
compatibility layer that *survives the campaign* so outside callers keep
working. This re-export lives inside one migration, is deleted by the move
slice that follows it, and exists so that the intervening commits are each
individually green. If a foundation slice's re-export is still there two slices
later, it has become a shim and the plan has drifted.

Concretely, in the old `src/redstring/<pkg>/thing.py`:

```python
from redstring.domain.thing import Thing, normalize_thing

__all__ = ["Thing", "normalize_thing"]
```

Three properties to hold to while writing it:

- **Move the definition, do not copy it.** Two definitions of `Thing` that are
  equal but not identical will pass most tests, and will fail the moment an
  `isinstance` check or a pydantic model validates an instance built by the
  other copy. The identity tests below exist to make that impossible, which is
  why they are written in this slice rather than after the move.
- **Extract downward only.** The foundation slice puts things in `domain` and
  `ports`, the two layers everything may import. Extracting into a sibling-band
  layer creates the cross-layer import you are trying to avoid, and it fails
  `lint-imports` in the same commit that introduces it.
- **A port extracted here brings its compliance tests with it.** Adding a read
  method to a `GraphStore`-shaped Protocol makes
  `tests/unit/graph/test_compliance_coverage.py` demand a mutation-isolation
  test and a tenant-isolation test for it by introspection. That is the same
  slice, not a follow-up.

**Write the identity tests now.**

```python
def test_domain_thing_is_the_one_the_old_path_exports() -> None:
    from redstring.domain.thing import Thing as new_thing
    from redstring.oldpkg.thing import Thing as old_thing

    assert new_thing is old_thing
```

`is`, not `==`. This is the one place in this codebase where identity is the
assertion and equality would be the bug — the inverse of the four
identity-vs-equality rows in `.claude/rules/recurring-defects.md` and
`CLAUDE.md`, where `is` passed only because a value sat in a CPython cache.
Here the claim *is* "one object, reachable by two names", and `==` would pass
against exactly the duplicated-definition mistake the test is for. Say so in a
comment; a future reader applying the recurring-defect rule mechanically will
otherwise "fix" it.

Two things these tests do that nothing else does:

- They fail if the old module re-implements rather than re-exports, which no
  behavioural test can see: two copies of a value type behave identically until
  something compares types.
- **They are the artifact you retarget on the move**, and that retargeting is
  the point. When step 3 deletes the old path, each identity test either
  becomes a test that the *new* path is what `redstring.__all__` exports, or
  it is deleted alongside the module it guarded. Do not let them rot into
  imports of a path that no longer exists — the `ModuleNotFoundError` guard in
  step 3 will catch that, loudly, which is the intended interaction between the
  two slices.

**The gate for this slice.** Every pre-existing import still resolves (the
whole suite is the assertion — it is full of them), the identity tests are
green, and `lint-imports` passes. Commit, then move. If the foundation slice
cannot be made green on its own, the layer assignment in the plan's table is
wrong, and the cheap moment to discover that is here rather than after a
`git mv`.

### Step 3 — move slice

This is the slice that changes paths, and only paths. The foundation slice
already changed the code; keep the two apart, because a file whose contents
changed in the same commit as its move is recorded as a delete plus an add and
`git log --follow` stops at the boundary.

**Move whole files with `git mv`, then check `git status`.**

```
git mv src/redstring/oldpkg/thing.py src/redstring/domain/thing.py
git mv tests/unit/oldpkg/test_thing.py tests/unit/domain/test_thing.py
git status --short
```

Every moved file must show `R` (rename), not a `D`/`A` pair:

```
R  src/redstring/oldpkg/thing.py -> src/redstring/domain/thing.py
```

`R` is not automatic and it is not guaranteed by `git mv`. Rename detection is
a similarity computation done at diff time, so a move plus enough edits to drop
below the threshold is recorded as delete+add however the file got there. If a
row shows `D`/`A`, revert the edits out of this commit and land them in the
next one. Do the import-rewriting pass **after** the move commit for the same
reason — the moved module's own `from redstring.oldpkg...` lines, and every
importer's, are content changes.

Two mechanical traps, both of which have shipped:

- **`git mv` leaves the old directory behind**, and a leftover directory —
  even one containing nothing but `__pycache__` — is a valid namespace package
  under PEP 420. `import redstring.oldpkg` then succeeds, the
  `ModuleNotFoundError` guard below fails, and if you skipped the guard the
  retired path silently still resolves. `rmdir` the old package directory and
  its test mirror, and let the sweep's `test -d` check confirm it
  (`sweep.sh` treats a leftover dir as fatal precisely because of this).
- **`git mv` does not stage the deletion of an untracked sibling.** Anything in
  the old package that was never committed — scratch files, a `.pyc`, a
  notebook — stays where it is and keeps the directory alive. `git clean -nd
  src/redstring/oldpkg/` before the `rmdir`.

**Mirror `tests/unit/<layer>/`.** The unit tree mirrors the source tree
package for package (`tests/unit/domain/`, `tests/unit/ports/`,
`tests/unit/graph/`, `tests/unit/extraction/`, …), so a module landing on
`domain` moves its tests to `tests/unit/domain/` in the same commit. Three
things about the mirror that are easy to get wrong:

- **Create `__init__.py` in a new test package.** Every existing
  `tests/unit/<layer>/` has one; a directory without it collides on module
  basename with any same-named test file elsewhere in the tree.
- **`tests/integration/` and `tests/compliance/` do not mirror and do not
  move.** They are organised by backend and by port contract rather than by
  layer, and they are deselected from the default run
  (`addopts = ["-m", "not accuracy and not integration"]`), so a broken import
  there is invisible to the commit gate. Repoint their imports in this slice
  and run them explicitly — see `docs/how-to/run-integration-and-mutation-suites.md`.
- **A moved test keeps its marker and its name.** Renaming a test in the move
  commit hides it from `git log --follow` exactly as renaming a module does,
  and the coverage ratchet reads the suite as a whole, so a test that
  disappears and reappears under a new name is indistinguishable from one that
  was deleted.

**Add a `ModuleNotFoundError` guard for the retired path.** The move is not
finished until something fails if the old path comes back. Nothing else checks
this: `lint-imports` sees only imports that exist, the sweep is a grep over the
working tree at one moment, and a resurrected namespace package produces no
error anywhere.

```python
import importlib

import pytest


@pytest.mark.unit
def test_the_retired_path_is_gone() -> None:
    """`redstring.oldpkg` moved to `redstring.domain` in <slice>.

    A leftover directory — `__pycache__` alone is enough — makes the old
    path resolve again as a PEP 420 namespace package, and nothing else in
    the suite notices.
    """
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("redstring.oldpkg")
```

Two details are load-bearing:

- **Use `importlib.import_module("...")` with the path as a string**, never a
  literal `import redstring.oldpkg` inside the `raises` block. The string form
  is what keeps the guard out of the sweep's import-shaped grep — a guard
  written with a real `import` statement is itself a fatal sweep finding, and
  the usual "fix" is to add the guard's file to an exclusion list, which is how
  the sweep starts rotting.
- **Say in the docstring where the module went and when.** The guard outlives
  everyone's memory of the migration, and a bare
  `pytest.raises(ModuleNotFoundError)` with no explanation reads to the next
  author as dead weight to delete.

Put the guard beside the other whole-surface tests at `tests/unit/` root
(`test_public_surface_is_self_contained.py` and `test_end_to_end_example.py`
live there) rather than inside the layer that received the module — the claim
is about the package as a whole, and the file it would otherwise sit in is one
`git mv` away from moving again.

**The gate for this slice**: `git status` shows `R` for every moved file, the
old directories are gone, `lint-imports` passes (this is the first commit where
it can actually fail — the module is now on its new layer), the guard test is
green, and `sweep.sh` reports no fatal finding. Commit; the pre-commit hook
runs the rest.

### Step 4 — docs/meta slice

The move is done; this slice is what stops the *reasoning* for it from being
lost. Four artifacts, all of them real files in this repo, and nothing else —
the list is short because most of the places a migration would traditionally
announce itself do not exist here.

**1. An ADR under `docs/adr/`.** Required when the migration changed the
layered import contract; `.claude/rules/definition-of-done.md` item 3 names
"changes to the layered import contract in `pyproject.toml`" as
architecturally significant by definition. A pure relocation that leaves the
`layers` list untouched does not need one.

Four conventions to follow, each visible in the existing files:

- **Do not allocate a number.** Numbers are allocated **at merge**, against
  the highest on `main` at that moment, so every parallel branch drafts the
  same next one and whichever merges second renumbers
  (`.claude/rules/definition-of-done.md` item 5). Name the file
  `0007-<descriptive-slug>.md` and cite it **by slug**, never by number —
  `docs/adr/` currently holds eight drafts sharing `0007`, which is the rule
  working rather than failing.
- **Open with a `**Status:**` line** saying accepted and naming the slice:
  `**Status:** accepted, slice 10 of the ring migration.` Every ADR here has
  one on line 3.
- **Say why it is an ADR.** The strong examples
  (`0007-composition-is-the-only-top-layer.md`) open with a "Why this is an
  ADR" paragraph explaining that the decision *looks* like a convenience and
  is not. That paragraph is what stops the next author reverting the placement
  as over-engineering — which, for the sibling band, is exactly the risk.
- **Amend prior ADRs by their Status line, never their body.** If the move
  changes something a prior ADR decided, add an "Amended by" / "Superseded by"
  pointer to that ADR's `**Status:**` and write the new record. ADR bodies are
  immutable; never rewrite a Decision retroactively
  (`.claude/rules/definition-of-done.md` item 2).

**2. The `docs/plans/ring-migration.md` index.** That file's "Decisions with
their own record" section holds **two** tables — allocated numbers, and drafts
still carrying `0007` — and states the invariant that binds them: *"The two
tables together are the whole of `docs/adr/`; if a file there appears in
neither, one of them is stale."* A new ADR therefore lands a row in the drafts
table, keyed by slug, in the same commit as the ADR itself. If the migration
also moved a module whose module-docstring argument is listed further down that
section (`temporal/inference.py`, `projections/graph.py`,
`extraction/schema_org.py`, …), fix the path there too.

**3. The `CLAUDE.md` architecture block.** Its "Architecture contract" section
restates the layer diagram and a condensed version of the sibling-band
reasoning. Update it whenever the `layers` list changes. This is the update
most worth doing carefully: `CLAUDE.md` is binding instruction read as current
by construction, so a stale diagram there sends the next session to a package
that does not exist, and the file itself says to keep the block in step.

**4. Inline reasoning in `pyproject.toml`.** The `layers` list is the source
of truth — the only copy the gate reads — and it carries the full argument as
comments beside each line. Two habits the existing comments demonstrate, both
of which pay off during a migration:

- **A removed layer leaves its comment behind, in the past tense.** `services`,
  `models`, `db`, `schemas`, `cache`, `config`, `context` and `encryption` all
  have a comment saying they left and in which slice, sitting where the layer
  used to be. That is deliberate: the comment answers "why is there no service
  layer?" at the point someone is about to add one back. Write the same when
  you dissolve a package.
- **Name the check the contract cannot perform.** The `llm` comment ends by
  pointing at `tests/unit/llm/test_port_does_not_leak.py`, because
  import-linter cannot see third-party imports. Any confinement in the same
  shape gets the same pointer.

#### Three things this slice deliberately does not do

Migration checklists from other repositories call for these; none applies here,
and adding one would be inventing an artifact rather than updating one.

| Not this | Because |
|---|---|
| An mkdocs nav entry | There is no `mkdocs.yml` and no site build. `docs/` is read in the repository. |
| A `scripts/validate_examples.py` run | `scripts/` holds one file, `coverage_ratchet.py`. The example is validated by `tests/unit/test_end_to_end_example.py`, which *runs* `docs/examples/build_a_graph.py` under the ordinary suite — so it is already covered by the commit gate and needs no separate step. |
| A CHANGELOG `BREAKING:` entry | There is no `CHANGELOG.md`. The retired path is announced by the `ModuleNotFoundError` guard from step 3 and by the commit message, which is the immutable, correctly-scoped place for specifics that decay (`.claude/rules/commits.md`). |

There is also no `docs/adr/index.md` and no `.claude/rules/architecture.md` —
the index lives in `docs/plans/ring-migration.md` and the architecture rules
live in `CLAUDE.md`. If a checklist you are working from names either, it is
from a different tree.

**The gate for this slice.** `sweep.sh` reports no fatal finding — note that
`docs/adr/`, `docs/plans/` and `docs/history/` are by-design exclusions, so the
sweep will *not* tell you the index row is missing; check the two-tables
invariant by eye. Then commit, and let pre-commit run the rest. Anything this
slice noticed and did not fix goes into `BACKLOG.md` in the same commit.

## Deferred work goes in `BACKLOG.md` — same commit, deleted when fixed

A migration turns up more deferrable work per commit than any other kind of
change, because it reads every line of a package that nobody has read in
months. The project's hardest rule applies to all of it: **anything you notice
and do not fix lands in `BACKLOG.md` in the commit that passes it by**, and a
commit that defers something without touching `BACKLOG.md` is incomplete
(`CLAUDE.md`; `.claude/rules/definition-of-done.md`, first section;
`.claude/rules/commits.md`, "Scope"). There is no substitute — not a TODO
comment, not the PR body, not the commit message, not a line in the plan.

### What a migration defers, specifically

Five shapes recur, and four of them are invisible to every gate:

| You did this | The entry says |
|---|---|
| Moved a module you could not fully understand | What you could not work out, and what a reader would need to check before changing it. A placement made on incomplete understanding is a decision, and the reasoning that made deferring right is the expensive part. |
| **Deleted** rather than ported | What the deleted code did that nothing now does, and the `recovery/<name>` tag or commit sha it is recoverable from. `B47` is the model: five paths, each verified resolvable, plus a paragraph per module on what it uniquely did. |
| Skipped, deselected, quarantined or weakened a test in the move | Which test, why the move made it fail, and whether the failure is about the test or the code. A test that stops running during a rename is the easiest thing in this workflow to lose. |
| Left a cross-layer import unresolved behind a placement you are not sure of | The import, the two layers, and which of the three responses in "Cross-layer imports" you rejected and why. |
| Moved coverage | Whether the movement is real. Deleting well-covered legacy code lowers the ratio while removing nothing that was tested — see `B14` and `docs/reference/quality-gates.md`. The justification goes in the commit message; the entry is only for a drop you accepted and intend to repay. |

The deletion row is the one worth over-serving. Slice 8 deleted roughly 1700
lines of timeline code, and `B47` is the only reason those five modules are
findable at all — an entry reading "port the timeline modules" would have
thrown away the part that cost something to learn.

### Writing the entry

- **Pick the next unused `B` number.** The numbers are opaque handles, not a
  taxonomy — `BACKLOG.md`'s "How to read this file" says so, and explains why
  renumbering was considered in slice 11 and deliberately rejected (fifteen
  open ids are cited by name across about twenty-eight files under `src/` and
  `tests/`).
- **Put it in the section a reader would search**, of the six: wrong answers
  in shipped code; things that are unverified; performance and scale; the test
  suite itself; capabilities deliberately not built, with the route back;
  tooling, packaging and hygiene. Most migration deferrals land in the fifth
  or the fourth. Ordering *within* a section is roughly by priority; ordering
  between sections carries no meaning.
- **Name the file and line, say what is actually wrong, and say what you
  learned that made you defer rather than fix.** An entry that only says
  "clean up X" has discarded the expensive half.
- **Write paths as they will be after the move.** An entry filed mid-migration
  that names the retired path is stale on the next commit, and the sweep will
  not tell you: `BACKLOG.md` is not a by-design sweep exclusion, so a retired
  path left in an entry is a *fatal finding* in the very next sweep. That is
  the intended interaction — fix the entry, do not exclude the file.

### Deleting entries

**When you fix a backlog item, delete its entry in the same commit.** A
migration fixes them by accident more often than by intent: dissolving a
package can close an entry about a module that no longer exists, and moving a
module onto its proper layer can close one about an import that can no longer
be written. Before the docs/meta slice, grep `BACKLOG.md` for the package you
just retired and settle every hit — either the entry is closed and goes, or it
survives with updated paths.

Two closure conventions, both already load-bearing here:

- **A closed entry is deleted, not marked done.** `BACKLOG.md` says so, and
  the record of what it was lives in the commit message that closed it.
- **Do not delete an id that shipped code cites.** Eight closed ids (B10b,
  B10d, B26, B33, B34, B40, B55, B56) are indexed in
  `docs/plans/ring-migration.md` precisely so the pointers left in source and
  tests still resolve. If the entry you are closing is named in a comment or a
  docstring, either remove that citation in the same commit or add the id to
  that index — a dangling `B` reference in shipped source is exactly the kind
  of rot this file exists to prevent.

### The one deferral this skill's own removal would need

If the retargeting described here is not taken and the skill is deleted
instead, that is itself deferred work: remove `SKILL.md` and `sweep.sh`, and
record the removal *and its reasoning* in `BACKLOG.md` in the same commit.
Deleting a how-to because it was aimed at the wrong repository is a decision
someone will otherwise re-derive from scratch.

## Sweep — whole repo, denylist not allowlist

Run it from the repo root, with the *retired top-level package name* as the
only argument:

```
.claude/skills/migrating-modules-to-rings/sweep.sh timeline
```

It greps for `redstring.<pkg>` across the whole tree and exits nonzero on a
fatal finding. Run it in the move slice (step 3), again in the docs/meta slice
(step 4), and once more after every sibling merge — a sibling branch can
reintroduce a reference to a path you retired, and neither branch's CI sees it.

### The two fatal checks

1. **Import-shaped references** — `from redstring.<pkg> …` or
   `import redstring.<pkg>` — anywhere outside the by-design exclusions
   below. These are the ones that break at runtime.
2. **Leftover directories** at `src/redstring/<pkg>` or `tests/unit/<pkg>`.
   A directory containing nothing but `__pycache__` is still a valid PEP 420
   namespace package, so `import redstring.<pkg>` keeps resolving and the
   `ModuleNotFoundError` guard from step 3 is the only other thing that would
   notice. `git mv` does not remove the source directory; `rmdir` it, and
   `git clean -nd` it first for untracked debris.

`tests/integration/` and `tests/compliance/` are **not** checked for leftover
directories: they are organised by backend and by port contract rather than by
layer, so they stay where they are and only their imports are repointed. Their
imports *are* covered by check 1 — which matters, because both are deselected
from the default run (`addopts = ["-m", "not accuracy and not integration"]`),
so a broken import there is invisible to the commit gate. See
`docs/how-to/run-integration-and-mutation-suites.md`.

Everything else — bare mentions in prose, comments, logger names — prints as a
**non-fatal triage list**. Read it. Some entries are intentional; some are a
stale sentence in `README.md` or under `docs/reference/`, which is what the
docs/meta slice is for.

### Denylist, not allowlist

The script sweeps `.` and excludes named directories. That direction is the
whole design. Directory *allowlists* — "check `src/` and `tests/`" — have
rotted every time they have been tried, because the list is written once and
the repository keeps growing: a stale import survives in whatever directory
was added after the list. In this tree the places an allowlist would have
missed are `README.md` (which carries a layer-by-layer table naming
`redstring.domain`, `redstring.ports` and the rest),
`docs/reference/domain-value-types.md`, `docs/how-to/`, and
`docs/examples/build_a_graph.py` — that last one is *executed* by
`tests/unit/test_end_to_end_example.py`, so a stale import in it fails the
ordinary suite rather than merely reading wrong.

When you add an exclusion, you are asserting that references there are correct
by design. That is a strong claim; make it in the script's header comment, in
the same sentence as the reason, so the next person can tell an argued
exclusion from a silenced finding.

### The by-design exclusions

Four locations, and each earns it differently:

| Excluded | Why references there are correct |
|---|---|
| `docs/adr/` | ADR bodies are immutable. An ADR that decided something about `redstring.<pkg>` still names it after the package is gone; the amendment goes in a *new* record's Status line, never by editing the old body (`.claude/rules/definition-of-done.md` item 2). |
| `docs/plans/` | Live plan artifacts. A migration plan's move list is *made of* old paths — that is its job (step 1). |
| `docs/history/` | Archived plans, kept unchanged. `docs/history/2026-08-ring-migration-plan.md` is the account of the campaign that retired most of these paths. |
| The public-surface guard tests | `tests/unit/test_public_surface_is_self_contained.py` and `tests/unit/test_end_to_end_example.py` walk the whole package and name module paths as *data*. |

Two things deliberately **not** excluded, and both catch real findings:

- **`BACKLOG.md`.** An entry filed mid-migration that names the retired path
  is a fatal finding on the next sweep. That is the intended interaction —
  rewrite the entry with post-move paths, do not add an exclusion.
- **`CLAUDE.md` and `pyproject.toml`.** The architecture block and the
  `layers` list are supposed to be current. A hit in either is the docs/meta
  slice telling you it is not finished.

Note what the exclusions cost you: the sweep is silent about `docs/plans/`, so
it will *not* tell you the `ring-migration.md` ADR index row is missing. Check
that invariant by eye (step 4).

### Guard tests must not trip the sweep

A `ModuleNotFoundError` guard written with a literal `import redstring.oldpkg`
inside `pytest.raises` is itself an import-shaped reference, and the sweep will
call it fatal. The repair is *not* an exclusion — it is
`importlib.import_module("redstring.oldpkg")`, where the path is a string the
grep cannot see. Adding the guard's file to `GREP_EXCLUDES` is exactly how a
denylist starts becoming an allowlist: one file at a time, each with a
plausible reason.

### Prove it can fail

Before believing a clean sweep, run it against a package that still exists —
`sweep.sh domain` should print dozens of hits and exit 1. A sweep that returns
clean because the grep pattern is wrong, or because the exclusion list swallowed
the tree, is indistinguishable from a finished migration. This is the same
instruction as "a passing check you have never seen fail is not yet evidence"
(`CLAUDE.md`), applied to a shell script.

## Sweep — the configuration spots the grep cannot reach

`sweep.sh` finds *import-shaped* references. Configuration is the blind spot:
`pyproject.toml` and `cosmic-ray.toml` name packages as **paths and glob-free
strings**, and a stale one there either fails loudly at the wrong moment or —
worse — silently narrows what a tool looks at. Check all four by hand in the
docs/meta slice.

The good news, and the reason this is a short checklist rather than a chore:
**every one of them is currently written at directory granularity, so a move
*within* `src/redstring/` touches none of them.** That is deliberate, and
keeping it that way is the real instruction. What follows is what to check,
and what would make each one start needing maintenance.

### 1. import-linter `layers` and `containers`

```toml
[tool.importlinter]
root_packages = ["redstring"]

[[tool.importlinter.contracts]]
name = "Layered architecture"
type = "layers"
containers = ["redstring"]
layers = [ ... ]
exhaustive = true
```

- **Layer names are bare** (`"projections"`, not `"redstring.projections"`)
  because they are relative to the container. A migration that spells a new
  layer with the package prefix gets a contract that resolves to nothing.
- **Dissolving a top-level package means deleting its line from `layers`** —
  and, per step 4, leaving a past-tense comment where it was. Every removed
  layer here has one (`services`, `models`, `db`, `schemas`, `cache`,
  `config`, `context`, `encryption`), each naming the slice it left in. The
  comment is the answer to "why is there no service layer?" at the moment
  someone is about to add one back.
- **`exhaustive = true` is what makes the gate fire on a *new* package.**
  Without it, a top-level package absent from `layers` is simply unconstrained
  and the contract passes. With it, the run fails until the package is placed
  deliberately. That option caught zero violations for its whole life, which
  is indistinguishable from being inert — slice 9 proved it bites by adding a
  throwaway package, watching the contract break, and removing it. If you
  change anything structural here, prove it can fail the same way.
- **Adding a *new* top-level package is a contract failure before it is
  anything else**, so the `layers` edit and the ADR land in the same commit as
  the package. That is the sequence `exhaustive` is designed to force.

### 2. `[tool.mutmut]` — and note the key is not `only_mutate`

```toml
[tool.mutmut]
paths_to_mutate = ["src/redstring/"]
tests_dir = ["tests/unit/"]
runner = "uv run pytest -x -q --no-header -p no:randomly"
also_copy = ["pyproject.toml", "tests/conftest.py"]
```

mutmut 3.x spells it `paths_to_mutate`; `only_mutate` is from an older
generation of the tool and from other repositories' configs. Searching for
`only_mutate` here finds nothing, and adding it configures nothing — it is
accepted silently, which is the failure mode this whole document keeps
warning about.

The value is the package root, so no per-module maintenance. Two things a
migration can still break:

- **`tests_dir` is `tests/unit/` only.** A moved test that lands outside the
  unit mirror (see step 3 — `tests/integration/` and `tests/compliance/` do
  not mirror) stops contributing to mutation kills without any signal.
- **`also_copy` is what survives into mutmut's scratch tree.** If a migration
  makes the suite depend on a new root-level file — a fixture module, a data
  file — it must be added here or every mutant dies on a collection error, and
  a run where every mutant dies reads exactly like a perfect one.

### 3. `cosmic-ray.toml`

```toml
[cosmic-ray]
module-path = "src/redstring"
timeout = 60.0
test-command = "uv run pytest -x -q --no-header -p no:randomly tests/unit"
excluded-modules = []
```

Again package-wide. Three notes:

- **`excluded-modules` is empty, and empty is the intended state.** It is an
  exclusion list, and per `CLAUDE.md` an exclusion over an empty set excludes
  nothing — so if a migration is tempted to add a path here, that is a visible
  decision in review, and any staleness guard you write over it must be a test
  that its entries still name real files. An entry naming a module you have
  just moved matches nothing and passes silently.
- **`test-command` names `tests/unit` explicitly**, so it has the same
  exposure as `tests_dir` above.
- **Prove the harness before reading a run.** cosmic-ray's `local` distributor
  mutates the working tree in place, so runs happen in a separate worktree —
  and a worktree is exactly where a missing extra goes unnoticed. Execute
  `test-command` unmutated in that environment and require it green first.
  Slice 7's `0 survivors out of 426` was a worktree synced without
  `--all-extras`; every mutant "died" on a collection error and `cr-report`
  showed `KILLED` for all 426. See
  `docs/how-to/run-integration-and-mutation-suites.md`.

### 4. pytest selection args

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = ["-m", "not accuracy and not integration"]
markers = ["unit", "integration", "accuracy", "slow"]
```

`addopts` deselects the `accuracy` and `integration` suites from the default
run, and therefore from the commit gate — which keeps it infra-free and fast,
and means **a migration can leave `tests/integration/` and
`tests/compliance/` importing a retired path and see nothing but green.** The
sweep's import check is what catches that; run the suites explicitly as well
(a CLI `-m` overrides the config one).

Three smaller traps:

- **`testpaths = ["tests"]`** is a root, not a list of layers, so mirroring a
  new `tests/unit/<layer>/` needs no edit here — but the new directory does
  need its `__init__.py` (step 3).
- **A moved test keeps its marker.** Dropping `@pytest.mark.integration` in a
  move quietly promotes the test into the commit gate, where it will look for
  a backend that is not running.
- **Do not add a path to `addopts` to skip something a migration broke.** That
  is a deferral, and it belongs in `BACKLOG.md` with the reason, in the same
  commit.

### Prove each edit, do not read it

The rule that governs this whole section is the one from `CLAUDE.md` about
measuring an exemption: **the command that measures a configured constraint
must be subject to that constraint.** `uv run ruff check --select ANN,TC
src/redstring/events/` printed "All checks passed!" unconditionally, because
`per-file-ignores` applied on top of `--select`; naming files explicitly on a
mypy command line likewise bypasses `exclude` and answers a different question
than the configured run. So after editing any of the four spots above, run the
**configured** gate — `uv run lint-imports`, the plain `uv run pytest`, the
configured mutation command — rather than a hand-built invocation that names
the paths you just changed.

## Gates — the gate is `git commit`

There is no `make check`, no `Makefile`, no `mkdocs build`, and no
`scripts/validate_examples.py` in this repository. The whole quality gate is
wired into `pre-commit` and runs on `git commit`, in this order
(`.pre-commit-config.yaml`, with `fail_fast: true` — the first failing hook
stops the run, so fix and re-commit rather than reading ahead):

| Hook | What it covers during a migration |
|---|---|
| whitespace / EOF / YAML / TOML / JSON / merge-conflict / large-file / `check-ast` / `check-docstring-first` | Catches a half-resolved seam file and a `pyproject.toml` broken by a `layers` edit before anything slow runs. |
| `ruff check --fix` then `ruff format` | Reformats moved files **in place**. |
| `mypy` (`pass_filenames: false`, so the configured whole-package `--strict` run) | The move's real type gate. It is not per-file: a moved module is checked together with everything that imports it. |
| `bandit` (`src/` only) | Unchanged by a move. |
| `lint-imports` (`files: ^(src/\|pyproject\.toml$)`) | The layer contract. Note the trigger: a commit touching **only tests or docs does not run it**, which is why the move slice and not the docs slice is where placement is proven. |
| `pytest` + coverage ratchet (`scripts/coverage_ratchet.py`, `files: ^(src/\|tests/\|pyproject\.toml$\|scripts/coverage_ratchet\.py$)`) | The suite, plus the ratchet against `.coverage-baseline`. Coverage may never fall; a rise raises the baseline and stages it automatically. |

**Do not run ruff, mypy, bandit, `lint-imports` or pytest as separate steps
before committing.** The hook does all of it, and running them by hand
duplicates the slowest work in the loop. `CLAUDE.md` states this as a project
rule, and it has a second edge specific to migrations: the hand-built
invocation is usually a *different* check from the configured one — naming
files on a mypy command line bypasses `exclude`, and `ruff check --select` is
still subject to `per-file-ignores`. Write the change, then commit, and let the
configured gate answer.

**Expect the first commit of a move slice to fail, and expect the fix to
already be applied.** `ruff check --fix` and `ruff format` rewrite files in
place; when they do, the hook reports failure with the file modified. Re-`git
add` the files and commit again. This is normal on a move slice, because a
relocated module usually crosses a formatter boundary (import block ordering
changes with the new path). It is not a signal that anything is wrong.

**Prefer many small commits.** Each commit runs the whole gate, so small
commits keep each run fast and — the part that matters during a migration —
keep the failure surface legible. A slice that moves eleven modules and edits
`__all__` in one commit produces a mypy failure list that does not say which
move caused it. The slice list from step 1 is already commit-sized; keep it
that way, and split further when a slice's gate output stops being readable.
Prefer, in order: the pure deletion, the foundation extraction, one `git mv`
commit, the import-rewrite commit, the docs/meta commit
(`.claude/rules/commits.md`, "Scope").

Two ordering consequences worth internalising:

- **The rename and the content edit are separate commits anyway** (step 3), and
  the gate is why that costs nothing: the `git mv` commit is usually green
  without further work, and the import-rewrite commit is where mypy and
  `lint-imports` have something to say.
- **A commit that defers anything must touch `BACKLOG.md` in the same
  commit.** No hook enforces it; it is the one gate that is on you
  (`.claude/rules/definition-of-done.md`).

What `git commit` does *not* cover — the `integration` suite, the compliance
suites, and mutation runs — is the next section.

## Gates — what `git commit` does not cover

Three families of checks are deliberately outside the commit gate, and a
migration is exactly the change that breaks all three without turning the
commit red. Run them by hand in the move slice; the full invocations,
environment variables and failure modes are in
[`docs/how-to/run-integration-and-mutation-suites.md`](../../../docs/how-to/run-integration-and-mutation-suites.md).

**Sync first, every time, in whatever checkout you are about to run in.**

```
uv sync --all-extras
uv run python -c "import neo4j, asyncpg, langchain_openai; print('extras ok')"
```

`--all-extras`, not `--extra dev`. This is not boilerplate during a migration:
`uv add` and `uv remove` re-sync as a side effect and can narrow the installed
extras back to `dev`, and both suites below fail *quietly* when they are
missing — the integration suite gets smaller, the mutation run gets perfect.

### The `integration` suite

`addopts = ["-m", "not accuracy and not integration"]` deselects it, so **a
migration can leave every adapter test importing a retired path and see nothing
but green.** `tests/integration/` does not mirror the layer tree and does not
move (step 3); only its imports are repointed, and the sweep's import check
plus this run are the only two things that verify the repointing.

```
docker compose -f docker-compose.test.yml up -d neo4j postgres   # wait for (healthy)
uv run pytest -m integration                                     # serial; never -n auto
```

A CLI `-m` overrides the config one. Run it whenever a move touched
`graph/adapters/`, `vector/adapters/`, `llm/adapters/`, or a port they
implement — the Cypher and the SQL execute nowhere else. The precedent is
**B10a**: an interrupted mutation run left corrupt source in
`graph/adapters/neo4j.py` and the whole default suite passed with it applied,
because every test for that module is `integration`-marked.

Do **not** reach for `-n auto` to speed it up. Neo4j Community allows one
database, so xdist workers wipe each other's data (**B10f**); the pgvector
suite is parallel-safe only because its table is named per worker.

### The two compliance suites — separate invocations, always

`tests/compliance/graph_store.py` and `tests/compliance/vector_store.py` are
shared base classes, subclassed once by the in-memory adapter under
`tests/unit/` and once by the real one under `tests/integration/`. Their
`@given` methods are defined **once, on the base**, and hypothesis attaches
per-test state to the function object rather than to the subclass. Put both
subclasses in one process and every property fails:

```
# WRONG — 21 failures for graph, 13 for vector, none about the code
uv run pytest -m "not accuracy" tests/unit/graph tests/integration/graph
```

```
hypothesis.errors.FailedHealthCheck: The method
GraphStoreCompliance.test_… was called from multiple different executors
```

Two invocations, never one (**B10m**):

```
uv run pytest tests/unit
uv run pytest -m integration
```

Three consequences for a migration. **Never widen the marker expression** to
prove a move did not break either adapter — the failures look like flakiness
and are not. **If a move adds a method to a port**,
`tests/unit/graph/test_compliance_coverage.py` derives the required list from
the Protocol by introspection and demands a mutation-isolation test and a
tenant-isolation test in the same slice; both land in the shared suite, so both
run twice, once per invocation. And **while iterating**, turn the cost down
with `KG_COMPLIANCE_MAX_EXAMPLES=10` rather than reaching for xdist — but read
the default (50) before believing a green run, because the variable is read at
module import and lowering it changes which boundary values are drawn at all.

### Mutation runs — on demand, and never from an unproven harness

Run these when a slice rewrote logic rather than merely moved it; a pure
`git mv` cannot change a mutation score.

```
uv run mutmut run
uv run cosmic-ray init cosmic-ray.toml session.sqlite
uv run cosmic-ray exec cosmic-ray.toml session.sqlite
uv run cr-report session.sqlite
```

Migration-specific hazards, all three of which have fired here:

- **cosmic-ray's `local` distributor mutates the working tree in place**, so it
  runs from a separate worktree — and a worktree is precisely where a missing
  extra goes unnoticed. Execute the configured `test-command` unmutated in that
  environment and require it green *before* reading any result. Slice 7's
  `0 survivors out of 426` was a worktree synced without `--all-extras`; every
  mutant died on a collection error and `cr-report` showed `KILLED` for all of
  them. **A zero-survivor run is the result most in need of suspicion.**
- **Both runners point at `tests/unit/` only** (mutmut's `tests_dir`,
  cosmic-ray's `test-command`). A test that a move landed outside the unit
  mirror stops contributing kills with no signal at all.
- **Never gate on a raw survivor count.** Every module here has
  `from __future__ import annotations`, so cosmic-ray rewrites the `|` in
  `X | None` as eleven other operators and no test can kill any of them — a
  gate on survival percentage rewards deleting type annotations. The bar is
  that every survivor is understood; classify them by diff hunk first.

None of this belongs in the commit gate, and none of it is optional after a
move that touched an adapter, a port, or a test's location. What you skip goes
in `BACKLOG.md` with the reason, in the same commit.

## Common mistakes

| Mistake | Reality |
|---|---|
| Sweep a directory allowlist | Allowlists rot (missed bench/ twice, examples/ once); sweep the whole repo via `sweep.sh` |
| Pick "next ADR number" from local docs/adrs/ | Sibling branches claim numbers concurrently; check open PRs first |
| First commit fails → assume error | pre-commit's ruff-format hook reformats and fails the commit; re-add and re-commit |
| Poll `gh pr view --json mergeable` right after push | Eventually-consistent; reads UNKNOWN/stale for a minute or two |
| `git rm`/`mv` then move on | Leftover dirs/`__pycache__` = silent namespace packages; `test -d` for the old dirs |
| "Strict docs build will catch nav" | It won't; add new pages to mkdocs nav by hand |
| Tree-wide `ruff format` | Collides with parallel campaigns; format only touched files. Shared files (`__init__.py`, pyproject.toml): re-read immediately before each surgical single-line edit |
| "Keep a shim for safety" | No shims. Clean break + BREAKING changelog entry |
| Edit an old ADR body | Amendments are Status-line pointers only |
