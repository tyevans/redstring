"""SQL templates, record formats, and query builders for PostgresChunkStore."""

from __future__ import annotations

import re

#: Table names are interpolated into SQL -- Postgres has no parameter form for
#: an identifier -- so the name is proved to be a bare lowercase identifier
#: first, exactly as `PgVectorStore` does. Anything else, including a quoted or
#: schema-qualified name, is rejected rather than escaped.
#:
#: This guard is what the `# nosec B608` markers below rest on: bandit sees an
#: f-string in a SQL literal and cannot see that the only interpolated value
#: was proved safe in `__init__`, nor that every caller-supplied value travels
#: as a `$n` parameter.
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

#: The record shape `jsonb_to_recordset` unpacks a payload into. Written once
#: because `upsert_many` and `replace_source` must agree about it: a column
#: typed differently in the two statements is the silent-divergence shape
#: inside a single adapter.
#:
#: `doc_length` and `embedding` are here and in `_COLUMNS` below, but
#: deliberately **not** in `_ON_CONFLICT` -- see that constant's docstring.
#: `embedding` is typed as the unconstrained `vector` rather than `vector(n)`:
#: `jsonb_to_recordset`'s `AS` clause names a type for the input function to
#: parse text into, and the actual column it lands in -- `vector({dimension})`
#: -- is what enforces the width, exactly as a plain `integer` value narrows
#: to a `smallint` column without the record shape naming that width either.
_INCOMING = (
    "t(tenant_id uuid, id text, source_id text, text text, chunk_index integer, "
    "start_char integer, end_char integer, entity_ids uuid[], metadata jsonb, "
    "doc_length integer, embedding vector)"
)

#: The record shape the term-index payload unpacks into. A term row has no
#: identity of its own beyond its key, so unlike `_INCOMING` there is no
#: matching `_ON_CONFLICT`: see `_TERMS_ON_CONFLICT`.
_TERMS_INCOMING = "t(tenant_id uuid, chunk_id text, term text, tf integer)"

#: Term rows are written `ON CONFLICT DO NOTHING`, never `DO UPDATE`. Chunk
#: ids are content-addressed over `(source_id, text)` and derived by the type
#: -- see `StoredChunk.id` -- so a given id always has the same text,
#: therefore always the same terms and the same `tf` for each. There is no
#: update path for a term row: the "obvious" DELETE-then-INSERT is both
#: unnecessary (the row can never legitimately change) and unsafe in one
#: statement, since a row deleted and reinserted by the same statement is a
#: same-statement double modification.
_TERMS_ON_CONFLICT = "ON CONFLICT (tenant_id, chunk_id, term) DO NOTHING"

#: What every write sets when the key already exists. Last-write-wins, and the
#: list is the whole row bar the key -- an omitted column here is a field one
#: adapter preserves and the other drops, which is `recurring-defects.md` §1's
#: third observed shape verbatim.
#:
#: `doc_length` and `embedding` are deliberately **absent** from this list,
#: unlike every other column in `_COLUMNS`. Neither is an oversight of that
#: rule: `doc_length` is a pure function of `text`, and `embedding` is written
#: once and never updated on conflict either -- a content-addressed id fixes
#: `text` for good, so there is no value either column could ever need
#: updating to (`StoredChunk.id` is a computed field, so this reasoning is
#: enforced by construction -- ADR 0044). Including them in the `SET` list
#: would be a no-op that reads as one more ordinary column and hides the
#: reasoning; omitting them and saying so here is the honest spelling.
_ON_CONFLICT = (
    "ON CONFLICT (tenant_id, id) DO UPDATE SET "
    "source_id = EXCLUDED.source_id, text = EXCLUDED.text, "
    "chunk_index = EXCLUDED.chunk_index, start_char = EXCLUDED.start_char, "
    "end_char = EXCLUDED.end_char, entity_ids = EXCLUDED.entity_ids, "
    "metadata = EXCLUDED.metadata"
)

_COLUMNS = (
    "tenant_id, id, source_id, text, chunk_index, start_char, end_char, "
    "entity_ids, metadata, doc_length, embedding"
)

#: `_COLUMNS` for a `SELECT`, not an `INSERT` -- pgvector's *text* output
#: rounds to seven significant digits, so reading `embedding` back as text
#: is lossy even though the stored float4 is exact. Casting to `real[]` hands
#: asyncpg a binary float4 array it decodes exactly, exactly as
#: `PgVectorStore.get` does; see that module's docstring for the round-trip
#: property that found the asymmetry.
_SELECT_COLUMNS = _COLUMNS.replace("embedding", "embedding::real[] AS embedding")

