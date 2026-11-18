"""Model output is bounded when it is parsed, at the limit and one past it.

Clipping and dropping are the documented rules (`redstring.extraction.bounds`).
Each limit is tested at exactly the limit, which must survive unchanged, and at
the limit plus one, which must be cut. Expected values are literals.
"""

from __future__ import annotations

import json

import pytest

from redstring.extraction.bounds import (
    MAX_DESCRIPTION_CHARS,
    MAX_ENTITIES_PER_EXTRACTION,
    MAX_NAME_CHARS,
    MAX_PROPERTY_KEYS,
    MAX_PROPERTY_VALUE_CHARS,
    MAX_RELATIONSHIPS_PER_EXTRACTION,
    MAX_TYPE_CHARS,
    prompt_safe,
)
from redstring.extraction.schema import ExtractedEntity, ExtractedRelationship, Extraction


def entity(**overrides) -> ExtractedEntity:
    fields = {"name": "Ada", "entity_type": "Person"} | overrides
    return ExtractedEntity(**fields)


class TestEntityFieldLimits:
    @pytest.mark.parametrize(
        ("field", "limit"),
        [
            ("name", MAX_NAME_CHARS),
            ("entity_type", MAX_TYPE_CHARS),
            ("description", MAX_DESCRIPTION_CHARS),
        ],
    )
    def test_a_field_at_its_limit_is_kept_whole(self, field, limit):
        value = "x" * limit

        assert getattr(entity(**{field: value}), field) == value

    @pytest.mark.parametrize(
        ("field", "limit"),
        [
            ("name", MAX_NAME_CHARS),
            ("entity_type", MAX_TYPE_CHARS),
            ("description", MAX_DESCRIPTION_CHARS),
        ],
    )
    def test_a_field_one_past_its_limit_is_clipped_to_it(self, field, limit):
        value = "x" * (limit + 1)

        assert getattr(entity(**{field: value}), field) == "x" * limit

    def test_the_documented_limits_are_the_numbers_the_docs_name(self):
        assert (MAX_NAME_CHARS, MAX_TYPE_CHARS, MAX_DESCRIPTION_CHARS) == (200, 100, 1000)


class TestRelationshipFieldLimits:
    def test_an_endpoint_one_past_the_limit_is_clipped(self):
        relationship = ExtractedRelationship(
            source_name="s" * (MAX_NAME_CHARS + 1),
            target_name="t" * MAX_NAME_CHARS,
            relationship_type="r" * (MAX_TYPE_CHARS + 1),
        )

        assert relationship.source_name == "s" * MAX_NAME_CHARS
        assert relationship.target_name == "t" * MAX_NAME_CHARS
        assert relationship.relationship_type == "r" * MAX_TYPE_CHARS


class TestCountLimits:
    def test_entities_at_the_limit_are_all_kept(self):
        extraction = Extraction(
            entities=[entity(name=f"e{i}") for i in range(MAX_ENTITIES_PER_EXTRACTION)]
        )

        assert len(extraction.entities) == MAX_ENTITIES_PER_EXTRACTION

    def test_entities_one_past_the_limit_drop_the_last_keeping_the_first(self):
        extraction = Extraction(
            entities=[entity(name=f"e{i}") for i in range(MAX_ENTITIES_PER_EXTRACTION + 1)]
        )

        assert len(extraction.entities) == MAX_ENTITIES_PER_EXTRACTION
        assert extraction.entities[0].name == "e0"
        assert extraction.entities[-1].name == f"e{MAX_ENTITIES_PER_EXTRACTION - 1}"

    def test_relationships_at_the_limit_are_all_kept(self):
        relationships = [
            ExtractedRelationship(source_name="a", target_name="b", relationship_type="R")
            for _ in range(MAX_RELATIONSHIPS_PER_EXTRACTION)
        ]

        assert len(Extraction(relationships=relationships).relationships) == (
            MAX_RELATIONSHIPS_PER_EXTRACTION
        )

    def test_relationships_one_past_the_limit_are_dropped(self):
        relationships = [
            ExtractedRelationship(source_name=f"a{i}", target_name="b", relationship_type="R")
            for i in range(MAX_RELATIONSHIPS_PER_EXTRACTION + 1)
        ]

        kept = Extraction(relationships=relationships).relationships

        assert len(kept) == MAX_RELATIONSHIPS_PER_EXTRACTION
        assert kept[0].source_name == "a0"


class TestPropertyLimits:
    def test_keys_at_the_limit_are_all_kept(self):
        properties = {f"k{i}": i for i in range(MAX_PROPERTY_KEYS)}

        assert len(entity(properties=properties).properties) == MAX_PROPERTY_KEYS

    def test_keys_one_past_the_limit_keep_the_first_given(self):
        properties = {f"k{i}": i for i in range(MAX_PROPERTY_KEYS + 1)}

        kept = entity(properties=properties).properties

        assert len(kept) == MAX_PROPERTY_KEYS
        assert "k0" in kept
        assert f"k{MAX_PROPERTY_KEYS}" not in kept

    def test_a_value_whose_json_is_at_the_limit_is_kept(self):
        value = "a" * (MAX_PROPERTY_VALUE_CHARS - 2)  # JSON adds two quotes
        assert len(json.dumps(value)) == MAX_PROPERTY_VALUE_CHARS

        assert entity(properties={"v": value}).properties == {"v": value}

    def test_a_value_one_past_the_limit_is_dropped_whole_not_clipped(self):
        value = "a" * (MAX_PROPERTY_VALUE_CHARS - 1)
        assert len(json.dumps(value)) == MAX_PROPERTY_VALUE_CHARS + 1

        assert entity(properties={"v": value, "w": 1}).properties == {"w": 1}

    def test_the_relationship_properties_follow_the_same_rule(self):
        relationship = ExtractedRelationship(
            source_name="a",
            target_name="b",
            relationship_type="R",
            properties={"big": "a" * MAX_PROPERTY_VALUE_CHARS, "small": 2},
        )

        assert relationship.properties == {"small": 2}


class TestPromptFlattening:
    def test_a_line_break_becomes_one_space(self):
        assert prompt_safe("Ada\nLovelace", 200) == "Ada Lovelace"

    def test_a_run_of_controls_becomes_one_space(self):
        assert prompt_safe("Ada\r\n\t\x1bLovelace", 200) == "Ada Lovelace"

    def test_the_result_is_stripped_before_clipping(self):
        assert prompt_safe("  \nAda\n  ", 3) == "Ada"

    def test_the_limit_counts_the_characters_the_model_reads(self):
        assert prompt_safe("a\nb", 3) == "a b"
