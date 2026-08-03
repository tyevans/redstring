"""
Unit tests for Neo4j tenant isolation service.

Tests the TenantScopedNeo4jService's ability to enforce tenant isolation
for all graph database queries and operations.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

# Mock dependencies before importing neo4j_tenant to avoid database module imports
mock_neo4j_module = MagicMock()
mock_neo4j_module.Neo4jService = MagicMock
mock_neo4j_module.get_neo4j_service = AsyncMock()
sys.modules["kg_builder.services.neo4j"] = mock_neo4j_module

# Import the module directly using importlib to avoid triggering __init__.py
spec = importlib.util.spec_from_file_location(
    "neo4j_tenant",
    Path(__file__).parent.parent.parent.parent
    / "src"
    / "kg_builder"
    / "services"
    / "neo4j_tenant.py",
)
neo4j_tenant = importlib.util.module_from_spec(spec)
sys.modules["kg_builder.services.neo4j_tenant"] = neo4j_tenant
spec.loader.exec_module(neo4j_tenant)

# Import symbols from the loaded module
TenantScopedNeo4jService = neo4j_tenant.TenantScopedNeo4jService
get_tenant_scoped_neo4j = neo4j_tenant.get_tenant_scoped_neo4j


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
def sample_entity(tenant_id, entity_id):
    """Create a sample entity record."""
    return {
        "id": str(entity_id),
        "tenant_id": str(tenant_id),
        "type": "FUNCTION",
        "name": "extract_entities",
        "description": "Extracts entities from text",
        "properties": {"signature": "def extract_entities(text: str)"},
        "node_id": "4:abc:123",
    }


@pytest.fixture
def sample_related_entities(tenant_id):
    """Create sample related entity records."""
    return [
        {
            "id": str(uuid4()),
            "tenant_id": str(tenant_id),
            "type": "CLASS",
            "name": "EntityExtractor",
            "node_id": "4:abc:124",
            "relationship_type": "USES",
            "relationship_direction": "outgoing",
        },
        {
            "id": str(uuid4()),
            "tenant_id": str(tenant_id),
            "type": "MODULE",
            "name": "extraction",
            "node_id": "4:abc:125",
            "relationship_type": "PART_OF",
            "relationship_direction": "outgoing",
        },
    ]


# =============================================================================
# TenantScopedNeo4jService Initialization Tests
# =============================================================================


def test_tenant_scoped_service_init(mock_neo4j_service, tenant_id):
    """Test TenantScopedNeo4jService initialization stores service and tenant."""
    service, _ = mock_neo4j_service

    scoped_service = TenantScopedNeo4jService(service, tenant_id)

    assert scoped_service._service is service
    assert scoped_service._tenant_id == tenant_id


def test_tenant_scoped_service_tenant_id_property(mock_neo4j_service, tenant_id):
    """Test tenant_id property returns correct value."""
    service, _ = mock_neo4j_service

    scoped_service = TenantScopedNeo4jService(service, tenant_id)

    assert scoped_service.tenant_id == tenant_id


def test_tenant_scoped_service_service_property(mock_neo4j_service, tenant_id):
    """Test service property returns underlying Neo4jService."""
    service, _ = mock_neo4j_service

    scoped_service = TenantScopedNeo4jService(service, tenant_id)

    assert scoped_service.service is service


# =============================================================================
# get_entity Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_entity_calls_service_with_tenant_id(
    mock_neo4j_service, tenant_id, entity_id, sample_entity
):
    """Test get_entity calls underlying service with tenant_id."""
    service, _ = mock_neo4j_service
    service.get_entity_node = AsyncMock(return_value=sample_entity)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    result = await scoped_service.get_entity(entity_id)

    service.get_entity_node.assert_called_once_with(entity_id, tenant_id)
    assert result == sample_entity


@pytest.mark.asyncio
async def test_get_entity_returns_none_for_wrong_tenant(mock_neo4j_service, tenant_id, entity_id):
    """Test get_entity returns None when entity doesn't belong to tenant."""
    service, _ = mock_neo4j_service
    # Service returns None because entity doesn't exist for this tenant
    service.get_entity_node = AsyncMock(return_value=None)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    result = await scoped_service.get_entity(entity_id)

    assert result is None


@pytest.mark.asyncio
async def test_get_entity_returns_none_for_nonexistent_entity(mock_neo4j_service, tenant_id):
    """Test get_entity returns None for entity that doesn't exist."""
    service, _ = mock_neo4j_service
    service.get_entity_node = AsyncMock(return_value=None)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    result = await scoped_service.get_entity(uuid4())

    assert result is None


