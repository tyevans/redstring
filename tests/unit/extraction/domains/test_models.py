"""Unit tests for domain schema Pydantic models.

This module tests all domain schema models for:
- Valid construction
- Field validation
- Name/ID normalization
- Helper methods
- Factory methods
- Edge cases
"""

import pytest
from pydantic import ValidationError

from redstring.extraction.domains.models import (
    ClassificationResult,
    ConfidenceThresholds,
    DomainSchema,
    DomainSummary,
    EntityTypeSchema,
    PropertySchema,
    RelationshipTypeSchema,
)

# =============================================================================
# PropertySchema Tests
# =============================================================================


class TestPropertySchema:
    """Tests for PropertySchema model."""

    def test_valid_property_with_defaults(self):
        """Test creating a property with minimal fields."""
        prop = PropertySchema(name="role")
        assert prop.name == "role"
        assert prop.type == "string"
        assert prop.description is None
        assert prop.required is False

    def test_valid_property_all_fields(self):
        """Test creating a property with all fields."""
        prop = PropertySchema(
            name="importance",
            type="number",
            description="Importance score from 1-10",
            required=True,
        )
        assert prop.name == "importance"
        assert prop.type == "number"
        assert prop.description == "Importance score from 1-10"
        assert prop.required is True

    def test_property_name_normalization_spaces(self):
        """Test that property names with spaces are normalized."""
        prop = PropertySchema(name="Character Role")
        assert prop.name == "character_role"

    def test_property_name_normalization_hyphens(self):
        """Test that property names with hyphens are normalized."""
        prop = PropertySchema(name="first-name")
        assert prop.name == "first_name"

    def test_property_name_normalization_uppercase(self):
        """Test that property names are lowercased."""
        prop = PropertySchema(name="UserName")
        assert prop.name == "username"

    def test_property_name_normalization_mixed(self):
        """Test normalization with mixed formatting."""
        prop = PropertySchema(name="  User-Full Name  ")
        assert prop.name == "user_full_name"

    def test_property_name_consecutive_underscores(self):
        """Test that consecutive underscores are collapsed."""
        prop = PropertySchema(name="user__role")
        assert prop.name == "user_role"

    def test_invalid_property_name_numeric_start(self):
        """Test that names starting with numbers are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            PropertySchema(name="123invalid")
        assert "valid identifier" in str(exc_info.value).lower()

    def test_invalid_property_name_special_chars(self):
        """Test that names with special characters are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            PropertySchema(name="user@role")
        assert "valid identifier" in str(exc_info.value).lower()

    def test_invalid_property_name_empty(self):
        """Test that empty names are rejected."""
        with pytest.raises(ValidationError):
            PropertySchema(name="")

    def test_invalid_property_name_only_special(self):
        """Test that names that normalize to empty are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            PropertySchema(name="---")
        assert "empty" in str(exc_info.value).lower()

    def test_property_type_validation(self):
        """Test that only valid property types are accepted."""
        for valid_type in ["string", "number", "boolean", "array", "object"]:
            prop = PropertySchema(name="test", type=valid_type)
            assert prop.type == valid_type

    def test_invalid_property_type(self):
        """Test that invalid property types are rejected."""
        with pytest.raises(ValidationError):
            PropertySchema(name="test", type="invalid")

    def test_property_description_max_length(self):
        """Test that description max length is enforced."""
        with pytest.raises(ValidationError):
            PropertySchema(name="test", description="x" * 501)

    def test_property_is_frozen(self):
        """Test that PropertySchema is immutable."""
        prop = PropertySchema(name="role")
        with pytest.raises(ValidationError):
            prop.name = "new_name"


# =============================================================================
# EntityTypeSchema Tests
# =============================================================================


class TestEntityTypeSchema:
    """Tests for EntityTypeSchema model."""

    def test_valid_entity_type_minimal(self):
        """Test creating an entity type with required fields only."""
        et = EntityTypeSchema(
            id="character",
            description="A person or being in the narrative",
        )
        assert et.id == "character"
        assert et.description == "A person or being in the narrative"
        assert et.properties == []
        assert et.examples == []

    def test_valid_entity_type_full(self):
        """Test creating an entity type with all fields."""
        et = EntityTypeSchema(
            id="character",
            description="A person or being in the narrative",
            properties=[
                PropertySchema(name="role", type="string"),
                PropertySchema(name="allegiance", type="string"),
            ],
            examples=["Hamlet", "Lady Macbeth", "Ophelia"],
        )
        assert et.id == "character"
        assert len(et.properties) == 2
        assert len(et.examples) == 3

    def test_entity_type_id_normalization(self):
        """Test that entity type IDs are normalized."""
        et = EntityTypeSchema(
            id="Plot Point",
            description="A significant event",
        )
        assert et.id == "plot_point"

    def test_entity_type_id_normalization_hyphens(self):
        """Test normalization with hyphens."""
        et = EntityTypeSchema(
            id="literary-device",
            description="A literary technique",
        )
        assert et.id == "literary_device"

    def test_invalid_entity_type_id(self):
        """Test that invalid IDs are rejected."""
        with pytest.raises(ValidationError):
            EntityTypeSchema(
                id="123invalid",
                description="Test",
            )

    def test_entity_type_empty_description_rejected(self):
        """Test that empty descriptions are rejected."""
        with pytest.raises(ValidationError):
            EntityTypeSchema(
                id="test",
                description="",
            )

    def test_entity_type_examples_max_10(self):
        """Test that more than 10 examples are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            EntityTypeSchema(
                id="test",
                description="Test entity",
                examples=[f"example{i}" for i in range(11)],
            )
        assert "10 examples" in str(exc_info.value)

    def test_entity_type_examples_exactly_10(self):
        """Test that exactly 10 examples are accepted."""
        et = EntityTypeSchema(
            id="test",
            description="Test entity",
            examples=[f"example{i}" for i in range(10)],
        )
        assert len(et.examples) == 10

    def test_get_property_existing(self):
        """Test get_property returns existing property."""
        et = EntityTypeSchema(
            id="character",
            description="A character",
            properties=[
                PropertySchema(name="role", type="string"),
                PropertySchema(name="allegiance", type="string"),
            ],
        )
        prop = et.get_property("role")
        assert prop is not None
        assert prop.name == "role"

    def test_get_property_normalized_lookup(self):
        """Test get_property with normalized lookup."""
        et = EntityTypeSchema(
            id="character",
            description="A character",
            properties=[PropertySchema(name="role", type="string")],
        )
        prop = et.get_property("Role")  # Different case
        assert prop is not None
        assert prop.name == "role"

    def test_get_property_not_found(self):
        """Test get_property returns None for unknown property."""
        et = EntityTypeSchema(
            id="character",
            description="A character",
            properties=[PropertySchema(name="role", type="string")],
        )
        assert et.get_property("unknown") is None


