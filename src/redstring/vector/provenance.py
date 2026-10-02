"""Provenance verification for vector stores (TASK-0007, BACKLOG B156).

Ensures that a vector store cannot be silently corrupted by writing vectors
embedded with different embedding models or mismatched task prefixes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

from redstring.domain.exceptions import (
    DimensionMismatchError,
    TaskPrefixMismatchError,
    VectorProvenanceMismatchError,
)
from redstring.domain.vector import VectorProvenance


def verify_vector_provenance(
    *,
    expected_dimension: int,
    actual_dimension: int,
    expected_model: str | None = None,
    actual_model: str | None = None,
    expected_document_prefix: str | None = None,
    actual_document_prefix: str | None = None,
) -> None:
    """Validate that vector provenance attributes match, raising domain errors on mismatch."""
    if expected_dimension != actual_dimension:
        raise DimensionMismatchError(expected=expected_dimension, actual=actual_dimension)

    if expected_model is not None and actual_model is not None and expected_model != actual_model:
        raise VectorProvenanceMismatchError(
            f"expected embedding model {expected_model!r}, got {actual_model!r}; "
            f"changing embedding model requires a new store"
        )

    if (
        expected_document_prefix is not None
        and actual_document_prefix is not None
        and expected_document_prefix != actual_document_prefix
    ):
        raise TaskPrefixMismatchError(
            expected=expected_document_prefix,
            actual=actual_document_prefix,
        )


def provenance_table_ddl(table: str) -> str:
    """DDL for the store's provenance tracking table."""
    return (
        f"CREATE TABLE IF NOT EXISTS {table}_provenance ("
        "  id integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),"
        "  model text,"
        "  dimension integer NOT NULL,"
        "  document_prefix text NOT NULL"
        ")"
    )


class PgConnectionLike(Protocol):
    """Structural protocol for connection operations needed by provenance verification."""

    async def execute(self, query: str, *args: object) -> object: ...

    async def fetchrow(self, query: str, *args: object) -> Mapping[str, object] | None: ...


async def ensure_pgvector_provenance(
    connection: PgConnectionLike,
    table: str,
    *,
    dimension: int,
    model: str | None,
    document_prefix: str | None,
) -> VectorProvenance | None:
    """Verify or record the provenance row for a pgvector store table."""
    await connection.execute(provenance_table_ddl(table))
    row = await connection.fetchrow(
        f"SELECT model, dimension, document_prefix FROM {table}_provenance WHERE id = 1"  # nosec B608
    )
    if row is not None:
        stored_dim = int(str(row["dimension"]))
        stored_model = str(row["model"]) if row["model"] is not None else None
        stored_prefix = str(row["document_prefix"])

        verify_vector_provenance(
            expected_dimension=stored_dim,
            actual_dimension=dimension,
            expected_model=stored_model,
            actual_model=model,
            expected_document_prefix=stored_prefix,
            actual_document_prefix=document_prefix,
        )
        return VectorProvenance(
            dimension=stored_dim,
            model=stored_model,
            document_prefix=stored_prefix,
        )

    if model is not None or document_prefix is not None:
        effective_prefix = document_prefix or ""
        await connection.execute(
            f"INSERT INTO {table}_provenance (id, model, dimension, document_prefix) "  # nosec B608
            "VALUES (1, $1, $2, $3) ON CONFLICT (id) DO NOTHING",
            model,
            dimension,
            effective_prefix,
        )
        return VectorProvenance(
            dimension=dimension,
            model=model,
            document_prefix=effective_prefix,
        )
    return None
