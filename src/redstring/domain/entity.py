"""The `Entity` domain type and how it was extracted."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, field_validator

from redstring.domain.ids import EntityId, TenantId
from redstring.domain.json_safety import Passthrough, reject_unstorable_text
from redstring.domain.provenance import Provenance
from redstring.domain.temporal import TemporalExtent


class Entity(BaseModel):
    """A thing extracted from a source: a person, place, concept, etc.

    Alias-ness is deliberately not a field here — it is an edge, carried by
    `Alias`. There is likewise no `synced_at`: the graph store is the store,
    not a cache of some other source of truth.

    `source_id`, `source_text`, `extraction_method`, `model` and `confidence`
    used to sit inline here and now live on `provenance`. The split is between
    *the thing* and *the claiming of it*: `name`, `entity_type`, `description`,
    `properties` and `temporal` are what was said, and the five that moved are
    who said it, when, how, from where and how sure. Grouping them is what
    lets a merge ask which of two competing values was observed most recently
    without asking an `Entity` a question about itself -- and it is what gave
    `observed_at` somewhere to live. See `domain/provenance.py`.
    """

    id: EntityId
    tenant_id: TenantId
    name: str
    normalized_name: str
    entity_type: str
    original_entity_type: str | None = None
    description: str | None = None
    external_ids: dict[str, str] = {}
    properties: dict[str, Any] = {}
    provenance: Provenance
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
        fields (`id`, `tenant_id`, `temporal`) cannot hold one, and walking
        them would make this a general schema check that happens to be about
        text. If a free-form field is added to this model it belongs in this
        list -- an omission is silent until a real event store refuses the
        write. `provenance` is absent for a different reason: `Provenance`
        runs the same check over its own free-form fields, so listing it here
        would be a second declaration site for one rule.
        """
        reject_unstorable_text(value, what="entity field")
        return value

    @field_validator("name")
    @classmethod
    def _require_non_blank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value

    @property
    def is_temporal(self) -> bool:
        return self.temporal is not None and not self.temporal.is_empty
