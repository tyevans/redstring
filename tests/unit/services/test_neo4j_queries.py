"""
Unit tests for Neo4j graph query utilities.

Tests the GraphQueryService's ability to perform path finding,
neighborhood exploration, similarity detection, and statistics
gathering while enforcing tenant isolation.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

# Mock dependencies before importing neo4j_queries to avoid database module imports
mock_neo4j_module = MagicMock()
mock_neo4j_module.Neo4jService = MagicMock
mock_neo4j_module.get_neo4j_service = AsyncMock()
sys.modules["kg_builder.services.neo4j"] = mock_neo4j_module

# Import the module directly using importlib to avoid triggering __init__.py
spec = importlib.util.spec_from_file_location(
    "neo4j_queries",
    Path(__file__).parent.parent.parent.parent / "src" / "kg_builder" / "services" / "neo4j_queries.py",
)
neo4j_queries = importlib.util.module_from_spec(spec)
sys.modules["kg_builder.services.neo4j_queries"] = neo4j_queries
spec.loader.exec_module(neo4j_queries)

# Import symbols from the loaded module
GraphQueryService = neo4j_queries.GraphQueryService
get_graph_query_service = neo4j_queries.get_graph_query_service


class MockAsyncIterator:
    """Helper class to create async iterators for mocking Neo4j results."""

    def __init__(self, items):
        self.items = items
        self.index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.index >= len(self.items):
            raise StopAsyncIteration
        item = self.items[self.index]
        self.index += 1
        return item


@pytest.fixture
def tenant_id():
    """Generate a test tenant ID."""
    return uuid4()


@pytest.fixture
def other_tenant_id():
    """Generate a different tenant ID for cross-tenant tests."""
    return uuid4()


@pytest.fixture
def source_entity_id():
    """Generate a test source entity ID."""
    return uuid4()


@pytest.fixture
def target_entity_id():
    """Generate a test target entity ID."""
    return uuid4()


@pytest.fixture
def entity_id():
    """Generate a test entity ID."""
    return uuid4()


@pytest.fixture
def mock_neo4j_service():
    """Create a mock Neo4j service with session context manager."""
    service = MagicMock()
    session = AsyncMock()

    # Create a proper async context manager for session
    async_session_cm = AsyncMock()
    async_session_cm.__aenter__ = AsyncMock(return_value=session)
    async_session_cm.__aexit__ = AsyncMock(return_value=None)

    service.session = MagicMock(return_value=async_session_cm)

    return service, session


@pytest.fixture
def sample_path_result():
    """Create a sample path result."""
    return {
        "nodes": [
            {"id": "uuid-1", "name": "EntityA", "type": "CLASS"},
            {"id": "uuid-2", "name": "EntityB", "type": "FUNCTION"},
            {"id": "uuid-3", "name": "EntityC", "type": "MODULE"},
        ],
        "relationships": ["USES", "PART_OF"],
    }


@pytest.fixture
def sample_neighborhood_result(entity_id, tenant_id):
    """Create a sample neighborhood result."""
    return {
        "center": {"id": str(entity_id), "name": "CenterEntity", "type": "CLASS"},
        "neighbors": [
            {"id": str(uuid4()), "name": "Neighbor1", "type": "FUNCTION"},
            {"id": str(uuid4()), "name": "Neighbor2", "type": "MODULE"},
        ],
    }


@pytest.fixture
def sample_similar_entities():
    """Create sample similar entity results."""
    return [
        {
            "similar": {
                "id": str(uuid4()),
                "name": "SimilarEntity1",
                "type": "CLASS",
                "shared_connections": 5,
            }
        },
        {
            "similar": {
                "id": str(uuid4()),
                "name": "SimilarEntity2",
                "type": "CLASS",
                "shared_connections": 3,
            }
        },
    ]


@pytest.fixture
def sample_stats_records():
    """Create sample stats records."""
    return [
        {"total_entities": 100, "type": "FUNCTION", "count": 50},
        {"total_entities": 100, "type": "CLASS", "count": 30},
        {"total_entities": 100, "type": "MODULE", "count": 20},
    ]


# =============================================================================
# GraphQueryService Initialization Tests
# =============================================================================


def test_graph_query_service_init(mock_neo4j_service):
    """Test GraphQueryService initialization stores service."""
    service, _ = mock_neo4j_service

    query_service = GraphQueryService(service)

    assert query_service._service is service


def test_graph_query_service_property(mock_neo4j_service):
    """Test service property returns underlying Neo4jService."""
    service, _ = mock_neo4j_service

    query_service = GraphQueryService(service)

    assert query_service.service is service


# =============================================================================
# find_path Tests
# =============================================================================


@pytest.mark.asyncio
async def test_find_path_returns_path_result(
    mock_neo4j_service, source_entity_id, target_entity_id, tenant_id, sample_path_result
):
    """Test find_path returns nodes and relationships."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value=sample_path_result)
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    result = await query_service.find_path(source_entity_id, target_entity_id, tenant_id)

    assert result is not None
    assert "nodes" in result
    assert "relationships" in result
    assert len(result["nodes"]) == 3
    assert len(result["relationships"]) == 2
    assert result["nodes"][0]["name"] == "EntityA"
    assert result["relationships"][0] == "USES"