#: One definition of cosine similarity in this library, not two -- see
#: `redstring.vector.adapters.pgvector.SCORE_SQL`. `chunks` cannot import
#: `vector` (siblings under the same layer, and `lint-imports` forbids it),
#: so this is a second declaration proved identical to the first by
SCORE_SQL = _SCORE = "1 - (embedding <=> $2::vector) / 2"


def schema_statements(table: str, dimension: int) -> tuple[str, ...]:
    """The DDL, as data, so a server-free test can read it.

    The primary key is the **pair**, because content addressing makes a
    collision on `id` alone ordinary rather than unthinkable: the same
    passage of the same source under two tenants hashes identically.

    The index covers `get_by_source`'s filter *and* its full ordering --
    `(tenant_id, source_id, chunk_index, id)` -- so the order is served by
    the index rather than by a sort. `id` is in it because the tie-break is
    part of the port's contract, and an index stopping at `chunk_index`
    would leave the tie resolved by whatever the plan happened to do.

    `chunk_index` is `integer`. See the module docstring: `text` here is a
    working store that reorders documents.

    `doc_length` is a column on this row rather than something computed
    on read, because `lexical_candidates` needs it for every candidate
    the term-index query returns and recomputing it there would mean
    re-tokenizing `text` in SQL -- exactly the divergence
    `domain/tokenize.py`'s module docstring exists to rule out. It is
    immutable per id for the same reason the term index is; see
    `_ON_CONFLICT`.

    `<table>_entity_ids_idx` is a GIN index over the array column,
    supporting `get_by_entity`'s `entity_ids @> ARRAY[$2::uuid]`. GIN's
    array operator class indexes `@>`, `<@`, `&&` and whole-array `=`;
    it does not index `scalar = ANY(col)`, which is why `get_by_entity`
    uses containment rather than the more obvious membership test.

    `<table>_terms` is the term index: one row per `(tenant_id, chunk_id,
    term)`, carrying that term's frequency in the chunk. **`ON DELETE
    CASCADE` is load-bearing, not incidental.** It is what lets
    `replace_source`'s orphan delete, `delete_by_source` and
    `delete_by_tenant` all keep the term index correct without any of
    them mentioning `<table>_terms` -- three delete paths that would each
    otherwise need a second statement, and each one edit away from
    forgetting it. `<table>_terms_term_idx` supports
    `lexical_candidates`'s per-term lookups (document frequency and
    candidate matching), both filtered on `(tenant_id, term)`.

    `embedding` is `vector(<dimension>)`, nullable -- `None` is the "not
    yet embedded" state `semantic_candidates` skips rather than scores.
    There is deliberately no index on it, matching `PgVectorStore`; see
    `docs/adr/0012-no-ann-index-in-a-multi-tenant-vector-store.md`.

    **The two `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements are
    the owed migration (B89).** `CREATE TABLE IF NOT EXISTS` adds nothing
    to a `kg_chunks` that predates the lexical or semantic work, so a
    table created before either column existed would otherwise have
    neither -- and every query naming `_COLUMNS` would fail against it.
    They run after the `CREATE TABLE`, so a fresh database is created
    with both columns already present and then no-op altered; only a
    pre-existing table is actually repaired. See
    `tests/integration/chunks/test_postgres_store.py::test_ensure_schema_repairs_a_table_created_without_the_new_columns`,
    the only test that proves an `ALTER` does anything at all -- run only
    against a table that already has the column, it is a statement never
    observed to do anything.
    """
    return (
        f"CREATE TABLE IF NOT EXISTS {table} ("
        "  tenant_id   uuid    NOT NULL,"
        "  id          text    NOT NULL,"
        "  source_id   text    NOT NULL,"
        "  text        text    NOT NULL,"
        "  chunk_index integer NOT NULL,"
        "  start_char  integer NOT NULL,"
        "  end_char    integer NOT NULL,"
        "  entity_ids  uuid[]  NOT NULL DEFAULT '{}',"
        "  metadata    jsonb   NOT NULL DEFAULT '{}'::jsonb,"
        "  doc_length  integer NOT NULL DEFAULT 0,"
        f"  embedding   vector({dimension}),"
        "  PRIMARY KEY (tenant_id, id)"
        ")",
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS doc_length integer NOT NULL DEFAULT 0",
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS embedding vector({dimension})",
        f"CREATE INDEX IF NOT EXISTS {table}_tenant_source_idx "
        f"ON {table} (tenant_id, source_id, chunk_index, id)",
        f"CREATE INDEX IF NOT EXISTS {table}_entity_ids_idx ON {table} USING gin (entity_ids)",
        f"CREATE TABLE IF NOT EXISTS {table}_terms ("
        "  tenant_id uuid    NOT NULL,"
        "  chunk_id  text    NOT NULL,"
        "  term      text    NOT NULL,"
        "  tf        integer NOT NULL,"
        "  PRIMARY KEY (tenant_id, chunk_id, term),"
        f"  FOREIGN KEY (tenant_id, chunk_id) REFERENCES {table} (tenant_id, id) "
        "    ON DELETE CASCADE"
        ")",
        f"CREATE INDEX IF NOT EXISTS {table}_terms_term_idx ON {table}_terms (tenant_id, term)",
    )


