# ADR 0044: A chunk id is derived, not supplied

## Status

Accepted.

Amends [`0038` the chunk's vector lives on the chunk](0038-the-chunks-vector-lives-on-the-chunk.md),
which stated content addressing as prose on `ChunkWriter.upsert_many` and left
its executable half open as BACKLOG B97. Closes B97.

## Context

`chunks/adapters/postgres.py` writes term rows `ON CONFLICT DO NOTHING`, and
its `_ON_CONFLICT` clause for the chunk row itself omits `doc_length` and
`embedding` from the `SET` list. Both omissions are justified the same way: a
chunk id is content-addressed over `(source_id, text)`, via
`chunk_id(source_id, text)`, so a write that reuses an id is assumed to be
writing the same text, and neither derived column can ever legitimately need
updating on conflict.

That argument held only for callers who built ids with `chunk_id` themselves.
Nothing enforced that they did — `StoredChunk.id` was a caller-supplied
`str`, and `ChunkWriter.upsert_many` promised unqualified last-write-wins on
`(tenant_id, id)`. A caller reusing an id for different text got, from
`InMemoryChunkStore`, ranking over the *new* text (it tokenizes at query
time), and from `PostgresChunkStore`, ranking over the *old* text and the
*old* `doc_length` — the two adapters silently disagreeing about the same
row, `.claude/rules/recurring-defects.md` §1's shape exactly. That gap is
BACKLOG B97.

## Decision

**`StoredChunk.id` is a `computed_field`, not a stored field.** It is defined
as a `@computed_field @property` returning `chunk_id(self.source_id,
self.text)`, so the id cannot be anything other than the hash of the fields
it names. `model_config = ConfigDict(extra="forbid")` means a caller who
tries to pass `id=` the old way gets a loud rejection rather than a value
that is silently overwritten or silently ignored.

**A supplied `id` is not simply rejected — it is checked, and a matching one
is accepted.** `extra="forbid"` alone breaks event-log replay:
`DocumentChunked` carries `list[StoredChunk]`, and `model_dump()` includes
the computed `id`, so every already-stored event became un-deserialisable
the moment `id` stopped being a plain field. A
`model_validator(mode="before")` handles the three cases a round trip can
produce: `id` absent — derive it, the ordinary construction path; `id`
present and **equal** to the derived value — pop it and accept, which is
what replaying a stored event does; `id` present and **unequal** — raise,
naming both ids, because the only way a payload's `id` can disagree with its
own `(source_id, text)` is if one of them was edited after the id was
computed. This is the trap to know about before re-deriving any field that a
serialised payload already carries: `extra="forbid"` plus a computed field is
not enough on its own, and the failure mode is not "rejects a stale id", it
is "cannot read back anything you already wrote".

With this in place, the two adapters' `ON CONFLICT` reasoning is a property
of the type rather than an assumption about callers: a write that reuses an
id is *necessarily* writing the same text, because there is no longer a way
to construct a `StoredChunk` where that isn't true.

## Rejected

**Last-write-wins on the derived columns instead.** This was the alternative
B97 itself proposed — treat a same-id-different-text write as within the
contract and have both adapters update `doc_length`, the term index and
`embedding` on conflict. It contradicts the identity this subsystem already
ships (`0023`'s content addressing, `0038`'s reuse of it for the vector
column) rather than merely leaving it partly enforced, and on Postgres it
needs an unsafe same-statement DELETE-then-INSERT of the term rows — a row
deleted and reinserted by the same statement is a same-statement double
modification, which `_TERMS_ON_CONFLICT`'s docstring already argues against
for a different reason.

**A validator instead of a computed field.** Rejecting a mismatched
caller-supplied id at construction is strictly weaker than not letting a
caller supply one at all: every caller still has to compute
`chunk_id(source_id, text)` themselves and pass it, and only finds out
afterwards whether they got it right. A computed field removes the
computation from the caller's responsibility entirely, rather than checking
their homework.

**A compliance case asserting the two adapters agree after a
same-id-different-text write.** This was B97's "thorough fix" as originally
filed. It is no longer a meaningful test: that state cannot be built through
`StoredChunk` any more, so the case would have to bypass the type — at which
point it is testing something no caller of this library can do, not a
behaviour of the port.

## Consequences

**Breaking for any caller that self-assigned ids.** A construction that
supplied an `id` unrelated to `(source_id, text)` now raises at construction
instead of being accepted and later disagreeing between adapters. No
production call site in this repository ever did this — every one of the 25
initial test failures when this landed was a test fixture passing a
placeholder id, not a real caller relying on self-assigned identity. B97 was
a latent contract hole in this codebase, not an active defect.

**`model_dump()`'s shape is unchanged.** `id` still appears in a dumped
`StoredChunk`, computed rather than stored, so `DocumentChunked` payloads —
past and future — serialise and deserialise the same way.

**A legacy row written before this change, if one carried a non-derived id,
reads back under its derived id.** `ChunkReader` methods return
`StoredChunk` instances built from stored columns, and construction now
always computes `id` from `source_id` and `text` rather than trusting a
stored `id` column. This repository has no such rows, per the point above,
but an adapter storing rows written by an older version of this library
would see their addressable identity change on the next read.
