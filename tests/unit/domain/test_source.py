"""Tests for kg_builder.domain.source."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from kg_builder.domain.source import SourceDocument


def _source(**overrides):
    fields = {"id": "doc-1", "text": "Some content."}
    fields.update(overrides)
    return SourceDocument(**fields)


def test_minimal_construction():
    doc = _source()
    assert doc.uri is None
    assert doc.title is None
    assert doc.published_at is None
    assert doc.metadata == {}


def test_empty_text_is_rejected():
    with pytest.raises(ValidationError):
        _source(text="")


def test_blank_text_is_rejected():
    with pytest.raises(ValidationError):
        _source(text="   ")


def test_naive_published_at_is_rejected():
    with pytest.raises(ValidationError):
        _source(published_at=datetime(2024, 1, 1))


def test_round_trip_through_model_dump():
    doc = _source(
        uri="https://example.com/doc",
        title="A Doc",
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        metadata={"lang": "en"},
    )
    reconstructed = SourceDocument.model_validate(doc.model_dump())
    assert reconstructed == doc
