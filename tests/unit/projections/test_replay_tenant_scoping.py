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
`replay` asked the adapter for.

The behavioural half is still here, because forwarding the options and having
the adapter honour them are two claims and each can fail without the other.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from eventsource.application.projections import replay
from eventsource.domain.tenant_context import tenant_scope
from eventsource.ports.positions import ExpectedVersion

from redstring.aggregates.consolidation_log import ConsolidationLog
from redstring.aggregates.document import Document
from redstring.domain.entity import Entity, ExtractionMethod
from redstring.events import DocumentExtracted
from redstring.events.streams import consolidation_stream, document_stream

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


async def _append_a_merge(event_store):
    """One `Consolidation` event in the same log, so the filter has something
    to exclude. Written through the aggregate rather than hand-built, so the
    `aggregate_type` on the envelope is the one production writes."""
    stream = consolidation_stream(tenant_id=QUIET)
    log = ConsolidationLog(aggregate_id=stream.aggregate_id)
    canonical, absorbed = uuid4(), uuid4()
    async with tenant_scope(QUIET):
        log.merge(
            tenant_id=QUIET,
            canonical_entity_id=canonical,
            merged_entity_ids=[absorbed],
            redirections=[],
        )
        await event_store.append(stream, list(log.uncommitted_events), ExpectedVersion.no_stream())


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

        await replay(feed, rig.projections, tenant_id=QUIET)

        ((_, options),) = feed.calls
        assert options is not None
        assert options.tenant_id == QUIET

    async def test_no_filter_is_sent_when_no_tenant_is_named(self) -> None:
        """A whole-feed rebuild must not narrow the read.

        This asserted `options is None` until `eventsource-py` 0.14.0, on the
        reasoning that `None` is what `read_all` documents as unfiltered and
        an adapter cannot misinterpret an object it never receives. 0.14.0
        reads the feed in bounded batches rather than materialising the whole
        log in one call, and the batch size travels in `FeedReadOptions.limit`
        -- so options are now sent on every read, including this one, and the
        old assertion was pinning the absence of an allocation bound rather
        than the absence of a filter.

        The claim that survives is the one the module is about: *narrowing*
        happens only when the caller asks for it. So assert the filter fields
        are unset rather than that the carrier is missing -- the two were the
        same thing upstream and are not any more, and only one of them was
        ever this test's subject.
        """
        rig, _ = await _shared_log()
        feed = RecordingFeed(rig.event_store)

        await replay(feed, rig.projections)

        ((_, options),) = feed.calls
        assert options is not None
        assert options.tenant_id is None
        assert options.aggregate_type is None


class TestTheScopedReplayRebuildsOnlyThatTenant:
    async def test_only_the_named_tenants_events_are_applied(self) -> None:
        rig, _quiet = await _shared_log()

        report = await replay(rig.event_store, rig.projections, tenant_id=QUIET)

        # Seven documents in the log, one of them this tenant's.
        assert report.applied == 1
        assert report.failed == 0

    async def test_the_other_tenant_is_left_out_of_the_read_models(self) -> None:
        rig, quiet = await _shared_log()

        await replay(rig.event_store, rig.projections, tenant_id=QUIET)

        shape = await rig.shape([QUIET, LOUD])
        assert shape[str(QUIET)]["entity_ids"] == [str(quiet.id)]
        assert shape[str(LOUD)]["entity_ids"] == []

    async def test_the_unscoped_replay_still_sees_everything(self) -> None:
        """The counterpart the scoped assertions need: without `tenant_id` the
        same log gives all seven, so a scoped result of 1 is the filter and not
        an empty log."""
        rig, _ = await _shared_log()

        report = await replay(rig.event_store, rig.projections)

        assert report.applied == 7

    async def test_the_last_position_is_the_last_matching_event(self) -> None:
        """A caller checkpoints on `last_position`. Under a filter it has to be
        a position the *filtered* read reached, or the next scoped replay
        resumes past events it never saw."""
        rig, _ = await _shared_log()

        scoped = await replay(rig.event_store, rig.projections, tenant_id=QUIET)
        whole = await replay(rig.event_store, fresh_rig().projections)

        assert scoped.last_position is not None
        assert scoped.last_position < whole.last_position


class TestAggregateTypeNarrowsTheReadTheSameWay:
    """`aggregate_type=` is `tenant_id=`'s companion, and new in 0.12.0.

    This library writes two aggregate types -- `Document` and `Consolidation`
    -- into one log, and only the first carries the events a read-model
    rebuild folds. Before `FeedReadOptions.aggregate_type` existed the filter
    had nowhere to go, so a rebuild read the merge log too and discarded it in
    Python (`BACKLOG.md` B68).

    The string is asserted against `Document.aggregate_type` rather than
    written out, because the two must agree and a literal here would keep
    passing after a rename that broke every caller following the how-to guide.
    """

    async def test_the_aggregate_type_is_forwarded_as_feed_read_options(self) -> None:
        rig, _ = await _shared_log()
        feed = RecordingFeed(rig.event_store)

        await replay(feed, rig.projections, aggregate_type=Document.aggregate_type)

        ((_, options),) = feed.calls
        assert options is not None
        assert options.aggregate_type == "Document"

    async def test_it_composes_with_the_tenant_rather_than_replacing_it(self) -> None:
        """Both in one `FeedReadOptions`, or the second silently wins."""
        rig, _ = await _shared_log()
        feed = RecordingFeed(rig.event_store)

        await replay(feed, rig.projections, tenant_id=QUIET, aggregate_type=Document.aggregate_type)

        ((_, options),) = feed.calls
        assert (options.tenant_id, options.aggregate_type) == (QUIET, "Document")

    async def test_the_merge_log_is_read_out_and_the_documents_are_not(self) -> None:
        """The behavioural half. A store holding both types, scoped to one.

        Asserted from both sides: scoping to `Consolidation` must read the one
        merge event and none of the seven documents, which is what would fail
        if the option were forwarded and ignored -- the counts would be equal
        either way if only the `Document` direction were checked, since seven
        is also what an unfiltered read of a document-only log returns.
        """
        rig, _ = await _shared_log()
        await _append_a_merge(rig.event_store)

        documents = await replay(
            rig.event_store, rig.projections, aggregate_type=Document.aggregate_type
        )
        merges = await replay(
            rig.event_store,
            fresh_rig().projections,
            aggregate_type=ConsolidationLog.aggregate_type,
        )
        everything = await replay(rig.event_store, fresh_rig().projections)

        assert (documents.applied, merges.applied, everything.applied) == (7, 1, 8)
