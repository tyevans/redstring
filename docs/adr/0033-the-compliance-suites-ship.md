# ADR 0033: The compliance suites ship

## Status

Accepted. **Amends
[`0006` the public surface is gated](0006-the-public-surface-is-gated.md)**:
there is now a second promised surface, `redstring.testing.__all__`, and 0006's
"anything reached by a dotted path is internal" describes the library rather
than the distribution. It does **not** supersede 0006 — the three checks over
`redstring.__all__` stand unchanged, and the new surface is gated separately
because none of the three can see it.

Also amends [`0007` composition is the only top layer](0007-composition-is-the-only-top-layer.md)
and [`0021`](0021-composition-holds-a-second-module.md): `composition` is no
longer the top layer of the import contract. It remains the top layer of the
*library*, and the argument both ADRs make — that a module there must name the
pair of layers it joins — is untouched. What sits above it is not a composer.

[`0002` two store ports](0002-two-store-ports.md),
[`0008` the two non-store ports](0008-the-two-non-store-ports.md),
[`0016`](0016-graph-store-is-five-capabilities.md),
[`0017`](0017-the-embedding-provider-port.md) and
[`0026`](0026-chunk-store-and-cache-are-capabilities-too.md) all **stand**. No
port changed. What changed is who can run the suites that check them.

## Context

Every port here has a shared compliance suite, and
`.claude/rules/recurring-defects.md` §1 makes running it the definition of a
correct adapter: "an adapter with only bespoke tests diverges silently from
its siblings; that is the single most expensive shape in this list."

**Until now, only adapters in this repository could obey that rule.** The
suites lived in `tests/compliance/`, which is not in the wheel. Someone
writing a `GraphStore` against a backend this project has never heard of got
the Protocol — which pins the signatures and says nothing about the
semantics — and a how-to page telling them to subclass a class they could not
import.

The gap is exactly the one the suites exist to close. A Protocol cannot state
that `find_by_blocking_key` hands back copies, that reads never cross tenants,
that `upsert_relationships` is all-or-nothing, or that `get` returns `str` and
not `bytes`. Those are the things adapters get wrong, and they are the things
only a shared body asserts.

## Decision

Move the suites into the package as `redstring.testing`, behind a `test`
extra supplying `pytest`, `hypothesis` and `pytest-asyncio`.

An adapter author writes what this repository's own adapters write:

```python
from redstring.testing.graph_store import GraphStoreCompliance


class TestMyStore(GraphStoreCompliance):
    async def new_store(self) -> GraphStore:
        return MyGraphStore()
```

**The shipped bodies are the same bodies.** There is no reduced public variant
and there will not be one: a weaker suite for outside adapters would make the
port mean two different things, which is the divergence the suites exist to
prevent. Whatever this project holds its own adapters to is what it publishes.

Four constraints follow from putting test code inside a library, and each is
enforced rather than intended.

**Nothing under `src/` may import it.** `testing` sits at the top of the
`lint-imports` layers contract, so `import redstring` can never reach `pytest`,
and `tests/unit/test_dependencies_stay_confined.py` carries a row for each of
the two libraries. The claim is finally about the artifact, so the artifact
checks it: `tests/integration/test_wheel_contents.py` installs the bare wheel
and asserts that importing `redstring` pulls in neither.

**It may import only `ports` and `domain`.** A layers contract pins one
direction; this package needs both, so a separate `forbidden` contract states
the other. The reason is concrete rather than tidy — see Consequences.

**Its exports are gated like the library's.** `redstring.testing.__all__` is
the promise, and `tests/unit/test_the_testing_surface_is_gated.py` fails when
a compliance class exists and is not named in it. A suite nobody can find is a
suite nobody runs.

**The extra takes ranges, not exact pins.** `pyproject.toml` pins tooling
extras exactly and says loudly not to widen them. That rule is about tools
nobody installs beside their own code. This extra goes into the environment a
consumer's own test suite runs in, so `pytest==9.1.1` would not be a policy,
it would be a conflict.

## Consequences

**The move immediately found a suite asserting against an adapter.**
`ChunkStoreCompliance` had a case that built the same corpus in an
`InMemoryChunkStore` and required the adapter under test to agree with it —
and its own docstring conceded the problem: *"on the in-memory adapter this
compares it with itself and is trivially true."* Two defects in one. Half the
adapters running that suite got no assertion at all, and for the other half
the *contract* was whatever the in-memory adapter happened to do, so a defect
in the reference was a defect in the port for everyone.

It also could not ship: a suite an outside adapter runs cannot demand
agreement with an implementation detail of this repository. The replacement
asserts the truncation rule the port states in prose, against an oracle
written from the corpus. **This is the argument for shipping, not a cost of
it** — the constraint "an outsider must be able to run this" is what made a
years-old tautology visible, and the `forbidden` contract is what will keep
the next one from landing.

**The suites are type-checked for the first time.** `tests/` is outside
`mypy --strict`; `src/` is not. That surfaced several hundred findings, nearly
all of them the id roles that
[`0032`](0032-the-id-names-are-newtypes.md) had just made distinct. Worth
having: these are the bodies an adapter author reads to learn what the port
means, and a `TenantId` where the reader expects an `EntityId` is exactly the
confusion the suites are supposed to remove.

**One bandit exemption exists now, and only one.** B101 (`assert_used`) is
skipped for this path — the suites are made of `assert`, and B101 is about
`python -O` deleting one in production. A path skip rather than
`exclude_dirs`, so every other check still runs there; and guarded both ways
per [`0014`](0014-exemption-lists-are-empty-and-must-stay-falsifiable.md),
because a config skip suppresses a finding *before* bandit reports it and the
existing `# nosec` gate therefore cannot see it at all.

**`KG_COMPLIANCE_MAX_EXAMPLES` is now a library module reading the
environment**, which
`tests/unit/test_library_reads_no_environment.py` forbids. Exempted, with the
reason recorded and both directions guarded. The rule that gate enforces is
that a *caller* cannot configure a library that reads its own environment;
here the caller is a pytest invocation, and setting a variable for one is how
you configure it.

**A consuming project must set `asyncio_mode = "auto"` itself.** Every case is
a bare `async def`, so under `pytest-asyncio`'s default strict mode they are
collected and **skipped** — which reads as a pass. A dependency cannot set
that for its consumer, so it is said in the package docstring and in the
how-to, and it is the first thing to check when a newly-added suite reports
success suspiciously fast.

**A second surface is a second thing to keep honest.** 0006 exists because
`__all__` claims are only worth something when checked; that argument applies
unchanged to the new list, which is why it arrived with its gate rather than
after one.
