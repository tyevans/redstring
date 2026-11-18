"""The Neo4j blocking read caps each key in the store, checked offline.

The compliance suite proves the capped rows are right against a real server
(`GraphStoreBlockingReadLimits`). These tests run in the default gate, with
no server: the guard clauses raise before any I/O, and the capped statement
puts `LIMIT` inside the per-key subquery, so rows above the cap never leave
the database. ADR-0152 states the contract.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from redstring.graph.adapters import neo4j as adapter


class _ExplodingDriver:
    """A driver that fails if anything tries to use it."""

    def session(self, **_: object) -> object:
        raise AssertionError("the adapter reached the database before validating")


def _offline_store() -> adapter.Neo4jGraphStore:
    return adapter.Neo4jGraphStore(_ExplodingDriver())  # type: ignore[arg-type]


class TestTheGuardsNeedNoDatabase:
    async def test_a_negative_limit_per_key_is_rejected(self):
        with pytest.raises(ValueError, match="limit_per_key must be non-negative, got -1"):
            await _offline_store().find_by_blocking_keys(["A"], uuid4(), limit_per_key=-1)

    async def test_limit_per_key_zero_answers_every_key_without_a_query(self):
        found = await _offline_store().find_by_blocking_keys(["A", "B"], uuid4(), limit_per_key=0)

        assert found == {"A": [], "B": []}


class _RecordingStore(adapter.Neo4jGraphStore):
    """A store whose `_run` records each statement and answers with no rows."""

    def __init__(self) -> None:
        super().__init__(object())  # type: ignore[arg-type]
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def _run(self, query: str, /, **parameters: object) -> list[Any]:
        self.calls.append((query, parameters))
        return []


class TestTheBlockingReadCapsEachKeyInTheStore:
    """`limit_per_key` reaches Cypher as a per-key `LIMIT`, not a Python slice.

    The compliance suite proves the rows are right against a real server.
    This proves, in the default gate, that the cap is a query parameter inside
    the per-key subquery, so the rows above it never leave the database.
    """

    async def test_a_capped_read_limits_inside_the_per_key_subquery(self):
        store = _RecordingStore()

        found = await store.find_by_blocking_keys(["A", "B"], uuid4(), limit_per_key=2)

        assert found == {"A": [], "B": []}
        [(query, parameters)] = store.calls
        # Between the subquery's opening and the outer RETURN, so the limit
        # applies per key and not to the whole result.
        assert query.index("CALL {") < query.index("LIMIT $limit") < query.index("RETURN wanted")
        assert parameters["limit"] == 2
        assert parameters["keys"] == ["A", "B"]

    async def test_an_uncapped_read_has_no_limit(self):
        store = _RecordingStore()

        await store.find_by_blocking_keys(["A"], uuid4())

        [(query, parameters)] = store.calls
        assert "LIMIT" not in query
        assert "limit" not in parameters
