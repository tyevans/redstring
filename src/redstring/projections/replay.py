"""Driving projections over the global event feed.

Deliberately small and explicit rather than a subscription runner: this is
what a *rebuild* looks like, and a rebuild is a foreground operation someone
is waiting on. `ProjectionCoordinator` in the library polls on a timer for
live catch-up, which is the other job.

## A poison event must not wedge the projection

`CheckpointTrackingProjection.handle` retries, writes to the DLQ, and then
re-raises -- the re-raise is what tells a *live subscription* to stop and not
checkpoint past a failure. A rebuild wants the opposite: the failure is
already recorded in the DLQ, and stopping means one bad event denies the
projection every event after it. So `project` catches, counts, and continues.
`ReplayReport.failed` is how the caller finds out, and it is a count rather
than a bool so "some events failed" cannot be mistaken for "none did" by a
truthiness check.

## When continuing is the wrong default

Tolerating a poison event is right for a rebuild over a long log and wrong
for a test or a first deployment, which is exactly when a silently partial
rebuild costs most and shows least -- a replay that dropped *every* event
still returns a report and exits successfully. `project(strict=True)` raises
`ReplayFailedError` on the first rejection instead, and the exception names
the event, its position and the projection that refused it: an error saying
"replay failed" with a count would be the same problem in a louder voice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from redstring.domain.exceptions import ReplayFailedError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from eventsource.ports.handlers import EventSubscriber
    from eventsource.ports.positions import Position
    from eventsource.ports.store import GlobalEventFeed

#: Events one `project` call will read before giving up.
#:
#: The feed is adapter-supplied and the loop's exit depends on it: a cursor
#: that failed to advance would turn this into a hang, and a hang in CI reads
#: as infrastructure trouble and gets retried rather than investigated. Ten
#: million is far above any real rebuild and far below forever.
MAX_EVENTS_PER_REPLAY = 10_000_000


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """What one `project` call did."""

    applied: int
    failed: int
    last_position: Position | None


async def project(
    feed: GlobalEventFeed,
    projections: Sequence[EventSubscriber],
    *,
    from_position: Position | None = None,
    max_events: int = MAX_EVENTS_PER_REPLAY,
    strict: bool = False,
) -> ReplayReport:
    """Read the feed from `from_position` and fold it into every projection.

    `from_position` is exclusive, matching `read_all`: `None` means from the
    very beginning, which is what a rebuild wants.

    An event that every projection ignores still counts as applied -- it was
    delivered and nothing rejected it. An event that any projection rejects
    counts as failed once, however many projections rejected it, because the
    count answers "how much of the log did not make it into the read models".

    `strict=True` raises `ReplayFailedError` on the first rejection instead of
    counting it, naming the event, its position and the projection that
    refused it. Use it where a partial rebuild is worse than no rebuild -- a
    test, or a first deployment -- and leave it off for a rebuild over a long
    log, where one poison event must not deny the projection every event after
    it. Defaulting it to `True` was rejected for that reason: the tolerant
    behaviour is right for the case this module was written for, and the
    strict one is right for the case a caller notices.
    """
    applied = failed = seen = 0
    last_position: Position | None = None

    async for envelope in feed.read_all(from_position):
        seen += 1
        if seen > max_events:
            raise RuntimeError(
                f"replay read more than {max_events} events without the feed "
                f"ending; the adapter's cursor is probably not advancing "
                f"(last position: {last_position})"
            )
        last_position = envelope.position

        rejected = False
        for projection in projections:
            try:
                await projection.handle(envelope.event)
            except Exception as exc:
                # Already retried and written to the DLQ by the projection base
                # class. Re-raising here is what would wedge the rebuild, so
                # the exception is deliberately not narrowed: a projection may
                # raise anything, and "this event did not apply" is the only
                # distinction a rebuild can act on.
                if strict:
                    raise ReplayFailedError(
                        event=envelope.event,
                        position=envelope.position,
                        projection=projection,
                    ) from exc
                rejected = True
        if rejected:
            failed += 1
        else:
            applied += 1

    return ReplayReport(applied=applied, failed=failed, last_position=last_position)
