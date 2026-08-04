"""Turning a `DomainSchema` into the string a model is told before a chunk.

This is the join BACKLOG B55 said was missing. `ExtractionPipeline` has taken
`system_prompt` since slice 6; `domains/` has held six YAML schemas since
before the rewrite; nothing connected them. One function does:

```python
pipeline = ExtractionPipeline(provider, system_prompt=domain_system_prompt("news_journalism"))
```

No new type was needed, which is the reason this survived the deletion that
took `strategy_router.py`. The router's surface was `route(job, content)` and
the job it took no longer exists; this one takes a domain id and returns a
string, and `str` is a type that will still be here in ten years.

## What generated the prompt but is gone

Three things this module used to do had no consumer and no way to acquire one,
so slice 10 deleted them rather than exempting them:

- **`generate_json_schema`** produced a hand-rolled JSON Schema `dict` with
  the domain's entity types as an enum. `LlmProvider.extract` takes a
  *pydantic class*, not a schema dict, and derives the JSON Schema from it --
  so there was nowhere to pass this. Worse, the dict it built described a
  different wire shape than `extraction.schema.Extraction` (`type`/`source`/
  `target` rather than `entity_type`/`source_name`/`target_name`), so a model
  that obeyed it would produce output `map_extraction` cannot read. Two
  disagreeing specifications of one wire format, and only one of them was
  ever sent. Constraining extraction to a domain's *vocabulary* is a real
  want; BACKLOG B57 records what it would actually take.
- **`generate_user_prompt`** truncated content at 8000 characters. The
  pipeline chunks a document and sends chunks; truncating on top of that
  silently discards the tail of every chunk that survived chunking, which is
  the opposite of what a chunker is for.
- **The module singleton** (`get_prompt_generator`/`reset_prompt_generator`)
  cached an object whose only state was the truncation limit that has just
  gone. Rendering a template is a pure function of the schema.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kg_builder.domain.exceptions import UnknownDomainError
from kg_builder.extraction.domains.registry import get_domain_registry, get_domain_schema

if TYPE_CHECKING:
    from kg_builder.extraction.domains.models import (
        DomainSchema,
        EntityTypeSchema,
        PropertySchema,
        RelationshipTypeSchema,
    )

#: How many of an entity type's examples reach the prompt.
#:
#: All of them is not better. The examples are there to disambiguate the type,
#: and a schema listing twenty of them would spend most of the prompt on one
#: type -- which reads to the model as emphasis rather than as illustration.
MAX_EXAMPLES_PER_TYPE = 3


def domain_system_prompt(domain: str | DomainSchema) -> str:
    """What to tell a model that is extracting for `domain`.

    Args:
        domain: A domain id such as `"literature_fiction"`, or a
            `DomainSchema` already in hand. The id form is the common one;
            the object form exists so a caller with a schema of its own --
            loaded from its own YAML, or built in code -- is not forced to
            register it first.

    Returns:
        The schema's `extraction_prompt_template` with
        `{entity_descriptions}` and `{relationship_descriptions}` filled in.
        Pass it to `ExtractionPipeline(provider, system_prompt=...)`.

    Raises:
        UnknownDomainError: No such domain. The message lists the ones that
            exist, because a typo in a domain id is the overwhelmingly likely
            cause. The registry raises a bare `KeyError`; it is translated
            here because this is the public boundary and `KgBuilderError` is
            the promise a caller catches on.
    """
    schema = _schema_for(domain) if isinstance(domain, str) else domain
    return (
        schema.extraction_prompt_template.replace(
            "{entity_descriptions}", _entity_descriptions(schema)
        ).replace("{relationship_descriptions}", _relationship_descriptions(schema))
        # A template with neither placeholder is legal and renders as itself:
        # a domain whose prompt is entirely prose is a domain whose author
        # decided the type list was not worth the tokens.
    )


def _schema_for(domain_id: str) -> DomainSchema:
    try:
        return get_domain_schema(domain_id)
    except KeyError as error:
        available = sorted(summary.domain_id for summary in get_domain_registry().list_domains())
        raise UnknownDomainError(domain_id, available) from error


def _entity_descriptions(schema: DomainSchema) -> str:
    """One markdown bullet per entity type, with examples and property hints."""
    lines: list[str] = []
    for entity_type in schema.entity_types:
        lines.append(_entity_line(entity_type))
        hints = _property_hints(entity_type.properties)
        if hints:
            lines.append(f"  Properties: {hints}")
    return "\n".join(lines)


def _entity_line(entity_type: EntityTypeSchema) -> str:
    line = f"- **{entity_type.id}**: {entity_type.description}"
    if entity_type.examples:
        examples = ", ".join(entity_type.examples[:MAX_EXAMPLES_PER_TYPE])
        line += f" (examples: {examples})"
    return line


def _property_hints(properties: list[PropertySchema]) -> str:
    return ", ".join(
        f"{prop.name} ({prop.description})" if prop.description else prop.name
        for prop in properties
    )


def _relationship_descriptions(schema: DomainSchema) -> str:
    """One markdown bullet per relationship type, with endpoint constraints."""
    return "\n".join(
        _relationship_line(relationship_type) for relationship_type in schema.relationship_types
    )


def _relationship_line(relationship_type: RelationshipTypeSchema) -> str:
    line = f"- **{relationship_type.id}**: {relationship_type.description}"
    constraints = []
    if relationship_type.valid_source_types:
        constraints.append(f"from: {', '.join(relationship_type.valid_source_types)}")
    if relationship_type.valid_target_types:
        constraints.append(f"to: {', '.join(relationship_type.valid_target_types)}")
    if relationship_type.bidirectional:
        constraints.append("bidirectional")
    if constraints:
        line += f" ({'; '.join(constraints)})"
    return line
