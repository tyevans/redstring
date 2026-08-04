"""Turning a document into entities and relationships, on the domain model.

The pipeline is the whole story:

```
SourceDocument -> Chunker -> LlmProvider -> map_extraction -> merge_extractions
               -> Document.record_extraction -> DocumentExtracted
```

Extraction **emits events**. It does not write to a `GraphStore` or a
`VectorStore` -- `kg_builder.projections` does that from the log. If a change
here starts wanting a store reference, that is the signal that the thing this
re-architecture removed is growing back.

## What slice 6 removed from this package

The vendor-specific extractors (`ollama_extractor`, `openai_extractor`,
`llm_extractor`), the provider registry (`factory`, `registry`) and the
`BaseExtractionService` hierarchy are gone. One narrow port,
`kg_builder.ports.llm_provider.LlmProvider`, replaced all of it: a registry
mapping a settings string onto a vendor class earns its keep only when the
vendor set is open, and behind an OpenAI-compatible adapter it is not.

Retry, rate limiting and circuit breaking moved to `kg_builder.llm`. They are
properties of calling a model over a network, not of turning prose into
entities -- any other transport would need the same three.

Chunkers and mergers arrived here from `preprocessing/`, which is gone. What
became of the mergers is `kg_builder.consolidation.policy`, which rebuilt
their two-threshold policy where a judgement becomes an `EntitiesMerged`.
"""

from kg_builder.extraction.chunkers import SlidingWindowChunker
from kg_builder.extraction.chunking import Chunk, ChunkingResult
from kg_builder.extraction.errors import ChunkerError, ChunkingError, ChunkSizeError
from kg_builder.extraction.mapping import MappedExtraction, entity_id_for, map_extraction
from kg_builder.extraction.merging import merge_extractions
from kg_builder.extraction.pipeline import (
    DEFAULT_SYSTEM_PROMPT,
    ExtractionPipeline,
    PartialExtractionError,
    PipelineResult,
)
from kg_builder.extraction.prompt_generator import domain_system_prompt
from kg_builder.extraction.protocols import Chunker
from kg_builder.extraction.schema import (
    DEFAULT_CONFIDENCE,
    ExtractedEntity,
    ExtractedRelationship,
    Extraction,
)
from kg_builder.extraction.schema_org import (
    extract_entities_from_open_graph,
    extract_entities_from_schema_org,
)

__all__ = [
    "DEFAULT_CONFIDENCE",
    "DEFAULT_SYSTEM_PROMPT",
    # Chunking
    "Chunk",
    "ChunkSizeError",
    "Chunker",
    "ChunkerError",
    "ChunkingError",
    "ChunkingResult",
    # What a model is asked for, and what comes back
    "ExtractedEntity",
    "ExtractedRelationship",
    "Extraction",
    # The pipeline
    "ExtractionPipeline",
    "MappedExtraction",
    "PartialExtractionError",
    "PipelineResult",
    "SlidingWindowChunker",
    # Domain-aware prompting: `domains/` supplies the schema, this turns it
    # into the `system_prompt` the pipeline already took. BACKLOG B55.
    "domain_system_prompt",
    "entity_id_for",
    # Non-LLM extraction
    "extract_entities_from_open_graph",
    "extract_entities_from_schema_org",
    "map_extraction",
    "merge_extractions",
]
