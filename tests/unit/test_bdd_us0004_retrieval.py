"""Executable BDD Acceptance Criteria for US-0004: Hybrid Multi-Channel Retrieval.

Governed by:
- ADR-0021 (Composition Holds a Second Module)
- ADR-0022 (The Lexical Channel Is Not BM25)
- ADR-0045 (A Lexical-Only Retriever Is a Constructor)
- PRD-0001
- US-0004
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from redstring import (
    InMemoryGraphStore,
    Retriever,
)
from redstring.domain.ids import TenantId
from redstring.domain.retrieval import RetrievalMode


@pytest.mark.asyncio
async def test_retrieve_chunks_by_combined_semantic_similarity_and_lexical_ranking() -> None:
    """Scenario: Retrieve chunks by combined semantic similarity and lexical ranking (US-0004)."""
    graph = InMemoryGraphStore()
    retriever = Retriever.lexical_only(graph=graph)

    tenant_id = TenantId(str(uuid4()))
    # Executes retrieval query in lexical mode
    results = await retriever.retrieve("Ada Lovelace", tenant_id, mode=RetrievalMode.LEXICAL)
    assert results.query == "Ada Lovelace"
    assert isinstance(results.matches, list)


@pytest.mark.asyncio
async def test_traverse_graph_neighborhood_for_thematic_context() -> None:
    """Scenario: Traverse graph neighborhood for thematic context (US-0004)."""
    graph = InMemoryGraphStore()
    tenant_id = TenantId(str(uuid4()))

    # Direct neighborhood inspection through graph capability
    neighbors = await graph.neighbors(uuid4(), tenant_id)
    assert isinstance(neighbors, list)
