# Event reference

The four events redstring writes to its log, the envelope they inherit, and
the stream each one lands in.

## Scope and stability

This page records **what is on the wire now**: field names, types, defaults,
validation rules, and the exact messages raised when validation fails. It is
descriptive, not persuasive — the reasoning behind the shape of the log lives
in [ADR 0001](../adr/0001-event-log-schema-and-granularity.md) and
[ADR 0004](../adr/0004-consolidation-emits-events.md), and in the docstrings of
`redstring/events/document.py` and `redstring/events/merge.py`.

Everything below is enforced by tests; see [Where each claim is
enforced](#where-each-claim-is-enforced).

## Conventions used in the field tables

Each event below has a **Field / Type / Default** table listing only the
fields that event declares. The envelope it inherits is tabled once, under
[Inherited envelope](#inherited-envelope-from-tenantdomainevent--domainevent),
and is not repeated per event.

Three spellings appear in the `Default` column, and they mean three different
things:

- **required** — the field has no default. Construction without it raises
  `pydantic.ValidationError`. Types are given as written in the source, so a
  domain alias appears as the alias with its underlying type in parentheses:
  `SourceId` (`str`), `EntityId` (`UUID`).
- **`Field(default_factory=list)`** — optional, defaulting to a fresh empty
  list per instance. An event may legitimately carry no entities, no
  relationships, no embeddings, no redirections: extraction that found nothing
  is a real outcome, and a merge that moved no edges is a real merge.
- **`Field(min_length=1)`** — **required, and additionally rejected when
  empty.** This is not "optional with a minimum": there is no default, so both
  omitting the field and passing `[]` fail. It is used for
  `merged_entity_ids` and `unmerged_entity_ids`, where an empty list would
  describe a merge that merged nothing.

A plain value in the column (`None` for `merge_reason`, `1` for
`event_version`) is an ordinary default — optional, and that value when
omitted.

Validation rules beyond the field declaration live in a `@model_validator`
with `mode="after"`, documented per event in its own subsection. `mode="after"`
means the rule runs on a fully constructed, field-validated model, so a
validator message is only ever raised once every field has already parsed.
Message text is quoted verbatim, with `<placeholders>` for interpolated
values; the tests assert these messages, so they are part of what this page
promises.

Every event is a pydantic model carrying `model_config = ConfigDict(frozen=True,
allow_inf_nan=False, extra="forbid")`, inherited from `DomainEvent`: events
reject attribute assignment after construction, reject non-finite floats, and
reject any field they do not declare. Two consequences worth stating plainly:

- `extra="forbid"` is what makes an unknown key an error rather than a
  silently dropped one — see [Compatibility and
  versioning](#compatibility-and-versioning) for what that means when readers
  and writers are at different revisions.
- `frozen=True` blocks *rebinding a field*, not mutating the object bound to
  it. The `list` payloads are ordinary lists; a consumer that appends to
  `event.entities` will succeed. Treat the payload as read-only, and copy
  before mutating in a fold.

## Inherited envelope from `TenantDomainEvent` / `DomainEvent`

All four events subclass `TenantDomainEvent`
(`eventsource.domain.tenant_events`), which subclasses `DomainEvent`
(`eventsource.domain.event`). Neither is defined in this repository; the
fields below arrive with the base classes and are on the wire for every
redstring event, in addition to the per-event fields tabled later.

| Field | Type | Default | Notes |
|---|---|---|---|
| `event_id` | `UUID` | `uuid4()` | Identifies this event *instance*. Fresh per construction, so two constructions of equal payload are distinguishable. |
| `event_type` | `str` | the class name | Wire name. Populated by a `mode="before"` validator from `event_type_name()`; never declared by hand here. |
| `event_version` | `int` | `1` | `ge=1`. Redeclared explicitly on each event class — see below. |
| `occurred_at` | `datetime` | `datetime.now(UTC)` | Timezone-aware, UTC. The only timestamp on the envelope: there is no separate recorded-at field, and the store supplies its own ordering. |
| `aggregate_id` | `UUID` | **required** | The stream's aggregate id — for redstring, whatever [`document_stream`/`consolidation_stream`](#stream-categories-and-stream-id-derivation) derived. |
| `aggregate_type` | `str` | **required on the base**, defaulted per event | Doubles as the stream category; redeclared with a default on each event class — see below. |
| `aggregate_version` | `int` | `1` | `ge=1`. The aggregate's version *after* this event, set by the aggregate via `with_aggregate_version(version)` rather than at construction. |
| `tenant_id` | `UUID` | **required** | `TenantDomainEvent`'s only addition: it re-declares the base's `TenantId \| None = None` as a required `UUID`. |
| `actor_id` | `str \| None` | `None` | Who or what triggered the event. redstring does not set it. |
| `correlation_id` | `UUID` | `uuid4()` | Links related events across aggregates. |
| `causation_id` | `UUID \| None` | `None` | The event that caused this one. Set by `with_causation(causing_event)`, which also copies the causing event's `correlation_id`. |
| `metadata` | `dict[str, Any]` | `{}` | Free-form. Set by `with_metadata(**kwargs)`. |

The `UUID` aliases in `eventsource.domain.types` — `EventId`, `AggregateId`,
`TenantId`, `CorrelationId`, `CausationId` — are all plain `UUID`; the table
gives the underlying type. redstring's own `TenantId` (in
`redstring.domain.ids`) is likewise `UUID`, so an event's `tenant_id` and a
payload's compare directly, which is what the per-event tenant validators do.

Three envelope fields are populated by a `with_*` method rather than at
construction, and each returns a **new** event (`model_config` is
`frozen=True`, so nothing is mutated in place): `with_causation`,
`with_metadata`, `with_aggregate_version`. Reassigning any envelope field on a
constructed event raises `pydantic.ValidationError`.

`aggregate_type` is validated by a `mode="after"` validator on `DomainEvent`
against the stream-category pattern `^[A-Za-z0-9_.-]+\Z` — the same pattern
`StreamId` applies to its `category`, because this value *becomes* that
category. A value with a space or a slash in it fails at construction:

```
aggregate_type '<value>' is not a valid stream category (must match
^[A-Za-z0-9_.-]+\Z); it is used as StreamId.category on the event's stream
```

Constructing with the ambient tenant instead of an explicit one:
`TenantDomainEvent.with_tenant_context(**fields)` fills `tenant_id` from the
tenant scope (`eventsource.tenant_scope`), raising `TenantContextNotSetError`
when no scope is set. An explicit `tenant_id` in `**fields` wins over the
scope. redstring's aggregates construct events explicitly, so this classmethod
matters mainly to callers driving the library from inside a request scope.

### `event_version` and `aggregate_type`

Both are inherited fields, and every event class **redeclares** them anyway.
The first two lines of each event body are the same shape:

```python
@register_event
class DocumentExtracted(TenantDomainEvent):
    event_version: int = 1
    aggregate_type: str = DOCUMENT_CATEGORY
```

Current values, in full — there is no other source to consult:

| Event | `event_version` | `aggregate_type` |
|---|---|---|
| `DocumentExtracted` | `1` | `DOCUMENT_CATEGORY` = `"Document"` |
| `EntitiesEmbedded` | `1` | `DOCUMENT_CATEGORY` = `"Document"` |
| `EntitiesMerged` | `1` | `CONSOLIDATION_CATEGORY` = `"Consolidation"` |
| `MergeUndone` | `1` | `CONSOLIDATION_CATEGORY` = `"Consolidation"` |

**`event_version` is `1` for all four**: nothing in the log has been versioned
past its first shape yet. A reader may not treat that as permanent — see
[Compatibility and versioning](#compatibility-and-versioning) — but it may
rely on this page moving when any of these numbers does.

The two fields are redeclared for different reasons.

`event_version` is redeclared because `DomainEvent` already defaults it to
`1`, so an event that never mentions it *looks* versioned and is not: nobody
chose the number, and nobody bumping the schema will think to look for a field
the class does not contain.
`test_every_event_declares_its_schema_version_explicitly` therefore checks
`"event_version" in event_type.__annotations__` — the class's **own**
annotations, not the resolved pydantic field — which is the difference between
"the value is 1" and "somebody wrote 1". It then asserts the default is `1`,
which is why a bump is a deliberate two-place edit: the class and the test's
expectation.

`aggregate_type` is redeclared because it is **required on the base**
(`Field(...)` on `DomainEvent`) and redstring events do not take it from the
caller. Giving it a default per class is what makes the category a property of
the event *type* rather than of the construction site, so two events on the
same stream cannot disagree about which stream that is.

The value doubles as the stream category. `DomainEvent` validates it with a
`mode="after"` validator against `CATEGORY_PATTERN`, imported from
`eventsource.domain.stream_id` — literally the same compiled regex `StreamId`
applies to its own `category`, reused rather than restated, because this value
*becomes* that category. The two constants here (`"Document"`,
`"Consolidation"`) satisfy it trivially; the constraint bites on anything
invented later with a space or a slash in it.

`test_every_event_belongs_to_one_of_the_two_stream_categories` narrows that
further, asserting the declared default is one of exactly those two constants.
A third category would be a stream no aggregate owns — no invariants enforced
on writes to it, and no repository managing its version — so a new event
belongs to `Document` or `Consolidation`, or the aggregate it needs has to be
written first. See [aggregates](aggregates.md) for the two that exist.

`event_type` is the opposite case, and the contrast is the point: it is
**never** declared by hand, so the wire name is always the class name —
`"DocumentExtracted"`, `"EntitiesEmbedded"`, `"EntitiesMerged"`,
`"MergeUndone"`. `DomainEvent` derives it from the class, and
`test_no_event_declares_its_event_type_by_hand` asserts both that
`"event_type"` is absent from the class's annotations and that
`event_type_name() == __name__`. Declaring it would be either noise (when it
matches) or a silent decoupling in which the wire name and the class name
drift apart and only the log knows.

So: two fields you must write on a new event, one you must not.

### `KG_EVENT_TYPES`

`redstring.events.KG_EVENT_TYPES` **is** the schema. Not a summary of it, not
a convenience export: it is the enumeration, in `redstring/events/__init__.py`,
and everything this page claims about "every event" is a claim about the
members of this tuple.

```python
KG_EVENT_TYPES: tuple[type[TenantDomainEvent], ...] = (
    DocumentExtracted,
    EntitiesEmbedded,
    EntitiesMerged,
    MergeUndone,
)
```

It is a `tuple` rather than a list or a set: immutable, and with a stable
order, so parametrised test ids are stable run to run.

The reason the schema is a data structure rather than prose is that the
properties every event must have — an explicitly declared `event_version`, a
required tenant, one of the two stream categories, no hand-declared
`event_type` — can then be asserted by *introspection over it*. A new event
class inherits every one of those checks the moment it joins the tuple.
Nobody has to remember to write them, and nobody has to remember to read a
rule.

**Adding an event means adding it here.** That is the one hand-maintained
step, and it is the step the tests refuse to let you skip.

#### The introspection tests keyed off it

`tests/unit/events/test_schema.py` parametrises seven cases over
`KG_EVENT_TYPES` — `ids=lambda t: t.__name__`, so a failure names the event —
plus one that deliberately does not:

| Test | Asserts, for each event |
|---|---|
| `test_the_schema_is_not_empty` | `len(KG_EVENT_TYPES) >= 4`. Not parametrised: a registry-driven suite over an empty registry passes vacuously, so this is the floor under the other seven. |
| `test_every_event_declares_its_schema_version_explicitly` | `"event_version" in event_type.__annotations__`, **and** `model_fields["event_version"].default == 1`. |
| `test_no_event_declares_its_event_type_by_hand` | `"event_type" not in event_type.__annotations__`, and `event_type_name() == __name__`. |
| `test_every_event_belongs_to_one_of_the_two_stream_categories` | `model_fields["aggregate_type"].default` is `DOCUMENT_CATEGORY` or `CONSOLIDATION_CATEGORY`. |
| `test_every_event_requires_a_tenant` | `model_fields["tenant_id"].is_required()`. |
| `test_every_event_rejects_fields_it_does_not_declare` | `model_validate` with an undeclared key raises, matching `[Ee]xtra` — i.e. `extra="forbid"` survived. |
| `test_every_event_resolves_from_the_registry_by_its_wire_name` | `get_event_class(event_type.event_type_name()) is event_type`. |
| `test_the_tuple_lists_exactly_the_registered_events` | the tuple and the registry agree, **both directions**. |

The first six are covered in detail where their fields are:
[`event_version` and `aggregate_type`](#event_version-and-aggregate_type) for
versions, categories and the wire name;
[conventions](#conventions-used-in-the-field-tables) for `extra="forbid"`; the
[envelope table](#inherited-envelope-from-tenantdomainevent--domainevent) for
the required tenant. Two of the eight are about the tuple itself and are
worth reading here.

**Registry resolution.** Every event carries `@register_event`
(`eventsource.domain.event_registry`), and that decorator is what turns a
*stored* event back into its class. Unregistered, an event round-trips through
JSON as a plain dict and nothing fails until a persistent store tries to
rehydrate one — long after the events were written. The in-memory store keeps
object identity, so no other test in the suite can see the difference, which
is precisely why a cosmic-ray mutant deleting the decorator survived until
this test existed. It also guards the reverse direction: slice 5b had to
*un*-register the legacy consolidation events, because they held the wire
names this schema needs and the registry refuses duplicates. If they ever come
back, this is what says so.

**Tuple-versus-registry, in both directions.** This is the one gate that
cannot key off `KG_EVENT_TYPES`, because it is the gate on `KG_EVENT_TYPES`.
It imports every module in the package with `pkgutil.iter_modules`, collects
every registered class whose `__module__` starts with `redstring.events`, and
compares:

- **registered but absent from the tuple** — the event exists, is written to
  the log, and gets no schema check, no replay case and no handler check.
  Nothing else would go red.
- **in the tuple but not registered** — a stored one cannot be deserialised.

Both assertions name the offending classes, sorted. The package walk is the
load-bearing part: a new event module that nothing imports registers nothing,
so a check reading only the registry would pass while the omission stayed
invisible in exactly the way a hand-maintained tuple is invisible. Walking the
package makes the **filesystem** the source of truth, which is the only thing
here that cannot be forgotten.

There is **no exclusion list**, and its absence is deliberate. The walk used
to skip legacy, never-emitted modules; those are gone (`events/consolidation`
in slice 7, `events/scraping` and `events/base` in slice 9), and the list went
with them rather than being kept empty. An exclusion over an empty set
excludes nothing, and a guard iterating it passes vacuously — see
[CLAUDE.md's rule on exemption lists](https://github.com/tyevans/redstring/blob/main/CLAUDE.md). Every module in
`redstring/events/` is now live schema.

`tests/unit/projections/test_replay_coverage.py` parametrises two more cases
over the same tuple —
[`test_every_event_type_is_replayed_by_a_pinned_case` and
`test_every_event_type_has_a_projection_handler`](#where-each-claim-is-enforced)
— so a new event also arrives with a replay scenario and a handler, or it is
red.

#### What this means if you are adding an event

1. Write the class in a module under `redstring/events/`, decorated with
   `@register_event`, declaring `event_version` and `aggregate_type`.
2. Import it in `redstring/events/__init__.py` and add it to
   `KG_EVENT_TYPES` and to that module's `__all__`.
3. Run the suite. Anything you skipped in step 1 now names itself, and the
   projection-side gates will additionally demand a handler and a pinned
   replay scenario.

#### Scope note

`KG_EVENT_TYPES` is exported from `redstring.events.__all__`, which is a
package-level `__all__`, not the library's public surface. The gated surface
is `redstring.__all__` alone — it carries `DocumentExtracted` and
`EntitiesEmbedded`, and does **not** carry `KG_EVENT_TYPES`, `EntitiesMerged`
or `MergeUndone`. Reaching them through the dotted path `redstring.events` is
reaching into an internal module, and per
[ADR 0006](../adr/0006-the-public-surface-is-gated.md) that may change without
notice. Read the tuple to understand the log; do not import it as an API.

## Stream categories and stream-id derivation

A stream is the unit of ordering and of optimistic concurrency: events in one
stream are ordered and versioned against each other, and events in different
streams are not. So "which stream does this event land in" is the same
question as "what is serialised against what".

redstring has **two** categories and two derivation functions, defined in
`redstring/events/streams.py` and re-exported from `redstring.events`:

| Constant | Value | Aggregate | `aggregate_id` is |
|---|---|---|---|
| `DOCUMENT_CATEGORY` | `"Document"` | `Document` | `uuid5(tenant_id, source_id)` |
| `CONSOLIDATION_CATEGORY` | `"Consolidation"` | `ConsolidationLog` | the `tenant_id` itself |

### `DOCUMENT_CATEGORY` = `"Document"` and `CONSOLIDATION_CATEGORY` = `"Consolidation"`

Both are plain module-level `str` constants in
`redstring/events/streams.py` — not an enum, not a `Literal` type — declared
in full as:

```python
#: Stream category (and `aggregate_type`) for the `Document` aggregate.
DOCUMENT_CATEGORY = "Document"

#: Stream category (and `aggregate_type`) for the `ConsolidationLog` aggregate.
CONSOLIDATION_CATEGORY = "Consolidation"
```

The literal values are what is **on the wire**: they are written into every
event's `aggregate_type` field and into every `StreamId.category`, so a stored
event carries the string `"Document"` or `"Consolidation"`, and a consumer
matching on the raw JSON matches on those. Import the constants rather than
retyping the literals; the strings are the contract, but a typo in one is a
silent second category.

Each constant is used in exactly three places, and the fact that it is *one*
constant across all three is what keeps them aligned:

| Used as | Document | Consolidation |
|---|---|---|
| `StreamId.category`, in the derivation function | `document_stream` | `consolidation_stream` |
| `aggregate_type` default on each event class | `DocumentExtracted`, `EntitiesEmbedded` | `EntitiesMerged`, `MergeUndone` |
| `aggregate_type` class attribute on the aggregate | `Document` (`redstring/aggregates/document.py`) | `ConsolidationLog` (`redstring/aggregates/consolidation_log.py`) |

That third row is the one worth noticing. The aggregate and its events name
the same constant, so an event cannot be written with a category its
aggregate's repository would not look for. Had each side spelled its own
literal, the two would agree until someone renamed one — and the failure would
be an empty replay rather than an error, because reading a category nothing
writes to yields no events, not a mistake.

The values themselves are `PascalCase` singular nouns matching the aggregate
class name minus any suffix (`Document` → `"Document"`, `ConsolidationLog` →
`"Consolidation"`). Nothing enforces that convention; what *is* enforced is
that they parse as stream categories. `StreamId`
(`eventsource.domain.stream_id`) is a frozen dataclass of exactly
`aggregate_id: UUID` and `category: str`; it renders as
`"{aggregate_id}:{category}"` and validates its category against
`^[A-Za-z0-9_.-]+\Z`, which is why `:` cannot appear in one. `DomainEvent`
applies that same compiled pattern to `aggregate_type` — see
[`event_version` and `aggregate_type`](#event_version-and-aggregate_type).

**Two, and adding a third is a test failure.**
`test_every_event_belongs_to_one_of_the_two_stream_categories` asserts each
event's declared `aggregate_type` default is in the set
`{DOCUMENT_CATEGORY, CONSOLIDATION_CATEGORY}`, so a new category is not a
matter of adding a constant: a third category would be a stream no aggregate
owns — no invariants enforced on its writes, no repository managing its
version — so the aggregate has to exist first. See
[aggregates](aggregates.md).

Both constants are exported from `redstring.events.__all__`. Neither is in
`redstring.__all__`, so neither is part of the gated public surface; a
consumer that needs to name a stream from outside should call
[`document_stream`](#document_stream-tenant_id-source_id), which is exported.

### `document_stream(*, tenant_id, source_id)`

```python
def document_stream(*, tenant_id: TenantId, source_id: SourceId) -> StreamId
```

Returns `StreamId(aggregate_id=uuid5(tenant_id, source_id),
category=DOCUMENT_CATEGORY)` — that is, category `"Document"`. Defined in
`redstring/events/streams.py`.

| Parameter | Type | |
|---|---|---|
| `tenant_id` | `TenantId` (`UUID`) | keyword-only, required. Used as the `uuid5` **namespace**. |
| `source_id` | `SourceId` (`str`) | keyword-only, required. Used as the `uuid5` **name**. Must not be blank. |

Both are keyword-only (the `*` in the signature is load-bearing): the two
arguments are a UUID and a string that are never interchangeable, and naming
them at every call site is what keeps a positional swap from being a
type-checked-clean mistake at some future point where both are strings.

The function is pure — no I/O, no state, no store lookup — so it is safe to
call anywhere, including inside a fold.

One stream per document per tenant. Extraction is per-document and parallel
across documents, so short streams give real concurrency while keeping
ordering where it matters — the re-extraction history of a single document.
Both `DocumentExtracted` and `EntitiesEmbedded` land here, so a document's
extraction and its embeddings are ordered against each other.

Four properties of the derivation, each of which a test in
`tests/unit/events/test_streams.py` pins:

- **Deterministic.** The same `(tenant_id, source_id)` always yields the same
  stream, so re-extracting a document *appends to the stream it already has*
  rather than starting a new one. There is no mapping table to keep consistent
  and no lookup on the write path.
  (`test_the_same_document_derives_the_same_id_every_time`)
- **Namespaced by tenant.** `tenant_id` is the `uuid5` **namespace**, not part
  of the hashed name. One `source_id` under two tenants is two streams — the
  expected case, since a `SourceId` is often a public URL, not a corner one.
  (`test_one_source_id_under_two_tenants_gets_different_streams`)
- **Unambiguously split.** Because the tenant is a fixed-width UUID namespace
  rather than text prepended to the name, the two halves of the key cannot be
  confused: a scheme that concatenated them before hashing would map
  `("t", "ab")` and `("ta", "b")` onto one stream, and `SourceId` is free-form
  text, so nothing else would stop it.
  (`test_the_two_halves_of_the_key_cannot_be_confused_for_each_other`, which
  uses the colliding pair rather than random ids — an accidental-collision
  test would pass under either scheme.)
- **Injective in the source id.** Two documents of one tenant get two streams.
  (`test_two_documents_of_one_tenant_get_different_streams`)

`test_a_document_stream_is_in_the_document_category` additionally pins the
category and that the returned `aggregate_id` is a `UUID`.

The type change is the reason the function exists at all:
`StreamId.aggregate_id` is a `UUID`, while `SourceId` is a caller-supplied
`str`. `uuid5` is the bridge. Note that `uuid5` is SHA-1-based and *not* a
security boundary — tenant isolation is enforced by the tenant checks on the
events themselves and by the stores, not by the unguessability of a stream id.

#### Blank `source_id` raises `ValueError`

The only way this function fails. The guard is `if not source_id.strip()`, so
it rejects both `""` and whitespace-only input such as `"   "`, and the
message is exactly:

```
source_id must not be blank; it identifies the document's stream
```

It is a plain `ValueError`, not a `RedstringError` subclass — an argument this
malformed is a programming error at the call site, not a domain condition a
caller catches. `SourceDocument.id` carries no validation of its own, so this
is the last point at which a blank id can be caught. Hashed rather than
rejected, a blank id would produce a perfectly valid-looking stream shared by
every blank-id document in the tenant: no error, no obvious symptom, and two
unrelated documents' extraction histories interleaved in one stream under one
`Document` aggregate's version counter.

Whitespace is stripped only *for the test*. A non-blank `source_id` is hashed
exactly as given — `"doc-1"` and `" doc-1"` are two different streams, and the
function normalises nothing.

Pinned by `test_a_blank_source_id_is_rejected`, parametrised over `""` and
`"   "` — one example is not enough, since a guard written `if not source_id`
passes the empty case and admits the whitespace one.

### `consolidation_stream(*, tenant_id)`

```python
def consolidation_stream(*, tenant_id: TenantId) -> StreamId
```

Returns `StreamId(aggregate_id=tenant_id, category=CONSOLIDATION_CATEGORY)` —
that is, category `"Consolidation"`. Defined in
`redstring/events/streams.py`, alongside
[`document_stream`](#document_stream-tenant_id-source_id).

| Parameter | Type | |
|---|---|---|
| `tenant_id` | `TenantId` (`UUID`) | keyword-only, required. Returned **as** the `aggregate_id`, unchanged. |

The aggregate id **is** the tenant id, not a derivation of it: there is
exactly one consolidation log per tenant, so any further mapping would be a
fiction with no second value to distinguish. No `uuid5`, no hashing, nothing
to keep consistent — the function is a two-field constructor call, pure and
total. Unlike `document_stream` it **cannot fail**: there is no blank-input
guard, because there is no free-form input to guard.

The keyword-only `*` is kept for the same reason as its sibling's, even with a
single parameter: the two functions are read together, and a call site that
names its argument stays correct if a second one is ever added.

`EntitiesMerged` and `MergeUndone` are the two events that land here — see
[`event_version` and `aggregate_type`](#event_version-and-aggregate_type) —
and the `ConsolidationLog` aggregate
(`redstring/aggregates/consolidation_log.py`) is what owns the stream.

One stream per tenant is a deliberate serialisation point rather than an
oversight. Merges span documents, and two concurrent merges touching the same
entities must not interleave; a per-tenant stream makes the event store's
optimistic-concurrency check do that work. That serialises a tenant's merges,
which is the cost, and there is no narrower boundary that still sees the
conflicts: an entity absorbed by one merge and simultaneously by another gives
that entity two canonical parents, and which wins depends on the fold order.
`ConsolidationLog` enforces that rule (and two others) against replayed state
under an `ExpectedVersion`, which only works because every merge in the tenant
is on the one stream.

The consequence to plan for is that this stream is **unbounded**: it grows
with a tenant's merge history rather than with anything document-sized.
Snapshots keep rehydration bounded — `EveryNEvents` in
`redstring.aggregates.repositories` — so a long log costs storage rather than
load time. See [aggregates](aggregates.md).

Pinned by `test_a_consolidation_stream_is_the_tenant_in_the_consolidation_
category` in `tests/unit/events/test_streams.py`, which asserts both halves:
`stream.aggregate_id == tenant_id` and `stream.category ==
CONSOLIDATION_CATEGORY`. Asserting the category alone would admit any
derivation of the tenant id, and asserting the id alone would admit the
document category.

### Why a shared `aggregate_id` is not a collision

Nothing stops `uuid5(tenant_id, source_id)` from equalling some tenant id in
principle, and `consolidation_stream` returns a tenant id directly. **The
category is part of the stream identity**, so `(id, "Document")` and
`(id, "Consolidation")` are two streams with independent version counters even
when the ids match. `tests/unit/aggregates/test_repositories.py::
test_a_document_and_a_consolidation_stream_never_collide` forces the case by
constructing a document stream *at* the consolidation aggregate id and
asserting both streams keep their own version.

### Callers

Aggregates take an `aggregate_id`, not a `StreamId`, so the pattern at every
call site is to derive the stream and take `.aggregate_id` from it:

```python
aggregate = Document(document_stream(tenant_id=tenant_id, source_id=document.id).aggregate_id)
```

That is what `build_graph` (`redstring/composition.py`) does for extraction,
and what `redstring/consolidation/service.py` does with
`consolidation_stream(tenant_id=tenant_id).aggregate_id` for merge and undo.
See [aggregates](aggregates.md) for what each one then enforces, and [drive
projections from an event
store](../how-to/drive-projections-from-an-event-store.md) for reading the
streams back.

### Public surface

`document_stream` **is** in `redstring.__all__`: a caller needs it to name a
document's stream when resuming or rebuilding a projection.
`consolidation_stream`, `DOCUMENT_CATEGORY` and `CONSOLIDATION_CATEGORY` are
exported from `redstring.events.__all__` only, which is a package-level
`__all__` and not the gated surface — reaching them through the dotted path is
reaching into an internal module.

## `DocumentExtracted`

Everything one extraction run found in one document. Defined in
`redstring/events/document.py`; category `Document`, so it lands on
[`document_stream(tenant_id=…, source_id=…)`](#document_stream-tenant_id-source_id)
alongside `EntitiesEmbedded`.

| Field | Type | Default |
|---|---|---|
| `source_id` | `SourceId` (`str`) | required |
| `model_version` | `str` | required |
| `entities` | `list[Entity]` | `Field(default_factory=list)` |
| `relationships` | `list[Relationship]` | `Field(default_factory=list)` |

Plus the [inherited
envelope](#inherited-envelope-from-tenantdomainevent--domainevent), and
`event_version: int = 1` / `aggregate_type: str = DOCUMENT_CATEGORY`
redeclared on the class. There are no other fields, and `extra="forbid"`
means there can be none on the wire either.

**`source_id`** — the document this run read, as the caller supplied it. It is
required, and it is deliberately redundant with the stream: `aggregate_id` is
a `uuid5` of the tenant and this value, and a hash cannot be read back, so a
consumer of a *global* feed would otherwise have no way to say which document
an event came from. Nothing normalises it — see
[`document_stream`](#document_stream-tenant_id-source_id) for the whitespace
rule that applies when it is hashed — and this field carries whatever was
hashed. It is also the value every entity's own `source_id` is checked
against, and every relationship's when it has one; see the
[validator](#validator-_payloads_belong_to_this_document_and_tenant).

**`model_version`** — which extraction model produced this payload, and the
idempotency key. `Document.record_extraction` returns `None` and emits nothing
when this document has already recorded an extraction under this string, so a
retry after a crash is a no-op rather than a second write of the same ten
thousand entities. Three consequences a consumer should know:

- **The key is the version string, not the payload.** A re-run of the same
  model can legitimately produce different output — decoding is not
  deterministic — so comparing payloads would classify the retry as new and
  write it, which is the double write being prevented. The cost is that a
  genuine re-run under an unchanged model cannot be recorded: bump the
  version, which is what a re-run worth recording implies.
- **It shares no key space with `EntitiesEmbedded.embedding_model`.** The
  aggregate keeps `extraction_model_versions` and `embedding_models` as
  separate lists, because the two namespaces do overlap in practice and one
  shared list would let an extraction suppress an embedding run under a model
  of the same name.
- **The value is the provider's model id, not a schema version.**
  `ExtractionPipeline` passes `self._provider.model`, and the convention
  (stated on `Entity.model`) is provider-qualified and versioned —
  `"ollama/qwen3.6-27b-mtp"`, not `"qwen"`. These values are durable log
  contents; an unversioned name makes "re-extract everything the old model
  touched" unanswerable.

**`entities`** and **`relationships`** — the whole of what the run found, in
one event rather than one event per entity. Both default to a fresh empty
list, and **an empty extraction is a legitimate event**: a document yielding
nothing is a fact worth recording, and making it illegal would force every
emitter to branch on the empty case
(`test_an_empty_extraction_is_a_legitimate_event`). Either list may be empty
independently — entities with no edges is the ordinary shape of a short
document.

The two lists are ordered as the extractor produced them, and nothing sorts
or deduplicates them. Order carries no meaning on the wire; what *is*
load-bearing is that the handler writes entities before relationships, since
`GraphStore.upsert_relationship` raises `MissingEntityError` on an absent
endpoint. That is one handler's business, inside one event — see [notes for
consumers writing a fold](#notes-for-consumers-writing-a-fold).

`relationships` are **not** required to reference entities carried in the same
event: an edge may point at an entity a previous document's extraction wrote.
Nothing in the event validates that, and the projection resolves both
endpoints through the merge map before upserting, so an edge whose endpoint
was since absorbed into a canonical entity lands on the canonical one.

Field-level detail on the payload types is in [domain value
types](domain-value-types.md); the constraints that matter here are that
`Entity.source_id` is `SourceId | None` on the type but effectively required
in this event (the validator rejects any entity whose `source_id` differs from
the event's — including, since `None != source_id`, an entity carrying none),
and that both types carry their own `tenant_id`, which is what the validator
compares.

### Validator `_payloads_belong_to_this_document_and_tenant`

A `@model_validator(mode="after")` on `DocumentExtracted`. It runs on a fully
constructed, field-validated event, and enforces **four** rules in a fixed
order, raising on the first one violated:

```python
@model_validator(mode="after")
def _payloads_belong_to_this_document_and_tenant(self) -> DocumentExtracted:
    _reject_foreign_tenants(self, self.entities, "entities")
    _reject_foreign_tenants(self, self.relationships, "relationships")
    strays = {e.source_id for e in self.entities if e.source_id != self.source_id}
    ...
```

Each raises `ValueError`, which pydantic wraps: the exception a caller sees is
`pydantic.ValidationError`, with the text below appearing after pydantic's
`Value error, ` prefix. Catch `ValidationError`, and match on the message
fragment rather than the whole line.

| # | Rule | Field named in the message |
|---|---|---|
| 1 | every `Entity` carries the event's `tenant_id` | `entities` |
| 2 | every `Relationship` carries the event's `tenant_id` | `relationships` |
| 3 | every `Entity.source_id` equals the event's `source_id` | `entities` |
| 4 | every `Relationship.source_id` is `None` or equals the event's `source_id` | `relationships` |

**Rules 3 and 4 are not the same rule.** Rule 3 rejects an absent
`source_id`; rule 4 accepts one. The asymmetry is about history rather than
strictness — `Relationship.source_id` was added after this event shipped, and
this validator runs on **replay**, so every edge in an existing log has none
and rejecting that would make already-written events unreadable.

#### 1 and 2 — the tenant checks

Both are one call to the module-level helper `_reject_foreign_tenants(event,
payloads, field)` in `redstring/events/document.py`, which is also what
`EntitiesEmbedded` uses. It collects the *set* of offending tenants and raises
when it is non-empty:

```
entities carries tenants the event does not belong to: ['<tenant>', '<tenant>'] != <event tenant_id>
```

and, identically but for the field name:

```
relationships carries tenants the event does not belong to: ['<tenant>', '<tenant>'] != <event tenant_id>
```

Reading the message precisely, because the two halves are formatted
differently: the left-hand list is `sorted(str(t) for t in foreign)` — every
*distinct* foreign tenant, as a quoted string, sorted, so the text is stable
whatever order the payloads arrived in and however many payloads shared one
bad tenant. The right-hand side is the event's own `tenant_id`, interpolated
bare (no quotes) because it is a `UUID`. A payload list of a thousand entities
under two foreign tenants yields two ids, not a thousand.

The check is `p.tenant_id != event.tenant_id`, on the payload's **own**
`tenant_id` field — `Entity`, `Relationship` and `VectorRecord` each carry
one. The helper takes them as `Sequence[_HasTenant]`, a `Protocol` declaring
just the `tenant_id` property, rather than a union of the three concrete
types: the check is genuinely structural, and a union is the kind of thing
that gets missed when a fourth payload type appears.

**Why this rule exists at all** is worth stating, because it is not defence in
depth. The projection writes each payload under *its own* `tenant_id`, not the
event's — `GraphStore.upsert_entities` says so explicitly. So an event that
passed validation while carrying a foreign-tenant entity would not fail
anywhere downstream: it would quietly write into a tenant that never emitted
it, and the only trace would be a row under the wrong tenant. **This validator
is the one place the two values are ever compared.** Every other tenant
boundary in the library is enforced by the stores, which are being handed the
wrong tenant here rather than being asked to defend against it.

The helper's docstring records why the payload type is a `Protocol` and not an
`assert isinstance(...)`: an assertion vanishes under `python -O`, and the
set comprehension would then raise `AttributeError` instead of the
`ValueError` a caller catches — a validation failure turned into a crash in
exactly the configuration where it is hardest to diagnose.

#### 3 — entities name the document they came from

```
entities must be attributed to the document they were extracted from; found source_id ['<other>', '<other>'] in an event for '<source_id>'
```

The list is `sorted(map(str, strays))` — again the distinct offenders, sorted,
each as a string. The trailing `'<source_id>'` is `{self.source_id!r}`, the
event's own value **repr'd**, so it appears in quotes; that is deliberate, as
`SourceId` is a free-form `str` and a trailing space or an empty value is
invisible without them.

The comparison is `e.source_id != self.source_id` on plain strings — exact,
case-sensitive, and un-normalised, consistent with
[`document_stream`](#document_stream-tenant_id-source_id) hashing the id
exactly as given. `"doc-1"` and `" doc-1"` are two documents here as well.

One consequence to note: `Entity.source_id` is typed `SourceId | None` with a
default of `None`, so an entity is constructible without one — but
`None != "<source_id>"` holds, so **this validator rejects it**. Inside a
`DocumentExtracted`, `Entity.source_id` is effectively required, and the
message names `None` among the strays.

#### 4 — relationships name it too, or name nothing

```
relationships must be attributed to the document they were extracted from; found source_id ['<other>', '<other>'] in an event for '<source_id>'
```

Formatted identically to rule 3 and computed identically but for the `None`
case:

```python
foreign = {
    r.source_id
    for r in self.relationships
    if r.source_id is not None and r.source_id != self.source_id
}
```

`extraction/mapping.py` fills every edge's `source_id` from the document being
extracted, so an event this library produces satisfies the stronger rule
anyway. What the weaker rule buys is that an old event replays.

Note what neither rule constrains: an edge may still connect entities
extracted from a *different* document — see
[the field notes](#documentextracted). `source_id` on the edge says which
document *stated* it, not where its endpoints came from.

#### What the rules do not say

- **Nothing checks the payloads against the stream.** `aggregate_id` is a
  `uuid5` and cannot be reversed, so the validator compares payloads to the
  event's `source_id` field, and nothing compares that field to the stream it
  is appended to. Deriving the stream from the same `source_id` at the call
  site is what keeps them consistent; see
  [callers](#callers).
- **Nothing checks that relationship endpoints exist.** Neither in this event
  nor anywhere else on the write path — that is the projection's business, and
  `GraphStore.upsert_relationship` raises `MissingEntityError` on an absent
  endpoint.
- **Nothing rejects duplicates.** Two identical entities in one event validate;
  the projection upserts.
- **An empty event passes every rule vacuously** — no entities and no
  relationships is a legitimate `DocumentExtracted`.

#### Where these claims are enforced

`tests/unit/events/test_payloads.py::TestDocumentExtracted`:

- `test_entities_of_another_tenant_are_rejected` and
  `test_relationships_of_another_tenant_are_rejected`, each matching
  `"entities carries tenants"` / `"relationships carries tenants"` — so the
  field name in the message is pinned, not just the failure.
- `test_entities_attributed_to_another_document_are_rejected`, matching
  `"attributed to the document"`, and
  `test_relationships_attributed_to_another_document_are_rejected`, matching
  `"relationships must be attributed"` — the fragment includes the field name,
  since the two messages are otherwise identical.
- `test_a_relationship_with_no_provenance_is_still_a_legal_event`, which is
  the asymmetry against rule 3 rather than an omission from it. It builds the
  edge through a factory that does not pass `source_id` at all, so it is the
  shape a replayed old event produces.
- `test_the_document_a_carrier_names_is_the_one_it_is_appended_to`, the
  positive case.
- `test_an_empty_extraction_is_a_legitimate_event`.

The parametrisation is the load-bearing part and the reason to trust the
messages above. Each rejection test runs against **two** foreign values
bracketing the good one — a tenant sorting below the event's and one sorting
above (`BELOW_TENANT` / `PIVOT_TENANT` / `ABOVE_TENANT`), and source ids
`"doc-0"` and `"doc-2"` around `"doc-1"`. Every rule here is a `!=`, and a
mutant rewriting one as `<` or `>` is half right against a single random
`uuid4()`: it rejects the offenders that happen to sort the correct side and
accepts the rest, so the suite would pass or fail by luck. Bracketing makes
both mutants fail deterministically.

## `EntitiesEmbedded`

Embeddings computed for entities of one document. Defined in
`redstring/events/document.py` beside `DocumentExtracted`; category
`Document`, so it lands on the same
[`document_stream(tenant_id=…, source_id=…)`](#document_stream-tenant_id-source_id)
as that document's extraction, ordered against it.

| Field | Type | Default |
|---|---|---|
| `source_id` | `SourceId` (`str`) | required |
| `embedding_model` | `str` | required |
| `embeddings` | `list[VectorRecord]` | `Field(default_factory=list)` |

Plus the [inherited
envelope](#inherited-envelope-from-tenantdomainevent--domainevent), and
`event_version: int = 1` / `aggregate_type: str = DOCUMENT_CATEGORY`
redeclared on the class. `extra="forbid"` means there is nothing else on the
wire.

The event is separate from `DocumentExtracted` rather than a field on it
because embedding is a separate step against a separate model: **re-embedding
under a new model must not re-emit the entities**. One `DocumentExtracted`
and any number of `EntitiesEmbedded` for the same document is the ordinary
shape.

**`source_id`** — the document these vectors belong to, same type and same
meaning as on `DocumentExtracted`. Required, and deliberately redundant with
the stream for the same reason: `aggregate_id` is a `uuid5` of the tenant and
this value, and a hash cannot be read back, so a consumer of a global feed
would otherwise not know which document an event came from. Nothing normalises
it.

Note what it does **not** do here. `VectorRecord` carries `entity_id`,
`tenant_id`, `vector` and `metadata` — and no `source_id` — so there is no
per-payload document attribution to check, and the [validator](#validator-_embeddings_belong_to_this_tenant)
enforces the tenant rule only. The `source_id`-agreement rule that
`DocumentExtracted` applies to its entities has no counterpart on this event,
because there is nothing to compare it against.

**`embedding_model`** — which model produced these vectors. Two distinct
jobs, and both matter to a consumer:

- **It is on the event rather than implied.** A `VectorStore` is built for
  exactly one embedding model, and two models' vectors are not comparable even
  at equal dimension — cosine between them is a number with no meaning. The
  store cannot detect the mix: `VectorStore` validates *length* and raises
  `DimensionMismatchError` on a mismatch, which catches the model swap only
  when the dimensions happen to differ. Recording the model on the event is
  what lets a consumer route vectors to the right store, and what makes "which
  model wrote these rows" answerable from the log rather than from a deploy
  history. See [ADR 0002](../adr/0002-two-store-ports.md) on the port, and note
  its rule: changing embedding model means a new store, not an in-place change.
- **It is the idempotency key**, exactly as `model_version` is for extraction.
  `Document.record_embeddings` returns `None` and emits nothing when this
  document has already recorded an embedding run under this string, so a retry
  after a crash is a no-op. The two keys are held in **separate lists** on the
  aggregate (`embedding_models`, `extraction_model_versions`), so an extraction
  under a model name cannot suppress an embedding run under the same name —
  the namespaces do overlap in practice, and
  `test_embedding_and_extraction_do_not_share_an_idempotency_key` pins the
  separation.

The convention for the value is a provider-qualified, versioned model id —
`"ollama/nomic-embed-text"`, which is what the tests use — not a bare
`"nomic"`. These strings are durable log contents and the only record of which
model a vector came from.

**`embeddings`** — the vectors, one `VectorRecord` per entity, defaulting to a
fresh empty list. An empty run is constructible and passes validation
vacuously. There is no requirement that the entities embedded here appeared in
this document's `DocumentExtracted`, and nothing cross-checks the two: the
event carries `entity_id`s, and the fold writes them.

Order is not meaningful. `VectorProjection` folds the whole list through
`VectorStore.upsert_many`, which is idempotent and last-write-wins per
`(tenant_id, entity_id)`, so a redelivered event leaves the same rows and two
events for disjoint entity sets commute. Two events touching one entity are a
genuine last-write-wins settled by log order. Unlike the graph fold there is
no ordering constraint *within* the event either — there are no endpoints to
exist first.

One failure a consumer should expect: a vector whose length is not the store's
`dimension` raises `DimensionMismatchError` in the projection. That is a
**poison event** and goes to the DLQ rather than being retried — it means the
store and the emitter disagree about which model is in play, and accepting it
would produce plausible nonsense rather than an error.

Field-level detail on `VectorRecord` — including the `metadata` NUL rejection
and why `vector` is a mutable `list[float]` — is in [domain value
types](domain-value-types.md).

`EntitiesEmbedded` **is** in `redstring.__all__`, unlike the two
consolidation events; a caller reading the log for embeddings can import it
directly.

### Validator `_embeddings_belong_to_this_tenant`

A `@model_validator(mode="after")` on `EntitiesEmbedded`, and the event's
**only** validation beyond its field declarations. It is one line:

```python
@model_validator(mode="after")
def _embeddings_belong_to_this_tenant(self) -> EntitiesEmbedded:
    _reject_foreign_tenants(self, self.embeddings, "embeddings")
    return self
```

**Rule:** every `VectorRecord` in `embeddings` must carry the event's own
`tenant_id`. Nothing else is checked.

The helper is the same module-level `_reject_foreign_tenants(event, payloads,
field)` that `DocumentExtracted` calls twice — see [that
validator](#validator-_payloads_belong_to_this_document_and_tenant) for the
full reading of the message and the reasoning behind the rule. It takes its
payloads as `Sequence[_HasTenant]`, a `Protocol` declaring only the
`tenant_id` property, which is exactly why `VectorRecord` needs no special
casing here despite sharing no base class with `Entity` or `Relationship`.

#### The message

`ValueError`, wrapped by pydantic into a `ValidationError` with a
`Value error, ` prefix:

```
embeddings carries tenants the event does not belong to: ['<tenant>', '<tenant>'] != <event tenant_id>
```

The field name is the literal `"embeddings"` passed at the call site. The
left-hand side is `sorted(str(t) for t in foreign)` — every *distinct*
offending tenant, quoted and sorted, so a thousand records under two foreign
tenants yield two ids and the text is stable regardless of arrival order. The
right-hand side is the event's `tenant_id`, interpolated bare because it is a
`UUID`.

Note the singular-verb wording — "embeddings **carries** tenants" — matching
`DocumentExtracted`'s `entities carries` / `relationships carries` rather than
the `carry` used by `EntitiesMerged` and `MergeUndone`, which build their
messages separately. Match on the fragment `"embeddings carries tenants"`, as
the test does; do not assume the grammar generalises across events.

#### What it does not check

- **Nothing compares a payload to `source_id`.** `VectorRecord` has no
  `source_id` field — it carries `entity_id`, `tenant_id`, `vector` and
  `metadata` — so the document-attribution rule `DocumentExtracted` applies to
  its entities has no counterpart here. A vector for an entity extracted from
  a *different* document validates.
- **Nothing checks `entity_id` against anything.** There is no requirement
  that the entity exists, that it was carried by this document's
  `DocumentExtracted`, or that ids are unique within the list. Duplicate
  records for one entity validate; the fold upserts, last write wins.
- **Nothing checks vector dimension or `embedding_model`.** Length is the
  `VectorStore`'s business and surfaces as `DimensionMismatchError` in the
  projection — a poison event, not a validation failure. `embedding_model` is
  a free `str` with no format rule; the provider-qualified convention is a
  convention.
- **An empty `embeddings` list passes vacuously.** `set()` of nothing is
  falsy, so an embedding run that produced no vectors is a legitimate event.

#### Why the rule is here rather than downstream

For the same reason it exists on `DocumentExtracted`, and it is not defence in
depth. `VectorProjection` writes each record under **its own** `tenant_id`,
not the event's, so a foreign-tenant record would not fail anywhere: it would
be upserted into a tenant that never emitted it, leaving rows under the wrong
tenant and no error to notice. This validator is the one place the event's
tenant and the payload's tenant are ever compared.

#### Where these claims are enforced

`tests/unit/events/test_payloads.py`:

- `TestEntitiesEmbedded::test_embeddings_of_another_tenant_are_rejected`,
  matching `"embeddings carries tenants"` — so the field name in the message
  is pinned, not merely the failure. It is parametrised over `OTHER_TENANTS`,
  a tenant sorting **below** the event's and one sorting **above**
  (`BELOW_TENANT` / `PIVOT_TENANT` / `ABOVE_TENANT`). The check is a `!=`, and
  a mutant rewriting it as `<` or `>` is half right against a single random
  `uuid4()` — it rejects the offenders that happen to sort the correct side.
  Bracketing makes both mutants fail deterministically.
- `TestComparisonsAreByValueNotIdentity::
  test_an_embedding_tenant_that_arrived_as_a_string_is_accepted` constructs
  the record's tenant as `UUID(str(tenant_id))` — equal but not identical —
  and asserts the event builds. Every other test supplies the same object, so
  `is not` and `!=` would agree; a stored event round-tripped through JSON has
  exactly this equal-not-identical shape, and an identity comparison would
  reject good payloads in production only.

## `EntitiesMerged`

One or more entities absorbed into a canonical entity. Defined in
`redstring/events/merge.py`; category `Consolidation`, so it lands on
[`consolidation_stream(tenant_id=…)`](#consolidation_stream-tenant_id) — the
one stream per tenant — alongside `MergeUndone`.

| Field | Type | Default |
|---|---|---|
| `canonical_entity_id` | `EntityId` (`UUID`) | required |
| `merged_entity_ids` | `list[EntityId]` | `Field(min_length=1)` |
| `merge_reason` | `str \| None` | `None` |
| `redirections` | `list[RelationshipRedirection]` | `Field(default_factory=list)` |

Plus the [inherited
envelope](#inherited-envelope-from-tenantdomainevent--domainevent), and
`event_version: int = 1` / `aggregate_type: str = CONSOLIDATION_CATEGORY`
redeclared on the class. `extra="forbid"` means there is nothing else on the
wire.

**`canonical_entity_id`** — the entity that survives. Every absorbed entity's
edges point at this one afterwards, and the alias rows the projection writes
name it as their canonical target. It is *not* validated to exist: nothing in
the event checks the graph, and the aggregate's rules are about merge history
rather than about entities. What the `ConsolidationLog` aggregate does refuse
is a canonical entity that is **itself already an alias** — `MergeIntoAliasError`,
raised before anything is emitted — so a chain `A → B → C` is never recorded
in the first place, and a consumer may read `canonical_entity_id` as final at
the moment of the merge.

**`merged_entity_ids`** — the entities absorbed, in the order the caller
supplied them. `Field(min_length=1)`, so the field is required *and* an empty
list is rejected: a merge that absorbed nothing is not a merge, and recording
one would put an event in a permanent log that no fold can act on. A single
merge absorbs a **batch** rather than one entity — `ConsolidationService.resolve`
confirms every candidate above the threshold and emits one event for all of
them — so a consumer must iterate this list, not read `[0]`.

Order carries no meaning; the [validator](#validator-_the_merge_is_coherent)
forbids duplicates within it, and the aggregate forbids re-absorbing an entity
a previous merge already took (`DoubleMergeError`). The projection reads the
list twice — once to fetch the absorbed entities' names, once to write an
alias row each — and both are order-independent.

**`merge_reason`** — free-form text, optional, defaulting to `None`, and the
**only record of why** a merge happened. It is not decoration and it is not
structured: `ConsolidationService.resolve` sets it to `"score >= 0.9"`-style
text for candidates that cleared the high band, and to the adjudicating
model's own `AdjudicationVerdict.reason` for pairs a model was asked about,
joined with `"; "` when one merge mixes both. A caller invoking `merge`
directly may pass anything or nothing.

Do not parse it. The format is a formatting decision inside
`redstring/consolidation/service.py`, not a wire contract, and a batch merge
concatenates one reason per absorbed entity **without saying which reason goes
with which id**. It is an audit trail for a human reading the log, and it is
the difference between a recorded judgement call and an unexplained one. The
value is carried onto every `Alias` row the projection writes, so it survives
into the graph rather than only into the log.

**`redirections`** — the whole effect on the edge set: every edge that moved
onto the canonical entity, and every edge the merge dropped. A
`RelationshipRedirection` is `before: Relationship` plus
`after: Relationship | None`, and the two cases are what the fold branches on:

| `after` | Meaning | What the projection does |
|---|---|---|
| a `Relationship` | the edge moved onto the canonical entity | `upsert_relationship(after)` |
| `None` | the edge was **dropped** | `delete_relationship(before.id, before.tenant_id)` |

`after is None` is not "nothing happened". An edge is dropped when the merge
would make it a self-loop — both endpoints absorbed into the same canonical
entity — which `Relationship` rejects outright, or when it would duplicate a
claim another edge already makes, in which case the loser is deleted and the
winner is decided by a total preference order rather than by store order. Undo
recreates a dropped edge from `before`, which is why `before` carries the
whole `Relationship` and not a pair of endpoint ids: the type, confidence and
properties have to come back too.

**The list is recorded rather than recomputed**, and that is the reason the
field exists at all: recomputing it needs the pre-merge graph, which by
definition no longer exists once the projection has applied the event. It is
also what makes undo possible from the log alone — `MergeUndone.restored_relationships`
is exactly `[r.before for r in redirections]`, derived by the aggregate from
replayed state.

Two properties of what `plan_redirections` puts here, both worth relying on:

- **Only edges that change appear.** An edge already in its final shape, and
  not duplicated by the merge, produces *nothing*. A redirection whose `after`
  equalled its `before` would be a no-op the projection applies and undo
  "restores" — noise in a permanent log.
- **The list is sorted by `str(before.id)`**, so the payload is stable
  whatever order the store returned its edges in. That is stability of the
  event bytes, not an ordering the fold depends on; each redirection is
  applied independently.

An empty `redirections` list is legitimate and common: absorbing an entity
with no edges changes no edges. Nothing cross-checks the list against
`merged_entity_ids` — a redirection is not required to touch an absorbed
entity, and an absorbed entity is not required to have one.

`EntitiesMerged` is **not** in `redstring.__all__`. It is exported from
`redstring.events.__all__` only, which is a package-level `__all__` and not
the gated public surface; reaching it through the dotted path is reaching into
an internal module (see [ADR 0006](../adr/0006-the-public-surface-is-gated.md)).
Field-level detail on `Relationship` and `RelationshipRedirection` is in
[domain value types](domain-value-types.md); [ADR
0004](../adr/0004-consolidation-emits-events.md) records why consolidation
emits this event rather than writing the graph itself.

### Validator `_the_merge_is_coherent`

A `@model_validator(mode="after")` on `EntitiesMerged`, in
`redstring/events/merge.py`. It runs on a fully constructed,
field-validated event and enforces **three** rules in a fixed order, raising
on the first one violated. Each raises `ValueError`, which pydantic wraps: the
exception a caller sees is `pydantic.ValidationError`, with the text below
appearing after pydantic's `Value error, ` prefix. Catch `ValidationError`,
and match on the message fragment rather than the whole line.

| # | Rule | Message fragment |
|---|---|---|
| 1 | `canonical_entity_id` is not itself in `merged_entity_ids` | `merged into itself` |
| 2 | no id repeats within `merged_entity_ids`, offenders named | `contains duplicates` |
| 3 | every redirection's `before.tenant_id` is the event's `tenant_id` | `redirections carry tenants` |

The `min_length=1` on `merged_entity_ids` is a *field* constraint, not part of
this validator, so an empty list fails earlier with pydantic's own
`List should have at least 1 item after validation, not 0`
(`test_a_merge_must_absorb_something` matches `"at least 1"`). Note that
`mode="after"` means the validator never sees an empty list.

#### 1 — no self-merge

```python
if self.canonical_entity_id in self.merged_entity_ids:
    raise ValueError(f"an entity cannot be merged into itself: {self.canonical_entity_id}")
```

```
an entity cannot be merged into itself: <canonical_entity_id>
```

The id is interpolated bare, without quotes, because it is a `UUID`.

An entity absorbing itself is not merely redundant. The projection writes an
`Alias` row per absorbed entity pointing at the canonical one, so a
self-merge would make an entity its own alias — and the `ConsolidationLog`
aggregate refuses a *later* merge whose canonical entity is already an alias
(`MergeIntoAliasError`), so the entity would become permanently unmergeable
by a fact the event itself invented. Undo would then have to un-alias an
entity to itself. Cheaper to make it unconstructible.

The check is `in` on a `list[UUID]`, so it is by **value**: `UUID.__eq__`
compares the underlying integer, and a canonical id that arrived as a string
and was parsed into a distinct object is still caught.

#### 2 — no duplicates, and the offenders are named

```python
duplicates = sorted(
    str(entity_id) for entity_id, count in Counter(self.merged_entity_ids).items() if count > 1
)
if duplicates:
    raise ValueError(f"merged_entity_ids contains duplicates: {duplicates}")
```

```
merged_entity_ids contains duplicates: ['<id>', '<id>']
```

The list is every id appearing **more than once**, as a quoted string,
sorted — so the text is stable regardless of the order the caller supplied,
and an id repeated five times appears once. Ids appearing exactly once are
not listed.

Two things about this spelling are deliberate, and the comment in the source
records both.

**It is a `Counter`, not `len(set(x)) != len(x)`.** The length comparison was
what stood here first, and a cosmic-ray mutant rewriting `!=` as `is not`
survived it: CPython caches small ints, so `len()` on any list short enough to
appear in a test returns *the same int object* both times and the two
spellings agree. The check would have inverted only above the cache boundary —
i.e. only in production, on a real batch. Counting the offenders has no int
comparison in it to mutate, which is the general form of the fix (see
[CLAUDE.md](https://github.com/tyevans/redstring/blob/main/CLAUDE.md) on preferring a spelling with no `len()`
comparison in it at all).

**Naming the ids is not politeness.** A length comparison can say *that*
something repeated and never *which*, and the id is the only handle a
consumer has for finding the caller that built the list.

The rule exists because a repeated id would break the aggregate's arithmetic
rather than merely look untidy. `ConsolidationLog` tracks absorbed entities to
enforce "no double merge" (`DoubleMergeError`), and a repeat inside one event
would either double-count or — worse — make the first occurrence legal and the
second a violation of an invariant that same event had just created.

#### 3 — redirections carry this event's tenant

```python
foreign = {r.before.tenant_id for r in self.redirections if r.before.tenant_id != self.tenant_id}
```

```
redirections carry tenants the event does not belong to: ['<tenant>', '<tenant>'] != <event tenant_id>
```

The left-hand side is `sorted(str(t) for t in foreign)` — every *distinct*
offending tenant, quoted and sorted, so a thousand redirections under two
foreign tenants yield two ids. The right-hand side is the event's own
`tenant_id`, interpolated bare because it is a `UUID`.

Two differences from the equivalent rules on the document events, both easy to
trip over:

- **The verb is "carry", not "carries".** `DocumentExtracted` and
  `EntitiesEmbedded` share the module-level helper `_reject_foreign_tenants`
  and say `entities carries tenants`; the two consolidation events build
  their messages inline in `merge.py` and say `carry`. Match on
  `"redirections carry tenants"`, as the test does, and do not assume the
  grammar generalises across events.
- **Only `before` is checked.** `RelationshipRedirection.after` is
  `Relationship | None`, and its tenant is not compared to anything here.
  `before` is the edge as it existed and is what undo restores, so it is the
  one the log has to be right about.

The reason the rule is on the event rather than left to the store is the same
as everywhere else in this page, and it is not defence in depth: the
projection writes each payload under **its own** `tenant_id`, not the
event's, so a foreign-tenant redirection would not fail downstream — it would
quietly upsert or delete an edge in a tenant that never merged anything. This
validator is the one place the two values are compared.

#### What the rules do not say

- **Nothing checks any of the ids against the graph.** Neither
  `canonical_entity_id` nor the absorbed ids are required to exist, and the
  redirections' endpoints are not required to be among them. An event is a
  record of a decision, not a query.
- **Nothing cross-checks `redirections` against `merged_entity_ids`.** A
  redirection need not touch an absorbed entity, and an absorbed entity need
  not have one. An empty `redirections` list is legitimate and common —
  absorbing an entity with no edges changes no edges — and passes rule 3
  vacuously.
- **Nothing looks at merge *history*.** "This entity was already absorbed by
  an earlier merge" (`DoubleMergeError`) and "the canonical entity is itself
  an alias" (`MergeIntoAliasError`) are `ConsolidationLog` rules, enforced
  against replayed state before anything is emitted — see
  [aggregates](aggregates.md). A single event cannot see them.
- **`merge_reason` is unvalidated.** Any string, or `None`.

#### Where these claims are enforced

`tests/unit/events/test_payloads.py::TestEntitiesMerged`:

- `test_a_merge_must_absorb_something` — the `min_length=1` field constraint,
  matching `"at least 1"`.
- `test_an_entity_cannot_be_merged_into_itself`, matching
  `"merged into itself"`.
- `test_the_same_entity_cannot_appear_twice_in_one_merge`, matching
  `"duplicates"`.
- `test_redirections_of_another_tenant_are_rejected`, matching
  `"redirections carry tenants"` — so the field name and the verb in the
  message are pinned, not merely the failure. It is parametrised over
  `OTHER_TENANTS`, one tenant sorting **below** the event's
  (`BELOW_TENANT`) and one **above** (`ABOVE_TENANT`), bracketing
  `PIVOT_TENANT`. The check is a `!=`, and a mutant rewriting it as `<` or
  `>` is half right against a single random `uuid4()`: it rejects the
  offenders that happen to sort the correct side and accepts the rest, so the
  suite would pass or fail by luck. Bracketing makes both mutants fail
  deterministically.

`TestComparisonsAreByValueNotIdentity::
test_a_redirection_tenant_that_arrived_as_a_string_is_accepted` builds the
redirection's tenant as `UUID(str(tenant_id))` — equal but not identical — and
asserts the event constructs. Every other test here supplies the same object,
so `is not` and `!=` would agree; a stored event round-tripped through JSON
has exactly this equal-not-identical shape, and an identity comparison would
reject good payloads in production only.

## `MergeUndone`

A merge reversed. Defined in `redstring/events/merge.py` beside
`EntitiesMerged`; category `Consolidation`, so it lands on
[`consolidation_stream(tenant_id=…)`](#consolidation_stream-tenant_id) — the
one stream per tenant — after the `EntitiesMerged` it compensates.

| Field | Type | Default |
|---|---|---|
| `merge_event_id` | `UUID` | required |
| `canonical_entity_id` | `EntityId` (`UUID`) | required |
| `unmerged_entity_ids` | `list[EntityId]` | `Field(min_length=1)` |
| `restored_relationships` | `list[Relationship]` | `Field(default_factory=list)` |

Plus the [inherited
envelope](#inherited-envelope-from-tenantdomainevent--domainevent), and
`event_version: int = 1` / `aggregate_type: str = CONSOLIDATION_CATEGORY`
redeclared on the class. `extra="forbid"` means there is nothing else on the
wire.

This is a **compensating event, not a deletion**: the `EntitiesMerged` it
reverses stays in the log, and the undo is a second fact appended after it.
A consumer folding the whole stream sees both.

**`merge_event_id`** — the `event_id` of the `EntitiesMerged` being reversed.
Plain `UUID`, required. It is the envelope `event_id` of that event, not an
aggregate id and not a stream id, so a consumer resolves it by matching
`event.event_id` while folding.

Naming the merge is what makes an undo distinguishable from an unrelated
correction, and it is what the `ConsolidationLog` aggregate keys off:
`undo_merge` looks for a `MergeRecord` whose `merge_event_id` matches **and
which is not already undone**, raising `UnknownMergeError` otherwise —
`no merge in effect with event id <id>`. That one error covers both "never
happened" and "already undone", deliberately: from the aggregate's position
they are one case, since there is nothing to reverse either way. So a second
undo of the same merge is rejected at the write side rather than emitted, and
two `MergeUndone` events naming one `merge_event_id` do not occur in a
well-formed log.

Nothing in the event itself validates the id. It is not checked to exist, to
name an `EntitiesMerged` rather than some other event, or to be on this
tenant's stream — those are aggregate rules, enforced against replayed state
before anything is emitted. See [aggregates](aggregates.md).

**`canonical_entity_id`** — the entity the merge had made canonical, copied
from the merge record rather than supplied by the caller. It is the same value
the `EntitiesMerged` carried. It is *not* what the fold uses to remove
aliases — `GraphStore.remove_alias(entity_id, tenant_id)` is called per
*unmerged* id — so on the graph side this field is context rather than an
instruction. It matters to a reader of the log, and to any consumer
reconstructing merge history without replaying the aggregate.

**`unmerged_entity_ids`** — the entities freed, exactly the
`merged_entity_ids` of the merge being reversed and in the same order.
`Field(min_length=1)`, so the field is required *and* an empty list is
rejected: an undo that frees nothing is not an undo, and a permanent log entry
no fold can act on is worse than no entry.

That constraint is enforced by pydantic before the validator runs, with
pydantic's own message — `List should have at least 1 item after validation,
not 0`. It is tested (`test_an_undo_must_name_at_least_one_entity`, matching
`"at least 1"`), and the test exists because a mutant relaxing `min_length` to
`0` survived until it did: `EntitiesMerged` had the equivalent test from the
start, which is how the asymmetry went unnoticed.

Each id stops being an alias when the fold applies the event — the projection
calls `remove_alias` per id, and the aggregate `pop`s each from `alias_of`
with a default, so a redelivered `MergeUndone` finding the entry already gone
is idempotent rather than an error. Freeing the entities is the point: it is
what makes a bad merge **correctable** rather than merely recorded, since
`ConsolidationLog.merge` refuses any entity already in `alias_of`
(`DoubleMergeError`).

**`restored_relationships`** — the edges to put back, as whole
`Relationship`s. Derived by the aggregate as `[r.before for r in
record.redirections]`: every edge the merge touched, in its **pre-merge**
shape, whether that merge had moved it onto the canonical entity or dropped
it. Both cases restore identically, which is why the undo handler has no
branch where [`EntitiesMerged`'s does](#entitiesmerged) — `before` is a
complete `Relationship`, carrying the original endpoints, type, confidence and
properties, so nothing has to be reconstructed.

The list defaults to a fresh empty list and an empty one is legitimate and
common: reversing a merge that moved no edges restores none.

**The event carries this payload rather than naming it**, and that is the
design decision the module docstring exists to record. Naming
`merge_event_id` alone would suffice for a reader of the whole log, but a
projection handler sees one event at a time — resolving the id would mean a
read of the log from inside a fold, which is precisely what a projection
exists to avoid.

The payload is nonetheless **not the source of truth**. `ConsolidationLog`
rehydrates its merge history by replay, so when asked to undo it *derives* the
restoration from replayed state and writes it into the event. Recovery is by
replay; the payload is that recovery materialised once at the boundary, so
every downstream consumer gets it for free. A caller cannot supply it:
`ConsolidationService.undo(tenant_id=…, merge_event_id=…)` takes an id and
nothing else, and performs **no graph read at all**.

One consequence to plan for, stated because it looks like an oversight and is
not: **an undo overwrites concurrent edits.** The projection upserts every
restored relationship wholesale, so an edge legitimately modified between the
merge and the undo comes back at its pre-merge value rather than merged with
the newer one. A compensating event's job is to reproduce the state before the
event it compensates — the round-trip test in
`tests/unit/consolidation/test_merge_undo_round_trip.py` asserts exactly that —
and an undo preserving intervening edits would reproduce something else.

`MergeUndone` is **not** in `redstring.__all__`. Like `EntitiesMerged` it is
exported from `redstring.events.__all__` only, which is a package-level
`__all__` and not the gated public surface; reaching it through the dotted
path is reaching into an internal module (see [ADR
0006](../adr/0006-the-public-surface-is-gated.md)). Field-level detail on
`Relationship` is in [domain value types](domain-value-types.md); [ADR
0004](../adr/0004-consolidation-emits-events.md) records why consolidation
emits events rather than writing the graph itself, and [rebuild a
projection](../how-to/rebuild-a-projection.md) covers replaying a stream that
contains one.

### Validator `_restorations_belong_to_this_tenant`

A `@model_validator(mode="after")` on `MergeUndone`, in
`redstring/events/merge.py`, and the event's **only** validation beyond its
field declarations. In full:

```python
@model_validator(mode="after")
def _restorations_belong_to_this_tenant(self) -> MergeUndone:
    foreign = {r.tenant_id for r in self.restored_relationships if r.tenant_id != self.tenant_id}
    if foreign:
        raise ValueError(
            f"restored_relationships carry tenants the event does not belong "
            f"to: {sorted(str(t) for t in foreign)} != {self.tenant_id}"
        )
    return self
```

**Rule:** every `Relationship` in `restored_relationships` must carry the
event's own `tenant_id`. Nothing else is checked.

The `min_length=1` on `unmerged_entity_ids` is a *field* constraint, not part
of this validator, so an empty list fails earlier with pydantic's own
`List should have at least 1 item after validation, not 0`. `mode="after"`
means this validator never sees one.

#### The message

`ValueError`, wrapped by pydantic into a `ValidationError` with a
`Value error, ` prefix. The source splits the f-string across two lines mid
-phrase, so the text on the wire is one line:

```
restored_relationships carry tenants the event does not belong to: ['<tenant>', '<tenant>'] != <event tenant_id>
```

The left-hand side is `sorted(str(t) for t in foreign)` — every *distinct*
offending tenant, quoted and sorted, so a thousand restorations under two
foreign tenants yield two ids and the text is stable regardless of the order
they arrived in. The right-hand side is the event's own `tenant_id`,
interpolated bare because it is a `UUID`.

Two details of the wording, both worth pinning a match on rather than
inferring:

- **The verb is "carry", not "carries".** This event and `EntitiesMerged`
  build their messages inline in `merge.py`; `DocumentExtracted` and
  `EntitiesEmbedded` share the module-level helper `_reject_foreign_tenants`
  in `document.py` and say `entities carries tenants`. Match on
  `"restored_relationships carry tenants"`, as the test does.
- **The field name is the literal field name.** Unlike the document events,
  which pass the field name as an argument to the shared helper, this message
  hard-codes it, so there is one call site and one spelling.

Note also that this validator does **not** use `_reject_foreign_tenants`,
despite being the same rule: that helper lives in `redstring/events/document.py`
and is not imported here. The set comprehension is written out. The check is
identical in effect — `r.tenant_id != self.tenant_id` on the payload's own
`tenant_id` field, by value.

#### Why the rule is here rather than downstream

The same reason it exists on every other event on this page, and it is not
defence in depth. `GraphProjection._apply_undo` folds the payload with
`await self._store.upsert_relationships(event.restored_relationships)` — a
bulk upsert that writes each relationship under **its own** `tenant_id`, not
the event's. A foreign-tenant restoration would therefore not fail anywhere
downstream: it would be written into a tenant that never merged anything and
never undid anything, leaving edges under the wrong tenant and no error to
notice. **This validator is the one place the event's tenant and the
payload's tenant are ever compared.**

It is a cheap check with an unusually small surface to protect, because the
payload is not caller-supplied: `ConsolidationLog.undo_merge` builds it as
`[r.before for r in record.redirections]` from its own replayed state, and
`ConsolidationService.undo(tenant_id=…, merge_event_id=…)` accepts no
relationships at all. The rule is what makes that provenance checkable at the
boundary rather than merely believed.

#### What it does not check

- **Nothing checks `unmerged_entity_ids` at all** — not for duplicates, not
  against `canonical_entity_id`, not against the restorations' endpoints.
  `EntitiesMerged` has both a self-merge rule and a duplicates rule over the
  corresponding field; `MergeUndone` has neither. It does not need them:
  the list is copied from the merge record, and that merge's own validator
  already rejected a self-merge and duplicates before it could be recorded.
- **Nothing checks `merge_event_id`.** Not that it exists, not that it names
  an `EntitiesMerged`, not that the merge is un-undone. Those are
  `ConsolidationLog` rules enforced against replayed state (`UnknownMergeError`)
  before anything is emitted — see [aggregates](aggregates.md).
- **Nothing checks `canonical_entity_id`.** It is not compared to the
  restorations' endpoints and is not required to appear among them.
- **Nothing checks the restorations against the graph.** An edge whose
  endpoints no longer exist validates here and fails in the fold, where
  `GraphStore` raises `MissingEntityError`.
- **Nothing rejects duplicate relationships.** The fold upserts.
- **An empty `restored_relationships` list passes vacuously** — `set()` of
  nothing is falsy, and reversing a merge that moved no edges restores none.

#### Where these claims are enforced

`tests/unit/events/test_payloads.py::TestMergeUndone`:

- `test_restorations_of_another_tenant_are_rejected`, matching
  `"restored_relationships carry tenants"` — so the field name and the verb
  in the message are pinned, not merely the failure. It is parametrised over
  `OTHER_TENANTS`, one tenant sorting **below** the event's (`BELOW_TENANT`)
  and one **above** (`ABOVE_TENANT`), bracketing `PIVOT_TENANT`, which is also
  the event's `tenant_id` and `aggregate_id`. The check is a `!=`, and a
  mutant rewriting it as `<` or `>` is half right against a single random
  `uuid4()`: it rejects the offenders that happen to sort the correct side and
  accepts the rest, so the suite would pass or fail by luck. Bracketing makes
  both mutants fail deterministically.
- `test_an_undo_must_name_at_least_one_entity` — the `min_length=1` field
  constraint, matching `"at least 1"`.
- `test_an_undo_names_the_merge_it_reverses` — the positive case, asserting
  `event.merge_event_id` round-trips.

`TestComparisonsAreByValueNotIdentity::
test_a_restored_relationship_tenant_that_arrived_as_a_string_is_accepted`
builds the relationship's tenant as `UUID(str(tenant_id))` — equal but not
identical — and asserts the event constructs. Every other test here supplies
the same object, so `is not` and `!=` would agree; a stored event
round-tripped through JSON has exactly this equal-not-identical shape, and an
identity comparison would reject good payloads in production only.

## Payload types referenced by the wire schema

The four events declare only the fields tabled above, and every non-scalar one
of those is a **domain** type. Nothing on the wire is defined in
`redstring/events/`: the package imports its payloads from
`redstring.domain` and adds no types of its own.

| Type | Defined in | Carried by |
|---|---|---|
| `Entity` | `redstring/domain/entity.py` | `DocumentExtracted.entities` |
| `Relationship` | `redstring/domain/relationship.py` | `DocumentExtracted.relationships`, `MergeUndone.restored_relationships`, and both halves of a `RelationshipRedirection` |
| `VectorRecord` | `redstring/domain/vector.py` | `EntitiesEmbedded.embeddings` |
| `RelationshipRedirection` | `redstring/domain/consolidation.py` | `EntitiesMerged.redirections` |
| `EntityId` = `UUID` | `redstring/domain/ids.py` | `EntitiesMerged.canonical_entity_id` / `.merged_entity_ids`, `MergeUndone.canonical_entity_id` / `.unmerged_entity_ids` |
| `SourceId` = `str` | `redstring/domain/ids.py` | `DocumentExtracted.source_id`, `EntitiesEmbedded.source_id` |
| `TenantId` = `UUID` | `redstring/domain/ids.py` | not a field of any event — the envelope's `tenant_id`, and the field on every payload the validators compare it against |

That the payloads live in `domain` and not in `events` is the layering: the
[architecture contract](https://github.com/tyevans/redstring/blob/main/CLAUDE.md) puts `domain` at the bottom and
`events` directly above it, so an event may name a domain type and a domain
type can never name an event. A payload is therefore usable — and testable —
without an event around it.

### The three id aliases

`redstring/domain/ids.py` is nine lines and declares four aliases, three of
which appear in the schema:

```python
EntityId = UUID
RelationshipId = UUID
TenantId = UUID
SourceId = str
```

They are **plain aliases, not `NewType` and not wrapper classes**, so they
carry no validation and no nominal typing: `EntityId` *is* `UUID`, and mypy
will not complain about passing a `RelationshipId` where an `EntityId` is
expected. They are documentation of intent at the point of declaration, and
nothing more. Two consequences on the wire:

- **`EntityId` and `TenantId` serialise as UUID strings; `SourceId` as an
  ordinary string.** There is no envelope in the JSON to distinguish them from
  any other `UUID` or `str` field.
- **A `SourceId` is free-form text**, which is why
  [`document_stream`](#document_stream-tenant_id-source_id) has to reject a
  blank one and why the `source_id` comparison in
  [`DocumentExtracted`'s validator](#validator-_payloads_belong_to_this_document_and_tenant)
  is exact and un-normalised.

`RelationshipId` is the fourth alias. It never appears as an event field, but
it is the type of `Relationship.id`, which is how a redirection identifies the
edge it moves.

All four are exported from `redstring.__all__`, so a consumer may annotate
against them without reaching into a dotted path.

### `Entity`

The largest payload. Required: `id`, `tenant_id`, `name`, `normalized_name`,
`entity_type`, `extraction_method`, `confidence`. Optional, with defaults:
`original_entity_type`, `description`, `source_id`, `source_text`,
`external_ids`, `properties`, `model`, `temporal`, `blocking_keys`.

Three of its rules bear on reading the log:

- **`source_id` is `SourceId | None` on the type and effectively required
  inside `DocumentExtracted`** — that event's validator rejects any entity
  whose `source_id` differs from the event's, and `None` differs.
- **`extraction_method` is an `ExtractionMethod` str-enum**, serialised as its
  value (`"llm"`, `"pattern"`, `"schema_org"`, `"open_graph"`, `"hybrid"`,
  `"manual"`). It deliberately names *how* the entity was derived and never
  which vendor answered — vendor identity goes in `model`, because these
  values outlive the vendor.
- **`model` may only be set for `LLM` and `HYBRID`**; any other method with a
  `model` raises. The convention for the value is provider-qualified and
  versioned, the same convention `DocumentExtracted.model_version` follows.

`temporal` is a `TemporalExtent` (which brings `DatePrecision` and
`UncertaintyMarker`), and `blocking_keys` is a `frozenset[str] | None` that
the entity *carries* — consolidation's blocking is a pure key function, and
the store only groups by what it is given.

### `Relationship`

A directed, typed edge: `id`, `tenant_id`, `source_entity_id`,
`target_entity_id`, `relationship_type`, `confidence`, and a `properties`
dict. Two validators, both of which a consumer can rely on for every
relationship anywhere in the log:

- `confidence` is within `0.0..1.0` (as on `Entity`).
- **self-loops are rejected outright** — `source_entity_id` and
  `target_entity_id` must differ. That rule is why a merge absorbing *both*
  endpoints of an edge has to drop the edge rather than rewrite it, which is
  where a `RelationshipRedirection` with `after is None` comes from.

There is no `source_id` on a `Relationship`, which is why
`DocumentExtracted`'s document-attribution rule applies to entities only.

### `VectorRecord`

`entity_id`, `tenant_id`, `vector: list[float]`, plus an inherited
`metadata: dict[str, Any]`. Notably **no `source_id`** — hence no
document-attribution check on `EntitiesEmbedded` — and **no dimension
constraint**: length is the `VectorStore`'s business and surfaces as
`DimensionMismatchError` in the projection rather than as a validation error
here.

`vector` and `metadata` are mutable on purpose. A store handing back its own
object would let a caller corrupt stored state, and the compliance suite's
mutation-isolation property would be unfalsifiable against an immutable
container — there would be nothing to mutate. `metadata` rejects any string
containing a NUL, at any nesting depth, because Postgres `jsonb` cannot hold
one and a Python dict can: without the rule the in-memory adapter would accept
metadata pgvector refuses.

`VectorMatch`, its sibling in the same module, is the *answer to a query* and
carries a score. It is not a payload type — nothing in the log holds one.

### `RelationshipRedirection`

The only payload type not used by the document events. Two fields:

```python
before: Relationship
after: Relationship | None = None
```

`after is None` means **the edge was dropped**, not that nothing happened —
see [`EntitiesMerged`'s `redirections`](#entitiesmerged) for the fold's two
branches.

It carries a `mode="after"` validator of its own, `_after_is_the_same_edge`,
which is easy to miss because it fires while an `EntitiesMerged` is being
constructed and reports as that event's `ValidationError`. When `after` is not
`None` it must match `before` on two fields:

```
after must describe the same relationship as before: <after.id> != <before.id>
after must belong to the same tenant as before: <after.tenant_id> != <before.tenant_id>
```

Both ids are interpolated bare, being `UUID`s. The rule exists because a
redirection is applied by upserting `after` **over the id it shares with
`before`**: differing ids would create a second edge and leave the original in
place, and the undo — which upserts `before` — would then be a no-op on half
the change. The tenant half of the same check is that leak crossing a tenant
boundary.

Note the interaction with
[`_the_merge_is_coherent`](#validator-_the_merge_is_coherent): that validator
compares only `before.tenant_id` to the event's tenant. It does not need to
check `after`, because this validator has already pinned `after.tenant_id` to
`before.tenant_id`.

`RelationshipRedirection` is **not** exported from `redstring.__all__`, nor
from `redstring.events.__all__`; it is reachable only as
`redstring.domain.consolidation.RelationshipRedirection`, which is an
internal dotted path (see [ADR
0006](../adr/0006-the-public-surface-is-gated.md)). That is consistent with
`EntitiesMerged` itself being un-exported.

### Which of these are public

| Type | In `redstring.__all__` |
|---|---|
| `Entity`, `Relationship`, `VectorRecord` | yes |
| `EntityId`, `RelationshipId`, `SourceId`, `TenantId` | yes |
| `ExtractionMethod`, `TemporalExtent`, `DatePrecision`, `UncertaintyMarker`, `VectorMatch`, `Alias` | yes |
| `RelationshipRedirection` | **no** |

Exporting a type pulls its closure — `Entity` obliges `TemporalExtent`, which
obliges `DatePrecision` — so the payload types a caller of `build_graph`
actually constructs and reads are all importable from the package root.
`RelationshipRedirection` is the exception because the event carrying it is
also internal.

Field-level detail for every type above — including `TemporalExtent`'s
precision and uncertainty model, and the `VectorMatch` score scale — is in
[domain value types](domain-value-types.md).

## Notes for consumers writing a fold

- **One `DocumentExtracted` per document**, carrying every entity and
  relationship the run found — not one event per entity.
- **Within an event, write entities before edges.**
  `GraphStore.upsert_relationship` raises `MissingEntityError` when an
  endpoint is absent, so the ordering is one handler's business.
- **Between events, the fold is order-independent.** That is the point of the
  coarse event: no cross-event ordering between an edge and its endpoints, so
  reordering or redelivery cannot produce a poison event out of good data.
- **Extraction is idempotent per `model_version`.** The `Document` aggregate
  refuses a second `DocumentExtracted` for a model version it has already
  recorded, so a retry after a crash is a no-op.
- **Upsert semantics mean removals are not expressible.** A re-extraction that
  finds *fewer* entities than the previous run cannot express the removal; the
  earlier entities survive. Tracked as BACKLOG **B32**.

See [drive projections from an event
store](../how-to/drive-projections-from-an-event-store.md) and [rebuild a
projection](../how-to/rebuild-a-projection.md).

## Compatibility and versioning

- Adding an optional field with a default is backwards compatible for readers
  that ignore unknown keys — but note `extra="forbid"`, so an **older** class
  reading a **newer** event fails validation. Roll readers before writers.
- Removing or renaming a field, changing a type, or tightening a validator is
  breaking, and requires bumping that event's `event_version` and a migration
  path for stored events.
- Renaming an event class renames the wire name, because `event_type` is
  derived from it. Pinning the old name means declaring `event_type`
  explicitly plus `suppress_event_type_warning = True`.
- **This page moves when `event_version` moves.** A version bump that leaves
  this page unchanged is a documentation bug.

## Where each claim is enforced

`tests/unit/events/test_schema.py`, parametrised over `KG_EVENT_TYPES`:

- `test_the_schema_is_not_empty`
- `test_every_event_declares_its_schema_version_explicitly` — `event_version`
  is in the class's own annotations and defaults to `1`
- `test_no_event_declares_its_event_type_by_hand` — and
  `event_type_name() == __name__`
- `test_every_event_belongs_to_one_of_the_two_stream_categories`
- `test_every_event_requires_a_tenant`
- `test_every_event_rejects_fields_it_does_not_declare`
- `test_every_event_resolves_from_the_registry_by_its_wire_name`
- `test_the_tuple_lists_exactly_the_registered_events`

`tests/unit/events/test_streams.py` covers stream-id derivation and the blank
`source_id` rejection; `tests/unit/events/test_payloads.py` covers the
validators.

`tests/unit/projections/test_replay_coverage.py`, also parametrised over
`KG_EVENT_TYPES`:

- `test_every_event_type_is_replayed_by_a_pinned_case`
- `test_every_event_type_has_a_projection_handler`

## Related

- [ADR 0001 — event log schema and granularity](../adr/0001-event-log-schema-and-granularity.md)
- [ADR 0004 — consolidation emits events](../adr/0004-consolidation-emits-events.md)
- [Aggregates reference](aggregates.md)
- [Domain value types reference](domain-value-types.md)
