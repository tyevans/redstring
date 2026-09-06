# ADR 0046: A chunk write reports what it added, and a reader answers "which of these do I have?"

## Status

Accepted.

Relates to [ADR 0023](0023-the-chunk-corpus.md), which **stands** — passages
are still content-addressed and replaced a source at a time. This ADR does not
change what is stored or how it is keyed; it changes what the two write and
read surfaces are able to tell a caller about it.
Relates to [ADR 0044](0044-a-chunk-id-is-derived-not-supplied.md), which
**stands** and is the direct cause of the defect below: an id derived from
`(source_id, text)` makes a collision a normal outcome of chunking rather than
a caller error, so the collapse it produces is correct and needs to be
*visible* rather than prevented.
Relates to [ADR 0026](0026-chunk-store-and-cache-are-capabilities-too.md),
which **stands** — the new method lands on `ChunkReader`, the capability whose
question it is, and not on the composed port.
Relates to [ADR 0012](0012-no-ann-index-in-a-multi-tenant-vector-store.md),
which **stands**: this read is an index seek on the primary key, not a vector
search, so nothing here bears on the ANN argument.

Closes BACKLOG B159 and B161.

## Context

Both halves of this decision were found by a consumer, not by inspection, and
both are the same absence seen from opposite sides: the store knew something
the caller needed and had no way to say it.

**The read.** Every `ChunkReader` method was keyed on one thing — `get` on a
chunk id, `get_by_source` on a source, `get_by_entity` on an entity. There was
no way to ask about a *set* of ids, which is the question a resumable ingest
asks on every batch. A caller loading a large corpus and wanting to skip what
it already held had three options, and the third is the one that says a port
is missing:

- `get` per candidate: one round trip per chunk, transferring whole
  `StoredChunk`s — text plus a full embedding each — to answer a yes/no
  question.
- `get_by_source` per node: the same round-trip count, and it answers whether
  *this source* is stored rather than whether *this text* is, which is the
  question a content-addressed id actually poses.
- Go around the port with SQL. This is what happened. A benchmark harness
  issued `SELECT id FROM <table> WHERE tenant_id = $1` directly from its CLI,
  which drags `asyncpg` and a DSN into a layer with no other reason to know
  Postgres exists, and re-derives the table name — so a change to the naming
  scheme desynchronises two places instead of one.

**The write.** `ChunkWriter.upsert_many` returned `None`. A caller handing it
thousands of chunks could not learn how many rows existed afterwards, and no
read answered it either. A corpus ingest reported 549,886 chunks written into
a tenant holding 549,697 — short by 189, across 78 documents, because a
sliding window over repetitive text emits byte-identical passages for one
source and those share an id. Re-chunking the corpus offline reproduced the
figure to the unit. A second arm was short by 10 by the same mechanism.

The discrepancy was found by a standalone script comparing a report against
`count(*)`. Nothing in the library failed, and nothing could have.

**The dedup is right and must stay.** Two byte-identical chunk texts embed to
the same vector, so the second row buys no retrieval and costs storage. Adding
`start_char` to the id would "fix" the count by storing redundant duplicates,
which is the positional identity `domain/chunk.py` records as rejected. The
defect is not the merge — it is that the merge is unobservable.

**A caller cannot compute either answer for itself.** Deduplicating its own
batch before the write catches collisions *within* one call and misses
collisions against rows already stored, which is the resume path and the case
that actually bites. Only the store knows.

## Decision

`ChunkReader` gains:

```python
async def existing_ids(self, chunk_ids: Sequence[ChunkId], tenant_id: TenantId) -> set[ChunkId]: ...
```

`ChunkWriter.upsert_many` returns `int` instead of `None`: **the number of
rows the call added to the store**, never rows it replaced.

Three properties are contract rather than implementation detail.

**The read is bounded by the caller's input, not by the corpus.** The obvious
alternative — `ids_for_tenant(tenant_id) -> set[ChunkId]` — is what the
consumer built for itself, and it is the wrong port for a library: fine at
129,375 chunks and ruinous at 50,000,000. A port should not have a corpus size
past which it becomes unusable. `existing_ids` composes with any batch size and
leaves the caller deciding how much to hold at once.

**The write returns a count rather than the set of ids written.** The set is
strictly more useful — it tells a chunker's author *which* passages collapsed —
and strictly more expensive at scale, for exactly the reason above. The count
answers the reporting defect that motivated it, and it reconciles against a row
count, which the set does only by being summed. Decided the same way and for
the same reason as `existing_ids`' shape, so the two surfaces do not disagree
about who bounds the output.

**An adapter over a database must make the read an index seek.** A full scan
and an index seek return identical sets and differ only in cost, so this is
stated in the port and asserted against the query plan rather than the result.

## Consequences

**This is a breaking change for third-party adapters**, twice over: a
`ChunkWriter` returning `None` no longer satisfies the protocol, and a
`ChunkReader` without `existing_ids` does not either. Both are caught by the
shipped compliance suite (ADR 0033) rather than at runtime, which is the point
of shipping it.

**The compliance coverage gate needed widening, and the reason generalises.**
`tests/unit/chunks/test_compliance_coverage.py` derived its read-method list
from return annotations that *mention a domain type*. `existing_ids` returns
`set[ChunkId]`, and `ChunkId` is `str` — so the gate would not have seen it,
and the mutation-isolation and tenant-isolation tests it exists to demand would
not have been demanded. The gate now selects on a second axis: a return
annotation that is, or contains, a mutable container. Adding `ChunkId` to the
domain-type set was rejected, because that set would really read
`{StoredChunk, str}` and every future method returning a bare string would
become a "read" needing two tests it does not need.

The general form is worth stating: **a coverage gate keyed on the types a port
happens to use today goes quiet when a method returns something outside that
set.** It does not fail — it reports nothing, which is indistinguishable from
having nothing to report.

**The Postgres count comes from `xmax = 0`, and nothing else can produce it.**
`ON CONFLICT DO UPDATE` returns every row it touched, so a plain `count(*)`
over the `RETURNING` set counts replacements as additions and reports
`len(chunks)` for a batch that added nothing — the exact figure this return
value exists to contradict. `DO NOTHING` would make the count fall out of
`RETURNING` alone and is not available, because the port promises
last-write-wins.

**`replace_source` is unchanged and still returns orphans removed.** It has the
same blind spot and has already spent its `int` on a different question;
widening it to a result object was considered and deferred rather than taken in
passing, because it is a second break for adapters and this one already has
two.

**The tenant-isolation case on the new read has teeth it would not have on
another port.** `existing_ids` is a filtered membership test, so a body that
forgets the tenant predicate returns *more* ids and the caller skips work it
should have done — silently building a corpus in which another tenant's chunk
stands in for one of its own. Content addressing makes the colliding id
ordinary rather than astronomically unlikely, so the compliance case forces the
collision instead of relying on `uuid4()` never producing one.
