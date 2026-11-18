"""Read GraphML `<node>` and `<edge>` elements from untrusted XML.

This module is the only place the library parses XML from a caller. It uses
`defusedxml`, which refuses DTDs and entity declarations, and it returns plain
tuples, so the domain conversion in `import_graph` handles no XML types.

Rules, stated once here:

- A payload with a DOCTYPE, an entity declaration, or an external reference
  raises `ValueError`.
- A malformed or unclosed document raises `ValueError`.
- A `<data>` element holds text only. Child markup raises `ValueError`. It is
  never flattened, because flattening would change the value with no signal.
- CDATA sections are read as plain text.
"""

from __future__ import annotations

from typing import Any

from defusedxml.ElementTree import ParseError, fromstring

__all__ = ["GraphMLEdge", "GraphMLNode", "read_graphml_elements"]

#: `(id, data)` for one `<node>`. `data` maps each `<data key=...>` to its text.
GraphMLNode = tuple[str, dict[str, str]]

#: `(id, source, target, data)` for one `<edge>`.
GraphMLEdge = tuple[str, str, str, dict[str, str]]


def _local_name(tag: str) -> str:
    """Drop a namespace: `{uri}node` and `node` both name the element `node`."""
    return tag.rsplit("}", 1)[-1]


# `element` is typed `Any`: defusedxml does not export `Element`, and importing
# it from stdlib `xml` (even under TYPE_CHECKING) trips bandit B405.
def _data_values(element: Any) -> dict[str, str]:  # noqa: ANN401
    """Collect the `<data key=...>` children of one `<node>` or `<edge>`.

    Raises `ValueError` when a `<data>` element contains child markup.
    """
    values: dict[str, str] = {}
    for child in element:
        if _local_name(child.tag) != "data":
            continue
        key = child.get("key")
        if key is None:
            continue
        if len(child):
            raise ValueError(f"GraphML <data key={key!r}> must contain text only, not markup")
        values[key] = child.text or ""
    return values


def read_graphml_elements(payload: str) -> tuple[list[GraphMLNode], list[GraphMLEdge]]:
    """Read every `<node>` and `<edge>` in a GraphML document.

    Raises `ValueError` for every malformed input. `defusedxml` raises
    `ValueError` subclasses for DTDs and entity declarations, and a parse
    failure is converted here, so the caller sees one exception type.
    """
    try:
        root = fromstring(payload, forbid_dtd=True)
    except ParseError as exc:
        raise ValueError(f"Malformed GraphML payload: {exc}") from exc
    if _local_name(root.tag) != "graphml":
        raise ValueError("Malformed GraphML payload: missing <graphml> root element")

    nodes: list[GraphMLNode] = []
    edges: list[GraphMLEdge] = []
    for element in root.iter():
        local = _local_name(element.tag)
        if local == "node":
            nid = element.get("id", "")
            if not nid:
                raise ValueError("GraphML <node> missing 'id' attribute")
            nodes.append((nid, _data_values(element)))
        elif local == "edge":
            eid = element.get("id", "")
            src = element.get("source", "")
            tgt = element.get("target", "")
            if not eid or not src or not tgt:
                raise ValueError(
                    "GraphML <edge> requires 'id', 'source', and 'target'; "
                    f"got id={eid!r}, source={src!r}, target={tgt!r}"
                )
            edges.append((eid, src, tgt, _data_values(element)))
    return nodes, edges