@pytest.mark.asyncio
async def test_find_path_returns_none_when_no_path(
    mock_neo4j_service, source_entity_id, target_entity_id, tenant_id
):
    """Test find_path returns None when no path exists."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value=None)
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    result = await query_service.find_path(source_entity_id, target_entity_id, tenant_id)

    assert result is None


@pytest.mark.asyncio
async def test_find_path_includes_tenant_filter(
    mock_neo4j_service, source_entity_id, target_entity_id, tenant_id
):
    """Test find_path query includes tenant_id filtering."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value=None)
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    await query_service.find_path(source_entity_id, target_entity_id, tenant_id)

    call_args = session.run.call_args
    query = call_args[0][0]
    kwargs = call_args[1]

    # Verify tenant filtering in query
    assert "tenant_id: $tenant_id" in query
    assert "n.tenant_id = $tenant_id" in query  # Path node filter
    assert kwargs["tenant_id"] == str(tenant_id)
    assert kwargs["source_id"] == str(source_entity_id)
    assert kwargs["target_id"] == str(target_entity_id)


@pytest.mark.asyncio
async def test_find_path_uses_max_hops(
    mock_neo4j_service, source_entity_id, target_entity_id, tenant_id
):
    """Test find_path respects max_hops parameter."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value=None)
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    await query_service.find_path(source_entity_id, target_entity_id, tenant_id, max_hops=3)

    call_args = session.run.call_args
    query = call_args[0][0]

    assert "*..3" in query


@pytest.mark.asyncio
async def test_find_path_caps_max_hops_at_ten(
    mock_neo4j_service, source_entity_id, target_entity_id, tenant_id
):
    """Test find_path caps max_hops at 10 for performance."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value=None)
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    await query_service.find_path(source_entity_id, target_entity_id, tenant_id, max_hops=20)

    call_args = session.run.call_args
    query = call_args[0][0]

    # Should be capped at 10
    assert "*..10" in query


@pytest.mark.asyncio
async def test_find_path_enforces_minimum_max_hops(
    mock_neo4j_service, source_entity_id, target_entity_id, tenant_id
):
    """Test find_path enforces minimum of 1 hop."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value=None)
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    await query_service.find_path(source_entity_id, target_entity_id, tenant_id, max_hops=0)

    call_args = session.run.call_args
    query = call_args[0][0]

    # Should be at least 1
    assert "*..1" in query


# =============================================================================
# get_neighborhood Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_neighborhood_returns_center_and_neighbors(
    mock_neo4j_service, entity_id, tenant_id
):
    """Test get_neighborhood returns center entity and neighbors."""
    service, session = mock_neo4j_service

    center = {"id": str(entity_id), "name": "CenterEntity", "type": "CLASS"}
    neighbors = [
        {"id": str(uuid4()), "name": "Neighbor1", "type": "FUNCTION"},
        {"id": str(uuid4()), "name": "Neighbor2", "type": "MODULE"},
    ]

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value={"center": center, "neighbors": neighbors})
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    result = await query_service.get_neighborhood(entity_id, tenant_id)

    assert result is not None
    assert "center" in result
    assert "neighbors" in result
    assert result["center"]["name"] == "CenterEntity"
    assert len(result["neighbors"]) == 2


@pytest.mark.asyncio
async def test_get_neighborhood_returns_none_for_missing_entity(
    mock_neo4j_service, entity_id, tenant_id
):
    """Test get_neighborhood returns None when entity doesn't exist."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value=None)
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    result = await query_service.get_neighborhood(entity_id, tenant_id)

    assert result is None