# =============================================================================
# get_entity_by_name Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_entity_by_name_includes_tenant_filter(
    mock_neo4j_service, tenant_id, sample_entity
):
    """Test get_entity_by_name query includes tenant_id filter."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value={"entity": sample_entity})
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    await scoped_service.get_entity_by_name("extract_entities")

    # Verify the query was called with tenant_id parameter
    call_args = session.run.call_args
    assert call_args is not None
    query = call_args[0][0]
    kwargs = call_args[1]

    assert "tenant_id: $tenant_id" in query
    assert kwargs["tenant_id"] == str(tenant_id)
    assert kwargs["name"] == "extract_entities"


@pytest.mark.asyncio
async def test_get_entity_by_name_returns_entity(mock_neo4j_service, tenant_id, sample_entity):
    """Test get_entity_by_name returns entity when found."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value={"entity": sample_entity})
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    result = await scoped_service.get_entity_by_name("extract_entities")

    assert result == sample_entity


@pytest.mark.asyncio
async def test_get_entity_by_name_returns_none_when_not_found(mock_neo4j_service, tenant_id):
    """Test get_entity_by_name returns None when entity not found."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value=None)
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    result = await scoped_service.get_entity_by_name("nonexistent")

    assert result is None


# =============================================================================
# get_related_entities Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_related_entities_filters_by_tenant(
    mock_neo4j_service, tenant_id, entity_id, sample_related_entities
):
    """Test get_related_entities filters both source and target by tenant."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator(
        [{"entity": e} for e in sample_related_entities]
    )
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    await scoped_service.get_related_entities(entity_id)

    call_args = session.run.call_args
    query = call_args[0][0]
    kwargs = call_args[1]

    # Verify tenant filtering on both source and related entities
    assert "tenant_id: $tenant_id" in query  # Source entity filter
    assert "related.tenant_id = $tenant_id" in query  # Related entity filter
    assert kwargs["tenant_id"] == str(tenant_id)


@pytest.mark.asyncio
async def test_get_related_entities_returns_list(
    mock_neo4j_service, tenant_id, entity_id, sample_related_entities
):
    """Test get_related_entities returns list of related entities."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator(
        [{"entity": e} for e in sample_related_entities]
    )
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    result = await scoped_service.get_related_entities(entity_id)

    assert len(result) == 2
    assert result[0]["name"] == "EntityExtractor"
    assert result[1]["name"] == "extraction"


@pytest.mark.asyncio
async def test_get_related_entities_with_relationship_type(
    mock_neo4j_service, tenant_id, entity_id
):
    """Test get_related_entities filters by relationship type."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    await scoped_service.get_related_entities(entity_id, relationship_type="USES")

    call_args = session.run.call_args
    query = call_args[0][0]

    assert ":USES" in query


@pytest.mark.asyncio
async def test_get_related_entities_outgoing_direction(mock_neo4j_service, tenant_id, entity_id):
    """Test get_related_entities with outgoing direction."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    await scoped_service.get_related_entities(entity_id, direction="outgoing")

    call_args = session.run.call_args
    query = call_args[0][0]

    assert "(e)-[r]->(related)" in query


@pytest.mark.asyncio
async def test_get_related_entities_incoming_direction(mock_neo4j_service, tenant_id, entity_id):
    """Test get_related_entities with incoming direction."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    await scoped_service.get_related_entities(entity_id, direction="incoming")

    call_args = session.run.call_args
    query = call_args[0][0]

    assert "(e)<-[r]-(related)" in query


@pytest.mark.asyncio
async def test_get_related_entities_both_direction(mock_neo4j_service, tenant_id, entity_id):
    """Test get_related_entities with both directions (default)."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    await scoped_service.get_related_entities(entity_id, direction="both")

    call_args = session.run.call_args
    query = call_args[0][0]

    assert "(e)-[r]-(related)" in query


@pytest.mark.asyncio
async def test_get_related_entities_empty_result(mock_neo4j_service, tenant_id, entity_id):
    """Test get_related_entities returns empty list when no relations."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    result = await scoped_service.get_related_entities(entity_id)

    assert result == []


# =============================================================================
# search_entities Tests
# =============================================================================


