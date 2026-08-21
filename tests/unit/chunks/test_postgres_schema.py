"""Default-gate checks on the Postgres `ChunkStore` that need no server.

Same rationale as `tests/unit/vector/test_pgvector_adapter_is_wired.py`: the
adapter's behavioural tests are `integration`-marked, so `addopts` deselects
them and the module would go unexecuted in the commit gate without this file.
A cosmic-ray mutant in unreached source passes every other test in the suite,
which is what slice 4's Neo4j adapter proved the expensive way.

These tests run every part of the adapter that does not need a server:
argument validation (an `_ExplodingPool` proves the guard raises before any
I/O), SQL construction (the statements are built by methods that take no
connection), encoding (`encode`, `encode_terms`, `deduplicate` are pure), and
structure (signatures against the port, no leaked SQL, importability).

What still needs the container -- the statements actually executing, the
term-index writes, tenant isolation, ranking agreement with the in-memory
adapter -- is `tests/integration/chunks/test_postgres_store.py` (Task 7), not
duplicated here with mocks.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from redstring.chunks.adapters import postgres as adapter
from redstring.chunks.adapters.postgres import (
    _SCORE,
    PostgresChunkStore,
    deduplicate,
    encode,
    encode_terms,
)
from redstring.chunks.adapters.postgres import encode_vector as chunk_encode_vector
from redstring.domain.chunk import StoredChunk
from redstring.domain.chunk import chunk_id as derive_chunk_id
from redstring.ports.chunk_store import ChunkStore


class _ExplodingPool:
    """A pool that fails on any use.

    Not a mock of Postgres: nothing here asserts what SQL was sent to it. It
    exists so that "the guard raised" and "the guard raised *before any I/O*"
    are the same assertion -- a validation check that runs after the
    statement is a validation check that does not work.
    """

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the adapter reached the database via {name!r} before validating")


def _store(*, table: str = "kg_chunks", dimension: int = 4) -> PostgresChunkStore:
    return PostgresChunkStore(_ExplodingPool(), table=table, dimension=dimension)  # type: ignore[arg-type]


def _chunk(
    *,
    chunk_id: str | None = None,
    tenant_id: Any = None,
    source_id: str = "doc-1",
    text: str = "some passage text",
    chunk_index: int = 0,
    entity_ids: list[Any] | None = None,
    embedding: list[float] | None = None,
    metadata: dict[str, Any] | None = None,
) -> StoredChunk:
    return StoredChunk(
        id=chunk_id if chunk_id is not None else derive_chunk_id(source_id, text),
        tenant_id=tenant_id or uuid4(),
        source_id=source_id,
        text=text,
        chunk_index=chunk_index,
        start_char=0,
        end_char=len(text),
        entity_ids=entity_ids or [],
        metadata=metadata or {},
        embedding=embedding,
    )


class TestConstruction:
    @pytest.mark.parametrize(
        "table",
        [
            pytest.param("kg chunks", id="space"),
            pytest.param("public.kg_chunks", id="schema-qualified"),
            pytest.param('kg_chunks"; DROP TABLE users; --', id="injection"),
            pytest.param("KgChunks", id="uppercase"),
            pytest.param("9lives", id="leading-digit"),
            pytest.param("", id="empty"),
            pytest.param("x" * 64, id="too-long"),
        ],
    )
    def test_a_table_name_that_is_not_a_bare_identifier_is_rejected(self, table: str):
        with pytest.raises(ValueError, match="identifier"):
            _store(table=table)

    @pytest.mark.parametrize("table", ["kg_chunks", "_c", "kg_chunks_test_gw0", "x" * 63])
    def test_a_bare_identifier_is_accepted(self, table: str):
        assert _store(table=table).table == table

    def test_the_table_name_reaches_every_statement(self):
        store = _store(table="kg_chunks_test_gw3")
        statements = [
            *store._schema_statements(),
            store._insert_sql(),
            store._replace_sql(),
            store._candidates_sql(),
            store._semantic_candidates_sql(),
            store._backfill_sql(),
        ]
        for statement in statements:
            assert "kg_chunks_test_gw3" in statement
            assert "kg_chunks " not in statement.replace("kg_chunks_test_gw3", "")

    @pytest.mark.parametrize("dimension", [0, -1])
    def test_a_non_positive_dimension_is_rejected(self, dimension: int):
        """Mirrors `InMemoryChunkStore` and `PgVectorStore`: a zero-dimension
        store accepts only the zero-length vector, which is also a zero
        vector, so nothing could ever be embedded into it."""
        with pytest.raises(ValueError, match="dimension"):
            _store(dimension=dimension)

    def test_dimension_is_exposed_and_reaches_the_column(self):
        store = _store(dimension=11)
        assert store.dimension == 11
        assert "vector(11)" in store._schema_statements()[0]
        assert "vector(11)" in " ".join(store._schema_statements())


class TestGuardsRunBeforeAnyIO:
    async def test_lexical_candidates_rejects_a_negative_limit(self):
        with pytest.raises(ValueError, match="limit"):
            await _store().lexical_candidates(["term"], uuid4(), -1)

    async def test_lexical_candidates_with_empty_terms_never_touches_the_store(self):
        """The port allows a zeroed-statistics answer without a round trip;
        this is the only place that promise is checked against something that
        would raise if a query were attempted."""
        result = await _store().lexical_candidates([], uuid4(), 10)
        assert result.candidates == []
        assert result.stats.n_docs == 0
        assert result.stats.doc_frequencies == {}

    async def test_an_empty_batch_writes_nothing(self):
        await _store().upsert_many([])

    async def test_replace_source_rejects_a_foreign_chunk_before_any_io(self):
        foreign = _chunk(source_id="other-doc")
        with pytest.raises(ValueError, match="source_id"):
            await _store().replace_source("doc-1", uuid4(), [foreign])

    async def test_upsert_many_rejects_a_zero_norm_embedding_before_any_io(self):
        zero = _chunk(embedding=[0.0, 0.0, 0.0, 0.0])
        with pytest.raises(ValueError, match="zero"):
            await _store().upsert_many([zero])

    async def test_replace_source_rejects_a_zero_norm_embedding_before_any_io(self):
        tenant = uuid4()
        zero = _chunk(tenant_id=tenant, embedding=[0.0, 0.0, 0.0, 0.0])
        with pytest.raises(ValueError, match="zero"):
            await _store().replace_source("doc-1", tenant, [zero])

    async def test_upsert_many_rejects_a_stored_embedding_of_the_wrong_width_before_any_io(self):
        from redstring.domain.exceptions import DimensionMismatchError

        narrow = _chunk(embedding=[1.0, 0.0, 0.0])
        with pytest.raises(DimensionMismatchError) as raised:
            await _store(dimension=4).upsert_many([narrow])
        assert raised.value.expected == 4
        assert raised.value.actual == 3

    async def test_replace_source_rejects_a_stored_embedding_of_the_wrong_width_before_any_io(
        self,
    ):
        from redstring.domain.exceptions import DimensionMismatchError

        tenant = uuid4()
        narrow = _chunk(tenant_id=tenant, embedding=[1.0, 0.0, 0.0])
        with pytest.raises(DimensionMismatchError) as raised:
            await _store(dimension=4).replace_source("doc-1", tenant, [narrow])
        assert raised.value.expected == 4
        assert raised.value.actual == 3

    async def test_semantic_candidates_rejects_a_negative_limit(self):
        with pytest.raises(ValueError, match="limit"):
            await _store().semantic_candidates([1.0, 0.0, 0.0, 0.0], uuid4(), -1)

    async def test_semantic_candidates_rejects_a_vector_of_the_wrong_width(self):
        from redstring.domain.exceptions import DimensionMismatchError

        with pytest.raises(DimensionMismatchError):
            await _store(dimension=4).semantic_candidates([1.0, 0.0, 0.0], uuid4(), 10)

    async def test_semantic_candidates_rejects_a_zero_norm_query_before_any_io(self):
        with pytest.raises(ValueError, match="zero"):
            await _store().semantic_candidates([0.0, 0.0, 0.0, 0.0], uuid4(), 10)

    async def test_semantic_candidates_with_a_zero_limit_never_touches_the_store(self):
        result = await _store().semantic_candidates([1.0, 0.0, 0.0, 0.0], uuid4(), 0)
        assert result == []


class TestSqlConstruction:
    def test_the_schema_has_a_terms_table_cascading_from_the_chunk_table(self):
        statements = _store()._schema_statements()
        joined = " ".join(statements)
        assert "kg_chunks_terms" in joined
        assert "ON DELETE CASCADE" in joined
        assert "PRIMARY KEY (tenant_id, chunk_id, term)" in joined

    def test_the_schema_indexes_entity_ids_and_terms(self):
        statements = _store()._schema_statements()
        joined = " ".join(statements)
        assert "USING gin (entity_ids)" in joined
        assert "kg_chunks_terms_term_idx" in joined

    def test_the_schema_is_idempotent_by_construction(self):
        for statement in _store()._schema_statements():
            assert "IF NOT EXISTS" in statement

    def test_doc_length_is_a_column_but_not_in_on_conflict(self):
        """`doc_length` is immutable per id -- see `_ON_CONFLICT`'s docstring.
        An omitted column there is otherwise this file's own documented
        defect shape, so the omission has to be deliberate and singular."""
        store = _store()
        assert "doc_length" in store._schema_statements()[0]
        assert "doc_length" in store._insert_sql()
        assert "doc_length" in store._replace_sql()

        on_conflict_start = store._insert_sql().index("ON CONFLICT")
        assert "doc_length" not in store._insert_sql()[on_conflict_start:]

    def test_the_insert_writes_terms_in_the_same_statement(self):
        sql = _store()._insert_sql()
        assert sql.count("INSERT INTO") == 2
        assert "kg_chunks_terms" in sql
        assert "ON CONFLICT (tenant_id, chunk_id, term) DO NOTHING" in sql
        assert sql.count("$2") >= 1

    def test_replace_source_stays_one_statement_including_terms(self):
        """The module docstring's whole argument: a crash between an upsert
        and a delete leaves a corpus that never existed. Splitting the term
        write into a second statement reintroduces exactly that."""
        sql = _store()._replace_sql()
        assert sql.strip().count(";") == 0
        assert "DELETE FROM kg_chunks" in sql
        assert "kg_chunks_terms" in sql
        assert "ON CONFLICT (tenant_id, chunk_id, term) DO NOTHING" in sql

    def test_replace_source_never_deletes_from_the_terms_table_directly(self):
        """Cascade does that job; a second delete here is the one this
        adapter is built to avoid needing."""
        sql = _store()._replace_sql()
        assert "DELETE FROM kg_chunks_terms" not in sql

    def test_get_by_entity_orders_by_the_ports_total_order(self):
        sql = inspect.getsource(PostgresChunkStore.get_by_entity)
        assert "ORDER BY source_id ASC, chunk_index ASC, id ASC" in sql
        # `@>` (containment), not `$2 = ANY (entity_ids)` (membership): GIN's
        # array operator class indexes the former and not the latter, so a
        # membership predicate here would plan as a tenant-wide scan with the
        # index maintained on every write and used by nothing. See
        # `tests/integration/chunks/test_postgres_store.py::test_get_by_entity_uses_the_gin_index`
        # for the plan assertion.
        assert "entity_ids @> ARRAY[$2::uuid]" in sql

    def test_the_candidates_query_limits_before_joining_the_wide_table(self):
        """The port's cost note: cutting after the join would pull every
        matching row's full text across the wire before discarding most of
        it."""
        sql = _store()._candidates_sql()
        assert sql.index("LIMIT $3") < sql.index("JOIN")

    def test_the_candidates_query_orders_by_the_ports_tie_break(self):
        sql = _store()._candidates_sql()
        assert "matched_terms DESC, chunk_id ASC" in sql

    def test_the_candidates_query_aggregates_term_frequencies_per_chunk(self):
        sql = _store()._candidates_sql()
        assert "jsonb_object_agg(term, tf)" in sql

    def test_the_schema_alters_an_existing_table_onto_the_current_columns(self):
        """`CREATE TABLE IF NOT EXISTS` adds nothing to a table that predates a column.

        B89: a `kg_chunks` created before the lexical or semantic work never
        got `doc_length` or `embedding`, and every query naming `_COLUMNS`
        fails against it. The ALTERs are the repair, and they ship with the
        columns that made them necessary.
        """
        statements = " ".join(_store()._schema_statements())
        assert "ADD COLUMN IF NOT EXISTS doc_length" in statements
        assert "ADD COLUMN IF NOT EXISTS embedding" in statements

    def test_the_similarity_expression_matches_the_vector_stores(self):
        """One definition of cosine similarity in this library, not two."""
        from redstring.vector.adapters.pgvector import _SCORE as VECTOR_SCORE

        assert _SCORE == VECTOR_SCORE

    def test_encode_vector_matches_the_vector_stores(self):
        """`encode_vector`'s own docstring claims this duplicate is proved
        identical the way `_SCORE` is -- by a test, not by sharing code, since
        `chunks` and `vector` are forbidden siblings. This is that test: it
        was missing, so the claim was unenforced rather than false, which is
        `recurring-defects.md` shape (g) -- a comment asserting an invariant
        nothing held to it. Compared by body, not by docstring, since the two
        modules deliberately give the duplicate different surrounding prose.
        """
        import ast
        import inspect

        from redstring.vector.adapters.pgvector import encode_vector as vector_encode_vector

        def body_source(func: object) -> str:
            tree = ast.parse(inspect.getsource(func))
            [function_def] = tree.body
            assert isinstance(function_def, ast.FunctionDef)
            body = function_def.body
            # Drop a leading docstring expression statement, which the two
            # copies are expected to state differently.
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                body = body[1:]
            return "\n".join(ast.unparse(statement) for statement in body)

        assert body_source(chunk_encode_vector) == body_source(vector_encode_vector)

    def test_embedding_is_a_column_but_not_in_on_conflict(self):
        """Same reasoning as `doc_length`: written once per content-addressed
        id and never updated on conflict; see `_ON_CONFLICT`'s docstring."""
        store = _store()
        assert "embedding" in store._schema_statements()[0]
        assert "embedding" in store._insert_sql()
        assert "embedding" in store._replace_sql()

        on_conflict_start = store._insert_sql().index("ON CONFLICT")
        assert "embedding" not in store._insert_sql()[on_conflict_start:]

    def test_semantic_candidates_query_limits_before_joining_the_wide_table(self):
        sql = _store()._semantic_candidates_sql()
        assert sql.index("LIMIT $4") < sql.index("JOIN")

    def test_semantic_candidates_query_filters_unembedded_chunks(self):
        sql = _store()._semantic_candidates_sql()
        assert "embedding IS NOT NULL" in sql

    def test_semantic_candidates_query_applies_min_score_before_limit(self):
        sql = _store()._semantic_candidates_sql()
        where_clause = sql[sql.index("WHERE") : sql.index("ORDER BY")]
        assert "$3" in where_clause

    def test_semantic_candidates_query_orders_by_the_ports_tie_break(self):
        sql = _store()._semantic_candidates_sql()
        assert "score DESC, id ASC" in sql
        assert "score DESC, m.chunk_id ASC" in sql


