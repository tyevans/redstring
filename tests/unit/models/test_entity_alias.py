"""Unit tests for EntityAlias model."""

from datetime import UTC, datetime
from uuid import uuid4

from kg_builder.models.entity_alias import EntityAlias


class TestEntityAliasCreation:
    """Tests for EntityAlias model creation."""

    def test_entity_alias_creation(self):
        """Test creating an entity alias with required fields."""
        tenant_id = uuid4()
        canonical_entity_id = uuid4()
        original_entity_id = uuid4()

        alias = EntityAlias(
            tenant_id=tenant_id,
            canonical_entity_id=canonical_entity_id,
            alias_name="Domain Event",
            original_entity_id=original_entity_id,
            merged_at=datetime.now(UTC),
        )

        assert alias.tenant_id == tenant_id
        assert alias.canonical_entity_id == canonical_entity_id
        assert alias.alias_name == "Domain Event"
        assert alias.original_entity_id == original_entity_id
        assert alias.id is not None

    def test_entity_alias_auto_generates_id(self):
        """Test that alias ID is auto-generated."""
        alias = EntityAlias(
            tenant_id=uuid4(),
            canonical_entity_id=uuid4(),
            alias_name="Test Entity",
            original_entity_id=uuid4(),
            merged_at=datetime.now(UTC),
        )
        assert alias.id is not None

    def test_entity_alias_auto_normalizes_name(self):
        """Test that alias_normalized_name is auto-computed from alias_name."""
        alias = EntityAlias(
            tenant_id=uuid4(),
            canonical_entity_id=uuid4(),
            alias_name="Domain Event",
            original_entity_id=uuid4(),
            merged_at=datetime.now(UTC),
        )
        assert alias.alias_normalized_name == "domain event"

    def test_entity_alias_normalizes_unicode(self):
        """Test that normalized name handles unicode correctly."""
        alias = EntityAlias(
            tenant_id=uuid4(),
            canonical_entity_id=uuid4(),
            alias_name="Cafe Latte",  # Note: e without accent
            original_entity_id=uuid4(),
            merged_at=datetime.now(UTC),
        )
        # Should be lowercase with no special characters
        assert alias.alias_normalized_name == "cafe latte"

    def test_entity_alias_normalizes_whitespace(self):
        """Test that normalized name collapses whitespace."""
        alias = EntityAlias(
            tenant_id=uuid4(),
            canonical_entity_id=uuid4(),
            alias_name="  Domain   Event  ",
            original_entity_id=uuid4(),
            merged_at=datetime.now(UTC),
        )
        assert alias.alias_normalized_name == "domain event"

    def test_entity_alias_explicit_normalized_name(self):
        """Test that explicit normalized name is not overwritten."""
        alias = EntityAlias(
            tenant_id=uuid4(),
            canonical_entity_id=uuid4(),
            alias_name="Domain Event",
            alias_normalized_name="custom_normalized",
            original_entity_id=uuid4(),
            merged_at=datetime.now(UTC),
        )
        assert alias.alias_normalized_name == "custom_normalized"


class TestEntityAliasOptionalFields:
    """Tests for EntityAlias optional fields."""

    def test_entity_alias_with_source_page(self):
        """Test alias with source_page_id."""
        source_page_id = uuid4()
        alias = EntityAlias(
            tenant_id=uuid4(),
            canonical_entity_id=uuid4(),
            alias_name="Test",
            original_entity_id=uuid4(),
            source_page_id=source_page_id,
            merged_at=datetime.now(UTC),
        )
        assert alias.source_page_id == source_page_id

    def test_entity_alias_with_merge_event_id(self):
        """Test alias with merge_event_id for provenance."""
        merge_event_id = uuid4()
        alias = EntityAlias(
            tenant_id=uuid4(),
            canonical_entity_id=uuid4(),
            alias_name="Test",
            original_entity_id=uuid4(),
            merge_event_id=merge_event_id,
            merged_at=datetime.now(UTC),
        )
        assert alias.merge_event_id == merge_event_id

    def test_entity_alias_with_merge_reason(self):
        """Test alias with merge_reason."""
        alias = EntityAlias(
            tenant_id=uuid4(),
            canonical_entity_id=uuid4(),
            alias_name="Test",
            original_entity_id=uuid4(),
            merge_reason="auto_high_confidence",
            merged_at=datetime.now(UTC),
        )
        assert alias.merge_reason == "auto_high_confidence"

    def test_entity_alias_merge_reason_values(self):
        """Test various merge reason values."""
        reasons = ["auto_high_confidence", "user_approved", "batch", "manual"]

        for reason in reasons:
            alias = EntityAlias(
                tenant_id=uuid4(),
                canonical_entity_id=uuid4(),
                alias_name="Test",
                original_entity_id=uuid4(),
                merge_reason=reason,
                merged_at=datetime.now(UTC),
            )
            assert alias.merge_reason == reason


class TestEntityAliasRepr:
    """Tests for EntityAlias string representation."""

    def test_entity_alias_repr(self):
        """Test alias string representation."""
        canonical_id = uuid4()
        alias = EntityAlias(
            tenant_id=uuid4(),
            canonical_entity_id=canonical_id,
            alias_name="Test Entity",
            original_entity_id=uuid4(),
            merged_at=datetime.now(UTC),
        )
        repr_str = repr(alias)
        assert "EntityAlias" in repr_str
        assert "Test Entity" in repr_str
        assert str(canonical_id) in repr_str

    def test_entity_alias_repr_with_special_chars(self):
        """Test repr handles special characters in name."""
        alias = EntityAlias(
            tenant_id=uuid4(),
            canonical_entity_id=uuid4(),
            alias_name="Test 'Entity' with \"quotes\"",
            original_entity_id=uuid4(),
            merged_at=datetime.now(UTC),
        )
        # Should not raise an exception
        repr_str = repr(alias)
        assert "EntityAlias" in repr_str
