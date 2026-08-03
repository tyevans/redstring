"""
Unit tests for entity split functionality.

Tests the MergeService.split_entity() method and related helper methods.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from kg_builder.models.entity_alias import EntityAlias
from kg_builder.models.extracted_entity import (
    EntityRelationship,
    EntityType,
    ExtractedEntity,
)
from kg_builder.services.consolidation.merge_service import (
    EntitySplitError,
    MergeService,
    SplitResult,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_session():
    """Create a mock async session."""
    session = AsyncMock()
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def mock_event_bus():
    """Create a mock event bus."""
    return AsyncMock()


@pytest.fixture
def tenant_id():
    """Create a test tenant ID."""
    return uuid.uuid4()


@pytest.fixture
def user_id():
    """Create a test user ID."""
    return uuid.uuid4()


@pytest.fixture
def original_entity(tenant_id):
    """Create a test entity to be split."""
    entity = MagicMock(spec=ExtractedEntity)
    entity.id = uuid.uuid4()
    entity.tenant_id = tenant_id
    entity.name = "Combined Entity"
    entity.normalized_name = "combined entity"
    entity.entity_type = EntityType.CONCEPT
    entity.description = "An entity that combines two concepts"
    entity.properties = {"key": "value"}
    entity.external_ids = {}
    entity.confidence_score = 0.95
    entity.source_text = "Source text"
    entity.source_page_id = uuid.uuid4()
    entity.is_canonical = True
    return entity


@pytest.fixture
def split_definitions():
    """Create split definitions for two new entities."""
    return [
        {
            "name": "Entity A",
            "entity_type": "person",
            "description": "First split entity",
            "properties": {"type": "A"},
        },
        {
            "name": "Entity B",
            "entity_type": "organization",
            "description": "Second split entity",
            "properties": {"type": "B"},
        },
    ]


# =============================================================================
# Test SplitResult
# =============================================================================


class TestSplitResult:
    """Tests for SplitResult dataclass."""

    def test_split_result_creation(self):
        """Test SplitResult can be created with all fields."""
        original_id = uuid.uuid4()
        new_ids = [uuid.uuid4(), uuid.uuid4()]
        new_entities = [MagicMock(spec=ExtractedEntity), MagicMock(spec=ExtractedEntity)]
        split_history_id = uuid.uuid4()
        event_id = uuid.uuid4()

        result = SplitResult(
            original_entity_id=original_id,
            new_entity_ids=new_ids,
            new_entities=new_entities,
            relationships_redistributed=5,
            aliases_redistributed=2,
            split_history_id=split_history_id,
            event_id=event_id,
        )

        assert result.original_entity_id == original_id
        assert result.new_entity_ids == new_ids
        assert result.new_entities == new_entities
        assert result.relationships_redistributed == 5
        assert result.aliases_redistributed == 2
        assert result.split_history_id == split_history_id
        assert result.event_id == event_id

    def test_split_result_repr(self):
        """Test SplitResult string representation."""
        result = SplitResult(
            original_entity_id=uuid.uuid4(),
            new_entity_ids=[uuid.uuid4(), uuid.uuid4()],
            new_entities=[MagicMock(), MagicMock()],
            relationships_redistributed=3,
            aliases_redistributed=1,
            split_history_id=uuid.uuid4(),
            event_id=uuid.uuid4(),
        )

        repr_str = repr(result)
        assert "SplitResult" in repr_str
        assert "new_entities=2" in repr_str
        assert "relationships=3" in repr_str


# =============================================================================
# Test EntitySplitError
# =============================================================================


class TestEntitySplitError:
    """Tests for EntitySplitError exception."""

    def test_entity_split_error_creation(self):
        """Test EntitySplitError can be created with message."""
        error = EntitySplitError("Entity not found")
        assert str(error) == "Entity not found"

    def test_entity_split_error_is_merge_error(self):
        """Test EntitySplitError is subclass of MergeError."""
        from kg_builder.services.consolidation.merge_service import MergeError
        error = EntitySplitError("Test error")
        assert isinstance(error, MergeError)


# =============================================================================
# Test Split Validation
# =============================================================================


class TestSplitValidation:
    """Tests for split validation logic."""

    @pytest.mark.asyncio
    async def test_split_entity_not_found_raises_error(
        self, mock_session, tenant_id, user_id, split_definitions
    ):
        """Test split raises error when entity not found."""
        # Setup: Return None for entity query
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        service = MergeService(mock_session)
        entity_id = uuid.uuid4()

        with pytest.raises(EntitySplitError, match="not found"):
            await service.split_entity(
                entity_id=entity_id,
                split_definitions=split_definitions,
                relationship_assignments={},
                alias_assignments=None,
                user_id=user_id,
                reason="Testing split",
            )

    @pytest.mark.asyncio
    async def test_split_insufficient_definitions_raises_error(
        self, mock_session, tenant_id, user_id, original_entity
    ):
        """Test split raises error when fewer than 2 definitions provided."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = original_entity
        mock_session.execute.return_value = mock_result

        service = MergeService(mock_session)

        # Only one definition (need at least 2)
        single_definition = [{"name": "Single Entity"}]

        with pytest.raises(EntitySplitError, match="at least 2"):
            await service.split_entity(
                entity_id=original_entity.id,
                split_definitions=single_definition,
                relationship_assignments={},
                alias_assignments=None,
                user_id=user_id,
                reason="Testing split",
            )

    @pytest.mark.asyncio
    async def test_split_missing_name_raises_error(
        self, mock_session, tenant_id, user_id, original_entity
    ):
        """Test split raises error when definition missing name."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = original_entity
        mock_session.execute.return_value = mock_result

        service = MergeService(mock_session)

        # Missing name field
        definitions = [
            {"entity_type": "person"},  # Missing name
            {"name": "Entity B"},
        ]

        with pytest.raises(EntitySplitError, match="missing required 'name'"):
            await service.split_entity(
                entity_id=original_entity.id,
                split_definitions=definitions,
                relationship_assignments={},
                alias_assignments=None,
                user_id=user_id,
                reason="Testing split",
            )


# =============================================================================
# Test Entity Creation
# =============================================================================


class TestEntityCreation:
    """Tests for entity creation during split."""

    @pytest.mark.asyncio
    async def test_split_creates_new_entities_with_correct_properties(
        self, mock_session, mock_event_bus, tenant_id, user_id, original_entity, split_definitions
    ):
        """Test that split creates new entities with correct properties."""
        # Setup mock responses
        call_count = [0]

        async def execute_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                # Entity lookup
                result.scalar_one_or_none.return_value = original_entity
            else:
                # Relationship/alias queries
                result.scalars.return_value.all.return_value = []
            return result

        mock_session.execute = execute_effect

        service = MergeService(mock_session, event_bus=mock_event_bus)

        result = await service.split_entity(
            entity_id=original_entity.id,
            split_definitions=split_definitions,
            relationship_assignments={},
            alias_assignments=None,
            user_id=user_id,
            reason="Testing split",
        )

        # Verify result
        assert isinstance(result, SplitResult)
        assert result.original_entity_id == original_entity.id
        assert len(result.new_entity_ids) == 2
        assert len(result.new_entities) == 2

        # Verify session.add was called for new entities
        assert mock_session.add.call_count >= 2

    @pytest.mark.asyncio
    async def test_split_preserves_source_page_id(
        self, mock_session, tenant_id, user_id, original_entity, split_definitions
    ):
        """Test that split preserves source_page_id in new entities."""
        call_count = [0]

        async def execute_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.scalar_one_or_none.return_value = original_entity
            else:
                result.scalars.return_value.all.return_value = []
            return result

        mock_session.execute = execute_effect

        service = MergeService(mock_session)

        result = await service.split_entity(
            entity_id=original_entity.id,
            split_definitions=split_definitions,
            relationship_assignments={},
            alias_assignments=None,
            user_id=user_id,
            reason="Testing split",
        )

        # New entities should have same source_page_id as original
        for new_entity in result.new_entities:
            assert new_entity.source_page_id == original_entity.source_page_id


# =============================================================================
# Test Relationship Redistribution
# =============================================================================


class TestRelationshipRedistribution:
    """Tests for relationship redistribution during split."""

    @pytest.mark.asyncio
    async def test_redistribute_relationships_default_to_first(
        self, mock_session, tenant_id, original_entity
    ):
        """Test relationships default to first new entity when no assignments."""
        service = MergeService(mock_session)

        # Create mock relationships
        rel1 = MagicMock(spec=EntityRelationship)
        rel1.id = uuid.uuid4()
        rel1.source_entity_id = original_entity.id
        rel1.target_entity_id = uuid.uuid4()

        rel2 = MagicMock(spec=EntityRelationship)
        rel2.id = uuid.uuid4()
        rel2.source_entity_id = uuid.uuid4()
        rel2.target_entity_id = original_entity.id

        # Setup mock to return relationships
        call_count = [0]

        async def execute_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.scalars.return_value.all.return_value = [rel1]
            elif call_count[0] == 2:
                result.scalars.return_value.all.return_value = [rel2]
            else:
                result.scalars.return_value.all.return_value = []
            return result

        mock_session.execute = execute_effect

        # Create new entities
        new_entity_1 = MagicMock(spec=ExtractedEntity)
        new_entity_1.id = uuid.uuid4()
        new_entity_2 = MagicMock(spec=ExtractedEntity)
        new_entity_2.id = uuid.uuid4()

        count = await service._redistribute_relationships(
            original_entity_id=original_entity.id,
            new_entities=[new_entity_1, new_entity_2],
            relationship_assignments={},  # No explicit assignments
            tenant_id=tenant_id,
        )

        assert count == 2
        # Both relationships should point to first new entity
        assert rel1.source_entity_id == new_entity_1.id
        assert rel2.target_entity_id == new_entity_1.id

    @pytest.mark.asyncio
    async def test_redistribute_relationships_with_explicit_assignments(
        self, mock_session, tenant_id, original_entity
    ):
        """Test relationships follow explicit assignments."""
        service = MergeService(mock_session)

        # Create mock relationships
        rel1 = MagicMock(spec=EntityRelationship)
        rel1.id = uuid.uuid4()
        rel1.source_entity_id = original_entity.id
        rel1.target_entity_id = uuid.uuid4()
        rel1.synced_to_neo4j = True

        rel2 = MagicMock(spec=EntityRelationship)
        rel2.id = uuid.uuid4()
        rel2.source_entity_id = original_entity.id
        rel2.target_entity_id = uuid.uuid4()
        rel2.synced_to_neo4j = True

        # Setup mock to return all relationships
        async def execute_effect(*args, **kwargs):
            result = MagicMock()
            result.scalars.return_value.all.return_value = [rel1, rel2]
            return result

        mock_session.execute = execute_effect

        # Create new entities
        new_entity_1 = MagicMock(spec=ExtractedEntity)
        new_entity_1.id = uuid.uuid4()
        new_entity_2 = MagicMock(spec=ExtractedEntity)
        new_entity_2.id = uuid.uuid4()

        # Assign rel1 to entity 1, rel2 to entity 2
        relationship_assignments = {
            rel1.id: 0,  # First entity
            rel2.id: 1,  # Second entity
        }

        count = await service._redistribute_relationships(
            original_entity_id=original_entity.id,
            new_entities=[new_entity_1, new_entity_2],
            relationship_assignments=relationship_assignments,
            tenant_id=tenant_id,
        )

        assert count == 2
        assert rel1.source_entity_id == new_entity_1.id
        assert rel2.source_entity_id == new_entity_2.id
        # Both should be marked for re-sync
        assert rel1.synced_to_neo4j is False
        assert rel2.synced_to_neo4j is False


# =============================================================================
# Test Alias Redistribution
# =============================================================================


class TestAliasRedistribution:
    """Tests for alias redistribution during split."""

    @pytest.mark.asyncio
    async def test_redistribute_aliases_with_assignments(
        self, mock_session, tenant_id, original_entity
    ):
        """Test aliases are redistributed according to assignments."""
        service = MergeService(mock_session)

        # Create mock aliases
        alias1 = MagicMock(spec=EntityAlias)
        alias1.id = uuid.uuid4()
        alias1.canonical_entity_id = original_entity.id

        alias2 = MagicMock(spec=EntityAlias)
        alias2.id = uuid.uuid4()
        alias2.canonical_entity_id = original_entity.id

        # Setup mock
        async def execute_effect(*args, **kwargs):
            result = MagicMock()
            result.scalars.return_value.all.return_value = [alias1, alias2]
            return result

        mock_session.execute = execute_effect

        # Create new entities
        new_entity_1 = MagicMock(spec=ExtractedEntity)
        new_entity_1.id = uuid.uuid4()
        new_entity_2 = MagicMock(spec=ExtractedEntity)
        new_entity_2.id = uuid.uuid4()

        # Assign aliases to different entities
        alias_assignments = {
            alias1.id: 0,
            alias2.id: 1,
        }

        count = await service._redistribute_aliases(
            original_entity_id=original_entity.id,
            new_entities=[new_entity_1, new_entity_2],
            alias_assignments=alias_assignments,
            tenant_id=tenant_id,
        )

        assert count == 2
        assert alias1.canonical_entity_id == new_entity_1.id
        assert alias2.canonical_entity_id == new_entity_2.id


# =============================================================================
# Test Full Split Flow
# =============================================================================


class TestFullSplitFlow:
    """Tests for the complete split operation flow."""

    @pytest.mark.asyncio
    async def test_split_entity_success(
        self,
        mock_session,
        mock_event_bus,
        tenant_id,
        user_id,
        original_entity,
        split_definitions,
    ):
        """Test successful split of an entity."""
        call_count = [0]

        async def execute_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.scalar_one_or_none.return_value = original_entity
            else:
                result.scalars.return_value.all.return_value = []
            return result

        mock_session.execute = execute_effect

        service = MergeService(mock_session, event_bus=mock_event_bus)

        result = await service.split_entity(
            entity_id=original_entity.id,
            split_definitions=split_definitions,
            relationship_assignments={},
            alias_assignments=None,
            user_id=user_id,
            reason="Testing split",
        )

        # Verify result
        assert isinstance(result, SplitResult)
        assert result.original_entity_id == original_entity.id
        assert len(result.new_entity_ids) == 2

        # Verify original entity was marked as non-canonical
        assert original_entity.is_canonical is False

        # Verify split metadata was stored
        assert "_split_into" in original_entity.properties
        assert "_split_by" in original_entity.properties
        assert str(user_id) == original_entity.properties["_split_by"]

        # Verify event was published
        mock_event_bus.publish.assert_called()

    @pytest.mark.asyncio
    async def test_split_creates_history_record(
        self,
        mock_session,
        tenant_id,
        user_id,
        original_entity,
        split_definitions,
    ):
        """Test that split creates a MergeHistory record."""
        call_count = [0]

        async def execute_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.scalar_one_or_none.return_value = original_entity
            else:
                result.scalars.return_value.all.return_value = []
            return result

        mock_session.execute = execute_effect

        service = MergeService(mock_session)

        result = await service.split_entity(
            entity_id=original_entity.id,
            split_definitions=split_definitions,
            relationship_assignments={},
            alias_assignments=None,
            user_id=user_id,
            reason="Testing split",
        )

        # Verify split_history_id was returned
        assert result.split_history_id is not None

        # Verify session.add was called (for entities and history)
        # Should be: 2 new entities + 1 history record = 3 adds minimum
        assert mock_session.add.call_count >= 3


# =============================================================================
# Test Load Entity Helper
# =============================================================================


class TestLoadEntity:
    """Tests for _load_entity helper method."""

    @pytest.mark.asyncio
    async def test_load_entity_found(self, mock_session, original_entity):
        """Test _load_entity returns entity when found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = original_entity
        mock_session.execute.return_value = mock_result

        service = MergeService(mock_session)
        result = await service._load_entity(original_entity.id)

        assert result == original_entity

    @pytest.mark.asyncio
    async def test_load_entity_not_found(self, mock_session):
        """Test _load_entity returns None when not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        service = MergeService(mock_session)
        result = await service._load_entity(uuid.uuid4())

        assert result is None
