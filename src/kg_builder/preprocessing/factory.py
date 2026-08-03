"""
Factory classes for preprocessing components.

Implements the Factory pattern with decorator-based registry for dynamically
creating preprocessors, chunkers, and entity mergers.

This follows the Open/Closed principle - new implementations can be added
without modifying the factory code.

Example:
    # Register a new preprocessor
    @PreprocessorFactory.register(PreprocessorType.MY_PREPROCESSOR)
    class MyPreprocessor:
        def preprocess(self, content, content_type, url):
            ...

    # Create instance
    preprocessor = PreprocessorFactory.create(PreprocessorType.MY_PREPROCESSOR, config={})
"""

import logging
from collections.abc import Callable
from enum import Enum
from typing import Any

from kg_builder.preprocessing.base import Chunker, EntityMerger, Preprocessor
from kg_builder.preprocessing.exceptions import (
    ChunkerNotRegisteredError,
    EntityMergerNotRegisteredError,
    PreprocessorNotRegisteredError,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Type Enums
# =============================================================================


class PreprocessorType(str, Enum):
    """Supported preprocessor types.

    Add new types here when implementing additional preprocessors.
    """

    TRAFILATURA = "trafilatura"
    PASSTHROUGH = "passthrough"


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
# Preprocessor Factory
# =============================================================================


class PreprocessorFactory:
    """Factory for creating preprocessor instances.

    Uses a decorator-based registry pattern for extensibility.

    Example:
        @PreprocessorFactory.register(PreprocessorType.TRAFILATURA)
        class TrafilaturaPreprocessor:
            ...

        preprocessor = PreprocessorFactory.create(PreprocessorType.TRAFILATURA)
    """

    _registry: dict[PreprocessorType, type[Preprocessor]] = {}

    @classmethod
    def register(
        cls,
        preprocessor_type: PreprocessorType,
    ) -> Callable[[type[Preprocessor]], type[Preprocessor]]:
        """Decorator to register a preprocessor implementation.

        Args:
            preprocessor_type: The type identifier for this preprocessor

        Returns:
            Decorator function that registers the class

        Example:
            @PreprocessorFactory.register(PreprocessorType.MY_TYPE)
            class MyPreprocessor:
                ...
        """

        def decorator(preprocessor_class: type[Preprocessor]) -> type[Preprocessor]:
            cls._registry[preprocessor_type] = preprocessor_class
            logger.debug(f"Registered preprocessor: {preprocessor_type.value}")
            return preprocessor_class

        return decorator

    @classmethod
    def create(
        cls,
        preprocessor_type: PreprocessorType,
        config: dict[str, Any] | None = None,
    ) -> Preprocessor:
        """Create a preprocessor instance from configuration.

        Args:
            preprocessor_type: Type of preprocessor to create
            config: Configuration dict passed to constructor

        Returns:
            Preprocessor instance

        Raises:
            PreprocessorNotRegisteredError: If type is not registered
        """
        if preprocessor_type not in cls._registry:
            available = [t.value for t in cls._registry.keys()]
            raise PreprocessorNotRegisteredError(
                f"Preprocessor type '{preprocessor_type.value}' is not registered. "
                f"Available types: {available}"
            )
        return cls._registry[preprocessor_type](**(config or {}))

    @classmethod
    def get_default(cls) -> Preprocessor:
        """Get the default preprocessor (trafilatura).

        Returns:
            Default Preprocessor instance
        """
        return cls.create(PreprocessorType.TRAFILATURA)

    @classmethod
    def is_registered(cls, preprocessor_type: PreprocessorType) -> bool:
        """Check if a preprocessor type is registered.

        Args:
            preprocessor_type: Type to check

        Returns:
            True if registered, False otherwise
        """
        return preprocessor_type in cls._registry

    @classmethod
    def list_registered(cls) -> list[PreprocessorType]:
        """List all registered preprocessor types.

        Returns:
            List of registered PreprocessorType values
        """
        return list(cls._registry.keys())


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

    _registry: dict[ChunkerType, type[Chunker]] = {}

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
            available = [t.value for t in cls._registry.keys()]
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

    _registry: dict[EntityMergerType, type[EntityMerger]] = {}

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
            available = [t.value for t in cls._registry.keys()]
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
