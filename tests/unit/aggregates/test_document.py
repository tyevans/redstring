"""The `Document` aggregate: extraction is idempotent per model version."""

from uuid import uuid4

import pytest

from kg_builder.aggregates.document import Document
from kg_builder.domain.entity import Entity, ExtractionMethod
from kg_builder.domain.vector import VectorRecord
from kg_builder.events import DocumentExtracted, EntitiesEmbedded
from kg_builder.events.streams import document_stream

SOURCE_ID = "doc-1"
MODEL = "ollama/qwen3.6-27b"


@pytest.fixture
def tenant_id():
    return uuid4()


@pytest.fixture
def document(tenant_id):
    return Document(document_stream(tenant_id=tenant_id, source_id=SOURCE_ID).aggregate_id)


def _entity(tenant_id, name="Ada Lovelace"):
    return Entity(
        id=uuid4(),
        tenant_id=tenant_id,
        name=name,
        normalized_name=name.lower(),
        entity_type="person",
        source_id=SOURCE_ID,
        extraction_method=ExtractionMethod.PATTERN,
        confidence=0.9,
    )


def _extract(document, tenant_id, *, model_version=MODEL, entities=()):
    return document.record_extraction(
        tenant_id=tenant_id,
        source_id=SOURCE_ID,
        model_version=model_version,
        entities=list(entities),
    )


class TestExtraction:
    def test_a_first_extraction_emits_an_event(self, document, tenant_id):
        event = _extract(document, tenant_id, entities=[_entity(tenant_id)])
        assert isinstance(event, DocumentExtracted)
        assert len(document.uncommitted_events) == 1

    def test_re_running_the_same_model_emits_nothing(self, document, tenant_id):
        """The idempotency the aggregate owns. A crash between `append` and the
        caller's acknowledgement is the normal case, and the retry must not
        write the same ten thousand entities a second time.
        """
        _extract(document, tenant_id)
        assert _extract(document, tenant_id) is None
        assert len(document.uncommitted_events) == 1

    def test_a_repeat_run_is_dropped_even_when_it_found_something_different(
        self, document, tenant_id
    ):
        """Idempotency is keyed on the model version, not on the payload.

        A retry that re-ran the model can legitimately return slightly
        different output -- decoding is not deterministic. Comparing payloads
        would make the second run a *new* extraction and write it, which is
        precisely the double write this rule exists to prevent.
        """
        _extract(document, tenant_id, entities=[_entity(tenant_id, "Ada")])
        assert _extract(document, tenant_id, entities=[_entity(tenant_id, "Grace")]) is None
        assert len(document.uncommitted_events) == 1

    def test_a_different_model_version_is_a_new_extraction(self, document, tenant_id):
        _extract(document, tenant_id)
        assert _extract(document, tenant_id, model_version="ollama/qwen4-30b") is not None
        assert len(document.uncommitted_events) == 2

    def test_the_rule_survives_rehydration(self, tenant_id):
        """Every real retry runs against an aggregate loaded from the log, not
        against the instance that emitted the first event."""
        aggregate_id = document_stream(tenant_id=tenant_id, source_id=SOURCE_ID).aggregate_id
        emitter = Document(aggregate_id)
        _extract(emitter, tenant_id)

        rehydrated = Document(aggregate_id)
        rehydrated.load_from_history(emitter.uncommitted_events)

        assert _extract(rehydrated, tenant_id) is None
        assert rehydrated.uncommitted_events == []

    def test_a_document_that_has_seen_nothing_accepts_any_model(self, tenant_id):
        """The zero case: no snapshot, no history, no prior run."""
        document = Document(uuid4())
        document.load_from_history([])
        assert document.version == 0
        assert _extract(document, tenant_id) is not None


class TestEmbedding:
    def _embed(self, document, tenant_id, *, model="ollama/nomic-embed-text"):
        return document.record_embeddings(
            tenant_id=tenant_id,
            source_id=SOURCE_ID,
            embedding_model=model,
            embeddings=[VectorRecord(entity_id=uuid4(), tenant_id=tenant_id, vector=[1.0, 0.0])],
        )

    def test_a_first_embedding_run_emits_an_event(self, document, tenant_id):
        assert isinstance(self._embed(document, tenant_id), EntitiesEmbedded)

    def test_re_running_the_same_embedding_model_emits_nothing(self, document, tenant_id):
        self._embed(document, tenant_id)
        assert self._embed(document, tenant_id) is None

    def test_a_different_embedding_model_is_a_new_run(self, document, tenant_id):
        self._embed(document, tenant_id)
        assert self._embed(document, tenant_id, model="openai/text-embedding-3-small")

    def test_embedding_and_extraction_do_not_share_an_idempotency_key(self, document, tenant_id):
        """One name space per kind of run. Sharing them would let an extraction
        under a model suppress an embedding run under a model of the same name
        -- and 'ollama/qwen3.6-27b' is a plausible name for both.
        """
        _extract(document, tenant_id, model_version="shared-name")
        assert self._embed(document, tenant_id, model="shared-name") is not None
