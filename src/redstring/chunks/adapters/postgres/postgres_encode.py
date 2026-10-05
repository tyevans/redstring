"""Encoding and normalization functions for the Postgres chunk store."""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING

from redstring.domain.tokenize import tokenize
from redstring.domain.vector import has_zero_norm

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.domain.chunk import StoredChunk


def reject_zero_norm(chunks: Sequence[StoredChunk]) -> None:
    """Cosine is undefined at zero magnitude.

    A stored zero vector would force every later `semantic_candidates` call
    to choose between a silent NaN and a per-row skip that hides a caller's
    bug, so it is rejected here instead -- the same choice
    `InMemoryChunkStore._reject_zero_norm` and `PgVectorStore._check` already
    make for their own ports. Chunks with no embedding at all are unaffected;
    only a *stored* zero vector is a problem.
    """
    for chunk in chunks:
        if chunk.embedding is not None and has_zero_norm(chunk.embedding):
            raise ValueError(f"chunk {chunk.id!r} has a zero vector; cosine is undefined for it")


def deduplicate(chunks: Sequence[StoredChunk]) -> list[StoredChunk]:
    """Collapse repeated `(tenant_id, id)` keys, keeping the last.

    Required, not an optimisation: `ON CONFLICT DO UPDATE` raises "cannot
    affect row a second time" when one statement touches a row twice, and a
    re-delivered event arrives as exactly that -- two chunks sharing
    `(source_id, text)` share a content-addressed id. Keeping the *last* is
    the port's stated rule, and it is the same one that applies across calls.

    The key is the **pair**. Content addressing makes a collision on `id`
    alone ordinary: the same passage under two tenants hashes identically, and
    a key built from `id` would silently merge two tenants' rows.
    """
    return list({(chunk.tenant_id, chunk.id): chunk for chunk in chunks}.values())


def encode(chunks: Sequence[StoredChunk]) -> str:
    """Render a batch as the `jsonb` document `jsonb_to_recordset` unpacks.

    Uuids become strings because JSON has no uuid; Postgres casts them back
    through the `uuid` and `uuid[]` column types in `_INCOMING`. `metadata`
    is nested as an object rather than a string, so it arrives as `jsonb`
    without a second parse.

    `doc_length` travels with the row rather than being computed in SQL: it
    is `len(tokenize(chunk.text))`, and computing that any other way risks
    the in-memory adapter and this one disagreeing about what a token is --
    exactly the divergence `domain/tokenize.py` exists to prevent.

    `embedding` travels as pgvector's text input form -- see `encode_vector`
    below -- or `None`, which `jsonb_to_recordset` casts straight to a `NULL`
    `vector` column.
    """
    return json.dumps(
        [
            {
                "tenant_id": str(chunk.tenant_id),
                "id": chunk.id,
                "source_id": chunk.source_id,
                "text": chunk.text,
                "chunk_index": chunk.chunk_index,
                "start_char": chunk.start_char,
                "end_char": chunk.end_char,
                "entity_ids": [str(entity_id) for entity_id in chunk.entity_ids],
                "metadata": chunk.metadata,
                "doc_length": len(tokenize(chunk.text)),
                "embedding": None if chunk.embedding is None else encode_vector(chunk.embedding),
            }
            for chunk in chunks
        ]
    )


def encode_vector(vector: Sequence[float]) -> str:
    """Render a vector as pgvector's text input form, `[1,2,3]`.

    A duplicate of `redstring.vector.adapters.pgvector.encode_vector`, not an
    import of it: `chunks` and `vector` are siblings in the layered contract
    in `pyproject.toml` and forbidden from importing each other, so the two
    copies are proved identical the way `_SCORE` is -- by a test, in
    `tests/unit/chunks/test_postgres_schema.py` -- rather than by sharing code.
    See that function's docstring for why this is text rather than
    `pgvector.asyncpg.register_vector`, and why there is deliberately no
    matching `decode_vector`.
    """
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


def encode_terms(chunks: Sequence[StoredChunk]) -> str:
    """Render each chunk's term index as the `jsonb` document `_TERMS_INCOMING`
    unpacks: one row per `(tenant_id, chunk_id, term)` carrying that term's
    frequency.

    Computed from `text` with the same `tokenize` the in-memory adapter
    scores against directly -- this table exists only because Postgres needs
    something to seek on, not as a second source of truth. It is written once
    per id and never updated; see `_TERMS_ON_CONFLICT`.
    """
    return json.dumps(
        [
            {
                "tenant_id": str(chunk.tenant_id),
                "chunk_id": chunk.id,
                "term": term,
                "tf": tf,
            }
            for chunk in chunks
            for term, tf in Counter(tokenize(chunk.text)).items()
        ]
    )
