"""Identifier rules for the Postgres chunk adapter's SQL text.

Postgres has no parameter form for an identifier. The table name is therefore
written into statement text. This module decides which names may be written,
and how long the names derived from them may be. Each SQL helper in
`postgres_sql` and `postgres_search` calls `check_table` before it formats a
name.

The rules are kept here, and not in `redstring.vector`, because the chunk and
vector adapters are sibling layers. Neither may import the other. The same
rules exist in `redstring.vector.adapters.pg_identifiers`, and the two copies
are kept equal by tests on each side.
"""

from __future__ import annotations

import re

#: Postgres truncates an identifier longer than this many bytes (`NAMEDATALEN - 1`).
#: It truncates an explicit index name without an error. Two derived names that
#: share a 63-byte prefix then collide, and `CREATE INDEX IF NOT EXISTS` skips
#: the second one with no error.
POSTGRES_IDENTIFIER_BYTES = 63

#: The suffixes this adapter appends to a base table name. Every statement that
#: builds a derived name uses these constants. The length limit is computed from
#: the same declarations, so a longer suffix added here lowers
#: `MAX_TABLE_NAME_BYTES` with no other edit.
#:
#: Postgres names the primary key and the foreign key constraints itself. It
#: picks a name it has not used, so those names are not declared here.
_TENANT_SOURCE_IDX = "_tenant_source_idx"
_ENTITY_IDS_IDX = "_entity_ids_idx"
_TERMS = "_terms"
_TERMS_TERM_IDX = "_terms_term_idx"
_DERIVED_SUFFIXES = (_TENANT_SOURCE_IDX, _ENTITY_IDS_IDX, _TERMS, _TERMS_TERM_IDX)

#: The longest base table name whose longest derived name still fits.
MAX_TABLE_NAME_BYTES = POSTGRES_IDENTIFIER_BYTES - max(len(s) for s in _DERIVED_SUFFIXES)

_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")


def check_table(table: str) -> None:
    """Refuse a table name that is not safe to write into SQL text.

    Every helper that formats `table` calls this first. A direct caller of a
    helper meets the same rule as a caller of the store. The name must be a bare
    lowercase identifier, with no quoting and no schema prefix. It must also be
    at most `MAX_TABLE_NAME_BYTES` long, so every derived name stays within the
    Postgres limit.

    The `# nosec B608` markers in the SQL helpers depend on this check. Bandit
    sees an f-string in a SQL literal. It cannot see that the only interpolated
    value was checked here, or that every caller-supplied value travels as a
    `$n` parameter.

    Raises:
        ValueError: `table` is not a bare identifier, or it is too long.
    """
    if not _IDENTIFIER.fullmatch(table) or len(table) > MAX_TABLE_NAME_BYTES:
        raise ValueError(
            "table must be a bare lowercase identifier of at most "
            f"{MAX_TABLE_NAME_BYTES} bytes, not {table!r}"
        )