# =============================================================================
# RelationshipTypeSchema Tests
# =============================================================================


class TestRelationshipTypeSchema:
    """Tests for RelationshipTypeSchema model."""

    def test_valid_relationship_type_minimal(self):
        """Test creating a relationship type with required fields only."""
        rt = RelationshipTypeSchema(
            id="loves",
            description="Romantic love between characters",
        )
        assert rt.id == "loves"
        assert rt.description == "Romantic love between characters"
        assert rt.valid_source_types == []
        assert rt.valid_target_types == []
        assert rt.bidirectional is False

    def test_valid_relationship_type_with_constraints(self):
        """Test creating a relationship type with source/target constraints."""
        rt = RelationshipTypeSchema(
            id="implements",
            description="A class implements an interface",
            valid_source_types=["class"],
            valid_target_types=["interface"],
            bidirectional=False,
        )
        assert rt.id == "implements"
        assert rt.valid_source_types == ["class"]
        assert rt.valid_target_types == ["interface"]

    def test_relationship_type_id_normalization(self):
        """Test that relationship type IDs are normalized."""
        rt = RelationshipTypeSchema(
            id="Related To",
            description="Generic relationship",
        )
        assert rt.id == "related_to"

    def test_relationship_type_source_target_normalized(self):
        """Test that source/target types are normalized."""
        rt = RelationshipTypeSchema(
            id="loves",
            description="Romantic love",
            valid_source_types=["Character", "Plot-Point"],
            valid_target_types=["CHARACTER"],
        )
        assert rt.valid_source_types == ["character", "plot_point"]
        assert rt.valid_target_types == ["character"]

    def test_is_valid_source_no_constraints(self):
        """Test is_valid_source when no constraints specified."""
        rt = RelationshipTypeSchema(
            id="related_to",
            description="Generic relationship",
        )
        assert rt.is_valid_source("any_type") is True
        assert rt.is_valid_source("character") is True

    def test_is_valid_source_with_constraints(self):
        """Test is_valid_source with constraints."""
        rt = RelationshipTypeSchema(
            id="implements",
            description="Implementation relationship",
            valid_source_types=["class", "struct"],
        )
        assert rt.is_valid_source("class") is True
        assert rt.is_valid_source("struct") is True
        assert rt.is_valid_source("function") is False

    def test_is_valid_target_no_constraints(self):
        """Test is_valid_target when no constraints specified."""
        rt = RelationshipTypeSchema(
            id="related_to",
            description="Generic relationship",
        )
        assert rt.is_valid_target("any_type") is True

    def test_is_valid_target_with_constraints(self):
        """Test is_valid_target with constraints."""
        rt = RelationshipTypeSchema(
            id="implements",
            description="Implementation relationship",
            valid_target_types=["interface", "protocol"],
        )
        assert rt.is_valid_target("interface") is True
        assert rt.is_valid_target("protocol") is True
        assert rt.is_valid_target("class") is False

    @pytest.mark.parametrize(
        "written",
        [
            "Main Character",
            "  Main Character  ",
            "Main-Character",
            "Main--Character",
            "MAIN_CHARACTER",
            "_main_character_",
        ],
        ids=["spaces", "padded", "hyphen", "double_hyphen", "shouting", "underscored"],
    )
    def test_a_type_written_one_way_validates_against_itself(self, written):
        """The constraint list and the lookup normalize with the *same* rule.

        This is the test BACKLOG B75 said no existing one performed, and it is
        the whole shape of the defect: every case here passed the string the
        schema was *built with*, which is the one string a caller has in hand,
        and `is_valid_source` answered `False` for four of the six. The list
        was stored collapsed and stripped; the lookup only lowercased and
        stripped whitespace, so the value did not match itself.

        Entity type *ids* are normalized on load, so a caller passing an
        `EntityTypeSchema.id` never saw it. The caller who did is one passing
        an `Entity.entity_type` -- free-form text from the model, where "Main
        Character" is an ordinary answer -- through
        `DomainSchema.validate_relationship`.
        """
        rt = RelationshipTypeSchema(
            id="loves",
            description="Romantic love",
            valid_source_types=[written],
            valid_target_types=[written],
        )
        assert rt.is_valid_source(written) is True
        assert rt.is_valid_target(written) is True

    def test_a_constraint_agrees_with_the_entity_type_id_it_names(self):
        """The list holds *references* to `EntityTypeSchema.id`, and a
        reference that normalizes differently from its referent points at
        nothing. Before the shared normalizer these produced `main_character`
        and `main__character` from the same source text."""
        entity_type = EntityTypeSchema(id="Main--Character", description="A person")
        rt = RelationshipTypeSchema(
            id="loves",
            description="Romantic love",
            valid_source_types=["Main--Character"],
        )
        assert rt.valid_source_types == [entity_type.id]
        assert rt.is_valid_source(entity_type.id) is True

    def test_bidirectional_flag(self):
        """Test bidirectional flag."""
        rt = RelationshipTypeSchema(
            id="married_to",
            description="Marriage relationship",
            bidirectional=True,
        )
        assert rt.bidirectional is True


