"""
Unit tests for MergeService.

Tests the core merge service functionality including:
- Merge validation (is_mergeable, validate_merge)
- Property merge strategies
- Merge result creation
- Error handling

Integration tests that require database access are in
tests/integration/consolidation/test_merge_integration.py
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kg_builder.services.consolidation.merge_service import (
    DEFAULT_PROPERTY_STRATEGIES,
    MergeError,
    MergeResult,
    MergeService,
    MergeValidationError,
    PropertyMergeStrategy,
    deep_merge_dicts,
    merge_property,
)


# Create mock EntityType enum for testing without database imports
class MockEntityType(str, Enum):
    """Mock entity type for testing."""

    CONCEPT = "concept"
    PERSON = "person"
    ORGANIZATION = "organization"

# Use as EntityType for tests
EntityType = MockEntityType


# =============================================================================
# Fixtures
# =============================================================================


def create_mock_entity(
    entity_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    name: str = "Test Entity",
    entity_type: EntityType = EntityType.CONCEPT,
    is_canonical: bool = True,
    is_alias_of: uuid.UUID | None = None,
    source_page_id: uuid.UUID | None = None,
    properties: dict | None = None,
    external_ids: dict | None = None,
    description: str | None = None,
    confidence_score: float = 0.9,
) -> MagicMock:
    """Create a mock ExtractedEntity for testing."""
    mock = MagicMock()
    mock.id = entity_id or uuid.uuid4()
    mock.tenant_id = tenant_id or uuid.uuid4()
    mock.name = name
    mock.normalized_name = name.lower().replace(" ", "_")
    mock.entity_type = entity_type
    mock.is_canonical = is_canonical
    mock.is_alias_of = is_alias_of
    mock.source_page_id = source_page_id or uuid.uuid4()
    mock.properties = properties or {}
    mock.external_ids = external_ids or {}
    mock.description = description
    mock.confidence_score = confidence_score
    return mock


@pytest.fixture
def tenant_id():
    """Common tenant ID for tests."""
    return uuid.uuid4()


@pytest.fixture
def canonical_entity(tenant_id):
    """Create canonical entity fixture."""
    return create_mock_entity(
        tenant_id=tenant_id,
        name="Canonical Entity",
        properties={"category": "test"},
        external_ids={"wikidata": "Q123"},
        description="The canonical entity",
    )


@pytest.fixture
def merged_entity(tenant_id):
    """Create entity to be merged fixture."""
    return create_mock_entity(
        tenant_id=tenant_id,
        name="Merged Entity",
        properties={"tag": "duplicate"},
        external_ids={"schema_org": "Thing"},
        description="Entity to be merged",
        confidence_score=0.95,
    )


@pytest.fixture
def mock_session():
    """Create mock async session."""
    session = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock()
    return session


# =============================================================================
# PropertyMergeStrategy Tests
# =============================================================================


class TestPropertyMergeStrategy:
    """Tests for PropertyMergeStrategy enum."""

    def test_all_strategies_defined(self):
        """Verify all expected strategies are defined."""
        expected = [
            "PREFER_CANONICAL",
            "PREFER_MERGED",
            "UNION",
            "LATEST",
            "DEEP_MERGE",
        ]
        for name in expected:
            assert hasattr(PropertyMergeStrategy, name)

    def test_strategy_values(self):
        """Verify strategy string values."""
        assert PropertyMergeStrategy.PREFER_CANONICAL.value == "prefer_canonical"
        assert PropertyMergeStrategy.PREFER_MERGED.value == "prefer_merged"
        assert PropertyMergeStrategy.UNION.value == "union"
        assert PropertyMergeStrategy.LATEST.value == "latest"
        assert PropertyMergeStrategy.DEEP_MERGE.value == "deep_merge"


class TestDefaultPropertyStrategies:
    """Tests for DEFAULT_PROPERTY_STRATEGIES mapping."""

    def test_name_strategy(self):
        """Name should prefer canonical."""
        assert DEFAULT_PROPERTY_STRATEGIES["name"] == PropertyMergeStrategy.PREFER_CANONICAL

    def test_tags_strategy(self):
        """Tags should union."""
        assert DEFAULT_PROPERTY_STRATEGIES["tags"] == PropertyMergeStrategy.UNION

    def test_properties_strategy(self):
        """Properties should deep merge."""
        assert DEFAULT_PROPERTY_STRATEGIES["properties"] == PropertyMergeStrategy.DEEP_MERGE

    def test_description_strategy(self):
        """Description should take latest."""
        assert DEFAULT_PROPERTY_STRATEGIES["description"] == PropertyMergeStrategy.LATEST


# =============================================================================
# merge_property Tests
# =============================================================================


class TestMergeProperty:
    """Tests for merge_property function."""

    def test_prefer_canonical_keeps_canonical(self):
        """PREFER_CANONICAL keeps canonical value."""
        result, details = merge_property(
            "canonical_value", "merged_value", PropertyMergeStrategy.PREFER_CANONICAL
        )
        assert result == "canonical_value"
        assert details["kept"] == "canonical"
        assert details["discarded"] == "merged_value"

    def test_prefer_canonical_with_same_value(self):
        """PREFER_CANONICAL with same values."""
        result, details = merge_property(
            "same_value", "same_value", PropertyMergeStrategy.PREFER_CANONICAL
        )
        assert result == "same_value"
        assert details["discarded"] is None

    def test_prefer_merged_takes_merged(self):
        """PREFER_MERGED takes merged value."""
        result, details = merge_property(
            "canonical_value", "merged_value", PropertyMergeStrategy.PREFER_MERGED
        )
        assert result == "merged_value"
        assert details["kept"] == "merged"
        assert details["discarded"] == "canonical_value"

    def test_union_with_lists(self):
        """UNION combines lists."""
        result, details = merge_property(
            ["a", "b"], ["b", "c"], PropertyMergeStrategy.UNION
        )
        assert set(result) == {"a", "b", "c"}
        assert details["union_count"] == 3
        assert details["added"] == 1

    def test_union_with_dicts(self):
        """UNION combines dict keys (canonical wins on conflicts)."""
        result, details = merge_property(
            {"key1": "value1", "shared": "canonical"},
            {"key2": "value2", "shared": "merged"},
            PropertyMergeStrategy.UNION,
        )
        assert result["key1"] == "value1"
        assert result["key2"] == "value2"
        assert result["shared"] == "canonical"  # canonical wins

    def test_deep_merge_dicts(self):
        """DEEP_MERGE recursively merges dicts."""
        result, details = merge_property(
            {"nested": {"a": 1}, "only_canonical": True},
            {"nested": {"b": 2}, "only_merged": True},
            PropertyMergeStrategy.DEEP_MERGE,
        )
        assert result["nested"]["a"] == 1
        assert result["nested"]["b"] == 2
        assert result["only_canonical"] is True
        assert result["only_merged"] is True

    def test_deep_merge_lists(self):
        """DEEP_MERGE concatenates and dedupes lists."""
        result, details = merge_property(
            ["a", "b"], ["b", "c"], PropertyMergeStrategy.DEEP_MERGE
        )
        # Should preserve order with canonical first
        assert result == ["a", "b", "c"]

    def test_latest_takes_merged(self):
        """LATEST takes merged value (assuming newer)."""
        result, details = merge_property(
            "old_value", "new_value", PropertyMergeStrategy.LATEST
        )
        assert result == "new_value"
        assert details["kept"] == "merged"

    def test_latest_with_none_merged(self):
        """LATEST with None merged returns canonical."""
        result, details = merge_property(
            "canonical_value", None, PropertyMergeStrategy.LATEST
        )
        assert result == "canonical_value"


# =============================================================================
# deep_merge_dicts Tests
# =============================================================================


class TestDeepMergeDicts:
    """Tests for deep_merge_dicts function."""

    def test_simple_merge(self):
        """Simple merge with non-overlapping keys."""
        result = deep_merge_dicts({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_dict_a_takes_precedence(self):
        """dict_a values take precedence for conflicts."""
        result = deep_merge_dicts(
            {"key": "value_a"},
            {"key": "value_b"},
        )
        assert result["key"] == "value_a"

    def test_nested_merge(self):
        """Nested dicts are recursively merged."""
        result = deep_merge_dicts(
            {"nested": {"a": 1, "shared": "a"}},
            {"nested": {"b": 2, "shared": "b"}},
        )
        assert result["nested"]["a"] == 1
        assert result["nested"]["b"] == 2
        assert result["nested"]["shared"] == "a"

    def test_list_merge(self):
        """Lists are concatenated and deduped."""
        result = deep_merge_dicts(
            {"items": ["a", "b"]},
            {"items": ["b", "c"]},
        )
        assert result["items"] == ["a", "b", "c"]

    def test_deeply_nested_merge(self):
        """Multiple levels of nesting."""
        result = deep_merge_dicts(
            {"l1": {"l2": {"l3": "a"}}},
            {"l1": {"l2": {"l3": "b", "other": "c"}}},
        )
        assert result["l1"]["l2"]["l3"] == "a"
        assert result["l1"]["l2"]["other"] == "c"


# =============================================================================
# MergeResult Tests
# =============================================================================


class TestMergeResult:
    """Tests for MergeResult class."""

    def test_creation(self):
        """Test MergeResult creation."""
        result = MergeResult(
            canonical_entity_id=uuid.uuid4(),
            merged_entity_ids=[uuid.uuid4()],
            aliases_created=[],
            relationships_transferred=5,
            properties_merged={"test": "value"},
            merge_history_id=uuid.uuid4(),
            event_id=uuid.uuid4(),
        )
        assert result.relationships_transferred == 5
        assert len(result.merged_entity_ids) == 1

    def test_repr(self):
        """Test string representation."""
        canonical_id = uuid.uuid4()
        result = MergeResult(
            canonical_entity_id=canonical_id,
            merged_entity_ids=[uuid.uuid4(), uuid.uuid4()],
            aliases_created=[MagicMock(), MagicMock()],
            relationships_transferred=3,
            properties_merged={},
            merge_history_id=uuid.uuid4(),
            event_id=uuid.uuid4(),
        )
        repr_str = repr(result)
        assert str(canonical_id) in repr_str
        assert "merged=2" in repr_str
        assert "aliases=2" in repr_str
        assert "relationships=3" in repr_str


# =============================================================================
# MergeService Tests
# =============================================================================


class TestMergeServiceInit:
    """Tests for MergeService initialization."""

    def test_init_with_defaults(self, mock_session):
        """Test initialization with default values."""
        service = MergeService(mock_session)
        assert service.session == mock_session
        assert service.event_bus is None
        assert service.property_strategies == DEFAULT_PROPERTY_STRATEGIES

    def test_init_with_custom_strategies(self, mock_session):
        """Test initialization with custom strategies."""
        custom = {"custom_prop": PropertyMergeStrategy.UNION}
        service = MergeService(mock_session, property_strategies=custom)
        assert service.property_strategies == custom

    def test_init_with_event_bus(self, mock_session):
        """Test initialization with event bus."""
        event_bus = MagicMock()
        service = MergeService(mock_session, event_bus=event_bus)
        assert service.event_bus == event_bus


class TestMergeServiceValidation:
    """Tests for MergeService validation methods."""

    @pytest.mark.asyncio
    async def test_is_mergeable_true(self, mock_session, tenant_id):
        """is_mergeable returns True for valid entities."""
        entity_a = create_mock_entity(tenant_id=tenant_id, name="Entity A")
        entity_b = create_mock_entity(tenant_id=tenant_id, name="Entity B")

        service = MergeService(mock_session)
        result = await service.is_mergeable(entity_a, entity_b, tenant_id)

        assert result is True

    @pytest.mark.asyncio
    async def test_is_mergeable_false_different_tenant(self, mock_session, tenant_id):
        """is_mergeable returns False for different tenants."""
        entity_a = create_mock_entity(tenant_id=tenant_id, name="Entity A")
        entity_b = create_mock_entity(tenant_id=uuid.uuid4(), name="Entity B")

        service = MergeService(mock_session)
        result = await service.is_mergeable(entity_a, entity_b, tenant_id)

        assert result is False

    @pytest.mark.asyncio
    async def test_is_mergeable_false_same_entity(self, mock_session, tenant_id):
        """is_mergeable returns False when merging with self."""
        entity = create_mock_entity(tenant_id=tenant_id, name="Entity")

        service = MergeService(mock_session)
        result = await service.is_mergeable(entity, entity, tenant_id)

        assert result is False

    @pytest.mark.asyncio
    async def test_is_mergeable_false_already_alias(self, mock_session, tenant_id):
        """is_mergeable returns False when entity is already an alias."""
        entity_a = create_mock_entity(tenant_id=tenant_id, name="Entity A")
        entity_b = create_mock_entity(
            tenant_id=tenant_id,
            name="Entity B",
            is_canonical=False,
            is_alias_of=uuid.uuid4(),
        )

        service = MergeService(mock_session)
        result = await service.is_mergeable(entity_a, entity_b, tenant_id)

        assert result is False

    @pytest.mark.asyncio
    async def test_validate_merge_returns_issues(self, mock_session, tenant_id):
        """validate_merge returns list of issues."""
        entity_a = create_mock_entity(tenant_id=tenant_id, name="Entity A")
        entity_b = create_mock_entity(
            tenant_id=uuid.uuid4(),  # Different tenant
            name="Entity B",
        )

        service = MergeService(mock_session)
        issues = await service.validate_merge(entity_a, [entity_b], tenant_id)

        assert len(issues) > 0
        assert any("tenant" in issue.lower() for issue in issues)

    @pytest.mark.asyncio
    async def test_validate_merge_different_types(self, mock_session, tenant_id):
        """validate_merge reports type mismatch."""
        entity_a = create_mock_entity(
            tenant_id=tenant_id, name="Entity A", entity_type=EntityType.PERSON.value
        )
        entity_b = create_mock_entity(
            tenant_id=tenant_id, name="Entity B", entity_type=EntityType.ORGANIZATION.value
        )

        service = MergeService(mock_session)
        issues = await service.validate_merge(entity_a, [entity_b], tenant_id)

        assert any("type" in issue.lower() for issue in issues)

    @pytest.mark.asyncio
    async def test_validate_merge_empty_merged_list(self, mock_session, tenant_id):
        """validate_merge requires at least one entity to merge."""
        entity = create_mock_entity(tenant_id=tenant_id, name="Entity")

        service = MergeService(mock_session)
        issues = await service.validate_merge(entity, [], tenant_id)

        assert len(issues) > 0
        assert any("required" in issue.lower() for issue in issues)

    @pytest.mark.asyncio
    async def test_validate_merge_no_issues(self, mock_session, tenant_id):
        """validate_merge returns empty list for valid merge."""
        entity_a = create_mock_entity(tenant_id=tenant_id, name="Entity A")
        entity_b = create_mock_entity(tenant_id=tenant_id, name="Entity B")

        service = MergeService(mock_session)
        issues = await service.validate_merge(entity_a, [entity_b], tenant_id)

        assert issues == []


class TestMergeServicePropertyMerging:
    """Tests for MergeService._merge_properties."""

    def test_merge_properties_combines(self, mock_session, canonical_entity, merged_entity):
        """_merge_properties combines properties from merged entity."""
        service = MergeService(mock_session)

        details = service._merge_properties(canonical_entity, merged_entity)

        # Should have merge details for properties and external_ids
        assert "properties" in details or "external_ids" in details

    def test_merge_properties_updates_description(self, mock_session, tenant_id):
        """_merge_properties adopts description if canonical lacks one."""
        canonical = create_mock_entity(
            tenant_id=tenant_id, name="Canonical", description=None
        )
        merged = create_mock_entity(
            tenant_id=tenant_id, name="Merged", description="Has description"
        )

        service = MergeService(mock_session)
        details = service._merge_properties(canonical, merged)

        assert canonical.description == "Has description"
        assert "description" in details

    def test_merge_properties_higher_confidence(self, mock_session, tenant_id):
        """_merge_properties takes higher confidence score."""
        canonical = create_mock_entity(
            tenant_id=tenant_id, name="Canonical", confidence_score=0.7
        )
        merged = create_mock_entity(
            tenant_id=tenant_id, name="Merged", confidence_score=0.95
        )

        service = MergeService(mock_session)
        details = service._merge_properties(canonical, merged)

        assert canonical.confidence_score == 0.95
        assert "confidence_score" in details


class TestMergeServiceErrors:
    """Tests for MergeService error handling."""

    @pytest.mark.asyncio
    async def test_merge_entities_raises_on_invalid(self, mock_session, tenant_id):
        """merge_entities raises MergeValidationError on invalid input."""
        entity = create_mock_entity(tenant_id=tenant_id, name="Entity")

        service = MergeService(mock_session)

        with pytest.raises(MergeValidationError):
            await service.merge_entities(
                canonical_entity=entity,
                merged_entities=[entity],  # Same entity - invalid
                tenant_id=tenant_id,
                merge_reason="test",
            )

    @pytest.mark.asyncio
    async def test_merge_entities_raises_on_none_canonical(self, mock_session, tenant_id):
        """merge_entities raises on None canonical."""
        entity = create_mock_entity(tenant_id=tenant_id, name="Entity")

        service = MergeService(mock_session)

        with pytest.raises(MergeValidationError):
            await service.merge_entities(
                canonical_entity=None,
                merged_entities=[entity],
                tenant_id=tenant_id,
                merge_reason="test",
            )

    @pytest.mark.asyncio
    async def test_merge_entities_raises_on_empty_merged(self, mock_session, tenant_id):
        """merge_entities raises on empty merged list."""
        entity = create_mock_entity(tenant_id=tenant_id, name="Entity")

        service = MergeService(mock_session)

        with pytest.raises(MergeValidationError):
            await service.merge_entities(
                canonical_entity=entity,
                merged_entities=[],
                tenant_id=tenant_id,
                merge_reason="test",
            )


# =============================================================================
# MergeService Integration-like Tests (mocked)
# =============================================================================


class TestMergeServiceMergeEntities:
    """Tests for MergeService.merge_entities with mocked database."""

    @pytest.mark.asyncio
    async def test_merge_entities_success(self, mock_session, tenant_id):
        """merge_entities successfully merges entities."""
        canonical = create_mock_entity(
            tenant_id=tenant_id,
            name="Canonical",
            properties={"a": 1},
        )
        merged = create_mock_entity(
            tenant_id=tenant_id,
            name="Merged",
            properties={"b": 2},
        )

        # Mock relationship queries to return empty
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        service = MergeService(mock_session)
        result = await service.merge_entities(
            canonical_entity=canonical,
            merged_entities=[merged],
            tenant_id=tenant_id,
            merge_reason="auto_high_confidence",
            similarity_scores={"combined_score": 0.95},
        )

        assert result.canonical_entity_id == canonical.id
        assert len(result.merged_entity_ids) == 1
        assert merged.id in result.merged_entity_ids

    @pytest.mark.asyncio
    async def test_merge_entities_creates_aliases(self, mock_session, tenant_id):
        """merge_entities creates EntityAlias records."""
        canonical = create_mock_entity(tenant_id=tenant_id, name="Canonical")
        merged = create_mock_entity(tenant_id=tenant_id, name="Merged")

        # Mock relationship queries
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        service = MergeService(mock_session)
        result = await service.merge_entities(
            canonical_entity=canonical,
            merged_entities=[merged],
            tenant_id=tenant_id,
            merge_reason="user_approved",
        )

        assert len(result.aliases_created) == 1
        alias = result.aliases_created[0]
        assert alias.canonical_entity_id == canonical.id
        assert alias.alias_name == merged.name
        assert alias.original_entity_id == merged.id

    @pytest.mark.asyncio
    async def test_merge_entities_publishes_events(self, mock_session, tenant_id):
        """merge_entities publishes events when bus is configured."""
        canonical = create_mock_entity(tenant_id=tenant_id, name="Canonical")
        merged = create_mock_entity(tenant_id=tenant_id, name="Merged")

        # Mock relationship queries
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        # Mock event bus
        event_bus = AsyncMock()
        event_bus.publish = AsyncMock()

        service = MergeService(mock_session, event_bus=event_bus)
        await service.merge_entities(
            canonical_entity=canonical,
            merged_entities=[merged],
            tenant_id=tenant_id,
            merge_reason="batch",
        )

        # Should publish EntitiesMerged and AliasCreated events
        assert event_bus.publish.call_count >= 2

    @pytest.mark.asyncio
    async def test_merge_entities_multiple(self, mock_session, tenant_id):
        """merge_entities handles multiple entities being merged."""
        canonical = create_mock_entity(tenant_id=tenant_id, name="Canonical")
        merged1 = create_mock_entity(tenant_id=tenant_id, name="Merged 1")
        merged2 = create_mock_entity(tenant_id=tenant_id, name="Merged 2")
        merged3 = create_mock_entity(tenant_id=tenant_id, name="Merged 3")

        # Mock relationship queries
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        service = MergeService(mock_session)
        result = await service.merge_entities(
            canonical_entity=canonical,
            merged_entities=[merged1, merged2, merged3],
            tenant_id=tenant_id,
            merge_reason="batch",
        )

        assert len(result.merged_entity_ids) == 3
        assert len(result.aliases_created) == 3
