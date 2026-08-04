"""
Modular text chunking and entity merging for entity extraction.

This module provides:
- Chunkers: Split content into smaller pieces for processing
- Entity Mergers: Combine entities from multiple chunks

Preprocessing (HTML boilerplate removal) is sourcing, which this library does
not do -- callers supply already-clean content.

Example:
    from kg_builder.preprocessing import PreprocessingPipeline, PipelineConfig

    config = PipelineConfig(chunk_size=3000, chunk_overlap=200)
    pipeline = PreprocessingPipeline(config)
    result = await pipeline.process(content=clean_text, extractor=extractor, url=url)
"""

# Import implementations to trigger factory registration
# These must be imported AFTER the factories are defined
from kg_builder.preprocessing import (
    chunkers,  # noqa: F401
    mergers,  # noqa: F401
)
from kg_builder.preprocessing.base import Chunker, EntityMerger, Preprocessor
from kg_builder.preprocessing.exceptions import (
    ChunkerError,
    ChunkerNotRegisteredError,
    EntityMergerError,
    EntityMergerNotRegisteredError,
    PreprocessingError,
    PreprocessorError,
)
from kg_builder.preprocessing.factory import (
    ChunkerFactory,
    ChunkerType,
    EntityMergerFactory,
    EntityMergerType,
)

# Import pipeline after all factories are populated
from kg_builder.preprocessing.pipeline import PipelineConfig, PipelineResult, PreprocessingPipeline
from kg_builder.preprocessing.schemas import (
    Chunk,
    ChunkingResult,
    EntityMergeCandidate,
    EntityMergeDecision,
    PreprocessingResult,
)

__all__ = [
    "Chunk",
    "Chunker",
    "ChunkerError",
    # Factories
    "ChunkerFactory",
    "ChunkerNotRegisteredError",
    # Types
    "ChunkerType",
    "ChunkingResult",
    "EntityMergeCandidate",
    "EntityMergeDecision",
    "EntityMerger",
    "EntityMergerError",
    "EntityMergerFactory",
    "EntityMergerNotRegisteredError",
    "EntityMergerType",
    "PipelineConfig",
    "PipelineResult",
    # Exceptions
    "PreprocessingError",
    # Pipeline
    "PreprocessingPipeline",
    # Schemas
    "PreprocessingResult",
    # Protocols
    "Preprocessor",
    "PreprocessorError",
]