class TestEncoding:
    def test_encode_includes_computed_doc_length(self):
        chunk = _chunk(text="one two three")
        [row] = json.loads(encode([chunk]))
        assert row["doc_length"] == 3

    def test_encode_renders_an_embedding_as_pgvector_text_form(self):
        chunk = _chunk(embedding=[1.0, -2.5, 0.0, 3.0])
        [row] = json.loads(encode([chunk]))
        assert row["embedding"] == "[1.0,-2.5,0.0,3.0]"

    def test_encode_of_an_unembedded_chunk_is_null(self):
        chunk = _chunk()
        assert chunk.embedding is None
        [row] = json.loads(encode([chunk]))
        assert row["embedding"] is None

    def test_encode_terms_produces_one_row_per_distinct_term(self):
        chunk = _chunk(text="alpha alpha beta")
        rows = json.loads(encode_terms([chunk]))
        by_term = {row["term"]: row["tf"] for row in rows}
        assert by_term == {"alpha": 2, "beta": 1}

    def test_encode_terms_scopes_each_row_to_its_chunk_and_tenant(self):
        tenant = uuid4()
        chunk = _chunk(tenant_id=tenant, text="only term")
        rows = json.loads(encode_terms([chunk]))
        assert all(row["chunk_id"] == chunk.id for row in rows)
        assert all(row["tenant_id"] == str(tenant) for row in rows)

    def test_encode_terms_of_an_empty_batch_is_empty(self):
        assert json.loads(encode_terms([])) == []

    def test_encode_terms_agrees_with_domain_tokenize(self):
        """The whole point of the split: this adapter's index and the
        in-memory adapter's on-the-fly tokenization must derive from the same
        function, not two implementations of "what is a term"."""
        from redstring.domain.tokenize import tokenize

        chunk = _chunk(text="Running dogs, running FAST.")
        rows = json.loads(encode_terms([chunk]))
        terms = {row["term"] for row in rows}
        assert terms == set(tokenize(chunk.text))