@pytest.mark.asyncio
async def test_search_entities_scopes_to_tenant(mock_neo4j_service, tenant_id):
    """Test search_entities filters results by tenant_id."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    await scoped_service.search_entities("extract")

    call_args = session.run.call_args
    query = call_args[0][0]
    kwargs = call_args[1]

    assert "tenant_id = $tenant_id" in query
    assert kwargs["tenant_id"] == str(tenant_id)


@pytest.mark.asyncio
async def test_search_entities_uses_case_insensitive_search(mock_neo4j_service, tenant_id):
    """Test search_entities performs case-insensitive search."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    await scoped_service.search_entities("Extract")

    call_args = session.run.call_args
    query = call_args[0][0]

    assert "toLower(e.name)" in query
    assert "toLower($query_text)" in query


@pytest.mark.asyncio
async def test_search_entities_with_type_filter(mock_neo4j_service, tenant_id):
    """Test search_entities filters by entity type when provided."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    await scoped_service.search_entities("extract", entity_type="FUNCTION")

    call_args = session.run.call_args
    query = call_args[0][0]
    kwargs = call_args[1]

    assert "e.type = $entity_type" in query
    assert kwargs["entity_type"] == "FUNCTION"


@pytest.mark.asyncio
async def test_search_entities_without_type_filter(mock_neo4j_service, tenant_id):
    """Test search_entities works without type filter."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    await scoped_service.search_entities("extract")

    call_args = session.run.call_args
    query = call_args[0][0]
    kwargs = call_args[1]

    assert "e.type = $entity_type" not in query
    assert "entity_type" not in kwargs


@pytest.mark.asyncio
async def test_search_entities_respects_limit(mock_neo4j_service, tenant_id):
    """Test search_entities respects the limit parameter."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    await scoped_service.search_entities("extract", limit=5)

    call_args = session.run.call_args
    kwargs = call_args[1]

    assert kwargs["limit"] == 5


@pytest.mark.asyncio
async def test_search_entities_returns_results(mock_neo4j_service, tenant_id, sample_entity):
    """Test search_entities returns matching entities."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([{"entity": sample_entity}])
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    result = await scoped_service.search_entities("extract")

    assert len(result) == 1
    assert result[0]["name"] == "extract_entities"


# =============================================================================
# get_entities_by_type Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_entities_by_type_filters_by_tenant(mock_neo4j_service, tenant_id):
    """Test get_entities_by_type filters by tenant_id."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    await scoped_service.get_entities_by_type("FUNCTION")

    call_args = session.run.call_args
    query = call_args[0][0]
    kwargs = call_args[1]

    assert "tenant_id: $tenant_id" in query
    assert kwargs["tenant_id"] == str(tenant_id)


@pytest.mark.asyncio
async def test_get_entities_by_type_supports_pagination(mock_neo4j_service, tenant_id):
    """Test get_entities_by_type supports limit and offset."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    await scoped_service.get_entities_by_type("FUNCTION", limit=20, offset=40)

    call_args = session.run.call_args
    query = call_args[0][0]
    kwargs = call_args[1]

    assert "SKIP $offset" in query
    assert "LIMIT $limit" in query
    assert kwargs["limit"] == 20
    assert kwargs["offset"] == 40


# =============================================================================
# count_entities Tests
# =============================================================================


@pytest.mark.asyncio
async def test_count_entities_filters_by_tenant(mock_neo4j_service, tenant_id):
    """Test count_entities filters by tenant_id."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value={"count": 42})
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    result = await scoped_service.count_entities()

    call_args = session.run.call_args
    query = call_args[0][0]
    kwargs = call_args[1]

    assert "tenant_id: $tenant_id" in query
    assert kwargs["tenant_id"] == str(tenant_id)
    assert result == 42


@pytest.mark.asyncio
async def test_count_entities_with_type_filter(mock_neo4j_service, tenant_id):
    """Test count_entities filters by type when provided."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value={"count": 10})
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    result = await scoped_service.count_entities(entity_type="FUNCTION")

    call_args = session.run.call_args
    query = call_args[0][0]
    kwargs = call_args[1]

    assert "type: $entity_type" in query
    assert kwargs["entity_type"] == "FUNCTION"
    assert result == 10


# =============================================================================
# get_entity_graph Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_entity_graph_filters_all_nodes_by_tenant(
    mock_neo4j_service, tenant_id, entity_id
):
    """Test get_entity_graph filters all nodes in path by tenant."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value=None)
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    await scoped_service.get_entity_graph(entity_id)

    call_args = session.run.call_args
    query = call_args[0][0]

    # Root entity filter
    assert "tenant_id: $tenant_id" in query
    # All nodes in path filter
    assert "n.tenant_id = $tenant_id" in query


@pytest.mark.asyncio
async def test_get_entity_graph_limits_depth(mock_neo4j_service, tenant_id, entity_id):
    """Test get_entity_graph limits depth to 3."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value=None)
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    await scoped_service.get_entity_graph(entity_id, depth=5)

    call_args = session.run.call_args
    query = call_args[0][0]

    # Should be limited to 3
    assert "*1..3" in query


