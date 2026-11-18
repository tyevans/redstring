# Import and export a graph

Use this guide to move a knowledge graph in or out of a file. The library
supports three formats: Mermaid, JSON-LD and GraphML.

| Format | Export | Import | Use |
|---|---|---|---|
| `ExportFormat.MERMAID` | yes | no | A diagram for a Markdown viewer |
| `ExportFormat.JSON_LD` | yes | yes | A lossless round trip |
| `ExportFormat.GRAPHML` | yes | yes | Gephi, Cytoscape, NetworkX |

Mermaid is a visualization format. It cannot be imported.

## Export a tenant's graph

`export_graph` reads one tenant. It returns a string. It does not write a
file.

```python
from redstring import ExportFormat, export_graph
from redstring.graph.adapters.memory import InMemoryGraphStore

store = InMemoryGraphStore()

text = await export_graph(
    tenant_id,
    graph=store,
    relationships=store,
    format=ExportFormat.JSON_LD,
)
```

`page_size` sets how many entities one read returns. The default is 500.
The value must be from 1 to 1000. A value outside that range raises
`ValueError`. The limit bounds the memory of one read. It does not limit
the size of the export.

## Import a file

`import_graph` parses a payload and writes its entities and relationships.

```python
from redstring import ExportFormat, import_graph

report = await import_graph(
    payload,
    tenant_id,
    graph=store,
    format=ExportFormat.JSON_LD,
)
print(report.entities_imported, report.relationships_imported)
```

An import raises `ValueError` for every malformed payload. This includes a
payload that is too large, JSON that is nested too deeply, and a GraphML
document that the XML rules refuse.

## The trust rule

**An import file is trusted for provenance and for entity identity.**

- Provenance is kept as the file states it. The `sourceId`, `observedAt`,
  `extractionMethod` and `confidence` values are written as found. The
  library does not check them against any source.
- A provenance field that the file omits is not invented. The method
  becomes `imported`, the confidence becomes `IMPORTED_CONFIDENCE` (0.5), and
  the observation time becomes the import time. A relationship without a
  confidence also gets 0.5. GraphML carries no provenance, so every GraphML
  entity is marked this way. Pass `imported_at=` to record a fixed time
  instead of the time of the call.
- An entity whose id already exists in the target tenant is replaced. It is
  not merged. A field that the file does not state becomes empty. A
  description that the file omits is removed.

Do not import a file from a source that you do not trust. An import
overwrites the entities that share an id with the file. Read the file before
you import it into a tenant that holds data you need to keep.

## Limits on the payload

| Limit | Value | What happens above it |
|---|---|---|
| Payload size | 64 Mi characters | `ValueError`. The payload is never truncated. |
| JSON nesting | The interpreter recursion limit | `ValueError`. The `RecursionError` does not reach the caller. |
| `page_size` on export | 1 to 1000 | `ValueError` |

The payload size limit is a constant in `redstring.composition.import_graph`.
It is checked before the payload is parsed.

## GraphML is read as XML, safely

The GraphML parser uses `defusedxml`. The parser applies these rules:

- A document with a `<!DOCTYPE>` declaration is refused. This includes any
  entity declaration.
- A malformed or unclosed document is refused.
- A `<data>` element holds text only. A `<data>` element that contains
  child markup is refused. The library does not flatten the markup into
  text.
- CDATA sections are read as plain text.

The GraphML exporter refuses a name, description or type that contains a
character that XML 1.0 cannot represent. The character codes are U+0000 to
U+0008, U+000B, U+000C, U+000E to U+001F, U+FFFE, U+FFFF, and unpaired
surrogates. The error names the entity or relationship. No partial file is
returned.

## Mermaid labels are encoded

A Mermaid viewer can render a label as HTML. The exporter encodes these
characters in each label:

| Character | Written as |
|---|---|
| `<` | `#lt;` |
| `>` | `#gt;` |
| `&` | `#amp;` |
| `#` | `#35;` |
| `"` | `#quot;` |

The exporter encodes `#` first. A name that contains `&amp;` shows
`&amp;` in the diagram and does not show `&`. A name that contains
`#quot;` shows `#quot;` in the diagram and does not show `"`.

## Related

- [Use the write model](use-the-write-model.md) for how entities enter the
  graph in the first place.
- [Consolidate duplicate entities](consolidate-duplicate-entities.md) before
  you export a graph that you plan to share.
