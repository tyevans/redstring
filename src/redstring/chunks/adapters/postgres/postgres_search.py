"""Candidate retrieval and backfill operations for PostgresChunkStore."""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING, Any

from redstring.domain.bm25 import CorpusStats
from redstring.domain.chunk_ranking import LexicalCandidate, LexicalCandidates
from redstring.domain.chunk_retrieval import SemanticCandidate
from redstring.domain.exceptions import DimensionMismatchError
from redstring.domain.tokenize import tokenize
from redstring.domain.vector import clamp_score, has_zero_norm

from .postgres_encode import encode_vector
from .postgres_sql import backfill_sql, candidates_sql, semantic_candidates_sql

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import asyncpg

    from redstring.domain.chunk import StoredChunk
    from redstring.domain.ids import TenantId


async def search_lexical(
    pool: asyncpg.Pool[Any],
    table: str,
    terms: Sequence[str],
    tenant_id: TenantId,
    limit: int,
    chunk_from: Callable[[Any], StoredChunk],
) -> LexicalCandidates:
    """Retrieve lexical candidates and corpus stats for given terms."""
    # Rejected before any query, matching the in-memory adapter: a
    # rejected call must not have counted a corpus.
    if limit < 0:
        raise ValueError(f"limit must not be negative, got {limit}")

    # Short-circuits before a round trip, not merely before a scan --
    # the in-memory adapter's equivalent guard only avoids the scan,
    # since building `tokenized` there is not a network cost. The port's
    # contract is stated in terms both readings satisfy: "without
    # touching the store."
    if not terms:
        return LexicalCandidates(
            stats=CorpusStats(n_docs=0, avg_doc_length=0.0, doc_frequencies={}), candidates=[]
        )

    # Sorted so the parameter array is deterministic across calls with
    # the same term set -- not required for correctness, but it keeps
    # the statement's bound values reproducible for anyone reading a
    # slow-query log.
    distinct_terms = sorted(set(terms))

    async with pool.acquire() as connection:
        corpus = await connection.fetchrow(
            f"SELECT count(*) AS n_docs, coalesce(avg(doc_length), 0) AS avg_len "  # nosec B608
            f"FROM {table} WHERE tenant_id = $1",
            tenant_id,
        )
        frequency_rows = await connection.fetch(
            f"SELECT term, count(*) AS df FROM {table}_terms "  # nosec B608
            "WHERE tenant_id = $1 AND term = ANY ($2) GROUP BY term",
            tenant_id,
            distinct_terms,
        )
        candidate_rows = await connection.fetch(
            candidates_sql(table), tenant_id, distinct_terms, limit
        )

    # `GROUP BY` in the frequency query cannot produce a row for a term no
    # chunk contains, so every requested term is seeded at `0` first --
    # the port requires `doc_frequencies` to cover exactly `terms`, absent
    # keys are not permitted as "the term wasn't asked about" here.
    doc_frequencies = dict.fromkeys(distinct_terms, 0)
    for row in frequency_rows:
        doc_frequencies[row["term"]] = row["df"]

    stats = CorpusStats(
        n_docs=corpus["n_docs"],
        avg_doc_length=float(corpus["avg_len"]),
        doc_frequencies=doc_frequencies,
    )
    candidates = [
        LexicalCandidate(
            chunk=chunk_from(row),
            doc_length=row["doc_length"],
            term_frequencies=json.loads(row["tfs"]),
        )
        for row in candidate_rows
    ]
    return LexicalCandidates(stats=stats, candidates=candidates)


async def search_semantic(
    pool: asyncpg.Pool[Any],
    table: str,
    vector: Sequence[float],
    tenant_id: TenantId,
    limit: int,
    dimension: int,
    chunk_from: Callable[[Any], StoredChunk],
    *,
    min_score: float | None = None,
) -> list[SemanticCandidate]:
    """Retrieve semantic nearest neighbor candidates."""
    # Same guard order as `InMemoryChunkStore` and `PgVectorStore._check`:
    # limit before width, width before zero-norm, so a rejected call
    # never reaches a query and a zero-norm check never runs against a
    # vector of the wrong shape.
    if limit < 0:
        raise ValueError(f"limit must not be negative, got {limit}")
    if len(vector) != dimension:
        raise DimensionMismatchError(expected=dimension, actual=len(vector))
    if has_zero_norm(vector):
        raise ValueError("a zero vector has no direction; cosine is undefined for it")
    if limit == 0:
        # `LIMIT 0` would answer correctly; not asking is cheaper, and the
        # port promises `[]` regardless of what the tenant holds -- the
        # same shape as `PgVectorStore.search`.
        return []

    rows = await pool.fetch(
        semantic_candidates_sql(table), tenant_id, encode_vector(vector), min_score, limit
    )
    return [
        SemanticCandidate(chunk=chunk_from(row), score=clamp_score(float(row["score"])))
        for row in rows
    ]


async def backfill_lexical(pool: asyncpg.Pool[Any], table: str) -> int:
    """Recompute `doc_length` and the term rows from stored `text`. Idempotent."""
    rows = await pool.fetch(f"SELECT id, tenant_id, text FROM {table}")  # nosec B608
    if not rows:
        return 0

    doc_lengths = json.dumps(
        [
            {
                "tenant_id": str(row["tenant_id"]),
                "id": row["id"],
                "doc_length": len(tokenize(row["text"])),
            }
            for row in rows
        ]
    )
    term_rows = json.dumps(
        [
            {"tenant_id": str(row["tenant_id"]), "chunk_id": row["id"], "term": term, "tf": tf}
            for row in rows
            for term, tf in Counter(tokenize(row["text"])).items()
        ]
    )
    touched = await pool.fetchval(backfill_sql(table), doc_lengths, term_rows)
    return int(touched)
