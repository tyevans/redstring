"""Where a claim about an entity came from, and when.

`Entity` describes a thing in the world; `Provenance` describes the *claiming*.
`name`, `entity_type`, `description`, `properties` and `temporal` are what was
said about the thing. These six fields are who said it, when, how, from where,
and how sure -- and separating them is what lets a merge ask "which of these
competing values was observed most recently" without asking an `Entity` a
question about itself.

## `observed_at` is required, and that is the whole point

The strategy now called `MOST_RECENTLY_OBSERVED` was previously `LATEST` and
raised, because nothing in the library recorded when anything was observed --
not per property, and not per entity either. An optional `observed_at` would
have rebuilt that hole one level down: the strategy would work for some callers
and refuse for others, and no caller could tell which it was going to be until
it ran. Required means the question is answerable by construction.

The cost is that every construction site supplies one and no `DocumentExtracted`
written before this change validates. Both were accepted deliberately; there was
no persisted log to migrate.

## It is not `TemporalExtent`, and the two must not be confused

`TemporalExtent` is *world* time -- when the fact held. `observed_at` is
*record* time -- when this library was told. A document published in 1923 and
extracted today has both, and they answer different questions. Nothing here
infers one from the other. See
`docs/adr/0005-temporal-inference-on-read.md`.

## Why `Relationship` does not get one

Symmetry is tempting and would be wrong. `Relationship` carries `confidence`
and `source_id` but no `extraction_method` and no `model`, so its provenance is
a different shape; sharing this type would mean three fields that are always
absent. The asymmetry is real and the relationship side is tracked on its own
terms in `BACKLOG.md`.

## `ExtractionMethod` lives here rather than in `domain/entity.py`

It is a property of the observation, not of the thing observed, so this is
where it belongs on the merits -- and it is also the import direction that does
not cycle. `Provenance` needs the enum to validate `model` against it, and
`Entity` needs `Provenance`; had the enum stayed on `entity.py` the two modules
would import each other.
"""

from __future__ import annotations

# Imported at runtime, not under `TYPE_CHECKING`: pydantic resolves field
# annotations at schema-build time, and a type-checking-only import leaves
# `Provenance` "not fully defined" at every construction site.
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from redstring.domain.ids import SourceId
from redstring.domain.json_safety import Passthrough, reject_unstorable_text


class ExtractionMethod(StrEnum):
    """How the entity was derived — not which vendor answered.

    Vendor identity is adapter detail and belongs in `Provenance.model`, which
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


#: The methods that can have invoked a model, and so may carry
#: `Provenance.model`.
MODEL_BEARING_METHODS = frozenset({ExtractionMethod.LLM, ExtractionMethod.HYBRID})


class Provenance(BaseModel):
    """What observed a claim, when, how, from where, and how sure."""

    observed_at: datetime
    extraction_method: ExtractionMethod
    confidence: float
    source_id: SourceId | None = None
    source_text: str | None = None
    model: str | None = Field(
        default=None,
        description=(
            "Which model produced this claim, for provenance. Convention: "
            "provider-qualified and versioned, e.g. 'ollama/qwen3.6-27b-mtp' "
            "or 'anthropic/claude-opus-4-20250514' -- never a bare family name "
            "like 'claude'. These values land in a durable event log, where an "
            "unversioned name makes 're-extract everything the old model "
            "touched' unanswerable. None when no model was involved, or when "
            "the extractor did not record one."
        ),
    )

    @field_validator("source_id", "source_text", "model")
    @classmethod
    def _reject_unstorable_in_free_form_text(cls, value: Passthrough) -> Passthrough:
        """No field reaching the event log may carry text that cannot be
        stored. See `domain/json_safety.py` for why this raises rather than
        strips, and `domain/entity.py` for why it is listed per field.
        """
        reject_unstorable_text(value, what="provenance field")
        return value

    @field_validator("observed_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        """Naive and aware datetimes raise `TypeError` when compared, and the
        comparison that matters happens inside a merge, several layers from
        anything that could say which entity was at fault. Refuse it at
        construction, where the offending value is in hand -- same reasoning as
        `Alias.merged_at`.
        """
        if value.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        return value

    @field_validator("confidence")
    @classmethod
    def _require_confidence_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return value

    @model_validator(mode="after")
    def _reject_model_without_a_model_call(self) -> Provenance:
        """`model` records which model ran, so a method that runs none cannot
        carry one.

        `HYBRID` is permitted alongside `LLM`: a hybrid extraction is
        pattern-matching *plus* a model, and it is precisely the case where
        knowing which model contributed matters. The rule constrains only the
        methods that cannot involve one at all.
        """
        if self.model is not None and self.extraction_method not in MODEL_BEARING_METHODS:
            raise ValueError(
                f"model must be None for extraction_method "
                f"{self.extraction_method.value!r}, which invokes no model"
            )
        return self
