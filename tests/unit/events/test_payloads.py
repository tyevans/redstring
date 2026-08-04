"""What each event refuses to carry.

Every check here exists because the projection cannot catch the mistake later:
it writes each payload under the payload's own `tenant_id`, so a foreign
tenant in a payload is a silent cross-tenant write rather than a failure.
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from kg_builder.domain.consolidation import RelationshipRedirection
from kg_builder.domain.entity import Entity, ExtractionMethod
from kg_builder.domain.relationship import Relationship
from kg_builder.domain.vector import VectorRecord
from kg_builder.events import (
    DocumentExtracted,
    EntitiesEmbedded,
    EntitiesMerged,
    MergeUndone,
)

SOURCE_ID = "doc-1"


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

    def test_entities_of_another_tenant_are_rejected(self):
        tenant_id = uuid4()
        with pytest.raises(ValidationError, match="entities carries tenants"):
            _extracted(tenant_id, entities=[_entity(uuid4())])

    def test_relationships_of_another_tenant_are_rejected(self):
        tenant_id = uuid4()
        with pytest.raises(ValidationError, match="relationships carries tenants"):
            _extracted(tenant_id, relationships=[_relationship(uuid4())])

    def test_entities_attributed_to_another_document_are_rejected(self):
        tenant_id = uuid4()
        with pytest.raises(ValidationError, match="attributed to the document"):
            _extracted(tenant_id, entities=[_entity(tenant_id, source_id="doc-2")])

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
    def test_embeddings_of_another_tenant_are_rejected(self):
        tenant_id = uuid4()
        record = VectorRecord(entity_id=uuid4(), tenant_id=uuid4(), vector=[1.0, 0.0])
        with pytest.raises(ValidationError, match="embeddings carries tenants"):
            EntitiesEmbedded(
                aggregate_id=uuid4(),
                tenant_id=tenant_id,
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

    def test_redirections_of_another_tenant_are_rejected(self):
        tenant_id = uuid4()
        redirection = RelationshipRedirection(before=_relationship(uuid4()))
        with pytest.raises(ValidationError, match="redirections carry tenants"):
            _merged(tenant_id, redirections=[redirection])


class TestMergeUndone:
    def test_restorations_of_another_tenant_are_rejected(self):
        tenant_id = uuid4()
        with pytest.raises(ValidationError, match="restored_relationships carry tenants"):
            MergeUndone(
                aggregate_id=tenant_id,
                tenant_id=tenant_id,
                merge_event_id=uuid4(),
                canonical_entity_id=uuid4(),
                unmerged_entity_ids=[uuid4()],
                restored_relationships=[_relationship(uuid4())],
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
