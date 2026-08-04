"""
Accuracy tests for entity and relationship extraction.

This module provides a framework for measuring extraction accuracy using
known documentation samples with expected entities and relationships.

Metrics calculated:
- Precision: ratio of correctly extracted entities to total extracted
- Recall: ratio of correctly extracted entities to total expected
- F1 Score: harmonic mean of precision and recall

Test Categories:
- Entity precision/recall tests
- Entity type accuracy tests
- Relationship extraction accuracy tests

Run with: pytest -m accuracy tests/accuracy/
"""

import logging
from dataclasses import dataclass, field

import httpx
import pytest

from kg_builder.config import settings
from kg_builder.extraction.ollama_extractor import (
    OllamaExtractionService,
    get_ollama_extraction_service,
    reset_ollama_extraction_service,
)
from kg_builder.extraction.prompts import DocumentationType

logger = logging.getLogger(__name__)


# =============================================================================
# Accuracy Dataclasses
# =============================================================================


@dataclass
class ExpectedEntity:
    """Expected entity for accuracy testing.

    Attributes:
        name: Entity name (case-insensitive matching)
        entity_type: Expected entity type (class, function, etc.)
        aliases: Alternative names that should also match
    """

    name: str
    entity_type: str
    aliases: list[str] = field(default_factory=list)

    def matches_name(self, extracted_name: str) -> bool:
        """Check if an extracted name matches this expected entity.

        Args:
            extracted_name: Name from extraction result

        Returns:
            True if name matches (case-insensitive) or matches an alias
        """
        extracted_lower = extracted_name.lower()
        if extracted_lower == self.name.lower():
            return True
        return any(alias.lower() == extracted_lower for alias in self.aliases)


@dataclass
class ExpectedRelationship:
    """Expected relationship for accuracy testing.

    Attributes:
        source: Source entity name
        target: Target entity name
        relationship_type: Expected relationship type (extends, uses, etc.)
        allow_inverse: Whether the inverse relationship also counts as correct
    """

    source: str
    target: str
    relationship_type: str
    allow_inverse: bool = False


@dataclass
class AccuracyTestCase:
    """Complete test case for accuracy testing.

    Contains sample content with known expected entities and relationships.

    Attributes:
        name: Descriptive name for the test case
        content: Documentation content to extract from
        url: Simulated page URL for context
        expected_entities: List of expected entities
        expected_relationships: List of expected relationships
        doc_type: Documentation type hint for extraction
        description: Optional description of what the test case validates
    """

    name: str
    content: str
    url: str
    expected_entities: list[ExpectedEntity]
    expected_relationships: list[ExpectedRelationship] = field(default_factory=list)
    doc_type: DocumentationType = DocumentationType.API_REFERENCE
    description: str = ""


# =============================================================================
# Accuracy Calculation Functions
# =============================================================================


def calculate_precision(
    extracted_names: set[str], expected_entities: list[ExpectedEntity]
) -> float:
    """Calculate precision: correct extractions / total extractions.

    Precision measures how many of the extracted entities are actually correct.
    High precision means few false positives.

    Args:
        extracted_names: Set of lowercase entity names from extraction
        expected_entities: List of expected entities

    Returns:
        Precision score between 0.0 and 1.0
    """
    if not extracted_names:
        return 0.0

    true_positives = 0
    for extracted_name in extracted_names:
        for expected in expected_entities:
            if expected.matches_name(extracted_name):
                true_positives += 1
                break

    return true_positives / len(extracted_names)


def calculate_recall(extracted_names: set[str], expected_entities: list[ExpectedEntity]) -> float:
    """Calculate recall: found expected / total expected.

    Recall measures how many of the expected entities were actually found.
    High recall means few false negatives.

    Args:
        extracted_names: Set of lowercase entity names from extraction
        expected_entities: List of expected entities

    Returns:
        Recall score between 0.0 and 1.0
    """
    if not expected_entities:
        return 1.0  # No expected entities means perfect recall

    found_expected = 0
    for expected in expected_entities:
        for extracted_name in extracted_names:
            if expected.matches_name(extracted_name):
                found_expected += 1
                break

    return found_expected / len(expected_entities)


