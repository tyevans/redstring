"""One stored passage of a document, and how it is identified.

## This is not `extraction.chunking.Chunk`

That type is a dataclass in the extraction layer describing a split in
progress: transient, tenantless, consumed within a single pipeline run. This
one is a stored record. They share four field names and no lifetime, and a
shared base class would put the extraction layer's type into the domain while
giving the transient one a tenant it has no way to fill.

## Identity is content-addressed

`chunk_id(source_id, text)` hashes the source id and the text exactly as
stored. Re-chunking a document under different settings therefore produces
genuinely new ids rather than overwriting old ones in place.

Positional identity -- `(source_id, chunk_index)` -- was rejected for that
reason. Under it, chunk 3 of a re-chunked document is a *different passage*
wearing the same id, so its stored entity links and its stored vector would
silently describe text that no longer says what they claim. The cost of
content addressing is orphans, and `ChunkStore.replace_source` is where that
cost is paid.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from redstring.domain.ids import EntityId, SourceId, TenantId
from redstring.domain.json_safety import reject_unstorable_text

#: A chunk's identity: the hex digest produced by `chunk_id`.
ChunkId = str

#: Separates the source id from the text in the hashed preimage. A NUL cannot
#: occur in either -- `reject_unstorable_text` refuses it in the text, and a
#: `SourceId` carrying one could not be stored -- so no pair of inputs can
#: produce the same preimage as a different pair. Without a delimiter,
#: ("ab", "c") and ("a", "bc") would be one chunk.
_DELIMITER = b"\x00"


def chunk_id(source_id: SourceId, text: str) -> ChunkId:
    """The identity of `text` as a passage of `source_id`.

    The text is hashed **exactly as stored**, with no normalisation. Two
    passages differing only in whitespace have different `start_char`/
    `end_char`, so collapsing them would give one id two offsets -- and
    normalising here would create a second scheme to keep in step with the one
    in `extraction/mapping.py`.
    """
    digest = hashlib.sha256()
    digest.update(source_id.encode("utf-8"))
    digest.update(_DELIMITER)
    digest.update(text.encode("utf-8"))
    return digest.hexdigest()


class StoredChunk(BaseModel):
    """One passage of one document, under one tenant.

    `entity_ids` and `metadata` are mutable on purpose: a store handing back
    its own object would let a caller corrupt stored state, and the port
    requires that it does not. Immutable containers would make the compliance
    suite's mutation-isolation tests unfalsifiable -- they would pass on an
    adapter that leaks, because there would be nothing to mutate.

    **An empty `entity_ids` means no entities were extracted from this
    passage. It does not mean extraction is pending.** It is legitimately
    empty for every chunk written by `index_documents`, which never calls an
    LLM, so code reading emptiness as "not yet processed" is wrong forever and
    looks reasonable in review.
    """

    model_config = ConfigDict(extra="forbid")

    tenant_id: TenantId
    source_id: SourceId
    text: str
    chunk_index: int
    start_char: int
    end_char: int
    entity_ids: list[EntityId] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    #: This chunk's semantic-channel vector, or `None` if it has not been
    #: embedded. Safe to store on this row precisely because identity is
    #: content-addressed: re-chunking a document under different settings
    #: produces a new `id` rather than overwriting this one in place, so a
    #: stale embedding can never silently describe text that changed under
    #: it. Under positional identity this field would need invalidation
    #: logic; under content addressing it needs none.
    embedding: list[float] | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> ChunkId:
        """This passage's identity, derived rather than supplied.

        A field here would let a caller name an id unrelated to the text,
        and both adapters skip re-deriving `doc_length`, the term index and
        `embedding` on an id conflict precisely because they assume no
        caller can (BACKLOG B97). A computed field makes that assumption
        true instead of merely documented.

        It is `computed_field` rather than a plain `property` because
        `DocumentChunked` carries these to the event log: a plain property
        would drop `id` from `model_dump()` and change the log's shape.
        """
        return chunk_id(self.source_id, self.text)

    @model_validator(mode="before")
    @classmethod
    def _accept_a_matching_id_pop_it_reject_a_mismatch(cls, data: Any) -> Any:  # noqa: ANN401
        """Let a round-tripped `id` back in, but only if it still agrees.

        `DocumentChunked` carries `list[StoredChunk]` to the event log, and
        the log is the authority: a payload that cannot be read back is a
        projection that can never replay. `model_dump()` includes the
        computed `id`, so `extra="forbid"` alone would make every stored
        event un-deserialisable -- the id this validator pops here is the
        same one `id` will recompute a moment later, not a caller's opinion.

        A *mismatched* id is a different thing entirely: it can only mean
        the text or source_id was edited after the id was computed, which is
        exactly the corruption `chunk_id` exists to catch (BACKLOG B97), so
        it still raises.

        Runs before field validation, so `data` may be anything pydantic
        would otherwise reject -- not a dict, or a dict missing/mistyping
        `source_id`/`text`. In every such case this leaves the input
        untouched and lets the normal field validators produce their own,
        clearer error instead of this one raising `KeyError`/`TypeError`.
        """
        if not isinstance(data, dict) or "id" not in data:
            return data
        source_id = data.get("source_id")
        text = data.get("text")
        if not isinstance(source_id, str) or not isinstance(text, str):
            return data
        expected = chunk_id(SourceId(source_id), text)
        supplied = data["id"]
        if supplied != expected:
            raise ValueError(
                f"id must be content-addressed over (source_id, text): "
                f"expected {expected}, got {supplied}"
            )
        data = dict(data)
        del data["id"]
        return data

    @field_validator("text")
    @classmethod
    def _text_is_storable(cls, value: str) -> str:
        reject_unstorable_text(value, what="text")
        return value

    @field_validator("metadata")
    @classmethod
    def _metadata_is_storable(cls, value: dict[str, Any]) -> dict[str, Any]:
        reject_unstorable_text(value, what="metadata")
        return value
