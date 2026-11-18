"""Identifier checks for the pgvector adapter's SQL text.

Postgres has no parameter form for an identifier, so the table name is written
into statement text. This module decides which names may be written. The
pgvector store and the provenance helpers both call it before they format a
name, so a direct caller of either meets the same rule as a caller of the store.

The chunk adapter keeps its own copy in `redstring.chunks.adapters.postgres`.
The chunk and vector adapters are sibling layers and may not import each
other. The copies are kept equal by tests on each side.
"""

from __future__ import annotations

import re

#: Postgres truncates an identifier longer than this many bytes (`NAMEDATALEN - 1`).
#: It truncates an explicit index name silently. Two derived names that share a
#: 63-byte prefix then collide, and `CREATE INDEX IF NOT EXISTS` skips the second
#: one with no error.
POSTGRES_IDENTIFIER_BYTES = 63

#: The suffixes this adapter appends to a base table name. Every statement that
#: builds a derived name uses these constants, so the length limit below is
#: computed from the same declaration the SQL uses. A longer suffix added here
#: tightens `MAX_TABLE_NAME_BYTES` with no other edit.
#:
#: Postgres names the primary key itself, and picks a name it has not used, so
#: that name is not declared here.
TENANT_TYPE_IDX = "_tenant_type_idx"
PROVENANCE = "_provenance"
_DERIVED_SUFFIXES = (TENANT_TYPE_IDX, PROVENANCE)

#: The longest base table name whose longest derived name still fits.
MAX_TABLE_NAME_BYTES = POSTGRES_IDENTIFIER_BYTES - max(len(s) for s in _DERIVED_SUFFIXES)

_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")


def check_table(table: str) -> None:
    """Refuse a table name that is not safe to write into SQL text.

    The name must be a bare lowercase identifier: no quoting and no schema
    prefix. It must also be at most `MAX_TABLE_NAME_BYTES` long, so every derived
    name stays within Postgres's limit.

    Raises:
        ValueError: `table` is not a bare identifier, or is too long.
    """
    if not _IDENTIFIER.fullmatch(table) or len(table) > MAX_TABLE_NAME_BYTES:
        raise ValueError(
            "table must be a bare lowercase identifier of at most "
            f"{MAX_TABLE_NAME_BYTES} bytes, not {table!r}"
        )
