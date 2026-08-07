"""The `Document` aggregate: extraction is idempotent per model version."""

from uuid import uuid4

import pytest

from redstring.aggregates.document import Document
from redstring.domain.chunk import StoredChunk, chunk_id
from redstring.domain.entity import Entity, ExtractionMethod
from redstring.domain.vector import VectorRecord
from redstring.events import DocumentChunked, DocumentExtracted, EntitiesEmbedded
from redstring.events.streams import document_stream

SOURCE_ID = "doc-1"
MODEL = "ollama/qwen3.6-27b"
SIGNATURE = "recursive:abc123"


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


def _chunk(tenant_id, text, index=0):
    return StoredChunk(
        id=chunk_id(SOURCE_ID, text),
        tenant_id=tenant_id,
        source_id=SOURCE_ID,
        text=text,
        chunk_index=index,
        start_char=0,
        end_char=len(text),
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


class TestChunking:
    """Chunking is idempotent per `chunking_signature`, in its own key space."""

    def _chunk_it(self, document, tenant_id, *, signature=SIGNATURE, texts=("first passage",)):
        return document.record_chunking(
            tenant_id=tenant_id,
            source_id=SOURCE_ID,
            chunking_signature=signature,
            chunks=[_chunk(tenant_id, text, i) for i, text in enumerate(texts)],
        )

    def test_a_first_chunking_emits_an_event(self, document, tenant_id):
        event = self._chunk_it(document, tenant_id)
        assert isinstance(event, DocumentChunked)
        assert [c.text for c in event.chunks] == ["first passage"]

    def test_recording_the_same_chunking_signature_twice_emits_nothing(self, document, tenant_id):
        """A retry after a crash is a no-op, matching `record_extraction`."""
        self._chunk_it(document, tenant_id)
        assert self._chunk_it(document, tenant_id) is None
        assert len(document.uncommitted_events) == 1

    def test_a_repeat_signature_is_dropped_even_when_it_split_differently(
        self, document, tenant_id
    ):
        """Keyed on the signature, not the payload -- the same rule extraction
        states, and the reason a signature is composed by the emitter from the
        settings that decided the split."""
        self._chunk_it(document, tenant_id, texts=("first passage",))
        assert self._chunk_it(document, tenant_id, texts=("a", "b")) is None
        assert len(document.uncommitted_events) == 1

    def test_a_different_chunking_signature_is_recorded(self, document, tenant_id):
        """Re-chunking under new settings -- or extraction after indexing -- is
        a new fact, not a repeat. The second signature is the extraction
        pipeline's shape: the indexer's, with a model version appended."""
        self._chunk_it(document, tenant_id, signature=SIGNATURE)
        assert self._chunk_it(document, tenant_id, signature=f"{SIGNATURE}:{MODEL}") is not None
        assert len(document.uncommitted_events) == 2

    def test_an_empty_chunking_is_recorded(self, document, tenant_id):
        """A document that chunks to nothing is a fact, and the projection
        needs the event to empty a source. A guard treating the empty payload
        as "nothing happened" would leave the old passages in place forever."""
        event = self._chunk_it(document, tenant_id, texts=())
        assert isinstance(event, DocumentChunked)
        assert event.chunks == []

    def test_chunking_and_extraction_keep_separate_key_spaces(self, document, tenant_id):
        """Recording a chunking under the string "v1" must not suppress an
        extraction under model_version "v1". The two namespaces overlap in
        practice, which is why embedding already has its own list."""
        self._chunk_it(document, tenant_id, signature="v1")
        assert _extract(document, tenant_id, model_version="v1") is not None

    def test_extraction_and_chunking_keep_separate_key_spaces(self, document, tenant_id):
        """The other direction. One shared list would let either suppress the
        other, and a test in one direction only cannot tell a shared list from
        a correctly separate pair when the code reads the wrong one."""
        _extract(document, tenant_id, model_version="v1")
        assert self._chunk_it(document, tenant_id, signature="v1") is not None

    def test_embedding_and_chunking_keep_separate_key_spaces(self, document, tenant_id):
        document.record_embeddings(
            tenant_id=tenant_id, source_id=SOURCE_ID, embedding_model="v1", embeddings=[]
        )
        assert self._chunk_it(document, tenant_id, signature="v1") is not None

    def test_the_rule_survives_rehydration(self, tenant_id):
        """Every real retry runs against an aggregate loaded from the log."""
        aggregate_id = document_stream(tenant_id=tenant_id, source_id=SOURCE_ID).aggregate_id
        emitter = Document(aggregate_id)
        self._chunk_it(emitter, tenant_id)

        rehydrated = Document(aggregate_id)
        rehydrated.load_from_history(emitter.uncommitted_events)

        assert self._chunk_it(rehydrated, tenant_id) is None
        assert rehydrated.uncommitted_events == []

    def test_a_signature_built_at_runtime_is_still_the_same_signature(self, document, tenant_id):
        """CPython interns literals, so a membership test written with `is`
        would pass on every literal in this file and fail on the signature a
        real emitter composes with an f-string."""
        built_at_runtime = ":".join(["recursive", "abc123"])
        assert built_at_runtime is not SIGNATURE
        self._chunk_it(document, tenant_id, signature=SIGNATURE)
        assert self._chunk_it(document, tenant_id, signature=built_at_runtime) is None
