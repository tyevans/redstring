"""
Entity merger implementations for cross-chunk entity resolution.

This module provides concrete implementations of the EntityMerger protocol:
- SimpleMerger: Basic deduplication by exact/fuzzy name matching
- LLMMerger: LLM-assisted resolution for ambiguous cases

Mergers are registered via decorators and created via EntityMergerFactory.
"""

# Import implementations to trigger registration
from kg_builder.preprocessing.mergers.llm_merger import LLMMerger
from kg_builder.preprocessing.mergers.simple_merger import SimpleMerger

__all__ = [
    "SimpleMerger",
    "LLMMerger",
]
