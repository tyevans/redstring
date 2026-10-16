"""Input coercion for documents and text payloads."""

from __future__ import annotations

import hashlib
from typing import Any

from redstring.domain.ids import SourceId
from redstring.domain.source import SourceDocument

__all__ = ["coerce_document", "content_addressed_source_id"]


def content_addressed_source_id(text: str) -> SourceId:
    """Derive a deterministic content-addressed SourceId from document text."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return SourceId(f"doc-{digest}")


def coerce_document(doc_or_text: SourceDocument | str | dict[str, Any]) -> SourceDocument:
    """Coerce a SourceDocument, plain string, or dictionary into a validated SourceDocument.

    Parameters:
        doc_or_text: An existing SourceDocument, a plain non-empty string,
                     or a dictionary containing at least a non-empty 'text' field.

    Returns:
        A validated SourceDocument instance.

    Raises:
        TypeError: If doc_or_text is not a SourceDocument, str, or dict.
        ValueError: If text is blank or dictionary fields are invalid.
    """
    if isinstance(doc_or_text, SourceDocument):
        return doc_or_text

    if isinstance(doc_or_text, str):
        if not doc_or_text.strip():
            raise ValueError("text must not be blank")
        return SourceDocument(
            id=content_addressed_source_id(doc_or_text),
            text=doc_or_text,
        )

    if isinstance(doc_or_text, dict):
        text = doc_or_text.get("text")
        if not isinstance(text, str):
            raise ValueError("dict must contain a string 'text' field")
        if not text.strip():
            raise ValueError("text must not be blank")

        raw_id = doc_or_text.get("id")
        source_id = (
            SourceId(str(raw_id)) if raw_id is not None else content_addressed_source_id(text)
        )

        return SourceDocument(
            id=source_id,
            text=text,
            uri=doc_or_text.get("uri"),
            title=doc_or_text.get("title"),
            published_at=doc_or_text.get("published_at"),
            metadata=doc_or_text.get("metadata", {}),
        )

    raise TypeError(f"Expected SourceDocument, str, or dict, got {type(doc_or_text).__name__}")
