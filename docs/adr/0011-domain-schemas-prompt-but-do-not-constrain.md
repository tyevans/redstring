# ADR 0011: Domain schemas prompt the model, they do not constrain it

**Status:** accepted, slice 10 of the ring migration (classifier half amended in the slice 11 fix round).

**Why this is an ADR:** two decisions currently argued only in module
docstrings — `extraction/prompt_generator.py` and `extraction/classifier.py`.
Both are decisions a reader will otherwise rediscover from behaviour: that a
domain schema is advice rather than a constraint, and that the classifier
answers even when it has nothing to answer with. A docstring is read by
someone already in the file; neither of these is discovered from inside the
file it lives in.

## Context: a domain schema was never wired to extraction

### What `DomainSchema` actually carries

`extraction/domains/models.py` defines a schema as a `domain_id`, a display
name and description, a list of `EntityTypeSchema` (each with an `id`, a
`description`, `properties`, and `examples`), a list of
`RelationshipTypeSchema` (`id`, `description`, `valid_source_types`,
`valid_target_types`, `bidirectional`), confidence thresholds, a `version`,
and an `extraction_prompt_template`. Six such schemas ship as YAML under
`extraction/domains/schemas/` — `academic_research`, `business_corporate`,
`encyclopedia_wiki`, `literature_fiction`, `news_journalism`,
`technical_documentation`. See
[the YAML reference](../reference/domain-schema-yaml.md) for the file format
and [the how-to](../how-to/author-a-domain-schema.md) for writing one.

Every one of those fields is prose or a list of identifiers. Nothing in the
type is machine-enforced against extraction output.

### What the port accepts: `LlmProvider.extract(prompt, <pydantic class>, system_prompt=...)` — a class, not a schema dict

`LlmProvider.extract` — the single non-store port's single method (see
[ADR: the two non-store ports](0008-the-two-non-store-ports.md)) — has this
signature, and the three parameters are the whole surface a domain could
travel through:

```python
async def extract[S: BaseModel](
    self,
    text: str,
    schema: type[S],
    *,
    system_prompt: str | None = None,
) -> S: ...
```

`schema` is a **pydantic class**, and the LangChain adapter turns it into the
`response_format` it decodes against itself. A caller has no way to hand the
provider a schema *document*: there is no `dict` parameter, and `type[S]` will
not accept one. Adding such a parameter would be a change to the port, which
is exactly the scrutiny that change deserves.

`text` is the content, and the pipeline passes a chunk of the document to it —
so that is not a place to put a domain either.

So a `DomainSchema` has exactly one way into a model call: `system_prompt`, a
string. The port's docstring is explicit that a provider supplies no default
of its own — prompts are extraction's business — which is what leaves the slot
free for a domain to fill.

### The join that landed: `domain_system_prompt(domain) -> str`, passed to `ExtractionPipeline(provider, system_prompt=...)`

`ExtractionPipeline` had taken a `system_prompt` argument since slice 6 —
defaulting to `DEFAULT_SYSTEM_PROMPT`, a general-purpose instruction that
mentions no domain — and the six YAML schemas had been in the tree longer than
that. Nothing connected them; BACKLOG B55 said so. Slice 10 added one function,
`extraction.prompt_generator.domain_system_prompt`, and the join is a single
keyword argument:

```python
pipeline = ExtractionPipeline(provider, system_prompt=domain_system_prompt("news_journalism"))
```

That is the whole wiring. `domain_system_prompt` takes a domain id **or** a
`DomainSchema` and returns a `str`; `ExtractionPipeline` stores it and passes
it to `LlmProvider.extract` on every chunk. No type crosses the boundary, so
nothing about the pipeline had to change to accept a domain.

`build_graph(..., domain="news_journalism")` does the same thing for callers
who want the composed path
([ADR: composition is the only top layer](0007-composition-is-the-only-top-layer.md)):
`composition.py` resolves `domain` to a prompt before constructing the
pipeline. Its parameter is `str | DomainSchema | AutoDomain | None`, and the
four cases map onto exactly the join described here — `None` leaves
`DEFAULT_SYSTEM_PROMPT` in place and makes no classification call, an id or a
schema is handed straight to `domain_system_prompt`, and `AUTO` classifies the
document first and then does the same with the answer.

