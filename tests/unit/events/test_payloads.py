"""What each event refuses to carry.

Every check here exists because the projection cannot catch the mistake later:
it writes each payload under the payload's own `tenant_id`, so a foreign
tenant in a payload is a silent cross-tenant write rather than a failure.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from redstring.domain.chunk import StoredChunk
from redstring.domain.consolidation import (
    MergeableFields,
    PropertyResolution,
    RelationshipRedirection,
)
from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.domain.vector import VectorRecord
from redstring.events import (
    DocumentChunked,
    DocumentExtracted,
    EntitiesEmbedded,
    EntitiesMerged,
    MergeUndone,
)

SOURCE_ID = "doc-1"

#: A tenant, and two others bracketing it -- one sorting below, one above.
#:
#: Every check below is a `!=`, and a mutant rewriting one as `<` or `>` is
#: half right against a single random `uuid4()`: it rejects the foreign
#: tenants that happen to sort the correct side and accepts the rest. Which
#: side a random pair lands on is luck, so the suite would pass or fail by
#: luck too. Bracketing makes both mutants fail, deterministically.
PIVOT_TENANT = UUID("88888888-8888-4888-8888-888888888888")
BELOW_TENANT = UUID("00000000-0000-4000-8000-000000000001")
ABOVE_TENANT = UUID("ffffffff-ffff-4fff-bfff-ffffffffffff")
OTHER_TENANTS = [BELOW_TENANT, ABOVE_TENANT]
TENANT_IDS = ["sorts-below", "sorts-above"]

#: Source ids bracketing `SOURCE_ID`, for the same reason.
OTHER_SOURCES = ["doc-0", "doc-2"]


#: A fixed observation instant. Never `datetime.now(UTC)`: a fixture that
#: varies per run makes any comparison on `observed_at` non-deterministic.
OBSERVED = datetime(2026, 2, 17, 11, 7, tzinfo=UTC)


def _entity(tenant_id, *, source_id=SOURCE_ID, **overrides):
    fields = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "name": "Ada Lovelace",
        "normalized_name": "ada lovelace",
        "entity_type": "person",
        "provenance": Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.PATTERN,
            confidence=0.9,
            source_id=source_id,
        ),
    }
    return Entity(**(fields | overrides))


def _relationship(tenant_id, **overrides):
    fields = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "source_entity_id": uuid4(),
        "target_entity_id": uuid4(),
        "relationship_type": "works_for",
        "confidence": 0.8,
    }
    return Relationship(**(fields | overrides))


def _extracted(tenant_id, **overrides):
    fields = {
        "aggregate_id": uuid4(),
        "tenant_id": tenant_id,
        "source_id": SOURCE_ID,
        "model_version": "ollama/qwen3.6-27b",
    }
    return DocumentExtracted(**(fields | overrides))


def _merged(tenant_id, **overrides):
    fields = {
        "aggregate_id": tenant_id,
        "tenant_id": tenant_id,
        "canonical_entity_id": uuid4(),
        "merged_entity_ids": [uuid4()],
    }
    return EntitiesMerged(**(fields | overrides))


def _chunk(tenant_id, **overrides):
    text = overrides.pop("text", "Ada Lovelace wrote the first program.")
    source_id = overrides.pop("source_id", SOURCE_ID)
    fields = {
        "tenant_id": tenant_id,
        "source_id": source_id,
        "text": text,
        "chunk_index": 0,
        "start_char": 0,
        "end_char": len(text),
    }
    return StoredChunk(**(fields | overrides))


def _chunked(tenant_id, **overrides):
    fields = {
        "aggregate_id": uuid4(),
        "tenant_id": tenant_id,
        "source_id": SOURCE_ID,
        "chunking_signature": "recursive:abc123",
    }
    return DocumentChunked(**(fields | overrides))


class TestDocumentExtracted:
    def test_an_empty_extraction_is_a_legitimate_event(self):
        """A document yielding nothing is a fact worth recording, and making it
        illegal would force every emitter to branch on the empty case."""
        event = _extracted(uuid4())
        assert event.entities == []
        assert event.relationships == []

    @pytest.mark.parametrize("other", OTHER_TENANTS, ids=TENANT_IDS)
    def test_entities_of_another_tenant_are_rejected(self, other):
        with pytest.raises(ValidationError, match="entities carries tenants"):
            _extracted(PIVOT_TENANT, entities=[_entity(other)])

    @pytest.mark.parametrize("other", OTHER_TENANTS, ids=TENANT_IDS)
    def test_relationships_of_another_tenant_are_rejected(self, other):
        with pytest.raises(ValidationError, match="relationships carries tenants"):
            _extracted(PIVOT_TENANT, relationships=[_relationship(other)])

    @pytest.mark.parametrize("other_source", OTHER_SOURCES)
    def test_entities_attributed_to_another_document_are_rejected(self, other_source):
        """Both a source id sorting below `SOURCE_ID` and one sorting above,
        because the check is `!=` and string comparison is ordered too."""
        tenant_id = uuid4()
        with pytest.raises(ValidationError, match="attributed to the document"):
            _extracted(tenant_id, entities=[_entity(tenant_id, source_id=other_source)])

    @pytest.mark.parametrize("other_source", OTHER_SOURCES)
    def test_relationships_attributed_to_another_document_are_rejected(self, other_source):
        tenant_id = uuid4()
        with pytest.raises(ValidationError, match="relationships must be attributed"):
            _extracted(tenant_id, relationships=[_relationship(tenant_id, source_id=other_source)])

    def test_a_relationship_with_no_provenance_is_still_a_legal_event(self):
        """Relationship.source_id was added later; existing log events omit it."""
        tenant_id = uuid4()
        event = _extracted(tenant_id, relationships=[_relationship(tenant_id)])
        assert event.relationships[0].source_id is None

    def test_the_document_a_carrier_names_is_the_one_it_is_appended_to(self):
        tenant_id = uuid4()
        event = _extracted(tenant_id, entities=[_entity(tenant_id)])
        assert event.entities[0].provenance.source_id == event.source_id


class TestDocumentChunked:
    @pytest.mark.parametrize("other", OTHER_TENANTS, ids=TENANT_IDS)
    def test_chunks_of_another_tenant_are_rejected(self, other):
        with pytest.raises(ValidationError, match="chunks carries tenants"):
            _chunked(PIVOT_TENANT, chunks=[_chunk(other)])

    @pytest.mark.parametrize("other_source", OTHER_SOURCES)
    def test_chunks_of_another_document_are_rejected(self, other_source):
        tenant_id = uuid4()
        with pytest.raises(ValidationError, match="attributed to the document"):
            _chunked(tenant_id, chunks=[_chunk(tenant_id, source_id=other_source)])

    def test_an_empty_chunking_is_a_legitimate_event(self):
        event = _chunked(uuid4())
        assert event.chunks == []

    def test_the_signature_is_carried_verbatim(self):
        signature = "recursive:abc123:ollama/qwen3.6-27b"
        assert _chunked(uuid4(), chunking_signature=signature).chunking_signature == signature

    def test_a_chunk_of_this_document_and_tenant_is_accepted(self):
        tenant_id = uuid4()
        event = _chunked(tenant_id, chunks=[_chunk(tenant_id)])
        assert event.chunks[0].source_id == event.source_id

    def test_a_dumped_event_survives_being_read_back(self):
        """DocumentChunked must round-trip through model_dump(mode='json')."""
        tenant_id = uuid4()
        event = _chunked(
            tenant_id,
            chunks=[
                _chunk(tenant_id, text="the first passage", chunk_index=0),
                _chunk(tenant_id, text="the second passage", chunk_index=1),
            ],
        )
        dumped = event.model_dump(mode="json")
        restored = DocumentChunked.model_validate(dumped)
        assert [chunk.id for chunk in restored.chunks] == [chunk.id for chunk in event.chunks]


class TestEntitiesEmbedded:
    @pytest.mark.parametrize("other", OTHER_TENANTS, ids=TENANT_IDS)
    def test_embeddings_of_another_tenant_are_rejected(self, other):
        record = VectorRecord(entity_id=uuid4(), tenant_id=other, vector=[1.0, 0.0])
        with pytest.raises(ValidationError, match="embeddings carries tenants"):
            EntitiesEmbedded(
                aggregate_id=uuid4(),
                tenant_id=PIVOT_TENANT,
                source_id=SOURCE_ID,
                embedding_model="ollama/nomic-embed-text",
                embeddings=[record],
            )


class TestEntitiesMerged:
    def test_a_merge_must_absorb_something(self):
        with pytest.raises(ValidationError, match="at least 1"):
            _merged(uuid4(), merged_entity_ids=[])

    def test_an_entity_cannot_be_merged_into_itself(self):
        entity_id = uuid4()
        with pytest.raises(ValidationError, match="merged into itself"):
            _merged(uuid4(), canonical_entity_id=entity_id, merged_entity_ids=[entity_id])

    def test_the_same_entity_cannot_appear_twice_in_one_merge(self):
        """Not tidiness: the aggregate counts absorbed entities to enforce "no
        double merge", and a repeated id inside one event would either
        double-count or, worse, make the first occurrence legal and the second
        a violation of an invariant the same event created."""
        entity_id = uuid4()
        with pytest.raises(ValidationError, match="duplicates"):
            _merged(uuid4(), merged_entity_ids=[entity_id, entity_id])

    @pytest.mark.parametrize("other", OTHER_TENANTS, ids=TENANT_IDS)
    def test_redirections_of_another_tenant_are_rejected(self, other):
        redirection = RelationshipRedirection(before=_relationship(other))
        with pytest.raises(ValidationError, match="redirections carry tenants"):
            _merged(PIVOT_TENANT, redirections=[redirection])


CANONICAL = EntityId(UUID(int=1))
ABSORBED = EntityId(UUID(int=2))


def _resolution(entity_id):
    return PropertyResolution(
        entity_id=entity_id,
        before=MergeableFields(properties={"role": "analyst"}),
        after=MergeableFields(properties={"role": "mathematician"}),
    )


class TestResolutionBelongsToTheCanonicalEntity:
    def test_a_resolution_naming_an_absorbed_entity_is_refused(self):
        """The projection upserts the row the resolution names. Naming an
        absorbed entity would overwrite the wrong row and have undo restore it,
        with nothing downstream able to tell."""
        with pytest.raises(ValidationError, match="canonical"):
            EntitiesMerged(
                aggregate_id=PIVOT_TENANT,
                tenant_id=PIVOT_TENANT,
                canonical_entity_id=CANONICAL,
                merged_entity_ids=[ABSORBED],
                resolution=_resolution(ABSORBED),
            )

    def test_a_resolution_naming_the_canonical_entity_is_accepted(self):
        event = EntitiesMerged(
            aggregate_id=PIVOT_TENANT,
            tenant_id=PIVOT_TENANT,
            canonical_entity_id=CANONICAL,
            merged_entity_ids=[ABSORBED],
            resolution=_resolution(CANONICAL),
        )
        assert event.resolution is not None
        assert event.resolution.after.properties == {"role": "mathematician"}

    def test_a_merge_may_decide_nothing_about_fields(self):
        """`None` is a true state: `ConsolidationLog` holds no entity data, so
        a direct aggregate caller genuinely has no resolution to give."""
        event = EntitiesMerged(
            aggregate_id=PIVOT_TENANT,
            tenant_id=PIVOT_TENANT,
            canonical_entity_id=CANONICAL,
            merged_entity_ids=[ABSORBED],
        )
        assert event.resolution is None


class TestMergeUndone:
    @pytest.mark.parametrize("other", OTHER_TENANTS, ids=TENANT_IDS)
    def test_restorations_of_another_tenant_are_rejected(self, other):
        with pytest.raises(ValidationError, match="restored_relationships carry tenants"):
            MergeUndone(
                aggregate_id=PIVOT_TENANT,
                tenant_id=PIVOT_TENANT,
                merge_event_id=uuid4(),
                canonical_entity_id=uuid4(),
                unmerged_entity_ids=[uuid4()],
                restored_relationships=[_relationship(other)],
            )

    def test_an_undo_must_name_at_least_one_entity(self):
        with pytest.raises(ValidationError, match="at least 1"):
            MergeUndone(
                aggregate_id=uuid4(),
                tenant_id=uuid4(),
                merge_event_id=uuid4(),
                canonical_entity_id=uuid4(),
                unmerged_entity_ids=[],
            )

    def test_an_undo_names_the_merge_it_reverses(self):
        tenant_id, merge_event_id = uuid4(), uuid4()
        event = MergeUndone(
            aggregate_id=tenant_id,
            tenant_id=tenant_id,
            merge_event_id=merge_event_id,
            canonical_entity_id=uuid4(),
            unmerged_entity_ids=[uuid4()],
        )
        assert event.merge_event_id == merge_event_id


class TestComparisonsAreByValueNotIdentity:
    """Every check compares by value (==) rather than object identity (is)."""

    def test_a_tenant_that_arrived_as_a_string_is_still_this_tenant(self):
        t = uuid4()
        e = _entity(UUID(str(t)))
        assert e.tenant_id is not t
        _extracted(t, entities=[e])

    def test_a_relationship_tenant_that_arrived_as_a_string_is_accepted(self):
        t = uuid4()
        r = _relationship(UUID(str(t)))
        assert r.tenant_id is not t
        _extracted(t, relationships=[r])

    def test_an_embedding_tenant_that_arrived_as_a_string_is_accepted(self):
        t = uuid4()
        rec = VectorRecord(entity_id=uuid4(), tenant_id=UUID(str(t)), vector=[1.0, 0.0])
        EntitiesEmbedded(
            aggregate_id=uuid4(),
            tenant_id=t,
            source_id=SOURCE_ID,
            embedding_model="ollama/nomic-embed-text",
            embeddings=[rec],
        )

    def test_a_source_id_built_at_runtime_is_still_this_document(self):
        t, src = uuid4(), "".join(["doc", "-", "1"])
        assert src is not SOURCE_ID
        assert src == SOURCE_ID
        _extracted(t, entities=[_entity(t, source_id=src)])

    def test_a_chunk_tenant_that_arrived_as_a_string_is_accepted(self):
        t = uuid4()
        c = _chunk(UUID(str(t)))
        assert c.tenant_id is not t
        _chunked(t, chunks=[c])

    def test_a_chunk_source_id_built_at_runtime_is_still_this_document(self):
        t, src = uuid4(), "".join(["doc", "-", "1"])
        assert src is not SOURCE_ID
        assert src == SOURCE_ID
        _chunked(t, chunks=[_chunk(t, source_id=src)])

    def test_a_redirection_tenant_that_arrived_as_a_string_is_accepted(self):
        t = uuid4()
        _merged(t, redirections=[RelationshipRedirection(before=_relationship(UUID(str(t))))])

    def test_a_restored_relationship_tenant_that_arrived_as_a_string_is_accepted(self):
        t = uuid4()
        MergeUndone(
            aggregate_id=t,
            tenant_id=t,
            merge_event_id=uuid4(),
            canonical_entity_id=uuid4(),
            unmerged_entity_ids=[uuid4()],
            restored_relationships=[_relationship(UUID(str(t)))],
        )
