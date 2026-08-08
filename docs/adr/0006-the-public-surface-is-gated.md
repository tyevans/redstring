# ADR 0006: The public surface is gated by three tests, not curated

**Status:** accepted, slice 10 of the ring migration. **Amended by**
[`0028` a capability declares its own release](0028-a-capability-declares-its-own-release.md),
which put a type on the surface that check 1 as decided here could not see —
see Consequences.

**Why this is an ADR:** the three tests look redundant, and each is blind to
the failures the other two catch. Deleting any one of them leaves a hole that
nothing reports. This records why there are three.

## Context

`redstring.__all__` is the whole promise. Anything reached through a dotted
path is internal and may change without notice. That claim is only worth
something if it is checked, and the natural check — "does `__all__` name things
that exist" — is the weakest of the available ones. Ruff's F822 already does it
and it catches almost nothing that matters.

## Decision

Three tests, each of which exists because the other two cannot see its failure:

1. **Every exported name's signature mentions only exported types.**
   (`tests/unit/test_public_surface_is_self_contained.py`.) A signature naming
   an unexported type is an unusable export — the caller cannot construct the
   argument. F822 is blind to this: the `__all__` entry resolves fine.

2. **Every `RedstringError` subclass is either exported or listed** against the
   capability whose export would bring it. A *signature* gate cannot see
   exceptions at all — removing `MissingEntityError` from `__all__` passes
   check 1 unchanged, and the caller then cannot write an `except` clause for
   an error the library documents as raisable.

3. **The end-to-end example imports nothing but `redstring`**, asserted by
   walking its AST (`tests/unit/test_end_to_end_example.py`, over
   `docs/examples/build_a_graph.py`). Without it the example can reach into an
   adapter module and pass while the exported surface is empty — the example
   would demonstrate a library nobody can use through its public API.

## Evidence the gate is worth more than review

Review of the same diff found **four** surface leaks. The gate found
**fourteen more**, including `_Auto` — a private class name — sitting in
`build_graph`'s public signature, which neither the reviewer nor the controller
caught while reading the diff.

`__all__` went from 30 names to 62, and most of the growth was transitive
closure rather than new capability: exporting `Entity` obliges `TemporalExtent`,
which obliges `DatePrecision`.

## Two mechanics that will recur

- **The check must walk the MRO.** A body-only version reported
  `GraphProjection` clean, because it declares no `__init__` — while the
  constructor a caller actually calls, `StoreProjection.__init__`, took five
  foreign types.

- **Exporting one name pulls its closure.** Exporting `DomainSchema` alone
  would have satisfied the letter of the finding and left it unconstructible.
  Expect the next capability exported to bring its own closure. The gate makes
  that visible at the moment it happens, which is the point of it.

## Consequences

- Adding an export is more expensive than it looks, and deliberately so: the
  closure comes with it, and the closure is the part a caller needs.
- Removing an export is a visible breaking change rather than a quiet one.
- **A base class is part of the surface, and check 1 was written in terms of
  signatures.** 0028 made every capability protocol inherit `AsyncClosable`,
  so `async with store` is supported against a port — while the type is named
  by no parameter and no return, and the check was silent. Check 1 therefore
  reads inherited *types* as well as inherited annotations: it is the same MRO
  mechanic recorded above, applied to the base list rather than to what the
  bases declare. Private bases are excluded, and foreign ones stay the
  business of `DOCUMENTED_FOREIGN_TYPES`, which is about types a signature
  forces a caller to name.
- The gate constrains *shape*, not taste. It cannot tell you an export is a bad
  idea — only that it is complete.
- `packaging` is proven rather than inferred alongside this:
  `tests/integration/test_wheel_contents.py` builds a wheel,
  installs it into a throwaway venv, renders all six domain schemas, and
  asserts the import resolved to `site-packages`, so a shadowing checkout
  cannot pass it. It was verified non-vacuous by excluding the YAML and
  watching it fail. It is `integration`-marked and therefore **not in the
  default gate** — run it before a release, or ship a wheel whose every domain
  id raises `KeyError` with the whole suite green.