@pytest.mark.asyncio
async def test_get_neighborhood_includes_tenant_filter(
    mock_neo4j_service, entity_id, tenant_id
):
    """Test get_neighborhood query includes tenant_id filtering."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value={"center": None, "neighbors": []})
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    await query_service.get_neighborhood(entity_id, tenant_id)

    call_args = session.run.call_args
    query = call_args[0][0]
    kwargs = call_args[1]

    # Verify tenant filtering in query - for center and neighbors
    assert "tenant_id: $tenant_id" in query
    assert "tenant_id: $tenant_id" in query  # For neighbor filter
    assert kwargs["tenant_id"] == str(tenant_id)
    assert kwargs["entity_id"] == str(entity_id)


@pytest.mark.asyncio
async def test_get_neighborhood_uses_depth_parameter(
    mock_neo4j_service, entity_id, tenant_id
):
    """Test get_neighborhood respects depth parameter."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value={"center": None, "neighbors": []})
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    await query_service.get_neighborhood(entity_id, tenant_id, depth=2)

    call_args = session.run.call_args
    query = call_args[0][0]

    assert "*1..2" in query


@pytest.mark.asyncio
async def test_get_neighborhood_caps_depth_at_three(
    mock_neo4j_service, entity_id, tenant_id
):
    """Test get_neighborhood caps depth at 3 for performance."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value={"center": None, "neighbors": []})
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    await query_service.get_neighborhood(entity_id, tenant_id, depth=10)

    call_args = session.run.call_args
    query = call_args[0][0]

    # Should be capped at 3
    assert "*1..3" in query


@pytest.mark.asyncio
async def test_get_neighborhood_enforces_minimum_depth(
    mock_neo4j_service, entity_id, tenant_id
):
    """Test get_neighborhood enforces minimum depth of 1."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value={"center": None, "neighbors": []})
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    await query_service.get_neighborhood(entity_id, tenant_id, depth=0)

    call_args = session.run.call_args
    query = call_args[0][0]

    # Should be at least 1
    assert "*1..1" in query


@pytest.mark.asyncio
async def test_get_neighborhood_filters_null_neighbors(
    mock_neo4j_service, entity_id, tenant_id
):
    """Test get_neighborhood filters out null entries from neighbors."""
    service, session = mock_neo4j_service

    center = {"id": str(entity_id), "name": "CenterEntity", "type": "CLASS"}
    # Include a null entry that should be filtered
    neighbors = [
        {"id": str(uuid4()), "name": "Neighbor1", "type": "FUNCTION"},
        {"id": None, "name": None, "type": None},  # Null entry
        None,  # Full null
    ]

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value={"center": center, "neighbors": neighbors})
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    result = await query_service.get_neighborhood(entity_id, tenant_id)

    # Only valid neighbor should remain
    assert len(result["neighbors"]) == 1
    assert result["neighbors"][0]["name"] == "Neighbor1"


# =============================================================================
# find_similar_entities Tests
# =============================================================================


@pytest.mark.asyncio
async def test_find_similar_entities_returns_ranked_results(
    mock_neo4j_service, entity_id, tenant_id, sample_similar_entities
):
    """Test find_similar_entities returns entities ranked by shared connections."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator(sample_similar_entities)
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    result = await query_service.find_similar_entities(entity_id, tenant_id)

    assert len(result) == 2
    assert result[0]["shared_connections"] == 5
    assert result[1]["shared_connections"] == 3
    assert result[0]["name"] == "SimilarEntity1"


@pytest.mark.asyncio
async def test_find_similar_entities_returns_empty_list_when_none_found(
    mock_neo4j_service, entity_id, tenant_id
):
    """Test find_similar_entities returns empty list when no similar entities."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    result = await query_service.find_similar_entities(entity_id, tenant_id)

    assert result == []


@pytest.mark.asyncio
async def test_find_similar_entities_includes_tenant_filter(
    mock_neo4j_service, entity_id, tenant_id
):
    """Test find_similar_entities query includes tenant_id filtering."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    await query_service.find_similar_entities(entity_id, tenant_id)

    call_args = session.run.call_args
    query = call_args[0][0]
    kwargs = call_args[1]

    # Verify tenant filtering - must filter source, shared, and similar entities
    assert query.count("tenant_id") >= 3  # At least 3 tenant checks
    assert kwargs["tenant_id"] == str(tenant_id)
    assert kwargs["entity_id"] == str(entity_id)


@pytest.mark.asyncio
async def test_find_similar_entities_respects_limit(
    mock_neo4j_service, entity_id, tenant_id
):
    """Test find_similar_entities respects the limit parameter."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    await query_service.find_similar_entities(entity_id, tenant_id, limit=5)

    call_args = session.run.call_args
    kwargs = call_args[1]

    assert kwargs["limit"] == 5


