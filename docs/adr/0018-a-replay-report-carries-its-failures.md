# ADR 0018: A replay report carries its failures, and the read can be scoped

## Status

**Superseded by [ADR 0020](0020-the-replay-driver-goes-upstream.md).** Every
decision below still holds; none of them is held *here* any more.
`eventsource-py` 0.12.0 upstreamed this driver (its ADR 0054) along with the
`aggregate_type` scoping this record left open (its ADR 0052), so
`projections/replay.py` is deleted and the five names it put in `__all__` are
gone from the surface. Read this for the reasoning — it is what the upstream
records are arguing with — and ADR 0020 for what is true now, including the
two places where upstream's version is not a transcription of this one.

Accepted (superseded).

Relates to [ADR 0006](0006-the-public-surface-is-gated.md), which settles that
`__all__` is the whole promise — it **stands**; this adds four names to it
(`ReplayFailure`, `ReplayFailedError`, `replay`, and a widened `project`) and
changes nothing about the mechanism.
[ADR 0007](0007-composition-is-the-only-top-layer.md) **stands** —
`project` remains the entry point for a caller who has an event store, and
nothing moved between layers.
[ADR 0004](0004-consolidation-emits-events.md) and
[ADR 0001](0001-event-log-schema-and-granularity.md) **stand**: no event
payload changed, and the log is read differently rather than written
differently.

## Context

Two reports from the first downstream project, filed as `BACKLOG.md` B68 and
B69. Both are about `project` returning too little and reading too much.

**The read is wider than the rebuild.** `project` called
`feed.read_all(from_position)` and nothing else, while `GlobalEventFeed`'s
second parameter — `FeedReadOptions(tenant_id=..., limit=...)` — is pushed
into SQL by the eventsource adapters. Downstream scoped instead with
`tenant_filter` on the projection, which is correct and drops foreign events
*after* delivery. In a store where their session events vastly outnumber their
knowledge events, every rebuild read the whole log to discard most of it.

**The failure was discarded.** The fold's `except` set a flag and dropped the
exception:

```python
except Exception:
    rejected = True
```

so `ReplayReport.failed` was a bare integer. Downstream's workaround refused to
open a project when it was non-zero — safe, and useless in the same breath,
because the operator then gets "3 knowledge event(s) failed to replay" and has
no path from that message to the poison event. The DLQ holds the events, but
reaching it means the caller already has the DLQ repository in hand and knows
to look; the report they were handed says nothing.

The usual framing of B69 is "there is no strict mode", and that framing is
wrong about which half matters. A caller can always turn detail into a raise.
No caller can turn a count back into detail.

## Decision

### `ReplayReport.failures` carries the exception object

One `ReplayFailure` per *rejection*, holding `position`, `event_type`, the
rejecting projection's class name, and `error` — the exception itself, so
`MissingEntityError.entity_id` is an attribute rather than a substring of a
message.

The projection is recorded by **name**, not by reference. A report is a thing
an operator reads or logs; holding the live projection would make it a handle
into the read model.

### `failed` becomes a property derived from `failures`

An event both folds reject is *one* failed event and *two* failures. Those are
genuinely different questions — "how much of the log did not make it into the
read models" versus "which fold do I fix" — so both are answerable, and
`failed` is computed from the distinct positions in `failures` rather than
counted alongside it.

Deriving it is the whole point: two counters maintained separately are the
project's [recurring defect §2](https://github.com/tyevans/redstring/blob/main/.claude/rules/recurring-defects.md)
(one fact, two declaration sites, nothing that fails when they disagree). The
constructor takes no `failed` argument, and a test asserts it cannot.

### `strict=True` raises `ReplayFailedError` on the first rejection

Offered because it is the common case, not because it is hard. The default
stays lenient — a rebuild that stops on a poison event denies the projection
every event after it, which is the reasoning the module has carried since it
was written. Strict is for a test or a first deployment, where a silent
partial rebuild is most costly and least visible.

`ReplayFailedError` carries the `ReplayFailure` and sets the original as its
`__cause__`. An exception saying "replay failed" with a count would be the same
problem in a louder voice.

### `tenant_id=` forwards `FeedReadOptions`, and stops there

`project(..., tenant_id=...)` becomes `FeedReadOptions(tenant_id=...)` on the
`read_all` call. When no tenant is named, `None` is passed rather than a
default-constructed options object: `read_all` documents `None` as unfiltered,
and constructing an empty filter invites an adapter to interpret it.

**Category and stream scoping are deliberately not added.** `read_category`
lives on `EventStore`, not on `GlobalEventFeed`, so taking it would mean
`project` accepting a narrower port than the one it documents — or accepting
both and branching, which needs an answer for "both given" that nobody has a
use case for. Tenant is what this library models end to end, and it was the
case actually reported.

### `replay` is exported as an alias for `project`

Any knowledge-graph consumer plausibly has a *project* noun of its own, and
downstream's landed in the same twelve-line function. A rename would break
every caller for a cosmetic gain; an alias costs the surface one name and lets
the collision be dodged at the import. It is asserted to be the *same object*,
not merely to behave the same — two separately-defined functions would drift
the first time one gained an argument, and the caller who chose the alias would
be choosing the stale one.

## Consequences

**`ReplayReport`'s field order changed.** `failed` was the second positional
field and is now a property; `failures` is a new third field with a default.
Keyword construction is unaffected, positional construction of the two-arg
prefix still works, and anything passing `failed=` now fails loudly rather than
silently. Reading `report.failed` is unchanged, which is the form every caller
and every doc uses.

**A scoped run's `last_position` is a scoped cursor.** It is the last position
the *filtered* read reached, so checkpointing it and later resuming without the
same `tenant_id` skips events that were never delivered. The how-to says so;
nothing enforces it, because a `Position` carries no record of the filter that
produced it.

**Setting both `tenant_id` and `tenant_filter` is redundant, not wrong.** The
filter has nothing left to drop. This is mildly confusing and was preferred to
having `project` reach into the projections to check.

**The exception is retained past the fold.** `failures` holds live exception
objects for the length of the call, including their tracebacks, so a replay
that fails on a very large fraction of a very large log holds more than it used
to. `MAX_EVENTS_PER_REPLAY` already bounds the loop; nothing bounds `failures`
separately, and `BACKLOG.md` records that as a deliberate deferral rather than
an oversight.
