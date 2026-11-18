"""Import hardening: the GraphML reader, the payload size bound, and the trust rule.

Each test states the contract it checks as a literal expectation, so no
assertion is derived from the code it tests.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from uuid import UUID

import pytest
from defusedxml.common import DefusedXmlException

from redstring.composition.export import ExportFormat, format_json_ld
from redstring.composition.import_graph import (
    MAX_IMPORT_CHARACTERS,
    import_graph,
    parse_graphml,
    parse_json_ld,
)
from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, SourceId, TenantId
from redstring.domain.normalization import normalize_name
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.graph.adapters.memory import InMemoryGraphStore

# `redstring.composition` re-exports a function under the module name, so a
# plain `from ... import import_graph` binds the function and not the module.
import_module = importlib.import_module("redstring.composition.import_graph")

TENANT = TenantId(UUID("00000000-0000-4000-8000-0000000000aa"))
ENTITY_ID = "00000000-0000-4000-8000-000000000001"
PROVENANCE = Provenance(
    observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    extraction_method=ExtractionMethod.MANUAL,
    confidence=1.0,
)


def _graphml(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">'
        f"{body}</graphml>"
    )


def _node(name_xml: str, *, nid: str = ENTITY_ID) -> str:
    return f'<node id="{nid}"><data key="d_name">{name_xml}</data></node>'


def _entity(name: str, *, description: str | None = None, source: str | None = None) -> Entity:
    provenance = PROVENANCE
    if source is not None:
        provenance = PROVENANCE.model_copy(update={"source_id": SourceId(source)})
    return Entity(
        id=EntityId(UUID(ENTITY_ID)),
        tenant_id=TENANT,
        name=name,
        normalized_name=normalize_name(name),
        entity_type="Person",
        description=description,
        provenance=provenance,
    )


class TestGraphMLRefusesDocumentTypes:
    """A DOCTYPE or entity declaration is refused by `defusedxml`, not parsed."""

    def test_doctype_is_refused(self) -> None:
        payload = "<!DOCTYPE graphml>" + _graphml(_node("Ada"))

        with pytest.raises(ValueError, match="DTDForbidden") as info:
            parse_graphml(payload, tenant_id=TENANT)

        assert isinstance(info.value, DefusedXmlException)

    def test_internal_entity_declaration_is_refused(self) -> None:
        payload = '<!DOCTYPE graphml [<!ENTITY who "Ada">]>' + _graphml(_node("&who;"))

        with pytest.raises(ValueError, match="DTDForbidden"):
            parse_graphml(payload, tenant_id=TENANT)

    def test_external_entity_is_refused_so_no_file_is_read(self) -> None:
        payload = '<!DOCTYPE graphml [<!ENTITY secret SYSTEM "file:///etc/hostname">]>' + _graphml(
            _node("&secret;")
        )

        with pytest.raises(ValueError, match="DTDForbidden"):
            parse_graphml(payload, tenant_id=TENANT)


class TestGraphMLDataIsText:
    """`<data>` holds text. CDATA and entity references decode to that text."""

    def test_cdata_text_is_kept_verbatim(self) -> None:
        cdata = '<![CDATA[Ada <Lovelace> & "Co"]]>'

        entities, _ = parse_graphml(_graphml(_node(cdata)), tenant_id=TENANT)

        assert entities[0].name == 'Ada <Lovelace> & "Co"'

    def test_predefined_entity_references_are_decoded(self) -> None:
        entities, _ = parse_graphml(_graphml(_node("Ada &amp; Co")), tenant_id=TENANT)

        assert entities[0].name == "Ada & Co"

    def test_nested_markup_inside_data_is_refused_rather_than_flattened(self) -> None:
        """Flattening `Ada<b>X</b>` to `AdaX` would change the value silently."""
        payload = _graphml(_node("Ada<b>Lovelace</b>"))

        with pytest.raises(ValueError, match="text only, not markup"):
            parse_graphml(payload, tenant_id=TENANT)

    def test_a_later_clean_node_does_not_hide_an_earlier_markup_node(self) -> None:
        """The bad row comes first, followed by a good one."""
        bad = _node("Ada<b>X</b>", nid=ENTITY_ID)
        good = _node("Bob", nid="00000000-0000-4000-8000-000000000002")

        with pytest.raises(ValueError, match="text only, not markup"):
            parse_graphml(_graphml(bad + good), tenant_id=TENANT)

    def test_prefixed_element_names_are_read_by_local_name(self) -> None:
        payload = (
            '<?xml version="1.0"?>'
            '<y:graphml xmlns:y="urn:example:y">'
            f'<y:node id="{ENTITY_ID}"><y:data key="d_name">Ada</y:data></y:node>'
            "</y:graphml>"
        )

        entities, _ = parse_graphml(payload, tenant_id=TENANT)

        assert [entity.name for entity in entities] == ["Ada"]


class TestGraphMLIsWellFormed:
    @pytest.mark.parametrize(
        "payload",
        [
            # the node is never closed
            '<?xml version="1.0"?><graphml>'
            f'<node id="{ENTITY_ID}"><data key="d_name">Ada</data>'
            "</graphml>",
            # the root is never closed
            '<?xml version="1.0"?><graphml>'
            f'<node id="{ENTITY_ID}"><data key="d_name">Ada</data></node>',
            # the tags do not match
            '<?xml version="1.0"?><graphml>'
            f'<node id="{ENTITY_ID}"><data key="d_name">Ada</node></data>'
            "</graphml>",
        ],
        ids=["unclosed-node", "unclosed-root", "mismatched-tags"],
    )
    def test_malformed_xml_is_a_value_error(self, payload: str) -> None:
        with pytest.raises(ValueError, match="Malformed GraphML payload"):
            parse_graphml(payload, tenant_id=TENANT)

    def test_a_document_without_a_graphml_root_is_refused(self) -> None:
        with pytest.raises(ValueError, match="missing <graphml> root"):
            parse_graphml("<?xml version='1.0'?><other/>", tenant_id=TENANT)


class TestPayloadSizeBound:
    def test_graphml_at_exactly_the_limit_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _graphml(_node("Ada"))
        monkeypatch.setattr(import_module, "MAX_IMPORT_CHARACTERS", len(payload))

        entities, _ = parse_graphml(payload, tenant_id=TENANT)

        assert [entity.name for entity in entities] == ["Ada"]

    def test_graphml_one_character_over_the_limit_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _graphml(_node("Ada"))
        monkeypatch.setattr(import_module, "MAX_IMPORT_CHARACTERS", len(payload) - 1)

        with pytest.raises(ValueError, match="the limit is"):
            parse_graphml(payload, tenant_id=TENANT)

    def test_json_ld_one_character_over_the_limit_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = '{"@context": {}, "@graph": []}'
        monkeypatch.setattr(import_module, "MAX_IMPORT_CHARACTERS", len(payload) - 1)

        with pytest.raises(ValueError, match="the limit is"):
            parse_json_ld(payload, tenant_id=TENANT)

    def test_the_documented_limit_is_enforced_before_parsing(self) -> None:
        """The text is not valid for either format, so only the size check can
        produce the `limit` message. Its absence would give a parse error."""
        oversize = "x" * (MAX_IMPORT_CHARACTERS + 1)

        with pytest.raises(ValueError, match="the limit is"):
            parse_json_ld(oversize, tenant_id=TENANT)
        with pytest.raises(ValueError, match="the limit is"):
            parse_graphml(oversize, tenant_id=TENANT)


class TestDeeplyNestedJson:
    def test_nesting_past_the_recursion_limit_is_a_value_error(self) -> None:
        payload = '{"@graph": ' + "[" * 100_000 + "]" * 100_000 + "}"

        with pytest.raises(ValueError, match="nests too deeply") as info:
            parse_json_ld(payload, tenant_id=TENANT)

        assert isinstance(info.value.__cause__, RecursionError)


class TestTrustRule:
    """An import file is trusted. Its provenance is kept, and an existing id
    in the target tenant is replaced, not merged."""

    async def test_json_ld_import_replaces_the_entity_and_keeps_its_provenance(self) -> None:
        store = InMemoryGraphStore()
        old = _entity("Old Name", description="Old note", source="doc-old")
        await store.upsert_entities([old])

        payload = format_json_ld([_entity("New Name", source="doc-new")], [])
        report = await import_graph(payload, TENANT, graph=store, format=ExportFormat.JSON_LD)

        stored = await store.get_entity(EntityId(UUID(ENTITY_ID)), TENANT)
        assert report.entities_imported == 1
        assert stored is not None
        assert stored.name == "New Name"
        assert stored.description is None, "replaced, not merged: the old note must be gone"
        assert stored.provenance.source_id == SourceId("doc-new")

    async def test_graphml_import_replaces_the_entity_in_the_target_tenant(self) -> None:
        store = InMemoryGraphStore()
        await store.upsert_entities([_entity("Old Name", description="Old note")])

        payload = _graphml(_node("New Name"))
        await import_graph(payload, TENANT, graph=store, format=ExportFormat.GRAPHML)

        stored = await store.get_entity(EntityId(UUID(ENTITY_ID)), TENANT)
        assert stored is not None
        assert stored.name == "New Name"
        assert stored.description is None
