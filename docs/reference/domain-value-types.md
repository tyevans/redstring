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
either in the module docstring (indexed under
[Where the reasoning lives](#where-the-reasoning-lives)) or in an ADR:
[ADR 0005](../adr/0005-temporal-inference-on-read.md) for why temporal edges
are inferred on read rather than persisted, and
[ADR 0010](../adr/0010-one-total-order-for-preference.md) for the single total
order every tie-break in the library defers to.

Not every type named here is importable from `redstring`. The public surface
is `redstring.__all__` and nothing else — see
[What is public](#what-is-public) and
[ADR 0006](../adr/0006-the-public-surface-is-gated.md). Types that reach you
inside an event rather than by import are described in
[Events](events.md).

## Scope and how to read this page

Everything documented here lives in `src/redstring/domain/`, one section per
concern, in roughly the order the modules build on each other:

| Section | Module |
|---|---|
| Identifiers | `ids.py` |
| Entity | `entity.py` |
| Relationship | `relationship.py` |
| SourceDocument | `source.py` |
| Alias, RelationshipRedirection | `alias.py`, `consolidation.py` |
| Temporal value types | `temporal.py` |
| Temporal parsing | `temporal_parsing.py` |
| Temporal intervals | `interval.py` |
| Merge strategies | `merge_strategy.py` |
| Similarity | `similarity.py` |
| Vector types | `vector.py` |
| Blocking keys | `blocking.py` |
| Name normalization | `normalization.py` |
| Error types | `exceptions.py` |

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

`redstring.domain.ids` declares four names. All four are plain type aliases —
no wrapper classes, no `NewType`, no validation of their own:

```python
EntityId = UUID
RelationshipId = UUID
TenantId = UUID
SourceId = str
```

### EntityId, RelationshipId, TenantId (UUID) and SourceId (str)

| Alias | Underlying type | Appears on |
|---|---|---|
| `EntityId` | `uuid.UUID` | `Entity.id`, `Relationship.source_entity_id` / `target_entity_id`, `Alias.canonical_entity_id` / `alias_entity_id`, `VectorRecord.entity_id`, `VectorMatch.entity_id` |
| `RelationshipId` | `uuid.UUID` | `Relationship.id` |
| `TenantId` | `uuid.UUID` | `Entity.tenant_id`, `Relationship.tenant_id`, `Alias.tenant_id`, `VectorRecord.tenant_id` |
| `SourceId` | `str` | `SourceDocument.id`, `Entity.source_id` (optional) |

There is no `AliasId`: `Alias.id` is annotated as a bare `uuid.UUID`.

Because they are aliases rather than distinct types, they are
interchangeable at runtime and to a type checker: `EntityId` *is* `UUID`, so
passing a `TenantId` where an `EntityId` is expected is not a type error.
Nothing in the library guards against it. The distinction the aliases carry
is documentary — they say which of the four roles a `UUID` is playing at a
given position, and they give the store ports a vocabulary to be written in.

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
[What is public](#what-is-public) and
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
`end_date >= start_date` here, interval bounds in
[Temporal intervals](#temporal-intervals), timeline ordering in
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
  `pydantic.ValidationError`; see
  [ValueError for a naive reference_date](#valueerror-for-a-naive-reference_date).
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

`Entity.confidence` and `Relationship.confidence` have no default. A
confidence of `1.0` is something a caller asserts, not something the type
assumes on their behalf, so every `Entity` and `Relationship` in existence
carries a number somebody chose. The `SimilarityFeatures` fields default to
`None`, which means "not computed" and is distinct from a computed `0.0` —
see [SimilarityFeatures: None vs 0.0](#similarityfeatures-none-vs-00).

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
and "unalike". The mapping that puts it there is described under
[VectorMatch.score](#vectormatchscore-is-1--cosine--2-on-01-not-a-distance).

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
own function and its own field — see
[normalize_name](#normalize_name-casefold-strip-collapse-whitespace-hyphens-preserved-never-raises)
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
- `Entity.description`, `Entity.source_text`, `SourceDocument.uri`,
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
| `source_id` | `SourceId \| None` | `None` |
| `source_text` | `str \| None` | `None` |
| `external_ids` | `dict[str, str]` | `{}` |
| `properties` | `dict[str, Any]` | `{}` |
| `extraction_method` | `ExtractionMethod` | none — required |
| `model` | `str \| None` | `None` |
| `confidence` | `float` | none — required |
| `temporal` | `TemporalExtent \| None` | `None` |
| `blocking_keys` | `frozenset[str] \| None` | `None` |

Notes on the ones whose type does not tell the whole story:

- **`normalized_name` is supplied, not derived.** Nothing in `Entity`
  populates it from `name`, and no validator relates the two: an entity whose
  `normalized_name` disagrees with `normalize_name(name)` constructs happily.
  Callers pass the result of
  [`normalize_name`](#normalize_name-casefold-strip-collapse-whitespace-hyphens-preserved-never-raises).
- **`entity_type` is a free string, not an enum.** `entity_type="plot_point"`
  is legal. `original_entity_type` is where the source's own label is kept
  when extraction mapped it onto something else.
- **`external_ids` and `properties` default to empty dicts** written as bare
  mutable literals. Pydantic deep-copies a default per instance, so two
  entities constructed without them do not share a dict.
- **`blocking_keys` is `frozenset[str] | None`, and `None` is not the same as
  `frozenset()`** — it means the keys were never computed, where the empty set
  would mean they were computed and came out empty. The entity *carries* the
  keys; the store groups by them and computes nothing. See
  [`blocking_keys_for`](#blocking_keys_for-frozenset-result-when-it-can-be-empty).
- **`model` names which model produced the entity**, by convention
  provider-qualified and versioned (`"ollama/qwen3.6-27b-mtp"`,
  `"anthropic/claude-opus-4-20250514"`) and never a bare family name like
  `"claude"`. The field's `description` says so, and a test asserts the
  description still says so. The reason is durability: these values land in an
  event log, where an unversioned name makes "re-extract everything the old
  model touched" unanswerable. `None` means no model was involved *or* that
  the extractor did not record one — the two are not distinguished.

### ExtractionMethod members

`ExtractionMethod` is declared `class ExtractionMethod(str, Enum)`, so every
member *is* a `str`: `ExtractionMethod.LLM == "llm"` is `True`, the member can
be used anywhere a string is expected, and pydantic serializes it as its
value. It is exported from `redstring` alongside `Entity` — see
[What is public](#what-is-public) and
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
[`Entity.model`](#fields-and-defaults), which is versioned and survives model
upgrades. These values become persisted event payloads (see
[Events](events.md)), so a vendor name here would outlive that vendor's
presence in the codebase, and every historical row would still carry it. Two
tests pin this — one asserting each member's value, one asserting the *whole*
value set — so adding a member is a visible decision rather than an edit
nothing notices. The same argument, cited by name, is what removed vendor
branches from `llm/rate_limiter.py` and `llm/circuit_breaker.py`.

The values are the string form, not the member name: `"schema_org"` and
`"open_graph"` are lowercase with an underscore. That spelling is the
persisted one. `graph/adapters/neo4j.py` writes `entity.extraction_method.value`
to the node and rebuilds it with `ExtractionMethod(node["extraction_method"])`,
so renaming a *value* would orphan every entity already stored, while renaming
a *member* would not. Round-tripping an `Entity` through `model_dump()` and
`model_validate()` goes through the same values.

There is no `UNKNOWN` member and no default: `Entity.extraction_method` is
required, so every entity in existence states how it was derived. Absence of
provenance is expressed on `model` (which is `None` when no model ran *or*
when the extractor did not record one), never on the method.

### Validation: non-blank name, confidence range, model only for LLM and HYBRID

`Entity` declares exactly three validators. Each raises a plain `ValueError`,
which pydantic surfaces as a `ValidationError` at construction — per the
convention in
[Scope and how to read this page](#scope-and-how-to-read-this-page). Nothing
here is re-checked on assignment: the model is not frozen and
`validate_assignment` is not set, so `entity.confidence = 5.0` after
construction succeeds.

| Validator | Kind | Message |
|---|---|---|
| `_require_non_blank_name` | `field_validator("name")` | `"name must not be blank"` |
| `_require_confidence_in_range` | `field_validator("confidence")` | `"confidence must be between 0.0 and 1.0"` |
| `_reject_model_without_a_model_call` | `model_validator(mode="after")` | `"model must be None for extraction_method '<method>', which invokes no model"` |

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
[Confidence and score fields](#confidence-and-score-fields-are-bounded-0001-inclusive).
The tests pin the boundary as `@example` values (`-1e-9`, `1.0 + 1e-9`, `1.5`,
`2.0`) alongside the property, because a sampler drawing floats reached the
far extremes readily and the immediate neighbourhood of `1.0` rarely — a
mutant widening the bound to `<= 2.0` survived the property test on its own.

**3. `model` must be `None` unless `extraction_method` is `LLM` or `HYBRID`.**
The permitted pair is a module-level `frozenset`,
`_MODEL_BEARING_METHODS = {ExtractionMethod.LLM, ExtractionMethod.HYBRID}`,
and the validator fires when `model is not None` and the method is outside it.
The message names the offending method by its *value*, so passing a `model`
with `PATTERN` reads
`model must be None for extraction_method 'pattern', which invokes no model`.
The reason is definitional: `model` records which model ran, so a method that
runs none cannot carry one — see
[`Entity.model`](#fields-and-defaults) for the naming convention that field
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

`extraction/mapping.py` is stricter, and deliberately keeps its own copy of
the same set (`_MODEL_BEARING`). `map_extraction` raises `ValueError` in
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
See [Temporal value types](#temporal-value-types).

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
exported from `redstring` — see [What is public](#what-is-public) and
[ADR 0006](../adr/0006-the-public-surface-is-gated.md).

A `Relationship` is a **directed, typed edge between two entities**: seven
fields, two validators, no derived properties. It is a plain pydantic
`BaseModel` — not frozen, no `extra="forbid"` — so unknown keyword arguments
are ignored rather than rejected and fields are assignable after construction,
exactly as for `Entity`. Validation runs at construction only.

The type is deliberately smaller than `Entity`. It carries no `TemporalExtent`,
no `extraction_method`, no `model` and no `blocking_keys`; the reasons are
given under [Fields and defaults](#fields-and-defaults-1). What it does carry
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
  happened". See
  [RelationshipRedirection](#relationshipredirection).
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
| `properties` | `dict[str, Any]` | `{}` |
| `confidence` | `float` | none — required |

Seven fields, six of them required. `Relationship` is a plain pydantic
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
- **`properties` defaults to an empty dict** written as a bare mutable
  literal. Pydantic deep-copies a default per instance, so two relationships
  constructed without it do not share a dict. Its values are `Any` and
  unvalidated here; a store that persists them imposes its own constraints.
- **`confidence` is required and bounded `0.0..1.0` inclusive**, by the same
  validator and the same message as `Entity.confidence` — see
  [Confidence and score fields](#confidence-and-score-fields-are-bounded-0001-inclusive).

Four fields `Entity` has are absent, and each absence is a decision rather
than an omission:

- **No `temporal`.** A `Relationship` carries no `TemporalExtent` and has no
  `is_temporal` property. Temporal edges are inferred on read from the extents
  on the *entities* — see
  [ADR 0005](../adr/0005-temporal-inference-on-read.md) and
  [How to query a timeline](../how-to/query-a-timeline.md) — so a field here
  would be a second, persisted home for the same information.
- **No `extraction_method` and no `model`.** Provenance for an edge is
  carried by the event that recorded it rather than by the edge; see
  [Events](events.md).
- **No `blocking_keys`.** Blocking exists to find candidate duplicate
  entities; relationships are deduplicated by their endpoints, not by a key.

Every field round-trips: `Relationship.model_validate(rel.model_dump())`
reconstructs an equal object, `properties` included, because nothing on the
type is derived or computed.

## SourceDocument

`redstring.domain.source` declares one name, `SourceDocument`, and it is
exported from `redstring` — see [What is public](#what-is-public) and
[ADR 0006](../adr/0006-the-public-surface-is-gated.md).

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
[Fields and defaults](#fields-and-defaults-2) below.

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
  [Every datetime field is timezone-required](#every-datetime-field-is-timezone-required)
  and
  [reference_date is required and may be None only for date-independent text](#reference_date-is-required-and-may-be-none-only-for-date-independent-text).
- **`metadata` defaults to an empty dict** written as a bare mutable literal.
  Pydantic deep-copies a default per instance, so two documents constructed
  without it do not share a dict. Its values are `Any` and unvalidated, and —
  like `uri` and `title` — nothing in the library reads it. It does not reach
  an event payload, and it is not the same thing as `VectorRecord.metadata`,
  which is validated and must be JSON-storable; see
  [Metadata must be JSON-storable](#metadata-must-be-json-storable-nul-characters-rejected).

Every field round-trips: `SourceDocument.model_validate(doc.model_dump())`
reconstructs an equal object, `metadata` included, because nothing on the type
is derived or computed. There are no properties and no methods.

`SourceDocument` is exported from `redstring` — it is the first name in the
package docstring's worked example, being what a caller puts in. See
[What is public](#what-is-public) and
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
`Entity.confidence`, in a separate copy on this type rather than in a shared
base. Both endpoints are legal; `NaN` is rejected because the comparison chain
evaluates `False`. The field is required, so every `Relationship` carries a
number somebody chose. See
[Confidence and score fields](#confidence-and-score-fields-are-bounded-0001-inclusive).

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
  [RelationshipRedirection](#relationshipredirection) and the module docstring
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
there — see [Fields and defaults](#fields-and-defaults-2) and
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
  [ValueError for a naive reference_date](#valueerror-for-a-naive-reference_date).
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
[reference_date is required and may be None only for date-independent text](#reference_date-is-required-and-may-be-none-only-for-date-independent-text)
and
[AmbiguousReferenceDateError and the two-probe ambiguity check](#ambiguousreferencedateerror-and-the-two-probe-ambiguity-check).

Both validators return the value unchanged, so neither participates in the
round trip beyond admitting it:
`SourceDocument.model_validate(doc.model_dump())` reconstructs an equal
object, aware `published_at` included.

## Alias

`redstring.domain.alias` declares one name, `Alias`, and it is exported from
`redstring` — see [What is public](#what-is-public) and
[ADR 0006](../adr/0006-the-public-surface-is-gated.md).

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
[`RelationshipRedirection`](#relationshipredirection)s and the log therefore
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
degenerate one-element case at construction. See
[AliasCycleError](#aliascycleerror-entity_id-tenant_id).

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
