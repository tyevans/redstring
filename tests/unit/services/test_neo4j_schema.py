"""
Unit tests for Neo4j schema setup and constraints.

Tests the Neo4j schema module's ability to create, verify, and drop
constraints and indexes for the knowledge graph.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Mock dependencies before importing neo4j_schema to avoid database module imports
mock_neo4j_service = MagicMock()
mock_neo4j_service.Neo4jService = MagicMock
mock_neo4j_service.get_neo4j_service = AsyncMock()
sys.modules["kg_builder.services.neo4j"] = mock_neo4j_service

# Import the module directly using importlib to avoid triggering __init__.py
spec = importlib.util.spec_from_file_location(
    "neo4j_schema",
    Path(__file__).parent.parent.parent.parent
    / "src"
    / "kg_builder"
    / "services"
    / "neo4j_schema.py",
)
neo4j_schema = importlib.util.module_from_spec(spec)
sys.modules["kg_builder.services.neo4j_schema"] = neo4j_schema
spec.loader.exec_module(neo4j_schema)

# Import symbols from the loaded module
setup_neo4j_schema = neo4j_schema.setup_neo4j_schema
verify_schema = neo4j_schema.verify_schema
drop_schema = neo4j_schema.drop_schema
get_schema_info = neo4j_schema.get_schema_info
CONSTRAINTS = neo4j_schema.CONSTRAINTS
INDEXES = neo4j_schema.INDEXES


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
def mock_constraint_records():
    """Sample constraint records from Neo4j."""
    return [
        {"name": "entity_id_unique", "type": "UNIQUENESS"},
    ]


@pytest.fixture
def mock_index_records():
    """Sample index records from Neo4j."""
    return [
        {"name": "entity_tenant_idx", "type": "RANGE"},
        {"name": "entity_type_idx", "type": "RANGE"},
        {"name": "entity_name_idx", "type": "RANGE"},
        {"name": "entity_tenant_type_idx", "type": "RANGE"},
    ]


# =============================================================================
# setup_neo4j_schema tests
# =============================================================================


@pytest.mark.asyncio
async def test_setup_neo4j_schema_creates_constraints(mock_neo4j_service):
    """Test that setup_neo4j_schema creates all defined constraints."""
    service, session = mock_neo4j_service

    # Mock session.run to return empty result
    session.run = AsyncMock(return_value=AsyncMock())

    result = await setup_neo4j_schema(service)

    # Verify all constraints were attempted to be created
    assert "constraints_created" in result
    assert len(result["constraints_created"]) == len(CONSTRAINTS)
    assert "entity_id_unique" in result["constraints_created"]


@pytest.mark.asyncio
async def test_setup_neo4j_schema_creates_indexes(mock_neo4j_service):
    """Test that setup_neo4j_schema creates all defined indexes."""
    service, session = mock_neo4j_service

    # Mock session.run to return empty result
    session.run = AsyncMock(return_value=AsyncMock())

    result = await setup_neo4j_schema(service)

    # Verify all indexes were attempted to be created
    assert "indexes_created" in result
    assert len(result["indexes_created"]) == len(INDEXES)
    assert "entity_tenant_idx" in result["indexes_created"]
    assert "entity_type_idx" in result["indexes_created"]
    assert "entity_name_idx" in result["indexes_created"]
    assert "entity_tenant_type_idx" in result["indexes_created"]


@pytest.mark.asyncio
async def test_setup_neo4j_schema_calls_correct_queries(mock_neo4j_service):
    """Test that setup_neo4j_schema executes the correct Cypher queries."""
    service, session = mock_neo4j_service

    session.run = AsyncMock(return_value=AsyncMock())

    await setup_neo4j_schema(service)

    # Verify session.run was called for each constraint and index
    expected_call_count = len(CONSTRAINTS) + len(INDEXES)
    assert session.run.call_count == expected_call_count

    # Verify constraint query was called
    call_args_list = [str(call) for call in session.run.call_args_list]
    assert any("entity_id_unique" in str(args) for args in call_args_list)


@pytest.mark.asyncio
async def test_setup_neo4j_schema_handles_existing_constraints(mock_neo4j_service):
    """Test that setup_neo4j_schema handles already existing constraints gracefully."""
    service, session = mock_neo4j_service

    # Mock first constraint creation to raise an exception (already exists)
    call_count = 0

    async def mock_run(query):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Constraint already exists")
        return AsyncMock()

    session.run = mock_run

    # Should not raise an exception
    result = await setup_neo4j_schema(service)

    # Should still report the constraint as created (it exists)
    assert "entity_id_unique" in result["constraints_created"]


@pytest.mark.asyncio
async def test_setup_neo4j_schema_uses_global_service():
    """Test that setup_neo4j_schema uses global service when none provided."""
    mock_service = MagicMock()
    mock_session = AsyncMock()

    async_session_cm = AsyncMock()
    async_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    async_session_cm.__aexit__ = AsyncMock(return_value=None)
    mock_service.session = MagicMock(return_value=async_session_cm)

    mock_session.run = AsyncMock(return_value=AsyncMock())

    # Patch the module directly using object patching instead of string path
    original_get_neo4j_service = neo4j_schema.get_neo4j_service
    neo4j_schema.get_neo4j_service = AsyncMock(return_value=mock_service)
    try:
        result = await setup_neo4j_schema()
    finally:
        neo4j_schema.get_neo4j_service = original_get_neo4j_service

    assert "constraints_created" in result
    assert "indexes_created" in result


# =============================================================================
# verify_schema tests
# =============================================================================


@pytest.mark.asyncio
async def test_verify_schema_returns_correct_structure(
    mock_neo4j_service, mock_constraint_records, mock_index_records
):
    """Test that verify_schema returns the expected structure."""
    service, session = mock_neo4j_service

    # Mock constraint query result
    constraint_result = AsyncMock()
    constraint_result.__aiter__ = lambda self: MockAsyncIterator(mock_constraint_records)

    # Mock index query result
    index_result = AsyncMock()
    index_result.__aiter__ = lambda self: MockAsyncIterator(mock_index_records)

    session.run = AsyncMock(side_effect=[constraint_result, index_result])

    result = await verify_schema(service)

    # Verify structure
    assert "constraints_count" in result
    assert "indexes_count" in result
    assert "constraints" in result
    assert "indexes" in result
    assert "expected_constraints" in result
    assert "expected_indexes" in result
    assert "missing_constraints" in result
    assert "missing_indexes" in result
    assert "is_valid" in result


@pytest.mark.asyncio
async def test_verify_schema_valid_when_all_present(
    mock_neo4j_service, mock_constraint_records, mock_index_records
):
    """Test that verify_schema returns is_valid=True when all schema elements exist."""
    service, session = mock_neo4j_service

    constraint_result = AsyncMock()
    constraint_result.__aiter__ = lambda self: MockAsyncIterator(mock_constraint_records)

    index_result = AsyncMock()
    index_result.__aiter__ = lambda self: MockAsyncIterator(mock_index_records)

    session.run = AsyncMock(side_effect=[constraint_result, index_result])

    result = await verify_schema(service)

    assert result["is_valid"] is True
    assert len(result["missing_constraints"]) == 0
    assert len(result["missing_indexes"]) == 0


@pytest.mark.asyncio
async def test_verify_schema_invalid_when_constraints_missing(mock_neo4j_service):
    """Test that verify_schema returns is_valid=False when constraints are missing."""
    service, session = mock_neo4j_service

    # No constraints exist
    constraint_result = AsyncMock()
    constraint_result.__aiter__ = lambda self: MockAsyncIterator([])

    # All indexes exist
    index_records = [
        {"name": "entity_tenant_idx"},
        {"name": "entity_type_idx"},
        {"name": "entity_name_idx"},
        {"name": "entity_tenant_type_idx"},
    ]
    index_result = AsyncMock()
    index_result.__aiter__ = lambda self: MockAsyncIterator(index_records)

    session.run = AsyncMock(side_effect=[constraint_result, index_result])

    result = await verify_schema(service)

    assert result["is_valid"] is False
    assert "entity_id_unique" in result["missing_constraints"]


@pytest.mark.asyncio
async def test_verify_schema_invalid_when_indexes_missing(mock_neo4j_service):
    """Test that verify_schema returns is_valid=False when indexes are missing."""
    service, session = mock_neo4j_service

    # Constraint exists
    constraint_result = AsyncMock()
    constraint_result.__aiter__ = lambda self: MockAsyncIterator([{"name": "entity_id_unique"}])

    # Only some indexes exist
    index_result = AsyncMock()
    index_result.__aiter__ = lambda self: MockAsyncIterator([{"name": "entity_tenant_idx"}])

    session.run = AsyncMock(side_effect=[constraint_result, index_result])

    result = await verify_schema(service)

    assert result["is_valid"] is False
    assert "entity_type_idx" in result["missing_indexes"]
    assert "entity_name_idx" in result["missing_indexes"]
    assert "entity_tenant_type_idx" in result["missing_indexes"]


@pytest.mark.asyncio
async def test_verify_schema_counts_all_schema_elements(mock_neo4j_service):
    """Test that verify_schema correctly counts all constraints and indexes."""
    service, session = mock_neo4j_service

    # Additional constraints/indexes beyond what we expect
    all_constraints = [
        {"name": "entity_id_unique"},
        {"name": "other_constraint"},
    ]
    all_indexes = [
        {"name": "entity_tenant_idx"},
        {"name": "entity_type_idx"},
        {"name": "entity_name_idx"},
        {"name": "entity_tenant_type_idx"},
        {"name": "other_index"},
    ]

    constraint_result = AsyncMock()
    constraint_result.__aiter__ = lambda self: MockAsyncIterator(all_constraints)

    index_result = AsyncMock()
    index_result.__aiter__ = lambda self: MockAsyncIterator(all_indexes)

    session.run = AsyncMock(side_effect=[constraint_result, index_result])

    result = await verify_schema(service)

    # Should count all, not just expected ones
    assert result["constraints_count"] == 2
    assert result["indexes_count"] == 5


# =============================================================================
# drop_schema tests
# =============================================================================


@pytest.mark.asyncio
async def test_drop_schema_drops_all_constraints(mock_neo4j_service):
    """Test that drop_schema drops all defined constraints."""
    service, session = mock_neo4j_service

    session.run = AsyncMock(return_value=AsyncMock())

    result = await drop_schema(service)

    assert "constraints_dropped" in result
    assert "entity_id_unique" in result["constraints_dropped"]


@pytest.mark.asyncio
async def test_drop_schema_drops_all_indexes(mock_neo4j_service):
    """Test that drop_schema drops all defined indexes."""
    service, session = mock_neo4j_service

    session.run = AsyncMock(return_value=AsyncMock())

    result = await drop_schema(service)

    assert "indexes_dropped" in result
    assert "entity_tenant_idx" in result["indexes_dropped"]
    assert "entity_type_idx" in result["indexes_dropped"]
    assert "entity_name_idx" in result["indexes_dropped"]
    assert "entity_tenant_type_idx" in result["indexes_dropped"]


@pytest.mark.asyncio
async def test_drop_schema_drops_indexes_before_constraints(mock_neo4j_service):
    """Test that drop_schema drops indexes before constraints."""
    service, session = mock_neo4j_service

    call_order = []

    async def mock_run(query):
        if "DROP INDEX" in query:
            call_order.append("index")
        elif "DROP CONSTRAINT" in query:
            call_order.append("constraint")
        return AsyncMock()

    session.run = mock_run

    await drop_schema(service)

    # All index drops should come before constraint drops
    index_count = len(INDEXES)
    assert call_order[:index_count] == ["index"] * index_count
    assert call_order[index_count:] == ["constraint"] * len(CONSTRAINTS)


@pytest.mark.asyncio
async def test_drop_schema_handles_nonexistent_elements(mock_neo4j_service):
    """Test that drop_schema handles nonexistent constraints/indexes gracefully."""
    service, session = mock_neo4j_service

    async def mock_run(query):
        raise Exception("Constraint/Index does not exist")

    session.run = mock_run

    # Should not raise an exception
    result = await drop_schema(service)

    # Should still report attempts
    assert "constraints_dropped" in result
    assert "indexes_dropped" in result


@pytest.mark.asyncio
async def test_drop_schema_uses_if_exists(mock_neo4j_service):
    """Test that drop_schema uses IF EXISTS clause in queries."""
    service, session = mock_neo4j_service

    executed_queries = []

    async def mock_run(query):
        executed_queries.append(query)
        return AsyncMock()

    session.run = mock_run

    await drop_schema(service)

    # All DROP queries should include IF EXISTS
    for query in executed_queries:
        assert "IF EXISTS" in query


# =============================================================================
# get_schema_info tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_schema_info_returns_details(
    mock_neo4j_service, mock_constraint_records, mock_index_records
):
    """Test that get_schema_info returns detailed schema information."""
    service, session = mock_neo4j_service

    constraint_result = AsyncMock()
    constraint_result.__aiter__ = lambda self: MockAsyncIterator(mock_constraint_records)

    index_result = AsyncMock()
    index_result.__aiter__ = lambda self: MockAsyncIterator(mock_index_records)

    session.run = AsyncMock(side_effect=[constraint_result, index_result])

    result = await get_schema_info(service)

    assert "constraints" in result
    assert "indexes" in result
    assert "constraint_count" in result
    assert "index_count" in result
    assert result["constraint_count"] == len(mock_constraint_records)
    assert result["index_count"] == len(mock_index_records)


# =============================================================================
# Schema Constants tests
# =============================================================================


def test_constraints_dictionary_structure():
    """Test that CONSTRAINTS dictionary has expected structure."""
    assert isinstance(CONSTRAINTS, dict)
    assert "entity_id_unique" in CONSTRAINTS
    assert "UNIQUE" in CONSTRAINTS["entity_id_unique"].upper()


def test_indexes_dictionary_structure():
    """Test that INDEXES dictionary has expected structure."""
    assert isinstance(INDEXES, dict)
    assert "entity_tenant_idx" in INDEXES
    assert "entity_type_idx" in INDEXES
    assert "entity_name_idx" in INDEXES
    assert "entity_tenant_type_idx" in INDEXES


def test_constraint_query_syntax():
    """Test that constraint queries have correct Cypher syntax."""
    for _name, query in CONSTRAINTS.items():
        assert "CREATE CONSTRAINT" in query
        assert "IF NOT EXISTS" in query
        assert "FOR" in query
        assert "REQUIRE" in query


def test_index_query_syntax():
    """Test that index queries have correct Cypher syntax."""
    for _name, query in INDEXES.items():
        assert "CREATE INDEX" in query
        assert "IF NOT EXISTS" in query
        assert "FOR" in query
        assert "ON" in query


def test_composite_index_has_multiple_properties():
    """Test that composite index includes multiple properties."""
    composite_query = INDEXES["entity_tenant_type_idx"]
    # Should have tenant_id and type in the ON clause
    assert "tenant_id" in composite_query
    assert "type" in composite_query