def insert_sql(table: str) -> str:
    """One statement for the whole batch, not a loop; returns rows added.

    A document's chunking is thousands of rows and the port says so. The
    payload is one `jsonb` parameter rather than parallel arrays because
    `entity_ids` is a per-row array; see the module docstring.

    The term-index insert rides in a CTE alongside the chunk insert.
    `terms_written` is unreferenced by the final `SELECT` and still runs
    -- Postgres executes every data-modifying CTE regardless of whether
    its output is read, the same property `_replace_sql` relies on. The
    shape of this statement now matches `_replace_sql`'s exactly: two
    data-modifying CTEs and a final `SELECT` over one of them, rather
    than a bare trailing `INSERT`.

    **`xmax = 0` is what separates an insert from an update**, and it is
    the only thing that can. `ON CONFLICT DO UPDATE` returns every row it
    touched, so a plain `count(*)` over `written` counts replacements as
    additions and reports `len(chunks)` for a batch that added nothing --
    which is the exact figure the port's return value exists to
    contradict. A row inserted by this statement has `xmax` zero; a row
    updated by it carries the current transaction's id there. `DO
    NOTHING` would make the count fall out of `RETURNING` alone, but it
    is not available here: the port promises last-write-wins, and
    `_ON_CONFLICT` is a real update path.

    `count(*) FILTER (WHERE inserted)` rather than `sum(inserted::int)`:
    both are correct, and the filter says what it means.
    """
    return (
        f"WITH written AS ("  # nosec B608
        f"    INSERT INTO {table} ({_COLUMNS})"
        f"    SELECT {_COLUMNS} FROM jsonb_to_recordset($1::jsonb) AS {_INCOMING}"
        f"    {_ON_CONFLICT}"
        "    RETURNING (xmax = 0) AS inserted"
        "), terms_written AS ("
        f"    INSERT INTO {table}_terms (tenant_id, chunk_id, term, tf)"
        f"    SELECT tenant_id, chunk_id, term, tf FROM jsonb_to_recordset($2::jsonb) "
        f"    AS {_TERMS_INCOMING} "
        f"    {_TERMS_ON_CONFLICT}"
        "    RETURNING 1"
        ") SELECT count(*) FILTER (WHERE inserted) FROM written"
    )


def replace_sql(table: str) -> str:
    """The whole fold in one statement: delete the orphans, write the rest.

    An empty payload is not special-cased anywhere. `id <> ALL (SELECT id
    FROM incoming)` over an empty set is true of every row, so an empty
    chunking empties the source -- which is what the port says it means,
    and `if not chunks: return 0` is the guard that looks defensive and
    leaves the old passages readable forever.

    The count comes through the `removed` CTE rather than from asyncpg's
    `"DELETE n"` status string, matching `PgVectorStore.delete_by_tenant`:
    a stringly-typed answer to a numeric question is one release note away
    from changing shape. `written` and `terms_written` are unreferenced by
    the final `SELECT` and still run -- Postgres executes every
    data-modifying CTE.

    The orphan `DELETE` needs no matching statement against
    `<table>_terms`: `ON DELETE CASCADE` removes those rows as a
    consequence of the row in `<table>` going, which is the whole reason
    this stays one statement instead of gaining a second delete to keep in
    step with the first.

    `terms_written` writes `ON CONFLICT DO NOTHING` rather than `DO
    UPDATE`; see `_TERMS_ON_CONFLICT`. Nothing here needs to reconcile a
    term row against a stale one, because a chunk id's terms cannot
    change.
    """
    return (
        "WITH incoming AS ("  # nosec B608
        f"    SELECT * FROM jsonb_to_recordset($3::jsonb) AS {_INCOMING}"
        "), removed AS ("
        f"    DELETE FROM {table}"
        "     WHERE tenant_id = $1 AND source_id = $2"
        "       AND id <> ALL (SELECT id FROM incoming)"
        "    RETURNING 1"
        "), written AS ("
        f"    INSERT INTO {table} ({_COLUMNS})"
        f"    SELECT {_COLUMNS} FROM incoming "
        f"    {_ON_CONFLICT}"
        "    RETURNING 1"
        "), terms_written AS ("
        f"    INSERT INTO {table}_terms (tenant_id, chunk_id, term, tf)"
        f"    SELECT tenant_id, chunk_id, term, tf FROM jsonb_to_recordset($4::jsonb) "
        f"    AS {_TERMS_INCOMING}"
        f"    {_TERMS_ON_CONFLICT}"
        "    RETURNING 1"
        ") SELECT (SELECT count(*) FROM removed)"
    )


