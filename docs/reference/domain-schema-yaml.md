# Domain schema YAML reference

A *domain schema* is a YAML file describing the entity types, relationship types
and prompt template that redstring uses when extracting a knowledge graph from
one kind of content. Six are bundled with the package, under
`src/redstring/extraction/domains/schemas/`; you can also load your own from
any directory.

Every file is parsed with `yaml.safe_load` and then validated against the
`DomainSchema` pydantic model in
`src/redstring/extraction/domains/models.py`. That model is the authority for
everything on this page: field names, defaults, bounds, and the rules that
reject a file. Each model in the hierarchy sets `extra="forbid"` and
`str_strip_whitespace=True`, so an unrecognised key is an error and surrounding
whitespace never reaches the graph.

The hierarchy is:

```
DomainSchema
├── EntityTypeSchema (list, ≥1)
│   └── PropertySchema (list)
├── RelationshipTypeSchema (list, ≥1)
├── ConfidenceThresholds
└── extraction_prompt_template
```

A schema **prompts the extractor; it does not constrain it.** Entity and
relationship types the LLM returns outside the declared set are not discarded —
`custom` is always an acceptable entity type and `related_to` is always an
acceptable relationship type. See
[ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md) for the
reasoning, and note the consequence throughout this reference: most fields here
shape what the model is asked for, and only a few are enforced at load time.

This page describes what each field means and what will be rejected. For a
step-by-step walk through writing a new schema, see
[Author a domain schema](../how-to/author-a-domain-schema.md). For where
domains fit in the extraction call, see the
[README](../../README.md).

## Scope and audience

This is a reference, not a walkthrough. It is written for someone who is
authoring or reviewing a domain schema YAML file, or debugging a
`SchemaLoadError`, and wants a per-field answer without reading
`models.py`. It assumes you already know what you want the schema to say and
covers what the file format allows you to say it with.

In scope:

- every field of `DomainSchema`, `EntityTypeSchema`, `PropertySchema`,
  `RelationshipTypeSchema` and `ConfidenceThresholds` — type, requiredness,
  default, and bounds
- the rules that make a file fail to load, and the shape of the resulting
  errors
- the identifier normalization that rewrites what you wrote before the schema
  exposes it
- conventions the six bundled schemas follow that the model does *not*
  enforce, and where those are checked instead
  (`tests/unit/extraction/domains/test_yaml_schemas.py`)
- the loader and registry entry points that turn a file into a `DomainSchema`,
  and the derived views (`DomainSummary`, `ClassificationResult`) built from
  one

Out of scope:

- **How to write your first schema.** That is a task, and it has a guide:
  [Author a domain schema](../how-to/author-a-domain-schema.md), which also
  covers turning the loaded schema into a system prompt via
  `domain_system_prompt` and handing it to extraction.
- **Why schemas prompt rather than constrain.** That is a design decision,
  argued in
  [ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md). It is
  restated here only where it changes what a field does.
- **Extraction, chunking, merging and the graph projection.** A schema's only
  contact with them is the prompt text it produces; see the
  [README](../../README.md) for the pipeline as a whole.
- **Domain classification.** `AUTO` and the classifier that picks a domain for
  you are a caller-side concern. This page describes `ClassificationResult`
  only as a value a schema author will see, never authors.

Two entry points are part of the public surface — `load_schema_from_file` and
`load_schema_from_string` are exported from `redstring`, alongside
`DomainSchema` itself. Everything else named here (the registry,
`load_all_schemas`, `validate_schema_file`, `SchemaLoadError`) is reached by a
dotted path into `redstring.extraction.domains`, which makes it internal by
the rule in
[ADR 0006](../adr/0006-the-public-surface-is-gated.md): documented so you can
use it knowingly, not promised.

## Where schema files live and how they are discovered

### The bundled directory

The six schemas that ship with the package live in
`src/redstring/extraction/domains/schemas/`, one file per domain:

```
academic_research.yaml
business_corporate.yaml
encyclopedia_wiki.yaml
literature_fiction.yaml
news_journalism.yaml
technical_documentation.yaml
```

That directory is computed once, at import, as `Path(__file__).parent /
"schemas"` in `loader.py`, and exposed as `get_schema_directory()`. It is the
default for every loader and registry entry point that takes a directory. There
is **no environment variable and no configuration file** that changes it: a
different directory is a caller argument (`schema_dir=`) or nothing. The
registry used to read `DOMAIN_SCHEMA_HOT_RELOAD` from the environment and no
longer does, for the reason recorded in
`tests/unit/test_library_reads_no_environment.py` — a library's disk-access
behaviour should not depend on the shell that started the process.

### The scan

`load_all_schemas(schema_dir=None)` is the discovery mechanism. Given a
directory it:

1. returns `{}` — with a warning logged, not an exception — if the directory
   does not exist; raises `SchemaLoadError` if the path exists but is not a
   directory
2. globs `*.yaml` and then `*.yml`, **non-recursively**; a schema in a
   subdirectory is not found
3. sorts the combined list of paths, so load order is deterministic and does
   not depend on filesystem order
4. loads each file through `load_schema_from_file`, which reads it as UTF-8 and
   parses it with `yaml.safe_load` — never `yaml.load`, so no YAML tag can
   construct a Python object
5. keys the result by the `domain_id` *inside* the file

Files with any other extension are ignored entirely. A `README.md`, a
`.yaml.bak`, or a `schema.json` sitting beside your schemas costs nothing.

### The filename does not name the domain

The dictionary `load_all_schemas` returns, and every registry lookup built on
it, is keyed by the `domain_id` field parsed out of the file — not by the
filename. The six bundled files happen to agree with their `domain_id`, and
following that convention is worth doing for the reader's sake, but nothing
enforces it. A file named `mine.yaml` declaring `domain_id: literature_fiction`
loads as `literature_fiction`.

The consequence to plan around is collisions. Two files in one directory
declaring the same `domain_id` is an error:

```
Duplicate domain_id 'literature_fiction' found in /path/to/second.yaml.
Already loaded from another file.
```

Because the scan is sorted, the file that loads first — and therefore the one
reported as the duplicate — is the alphabetically later of the two, stably. By
default this raises `SchemaLoadError`; with `ignore_errors=True` it is logged
as a warning and the later file is skipped, leaving the first one registered.

### Loading one file

`load_schema_from_file(file_path, schema_dir=None)` takes either form of path:

- an **absolute** path is used as-is, from anywhere on the filesystem
- a **relative** path is resolved against `schema_dir`, defaulting to the
  bundled directory — so `load_schema_from_file("literature_fiction.yaml")`
  reads a bundled schema, and the same call with `schema_dir=Path("./schemas")`
  reads yours

Nonexistent paths and directories-passed-as-files each raise `SchemaLoadError`
before any parsing happens, so you get "Schema file not found: …" rather than a
YAML error. `load_schema_from_string(yaml_content, source_name="<string>")`
skips the filesystem altogether; `source_name` appears in the error messages
and in `SchemaLoadError.file_path`, so give it something recognisable when you
have one.

### Your own directory

Nothing about a custom directory is second-class — the bundled schemas are
loaded by exactly the calls you would make:

```python
from pathlib import Path

from redstring.extraction.domains import load_all_schemas
from redstring.extraction.domains.registry import DomainSchemaRegistry

# One-shot: a plain dict of domain_id -> DomainSchema
schemas = load_all_schemas(Path("./my-schemas"))

# Or point the registry at it (see "Loading and registry behaviour" below)
registry = DomainSchemaRegistry.get_instance(Path("./my-schemas"))
```

Two things to know before you do:

- **Your directory replaces the bundled one; it does not extend it.** A
  registry pointed at `./my-schemas` sees only what is in `./my-schemas`,
  including for the default-schema lookup, which looks for `encyclopedia_wiki`
  by id and falls back to whichever schema loads first. Copy the bundled files
  in if you want both.
- **`ignore_errors=False` is the default, and is the one you want in CI.** A
  single malformed file aborts the whole scan with the offending path on
  `SchemaLoadError.file_path`. `ignore_errors=True` degrades to loading what it
  can — appropriate for a long-running process reloading schemas, not for
  checking a directory is sound. To check one file deliberately, use
  `validate_schema_file`, which returns `(is_valid, error_message)` rather than
  raising.

`get_available_domain_ids(schema_dir=None)` is the cheap "what is here?" call:
it scans with `ignore_errors=True` and returns the sorted ids. Its docstring
describes it as only parsing `domain_id` from each file; it does not — it
fully loads and validates every file, so invalid schemas are silently absent
from its result rather than reported.

## Minimal complete example

This is the smallest file that loads. Every key shown is required; nothing here
can be removed, and everything not shown has a default.

```yaml
domain_id: recipes
display_name: Recipes
description: Cooking recipes, their ingredients, and the techniques they use.

entity_types:
  - id: dish
    description: A prepared dish that a recipe produces.
  - id: ingredient
    description: A single ingredient a recipe calls for.

relationship_types:
  - id: uses
    description: A dish uses an ingredient.
    valid_source_types: [dish]
    valid_target_types: [ingredient]

extraction_prompt_template: |
  You are analyzing a cooking recipe.

  Extract the following types of entities:
  {entity_descriptions}

  Extract the following types of relationships:
  {relationship_descriptions}
```

Loading it:

```python
from pathlib import Path

from redstring import load_schema_from_file

schema = load_schema_from_file(Path("recipes.yaml"))
```

### What the model filled in

The loaded `DomainSchema` has more on it than the file says, because three
fields are optional and defaulted:

| Field | Value after load | Where the default comes from |
|---|---|---|
| `version` | `"1.0.0"` | `DomainSchema.version` default |
| `confidence_thresholds.entity_extraction` | `0.6` | `ConfidenceThresholds` default |
| `confidence_thresholds.relationship_extraction` | `0.5` | `ConfidenceThresholds` default |
| every entity type's `properties` | `[]` | `default_factory=list` |
| every entity type's `examples` | `[]` | `default_factory=list` |
| every relationship type's `valid_source_types` / `valid_target_types` | `[]` — meaning *any* entity type | `default_factory=list` |
| every relationship type's `bidirectional` | `false` | `RelationshipTypeSchema.bidirectional` default |
| every property's `type` | `"string"` | `PropertySchema.type` default |
| every property's `required` | `false` | `PropertySchema.required` default |

So `schema.version` is `"1.0.0"` and
`schema.confidence_thresholds.entity_extraction` is `0.6` for the file above,
and omitting `valid_source_types` on a relationship is not "unconstrained by
oversight" — it is the documented way to say *any source is fine*.

### What is required, and only that

The file above exercises exactly the required set:

- `domain_id`, `display_name`, `description` — three non-empty strings
- `entity_types` — a list of **at least one** entity type, each with an `id`
  and a non-empty `description`
