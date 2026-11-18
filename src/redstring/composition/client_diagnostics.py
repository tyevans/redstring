"""Diagnostic, lineage, portability, and governance mixin for the Redstring client facade."""

from __future__ import annotations

from typing import TYPE_CHECKING

from redstring.composition.consolidation_sweep import (
    ConsolidationPreview,
    TenantConsolidationReport,
    consolidate_tenant,
    preview_consolidation,
)
from redstring.composition.export import ExportFormat, export_graph
from redstring.composition.import_graph import GraphImportReport, import_graph
from redstring.composition.lineage import (
    CorpusHealthReport,
    EntityLineageReport,
    SourceLineageReport,
    analyze_corpus,
    trace_entity_lineage,
    trace_source_lineage,
)
from redstring.composition.paths import PathExplanation, explain_path
from redstring.composition.retrieval_diagnostics import (
    ChunkRetrievalExplanation,
    EntityRetrievalExplanation,
    explain_chunk_retrieval,
    explain_entity_retrieval,
)
from redstring.composition.schema_audit import (
    SchemaConformanceReport,
    audit_schema_conformance,
    format_schema_mermaid,
)
from redstring.composition.topology_diagnostics import (
    GraphDiffReport,
    GraphTopologyReport,
    analyze_topology,
    diff_graphs,
)
from redstring.consolidation.policy import HIGH_SIMILARITY, LOW_SIMILARITY

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.composition.build_graph import Consolidator
    from redstring.consolidation.protocols import MergeAdjudicator
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, SourceId, TenantId
    from redstring.domain.retrieval import RetrievalMode
    from redstring.extraction.domains.models import DomainSchema
    from redstring.ports.chunk_store import ChunkStore
    from redstring.ports.embedding_provider import EmbeddingProvider
    from redstring.ports.graph_store import GraphStore
    from redstring.ports.vector_store import VectorStore


