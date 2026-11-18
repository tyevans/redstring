"""Export hardening: GraphML refuses XML-forbidden characters, Mermaid encodes
markup, and `page_size` has a documented maximum.

Assertions are literal strings from the contract. Each name is chosen so one
wrong encoding would change the expected output.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from uuid import UUID

import pytest

from redstring.composition.export import (
    ExportFormat,
    export_graph,
    format_graphml,
    format_mermaid,
)
from redstring.composition.import_graph import parse_graphml
from redstring.composition.themes.models import MAX_PAGE_SIZE
from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, RelationshipId, TenantId
from redstring.domain.normalization import normalize_name
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.graph.adapters.memory import InMemoryGraphStore

TENANT = TenantId(UUID("00000000-0000-4000-8000-0000000000aa"))
ENTITY_ID = UUID("00000000-0000-4000-8000-000000000001")
OTHER_ID = UUID("00000000-0000-4000-8000-000000000002")
REL_ID = UUID("00000000-0000-4000-8000-000000000003")
PROVENANCE = Provenance(
    observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    extraction_method=ExtractionMethod.MANUAL,
    confidence=1.0,
)


def _entity(
    name: str,
    *,
    entity_id: UUID = ENTITY_ID,
    description: str | None = None,
) -> Entity:
    return Entity(
        id=EntityId(entity_id),
        tenant_id=TENANT,
        name=name,
        normalized_name=normalize_name(name),
        entity_type="Person",
        description=description,
        provenance=PROVENANCE,
    )


def _relationship(relationship_type: str = "KNOWS") -> Relationship:
    return Relationship(
        id=RelationshipId(REL_ID),
        tenant_id=TENANT,
        source_entity_id=EntityId(ENTITY_ID),
        target_entity_id=EntityId(OTHER_ID),
        relationship_type=relationship_type,
        confidence=1.0,
    )


def _unvalidated_entity(name: str) -> Entity:
    """An entity whose name the domain would refuse. `model_construct` skips
    validation, so the exporter's own guard is the only thing left to check it.
    """
    return Entity.model_construct(
        id=EntityId(ENTITY_ID),
        tenant_id=TENANT,
        name=name,
        normalized_name=name,
        entity_type="Person",
        description=None,
        provenance=PROVENANCE,
    )


def _data_values(root: ET.Element, key: str) -> set[str]:
    """Every text value of `<data key=...>`, whatever the namespace."""
    return {
        element.text or ""
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "data" and element.get("key") == key
    }


class TestGraphMLRefusesXmlForbiddenCharacters:
    @pytest.mark.parametrize(
        ("name", "description", "code_point"),
        [
            ("Ada\x01", None, "U+0001"),
            ("Ada", "bad\x0bdesc", "U+000B"),
            ("Ada￾", None, "U+FFFE"),
            ("Ada￿", None, "U+FFFF"),
            ("Ada\ud800", None, "U+D800"),
        ],
        ids=["nul-soh-in-name", "vt-in-description", "fffe", "ffff", "lone-surrogate"],
    )
    def test_an_entity_field_with_a_forbidden_character_is_refused(
        self, name: str, description: str | None, code_point: str
    ) -> None:
        entity = (
            _unvalidated_entity(name)
            if "\ud800" in name
            else _entity(name, description=description)
        )

        with pytest.raises(ValueError, match=r"XML 1\.0 cannot represent") as info:
            format_graphml([entity], [])

        message = str(info.value)
        assert str(ENTITY_ID) in message, "the message must name the entity"
        assert code_point in message

    def test_a_relationship_with_a_forbidden_character_names_the_relationship(self) -> None:
        """Relationships are exported only between exported entities, so both
        endpoints are present, or the check would never run."""
        with pytest.raises(ValueError, match=r"XML 1\.0 cannot represent") as info:
            format_graphml(
                [_entity("Ada"), _entity("Bob", entity_id=OTHER_ID)],
                [_relationship("KNOWS\x1f")],
            )

        assert str(REL_ID) in str(info.value)
        assert "relationship_type" in str(info.value)

    def test_tab_and_newline_are_allowed_and_survive_the_round_trip(self) -> None:
        entity = _entity("Ada\tLovelace", description="line one\nline two")

        exported = format_graphml([entity], [])
        entities, _ = parse_graphml(exported, tenant_id=TENANT)

        assert entities[0].name == "Ada\tLovelace"
        assert entities[0].description == "line one\nline two"


class TestExportedGraphMLIsWellFormed:
    def test_every_name_with_markup_or_unicode_parses_with_stdlib_xml(self) -> None:
        names = [
            "Ada <Lovelace>",
            'Tom & "Jerry"',
            "it's",
            "]]>",
            "émile 🙂",
            "tab\there",
        ]
        entities = [
            _entity(name, entity_id=UUID(int=index + 1)) for index, name in enumerate(names)
        ]

        exported = format_graphml(entities, [])
        root = ET.fromstring(exported)

        assert _data_values(root, "d_name") == set(names)

    def test_the_exported_file_round_trips_through_the_import_parser(self) -> None:
        entities = [_entity("Tom & <Jerry>", entity_id=ENTITY_ID)]

        exported = format_graphml(entities, [])
        parsed, _ = parse_graphml(exported, tenant_id=TENANT)

        assert [entity.name for entity in parsed] == ["Tom & <Jerry>"]


class TestMermaidLabelsEncodeMarkup:
    def test_an_html_payload_in_a_name_is_encoded_not_rendered(self) -> None:
        text = format_mermaid([_entity("<img src=x onerror=1>")], [])

        assert "#lt;img src=x onerror=1#gt;" in text
        assert "<img" not in text
        assert "onerror=1>" not in text

    def test_a_literal_quot_code_in_a_name_is_shown_not_decoded(self) -> None:
        """`#` is encoded first, so the name's `#quot;` becomes `#35;quot;`. A
        second pass would encode the code and show `#quot;` as `"`."""
        text = format_mermaid([_entity("#quot;")], [])

        assert "#35;quot;" in text
        assert "#quot;" not in text

    def test_ampersand_and_hash_are_encoded_exactly_once(self) -> None:
        text = format_mermaid([_entity("A &amp; B #1")], [])

        assert "A #amp;amp; B #35;1" in text

    def test_a_double_quote_is_encoded(self) -> None:
        text = format_mermaid([_entity('Say "hi"')], [])

        assert "Say #quot;hi#quot;" in text


class TestPageSizeMaximum:
    async def test_the_shared_maximum_is_accepted(self) -> None:
        store = InMemoryGraphStore()

        exported = await export_graph(
            TENANT,
            graph=store,
            relationships=store,
            format=ExportFormat.JSON_LD,
            page_size=MAX_PAGE_SIZE,
        )

        assert isinstance(exported, str)

    async def test_one_above_the_maximum_is_refused(self) -> None:
        store = InMemoryGraphStore()

        with pytest.raises(ValueError, match=f"at most {MAX_PAGE_SIZE}"):
            await export_graph(
                TENANT,
                graph=store,
                relationships=store,
                format=ExportFormat.JSON_LD,
                page_size=MAX_PAGE_SIZE + 1,
            )

    async def test_zero_is_still_refused(self) -> None:
        store = InMemoryGraphStore()

        with pytest.raises(ValueError, match="at least 1"):
            await export_graph(
                TENANT,
                graph=store,
                relationships=store,
                format=ExportFormat.JSON_LD,
                page_size=0,
            )