`AUTO` is a sentinel object rather than the string `"auto"`, because `domain`
also takes domain ids and a schema legitimately called `auto` would otherwise
be unreachable. See [the README](https://github.com/tyevans/redstring/blob/main/README.md) for the end-to-end shape of a
`build_graph` call.

## Decision 1: the domain reaches the model as description only

A selected domain changes exactly one thing about a model call: the
`system_prompt` string. It does not change the class the provider decodes
against, which is `extraction.schema.Extraction` on every chunk of every
document, for every domain and for none —
`ExtractionPipeline.extract` calls
`self._provider.extract(chunk.text, Extraction, system_prompt=self._system_prompt)`
and there is no branch on domain anywhere in that path.

So the domain's entity types and relationship types are *described* to the
model, in prose, as part of the instruction; they are never *encoded* into the
shape the answer must satisfy. The schema is advice. The model is free to
ignore it, and if it does, nothing downstream notices: `map_extraction` will
map an out-of-vocabulary `entity_type` as readily as an expected one.

That is a decision, not an omission — the alternative was written, and is
[rejected below](#rejected-alternative-generate_json_schema-deleted-and-could-not-have-worked)
for reasons that start with "there was nowhere to pass it". What follows is
what the description actually contains and what the choice costs.

### `domain_system_prompt` renders `{entity_descriptions}` and `{relationship_descriptions}` into the schema's own template

`extraction/prompt_generator.py` holds one public function, and its body is
one expression:

```python
def domain_system_prompt(domain: str | DomainSchema) -> str:
    schema = _schema_for(domain) if isinstance(domain, str) else domain
    return schema.extraction_prompt_template.replace(
        "{entity_descriptions}", _entity_descriptions(schema)
    ).replace("{relationship_descriptions}", _relationship_descriptions(schema))
```

The parameter takes a domain id **or** a `DomainSchema` already in hand. The
id is the common form; the object form exists so a caller with a schema of its
own — loaded from its own YAML, or built in code — is not forced to register
it globally before it can prompt with it. The two paths are the same function
after the first line, and a test pins that:
`domain_system_prompt(schema) == domain_system_prompt("news_journalism")` for
the registered schema of that id.

What comes back is the schema's own `extraction_prompt_template` — a required
field, `min_length=1`, authored per domain in YAML — with two named
placeholders substituted. The template owns the prose: the ordering, the
framing, any domain-specific instruction about what to ignore. This function
contributes only the two rendered type lists and puts them exactly where the
author asked for them. There is no wrapper text, no preamble, and no
general-purpose instruction folded in around the result; whatever
`DEFAULT_SYSTEM_PROMPT` says is gone the moment a domain is selected, replaced
rather than appended to.

The substitution is `str.replace`, not a format call. That matters for a
reason the templates themselves demonstrate: they contain other braced tokens
(`{content}` appears in the models module's own doctest schema), and
`str.format` would raise `KeyError` on every one of them. `replace` sees only
the two substrings it is given and leaves every other brace alone — which is
also what makes
[a template with neither placeholder legal](#a-template-with-neither-placeholder-is-legal-and-renders-as-itself).

The two replacements are separate calls over one string, so each list lands at
its own placeholder rather than both arriving in a block; the test suite
asserts an entity id appears at the entity placeholder's offset and not the
relationship one.

Everything else the schema carries — `domain_id`, `name`, `description`,
`confidence_thresholds`, `version` — reaches no model. It is registry and
routing metadata. Only the two rendered lists and the author's own template
text are said out loud.

The error path is the other thing this function does. A domain id with no
schema raises `UnknownDomainError` listing the ids that do exist, translating
the registry's bare `KeyError` at the public boundary — see
[Enforcement and boundary behaviour](#unknowndomainerror-translates-the-registrys-bare-keyerror-and-lists-available-domains).

### What the rendering commits to: `MAX_EXAMPLES_PER_TYPE = 3`, property hints, endpoint constraints

The two rendered lists are the only place a domain's *content* reaches a
model, so what they include and what they leave out is the substance of
Decision 1. Both are markdown bullet lists, one bullet per type, in the order
the schema declares them.

**Entity types.** Each contributes `- **id**: description`, followed by
`(examples: ...)` when the type has any, followed by an indented `Properties:`
line when it has any:

```
- **character**: A person or being in the narrative (examples: Hamlet, Lady Macbeth)
  Properties: role (Role in story), allegiance (Faction)
- **setting**: Where the narrative takes place
```

Three commitments are visible there, and each is a decision rather than a
formatting accident:

- **At most `MAX_EXAMPLES_PER_TYPE = 3` examples**, taken from the front of
  the schema's list. `EntityTypeSchema` permits up to ten and the constant is
  three, so the cap bites on real schemas. All of them is not better: examples
  exist to disambiguate a type, and a type spending twenty lines on itself
  reads to a model as emphasis rather than as illustration — the effect on a
  prompt is to weight that type over its siblings, which is not what an author
  listing examples was asking for. Because the slice is taken from the front,
  an author who cares which three survive controls it by ordering the YAML.
- **A property contributes its name, and its description in parentheses when
  it has one** — `role (Role in story)` versus a bare `role`. The property's
  `type` (`string`, `number`, `boolean`, `array`, `object`) and its `required`
  flag do **not** reach the prompt. That follows from the ADR's premise: those
  two fields are only meaningful if something validates the answer against
  them, and nothing does. Saying "required" to a model that is not held to it
  states a constraint the system will not enforce, which is the failure this
  ADR is trying not to accumulate.
- **An absent thing renders as nothing, not as an empty shell.** A type with
  no examples gets no `(examples: )`, and one with no properties gets no
  `Properties:` line at all. Both are pinned by tests asserting the empty
  spelling is absent, not merely that the populated one is present.

**Relationship types.** Each contributes `- **id**: description` plus its
endpoint constraints in a single parenthesis, semicolon-separated, in a fixed
order — `from: ...`, `to: ...`, `bidirectional`:

```
- **loves**: Romantic love (from: character; to: character)
- **related_to**: General relationship (bidirectional)
```

`valid_source_types` and `valid_target_types` are omitted when empty, which
`RelationshipTypeSchema` documents as meaning "any", and `bidirectional`
appears only when true. A relationship constraining nothing gets no
parentheses — again pinned by asserting `"()"` does not occur.

**These constraints are stated, not checked.** `DomainSchema` carries the
machinery to enforce them — `is_valid_source`, `is_valid_target`,
`validate_relationship`, `is_valid_entity_type` — and nothing on the
extraction path calls any of it. A model told `loves (from: character; to:
character)` may return `loves` between two settings, and `map_extraction` will
map it. The methods are validated against the *schema itself* at load time
(the model validator rejects a relationship naming an entity type the domain
does not declare), so the constraints are internally consistent; they are just
never turned on the output.

That asymmetry is the decision, and the reason it is defensible is that a
domain-aware validation pass is a separate question with its own open
consequences — whether an out-of-vocabulary type is a finding to record or a
defect to reject. That question is BACKLOG B57, not this ADR.

### A template with neither placeholder is legal and renders as itself

A domain whose prompt is entirely prose is a domain whose author decided the
type list was not worth the tokens — the types exist for the registry and for
whatever validation is added later, and the prompt says something else
entirely. That is allowed:

```python
DomainSchema(..., extraction_prompt_template="Just extract whatever you find.")
```

renders as exactly `"Just extract whatever you find."`, and
`tests/unit/extraction/test_prompt_generator.py::TestTemplateEdgeCases::test_a_template_with_no_placeholders_renders_as_itself`
asserts that equality.

It needs no special case and gets none. `str.replace` on an absent substring
returns the string unchanged, so the two calls run and contribute nothing;
there is no `if "{entity_descriptions}" in template` anywhere in the module.
The same property makes **one** placeholder legal on its own: a template
naming only `{entity_descriptions}` gets its entity list and simply never
mentions relationships. Nothing rejects that either.

The reason this is worth stating in an ADR is that the obvious alternatives
both make it an error. `str.format` would raise `KeyError` on any other braced
token in the template — and the templates do carry them — and a validator
requiring both placeholders in `extraction_prompt_template` would be a natural
thing for someone to add to `DomainSchema` on the grounds that a template not
using its inputs looks like a mistake. It is not a mistake, and the field's
only constraint is `min_length=1`.

What the shape costs is worth being explicit about, because it is the same
cost as the rest of Decision 1 in miniature: a domain that renders no type
list tells the model nothing about its vocabulary, and *still* decodes against
`Extraction` with a bare-`str` `entity_type`. So it is not a way to opt out of
being constrained — nothing was constraining — it is a way to opt out of being
*asked*. Every one of the six shipped schemas uses both placeholders; see
[the how-to](../how-to/author-a-domain-schema.md) for when writing your own
template is the right move.

### The decoding schema still comes from `extraction.schema.Extraction`, whose `entity_type` is a bare `str`

This is the title of the ADR, stated as a fact about one line of code.
Whatever domain was selected, `ExtractionPipeline.extract` calls the port with
the same class:

```python
result = await self._provider.extract(chunk.text, Extraction, system_prompt=self._system_prompt)
```

`Extraction` is a module-level import in `pipeline.py`, not a parameter, not a
field, and not a lookup on anything domain-shaped. There is no branch on domain
between the pipeline and the provider.

And what that class says about a type is one line:

```python
entity_type: str = Field(description="What kind of thing this is, e.g. Person, Place, Concept")
```

A bare `str` with a description and three off-hand examples — **not** the
domain's examples, and not an `enum`. `ExtractedRelationship.relationship_type`
is the same shape. The JSON Schema the adapter derives from `Extraction` is
what a structured-decoding server actually enforces, and it enforces that the
field is a string of any content whatsoever.

So the two halves of a model call disagree about how much they know. The
system prompt has just described `character`, `setting` and `event` in careful
prose with examples and property hints; the decoding schema, which is the only
part with teeth, has never heard of them. A model told about `character` and
`setting` may return `Gadget`, and the extraction succeeds — not degrades,
not warns. Downstream, `map_extraction` builds an `Entity` from it exactly as
it would from `character`: `Entity.entity_type` is also a bare `str`, and
nothing between the provider and the emitted `DocumentExtracted` compares a
type against a vocabulary.

Two consequences are worth naming, because neither is visible from the
extraction call site.

**An out-of-vocabulary type is not inert — it participates in identity.**
`entity_id_for` seeds its `uuid5` chain with `entity_type`:

```python
within_tenant = uuid5(tenant_id, source_id)
within_document = uuid5(within_tenant, entity_type)
return uuid5(within_document, normalize_name(name))
```

So a model that says `Gadget` for one chunk and `gadget` for the next produces
two entities with different ids for one thing, and merging within the document
will not join them — the merge is keyed on that id. Type drift becomes
duplicate nodes rather than a validation error, which is a much quieter
failure than the one a constrained schema would have produced.

**The schema knows how to check, and is never asked.** `DomainSchema`
implements `is_valid_entity_type`, and it is even lenient in an interesting way
— it lowercases and strips, and admits the literal `"custom"` as an escape
hatch. Nothing on the extraction path calls it. That is the asymmetry this
ADR exists to record: the vocabulary is enforceable, and the decision was to
describe it rather than enforce it.

The reason is the one from the port: `LlmProvider.extract` takes a pydantic
class, and there is exactly one such class in `extraction/schema.py`. Making
the class depend on the domain means building it per domain at runtime and
threading it through the pipeline, which is a real change to two modules'
signatures and to what `map_extraction` is generic over. That work is
[BACKLOG B57](#the-open-question-is-recorded-as-backlog-b57-not-closed-by-this-adr),
and this ADR records that it is not done — not that it should not be.

What the field descriptions in `Extraction` *do* carry is worth knowing while
reading this, because it is the same lever from the other end: those
descriptions are part of the JSON Schema and therefore reach the model.
`Extraction`'s own docstring says so — they are prompt, not documentation, and
editing one changes extraction output. The domain has no way to edit them.

### Why the return type is `str`: no new type means nothing to migrate when constrained decoding arrives

`domain_system_prompt(domain: str | DomainSchema) -> str` returns the most
ordinary type in the language, and that is deliberate. The obvious alternative
was to return a small object — an `ExtractionStrategy`, a `DomainPrompt`, a
pair of prompt-and-schema — on the grounds that constrained decoding is coming
and the return value will need somewhere to put a schema. The argument against
it is that this project has already paid for exactly that shape once.

`extraction/strategy_router.py`, deleted in slice 10, was that design.
`ExtractionStrategyRouter.route(job, content, *, tenant_id=None)` returned an
`ExtractionStrategy`, and the router held a classifier, a prompt generator, a
registry, a confidence threshold and a `job_update_callback` so it could write
classification results back onto the job it was passed. When `ScrapingJob`
went — it was a persistence type, and the `services`/`models`/`db` layer went
with it in slice 9 — none of that survived contact. The router did not need
adapting; it needed deleting, because its whole surface was phrased in a
vocabulary that no longer existed. The strategy object went with it: its only
consumer was the router, and its only content was what to say to the model.

A `str` cannot acquire that problem. It names nothing about jobs, tenants,
callbacks or registries, so there is no shape for a future deletion elsewhere
to invalidate. It is also already the type the port wants: `system_prompt` on
`LlmProvider.extract` is `str | None`, and `ExtractionPipeline` stores a
`str`, so the value crosses two boundaries without a wrapper, an adapter, or
an `__init__` on either side. Nothing in the pipeline had to change to accept
a domain, which is the practical form of "there is nothing to migrate".

The migration this section is named for is
[BACKLOG B57](#the-open-question-is-recorded-as-backlog-b57-not-closed-by-this-adr),
and it is worth being concrete about what it does and does not touch. B57's
first option builds a per-domain pydantic model at runtime
(`pydantic.create_model` with `entity_type: Literal[...]`) and threads it
through `ExtractionPipeline` as a **schema** argument. That is a second value
travelling a second path — the `schema` parameter of `LlmProvider.extract`,
which today always receives `Extraction`. It sits beside the prompt rather
than replacing it: a constrained run still wants the descriptions, the
examples and the property hints, because an `enum` says *which* labels are
legal and says nothing about when to apply them. So the function that produces
the prompt is unaffected either way, and so is every caller that holds its
result. B57's second option — a validation pass after extraction — does not
touch this function at all.

Had the same information been returned as an object, adding the schema would
mean changing a published type: `domain_system_prompt`'s return is public
surface ([ADR 0006](0006-the-public-surface-is-gated.md)), and the export gate
pulls in the closure of every type an exported signature mentions, so a
`DomainPrompt` would have obliged its own export and its own fields' export
with it. Adding a field to that is a change to the promise. Adding a second
function that returns a schema is not.

The cost of the choice is real and small: a caller wanting both a prompt and a
domain-derived schema will make two calls and hold two values, rather than one
call returning a bundle. That is the correct trade while the second value does
not exist — and if it ever does, a bundling function can be added over the two
without disturbing either.

## Rejected alternative: `generate_json_schema` (deleted, and could not have worked)

The tree used to contain something that looked exactly like the constraint
this ADR declines to impose. `DomainPromptGenerator.generate_json_schema`
built a hand-rolled JSON Schema `dict` from a `DomainSchema`, with the
domain's entity type ids as an `enum` — plus `"custom"` appended as an escape
hatch, and `"related_to"` appended to the relationship enum when the domain
did not declare it. It is worth reading this section as being about a
*plausible* piece of code: it had a docstring describing constrained
extraction, it had tests, and its name says what a reader wants.

It is deleted, and the reason is not that constraining is wrong. It is that
this particular function could not have constrained anything.

### There was no parameter to pass a `dict` to

`LlmProvider.extract(text, schema, *, system_prompt)` takes `schema` as
`type[S]` bound to `BaseModel` — a pydantic **class**. A `dict` is not one and
will not be accepted by it. There is no other parameter on the port, and no
other way into a model call, so a JSON Schema document built anywhere in this
library has nowhere to go. `generate_json_schema` returned
`dict[str, Any]`, so its return type alone rules out the only destination that
would have made it a constraint.

The call graph shows what happened instead of it being sent. Its callers were
its own module-level convenience wrapper (`generate_output_schema`), its own
docstring's usage example, its tests, and — until slice 10 deleted the router
one commit earlier — `ExtractionStrategyRouter._build_strategy`, which called
it beside `generate_system_prompt` and put both on an `ExtractionStrategy`:

```python
system_prompt = generator.generate_system_prompt(schema)
json_schema = generator.generate_json_schema(schema)
```

That is the shape to recognise, because it is not "dead code". The dict was
built, carried on a returned object, and logged about; what never happened was
the last step, where something hands it to a provider. No such call existed,
because no parameter existed to make it — the router's own consumers were its
tests. So this was never a feature that regressed. It was a feature that had
not been connected, in a shape that could not be connected without changing a
port.

The docstring is the load-bearing detail for anyone auditing similar code. It
said the function "Generate[s] JSON schema for structured LLM output" and
returned a "JSON Schema dict for LLM response validation" — a claim about a
validation step that nothing performed. A function's own documentation is the
weakest available evidence that it does anything, and this is the local proof:
the description was accurate about the dict's *contents* and wrong about its
*role*, which is the combination no reader catches.

The shape is recoverable from `6058746^` — the commit that deleted it wired
`domain_system_prompt` in its place — if a future constrained-decoding attempt
wants the enum construction to start from. Nothing in the current tree
references it, and [B57](#the-open-question-is-recorded-as-backlog-b57-not-closed-by-this-adr)
describes why the replacement is a generated pydantic class rather than this
dict.

### It described a second, disagreeing wire shape: `type`/`source`/`target` versus `entity_type`/`source_name`/`target_name`

Grant it the parameter it never had, and obeying it would still have broken
extraction. Put the two shapes side by side. The deleted dict, per entity:

```python
"name": {"type": "string"},
"type": {"type": "string", "enum": entity_type_enum},
"description": {"type": "string"},
"confidence": {"type": "number", "minimum": 0, "maximum": 1},
"properties": {"type": "object"},
# required: ["name", "type"]
```

`ExtractedEntity`, per entity: `name`, **`entity_type`**, `description`,
`confidence`, `properties`, `temporal_expression`, `sequence_position`. And
per relationship the dict said `source`/`target`/`type`, where
`ExtractedRelationship` says **`source_name`**/**`target_name`**/
**`relationship_type`**.

Those are the names with teeth. `extraction.mapping` reads
`candidate.entity_type` when it seeds the `uuid5` chain that becomes an
`Entity` id, and reads `stated.source_name` and `stated.target_name` when it
resolves each stated relationship's endpoints back to entities it already
mapped. A model that answered the deleted schema exactly would have produced a
completion that fails pydantic validation on `Extraction` — the four renamed
fields are the four with no default, precisely because a nameless or typeless
entity is not a partial answer — so the outcome is `MalformedCompletionError`,
not a degraded extraction. The renaming is not cosmetic drift; it is the
difference between a completion the pipeline can use and one it discards.

The disagreement is not only in names. The dict has no `temporal_expression`
and no `sequence_position`, so a model constrained by it could not report a
date or an ordering even where the text states one, and the temporal pipeline
downstream would see nothing to work with. It also gives relationships no
`properties`, which `ExtractedRelationship` does carry. Two fields the model
is asked for in one specification are unaskable in the other — and nothing
would have reported that, because only one of the two was ever sent.

The `"custom"` and `"related_to"` fallbacks are the part worth carrying
forward, because they are a real design question rather than a defect. The
function appended `"custom"` to the entity enum unconditionally and
`"related_to"` to the relationship enum when the domain did not already
declare it. That is an admission built into the constraint itself: an enum
turns everything the domain author did not anticipate into either a wrong
label or nothing at all, and those two entries were the escape hatch. Any
future constrained-decoding work inherits the question on its first day, and
the codebase still leans the same way —
`DomainSchema.is_valid_entity_type` returns `True` for the literal `"custom"`
however the domain is defined, and `is_valid_relationship_type` does the same
for `"related_to"`.

### Two specifications of one format, only one of them ever sent

The two previous subsections are symptoms; this is the diagnosis, and it is
why the function was deleted rather than repaired.

The extraction wire format had two specifications. One — the pydantic classes
in `extraction/schema.py` — is derived into a JSON Schema by the adapter on
every call, so it cannot drift from what is sent. The other was a `dict`
literal maintained by hand, never sent to anything, and never compared against
the first by any test. A test can only pin what a function returns, and both
of these functions returned exactly what their authors wrote; nothing existed
that could observe the two disagreeing, which is why they did disagree, in
field names, in required-field lists, and in vocabulary.

A second specification of a format, free to drift and never exercised, is
worse than no second specification, because it reads as authoritative. It is
two of this project's catalogued defect shapes at once
(`.claude/rules/recurring-defects.md`): redundant declaration sites with
undocumented precedence, where the precedence turns out to be "one of them is
never used", and inert code, which passes every test that does not assert on
it. The tests it had are the reason to distrust "it had tests" as a defence —
they asserted that the dict contained the domain's type ids, which was true,
and nothing in a test of a function's return value can notice that the return
value goes nowhere.

Which of the two specifications is authoritative is not a judgement call, and
that is what makes the deletion the right repair rather than the cheap one.
`extraction/schema.py` is authoritative *by construction*: the adapter derives
what it decodes against from those classes on every call, so the pydantic
classes cannot disagree with the wire format — they are it. Any hand-written
restatement is therefore a copy of a derived thing, and a copy of a derived
thing has no mechanism that can fail when it drifts. Deleting it removes the
possibility rather than the instance.

The same reasoning removed the classification prompt's hand-written example of
its own response object — see
[the classification prompt no longer restates the response format](#the-classification-prompt-no-longer-restates-the-response-format).
Both deletions are the same move: where a shape is already derived from a
pydantic class, do not also state it by hand. The classifier's is the sharper
of the two, because there the second statement had a visible cost — it is why
the classifier once carried a `_parse_response` that dug a JSON object out of
surrounding prose.

Repairing `generate_json_schema` instead would have meant two commitments, not
one: renaming the fields to match `Extraction` *and* adding the port parameter
that would let the result be sent. Only the second is real work, and it is a
change to `LlmProvider.extract` — which is where the scrutiny belongs, and
which the field-name fix would have bought no progress towards. Worse, the
repaired dict would still have been a hand-maintained copy with nothing
holding it in step, so the fix that looks complete is the one that reinstates
the shape.

The pre-deletion source is at `6058746^` if a future constrained-decoding
attempt wants the enum construction to start from — `6058746` deleted the
function and wired `domain_system_prompt` in its place, and `3502900` deleted
the router that had been calling it one commit earlier. Nothing in the current
tree references either.

### The open question is recorded as BACKLOG B57, not closed by this ADR

Deleting the code does not decide the question, and B57 is where the question
lives. It states what constraining would actually take:

- **A per-domain pydantic model built at runtime** — `pydantic.create_model`
  with `entity_type: Literal[...]` from the domain's type ids — threaded
  through `ExtractionPipeline` as a **schema** argument rather than a prompt
  one. That is the option that makes `map_extraction` generic over the schema
  it maps, since it reads `Extraction`'s field names today. Note what this
  does *not* need: it does not change `LlmProvider.extract`, because a
  generated model is still a pydantic class. The deleted function's fatal
  problem is absent from the design that replaces it.
- **Or a validation pass after extraction**, dropping or re-labelling
  out-of-vocabulary types. This one needs a decision the code cannot make for
  you: whether an unexpected type is a *finding* about the corpus worth
  recording, or a *defect* worth rejecting. `DomainSchema` already has the
  predicates (`is_valid_entity_type`, `validate_relationship`) either would
  call.

B57 also records why it is not obviously worth doing: a domain's entity list
is a hint about what matters, and a hard enum converts everything the author
did not think of into `"custom"` or into nothing. Those two words are not
rhetorical — they are the two escape hatches `DomainSchema` already grants
(`is_valid_entity_type` admits `"custom"`, `is_valid_relationship_type` admits
`"related_to"`), so whichever option is taken inherits them on day one.

Whether constrained decoding extracts *better* is currently an argument rather
than a measurement, and this repository has no way to settle it. Every test
here checks that the library is *correct*; nothing checks that it is
*accurate*, and extraction can satisfy every invariant and still find the
wrong entities. The suite that would decide it is B12, which does not exist:
`uv run pytest -m accuracy tests/accuracy/` collects zero tests. B57 is one of
two entries B12 names as blocked on it.

> **Amendment.** B12 has since been built, so the paragraph above is a record
> of the state at the time of the decision rather than a description of the
> tree. `tests/accuracy/` now measures precision, recall and F1 over a graded
> corpus under `-m accuracy`. It does **not** settle the question this ADR is
> about: the corpus is five hand-graded documents and its floors are set where
> a regression trips them, so it can show that off-schema extraction got worse
> and cannot show that constraining would be better. The decision stands
> unchanged; what changed is that the argument is now falsifiable in principle.

One correction for anyone following the trail: B57's own text says the deleted
function is "recoverable from `e063faa`". It is not — that commit deletes the
settings object and never touches `prompt_generator.py`. The pre-deletion
source is at `6058746^`.

So this ADR records two things and decides only the first: that extraction is
prompted rather than constrained today, and that the deleted function was not
the thing that would have changed that. Re-adding a schema-dict path means
changing `LlmProvider.extract`, which is a port change and
[gets the scrutiny of one](0008-the-two-non-store-ports.md); building a
per-domain pydantic class does not, and is the option to reach for first.

## Also deleted with it, and why they are not coming back

`generate_json_schema` was not the only casualty of slice 10's pass over
`prompt_generator.py`. Two more of the module's four public halves went in the
same commit (`6058746`), and the module came out of it as three functions —
one public — where it had been a class, a singleton, and four
module-level wrappers. The three deletions are one argument in three shapes,
and it is worth separating from the schema argument above: the schema dict
*could not* have worked. These two would have worked and were harmful anyway.

### `generate_user_prompt`: an 8000-character truncation applied on top of chunking

`DomainPromptGenerator.__init__` took `max_content_length`, defaulting to
`DEFAULT_MAX_CONTENT_LENGTH = 8000`, and `generate_user_prompt(content,
truncate=True)` cut the content to that length, backed up to the last `". "`
if one fell past the halfway mark (`if last_period > self.max_content_length
// 2`), appended `"\n\n[Content truncated due to length]"`, logged the before
and after lengths at `DEBUG`, and wrapped the result in `"Extract entities and
relationships from the following content:"`.

Every part of that is defensible in isolation, which is why it survived as
long as it did — it is careful code: it respects sentence boundaries, it tells
the model that something was cut, it has an opt-out (`truncate=False`), and
its default is generous. Taken with the rest of the pipeline it is not
defensible at all, for two separate reasons.

**It duplicates a decision the chunker already made, and disagrees with it.**
`SlidingWindowChunker` splits a document into chunks of at most
`default_chunk_size` (3000) with an overlap, deliberately at sentence and
paragraph boundaries, and `ExtractionPipeline.extract` sends each chunk's
`text` to the provider. Chunking exists precisely so that a document longer
than a context window is extracted *entirely*. A truncation applied after it
discards the tail of every chunk that survived — silently, since the marker
goes to the model, not to a caller. At the shipped default sizes it never
bites (3000 < 8000), which makes it worse rather than better: the limit is
inert until someone raises the chunk size past 8000 for a model that can take
it, at which point the improvement quietly starts losing text. Two places
deciding how much content reaches a model, with no stated precedence, is
recurring defect shape 2 — and here the precedence is worse than undocumented,
because which one wins depends on a number the caller of the *other* one sets.
The `DEBUG` log line is the only signal, and it names lengths rather than a
document, so it is not something a caller reading a `GraphBuildReport` could
ever act on.

**And there is no user prompt to generate.** The port is
`extract(text, schema, *, system_prompt)`; the pipeline passes `chunk.text` as
`text`, unwrapped. The framing sentence this function added has nowhere to be
said. Like `generate_json_schema`, its callers were its own module-level
wrapper, `generate_full_prompt`, and its tests.

So it is not coming back in this shape. If a chunk ever needs a length guard,
it belongs where chunk sizes are decided, not in the module that renders a
domain's prose.

### The module singleton `get_prompt_generator`/`reset_prompt_generator`

A module-level `_generator: DomainPromptGenerator | None = None`, a `global`
assignment on first call, and a companion whose docstring said what it was for
— "Useful for testing to ensure a fresh instance":

```python
def get_prompt_generator(
    max_content_length: int = DEFAULT_MAX_CONTENT_LENGTH,
) -> DomainPromptGenerator:
    global _generator
    if _generator is None:
        _generator = DomainPromptGenerator(max_content_length=max_content_length)
    return _generator
```

All three public convenience functions (`generate_extraction_prompt`,
`generate_output_schema`, `generate_user_prompt`) reached the class through it,
so every prompt this module produced went past a global.

**What the singleton cached was one integer.** `DomainPromptGenerator`'s only
field was `max_content_length`, and that field existed only to serve
`generate_user_prompt` — the truncation deleted in the previous subsection.
There was no connection, no compiled template, no loaded registry: rendering a
domain's prose reads the `DomainSchema` it is handed and nothing else. So the
process-wide instance existed to avoid re-binding an `int`.

**And it failed even at that.** `get_prompt_generator` accepts
`max_content_length` and discards it whenever an instance already exists — its
own docstring says "only used on first call" — so the first caller anywhere in
a process fixed the limit for every later one, and which caller that was
depended on import and call order. `generate_user_prompt(max_length=...)` had
to work around its own module's singleton to honour its argument:

```python
generator = get_prompt_generator()
if max_length is not None:
    temp_generator = DomainPromptGenerator(max_content_length=max_length)
    return temp_generator.generate_user_prompt(content, truncate=truncate)
```

A cache whose key is ignored, and which the caller bypasses when the key
matters, is a global variable with a longer name.

`reset_prompt_generator` is the tell worth naming, because it recurs
elsewhere. A function whose only purpose is to let tests undo module state is
not a testing convenience; it is the module telling you the state should not
be module-level. It is also a standing order-dependence hazard under
`pytest-randomly`, which randomises test order by design here: the suite is
correct only while every test that cares remembers to call it, and a test that
forgets fails in some orders and not others. That is precisely the shape this
project treats as a bug in the test rather than a reason to pin a seed.

Nothing replaced it. `prompt_generator.py` today has no module-level mutable
state at all — one constant (`MAX_EXAMPLES_PER_TYPE`), one public function and
five private helpers, no `global` anywhere, and no reset for a test to call.

### Rendering a template is a pure function of the schema

With the truncation limit gone there was nothing left for an instance to hold,
so `DomainPromptGenerator` went too and the module exposes functions.
`domain_system_prompt(domain)` takes everything it needs as an argument and
returns a `str`: same schema in, same string out, on any call, in any order,
from any process. There is no construction step, no configuration, and nothing
to reset between tests. The six private helpers are pure over their arguments
as well — `_entity_descriptions` and `_relationship_descriptions`, their
per-type companions `_entity_line` and `_relationship_line`, `_property_hints`,
and `_schema_for`, which is the only one that touches anything outside its
argument at all (it reads the registry, and raises `UnknownDomainError` when
the id is not in it). `MAX_EXAMPLES_PER_TYPE` is a module constant rather than
a field precisely because varying it per instance was never a use case anyone
had; as a field it would have been a second `max_content_length` waiting to
happen.

This is not a general preference for functions over classes. It is the
narrower observation that an object is worth having when it holds state that
outlives a call — a connection, a cache with a key that is actually used, an
accumulating buffer — and that the moment its only field was deleted, the
class was a namespace with a constructor in front of it. The practical gain is
in the tests: a purity claim is checkable by calling the function twice and
comparing, where the class version needed a `reset_prompt_generator` and a
convention about calling it.

`ContentClassifier`'s `classify_content` wrapper went the same way in the
slice 11 fix round (`a401961`), from the other direction: an `async` module
function that built a `ContentClassifier` from its arguments and called
`classify` on it, where `ContentClassifier(provider).classify(text)` is the
same line without the layer. It had no caller, and its docstring's example had
rotted in a way only a caller would have caught — it imported
`LangChainLlmProvider` and then constructed an `OllamaProvider`, a class
deleted in slice 6, on the next line.

`ContentClassifier` keeps its class shape, and that is the contrast worth
drawing rather than an inconsistency. It genuinely holds collaborators across
calls — a provider, a timeout, a confidence threshold, a fallback domain, and
a registry it resolves lazily and caches on first use — so an instance is
something a caller configures once and classifies many documents with. The
`__all__` entry `classify_content` vacated carries a comment recording exactly
that, which is the same reasoning this section records for the module next
door, left where the next person to consider re-adding a convenience wrapper
will read it.

## Decision 2: `ContentClassifier` never fails

### The three paths to `encyclopedia_wiki` at confidence 0.0: content under `MIN_CONTENT_LENGTH`, an answer under `confidence_threshold`, and any `LlmProviderError` (plus `TimeoutError`)

Content whose stripped length is under `MIN_CONTENT_LENGTH` (100) is never
sent to a model at all. `LlmProviderError` — the port's whole failure family,
empty completions and completions that did not validate — is caught and
swallowed, as is `TimeoutError`. Both return `DEFAULT_FALLBACK_DOMAIN`
(`encyclopedia_wiki`) at confidence `0.0`, with the reason in `reasoning`.

### The low-confidence path is not the same as the other two: it substitutes the domain, preserves the model's confidence, and records the original under `alternatives`

An answer below `confidence_threshold` (default `0.5`, and the comparison is
`<`, so exactly at the threshold is accepted) returns the fallback domain but
keeps the model's own confidence value, not `0.0`, and puts the rejected
`{"domain": ..., "confidence": ...}` into `alternatives`. So a
`domain_confidence` of `0.3` means "the model answered, weakly, and was
overruled" while `0.0` means "no usable answer existed". Those are different
facts and the result distinguishes them.

### Why this inverts the no-fallback rule extraction applies elsewhere

Extraction refuses to degrade: `LlmProvider` raises on an empty completion
rather than returning an empty result, because "this document held no
entities" is a legitimate answer and a caller cannot tell it from a failure.

Classification is the opposite case. A misclassified document is extracted
with the general-purpose schema — a *worse* answer, still an answer. A
silently empty extraction is a *missing* answer that looks like a real one.
The rule is not "never fall back"; it is "never let a failure impersonate a
result", and here the result is visibly worse rather than invisibly absent.

### Rejected alternative: raise and let the caller choose

`AUTO` exists so a caller does not have to know the domain list. A version
that raises hands that decision straight back — every caller of `AUTO` would
need a `try` and a fallback domain of its own, and would pick
`encyclopedia_wiki`. The cost of the fallback is that a choice and a give-up
look alike, and that is addressable without pushing the decision outward.

## Making a fallback distinguishable from a choice

### Confidence 0.0 is carried out, not logged and dropped: `GraphBuildReport.domain_confidence`

`build_graph` returns the classifier's confidence on the report. It is `None`
when no classifier ran — when `domain` was given explicitly or omitted —
rather than `0.0`, so a caller filtering for give-ups on `== 0.0` does not
also catch every run that named its own domain.

### `ClassificationResult.reasoning` names the reason on every fallback path

`"Fallback classification: Content too short"`,
`"Fallback classification: Classification failed: ..."`,
`"Fallback classification: Classification timeout"`, and for the low-confidence
path a sentence naming both numbers and the original domain. The field is
never empty on a fallback.

### Why a fallback reporting the same shape as a choice is the hazard being mitigated

A plausible answer nobody investigates is the failure mode of any silent
default. The mitigation is not to remove the default but to make the
give-up observable at the boundary a caller actually reads.

## Enforcement and boundary behaviour

### `UnknownDomainError` translates the registry's bare `KeyError` and lists available domains

`domain_system_prompt` is public surface
([ADR 0006](0006-the-public-surface-is-gated.md)), and `RedstringError` is the
documented base of everything this library raises deliberately — a `KeyError`
escaping is a leak of the registry's implementation. A typo in a domain id is
the overwhelmingly likely cause, so the message lists the ids that do exist.

### PII sanitisation and `MAX_CONTENT_FOR_CLASSIFICATION` truncation apply to classification only, never to extraction input

Before the classifier sends anything it replaces emails, phone numbers, SSNs
and card-shaped numbers with placeholder tokens, then truncates at
`MAX_CONTENT_FOR_CLASSIFICATION` (4000 characters). Both are correct here
precisely because classification only needs a sample: it is deciding a label,
not reading the document. Neither applies on the extraction path, where
discarding text discards entities.

### The classification prompt no longer restates the response format

`CLASSIFICATION_PROMPT` lists the domains and the content and stops. The shape
of the answer is pinned by `ClassificationResult` and the port's validation,
so the old template's "respond with ONLY a JSON object" plus a hand-written
example were a second specification of the same thing — and the reason the
classifier once needed a `_parse_response` that dug a JSON object out of
surrounding prose.

## Consequences

- A model may emit an `entity_type` outside the domain's vocabulary and
  nothing rejects it. Domain schemas steer; they do not gate.
- `AUTO` can silently select `encyclopedia_wiki`; readers of a build must
  check `domain_confidence`. `0.0` is a give-up, a small positive value is an
  overruled weak answer, `None` means no classifier ran.
- BACKLOG B57 stays open and is blocked on the accuracy suite (B12). Whether
  constrained decoding extracts *better* is currently an argument, not a
  measurement.
- Re-adding a schema-dict path means changing `LlmProvider.extract`, not
  restoring a deleted function. That is a port change and gets the scrutiny of
  one.