class _RedstringDiagnosticsMixin:
    """Diagnostic, portability, lineage, and governance methods mixed into `Redstring`."""

    _graph_store: GraphStore
    _vector_store: VectorStore | None
    _chunk_store: ChunkStore | None
    _embeddings: EmbeddingProvider | None
    _consolidator: Consolidator

    def _active_tenant(self, tenant_id: TenantId | None) -> TenantId:
        raise NotImplementedError

    async def explain_entity_retrieval(
        self,
        query: str,
        *,
        k: int = 5,
        overfetch: int = 3,
        entity_types: Sequence[str] | None = None,
        mode: RetrievalMode | None = None,
        tenant_id: TenantId | None = None,
    ) -> EntityRetrievalExplanation:
        """Return per-channel RRF attribution and diagnostics for entity retrieval."""
        return await explain_entity_retrieval(
            query,
            self._active_tenant(tenant_id),
            graph=self._graph_store,
            embeddings=self._embeddings,
            vectors=self._vector_store,
            k=k,
            overfetch=overfetch,
            entity_types=entity_types,
            mode=mode,
        )

    async def explain_chunk_retrieval(
        self,
        query: str,
        *,
        k: int = 5,
        overfetch: int = 3,
        mode: RetrievalMode | None = None,
        tenant_id: TenantId | None = None,
    ) -> ChunkRetrievalExplanation:
        """Return per-channel RRF and BM25 diagnostics for chunk retrieval."""
        if self._chunk_store is None:
            raise ValueError("No chunk store configured for this Redstring client")
        return await explain_chunk_retrieval(
            query,
            self._active_tenant(tenant_id),
            chunks=self._chunk_store,
            embeddings=self._embeddings,
            k=k,
            overfetch=overfetch,
            mode=mode,
        )

    async def explain_passage_retrieval(
        self,
        query: str,
        *,
        k: int = 5,
        overfetch: int = 3,
        mode: RetrievalMode | None = None,
        tenant_id: TenantId | None = None,
    ) -> ChunkRetrievalExplanation:
        """Return per-channel RRF and BM25 diagnostics for passage retrieval."""
        return await self.explain_chunk_retrieval(
            query, k=k, overfetch=overfetch, mode=mode, tenant_id=tenant_id
        )

    async def explain_path(
        self,
        source: EntityId | str,
        target: EntityId | str,
        *,
        max_depth: int = 3,
        tenant_id: TenantId | None = None,
    ) -> PathExplanation | None:
        """Find the shortest relationship path between `source` and `target` with citations."""
        return await explain_path(
            source,
            target,
            tenant_id=self._active_tenant(tenant_id),
            graph=self._graph_store,
            chunks=self._chunk_store,
            max_depth=max_depth,
        )

    async def export_graph(
        self,
        *,
        format: ExportFormat | str = ExportFormat.MERMAID,
        entity_types: Sequence[str] | None = None,
        relationship_types: Sequence[str] | None = None,
        center_entity_id: EntityId | str | None = None,
        max_depth: int = 1,
        tenant_id: TenantId | None = None,
    ) -> str:
        """Serialize the tenant's knowledge graph (or a filtered subgraph)."""
        return await export_graph(
            self._active_tenant(tenant_id),
            graph=self._graph_store,
            relationships=self._graph_store,
            format=format,
            entity_types=entity_types,
            relationship_types=relationship_types,
            center_entity_id=center_entity_id,
            max_depth=max_depth,
        )

    async def import_graph(
        self,
        payload: str,
        *,
        format: ExportFormat | str = ExportFormat.JSON_LD,
        tenant_id: TenantId | None = None,
    ) -> GraphImportReport:
        """Import a JSON-LD or GraphML payload into the tenant's graph store."""
        return await import_graph(
            payload,
            self._active_tenant(tenant_id),
            graph=self._graph_store,
            relationships=self._graph_store,
            format=format,
        )

    async def analyze_topology(
        self, *, top_k_hubs: int = 10, tenant_id: TenantId | None = None
    ) -> GraphTopologyReport:
        """Compute structural topology metrics and hub centrality for the active tenant."""
        return await analyze_topology(
            self._active_tenant(tenant_id),
            graph=self._graph_store,
            relationships=self._graph_store,
            top_k_hubs=top_k_hubs,
        )

    async def diff_graphs(
        self, target_tenant_id: TenantId, *, base_tenant_id: TenantId | None = None
    ) -> GraphDiffReport:
        """Compare the base tenant snapshot against `target_tenant_id`."""
        return await diff_graphs(
            self._active_tenant(base_tenant_id),
            target_tenant_id,
            graph=self._graph_store,
            relationships=self._graph_store,
        )

    async def trace_source_lineage(
        self, source_id: SourceId, *, tenant_id: TenantId | None = None
    ) -> SourceLineageReport:
        """Trace every entity, relationship, and chunk originating from `source_id`."""
        return await trace_source_lineage(
            source_id,
            self._active_tenant(tenant_id),
            graph=self._graph_store,
            relationships=self._graph_store,
            chunks=self._chunk_store,
        )

    async def trace_entity_lineage(
        self, entity_id: EntityId | str, *, tenant_id: TenantId | None = None
    ) -> EntityLineageReport | None:
        """Trace full evidence lineage for `entity_id`."""
        return await trace_entity_lineage(
            entity_id,
            self._active_tenant(tenant_id),
            graph=self._graph_store,
            relationships=self._graph_store,
            aliases=self._graph_store,
            chunks=self._chunk_store,
        )

    async def analyze_corpus(
        self, *, source_ids: Sequence[SourceId] | None = None, tenant_id: TenantId | None = None
    ) -> CorpusHealthReport:
        """Compute provenance and chunk coverage health statistics across the tenant."""
        return await analyze_corpus(
            self._active_tenant(tenant_id),
            graph=self._graph_store,
            relationships=self._graph_store,
            chunks=self._chunk_store,
            source_ids=source_ids,
        )

    async def preview_consolidation(
        self,
        subject: Entity | EntityId | str | None = None,
        *,
        high: float = HIGH_SIMILARITY,
        low: float = LOW_SIMILARITY,
        use_graph_signal: bool = True,
        tenant_id: TenantId | None = None,
    ) -> ConsolidationPreview:
        """Preview candidate entity merges without mutating the store."""
        return await preview_consolidation(
            self._active_tenant(tenant_id),
            graph_store=self._graph_store,
            subject=subject,
            vector_store=self._vector_store,
            use_graph_signal=use_graph_signal,
            high=high,
            low=low,
        )

    async def consolidate_tenant(
        self,
        *,
        adjudicator: MergeAdjudicator | None = None,
        high: float = HIGH_SIMILARITY,
        low: float = LOW_SIMILARITY,
        tenant_id: TenantId | None = None,
    ) -> TenantConsolidationReport:
        """Sweep all canonical entities in the tenant and execute confirmed merges."""
        return await consolidate_tenant(
            self._active_tenant(tenant_id),
            store=self._graph_store,
            consolidator=self._consolidator,
            adjudicator=adjudicator,
            high=high,
            low=low,
        )

    async def audit_schema_conformance(
        self, schema: DomainSchema | str, *, tenant_id: TenantId | None = None
    ) -> SchemaConformanceReport:
        """Audit stored entities and relationships against a DomainSchema ontology."""
        return await audit_schema_conformance(
            self._active_tenant(tenant_id),
            schema,
            graph=self._graph_store,
            relationships=self._graph_store,
        )

    @staticmethod
    def format_schema_mermaid(schema: DomainSchema | str) -> str:
        """Render a DomainSchema ontology as a Mermaid flowchart diagram."""
        return format_schema_mermaid(schema)
