"""
Unit tests for extraction schemas.

These tests verify the Pydantic schemas used for LLM extraction output
are working correctly with proper validation rules.

Note: This module imports directly from the schemas module file to avoid
loading the full app context with database dependencies. This enables
running these pure Pydantic tests without Docker/database setup.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from pydantic import ValidationError

# Direct import from schemas.py file to avoid __init__.py chain
# that pulls in database-dependent modules
_schemas_path = (
    Path(__file__).parent.parent.parent.parent / "src" / "kg_builder" / "extraction" / "schemas.py"
)
_spec = spec_from_file_location("extraction_schemas", _schemas_path)
_schemas = module_from_spec(_spec)
_spec.loader.exec_module(_schemas)

# Extract the classes and types we need
EntityTypeLiteral = _schemas.EntityTypeLiteral
RelationshipTypeLiteral = _schemas.RelationshipTypeLiteral
FunctionProperties = _schemas.FunctionProperties
ClassProperties = _schemas.ClassProperties
ModuleProperties = _schemas.ModuleProperties
PatternProperties = _schemas.PatternProperties
ExampleProperties = _schemas.ExampleProperties
ParameterProperties = _schemas.ParameterProperties
ExceptionProperties = _schemas.ExceptionProperties
ExtractedEntitySchema = _schemas.ExtractedEntitySchema
ExtractedRelationshipSchema = _schemas.ExtractedRelationshipSchema
ExtractionResult = _schemas.ExtractionResult
get_property_schema_for_type = _schemas.get_property_schema_for_type


class TestExtractedEntitySchema:
    """Tests for ExtractedEntitySchema."""

    def test_valid_entity_minimal(self):
        """Test creating entity with minimal required fields."""
        entity = ExtractedEntitySchema(
            name="MyClass",
            entity_type="class",
            confidence=0.9,
        )

        assert entity.name == "MyClass"
        assert entity.entity_type == "class"
        assert entity.confidence == 0.9
        assert entity.description is None
        assert entity.properties == {}
        assert entity.source_text is None
        assert entity.aliases == []

    def test_valid_entity_full(self):
        """Test creating entity with all fields."""
        entity = ExtractedEntitySchema(
            name="DomainEvent",
            entity_type="class",
            description="Base class for domain events",
            properties={"base_classes": ["BaseModel"], "is_abstract": True},
            confidence=0.95,
            source_text="class DomainEvent(BaseModel): ...",
            aliases=["Event", "BaseEvent"],
        )

        assert entity.name == "DomainEvent"
        assert entity.entity_type == "class"
        assert entity.description == "Base class for domain events"
        assert entity.properties["base_classes"] == ["BaseModel"]
        assert entity.properties["is_abstract"] is True
        assert entity.confidence == 0.95
        assert entity.source_text == "class DomainEvent(BaseModel): ..."
        assert "Event" in entity.aliases

    def test_entity_name_normalization(self):
        """Test that entity names are normalized (whitespace stripped)."""
        entity = ExtractedEntitySchema(
            name="  MyFunction  ",
            entity_type="function",
            confidence=0.8,
        )

        assert entity.name == "MyFunction"

    def test_unknown_entity_type_normalizes_to_custom(self):
        """Unknown entity types are normalized, not rejected.

        `entity_type` is a free string so domain-specific types survive;
        `normalize_entity_type` maps anything unrecognised to "custom".
        """
        entity = ExtractedEntitySchema(
            name="Test",
            entity_type="invalid_type",
            confidence=0.9,
        )

        assert entity.entity_type == "custom"

    def test_invalid_confidence_too_high(self):
        """Test that confidence > 1.0 raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ExtractedEntitySchema(
                name="Test",
                entity_type="function",
                confidence=1.5,
            )

        assert "confidence" in str(exc_info.value)

    def test_invalid_confidence_too_low(self):
        """Test that confidence < 0.0 raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ExtractedEntitySchema(
                name="Test",
                entity_type="function",
                confidence=-0.1,
            )

        assert "confidence" in str(exc_info.value)

    def test_invalid_empty_name(self):
        """Test that empty name raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ExtractedEntitySchema(
                name="",
                entity_type="function",
                confidence=0.9,
            )

        assert "name" in str(exc_info.value)

    def test_invalid_name_too_long(self):
        """Test that name exceeding max length raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ExtractedEntitySchema(
                name="x" * 600,  # Exceeds 512 max length
                entity_type="function",
                confidence=0.9,
            )

        assert "name" in str(exc_info.value)

    def test_source_text_truncation(self):
        """Test that source_text is truncated to max 500 chars."""
        long_text = "x" * 600
        entity = ExtractedEntitySchema(
            name="Test",
            entity_type="function",
            confidence=0.9,
            source_text=long_text,
        )

        assert len(entity.source_text) == 500
        assert entity.source_text.endswith("...")

    def test_all_entity_types(self):
        """Test that all entity types from the literal are valid."""
        entity_types = [
            # General types
            "person",
            "organization",
            "location",
            "event",
            "product",
            "concept",
            "document",
            "date",
            "custom",
            # Documentation-specific types
            "function",
            "class",
            "module",
            "pattern",
            "example",
            "parameter",
            "return_type",
            "exception",
        ]

        for entity_type in entity_types:
            entity = ExtractedEntitySchema(
                name=f"Test{entity_type}",
                entity_type=entity_type,  # type: ignore
                confidence=0.9,
            )
            assert entity.entity_type == entity_type


class TestExtractedRelationshipSchema:
    """Tests for ExtractedRelationshipSchema."""

    def test_valid_relationship_minimal(self):
        """Test creating relationship with minimal required fields."""
        rel = ExtractedRelationshipSchema(
            source_name="ClassA",
            target_name="ClassB",
            relationship_type="extends",
            confidence=0.85,
        )

        assert rel.source_name == "ClassA"
        assert rel.target_name == "ClassB"
        assert rel.relationship_type == "extends"
        assert rel.confidence == 0.85
        assert rel.context is None
        assert rel.properties == {}

    def test_valid_relationship_full(self):
        """Test creating relationship with all fields."""
        rel = ExtractedRelationshipSchema(
            source_name="UserService",
            target_name="UserRepository",
            relationship_type="uses",
            confidence=0.9,
            context="UserService depends on UserRepository for data access",
            properties={"optional": False},
        )

        assert rel.source_name == "UserService"
        assert rel.target_name == "UserRepository"
        assert rel.relationship_type == "uses"
        assert rel.context is not None
        assert rel.properties["optional"] is False

    def test_relationship_name_normalization(self):
        """Test that relationship entity names are normalized."""
        rel = ExtractedRelationshipSchema(
            source_name="  ClassA  ",
            target_name="  ClassB  ",
            relationship_type="extends",
            confidence=0.9,
        )

        assert rel.source_name == "ClassA"
        assert rel.target_name == "ClassB"

    def test_invalid_relationship_same_entity(self):
        """Test that relationship with same source and target raises error."""
        with pytest.raises(ValidationError) as exc_info:
            ExtractedRelationshipSchema(
                source_name="ClassA",
                target_name="ClassA",
                relationship_type="extends",
                confidence=0.9,
            )

        assert "different entities" in str(exc_info.value)

    def test_invalid_relationship_same_entity_case_insensitive(self):
        """Test that same entity check is case insensitive."""
        with pytest.raises(ValidationError) as exc_info:
            ExtractedRelationshipSchema(
                source_name="classa",
                target_name="ClassA",
                relationship_type="extends",
                confidence=0.9,
            )

        assert "different entities" in str(exc_info.value)

    def test_unknown_relationship_type_normalizes_to_related_to(self):
        """Unknown relationship types are normalized, not rejected."""
        relationship = ExtractedRelationshipSchema(
            source_name="A",
            target_name="B",
            relationship_type="invalid_rel",
            confidence=0.9,
        )

        assert relationship.relationship_type == "related_to"

    def test_all_relationship_types(self):
        """Test that all relationship types from the literal are valid."""
        relationship_types = [
            # Code structure
            "uses",
            "implements",
            "extends",
            "inherits_from",
            "contains",
            "part_of",
            # Function/method
            "calls",
            "returns",
            "accepts",
            "raises",
            # Dependencies
            "depends_on",
            "imports",
            "requires",
            # Documentation
            "documented_in",
            "example_of",
            "demonstrates",
            # Generic
            "related_to",
            "references",
            "defines",
            "instantiates",
        ]

        for rel_type in relationship_types:
            rel = ExtractedRelationshipSchema(
                source_name="A",
                target_name="B",
                relationship_type=rel_type,  # type: ignore
                confidence=0.9,
            )
            assert rel.relationship_type == rel_type


class TestExtractionResult:
    """Tests for ExtractionResult container schema."""

    def test_empty_result(self):
        """Test creating empty extraction result."""
        result = ExtractionResult()

        assert result.entities == []
        assert result.relationships == []
        assert result.entity_count == 0
        assert result.relationship_count == 0

    def test_result_with_entities_only(self):
        """Test extraction result with entities but no relationships."""
        entities = [
            ExtractedEntitySchema(name="ClassA", entity_type="class", confidence=0.9),
            ExtractedEntitySchema(name="ClassB", entity_type="class", confidence=0.8),
        ]
        result = ExtractionResult(entities=entities)

        assert result.entity_count == 2
        assert result.relationship_count == 0

    def test_result_with_entities_and_relationships(self):
        """Test complete extraction result with entities and relationships."""
        entities = [
            ExtractedEntitySchema(name="A", entity_type="class", confidence=0.9),
            ExtractedEntitySchema(name="B", entity_type="function", confidence=0.8),
        ]
        relationships = [
            ExtractedRelationshipSchema(
                source_name="A",
                target_name="B",
                relationship_type="uses",
                confidence=0.85,
            ),
        ]
        result = ExtractionResult(
            entities=entities,
            relationships=relationships,
        )

        assert result.entity_count == 2
        assert result.relationship_count == 1

    def test_result_relationship_with_missing_source_is_filtered(self):
        """Relationships referencing an unknown source entity are dropped."""
        entities = [
            ExtractedEntitySchema(name="B", entity_type="class", confidence=0.9),
        ]
        relationships = [
            ExtractedRelationshipSchema(
                source_name="A",  # A doesn't exist in entities
                target_name="B",
                relationship_type="uses",
                confidence=0.85,
            ),
        ]

        result = ExtractionResult(entities=entities, relationships=relationships)

        assert result.relationships == []

    def test_result_relationship_with_missing_target_is_filtered(self):
        """Relationships referencing an unknown target entity are dropped."""
        entities = [
            ExtractedEntitySchema(name="A", entity_type="class", confidence=0.9),
        ]
        relationships = [
            ExtractedRelationshipSchema(
                source_name="A",
                target_name="B",  # B doesn't exist in entities
                relationship_type="uses",
                confidence=0.85,
            ),
        ]

        result = ExtractionResult(entities=entities, relationships=relationships)

        assert result.relationships == []

    def test_result_relationship_validation_case_insensitive(self):
        """Test that relationship validation is case insensitive for entity names."""
        entities = [
            ExtractedEntitySchema(name="ClassA", entity_type="class", confidence=0.9),
            ExtractedEntitySchema(name="classb", entity_type="class", confidence=0.8),
        ]
        relationships = [
            ExtractedRelationshipSchema(
                source_name="classa",  # Different case
                target_name="ClassB",  # Different case
                relationship_type="extends",
                confidence=0.85,
            ),
        ]

        # Should not raise - case insensitive match
        result = ExtractionResult(entities=entities, relationships=relationships)
        assert result.relationship_count == 1

    def test_get_entities_by_type(self):
        """Test filtering entities by type."""
        entities = [
            ExtractedEntitySchema(name="func1", entity_type="function", confidence=0.9),
            ExtractedEntitySchema(name="Class1", entity_type="class", confidence=0.8),
            ExtractedEntitySchema(name="func2", entity_type="function", confidence=0.7),
            ExtractedEntitySchema(name="mod1", entity_type="module", confidence=0.9),
        ]
        result = ExtractionResult(entities=entities)

        functions = result.get_entities_by_type("function")
        classes = result.get_entities_by_type("class")
        patterns = result.get_entities_by_type("pattern")

        assert len(functions) == 2
        assert len(classes) == 1
        assert len(patterns) == 0

    def test_get_entity_names(self):
        """Test getting set of entity names."""
        entities = [
            ExtractedEntitySchema(name="FuncA", entity_type="function", confidence=0.9),
            ExtractedEntitySchema(name="ClassB", entity_type="class", confidence=0.8),
        ]
        result = ExtractionResult(entities=entities)

        names = result.get_entity_names()

        assert "funca" in names  # Lowercase
        assert "classb" in names
        assert len(names) == 2

    def test_result_with_notes(self):
        """Test extraction result with extraction notes."""
        result = ExtractionResult(
            entities=[],
            relationships=[],
            extraction_notes="Some content was ambiguous and skipped.",
        )

        assert result.extraction_notes is not None
        assert "ambiguous" in result.extraction_notes


class TestFunctionProperties:
    """Tests for FunctionProperties schema."""

    def test_minimal_function_properties(self):
        """Test creating function properties with defaults."""
        props = FunctionProperties()

        assert props.signature is None
        assert props.parameters == []
        assert props.return_type is None
        assert props.is_async is False
        assert props.is_generator is False
        assert props.decorators == []
        assert props.docstring is None

    def test_full_function_properties(self):
        """Test creating function properties with all fields."""
        props = FunctionProperties(
            signature="async def process(data: dict, timeout: int = 30) -> Result",
            parameters=[
                {"name": "data", "type": "dict", "description": "Input data"},
                {"name": "timeout", "type": "int", "default": "30"},
            ],
            return_type="Result",
            is_async=True,
            is_generator=False,
            decorators=["cache", "retry"],
            docstring="Process data with optional timeout.",
        )

        assert props.is_async is True
        assert len(props.parameters) == 2
        assert props.decorators == ["cache", "retry"]


class TestClassProperties:
    """Tests for ClassProperties schema."""

    def test_minimal_class_properties(self):
        """Test creating class properties with defaults."""
        props = ClassProperties()

        assert props.base_classes == []
        assert props.methods == []
        assert props.is_abstract is False
        assert props.is_dataclass is False
        assert props.is_pydantic_model is False

    def test_pydantic_model_properties(self):
        """Test class properties for a Pydantic model."""
        props = ClassProperties(
            base_classes=["BaseModel"],
            attributes=["name", "value"],
            class_methods=["from_dict"],
            is_pydantic_model=True,
            docstring="A Pydantic model for data validation.",
        )

        assert props.is_pydantic_model is True
        assert "BaseModel" in props.base_classes


class TestModuleProperties:
    """Tests for ModuleProperties schema."""

    def test_module_properties(self):
        """Test creating module properties."""
        props = ModuleProperties(
            path="kg_builder.extraction.schemas",
            package="kg_builder.extraction",
            submodules=["helpers", "validators"],
            public_api=["ExtractedEntitySchema", "ExtractionResult"],
            dependencies=["pydantic"],
        )

        assert props.path == "kg_builder.extraction.schemas"
        assert "helpers" in props.submodules
        assert "pydantic" in props.dependencies


class TestPatternProperties:
    """Tests for PatternProperties schema."""

    def test_pattern_properties(self):
        """Test creating pattern properties."""
        props = PatternProperties(
            category="behavioral",
            problem="Need to decouple request sender from receiver",
            solution="Create chain of handler objects",
            consequences=["Reduced coupling", "Dynamic handler configuration"],
            related_patterns=["Decorator", "Composite"],
        )

        assert props.category == "behavioral"
        assert len(props.consequences) == 2


class TestExampleProperties:
    """Tests for ExampleProperties schema."""

    def test_example_properties(self):
        """Test creating example properties."""
        props = ExampleProperties(
            code_snippet="result = await client.fetch(url)",
            language="python",
            demonstrates=["async/await", "HTTP client"],
            prerequisites=["aiohttp"],
            expected_output="Response object",
            is_runnable=True,
        )

        assert props.language == "python"
        assert "async/await" in props.demonstrates


class TestParameterProperties:
    """Tests for ParameterProperties schema."""

    def test_required_parameter(self):
        """Test required parameter properties."""
        props = ParameterProperties(
            type_annotation="str",
            is_required=True,
        )

        assert props.is_required is True
        assert props.default_value is None

    def test_optional_parameter_with_default(self):
        """Test optional parameter with default value."""
        props = ParameterProperties(
            type_annotation="int",
            default_value="30",
            is_required=False,
        )

        assert props.is_required is False
        assert props.default_value == "30"

    def test_variadic_parameter(self):
        """Test variadic parameter (*args, **kwargs)."""
        props = ParameterProperties(
            type_annotation="Any",
            is_variadic=True,
            is_required=False,
        )

        assert props.is_variadic is True


class TestExceptionProperties:
    """Tests for ExceptionProperties schema."""

    def test_custom_exception(self):
        """Test custom exception properties."""
        props = ExceptionProperties(
            base_exception="ValueError",
            raised_by=["validate_input", "parse_data"],
            message_template="Invalid data: {details}",
            is_custom=True,
        )

        assert props.is_custom is True
        assert "validate_input" in props.raised_by


class TestGetPropertySchemaForType:
    """Tests for get_property_schema_for_type utility function."""

    def test_function_type(self):
        """Test getting schema for function type."""
        schema = get_property_schema_for_type("function")
        assert schema is FunctionProperties

    def test_class_type(self):
        """Test getting schema for class type."""
        schema = get_property_schema_for_type("class")
        assert schema is ClassProperties

    def test_module_type(self):
        """Test getting schema for module type."""
        schema = get_property_schema_for_type("module")
        assert schema is ModuleProperties

    def test_pattern_type(self):
        """Test getting schema for pattern type."""
        schema = get_property_schema_for_type("pattern")
        assert schema is PatternProperties

    def test_example_type(self):
        """Test getting schema for example type."""
        schema = get_property_schema_for_type("example")
        assert schema is ExampleProperties

    def test_parameter_type(self):
        """Test getting schema for parameter type."""
        schema = get_property_schema_for_type("parameter")
        assert schema is ParameterProperties

    def test_exception_type(self):
        """Test getting schema for exception type."""
        schema = get_property_schema_for_type("exception")
        assert schema is ExceptionProperties

    def test_unknown_type_returns_none(self):
        """Test that unknown entity type returns None."""
        schema = get_property_schema_for_type("person")
        assert schema is None

        schema = get_property_schema_for_type("concept")
        assert schema is None


class TestSchemaIntegration:
    """Integration tests combining multiple schemas."""

    def test_complete_extraction_workflow(self):
        """Test a complete extraction result simulating real LLM output."""
        # Create entities
        class_entity = ExtractedEntitySchema(
            name="UserService",
            entity_type="class",
            description="Service for managing user operations",
            properties={
                "base_classes": ["BaseService"],
                "methods": ["create_user", "get_user", "delete_user"],
                "is_abstract": False,
            },
            confidence=0.95,
            source_text="class UserService(BaseService): ...",
        )

        function_entity = ExtractedEntitySchema(
            name="create_user",
            entity_type="function",
            description="Creates a new user in the system",
            properties={
                "signature": "async def create_user(self, data: UserCreate) -> User",
                "parameters": [{"name": "data", "type": "UserCreate"}],
                "return_type": "User",
                "is_async": True,
            },
            confidence=0.9,
        )

        # Create relationships
        contains_rel = ExtractedRelationshipSchema(
            source_name="UserService",
            target_name="create_user",
            relationship_type="contains",
            confidence=0.95,
            context="create_user is a method of UserService",
        )

        # Create result
        result = ExtractionResult(
            entities=[class_entity, function_entity],
            relationships=[contains_rel],
            extraction_notes="Extracted from UserService documentation",
        )

        # Verify
        assert result.entity_count == 2
        assert result.relationship_count == 1
        assert len(result.get_entities_by_type("class")) == 1
        assert len(result.get_entities_by_type("function")) == 1

    def test_json_serialization(self):
        """Test that schemas can be serialized to JSON."""
        entity = ExtractedEntitySchema(
            name="TestEntity",
            entity_type="function",
            description="A test entity",
            confidence=0.9,
        )

        json_str = entity.model_dump_json()
        assert "TestEntity" in json_str
        assert "function" in json_str

    def test_json_deserialization(self):
        """Test that schemas can be deserialized from JSON."""
        json_data = {
            "name": "TestEntity",
            "entity_type": "class",
            "description": "A test class",
            "properties": {"is_abstract": True},
            "confidence": 0.85,
            "source_text": None,
            "aliases": ["Test"],
        }

        entity = ExtractedEntitySchema.model_validate(json_data)

        assert entity.name == "TestEntity"
        assert entity.entity_type == "class"
        assert entity.properties["is_abstract"] is True

    def test_extraction_result_json_round_trip(self):
        """Test complete JSON round-trip for ExtractionResult."""
        original = ExtractionResult(
            entities=[
                ExtractedEntitySchema(name="A", entity_type="class", confidence=0.9),
                ExtractedEntitySchema(name="B", entity_type="function", confidence=0.8),
            ],
            relationships=[
                ExtractedRelationshipSchema(
                    source_name="A",
                    target_name="B",
                    relationship_type="contains",
                    confidence=0.85,
                ),
            ],
        )

        # Serialize
        json_str = original.model_dump_json()

        # Deserialize
        restored = ExtractionResult.model_validate_json(json_str)

        assert restored.entity_count == original.entity_count
        assert restored.relationship_count == original.relationship_count
        assert restored.entities[0].name == "A"
        assert restored.relationships[0].relationship_type == "contains"
