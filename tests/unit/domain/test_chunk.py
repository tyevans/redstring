"""`StoredChunk` and the content-addressed id."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from redstring.domain.chunk import StoredChunk, chunk_id
from redstring.domain.ids import SourceId, TenantId


def test_the_same_text_under_the_same_source_gets_the_same_id() -> None:
    assert chunk_id("doc-1", "Ada Lovelace wrote the first algorithm.") == chunk_id(
        "doc-1", "Ada Lovelace wrote the first algorithm."
    )


def test_the_same_text_under_a_different_source_gets_a_different_id() -> None:
    """Identity includes provenance.

    Boilerplate shared by two documents is two chunks. Were it one, one
    document's `entity_ids` would attach to the other's passage and the two
    would fight over the same row on every replay.
    """
    assert chunk_id("doc-1", "All rights reserved.") != chunk_id("doc-2", "All rights reserved.")


def test_text_differing_only_in_whitespace_gets_a_different_id() -> None:
    """No normalisation. Two passages with different offsets are two passages."""
    assert chunk_id("doc-1", "Ada  Lovelace") != chunk_id("doc-1", "Ada Lovelace")


def test_the_source_and_the_text_cannot_be_confused_for_one_another() -> None:
    """The delimiter is load-bearing.

    A naive `hash(source_id + text)` makes ("ab", "c") and ("a", "bc") the
    same chunk. They are different chunks of different documents.
    """
    assert chunk_id("ab", "c") != chunk_id("a", "bc")


def test_a_chunk_defaults_to_no_entities_and_no_metadata() -> None:
    """Built directly, not through a factory, so the defaults actually run."""
    chunk = StoredChunk(
        id=chunk_id("doc-1", "text"),
        tenant_id=uuid4(),
        source_id="doc-1",
        text="text",
        chunk_index=0,
        start_char=0,
        end_char=4,
    )
    assert chunk.entity_ids == []
    assert chunk.metadata == {}


def test_two_chunks_do_not_share_a_default_entity_list() -> None:
    """A mutable default shared between instances is the classic pydantic trap."""
    tenant = uuid4()
    first = StoredChunk(
        id=chunk_id("d", "t"),
        tenant_id=tenant,
        source_id="d",
        text="t",
        chunk_index=0,
        start_char=0,
        end_char=1,
    )
    second = StoredChunk(
        id=chunk_id("d", "t"),
        tenant_id=tenant,
        source_id="d",
        text="t",
        chunk_index=1,
        start_char=1,
        end_char=2,
    )
    first.entity_ids.append(uuid4())
    assert second.entity_ids == []


def test_a_nul_byte_in_the_text_is_rejected() -> None:
    """Postgres rejects it at INSERT; the boundary is where it should fail.

    Not hypothetical -- it arrives from PDF text extraction.

    `id` is properly derived from `(source_id, text)` -- including the NUL --
    so this fails only for the reason asserted (`match=`) and not because the
    id validator would also object. Left un-derived, this test would still
    raise after the id-derivation validator was deleted, for the wrong
    reason.
    """
    text = "bad\x00text"
    with pytest.raises(ValidationError, match="NUL character"):
        StoredChunk(
            id=chunk_id("d", text),
            tenant_id=uuid4(),
            source_id="d",
            text=text,
            chunk_index=0,
            start_char=0,
            end_char=8,
        )


def test_a_nul_byte_in_the_metadata_is_rejected() -> None:
    """`id` is properly derived so this fails only for the metadata NUL, not
    also for a mismatched id -- see `test_a_nul_byte_in_the_text_is_rejected`
    for why both matter."""
    with pytest.raises(ValidationError, match="NUL character"):
        StoredChunk(
            id=chunk_id("d", "fine"),
            tenant_id=uuid4(),
            source_id="d",
            text="fine",
            chunk_index=0,
            start_char=0,
            end_char=4,
            metadata={"note": "bad\x00"},
        )


def test_a_stored_chunk_has_no_embedding_by_default() -> None:
    """`None` means not embedded, and is distinct from a zero vector.

    Built directly rather than through a factory: the defaults on the public
    type are what a caller constructing one gets, and a helper that passes
    every field never executes them.
    """
    chunk = StoredChunk(
        id=chunk_id(SourceId("doc-1"), "Ada Lovelace wrote the first algorithm."),
        tenant_id=TenantId(UUID(int=1)),
        source_id=SourceId("doc-1"),
        text="Ada Lovelace wrote the first algorithm.",
        chunk_index=0,
        start_char=0,
        end_char=39,
    )
    assert chunk.embedding is None


def test_entity_ids_survive_a_round_trip_as_uuids() -> None:
    entity = uuid4()
    chunk = StoredChunk(
        id=chunk_id("d", "t"),
        tenant_id=uuid4(),
        source_id="d",
        text="t",
        chunk_index=0,
        start_char=0,
        end_char=1,
        entity_ids=[entity],
    )
    restored = StoredChunk.model_validate(chunk.model_dump(mode="json"))
    assert restored.entity_ids == [entity]
    assert isinstance(restored.entity_ids[0], UUID)


def test_a_stored_chunk_whose_id_is_not_derived_from_its_text_is_rejected() -> None:
    """The id is what both adapters rest on to skip re-deriving state (B97).

    The wrong id here is a *real other chunk's* id rather than a random
    string, because a random string could be rejected by any format check
    the field ever grows, and this test would then pass for a reason that
    has nothing to do with derivation.
    """
    source = SourceId("doc-1")
    with pytest.raises(ValidationError, match="content-addressed"):
        StoredChunk(
            id=chunk_id(source, "some other passage"),
            tenant_id=TenantId(uuid4()),
            source_id=source,
            text="the passage actually being stored",
            chunk_index=0,
            start_char=0,
            end_char=33,
        )
