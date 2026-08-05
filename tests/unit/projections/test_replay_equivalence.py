"""The deliverable: a projection that has only ever been fed live has never
been shown to rebuild.

Four claims, in increasing order of what they catch:

1. Folding a log produces the state the log describes -- checked against an
   oracle the builder maintains, not against the fold itself.
2. Projecting, wiping the stores, and replaying from position zero produces
   the same state. This is what "the stores are derived and disposable"
   means, and until it is tested it is a slogan.
3. The same holds when every event is delivered twice, which is what
   at-least-once delivery does on any real bus.
4. The same holds when the whole log is re-projected on top of itself, which
   is what a rebuild started without wiping does.

**Claim 1 is not redundant, and finding that out cost three surviving
mutants.** Claims 2-4 are self-consistency properties: both sides run the same
handlers, so a handler that does too little -- never applies an undo, never
deletes a dropped edge, never writes relationships -- makes both sides agree
on the same wrong state and all three pass. Only an independent oracle
distinguishes "replays consistently" from "replays correctly".

`hypothesis` generates the sequences, because a hand-written one tests the
single ordering its author thought of and the interesting failures here are
all orderings. A property test is a sampler rather than a proof, though, so
the named boundaries run as pinned cases every time rather than being left to
the sampler to stumble on.

Every test builds its own rig. Not tidiness: a function-scoped fixture is
created once for a whole `@given`, so the second example onwards would run
against the log and stores the first left behind -- which made this suite fail
intermittently before `fresh_rig` existed.
"""

from __future__ import annotations

from contextlib import suppress

import pytest
from eventsource.adapters.memory import InMemorySnapshotStore
from hypothesis import given, settings

from redstring.projections import project

from .conftest import fresh_rig
from .log_builder import DocumentSpec, Scenario, build_log, scenarios

REPLAY_SETTINGS = settings(max_examples=50)

#: An empty log. Position zero with nothing at it -- the case a replay loop
#: that never enters its body would pass, and a `read_all` mishandling
#: `from_position=None` would not.
EMPTY = Scenario(tenant_count=1, documents=())

#: One document, one entity, no edges: the smallest non-empty log.
SINGLE = Scenario(
    tenant_count=1,
    documents=(DocumentSpec(tenant=0, index=0, entity_count=1, edges=(), embedded=True),),
)

#: A merge that moves an edge onto the canonical entity.
WITH_MERGE = Scenario(
    tenant_count=1,
    documents=(DocumentSpec(tenant=0, index=0, entity_count=3, edges=((1, 2),), embedded=True),),
    merges=((0, 0, 1),),
)

#: A merge absorbing both endpoints of an edge, so the edge is dropped rather
#: than moved -- the `after is None` branch, which no amount of moving covers.
DROPPING_MERGE = Scenario(
    tenant_count=1,
    documents=(DocumentSpec(tenant=0, index=0, entity_count=3, edges=((1, 2),), embedded=False),),
    merges=((0, 0, 1), (0, 0, 2)),
)

#: A merge and its undo. The state must come back to what it was before.
WITH_UNDO = Scenario(
    tenant_count=1,
    documents=(DocumentSpec(tenant=0, index=0, entity_count=3, edges=((1, 2),), embedded=True),),
    merges=((0, 0, 1),),
    undo_positions=(0,),
)

#: An undo of a merge that *dropped* an edge, so the undo has to recreate the
#: edge rather than move it back. Distinct from WITH_UNDO in exactly the
#: branch that a "restore endpoints" implementation would get wrong.
UNDO_OF_DROPPING_MERGE = Scenario(
    tenant_count=1,
    documents=(DocumentSpec(tenant=0, index=0, entity_count=3, edges=((1, 2),), embedded=False),),
    merges=((0, 0, 1), (0, 0, 2)),
    undo_positions=(1,),
)

