# ADR 0031: The id names are `NewType`s

## Status

Accepted. **Amends
[`0002` two store ports](0002-two-store-ports.md)** and
[`0006` the public surface is gated](0006-the-public-surface-is-gated.md) in
typing only: the ports' method signatures are unchanged as written, the
exported set is unchanged, and every runtime contract either of them states is
untouched. What changes is that the four names the ports are written in are
now distinct to a type checker.

[`0001` event log schema and granularity](0001-event-log-schema-and-granularity.md)
stands **unamended**, and that is the load-bearing part: `NewType` has no
representation of its own, so no persisted event, no Neo4j property and no
Postgres column changes. An event log already written cannot be migrated, and
this decision was only available because it does not ask to.

## Context

`redstring.domain.ids` declared four names as bare aliases:

```python
EntityId = UUID
RelationshipId = UUID
TenantId = UUID
SourceId = str
```

Three of them were **the same object**. `EntityId is TenantId` was true, and
the module's own tests asserted exactly that — `assert EntityId is uuid.UUID`,
four times, which is a test that cannot distinguish four names from one and
passes identically if three of them are deleted.

The reference documentation was honest about the consequence: "passing a
`TenantId` where an `EntityId` is expected is not a type error. Nothing in the
library guards against it." The distinction was described as documentary.

**The swap those aliases fail to prevent is the one this codebase has already
shipped.** Every store port keys on `(tenant_id, id)` and takes both as
arguments, adjacent, of what was one type:

```python
async def get_entity(self, entity_id: EntityId, tenant_id: TenantId) -> Entity | None: ...
```

Transposing those two arguments is a tenant-isolation defect — one tenant's
read answered from another's data — and it type-checked cleanly. CLAUDE.md's
table of test shapes that prove nothing carries the matching row: *"ids drawn
from `uuid4()`, never colliding across tenants — a `(tenant_id, id)` key
compared on `id` alone: one tenant's write vouches for another's."* That row
was filed because the defect happened, in a fix round that cited the table, to
an implementer who had just read it. A rule stated in prose did not survive
contact with a habit. A type checker is not a habit.

## Decision

Declare all four as `NewType` over the base they already had.

```python
EntityId = NewType("EntityId", UUID)
RelationshipId = NewType("RelationshipId", UUID)
TenantId = NewType("TenantId", UUID)
SourceId = NewType("SourceId", str)
```

`NewType` and not a wrapper class, and the distinction is the whole reason
this is affordable. `NewType` compiles to the identity function: `TenantId(u)`
**is** `u`, so `isinstance` checks, dict keys, `uuid5` seeding, pydantic
validation, JSON serialisation and every existing caller passing a bare
`uuid4()` all behave exactly as before. A wrapper class would have changed all
of those, and would have needed a migration of the event log to go with it.

The asymmetry is what keeps the annotation burden small. A `TenantId` **is** a
`UUID` to mypy, so passing one anywhere a plain `UUID` is expected is fine; a
plain `UUID` is *not* a `TenantId`, so producing one names the role. The cost
therefore lands exactly at the boundaries where a raw UUID enters the domain
and nowhere else — a row read back from Neo4j, an id minted by `uuid5` in
`extraction/mapping.py`, and `event.tenant_id`, which is typed by
`eventsource-py`'s `TenantDomainEvent` and cannot be annotated from here.

## Consequences

**The library's own type checking is what enforces this, and it already
covers every module.** `mypy --strict` runs over all of `src/redstring` with
no `exclude` (see
[`0014`](0014-exemption-lists-are-empty-and-must-stay-falsifiable.md)), so a
new argument transposition is a gate failure rather than a review catch.
`tests/` is not type-checked, which bounds both the benefit and the cost: no
test had to change, and a test can still transpose the arguments freely.

**Downstream callers get it too, and only because of the `py.typed` marker.**
A `NewType` in a dependency is invisible to a consumer's type checker unless
the installed package declares itself typed. That marker exists, and
`tests/integration/test_wheel_contents.py` asserts it survives packaging —
which is the reason this decision buys anything outside this repository
rather than only inside it.

**It is not validation, and should not be mistaken for it.** `EntityId(x)`
checks nothing at runtime. What it prevents is a well-formed id of the wrong
*kind* reaching a position that expects another; what it does not prevent is a
malformed one, an id belonging to a deleted entity, or an id from the wrong
tenant passed knowingly. Tenant isolation is still asserted behaviourally, by
the `never_crosses_tenants` case every store port's compliance suite carries
per read method.

**Three projections now call an id constructor for a value they did not
build.** `TenantId(event.tenant_id)` in `projections/graph.py` and
`projections/chunk.py` is the seam between the event framework's vocabulary
and this library's. It reads as ceremony and is not: it is the one place the
two type systems meet, and writing it out is what stops the alternative —
widening the port signatures back to `UUID` — from looking like the tidier
option.

**The alternative of a wrapper class stays closed for a stated reason.**
Validation on construction is occasionally proposed for id types, and it would
buy a genuine thing: `EntityId("garbage")` failing loudly. It costs a changed
wire format, a changed `isinstance` contract, and a migration of every
persisted event, which is a price
[`0001`](0001-event-log-schema-and-granularity.md) says outright is not
payable. If ids ever need validation, it belongs in a validator on the
pydantic models that carry them, not in the id type.