def calculate_f1_score(precision: float, recall: float) -> float:
    """Calculate F1 score: harmonic mean of precision and recall.

    F1 provides a balanced measure of accuracy that considers both
    precision and recall equally.

    Args:
        precision: Precision score (0.0-1.0)
        recall: Recall score (0.0-1.0)

    Returns:
        F1 score between 0.0 and 1.0
    """
    if precision + recall == 0:
        return 0.0
    return 2 * (precision * recall) / (precision + recall)


def calculate_relationship_precision(
    extracted_relationships: list, expected_relationships: list[ExpectedRelationship]
) -> float:
    """Calculate relationship extraction precision.

    Args:
        extracted_relationships: Relationships from extraction result
        expected_relationships: Expected relationships

    Returns:
        Precision score for relationship extraction
    """
    if not extracted_relationships:
        return 0.0 if expected_relationships else 1.0

    true_positives = 0
    for extracted in extracted_relationships:
        for expected in expected_relationships:
            # Check if source and target match (case-insensitive)
            source_matches = extracted.source_name.lower() == expected.source.lower()
            target_matches = extracted.target_name.lower() == expected.target.lower()

            # Check inverse if allowed
            inverse_matches = expected.allow_inverse and (
                extracted.source_name.lower() == expected.target.lower()
                and extracted.target_name.lower() == expected.source.lower()
            )

            if (source_matches and target_matches) or inverse_matches:
                true_positives += 1
                break

    return true_positives / len(extracted_relationships)


def calculate_relationship_recall(
    extracted_relationships: list, expected_relationships: list[ExpectedRelationship]
) -> float:
    """Calculate relationship extraction recall.

    Args:
        extracted_relationships: Relationships from extraction result
        expected_relationships: Expected relationships

    Returns:
        Recall score for relationship extraction
    """
    if not expected_relationships:
        return 1.0

    found_expected = 0
    for expected in expected_relationships:
        for extracted in extracted_relationships:
            source_matches = extracted.source_name.lower() == expected.source.lower()
            target_matches = extracted.target_name.lower() == expected.target.lower()

            inverse_matches = expected.allow_inverse and (
                extracted.source_name.lower() == expected.target.lower()
                and extracted.target_name.lower() == expected.source.lower()
            )

            if (source_matches and target_matches) or inverse_matches:
                found_expected += 1
                break

    return found_expected / len(expected_relationships)


# =============================================================================
# Test Cases
# =============================================================================


EVENTSOURCE_DOC_SAMPLE = AccuracyTestCase(
    name="eventsource_domain_event",
    description="Tests extraction of Python class definitions with inheritance and decorators",
    content="""
# DomainEvent

The `DomainEvent` class is the base for all domain events.

```python
class DomainEvent(BaseModel):
    event_type: str
    aggregate_id: UUID

    def to_dict(self) -> dict:
        return self.model_dump()
```

Events are registered using `@register_event`:

```python
@register_event
class UserCreated(DomainEvent):
    event_type = "UserCreated"
    user_id: UUID
```
""",
    url="https://docs.example.com/events",
    expected_entities=[
        ExpectedEntity("DomainEvent", "class"),
        ExpectedEntity("UserCreated", "class"),
        ExpectedEntity("to_dict", "function", aliases=["to_dict()"]),
        ExpectedEntity("register_event", "function", aliases=["@register_event"]),
    ],
    expected_relationships=[
        ExpectedRelationship("UserCreated", "DomainEvent", "extends", allow_inverse=False),
    ],
    doc_type=DocumentationType.API_REFERENCE,
)


