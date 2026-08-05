# Author a domain schema

This guide shows you how to write a domain schema in YAML, load it, turn it
into a system prompt, and hand that prompt to extraction.

A domain schema is a YAML file describing the entity types and relationship
types you expect in a body of text, plus the prompt template that presents
them to the model. `redstring` bundles six of them —
`literature_fiction`, `news_journalism`, `academic_research`,
`technical_documentation`, `business_corporate`, and `encyclopedia_wiki` —
under `src/redstring/extraction/domains/schemas/`. You write your own when
none of those describes the material you are extracting from.

By the end you will have:

- a YAML file that `load_schema_from_file` accepts,
- a `DomainSchema` object,
- a system prompt from `domain_system_prompt(schema)`, and
- an `ExtractionPipeline` (or a `build_graph` call) running with it.

There is no registration step and no plugin hook: `domain_system_prompt`
takes either a bundled domain id or a `DomainSchema` object, so a schema you
loaded yourself is a first-class argument.

One thing to hold onto before you start, because it governs what a schema can
and cannot do for you: **the schema shapes the prompt, it does not enforce the
wire format.** Declaring an entity type tells the model what you are looking
for; nothing rejects an extraction that invents a type you never declared. See
[ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md) for why,
and [What this guide deliberately does not cover](#what-this-guide-deliberately-does-not-cover)
for what that means in practice.

For the field-by-field specification of every key, see the reference:
[`docs/reference/domain-schema-yaml.md`](../reference/domain-schema-yaml.md).

## Before you start

Writing a schema is cheap, but it is not free: it is a file you now own,
and its entity types shape every prompt you send. Check first whether one of
the six bundled domains already covers your material.

### What the six bundled domains are for

| `domain_id` | `display_name` | The material it describes |
|---|---|---|
| `literature_fiction` | Literature & Fiction | Novels, plays, short stories, poetry, narrative works |
| `news_journalism` | News & Journalism | News articles, press releases, current events |
| `academic_research` | Academic Research | Research papers, journals, scientific studies |
| `technical_documentation` | Technical Documentation | API docs, code tutorials, software guides |
| `business_corporate` | Business & Corporate | Annual reports, business news, financial content |
| `encyclopedia_wiki` | Encyclopedia & Wiki | Encyclopedic articles, wiki content, reference material |

Each is a YAML file under `src/redstring/extraction/domains/schemas/`, written
against exactly the rules this guide describes. Reading the one closest to your
material is the fastest way to see a complete, valid schema — start there
rather than from a blank file.

Using one takes an id and nothing else:

```python
from redstring import domain_system_prompt

prompt = domain_system_prompt("news_journalism")
```

`encyclopedia_wiki` is the deliberately general one. It is what `build_graph`
falls back to when `domain=AUTO` classifies with low confidence, and it is a
reasonable answer for mixed or unclassifiable text.

### One of the bundled ids is enough when

- **Your material is one of the six kinds above**, even if the vocabulary is
  not a perfect fit. The schema shapes the prompt; it does not constrain what
  the model may return, so a type you did not declare is not rejected — see
  [ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md). An
  approximate domain still steers extraction in the right direction.
- **You have not yet measured that extraction is going wrong.** Start with
  `domain=None` (the general prompt) or a bundled id, look at what comes out,
  and let the gap tell you which entity types you are actually missing. A
  schema written before that is a guess with a maintenance cost.
- **You want the domain chosen per document.** `build_graph(..., domain=AUTO)`
  runs `ContentClassifier` over the head of each document and picks one of the
  bundled six. It costs one extra model call per document, and never fails:
  documents under 100 characters are not classified at all, and a
  low-confidence result falls back to `encyclopedia_wiki`. Read
  `report.domain_confidence` to tell a real choice from a give-up — `0.0`
  means the classifier gave up. `AUTO` cannot select a schema of yours; it
  only ranges over what is bundled.

### Write your own when

- **Your material is not any of the six.** Clinical notes, legal contracts,
  incident reports, product catalogues, transcripts — nothing bundled
  describes these, and the general prompt will extract generic people and
  organizations where you wanted diagnoses, clauses, or SKUs.
- **The entity types you need do not exist in any bundled schema**, or the
  relationship vocabulary is wrong. Naming the types you want is the whole
  mechanism by which you get them.
- **You want to pin the prompt.** A bundled schema is part of the library and
  can change between releases; a schema in your repository changes when you
  change it. If extraction output is something you regression-test, own the
  file.

There is no halfway option worth reaching for: schemas are not merged or
inherited, and there is no registration step for a custom one. You load your
YAML and pass the resulting `DomainSchema` object straight to
`domain_system_prompt`, which accepts an object exactly as readily as an id.
Copying the closest bundled file into your own repository and editing it is
the supported way to "extend" one.

### What you need in place

- `redstring` installed, and the four names this guide uses —
  `load_schema_from_file`, `load_schema_from_string`, `DomainSchema` and
  `domain_system_prompt` — are all importable from the top-level package.
  Nothing here needs a dotted import into `redstring.extraction`.
- An `LlmProvider` if you intend to run the prompt at the end. `build_graph`
  and `ExtractionPipeline` both need one; writing and validating a schema does
  not.

## Step 1: Write the YAML

Create a file anywhere you like — the loader takes a path, so nothing has to
live inside the package. Call it `field_reports.yaml` for the rest of this
guide.

A schema is a single YAML mapping. Everything below is a top-level key of that
mapping; there is no wrapper, no `schema:` root, and no list at the top level.

### Required top-level keys

Six keys have no default, and a file missing any of them will not load:

| Key | What it is |
|---|---|
| `domain_id` | The identifier, e.g. `field_reports` |
| `display_name` | Human-readable name, up to 100 characters |
| `description` | One sentence describing the material, up to 500 characters |
| `entity_types` | A list of entity types, at least one |
| `relationship_types` | A list of relationship types, at least one |
| `extraction_prompt_template` | The prompt text, non-empty |

Two more are optional: `confidence_thresholds` and `version`. Everything else
is an error — see [Step 2](#step-2-know-what-the-validator-will-reject).

A minimal file that loads:

```yaml
domain_id: field_reports
display_name: Field Reports
description: Incident and site-visit reports written by field engineers

entity_types:
  - id: site
    description: A physical location a report was written about

relationship_types:
  - id: observed_at
    description: An observation was made at a site

extraction_prompt_template: |
  Extract entities and relationships from this field report.

  Entity types:
  {entity_descriptions}

  Relationship types:
  {relationship_descriptions}
```

### `domain_id` and `version`

`domain_id` must match `^[a-z][a-z0-9_]*$` and be 1–50 characters: a lowercase
letter first, then lowercase letters, digits and underscores. Unlike entity and
relationship ids, this one is **not** normalized for you — it is rejected
rather than rewritten, so write the form you want:

| Rejected | Why |
|---|---|
| `Field_Reports` | uppercase |
| `123reports` | starts with a digit |
| `reports!` | special character |
| `field-reports` | hyphen |
| `_reports` | leading underscore |

Write `field_reports`.

`version` is optional and defaults to `1.0.0`. When you set it, it must match
`^\d+\.\d+\.\d+$` — three numeric components, nothing else. `"1.0"` and
`"1.0.0-beta"` both fail. Quote it, or YAML will read `1.0.0` fine but `1.0`
as a float:

```yaml
version: "1.2.0"
```

Nothing in the library compares versions or behaves differently across them;
the field is there so your own tooling can tell two revisions of your schema
apart.

### Declare entity types

Each item in `entity_types` needs `id` and `description`, and may carry
`properties` and `examples`.

```yaml
entity_types:
  - id: site
    description: A physical location a report was written about
    properties:
      - name: region
        type: string
        description: Operating region the site belongs to
      - name: commissioned
        type: boolean
        description: Whether the site is in service
    examples:
      - Thornbury Substation
      - Platform 4

  - id: fault
    description: A defect or failure observed during the visit
    examples:
      - Bearing overheat
      - Corroded earth strap
```

Those two entity types render into the prompt as:

```text
- **site**: A physical location a report was written about (examples: Thornbury Substation, Platform 4)
  Properties: region (Operating region the site belongs to), commissioned (Whether the site is in service)
- **fault**: A defect or failure observed during the visit (examples: Bearing overheat, Corroded earth strap)
```

Reading that block back is the quickest check that a type is pulling its
weight: what you see there is the whole of what the model is told.

#### `id` — required, 1–100 characters, normalized

The id is normalized before it is stored: lowercased, surrounding whitespace
stripped, spaces and hyphens turned into underscores, runs of underscores
collapsed, leading and trailing underscores stripped. The result must be a
valid Python identifier.

| Written | Stored | Outcome |
|---|---|---|
| `site` | `site` | loads |
| `Access Road` | `access_road` | loads |
| `fault-code` | `fault_code` | loads |
| `2nd stage` | `2nd_stage` | rejected — an identifier may not start with a digit |
| `___` | (empty) | rejected — nothing left after normalization |

Write the normalized form yourself. Everything else that names an entity type
— a relationship's `valid_source_types` and `valid_target_types`, and the
bullet in the generated prompt — uses the *normalized* id, so a schema written
in mixed case reads as one thing and behaves as another.

#### `description` — required, 1–500 characters

This is not decoration. It is copied verbatim into the prompt after the id,
and it is the main thing telling the model what the type means. Write it as an
instruction to someone who has never seen your material: "A defect or failure
observed during the visit" earns its place; "A fault" does not.

#### `properties` — optional

Each property is a mapping with:

| Key | Required | Default | Notes |
|---|---|---|---|
| `name` | yes | — | 1–100 characters, normalized by the same rules as an id |
| `type` | no | `string` | exactly one of `string`, `number`, `boolean`, `array`, `object` |
| `description` | no | none | up to 500 characters |
| `required` | no | `false` | boolean |

Only `name` and `description` reach the prompt, as the `Properties:` hint line
above — a property with no description contributes its bare name. **`type` and
`required` are declarations, not enforcement**: nothing checks the model's
output against them, and a `required` property that comes back missing is not
an error ([ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md)).
Declare them for your own downstream code, and write a `description` for
anything you actually want the model to look for.

Because properties cost prompt space on every chunk, list the ones that change
what gets extracted rather than every field your storage layer has.

#### `examples` — optional, at most 10

Example entity *names*, for few-shot prompting. More than ten is a load error
("Maximum 10 examples allowed"). Only the **first three** reach the prompt (see
[Step 4](#step-4-turn-the-schema-into-a-system-prompt-with-domain_system_promptschema)),
so put your clearest ones first and treat entries four onwards as documentation
for the next person to edit the file.

The three that ship are doing disambiguation, not enumeration: pick examples
that mark the *edges* of the type — one obvious member and one you would
otherwise have to argue about — rather than three of the same shape.

#### Unknown keys are errors

An entity type is validated with `extra: forbid`, like the top level: `example:`
for `examples:`, or `properties` on a *relationship* type, fails the load rather
than being ignored. See [Step 2](#step-2-know-what-the-validator-will-reject).

### Declare relationship types

Each item in `relationship_types` needs `id` and `description`, and may
constrain its endpoints.

```yaml
relationship_types:
  - id: observed_at
    description: A fault was observed at a site
    valid_source_types: [fault]
    valid_target_types: [site]

  - id: co_occurs_with
    description: Two faults were seen together
    valid_source_types: [fault]
    valid_target_types: [fault]
    bidirectional: true

  - id: related_to
    description: General relationship
    bidirectional: true
```

Those three render into the prompt as:

```text
- **observed_at**: A fault was observed at a site (from: fault; to: site)
- **co_occurs_with**: Two faults were seen together (from: fault; to: fault; bidirectional)
- **related_to**: General relationship (bidirectional)
```

That parenthesis is the whole of what the endpoint constraints do at extraction
time — they are text in the prompt. Nothing downstream drops a triple that
ignores them.

#### `id` — required, 1–100 characters, normalized

Same rules and same normalization as an entity type's id: lowercased,
whitespace stripped, spaces and hyphens turned into underscores, runs of
underscores collapsed, leading and trailing underscores stripped, and the
result must be a valid Python identifier. `Observed At` and `observed-at` both
store as `observed_at`; `2nd_visit` is rejected.

Write the ids as verbs read source-to-target (`observed_at`, `causes`,
`serves`), the way every bundled schema does. The direction is not recorded
anywhere else, so an id like `link` leaves the model with nothing to orient
from.

#### `description` — required, 1–500 characters

Copied verbatim into the prompt after the id. Say which end is which:
"A fault was observed at a site" tells the model the ordering; "Observation"
does not.

#### `valid_source_types` / `valid_target_types` — optional

Lists of entity type ids. **Omitting one, or giving an empty list, means "any
type"** — there is no way to say "no valid source". Give both when you know
both; a relationship with neither renders without a `from:`/`to:` annotation
at all.

Every entry must name an entity type declared **in the same file**. A typo is a
load error naming the offender, not a silently dropped constraint:

```
Relationship 'observed_at' references unknown source type: 'falt'.
Valid types: ['fault', 'site']
```

Entries are normalized before that check, but by a *shorter* rule than an id:
lowercase, strip whitespace, and turn spaces and hyphens into underscores.
Repeated underscores are not collapsed and surrounding ones are not stripped.
So an entity type written `__site__` stores as `site` while a reference written
`__site__` stays `__site__` and fails to load. Write both the declaration and
every reference in normalized form and the difference never arises.

A type may of course appear on both ends — `co_occurs_with` above joins two
faults — and one end may list several types, as `literature_fiction`'s `rules`
does with `valid_target_types: [character, setting]`.

#### `bidirectional` — optional, defaults to `false`

It declares that the direction carries no meaning, and it appears in the prompt
as the word `bidirectional`. **It does not cause a second edge to be written**,
and it does not make `validate_relationship` accept the endpoints reversed:
a `bidirectional` type with `valid_source_types: [fault]` and
`valid_target_types: [site]` still rejects a site-to-fault triple. If both
directions are genuinely legal, say so by listing both types on both ends.

#### The constraints are checkable, but nothing checks them for you

`DomainSchema.validate_relationship(rel_type, source_type, target_type)`
returns `(True, None)` or `(False, message)` against the declarations above,
and `RelationshipTypeSchema.is_valid_source` / `is_valid_target` do one end
each. Nothing in the extraction pipeline calls any of them — they are there for
you to call over extraction output if you want that filter. See
[ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md).

One sharp edge if you do call it: these helpers only lowercase and strip the
type you pass, so hand them ids in normalized form (`access_road`, not
`Access Road`) or a legal endpoint will read as invalid.

#### Declare a `related_to` type

All six bundled schemas end their list with:

```yaml
  - id: related_to
    description: General relationship
    bidirectional: true
```

`related_to` is accepted by `is_valid_relationship_type` whether or not you
declare it — it is the built-in fallback. Declaring it anyway costs two lines
and puts it in the prompt, which gives the model a sanctioned way to say "these
are connected and I cannot say how" instead of inventing a type. Leave its ends
unconstrained.

#### Unknown keys are errors

Relationship types are validated with `extra: forbid` too. `properties:` on a
relationship, or `valid_source_type:` singular, fails the load rather than
being ignored — see [Step 2](#step-2-know-what-the-validator-will-reject).

### Write `extraction_prompt_template`

The template is the prompt. Write it as a YAML block scalar (`|`) and put the
two placeholders where you want the generated type lists to land:

```yaml
extraction_prompt_template: |
  You are reading a field report written by an engineer after a site visit.

  Extract the following types of entities:
  {entity_descriptions}

  Extract the following types of relationships:
  {relationship_descriptions}

  Prefer the engineer's own wording for fault names. Do not infer a fault
  from the absence of a remark about one.
```

With the entity and relationship types declared earlier in this guide,
`domain_system_prompt(schema)` renders that template as:

```text
You are reading a field report written by an engineer after a site visit.

Extract the following types of entities:
- **site**: A physical location a report was written about (examples: Thornbury Substation, Platform 4)
  Properties: region (Operating region the site belongs to), commissioned (Whether the site is in service)
- **fault**: A defect or failure observed during the visit (examples: Bearing overheat, Corroded earth strap)

Extract the following types of relationships:
- **observed_at**: A fault was observed at a site (from: fault; to: site)
- **co_occurs_with**: Two faults were seen together (from: fault; to: fault; bidirectional)
- **related_to**: General relationship (bidirectional)

Prefer the engineer's own wording for fault names. Do not infer a fault
from the absence of a remark about one.
```

The placeholder is replaced by the bullet block alone, with no heading and no
trailing newline, so whatever line you put it on is where the list starts.
Indent the placeholder and only the block's *first* line gets that indent —
put it at the left margin of the template body, as above and as every bundled
schema does.

#### The substitution is two literal string replacements

`domain_system_prompt` calls `str.replace` for `{entity_descriptions}` and then
for `{relationship_descriptions}`. That is the whole templating engine, and
three consequences follow:

- **It is not Jinja and not `str.format`**, despite the field's docstring
  calling it "Jinja2-style". Braces around any other word survive verbatim:
  a template saying `Extract from: {content}` renders with `{content}` still
  in it. There are no other variables — the document text is not interpolated
  here, it is sent separately by the pipeline.
- **Every occurrence is replaced**, not just the first. Naming a placeholder
  twice emits the list twice.
- **Both placeholders are optional.** A template using neither is legal and
  renders as itself. That is a real choice — a domain whose prompt is entirely
  prose — but it means the prompt's type list no longer tracks the
  declarations above it. If a rendered prompt still shows a literal
  `{entity_descriptions}`, you misspelled it; see
  [Placeholders left unfilled](#placeholders-left-unfilled--a-template-with-neither-placeholder-renders-as-itself).

#### What to write around them

The template must be non-empty (`min_length=1`); beyond that its content is
never validated, so the prose is entirely yours and is where the domain
knowledge goes. The bundled schemas all use the same four-part shape, and it
is a good default:

1. one sentence orienting the model — "You are analyzing a news article or
   journalistic content";
2. `{entity_descriptions}` under a heading line;
3. `{relationship_descriptions}` under a heading line;
4. a short `Focus on:` list of what matters in this material, and what not to
   invent.

Say what should *not* be extracted as explicitly as what should. The
declarations give the model a vocabulary; only the prose tells it how to
adjudicate a doubtful case, and that is the difference this section makes to
extraction quality.

Read the rendered prompt once before you ship a schema —
`print(domain_system_prompt(schema))`. It is short, it is exactly what the
model sees, and it is the only place where the template and the generated
lists appear together.

### Optional: override `confidence_thresholds`

```yaml
confidence_thresholds:
  entity_extraction: 0.75
  relationship_extraction: 0.65
```

The block is optional, and so is each key inside it:

| Key | Required | Default | Range |
|---|---|---|---|
| `entity_extraction` | no | `0.6` | `0.0`–`1.0` inclusive |
| `relationship_extraction` | no | `0.5` | `0.0`–`1.0` inclusive |

Set one and let the other default, or omit the whole block to take both. A
value outside the range is a load error, and — like the top level and both
type models — the block is `extra: forbid`, so `entity_threshold:` for
`entity_extraction:` fails the load rather than being ignored.

#### These are declarations, not a filter

Nothing in the library reads them. They are parsed, range-checked, and carried
on the loaded schema as `schema.confidence_thresholds.entity_extraction` and
`.relationship_extraction` — and no extraction, consolidation or projection
code consults either value. Setting `entity_extraction: 0.95` does not drop
low-confidence entities; it records that your domain considers 0.95 the usable
bar. This is the same division as `valid_source_types` and `required` on a
property: the schema states what the domain expects, and enforcement is yours
([ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md)).

Nor do they reach the prompt. `domain_system_prompt` renders only the two type
lists into your template, so a threshold is invisible to the model.

If you want the filter, write it over the extraction output yourself:

```python
threshold = schema.confidence_thresholds.entity_extraction
kept = [e for e in result.entities if e.confidence >= threshold]
```

`ConfidenceThresholds` is exported from `redstring`, so you can construct or
type-annotate one without a dotted import.

#### Choosing values

Raise them when a wrong extraction costs more than a missed one, and leave
them alone when it does not. The bundled schemas show the intended spread:
`literature_fiction` keeps both defaults, `news_journalism` and
`business_corporate` go to `0.75` / `0.65`, and the other three sit at
`0.7` / `0.6`.

Two conventions worth following, both visible in every bundled file: keep
`relationship_extraction` at or below `entity_extraction` — a relationship is
only as certain as its endpoints, and the suite for the bundled schemas checks
this ordering — and keep the gap around 0.1 rather than setting one high and
one low.

Also note the name collision this key does *not* have: the classifier's
`confidence_threshold` (singular), which decides whether `domain=AUTO` accepts
its answer or falls back to `encyclopedia_wiki`, is a constructor argument on
`ContentClassifier` and has nothing to do with this block.

With the file written, the next question is what will make it fail to load.

## Step 2: Know what the validator will reject

Every rule below is enforced by the `DomainSchema` model, so it fires the
moment you load the file — before any model call, and identically for
`load_schema_from_file` and `load_schema_from_string`. A failure raises
`SchemaLoadError` wrapping the underlying pydantic `ValidationError`, and its
message names the offending field by path (`entity_types.0.examples`), so read
the path before the prose.

The four rules in this step are the ones that reject a file you would
otherwise expect to work.

### `extra: forbid` — a misspelled key is an error, not a silent no-op

The top level, every entity type, every relationship type, every property and
the `confidence_thresholds` block are all declared `extra="forbid"`. An
unrecognised key anywhere fails the load:

```yaml
entity_types:
  - id: site
    description: A physical location a report was written about
    exampels:              # typo
      - Thornbury Substation
```

```
Schema validation failed for /srv/schemas/field_reports.yaml: 1 validation error for DomainSchema
entity_types.0.exampels
  Extra inputs are not permitted [type=extra_forbidden, ...]
```

That leading name is the source: `load_schema_from_file` puts the resolved
path there, and `load_schema_from_string` uses `<string>` unless you pass
`source_name=`. Pass it when you load several schemas from memory, or every
failure reads the same.

This is the behaviour you want and the one most likely to surprise you: a
schema loader that ignored unknown keys would accept `exampels:` and give you
an entity type with no examples, and nothing downstream would ever say so.

Three consequences worth knowing before you go hunting:

- **Read the dotted path, not the prose.** `entity_types.0.exampels` names the
  first entry of `entity_types`; the message itself is the same six words for
  every extra key in the file.
- **Misspelling a required key produces two errors, not one** — `missing` for
  the key that is absent and `extra_forbidden` for the one you actually wrote.
  Fix the spelling and both go away.
- **A key valid in one place is still extra in another.** `properties:` belongs
  to an entity type; on a relationship type it is rejected. Likewise
  `valid_source_type:` (singular) and `entity_threshold:` for
  `entity_extraction:`.

The five models are `DomainSchema`, `EntityTypeSchema`,
`RelationshipTypeSchema`, `PropertySchema` and `ConfidenceThresholds` — there
is no level of the file where an extra key is tolerated, and no `x-` escape
hatch for your own metadata. If you want to annotate a schema for your own
tooling, use a YAML comment; comments are discarded by the parser and never
reach validation.

### Ids and property names are normalized, and must be valid identifiers

Four kinds of field are rewritten before they are stored: `EntityTypeSchema.id`,
`RelationshipTypeSchema.id`, each property's `name`, and the entries of
`valid_source_types` / `valid_target_types`. The first three share one
normalization — lowercase, strip surrounding whitespace, spaces and hyphens to
underscores, collapse runs of underscores, strip leading and trailing
underscores — and the result **must be a valid Python identifier**:

| Written | Stored | Outcome |
|---|---|---|
| `Access Road` | `access_road` | loads |
| `fault-code` | `fault_code` | loads |
| `2nd stage` | `2nd_stage` | rejected — an identifier may not start with a digit |
| `fault code!` | `fault_code!` | rejected — not an identifier |
| `___` | (empty) | rejected — "cannot be empty after normalization" |

`domain_id` is the exception: it is pattern-checked against
`^[a-z][a-z0-9_]*$` and **not** normalized, so `Field Reports` is rejected
rather than rewritten.

The error message names both forms — `Entity type ID must be a valid
identifier: '2nd stage' -> '2nd_stage'` — so you can see what the normalizer
made of what you wrote.

Three things this rule does *not* do, all of which can bite:

- **Duplicate ids are not rejected.** Declaring `Site` and `site` as separate
  entity types gives you two types both stored as `site`; the load succeeds,
  the prompt lists the type twice, and `get_entity_type` returns the first.
  Nothing in the model checks for this — check it yourself.
- **The reference lists normalize by a *shorter* rule.** Entries in
  `valid_source_types` / `valid_target_types` are only lowercased, stripped,
  and have spaces and hyphens turned into underscores. Runs of underscores are
  not collapsed and surrounding ones are not stripped, so an entity type
  written `__site__` stores as `site` while a reference written `__site__`
  stays `__site__` and fails the cross-check below. They are not required to
  be identifiers either — the check that catches a malformed entry is the
  cross-reference against declared entity types, nothing else.
- **The lookup helpers normalize by a shorter rule still.** `get_entity_type`,
  `get_relationship_type`, `is_valid_entity_type`, `is_valid_relationship_type`
  and the `is_valid_source` / `is_valid_target` endpoint checks only lowercase
  and strip the argument you pass. `get_property` does the space-and-hyphen
  replacement as well, but no collapsing. So a type stored as `access_road`
  is *not* found by `get_entity_type("Access Road")`, and a legal endpoint
  handed to `validate_relationship` in unnormalized form reads as invalid.

Write every id in its normalized form, in the declaration and in every
reference. That is the one habit that makes all three of the above
unreachable: the prompt, the cross-reference check and the lookup helpers each
apply a *different* amount of normalization, and they agree only on input that
was already normalized. A schema written in mixed case reads as one thing and
behaves as another.

### At least one entity type and one relationship type; at most 10 examples

`entity_types` and `relationship_types` are both required and both
`min_length=1`. An empty list is as much an error as an absent key, and it is
the easier of the two to write by accident — a `relationship_types:` heading
with every entry commented out is an empty list, not a missing one:

```
relationship_types
  List should have at least 1 item after validation, not 0 [type=too_short, ...]
```

A schema with no relationship types is rejected even if you only ever wanted
entities. Declare the `related_to` type described in
[Step 1](#declare-relationship-types) and the requirement costs you two lines.

Neither list has an upper bound, but every entry of both is rendered into the
prompt on every chunk you extract, so a hundred entity types is a hundred
bullet points per call. The limit that matters is a budget you set, not one
the validator enforces.

#### `examples` is capped at 10, and only 3 are used

`examples` on an entity type accepts at most **10** entries; an eleventh fails
the load with `Maximum 10 examples allowed, got 11`. The cap is a hard error,
not a truncation.

Only the **first three** reach the prompt —
`prompt_generator.MAX_EXAMPLES_PER_TYPE` is `3`, and `_entity_line` slices
`examples[:3]` (see
[Step 4](#step-4-turn-the-schema-into-a-system-prompt-with-domain_system_promptschema)).
So the practical rule is: order matters, entries four to ten are documentation
for the next person editing the file, and the gap between 3 and 10 is the only
place in this schema where a value validates, is stored, and is then never
used.

Two smaller edges:

- **`examples` is optional and an empty list is fine.** An entity type with no
  examples simply renders without the `(examples: ...)` clause.
- **Entries are not otherwise validated.** They are plain strings with no
  minimum length and no normalization, so `examples: ["", "Platform 4"]` loads
  and puts an empty first example into the prompt — spending one of your three
  slots on nothing.

Relationship types have no `examples` field at all. Offering one is an
`extra_forbidden` error, per
[`extra: forbid`](#extra-forbid--a-misspelled-key-is-an-error-not-a-silent-no-op).

#### The length limits, in one place

The other rejections in this class are the string bounds:

| Field | Min | Max |
|---|---|---|
| `domain_id` | 1 | 50 |
| `display_name` | 1 | 100 |
| `description` (domain, entity type, relationship type) | 1 | 500 |
| `description` (property) | — (optional) | 500 |
| entity/relationship `id`, property `name` | 1 | 100 |
| `extraction_prompt_template` | 1 | none |

Every required `description` and the template are `min_length=1`, so
`description: ""` fails exactly as omitting the key does — a different error
code (`string_too_short` rather than `missing`), the same outcome. A property's
`description` is the one that may legitimately be absent; writing it as `""`
is still an error.

### `valid_source_types` / `valid_target_types` must name declared entity types

This is the last rule to fire. Every entity type and relationship type has
already validated on its own; only then does a model-level check walk the
relationship types and require each entry in `valid_source_types` and
`valid_target_types` to name an entity type declared **in the same file**. A
typo is a load error naming the offender and listing the alternatives:

```yaml
entity_types:
  - id: fault
    description: A defect or failure observed during the visit
  - id: site
    description: A physical location a report was written about

relationship_types:
  - id: observed_at
    description: A fault was observed at a site
    valid_source_types: [falt]     # typo
    valid_target_types: [site]
```

```
Schema validation failed for /srv/schemas/field_reports.yaml: 1 validation error for DomainSchema
  Value error, Relationship 'observed_at' references unknown source type: 'falt'.
Valid types: ['fault', 'site']
```

The message names the relationship, the offending entry, whether it was a
source or a target, and every declared entity type sorted alphabetically —
enough to fix it without opening the file. Because this check raises from a
model validator rather than a field, the error carries **no dotted path**: it
is attributed to the whole `DomainSchema`, not to
`relationship_types.0.valid_source_types.0`. The relationship id in the text
is what tells you where to look.

Schemas are not merged, inherited, or registered, so "in the same file" is the
whole of the namespace. You cannot reference an entity type from a bundled
schema, and there is no way to extend one — copy the bundled file and edit it
(see [Before you start](#before-you-start)).

Five details of this check:

- **It runs against normalized ids on both sides.** The declared ids were
  normalized by the full rule (underscore collapsing, leading and trailing
  underscores stripped); the references were normalized by the *shorter* rule
  (lowercase, strip, spaces and hyphens to underscores). So an entity type
  written `__site__` is stored as `site` while a reference written `__site__`
  stays `__site__` and fails here, quoting a value you did not type. Writing
  both in normalized form makes the difference unreachable — see
  [Ids and property names](#ids-and-property-names-are-normalized-to-lowercase-underscore-and-must-be-valid-python-identifiers).
- **An empty-string entry is skipped, not rejected.** `valid_source_types:
  ['']` loads, and constrains nothing at all: the check ignores falsy entries,
  and a list of one empty string still reads as "any" to `is_valid_source`,
  which only tests whether the list is empty before comparing. If you meant a
  type, you get silence rather than an error.
- **It stops at the first bad entry.** The validator raises on the offender it
  reaches first — sources before targets, in declaration order — so three
  typos take three loads to find.
- **Duplicates and self-references are fine.** `valid_source_types:
  [fault, fault]` loads, as does a relationship whose source and target are
  the same type (`co_occurs_with` above). Nothing deduplicates the list;
  a repeated entry simply appears twice in the prompt's `from:` annotation.
- **It is the only cross-field check in the model.** Nothing verifies that
  your template mentions the two placeholders, that the thresholds are ordered
  sensibly, that entity type ids are unique after normalization, or that every
  entity type is reachable by some relationship. If you want those, assert
  them in your own test over the loaded schema.

And what it constrains is the *schema*, not extraction. A validated endpoint
list becomes two things: the `(from: fault; to: site)` annotation in the
generated prompt, and an argument to `DomainSchema.validate_relationship` if
you choose to call it. Nothing in the pipeline calls it, and nothing rejects an
extracted triple that ignores the constraint — so this rule buys you a correct
prompt and a checkable declaration, not enforcement
([ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md)).

With the rules known, load the file and see which of them you hit.

## Step 3: Load and validate the schema

Two functions turn YAML into a `DomainSchema`, and both are exported from the
top-level package:

```python
from redstring import load_schema_from_file, load_schema_from_string
```

They share all their validation: `load_schema_from_file` reads the file and
hands the text to `load_schema_from_string`, so every rule in
[Step 2](#step-2-know-what-the-validator-will-reject) fires identically either
way. The only difference is where the bytes come from and what the error
message calls the source.

There is no separate `validate()` step and nothing to register. Loading *is*
validating: either you get a fully-checked `DomainSchema` back, or you get an
exception.

### `load_schema_from_file(path)` for a file on disk

```python
from pathlib import Path

from redstring import load_schema_from_file

schema = load_schema_from_file(Path("/srv/schemas/field_reports.yaml"))
print(schema.domain_id, schema.version)  # field_reports 1.0.0
```

The path may be a `str` or a `Path`. The file is read as UTF-8 and parsed with
`yaml.safe_load`, so no YAML tag can construct a Python object — a schema file
is data, even one you did not write.

**Pass an absolute path.** This is the one surprise in the function: a
*relative* path is not resolved against your working directory. It is resolved
against the bundled schema directory inside the installed package:

```python
# Loads the library's own news_journalism.yaml, wherever you run from.
schema = load_schema_from_file("news_journalism.yaml")

# NOT ./field_reports.yaml -- this looks inside the installed package
# and raises SchemaLoadError: Schema file not found.
schema = load_schema_from_file("field_reports.yaml")
```

That behaviour is deliberate — it is how the bundled six are loaded by id —
but it means a relative path to *your* schema fails with a "not found" naming
a directory you have never heard of. Use `Path(...).resolve()`, or an absolute
path built from your own package root, and the question never comes up.

If you keep a directory of your own schemas and want to name them by file, the
second parameter redirects that resolution:

```python
schema = load_schema_from_file("field_reports.yaml", schema_dir=MY_SCHEMAS)
```

`schema_dir` is ignored for an absolute `file_path`, so it is a convenience for
short names rather than a sandbox — it does not stop a path escaping the
directory.

Three failures come from the file rather than its contents, each with its own
message: the path does not exist (`Schema file not found: ...`), the path is a
directory (`Schema path is not a file: ...`), and the read fails on permissions
or encoding (`Failed to read schema file ...`). All three raise the same
exception type as a validation failure.

### `load_schema_from_string(yaml_text)` for YAML you already hold

Use this when the YAML arrives from somewhere other than the local filesystem —
an object store, a database column, an HTTP response, a config map, or a test
fixture:

```python
from redstring import load_schema_from_string

yaml_text = fetch_schema_body("field_reports", version=3)
schema = load_schema_from_string(yaml_text, source_name="field_reports@v3")
```

The signature is `load_schema_from_string(yaml_content, source_name="<string>")`.
It takes the YAML **text**, not bytes and not a parsed object: decode a
response body yourself, and if you already have a `dict` you do not want this
function at all — call `DomainSchema.model_validate(mapping)` and catch
pydantic's `ValidationError` directly.

This is the function `load_schema_from_file` delegates to once it has read the
file, so the two agree on every rule in
[Step 2](#step-2-know-what-the-validator-will-reject) by construction rather
than by convention. Parsing is `yaml.safe_load`, here as there — no YAML tag
can construct a Python object, which matters more for a string arriving over
the network than for a file you wrote.

#### Always pass `source_name`

`source_name` is used only in error messages, and it defaults to `<string>`.
Pass it anyway. It is the first thing in every failure message this function
raises:

```
Schema validation failed for field_reports@v3: 1 validation error for DomainSchema
entity_types.0.exampels
  Extra inputs are not permitted [type=extra_forbidden, ...]
```

Loading several schemas from memory without it gives you a run of failures
that all begin `Schema validation failed for <string>`, with nothing but the
dotted field path to say which input broke — and the paths collide too, since
every schema has an `entity_types.0`. The value is free-form and never parsed,
so use whatever identifies the source in your own system: a row id, a URL, an
object key, a name and version like the example above.

#### Two rejections belong to this function

Both fire before any of your declarations are looked at, and neither can
happen to a `dict` you validate directly:

- **Empty content.** Text that is only comments, only whitespace, or genuinely
  empty parses to `None`, and you get `Empty YAML content in field_reports@v3`
  rather than a pile of missing-key errors. This is the message that means your
  fetch returned nothing — check the source before you check the schema.
- **A top level that is not a mapping.** The error names the type it got:
  `YAML content must be a mapping, got list in field_reports@v3`. A leading
  `- domain_id: ...` gives `list`; a fragment that is just a bare word gives
  `str`. Both are one structural mistake — a schema is a single mapping with
  no wrapper, as [Step 1](#step-1-write-the-yaml) describes.

Invalid YAML syntax raises before either, carrying the parser's own message
with the line and column: `Invalid YAML syntax in field_reports@v3: ...`.

All three raise `SchemaLoadError`, the same type as a validation failure — see
[Validating ahead of time](#validating-ahead-of-time-call-the-loader-and-catch-the-load-error).
Only the empty-content and non-mapping cases arrive with `cause` set to
`None`; a parse failure carries the `yaml.YAMLError` and a validation failure
the pydantic `ValidationError`.

#### Round-tripping a schema you already loaded

`DomainSchema` is a pydantic model, so a loaded schema serialises back out and
reloads — useful for storing the normalized form rather than the text someone
typed:

```python
import yaml

canonical = yaml.safe_dump(schema.model_dump(mode="json"), sort_keys=False)
assert load_schema_from_string(canonical, source_name="canonical") == schema
```

The reloaded schema is equal, not identical, and the text is not: ids come
back in their normalized form and every default is written out explicitly, so
`version: 1.0.0` and the full `confidence_thresholds` block appear whether or
not you wrote them. Keep the authored file as the thing humans edit.

### Validating ahead of time: call the loader and catch the load error

To check a schema in a test, a CI step, or an admin command, load it and catch
the failure. Every failure above — parse error, empty file, non-mapping, file
missing, unreadable, and every validation rule in Step 2 — raises the single
type `SchemaLoadError`:

```python
from redstring import load_schema_from_file
from redstring.extraction.domains.loader import SchemaLoadError

try:
    schema = load_schema_from_file("/srv/schemas/field_reports.yaml")
except SchemaLoadError as exc:
    print(exc)              # the full message, source name first
    print(exc.file_path)    # Path to the source, or None
    print(exc.cause)        # the underlying YAMLError / ValidationError, or None
```

`SchemaLoadError` is **not** part of the exported surface: `redstring.__all__`
carries the two loader functions and `DomainSchema`, but not the exception, so
catching it needs the dotted import above. That import reaches into an internal
module and is not covered by the public-API promise — if you would rather not
depend on it, catch `Exception` at the boundary and treat any failure as "this
schema does not load", which is the only distinction the type gives you anyway.
It derives from `Exception`, not from `RedstringError`, so a handler written
for the library's own error hierarchy will not catch it.

The two attributes are less useful than they look. `cause` is `None` for the
empty-content, non-mapping, file-not-found and not-a-file cases, and set only
for a parse failure (`yaml.YAMLError`), a validation failure
(`ValidationError`) and an unreadable file (`OSError`). And `file_path` is
always a `Path`, even when there was no file: `load_schema_from_string` passes
`source_name` straight through, so an unnamed string load leaves you with
`Path('<string>')`. Treat `file_path` as an echo of the source name, not as a
path you can open.

Read the message in three parts:

1. **The source name**, first in the message — the resolved path from
   `load_schema_from_file`, or your `source_name` (default `<string>`).
2. **The dotted field path**, on its own line for a validation failure:
   `entity_types.0.exampels` is the first entry of `entity_types`. The
   cross-reference check in
   [Step 2](#valid_source_types--valid_target_types-must-name-declared-entity-types)
   is the exception — it raises from a model validator and carries no path,
   naming the relationship in its text instead.
3. **`exc.cause`**, the original exception. For a validation failure this is
   pydantic's `ValidationError`, whose `.errors()` gives you the failures as
   structured dicts (`loc`, `msg`, `type`) rather than as prose. Use that if
   you are reporting them somewhere other than a terminal.

A validation failure reports **every** field error at once, so a file with four
problems takes one load to diagnose, not four. The exceptions are the ordered
ones: a parse error stops at the first syntax problem, and the endpoint
cross-check stops at the first bad entry.

#### A boolean form, if you are writing a lint command

`validate_schema_file(path)` wraps exactly the try/except above and returns
`(True, None)` or `(False, message)`:

```python
from redstring.extraction.domains.loader import validate_schema_file

for path in sorted(Path("/srv/schemas").glob("*.yaml")):
    ok, message = validate_schema_file(path)
    if not ok:
        print(f"FAIL {path}: {message}")
```

It is convenient for a checker that reports on several files without stopping
at the first, and it costs nothing over the loader — it *is* the loader, and
the schema it built is discarded. Its docstring says it validates "without
fully loading"; it does not, so do not reach for it expecting a cheaper check.
Like `SchemaLoadError`, it is not exported from `redstring`, and it collapses
`cause` and `file_path` into a string. When you want the schema anyway, call
the loader.

Worth pinning in your own suite, once per schema you own:

```python
def test_field_reports_schema_loads() -> None:
    schema = load_schema_from_file(SCHEMA_PATH)
    assert {e.id for e in schema.entity_types} == {"site", "fault"}
```

Asserting the loaded ids, not just that the call returned, is what catches the
things the validator does not: a second declaration that normalized onto an id
you already had (`Site` and `site` are two types, both stored as `site`), and
an entity type you renamed and now nothing references. Both leave a file that
loads perfectly.

With a `DomainSchema` in hand, turn it into a prompt.

## Step 4: Turn the schema into a system prompt with `domain_system_prompt(schema)`

One function turns a schema into the string a model is told before it sees a
chunk:

```python
from redstring import domain_system_prompt

prompt = domain_system_prompt(schema)
```

That is the whole join between `domains/` and extraction. It takes a bundled
domain id or a `DomainSchema`, returns a `str`, and has no other parameters,
no state, and no side effects — calling it twice with the same schema gives
the same string.

Print it before you ship a schema. It is short, it is exactly what the model
sees, and it is the only place your template and the generated type lists
appear together.

### Passing the `DomainSchema` object rather than a domain id — no registration step

`domain_system_prompt(domain: str | DomainSchema)` branches on the type of its
argument and nothing else:

```python
prompt = domain_system_prompt("news_journalism")   # bundled id: registry lookup
prompt = domain_system_prompt(schema)              # your object: used directly
```

The object form is why there is no registration step. A schema you loaded
yourself is passed straight in — it is never looked up, so it does not need a
name the library knows, does not need to live in the package's schema
directory, and does not collide with a bundled id if you reuse one. Nothing
mutates the registry and nothing caches your schema; the id path is the only
one that touches the registry at all.

Two consequences of the branch being on *type*:

- **Only a `str` is a lookup.** Passing your `DomainSchema` never raises
  `UnknownDomainError`, because no id is involved.
- **The id form only ever finds the bundled six.** The registry loads the
  package's own `schemas/` directory, so `domain_system_prompt("field_reports")`
  raises `UnknownDomainError` however many times you have loaded your file.
  Load it and pass the object; there is no third option.

Ids are matched case-insensitively with surrounding whitespace stripped, so
`"News_Journalism"` and `" news_journalism "` both resolve. An unknown one
raises `UnknownDomainError`, which is a `RedstringError` and lists the ids that
do exist — see
[Troubleshooting](#unknowndomainerror--you-passed-an-id-not-a-schema-or-misspelled-a-bundled-id).

Because the argument is just an object, a schema built in code works as well as
one loaded from YAML — useful in tests, where constructing a two-type
`DomainSchema` directly is quicker than a fixture file:

```python
from redstring import DomainSchema

prompt = domain_system_prompt(
    DomainSchema(
        domain_id="tiny",
        display_name="Tiny",
        description="A schema built in code",
        entity_types=[{"id": "site", "description": "A location"}],
        relationship_types=[{"id": "related_to", "description": "General relationship"}],
        extraction_prompt_template="Types:\n{entity_descriptions}",
    )
)
```

The same validation runs either way — `DomainSchema` is a pydantic model, so
constructing one applies every rule in
[Step 2](#step-2-know-what-the-validator-will-reject), raising pydantic's
`ValidationError` rather than `SchemaLoadError`.

### What the generated prompt contains, including the three-example cap per entity type

The returned string is your `extraction_prompt_template` with two literal
substitutions performed on it, and nothing else. No preamble is prepended, no
instruction is appended, and no default prompt is merged in: text you did not
write does not appear, and text you did write is never removed.

`{entity_descriptions}` becomes one bullet per entity type, in declaration
order:

```text
- **<id>**: <description> (examples: <first>, <second>, <third>)
  Properties: <name> (<description>), <name>
```

- The **id is the normalized one** and is wrapped in `**`.
- The **description is verbatim**.
- The `(examples: ...)` clause appears only if the type has examples, and
  carries at most the **first three** — `MAX_EXAMPLES_PER_TYPE` is `3`. A
  schema may declare up to ten ([Step 2](#at-least-one-entity-type-and-one-relationship-type-at-most-10-examples-per-type)); entries
  four onwards are stored, are never rendered, and are documentation for the
  next person editing the file. Order the list accordingly. The slice is
  positional and unfiltered, so an empty string in one of the first three
  slots spends a slot on nothing.
- The `Properties:` line appears only if the type declares properties, on its
  own line indented by two spaces. Each property renders as `name
  (description)`, or a bare `name` when it has no description. **The declared
  `type` and `required` flag do not appear** — the model is told a property's
  name and meaning, never that it is a boolean or that you consider it
  mandatory.

`{relationship_descriptions}` becomes one bullet per relationship type, again
in declaration order:

```text
- **<id>**: <description> (from: <types>; to: <types>; bidirectional)
```

The parenthesis holds whichever of the three parts apply, joined by `; `, and
is omitted entirely when a type constrains nothing. Endpoint lists are
comma-joined in the order you wrote them, duplicates included. Relationship
types have no examples and no properties, so those never appear here.

Neither block is wrapped in a heading, a blank line, or a trailing newline: a
placeholder is replaced by the bullets alone, so the surrounding layout is
whatever your template says. The cap of three is the only place the generated
prompt is smaller than the schema — everything else you declare is rendered in
full, on **every chunk of every document**, which is the budget to keep in mind
when a schema grows past a dozen types.

The whole rendered result, for the `field_reports` schema built up through this
guide, is the block shown under
[Write `extraction_prompt_template`](#write-extraction_prompt_template-with-the-entity_descriptions-and-relationship_descriptions-placeholders).

What the prompt does *not* contain is as worth knowing: not `domain_id`,
`display_name`, `description` or `version`, and not `confidence_thresholds`.
Those are schema metadata for your code to read; only the two type lists and
your own prose ever reach the model. And nothing here constrains the model's
output — the prompt asks, the wire format is `Extraction`, and an entity typed
with something you never declared comes back intact
([ADR 0007](../adr/0007-domain-schemas-prompt-but-do-not-constrain.md)).

With a prompt in hand, hand it to extraction.