# =============================================================================
# ConfidenceThresholds Tests
# =============================================================================


class TestConfidenceThresholds:
    """Tests for ConfidenceThresholds model."""

    def test_default_thresholds(self):
        """Test default confidence thresholds."""
        ct = ConfidenceThresholds()
        assert ct.entity_extraction == 0.6
        assert ct.relationship_extraction == 0.5

    def test_custom_thresholds(self):
        """Test custom confidence thresholds."""
        ct = ConfidenceThresholds(
            entity_extraction=0.8,
            relationship_extraction=0.7,
        )
        assert ct.entity_extraction == 0.8
        assert ct.relationship_extraction == 0.7

    def test_threshold_bounds_zero(self):
        """Test that zero is a valid threshold."""
        ct = ConfidenceThresholds(
            entity_extraction=0.0,
            relationship_extraction=0.0,
        )
        assert ct.entity_extraction == 0.0
        assert ct.relationship_extraction == 0.0

    def test_threshold_bounds_one(self):
        """Test that one is a valid threshold."""
        ct = ConfidenceThresholds(
            entity_extraction=1.0,
            relationship_extraction=1.0,
        )
        assert ct.entity_extraction == 1.0
        assert ct.relationship_extraction == 1.0

    def test_threshold_above_one_rejected(self):
        """Test that thresholds above 1.0 are rejected."""
        with pytest.raises(ValidationError):
            ConfidenceThresholds(entity_extraction=1.1)

    def test_threshold_negative_rejected(self):
        """Test that negative thresholds are rejected."""
        with pytest.raises(ValidationError):
            ConfidenceThresholds(entity_extraction=-0.1)


