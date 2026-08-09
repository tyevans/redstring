"""Turning a domain's vocabulary from a description into a constraint.

`domain_system_prompt` tells the model what a domain's entity and relationship
types *are*. Nothing makes it use them: the JSON Schema the server decodes
against comes from `extraction.schema.Extraction`, whose `entity_type` is a
bare `str`, so "chief executive" and "person" are equally valid answers and
only one of them is the domain's.

This module builds the other kind of schema -- a subclass of `Extraction`
whose `entity_type` and `relationship_type` are `Literal`s over the domain's
declared ids. Handed to `LlmProvider.extract` as `schema`, it reaches the
server's structured decoding, and a model that would have said "chief
executive" says "person" instead because no other token is decodable.

**The port did not have to change for this.** `extract` has always taken
`type[S]` and derived the JSON Schema from it; what was missing was a caller
that passed something other than `Extraction`. That is worth stating because
the deleted `generate_json_schema` tried to do this by building a schema
*dict*, which there was no parameter to pass -- see `BACKLOG.md` B57.

## Why this is opt-in and will stay opt-in

`docs/adr/0011-domain-schemas-prompt-but-do-not-constrain.md` decided that an
off-schema entity is not an error, and the reasoning still holds: a domain
schema's type list is what its author thought of, and a hard enum turns
everything they did not think of into the nearest wrong answer rather than
into a new type. A news schema without `legislation` does not stop documents
mentioning acts of parliament; unconstrained, the model says "legislation" and
a reader of the graph learns something, while constrained it says "document"
or "organization" and the graph is quietly wrong.

So this is a dial a caller reaches for when consistency matters more than
coverage -- the same trade LlamaIndex ships as `SchemaLLMPathExtractor`
against `DynamicLLMPathExtractor`, and it ships both for the same reason.

## There is no empty-vocabulary case, and that is `DomainSchema`'s doing

`Literal[()]` is not a type and a domain with no entity types would produce
one, so this module started with a guard and an exception for it. Both were
**deleted**: `DomainSchema.entity_types` and `relationship_types` are declared
`min_length=1`, so the condition cannot arise and the guard was a branch no
test could reach and no input could take -- the inert-code shape in
`.claude/rules/recurring-defects.md` §3, arriving as defensiveness.

What replaces it is a test over `DomainSchema` itself
(`test_a_domain_cannot_declare_an_empty_vocabulary`), because the assumption
is real even though the branch was not. Relaxing either `min_length` would
make this module raise an unhelpful `TypeError` out of `typing`, and that test
is what fails first and says so.

## Subclassing rather than rebuilding

`Extraction`'s field *names* are load-bearing: `map_extraction` reads
`entity_type`, `source_name` and `target_name` off whatever it is given. A
freshly built model that renamed a field would produce answers the mapper
silently cannot read -- which is exactly how the deleted function was broken,
and it went unnoticed because nothing ever passed its output anywhere.

Building these as subclasses means the field names cannot drift: they are
inherited, and only the two annotations are narrowed. It also means the
descriptions -- which are prompt, not documentation -- come along unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import create_model

from redstring.extraction.schema import ExtractedEntity, ExtractedRelationship, Extraction

if TYPE_CHECKING:
    from redstring.extraction.domains.models import DomainSchema


def constrained_extraction(schema: DomainSchema) -> type[Extraction]:
    """An `Extraction` subclass admitting only this domain's type ids.

    Args:
        schema: The domain whose `entity_types` and `relationship_types`
            become the permitted values.

    Returns:
        A subclass of `Extraction`. Pass it to `LlmProvider.extract` as
        `schema`, and to `map_extraction` unchanged -- the field names are
        inherited, so nothing downstream knows the difference.

    Note:
        Not cached. Building one is a few microseconds of pydantic model
        construction against a model call of a few seconds, and a cache keyed
        on a mutable `DomainSchema` is a correctness question nobody needs to
        have. `ExtractionPipeline` takes the built type once, so the pipeline
        does not rebuild it per chunk either way.
    """
    entity = create_model(
        f"{_class_prefix(schema)}Entity",
        __base__=ExtractedEntity,
        entity_type=(_literal([t.id for t in schema.entity_types]), ...),
    )
    relationship = create_model(
        f"{_class_prefix(schema)}Relationship",
        __base__=ExtractedRelationship,
        relationship_type=(_literal([t.id for t in schema.relationship_types]), ...),
    )
    return create_model(
        f"{_class_prefix(schema)}Extraction",
        __base__=Extraction,
        entities=(list[entity], []),
        relationships=(list[relationship], []),
    )


def _literal(ids: list[str]) -> object:
    """`Literal[...]` over the ids, deduplicated, in declaration order.

    Deduplicated because two entity types with one id is a schema the loader
    permits and `Literal` does not care about, but which produces a JSON
    Schema `enum` with a repeated member -- valid, and read by at least one
    server as a weighting.

    Order is the schema's own, never `sorted`. The order reaches the model as
    the enum's order, and a domain author who put the common types first meant
    it.
    """
    unique = list(dict.fromkeys(ids))
    return Literal[tuple(unique)]


def _class_prefix(schema: DomainSchema) -> str:
    """A readable class name, because pydantic puts it in validation errors.

    A `MalformedCompletionError` naming `NewsJournalismEntity` says which
    vocabulary rejected the answer; one naming `Model1` says a model was
    involved.
    """
    return "".join(part.title() for part in schema.domain_id.replace("-", "_").split("_"))


def constrained_extraction_for(schema: DomainSchema | None) -> type[Extraction]:
    """`constrained_extraction(schema)`, or plain `Extraction` for `None`.

    The form `build_graph` calls, so that "no domain was given" and "this
    domain constrains" are one expression at the call site rather than a
    branch the caller writes.
    """
    return Extraction if schema is None else constrained_extraction(schema)


def permitted_entity_types(schema: type[Extraction]) -> tuple[str, ...]:
    """The entity type ids `schema` admits, or `()` if it admits anything.

    Exists so a test can assert what was built rather than reaching into
    pydantic's field internals in three places, and so a caller can log the
    vocabulary a run was constrained to. `()` is the honest answer for the
    unconstrained schema: not "no types are permitted", but "this schema does
    not restrict them", and the two are distinguished by the caller knowing
    which it asked for.
    """
    annotation = schema.model_fields["entities"].annotation
    item = _sole_argument(annotation)
    if item is None:
        return ()
    field = item.model_fields.get("entity_type")
    if field is None:  # pragma: no cover - inherited from ExtractedEntity always
        return ()
    return _literal_values(field.annotation)


def _sole_argument(annotation: object) -> type[ExtractedEntity] | None:
    from typing import get_args

    args = get_args(annotation)
    if len(args) != 1 or not isinstance(args[0], type):
        return None
    if not issubclass(args[0], ExtractedEntity):
        return None
    return args[0]


def _literal_values(annotation: object) -> tuple[str, ...]:
    from typing import get_args, get_origin

    if get_origin(annotation) is not Literal:
        return ()
    return tuple(str(value) for value in get_args(annotation))
