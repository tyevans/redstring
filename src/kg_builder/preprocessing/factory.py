"""
Factory classes for preprocessing components.

Implements the Factory pattern with decorator-based registry for dynamically
creating chunkers and entity mergers.

This follows the Open/Closed principle - new implementations can be added
without modifying the factory code.

Example:
    # Register a new chunker
    @ChunkerFactory.register(ChunkerType.MY_CHUNKER)
    class MyChunker:
        def chunk(self, text, max_chunk_size, overlap_size):
            ...

    # Create instance
    chunker = ChunkerFactory.create(ChunkerType.MY_CHUNKER, config={})
"""

import logging
from collections.abc import Callable
from enum import Enum
from typing import Any, ClassVar

from kg_builder.preprocessing.base import Chunker, EntityMerger
from kg_builder.preprocessing.exceptions import (
    ChunkerNotRegisteredError,
    EntityMergerNotRegisteredError,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Type Enums
# =============================================================================


class ChunkerType(str, Enum):
    """Supported chunker types.

    Add new types here when implementing additional chunkers.
    """

    SLIDING_WINDOW = "sliding_window"
    SENTENCE = "sentence"
    FIXED_SIZE = "fixed_size"


class EntityMergerType(str, Enum):
    """Supported entity merger types.

    Add new types here when implementing additional mergers.
    """

    SIMPLE = "simple"
    LLM = "llm"


# =============================================================================
# Chunker Factory
# =============================================================================


class ChunkerFactory:
    """Factory for creating chunker instances.

    Uses a decorator-based registry pattern for extensibility.

    Example:
        @ChunkerFactory.register(ChunkerType.SLIDING_WINDOW)
        class SlidingWindowChunker:
            ...

        chunker = ChunkerFactory.create(ChunkerType.SLIDING_WINDOW)
    """

    _registry: ClassVar[dict[ChunkerType, type[Chunker]]] = {}

    @classmethod
    def register(
        cls,
        chunker_type: ChunkerType,
    ) -> Callable[[type[Chunker]], type[Chunker]]:
        """Decorator to register a chunker implementation.

        Args:
            chunker_type: The type identifier for this chunker

        Returns:
            Decorator function that registers the class
        """

        def decorator(chunker_class: type[Chunker]) -> type[Chunker]:
            cls._registry[chunker_type] = chunker_class
            logger.debug(f"Registered chunker: {chunker_type.value}")
            return chunker_class

        return decorator

    @classmethod
    def create(
        cls,
        chunker_type: ChunkerType,
        config: dict[str, Any] | None = None,
    ) -> Chunker:
        """Create a chunker instance from configuration.

        Args:
            chunker_type: Type of chunker to create
            config: Configuration dict passed to constructor

        Returns:
            Chunker instance

        Raises:
            ChunkerNotRegisteredError: If type is not registered
        """
        if chunker_type not in cls._registry:
            available = [t.value for t in cls._registry]
            raise ChunkerNotRegisteredError(
                f"Chunker type '{chunker_type.value}' is not registered. "
                f"Available types: {available}"
            )
        return cls._registry[chunker_type](**(config or {}))

    @classmethod
    def get_default(cls) -> Chunker:
        """Get the default chunker (sliding_window).

        Returns:
            Default Chunker instance
        """
        return cls.create(ChunkerType.SLIDING_WINDOW)

    @classmethod
    def is_registered(cls, chunker_type: ChunkerType) -> bool:
        """Check if a chunker type is registered.

        Args:
            chunker_type: Type to check

        Returns:
            True if registered, False otherwise
        """
        return chunker_type in cls._registry

    @classmethod
    def list_registered(cls) -> list[ChunkerType]:
        """List all registered chunker types.

        Returns:
            List of registered ChunkerType values
        """
        return list(cls._registry.keys())


# =============================================================================
# Entity Merger Factory
# =============================================================================


class EntityMergerFactory:
    """Factory for creating entity merger instances.

    Uses a decorator-based registry pattern for extensibility.

    Example:
        @EntityMergerFactory.register(EntityMergerType.LLM)
        class LLMMerger:
            ...

        merger = EntityMergerFactory.create(EntityMergerType.LLM)
    """

    _registry: ClassVar[dict[EntityMergerType, type[EntityMerger]]] = {}

    @classmethod
    def register(
        cls,
        merger_type: EntityMergerType,
    ) -> Callable[[type[EntityMerger]], type[EntityMerger]]:
        """Decorator to register an entity merger implementation.

        Args:
            merger_type: The type identifier for this merger

        Returns:
            Decorator function that registers the class
        """

        def decorator(merger_class: type[EntityMerger]) -> type[EntityMerger]:
            cls._registry[merger_type] = merger_class
            logger.debug(f"Registered entity merger: {merger_type.value}")
            return merger_class

        return decorator

    @classmethod
    def create(
        cls,
        merger_type: EntityMergerType,
        config: dict[str, Any] | None = None,
    ) -> EntityMerger:
        """Create an entity merger instance from configuration.

        Args:
            merger_type: Type of merger to create
            config: Configuration dict passed to constructor

        Returns:
            EntityMerger instance

        Raises:
            EntityMergerNotRegisteredError: If type is not registered
        """
        if merger_type not in cls._registry:
            available = [t.value for t in cls._registry]
            raise EntityMergerNotRegisteredError(
                f"Entity merger type '{merger_type.value}' is not registered. "
                f"Available types: {available}"
            )
        return cls._registry[merger_type](**(config or {}))

    @classmethod
    def get_default(cls) -> EntityMerger:
        """Get the default entity merger (simple).

        Returns:
            Default EntityMerger instance
        """
        return cls.create(EntityMergerType.SIMPLE)

    @classmethod
    def is_registered(cls, merger_type: EntityMergerType) -> bool:
        """Check if a merger type is registered.

        Args:
            merger_type: Type to check

        Returns:
            True if registered, False otherwise
        """
        return merger_type in cls._registry

    @classmethod
    def list_registered(cls) -> list[EntityMergerType]:
        """List all registered merger types.

        Returns:
            List of registered EntityMergerType values
        """
        return list(cls._registry.keys())