- `relationship_types` — a list of **at least one** relationship type, same two
  required keys
- `extraction_prompt_template` — a non-empty string

`min_length=1` on both lists is the whole structural requirement. A schema with
one entity type and one relationship type is valid, and the two-and-one shown
here is already more than the model demands. The "at least 5 of each" rule you
will see in the six bundled files is a *repository convention* enforced by
`tests/unit/extraction/domains/test_yaml_schemas.py`, not by `DomainSchema` —
see [Conventions the repository schemas follow beyond model
validation](#conventions-the-repository-schemas-follow-beyond-model-validation).
Your own schemas are not subject to it.

### An example with the optional fields filled in

Everything the format offers, on one entity type and one relationship type:

```yaml
entity_types:
  - id: ingredient
    description: A single ingredient a recipe calls for.
    properties:
      - name: quantity
        type: string
        description: Amount used, as the recipe writes it.
        required: false
      - name: grams
        type: number
        description: Normalized weight in grams.
    examples:
      - butter
      - saffron

relationship_types:
  - id: substitutes_for
    description: One ingredient can stand in for another.
    valid_source_types: [ingredient]
    valid_target_types: [ingredient]
    bidirectional: true

confidence_thresholds:
  entity_extraction: 0.7
  relationship_extraction: 0.6

version: "1.2.0"
```

`examples` is capped at 10 entries per entity type; the eleventh is an error,
not a truncation. `type` on a property must be one of `string`, `number`,
`boolean`, `array`, `object`. `version` must be three dot-separated integers,
and YAML will read a bare `1.2` as a float, so **quote it**.

### Three things this example is quietly demonstrating

- **`{entity_descriptions}` and `{relationship_descriptions}` are the two
  placeholders the prompt builder substitutes.** The template is otherwise free
  text. Nothing in `DomainSchema` checks that they are present — a template
  without them loads and then produces a prompt that never names your types.
- **`valid_source_types` and `valid_target_types` must name entity types you
  declared in the same file.** This is the one cross-field rule the model
  enforces: `dish` and `ingredient` are legal above only because both appear in
  `entity_types`. A typo here is a load-time error, not a silently inert
  constraint.
- **These constraints shape the prompt; they do not filter the result.** The
  extractor is free to return an `ingredient` as the source of something you
  restricted to `dish`, and to return entity types you never declared —
  `custom` and `related_to` are always accepted. See
  [ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md). Use
  `DomainSchema.validate_relationship` if you want to check a relationship
  against the declared constraints yourself.

A schema is easiest to get right by starting from a bundled one rather than
from this minimum —
`src/redstring/extraction/domains/schemas/technical_documentation.yaml` is the
most fully populated of the six. For the writing process rather than the
format, see [Author a domain schema](../how-to/author-a-domain-schema.md).

## Top-level fields (`DomainSchema`)

`DomainSchema` is the root model: one YAML file is one `DomainSchema`. It
declares eight fields, six of them required, and `model_config` sets
`extra="forbid"` and `str_strip_whitespace=True` — so any ninth key is a load
error, and leading or trailing whitespace on a string field is removed before
validation bounds are applied.

Unlike the three nested models, `DomainSchema` is **not** `frozen=True`.
`EntityTypeSchema`, `PropertySchema` and `RelationshipTypeSchema` are all
frozen and therefore hashable; the root object is mutable in Python. Nothing in
the loader depends on that, and treating a loaded schema as read-only is the
safe habit — the registry hands the same instance to every caller.

| Field | Required | Type | Default | Bounds |
|---|---|---|---|---|
| `domain_id` | yes | string | — | 1-50 chars, pattern `^[a-z][a-z0-9_]*$` |
| `display_name` | yes | string | — | 1-100 chars |
| `description` | yes | string | — | 1-500 chars |
| `entity_types` | yes | list of `EntityTypeSchema` | — | `min_length=1` |
| `relationship_types` | yes | list of `RelationshipTypeSchema` | — | `min_length=1` |
| `extraction_prompt_template` | yes | string | — | `min_length=1` |
| `confidence_thresholds` | no | `ConfidenceThresholds` | both defaults applied | see that model |
| `version` | no | string | `"1.0.0"` | pattern `^\d+\.\d+\.\d+$` |

The subsections below take each in turn.

### Which rules live here, and which do not

Three kinds of check apply to a schema file, and only the first is
`DomainSchema`'s:

- **Field-level**, on the model: the bounds and patterns in the table, plus the
  normalization each nested model's `id`/`name` validator performs.
- **Cross-field**, on the model: exactly one — the `validate_relationship_type_references`
  model validator, which requires every entry in a relationship's
  `valid_source_types` and `valid_target_types` to name an entity type declared
  in the same file. There is no other whole-schema rule.
- **Repository convention**, not on the model: at least five entity types and
  five relationship types, a declared `related_to`, entity descriptions of at
  least 10 characters, a relationship threshold no higher than the entity
  threshold, and **uniqueness of entity type ids and of relationship type ids**.
  All of these are asserted in
  `tests/unit/extraction/domains/test_yaml_schemas.py` against the six bundled
  files only. A schema of your own declaring the same entity type id twice
  loads without complaint; the duplicate simply sits in the list, and
  `get_entity_type` returns the first match.

Two absences are worth stating plainly, because both look like they should be
checked and are not:

- **The prompt template is a string, not a template.** `min_length=1` is its
  only validation. Neither `{entity_descriptions}` nor
  `{relationship_descriptions}` is required to appear, and no check rejects a
  placeholder that the prompt builder does not substitute. See
  [Prompt template contract](#prompt-template-contract).
- **The declared types are not a closed set.** `is_valid_entity_type` accepts
  `custom` for any schema and `is_valid_relationship_type` accepts
  `related_to`, and neither is consulted at all during extraction — nothing
  filters the LLM's output down to what you declared. The fields above shape
  what the model is asked for. That is the whole of
  [ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md), and
  it is the reason `entity_types` is bounded below (a prompt needs something to
  say) and not above.

### Reading the loaded object

The root model exposes six helpers over these fields, all normalizing their
argument with `.lower().strip()` before comparing:

```python
schema.get_entity_type_ids()          # ['dish', 'ingredient']
schema.get_relationship_type_ids()    # ['uses']
schema.get_entity_type("Dish")        # EntityTypeSchema | None
schema.get_relationship_type("USES")  # RelationshipTypeSchema | None
schema.is_valid_entity_type("custom")         # True, always
schema.is_valid_relationship_type("related_to")  # True, always
```

Note that these lookups normalize *case and surrounding whitespace only* — they
do not apply the space- and hyphen-to-underscore rewriting that the `id`
validators do at load time. `get_entity_type("story arc")` finds nothing even
though `id: story arc` in the file would have been stored as `story_arc`. See
[Identifier normalization rules](#identifier-normalization-rules) and
[Runtime helpers a schema author should know](#runtime-helpers-a-schema-author-should-know).

### `domain_id` — required, pattern `^[a-z][a-z0-9_]*$`, max 50

The key the schema is known by. It is what `load_all_schemas` uses as the
dictionary key, what `get_domain_schema(...)` and `registry.has_domain(...)`
take, and what `DomainSummary.from_schema` copies onto the summary. It is not
derived from the filename — see [Where schema files live and how they are
discovered](#where-schema-files-live-and-how-they-are-discovered).

```yaml
domain_id: literature_fiction
```

Constraints, all declared on the field itself:

| Rule | Value |
|---|---|
| Required | yes |
| Type | string |
| Minimum length | 1 |
| Maximum length | 50 |
| Pattern | `^[a-z][a-z0-9_]*$` |
| Whitespace | stripped before validation (`str_strip_whitespace=True`) |

The pattern says: **lowercase ASCII letter first, then any run of lowercase
ASCII letters, digits and underscores.** So `recipes`, `literature_fiction`,
`iso_9001` and `x` are all accepted. Rejected: `Literature_Fiction` (uppercase),
`3d_printing` (leading digit), `_internal` (leading underscore),
`literature-fiction` (hyphen), `literature fiction` (space), `café` (non-ASCII),
and the empty string.

A rejection is a pydantic `ValidationError`, wrapped by the loader in
`SchemaLoadError` with the file path attached:

```
Schema validation failed for /path/to/mine.yaml: 1 validation error for DomainSchema
domain_id
  String should match pattern '^[a-z][a-z0-9_]*$' [type=string_pattern_mismatch, ...]
```

#### It is not normalized — unlike every other identifier in the file

This is the field's one real surprise, and it runs against the habit the rest
of the format teaches. Entity type ids, relationship type ids, property names
and the entries of `valid_source_types` / `valid_target_types` all pass through
a validator that lowercases, rewrites spaces and hyphens to underscores,
collapses repeated underscores and strips the edges — so `id: Story Arc` is
*accepted* and stored as `story_arc`. `domain_id` has no such validator. It has
a pattern instead, and a pattern rejects rather than repairs.

The practical consequence: `domain_id: Literature Fiction` is a load error,
where `id: Literature Fiction` on an entity type is not. Write the id in the
form you want it stored. See [Identifier normalization
rules](#identifier-normalization-rules) for the rewriting the other fields do.

Whitespace *around* the value is the exception, and it is handled by the model
config rather than by a validator: `domain_id: "  recipes  "` strips to
`recipes` before the pattern is applied, and loads.

#### Lookups normalize, so callers get some slack

The registry compares with `domain_id.lower().strip()` on the *argument*, not
on the stored key — `get_schema("  Literature_Fiction  ")` and
`has_domain("LITERATURE_FICTION")` both find `literature_fiction`. That
tolerance is one-directional: it lets a caller pass a scruffy string, and does
nothing to make a scruffy `domain_id` in a file loadable.

An unknown id from the registry raises `SchemaNotFoundError`, listing what is
available:

```
Unknown domain: 'recipies'. Available domains: academic_research, business_corporate, ...
```

#### Uniqueness is per-directory, and only checked there

`DomainSchema` itself has no opinion about uniqueness — nothing stops you
constructing two schemas with the same `domain_id`. The collision is caught by
`load_all_schemas`, which raises `SchemaLoadError` (or warns and skips, under
`ignore_errors=True`) when a second file in the same scan declares an id
already loaded. Two directories, or two separate `load_schema_from_file` calls,
will happily produce two `literature_fiction` schemas.

#### Conventions worth following

None of these are enforced for schemas of your own; the first two are asserted
against the six bundled files by
`tests/unit/extraction/domains/test_yaml_schemas.py`.

- **Name the file after the id.** The bundled schemas do
  (`literature_fiction.yaml` declares `literature_fiction`), and a test asserts
  the match for each of them. It makes a stack trace mentioning a path
  immediately tell you which domain broke.
- **Treat it as permanent.** It is the key callers pass, so renaming it is a
  breaking change to anyone selecting a domain by name — more like a table name
  than a label. `display_name` is the field to edit when the wording is wrong.
- **Use `noun_noun` snake case, not an abbreviation.** `business_corporate`
  over `bizcorp`. The id shows up in classification output and error messages,
  where it is read by people.

### `display_name` — required, 1-100 chars

The human-readable label for the domain. Unlike `domain_id`, nothing looks a
schema up by it and nothing parses it: it is free text shown to people.

```yaml
display_name: Literature & Fiction
```

| Rule | Value |
|---|---|
| Required | yes |
| Type | string |
| Minimum length | 1 |
| Maximum length | 100 |
| Pattern | none |
| Normalization | none |
| Whitespace | stripped before validation (`str_strip_whitespace=True`) |

Those are the only constraints declared on the field. Punctuation, spaces,
mixed case, ampersands and non-ASCII are all fine — five of the six bundled
schemas use an ampersand (`Literature & Fiction`, `News & Journalism`,
`Business & Corporate`, `Encyclopedia & Wiki`), and the sixth is
`Technical Documentation`. Only two inputs are rejected: the empty string, and
anything over 100 characters after stripping. A whitespace-only value fails
too, because stripping happens first and leaves a zero-length string.

Note the YAML consequence of that punctuation freedom: a value beginning with
`&` would be read as an anchor, and one containing `: ` as a mapping. Neither
bites the bundled files, but quote the value if it starts with any of
``& * ! % @ ` { [`` or contains a colon-space.

#### Where it is actually used

Two places in the package read it, and neither is extraction:

- **`DomainSummary.from_schema`** copies it onto the summary verbatim, so it is
  what appears in `registry.list_domains()` output. See
  [Derived views](#derived-views).
- **`DomainSchemaRegistry.list_domains`** sorts that list by it —
  `sorted(self._schemas.values(), key=lambda s: s.display_name)`. This is a
  plain string sort, so it is case-sensitive and orders by code point: a name
  starting with a lowercase letter sorts after every capitalised one.
  `list_domain_ids()` sorts by id instead, and the two orderings need not
  agree.

It does **not** reach the prompt. The extraction template is built from entity
and relationship descriptions (see
[Prompt template contract](#prompt-template-contract)); no code path
substitutes `display_name` into it, so rewording it cannot change what the LLM
is asked for. Nor does it participate in lookup, classification or error
messages — `SchemaNotFoundError` lists `domain_id`s.

#### Conventions worth following

None of these are enforced. `tests/unit/extraction/domains/test_yaml_schemas.py`
asserts only that the key is *present* in each bundled file; it makes no claim
about its content.

- **Title case, and no relation to the filename or id required.** The bundled
  set pairs `literature_fiction` with `Literature & Fiction` — the id is the
  machine key, this is the label, and the second exists precisely so the first
  can stay ugly and stable.
- **Keep it short enough to sit in a list.** 100 characters is the ceiling, not
  a target; every bundled name is under 30. It is rendered beside five other
  domains in `list_domains()`.
- **Edit this, not `domain_id`, when the wording is wrong.** Changing
  `display_name` breaks nothing, because no caller selects on it. Changing
  `domain_id` breaks every caller that names the domain.

### `description` — required, 1-500 chars

One sentence saying what kind of content this domain covers. The field's own
`description` in `models.py` states its purpose exactly: *"Domain description
for classification hints."* It is the only free-text field on `DomainSchema`
that reaches an LLM.

```yaml
description: Novels, plays, short stories, poetry, and narrative works
```

| Rule | Value |
|---|---|
| Required | yes |
| Type | string |
| Minimum length | 1 |
| Maximum length | 500 |
| Pattern | none |
| Normalization | none |
| Whitespace | stripped before validation (`str_strip_whitespace=True`) |

As with `display_name`, the only rejections are the empty string, a
whitespace-only value (stripping runs first and leaves length zero), and
anything over 500 characters after stripping. Newlines are permitted, so a
YAML block scalar works — but see below for why you probably do not want one.

#### It is the classifier's entire view of the domain

This is what distinguishes `description` from `display_name`, which no LLM ever
sees. `ContentClassifier._build_prompt` builds the list of candidate domains
by asking the registry for summaries and rendering one line each:

```python
domain_list = "\n".join(f"- {d.domain_id}: {d.description}" for d in domains)
```

`d` there is a `DomainSummary`, and `DomainSummary.from_schema` copies
`description` across verbatim. So when a caller extracts with the domain set to
`AUTO`, the choice between your schema and the five bundled ones is made from
this one string and the `domain_id` beside it. Nothing else about the schema —
not the entity types, not the relationship types, not the prompt template —
appears in the classification prompt.

Two consequences follow, and they are the reason to spend a minute on this
field:

- **Write it as a list of content kinds, not as a mission statement.** The
  bundled schemas all do: `Research papers, academic journals, scientific
  studies, and scholarly works`; `API documentation, code tutorials, software
  guides, and technical references`; `Annual reports, business news, corporate
  communications, and financial content`. Each names the artefacts a classifier
  would be looking at, because that is the judgement it is being asked to make.
- **Make it discriminating against the domains it sits beside.** The classifier
  sees all loaded domains in one prompt, so a description that overlaps another
  is the failure mode to design against. `encyclopedia_wiki` is the fallback
  and the broadest of the six, so a new domain whose description could plausibly
  read as "reference material" will lose to it.

The `f"- {d.domain_id}: {d.description}"` format is also why a multi-line
description is a poor idea despite being legal: a block scalar's newlines land
inside a bullet list and blur the boundary between one domain's entry and the
next. One line, under about 120 characters, matches every bundled schema.

#### Where else it surfaces

- **`DomainSummary.description`**, copied unchanged by
  `DomainSummary.from_schema`, and therefore in every
  `registry.list_domains()` result. See [Derived
  views](#derived-views).
- Nowhere in extraction. The extraction prompt is assembled by
  `prompt_generator` from `extraction_prompt_template` with
  `{entity_descriptions}` and `{relationship_descriptions}` substituted — both
  built from the *entity type* and *relationship type* descriptions, not this
  one. See [Prompt template contract](#prompt-template-contract). Rewording the
  domain description cannot change what is extracted once a domain has been
  chosen; it can only change whether it is chosen.

#### Conventions worth following

`tests/unit/extraction/domains/test_yaml_schemas.py` asserts only that the
`description` key is *present* in each bundled file, alongside the other five
required top-level keys. It makes no claim about the content, and there is no
minimum-length convention here — the 10-character floor that test enforces
applies to *entity type* descriptions, not to this field.

- **One line, no trailing period.** All six bundled schemas are a single
  unpunctuated noun phrase; consistency matters more than the specific style,
  because they are rendered as a list.
- **Name three to five concrete content kinds.** 500 characters is a long way
  above what helps: the longest bundled description is 78 characters.
- **Revisit it when you add a domain next to an existing one.** The field is
  only as good as its contrast with its neighbours, and adding a seventh domain
  can make a description that read well in isolation ambiguous. Nothing checks
  this, and a misclassification does not raise — it silently extracts with the
  wrong entity types.

### `entity_types` — required, at least 1 (repository schemas require 5)

The list of entity categories the extractor is asked to look for. Each element
is an `EntityTypeSchema`; the list is what
`{entity_descriptions}` expands to in the prompt.

```yaml
entity_types:
  - id: character
    description: A person, being, or personified entity in the narrative
    properties:
      - name: role
        type: string
        description: "Role in story: protagonist, antagonist, supporting, minor"
    examples:
      - Hamlet
      - Lady Macbeth
```

| Rule | Value |
|---|---|
| Required | yes |
| Type | list of `EntityTypeSchema` |
| Minimum length | 1 (`min_length=1`) |
| Maximum length | none |
| Uniqueness of `id` | **not** enforced by the model |
| Element model | `frozen=True`, `extra="forbid"`, `str_strip_whitespace=True` |

An empty list is the only rejection the field itself makes:

```
entity_types
  List should have at least 1 item after validation, not 0 [type=too_short, ...]
```

Per-element rules — `id` normalization and the identifier requirement, the
1-500 character `description`, the `properties` list, the 10-entry `examples`
cap — live on `EntityTypeSchema` and are documented under [Entity type fields
(`EntityTypeSchema`)](#entity-type-fields-entitytypeschema).

#### One is enough for the model; five is the bundled convention

`min_length=1` is the whole structural requirement, so a schema declaring a
single entity type loads. The "at least 5" in this heading is a *repository*
convention, asserted only against the six bundled files by
`tests/unit/extraction/domains/test_yaml_schemas.py`:

```python
assert len(schema.entity_types) >= 5, (
    f"Schema {domain_id} needs at least 5 entity types, has {len(schema.entity_types)}"
)
```

The bundled files sit well clear of the floor — `literature_fiction` has 7 and
the other five have 9 each — and the same test file requires each of their
entity descriptions to be at least 10 characters. Your own schemas are subject
to neither; both are conventions worth borrowing, and neither is a load-time
error. A companion test asserts only that `examples` *is a list* on every
bundled entity type, despite its name (`test_entity_types_have_examples`) — it
does not require the list to be non-empty. All six bundled files populate
`examples` and `properties` on every entity type anyway.

#### Duplicate ids load, and the second one is unreachable

Nothing on `DomainSchema` checks that entity type ids are distinct. Declaring
`character` twice validates; the list keeps both, and `get_entity_type` returns
the **first** match because it scans in order and returns on the first hit.
`get_entity_type_ids()` will show the duplicate. The prompt will describe the
type twice.

Uniqueness *is* asserted for the bundled schemas, in the same test file:

```python
entity_ids = [et.id for et in schema.entity_types]
assert len(entity_ids) == len(set(entity_ids)), ...
```

Watch for a duplicate produced by normalization rather than by copy-paste:
`id: Story Arc` and `id: story-arc` both normalize to `story_arc`, so two
lines that look unrelated in the YAML collide in the loaded schema. See
[Identifier normalization
rules](#identifier-normalization-rules).

#### It is the target of the one cross-field rule

The set of ids declared here is what `validate_relationship_type_references`
checks `valid_source_types` and `valid_target_types` against — it builds
`{et.id for et in self.entity_types}` and rejects any endpoint naming something
outside it:

```
Relationship 'uses' references unknown source type: 'dishe'.
Valid types: ['dish', 'ingredient']
```

Note the direction: entity types are validated *by* nothing and validate
everything else. Two consequences for editing an existing schema — **removing
an entity type breaks every relationship that names it**, at load time and with
a message naming the relationship rather than the removal; and the comparison
is against *normalized* ids on both sides, so `valid_source_types: [Story Arc]`
matches `id: story_arc` and neither spelling has to match the other literally.

#### What reaches the prompt

`domain_system_prompt` renders one markdown bullet per entity type, in
**declaration order** — the list is never sorted, so the order you write is the
order the model reads:

```
- **character**: A person, being, or personified entity in the narrative (examples: Hamlet, Lady Macbeth)
  Properties: role (Role in story: protagonist, antagonist, supporting, minor)
```

Three things about that rendering are worth knowing while authoring the list:

- **Only the first 3 examples of each type appear.** `MAX_EXAMPLES_PER_TYPE` in
  `src/redstring/extraction/prompt_generator.py` is 3, while the model permits
  10. Examples 4 through 10 are stored on the schema and never shown to the
  extractor; put the most disambiguating ones first.
- **Every property is listed, with its description in parentheses** when it has
  one, and by name alone when it does not. There is no cap here, so a type with
  fifteen properties spends fifteen names of prompt on itself.
- **`type` and `required` on a property do not appear at all.** The prompt names
  the property and describes it; nothing tells the model that `grams` is a
  number or that a property is mandatory. If that matters, say so in the
  property's `description`.

#### The list does not constrain the result

The extractor is not restricted to what you declare. `is_valid_entity_type`
accepts `custom` for any schema, and nothing in the pipeline filters an
extracted entity against `get_entity_type_ids()` — an entity type you never
wrote is not discarded. That is the design in
[ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md), and it
is why this field is bounded below and not above: the list exists so the prompt
has something to say.

#### Conventions worth following

- **Order by importance, not alphabetically.** Declaration order is prompt
  order, and the first bullets are the ones a model weights most.
- **Five to nine types.** The bundled range, and it fits a prompt without
  crowding out the relationship list. A schema with thirty types is usually two
  domains.
- **Give every type two or three examples.** Three is all that reaches the
  prompt, and examples do more to disambiguate a type than a longer
  description does.
- **Descriptions of 10+ characters, phrased as what the thing *is*.** The
  bundled floor, and the string lands verbatim in the prompt after the type
  name — write it to be read there.

### `relationship_types` — required, at least 1 (repository schemas require 5)

The list of edge kinds the extractor is asked to look for. Each element is a
`RelationshipTypeSchema`; the list is what `{relationship_descriptions}`
expands to in the prompt.

```yaml
relationship_types:
  - id: loves
    description: Romantic love between characters
    valid_source_types: [character]
    valid_target_types: [character]

  - id: related_to
    description: General relationship
    bidirectional: true
```

| Rule | Value |
|---|---|
| Required | yes |
| Type | list of `RelationshipTypeSchema` |
| Minimum length | 1 (`min_length=1`) |
| Maximum length | none |
| Uniqueness of `id` | **not** enforced by the model |
| Element model | `frozen=True`, `extra="forbid"`, `str_strip_whitespace=True` |

An empty list is the only rejection the field itself makes:

```
relationship_types
  List should have at least 1 item after validation, not 0 [type=too_short, ...]
```

Per-element rules — `id` normalization, the 1-500 character `description`, the
two endpoint lists and `bidirectional` — live on `RelationshipTypeSchema` and
are documented under [Relationship type fields
(`RelationshipTypeSchema`)](#relationship-type-fields-relationshiptypeschema).

#### One is enough for the model; five is the bundled convention

`min_length=1` is the whole structural requirement. The "at least 5" in this
heading is a *repository* convention, asserted only against the six bundled
files by `tests/unit/extraction/domains/test_yaml_schemas.py`:

```python
assert len(schema.relationship_types) >= 5, (
    f"Schema {domain_id} needs at least 5 relationship types, "
    f"has {len(schema.relationship_types)}"
)
```

The bundled files clear the floor comfortably: 11 for
`technical_documentation`, 12 for `academic_research` and `news_journalism`,
13 for `business_corporate`, 14 for `encyclopedia_wiki`, and 19 for
`literature_fiction`. Two other conventions from that file apply here and to
nothing else in the format — every bundled schema must declare a `related_to`
relationship type, and every relationship type must have a non-empty
`description` (no minimum length, unlike the 10-character floor on *entity*
descriptions). None of the three binds a schema of your own.

#### `related_to` is the fallback, and declaring it is a convention

`is_valid_relationship_type` accepts `related_to` for *any* schema, declared or
not, and `validate_relationship` short-circuits on it: an unknown type named
`related_to` returns `(True, None)` without any endpoint check, because there
is no `RelationshipTypeSchema` to check against. So declaring it buys you
nothing at the model level.

What it buys is prompt text. A declared `related_to` gets a bullet in
`{relationship_descriptions}` telling the model the escape hatch exists; an
undeclared one is silently accepted after the fact and never offered. All six
bundled schemas declare it the same way — a one-line description, no endpoint
constraints (so any pair of entity types is legal), and `bidirectional: true`
— and a test asserts its presence in each. Put it last, where declaration
order puts it last in the prompt.

#### Duplicate ids load, and the second one is unreachable

Nothing on `DomainSchema` checks that relationship type ids are distinct.
Declaring `cites` twice validates; both stay in the list,
`get_relationship_type` returns the **first** match, and the prompt describes
the type twice. Uniqueness *is* asserted for the bundled schemas
(`test_no_duplicate_relationship_type_ids`), against the loaded model rather
than the raw YAML — so it catches collisions produced by normalization as well
as by copy-paste. `id: Depends On` and `id: depends-on` both normalize to
`depends_on`. See [Identifier normalization
rules](#identifier-normalization-rules).

#### The endpoint lists are the one thing validated across fields

`validate_relationship_type_references` runs after the whole model is built and
requires every entry of every `valid_source_types` and `valid_target_types` to
name an entity type declared in the same file:

```
Relationship 'uses' references unknown source type: 'dishe'.
Valid types: ['dish', 'ingredient']
```

Both sides are compared *after* normalization — the endpoint lists get the same
lowercase/underscore rewriting the entity ids do — so `valid_source_types:
[Story Arc]` matches `id: story_arc` without the spellings agreeing literally.
An empty list means *any entity type*, and that is the default; the check skips
empty strings, so a stray `- ""` in the list is ignored rather than rejected.

The direction of the dependency matters when editing: relationship types point
at entity types and nothing points back. **Removing an entity type breaks every
relationship that names it**, at load time, with a message naming the
relationship.

#### What reaches the prompt

`domain_system_prompt` renders one bullet per relationship type, in
**declaration order** — the list is never sorted:

```
- **loves**: Romantic love between characters (from: character; to: character)
- **related_to**: General relationship (bidirectional)
```

The parenthetical is assembled from whichever of the three is present:
`from: …` when `valid_source_types` is non-empty, `to: …` when
`valid_target_types` is, and the bare word `bidirectional` when the flag is
set. A relationship with none of them gets no parenthetical at all. Unlike
entity examples, nothing here is capped — every relationship type and every
endpoint you list reaches the model, so a list of nineteen spends nineteen
lines of prompt.

#### The list does not constrain the result

Nothing in the pipeline filters an extracted relationship against
`get_relationship_type_ids()`, and the endpoint lists are advisory in exactly
the same way: an extractor is free to return `loves` between two `location`s.
`validate_relationship(relationship_type, source_entity_type, target_entity_type)`
exists so a caller can perform that check deliberately, and returns
`(is_valid, error_message)` rather than raising. See
[ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md) and
[Runtime helpers a schema author should
know](#runtime-helpers-a-schema-author-should-know).

#### Conventions worth following

- **Verb phrases in the present tense, snake case.** `authored_by`,
  `depends_on`, `occurred_in`. Every bundled id reads as *source verb target*,
  which is what makes an unconstrained relationship still unambiguous.
- **Constrain endpoints when the pairing is genuinely narrow, and leave them
  empty otherwise.** An empty list is the documented way to say "any", not an
  omission, and over-constraining costs you a load error the day you add an
  entity type the relationship should have accepted.
- **`bidirectional: true` for symmetric relations only** — `married_to`,
  `sibling_of`, `competes_with`, `partners_with`, `contradicts`. It changes the
  prompt text and nothing else; no code reverses an edge on the strength of it.
- **Declare `related_to` last.** Six for six in the bundled set, and it is the
  bullet you want read after the specific ones rather than before them.
- **Five to fifteen types.** The bundled range. The list is uncapped in the
  prompt, so it is the field most able to crowd out the entity descriptions.

### `extraction_prompt_template` — required, non-empty

The text a model is given before it sees a chunk. It is the schema's only
output that reaches extraction: `domain_system_prompt(domain)` takes this
string, substitutes two placeholders, and returns the result for
`ExtractionPipeline(provider, system_prompt=...)`.

```yaml
extraction_prompt_template: |
  You are analyzing a work of literature (novel, play, short story, poem).

  Extract the following types of entities:
  {entity_descriptions}

  Extract the following types of relationships:
  {relationship_descriptions}

  Focus on:
  - Named characters and their roles in the narrative
  - Key themes and motifs
```

| Rule | Value |
|---|---|
| Required | yes |
| Type | string |
| Minimum length | 1 (`min_length=1`) |
| Maximum length | none |
| Pattern | none |
| Placeholders required | **none** — see below |
| Whitespace | stripped before validation (`str_strip_whitespace=True`) |

`min_length=1` is the entire validation. The field's docstring in `models.py`
calls it a "Jinja2-style template", which it is not: no Jinja2 is involved
anywhere in the package, and no template engine of any kind parses it.

#### Substitution is two literal `str.replace` calls

`domain_system_prompt` in
`src/redstring/extraction/prompt_generator.py` does exactly this:

```python
schema.extraction_prompt_template.replace(
    "{entity_descriptions}", _entity_descriptions(schema)
).replace("{relationship_descriptions}", _relationship_descriptions(schema))
```

Three consequences follow from it being `replace` rather than `str.format` or a
template engine, and each is a thing you can rely on:

- **Only those two exact strings are substituted.** There is no `{content}`
  placeholder, no `{domain_id}`, no `{display_name}`. Several tests and the
  module docstrings in `domains/` use `"Extract entities from: {content}"` as a
  throwaway template; that is a valid schema whose prompt contains the literal
  characters `{content}`, not a supported placeholder. Content reaches the
  model as the user message, not through this string.
- **Every other brace is safe.** `str.format` would raise `KeyError` on a
  stray `{` — for instance a JSON example inside your prompt. `replace` passes
  it through untouched, so you may show the model a literal
  `{"entities": [...]}` without escaping anything.
- **Repeats are substituted every time, and omissions are silent.** Naming
  `{entity_descriptions}` twice renders the entity list twice. Naming it zero
  times is legal and renders the template as itself — the source comments on
  this deliberately: *"a domain whose prompt is entirely prose is a domain
  whose author decided the type list was not worth the tokens."* Nothing warns
  you, so a typo like `{entity_description}` produces a prompt that names none
  of your entity types and still extracts.

What each placeholder expands to is documented under
[Prompt template contract](#prompt-template-contract); briefly, one markdown
bullet per declared type in declaration order, with the first three examples
and all properties for entities, and endpoint/bidirectional annotations for
relationships.

#### YAML: use a block scalar, and expect the trailing newline gone

All six bundled schemas write the value as `|` (literal block scalar), which is
the right choice: it preserves line breaks without requiring quotes or escapes,
and a prompt is multi-line by nature. Two details:

- A `|` block keeps a single trailing newline, but `str_strip_whitespace=True`
  removes it — the loaded string ends at the last non-whitespace character.
  Leading indentation common to the block is stripped by YAML itself, so the
  two-space indent in the file does not reach the model.
- The colon in a plain scalar is the usual trap. `extraction_prompt_template:
  Extract entities: names, places` is a YAML error; the block scalar form has
  no such problem.

#### No length rule for your schemas; a 100-character floor for the bundled ones

`tests/unit/extraction/domains/test_yaml_schemas.py` asserts two things about
each of the six bundled templates and nothing about yours:

```python
assert len(schema.extraction_prompt_template) > 100, ...
assert "{entity_descriptions}" in template, ...
assert "{relationship_descriptions}" in template, ...
```

That is the only place the two placeholders are required at all — the model
does not check for them, so the rule binds the repository's own files and not a
schema you load from your own directory. The bundled templates run 599 to 702
characters, comfortably clear of the floor, and share a shape worth copying:
one sentence naming the content kind, the two placeholder blocks under
imperative headings, then a "Focus on:" list of domain-specific instructions.

#### Conventions worth following

- **Include both placeholders.** They are the only mechanism by which the
  entity and relationship types you carefully declared reach the model. A
  template without them makes the rest of the file inert for extraction, which
  is almost never what an author means.
- **Open by naming the content kind.** Every bundled template starts "You are
  analyzing …". It costs one line and it is what tells the model which reading
  to apply to an ambiguous chunk.
- **Put the free-text guidance after the placeholders, not before.** The
  bundled order is intro, entities, relationships, then "Focus on:" — the
  specifics land as elaboration on a type list the model has already read.
- **Write instructions, not output format.** The wire format is fixed by the
  pydantic model `LlmProvider.extract` is given, and a template that describes
  a different JSON shape does not change it — it only invites output the
  mapper cannot read. The deleted `generate_json_schema` documented in
  `prompt_generator.py` is exactly that failure preserved as a comment.
- **Keep it to a few hundred characters.** The placeholders expand to
  everything you declared, uncapped for relationships, so the template's own
  prose competes with the type lists for the model's attention.

### `confidence_thresholds` — optional, defaults applied

A pair of floats saying how confident an extraction should be before a caller
takes it seriously. The field is optional; omitting it yields a
`ConfidenceThresholds` with both defaults, so `schema.confidence_thresholds` is
never `None`.

```yaml
confidence_thresholds:
  entity_extraction: 0.7
  relationship_extraction: 0.6
```

| Rule | Value |
|---|---|
| Required | no |
| Type | `ConfidenceThresholds` (a mapping) |
| Default | `ConfidenceThresholds()` — `entity_extraction: 0.6`, `relationship_extraction: 0.5` |
| Keys | `entity_extraction`, `relationship_extraction`, both optional |
| Bounds | each `0.0 <= x <= 1.0` (`ge=0.0`, `le=1.0`) |
| Unknown keys | rejected (`extra="forbid"`) |
| Mutability | `frozen=True` — unlike `DomainSchema` itself |

Per-key detail is under [Confidence threshold
fields (`ConfidenceThresholds`)](#confidence-threshold-fields-confidencethresholds).

#### Partial mappings are fine

The two keys default independently, so specifying one leaves the other at its
own default:

```yaml
confidence_thresholds:
  entity_extraction: 0.9    # relationship_extraction is still 0.5
```

There is no cross-field validator on this model — nothing requires
`relationship_extraction` to be less than or equal to `entity_extraction`, and
nothing rejects the pair `0.0 / 1.0`. Both `0.0` and `1.0` are explicitly valid
bounds, tested as such in `tests/unit/extraction/domains/test_models.py`.

An out-of-range value is a pydantic `ValidationError`, wrapped by the loader:

```
Schema validation failed for /path/to/mine.yaml: 1 validation error for DomainSchema
confidence_thresholds.entity_extraction
  Input should be less than or equal to 1 [type=less_than_equal, input_value=1.1, ...]
```

Note that these are floats, not percentages: `entity_extraction: 70` is a load
error, not seventy percent.

#### Nothing in the library reads them

This is the field's most important property and the easiest to get wrong.
Grep the package and `confidence_thresholds` appears in exactly three places:
its definition in `models.py`, the re-exports in
`redstring/__init__.py` and `extraction/domains/__init__.py`, and the tests.
**No extraction, merging, mapping or projection code consults it.** Setting
`entity_extraction: 1.0` does not cause a single entity to be dropped, and
setting `0.0` does not admit one that would otherwise have been filtered.

Two nearby things are separate and easy to confuse with it:

- **`ContentClassifier`'s `confidence_threshold`** is a *constructor argument*
  with its own `DEFAULT_CONFIDENCE_THRESHOLD`, applied to the classifier's
  confidence in the domain it picked. It has nothing to do with this field, and
  is not read from any schema.
- **`DEFAULT_CONFIDENCE` in `extraction/schema.py`** is the value an extracted
  entity gets when the model omits one — the midpoint, deliberately not `1.0`.
  It is a fallback for missing data, not a filter.

So the field is **advisory metadata a caller may act on**. If you want it
enforced, read it and filter yourself:

```python
threshold = schema.confidence_thresholds.entity_extraction
kept = [e for e in result.entities if e.confidence >= threshold]
```

This fits the design in
[ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md) — a
schema describes a domain and prompts for it; it does not police the output —
but note the difference in kind from the other fields. `entity_types` and
`extraction_prompt_template` at least reach the model as prompt text. These two
numbers reach nothing at all until you use them.

#### What the bundled schemas set

All six declare the block explicitly rather than relying on defaults:

| Domain | `entity_extraction` | `relationship_extraction` |
|---|---|---|
| `literature_fiction` | 0.6 | 0.5 |
| `academic_research` | 0.7 | 0.6 |
| `encyclopedia_wiki` | 0.7 | 0.6 |
| `technical_documentation` | 0.7 | 0.6 |
| `business_corporate` | 0.75 | 0.65 |
| `news_journalism` | 0.75 | 0.65 |

The pattern is a 0.1 gap with the relationship threshold lower, and the level
tracks how much a wrong edge costs: fiction is the most forgiving, financial
and journalistic content the least.

#### What is checked, and how weakly

`tests/unit/extraction/domains/test_yaml_schemas.py` has two tests here, and
neither can fail:

- `test_schema_has_valid_confidence_thresholds` asserts each value is within
  `0.0..1.0` — which the model has already guaranteed with `ge`/`le`, so the
  assertion is unreachable by construction.
- `test_relationship_threshold_not_higher_than_entity` **skips** rather than
  fails when the relationship threshold is the higher of the two. It is a
  reporting device, not a gate; a bundled schema inverting the pair would show
  up as a skipped test and a green suite.

Treat the "relationship threshold should not exceed the entity threshold"
convention as advice with a reminder attached, not a rule. It binds nothing,
including the bundled files.

#### Conventions worth following

- **Declare the block even when you want the defaults.** Six for six in the
  bundled set. The numbers are a statement about the domain's tolerance for a
  wrong extraction, and writing them down is how a reader learns you thought
  about it.
- **Keep the relationship threshold at or below the entity one.** An edge
  cannot be more trustworthy than the two entities it connects, and this is
  what the (skipping) test is gesturing at.
- **Do not encode a filter you are not applying.** Since nothing in the library
  reads these, a schema whose thresholds imply strict filtering while the
  caller applies none is a lie by omission. Either wire them up at the call
  site or leave them at their defaults.

### `version` — optional, defaults to `1.0.0`, pattern `^\d+\.\d+\.\d+$`

A three-part version string for the schema file itself. It is optional, and
omitting it leaves `schema.version == "1.0.0"`.

```yaml
version: "1.2.0"
```

| Rule | Value |
|---|---|
| Required | no |
| Type | string |
| Default | `"1.0.0"` |
| Pattern | `^\d+\.\d+\.\d+$` |
| Minimum / maximum length | none declared |
| Normalization | none |
| Whitespace | stripped before validation (`str_strip_whitespace=True`) |

The pattern is the whole validation: **three dot-separated runs of digits, and
nothing else.** Accepted: `1.0.0`, `0.0.1`, `10.20.30`, `0.1.0`. Rejected, each
of these exercised in `tests/unit/extraction/domains/test_models.py`: `1.0`
(two parts), `1.0.0.0` (four), `v1.0.0` (prefix), `1.0.0-beta` (semver
pre-release). Despite the field's description calling it "semver format", it is
narrower than semver — the `-beta` and `+build` suffixes SemVer 2.0.0 permits
are load errors here.

A rejection looks like:

```
Schema validation failed for /path/to/mine.yaml: 1 validation error for DomainSchema
version
  String should match pattern '^\d+\.\d+\.\d+$' [type=string_pattern_mismatch, input_value='1.0', ...]
```

#### Quote it

`version: 1.2.0` happens to work — YAML reads three-part dotted values as a
string — but `version: 1.2` is read as the **float** `1.2`, and `version: 1.0`
as `1.0`, neither of which is a string at all:

```
version
  Input should be a valid string [type=string_type, input_value=1.2, ...]
```

That error names a type problem rather than a format one, which is confusing
when what you meant was a version. All six bundled schemas write
`version: "1.0.0"` with quotes; do the same and the failure mode disappears.

#### Nothing reads it

`version` appears in exactly three places in the package: its declaration in
`models.py`, the docstring above it, and the tests. No loader branches on it,
no registry compares it, `DomainSummary` does not carry it, and no migration
or compatibility check exists to consult it. Bumping it changes nothing about
how the schema loads or what it extracts.

It is therefore **documentation for humans and for whatever versioning
discipline you impose yourself**, in the same category as the confidence
thresholds: recorded on the model, acted on only if a caller chooses to. The
difference is that a caller plausibly *will* read
`confidence_thresholds`; there is no code anywhere, in the library or its
tests, that reads `version` for any purpose but asserting its shape.

The one place it is load-bearing by convention is review. A schema is a prompt,
and a prompt change alters extraction results without altering any code, so the
version is the only marker in the file that says "the graph this produces is
not the graph the previous revision produced." Git history records that too,
but only for people who go looking.

#### What is checked

`tests/unit/extraction/domains/test_yaml_schemas.py::test_schema_version_is_semver`
asserts, for each of the six bundled files, that `schema.version` splits on `.`
into three parts and that each part is `.isdigit()`. Both assertions are
already guaranteed by the field's own pattern, so — like the confidence-bounds
test beside it — this one cannot fail while the model is unchanged. There is no
test that the bundled versions differ from each other, that they ever increase,
or that a change to a schema file is accompanied by a bump.

All six bundled schemas are at `1.0.0` and have never moved.

#### Conventions worth following

- **Quote the value, always.** One rule prevents the only confusing error this
  field produces.
- **Bump the minor when you change the prompt or the type lists; bump the patch
  for wording.** Anything that changes what the extractor is asked for changes
  the graph, and that is the distinction worth recording. Adding an entity type
  is a minor; fixing a typo in a description is a patch.
- **Bump the major when you rename or remove a declared id.** Downstream code
  and stored graphs may key on entity and relationship type ids, so removing
  one is the schema's breaking change even though nothing in redstring will
  say so.
- **Do not encode anything but three integers.** The pattern rejects `-rc1` and
  build metadata, so a release process that appends either produces a schema
  that will not load.

## Entity type fields (`EntityTypeSchema`)

One element of the `entity_types` list. It names a category of thing the
extractor is asked to find, describes it, and optionally lists properties and
examples. Everything on the model exists to produce prompt text — nothing here
filters or validates an extraction result.

```yaml
- id: character
  description: A person, being, or personified entity in the narrative
  properties:
    - name: role
      type: string
      description: "Role in story: protagonist, antagonist, supporting, minor"
  examples:
    - Hamlet
    - Lady Macbeth
```

| Field | Required | Type | Default | Bounds |
|---|---|---|---|---|
| `id` | yes | string | — | 1-100 chars, normalized, must be a valid Python identifier |
| `description` | yes | string | — | 1-500 chars |
| `properties` | no | list of `PropertySchema` | `[]` | none |
| `examples` | no | list of strings | `[]` | at most 10 entries |

`model_config` is `extra="forbid"`, `frozen=True`, `str_strip_whitespace=True`.
Three consequences:

- **A fifth key is a load error.** There is no `aliases`, no `parent`, no
  `synonyms`; a misspelled `descripton` fails rather than being ignored.
- **The object is immutable and hashable**, unlike `DomainSchema` itself. You
  can put entity types in a set; you cannot repair one after load.
- **Whitespace is stripped from `id` and `description`** before their length
  bounds apply, so a value that is entirely spaces fails `min_length=1`.

### The one lookup helper

`EntityTypeSchema.get_property(name)` returns a `PropertySchema` or `None`. It
normalizes its argument the way the `name` validator does — lowercase, strip,
spaces and hyphens to underscores — so `get_property("Return Type")` finds
`return_type`. It does *not* collapse repeated underscores or strip the edges,
which the validator does, so a property stored as `return_type` is not found by
`get_property("__return__type__")`. This is the more forgiving of the two
lookup styles in the format: `DomainSchema.get_entity_type` normalizes case and
whitespace *only*.

### Nothing enforces uniqueness or a maximum

Neither `EntityTypeSchema` nor `DomainSchema` checks that entity type ids are
distinct, and there is no upper bound on how many you declare or on how many
properties one carries. Uniqueness is a repository convention asserted against
the six bundled files by
`tests/unit/extraction/domains/test_yaml_schemas.py`; see [`entity_types`
— required, at least 1](#entity_types--required-at-least-1-repository-schemas-require-5)
for what a duplicate does (it loads, and `get_entity_type` returns the first).

### The whole type in one line of prompt

`_entity_descriptions` in `src/redstring/extraction/prompt_generator.py`
renders each entity type as one bullet, plus a second indented line when it has
properties:

```
- **character**: A person, being, or personified entity in the narrative (examples: Hamlet, Lady Macbeth)
  Properties: role (Role in story: protagonist, antagonist, supporting, minor)
```

Reading that rendering backwards tells you what each field is worth:

- `id` and `description` always appear, in declaration order across the list.
- `examples` appears as a parenthetical, **truncated to the first
  `MAX_EXAMPLES_PER_TYPE` — which is 3**, while the model permits 10. Examples
  four through ten are stored and never shown.
- `properties` appears as names with their descriptions in parentheses, all of
  them, uncapped. A property's `type` and `required` flag do **not** appear
  anywhere in the prompt.

An entity type with no examples and no properties renders as the bullet alone,
which is legal and is what the minimal example in this page produces.

### Which entity type ids are special

`is_valid_entity_type` returns `True` for anything in
`get_entity_type_ids()` and for the literal `custom`, whatever the schema
declares. Nothing in the extraction pipeline calls it, so an entity type the
model invents is neither rejected nor recorded as invalid — the helper is there
for a caller who wants to ask. See [Special identifiers with built-in
meaning](#special-identifiers-with-built-in-meaning) and
[ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md).

The other direction is enforced: the ids declared here are the closed set that
`valid_source_types` and `valid_target_types` are checked against at load time.
Entity types validate relationships; nothing validates entity types.

### Conventions worth following

- **Singular nouns.** `character`, `function`, `organization` — every bundled
  id is singular, and the type names a kind of thing rather than a collection
  of them.
- **Descriptions of at least 10 characters, phrased as what the thing is.**
  The bundled floor, asserted by `test_entity_types_have_descriptions`. The
  string lands verbatim after the type name in the prompt, so write it to be
  read there rather than as a schema comment.
- **Two or three examples, most disambiguating first.** Only three reach the
  model, so a list of ten is nine characters of YAML per wasted entry. A test
  named `test_entity_types_have_examples` checks only that the field is a
  *list*, so the bundled files' full coverage of `examples` is habit, not a
  gate.
- **Add a property only when you want the value extracted.** Properties cost
  prompt length in proportion to their descriptions and are uncapped, so a type
  with fifteen of them crowds out the rest of the schema.

The per-field detail for `id`, `description`, `properties` and `examples`
follows; for the writing process rather than the format, see
[Author a domain schema](../how-to/author-a-domain-schema.md).

### `id` — required, normalized, must be a valid Python identifier

The name of the entity type, as the prompt will show it and as
`valid_source_types` / `valid_target_types` will refer to it. It is the one
field in the format that is **rewritten** rather than merely checked.

```yaml
- id: plot_point
  description: A significant event in the narrative
```

| Rule | Value |
|---|---|
| Required | yes |
| Type | string |
| Minimum length | 1 (applied to the input, after whitespace stripping) |
| Maximum length | 100 (applied to the input, before normalization) |
| Pattern | none — a normalizing validator instead |
| Post-condition | the normalized value must satisfy `str.isidentifier()` |
| Uniqueness | **not** enforced by the model |

#### The normalization, exactly

`EntityTypeSchema.validate_entity_type_id` in
`src/redstring/extraction/domains/models.py` runs four steps in this order:

1. `v.lower().strip()` — lowercase, then trim surrounding whitespace
2. `.replace(" ", "_").replace("-", "_")` — spaces and hyphens become
   underscores
3. collapse runs: `while "__" in normalized: normalized.replace("__", "_")`
4. `.strip("_")` — remove leading and trailing underscores

Then two rejections: an empty result, and a result that is not a valid Python
identifier.

| You write | Stored as | Outcome |
|---|---|---|
| `character` | `character` | unchanged |
| `Plot Point` | `plot_point` | tested in `test_entity_type_id_normalization` |
| `literary-device` | `literary_device` | tested in `test_entity_type_id_normalization_hyphens` |
| `Story  Arc` (two spaces) | `story_arc` | runs collapsed by step 3 |
| `__internal__` | `internal` | edges stripped by step 4 |
| `123invalid` | `123invalid` | **rejected** — tested in `test_invalid_entity_type_id` |
| `---` | `` | **rejected** — empty after normalization |
| `plot.point` | `plot.point` | **rejected** — `.` is not rewritten |
| `café` | `café` | **accepted** — `isidentifier()` is Unicode-aware |
| `class` | `class` | **accepted** — a keyword is a valid identifier |

The last two are the surprises. `str.isidentifier()` is the whole test, and it
accepts any Unicode identifier character, so `café` and `entité` load. It also
accepts Python keywords, because `keyword.iskeyword` is not consulted — an
entity type called `class`, `import` or `None` is legal here. Nothing in
redstring `eval`s or `exec`s an id, so neither is a hazard; they are just not
rejected.

Note also what is *not* rewritten: only the space and the hyphen become
underscores. A dot, slash, colon or ampersand survives step 2 unchanged and
then fails `isidentifier()`. So `read-only` loads and `read/only` does not.

#### The two rejection messages

An empty result and an invalid one are distinct errors, both raised as
`ValueError` inside the validator and surfaced by pydantic as a
`ValidationError` — which the loader wraps in `SchemaLoadError` with the file
path:

```
Entity type ID cannot be empty after normalization: '---'
Entity type ID must be a valid identifier: '123invalid' -> '123invalid'
```

The second message shows both spellings, which is what makes a normalization
failure diagnosable: the arrow tells you what your input became before it was
judged.

The length bounds fail differently, because `min_length` and `max_length` are
field constraints applied *before* the validator runs. `id: "   "` fails as
`String should have at least 1 character` (whitespace stripping happens first
and leaves nothing), not as a normalization error. And the 100-character
ceiling applies to what you wrote, not to what it normalizes to — a
101-character id whose underscores would collapse to 40 characters is still
rejected.

#### The id you write is not necessarily the id the schema exposes

This is the consequence to keep in mind everywhere else in the file, and it has
three edges:

- **`get_entity_type_ids()` returns the normalized forms.** Writing
  `id: Plot Point` means the prompt says `plot_point` and every lookup key is
  `plot_point`.
- **`DomainSchema.get_entity_type` normalizes case and whitespace only.** It
  compares `entity_type.lower().strip()` against the stored ids, so
  `get_entity_type("Character")` works and `get_entity_type("Plot Point")`
  returns `None` even though `id: Plot Point` is what you wrote. The lookup is
  weaker than the validator, deliberately or not — pass the normalized form.
- **The endpoint lists normalize the same way.** `normalize_type_lists` on
  `RelationshipTypeSchema` applies the identical rewriting to every entry of
  `valid_source_types` and `valid_target_types`, so `valid_source_types:
  [Plot Point]` matches `id: Plot Point` without either spelling being literal.
  The cross-field check compares normalized to normalized.

Two ids that look different in YAML can therefore collide: `Story Arc`,
`story-arc` and `__story_arc__` are one entity type after loading. Nothing
rejects the duplicate — the list keeps both entries, `get_entity_type` returns
the first, and the prompt describes the type twice. See [Identifier
normalization rules](#identifier-normalization-rules) for the rule stated once
across all four fields it governs.

#### It is a prompt token, not a constraint

The id reaches the extractor as the bolded name in one markdown bullet
(`- **character**: …`), in declaration order. Nothing downstream filters an
extracted entity against it: `is_valid_entity_type` accepts `custom` for any
schema and is not called during extraction at all. The id's only enforced role
is as the target of the relationship endpoint check — entity types validate
relationships, and nothing validates entity types. See
[ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md).

#### Conventions worth following

- **Write the normalized form.** `plot_point`, not `Plot Point`. The validator
  will accept either, but a file whose ids differ from the ids the schema
  exposes makes every lookup and every `valid_source_types` entry a small
  translation exercise. All six bundled schemas are already in snake case.
- **Singular nouns, ASCII, no keywords.** The bundled set is uniformly
  singular lowercase ASCII; the Unicode and keyword allowances exist because
  `isidentifier()` grants them, not because anything wants them.
- **Treat it as permanent.** Stored graphs and downstream code key on entity
  type ids, so renaming one is the schema's breaking change — bump the major
  `version`, and expect nothing in redstring to warn you.
- **Check for a normalization collision after editing.** Two ids that differ
  only in case, spacing or hyphenation load as duplicates in silence for your
  schemas; the bundled files are protected by
  `tests/unit/extraction/domains/test_yaml_schemas.py`, which asserts
  uniqueness against the *loaded* model and therefore catches this shape.

### `description` — required, 1-500 chars

What this entity type is. The string lands verbatim in the extraction prompt,
immediately after the bolded type name, so it is instruction text for a model
rather than a comment for a reader.

```yaml
- id: function
  description: A callable function or method with a specific signature
```

| Rule | Value |
|---|---|
| Required | yes |
| Type | string |
| Minimum length | 1 (`min_length=1`) |
| Maximum length | 500 (`max_length=500`) |
| Pattern | none |
| Normalization | none — unlike `id`, it is stored exactly as written |
| Whitespace | stripped before validation (`str_strip_whitespace=True`) |

The field's own declaration in `models.py` describes it as a
*"Human-readable description for extraction prompts"*, which is precisely its
scope. Only three inputs are rejected: a missing key, the empty string, and a
whitespace-only value — stripping runs first, so `description: "   "` fails
`min_length=1` rather than passing as three spaces. Anything over 500
characters after stripping fails too. Newlines are legal; see below for why
they are a bad idea.

Note that this is a *different* field from the top-level
[`description`](#description--required-1-500-chars) on `DomainSchema`, which
has the same name and the same bounds but a completely different job: that one
is the classifier's view of the whole domain and never reaches extraction,
while this one reaches extraction and never reaches the classifier.

#### Exactly where it goes

`_entity_line` in `src/redstring/extraction/prompt_generator.py` is the whole
of its use:

```python
line = f"- **{entity_type.id}**: {entity_type.description}"
```

so the bullet that reaches the model reads:

```
- **function**: A callable function or method with a specific signature (examples: parse_document, get_entity)
```

Two things follow. First, **a multi-line description breaks the bullet list** —
the second line is not indented and is not prefixed, so it renders as a
paragraph between two bullets and blurs which type it belongs to. Keep it to
one line. Second, **there is no escaping and no markdown processing**: the
string is interpolated as-is, so a backtick, an asterisk or a colon in your
description is passed through and read by the model as markdown. That is
usually harmless and occasionally useful (the bundled schemas use a colon in
property descriptions to introduce a value list), but a stray `**` will bold
the rest of the line.

#### A 10-character floor, for the bundled schemas only

`tests/unit/extraction/domains/test_yaml_schemas.py::test_entity_types_have_descriptions`
asserts, for every entity type in each of the six bundled files:

```python
assert et.description, f"Entity type {et.id} in {domain_id} has no description"
assert len(et.description) >= 10, (
    f"Entity type {et.id} in {domain_id} has too short description"
)
```

The first assertion is already guaranteed by `min_length=1`. The second is
not — `description: A person` is nine characters and loads fine in a schema of
your own. The floor is a repository convention and binds nothing you load from
your own directory. It is the only length convention on any description field:
*relationship* type descriptions are checked for presence and not for length,
and neither the top-level domain description nor a property description has a
convention at all.

#### It is prompt text, not metadata

Nothing reads this field except the prompt builder. It does not appear in
`DomainSummary`, it is not consulted by `is_valid_entity_type` or
`validate_relationship`, and no extracted entity is checked against it. Its
entire effect is on what the model is asked to look for, which is the design
recorded in
[ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md): the
schema prompts and does not constrain. The practical reading is that editing a
description is a *behavioural* change to extraction with no test in the
library that can see it — which is what the `version` field exists to record.

#### Conventions worth following

- **Say what the thing is, in a noun phrase, without repeating the id.**
  `A callable function or method with a specific signature`, not
  `The function entity type`. The id is already printed immediately before it.
- **One line, 30 to 90 characters.** The bundled descriptions all sit in that
  band, and the bullet is competing with every other type for the model's
  attention.
- **Name the boundary when two of your types could be confused.** The
  description is where you tell the model that `class` is object-oriented and
  `module` is an import path — the ids alone do not distinguish them, and
  `examples` only reaches the prompt three entries deep.
- **No trailing period.** Six for six in the bundled set, and the bullet
  continues into the `(examples: …)` parenthetical, where a period reads
  oddly.

### `properties` — optional list of `PropertySchema`

The structured attributes you want extracted alongside an entity of this type.
Each element is a `PropertySchema`; the list is optional and defaults to `[]`.

```yaml
- id: function
  description: A callable function or method with a specific signature
  properties:
    - name: signature
      type: string
      description: Function signature including parameters
    - name: is_async
      type: boolean
      description: Whether the function is asynchronous
```

| Rule | Value |
|---|---|
| Required | no |
| Type | list of `PropertySchema` |
| Default | `[]` (`default_factory=list`) |
| Minimum length | none — an empty list is valid, and so is omitting the key |
| Maximum length | **none** |
| Uniqueness of `name` | **not** enforced by the model |
| Element model | `frozen=True`, `extra="forbid"`, `str_strip_whitespace=True` |

The field itself declares no constraints at all: no `min_length`, no
`max_length`, no validator. Everything that can reject a `properties` block is
a per-element rule on `PropertySchema` — a `name` that is empty or does not
normalize to a valid identifier, a `type` outside the five allowed literals, a
`description` over 500 characters, or any key other than those four. Those are
documented under [Property fields
(`PropertySchema`)](#property-fields-propertyschema).

#### What a property reaches the model as

`_property_hints` in `src/redstring/extraction/prompt_generator.py` is the
whole of the list's use:

```python
", ".join(
    f"{prop.name} ({prop.description})" if prop.description else prop.name
    for prop in properties
)
```

and `_entity_descriptions` emits that as a second, indented line under the
entity bullet — only when the list is non-empty:

```
- **function**: A callable function or method with a specific signature (examples: extract_entities, create_user)
  Properties: signature (Function signature including parameters), is_async (Whether the function is asynchronous)
```

Three properties of that rendering matter while authoring:

- **Every property appears — the list is uncapped.** Unlike `examples`, which
  is truncated to the first `MAX_EXAMPLES_PER_TYPE` (3), nothing here is
  dropped. `technical_documentation`'s `function` type declares five
  properties and all five are printed. A type with fifteen spends fifteen
  names and fifteen descriptions of prompt on itself, in declaration order.
- **`type` and `required` do not appear anywhere.** The rendered hint is the
  name and, in parentheses, the description. Nothing tells the model that
  `parameters` is an array or that a property is mandatory. If either matters,
  say so in the property's `description` — that is the only channel.
- **Declaration order is prompt order**, and a property with no `description`
  contributes its bare name.

#### The extractor is not held to it

This is the field's biggest gap between what it looks like and what it does.
The wire model `LlmProvider.extract` is given carries a free-form
`properties: dict[str, Any]` per entity, described to the model as *"Any other
attributes the text states about this entity"* — it is **not** built from the
schema, and its keys are not constrained to the names you declare. Nothing in
`mapping.py` compares an extracted entity's property keys against
`EntityTypeSchema.properties`: `map_extraction` copies the dict through with
`properties=dict(candidate.properties)` and no filtering, renaming, defaulting
or type coercion.

So all four consequences hold at once, and none of them is an oversight —
they are [ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md)
applied at property granularity:

- a property you declared may be **absent** from an extracted entity, even
  with `required: true`
- a property you never declared may be **present**
- a property declared `type: number` may arrive as a string; `Any` is the
  declared value type and no coercion runs
- nothing raises, warns, or records a mismatch

`required: true` is therefore a statement of intent for a reader, not a
guarantee — and since `required` does not even reach the prompt, it is not
currently a hint to the model either. If you need any of this enforced, read
`schema.get_entity_type(entity.entity_type).properties` and check the
extracted dict yourself.

#### Looking one up

`EntityTypeSchema.get_property(name)` is the only accessor, returning a
`PropertySchema` or `None`. It normalizes its argument with
`.lower().strip().replace(" ", "_").replace("-", "_")`, so
`get_property("Return Type")` and `get_property("return-type")` both find
`return_type`. It stops short of the two later steps the `name` validator
performs — collapsing repeated underscores and stripping the edges — so a
property stored as `return_type` is not found by `get_property("__return__type__")`
even though `name: __return__type__` in the file would have been stored as
`return_type`. Duplicate names are not rejected; `get_property` returns the
first match, scanning in declaration order.

#### What the bundled schemas do

All six populate `properties` on every entity type, most heavily in
`technical_documentation` (`function` has `signature`, `parameters`,
`return_type`, `is_async`, `docstring`). No test asserts any of this: the only
`properties`-adjacent assertion in
`tests/unit/extraction/domains/test_yaml_schemas.py` is
`test_entity_types_have_examples`, which checks `examples` is a list, and
whose comment ("ensure the property exists and is a list") uses "property" in
the Python sense rather than this one. There is no convention here that a test
enforces — not a count, not a naming style, not a required description.

#### Conventions worth following

- **Declare a property only when you want the value extracted.** Every entry
  costs prompt length in proportion to its description, uncapped, competing
  with the entity and relationship lists. Three to five per type is the
  bundled range.
- **Always write a `description`.** A bare name renders as a bare name, and
  `is_async` alone tells a model less than `is_async (Whether the function is
  asynchronous)`. Every property in every bundled schema has one.
- **Put the type expectation in the description when it matters.** `type:
  number` is invisible to the model and unenforced afterwards, so "Normalized
  weight in grams" does more work than the `type` field does.
- **Snake-case singular names, written in normalized form.** `return_type`,
  not `Return Type` — the validator accepts either, and a file whose names
  differ from the stored names makes `get_property` a translation exercise.
- **Do not model relationships as properties.** A property is a scalar
  attribute of one entity; a link to another entity belongs in
  `relationship_types`, where the endpoint lists and the graph projection can
  see it.

### `examples` — optional, maximum 10 entries

Sample names of things that are instances of this entity type, used for
few-shot prompting. The list is optional and defaults to `[]`.

```yaml
- id: character
  description: A person, being, or personified entity in the narrative
  examples:
    - Hamlet
    - Lady Macbeth
    - Jay Gatsby
    - Elizabeth Bennet
```

| Rule | Value |
|---|---|
| Required | no |
| Type | list of strings |
| Default | `[]` (`default_factory=list`) |
| Minimum length | none — an empty list is valid, and so is omitting the key |
| Maximum length | **10** (`validate_examples_length`) |
| Per-entry bounds | **none** — no minimum, no maximum, no pattern |
| Normalization | none, beyond whitespace stripping on each entry |
| Uniqueness | not enforced |

#### The cap rejects; it does not truncate

`EntityTypeSchema.validate_examples_length` is three lines and the whole of the
field's validation:

```python
if len(v) > 10:
    raise ValueError(f"Maximum 10 examples allowed, got {len(v)}")
```

So an eleventh example is a load error, surfaced by pydantic as a
`ValidationError` and wrapped by the loader in `SchemaLoadError` with the file
path attached:

```
Value error, Maximum 10 examples allowed, got 11
```

Exactly 10 is accepted; both boundaries are pinned as examples in
`tests/unit/extraction/domains/test_models.py`
(`test_entity_type_examples_exactly_10` and `test_entity_type_examples_max_10`).
Nothing silently drops the tail at load time — the truncation happens later, in
the prompt builder, and at a much lower number.

#### Only the first three reach the model

`MAX_EXAMPLES_PER_TYPE` in
`src/redstring/extraction/prompt_generator.py` is **3**, and `_entity_line`
slices with it:

```python
if entity_type.examples:
    examples = ", ".join(entity_type.examples[:MAX_EXAMPLES_PER_TYPE])
    line += f" (examples: {examples})"
```

giving the parenthetical at the end of the entity bullet:

```
- **character**: A person, being, or personified entity in the narrative (examples: Hamlet, Lady Macbeth, Jay Gatsby)
```

The constant carries its own reasoning in the source — *"All of them is not
better. The examples are there to disambiguate the type, and a schema listing
twenty of them would spend most of the prompt on one type — which reads to the
model as emphasis rather than as illustration."*

The gap between the two numbers is the thing to plan around: **the model
permits 10 and the prompt shows 3.** Entries four through ten are stored on the
loaded schema, are readable through
`schema.get_entity_type("character").examples`, and are never shown to the
extractor. `literature_fiction` declares four examples on `character` and four
on `theme`; `Elizabeth Bennet` and `mortality` are the entries no model sees.
So **order the list deliberately** — put the most disambiguating examples
first, and treat positions four onward as documentation for whoever reads the
YAML rather than as prompt input.

An empty list produces no parenthetical at all: the bullet is `- **id**:
description` and nothing is lost.

#### Entries are stripped, and otherwise unchecked

`str_strip_whitespace=True` on `EntityTypeSchema` applies to each string in the
list, so `- "  Hamlet  "` is stored as `Hamlet`. Nothing else is done to an
entry. In particular:

- **The empty string is a legal entry.** `examples: ["", "Hamlet"]` loads, and
  renders as `(examples: , Hamlet)`. There is no `min_length` on the items.
- **No identifier normalization.** Unlike `id`, an example is free text —
  spaces, capitals, punctuation and non-ASCII all survive verbatim, which is
  the point. `The murder of King Duncan` and `1920s New York` are bundled
  entries.
- **Duplicates are kept.** Nothing deduplicates, and a repeated example simply
  appears twice in the prompt if it falls in the first three.
- **Commas are the separator in the rendered prompt.** `", ".join` is what
  builds the parenthetical, so an example containing a comma is
  indistinguishable from two examples once the model reads it. Prefer entries
  without one.

#### It is prompt text and nothing else

Nothing reads `examples` outside the prompt builder. It does not appear in
`DomainSummary`, it is not consulted by `is_valid_entity_type`,
`get_entity_type` or `validate_relationship`, and no extracted entity is
checked against it. An extractor is free to return entities that resemble none
of them, and returning exactly one of them is not treated as a stronger result.
The list biases what the model looks for; it constrains nothing, per
[ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md).

That has a review consequence worth stating: editing this list changes
extraction behaviour with no test in the library able to see the change, which
is one of the things the schema's [`version`](#version--optional-defaults-to-100-pattern-ddd)
field exists to record.

#### What is checked

`tests/unit/extraction/domains/test_yaml_schemas.py::test_entity_types_have_examples`
is the only test naming this field against the bundled schemas, and **it does
not check what its name says**:

```python
for et in schema.entity_types:
    # Not all entity types require examples, but most should have them
    # We'll just ensure the property exists and is a list
    assert isinstance(et.examples, list), (
        f"Entity type {et.id} in {domain_id} examples should be a list"
    )
```

`examples` is typed `list[str]` with a `default_factory=list`, so the assertion
is true by construction and cannot fail. There is no enforced convention here —
not a minimum count, not non-emptiness, not a style. The six bundled files
populate every entity type with two, three or four examples out of habit rather
than under a gate.

#### Conventions worth following

- **Two or three entries, most disambiguating first.** Every bundled entity
  type sits in the two-to-four range, and only the first three are ever seen.
  A fourth is for the reader; a fifth is usually noise.
- **Use real instances, not categories.** `Hamlet` and `Elizabeth Bennet`, not
  `a protagonist`. An example does more to fix the type's boundary than a
  longer `description` does, and it does it by being concrete.
- **Pick examples that separate this type from its neighbours.** If your schema
  has both `class` and `module`, the examples are where you show the
  difference — the ids do not, and `description` is competing for the same
  line.
- **Keep entries short and comma-free.** They are rendered inline in a
  parenthetical joined by `", "`, so a long example crowds the bullet and one
  containing a comma reads as two.

## Property fields (`PropertySchema`)

One element of an entity type's `properties` list. It names a structured
attribute you want extracted alongside an entity of that type, and — like
everything else in the format — it produces prompt text and nothing more.

```yaml
properties:
  - name: signature
    type: string
    description: Function signature including parameters
    required: false
```

| Field | Required | Type | Default | Bounds |
|---|---|---|---|---|
| `name` | yes | string | — | 1-100 chars, normalized, must be a valid Python identifier |
| `type` | no | one of `string`, `number`, `boolean`, `array`, `object` | `"string"` | `Literal` — anything else is rejected |
| `description` | no | string or absent | `None` | max 500 chars, no minimum |
| `required` | no | boolean | `false` | — |

`model_config` is `extra="forbid"`, `frozen=True`, `str_strip_whitespace=True`,
the same as the other two nested models. So a fifth key is a load error (there
is no `default`, no `enum`, no `pattern`), the object is immutable and
hashable, and whitespace is stripped from `name` and `description` before their
bounds apply.

Note that `description` is the only string field in the whole format that is
**optional and nullable**: `PropertySchema.description` is `str | None` with a
default of `None`, where the domain, entity type and relationship type
descriptions are all required and non-empty. A property with no description is
legal and renders as a bare name.

### Only two of the four fields reach the model

`_property_hints` in `src/redstring/extraction/prompt_generator.py` is the
entire consumer of this model:

```python
", ".join(
    f"{prop.name} ({prop.description})" if prop.description else prop.name
    for prop in properties
)
```

and `_entity_descriptions` emits that as a second, indented line under the
entity bullet, only when the list is non-empty:

```
- **function**: A callable function or method with a specific signature (examples: extract_entities, create_user)
  Properties: signature (Function signature including parameters), parameters (List of parameter names and types), is_async (Whether the function is asynchronous)
```

**`type` and `required` appear nowhere in that string.** Nothing tells the
model that `parameters` is an array or that a property is mandatory; the only
channel for either is the `description` text. That is the single most important
fact about this model, and it is why the conventions below push everything you
want the extractor to know into the description.

Nothing is capped, either — unlike `examples`, which is truncated to the first
three, every property is printed in declaration order.

### Nothing enforces the schema afterwards

The wire model an `LlmProvider` fills in carries a free-form
`properties: dict[str, Any]` per entity, and `map_extraction` copies it through
with `properties=dict(candidate.properties)` — no filtering against declared
names, no renaming, no defaulting, no coercion. So:

- a property you declared may be **absent**, `required: true` notwithstanding
- a property you never declared may be **present**
- a property declared `type: number` may arrive as a string
- nothing raises, warns, or records the mismatch

This is [ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md)
at property granularity. `required` is currently the weakest field in the
format: it does not validate, and it does not even reach the prompt, so it is a
note to a human reader. If you need any of this enforced, read
`schema.get_entity_type(entity.entity_type).properties` and check the extracted
dict at the call site.

### Looking one up

`EntityTypeSchema.get_property(name)` is the only accessor. It normalizes its
argument with `.lower().strip().replace(" ", "_").replace("-", "_")` — so
`get_property("Return Type")` and `get_property("return-type")` both find
`return_type` — but stops short of the two remaining steps the `name` validator
performs, collapsing repeated underscores and stripping the edges. A property
stored as `return_type` is therefore *not* found by
`get_property("__return__type__")`, even though `name: __return__type__` in the
file would have been stored as `return_type`. Duplicate names are not rejected
by anything; `get_property` returns the first match in declaration order.

### What the bundled schemas do

Across the six bundled files there are **120 properties**, and the distribution
is worth knowing before you reach for a field the format offers:

| Observation | Count |
|---|---|
| `type: string` | 110 |
| `type: array` | 7 |
| `type: boolean` | 3 |
| `type: number` / `type: object` | 0 — never used |
| `required:` written at all | 0 — never used |
| properties with no `description` | 0 |
| most properties on one entity type | 5 (`technical_documentation`'s `function`) |

No test in `tests/unit/extraction/domains/test_yaml_schemas.py` asserts
anything about properties: there is no count convention, no naming rule, no
required description. The uniformity above is habit, and the habit is sound —
`type` and `required` buy nothing at present, so the bundled schemas mostly
leave them at their defaults and spend the effort on descriptions.

The per-field detail for `name`, `type`, `description` and `required` follows.