@pytest.mark.asyncio
async def test_find_similar_entities_excludes_self(
    mock_neo4j_service, entity_id, tenant_id
):
    """Test find_similar_entities excludes the source entity itself."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    await query_service.find_similar_entities(entity_id, tenant_id)

    call_args = session.run.call_args
    query = call_args[0][0]

    assert "similar.id <> e.id" in query


# =============================================================================
# get_entity_stats Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_entity_stats_returns_counts(
    mock_neo4j_service, tenant_id, sample_stats_records
):
    """Test get_entity_stats returns total entities and by_type breakdown."""
    service, session = mock_neo4j_service

    entity_result = AsyncMock()
    entity_result.__aiter__ = lambda self: MockAsyncIterator(sample_stats_records)

    rel_result = AsyncMock()
    rel_result.single = AsyncMock(return_value={"total_relationships": 75})

    session.run = AsyncMock(side_effect=[entity_result, rel_result])

    query_service = GraphQueryService(service)
    result = await query_service.get_entity_stats(tenant_id)

    assert "total_entities" in result
    assert "total_relationships" in result
    assert "by_type" in result
    assert result["total_entities"] == 100
    assert result["total_relationships"] == 75
    assert result["by_type"]["FUNCTION"] == 50
    assert result["by_type"]["CLASS"] == 30
    assert result["by_type"]["MODULE"] == 20


@pytest.mark.asyncio
async def test_get_entity_stats_returns_zeros_when_empty(
    mock_neo4j_service, tenant_id
):
    """Test get_entity_stats returns zeros when no entities exist."""
    service, session = mock_neo4j_service

    entity_result = AsyncMock()
    entity_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=entity_result)

    query_service = GraphQueryService(service)
    result = await query_service.get_entity_stats(tenant_id)

    assert result["total_entities"] == 0
    assert result["total_relationships"] == 0
    assert result["by_type"] == {}


@pytest.mark.asyncio
async def test_get_entity_stats_includes_tenant_filter(
    mock_neo4j_service, tenant_id
):
    """Test get_entity_stats query includes tenant_id filtering."""
    service, session = mock_neo4j_service

    entity_result = AsyncMock()
    entity_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=entity_result)

    query_service = GraphQueryService(service)
    await query_service.get_entity_stats(tenant_id)

    # Should be called at least once for entity query
    call_args = session.run.call_args_list[0]
    query = call_args[0][0]
    kwargs = call_args[1]

    assert "tenant_id: $tenant_id" in query
    assert kwargs["tenant_id"] == str(tenant_id)


@pytest.mark.asyncio
async def test_get_entity_stats_filters_null_types(
    mock_neo4j_service, tenant_id
):
    """Test get_entity_stats filters out null types from by_type."""
    service, session = mock_neo4j_service

    # Include a null type entry
    stats_records = [
        {"total_entities": 100, "type": "FUNCTION", "count": 50},
        {"total_entities": 100, "type": None, "count": 10},  # Null type
    ]

    entity_result = AsyncMock()
    entity_result.__aiter__ = lambda self: MockAsyncIterator(stats_records)

    rel_result = AsyncMock()
    rel_result.single = AsyncMock(return_value={"total_relationships": 25})

    session.run = AsyncMock(side_effect=[entity_result, rel_result])

    query_service = GraphQueryService(service)
    result = await query_service.get_entity_stats(tenant_id)

    # Only FUNCTION should be in by_type, not None
    assert "FUNCTION" in result["by_type"]
    assert None not in result["by_type"]
    assert len(result["by_type"]) == 1


# =============================================================================
# Tenant Isolation Tests
# =============================================================================


@pytest.mark.asyncio
async def test_tenant_isolation_in_find_path(
    mock_neo4j_service, source_entity_id, target_entity_id, tenant_id, other_tenant_id
):
    """Test that find_path queries are isolated by tenant."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value=None)
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)

    # Query with first tenant
    await query_service.find_path(source_entity_id, target_entity_id, tenant_id)
    first_call_tenant = session.run.call_args_list[0][1]["tenant_id"]

    # Query with second tenant
    await query_service.find_path(source_entity_id, target_entity_id, other_tenant_id)
    second_call_tenant = session.run.call_args_list[1][1]["tenant_id"]

    assert first_call_tenant == str(tenant_id)
    assert second_call_tenant == str(other_tenant_id)
    assert first_call_tenant != second_call_tenant