#: Two tenants in one log. The projection folds the *global* feed, so this is
#: where a handler that ignored `tenant_id` would blend them.
TWO_TENANTS = Scenario(
    tenant_count=2,
    documents=(
        DocumentSpec(tenant=0, index=0, entity_count=2, edges=((0, 1),), embedded=True),
        DocumentSpec(tenant=1, index=1, entity_count=2, edges=((0, 1),), embedded=True),
    ),
)

PINNED = {
    "empty": EMPTY,
    "single": SINGLE,
    "merge": WITH_MERGE,
    "dropping-merge": DROPPING_MERGE,
    "undo": WITH_UNDO,
    "undo-of-dropping-merge": UNDO_OF_DROPPING_MERGE,
    "two-tenants": TWO_TENANTS,
}


async def _built(scenario):
    """A fresh rig with `scenario`'s events already appended to its log."""
    rig = fresh_rig()
    built = await build_log(rig.event_store, InMemorySnapshotStore(), scenario)
    return rig, built


async def _wipe(rig, tenant_ids):
    """Empty both stores for these tenants, and prove they are empty.

    The assertion is the point. A wipe that silently did nothing would make
    every replay test here pass trivially: the "replayed" state would be the
    state the first projection left behind, and the replay could be deleted
    outright without a test noticing.
    """
    for tenant_id in tenant_ids:
        await rig.graph_store.delete_by_tenant(tenant_id)
        await rig.vector_store.delete_by_tenant(tenant_id)
    assert await rig.dump(tenant_ids) == {
        str(tenant_id): {"entities": [], "relationships": [], "aliases": [], "vectors": []}
        for tenant_id in sorted(tenant_ids, key=str)
    }


async def _deliver_twice(rig):
    """Every event, in order, handed to every projection twice.

    Order-preserving redelivery, which is what a checkpointed feed produces:
    the same events, possibly repeated, never reordered. `GraphProjection`
    documents why it needs that much and no more.
    """
    async for envelope in rig.event_store.read_all():
        for projection in rig.projections:
            for _ in range(2):
                # Same contract as `project`: a poison event is recorded in the
                # DLQ and does not stop the replay. Inlined rather than reusing
                # `project` because the double delivery is this helper's point.
                with suppress(Exception):
                    await projection.handle(envelope.event)


class TestTheFoldIsCorrect:
    """Claim 1: the projection produces the state the log describes."""

    @given(scenario=scenarios())
    @REPLAY_SETTINGS
    async def test_the_projected_graph_matches_the_oracle(self, scenario):
        rig, built = await _built(scenario)
        report = await project(rig.event_store, rig.projections)
        assert report.failed == 0
        assert await rig.shape(built.tenant_ids) == built.expected_shape()

    @pytest.mark.parametrize("scenario", PINNED.values(), ids=PINNED.keys())
    async def test_the_pinned_cases_match_the_oracle(self, scenario):
        rig, built = await _built(scenario)
        report = await project(rig.event_store, rig.projections)
        assert report.failed == 0
        assert await rig.dlq.get_failed_events() == []
        assert await rig.shape(built.tenant_ids) == built.expected_shape()


class TestReplayEquivalence:
    @given(scenario=scenarios())
    @REPLAY_SETTINGS
    async def test_a_wiped_store_replays_to_the_same_state(self, scenario):
        rig, built = await _built(scenario)

        await project(rig.event_store, rig.projections)
        live = await rig.dump(built.tenant_ids)

        await _wipe(rig, built.tenant_ids)
        await project(rig.event_store, rig.projections)

        assert await rig.dump(built.tenant_ids) == live

    @given(scenario=scenarios())
    @REPLAY_SETTINGS
    async def test_at_least_once_delivery_changes_nothing(self, scenario):
        """The harder one: it catches a fold that accumulates rather than
        replaces, which a single clean replay cannot distinguish."""
        rig, built = await _built(scenario)

        await project(rig.event_store, rig.projections)
        once = await rig.dump(built.tenant_ids)

        await _wipe(rig, built.tenant_ids)
        await _deliver_twice(rig)

        assert await rig.dump(built.tenant_ids) == once

    @given(scenario=scenarios())
    @REPLAY_SETTINGS
    async def test_replaying_over_a_live_projection_changes_nothing(self, scenario):
        """A rebuild started without wiping first. Not the recommended
        procedure, but the one an operator reaches for, and it has to converge
        rather than double every edge."""
        rig, built = await _built(scenario)

        await project(rig.event_store, rig.projections)
        live = await rig.dump(built.tenant_ids)
        await project(rig.event_store, rig.projections)

        assert await rig.dump(built.tenant_ids) == live


