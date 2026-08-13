"""Schema.org and Open Graph extraction, and the decision that entity type is a string.

This module had **no tests at all** before slice 9, while being exported from
`redstring.extraction.__all__`. It is tested here because slice 9 changes it:
it used to map Schema.org types onto `models.extracted_entity.EntityType`, an
enum that dies with the relational layer.

**The entity-type strings are pinned as literals on purpose.** They are the
only remaining record of what the deleted enum's members were, so these
assertions are what makes the port behaviour-preserving rather than merely
green. See the module docstring of `extraction/schema_org.py` for why the
replacement is a string and not another enum.

Two shapes CLAUDE.md warns about are avoided deliberately:

- Every `_map_og_type` branch is asserted **and** a type in none of them, so a
  test cannot pass against an implementation that collapsed to the fallback.
  Asserting only `"article" -> "document"` cannot distinguish the first branch
  from the `else`.
- The mapped and unmapped Schema.org cases are asserted to **differ**. A
  `SCHEMA_TYPE_MAP` deleted entirely still answers `"concept"` to everything,
  which a test that only checks the fallback would call correct.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from redstring.domain.entity import Entity
from redstring.domain.provenance import ExtractionMethod
from redstring.extraction.schema_org import (
    SCHEMA_TYPE_MAP,
    _map_og_type,
    extract_entities_from_open_graph,
    extract_entities_from_schema_org,
)

#: What the deleted `EntityType` enum's members were, as strings. Pinned here
#: because nothing else records them once `models/` is gone.
LEGACY_ENTITY_TYPES = frozenset(
    {
        "person",
        "organization",
        "location",
        "event",
        "product",
        "concept",
        "document",
        "date",
    }
)


class TestEntityTypeIsAStringAndNotAnEnum:
    """The slice-9 decision, stated as a test rather than only as prose."""

    def test_every_mapped_type_is_a_plain_string(self):
        """`str`-subclassing enums pass `isinstance(x, str)`, so that is not the check.

        `type(value) is str` is, and it is the assertion that would fail if a
        replacement enum were introduced later by momentum.
        """
        for schema_type, entity_type in SCHEMA_TYPE_MAP.items():
            assert type(entity_type) is str, (
                f"{schema_type} maps to {entity_type!r} of type "
                f"{type(entity_type).__name__}; entity type is a free string"
            )

    def test_the_mapping_targets_exactly_the_legacy_vocabulary(self):
        """The port changed the representation, not the values."""
        assert set(SCHEMA_TYPE_MAP.values()) == LEGACY_ENTITY_TYPES

    def test_a_mapped_type_reaches_entity_unchanged(self):
        """`Entity.entity_type` is a `str` field, so the output is directly usable.

        This is the whole argument for not reintroducing an enum: an enum
        member would have to be unwrapped by every caller, and
        `entity_id_for` hashes the type as a string regardless.
        """
        [entity] = extract_entities_from_schema_org([{"@type": "Person", "name": "Ada"}])
        constructed = Entity(
            id=UUID("22222222-2222-2222-2222-222222222222"),
            tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
            name=entity["name"],
            normalized_name="ada",
            entity_type=entity["type"],
            extraction_method=entity["method"],
            confidence=entity["confidence"],
        )
        assert constructed.entity_type == "person"

    def test_the_extraction_method_is_the_domain_one(self):
        [entity] = extract_entities_from_schema_org([{"@type": "Person", "name": "Ada"}])
        assert entity["method"] is ExtractionMethod.SCHEMA_ORG


class TestSchemaOrgExtraction:
    def test_a_known_type_and_an_unknown_type_do_not_agree(self):
        """A deleted `SCHEMA_TYPE_MAP` answers `"concept"` to both."""
        [known] = extract_entities_from_schema_org([{"@type": "Person", "name": "Ada"}])
        [unknown] = extract_entities_from_schema_org([{"@type": "GardenGnome", "name": "Ada"}])
        assert known["type"] == "person"
        assert unknown["type"] == "concept"
        assert known["type"] != unknown["type"]

    def test_an_array_type_takes_the_first(self):
        [entity] = extract_entities_from_schema_org(
            [{"@type": ["Organization", "Person"], "name": "Acme"}]
        )
        assert entity["type"] == "organization"

    def test_an_item_with_no_type_is_skipped(self):
        assert extract_entities_from_schema_org([{"name": "Ada"}]) == []

    def test_an_item_with_no_usable_name_is_skipped(self):
        assert extract_entities_from_schema_org([{"@type": "Person"}]) == []

    def test_a_non_dict_item_is_skipped(self):
        assert extract_entities_from_schema_org(["not a dict", 42, None]) == []

    @pytest.mark.parametrize(
        ("field", "expected"),
        [
            ("name", "From name"),
            ("headline", "From headline"),
            ("title", "From title"),
            ("alternateName", "From alternateName"),
            ("legalName", "From legalName"),
        ],
    )
    def test_each_name_field_in_the_fallback_chain_is_reached(self, field, expected):
        """Each is asserted separately because the chain is ordered.

        A single test using `name` passes against an implementation that
        knows only `name`.
        """
        [entity] = extract_entities_from_schema_org([{"@type": "Person", field: expected}])
        assert entity["name"] == expected

    def test_the_name_chain_prefers_the_earlier_field(self):
        [entity] = extract_entities_from_schema_org(
            [{"@type": "Person", "name": "first", "headline": "second"}]
        )
        assert entity["name"] == "first"

    def test_a_name_is_constructed_from_given_and_family_names(self):
        [entity] = extract_entities_from_schema_org(
            [{"@type": "Person", "givenName": "Ada", "familyName": "Lovelace"}]
        )
        assert entity["name"] == "Ada Lovelace"

    def test_a_list_valued_name_takes_the_first(self):
        [entity] = extract_entities_from_schema_org(
            [{"@type": "Person", "name": ["Ada", "Augusta"]}]
        )
        assert entity["name"] == "Ada"

    def test_external_ids_carry_the_schema_org_id_and_same_as(self):
        [entity] = extract_entities_from_schema_org(
            [
                {
                    "@type": "Person",
                    "name": "Ada",
                    "@id": "https://example.org/ada",
                    "sameAs": "https://wikidata.org/Q7259",
                }
            ]
        )
        assert entity["external_ids"] == {
            "schema_org_id": "https://example.org/ada",
            "same_as": ["https://wikidata.org/Q7259"],
        }

    def test_a_list_valued_same_as_is_left_as_a_list(self):
        [entity] = extract_entities_from_schema_org(
            [{"@type": "Person", "name": "Ada", "sameAs": ["a", "b"]}]
        )
        assert entity["external_ids"]["same_as"] == ["a", "b"]

    def test_a_nested_object_becomes_its_own_entity(self):
        entities = extract_entities_from_schema_org(
            [
                {
                    "@type": "Article",
                    "name": "On Computing",
                    "author": {"@type": "Person", "name": "Ada"},
                }
            ]
        )
        assert [(e["name"], e["type"]) for e in entities] == [
            ("On Computing", "document"),
            ("Ada", "person"),
        ]

    def test_a_nested_object_without_a_type_is_not_an_entity(self):
        entities = extract_entities_from_schema_org(
            [{"@type": "Article", "name": "On Computing", "author": {"name": "Ada"}}]
        )
        assert [e["name"] for e in entities] == ["On Computing"]

    def test_a_nested_dict_property_is_flattened_to_its_name(self):
        [article, _] = extract_entities_from_schema_org(
            [
                {
                    "@type": "Article",
                    "name": "On Computing",
                    "publisher": {"@type": "Organization", "name": "Acme"},
                }
            ]
        )
        assert article["properties"]["publisher"] == "Acme"

    def test_confidence_is_higher_than_open_graph(self):
        """The two paths differ deliberately; asserting one alone would not say so."""
        [schema] = extract_entities_from_schema_org([{"@type": "Person", "name": "Ada"}])
        [og] = extract_entities_from_open_graph({"type": "profile", "title": "Ada"})
        assert schema["confidence"] > og["confidence"]


class TestOpenGraphExtraction:
    def test_metadata_with_no_title_yields_nothing(self):
        assert extract_entities_from_open_graph({"type": "article"}) == []

    def test_empty_metadata_yields_nothing(self):
        assert extract_entities_from_open_graph({}) == []

    def test_the_extraction_method_is_open_graph(self):
        [entity] = extract_entities_from_open_graph({"type": "website", "title": "Home"})
        assert entity["method"] is ExtractionMethod.OPEN_GRAPH

    def test_an_absent_type_defaults_to_website(self):
        [entity] = extract_entities_from_open_graph({"title": "Home"})
        assert entity["type"] == "document"

    def test_article_metadata_carries_the_article_properties(self):
        [entity] = extract_entities_from_open_graph(
            {
                "type": "article",
                "title": "On Computing",
                "article:published_time": "1843-01-01",
                "article:author": "Ada",
                "article:section": "Science",
            }
        )
        assert entity["properties"]["published_time"] == "1843-01-01"
        assert entity["properties"]["author"] == "Ada"
        assert entity["properties"]["section"] == "Science"

    def test_product_metadata_carries_the_price_properties(self):
        [entity] = extract_entities_from_open_graph(
            {
                "type": "product",
                "title": "Difference Engine",
                "product:price:amount": "1000",
                "product:price:currency": "GBP",
            }
        )
        assert entity["properties"]["price_amount"] == "1000"
        assert entity["properties"]["price_currency"] == "GBP"

    def test_a_non_article_carries_no_article_properties(self):
        """Pins that the branch is a branch, not an unconditional block."""
        [entity] = extract_entities_from_open_graph({"type": "profile", "title": "Ada"})
        assert "published_time" not in entity["properties"]
        assert "price_amount" not in entity["properties"]


class TestOpenGraphTypeMapping:
    """Every branch, plus one type in none of them.

    Without the last case, an implementation that returned its first branch's
    answer unconditionally would still fail -- but one that fell through to
    `"concept"` for everything it did not recognise could not be told apart
    from one with no branches at all.
    """

    @pytest.mark.parametrize(
        ("og_type", "expected"),
        [
            ("website", "document"),
            ("article", "document"),
            ("blog", "document"),
            ("profile", "person"),
            ("product", "product"),
            ("book", "product"),
            ("music.album", "product"),
            ("video.movie", "product"),
            ("place", "location"),
            ("business.business", "location"),
            ("music.song", "document"),
            ("music.playlist", "document"),
            ("video.episode", "document"),
        ],
    )
    def test_each_known_type_maps_to_its_entity_type(self, og_type, expected):
        assert _map_og_type(og_type) == expected

    def test_an_unrecognised_type_falls_back_to_concept(self):
        assert _map_og_type("garden.gnome") == "concept"

    def test_the_mapping_is_case_insensitive(self):
        assert _map_og_type("PROFILE") == "person"

    def test_every_result_is_a_plain_string(self):
        assert type(_map_og_type("profile")) is str
        assert type(_map_og_type("garden.gnome")) is str
