"""`tenant_id=` narrows the read, not the delivery.

Reported downstream: scoping a rebuild with `tenant_filter` on the projection
is correct and reads the whole log anyway, discarding most of it in Python. In
a shared store where one tenant's events vastly outnumber another's, that is
the difference between O(whole store) and O(this tenant) per rebuild.

## Why this asserts on the options and not only on the result

Filtering client-side and pushing the filter into the query produce **the same
answers**. That is `.claude/rules/recurring-defects.md` §4 exactly -- the
inputs cannot distinguish the implementations -- so a test that only looked at
which entities landed would pass against the workaround it exists to replace.
`RecordingFeed` is the query plan, in the only form a port exposes one: what
`project` asked the adapter for.

The behavioural half is still here, because forwarding the options and having
the adapter honour them are two claims and each can fail without the other.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from eventsource.domain.tenant_context import tenant_scope
from eventsource.ports.positions import ExpectedVersion

from redstring.domain.entity import Entity, ExtractionMethod
from redstring.events import DocumentExtracted
from redstring.events.streams import document_stream
from redstring.projections import project

from .conftest import fresh_rig

if TYPE_CHECKING:
    from eventsource.adapters.memory import InMemoryEventStore

LOUD = uuid4()
QUIET = uuid4()


class RecordingFeed:
    """A `GlobalEventFeed` that remembers how it was asked."""

    def __init__(self, inner: InMemoryEventStore) -> None:
        self._inner = inner
        self.calls: list[tuple[object, object]] = []

    def read_all(self, from_position=None, options=None):
        self.calls.append((from_position, options))
        return self._inner.read_all(from_position, options)

    async def current_position(self):
        return await self._inner.current_position()


def _entity(tenant_id, source_id, name):
    return Entity(
        id=uuid4(),
        tenant_id=tenant_id,
        name=name,
        normalized_name=name.lower(),
        entity_type="thing",
        source_id=source_id,
        extraction_method=ExtractionMethod.PATTERN,
        confidence=0.5,
    )


async def _append(event_store, entity):
    stream = document_stream(tenant_id=entity.tenant_id, source_id=entity.source_id)
    async with tenant_scope(entity.tenant_id):
        await event_store.append(
            stream,
            [
                DocumentExtracted(
                    aggregate_id=stream.aggregate_id,
                    tenant_id=entity.tenant_id,
                    source_id=entity.source_id,
                    model_version="ollama/qwen3.6-27b",
                    entities=[entity],
                    relationships=[],
                )
            ],
            ExpectedVersion.no_stream(),
        )


async def _shared_log():
    """One quiet tenant's document buried among a loud tenant's."""
    rig = fresh_rig()
    quiet = _entity(QUIET, "quiet-1", "Ada")
    for index in range(3):
        await _append(rig.event_store, _entity(LOUD, f"loud-{index}", f"Noise {index}"))
    await _append(rig.event_store, quiet)
    for index in range(3, 6):
        await _append(rig.event_store, _entity(LOUD, f"loud-{index}", f"Noise {index}"))
    return rig, quiet


class TestTheFilterReachesTheAdapter:
    async def test_the_tenant_is_forwarded_as_feed_read_options(self) -> None:
        rig, _ = await _shared_log()
        feed = RecordingFeed(rig.event_store)

        await project(feed, rig.projections, tenant_id=QUIET)

        ((_, options),) = feed.calls
        assert options is not None
        assert options.tenant_id == QUIET

    async def test_no_options_are_sent_when_no_tenant_is_named(self) -> None:
        """A whole-feed rebuild must not start sending a filter object an
        adapter might interpret -- `None` is what `read_all` documents as
        unfiltered."""
        rig, _ = await _shared_log()
        feed = RecordingFeed(rig.event_store)

        await project(feed, rig.projections)

        ((_, options),) = feed.calls
        assert options is None


class TestTheScopedReplayRebuildsOnlyThatTenant:
    async def test_only_the_named_tenants_events_are_applied(self) -> None:
        rig, _quiet = await _shared_log()

        report = await project(rig.event_store, rig.projections, tenant_id=QUIET)

        # Seven documents in the log, one of them this tenant's.
        assert report.applied == 1
        assert report.failed == 0

    async def test_the_other_tenant_is_left_out_of_the_read_models(self) -> None:
        rig, quiet = await _shared_log()

        await project(rig.event_store, rig.projections, tenant_id=QUIET)

        shape = await rig.shape([QUIET, LOUD])
        assert shape[str(QUIET)]["entity_ids"] == [str(quiet.id)]
        assert shape[str(LOUD)]["entity_ids"] == []

    async def test_the_unscoped_replay_still_sees_everything(self) -> None:
        """The counterpart the scoped assertions need: without `tenant_id` the
        same log gives all seven, so a scoped result of 1 is the filter and not
        an empty log."""
        rig, _ = await _shared_log()

        report = await project(rig.event_store, rig.projections)

        assert report.applied == 7

    async def test_the_last_position_is_the_last_matching_event(self) -> None:
        """A caller checkpoints on `last_position`. Under a filter it has to be
        a position the *filtered* read reached, or the next scoped replay
        resumes past events it never saw."""
        rig, _ = await _shared_log()

        scoped = await project(rig.event_store, rig.projections, tenant_id=QUIET)
        whole = await project(rig.event_store, fresh_rig().projections)

        assert scoped.last_position is not None
        assert scoped.last_position < whole.last_position