def candidates_sql(table: str) -> str:
    """Which chunks match, truncated by `limit`, with their term counts.

    `matched` picks the surviving chunk ids first, ordered by the
    contract's tie-break -- number of distinct requested terms matched,
    descending, then `id` ascending -- and `LIMIT`s there, before the join
    against `<table>` ever runs. Joining first and limiting after would
    pull every matching row's full text across the wire only to discard
    most of it.

    `jsonb_object_agg(term, tf)` builds this candidate's term frequencies
    in one aggregate rather than a second query per chunk; zero-frequency
    terms the chunk does not contain are filled in by the caller from the
    requested list, since a term with no match has no row here to
    aggregate.
    """
    return (
        "WITH matched AS ("  # nosec B608
        f"    SELECT chunk_id, count(*) AS matched_terms, jsonb_object_agg(term, tf) AS tfs"
        f"    FROM {table}_terms"
        "     WHERE tenant_id = $1 AND term = ANY ($2)"
        "     GROUP BY chunk_id"
        "     ORDER BY matched_terms DESC, chunk_id ASC"
        "     LIMIT $3"
        ")"
        f"SELECT c.{_SELECT_COLUMNS}, m.tfs"
        "  FROM matched m"
        f" JOIN {table} c ON c.tenant_id = $1 AND c.id = m.chunk_id"
        " ORDER BY m.matched_terms DESC, m.chunk_id ASC"
    )


def semantic_candidates_sql(table: str) -> str:
    """Which chunks are nearest, truncated by `limit`, with their score.

    Follows `_candidates_sql`'s shape: `matched` picks the surviving ids
    first, ordered by the port's tie-break -- score descending, then `id`
    ascending -- and `LIMIT`s there, before the join against `<table>`
    pulls the full row across the wire.

    **The ORDER BY says `embedding <=> $2` ASC, which is that same order
    spelled so an index can serve it.** See the comment on the clause.

    `embedding IS NOT NULL` excludes unembedded chunks from being
    candidates at all, rather than scoring them -- the port's stated
    difference from a missing lexical match. `min_score` is applied in
    this `WHERE`, before `LIMIT`, matching `VectorStore.search` and the
    port's own docstring for this method.
    """
    return (
        "WITH matched AS ("  # nosec B608
        f"    SELECT id AS chunk_id, {_SCORE} AS score"
        f"    FROM {table}"
        "     WHERE tenant_id = $1 AND embedding IS NOT NULL"
        f"       AND ($3::float8 IS NULL OR {_SCORE} >= $3)"
        "     ORDER BY embedding <=> $2::vector ASC, id ASC"
        "     LIMIT $4"
        ")"
        f"SELECT c.{_SELECT_COLUMNS}, m.score"
        "  FROM matched m"
        f" JOIN {table} c ON c.tenant_id = $1 AND c.id = m.chunk_id"
        " ORDER BY m.score DESC, m.chunk_id ASC"
    )


def backfill_sql(table: str) -> str:
    """One statement: repair `doc_length`, then the term rows it implies.

    `updated` and `terms_written` are unreferenced by the final `SELECT`
    and still run -- Postgres executes every data-modifying CTE, the same
    property `_insert_sql` and `_replace_sql` rely on for their own
    unreferenced CTEs. The count comes from `updated` rather than
    asyncpg's status string, matching every other counted write here.
    """
    return (
        "WITH incoming AS ("  # nosec B608
        "    SELECT * FROM jsonb_to_recordset($1::jsonb) "
        "    AS t(tenant_id uuid, id text, doc_length integer)"
        "), updated AS ("
        f"    UPDATE {table} c SET doc_length = incoming.doc_length"
        "     FROM incoming"
        "     WHERE c.tenant_id = incoming.tenant_id AND c.id = incoming.id"
        "    RETURNING 1"
        "), terms_written AS ("
        f"    INSERT INTO {table}_terms (tenant_id, chunk_id, term, tf)"
        f"    SELECT tenant_id, chunk_id, term, tf FROM jsonb_to_recordset($2::jsonb) "
        f"    AS {_TERMS_INCOMING}"
        f"    {_TERMS_ON_CONFLICT}"
        "    RETURNING 1"
        ") SELECT (SELECT count(*) FROM updated)"
    )
