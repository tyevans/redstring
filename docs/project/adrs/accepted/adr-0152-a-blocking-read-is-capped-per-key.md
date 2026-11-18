---
id: '0152'
title: A blocking read is capped per key
status: Accepted
target_bc: core
governing_prds:
- PRD-0013
governing_stories:
- US-0030
amends:
- ADR-0116
- ADR-0150
---

# ADR-0152: A blocking read is capped per key

## Status

Accepted. Amends [ADR-0116](adr-0116-graph-store-is-five-capabilities.md):
`EntityReader.find_by_blocking_keys` gains a keyword argument. Completes the
work that decision 1 of
[ADR-0150](adr-0150-attacker-influenced-input-is-bounded-and-inert.md) left
open for consolidation, which its Consequences recorded.

## Context

ADR-0150 capped how many candidates consolidation scores for one subject. It
did not cap the read before the cut. `find_by_blocking_keys` returned the
whole block of each key. The default blocking strategies include the entity
type, and the `t:<type>` block holds every entity of that type in the tenant.
A tenant with a very common type therefore read a block the size of the type
for each subject, and then scored only the capped number of it. Lexical
retrieval reads the blocks of the prefix and soundex keys of a query. Those
blocks are smaller, but the query is caller text, and a common prefix has no
limit either.

Two fixes were considered:

- **Remove the type key from the default strategies.** This bounds the
  blocks without a port change. It also removes the only key that two
  differently spelled names of one type always share, which lowers recall for
  every tenant, small ones included.
- **Cap the read in the port.** This changes no result for a block at or
  below the cap, and bounds the read above it.

## Decision

1. **`find_by_blocking_keys` takes a keyword-only `limit_per_key`.** `None`
   means no cap, so existing callers are unchanged. The cap applies to each
   key's group on its own, not to the total. A capped group holds the
   entities with the lowest ids, in the total order that `find_entities`
   documents, so two adapters cap one block identically. The adapter applies
   the cap before rows leave the store. A negative value raises `ValueError`.
   The compliance suite tests the selection, the per-key scope, zero, a cap
   above the block, and a cap with another tenant's lower ids under the same
   key.

2. **`MAX_BLOCK_READ` in `domain/blocking.py` is the one cap the library
   uses.** Consolidation's candidate finder, lexical entity retrieval and its
   diagnostic all pass it. The candidate cap of ADR-0150 does not exceed it,
   so a caller's candidate cap is never larger than what one key can supply.

3. **The type key stays in the default strategies.**

## Consequences

- For a block at or below `MAX_BLOCK_READ`, every result is the same as
  before: the block is read whole. The accuracy corpus has no block near the
  cap, so it measures no change by construction, and no accuracy run was
  needed to accept this record.
- Above the cap, the block is cut by id, and an entity id carries no
  information about its name. A duplicate that shares only an oversized key
  with its subject can be cut before it is scored. A duplicate that also
  shares a prefix or soundex key is still found through that smaller key.
  This is the recall cost of a bounded read, and it applies only to tenants
  whose blocks are larger than the cap.
- A third-party `GraphStore` must accept the new keyword. An adapter written
  against the earlier signature raises `TypeError` on the first consolidation
  or lexical retrieval call, and a type checker reports the mismatch first.
  The shipped compliance suite (ADR-0133) fails the adapter until it
  implements the cap.
- The Neo4j adapter caps each key in a subquery with `LIMIT`. The database
  still visits the key's edges to order them, but it returns and the driver
  builds only the capped rows.

## Related records

- [ADR-0103](adr-0103-blocking-keys-as-nodes.md) stands. Keys stay nodes,
  and the cap is a property of the read.
- [ADR-0141](adr-0141-the-consolidation-pass-is-decide-then-emit.md) stands.
  The cap applies inside the scoring phase.
- [ADR-0122](adr-0122-the-lexical-channel-is-not-bm25.md) stands. Lexical
  retrieval still ranks the blocking-key candidates. A capped block gives it
  fewer candidates to rank only when a query key is more common than the cap.