# =============================================================================
# DomainSchema Tests
# =============================================================================


class TestDomainSchema:
    """Tests for DomainSchema model."""

    @pytest.fixture
    def valid_domain_schema(self):
        """Create a valid domain schema for testing."""
        return DomainSchema(
            domain_id="literature_fiction",
            display_name="Literature & Fiction",
            description="Novels, plays, and narrative works",
            entity_types=[
                EntityTypeSchema(id="character", description="A character in the narrative"),
                EntityTypeSchema(id="theme", description="A thematic element"),
                EntityTypeSchema(id="setting", description="Time and place of the story"),
            ],
            relationship_types=[
                RelationshipTypeSchema(
                    id="loves",
                    description="Romantic love",
                    valid_source_types=["character"],
                    valid_target_types=["character"],
                ),
                RelationshipTypeSchema(
                    id="embodies",
                    description="Character embodies theme",
                    valid_source_types=["character"],
                    valid_target_types=["theme"],
                ),
            ],
            extraction_prompt_template="Extract entities from: {content}",
        )

    @pytest.mark.parametrize(
        "written",
        ["Plot Point", "plot-point", "  Plot  Point  ", "__plot_point__"],
        ids=["spaces", "hyphen", "padded", "underscored"],
    )
    def test_an_entity_type_is_found_by_the_id_it_was_declared_with(self, written):
        """The second instance of B75, found by grepping for it rather than by
        another report -- `get_entity_type`, `get_relationship_type`,
        `is_valid_entity_type` and `is_valid_relationship_type` all normalized
        their argument with `.lower().strip()` while the id they search was
        stored fully normalized.

        The reference page documented it as "the lookup is weaker than the
        validator, deliberately or not", which is the shape worth noticing:
        two normalizers, no mechanism that fails when they disagree, and a doc
        sentence standing in for the test.
        """
        schema = DomainSchema(
            domain_id="literature_fiction",
            display_name="Literature & Fiction",
            description="Novels",
            entity_types=[EntityTypeSchema(id=written, description="A beat in the plot")],
            relationship_types=[RelationshipTypeSchema(id=written, description="Follows on")],
            extraction_prompt_template="Extract entities from: {content}",
        )

        assert schema.get_entity_type(written) is not None
        assert schema.get_relationship_type(written) is not None
        assert schema.is_valid_entity_type(written) is True
        assert schema.is_valid_relationship_type(written) is True

    def test_valid_domain_schema(self, valid_domain_schema):
        """Test creating a valid domain schema."""
        assert valid_domain_schema.domain_id == "literature_fiction"
        assert valid_domain_schema.display_name == "Literature & Fiction"
        assert len(valid_domain_schema.entity_types) == 3
        assert len(valid_domain_schema.relationship_types) == 2

    def test_domain_schema_defaults(self, valid_domain_schema):
        """Test default values in domain schema."""
        assert valid_domain_schema.version == "1.0.0"
        assert valid_domain_schema.confidence_thresholds.entity_extraction == 0.6
        assert valid_domain_schema.confidence_thresholds.relationship_extraction == 0.5

    def test_domain_id_pattern_valid(self):
        """Test valid domain ID patterns."""
        valid_ids = ["books", "literature_fiction", "tech_docs", "news123", "a"]
        for domain_id in valid_ids:
            schema = DomainSchema(
                domain_id=domain_id,
                display_name="Test",
                description="Test domain",
                entity_types=[EntityTypeSchema(id="test", description="Test")],
                relationship_types=[RelationshipTypeSchema(id="test", description="Test")],
                extraction_prompt_template="Test",
            )
            assert schema.domain_id == domain_id

    def test_domain_id_pattern_invalid(self):
        """Test invalid domain ID patterns."""
        invalid_ids = [
            "Literature_Fiction",  # uppercase
            "123books",  # starts with number
            "books!",  # special character
            "lit-fiction",  # hyphen
            "_books",  # starts with underscore
        ]
        for domain_id in invalid_ids:
            with pytest.raises(ValidationError):
                DomainSchema(
                    domain_id=domain_id,
                    display_name="Test",
                    description="Test domain",
                    entity_types=[EntityTypeSchema(id="test", description="Test")],
                    relationship_types=[RelationshipTypeSchema(id="test", description="Test")],
                    extraction_prompt_template="Test",
                )

    def test_domain_schema_requires_entity_types(self):
        """Test that at least one entity type is required."""
        with pytest.raises(ValidationError):
            DomainSchema(
                domain_id="test",
                display_name="Test",
                description="Test domain",
                entity_types=[],
                relationship_types=[RelationshipTypeSchema(id="test", description="Test")],
                extraction_prompt_template="Test",
            )

    def test_domain_schema_requires_relationship_types(self):
        """Test that at least one relationship type is required."""
        with pytest.raises(ValidationError):
            DomainSchema(
                domain_id="test",
                display_name="Test",
                description="Test domain",
                entity_types=[EntityTypeSchema(id="test", description="Test")],
                relationship_types=[],
                extraction_prompt_template="Test",
            )

    def test_relationship_references_valid_source_types(self, valid_domain_schema):
        """Test that relationship source types are validated against entity types."""
        with pytest.raises(ValidationError) as exc_info:
            DomainSchema(
                domain_id="test",
                display_name="Test",
                description="Test domain",
                entity_types=[EntityTypeSchema(id="character", description="A character")],
                relationship_types=[
                    RelationshipTypeSchema(
                        id="loves",
                        description="Love relationship",
                        valid_source_types=["unknown_type"],  # Invalid reference
                    )
                ],
                extraction_prompt_template="Test",
            )
        assert "unknown source type" in str(exc_info.value).lower()

    def test_relationship_references_valid_target_types(self):
        """Test that relationship target types are validated against entity types."""
        with pytest.raises(ValidationError) as exc_info:
            DomainSchema(
                domain_id="test",
                display_name="Test",
                description="Test domain",
                entity_types=[EntityTypeSchema(id="character", description="A character")],
                relationship_types=[
                    RelationshipTypeSchema(
                        id="loves",
                        description="Love relationship",
                        valid_target_types=["unknown_type"],  # Invalid reference
                    )
                ],
                extraction_prompt_template="Test",
            )
        assert "unknown target type" in str(exc_info.value).lower()

    def test_version_format_valid(self):
        """Test valid version formats."""
        valid_versions = ["1.0.0", "0.1.0", "10.20.30", "0.0.1"]
        for version in valid_versions:
            schema = DomainSchema(
                domain_id="test",
                display_name="Test",
                description="Test domain",
                entity_types=[EntityTypeSchema(id="test", description="Test")],
                relationship_types=[RelationshipTypeSchema(id="test", description="Test")],
                extraction_prompt_template="Test",
                version=version,
            )
            assert schema.version == version

    def test_version_format_invalid(self):
        """Test invalid version formats."""
        invalid_versions = ["1.0", "v1.0.0", "1.0.0.0", "1.0.0-beta"]
        for version in invalid_versions:
            with pytest.raises(ValidationError):
                DomainSchema(
                    domain_id="test",
                    display_name="Test",
                    description="Test domain",
                    entity_types=[EntityTypeSchema(id="test", description="Test")],
                    relationship_types=[RelationshipTypeSchema(id="test", description="Test")],
                    extraction_prompt_template="Test",
                    version=version,
                )

    def test_get_entity_type_ids(self, valid_domain_schema):
        """Test getting entity type IDs."""
        ids = valid_domain_schema.get_entity_type_ids()
        assert ids == ["character", "theme", "setting"]

    def test_get_relationship_type_ids(self, valid_domain_schema):
        """Test getting relationship type IDs."""
        ids = valid_domain_schema.get_relationship_type_ids()
        assert ids == ["loves", "embodies"]

    def test_get_entity_type_existing(self, valid_domain_schema):
        """Test getting existing entity type."""
        et = valid_domain_schema.get_entity_type("character")
        assert et is not None
        assert et.id == "character"

    def test_get_entity_type_normalized(self, valid_domain_schema):
        """Test getting entity type with different case."""
        et = valid_domain_schema.get_entity_type("CHARACTER")
        assert et is not None
        assert et.id == "character"

    def test_get_entity_type_not_found(self, valid_domain_schema):
        """Test getting non-existent entity type."""
        assert valid_domain_schema.get_entity_type("unknown") is None

    def test_get_relationship_type_existing(self, valid_domain_schema):
        """Test getting existing relationship type."""
        rt = valid_domain_schema.get_relationship_type("loves")
        assert rt is not None
        assert rt.id == "loves"

    def test_get_relationship_type_not_found(self, valid_domain_schema):
        """Test getting non-existent relationship type."""
        assert valid_domain_schema.get_relationship_type("unknown") is None

    def test_is_valid_entity_type_existing(self, valid_domain_schema):
        """Test checking valid entity type."""
        assert valid_domain_schema.is_valid_entity_type("character") is True
        assert valid_domain_schema.is_valid_entity_type("theme") is True
        assert valid_domain_schema.is_valid_entity_type("setting") is True

    def test_is_valid_entity_type_custom_always_valid(self, valid_domain_schema):
        """Test that 'custom' is always a valid entity type."""
        assert valid_domain_schema.is_valid_entity_type("custom") is True

    def test_is_valid_entity_type_unknown(self, valid_domain_schema):
        """Test checking invalid entity type."""
        assert valid_domain_schema.is_valid_entity_type("unknown") is False

    def test_is_valid_relationship_type_existing(self, valid_domain_schema):
        """Test checking valid relationship type."""
        assert valid_domain_schema.is_valid_relationship_type("loves") is True
        assert valid_domain_schema.is_valid_relationship_type("embodies") is True

    def test_is_valid_relationship_type_related_to_always_valid(self, valid_domain_schema):
        """Test that 'related_to' is always a valid relationship type."""
        assert valid_domain_schema.is_valid_relationship_type("related_to") is True

    def test_is_valid_relationship_type_unknown(self, valid_domain_schema):
        """Test checking invalid relationship type."""
        assert valid_domain_schema.is_valid_relationship_type("unknown") is False

    def test_validate_relationship_valid(self, valid_domain_schema):
        """Test validating a valid relationship."""
        is_valid, error = valid_domain_schema.validate_relationship(
            relationship_type="loves",
            source_entity_type="character",
            target_entity_type="character",
        )
        assert is_valid is True
        assert error is None

    def test_validate_relationship_invalid_type(self, valid_domain_schema):
        """Test validating relationship with invalid type."""
        is_valid, error = valid_domain_schema.validate_relationship(
            relationship_type="unknown_rel",
            source_entity_type="character",
            target_entity_type="character",
        )
        assert is_valid is False
        assert "unknown relationship type" in error.lower()

    def test_validate_relationship_invalid_source(self, valid_domain_schema):
        """Test validating relationship with invalid source type."""
        is_valid, error = valid_domain_schema.validate_relationship(
            relationship_type="loves",
            source_entity_type="theme",  # loves only allows character -> character
            target_entity_type="character",
        )
        assert is_valid is False
        assert "not a valid source" in error.lower()

    def test_validate_relationship_invalid_target(self, valid_domain_schema):
        """Test validating relationship with invalid target type."""
        is_valid, error = valid_domain_schema.validate_relationship(
            relationship_type="embodies",
            source_entity_type="character",
            target_entity_type="character",  # embodies only allows character -> theme
        )
        assert is_valid is False
        assert "not a valid target" in error.lower()

    def test_validate_relationship_related_to_always_valid(self, valid_domain_schema):
        """Test that 'related_to' is always valid."""
        is_valid, error = valid_domain_schema.validate_relationship(
            relationship_type="related_to",
            source_entity_type="anything",
            target_entity_type="else",
        )
        assert is_valid is True
        assert error is None


