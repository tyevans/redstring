"""A rejected event is named, not just counted.

`ReplayReport.failed` on its own is safe and useless in the same breath: an
operator told "1 event failed" has no path from that message to the poison
event, because the exception was discarded inside the `except`. These tests
pin the detail that replaces it, and the `strict=True` raise built on top.

The failure used here is the same real one `test_poison_events.py` uses -- a
relationship whose endpoint this tenant does not have -- so the assertions are
about a fold that genuinely refused, not about an injected exception. The one
exception is `TestFailedCountsEventsAndFailuresCountRejections`, which needs
*two* projections to reject the *same* event and so supplies its own.
"""

from __future__ import annotations

import pytest

from redstring.domain.exceptions import MissingEntityError
from redstring.projections import ReplayFailedError, project
from redstring.projections.replay import ReplayReport

from .conftest import POISON_TENANT_ID


class AlwaysRejects:
    """A subscriber that refuses everything, with a distinguishable error."""

    def __init__(self, message: str) -> None:
        self._message = message

    async def handle(self, event: object) -> None:
        raise RuntimeError(self._message)


class TestAFailureNamesTheEvent:
    async def test_it_carries_the_position_the_event_type_and_the_projection(
        self, poisoned_log
    ) -> None:
        rig, _ = poisoned_log
        report = await project(rig.event_store, rig.projections)

        (failure,) = report.failures
        assert failure.event_type == "DocumentExtracted"
        assert failure.projection == "GraphProjection"

        positions = [envelope.position async for envelope in rig.event_store.read_all()]
        # The poison is the *middle* document, so a failure that reported the
        # first or last position would still be a position.
        assert failure.position == positions[1]

    async def test_it_carries_the_exception_itself_rather_than_its_message(
        self, poisoned_log
    ) -> None:
        """A message has to be parsed back into the ids it names. The
        exception already has them as attributes."""
        rig, entities = poisoned_log
        report = await project(rig.event_store, rig.projections)

        (failure,) = report.failures
        assert isinstance(failure.error, MissingEntityError)
        assert failure.error.entity_id not in {e.id for e in entities}

    async def test_a_clean_replay_reports_no_failures(self, rig) -> None:
        report = await project(rig.event_store, rig.projections)
        assert report.failures == ()
        assert report.failed == 0


class TestFailedCountsEventsAndFailuresCountRejections:
    """The two numbers are allowed to differ, and here they do.

    Both projections reject the one event, so a `failed` counted alongside
    `failures` -- or derived as `len(failures)` -- would say two events failed
    when the log holds one. Deriving it from the *distinct positions* is what
    keeps "how much of the log did not make it into the read models" true.
    """

    async def test_two_projections_rejecting_one_event_is_one_failed_event(
        self, poisoned_log
    ) -> None:
        rig, _ = poisoned_log
        report = await project(
            rig.event_store,
            [AlwaysRejects("left"), AlwaysRejects("right")],
        )

        assert report.failed == 3  # every document, rejected by both
        assert len(report.failures) == 6
        assert {str(f.error) for f in report.failures} == {"left", "right"}

    async def test_nothing_is_applied_when_every_projection_rejects(self, poisoned_log) -> None:
        rig, _ = poisoned_log
        report = await project(rig.event_store, [AlwaysRejects("no")])
        assert report.applied == 0


class TestStrictRaisesOnTheFirstRejection:
    async def test_it_raises_carrying_the_failure(self, poisoned_log) -> None:
        rig, _ = poisoned_log
        with pytest.raises(ReplayFailedError) as raised:
            await project(rig.event_store, rig.projections, strict=True)

        failure = raised.value.failure
        assert failure.event_type == "DocumentExtracted"
        assert failure.projection == "GraphProjection"
        assert isinstance(failure.error, MissingEntityError)
        assert raised.value.__cause__ is failure.error

    async def test_the_message_names_the_event_rather_than_a_count(self, poisoned_log) -> None:
        rig, _ = poisoned_log
        with pytest.raises(ReplayFailedError, match="GraphProjection rejected DocumentExtracted"):
            await project(rig.event_store, rig.projections, strict=True)

    async def test_it_stops_rather_than_carrying_on(self, poisoned_log) -> None:
        """The poison is the middle document. A strict replay that raised and
        still folded the third would be a louder default, not a stop."""
        rig, entities = poisoned_log
        with pytest.raises(ReplayFailedError):
            await project(rig.event_store, rig.projections, strict=True)

        shape = await rig.shape([POISON_TENANT_ID])
        assert str(entities[2].id) not in shape[str(POISON_TENANT_ID)]["entity_ids"]

    async def test_a_clean_log_is_unaffected_by_strict(self, poisoned_log) -> None:
        """Strict changes nothing when nothing fails -- so a caller can leave
        it on."""
        rig, _ = poisoned_log
        lenient = await project(rig.event_store, [])
        strict = await project(rig.event_store, [], strict=True)
        assert (lenient.applied, lenient.failed) == (strict.applied, strict.failed) == (3, 0)


class TestFailedIsDerivedRatherThanSupplied:
    """`ReplayReport` takes no `failed` argument.

    Pinned because restoring one is the change that would let the two drift
    apart again, and it would look like a harmless convenience.
    """

    def test_it_cannot_be_set_independently_of_the_failures(self) -> None:
        with pytest.raises(TypeError):
            ReplayReport(applied=1, failed=9, last_position=None)  # type: ignore[call-arg]