API_DOC_SAMPLE = AccuracyTestCase(
    name="api_reference_sample",
    description="Tests extraction from API reference documentation with functions and classes",
    content="""
# Event Store API

The EventStore class provides methods for persisting and retrieving events.

## EventStore

```python
class EventStore:
    '''Event persistence layer.'''

    def __init__(self, connection: Connection):
        self.connection = connection

    async def append(self, stream_id: str, events: list[Event]) -> int:
        '''Append events to a stream.

        Args:
            stream_id: Target event stream
            events: List of events to append

        Returns:
            New stream version
        '''
        pass

    async def read_stream(self, stream_id: str) -> list[Event]:
        '''Read all events from a stream.'''
        pass
```

## EventRepository

The `EventRepository` uses `EventStore` internally:

```python
class EventRepository:
    def __init__(self, store: EventStore):
        self.store = store
```
""",
    url="https://docs.example.com/api/event-store",
    expected_entities=[
        ExpectedEntity("EventStore", "class"),
        ExpectedEntity("EventRepository", "class"),
        ExpectedEntity("append", "function", aliases=["append()"]),
        ExpectedEntity("read_stream", "function", aliases=["read_stream()"]),
    ],
    expected_relationships=[
        ExpectedRelationship("EventRepository", "EventStore", "uses", allow_inverse=True),
    ],
    doc_type=DocumentationType.API_REFERENCE,
)


TUTORIAL_SAMPLE = AccuracyTestCase(
    name="tutorial_event_sourcing",
    description="Tests extraction from tutorial content with concepts and patterns",
    content="""
# Getting Started with Event Sourcing

Event sourcing is a powerful pattern for building scalable applications.

## Core Concepts

### What is Event Sourcing?

Event Sourcing stores the state of an application as a sequence of events.
Instead of storing current state, we store all changes that led to the current state.

### Events vs Commands

- **Events** represent facts that have happened (past tense: OrderCreated, UserRegistered)
- **Commands** represent intent to change state (imperative: CreateOrder, RegisterUser)

## Implementation Example

```python
class OrderAggregate:
    '''Aggregate root for Order domain.'''

    def __init__(self, order_id: UUID):
        self.order_id = order_id
        self.items = []
        self.status = "pending"

    def add_item(self, item: OrderItem) -> ItemAdded:
        event = ItemAdded(order_id=self.order_id, item=item)
        self.apply(event)
        return event

    def apply(self, event: DomainEvent) -> None:
        if isinstance(event, ItemAdded):
            self.items.append(event.item)
```

## Best Practices

1. Events should be immutable
2. Events should be named in past tense
3. Events should contain all necessary data
""",
    url="https://docs.example.com/tutorials/event-sourcing",
    expected_entities=[
        ExpectedEntity("Event Sourcing", "pattern", aliases=["EventSourcing", "event_sourcing"]),
        ExpectedEntity("OrderAggregate", "class"),
        ExpectedEntity("add_item", "function", aliases=["add_item()"]),
        ExpectedEntity("apply", "function", aliases=["apply()"]),
    ],
    expected_relationships=[],
    doc_type=DocumentationType.TUTORIAL,
)


ADVANCED_API_SAMPLE = AccuracyTestCase(
    name="advanced_api_patterns",
    description="Tests extraction of more complex class hierarchies and patterns",
    content="""
# Projection System

Projections transform event streams into read models.

## Base Projection

```python
from abc import ABC, abstractmethod

class Projection(ABC):
    '''Base class for all projections.'''

    @abstractmethod
    async def handle(self, event: DomainEvent) -> None:
        '''Handle a domain event.'''
        pass

    @abstractmethod
    async def rebuild(self) -> None:
        '''Rebuild the projection from scratch.'''
        pass
```

## Implementation

```python
class UserListProjection(Projection):
    '''Projects user events to a searchable list.'''

    def __init__(self, repository: UserReadRepository):
        self.repository = repository

    async def handle(self, event: DomainEvent) -> None:
        if isinstance(event, UserCreated):
            await self._handle_user_created(event)
        elif isinstance(event, UserUpdated):
            await self._handle_user_updated(event)

    async def _handle_user_created(self, event: UserCreated) -> None:
        await self.repository.insert(
            UserReadModel(id=event.user_id, email=event.email)
        )
```

The projection inherits from the abstract `Projection` base class and uses
`UserReadRepository` for persistence.
""",
    url="https://docs.example.com/api/projections",
    expected_entities=[
        ExpectedEntity("Projection", "class"),
        ExpectedEntity("UserListProjection", "class"),
        ExpectedEntity("handle", "function", aliases=["handle()"]),
        ExpectedEntity("rebuild", "function", aliases=["rebuild()"]),
        ExpectedEntity("UserReadRepository", "class", aliases=["repository"]),
    ],
    expected_relationships=[
        ExpectedRelationship("UserListProjection", "Projection", "extends", allow_inverse=False),
        ExpectedRelationship(
            "UserListProjection", "UserReadRepository", "uses", allow_inverse=True
        ),
    ],
    doc_type=DocumentationType.API_REFERENCE,
)