# =============================================================================
# DomainSummary Tests
# =============================================================================


class TestDomainSummary:
    """Tests for DomainSummary model."""

    def test_from_schema(self):
        """Test creating summary from full schema."""
        schema = DomainSchema(
            domain_id="test_domain",
            display_name="Test Domain",
            description="A test domain",
            entity_types=[
                EntityTypeSchema(id="entity1", description="Entity 1"),
                EntityTypeSchema(id="entity2", description="Entity 2"),
            ],
            relationship_types=[
                RelationshipTypeSchema(id="rel1", description="Relationship 1"),
            ],
            extraction_prompt_template="Test prompt",
        )

        summary = DomainSummary.from_schema(schema)

        assert summary.domain_id == "test_domain"
        assert summary.display_name == "Test Domain"
        assert summary.description == "A test domain"
        assert summary.entity_type_count == 2
        assert summary.relationship_type_count == 1
        assert summary.entity_types == ["entity1", "entity2"]
        assert summary.relationship_types == ["rel1"]

    def test_summary_is_frozen(self):
        """Test that DomainSummary is immutable."""
        summary = DomainSummary(
            domain_id="test",
            display_name="Test",
            description="Test",
            entity_type_count=1,
            relationship_type_count=1,
            entity_types=["entity1"],
            relationship_types=["rel1"],
        )
        with pytest.raises(ValidationError):
            summary.domain_id = "new_id"


