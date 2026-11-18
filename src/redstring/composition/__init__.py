"""The top layer: modules holding collaborators no lower layer may hold together.

`pyproject.toml` states that a module wanting in here has to say what it
composes. There are three:

- `build_graph` composes `LlmProvider` + `GraphStore` (+ optionally
  `EmbeddingProvider` + `VectorStore`). `extraction` may not import
  `projections`, so nothing below can hold both halves.
- `retrieval` composes `EmbeddingProvider` + `VectorStore` + `GraphStore` in
  `Retriever`, and `EmbeddingProvider` + `ChunkStore` in `ChunkRetriever`.
  `vector` and `graph` are siblings that may not import each other and
  neither may import `llm`, so no sibling can hold `Retriever`'s three.
  `ChunkRetriever` joins a second forbidden pair in the same module: `llm`
  and `chunks` are likewise siblings forbidden from importing each other, so
  no sibling can hold both of its two.
- `themes` composes `EntityReader` + `RelationshipStore` + `LlmProvider` +
  optionally `ChunkReader` in `summarize_themes`. `graph`, `llm` and `chunks`
  are three siblings forbidden from importing each other, so no sibling can
  hold them; ADR 0042 records the decision the module implements.
- `index_documents` composes `Chunker` + `ChunkStore` -- the same
  `extraction` + `projections` pair `build_graph` names, so it is admitted by
  the argument already recorded rather than by a new one.

This was one module until retrieval arrived; see the ADR amending 0007.

Everything `redstring.composition` exported as a module is re-exported here, so
no import path changed in the move. That includes `Consolidator` and
`ConsolidationReport`, which `redstring/__init__.py` imports from this name.

**`build_graph` is both a submodule and the function in it, and the function
wins.** That is deliberate and it is what keeps `from redstring.composition
import build_graph` meaning what it meant before the move. The order is what
makes it deterministic: the `from` below loads the submodule (which binds
`build_graph` on this package to the *module*), then rebinds the name to the
function, and a later `import redstring.composition.build_graph` finds the
module already in `sys.modules` and does not re-bind. Renaming the submodule to
un-shadow it would break the public import path this package exists to
preserve.

`index_documents` is the same shape for the same reason, and the same import
order settles it.
"""

from __future__ import annotations

from redstring.composition.batch import (
    BatchDocumentError,
    BatchGraphReport,
    build_graph_batch,
    build_graph_stream,
)
from redstring.composition.build_graph import (
    AUTO,
    AutoDomain,
    ConsolidationReport,
    Consolidator,
    GraphBuildReport,
    build_graph,
)
from redstring.composition.client import Redstring
from redstring.composition.coercion import coerce_document, content_addressed_source_id
from redstring.composition.consolidation_sweep import (
    ConsolidationPreview,
    PreviewCandidate,
    TenantConsolidationReport,
    consolidate_tenant,
    preview_consolidation,
)
from redstring.composition.export import (
    ExportFormat,
    export_graph,
    format_graphml,
    format_json_ld,
    format_mermaid,
)
from redstring.composition.import_graph import (
    GraphImportReport,
    import_graph,
    parse_graphml,
    parse_json_ld,
)
from redstring.composition.index_documents import IndexReport, index_documents
from redstring.composition.lineage import (
    CorpusHealthReport,
    EntityLineageReport,
    SourceLineageReport,
    analyze_corpus,
    trace_entity_lineage,
    trace_source_lineage,
)
from redstring.composition.paths import PathExplanation, explain_path
from redstring.composition.retrieval import ChunkRetriever, Retriever
from redstring.composition.retrieval_diagnostics import (
    ChunkRetrievalExplanation,
    EntityRetrievalExplanation,
    ExplainedChunkMatch,
    ExplainedEntityMatch,
    explain_chunk_retrieval,
    explain_entity_retrieval,
)
from redstring.composition.schema_audit import (
    SchemaConformanceReport,
    SchemaViolation,
    audit_schema_conformance,
    format_schema_mermaid,
)
from redstring.composition.themes import Theme, ThemeReport, summarize_themes
from redstring.composition.topology_diagnostics import (
    EntityHub,
    EntityModification,
    GraphDiffReport,
    GraphTopologyReport,
    RelationshipModification,
    analyze_topology,
    diff_graphs,
)

__all__ = [
    "AUTO",
    "AutoDomain",
    "BatchDocumentError",
    "BatchGraphReport",
    "ChunkRetrievalExplanation",
    "ChunkRetriever",
    "ConsolidationPreview",
    "ConsolidationReport",
    "Consolidator",
    "CorpusHealthReport",
    "EntityHub",
    "EntityLineageReport",
    "EntityModification",
    "EntityRetrievalExplanation",
    "ExplainedChunkMatch",
    "ExplainedEntityMatch",
    "ExportFormat",
    "GraphBuildReport",
    "GraphDiffReport",
    "GraphImportReport",
    "GraphTopologyReport",
    "IndexReport",
    "PathExplanation",
    "PreviewCandidate",
    "Redstring",
    "RelationshipModification",
    "Retriever",
    "SchemaConformanceReport",
    "SchemaViolation",
    "SourceLineageReport",
    "TenantConsolidationReport",
    "Theme",
    "ThemeReport",
    "analyze_corpus",
    "analyze_topology",
    "audit_schema_conformance",
    "build_graph",
    "build_graph_batch",
    "build_graph_stream",
    "coerce_document",
    "consolidate_tenant",
    "content_addressed_source_id",
    "diff_graphs",
    "explain_chunk_retrieval",
    "explain_entity_retrieval",
    "explain_path",
    "export_graph",
    "format_graphml",
    "format_json_ld",
    "format_mermaid",
    "format_schema_mermaid",
    "import_graph",
    "index_documents",
    "parse_graphml",
    "parse_json_ld",
    "preview_consolidation",
    "summarize_themes",
    "trace_entity_lineage",
    "trace_source_lineage",
]
