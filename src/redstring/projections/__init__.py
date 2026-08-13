"""Read models derived from the event log.

The event log is the write model; a `GraphStore`, a `VectorStore` and a
`ChunkStore` are projections of it -- derived, disposable, and rebuildable by
replay. That claim is only worth as much as the test that proves it, which is
`tests/unit/projections/test_replay_equivalence.py`.

## The rebuild driver lives upstream now

`project`/`replay` and its `ReplayReport`, `ReplayFailure` and
`ReplayFailedError` were written here because `eventsource-py` had no rebuild
driver -- `ProjectionCoordinator` dispatches events a caller already holds, and
rebuilding a store from a feed is the other job. (It has never polled on a
timer; upstream checked its full history against that claim in 0.14.0 and
deleted the interval nothing read.) They were reported upstream and landed in
`eventsource-py`
0.12.0 (its ADR 0054), so this package no longer carries a copy:

    from eventsource import replay

The upstream version is a superset, not a transcription. It scopes the read by
`aggregate_type=` as well as `tenant_id=`, caps the retained failure list with
`max_failures=` and *reports* what the cap dropped as
`ReplayReport.failures_truncated` (BACKLOG B73's honest shape, which this copy
never grew), streams every failure to `on_failure=` whether retained or not,
names the failing `event_id`, and derives `ReplayReport.failed` from event ids
rather than positions -- `position` is `Position | None` by contract, so a
feedless store collapsed a whole failed rebuild into a count of one here. Since
0.14.0 it also reads the feed in `batch_size=` batches (defaulting to
`REPLAY_BATCH_SIZE`, 1000) and folds each before reading the next, so a rebuild
holds a batch rather than the whole log: every store adapter materializes its
result set before yielding an envelope, and this copy asked for all of it in
one call. `max_events` never bounded that and could not -- it counts envelopes
already in hand, so it fires after the allocation it would have prevented.

`ReplayFailedError` is an `eventsource` `ProjectionError` rather than a
`RedstringError`, which is the correct root: a projection failed to process an
event, and `except ProjectionError` should catch a strict rebuild's stop for
the same reason it catches a live projection's.

`StoreProjection` moved the same way (upstream ADR 0055) and is imported from
`eventsource.application.projections`. A subclass wanting its own constructor
parameters forwards the rest with `**options: Unpack[ProjectionOptions]`
instead of restating them; no projection here needs one.
"""

from redstring.projections.chunk import ChunkProjection
from redstring.projections.graph import GraphProjection
from redstring.projections.vector import VectorProjection

__all__ = [
    "ChunkProjection",
    "GraphProjection",
    "VectorProjection",
]