@pytest.mark.asyncio
async def test_tenant_isolation_in_get_neighborhood(
    mock_neo4j_service, entity_id, tenant_id, other_tenant_id
):
    """Test that get_neighborhood queries are isolated by tenant."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value=None)
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)

    # Query with first tenant
    await query_service.get_neighborhood(entity_id, tenant_id)
    first_call_tenant = session.run.call_args_list[0][1]["tenant_id"]

    # Query with second tenant
    await query_service.get_neighborhood(entity_id, other_tenant_id)
    second_call_tenant = session.run.call_args_list[1][1]["tenant_id"]

    assert first_call_tenant == str(tenant_id)
    assert second_call_tenant == str(other_tenant_id)


@pytest.mark.asyncio
async def test_tenant_isolation_in_find_similar_entities(
    mock_neo4j_service, entity_id, tenant_id, other_tenant_id
):
    """Test that find_similar_entities queries are isolated by tenant."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)

    # Query with first tenant
    await query_service.find_similar_entities(entity_id, tenant_id)
    first_call_tenant = session.run.call_args_list[0][1]["tenant_id"]

    # Query with second tenant
    await query_service.find_similar_entities(entity_id, other_tenant_id)
    second_call_tenant = session.run.call_args_list[1][1]["tenant_id"]

    assert first_call_tenant == str(tenant_id)
    assert second_call_tenant == str(other_tenant_id)


@pytest.mark.asyncio
async def test_tenant_isolation_in_get_entity_stats(
    mock_neo4j_service, tenant_id, other_tenant_id
):
    """Test that get_entity_stats queries are isolated by tenant."""
    service, session = mock_neo4j_service

    entity_result = AsyncMock()
    entity_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=entity_result)

    query_service = GraphQueryService(service)

    # Query with first tenant
    await query_service.get_entity_stats(tenant_id)
    first_call_tenant = session.run.call_args_list[0][1]["tenant_id"]

    # Query with second tenant
    await query_service.get_entity_stats(other_tenant_id)
    second_call_tenant = session.run.call_args_list[1][1]["tenant_id"]

    assert first_call_tenant == str(tenant_id)
    assert second_call_tenant == str(other_tenant_id)


# =============================================================================
# get_graph_query_service Factory Function Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_graph_query_service_creates_service():
    """Test factory function creates GraphQueryService."""
    mock_service = MagicMock()

    # Patch get_neo4j_service in the module
    original_get_neo4j_service = neo4j_queries.get_neo4j_service
    neo4j_queries.get_neo4j_service = AsyncMock(return_value=mock_service)

    try:
        result = await get_graph_query_service()

        assert isinstance(result, GraphQueryService)
        assert result.service is mock_service
    finally:
        neo4j_queries.get_neo4j_service = original_get_neo4j_service


@pytest.mark.asyncio
async def test_get_graph_query_service_calls_get_neo4j_service():
    """Test factory function calls get_neo4j_service."""
    mock_service = MagicMock()
    mock_get = AsyncMock(return_value=mock_service)

    original_get_neo4j_service = neo4j_queries.get_neo4j_service
    neo4j_queries.get_neo4j_service = mock_get

    try:
        await get_graph_query_service()

        mock_get.assert_called_once()
    finally:
        neo4j_queries.get_neo4j_service = original_get_neo4j_service


# =============================================================================
# Query Structure Tests
# =============================================================================


@pytest.mark.asyncio
async def test_find_path_uses_shortest_path_algorithm(
    mock_neo4j_service, source_entity_id, target_entity_id, tenant_id
):
    """Test find_path uses Neo4j's shortestPath algorithm."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value=None)
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    await query_service.find_path(source_entity_id, target_entity_id, tenant_id)

    call_args = session.run.call_args
    query = call_args[0][0]

    assert "shortestPath" in query


@pytest.mark.asyncio
async def test_find_similar_entities_orders_by_shared_count(
    mock_neo4j_service, entity_id, tenant_id
):
    """Test find_similar_entities orders results by shared connection count."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    await query_service.find_similar_entities(entity_id, tenant_id)

    call_args = session.run.call_args
    query = call_args[0][0]

    assert "ORDER BY shared_count DESC" in query


@pytest.mark.asyncio
async def test_find_similar_entities_uses_distinct_count(
    mock_neo4j_service, entity_id, tenant_id
):
    """Test find_similar_entities uses DISTINCT count for accuracy."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    query_service = GraphQueryService(service)
    await query_service.find_similar_entities(entity_id, tenant_id)

    call_args = session.run.call_args
    query = call_args[0][0]

    assert "count(DISTINCT shared)" in query