@pytest.mark.asyncio
async def test_get_entity_graph_returns_structure(
    mock_neo4j_service, tenant_id, entity_id, sample_entity
):
    """Test get_entity_graph returns proper structure."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(
        return_value={
            "root": sample_entity,
            "related_nodes": [{"id": "123", "name": "Related"}],
            "relationships": [{"source": "a", "target": "b", "type": "USES", "properties": {}}],
        }
    )
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    result = await scoped_service.get_entity_graph(entity_id)

    assert "root" in result
    assert "related_entities" in result
    assert "relationships" in result
    assert result["root"]["name"] == "extract_entities"


@pytest.mark.asyncio
async def test_get_entity_graph_returns_empty_for_missing_entity(
    mock_neo4j_service, tenant_id, entity_id
):
    """Test get_entity_graph returns empty structure for missing entity."""
    service, session = mock_neo4j_service

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value=None)
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)
    result = await scoped_service.get_entity_graph(entity_id)

    assert result["root"] is None
    assert result["related_entities"] == []
    assert result["relationships"] == []


# =============================================================================
# get_tenant_scoped_neo4j Factory Function Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_tenant_scoped_neo4j_creates_scoped_service(tenant_id):
    """Test factory function creates TenantScopedNeo4jService."""
    mock_service = MagicMock()

    # Patch get_neo4j_service in the module
    original_get_neo4j_service = neo4j_tenant.get_neo4j_service
    neo4j_tenant.get_neo4j_service = AsyncMock(return_value=mock_service)

    try:
        result = await get_tenant_scoped_neo4j(tenant_id)

        assert isinstance(result, TenantScopedNeo4jService)
        assert result.tenant_id == tenant_id
        assert result.service is mock_service
    finally:
        neo4j_tenant.get_neo4j_service = original_get_neo4j_service


@pytest.mark.asyncio
async def test_get_tenant_scoped_neo4j_calls_get_neo4j_service(tenant_id):
    """Test factory function calls get_neo4j_service."""
    mock_service = MagicMock()
    mock_get = AsyncMock(return_value=mock_service)

    original_get_neo4j_service = neo4j_tenant.get_neo4j_service
    neo4j_tenant.get_neo4j_service = mock_get

    try:
        await get_tenant_scoped_neo4j(tenant_id)

        mock_get.assert_called_once()
    finally:
        neo4j_tenant.get_neo4j_service = original_get_neo4j_service


# =============================================================================
# Cross-Tenant Isolation Tests
# =============================================================================


@pytest.mark.asyncio
async def test_different_tenants_get_different_services(
    mock_neo4j_service, tenant_id, other_tenant_id
):
    """Test that different tenants get services with different tenant IDs."""
    service, _ = mock_neo4j_service

    scoped_1 = TenantScopedNeo4jService(service, tenant_id)
    scoped_2 = TenantScopedNeo4jService(service, other_tenant_id)

    assert scoped_1.tenant_id != scoped_2.tenant_id
    assert scoped_1.tenant_id == tenant_id
    assert scoped_2.tenant_id == other_tenant_id


@pytest.mark.asyncio
async def test_tenant_id_consistently_passed_to_queries(mock_neo4j_service, tenant_id, entity_id):
    """Test tenant_id is consistently passed to all query methods."""
    service, session = mock_neo4j_service
    service.get_entity_node = AsyncMock(return_value=None)

    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value=None)
    mock_result.__aiter__ = lambda self: MockAsyncIterator([])
    session.run = AsyncMock(return_value=mock_result)

    scoped_service = TenantScopedNeo4jService(service, tenant_id)

    # Call multiple methods
    await scoped_service.get_entity(entity_id)
    await scoped_service.get_entity_by_name("test")
    await scoped_service.search_entities("test")
    await scoped_service.get_related_entities(entity_id)

    # Verify get_entity passed tenant_id to underlying service
    service.get_entity_node.assert_called_with(entity_id, tenant_id)

    # Verify session.run calls all include tenant_id
    for call in session.run.call_args_list:
        kwargs = call[1]
        assert kwargs["tenant_id"] == str(tenant_id)
