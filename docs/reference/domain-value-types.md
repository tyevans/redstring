# Domain value types

The types under `redstring.domain` are the vocabulary everything else in the
library is written in: what an entity is, what a relationship is, what a
timespan is, and which values each of them will accept. They are pure — the
package depends on the standard library and pydantic and nothing else, does no
I/O, and knows nothing about a store, a session, or an ORM.

This page is the specification for those types: fields, defaults, validation
rules, derived properties, and the module-level functions that operate on them
(`parse_temporal`, `blocking_keys_for`, `normalize_name`, `cosine_score`, and
the rest). It states what the code does, not how to accomplish a task with it —
for a worked example see
[How to query a timeline](../how-to/query-a-timeline.md).

Most of these types are values rather than records: constructed, compared, and
passed around, never mutated in place. The ones that are validated are
validated at construction, so an invalid `Entity` or `TemporalExtent` does not
exist to be handed on. Where a rule looks arbitrary — a required timezone, a
confidence ceiling, an interval that is half-open — the reasoning is recorded
either in the module docstring or in an ADR:
[ADR 0005](../adr/0005-temporal-inference-on-read.md) for why temporal edges
are inferred on read rather than persisted, and
[ADR 0010](../adr/0010-one-total-order-for-preference.md) for the single total
order every tie-break in the library defers to.

Not every type named here is importable from `redstring`. The public surface
is `redstring.__all__` and nothing else — see
[ADR 0006](../adr/0006-the-public-surface-is-gated.md). Types that reach you
inside an event rather than by import are described in
[Events](events.md).

## Scope and how to read this page

Everything documented here lives in `src/redstring/domain/`, one section per
concern, **in page order**:

| Section | Module |
|---|---|
| Identifiers | `ids.py` |
| Entity | `entity.py` |
| Relationship | `relationship.py` |
| SourceDocument | `source.py` |
| Alias | `alias.py` |
| Temporal value types | `temporal.py` |
| Vector types | `vector.py` |
| Name normalization | `normalization.py` |
| Blocking keys | `blocking.py` |
| Similarity | `similarity.py` |
| Temporal intervals | `interval.py` |
| Merge strategies | `merge_strategy.py` |
| RelationshipRedirection | `consolidation.py` |
| Temporal parsing | `temporal_parsing.py` |
| Error types | `exceptions.py` |

`tests/unit/test_reference_map_tables_are_honest.py` fails if a row here names
a section this page does not have, or if the two orders disagree. That gate
exists because the absence of one is what this table's previous version
demonstrated: it listed nine sections that had never been written, the links
to them were the only trace, and repairing the links made the gap invisible
again.

Out of scope, deliberately: the write model (`aggregates`, and the events it
emits — see [Events](events.md)), the store ports and their adapters, and the
extraction, consolidation and temporal-inference pipelines that consume these
types. Where one of those is the reason a rule exists, this page links to it
rather than restating it.

Each type is presented the same way — its fields and defaults first, then the
validation it enforces, then any derived properties. Two conventions run
through all of them:

- **"Rejected" means a `pydantic.ValidationError` at construction.** The
  validators in this package raise plain `ValueError`; pydantic wraps them.
  So "self-merge rejected" means `Alias(canonical_entity_id=x,
  alias_entity_id=x, ...)` raises, not that it constructs an object you are
  expected to check afterwards. Module-level *functions* are different, and
  each one's own entry says what it raises or returns — `parse_temporal`
  returns `None` for text it cannot read, `cosine_score` raises `ValueError`
  on a zero vector.
- **Defaults are stated because they are part of the type's promise.** These
  models are constructed directly by callers as often as through a factory, so
  a default is public behaviour rather than an implementation convenience.

