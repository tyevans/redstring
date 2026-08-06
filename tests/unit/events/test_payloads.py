"""What each event refuses to carry.

Every check here exists because the projection cannot catch the mistake later:
it writes each payload under the payload's own `tenant_id`, so a foreign
tenant in a payload is a silent cross-tenant write rather than a failure.
"""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from redstring.domain.consolidation import RelationshipRedirection
from redstring.domain.entity import Entity, ExtractionMethod
from redstring.domain.relationship import Relationship
from redstring.domain.vector import VectorRecord
from redstring.events import (
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


def _entity(tenant_id, **overrides):
    fields = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "name": "Ada Lovelace",
        "normalized_name": "ada lovelace",
        "entity_type": "person",
        "source_id": SOURCE_ID,
        "extraction_method": ExtractionMethod.PATTERN,
        "confidence": 0.9,
    }
    fields.update(overrides)
    return Entity(**fields)


def _relationship(tenant_id, **overrides):
    fields = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "source_entity_id": uuid4(),
        "target_entity_id": uuid4(),
        "relationship_type": "works_for",
        "confidence": 0.8,
    }
    fields.update(overrides)
    return Relationship(**fields)


def _extracted(tenant_id, **overrides):
    fields = {
        "aggregate_id": uuid4(),
        "tenant_id": tenant_id,
        "source_id": SOURCE_ID,
        "model_version": "ollama/qwen3.6-27b",
    }
    fields.update(overrides)
    return DocumentExtracted(**fields)


def _merged(tenant_id, **overrides):
    fields = {
        "aggregate_id": tenant_id,
        "tenant_id": tenant_id,
        "canonical_entity_id": uuid4(),
        "merged_entity_ids": [uuid4()],
    }
    fields.update(overrides)
    return EntitiesMerged(**fields)


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
        """Not laxness -- history. `Relationship.source_id` was added after
        this event shipped, so every edge in an existing log has none, and
        this validator runs on replay. Rejecting the absent case would make
        already-written events unreadable rather than catch anything.

        `_relationship` omits `source_id`, so this is the shape a real replay
        of an old event produces rather than one written to pass.
        """
        tenant_id = uuid4()
        event = _extracted(tenant_id, relationships=[_relationship(tenant_id)])
        assert event.relationships[0].source_id is None

    def test_the_document_a_carrier_names_is_the_one_it_is_appended_to(self):
        """`source_id` is on the event as well as implied by its stream.

        The stream id is a `uuid5` of the source id and so cannot be read back
        -- a consumer of the global feed would have no way to say which
        document an event came from without this field.
        """
        tenant_id = uuid4()
        event = _extracted(tenant_id, entities=[_entity(tenant_id)])
        assert event.entities[0].source_id == event.source_id


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
        """An undo that frees nothing is not an undo. Without this the field's
        `min_length=1` is decoration -- a mutant relaxing it to 0 survived
        until this test existed, and `EntitiesMerged` had the equivalent test
        from the start, which is how the asymmetry went unnoticed."""
        tenant_id = uuid4()
        with pytest.raises(ValidationError, match="at least 1"):
            MergeUndone(
                aggregate_id=tenant_id,
                tenant_id=tenant_id,
                merge_event_id=uuid4(),
                canonical_entity_id=uuid4(),
                unmerged_entity_ids=[],
            )

    def test_an_undo_names_the_merge_it_reverses(self):
        """A `MergeUndone` that only carried restorations would be
        indistinguishable from an unrelated correction, and the aggregate
        could not tell whether the merge it refers to ever happened."""
        tenant_id = uuid4()
        merge_event_id = uuid4()
        event = MergeUndone(
            aggregate_id=tenant_id,
            tenant_id=tenant_id,
            merge_event_id=merge_event_id,
            canonical_entity_id=uuid4(),
            unmerged_entity_ids=[uuid4()],
        )
        assert event.merge_event_id == merge_event_id


class TestComparisonsAreByValueNotIdentity:
    """Every check above compares two ids or two strings, and every test above
    supplies them as the *same object* -- so `is` and `==` agree and nothing
    distinguishes them.

    That is not theoretical here. An event round-tripped through JSON, which is
    what a stored event is, has values that are equal and not identical; and
    CPython interns string literals, so a `source_id` built at runtime is a
    different object from the one in a test. A `!=` silently meaning `is not`
    would *reject* perfectly good payloads, and only in production.
    """

    def test_a_tenant_that_arrived_as_a_string_is_still_this_tenant(self):
        tenant_id = uuid4()
        entity = _entity(UUID(str(tenant_id)))
        assert entity.tenant_id is not tenant_id
        _extracted(tenant_id, entities=[entity])

    def test_a_relationship_tenant_that_arrived_as_a_string_is_accepted(self):
        tenant_id = uuid4()
        relationship = _relationship(UUID(str(tenant_id)))
        assert relationship.tenant_id is not tenant_id
        _extracted(tenant_id, relationships=[relationship])

    def test_an_embedding_tenant_that_arrived_as_a_string_is_accepted(self):
        tenant_id = uuid4()
        record = VectorRecord(entity_id=uuid4(), tenant_id=UUID(str(tenant_id)), vector=[1.0, 0.0])
        EntitiesEmbedded(
            aggregate_id=uuid4(),
            tenant_id=tenant_id,
            source_id=SOURCE_ID,
            embedding_model="ollama/nomic-embed-text",
            embeddings=[record],
        )

    def test_a_source_id_built_at_runtime_is_still_this_document(self):
        """CPython interns literals, so `"doc-1"` in two places is one object.
        A source id assembled from parts is not, which is what a real
        extractor produces."""
        tenant_id = uuid4()
        built_at_runtime = "".join(["doc", "-", "1"])
        assert built_at_runtime is not SOURCE_ID
        assert built_at_runtime == SOURCE_ID
        _extracted(tenant_id, entities=[_entity(tenant_id, source_id=built_at_runtime)])

    def test_a_redirection_tenant_that_arrived_as_a_string_is_accepted(self):
        tenant_id = uuid4()
        redirection = RelationshipRedirection(before=_relationship(UUID(str(tenant_id))))
        _merged(tenant_id, redirections=[redirection])

    def test_a_restored_relationship_tenant_that_arrived_as_a_string_is_accepted(self):
        tenant_id = uuid4()
        MergeUndone(
            aggregate_id=tenant_id,
            tenant_id=tenant_id,
            merge_event_id=uuid4(),
            canonical_entity_id=uuid4(),
            unmerged_entity_ids=[uuid4()],
            restored_relationships=[_relationship(UUID(str(tenant_id)))],
        )
