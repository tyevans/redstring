"""Postgres `ChunkStore`: the second adapter, and the test of the port.

Modelled on `redstring.vector.adapters.pgvector` -- asyncpg directly with no
ORM, a guarded import naming the extra to install, an interpolated table name
proved to be a bare identifier first, and delete counts taken through a CTE
rather than by parsing asyncpg's `"DELETE n"` status string.
"""

from .postgres_chunk import PostgresChunkStore, _chunk_from
from .postgres_encode import deduplicate, encode, encode_terms, encode_vector, reject_zero_norm
from .postgres_sql import SCORE_SQL

__all__ = [
    "SCORE_SQL",
    "PostgresChunkStore",
    "_chunk_from",
    "deduplicate",
    "encode",
    "encode_terms",
    "encode_vector",
    "reject_zero_norm",
]
