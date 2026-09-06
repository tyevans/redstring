# ADR 0047: Four adapter import paths are stable, without being exported

## Status

Accepted.

Amends [ADR 0006](0006-the-public-surface-is-gated.md), which otherwise
**stands**: `__all__` remains the public API, the three gates that keep it
honest are unchanged, and nothing here adds a name to it. What this ADR
qualifies is 0006's second sentence — that everything reached by a dotted path
may change without notice — by naming four paths that may not.
Relates to [ADR 0002](0002-two-store-ports.md), [ADR
0008](0008-the-two-non-store-ports.md) and [ADR
0017](0017-the-embedding-provider-port.md), all of which **stand**: the ports
are unchanged and this is about where their implementations live, not what
they promise.
Relates to [ADR 0033](0033-the-compliance-suites-ship.md), which **stands** and
is the precedent — `redstring.testing` is a second gated surface reached
without being in `__all__`, so a promise about something outside `__all__` is
not a new kind of thing here.

Closes BACKLOG B103.

## Context

`redstring/__init__.py` stated the contract plainly: `__all__` is the whole
promise, and anything reached through a dotted path "is internal and may change
without notice, including in a patch release."

The adapters a caller actually deploys are all on dotted paths:

- `redstring.graph.adapters.neo4j.Neo4jGraphStore`
- `redstring.vector.adapters.pgvector.PgVectorStore`
- `redstring.llm.adapters.langchain.LangChainLlmProvider`
- `redstring.llm.adapters.langchain_embedding.LangChainEmbeddingProvider`

The only `LlmProvider` and `EmbeddingProvider` in `__all__` are
`FakeLlmProvider` and `FakeEmbeddingProvider`. So **the supported surface was
the one nobody ships**, and the README's own quickstart imported from a path
the package described as liable to change in a patch release. A consumer
following the documentation could not write an import the library would stand
behind.

**The reason for not exporting them is good and is not reversed here.**
Exporting `LangChainLlmProvider` makes `import redstring` import LangChain;
the same goes for neo4j and asyncpg. The extras exist so that a caller pays
only for the backends they use, and that is worth more than the convenience of
a shorter import.

**The obvious fix is blocked by the architecture contract.** A
`redstring.adapters` namespace re-exporting them would import `llm`, `graph`
and `vector` — three siblings forbidden from importing each other — so it could
only sit on `composition`. And `pyproject.toml`'s contract says what to do with
such a candidate: ask what it composes, because a candidate that cannot name a
pair of layers forbidden from importing each other is a piece of one half
placed above it for convenience. A re-export shim composes nothing.
`context`, a re-export shim, was deleted in slice 10 for that reason.

## Decision

Those four **import paths** are stable. They will not move or be renamed
without a major version and a changelog note.

Nothing else changes. They are not exported, `__all__` is untouched, no module
is added to any layer, and `import redstring` stays lazy. Everything else about
the modules holding them remains internal: a private helper beside them may
change in a patch release, and nothing is promised about the rest of
`graph.adapters` or `llm.adapters`.

`tests/unit/test_deployed_adapter_paths_are_stable.py` enforces it by importing
each path and asserting the attribute resolves to a class.

**The alternative that gives a caller more was rejected for now.** A
module-level `__getattr__` on `redstring` — raising a helpful `ImportError`
naming the extra, with the names declared under `TYPE_CHECKING` so checkers
still resolve them — is what a caller actually wants, and it adds no module to
the contract. Its cost is that `__all__` stops being the literal list of what a
caller may use, which is the premise all three public-surface gates are built
on; they would each need to learn about it. That is a larger change to 0006
than this one, and it can be made later on top of this without being undone.

## Consequences

**A rename is now a decision made in review rather than a silent break.** That
is the whole mechanism, and it earned itself immediately: the first run of the
new test failed, because the path recorded in the backlog entry that motivated
it —
`redstring.llm.adapters.langchain.LangChainEmbeddingProvider` — does not exist.
The class lives in `langchain_embedding.py`. A promise written only in prose
was **already wrong about one of its four items** before it was made.

**The promise has two declaration sites, and that is deliberate.** The prose in
`__init__.py` is where a caller reads it; the list in the test is what can be
executed. A test asserts the two agree, so a fifth adapter added to one without
the other fails — the mitigation `recurring-defects.md` section 2 asks for when
a second site is genuinely required.

**This does not make the adapters part of the API.** Their constructor
signatures and behaviour are still covered by the ports and the compliance
suites, not by this ADR. What is promised is where they live, which is the only
part a consumer's `import` line depends on.

**The gate runs in the default suite, not behind `-m integration`.** Every one
of these adapters is importable without its backend installed — the guarded
import raises when the class is constructed, not when the module is read — so
the test needs no container. If that ever stops being true, the fix is the
adapter's import guard rather than a skip in the test.