A field described only by its type and default has no validation beyond what
pydantic derives from the annotation. Where a constraint is not visible in the
annotation — a required timezone, a bounded float, a cross-field rule — it has
its own "Validation" heading. The universal rules that repeat across several
types are stated once, under
[Universal validation rules](#universal-validation-rules), and the per-type
sections then name them rather than repeating them.

## Identifiers

`redstring.domain.ids` declares four names. All four are `NewType`s — distinct
to a type checker, identical to their base at runtime, with no wrapper classes
and no validation of their own:

```python
EntityId = NewType("EntityId", UUID)
RelationshipId = NewType("RelationshipId", UUID)
TenantId = NewType("TenantId", UUID)
SourceId = NewType("SourceId", str)
```

### EntityId, RelationshipId, TenantId (UUID) and SourceId (str)

| Name | Base type | Appears on |
|---|---|---|
| `EntityId` | `uuid.UUID` | `Entity.id`, `Relationship.source_entity_id` / `target_entity_id`, `Alias.canonical_entity_id` / `alias_entity_id`, `VectorRecord.entity_id`, `VectorMatch.entity_id` |
| `RelationshipId` | `uuid.UUID` | `Relationship.id` |
| `TenantId` | `uuid.UUID` | `Entity.tenant_id`, `Relationship.tenant_id`, `Alias.tenant_id`, `VectorRecord.tenant_id` |
| `SourceId` | `str` | `SourceDocument.id`, `Entity.provenance.source_id` (optional), `Relationship.source_id` (optional) |

There is no `AliasId`: `Alias.id` is annotated as a bare `uuid.UUID`.

**A type checker distinguishes them; the runtime does not.** `TenantId(u)`
returns `u` itself — `NewType` compiles to the identity function — so every
`isinstance(x, UUID)` check, every dict keyed on an id, and every existing
call site passing a bare `uuid4()` keeps working unchanged. What changes is
that `mypy` now rejects passing a `TenantId` where an `EntityId` is expected,
which is the swap the store ports are most exposed to: they key entities on
`(tenant_id, id)` and take both as arguments, adjacent, of what used to be one
type.

The direction of the asymmetry is worth stating, because it is what keeps the
change from being a break. A `TenantId` **is** a `UUID` to mypy, so handing one
to anything expecting a plain `UUID` is fine; a plain `UUID` is *not* a
`TenantId`, so producing one requires naming the role. That puts the annotation
burden exactly at the boundaries where a raw UUID enters the domain — a Neo4j
row, an event field typed by the event framework — and nowhere else. Those
call sites are the only ones in `src/` that had to change.

They still carry no validation. `EntityId("not a uuid")` is not an error at
runtime and not an error to mypy either (mypy checks the argument against
`UUID` — a `str` there *is* an error, but a malformed `UUID` cannot exist).
The nominal typing is the whole of what they add.

The three UUID aliases are UUIDs because the library never allocates
identifiers from a store. An id is chosen by the caller (or by extraction)
before anything is written, so it has to be generable without a round trip
and has to not collide across processes. `SourceId` is a `str` for the
opposite reason: it names something outside the library — a URL, a filename,
a row key in the caller's own system — and the library never fetches content,
so it never gets to pick that name. It is stored and echoed back, not parsed.

**`tenant_id` is half of every key.** The store ports key entities on
`(tenant_id, id)` and relationships likewise, and every read method takes a
`tenant_id` argument alongside the id it is looking up. An `EntityId` on its
own does not identify anything; the same UUID under two tenants is two
different entities. See [Events](events.md) for the same pairing on the event
side — every event in the library carries a tenant.

There is no validation attached to any of these. A `UUID` field rejects a
malformed string the way pydantic always does, and a `SourceId` field accepts
any `str` — including an empty one, which the library does not treat as
special. (`SourceDocument.text` must be non-blank; `SourceDocument.id` has no
such rule.)

All four names are exported from `redstring` — see
[ADR 0006](../adr/0006-the-public-surface-is-gated.md). They are exported
because they appear in the signature of exported types, which is exactly the
condition the public-surface gate enforces.

## Universal validation rules

Three rules recur across the types on this page. They are stated once here;
each type's own "Validation" heading names the rule rather than restating it.

None of the three is universal in the sense of being applied by a shared base
class or a global pydantic config — each type declares its own validator, and
the "universal" part is the rule, not the mechanism. Where a type does *not*
apply one of these rules, that is a deliberate difference and is called out
below.

### Every datetime field is timezone-required

Every validated `datetime` field in `redstring.domain` rejects a naive value.
There are five of them, across three models:

| Type | Field | Optional? | Message |
|---|---|---|---|
| `TemporalExtent` | `start_date`, `end_date`, `publication_date` | yes | `"datetime must be timezone-aware"` |
| `SourceDocument` | `published_at` | yes | `"published_at must be timezone-aware"` |
| `Alias` | `merged_at` | no | `"merged_at must be timezone-aware"` |

For the optional fields the rule applies only to a value that is present:
`None` is accepted and is not a naive datetime. `Alias.merged_at` is required,
so every `Alias` in existence carries an aware timestamp.

The message differs slightly by field — `TemporalExtent` covers its three
fields with one validator and one message, the other two name their field —
but the condition is the same one in each case: `value.tzinfo is None`. A value whose
`tzinfo` is set to any offset is accepted; the rule is about the *presence* of
an offset, not about UTC. Nothing in the domain package normalizes an aware
datetime to UTC on the way in, so a value stored with `+02:00` comes back
with `+02:00`.

The reason is comparison. These values are ordered against each other —
`end_date >= start_date` here, interval bounds in `domain/interval.py`,
timeline ordering in
[How to query a timeline](../how-to/query-a-timeline.md) — and Python raises
`TypeError` when an aware and a naive datetime are compared. Admitting one
naive value would make every later comparison it participates in a runtime
error at a distance from the construction that allowed it, so the rejection
happens at construction instead.

Two datetimes in this package are *not* covered by a model validator, because
neither is a pydantic field:

- The `reference_date` argument to `parse_temporal` enforces the same
  requirement in the same way — `reference_date.tzinfo is None` — but as a
  function it raises a plain `ValueError` rather than a
  `pydantic.ValidationError`.
- `Bounds.lower` and `Bounds.upper` are fields of a `NamedTuple`, which
  validates nothing at all. They do not need to: `bounds()` derives them from
  a `TemporalExtent` that was validated at construction, so an aware value is
  what they are built from. Constructing a `Bounds` by hand from naive
  datetimes is possible and will fail later, in a comparison, rather than
  sooner.

### Confidence and score fields are bounded 0.0..1.0 inclusive

Every confidence and every similarity score in the domain package lies on
`0.0..1.0`, and **both endpoints are legal values**. `0.0` and `1.0`
construct; anything strictly outside is rejected. `-0.0` compares equal to
`0.0` and is accepted, and is kept as `-0.0`. `NaN` is rejected everywhere,
though by two different routes.

| Type | Field | How it is bounded | Default |
|---|---|---|---|
| `Entity` | `confidence` | `field_validator`: rejects unless `0.0 <= value <= 1.0` | none — required |
| `Relationship` | `confidence` | `field_validator`: rejects unless `0.0 <= value <= 1.0` | none — required |
| `VectorMatch` | `score` | `Field(ge=0.0, le=1.0)` | none — required |
| `SimilarityFeatures` | `name`, `embedding`, `graph` | `Field(ge=0.0, le=1.0)` | `None` |

The two mechanisms differ only in what the error looks like. Both
`confidence` validators raise the same message,
`"confidence must be between 0.0 and 1.0"`, which pydantic reports as a
`value_error`; the `Field` constraints produce pydantic's own
`greater_than_equal` / `less_than_equal` errors, naming the bound rather than
the field's intent. The accepted interval is identical either way, closed at
both ends.

They also reach the same verdict on `NaN` by different reasoning. The
validators reject it because `0.0 <= nan <= 1.0` is `False`, so the guard
fires and the message reads as it does for any out-of-range value. The `Field`
constraints reject it because both comparisons against a `NaN` are `False`;
the reported error is `less_than_equal` with `input_value=nan`, which is
accurate but reads oddly. Neither type admits a `NaN` confidence or score,
which matters more than the tidiness of the message: a `NaN` propagates
silently through every subsequent arithmetic operation and compares `False`
against every threshold, so a single one entering a scoring loop makes that
pair permanently unmergeable with no error anywhere.

`Entity.provenance.confidence` and `Relationship.confidence` have no default. A
confidence of `1.0` is something a caller asserts, not something the type
assumes on their behalf, so every `Entity` and `Relationship` in existence
carries a number somebody chose. The `SimilarityFeatures` fields default to
`None`, which means "not computed" and is distinct from a computed `0.0`.

Four nearby numbers reach this scale differently, and are worth reading as
exceptions to the rule rather than as instances of it:

- `FeatureWeights.name` / `.embedding` / `.graph` are bounded `ge=0.0` with
  **no upper bound**. They are weights, not scores; `combined_score`
  normalizes over whichever features are present, so their magnitudes matter
  only relative to each other. A model validator separately rejects the
  all-zero vector, because weights that score every pair identically produce
  output indistinguishable from a corpus containing no duplicates — a
  plausible answer, and therefore one nobody investigates.
- `cosine_score` and `combined_score` return values on `0.0..1.0`, but they
  get there by **clamping rather than rejecting**. Each computes a value that
  should already be in range and can land a hair outside it through
  floating-point error: `cosine_score` because the cosine of two identical
  float32 vectors can exceed `1.0`, `combined_score` because a weighted mean
  divided by the exact sum of those same weights is still a division. Clamping
  at the producer is what lets the bound on the consumer be strict — the
  alternative is a `ValidationError` constructing a `VectorMatch` for a pair
  of identical vectors, which is what slice 0 actually hit.
- `string_similarity` and `graph_similarity` return `0.0..1.0` with no
  enforcement at all, because they are functions rather than fields — the
  interval is a property of Jaro-Winkler and of Jaccard, not a check. One
  value in that range is chosen rather than inherited: `graph_similarity` of
  two *empty* neighbour sets is `0.0`, not the conventional Jaccard `1.0`, so
  that knowing nothing about either entity cannot read as perfect agreement.

A bound is not a threshold, and the difference bites on the score scale in
particular. `VectorStore.search` takes `min_score` on this same `0.0..1.0`
scale and drops results scoring strictly below it, so `min_score=0.0` is not
a disabled filter but an assertion that any match at all will do, and `0.5` is
the score of two *orthogonal* vectors rather than a midpoint between "alike"
and "unalike". The mapping that puts it there is `(1 + cosine) / 2`.

### Non-blank string fields

Exactly two model fields in the domain package reject a value that is empty or
consists only of whitespace:

| Type | Field | Validator | Message |
|---|---|---|---|
| `Entity` | `name` | `field_validator("name")` | `"name must not be blank"` |
| `SourceDocument` | `text` | `field_validator("text")` | `"text must not be blank"` |

Both are spelled the same way — `if not value.strip(): raise ValueError(...)` —
so `""`, `"   "`, `"\n"`, `"\t "` and any other whitespace-only string are
rejected, and pydantic reports the `ValueError` as a `ValidationError`.

**Neither validator strips.** A value that merely *has* surrounding whitespace
is accepted and stored exactly as given: `Entity(name="  Ada  ")` constructs
and keeps both pairs of spaces. Normalization is a separate concern with its
own function and its own field — `normalize_name`
and `Entity.normalized_name`, which is populated by the caller rather than
derived here.

The list is short on purpose, and the fields *not* on it are worth naming
because they look like they should be:

- `Entity.normalized_name`, `Entity.entity_type` and
  `Relationship.relationship_type` are required `str`s with **no** blank
  check. A blank `entity_type` constructs, and `blocking.entity_type_key`
  documents that it treats the resulting `"t:"` as the right answer rather
  than an error: those entities do share a type, vacuous as it is, and a key
  of `None` would leave them with one fewer way to be found.
- `SourceDocument.id`, and every other `SourceId`, accepts an empty string —
  already stated under [Identifiers](#identifiers).
- `Alias.alias_name` and `Alias.alias_normalized_name` are `str | None`.
  Absence is expressed as `None` rather than as `""`, and the field's own
  docstring argues the point: the projection that writes aliases folds
  `EntitiesMerged`, which carries ids and no names, so a required field would
  have forced a fabricated one.
- `Entity.description`, `Entity.provenance.source_text`, `SourceDocument.uri`,
  `SourceDocument.title` and `Alias.merge_reason` are all optional and
  unchecked.

So the rule covers exactly the two fields whose blankness would be silently
lossy rather than merely odd: an entity with no name and a document with no
text are both things nothing downstream can do anything with. Two consequences
of that narrowness show up elsewhere in the library and are worth knowing here.

**`Entity.name` being non-blank is what makes the blocking keys total.**
`blocking.prefix_key` has no empty-name branch, deliberately: `Entity` rejects
a name whose `strip()` is falsy and `normalize_name` strips with the same
function, so a valid entity cannot normalize to nothing. The docstring makes
the argument that a guard there would be a branch no input reaches, which is
worse than none because it describes a situation that cannot arise. That is a
direct dependency on this validator — weakening it would silently make
`prefix_key` partial.

**`SourceDocument.id` being unchecked is caught later, not here.**
`events.streams.document_stream` raises a plain `ValueError`
(`"source_id must not be blank; it identifies the document's stream"`) because
it is the last point at which a blank id can be caught: hashed instead, it
would yield a valid-looking stream shared by every document that had one. See
[Events](events.md). Note the different error type — a function raising
`ValueError` rather than a model raising `ValidationError`, per the convention
in [Scope and how to read this page](#scope-and-how-to-read-this-page).

A third consequence is in extraction rather than the domain: because a blank
name raises, `extraction/mapping.py` checks `name.strip()` itself before
constructing, so one blank row from a model is dropped and counted rather than
failing the whole document. The domain rule is the reason that guard exists;
the domain does not soften for it.

## Entity

`redstring.domain.entity` declares `Entity` and `ExtractionMethod`. Both are
exported from `redstring`.

An `Entity` is a thing extracted from a source — a person, a place, a concept.
It is a plain pydantic `BaseModel`: not frozen, no `extra="forbid"`, so unknown
keyword arguments are ignored rather than rejected (unlike `Alias`) and fields
are assignable after construction. Validation runs at construction, so an
`Entity` that exists satisfies every rule below; assigning to a field
afterwards is not re-validated.

Two fields are deliberately absent, and their absence is asserted by tests:

- **No `is_canonical` / `is_alias_of`.** Alias-ness is an edge, carried by
  `Alias`, not a flag on the entity.
- **No `synced_at`.** The graph store *is* the store, not a cache of some
  other source of truth, so there is nothing to be in sync with.

### Fields and defaults

| Field | Type | Default |
|---|---|---|
| `id` | `EntityId` | none — required |
| `tenant_id` | `TenantId` | none — required |
| `name` | `str` | none — required |
| `normalized_name` | `str` | none — required |
| `entity_type` | `str` | none — required |
| `original_entity_type` | `str \| None` | `None` |
| `description` | `str \| None` | `None` |
| `external_ids` | `dict[str, str]` | `{}` |
| `properties` | `dict[str, Any]` | `{}` |
| `provenance` | `Provenance` | none — required |
| `temporal` | `TemporalExtent \| None` | `None` |
| `blocking_keys` | `frozenset[str] \| None` | `None` |

`source_id`, `source_text`, `extraction_method`, `model` and `confidence` are
no longer fields of `Entity`. They are fields of `Provenance`, reached as
`entity.provenance.confidence` and so on; there is no forwarding property, so
the old spelling raises `AttributeError` rather than quietly working.

### `Provenance`

`Entity` describes a thing in the world; `Provenance` describes the *claiming*
of it — who said it, when, how, from where, and how sure. The split is what
lets a merge ask which of two competing values was observed most recently
without asking an `Entity` a question about itself.

| Field | Type | Default |
|---|---|---|
| `observed_at` | `datetime` | none — required, and timezone-aware |
| `extraction_method` | `ExtractionMethod` | none — required |
| `confidence` | `float` | none — required |
| `source_id` | `SourceId \| None` | `None` |
| `source_text` | `str \| None` | `None` |
| `model` | `str \| None` | `None` |

- **`observed_at` is *record* time, and `TemporalExtent` is *world* time.**
  When this library was told, versus when the fact held. A document published
  in 1923 and extracted today has both, and nothing infers one from the other.
- **It is required, and that is the point.** An optional one would make a
  most-recently-observed merge work for some callers and refuse for others,
  with no way to tell which until it ran.
- **No clock reads it below `composition`.** `map_extraction` and
  `ExtractionPipeline.extract` take it as a required keyword argument, so
  re-extracting one document produces identical entities however much later it
  runs. `build_graph` is the only place in the library that reads a clock, and
  it takes an `observed_at` that overrides it.
- **`Relationship` deliberately does not have one.** It carries `confidence`
  and `source_id` but no `extraction_method` and no `model`, so sharing this
  type would mean three fields that are always absent.

Notes on the ones whose type does not tell the whole story:

- **`normalized_name` is supplied, not derived.** Nothing in `Entity`
  populates it from `name`, and no validator relates the two: an entity whose
  `normalized_name` disagrees with `normalize_name(name)` constructs happily.
  Callers pass the result of
  `normalize_name`.
- **`entity_type` is a free string, not an enum.** `entity_type="plot_point"`
  is legal. `original_entity_type` is where the source's own label is kept
  when extraction mapped it onto something else.
- **`external_ids` and `properties` default to empty dicts** written as bare
  mutable literals. Pydantic deep-copies a default per instance, so two
  entities constructed without them do not share a dict.
- **`blocking_keys` is `frozenset[str] | None`, and `None` is not the same as
  `frozenset()`** — it means the keys were never computed, where the empty set
  would mean they were computed and came out empty. The entity *carries* the
  keys; the store groups by them and computes nothing — `blocking_keys_for`
  is what computes them.
- **`model` names which model produced the entity**, by convention
  provider-qualified and versioned (`"ollama/qwen3.6-27b-mtp"`,
  `"anthropic/claude-opus-4-20250514"`) and never a bare family name like
  `"claude"`. The field's `description` says so, and a test asserts the
  description still says so. The reason is durability: these values land in an
  event log, where an unversioned name makes "re-extract everything the old
  model touched" unanswerable. `None` means no model was involved *or* that
  the extractor did not record one — the two are not distinguished.

### ExtractionMethod members

`ExtractionMethod` is declared `class ExtractionMethod(StrEnum)`, so every
member *is* a `str`: `ExtractionMethod.LLM == "llm"` is `True`, the member can
be used anywhere a string is expected, and pydantic serializes it as its
value. It is exported from `redstring` alongside `Entity` — see
[ADR 0006](../adr/0006-the-public-surface-is-gated.md).

Six members, and the set is exactly these six:

| Member | Value | May carry `model` | Produced by |
|---|---|---|---|
| `LLM` | `"llm"` | yes | `extraction.mapping`, the default `method` |
| `PATTERN` | `"pattern"` | no | `extraction.mapping`, when a caller passes it |
| `SCHEMA_ORG` | `"schema_org"` | no | `extraction.schema_org`, JSON-LD / microdata |
| `OPEN_GRAPH` | `"open_graph"` | no | `extraction.schema_org`, `og:` meta tags |
| `HYBRID` | `"hybrid"` | yes | callers combining patterns with a model |
| `MANUAL` | `"manual"` | no | callers constructing entities by hand |

The "may carry `model`" column is the enum's only behavioural consequence
inside the domain. `Entity` holds the two model-bearing members in a
module-level `frozenset` and a `model_validator` rejects a `model` alongside
any other member — the rule, its message and the reason `HYBRID` is on the
permissive side are under
[Validation](#validation-non-blank-name-confidence-range-model-only-for-llm-and-hybrid).
`extraction/mapping.py` keeps a second copy of that same set, deliberately, so
a missing provenance string is caught where the fix is obvious rather than
passing `Entity` validation and reaching the log unattributed.

**The members name how the entity was derived, never which vendor answered.**
There is no `OPENAI` or `ANTHROPIC` member and there will not be one: vendor
identity is adapter detail and belongs in
[`Entity.provenance.model`](#fields-and-defaults), which is versioned and survives model
upgrades. These values become persisted event payloads (see
[Events](events.md)), so a vendor name here would outlive that vendor's
presence in the codebase, and every historical row would still carry it. Two
tests pin this — one asserting each member's value, one asserting the *whole*
value set — so adding a member is a visible decision rather than an edit
nothing notices. The same argument, cited by name, is what removed vendor
branches from `llm/rate_limiter.py` and `llm/circuit_breaker.py`.

The values are the string form, not the member name: `"schema_org"` and
`"open_graph"` are lowercase with an underscore. That spelling is the
persisted one. `graph/adapters/neo4j.py` writes
`entity.provenance.extraction_method.value`
to the node and rebuilds it with `ExtractionMethod(node["extraction_method"])`,
so renaming a *value* would orphan every entity already stored, while renaming
a *member* would not. Round-tripping an `Entity` through `model_dump()` and
`model_validate()` goes through the same values.

There is no `UNKNOWN` member and no default: `Entity.provenance.extraction_method` is
required, so every entity in existence states how it was derived. Absence of
provenance is expressed on `model` (which is `None` when no model ran *or*
when the extractor did not record one), never on the method.

### Validation: non-blank name, confidence range, model only for LLM and HYBRID

The three rules below are declared on **two** models now. `Entity` keeps the
`name` rule; the `confidence` and `model` rules moved to `Provenance` with the
fields they constrain, and `Provenance` adds a fourth of its own —
`observed_at` must be timezone-aware, refused at construction because a naive
and an aware datetime raise `TypeError` only when compared, several layers
from anything that could say which entity was at fault.

Each raises a plain `ValueError`,
which pydantic surfaces as a `ValidationError` at construction — per the
convention in
[Scope and how to read this page](#scope-and-how-to-read-this-page). Nothing
here is re-checked on assignment: the model is not frozen and
`validate_assignment` is not set, so `provenance.confidence = 5.0` after
construction succeeds.

| Model | Validator | Kind | Message |
|---|---|---|---|
| `Entity` | `_require_non_blank_name` | `field_validator("name")` | `"name must not be blank"` |
| `Provenance` | `_require_confidence_in_range` | `field_validator("confidence")` | `"confidence must be between 0.0 and 1.0"` |
| `Provenance` | `_require_timezone` | `field_validator("observed_at")` | `"observed_at must be timezone-aware"` |
| `Provenance` | `_reject_model_without_a_model_call` | `model_validator(mode="after")` | `"model must be None for extraction_method '<method>', which invokes no model"` |

**1. `name` must not be blank.** The check is `if not value.strip()`, so
`""`, `"   "`, `"\n"` and any other whitespace-only string are rejected. It
does not strip: `Entity(name="  Ada  ")` constructs and keeps both pairs of
spaces — normalization is the caller's job and lands in `normalized_name`,
which has **no** validator of its own. Neither do `entity_type` or
`original_entity_type`: a blank `entity_type` constructs, and
`blocking.entity_type_key` treats the resulting `"t:"` as the right answer.
See [Non-blank string fields](#non-blank-string-fields), which also records
what depends on this rule — `blocking.prefix_key` has no empty-name branch
precisely because a valid `Entity` cannot normalize to nothing.

**2. `confidence` must lie on `0.0..1.0` inclusive.** The check is
`if not 0.0 <= value <= 1.0`. Both endpoints are legal; `NaN` is rejected
because the comparison chain evaluates `False`. The field is required — there
is no default, so every `Entity` carries a number somebody chose. See
[Confidence and score fields](#confidence-and-score-fields-are-bounded-0010-inclusive).
The tests pin the boundary as `@example` values (`-1e-9`, `1.0 + 1e-9`, `1.5`,
`2.0`) alongside the property, because a sampler drawing floats reached the
far extremes readily and the immediate neighbourhood of `1.0` rarely — a
mutant widening the bound to `<= 2.0` survived the property test on its own.

**3. `model` must be `None` unless `extraction_method` is `LLM` or `HYBRID`.**
The permitted pair is a module-level `frozenset`,
`MODEL_BEARING_METHODS = {ExtractionMethod.LLM, ExtractionMethod.HYBRID}` in
`domain/provenance.py`,
and the validator fires when `model is not None` and the method is outside it.
The message names the offending method by its *value*, so passing a `model`
with `PATTERN` reads
`model must be None for extraction_method 'pattern', which invokes no model`.
The reason is definitional: `model` records which model ran, so a method that
runs none cannot carry one — see
[`Entity.provenance.model`](#fields-and-defaults) for the naming convention that field
expects.

`HYBRID` is on the permissive side because a hybrid extraction is
pattern-matching *plus* a model, and that is exactly the case where knowing
which model contributed matters. The rule constrains only the four methods
that cannot involve a model at all: `PATTERN`, `SCHEMA_ORG`, `OPEN_GRAPH` and
`MANUAL`.

Note the asymmetry, which is the part most likely to surprise: **omitting
`model` is always legal**, for every method including `LLM`. An `Entity` built
with `extraction_method=LLM` and `model=None` validates. The domain has no
way to distinguish "no model ran" from "the extractor did not record one", and
declines to guess.

`extraction/mapping.py` is stricter, and binds the *same* set under a local
name (`_MODEL_BEARING = MODEL_BEARING_METHODS`) rather than restating it. `map_extraction` raises `ValueError` in
*both* directions — a model-bearing method with `model=None` as well as a
model string on a method that invokes none — because an entity built there is
headed for a durable event log, and an unattributed row cannot be repaired
after the fact. That is a pipeline rule rather than a domain one; the two
copies are a knowingly duplicated fact, so that a missing provenance string
fails where the fix is obvious instead of passing `Entity` validation and
reaching the log. See [Events](events.md).

One more asymmetry runs the other way. Because a blank `name` raises,
`extraction/mapping.py` checks `name.strip()` itself before constructing and
drops the offending row into a counter rather than failing the whole document.
The domain rule is why that guard exists; the domain does not soften to
accommodate it.

### Derived: is_temporal

```python
@property
def is_temporal(self) -> bool:
    return self.temporal is not None and not self.temporal.is_empty
```

`Entity` declares one derived property, and it is the only member of the type
that is computed rather than stored. It is `True` exactly when a
`TemporalExtent` is attached **and** that extent carries at least one populated
field.

| `temporal` | `is_temporal` |
|---|---|
| `None` | `False` |
| `TemporalExtent()` — every field `None` | `False` |
| `TemporalExtent(start_date=...)`, or any other single field set | `True` |

So the two ways of saying "no temporal information" agree, which is the point
of the second conjunct: a caller that always attaches an extent and a caller
that attaches one only when it has something to say get the same answer.
`extraction/mapping.py` is the first kind of caller in spirit and the second in
fact — an entity whose `temporal_expression` did not parse is left with
`temporal=None` rather than an empty extent, and a test asserts
`is_temporal is False` for it.

**Any populated field is enough.** `TemporalExtent.is_empty` is
`all(value is None for value in (...))` over all seven fields — `start_date`,
`end_date`, `precision`, `uncertainty`, `original_text`, `sequence_position`
and `publication_date` — so an extent carrying nothing but an `original_text`,
or nothing but a `sequence_position`, is temporal. Note that
`sequence_position=0` is a populated field: the emptiness test is `is None`,
not falsiness, and `0` is a legal position (the validator rejects only
negatives).

`is_temporal` is not the same question as `has_range`, which asks specifically
whether *both* endpoints are present. An extent with a `start_date` and no
`end_date` is temporal and has no range; an extent that is empty is neither.

`is_temporal` is a property, not a field. It does not appear in
`Entity.model_fields`, is not serialized by `model_dump()`, and cannot be
passed to the constructor or set — it is recomputed from `temporal` on every
access, so mutating `entity.temporal` after construction changes the answer.

Finally, what it does *not* tell you: whether the entity participates in a
timeline. Temporal relationships are inferred on read from the extents on
entities rather than persisted as edges — see
[ADR 0005](../adr/0005-temporal-inference-on-read.md) and
[How to query a timeline](../how-to/query-a-timeline.md) — so
`entity.is_temporal` reports what extraction recorded on *this* entity, not
what a timeline query will place around it. `Relationship` has no counterpart
property for the same reason: it carries no `TemporalExtent` at all.

## Relationship

`redstring.domain.relationship` declares one name, `Relationship`, and it is
exported from `redstring` — see [ADR 0006](../adr/0006-the-public-surface-is-gated.md).

A `Relationship` is a **directed, typed edge between two entities**: eight
fields, two validators, no derived properties. It is a plain pydantic
`BaseModel` — not frozen, no `extra="forbid"` — so unknown keyword arguments
are ignored rather than rejected and fields are assignable after construction,
exactly as for `Entity`. Validation runs at construction only.

The type is deliberately smaller than `Entity`. It carries no `TemporalExtent`,
no `extraction_method`, no `model` and no `blocking_keys`; the reasons are
given under [Fields and defaults](#fields-and-defaults_1). What it does carry
it enforces strictly, and one of those rules — the self-loop rejection — is
depended on by three packages outside the domain, which makes it the most
load-bearing four lines in the module:

- `extraction/mapping.py` checks `start == end` on the *resolved* ids before
  constructing, counting the dropped edge in `self_loops` rather than failing
  the document. It has to check on ids rather than names, because two
  spellings of one name resolve to one entity.
- `consolidation/planning.py` drops an edge whose endpoints were both absorbed
  by the same merge, and `domain/consolidation.py` records that as
  `RelationshipRedirection(after=None)` — "the edge was dropped", not "nothing
  happened". See [`RelationshipRedirection`](events.md#relationshipredirection).
- `projections/graph.py` treats the same case as nothing to upsert, and says so
  in a comment naming this validator.

Each of those is a place where the domain rule is *anticipated* rather than
caught: the callers avoid constructing the forbidden object instead of
handling the `ValidationError`. Weakening the validator would leave three
guards protecting against nothing, and would let a self-loop reach the graph
store from any of them.

### Fields and defaults

| Field | Type | Default |
|---|---|---|
| `id` | `RelationshipId` | none — required |
| `tenant_id` | `TenantId` | none — required |
| `source_entity_id` | `EntityId` | none — required |
| `target_entity_id` | `EntityId` | none — required |
| `relationship_type` | `str` | none — required |
| `source_id` | `SourceId \| None` | `None` |
| `properties` | `dict[str, Any]` | `{}` |
| `confidence` | `float` | none — required |

Eight fields, six of them required. `Relationship` is a plain pydantic
`BaseModel` — not frozen, no `extra="forbid"` — so unknown keyword arguments
are ignored and fields are assignable after construction, exactly as for
`Entity`. Validation runs at construction only.

Notes on the ones whose type does not tell the whole story:

- **The edge is directed, and the direction is the field names.** There is no
  `directed` flag and no symmetric variant: `source_entity_id` →
  `target_entity_id` is the only reading, and an undirected relationship is
  modelled by the caller writing two `Relationship`s with distinct `id`s.
- **`relationship_type` is a free string, not an enum**, and — unlike
  `Entity.name` — has **no blank check**. `relationship_type=""` constructs.
  See [Non-blank string fields](#non-blank-string-fields) for why the rule
  covers only the two fields whose blankness is lossy. There is also no
  `original_relationship_type` counterpart to `Entity.original_entity_type`:
  what the source called the edge is not preserved separately.
- **`source_id` says which document stated the edge**, and is optional
  because it reaches the event log: an event written before the field existed
  replays without it, so `None` means "not recorded" rather than "no
  document". `extraction/mapping.py` fills it from the document being
  extracted. There is deliberately **no `source_text` beside it**, unlike
  `Entity`: `ExtractedRelationship` has no span field, so the model is never
  asked for one, and a value here could only be reconstructed or paraphrased.
  `DocumentExtracted` rejects an edge naming a *different* document, and
  accepts one naming none — see [Events](events.md).
- **`properties` defaults to an empty dict** written as a bare mutable
  literal. Pydantic deep-copies a default per instance, so two relationships
  constructed without it do not share a dict. Its values are `Any` and
  unvalidated here; a store that persists them imposes its own constraints.
- **`confidence` is required and bounded `0.0..1.0` inclusive**, by the same
  validator and the same message as `Entity.provenance.confidence` — see
  [Confidence and score fields](#confidence-and-score-fields-are-bounded-0010-inclusive).

Four fields `Entity` has are absent, and each absence is a decision rather
than an omission:

- **No `temporal`.** A `Relationship` carries no `TemporalExtent` and has no
  `is_temporal` property. Temporal edges are inferred on read from the extents
  on the *entities* — see
  [ADR 0005](../adr/0005-temporal-inference-on-read.md) and
  [How to query a timeline](../how-to/query-a-timeline.md) — so a field here
  would be a second, persisted home for the same information.
- **No `extraction_method` and no `model`.** *Which* document stated the edge
  is on the edge, as `source_id`; *how* it was found and by which model are
  carried by the event that recorded it rather than by the edge; see
  [Events](events.md).
- **No `blocking_keys`.** Blocking exists to find candidate duplicate
  entities; relationships are deduplicated by their endpoints, not by a key.

Every field round-trips: `Relationship.model_validate(rel.model_dump())`
reconstructs an equal object, `properties` included, because nothing on the
type is derived or computed.

## SourceDocument

`redstring.domain.source` declares one name, `SourceDocument`, and it is
exported from `redstring` — see [ADR 0006](../adr/0006-the-public-surface-is-gated.md).

A `SourceDocument` is **what a caller hands the library**: a piece of content
to build a graph from, plus whatever provenance the caller wants to carry
alongside it. Six fields, two validators, no derived properties, no methods.
It is a plain pydantic `BaseModel` — not frozen, no `extra="forbid"` — so
unknown keyword arguments are ignored rather than rejected and fields are
assignable after construction, exactly as for `Entity` and `Relationship`.
Validation runs at construction only.

The module docstring states the constraint that shapes the whole type: **the
library never fetches content**, so a `SourceDocument` is always supplied
rather than produced. Nothing here takes a URL and returns text. That is why
`id` is a free `str` the caller chooses rather than an allocated identifier
(see [Identifiers](#identifiers)), why `uri` is optional and unread, and why
`text` — the one field the library actually consumes — is the one field with a
content rule.

It is also the only type on this page with **no `tenant_id`**. A document is
an argument, not a stored record: the tenant is supplied beside it at the call
(`ExtractionPipeline.extract(document, tenant_id)`,
`build_graph(document, ..., tenant_id=...)`), so the same document can be fed
to two tenants without being rebuilt. See
[Fields and defaults](#fields-and-defaults_2) below.

### Fields and defaults

| Field | Type | Default |
|---|---|---|
| `id` | `SourceId` (`str`) | none — required |
| `text` | `str` | none — required |
| `uri` | `str \| None` | `None` |
| `title` | `str \| None` | `None` |
| `published_at` | `datetime \| None` | `None` |
| `metadata` | `dict[str, Any]` | `{}` |

Six fields, two of them required. `SourceDocument` is a plain pydantic
`BaseModel` — not frozen, no `extra="forbid"` — so unknown keyword arguments
are ignored and fields are assignable after construction, exactly as for
`Entity` and `Relationship`. Validation runs at construction only.

**There is no `tenant_id`.** It is the one required-looking field the type
does not have, and its absence is the difference between a `SourceDocument`
and everything else on this page: a document is an *argument*, not a stored
record. The tenant is supplied alongside it at the call —
`ExtractionPipeline.extract(document, tenant_id)` and `build_graph(...)` both
take the two separately — so the same document can be fed to two tenants
without being rebuilt. `extraction/mapping.py` is where the tenant is
stamped onto every entity and relationship produced, and its docstring notes
that being the only such place is what makes a mistake there uncatchable
downstream. See [Identifiers](#identifiers) for the pairing everywhere else.

Notes on the ones whose type does not tell the whole story:

- **`id` is a `SourceId`, so it is a free-form `str` with no validation** —
  a URL, a filename, a row key in the caller's own system. The library never
  fetches content, so it never gets to pick that name; it stores and echoes
  it back. An empty `id` constructs here and is caught later, by
  `events.streams.document_stream`, which raises a plain `ValueError` because
  hashing a blank id would yield one valid-looking stream shared by every
  document that had one. See [Events](events.md).
- **`text` is the content itself, and it must not be blank** — the only
  validated string on the type, sharing its rule and its spelling with
  `Entity.name`. See
  [Validation](#validation-text-must-not-be-blank-published_at-timezone-required)
  below and [Non-blank string fields](#non-blank-string-fields). The library
  reads it in exactly two places: `ExtractionPipeline.extract`, which hands
  it to the chunker, and `build_graph`, which passes it to
  `ContentClassifier` when the domain has to be inferred. There is no length
  ceiling and no encoding assumption.
- **`uri` and `title` are optional, unchecked, and unused by the library.**
  Nothing in `src/` reads either. They exist so a caller can carry the
  document's provenance alongside its content rather than in a parallel
  structure.
- **`published_at` is the vantage point for relative dates.** It is the
  field with a consequence beyond its own type: the pipeline passes it to
  `map_extraction` as `reference_date`, and the temporal parser reads no
  clock of its own, so `"last summer"` resolves against this value or not at
  all. `None` means "this document is undated", and expressions needing a
  vantage point are then dropped and counted rather than silently resolved
  against today. It is timezone-required when present — see
  [Every datetime field is timezone-required](#every-datetime-field-is-timezone-required);
  `reference_date` is the one exception, and may be `None` only for
  date-independent text.
- **`metadata` defaults to an empty dict** written as a bare mutable literal.
  Pydantic deep-copies a default per instance, so two documents constructed
  without it do not share a dict. Its values are `Any` and unvalidated, and —
  like `uri` and `title` — nothing in the library reads it. It does not reach
  an event payload, and it is not the same thing as `VectorRecord.metadata`,
  which is validated and must be JSON-storable; see
  Metadata must be JSON-storable.

Every field round-trips: `SourceDocument.model_validate(doc.model_dump())`
reconstructs an equal object, `metadata` included, because nothing on the type
is derived or computed. There are no properties and no methods.

`SourceDocument` is exported from `redstring` — it is the first name in the
package docstring's worked example, being what a caller puts in. See
[ADR 0006](../adr/0006-the-public-surface-is-gated.md).

### Validation: confidence range, self-loops rejected

`Relationship` declares exactly two validators. Both raise a plain
`ValueError`, which pydantic surfaces as a `ValidationError` at construction —
per the convention in
[Scope and how to read this page](#scope-and-how-to-read-this-page). Neither is
re-checked on assignment: the model is not frozen and `validate_assignment` is
not set, so `rel.target_entity_id = rel.source_entity_id` after construction
succeeds and produces a self-loop the type would have refused.

| Validator | Kind | Message |
|---|---|---|
| `_require_confidence_in_range` | `field_validator("confidence")` | `"confidence must be between 0.0 and 1.0"` |
| `_reject_self_loops` | `model_validator(mode="after")` | `"source_entity_id and target_entity_id must differ"` |

**1. `confidence` must lie on `0.0..1.0` inclusive.** The check is
`if not 0.0 <= value <= 1.0` — the same spelling and the same message as
`Entity.provenance.confidence`, in a separate copy on this type rather than in a shared
base. Both endpoints are legal; `NaN` is rejected because the comparison chain
evaluates `False`. The field is required, so every `Relationship` carries a
number somebody chose. See
[Confidence and score fields](#confidence-and-score-fields-are-bounded-0010-inclusive).

**2. The two endpoints must differ.** The check is
`self.source_entity_id == self.target_entity_id`, on a `model_validator`
because it spans two fields. It is an equality test on `UUID`s, not an identity
test, so two distinct `UUID` objects with the same value are correctly
rejected — the distinction matters because the ids these are compared against
have usually come back through a store or been rebuilt from a string.

There is no flag to relax the second rule and no `allow_self_loops` escape.
`A -> A` is not modelled here at all: an edge from a thing to itself carries no
information the entity does not already carry, and admitting one would put a
cycle of length 1 into every traversal in the library.

The self-loop rule is the most load-bearing four lines in the module, because
**three packages outside the domain anticipate it rather than handle it** —
each avoids constructing the forbidden object instead of catching the
`ValidationError`:

- `extraction/mapping.py` compares the *resolved* ids
  (`if start == end: self_loops += 1; continue`) and counts the dropped edge
  in `MappingCounts.self_loops` rather than failing the document. The comment
  there records why the check cannot be on names: two spellings of one name
  resolve to one entity, so `"Ada" -> "Ada Lovelace"` is a self-loop only after
  resolution. The count reaches the caller through
  `ExtractionPipeline`'s result, so a document that stated only self-referential
  edges is distinguishable from one that stated none.
- `consolidation/planning.py` drops an edge whose endpoints were both absorbed
  by the same merge — it has no post-redirection signature to group by — and
  `domain/consolidation.py` records that as `RelationshipRedirection(after=None)`,
  which means "the edge was dropped", not "nothing happened". See
  RelationshipRedirection and the module docstring
  in `consolidation/planning.py`, which argues why a dropped edge is a
  redirection rather than an omission.
- `projections/graph.py` treats the same case as nothing to upsert
  (`if source == target:`), and its docstring names this validator as the
  reason.

Weakening the validator would therefore not loosen anything: it would leave
three guards protecting against a rule that no longer exists, and let a
self-loop reach the graph store from any of them.

Two rules the type conspicuously does **not** enforce:

- **`relationship_type` may be blank.** `Relationship(relationship_type="")`
  constructs — see [Non-blank string fields](#non-blank-string-fields) for why
  the blank check covers only `Entity.name` and `SourceDocument.text`.
- **Neither endpoint is checked for existence, and neither is checked against
  `tenant_id`.** The domain has no store to ask, so an edge between two ids
  that were never written constructs happily; the `MissingEntityError` for a
  dangling endpoint comes from the write model, not from here. Nothing stops a
  caller pairing a `source_entity_id` from one tenant with a `target_entity_id`
  from another, either — the edge's own `tenant_id` is what the store keys on.
  See [Events](events.md).

### Validation: text must not be blank, published_at timezone-required

`SourceDocument` declares exactly two validators, both `field_validator`s,
both raising a plain `ValueError` that pydantic surfaces as a
`ValidationError` at construction — per the convention in
[Scope and how to read this page](#scope-and-how-to-read-this-page). Neither
is re-checked on assignment: the model is not frozen and `validate_assignment`
is not set, so `doc.text = "   "` after construction succeeds and leaves a
document the type would have refused.

| Validator | Field | Message |
|---|---|---|
| `_require_non_blank_text` | `text` | `"text must not be blank"` |
| `_require_timezone` | `published_at` | `"published_at must be timezone-aware"` |

**1. `text` must not be blank.** The check is `if not value.strip()`, the same
spelling as `Entity.name`'s, so `""`, `"   "`, `"\n"`, `"\t "` and any other
whitespace-only string are rejected. It does not strip — a document whose text
has leading or trailing whitespace constructs and keeps it exactly. See
[Non-blank string fields](#non-blank-string-fields), which lists the only two
fields in the package carrying this rule and names the fields that look like
they should have it and do not.

The rule is what removes an empty-document case from everything downstream.
`ExtractionPipeline.extract` says so in its own docstring — "`SourceDocument`
already refuses blank text, so there is no empty-document case to handle
here" — and hands `document.text` straight to the chunker. A blank document
would otherwise chunk to nothing, extract nothing, and produce a result
indistinguishable from a document the model genuinely found nothing in.

Note what the rule does *not* cover on this type. `id` is a `SourceId` and
accepts an empty string; that is caught later, by
`events.streams.document_stream`, and only because someone wrote the guard
there — see [Fields and defaults](#fields-and-defaults_2) and
[Events](events.md). `uri`, `title` and the values in `metadata` are unchecked.

**2. `published_at` must be timezone-aware when present.** The check is
`if value is not None and value.tzinfo is None`, so `None` is accepted — it is
not a naive datetime, it is the absence of one — and any aware value is
accepted whatever its offset. Nothing normalizes to UTC on the way in. See
[Every datetime field is timezone-required](#every-datetime-field-is-timezone-required)
for the rule and the comparison argument behind it.

On this field the rule earns its keep twice over, because `published_at` is
not merely stored: it is the vantage point every relative date in the document
is read against. `ExtractionPipeline.extract` passes it to `map_extraction` as
`reference_date`, and `parse_temporal` reads no clock of its own, so
`"last summer"` resolves against this value or not at all. Two consequences
follow from the validator:

- **`parse_temporal` re-checks the same condition and raises a plain
  `ValueError` for a naive `reference_date`**, because it is a function
  argument rather than a model field and can arrive from somewhere other than
  a `SourceDocument`. The two spellings agree deliberately; see
  ValueError for a naive reference_date.
- **A naive `published_at` would otherwise fail at parse time**, at a distance
  from the construction that allowed it, and once per expression rather than
  once per document. Rejecting at construction keeps the failure attached to
  the value that caused it.

`published_at=None` is legal and means "this document is undated". It is not
an error and does not fail extraction: an expression that needs a vantage
point is dropped and counted on `PipelineResult.undatable_relative` rather
than resolved against today, which would make a re-extraction of the same
document produce a different graph. `parse_temporal` raises
`AmbiguousReferenceDateError` — a `ValueError`, not a `RedstringError` — only
for text that has been *shown* to resolve differently against two vantage
points, so date-independent text still parses with no reference date at all.
See
reference_date is required and may be None only for date-independent text
and
AmbiguousReferenceDateError and the two-probe ambiguity check.

Both validators return the value unchanged, so neither participates in the
round trip beyond admitting it:
`SourceDocument.model_validate(doc.model_dump())` reconstructs an equal
object, aware `published_at` included.

## Alias

`redstring.domain.alias` declares one name, `Alias`, and it is exported from
`redstring` — see [ADR 0006](../adr/0006-the-public-surface-is-gated.md).

An `Alias` is **one entity having been merged into another**: the absorbed
entity's id, the canonical entity's id, when it happened, and — when they are
known — the absorbed entity's names. Eight fields, two validators, no derived
properties, no methods.

Unlike every other model on this page it sets `model_config =
ConfigDict(extra="forbid")`, so an unknown keyword argument is **rejected**
rather than ignored. That is a deliberate difference and the module docstring
gives the reason; see
[Validation](#validation-extraforbid-merged_at-timezone-required-self-merge-rejected).
It is not frozen and `validate_assignment` is not set, so fields remain
assignable after construction and neither validator re-runs on assignment.

An `Alias` records that a merge happened. It is **not** a record of what the
merge displaced: there is no `displaced` payload, because undo is a
compensating `MergeUndone` event carrying typed
`RelationshipRedirection`s and the log therefore
already holds the pre-merge state. A field here would be a second, unversioned
copy of it. See [Events](events.md).

Aliases are written by the projection, not by a caller: `projections/graph.py`
folds `EntitiesMerged` into one `upsert_alias` per absorbed entity. The port
keys the row on `(tenant_id, alias_entity_id)` — an entity has at most one
canonical parent — and neither endpoint has to exist, because an alias is a
statement about ids rather than about stored entities.

### Fields and defaults, including optional alias_name

| Field | Type | Default |
|---|---|---|
| `id` | `uuid.UUID` | none — required |
| `tenant_id` | `TenantId` | none — required |
| `canonical_entity_id` | `EntityId` | none — required |
| `alias_entity_id` | `EntityId` | none — required |
| `alias_name` | `str \| None` | `None` |
| `alias_normalized_name` | `str \| None` | `None` |
| `merged_at` | `datetime` | none — required |
| `merge_reason` | `str \| None` | `None` |

Eight fields, five required and three optional. Field order matters only for
positional-free construction, which pydantic does not offer — but note that
`merged_at` is declared *after* the two optional name fields and is still
required, so a caller relying on "optional fields come last" will be
surprised.

Notes on the ones whose type does not tell the whole story:

- **`id` is annotated as a bare `uuid.UUID`, not through an alias.** There is
  no `AliasId` in `redstring.domain.ids` — see
  [Identifiers](#identifiers). The domain accepts any `UUID`, but the value is
  not arbitrary in practice: `projections/graph.py` derives it with
  `uuid5(NAMESPACE_OID, f"redstring:alias:{tenant_id}:{alias_entity_id}")`,
  so replaying the same log produces the same alias rows. A `uuid4` there
  would make a replay disagree with the run it replays, which is what the
  replay-equivalence tests forbid. The merge event's id is deliberately *not*
  in the hash even though it is to hand: the row is keyed on
  `(tenant_id, alias_entity_id)` in every adapter — an entity has at most one
  canonical parent — so hashing anything else in would let one logical row
  carry two ids depending on which merge wrote it last.
- **The direction is the field names.** `alias_entity_id` was absorbed *into*
  `canonical_entity_id`. Nothing in the type marks which of the two still
  exists as an entity; that is the store's business, and neither id is
  checked for existence here.
- **`alias_name` and `alias_normalized_name` are optional, and their absence
  is `None` rather than `""`.** `alias_name` carries the longest field
  docstring in the package, and its argument is the reason the field is
  shaped this way: the projection folds `EntitiesMerged`, which carries ids
  and no names — a permanent event schema (ADR 0001) that will not gain
  them — so the fold looks the names up in the store with `get_entities`, and
  an entity whose extraction has not been folded yet simply has none to look
  up. In that case the fold writes `alias_name=None` and
  `alias_normalized_name=None` explicitly. A required field would have forced
  a fabricated one, and `str(entity_id)` sitting in a `name` field is worse
  than an honest absence: it reads as a name everywhere it is displayed.
- Neither name field has a blank check, so `alias_name=""` constructs — see
  [Non-blank string fields](#non-blank-string-fields). The two are set and
  cleared together by the fold, but **the type does not relate them**: an
  `Alias` with one and not the other constructs, and no validator compares
  `alias_normalized_name` against `normalize_name(alias_name)` — the same
  looseness `Entity.normalized_name` has.
- **`merged_at` is the only required `datetime` in the package**, and the only
  one with no `None` alternative, so every `Alias` in existence carries an
  aware timestamp. The projection supplies the merge event's `occurred_at`
  rather than a clock read at fold time, which is what keeps a replay
  producing equal rows — and it is why the checkpoint tests have a helper
  that strips `merged_at` before comparing dumps in the one place where a
  differing merge event is expected.
- **`merge_reason` is free text and unchecked.** The projection copies
  `EntitiesMerged.merge_reason` through unchanged; nothing in the library
  parses or branches on it.

Every field round-trips: `Alias.model_validate(alias.model_dump())`
reconstructs an equal object, because nothing on the type is derived or
computed — there are no properties and no methods. The Neo4j adapter
round-trips it through a different medium, and two details of that are worth
knowing here because both are consequences of fields on this table. `merged_at`
is stored as ISO text rather than a native Neo4j `DateTime`, because the
driver's conversion back is lossy for offsets Python spells differently and
the port compares `Alias`es for equality. And the three optional fields are
read back with `edge.get(...)` rather than `edge[...]`, because Neo4j drops a
property written as null — which is exactly what the fold writes when the
absorbed entity's names are not yet known.


### Validation: extra=forbid, merged_at timezone-required, self-merge rejected

`Alias` declares two validators and one model config setting. Both validators
raise a plain `ValueError`, which pydantic surfaces as a `ValidationError` at
construction — per the convention in
[Scope and how to read this page](#scope-and-how-to-read-this-page).

| Rule | Mechanism | Message |
|---|---|---|
| unknown keyword rejected | `ConfigDict(extra="forbid")` | pydantic's `extra_forbidden` |
| `merged_at` timezone-aware | `field_validator("merged_at")` | `"merged_at must be timezone-aware"` |
| endpoints differ | `model_validator(mode="after")` | `"canonical_entity_id and alias_entity_id must differ"` |

**1. `extra="forbid"`.** This is the only model on this page that sets it.
`Entity`, `Relationship` and `SourceDocument` all take pydantic's default,
which silently ignores an unknown keyword argument. The setting is here to
make a *removal* visible: `Alias.displaced` was a `dict[str, Any]` added when
undo was a storage problem, and once undo became a compensating event a call
site still passing `displaced=` would, under the default, keep constructing an
`Alias` that quietly lost the data. With `extra="forbid"` it is told instead.
A test pins this, and pins it by name — `_alias(displaced=...)` must raise —
so the config is not something a later edit can drop without a failure.

The scope is worth being precise about: it rejects keywords, not values.
`Alias(**row)` over a store row carrying one extra column raises, which is the
intended behaviour on this type and a real difference from the other three.

**2. `merged_at` must be timezone-aware.** The check is
`if value.tzinfo is None`, the same condition as everywhere else in the
package. Because the field is required and has no `None` branch, this is the
strictest instance of the rule — there is no "absent" case to fall through.
Any offset is accepted and nothing normalizes to UTC. See
[Every datetime field is timezone-required](#every-datetime-field-is-timezone-required)
for the comparison argument behind it.

**3. The two endpoints must differ.** The check is
`self.canonical_entity_id == self.alias_entity_id`, on a `model_validator`
because it spans two fields, and it is an equality test on `UUID`s rather than
an identity test — which matters because these ids have usually come back
through a store or been rebuilt from a string. The same shape as
`Relationship`'s self-loop rule, and for a related reason: an entity merged
into itself asserts nothing, and would make alias resolution a fixed point
that never terminates in the useful direction. `resolve_canonical` on
`GraphStore` refuses to merge *into* an alias, which is what stops longer
cycles and is reported as `AliasCycleError`; this validator is what stops the
degenerate one-element case at construction. `AliasCycleError` is the error
that reports the longer case; it is exported, so a caller can name it.

Three rules the type conspicuously does **not** enforce:

- **Neither endpoint is checked for existence.** The domain has no store to
  ask, and the port is explicit that an alias may name ids it does not hold:
  an alias is a statement about ids, and requiring the absorbed entity to
  exist would reinstate exactly the assumption aliases exist to remove.
- **`tenant_id` is not checked against either endpoint**, because there is
  nothing to check it against — an `EntityId` carries no tenant. The alias
  row's own `tenant_id` is half of the key the store uses; see
  [Identifiers](#identifiers).
- **No chain rule.** Nothing here prevents an `Alias` whose
  `canonical_entity_id` is itself some other alias's `alias_entity_id`.
  Resolution is transitive at the port, and cycle detection lives there.

## Temporal value types

`temporal.py`: one model and two enums, all three exported. `TemporalExtent`
is the type `Entity.temporal` holds, and it is the answer to "when did this
happen, and how confidently do we know".

**All seven fields are optional and default to `None`**, which is unusual
enough on this page to state first: an extent carrying nothing is legal, and
`is_empty` is how you ask. `Entity.temporal` is itself `None` when extraction
found no date, so a caller sees "no extent" and "an empty extent" as different
values — see [`is_empty`](#derived-is_empty-and-has_range) for why the second
exists at all.

### Fields and defaults

| Field | Type | Default | Notes |
|---|---|---|---|
| `start_date` | `datetime \| None` | `None` | timezone-required |
| `end_date` | `datetime \| None` | `None` | timezone-required; must be `>= start_date` |
| `precision` | `DatePrecision \| None` | `None` | how much of the date is meant |
| `uncertainty` | `UncertaintyMarker \| None` | `None` | how the source hedged |
| `original_text` | `str \| None` | `None` | the phrase this was read from |
| `sequence_position` | `int \| None` | `None` | must be `>= 0` |
| `publication_date` | `datetime \| None` | `None` | timezone-required |

`original_text` is the field to keep populated. It is what
`render_temporal` has to reconstruct when it goes the other way, and it is the
only record of what the model actually saw — a `TemporalExtent` for 1815 does
not say whether the document wrote "1815", "the mid-1810s" or "circa 1815",
and the last two are different claims that the `uncertainty` marker only
partly captures.

`sequence_position` is for narrative order where no date exists at all — "the
third thing that happens" — and is deliberately not a date. Nothing in this
package orders by it; it is carried for a caller who has a use for it.

`publication_date` is the vantage point a *relative* expression was read
against, not a property of the event. It is the same value
`parse_temporal(..., reference_date=...)` takes, kept on the extent so that
"last year" remains interpretable after the document that said it is out of
scope.

### DatePrecision members

`YEAR`, `MONTH`, `DAY`, `HOUR`, `MINUTE` — a `str` enum, so the value is the
lowercase name and it serialises as text.

**Precision is a claim about the source, not about the stored value.** A
`start_date` is always a full `datetime`; `precision = YEAR` means only the
year part was stated and the rest is padding. Two consequences worth knowing
before comparing extents:

- **A padded field is not a measurement.** `datetime(2023, 1, 1)` with
  `precision = YEAR` does not assert January, and code that compares months
  across two extents of differing precision is comparing one real number with
  one arbitrary one. `interval.py` exists for this: it widens an extent to the
  bounds its precision actually supports before relating two of them.
- **There is no `DECADE` or `CENTURY`**, though the parser reads both. "The
  1810s" and "the 19th century" become a *range* at `YEAR` precision rather
  than a coarser precision value, which keeps the enum a statement about
  calendar fields and puts the width in the dates where arithmetic can reach
  it.

### UncertaintyMarker members

`EXACT`, `APPROXIMATE`, `CIRCA`, `BEFORE`, `AFTER`, `INFERRED`.

These are **not ordered and not a confidence scale**, and nothing in the
package treats them as one. `BEFORE` and `AFTER` are directional claims about
a boundary rather than degrees of doubt, so any code sorting or thresholding
this enum has mistaken it for `Entity.provenance.confidence`, which is the bounded float
that does mean that (see
[Confidence and score fields](#confidence-and-score-fields-are-bounded-0010-inclusive)).

`INFERRED` is the one a caller sets rather than the parser: it marks an extent
worked out from context rather than read from the text.

### Validation: timezones, ordering, and a non-negative position

**1. Every datetime must be timezone-aware.** `start_date`, `end_date` and
`publication_date`, by the rule stated once under
[Every datetime field is timezone-required](#every-datetime-field-is-timezone-required).
Any offset is accepted and nothing normalizes to UTC.

**2. `end_date` must be `>= start_date`.** A `model_validator`, since it spans
two fields, and it is skipped when either is `None` — an extent with one
endpoint is legal and common. Note that **equality is permitted**: an extent
whose endpoints coincide is an instant, not an error.

Do not confuse that with what `interval.py` produces. `widen(moment,
precision)` returns the first instant *after* the unit containing `moment` —
half-open, deliberately, so that no comparison has to know whether the store
underneath counts in microseconds or nanoseconds. So a widened one-day extent
has endpoints one day apart rather than coinciding, and a coincident pair here
is a caller's instant rather than a parsed unit.

**3. `sequence_position` must be `>= 0`.** Position, not offset; a negative
one has no reading.

Two rules it does **not** enforce, both deliberate:

- **`precision` is not checked against the dates.** Nothing stops
  `precision = MINUTE` on a date whose time is midnight, because midnight is a
  real minute and the type cannot tell a padded field from a measured one.
  That is what `original_text` is for.
- **`uncertainty` is not checked against the range.** `BEFORE` with both
  endpoints set is not rejected; the marker describes the source's hedge and
  the dates describe the extent, and a caller combining them is doing
  interpretation the domain does not own.

### Derived: `is_empty` and `has_range`

`is_empty` is `True` when **all seven** fields are `None` — not when the dates
are. An extent carrying only `original_text` is not empty, which is the point:
it records that the document said something temporal that could not be parsed,
and discarding it would lose the only evidence that there was anything to
read.

`has_range` is `True` only when *both* `start_date` and `end_date` are set. A
one-sided extent is not a range, and code that treats a `None` endpoint as
"open" has to say which direction it means — `interval.py` does, and is where
that decision lives.

`Entity.is_temporal` composes with these: it is `True` when `temporal` is not
`None` **and** not empty, so an entity carrying an empty extent reads as
untemporal, which is the answer a caller wants.

## Vector types

`vector.py`: two models and two functions. `VectorRecord` and `VectorMatch`
are exported; `cosine_score` and `has_zero_norm` are reached by path.

**Two types, deliberately not one.** A `VectorRecord` is what a tenant *has*;
a `VectorMatch` is the answer to a question, and its score only means anything
relative to the query that produced it. Folding the score onto the record
would make "the score of this record" look like a stored property.

### VectorRecord — fields and defaults

| Field | Type | Default | Notes |
|---|---|---|---|
| `entity_id` | `EntityId` | — | required |
| `tenant_id` | `TenantId` | — | required |
| `vector` | `list[float]` | — | required; length is the store's business, not the type's |
| `metadata` | `dict[str, Any]` | `{}` | no NUL, no unpaired surrogate |

`vector` is a mutable `list` and `metadata` a mutable `dict` **on purpose**. A
store handing back its own object would let a caller corrupt stored state, and
the port requires that it does not — so the compliance suite mutates what a
read returned and asserts a later read is unaffected. Immutable containers
here would make that property unfalsifiable: it would pass against an adapter
that leaks, because there would be nothing to mutate.

Note what the type does *not* check: `len(vector)`. Dimension is a property of
the store, not of the record, and `DimensionMismatchError` comes from the port.

### VectorMatch — fields and defaults

| Field | Type | Default | Notes |
|---|---|---|---|
| `entity_id` | `EntityId` | — | required |
| `score` | `float` | — | bounded `0.0..1.0` inclusive |
| `metadata` | `dict[str, Any]` | `{}` | same rule as above |

There is no `tenant_id`: a search is already scoped to one, and repeating it
on every row would invite a caller to filter results by a tenant the query did
not use.

### The score scale, stated once and enforced by the bound

**`score` is cosine similarity mapped onto `0..1` by `(1 + cosine) / 2`**,
higher meaning more similar:

| cosine | meaning | score |
|---|---|---|
| `1.0` | identical direction | `1.0` |
| `0.0` | orthogonal | `0.5` |
| `-1.0` | opposite direction | `0.0` |

Note the sign. `(1 - cosine) / 2` is a *distance* on the same range and
differs on every input, and an earlier version of this page said exactly that
— a dead anchor that nobody could follow and therefore nobody re-checked. The
formula above is the one in `domain/vector.py`.

Pinning the scale in the domain type is what makes `min_score` portable
between adapters. "Score" is ambiguous across vector databases — several
report a distance, where lower is better — so an adapter that inverted the
sense would return plausible nonsense rather than an error. With the scale
fixed and the `0..1` bound on the model, an inversion becomes a validation
failure at the boundary instead of a silent quality regression. The mapping is
strictly monotone in cosine, so **ranking is unaffected by the choice**; what
it buys is that every adapter reports the same number for the same pair.

`0.0` is not a no-op `min_score`: it excludes exactly the antipodal vectors.

### `cosine_score` — clamped, and undefined at the origin

`cosine_score(left, right)` returns the transform above, clamped into
`0..1`, and raises `ValueError` if either vector has no direction.

The clamp is `clamp_score`, shared with every adapter that computes the
mapping in its backend rather than spelled once per caller — a private copy is
a branch no test can reach through its own caller, which is how two of them
came to be separately unenforced.

It is not defensive tidying. Accumulated rounding makes the dot product of a
float vector with *itself* exceed its squared norm by an ulp or two, so the
unclamped value for an identical pair can land marginally above `1.0` — which
the `le=1` bound would then reject.

**Measured, and kept despite the measurement.** Over roughly 2×10⁶ random
float64 vectors the unclamped mapping never exceeded `1.0` — the overshoot is
about one ulp of the ratio, and the `(1 + ratio) / 2` halves it into the ulp
below 1.0 where it rounds away — and pgvector 0.8.5 clamps its distance
operator internally. The guarantee is for the precisions and backends this
repository does not have yet: a store reporting a raw cosine hands
`VectorMatch` a value its bound rejects, turning a rounding artefact into a
hard `ValidationError` for the caller. The clamp can only pull an overshoot
back, so `cosine_score(v, v)` is `<= 1.0` and approximately `1.0`, **not
exactly** `1.0`; every score assertion in the compliance suite compares with a
tolerance for that reason.

Vectors of different lengths raise rather than being truncated to the shorter
one, which would produce a plausible score for two incomparable vectors.

### `has_zero_norm` — the norm, not the components

`has_zero_norm(vector)` is what the port's rejection is written in terms of,
and it asks whether the **norm** is zero *as float32* — not whether every
component is zero, which is a different question.

`[1e-30, 1e-30]` has two perfectly good float64 components and a non-zero
float64 norm, and each squares to `1e-60`, which is zero in float32. Since a
stored vector is float32 (pgvector's `vector` is float4, and so is most of the
managed competition), such a vector has no direction any backend can compute —
and the two adapters disagreed about what that meant until the guard asked the
right question. The threshold is float32 because that is the one every adapter
already imposes, not because a magnitude was chosen; no real embedding is
anywhere near it.

### Metadata must survive a JSON column

Both models reject metadata containing a **NUL** or an **unpaired surrogate**,
at any depth — inside a nested dict, a list, a tuple or a set, and in keys as
well as values.

Postgres `jsonb` cannot hold a NUL in text and refuses the write; an unpaired
surrogate is a legal Python `str` with no UTF-8 encoding at all, so it cannot
cross a connection that speaks one. A Python `dict` holds both quite happily,
which is the whole problem: without the check the in-memory adapter accepts
what the first persistent store refuses.

The rule lives in `domain/json_safety.py` and is shared with `Entity` and
`Relationship`. It **rejects rather than strips**, because silently altering
the value would make every round-trip contract in this repository a lie, and a
caller with unstorable text has a bug upstream that is better surfaced than
smoothed over.

## Name normalization

`normalization.py` holds one function.

`normalize_name(name)` **casefolds, strips, and collapses internal whitespace
runs to a single space.** It never raises, and it returns a `str` for any
`str`.

Hyphens and underscores are **left untouched**, and that is the entire design
decision. This is an *identity* concern: `normalize_name` feeds
`entity_id_for`, so two names it maps together become one entity. Collapsing
`"foo bar"` and `"foo-bar"` would silently merge two things a document
distinguished, and no later step could tell they had been merged.

The slug-producing normalizer in `extraction/domains/models.py` — which *does*
turn spaces and hyphens into underscores — answers a different question: what
is this type's identifier in a schema file. Do not reach for one where the
other belongs. Blocking is the third case again: `prefix_key` normalizes for
*grouping*, where being lossy is the point, and it calls this function rather
than a looser one because a key must not depend on which extractor wrote
`Entity.normalized_name`.

Note the asymmetry with `Entity.normalized_name`: the field is whatever the
extractor put there, and every key function re-normalizes rather than trusting
it.

## Blocking keys

`blocking.py`: three key functions, an enum naming them, and
`blocking_keys_for` to apply a set of them.

**Blocking decides which entities are worth comparing at all.** Comparing
every pair of a tenant's entities is quadratic and unaffordable past a few
thousand, so each entity carries a small set of deliberately lossy keys and
only entities sharing a key are ever scored against each other.

**Keys are computed here and stored on the entity.**
`GraphStore.find_by_blocking_key` looks them up and computes nothing, which is
what keeps this pure domain logic rather than one strategy per backend.

### The three key functions

| Function | Namespace | Returns |
|---|---|---|
| `prefix_key(entity)` | `p:` | first `PREFIX_LENGTH` (5) characters of the normalized name |
| `entity_type_key(entity)` | `t:` | the normalized entity type — **always** present |
| `soundex_key(entity)` | `s:` | phonetic code, or `None` |

`BlockingKeyStrategy` names the three (`PREFIX`, `ENTITY_TYPE`, `SOUNDEX`) and
`DEFAULT_STRATEGIES` is all of them, because they fail differently: a prefix
catches "Ada Lovelace" against "Ada Lovelace, Countess" and misses
"A. Lovelace"; soundex catches spelling variants and misses abbreviations; the
type key catches nothing on its own and is what stops an entity from being
unblockable.

There is no `TRIGRAM`. It was never a key function — approximate matching is
`VectorStore.search`, and an adapter with a native fuzzy index may serve it
faster, but nothing may depend on it having one.

### Namespacing is not decoration

`"person"` as an entity type and `"person"` as a five-character name prefix
are different claims. Un-namespaced, `"Personal Data"` would block with every
person in the tenant — a block big enough to undo the point of blocking. Hence
`p:`, `t:`, `s:`, and hence no key is ever just its namespace.

### Why a soundex key can be absent

`soundex_key` returns `None` for a name with no ASCII letters. This is a
correction rather than defensiveness: `jellyfish.soundex` refuses nothing and
produces nonsense instead — `"2024"` and `"2007"` both code `2000`, so every
year lands in one block. An oversized block puts the quadratic back, which is
the one failure blocking cannot survive. So the name is reduced to its ASCII
letters first, and a name with none gets no soundex key rather than a junk
one.

**Accents are folded, not discarded**, and the difference is the point of the
reduction. Dropping non-ASCII characters loses a *coded* letter whenever the
accent sits on a consonant: "Muñoz" becomes "muoz" and codes `M200` while
"Munoz" codes `M520`, so two spellings of one name could never share a block.
NFKD splits the character into base letter plus combining mark, the letter
survives, and only the mark is dropped. An accented *vowel* hides this
entirely, since soundex ignores vowels after the first letter — which is why
the test for it uses a consonant.

### `blocking_keys_for`

Returns a `frozenset`, matching `Entity.blocking_keys`: the keys are a set of
claims and their order means nothing. **Absent keys are dropped rather than
represented**, so the result can be empty — but only if `ENTITY_TYPE` was left
out of the strategies, since that one always produces a key.

## Similarity

`similarity.py`: two pure functions, two frozen models, and the function that
combines them. `FeatureWeights` and `SimilarityFeatures` are exported.

**Three signals, deliberately independent, because they fail in different
places.** Name similarity catches typos and inflections and is fooled by two
different people with the same name. Graph similarity catches the case the
others cannot — two records that barely look alike but sit in the same part of
the graph. The embedding signal is *not computed here*: it arrives from
`VectorStore.search`, already on the port's `0..1` scale.

Everything in the module is a function of its arguments — no store, no
provider, no I/O — which is what makes any of it testable in a scoring loop.

### `string_similarity(left, right)`

Jaro-Winkler over **normalized** names, on `0..1`. Casing and whitespace are
not differences, so `"Ada  LOVELACE"` and `"ada lovelace"` score `1.0`.

Symmetric, and exactly `1.0` when the normalized names are equal. Neither
property is free: some Jaro-Winkler implementations apply the prefix bonus to
whichever string is passed first, so both are pinned by test.

### `graph_similarity(left, right)`

Jaccard overlap of two entities' **neighbour sets**, on `0..1`. It takes the
neighbours rather than two entities and a store, which is what keeps it usable
inside a loop that has already fetched them.

**Two empty sets score `0.0`, not the conventional `1.0`.** Jaccard of two
empty sets is defined as 1 in most references and that convention is wrong
here: two freshly-extracted entities that nothing points at yet would score a
perfect graph match and drag a merge over the threshold on the strength of
knowing nothing about either. *No evidence is not perfect agreement.*

### `FeatureWeights` — fields and defaults

| Field | Type | Default | Bounds |
|---|---|---|---|
| `name` | `float` | `0.5` | `>= 0.0` |
| `embedding` | `float` | `0.3` | `>= 0.0` |
| `graph` | `float` | `0.2` | `>= 0.0` |

**Frozen**, because a weight vector mutated between two comparisons makes the
scores incomparable and nothing in a score would show it.

The values need not sum to anything. `combined_score` renormalizes over the
features actually present, so a caller with no embedding gets a
name-and-graph score on the same `0..1` scale rather than a smaller number —
otherwise "the embedding provider was down" and "these entities are unalike"
would produce the same number.

**All-zero weights are rejected at construction.** A scorer returning `0.0`
for everything looks exactly like a corpus with no duplicates in it, which is
a plausible answer and therefore not one anybody investigates.

### `SimilarityFeatures` — fields and defaults

| Field | Type | Default | Bounds |
|---|---|---|---|
| `name` | `float \| None` | `None` | `0.0..1.0` |
| `embedding` | `float \| None` | `None` | `0.0..1.0` |
| `graph` | `float \| None` | `None` | `0.0..1.0` |

Frozen, for the same reason.

**`None` and `0.0` are different and must stay so.** `None` drops the feature
out of the weighting; `0.0` is positive evidence that the entities disagree on
it. Collapsing them would let a missing embedding push a pair *below* the
merge threshold — a merge not happening because a provider was slow.

### `combined_score(features, weights=None)`

A weighted mean of whichever features are present, on `0..1`, clamped.

The clamp is not tidying: a weighted mean of values each within `1.0` can
still land a hair above it once the weights are renormalized, and that is the
same overshoot that broke a `le=1` bound downstream in slice 0.

**Returns `0.0` when nothing was computed at all.** Both alternatives are
worse: "perfectly alike" merges on no evidence, and raising turns a provider
outage into a crash in the middle of a consolidation run rather than a run
that merges nothing.

**A weight of zero is exactly equivalent to not supplying the feature**, and
that falls out of the arithmetic rather than being arranged — the term leaves
the numerator and the weight leaves the divisor together. An earlier version
filtered zero-weight features out explicitly, and a hand-applied mutant
removing that filter survived every test, which is what an equivalent branch
looks like from outside. The filter is gone and the property is pinned
directly.

## Temporal intervals

`interval.py`: `Bounds`, `TemporalRelation`, and three functions —
`bounds`, `relate`, `relate_bounds`. `Bounds` and `TemporalRelation` are
exported.

This is where two `TemporalExtent`s become comparable. The extent says what
the text stated; the interval says what that *denotes*, with the precision
rule applied, so that "2023" and "March 2023" can be related without either
pretending to a resolution it does not have.

### `Bounds`

A `NamedTuple` of `lower` and `upper`, representing the **half-open** interval
`[lower, upper)`. `None` is infinity outwards — `None` as a lower bound is
minus infinity, `None` as an upper bound is plus infinity.

Half-open is what lets adjacent units abut without overlapping: 2023 ends
exactly where 2024 begins, and neither contains that instant twice. It is also
why no comparison has to know whether the store underneath counts microseconds
or nanoseconds.

`INSTANT` is module-level rather than an attribute of `Bounds`, and the reason
is a real trap: an annotated name in a `NamedTuple` body becomes a *field*, so
`Bounds.INSTANT` would make every `Bounds(lower, upper)` call site a
`TypeError` waiting for the first branch that read it. It is **one
microsecond** — the width given to a moment whose extent states no precision.
Not a day: defaulting to a day would invent a claim the extent never made, and
let one exact timestamp swallow every other event that day.

### `TemporalRelation` members

`BEFORE`, `AFTER`, `DURING`, `CONTAINS`, `OVERLAPS`, `EQUALS`.

**Deliberately coarser than Allen's thirteen relations.** `meets`, `starts`,
`finishes` and their inverses turn on exact endpoint equality, and an endpoint
that came from widening a year is an artefact of the precision rule rather
than something the text asserted. Offering them would be offering a
distinction the data cannot support.

### `bounds(extent)`

Returns the interval, or `None` for an extent that denotes none — one holding
only a `sequence_position`, say. That is not an error: sequence position
orders events that have no dates at all, and no interval comparison applies.

**Only two uncertainty markers change the interval.** `BEFORE` and `AFTER`
open a bound. `EXACT`, `CIRCA`, `APPROXIMATE` and `INFERRED` all fall through
to the ordinary closed interval, and that is a decision rather than an
omission: "circa 1850" is a claim about *how confidently* 1850 is known, not
about which years it might have been. Widening it means inventing a margin — a
decade? a century? — and then every comparison rests on a number nobody chose.
The uncertainty stays on the extent for a caller that wants to weight it.

**An open marker discards the far endpoint.** "before 1900" names one instant,
so an extent carrying both a `BEFORE` marker and an `end_date` has said
something contradictory, and the marker wins. Reading it as
`(-inf, end_date)` would honour both and silently convert a contradiction into
a plausible interval. `parse_temporal` cannot construct one, so this is a
guard against hand-built extents, and it fails towards the smaller claim.

Note the asymmetry in where an open bound lands: `BEFORE 1900` stops where
1900 *begins*, and `AFTER 1900` starts once 1900 is *over*. Both exclude the
named unit, which is what the words mean.

### `relate(first, second)` and `relate_bounds(first, second)`

`relate` returns how `first` stands to `second`, or `None` if either extent
states no date. `relate_bounds` does the same for two intervals and is
**total** — always exactly one answer.

`relate_bounds` is public rather than an implementation detail, and the reason
is worth copying: the interval open at *both* ends is not reachable from any
`TemporalExtent`, since an extent with neither date has no bounds at all. It
is exactly the case where a `None`-handling mistake hides, so it needs to be
callable directly to be testable at all.

The order of the checks is the specification: `EQUALS` first, then disjointness
in each direction, then containment in each direction, and `OVERLAPS` as the
remainder. Containment is tested with helpers that read `None` as the right
infinity for the *position* it appears in — a `None` lower bound is minus
infinity and a `None` upper bound is plus infinity, so the two comparisons are
not the same function.

## Merge strategies

`merge_strategy.py`: one enum, one value object and three functions. Merging
"Ada Lovelace" into "Augusta Ada King" leaves one entity and several candidate
values for every property; `resolve` is that choice, made **per property**, so
a caller can keep the canonical description while unioning the external ids.

### `PropertyMergeStrategy` members, and which resolve

| Member | Status |
|---|---|
| `PREFER_CANONICAL` | implemented; the default |
| `UNION` | implemented |
| `PREFER_MERGED` | implemented |
| `MOST_RECENTLY_OBSERVED` | implemented |
| `DEEP_MERGE` | raises `NotImplementedError` |

`IMPLEMENTED` is the frozenset of the first four, so a caller can ask before
committing to a strategy.

**`DEEP_MERGE` raises, and does not fall back to the default.** That is the
point rather than an omission: a silent fallback writes the canonical value
while the caller believes it asked for a deep merge, which corrupts data while
looking like it worked and leaves nothing in the result to show it happened. It
stays deferred on its own merits — the pre-merge shape is not recoverable from
a deep merge's result, so a wrong one is hard to undo.

`UNION` is structural rather than a preference. Merging *inherently* produces
alias sets — the whole point is that several names denote one thing — so a
strategy that accumulates instead of picking is not optional equipment.

**`MOST_RECENTLY_OBSERVED` was called `LATEST` and raised**, on the argument
that timestamps were per entity rather than per property. That argument was
wrong twice: there were no per-entity timestamps either, and per-property ones
were never the obstacle. The obstacle was `resolve`'s signature, which took
bare values and so dropped everything a strategy might need beyond the value
itself. Taking claims fixed it, and `PREFER_MERGED` came along for free — it
was never hard, only ill-defined about *which* absorbed entity when there are
several.

The rename narrows a promise rather than tidying a name. Nothing here tracks
a property's edit history, so "latest" would have invited a caller to assume a
modification time the library does not have. What it can answer is which of the
entities asserting this property was *observed* most recently.

### `PropertyClaim` and `claims_for`

A `PropertyClaim` is one entity's value for one property, with the observation
behind it: `value`, `provenance` (a `Provenance`) and `origin` (the asserting
`EntityId`).

`claims_for(property_name, canonical, others)` builds them, canonical first.
An entity whose `properties` lack the key is **skipped**, not given a `None`
claim: silence is not an assertion, and treating it as one would let an entity
with no opinion outvote one with an opinion under `MOST_RECENTLY_OBSERVED`
merely by being newer. An explicit `None` *is* a claim and is kept. It returns
`[]` when nobody claims the property, which the caller distinguishes from
"everybody claimed `None`".

### `resolve(strategy, claims)`

`claims` is non-empty — `resolve` raises `ValueError` on an empty sequence
rather than inventing an answer — and `claims[0]` is the canonical entity's,
the rest the absorbed entities' in the order the merge listed them. Positional
rather than a `canonical=`/`others=` pair because every strategy but
`PREFER_CANONICAL` treats them as one ordered sequence. A claim's value **may
be `None`, which is a value rather than an absence** — `PREFER_CANONICAL`
keeps it.

`PREFER_MERGED` returns `claims[1].value`, or `claims[0].value` when nothing
was absorbed.

`MOST_RECENTLY_OBSERVED` returns the value of the claim greatest under
`(observed_at, confidence, str(origin))`. Recency is the strategy's content;
confidence breaks the tie between two observations sharing an instant exactly,
which happens whenever a batch is extracted together; and `str(origin)` carries
no meaning at all, existing solely so no two distinct claims compare equal. The
moment two do, the winner is whichever the caller happened to list first, in a
durable replayable log — see
[`0010` one total order for preference](../adr/0010-one-total-order-for-preference.md).
Totality is asserted as a property test rather than argued in a comment.

`UNION` returns a **list**, canonical first, in first-seen order, flattening
one level. Three properties of that, each load-bearing:

- **A list rather than a set**, because these values reach an event payload
  where a set has no JSON form and no stable ordering to compare replays
  against.
- **`==` rather than hashing**, because the values are frequently unhashable —
  a list of external ids, a dict of properties — so a set would raise on
  exactly the nested values `UNION` exists to accumulate. That makes it
  O(n²) in the number of entities in one merge, which is single digits.
- **Flattens one level**, so applying `UNION` twice does not produce
  `[[a, b], c]`. Idempotence matters because a projection replays.

## RelationshipRedirection

`consolidation.py` holds one model: an edge, `before` and `after` a merge
moved or dropped it. `after` is `None` when the merge *dropped* the edge —
which happens when redirecting both endpoints onto the canonical entity would
have made it a self-loop.

| Field | Type | Default |
|---|---|---|
| `before` | `Relationship` | — |
| `after` | `Relationship \| None` | `None` |

### Validation: `after` must be the same edge, moved

When `after` is present, its `id` and its `tenant_id` must equal `before`'s.

The reason is about **undo**, not about tidiness. A redirection is applied by
upserting `after` over the id it shares with `before`. If the ids could
differ, applying it would create a *second* edge and leave the original in
place — and undoing it by upserting `before` would not remove that second
edge, so the undo would silently be a no-op on half the change. The
`tenant_id` check is the same argument where the leak crosses a tenant
boundary.

## Temporal parsing

`temporal_parsing.py`: `parse_temporal`, `render_temporal`, `widen`, and
`AmbiguousReferenceDateError`. This is the only module here that reads free
text, and the only one whose answer depends on *when the text was written*.

### `parse_temporal(text, *, reference_date)`

Returns a `TemporalExtent`, or **`None` if the text states no date**. `None`
is a normal outcome, not a failure: most entity mentions are not dated.

`reference_date` is the vantage point relative expressions resolve against —
`SourceDocument.published_at`, typically — and must be timezone-aware; a naive
one raises `ValueError`.

Text is rejected without parsing if it is blank or longer than
`MAX_INPUT_LENGTH` (500 characters). A temporal expression is short; the bound
is there so a paragraph handed to it by mistake does not become a
`dateparser` workload.

### `reference_date=None` is checked, not assumed

`None` is permitted and means *"this text had better not need one"* — and that
claim is **tested rather than trusted**. The text is parsed twice, against two
probe dates far apart (1999 and 2035), and if the two answers differ it raises
`AmbiguousReferenceDateError`.

This is the design decision worth carrying: the failure mode it prevents is
that "last year" silently becomes a date relative to *now*, so re-extracting
the same document next year produces a different graph — a corruption with no
symptom at the time it happens. The error is raised only after the ambiguity
has been *demonstrated* on that specific text, so it never fires speculatively
on text that happens to contain a relative-looking word.

### `render_temporal(extent)`

The inverse: `extent` as text this module parses back to the same extent, or
`None`.

It exists for the **round-trip property**, which is the only test shape that
can catch a strategy quietly mangling a form an earlier strategy already
handled. That is why it returns `None` for anything it cannot render
*faithfully* — an approximate rendering would make the property pass by
lowering the bar it was written to hold.

So the set of extents it can render is deliberately narrow: it needs a
`start_date` and a `precision`, refuses anything carrying a
`sequence_position` or a `publication_date`, refuses a start that is not
exactly on the boundary of its stated precision, and refuses month and day
*ranges* entirely, because the range patterns it would have to parse back
cannot express a cross-year span.

### `widen(moment, precision)`

**The first instant *after* the unit of `precision` containing `moment`** —
half-open, deliberately. The alternative, the last representable instant
inside the unit, forces every comparison to know the resolution of the
underlying store, and `datetime`'s microseconds, Neo4j's nanoseconds and
Postgres's microseconds do not agree on what that is.

It lives here rather than in `interval.py` because it is the exact inverse of
what the parsers do when they floor a partial date, and the two have to stay
each other's inverse.

### `AmbiguousReferenceDateError`

A `ValueError` subclass carrying the offending `text`. Its message names the
consequence rather than the rule — that parsing without a reference date would
make a re-extraction produce a different graph — and says what to pass instead.

## Error types

`exceptions.py`. **`RedstringError` is the base of every error this library
raises deliberately**, so `except RedstringError` is a complete catch for
anything the library means to tell you.

`AmbiguousReferenceDateError` is the one exception to that shape: it subclasses
`ValueError` rather than `RedstringError`, because it is raised by a pure
parsing function about its own argument.

`ReplayFailedError` used to be in the table below. It is `eventsource`'s since
0.12.0, rooted in its `ProjectionError` — the right root, because what
happened is that a projection failed to process an event. Catch it from there;
`except RedstringError` does not cover a strict rebuild's stop.

| Error | Raised when |
|---|---|
| `MissingEntityError` | writing a relationship whose endpoint the tenant does not have |
| `DimensionMismatchError` | a vector's length is not the store's `dimension` |
| `AliasCycleError` | resolving an entity through its aliases did not terminate |
| `UnknownDomainError` | no bundled or registered domain schema by that id |
| `LlmProviderError` | base for the three provider failures below |
| `EmptyCompletionError` | the model returned no usable content |
| `RefusedCompletionError` | the model declined, and its safety layer said so |
| `MalformedCompletionError` | content came back and did not validate against the schema |
| `EmbeddingProviderError` | an `EmbeddingProvider` could not produce usable vectors |
| `ConsolidationInvariantError` | base for the merge-log invariants below |
| `MergeIntoAliasError` | the merge target is itself already an alias |
| `DoubleMergeError` | an entity in this merge has already been merged elsewhere |
| `UnknownMergeError` | no merge in effect matches the event id an undo names |

Two groupings are load-bearing rather than tidy. **`RefusedCompletionError`
must be distinguishable from `EmptyCompletionError`** — a refusal is a
decision about the content and a retry will produce it again, while an empty
completion is usually worth retrying — which is why all three are exported
rather than collapsed into `LlmProviderError`. And **`UnknownMergeError`
covers both "never happened" and "already undone"**, deliberately: from a
caller's point of view there is no merge to reverse either way, and
distinguishing them in the type would invite handling only one.
