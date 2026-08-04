"""What we ask a model for: the schema an `LlmProvider` fills in.

Deliberately **not** the domain model. `Entity` needs a `UUID`, a tenant, a
`normalized_name` and a source attribution, none of which a language model can
supply and all of which it would hallucinate if asked -- a model invited to
emit a UUID emits a plausible one, and two chunks would then disagree about
the identity of the same person. So the model is asked only for what it can
actually read out of the text, and `kg_builder.extraction.mapping` supplies
the rest.

## Relationships name their endpoints; they do not identify them

`ExtractedRelationship` carries `source_name` and `target_name`, because names
are what the model has. Resolving a name to an entity id is the mapper's job
and is the step where a relationship can turn out to be unresolvable -- an
edge to something the model mentioned in passing but never listed as an
entity. That is a normal outcome and not an error; see
`kg_builder.extraction.mapping.MappedExtraction`.

## Every field has a default, and that is not laziness

Structured decoding constrains the *shape* of the output, not the diligence of
the model, and servers vary in how strictly they enforce a schema. A single
omitted `confidence` in the two-hundredth entity of a long document would
otherwise raise `MalformedCompletionError` and discard the other hundred and
ninety-nine. Defaults keep one sloppy field from costing a whole extraction,
while a genuinely broken completion still fails on the shape.

`name`, `entity_type` and the two endpoint names have no default: an entity
with no name is not a partial answer, it is nothing at all.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

#: What a model gets when it declines to say how sure it is.
#:
#: Not 1.0. An omitted confidence means the model did not commit, and reading
#: that as certainty would put unmarked guesses at the top of every ranking
#: that sorts on confidence. Midpoint is the least informative value available
#: in a required 0..1 field, which is the honest encoding of "it did not say".
DEFAULT_CONFIDENCE = 0.5


class ExtractedEntity(BaseModel):
    """One thing a model found in a piece of text."""

    name: str = Field(description="The entity's name exactly as it appears in the text")
    entity_type: str = Field(description="What kind of thing this is, e.g. Person, Place, Concept")
    description: str | None = Field(
        default=None, description="A one-sentence description drawn from the text"
    )
    confidence: float = Field(
        default=DEFAULT_CONFIDENCE,
        ge=0.0,
        le=1.0,
        description="How confident you are that this entity is really present, from 0 to 1",
    )
    properties: dict[str, Any] = Field(
        default_factory=dict, description="Any other attributes the text states about this entity"
    )


class ExtractedRelationship(BaseModel):
    """A directed, typed connection between two entities the model found."""

    source_name: str = Field(description="Name of the entity the relationship starts from")
    target_name: str = Field(description="Name of the entity the relationship points to")
    relationship_type: str = Field(description="What the relationship is, e.g. WORKED_WITH")
    confidence: float = Field(
        default=DEFAULT_CONFIDENCE,
        ge=0.0,
        le=1.0,
        description="How confident you are that this relationship is stated, from 0 to 1",
    )
    properties: dict[str, Any] = Field(
        default_factory=dict, description="Any other attributes the text states about this link"
    )


class Extraction(BaseModel):
    """Everything a model found in one piece of text.

    This is the type handed to `LlmProvider.extract` as `schema`, so its
    JSON Schema is what constrains the server's decoding. The field
    descriptions above are part of that schema and reach the model -- they are
    prompt, not documentation, and editing one changes extraction output.
    """

    entities: list[ExtractedEntity] = Field(
        default_factory=list, description="Every entity present in the text"
    )
    relationships: list[ExtractedRelationship] = Field(
        default_factory=list,
        description="Every relationship stated between entities you listed above",
    )
