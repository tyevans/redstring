"""Identifier checks for the Postgres chunk adapter's SQL helpers, with no server.

Each module-level helper that formats a table name must refuse a hostile name
on its own. A test that goes through the store constructor would miss a helper
that a direct caller can reach without it. The pool used here fails on any
attribute access, so a refusal proves the check ran before any I/O.

The length tests read the names back out of the statement text. They do not
recompute them from the suffix constants, so a suffix that the SQL uses but the
limit does not count is still caught.
"""

from __future__ import annotations

import re
from typing import Any, cast
from uuid import uuid4

import pytest

from redstring.chunks.adapters.postgres import PostgresChunkStore
from redstring.chunks.adapters.postgres.postgres_identifiers import (
    MAX_TABLE_NAME_BYTES,
    POSTGRES_IDENTIFIER_BYTES,
    check_table,
)
from redstring.chunks.adapters.postgres.postgres_search import (
    backfill_lexical,
    search_lexical,
    search_semantic,
)
from redstring.chunks.adapters.postgres.postgres_sql import (
    backfill_sql,
    candidates_sql,
    insert_sql,
    replace_sql,
    schema_statements,
    semantic_candidates_sql,
)

#: A name that would inject SQL if it were written into statement text unchecked.
HOSTILE = "x; DROP TABLE y"


class _ExplodingPool:
    """A pool that fails on any use, so a refusal is proved to precede I/O."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the helper reached the database via {name!r} before validating")


#: Matches the object name in each `CREATE TABLE` and `CREATE INDEX` statement.
_CREATED = re.compile(r"CREATE (?:TABLE|INDEX) IF NOT EXISTS (\S+)")

#: The longest suffix the chunk adapter appends to a base name, written out here
#: rather than read from the module. Pinning it is what makes a new, longer
#: suffix change `MAX_TABLE_NAME_BYTES` on purpose.
_LONGEST_SUFFIX = "_tenant_source_idx"


class TestEachHelperRefusesAHostileName:
    @pytest.mark.parametrize(
        "helper",
        [
            pytest.param(lambda table: schema_statements(table, 4), id="schema_statements"),
            pytest.param(insert_sql, id="insert_sql"),
            pytest.param(replace_sql, id="replace_sql"),
            pytest.param(candidates_sql, id="candidates_sql"),
            pytest.param(semantic_candidates_sql, id="semantic_candidates_sql"),
            pytest.param(backfill_sql, id="backfill_sql"),
        ],
    )
    def test_a_statement_builder_refuses_a_hostile_name(self, helper: Any):
        with pytest.raises(ValueError, match="identifier"):
            helper(HOSTILE)

    async def test_search_lexical_refuses_a_hostile_name(self):
        with pytest.raises(ValueError, match="identifier"):
            await search_lexical(
                cast("Any", _ExplodingPool()), HOSTILE, ["term"], uuid4(), 1, lambda row: row
            )

    async def test_search_semantic_refuses_a_hostile_name(self):
        with pytest.raises(ValueError, match="identifier"):
            await search_semantic(
                cast("Any", _ExplodingPool()), HOSTILE, [1.0], uuid4(), 1, 1, lambda row: row
            )

    async def test_backfill_lexical_refuses_a_hostile_name(self):
        with pytest.raises(ValueError, match="identifier"):
            await backfill_lexical(cast("Any", _ExplodingPool()), HOSTILE)


class TestTheLengthLimit:
    def test_the_limit_is_the_identifier_limit_less_the_longest_suffix(self):
        assert POSTGRES_IDENTIFIER_BYTES == 63
        assert len(_LONGEST_SUFFIX) == 18
        assert MAX_TABLE_NAME_BYTES == 45

    def test_a_name_at_the_limit_is_accepted(self):
        check_table("x" * MAX_TABLE_NAME_BYTES)

    def test_a_name_one_byte_over_the_limit_is_refused(self):
        with pytest.raises(ValueError, match="identifier"):
            check_table("x" * (MAX_TABLE_NAME_BYTES + 1))

    def test_the_store_accepts_the_limit_and_refuses_one_more(self):
        store = PostgresChunkStore(
            cast("Any", _ExplodingPool()), table="x" * MAX_TABLE_NAME_BYTES, dimension=4
        )
        assert store.table == "x" * MAX_TABLE_NAME_BYTES
        with pytest.raises(ValueError, match="identifier"):
            PostgresChunkStore(
                cast("Any", _ExplodingPool()),
                table="x" * (MAX_TABLE_NAME_BYTES + 1),
                dimension=4,
            )

    def test_one_byte_more_than_the_limit_overflows_the_longest_derived_name(self):
        """Shows the limit is tight: the longest name would be 64 bytes, not 63."""
        over = "x" * (MAX_TABLE_NAME_BYTES + 1)
        assert len(over) + len(_LONGEST_SUFFIX) > POSTGRES_IDENTIFIER_BYTES


class TestDerivedNamesOfAMaximumLengthTable:
    def test_every_derived_name_fits_in_the_identifier_limit(self):
        base = "x" * MAX_TABLE_NAME_BYTES
        created = [m.group(1) for s in schema_statements(base, 4) for m in _CREATED.finditer(s)]
        # Guard the detector: the schema creates the table, two indexes, the
        # term table and the term index. A detector that matched nothing would
        # pass the loop below vacuously.
        assert len(created) == 5
        assert len(set(created)) == len(created)
        for name in created:
            assert len(name.encode()) <= POSTGRES_IDENTIFIER_BYTES, name

    def test_the_longest_derived_name_is_exactly_at_the_limit(self):
        base = "x" * MAX_TABLE_NAME_BYTES
        created = [m.group(1) for s in schema_statements(base, 4) for m in _CREATED.finditer(s)]
        assert max(len(name.encode()) for name in created) == POSTGRES_IDENTIFIER_BYTES