# =============================================================================
# ClassificationResult Tests
# =============================================================================


class TestClassificationResult:
    """Tests for ClassificationResult model."""

    def test_valid_classification_minimal(self):
        """Test creating a classification result with minimal fields."""
        result = ClassificationResult(
            domain="literature_fiction",
            confidence=0.92,
        )
        assert result.domain == "literature_fiction"
        assert result.confidence == 0.92
        assert result.reasoning is None
        assert result.alternatives is None

    def test_valid_classification_full(self):
        """Test creating a classification result with all fields."""
        result = ClassificationResult(
            domain="literature_fiction",
            confidence=0.92,
            reasoning="Contains character dialogue and narrative structure",
            alternatives=[
                {"domain": "news", "confidence": 0.05},
                {"domain": "technical", "confidence": 0.03},
            ],
        )
        assert result.domain == "literature_fiction"
        assert result.confidence == 0.92
        assert "dialogue" in result.reasoning
        assert len(result.alternatives) == 2

    def test_confidence_bounds_valid(self):
        """Test valid confidence bounds."""
        # Minimum
        result = ClassificationResult(domain="test", confidence=0.0)
        assert result.confidence == 0.0

        # Maximum
        result = ClassificationResult(domain="test", confidence=1.0)
        assert result.confidence == 1.0

    def test_confidence_above_one_rejected(self):
        """Test that confidence above 1.0 is rejected."""
        with pytest.raises(ValidationError):
            ClassificationResult(domain="test", confidence=1.5)

    def test_confidence_negative_rejected(self):
        """Test that negative confidence is rejected."""
        with pytest.raises(ValidationError):
            ClassificationResult(domain="test", confidence=-0.1)

    def test_is_confident_above_threshold(self):
        """Test is_confident method above threshold."""
        result = ClassificationResult(domain="test", confidence=0.85)
        assert result.is_confident(threshold=0.7) is True

    def test_is_confident_below_threshold(self):
        """Test is_confident method below threshold."""
        result = ClassificationResult(domain="test", confidence=0.65)
        assert result.is_confident(threshold=0.7) is False

    def test_is_confident_at_threshold(self):
        """Test is_confident method at exact threshold."""
        result = ClassificationResult(domain="test", confidence=0.7)
        assert result.is_confident(threshold=0.7) is True

    def test_is_confident_default_threshold(self):
        """Test is_confident method with default threshold (0.7)."""
        result_confident = ClassificationResult(domain="test", confidence=0.75)
        assert result_confident.is_confident() is True

        result_not_confident = ClassificationResult(domain="test", confidence=0.65)
        assert result_not_confident.is_confident() is False


