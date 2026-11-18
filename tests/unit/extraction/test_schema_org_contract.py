"""Contract tests for Schema.org and Open Graph extraction.

These cover the documented fallbacks: a malformed item is logged and skipped
without losing its neighbours, a list description yields its first element,
nested property objects are flattened by a fixed precedence, and a malformed
Open Graph payload yields an empty list rather than an exception.
"""

from __future__ import annotations

import logging
from typing import Any

from redstring.extraction.schema_org import (
    extract_entities_from_open_graph,
    extract_entities_from_schema_org,
)


def test_a_malformed_item_is_logged_and_its_neighbours_are_still_extracted(
    caplog: Any,
) -> None:
    items: list[Any] = [
        # An object-valued @type cannot be looked up in the type map.
        {"@type": {"nested": "object"}, "name": "Broken Item"},
        {"@type": "Person", "name": "Ada Lovelace"},
    ]

    with caplog.at_level(logging.WARNING, logger="redstring.extraction.schema_org"):
        entities = extract_entities_from_schema_org(items)

    assert [e["name"] for e in entities] == ["Ada Lovelace"]
    assert entities[0]["type"] == "person"
    assert any("Failed to extract entity from schema item" in r.message for r in caplog.records)


def test_a_list_description_yields_its_first_element_and_an_empty_one_yields_none() -> None:
    items: list[Any] = [
        {"@type": "Person", "name": "Ada", "description": ["First line", "Second line"]},
        {"@type": "Person", "name": "Charles", "description": []},
    ]

    entities = extract_entities_from_schema_org(items)

    by_name = {e["name"]: e for e in entities}
    assert by_name["Ada"]["description"] == "First line"
    assert by_name["Charles"]["description"] is None


def test_nested_property_objects_are_flattened_by_name_then_value_then_url() -> None:
    items: list[Any] = [
        {
            "@type": "Organization",
            "name": "Acme",
            "logo": {"@value": "https://cdn.example/logo.png"},
            "image": {"url": "https://cdn.example/photo.jpg"},
            "email": {"name": "Named Wins", "url": "https://ignored.example"},
        }
    ]

    [entity] = extract_entities_from_schema_org(items)

    assert entity["properties"]["logo"] == "https://cdn.example/logo.png"
    assert entity["properties"]["image"] == "https://cdn.example/photo.jpg"
    assert entity["properties"]["email"] == "Named Wins"


def test_a_string_in_a_nested_field_yields_no_nested_entity_and_keeps_the_parent() -> None:
    items: list[Any] = [{"@type": "Book", "name": "Notes", "author": "Jane Doe"}]

    entities = extract_entities_from_schema_org(items)

    assert [e["name"] for e in entities] == ["Notes"]
    assert entities[0]["properties"]["author"] == "Jane Doe"


def test_a_non_string_open_graph_type_yields_no_entities_and_does_not_raise() -> None:
    assert extract_entities_from_open_graph({"type": 5, "title": "Some Page"}) == []


def test_a_string_open_graph_type_still_yields_an_entity() -> None:
    [entity] = extract_entities_from_open_graph({"type": "website", "title": "Some Page"})

    assert entity["name"] == "Some Page"
    assert entity["type"] == "document"
