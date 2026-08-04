"""Default-gate checks on the pgvector adapter that need no Postgres.

**Why this file exists.** The adapter's behavioural tests are
`integration`-marked, so `addopts` deselects all 61 of them and the whole
module would go unexecuted in the commit gate. Slice 4 shipped a Neo4j adapter
in exactly that state and a cosmic-ray mutant left in its source passed the
entire suite -- 2026 tests green over corrupt code -- because not one line of
it ran. That is a property of the marking decision, not of that mutant, and it
applies here identically.

These tests run every part of the adapter that does not need a server:

- **Argument validation.** Every guard clause raises before the first
  statement, so an `_ExplodingPool` proves both the rejection and that no I/O
  happened.
- **SQL construction.** The DDL, the batch insert and the search statement are
  built by methods that take no connection, so what they emit can be asserted
  directly -- including the things a passing behavioural test cannot see, like
  `WHERE` preceding `LIMIT` and the batch insert being one statement.
- **Encoding.** `encode_vector`, `entity_type_of` and `deduplicate` are pure
  and are where the adapter does its fiddly work.
- **Structure.** Importing the module means a syntax error, a bad name or an
  import cycle fails the default run; signatures are checked against the port;
  and SQL is checked not to have leaked out of the adapter.

What still needs the container is everything that requires the SQL to
*execute*: the queries, the DDL, tenant isolation, ranking and the query
plans. That is `tests/integration/vector/test_pgvector_store.py`, and it is
**not** duplicated here with mocks, which would only assert what the mock was
told to say.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from kg_builder.domain.exceptions import DimensionMismatchError
from kg_builder.domain.vector import VectorRecord
from kg_builder.ports.vector_store import VectorStore
from kg_builder.vector.adapters import pgvector as adapter
from kg_builder.vector.adapters.pgvector import (
    PgVectorStore,
    deduplicate,
    encode_vector,
    entity_type_of,
)

DIMENSION = 8


class _ExplodingPool:
    """A pool that fails on any use.

    Not a mock of Postgres: nothing here asserts what SQL was sent to it. It
    exists so that "the guard raised" and "the guard raised *before any
    I/O*" are the same assertion -- a validation check that runs after the
    statement is a validation check that does not work.
    """

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the adapter reached the database via {name!r} before validating")


def _store(*, dimension: int = DIMENSION, table: str = "kg_vectors") -> PgVectorStore:
    return PgVectorStore(_ExplodingPool(), dimension=dimension, table=table)  # type: ignore[arg-type]


def _vector(*, length: int = DIMENSION) -> list[float]:
    return [1.0, *([0.0] * (length - 1))] if length else []


class TestConstruction:
    def test_dimension_must_be_positive(self):
        for bad in (0, -1):
            with pytest.raises(ValueError, match="dimension"):
                _store(dimension=bad)

    def test_the_dimension_is_reported(self):
        assert _store(dimension=768).dimension == 768

    @pytest.mark.parametrize(
        "table",
        [
            pytest.param("kg vectors", id="space"),
            pytest.param("public.kg_vectors", id="schema-qualified"),
            pytest.param('kg_vectors"; DROP TABLE users; --', id="injection"),
            pytest.param("KgVectors", id="uppercase"),
            pytest.param("9lives", id="leading-digit"),
            pytest.param("", id="empty"),
            pytest.param("x" * 64, id="too-long"),
        ],
    )
    def test_a_table_name_that_is_not_a_bare_identifier_is_rejected(self, table: str):
        """The name is interpolated into SQL -- Postgres has no parameter form
        for an identifier -- so it is proved safe rather than escaped."""
        with pytest.raises(ValueError, match="identifier"):
            _store(table=table)

    @pytest.mark.parametrize("table", ["kg_vectors", "_v", "kg_vectors_test_gw0", "x" * 63])
    def test_a_bare_identifier_is_accepted(self, table: str):
        assert _store(table=table).table == table

    def test_the_table_name_reaches_every_statement(self):
        """A statement left on the default table would write to the wrong place
        while every other test, which uses the default, stayed green."""
        store = _store(table="kg_vectors_test_gw3")
        statements = [
            *store._schema_statements(),
            store._insert_sql(),
            store._search_sql(),
        ]
        for statement in statements:
            assert "kg_vectors_test_gw3" in statement
            assert "kg_vectors " not in statement.replace("kg_vectors_test_gw3", "")


class TestGuardsRunBeforeAnyIO:
    """Each of these reaches the database if the guard is removed."""

    async def test_upsert_rejects_the_wrong_dimension(self):
        with pytest.raises(DimensionMismatchError) as raised:
            await _store().upsert(uuid4(), [1.0, 2.0], uuid4())
        assert raised.value.expected == DIMENSION
        assert raised.value.actual == 2

    async def test_upsert_many_rejects_the_wrong_dimension(self):
        record = VectorRecord(entity_id=uuid4(), tenant_id=uuid4(), vector=[1.0, 2.0])
        with pytest.raises(DimensionMismatchError):
            await _store().upsert_many([record])

    async def test_search_rejects_the_wrong_dimension(self):
        with pytest.raises(DimensionMismatchError):
            await _store().search([1.0, 2.0], uuid4())

    async def test_zero_vectors_are_rejected(self):
        zeroes = [0.0] * DIMENSION
        with pytest.raises(ValueError, match="zero"):
            await _store().upsert(uuid4(), zeroes, uuid4())
        with pytest.raises(ValueError, match="zero"):
            await _store().search(zeroes, uuid4())

    async def test_a_negative_k_is_rejected(self):
        with pytest.raises(ValueError, match="k"):
            await _store().search(_vector(), uuid4(), k=-1)

    async def test_k_zero_answers_without_asking(self):
        """`LIMIT 0` would be correct; not asking is cheaper, and the port
        promises `[]` regardless of what the tenant holds."""
        assert await _store().search(_vector(), uuid4(), k=0) == []

    async def test_an_empty_batch_writes_nothing(self):
        await _store().upsert_many([])


class TestSqlConstruction:
    """What the statements say, asserted without executing them.

    These are the properties a passing behavioural test cannot distinguish
    from a slower or subtly wrong implementation.
    """

    def test_the_batch_insert_is_one_statement(self):
        """`unnest` over five arrays, not a loop and not a `VALUES` list whose
        length grows with the batch. Embedding batches are thousands of rows."""
        sql = _store()._insert_sql()
        assert sql.count("INSERT INTO") == 1
        assert "unnest(" in sql
        # Exactly five parameters, one array each: nothing scales with the
        # batch. A per-row `VALUES` list would need `$6` and beyond.
        assert sql.count("$5") == 1
        assert "$6" not in sql

    def test_the_batch_insert_upserts_rather_than_failing(self):
        sql = _store()._insert_sql()
        assert "ON CONFLICT (tenant_id, entity_id) DO UPDATE" in sql
        # Wholesale replacement, not `metadata || EXCLUDED.metadata`: a merge
        # would let a key removed by a later event survive, which makes replay
        # order-dependent.
        assert "metadata = EXCLUDED.metadata" in sql
        assert "||" not in sql

    def test_the_search_filters_before_it_limits(self):
        """`WHERE` ahead of `ORDER BY` ahead of `LIMIT`, in that order.

        The port's most important rule: taking `k` first and filtering after
        returns fewer than `k` while matching rows exist further down, which
        is indistinguishable from a tenant with little data.
        """
        sql = _store()._search_sql()
        assert sql.index("WHERE") < sql.index("ORDER BY") < sql.index("LIMIT")

    def test_the_search_scores_with_cosine_distance_the_right_way_round(self):
        """`<=>` is cosine *distance*, so the score is `1 - distance / 2`.

        Written down here as well as executed against a server, because an
        inversion is the bug that returns plausible nonsense rather than an
        error, and this is the only test of it that runs in the commit gate.
        """
        assert adapter._SCORE == "1 - (embedding <=> $2::vector) / 2"
        assert adapter._SCORE in _store()._search_sql()

    def test_the_search_orders_by_score_then_id(self):
        sql = _store()._search_sql()
        assert "ORDER BY score DESC, entity_id::text ASC" in sql

    def test_the_search_is_tenant_scoped_and_type_filtered_in_sql(self):
        sql = _store()._search_sql()
        assert "WHERE tenant_id = $1" in sql
        # `$3` carries "no type filter", so an empty `$4` means "nothing
        # matches" rather than "everything" -- the natural `if entity_types:`
        # bug, moved into SQL where it cannot be written by accident.
        assert "($3 OR entity_type = ANY($4::text[]))" in sql

    def test_the_schema_keys_on_the_pair_and_indexes_no_embedding(self):
        statements = _store()._schema_statements()
        table = statements[0]
        assert "PRIMARY KEY (tenant_id, entity_id)" in table
        assert f"vector({DIMENSION})" in table
        # No `USING hnsw` or `USING ivfflat` anywhere: an ANN index would let
        # the planner take the k globally nearest rows and drop other tenants'
        # afterwards. See the adapter's module docstring.
        joined = " ".join(statements)
        assert "hnsw" not in joined
        assert "ivfflat" not in joined

    def test_the_schema_is_idempotent_by_construction(self):
        """`ensure_schema` runs on every connect."""
        for statement in _store()._schema_statements():
            assert "IF NOT EXISTS" in statement

    def test_the_declared_dimension_reaches_the_column_type(self):
        assert "vector(768)" in _store(dimension=768)._schema_statements()[0]


class TestEncoding:
    def test_a_vector_renders_as_pgvectors_input_form(self):
        assert encode_vector([1.0, -2.5, 0.0]) == "[1.0,-2.5,0.0]"

    def test_encoding_does_not_shorten_a_float(self):
        """`str` would round; the value must reach Postgres intact.

        pgvector's *output* is lossy at seven significant digits, which is why
        reads cast to `real[]` instead of parsing text -- the asymmetry is
        deliberate and is documented on `encode_vector`.
        """
        awkward = 0.1 + 0.2  # 0.30000000000000004
        assert float(encode_vector([awkward])[1:-1]) == awkward

    def test_integers_are_rendered_as_floats(self):
        """A `Sequence[float]` may legitimately hold `int`s in Python."""
        assert encode_vector([1, 2]) == "[1.0,2.0]"

    def test_an_empty_vector_is_representable(self):
        """Never written -- the guards reject it -- but the function must not
        produce `[]` with a stray comma if it ever is."""
        assert encode_vector([]) == "[]"

    def test_entity_type_is_taken_from_the_metadata(self):
        assert entity_type_of({"entity_type": "person"}) == "person"

    @pytest.mark.parametrize(
        "metadata",
        [
            pytest.param({}, id="absent"),
            pytest.param({"entity_type": None}, id="null"),
            pytest.param({"entity_type": 7}, id="int"),
            pytest.param({"entity_type": True}, id="bool"),
            pytest.param({"entity_type": ["person"]}, id="list"),
            pytest.param({"Entity_Type": "person"}, id="wrong-case"),
        ],
    )
    def test_a_non_string_entity_type_becomes_null(self, metadata: dict[str, Any]):
        """The column is `text`.

        Coercing `7` to `"7"` would invent a match the in-memory adapter --
        which compares the raw value -- would never make, so the two adapters
        would answer the same `entity_types=["7"]` query differently.
        """
        assert entity_type_of(metadata) is None

    def test_the_metadata_key_is_the_ports_constant(self):
        """A literal here would drift from the port silently."""
        from kg_builder.ports.vector_store import ENTITY_TYPE_KEY

        assert entity_type_of({ENTITY_TYPE_KEY: "person"}) == "person"


class TestDeduplicate:
    """`ON CONFLICT DO UPDATE` raises if one statement touches a row twice."""

    def test_a_repeated_key_keeps_the_last(self):
        entity_id, tenant = uuid4(), uuid4()

        def record(value: int) -> VectorRecord:
            return VectorRecord(
                entity_id=entity_id, tenant_id=tenant, vector=[float(value)], metadata={"n": value}
            )

        first, second = record(1), record(2)

        assert deduplicate([first, second]) == [second]

    def test_the_key_is_the_ordered_pair(self):
        """`(x, y)` and `(y, x)` are different rows.

        A key built from an unordered pair, a `frozenset`, or `hash(a) ^
        hash(b)` collapses these two into one and passes every other test in
        this file. When a key is a tuple, something has to make its components
        collide.
        """
        x, y = uuid4(), uuid4()
        forward = VectorRecord(entity_id=y, tenant_id=x, vector=[1.0])
        reversed_ = VectorRecord(entity_id=x, tenant_id=y, vector=[2.0])

        assert deduplicate([forward, reversed_]) == [forward, reversed_]

    def test_one_entity_id_under_two_tenants_survives_as_two_rows(self):
        entity_id = uuid4()
        one = VectorRecord(entity_id=entity_id, tenant_id=uuid4(), vector=[1.0])
        two = VectorRecord(entity_id=entity_id, tenant_id=uuid4(), vector=[2.0])

        assert deduplicate([one, two]) == [one, two]

    def test_order_is_otherwise_preserved(self):
        records = [
            VectorRecord(entity_id=uuid4(), tenant_id=uuid4(), vector=[float(n)]) for n in range(4)
        ]
        assert deduplicate(records) == records

    def test_an_empty_batch_stays_empty(self):
        assert deduplicate([]) == []


class TestStructure:
    def test_the_adapter_satisfies_the_port(self):
        assert isinstance(_store(), VectorStore)

    @pytest.mark.parametrize(
        "method", ["upsert", "upsert_many", "get", "search", "delete", "delete_by_tenant"]
    )
    def test_signatures_match_the_port(self, method: str):
        """Keyword-only arguments and defaults included.

        A `k` that defaulted to 5 here would satisfy `isinstance` against a
        `runtime_checkable` Protocol, which only checks that the names exist.
        """
        port = inspect.signature(getattr(VectorStore, method))
        implementation = inspect.signature(getattr(PgVectorStore, method))
        assert [(p.name, p.kind, p.default) for p in port.parameters.values()] == [
            (p.name, p.kind, p.default) for p in implementation.parameters.values()
        ]

    def test_the_in_memory_adapter_has_the_same_signatures(self):
        """Two adapters agreeing with the port is not the same as agreeing
        with each other only where the port was checked."""
        from kg_builder.vector.adapters.memory import InMemoryVectorStore

        for name in ("upsert", "upsert_many", "get", "search", "delete", "delete_by_tenant"):
            assert inspect.signature(getattr(PgVectorStore, name)) == inspect.signature(
                getattr(InMemoryVectorStore, name)
            )


#: pgvector-specific syntax, distinctive enough that finding it outside the
#: adapter means storage detail has leaked upward. `SELECT` alone would be far
#: too common: this repo still has a whole relational layer awaiting deletion
#: in slice 9, and a test that fires on ordinary SQL would be noise rather than
#: a signal.
#: `" vector("` carries the leading space on purpose: it is the SQL column
#: type `embedding vector(8)`, and without the space it would also match every
#: Python call ending in `_vector(`.
PGVECTOR_MARKERS = ("<=>", "::vector", " vector(", "Vector(")

#: The pre-rewrite embedding column, condemned by the migration plan and
#: deleted in slice 9. Exempt because it is already on the way out, not
#: because the rule does not apply to it.
#:
#: **This list may only shrink.** Delete the entry in the commit that deletes
#: its module; `test_the_exemption_list_has_no_stale_entries` fails if it names
#: a file that is gone, and the main test fails if a *new* module grows
#: pgvector syntax. Between them, the only way to add pgvector detail outside
#: the adapter is to edit this list, which is a visible decision in review.
LEGACY_PGVECTOR = frozenset({"models/extracted_entity.py"})

SOURCE_ROOT = Path(adapter.__file__).parent.parent.parent


class TestPgVectorSyntaxDoesNotLeak:
    """The port must not become pgvector-shaped.

    The whole value of a port is that callers above it do not know which
    backend they have. A `<=>` in a service is that guarantee quietly ending.
    """

    def _offenders(self) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for path in SOURCE_ROOT.rglob("*.py"):
            relative = path.relative_to(SOURCE_ROOT).as_posix()
            if relative == "vector/adapters/pgvector.py" or relative in LEGACY_PGVECTOR:
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
        """Guard the guard: a marker list that matches nothing passes
        vacuously, so prove it fires on the adapter itself."""
        text = (SOURCE_ROOT / "vector/adapters/pgvector.py").read_text(encoding="utf-8")
        assert [marker for marker in PGVECTOR_MARKERS if marker in text]

    def test_the_exemption_list_has_no_stale_entries(self):
        for relative in LEGACY_PGVECTOR:
            assert (SOURCE_ROOT / relative).exists(), (
                f"{relative} is gone; delete its entry from LEGACY_PGVECTOR"
            )
