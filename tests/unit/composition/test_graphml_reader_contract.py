"""Contract tests for `composition.graphml_reader.read_graphml_elements`.

Documented contract (module docstring): a `<data>` element holds text only,
and a `<node>` or `<edge>` that lacks its identifying attributes raises
`ValueError`. Elements that are not `<data>` are not properties, so they must
not leak into a node's data map.
"""

from __future__ import annotations

import pytest

from redstring.composition.graphml_reader import read_graphml_elements

_OPEN = '<graphml xmlns="http://graphml.graphdrawing.org/xmlns"><graph id="G">'
_CLOSE = "</graph></graphml>"


def _doc(body: str) -> str:
    return f"{_OPEN}{body}{_CLOSE}"


def test_child_elements_that_are_not_data_are_not_properties() -> None:
    # `<desc>` carries a `key` attribute, so a reader that stopped checking the
    # tag would file its text under `d_desc`. The contract says it is not a property.
    payload = _doc(
        '<node id="n1"><desc key="d_desc">not a property</desc><data key="d_name">Ada</data></node>'
    )

    nodes, edges = read_graphml_elements(payload)

    assert nodes == [("n1", {"d_name": "Ada"})]
    assert edges == []


def test_data_element_without_key_is_not_a_property() -> None:
    payload = _doc('<node id="n1"><data>orphan text</data><data key="d_name">Ada</data></node>')

    nodes, _ = read_graphml_elements(payload)

    assert nodes == [("n1", {"d_name": "Ada"})]


def test_node_without_id_is_refused() -> None:
    payload = _doc('<node><data key="d_name">Ada</data></node>')

    with pytest.raises(ValueError, match="GraphML <node> missing 'id' attribute"):
        read_graphml_elements(payload)


def test_edge_missing_target_is_refused_after_a_valid_edge() -> None:
    # A valid edge comes first, so the refusal is for the second row, not the first.
    payload = _doc(
        '<node id="n1"/><node id="n2"/>'
        '<edge id="e1" source="n1" target="n2"/>'
        '<edge id="e2" source="n1"/>'
    )

    with pytest.raises(ValueError, match="requires 'id', 'source', and 'target'"):
        read_graphml_elements(payload)
