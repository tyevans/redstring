"""Rendering a `DomainSchema` into a system prompt.

Slice 10 cut this module to the one thing that has a caller
(`domain_system_prompt`), so the JSON-schema, user-prompt and singleton tests
that used to live here went with the code -- see the module docstring of
`redstring.extraction.prompt_generator` for why each was unreachable.

What is tested here is the rendering. That the rendered string reaches the
model is `test_domain_prompting.py`'s job, and that the six bundled YAML
schemas render at all is `domains/test_yaml_schemas.py`'s.
"""

from __future__ import annotations

import pytest

from redstring.extraction.domains.models import (
    DomainSchema,
    EntityTypeSchema,
    PropertySchema,
    RelationshipTypeSchema,
)
from redstring.extraction.prompt_generator import MAX_EXAMPLES_PER_TYPE, domain_system_prompt

BOTH_PLACEHOLDERS = (
    "Entities:\n{entity_descriptions}\n\nRelationships:\n{relationship_descriptions}"
)


def _schema(
    *,
    entity_types: list[EntityTypeSchema],
    relationship_types: list[RelationshipTypeSchema],
    template: str = BOTH_PLACEHOLDERS,
) -> DomainSchema:
    return DomainSchema(
        domain_id="test_domain",
        display_name="Test Domain",
        description="A test domain for unit tests",
        entity_types=entity_types,
        relationship_types=relationship_types,
        extraction_prompt_template=template,
    )


@pytest.fixture
def sample_schema() -> DomainSchema:
    return _schema(
        entity_types=[
            EntityTypeSchema(
                id="character",
                description="A character in the story",
                examples=["Hamlet", "Macbeth", "Ophelia"],
            ),
            EntityTypeSchema(id="theme", description="A theme in the work"),
        ],
        relationship_types=[
            RelationshipTypeSchema(
                id="loves",
                description="Romantic love",
                valid_source_types=["character"],
                valid_target_types=["character"],
            ),
            RelationshipTypeSchema(
                id="related_to", description="General relationship", bidirectional=True
            ),
        ],
    )


class TestRendering:
    def test_keeps_the_template_s_own_prose_and_fills_both_placeholders(
        self, sample_schema: DomainSchema
    ) -> None:
        prompt = domain_system_prompt(sample_schema)

        assert "Entities:" in prompt
        assert "Relationships:" in prompt
        assert "{entity_descriptions}" not in prompt
        assert "{relationship_descriptions}" not in prompt

    def test_each_type_contributes_its_id_and_description(
        self, sample_schema: DomainSchema
    ) -> None:
        prompt = domain_system_prompt(sample_schema)

        for expected in (
            "- **character**: A character in the story",
            "- **theme**: A theme in the work",
            "- **loves**: Romantic love",
        ):
            assert expected in prompt

    def test_entity_descriptions_go_where_the_entity_placeholder_was(
        self, sample_schema: DomainSchema
    ) -> None:
        # The two substitutions are separate `str.replace` calls over one
        # template, and swapping them would leave every assertion above
        # passing: both lists would still be present, in the wrong halves.
        prompt = domain_system_prompt(sample_schema)

        assert prompt.index("- **character**") < prompt.index("Relationships:")
        assert prompt.index("- **loves**") > prompt.index("Relationships:")

    def test_at_most_three_examples_reach_the_prompt(self) -> None:
        schema = _schema(
            entity_types=[
                EntityTypeSchema(id="item", description="An item", examples=list("ABCDE"))
            ],
            relationship_types=[RelationshipTypeSchema(id="related_to", description="General")],
        )

        prompt = domain_system_prompt(schema)

        assert "examples: A, B, C)" in prompt
        assert "D" not in prompt
        assert MAX_EXAMPLES_PER_TYPE == 3

    def test_a_type_with_no_examples_gets_no_empty_parenthesis(self) -> None:
        schema = _schema(
            entity_types=[EntityTypeSchema(id="item", description="An item")],
            relationship_types=[RelationshipTypeSchema(id="related_to", description="General")],
        )

        prompt = domain_system_prompt(schema)

        assert "- **item**: An item" in prompt
        assert "examples:" not in prompt

    def test_endpoint_constraints_and_bidirectionality_are_stated(
        self, sample_schema: DomainSchema
    ) -> None:
        prompt = domain_system_prompt(sample_schema)

        assert "- **loves**: Romantic love (from: character; to: character)" in prompt
        assert "- **related_to**: General relationship (bidirectional)" in prompt

    def test_a_relationship_with_no_constraints_gets_no_empty_parenthesis(self) -> None:
        schema = _schema(
            entity_types=[EntityTypeSchema(id="item", description="An item")],
            relationship_types=[RelationshipTypeSchema(id="related_to", description="General")],
        )

        prompt = domain_system_prompt(schema)

        assert "- **related_to**: General" in prompt
        assert "()" not in prompt


class TestPropertyHints:
    def test_properties_are_listed_under_their_entity_type(self) -> None:
        schema = _schema(
            entity_types=[
                EntityTypeSchema(
                    id="character",
                    description="A character",
                    properties=[
                        PropertySchema(name="role", type="string", description="Role in story"),
                        PropertySchema(name="allegiance", type="string", description="Faction"),
                    ],
                )
            ],
            relationship_types=[RelationshipTypeSchema(id="related_to", description="General")],
        )

        prompt = domain_system_prompt(schema)

        assert "  Properties: role (Role in story), allegiance (Faction)" in prompt

    def test_a_property_without_a_description_contributes_its_name_alone(self) -> None:
        schema = _schema(
            entity_types=[
                EntityTypeSchema(
                    id="character",
                    description="A character",
                    properties=[PropertySchema(name="role", type="string")],
                )
            ],
            relationship_types=[RelationshipTypeSchema(id="related_to", description="General")],
        )

        assert "  Properties: role\n" in domain_system_prompt(schema) + "\n"

    def test_an_entity_type_with_no_properties_gets_no_properties_line(self) -> None:
        schema = _schema(
            entity_types=[EntityTypeSchema(id="character", description="A character")],
            relationship_types=[RelationshipTypeSchema(id="related_to", description="General")],
        )

        assert "Properties:" not in domain_system_prompt(schema)


class TestTemplateEdgeCases:
    def test_a_template_with_no_placeholders_renders_as_itself(self) -> None:
        schema = _schema(
            entity_types=[EntityTypeSchema(id="item", description="An item")],
            relationship_types=[RelationshipTypeSchema(id="related_to", description="General")],
            template="Just extract whatever you find.",
        )

        assert domain_system_prompt(schema) == "Just extract whatever you find."

    def test_every_entity_type_appears_exactly_once(self) -> None:
        entity_types = [EntityTypeSchema(id=f"type_{n}", description=f"Type {n}") for n in range(8)]
        schema = _schema(
            entity_types=entity_types,
            relationship_types=[RelationshipTypeSchema(id="related_to", description="General")],
        )

        prompt = domain_system_prompt(schema)

        for entity_type in entity_types:
            assert prompt.count(f"- **{entity_type.id}**:") == 1
