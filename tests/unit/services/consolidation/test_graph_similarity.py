"""
Unit tests for GraphSimilarityService.

Tests the graph-based similarity computation with mocked Neo4j driver.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from kg_builder.schemas.similarity import SimilarityType
from kg_builder.services.consolidation.graph_similarity import (
    GraphNeighborhood,
    GraphSimilarityService,
    get_graph_similarity_service,
)


class TestGraphNeighborhood:
    """Tests for GraphNeighborhood dataclass."""

    def test_all_neighbors_combines_sets(self):
        """Test all_neighbors combines outgoing and incoming."""
        outgoing = {uuid4(), uuid4()}
        incoming = {uuid4()}

        n = GraphNeighborhood(
            entity_id=uuid4(),
            outgoing_neighbors=outgoing,
            incoming_neighbors=incoming,
        )

        assert n.all_neighbors == outgoing | incoming
        assert n.neighbor_count == 3

    def test_all_neighbors_deduplicates(self):
        """Test shared neighbors are counted once."""
        shared = uuid4()
        n = GraphNeighborhood(
            entity_id=uuid4(),
            outgoing_neighbors={shared, uuid4()},
            incoming_neighbors={shared},
        )

        assert n.neighbor_count == 2

    def test_outgoing_count(self):
        """Test outgoing count property."""
        n = GraphNeighborhood(
            entity_id=uuid4(),
            outgoing_neighbors={uuid4(), uuid4(), uuid4()},
            incoming_neighbors={uuid4()},
        )

        assert n.outgoing_count == 3
        assert n.incoming_count == 1

    def test_relationship_type_set(self):
        """Test relationship_type_set returns unique types."""
        id1, id2, id3 = uuid4(), uuid4(), uuid4()
        n = GraphNeighborhood(
            entity_id=uuid4(),
            outgoing_neighbors={id1, id2, id3},
            relationship_types={
                id1: "RELATES_TO",
                id2: "EXTENDS",
                id3: "RELATES_TO",  # Duplicate type
            },
        )

        assert n.relationship_type_set == {"RELATES_TO", "EXTENDS"}

    def test_empty_neighborhood(self):
        """Test empty neighborhood has zero counts."""
        n = GraphNeighborhood(entity_id=uuid4())

        assert n.neighbor_count == 0
        assert n.outgoing_count == 0
        assert n.incoming_count == 0
        assert n.all_neighbors == set()
        assert n.relationship_type_set == set()


class TestGraphSimilarityServiceInit:
    """Tests for GraphSimilarityService initialization."""

    def test_init_with_driver(self):
        """Test initialization with Neo4j driver."""
        mock_driver = MagicMock()
        service = GraphSimilarityService(neo4j_driver=mock_driver)

        assert service._driver is mock_driver


class TestJaccardSimilarity:
    """Tests for Jaccard similarity computation."""

    @pytest.fixture
    def service(self):
        """Create service with mock driver."""
        return GraphSimilarityService(neo4j_driver=MagicMock())

    def test_identical_neighbors(self, service):
        """Test identical neighbor sets have similarity 1.0."""
        ids = {uuid4(), uuid4(), uuid4()}

        n1 = GraphNeighborhood(entity_id=uuid4(), outgoing_neighbors=ids)
        n2 = GraphNeighborhood(entity_id=uuid4(), outgoing_neighbors=ids)

        similarity = service.compute_jaccard_similarity(n1, n2)

        assert similarity == pytest.approx(1.0)

    def test_no_overlap(self, service):
        """Test non-overlapping neighbors have similarity 0.0."""
        n1 = GraphNeighborhood(
            entity_id=uuid4(),
            outgoing_neighbors={uuid4(), uuid4()},
        )
        n2 = GraphNeighborhood(
            entity_id=uuid4(),
            outgoing_neighbors={uuid4(), uuid4()},
        )

        similarity = service.compute_jaccard_similarity(n1, n2)

        assert similarity == pytest.approx(0.0)

    def test_partial_overlap(self, service):
        """Test partial overlap has correct Jaccard value."""
        shared = uuid4()
        n1 = GraphNeighborhood(
            entity_id=uuid4(),
            outgoing_neighbors={shared, uuid4()},  # 2 neighbors
        )
        n2 = GraphNeighborhood(
            entity_id=uuid4(),
            outgoing_neighbors={shared, uuid4()},  # 2 neighbors
        )
        # Intersection = 1 (shared), Union = 3 (shared + 2 unique)
        # Jaccard = 1/3 = 0.333...

        similarity = service.compute_jaccard_similarity(n1, n2)

        assert similarity == pytest.approx(1 / 3)

    def test_empty_neighborhoods_returns_neutral(self, service):
        """Test both empty neighborhoods return 0.5 (neutral)."""
        n1 = GraphNeighborhood(entity_id=uuid4())
        n2 = GraphNeighborhood(entity_id=uuid4())

        similarity = service.compute_jaccard_similarity(n1, n2)

        assert similarity == pytest.approx(0.5)

    def test_one_empty_neighborhood(self, service):
        """Test one empty neighborhood returns 0.0."""
        n1 = GraphNeighborhood(
            entity_id=uuid4(),
            outgoing_neighbors={uuid4()},
        )
        n2 = GraphNeighborhood(entity_id=uuid4())

        similarity = service.compute_jaccard_similarity(n1, n2)

        assert similarity == pytest.approx(0.0)


class TestRelationshipTypeSimilarity:
    """Tests for relationship type similarity."""

    @pytest.fixture
    def service(self):
        """Create service with mock driver."""
        return GraphSimilarityService(neo4j_driver=MagicMock())

    def test_identical_types(self, service):
        """Test identical relationship types have similarity 1.0."""
        id1, id2 = uuid4(), uuid4()
        n1 = GraphNeighborhood(
            entity_id=uuid4(),
            outgoing_neighbors={id1},
            relationship_types={id1: "EXTENDS"},
        )
        n2 = GraphNeighborhood(
            entity_id=uuid4(),
            outgoing_neighbors={id2},
            relationship_types={id2: "EXTENDS"},
        )

        similarity = service.compute_relationship_type_similarity(n1, n2)

        assert similarity == pytest.approx(1.0)

    def test_different_types(self, service):
        """Test different relationship types have similarity 0.0."""
        id1, id2 = uuid4(), uuid4()
        n1 = GraphNeighborhood(
            entity_id=uuid4(),
            outgoing_neighbors={id1},
            relationship_types={id1: "EXTENDS"},
        )
        n2 = GraphNeighborhood(
            entity_id=uuid4(),
            outgoing_neighbors={id2},
            relationship_types={id2: "IMPLEMENTS"},
        )

        similarity = service.compute_relationship_type_similarity(n1, n2)

        assert similarity == pytest.approx(0.0)

    def test_partial_type_overlap(self, service):
        """Test partial type overlap."""
        id1, id2 = uuid4(), uuid4()
        id3, id4 = uuid4(), uuid4()
        n1 = GraphNeighborhood(
            entity_id=uuid4(),
            outgoing_neighbors={id1, id2},
            relationship_types={id1: "EXTENDS", id2: "USES"},
        )
        n2 = GraphNeighborhood(
            entity_id=uuid4(),
            outgoing_neighbors={id3, id4},
            relationship_types={id3: "EXTENDS", id4: "IMPLEMENTS"},
        )
        # Types: n1 = {EXTENDS, USES}, n2 = {EXTENDS, IMPLEMENTS}
        # Intersection = 1, Union = 3
        # Similarity = 1/3

        similarity = service.compute_relationship_type_similarity(n1, n2)

        assert similarity == pytest.approx(1 / 3)

    def test_both_empty_returns_neutral(self, service):
        """Test both empty relationship types return 0.5."""
        n1 = GraphNeighborhood(entity_id=uuid4())
        n2 = GraphNeighborhood(entity_id=uuid4())

        similarity = service.compute_relationship_type_similarity(n1, n2)

        assert similarity == pytest.approx(0.5)


class TestGetNeighborhood:
    """Tests for neighborhood retrieval from Neo4j."""

    @pytest.fixture
    def mock_driver(self):
        """Create mock Neo4j driver."""
        driver = MagicMock()
        return driver

    @pytest.fixture
    def service(self, mock_driver):
        """Create service with mock driver."""
        return GraphSimilarityService(neo4j_driver=mock_driver)

    @pytest.mark.asyncio
    async def test_get_neighborhood_queries_both_directions(
        self, service, mock_driver
    ):
        """Test neighborhood query retrieves outgoing and incoming."""
        entity_id = uuid4()

        # Create mock session and results
        mock_session = AsyncMock()
        mock_driver.session.return_value.__aenter__.return_value = mock_session

        # Mock outgoing result
        mock_outgoing_result = AsyncMock()
        neighbor_id = uuid4()
        mock_outgoing_result.data.return_value = [
            {"neighbor_id": str(neighbor_id), "rel_type": "EXTENDS"}
        ]

        # Mock incoming result
        mock_incoming_result = AsyncMock()
        mock_incoming_result.data.return_value = []

        mock_session.run.side_effect = [mock_outgoing_result, mock_incoming_result]

        result = await service.get_neighborhood(entity_id)

        assert mock_session.run.call_count == 2
        assert isinstance(result, GraphNeighborhood)
        assert neighbor_id in result.outgoing_neighbors

    @pytest.mark.asyncio
    async def test_get_neighborhood_handles_invalid_uuid(
        self, service, mock_driver
    ):
        """Test neighborhood handles invalid neighbor IDs gracefully."""
        entity_id = uuid4()

        mock_session = AsyncMock()
        mock_driver.session.return_value.__aenter__.return_value = mock_session

        # Return invalid UUID
        mock_result = AsyncMock()
        mock_result.data.return_value = [
            {"neighbor_id": "not-a-uuid", "rel_type": "EXTENDS"}
        ]
        mock_session.run.side_effect = [mock_result, AsyncMock(data=AsyncMock(return_value=[]))]

        result = await service.get_neighborhood(entity_id)

        # Should not raise, just skip invalid
        assert result.neighbor_count == 0


class TestComputeSimilarity:
    """Tests for overall graph similarity computation."""

    @pytest.fixture
    def mock_driver(self):
        """Create mock Neo4j driver."""
        return MagicMock()

    @pytest.fixture
    def service(self, mock_driver):
        """Create service with mock driver."""
        return GraphSimilarityService(neo4j_driver=mock_driver)

    @pytest.mark.asyncio
    async def test_compute_similarity_combined_score(self, service):
        """Test compute_similarity returns weighted combination."""
        entity_a_id = uuid4()
        entity_b_id = uuid4()

        shared_neighbor = uuid4()

        # Mock get_neighborhood to return overlapping neighborhoods
        async def mock_get_neighborhood(entity_id, max_neighbors=100):
            return GraphNeighborhood(
                entity_id=entity_id,
                outgoing_neighbors={shared_neighbor},
                relationship_types={shared_neighbor: "EXTENDS"},
            )

        with patch.object(service, "get_neighborhood", mock_get_neighborhood):
            result = await service.compute_similarity(entity_a_id, entity_b_id)

        # Both have identical neighbors and types
        # Jaccard = 1.0, RelType = 1.0
        # Combined = 0.7 * 1.0 + 0.3 * 1.0 = 1.0
        assert result == pytest.approx(1.0)


class TestComputeSimilarityScores:
    """Tests for full similarity score computation."""

    @pytest.fixture
    def service(self):
        """Create service with mock driver."""
        return GraphSimilarityService(neo4j_driver=MagicMock())

    @pytest.mark.asyncio
    async def test_returns_graph_similarity_scores(self, service):
        """Test returns GraphSimilarityScores with correct types."""
        entity_a_id = uuid4()
        entity_b_id = uuid4()

        # Mock to return empty neighborhoods
        async def mock_get_neighborhood(entity_id, max_neighbors=100):
            return GraphNeighborhood(entity_id=entity_id)

        with patch.object(service, "get_neighborhood", mock_get_neighborhood):
            result = await service.compute_similarity_scores(entity_a_id, entity_b_id)

        assert result.neighborhood is not None
        assert result.neighborhood.similarity_type == SimilarityType.GRAPH_NEIGHBORHOOD
        assert result.neighborhood.computation_time_ms is not None


class TestBatchSimilarity:
    """Tests for batch similarity computation."""

    @pytest.fixture
    def mock_driver(self):
        """Create mock Neo4j driver."""
        driver = MagicMock()
        return driver

    @pytest.fixture
    def service(self, mock_driver):
        """Create service with mock driver."""
        return GraphSimilarityService(neo4j_driver=mock_driver)

    @pytest.mark.asyncio
    async def test_batch_empty_candidates(self, service):
        """Test batch with empty candidates returns empty dict."""
        result = await service.compute_similarities_batch(uuid4(), [])

        assert result == {}

    @pytest.mark.asyncio
    async def test_batch_queries_once(self, service, mock_driver):
        """Test batch uses single Cypher query."""
        entity_id = uuid4()
        candidate_ids = [uuid4(), uuid4(), uuid4()]

        mock_session = AsyncMock()
        mock_driver.session.return_value.__aenter__.return_value = mock_session

        mock_result = AsyncMock()
        mock_result.data.return_value = [
            {"candidate_id": str(cid), "similarity": 0.5}
            for cid in candidate_ids
        ]
        mock_session.run.return_value = mock_result

        result = await service.compute_similarities_batch(entity_id, candidate_ids)

        # Should only run one query
        mock_session.run.assert_called_once()
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_batch_scores_returns_dict(self, service, mock_driver):
        """Test batch_scores returns dict of GraphSimilarityScores."""
        entity_id = uuid4()
        candidate_ids = [uuid4(), uuid4()]

        mock_session = AsyncMock()
        mock_driver.session.return_value.__aenter__.return_value = mock_session

        mock_result = AsyncMock()
        mock_result.data.return_value = [
            {"candidate_id": str(cid), "similarity": 0.5}
            for cid in candidate_ids
        ]
        mock_session.run.return_value = mock_result

        result = await service.compute_batch_scores(entity_id, candidate_ids)

        assert len(result) == 2
        for cid in candidate_ids:
            assert cid in result
            assert result[cid].neighborhood is not None


class TestGetGraphSimilarityService:
    """Tests for factory function."""

    @pytest.mark.asyncio
    async def test_returns_service_when_driver_available(self):
        """Test factory returns service when driver available."""
        mock_driver = MagicMock()
        mock_client = MagicMock()
        mock_client.get_async_driver.return_value = mock_driver

        with patch(
            "kg_builder.graph.client.get_neo4j_client"
        ) as mock_get_client:
            mock_get_client.return_value = mock_client

            result = await get_graph_similarity_service()

            assert result is not None
            assert isinstance(result, GraphSimilarityService)

    @pytest.mark.asyncio
    async def test_returns_none_when_client_unavailable(self):
        """Test factory returns None when client unavailable."""
        with patch(
            "kg_builder.graph.client.get_neo4j_client"
        ) as mock_get_client:
            mock_get_client.return_value = None

            result = await get_graph_similarity_service()

            assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        """Test factory returns None on error."""
        with patch(
            "kg_builder.graph.client.get_neo4j_client"
        ) as mock_get_client:
            mock_get_client.side_effect = Exception("Connection error")

            result = await get_graph_similarity_service()

            assert result is None
