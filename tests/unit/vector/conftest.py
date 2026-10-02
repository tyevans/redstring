"""Shared fixtures and harness for pgvector unit tests."""

from __future__ import annotations

from typing import Any

from redstring.vector.adapters.pgvector import PgVectorStore

DIMENSION = 8


class ExplodingPool:
    """A pool that fails on any use.

    Not a mock of Postgres: nothing here asserts what SQL was sent to it. It
    exists so that "the guard raised" and "the guard raised *before any
    I/O*" are the same assertion -- a validation check that runs after the
    statement is a validation check that does not work.
    """

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the adapter reached the database via {name!r} before validating")


def make_store(*, dimension: int = DIMENSION, table: str = "kg_vectors") -> PgVectorStore:
    return PgVectorStore(ExplodingPool(), dimension=dimension, table=table)  # type: ignore[arg-type]


def make_vector(*, length: int = DIMENSION) -> list[float]:
    return [1.0, *([0.0] * (length - 1))] if length else []