class TestDeduplicate:
    def test_a_repeated_key_keeps_the_last(self):
        """Same `(tenant_id, id)` key, distinguished by `metadata` -- which
        does not affect the content-addressed id, so both rows are genuinely
        the same key with different payloads, the case the function exists
        to resolve."""
        tenant = uuid4()
        first = _chunk(tenant_id=tenant, text="repeated", metadata={"version": "first"})
        second = _chunk(tenant_id=tenant, text="repeated", metadata={"version": "second"})
        assert deduplicate([first, second]) == [second]

    def test_the_key_is_the_pair_not_the_id_alone(self):
        """Same source and text -- hence the same content-addressed id --
        under two different tenants. The pair is the key, not the id alone."""
        one = _chunk(tenant_id=uuid4(), text="shared")
        two = _chunk(tenant_id=uuid4(), text="shared")
        assert one.id == two.id
        assert deduplicate([one, two]) == [one, two]

    def test_an_empty_batch_stays_empty(self):
        assert deduplicate([]) == []


class TestStructure:
    def test_the_adapter_satisfies_the_port(self):
        assert isinstance(_store(), ChunkStore)

    @pytest.mark.parametrize(
        "method",
        [
            "upsert_many",
            "get",
            "get_by_source",
            "replace_source",
            "lexical_candidates",
            "semantic_candidates",
            "get_by_entity",
            "delete_by_source",
            "delete_by_tenant",
        ],
    )
    def test_signatures_match_the_port(self, method: str):
        port = inspect.signature(getattr(ChunkStore, method))
        implementation = inspect.signature(getattr(PostgresChunkStore, method))
        assert [(p.name, p.kind, p.default) for p in port.parameters.values()] == [
            (p.name, p.kind, p.default) for p in implementation.parameters.values()
        ]

    def test_the_in_memory_adapter_has_the_same_signatures(self):
        from redstring.chunks.adapters.memory import InMemoryChunkStore

        for method in (
            "upsert_many",
            "get",
            "get_by_source",
            "replace_source",
            "lexical_candidates",
            "semantic_candidates",
            "get_by_entity",
            "delete_by_source",
            "delete_by_tenant",
        ):
            assert inspect.signature(getattr(PostgresChunkStore, method)) == inspect.signature(
                getattr(InMemoryChunkStore, method)
            )


