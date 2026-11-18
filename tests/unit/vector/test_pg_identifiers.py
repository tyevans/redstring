"""Identifier checks for the pgvector adapter's SQL helpers, with no server.

Each module-level helper that formats a table name must refuse a hostile name
on its own. A test that goes through the store constructor would miss a helper
that a direct caller can reach without it. The connection used here fails on
any attribute access, so a refusal proves the check ran before any I/O.

The length tests read the names back out of the statement text. They do not
recompute them from the suffix constants, so a suffix that the SQL uses but the
limit does not count is still caught.
"""

from __future__ import annotations

import re
from typing import Any, cast

import pytest

from redstring.vector.adapters.pg_identifiers import (
    MAX_TABLE_NAME_BYTES,
    POSTGRES_IDENTIFIER_BYTES,
    check_table,
)
from redstring.vector.provenance import ensure_pgvector_provenance, provenance_table_ddl

from .conftest import make_store

#: A name that would inject SQL if it were written into statement text unchecked.
HOSTILE = "x; DROP TABLE y"


class _ExplodingConnection:
    """A connection that fails on any use, so a refusal is proved to precede I/O."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the helper reached the database via {name!r} before validating")


#: Matches the object name in each `CREATE TABLE` and `CREATE INDEX` statement.
_CREATED = re.compile(r"CREATE (?:TABLE|INDEX) IF NOT EXISTS (\S+)")

#: The longest suffix the vector adapter appends to a base name, written out
#: here rather than read from the module. Pinning it is what makes a new, longer
#: suffix change `MAX_TABLE_NAME_BYTES` on purpose.
_LONGEST_SUFFIX = "_tenant_type_idx"


class TestEachHelperRefusesAHostileName:
    def test_provenance_table_ddl_refuses_a_hostile_name(self):
        with pytest.raises(ValueError, match="identifier"):
            provenance_table_ddl(HOSTILE)

    async def test_ensure_pgvector_provenance_refuses_a_hostile_name(self):
        with pytest.raises(ValueError, match="identifier"):
            await ensure_pgvector_provenance(
                cast("Any", _ExplodingConnection()),
                HOSTILE,
                dimension=4,
                model=None,
                document_prefix=None,
            )

    def test_the_store_constructor_refuses_a_hostile_name(self):
        with pytest.raises(ValueError, match="identifier"):
            make_store(table=HOSTILE)


class TestTheLengthLimit:
    def test_the_limit_is_the_identifier_limit_less_the_longest_suffix(self):
        assert POSTGRES_IDENTIFIER_BYTES == 63
        assert len(_LONGEST_SUFFIX) == 16
        assert MAX_TABLE_NAME_BYTES == 47

    def test_a_name_at_the_limit_is_accepted(self):
        check_table("x" * MAX_TABLE_NAME_BYTES)

    def test_a_name_one_byte_over_the_limit_is_refused(self):
        with pytest.raises(ValueError, match="identifier"):
            check_table("x" * (MAX_TABLE_NAME_BYTES + 1))

    def test_the_store_accepts_the_limit_and_refuses_one_more(self):
        assert make_store(table="x" * MAX_TABLE_NAME_BYTES).table == "x" * MAX_TABLE_NAME_BYTES
        with pytest.raises(ValueError, match="identifier"):
            make_store(table="x" * (MAX_TABLE_NAME_BYTES + 1))

    def test_one_byte_more_than_the_limit_overflows_the_longest_derived_name(self):
        """Shows the limit is tight: the longest name would be 64 bytes, not 63."""
        over = "x" * (MAX_TABLE_NAME_BYTES + 1)
        assert len(over) + len(_LONGEST_SUFFIX) > POSTGRES_IDENTIFIER_BYTES


class TestDerivedNamesOfAMaximumLengthTable:
    def _created_names(self) -> list[str]:
        base = "x" * MAX_TABLE_NAME_BYTES
        # The schema already includes the provenance DDL, so it is not added again.
        statements = list(make_store(table=base)._schema_statements())
        return [m.group(1) for s in statements for m in _CREATED.finditer(s)]

    def test_every_derived_name_fits_in_the_identifier_limit(self):
        created = self._created_names()
        # Guard the detector: the schema creates the table, the tenant index, and
        # the provenance table. A detector that matched nothing would pass the
        # loop below vacuously.
        assert len(created) == 3
        assert len(set(created)) == len(created)
        for name in created:
            assert len(name.encode()) <= POSTGRES_IDENTIFIER_BYTES, name

    def test_the_longest_derived_name_is_exactly_at_the_limit(self):
        longest = max(len(name.encode()) for name in self._created_names())
        assert longest == POSTGRES_IDENTIFIER_BYTES
