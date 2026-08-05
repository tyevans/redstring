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
projection every event after it. So `project` catches, records, and continues.
`ReplayReport.failed` is how the caller finds out, and it is a count rather
than a bool so "some events failed" cannot be mistaken for "none did" by a
truthiness check.

**A count on its own is safe and useless in the same breath.** Reported
downstream: an operator told "3 events failed to replay" has no path from
that message to the poison event, and the exception that would have supplied
one was discarded inside the `except`. `ReplayReport.failures` carries it --
position, event type, the rejecting projection, and the exception itself. A
caller can always turn detail into a raise; no caller can turn a count back
into detail, which is why the detail is the part that had to live here.

`strict=True` is the raise, offered because it is the common case rather than
because it is hard: it stops at the first rejection and raises
`ReplayFailedError` carrying the same `ReplayFailure`.

## Scoping the read

`project` reads the whole feed by default. `tenant_id=` forwards a
`FeedReadOptions` to the adapter, which pushes the filter into the query --
so a shared store rebuilds one tenant with an indexed read rather than
scanning every other tenant's events and discarding them in Python. This is
narrower than `tenant_filter` on the projection, which is applied *after*
delivery and therefore costs the read either way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

# `eventsource.ports.store` re-exports this but declares no `__all__`, so a
# strict import from there is an attr-defined error. Take it from its home.
from eventsource.ports.envelopes import FeedReadOptions

from redstring.domain.exceptions import RedstringError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

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
class ReplayFailure:
    """One projection's refusal of one event.

    `projection` is the rejecting projection's class name rather than the
    object: a report is something an operator reads or logs, and holding the
    live projection would make it a handle into the read model.
    """

    #: `Position | None` because that is what an envelope promises. In every
    #: adapter here it is set, and a failure without one is barely actionable
    #: -- but narrowing it with an assert would turn an adapter's quirk into a
    #: crash on the path whose whole job is not to crash.
    position: Position | None
    event_type: str
    projection: str
    error: Exception


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """What one `project` call did.

    `failed` is *derived* from `failures` rather than counted alongside it.
    Two projections can reject the same event, and the two numbers then
    differ -- `failed` answers "how much of the log did not make it into the
    read models", which is per event, while `failures` has one entry per
    refusal because that is what names the projection to fix. Counting both
    independently is how they drift apart; deriving one means they cannot.
    """

    applied: int
    last_position: Position | None
    failures: tuple[ReplayFailure, ...] = field(default_factory=tuple)

    @property
    def failed(self) -> int:
        """Events at least one projection rejected."""
        return len({failure.position for failure in self.failures})


class ReplayFailedError(RedstringError):
    """A `strict=True` replay hit an event a projection rejected.

    Carries the `ReplayFailure` rather than a message about one: the whole
    point of strict mode is that the caller can act on the specific event,
    and re-deriving it by parsing `str(exc)` is not acting on it.
    """

    def __init__(self, *, failure: ReplayFailure) -> None:
        self.failure = failure
        super().__init__(
            f"{failure.projection} rejected {failure.event_type} at "
            f"{failure.position}: {failure.error}"
        )


async def project(
    feed: GlobalEventFeed,
    projections: Sequence[EventSubscriber],
    *,
    from_position: Position | None = None,
    tenant_id: UUID | None = None,
    strict: bool = False,
    max_events: int = MAX_EVENTS_PER_REPLAY,
) -> ReplayReport:
    """Read the feed from `from_position` and fold it into every projection.

    `from_position` is exclusive, matching `read_all`: `None` means from the
    very beginning, which is what a rebuild wants.

    `tenant_id` narrows the *read*, not the delivery: it is pushed down into
    the adapter's query, so rebuilding one tenant out of a shared store does
    not pay for every other tenant's events. `None` reads the whole feed.

    `strict=True` raises `ReplayFailedError` on the first rejection instead of
    carrying on. The default is the rebuild's behaviour -- one bad event must
    not deny the projection every event after it -- and strict is for a test
    or a first deployment, where a silent partial rebuild is most costly and
    least visible.

    An event that every projection ignores still counts as applied -- it was
    delivered and nothing rejected it. An event that any projection rejects
    counts as failed once, however many projections rejected it, because the
    count answers "how much of the log did not make it into the read models";
    `failures` carries one entry per rejection, so the projection to fix is
    named.
    """
    applied = seen = 0
    failures: list[ReplayFailure] = []
    last_position: Position | None = None
    options = FeedReadOptions(tenant_id=tenant_id) if tenant_id is not None else None

    async for envelope in feed.read_all(from_position, options):
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
                # distinction a rebuild can act on. It is *recorded* rather
                # than swallowed -- see `ReplayFailure`.
                failure = ReplayFailure(
                    position=envelope.position,
                    event_type=type(envelope.event).__name__,
                    projection=type(projection).__name__,
                    error=exc,
                )
                if strict:
                    raise ReplayFailedError(failure=failure) from exc
                failures.append(failure)
                rejected = True
        if not rejected:
            applied += 1

    return ReplayReport(
        applied=applied,
        last_position=last_position,
        failures=tuple(failures),
    )


#: `project` under a name that does not collide with a caller's own "project".
#:
#: Reported downstream: any knowledge-graph consumer plausibly has a *project*
#: noun of its own, and ours landed in the same twelve-line function. Renaming
#: would break every caller for a cosmetic gain; an alias costs the surface one
#: name and lets the collision be dodged at the import.
replay = project