@pytest.mark.parametrize("scenario", PINNED.values(), ids=PINNED.keys())
class TestPinnedBoundaries:
    """The named cases, run every time rather than sampled toward."""

    async def test_a_wiped_store_replays_to_the_same_state(self, scenario):
        rig, built = await _built(scenario)
        await project(rig.event_store, rig.projections)
        live = await rig.dump(built.tenant_ids)
        await _wipe(rig, built.tenant_ids)
        await project(rig.event_store, rig.projections)
        assert await rig.dump(built.tenant_ids) == live

    async def test_at_least_once_delivery_changes_nothing(self, scenario):
        rig, built = await _built(scenario)
        await project(rig.event_store, rig.projections)
        once = await rig.dump(built.tenant_ids)
        await _wipe(rig, built.tenant_ids)
        await _deliver_twice(rig)
        assert await rig.dump(built.tenant_ids) == once


class TestReplayFromNothing:
    async def test_an_empty_log_projects_to_empty_stores(self, rig):
        """Genuinely nothing: no events, no prior run, no state left by an
        earlier phase. This is what would catch a `project` whose loop never
        ran and whose report was fabricated.
        """
        report = await project(rig.event_store, rig.projections)
        assert report.applied == 0
        assert report.failed == 0
        assert report.last_position is None
        assert await rig.dump([]) == {}

    async def test_a_replay_from_position_zero_reads_the_first_event(self):
        """`from_position=None` must mean *before* the first event, not *at*
        it. An off-by-one drops the first event of every rebuild, which on
        this schema is a whole document.
        """
        rig, built = await _built(SINGLE)
        report = await project(rig.event_store, rig.projections)
        assert report.applied == 2  # DocumentExtracted, then EntitiesEmbedded
        shape = await rig.shape(built.tenant_ids)
        assert len(shape[str(built.tenant_ids[0])]["entity_ids"]) == 1
        assert len(shape[str(built.tenant_ids[0])]["vectors"]) == 1

    async def test_resuming_from_the_last_position_applies_nothing(self):
        """`from_position` is exclusive, so resuming from the position of the
        last event applied is a no-op rather than a re-application of it."""
        rig, _ = await _built(SINGLE)
        first = await project(rig.event_store, rig.projections)
        resumed = await project(rig.event_store, rig.projections, from_position=first.last_position)
        assert resumed.applied == 0
        assert resumed.last_position is None


class TestTheReplayIsBounded:
    """The loop's exit depends on adapter-supplied data, so it has a bound --
    and a bound nothing exercises is a bound nobody knows works.

    A cursor that failed to advance would otherwise hang, and a hang in CI
    reads as infrastructure trouble and gets retried rather than investigated.
    """

    async def test_a_feed_that_will_not_end_fails_instead_of_hanging(self):
        rig, _ = await _built(SINGLE)
        with pytest.raises(RuntimeError, match="cursor is probably not advancing"):
            await project(rig.event_store, rig.projections, max_events=1)

    async def test_a_log_exactly_at_the_bound_is_not_rejected(self):
        """Off-by-one: the bound is the number of events allowed, not the
        number after which reading stops."""
        rig, _ = await _built(SINGLE)
        report = await project(rig.event_store, rig.projections, max_events=2)
        assert report.applied == 2
