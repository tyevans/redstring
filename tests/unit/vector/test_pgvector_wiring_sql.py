"""SQL construction, port structure, and syntax leakage tests for pgvector."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from redstring.ports.vector_store import VectorStore
from redstring.vector.adapters import pgvector as adapter
from redstring.vector.adapters.pgvector import PgVectorStore

from .conftest import DIMENSION, make_store

PGVECTOR_MARKERS = ("<=>", "::vector", " vector(", "Vector(")
LEGACY_PGVECTOR: frozenset[str] = frozenset()
OTHER_PGVECTOR_ADAPTERS: frozenset[str] = frozenset({"chunks/adapters/postgres.py"})
SOURCE_ROOT = Path(adapter.__file__).parent.parent.parent


class TestSqlConstruction:
    """What the statements say, asserted without executing them."""

    def test_the_batch_insert_is_one_statement(self):
        sql = make_store()._insert_sql()
        assert sql.count("INSERT INTO") == 1
        assert "unnest(" in sql
        assert sql.count("$5") == 1
        assert "$6" not in sql

    def test_the_batch_insert_upserts_rather_than_failing(self):
        sql = make_store()._insert_sql()
        assert "ON CONFLICT (tenant_id, entity_id) DO UPDATE" in sql
        assert "metadata = EXCLUDED.metadata" in sql
        assert "||" not in sql

    def test_the_search_filters_before_it_limits(self):
        sql = make_store()._search_sql()
        assert sql.index("WHERE") < sql.index("ORDER BY") < sql.index("LIMIT")

    def test_the_search_scores_with_cosine_distance_the_right_way_round(self):
        assert adapter._SCORE == "1 - (embedding <=> $2::vector) / 2"
        assert adapter._SCORE in make_store()._search_sql()

    def test_the_search_orders_by_score_then_id(self):
        sql = make_store()._search_sql()
        assert "ORDER BY embedding <=> $2::vector ASC, entity_id::text ASC" in sql

    def test_the_search_is_tenant_scoped_and_type_filtered_in_sql(self):
        sql = make_store()._search_sql()
        assert "WHERE tenant_id = $1" in sql
        assert "($3 OR entity_type = ANY($4::text[]))" in sql

    def test_the_schema_keys_on_the_pair_and_indexes_no_embedding(self):
        statements = make_store()._schema_statements()
        table = statements[0]
        assert "PRIMARY KEY (tenant_id, entity_id)" in table
        assert f"vector({DIMENSION})" in table
        joined = " ".join(statements)
        assert "hnsw" not in joined
        assert "ivfflat" not in joined

    def test_the_schema_is_idempotent_by_construction(self):
        for statement in make_store()._schema_statements():
            assert "IF NOT EXISTS" in statement

    def test_the_declared_dimension_reaches_the_column_type(self):
        assert "vector(768)" in make_store(dimension=768)._schema_statements()[0]


class TestStructure:
    def test_the_adapter_satisfies_the_port(self):
        assert isinstance(make_store(), VectorStore)

    @pytest.mark.parametrize(
        "method", ["upsert", "upsert_many", "get", "search", "delete", "delete_by_tenant"]
    )
    def test_signatures_match_the_port(self, method: str):
        port = inspect.signature(getattr(VectorStore, method))
        implementation = inspect.signature(getattr(PgVectorStore, method))
        assert [(p.name, p.kind, p.default) for p in port.parameters.values()] == [
            (p.name, p.kind, p.default) for p in implementation.parameters.values()
        ]

    def test_the_in_memory_adapter_has_the_same_signatures(self):
        from redstring.vector.adapters.memory import InMemoryVectorStore

        for name in ("upsert", "upsert_many", "get", "search", "delete", "delete_by_tenant"):
            assert inspect.signature(getattr(PgVectorStore, name)) == inspect.signature(
                getattr(InMemoryVectorStore, name)
            )


class TestPgVectorSyntaxDoesNotLeak:
    """The port must not become pgvector-shaped."""

    def _offenders(self) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for path in SOURCE_ROOT.rglob("*.py"):
            relative = path.relative_to(SOURCE_ROOT).as_posix()
            if (
                relative == "vector/adapters/pgvector.py"
                or relative in LEGACY_PGVECTOR
                or relative in OTHER_PGVECTOR_ADAPTERS
            ):
                continue
            text = path.read_text(encoding="utf-8")
            hits = [marker for marker in PGVECTOR_MARKERS if marker in text]
            if hits:
                found[relative] = hits
        return found

    def test_no_module_outside_the_adapter_speaks_pgvector(self):
        assert self._offenders() == {}, (
            "pgvector syntax outside the adapter: the port's whole value is "
            "that callers do not know which backend they have."
        )

    def test_the_detector_would_notice(self):
        text = (SOURCE_ROOT / "vector/adapters/pgvector.py").read_text(encoding="utf-8")
        assert [marker for marker in PGVECTOR_MARKERS if marker in text]

    def test_the_exemption_list_has_no_stale_entries(self):
        for relative in LEGACY_PGVECTOR:
            assert (SOURCE_ROOT / relative).exists(), (
                f"{relative} is gone; delete its entry from LEGACY_PGVECTOR"
            )

    def test_the_other_adapters_list_has_no_stale_entries(self):
        for relative in OTHER_PGVECTOR_ADAPTERS:
            assert (SOURCE_ROOT / relative).exists(), (
                f"{relative} is gone; delete its entry from OTHER_PGVECTOR_ADAPTERS"
            )

    def test_the_other_adapters_list_would_notice_a_leak_too(self):
        for relative in OTHER_PGVECTOR_ADAPTERS:
            text = (SOURCE_ROOT / relative).read_text(encoding="utf-8")
            assert [marker for marker in PGVECTOR_MARKERS if marker in text]
