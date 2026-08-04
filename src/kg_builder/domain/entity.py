"""The `Entity` domain type and how it was extracted."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, field_validator

from kg_builder.domain.ids import EntityId, SourceId, TenantId
from kg_builder.domain.temporal import TemporalExtent


class ExtractionMethod(str, Enum):
    """How the entity was extracted."""

    SCHEMA_ORG = "schema_org"
    OPEN_GRAPH = "open_graph"
    LLM_CLAUDE = "llm_claude"
    LLM_OLLAMA = "llm_ollama"
    LLM_OPENAI = "llm_openai"
    PATTERN = "pattern"
    SPACY = "spacy"
    HYBRID = "hybrid"


class Entity(BaseModel):
    """A thing extracted from a source: a person, place, concept, etc.

    Alias-ness is deliberately not a field here — it is an edge, carried by
    `Alias`. There is likewise no `synced_at`: the graph store is the store,
    not a cache of some other source of truth.
    """

    id: EntityId
    tenant_id: TenantId
    name: str
    normalized_name: str
    entity_type: str
    original_entity_type: str | None = None
    description: str | None = None
    source_id: SourceId | None = None
    source_text: str | None = None
    external_ids: dict[str, str] = {}
    properties: dict[str, Any] = {}
    extraction_method: ExtractionMethod
    confidence: float
    temporal: TemporalExtent | None = None

    @field_validator("name")
    @classmethod
    def _require_non_blank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value

    @field_validator("confidence")
    @classmethod
    def _require_confidence_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return value

    @property
    def is_temporal(self) -> bool:
        return self.temporal is not None and not self.temporal.is_empty