#: Postgres-specific syntax, distinctive enough that finding it outside the
#: adapter means storage detail has leaked upward. See the equivalent list in
#: `test_pgvector_adapter_is_wired.py` for why plain `SELECT` is too common
#: to use here.
CHUNK_STORE_MARKERS = ("jsonb_to_recordset", "jsonb_object_agg", "USING gin")

#: **Empty, and staying that way.** See `LEGACY_PGVECTOR` in
#: `test_pgvector_adapter_is_wired.py` for why this is kept rather than
#: deleted with its last entry.
LEGACY_CHUNK_STORE: frozenset[str] = frozenset()

SOURCE_ROOT = Path(adapter.__file__).parent.parent.parent


class TestChunkStoreSyntaxDoesNotLeak:
    def _offenders(self) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for path in SOURCE_ROOT.rglob("*.py"):
            relative = path.relative_to(SOURCE_ROOT).as_posix()
            if relative == "chunks/adapters/postgres.py" or relative in LEGACY_CHUNK_STORE:
                continue
            text = path.read_text(encoding="utf-8")
            hits = [marker for marker in CHUNK_STORE_MARKERS if marker in text]
            if hits:
                found[relative] = hits
        return found

    def test_no_module_outside_the_adapter_speaks_postgres_chunk_syntax(self):
        assert self._offenders() == {}

    def test_the_detector_would_notice(self):
        text = (SOURCE_ROOT / "chunks/adapters/postgres.py").read_text(encoding="utf-8")
        assert [marker for marker in CHUNK_STORE_MARKERS if marker in text]

    def test_the_exemption_list_has_no_stale_entries(self):
        for relative in LEGACY_CHUNK_STORE:
            assert (SOURCE_ROOT / relative).exists()