# =============================================================================
# Integration Tests
# =============================================================================


class TestModelIntegration:
    """Integration tests for domain schema models."""

    def test_full_domain_schema_serialization(self):
        """Test serializing a full domain schema to JSON and back."""
        schema = DomainSchema(
            domain_id="literature_fiction",
            display_name="Literature & Fiction",
            description="Novels, plays, and narrative works",
            entity_types=[
                EntityTypeSchema(
                    id="character",
                    description="A person or being in the narrative",
                    properties=[
                        PropertySchema(name="role", type="string", description="Character's role"),
                        PropertySchema(name="allegiance", type="string"),
                    ],
                    examples=["Hamlet", "Lady Macbeth"],
                ),
                EntityTypeSchema(id="theme", description="A thematic element"),
            ],
            relationship_types=[
                RelationshipTypeSchema(
                    id="loves",
                    description="Romantic love",
                    valid_source_types=["character"],
                    valid_target_types=["character"],
                    bidirectional=True,
                ),
            ],
            extraction_prompt_template="Extract entities from: {content}",
            confidence_thresholds=ConfidenceThresholds(
                entity_extraction=0.7,
                relationship_extraction=0.6,
            ),
            version="1.2.0",
        )

        # Serialize to JSON
        json_str = schema.model_dump_json()

        # Deserialize back
        loaded_schema = DomainSchema.model_validate_json(json_str)

        # Verify all fields
        assert loaded_schema.domain_id == schema.domain_id
        assert loaded_schema.display_name == schema.display_name
        assert len(loaded_schema.entity_types) == 2
        assert len(loaded_schema.relationship_types) == 1
        assert loaded_schema.confidence_thresholds.entity_extraction == 0.7
        assert loaded_schema.version == "1.2.0"

    def test_domain_schema_from_dict(self):
        """Test creating domain schema from dictionary."""
        data = {
            "domain_id": "tech_docs",
            "display_name": "Technical Documentation",
            "description": "API docs, README files, and technical guides",
            "entity_types": [
                {"id": "function", "description": "A code function"},
                {"id": "class", "description": "A code class"},
            ],
            "relationship_types": [
                {"id": "calls", "description": "Function calls another function"},
            ],
            "extraction_prompt_template": "Extract code entities from: {content}",
        }

        schema = DomainSchema.model_validate(data)

        assert schema.domain_id == "tech_docs"
        assert len(schema.entity_types) == 2
        assert schema.entity_types[0].id == "function"

    def test_imports_from_package(self):
        """Test that all models can be imported from the package."""
        from redstring.extraction.domains import (
            ClassificationResult,
            ConfidenceThresholds,
            DomainSchema,
            DomainSummary,
            EntityTypeSchema,
            PropertySchema,
            RelationshipTypeSchema,
        )

        # Verify all imports work
        assert PropertySchema is not None
        assert EntityTypeSchema is not None
        assert RelationshipTypeSchema is not None
        assert ConfidenceThresholds is not None
        assert DomainSchema is not None
        assert DomainSummary is not None
        assert ClassificationResult is not None
