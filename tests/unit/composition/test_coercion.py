"""Tests for flexible document input coercion."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from redstring import SourceDocument, SourceId
from redstring.composition.coercion import coerce_document, content_addressed_source_id


def test_coerce_source_document_returns_identity() -> None:
    doc = SourceDocument(id=SourceId("doc-1"), text="Ada Lovelace was a mathematician.")
    coerced = coerce_document(doc)
    assert coerced is doc
    assert coerced.id == SourceId("doc-1")
    assert coerced.text == "Ada Lovelace was a mathematician."


def test_coerce_plain_string_derives_content_addressed_id() -> None:
    text = "Ada Lovelace worked with Charles Babbage on the Analytical Engine."
    coerced = coerce_document(text)
    assert isinstance(coerced, SourceDocument)
    assert coerced.text == text
    assert coerced.id.startswith("doc-")
    # Verify deterministic derivation
    expected_id = content_addressed_source_id(text)
    assert coerced.id == expected_id

    # Another identical text yields identical id
    second = coerce_document(text)
    assert second.id == coerced.id


def test_coerce_plain_string_rejects_blank() -> None:
    with pytest.raises(ValueError, match="text must not be blank"):
        coerce_document("   \n\t  ")


def test_coerce_dict_with_explicit_id() -> None:
    payload = {
        "id": "custom-id-42",
        "text": "Charles Babbage designed the Difference Engine.",
        "title": "Babbage History",
        "uri": "https://example.org/babbage",
        "metadata": {"source": "archive"},
    }
    coerced = coerce_document(payload)
    assert coerced.id == SourceId("custom-id-42")
    assert coerced.text == payload["text"]
    assert coerced.title == "Babbage History"
    assert coerced.uri == "https://example.org/babbage"
    assert coerced.metadata == {"source": "archive"}


def test_coerce_dict_without_id_generates_content_addressed_id() -> None:
    text = "Alan Turing developed the bombe at Bletchley Park."
    payload = {"text": text}
    coerced = coerce_document(payload)
    assert coerced.text == text
    assert coerced.id == content_addressed_source_id(text)


def test_coerce_dict_with_published_at() -> None:
    now = datetime(1843, 1, 1, tzinfo=UTC)
    payload = {
        "text": "Sketch of the Analytical Engine.",
        "published_at": now,
    }
    coerced = coerce_document(payload)
    assert coerced.published_at == now


def test_coerce_dict_rejects_missing_or_blank_text() -> None:
    with pytest.raises(ValueError, match="dict must contain a string 'text' field"):
        coerce_document({"id": "foo"})

    with pytest.raises(ValueError, match="text must not be blank"):
        coerce_document({"text": "  "})


def test_coerce_rejects_invalid_types() -> None:
    with pytest.raises(TypeError, match="Expected SourceDocument, str, or dict"):
        coerce_document(12345)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="Expected SourceDocument, str, or dict"):
        coerce_document(None)  # type: ignore[arg-type]
