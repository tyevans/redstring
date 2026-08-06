"""The `Entity` domain type and how it was extracted."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from redstring.domain.ids import EntityId, SourceId, TenantId
from redstring.domain.json_safety import Passthrough, reject_unstorable_text
from redstring.domain.temporal import TemporalExtent


class ExtractionMethod(StrEnum):
    """How the entity was derived — not which vendor answered.

    Vendor identity is adapter detail and belongs in `Entity.model`, which
    survives model upgrades and makes "re-extract everything the old model
    touched" a query. These values become persisted event payloads, so a
    vendor name here would outlive that vendor's presence in the codebase.
    """

    LLM = "llm"
    PATTERN = "pattern"
    SCHEMA_ORG = "schema_org"
    OPEN_GRAPH = "open_graph"
    HYBRID = "hybrid"
    MANUAL = "manual"


#: The methods that can have invoked a model, and so may carry `Entity.model`.
_MODEL_BEARING_METHODS = frozenset({ExtractionMethod.LLM, ExtractionMethod.HYBRID})


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
    model: str | None = Field(
        default=None,
        description=(
            "Which model produced this entity, for provenance. Convention: "
            "provider-qualified and versioned, e.g. 'ollama/qwen3.6-27b-mtp' "
            "or 'anthropic/claude-opus-4-20250514' -- never a bare family name "
            "like 'claude'. These values land in a durable event log, where an "
            "unversioned name makes 're-extract everything the old model "
            "touched' unanswerable. None when no model was involved, or when "
            "the extractor did not record one."
        ),
    )
    confidence: float
    temporal: TemporalExtent | None = None
    # Consolidation blocks candidates by a pure key function (prefix, entity
    # type, soundex). The entity carries the keys; the store only groups by
    # them and computes nothing.
    blocking_keys: frozenset[str] | None = None

    @field_validator(
        "name",
        "normalized_name",
        "entity_type",
        "original_entity_type",
        "description",
        "source_id",
        "source_text",
        "model",
        "external_ids",
        "properties",
        "blocking_keys",
    )
    @classmethod
    def _reject_unstorable_in_free_form_text(cls, value: Passthrough) -> Passthrough:
        """No field reaching the event log may carry text that cannot be
        stored -- a NUL, or an unpaired surrogate. See
        `domain/json_safety.py` for why, and why it raises rather than strips.

        Listed per field rather than checked over the whole model: the typed
        fields (`id`, `tenant_id`, `confidence`, `temporal`) cannot hold one,
        and walking them would make this a general schema check that happens
        to be about text. If a free-form field is added to this model it
        belongs in this list -- an omission is silent until a real event store
        refuses the write.
        """
        reject_unstorable_text(value, what="entity field")
        return value

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

    @model_validator(mode="after")
    def _reject_model_without_a_model_call(self) -> Entity:
        """`model` records which model ran, so a method that runs none cannot
        carry one.

        `HYBRID` is permitted alongside `LLM`: a hybrid extraction is
        pattern-matching *plus* a model, and it is precisely the case where
        knowing which model contributed matters. The rule constrains only the
        methods that cannot involve one at all.
        """
        if self.model is not None and self.extraction_method not in _MODEL_BEARING_METHODS:
            raise ValueError(
                f"model must be None for extraction_method "
                f"{self.extraction_method.value!r}, which invokes no model"
            )
        return self

    @property
    def is_temporal(self) -> bool:
        return self.temporal is not None and not self.temporal.is_empty