# All test cases for iteration
ALL_TEST_CASES = [
    EVENTSOURCE_DOC_SAMPLE,
    API_DOC_SAMPLE,
    TUTORIAL_SAMPLE,
    ADVANCED_API_SAMPLE,
]


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
async def ollama_available():
    """Check if Ollama is available and return True/False.

    This fixture checks connectivity to the Ollama server, that the
    configured model is listed, and that the model can actually be loaded
    and served. A model can be present in /api/tags but still fail to load
    on a resource-constrained host, which would otherwise surface as a
    spurious accuracy failure rather than a skip.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            if response.status_code != 200:
                return False

            models = response.json().get("models", [])
            model_names = [m.get("name", "") for m in models]
            if not any(
                settings.OLLAMA_MODEL in name or name in settings.OLLAMA_MODEL
                for name in model_names
            ):
                return False

        # Listed is not the same as loadable -- make one minimal completion.
        async with httpx.AsyncClient(timeout=60.0) as client:
            probe = await client.post(
                f"{settings.OLLAMA_BASE_URL}/v1/chat/completions",
                json={
                    "model": settings.OLLAMA_MODEL,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
            )
            return probe.status_code == 200
    except Exception:
        return False


@pytest.fixture(autouse=True)
def reset_service():
    """Reset the global service instance before each test."""
    reset_ollama_extraction_service()
    yield
    reset_ollama_extraction_service()


@pytest.fixture
def extraction_service() -> OllamaExtractionService:
    """Get an extraction service instance for testing."""
    return get_ollama_extraction_service()


# =============================================================================
# Entity Accuracy Tests
# =============================================================================


@pytest.mark.accuracy
class TestEntityExtractionAccuracy:
    """Tests for entity extraction precision and recall."""

    @pytest.mark.asyncio
    async def test_extraction_precision_recall_eventsource(
        self, ollama_available, extraction_service
    ):
        """Test entity extraction precision and recall on eventsource sample."""
        if not ollama_available:
            pytest.skip("Ollama not available")

        test_case = EVENTSOURCE_DOC_SAMPLE

        result = await extraction_service.extract(
            content=test_case.content,
            page_url=test_case.url,
            doc_type=test_case.doc_type,
        )

        # Calculate metrics
        extracted_names = {e.name.lower() for e in result.entities}
        precision = calculate_precision(extracted_names, test_case.expected_entities)
        recall = calculate_recall(extracted_names, test_case.expected_entities)
        f1 = calculate_f1_score(precision, recall)

        # Log metrics for baseline documentation
        logger.info(
            "Accuracy metrics for %s",
            test_case.name,
            extra={
                "test_case": test_case.name,
                "entity_precision": f"{precision:.2%}",
                "entity_recall": f"{recall:.2%}",
                "f1_score": f"{f1:.2%}",
                "extracted_count": len(extracted_names),
                "expected_count": len(test_case.expected_entities),
                "extracted_entities": list(extracted_names),
            },
        )

        print(f"\n=== Accuracy Report: {test_case.name} ===")
        print(f"Extracted entities: {sorted(extracted_names)}")
        print(f"Expected entities: {[e.name for e in test_case.expected_entities]}")
        print(f"Entity Precision: {precision:.2%}")
        print(f"Entity Recall: {recall:.2%}")
        print(f"F1 Score: {f1:.2%}")

        # Assert minimum thresholds (70% for initial baseline)
        assert precision >= 0.70, f"Precision {precision:.2%} below 70% threshold"
        assert recall >= 0.50, f"Recall {recall:.2%} below 50% threshold"

    @pytest.mark.asyncio
    async def test_extraction_precision_recall_api_reference(
        self, ollama_available, extraction_service
    ):
        """Test entity extraction precision and recall on API reference sample."""
        if not ollama_available:
            pytest.skip("Ollama not available")

        test_case = API_DOC_SAMPLE

        result = await extraction_service.extract(
            content=test_case.content,
            page_url=test_case.url,
            doc_type=test_case.doc_type,
        )

        extracted_names = {e.name.lower() for e in result.entities}
        precision = calculate_precision(extracted_names, test_case.expected_entities)
        recall = calculate_recall(extracted_names, test_case.expected_entities)
        f1 = calculate_f1_score(precision, recall)

        logger.info(
            "Accuracy metrics for %s",
            test_case.name,
            extra={
                "test_case": test_case.name,
                "entity_precision": f"{precision:.2%}",
                "entity_recall": f"{recall:.2%}",
                "f1_score": f"{f1:.2%}",
            },
        )

        print(f"\n=== Accuracy Report: {test_case.name} ===")
        print(f"Extracted entities: {sorted(extracted_names)}")
        print(f"Expected entities: {[e.name for e in test_case.expected_entities]}")
        print(f"Entity Precision: {precision:.2%}")
        print(f"Entity Recall: {recall:.2%}")
        print(f"F1 Score: {f1:.2%}")

        assert precision >= 0.70, f"Precision {precision:.2%} below 70% threshold"
        assert recall >= 0.50, f"Recall {recall:.2%} below 50% threshold"

    @pytest.mark.asyncio
    async def test_extraction_precision_recall_tutorial(self, ollama_available, extraction_service):
        """Test entity extraction precision and recall on tutorial sample."""
        if not ollama_available:
            pytest.skip("Ollama not available")

        test_case = TUTORIAL_SAMPLE

        result = await extraction_service.extract(
            content=test_case.content,
            page_url=test_case.url,
            doc_type=test_case.doc_type,
        )

        extracted_names = {e.name.lower() for e in result.entities}
        precision = calculate_precision(extracted_names, test_case.expected_entities)
        recall = calculate_recall(extracted_names, test_case.expected_entities)
        f1 = calculate_f1_score(precision, recall)

        logger.info(
            "Accuracy metrics for %s",
            test_case.name,
            extra={
                "test_case": test_case.name,
                "entity_precision": f"{precision:.2%}",
                "entity_recall": f"{recall:.2%}",
                "f1_score": f"{f1:.2%}",
            },
        )

        print(f"\n=== Accuracy Report: {test_case.name} ===")
        print(f"Extracted entities: {sorted(extracted_names)}")
        print(f"Expected entities: {[e.name for e in test_case.expected_entities]}")
        print(f"Entity Precision: {precision:.2%}")
        print(f"Entity Recall: {recall:.2%}")
        print(f"F1 Score: {f1:.2%}")

        # Tutorials may have lower precision due to conceptual extractions
        assert precision >= 0.50, f"Precision {precision:.2%} below 50% threshold"
        assert recall >= 0.50, f"Recall {recall:.2%} below 50% threshold"


# =============================================================================
# Entity Type Accuracy Tests
# =============================================================================


@pytest.mark.accuracy
class TestEntityTypeAccuracy:
    """Tests for entity type classification accuracy."""

    @pytest.mark.asyncio
    async def test_entity_type_accuracy_classes(self, ollama_available, extraction_service):
        """Test that classes are correctly identified as class type."""
        if not ollama_available:
            pytest.skip("Ollama not available")

        test_case = EVENTSOURCE_DOC_SAMPLE

        result = await extraction_service.extract(
            content=test_case.content,
            page_url=test_case.url,
            doc_type=test_case.doc_type,
        )

        # Find expected class entities
        expected_classes = [e for e in test_case.expected_entities if e.entity_type == "class"]

        # Check extracted entities for correct typing
        correct_type_count = 0
        for expected in expected_classes:
            for extracted in result.entities:
                if expected.matches_name(extracted.name):
                    if extracted.entity_type == "class":
                        correct_type_count += 1
                    else:
                        print(
                            f"Type mismatch: {extracted.name} expected 'class', "
                            f"got '{extracted.entity_type}'"
                        )
                    break

        type_accuracy = correct_type_count / len(expected_classes) if expected_classes else 1.0

        print("\n=== Entity Type Accuracy Report ===")
        print(f"Expected classes: {[e.name for e in expected_classes]}")
        print(f"Correctly typed: {correct_type_count}/{len(expected_classes)}")
        print(f"Type Accuracy: {type_accuracy:.2%}")

        logger.info(
            "Entity type accuracy",
            extra={
                "entity_type": "class",
                "type_accuracy": f"{type_accuracy:.2%}",
                "correct_count": correct_type_count,
                "expected_count": len(expected_classes),
            },
        )

        # Classes should be accurately typed
        assert type_accuracy >= 0.50, f"Class type accuracy {type_accuracy:.2%} below 50%"

    @pytest.mark.asyncio
    async def test_entity_type_accuracy_functions(self, ollama_available, extraction_service):
        """Test that functions are correctly identified as function type."""
        if not ollama_available:
            pytest.skip("Ollama not available")

        test_case = API_DOC_SAMPLE

        result = await extraction_service.extract(
            content=test_case.content,
            page_url=test_case.url,
            doc_type=test_case.doc_type,
        )

        expected_functions = [e for e in test_case.expected_entities if e.entity_type == "function"]

        correct_type_count = 0
        for expected in expected_functions:
            for extracted in result.entities:
                if expected.matches_name(extracted.name):
                    if extracted.entity_type == "function":
                        correct_type_count += 1
                    else:
                        print(
                            f"Type mismatch: {extracted.name} expected 'function', "
                            f"got '{extracted.entity_type}'"
                        )
                    break

        type_accuracy = correct_type_count / len(expected_functions) if expected_functions else 1.0

        print("\n=== Entity Type Accuracy Report ===")
        print(f"Expected functions: {[e.name for e in expected_functions]}")
        print(f"Correctly typed: {correct_type_count}/{len(expected_functions)}")
        print(f"Type Accuracy: {type_accuracy:.2%}")

        logger.info(
            "Entity type accuracy",
            extra={
                "entity_type": "function",
                "type_accuracy": f"{type_accuracy:.2%}",
                "correct_count": correct_type_count,
                "expected_count": len(expected_functions),
            },
        )

        # Functions may be harder to distinguish, use lower threshold
        assert type_accuracy >= 0.30, f"Function type accuracy {type_accuracy:.2%} below 30%"


# =============================================================================
# Relationship Accuracy Tests
# =============================================================================


@pytest.mark.accuracy
class TestRelationshipAccuracy:
    """Tests for relationship extraction accuracy."""

    @pytest.mark.asyncio
    async def test_relationship_accuracy_inheritance(self, ollama_available, extraction_service):
        """Test extraction of inheritance relationships."""
        if not ollama_available:
            pytest.skip("Ollama not available")

        test_case = EVENTSOURCE_DOC_SAMPLE

        result = await extraction_service.extract(
            content=test_case.content,
            page_url=test_case.url,
            doc_type=test_case.doc_type,
        )

        precision = calculate_relationship_precision(
            result.relationships, test_case.expected_relationships
        )
        recall = calculate_relationship_recall(
            result.relationships, test_case.expected_relationships
        )
        f1 = calculate_f1_score(precision, recall)

        print(f"\n=== Relationship Accuracy Report: {test_case.name} ===")
        print(f"Expected relationships: {len(test_case.expected_relationships)}")
        print(f"Extracted relationships: {len(result.relationships)}")
        for rel in result.relationships:
            print(f"  - {rel.source_name} --[{rel.relationship_type}]--> {rel.target_name}")
        print(f"Relationship Precision: {precision:.2%}")
        print(f"Relationship Recall: {recall:.2%}")
        print(f"F1 Score: {f1:.2%}")

        logger.info(
            "Relationship accuracy",
            extra={
                "test_case": test_case.name,
                "relationship_precision": f"{precision:.2%}",
                "relationship_recall": f"{recall:.2%}",
                "f1_score": f"{f1:.2%}",
                "extracted_count": len(result.relationships),
                "expected_count": len(test_case.expected_relationships),
            },
        )

        # Relationship extraction is harder, use lower threshold
        # Note: This may fail initially - that's expected for baseline
        if test_case.expected_relationships:
            assert recall >= 0.30, f"Relationship recall {recall:.2%} below 30%"

    @pytest.mark.asyncio
    async def test_relationship_accuracy_uses(self, ollama_available, extraction_service):
        """Test extraction of 'uses' relationships."""
        if not ollama_available:
            pytest.skip("Ollama not available")

        test_case = ADVANCED_API_SAMPLE

        result = await extraction_service.extract(
            content=test_case.content,
            page_url=test_case.url,
            doc_type=test_case.doc_type,
        )

        precision = calculate_relationship_precision(
            result.relationships, test_case.expected_relationships
        )
        recall = calculate_relationship_recall(
            result.relationships, test_case.expected_relationships
        )

        print(f"\n=== Relationship Accuracy Report: {test_case.name} ===")
        print("Expected relationships:")
        for rel in test_case.expected_relationships:
            print(f"  - {rel.source} --[{rel.relationship_type}]--> {rel.target}")
        print("Extracted relationships:")
        for rel in result.relationships:
            print(f"  - {rel.source_name} --[{rel.relationship_type}]--> {rel.target_name}")
        print(f"Relationship Precision: {precision:.2%}")
        print(f"Relationship Recall: {recall:.2%}")

        logger.info(
            "Relationship accuracy for uses relationships",
            extra={
                "test_case": test_case.name,
                "relationship_precision": f"{precision:.2%}",
                "relationship_recall": f"{recall:.2%}",
            },
        )


# =============================================================================
# Aggregate Accuracy Tests
# =============================================================================


@pytest.mark.accuracy
class TestAggregateAccuracy:
    """Tests for aggregate accuracy across all test cases."""

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_aggregate_entity_accuracy(self, ollama_available, extraction_service):
        """Test aggregate entity extraction accuracy across all samples.

        This test runs all test cases and reports aggregate metrics.
        Useful for establishing baseline accuracy across diverse documentation.
        """
        if not ollama_available:
            pytest.skip("Ollama not available")

        total_precision = 0.0
        total_recall = 0.0
        total_f1 = 0.0
        results = []

        for test_case in ALL_TEST_CASES:
            result = await extraction_service.extract(
                content=test_case.content,
                page_url=test_case.url,
                doc_type=test_case.doc_type,
            )

            extracted_names = {e.name.lower() for e in result.entities}
            precision = calculate_precision(extracted_names, test_case.expected_entities)
            recall = calculate_recall(extracted_names, test_case.expected_entities)
            f1 = calculate_f1_score(precision, recall)

            total_precision += precision
            total_recall += recall
            total_f1 += f1

            results.append(
                {
                    "name": test_case.name,
                    "precision": precision,
                    "recall": recall,
                    "f1": f1,
                    "extracted": len(extracted_names),
                    "expected": len(test_case.expected_entities),
                }
            )

        avg_precision = total_precision / len(ALL_TEST_CASES)
        avg_recall = total_recall / len(ALL_TEST_CASES)
        avg_f1 = total_f1 / len(ALL_TEST_CASES)

        print("\n" + "=" * 60)
        print("AGGREGATE ACCURACY REPORT")
        print("=" * 60)
        for r in results:
            print(
                f"{r['name']:30s} | P: {r['precision']:.2%} | R: {r['recall']:.2%} | "
                f"F1: {r['f1']:.2%} | {r['extracted']}/{r['expected']}"
            )
        print("-" * 60)
        print(f"{'AVERAGE':30s} | P: {avg_precision:.2%} | R: {avg_recall:.2%} | F1: {avg_f1:.2%}")
        print("=" * 60)

        logger.info(
            "Aggregate accuracy report",
            extra={
                "average_precision": f"{avg_precision:.2%}",
                "average_recall": f"{avg_recall:.2%}",
                "average_f1": f"{avg_f1:.2%}",
                "test_cases_count": len(ALL_TEST_CASES),
                "individual_results": results,
            },
        )

        # Assert aggregate thresholds
        assert avg_precision >= 0.50, f"Average precision {avg_precision:.2%} below 50%"
        assert avg_recall >= 0.40, f"Average recall {avg_recall:.2%} below 40%"


# =============================================================================
# Unit Tests for Calculation Functions
# =============================================================================


class TestAccuracyCalculations:
    """Unit tests for accuracy calculation functions."""

    def test_calculate_precision_all_correct(self):
        """Test precision calculation when all extractions are correct."""
        extracted = {"domainevent", "usercreated"}
        expected = [
            ExpectedEntity("DomainEvent", "class"),
            ExpectedEntity("UserCreated", "class"),
        ]

        precision = calculate_precision(extracted, expected)
        assert precision == 1.0

    def test_calculate_precision_half_correct(self):
        """Test precision calculation when half of extractions are correct."""
        extracted = {"domainevent", "usercreated", "wrong1", "wrong2"}
        expected = [
            ExpectedEntity("DomainEvent", "class"),
            ExpectedEntity("UserCreated", "class"),
        ]

        precision = calculate_precision(extracted, expected)
        assert precision == 0.5

    def test_calculate_precision_empty_extracted(self):
        """Test precision calculation with no extractions."""
        extracted: set[str] = set()
        expected = [ExpectedEntity("DomainEvent", "class")]

        precision = calculate_precision(extracted, expected)
        assert precision == 0.0

    def test_calculate_precision_with_aliases(self):
        """Test precision calculation considers aliases."""
        extracted = {"to_dict()"}  # Alias form
        expected = [ExpectedEntity("to_dict", "function", aliases=["to_dict()"])]

        precision = calculate_precision(extracted, expected)
        assert precision == 1.0

    def test_calculate_recall_all_found(self):
        """Test recall calculation when all expected are found."""
        extracted = {"domainevent", "usercreated", "extra"}
        expected = [
            ExpectedEntity("DomainEvent", "class"),
            ExpectedEntity("UserCreated", "class"),
        ]

        recall = calculate_recall(extracted, expected)
        assert recall == 1.0

    def test_calculate_recall_half_found(self):
        """Test recall calculation when half of expected are found."""
        extracted = {"domainevent"}
        expected = [
            ExpectedEntity("DomainEvent", "class"),
            ExpectedEntity("UserCreated", "class"),
        ]

        recall = calculate_recall(extracted, expected)
        assert recall == 0.5

    def test_calculate_recall_none_found(self):
        """Test recall calculation when none are found."""
        extracted = {"wrong1", "wrong2"}
        expected = [
            ExpectedEntity("DomainEvent", "class"),
            ExpectedEntity("UserCreated", "class"),
        ]

        recall = calculate_recall(extracted, expected)
        assert recall == 0.0

    def test_calculate_recall_empty_expected(self):
        """Test recall calculation with no expected entities."""
        extracted = {"something"}
        expected: list[ExpectedEntity] = []

        recall = calculate_recall(extracted, expected)
        assert recall == 1.0  # Perfect recall when nothing expected

    def test_calculate_f1_balanced(self):
        """Test F1 calculation with balanced precision and recall."""
        f1 = calculate_f1_score(0.8, 0.8)
        assert abs(f1 - 0.8) < 0.001

    def test_calculate_f1_perfect(self):
        """Test F1 calculation with perfect scores."""
        f1 = calculate_f1_score(1.0, 1.0)
        assert f1 == 1.0

    def test_calculate_f1_zero(self):
        """Test F1 calculation with zero scores."""
        f1 = calculate_f1_score(0.0, 0.0)
        assert f1 == 0.0

    def test_calculate_f1_imbalanced(self):
        """Test F1 calculation with imbalanced scores."""
        # Precision=1.0, Recall=0.5 -> F1=0.6667
        f1 = calculate_f1_score(1.0, 0.5)
        assert abs(f1 - 0.6667) < 0.001

    def test_expected_entity_matches_name_exact(self):
        """Test exact name matching."""
        entity = ExpectedEntity("DomainEvent", "class")
        assert entity.matches_name("DomainEvent")
        assert entity.matches_name("domainevent")
        assert entity.matches_name("DOMAINEVENT")

    def test_expected_entity_matches_name_alias(self):
        """Test alias matching."""
        entity = ExpectedEntity("to_dict", "function", aliases=["to_dict()", "toDict"])
        assert entity.matches_name("to_dict")
        assert entity.matches_name("to_dict()")
        assert entity.matches_name("toDict")
        assert not entity.matches_name("to_dictionary")
